"""Bounded, correlated RPC delivery over agent-initiated HTTP connections.

Nothing here contacts Bitcoin Core directly. The agent retains Core credentials
and enforces its own permissions. Signing/unlocking/private exports are excluded.
"""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import json,re,time,uuid

NODE_METHODS=frozenset('getblockchaininfo getblockcount getbestblockhash getblockhash getblockheader getblock getchaintips getdifficulty getmempoolinfo getrawmempool getmempoolentry getmempoolancestors getmempooldescendants gettxout gettxoutsetinfo getrawtransaction gettxoutproof verifytxoutproof getnetworkinfo getnettotals getpeerinfo getconnectioncount getindexinfo estimatesmartfee deriveaddresses getdescriptorinfo validateaddress decoderawtransaction decodescript decodepsbt analyzepsbt listwallets listwalletdir scantxoutset scanblocks getdescriptoractivity getblockfrompeer'.split())
WALLET_METHODS=frozenset('getwalletinfo getbalances getbalance listunspent gettransaction listtransactions listsinceblock getaddressinfo getaddressesbylabel listlabels listaddressgroupings listreceivedbyaddress listreceivedbylabel listdescriptors'.split())
METHODS=NODE_METHODS|WALLET_METHODS
MAX_REQUEST_BYTES=4*1024*1024
MAX_RESULT_BYTES=16*1024*1024
MAX_TOTAL_BYTES=64*1024*1024
MAX_REQUESTS=256
MAX_PENDING_PER_NODE=16
MAX_IN_FLIGHT_PER_AGENT=2
RESULT_RETENTION=600
PRIVATE=re.compile(r'(?i)(?:[xtyzuv]prv[1-9A-HJ-NP-Za-km-z]{20,})|(?<![A-Za-z0-9])(?:[KL][1-9A-HJ-NP-Za-km-z]{51}|5[1-9A-HJ-NP-Za-km-z]{50})(?![A-Za-z0-9])')

def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def clean_json(value,limit):
    """Retain fractional JSON numbers as exact decimal strings, never binary floats."""
    def decimal_text(v):
        if not isinstance(v,Decimal) or not v.is_finite() or abs(v.adjusted())>100 or len(v.as_tuple().digits)>1000:raise ValueError('Unsupported or oversized decimal value.')
        return format(v,'f')
    raw=json.dumps(value,ensure_ascii=True,allow_nan=False,default=decimal_text)
    if len(raw.encode())>limit:raise ValueError('RPC message exceeds its size limit.')
    if PRIVATE.search(raw):raise ValueError('Private key material is not accepted by the agent transport.')
    return json.loads(raw),len(raw.encode())

def capabilities(value):
    if value is None:return []
    if not isinstance(value,dict) or set(value)!={'version','methods'} or type(value['version']) is not int or value['version']!=1:raise ValueError('Unsupported RPC capability version.')
    methods=value['methods']
    if not isinstance(methods,list) or len(methods)>256 or any(not isinstance(m,str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}',m) for m in methods):raise ValueError('Invalid RPC method capability list.')
    return sorted(set(methods)&METHODS)

def request(method,params,wallet):
    if not isinstance(method,str) or method not in METHODS:raise ValueError('RPC method is not enabled for Dashboard agents.')
    if not isinstance(params,(list,dict)):raise ValueError('RPC params must be an array or object.')
    params,size=clean_json(params,MAX_REQUEST_BYTES)
    if wallet is not None and (not isinstance(wallet,str) or len(wallet)>256 or any(ord(c)<32 for c in wallet)):raise ValueError('Invalid wallet name.')
    if method in WALLET_METHODS and wallet is None:raise ValueError('Specify the exact Core wallet name for a wallet RPC.')
    if method not in WALLET_METHODS and wallet is not None:raise ValueError('This node RPC does not take a wallet endpoint.')
    if method=='listdescriptors' and params not in ([],[False],{}, {'private':False}):raise ValueError('Only public listdescriptors false exports are permitted.')
    return params,size

class RPCBroker:
    def __init__(self,registry):
        import threading
        self.registry=registry;self.condition=threading.Condition(registry.lock);self.rows={};self.consumed={};self.total_bytes=0
    def _finish(self,row,state,error=None,result=None):
        self.total_bytes-=row['size'];row['state']=state;row['finished']=self.registry.clock();row['finished_utc']=stamp()
        row.pop('params',None);row['error']=error;row['result']=result
        row['size']=len(json.dumps({'result':result,'error':error},ensure_ascii=True).encode());self.total_bytes+=row['size'];self.condition.notify_all()
    def _maintain(self):
        now=self.registry.clock()
        for ident,row in list(self.consumed.items()):
            if now-row['finished']>=RESULT_RETENTION:self.consumed.pop(ident,None)
        for ident,row in list(self.rows.items()):
            if row['state'] in ('queued','delivered','running') and now>=row['deadline']:
                self._finish(row,'timed_out',{'code':'timeout','message':'Agent RPC timed out. Its execution outcome may be unknown; it was not retried.'})
            if row['state'] not in ('queued','delivered','running') and now-row['finished']>=RESULT_RETENTION:
                self.total_bytes-=row['size'];del self.rows[ident]
    def disconnected(self,token):
        for row in self.rows.values():
            if row['token']==token and row['state'] in ('queued','delivered','running'):
                self._finish(row,'failed',{'code':'connection_lost','message':'Agent connection ended. Execution outcome may be unknown; the request was not replayed.'})
    def _connection(self,token):
        self.registry._expire();connection=self.registry.connections.get(token)
        if not connection:raise PermissionError('Connection expired. Register again.')
        return connection
    def _unique(self,connection):
        return sum(c['mac']==connection['mac'] for c in self.registry.connections.values())==1 and sum(c['hostname']==connection['hostname'] for c in self.registry.connections.values())==1
    def reconcile(self):
        for token,c in self.registry.connections.items():
            if not self._unique(c):
                for row in self.rows.values():
                    if row['token']==token and row['state'] in ('queued','delivered','running'):
                        self._finish(row,'failed',{'code':'identity_conflict','message':'RPC stopped because the node hostname or MAC has a conflicting live connection.'})
        self.condition.notify_all()
    def queue(self,node_id,method,params=None,wallet=None,timeout_seconds=30,owner=None,priority=0):
        if type(priority) is not int or not 0<=priority<=100:raise ValueError('Invalid internal RPC priority.')
        params,size=request(method,[] if params is None else params,wallet)
        if type(timeout_seconds) not in (int,float) or not 1<=timeout_seconds<=3600:raise ValueError('RPC timeout must be from 1 to 3600 seconds.')
        with self.condition:
            self.registry._expire();self._maintain();self.reconcile()
            matches=[(t,c) for t,c in self.registry.connections.items() if c['node_id']==node_id]
            if len(matches)!=1 or not self._unique(matches[0][1]):raise ValueError('Node is disconnected or has a conflicting identity.')
            token,connection=matches[0]
            if method not in connection.get('rpc_methods',[]):raise ValueError('The connected agent has not enabled this RPC method.')
            if sum(r['node_id']==node_id and r['state'] in ('queued','delivered','running') for r in self.rows.values())>=MAX_PENDING_PER_NODE:raise ValueError('Too many pending RPC requests for this node.')
            if len(self.rows)>=MAX_REQUESTS or self.total_bytes+size>MAX_TOTAL_BYTES:raise ValueError('RPC request/result storage is full. Wait for completed requests to expire.')
            ident=uuid.uuid4().hex;now=self.registry.clock();self.rows[ident]={'id':ident,'node_id':node_id,'method':method,'params':params,'wallet':wallet,'owner':owner,'priority':priority,'token':token,'state':'queued','created_utc':stamp(),'deadline':now+timeout_seconds,'timeout_seconds':timeout_seconds,'size':size};self.total_bytes+=size;self.condition.notify_all();return self.status(ident,owner)
    def _commands(self,token):
        connection=self._connection(token);self._maintain()
        if not self._unique(connection):return []
        commands=[]
        active=sum(r['token']==token and r['state'] in ('delivered','running') for r in self.rows.values())
        foreground=any(r['token']==token and r['state'] in ('queued','delivered','running') and r.get('priority',0)==0 for r in self.rows.values())
        for row in sorted(self.rows.values(),key=lambda r:r.get('priority',0)):
            if foreground and row.get('priority',0)>0 and row['state']=='queued':continue
            if row['token']==token and (row['state']=='delivered' or row['state']=='queued' and active<MAX_IN_FLIGHT_PER_AGENT):
                row['state']='delivered';commands.append({'id':row['id'],'kind':'rpc','method':row['method'],'params':deepcopy(row['params']),'wallet':row['wallet'],'expires_in_seconds':max(0,row['deadline']-self.registry.clock())});break
        return commands
    def poll(self,token,wait_seconds=20):
        if type(wait_seconds) not in (int,float,Decimal) or not 0<=wait_seconds<=20:raise ValueError('Poll wait must be from 0 to 20 seconds.')
        wait_seconds=float(wait_seconds)
        with self.condition:
            connection=self._connection(token);connection['seen']=self.registry.clock();until=time.monotonic()+wait_seconds
            while True:
                commands=self._commands(token)
                if commands or time.monotonic()>=until:return {'ok':True,'commands':commands}
                self.condition.wait(min(1,max(0,until-time.monotonic())))
    def ack(self,token,ident):
        with self.condition:
            self._connection(token);self._maintain();row=self.rows.get(ident)
            if not row and ident in self.consumed and self.consumed[ident]['token']==token:return {'ok':True,'state':self.consumed[ident]['state'],'execute':False}
            if not row or row['token']!=token:raise PermissionError('RPC request does not belong to this agent connection.')
            execute=row['state'] in ('queued','delivered')
            if execute:row['state']='running';row['started_utc']=stamp()
            return {'ok':True,'state':row['state'],'execute':execute}
    def result(self,token,ident,result=None,error=None):
        with self.condition:
            self._connection(token);self._maintain();row=self.rows.get(ident)
            if not row and ident in self.consumed and self.consumed[ident]['token']==token:return {'ok':True,'state':self.consumed[ident]['state'],'accepted':False}
            if not row or row['token']!=token:raise PermissionError('RPC request does not belong to this agent connection.')
            if error is not None:
                if not isinstance(error,dict) or not {'code','message'}<=set(error) or set(error)-{'code','message','data'} or type(error['code']) not in (int,str) or not isinstance(error['message'],str):raise ValueError('Invalid RPC error response.')
            if row['state'] not in ('delivered','running'):return {'ok':True,'state':row['state'],'accepted':False}
            try:
                value,size=clean_json({'result':result,'error':error},MAX_RESULT_BYTES)
                if self.total_bytes-row['size']+size>MAX_TOTAL_BYTES:raise ValueError('RPC result storage limit exceeded.')
            except ValueError:
                self._finish(row,'failed',{'code':'invalid_result','message':'Agent result was rejected (size, JSON validity or private key material).'});raise
            self._finish(row,'failed' if error is not None else 'succeeded',value['error'],value['result']);return {'ok':True,'state':row['state'],'accepted':True}
    def status(self,ident,owner=None):
        with self.condition:
            self.registry._expire();self._maintain();row=self.rows.get(ident)
            if not row or row['owner']!=owner:raise ValueError('RPC request is unavailable in this browser workspace.')
            fields=('id','node_id','method','wallet','state','created_utc','started_utc','finished_utc','result','error')
            return deepcopy({key:row[key] for key in fields if key in row})
    def pending(self,owner):
        with self.condition:
            self.registry._expire();self._maintain()
            return any(row['owner']==owner and row['state'] in ('queued','delivered','running') for row in self.rows.values())
    def call(self,node_id,method,params=None,wallet=None,timeout_seconds=30,consume=False,cancel=None,priority=0):
        """Blocking read workflow; optional cancellation and consumed internal results."""
        queued=self.queue(node_id,method,params,wallet,timeout_seconds,priority=priority)
        with self.condition:
            while True:
                row=self.rows.get(queued['id'])
                if cancel is not None and cancel.is_set() and row and row['state'] in ('queued','delivered','running'):
                    self._finish(row,'cancelled',{'code':'cancelled','message':'Dashboard read workflow cancelled.'})
                state=self.status(queued['id'])
                if state['state'] not in ('queued','delivered','running'):
                    if consume:
                        row=self.rows.pop(queued['id']);self.total_bytes-=row['size']
                        self.consumed[queued['id']]={k:row[k] for k in ('token','state','finished')}
                        while len(self.consumed)>4*MAX_REQUESTS:self.consumed.pop(next(iter(self.consumed)))
                    return state
                self.condition.wait(.25)
    def reset(self):
        self.consumed.clear()
        for row in self.rows.values():
            if row['state'] in ('queued','delivered','running'):self._finish(row,'failed',{'code':'profile_reset','message':'The shared profile was replaced or the service stopped.'})
        self.condition.notify_all()
