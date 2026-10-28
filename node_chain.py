"""Read-only Bitcoin evidence from connected Core nodes through Wallaby Agent.

No explorer fallback. A missing pruned transaction or spend index is an explicit
coverage error, never evidence that an output is unspent.
"""
import hashlib,json,re,threading
from decimal import Decimal
from vendor.embit.transaction import Transaction
from vendor.embit.script import Script
from vendor.embit.networks import NETWORKS

SAT=Decimal('0.00000001')
_CONTEXT=threading.local();_DEFAULT=None
class NodeDataUnavailable(ValueError):pass

class RegistryRPC:
    def __init__(self,registry,priority=0):self.registry=registry;self.priority=priority
    def nodes(self):return [n for n in self.registry.snapshot()['nodes'] if n['connected'] and n.get('rpc_ready')]
    def call(self,node,method,params=None,wallet=None):
        state=self.registry.rpc.call(node,method,params or [],wallet,timeout_seconds=120,consume=True,priority=self.priority)
        if state['state']!='succeeded':raise NodeDataUnavailable(str((state.get('error') or {}).get('message',state['state'])))
        return state['result']

def configure(rpc,catalog=None,default=False):
    global _DEFAULT
    value=NodeChain(rpc,catalog or {'wallets':[]})
    if default:_DEFAULT=value
    else:_CONTEXT.chain=value
    return value

def current():
    value=getattr(_CONTEXT,'chain',None) or _DEFAULT
    if value is None:raise NodeDataUnavailable('Connect Wallaby Agent and load node wallets before requesting Bitcoin history.')
    return value

def satoshis(value):
    n=Decimal(str(value))*100000000
    if not n.is_finite() or n!=n.to_integral_value() or n<0:raise ValueError('Invalid node Bitcoin amount')
    return int(n)

def address(script):
    try:return Script(bytes.fromhex(script)).address(NETWORKS['main'])
    except ValueError:return ''

class NodeChain:
    def __init__(self,rpc,catalog):
        self.rpc=rpc;self.catalog=catalog;self.raws={};self.metadata={};self.txs={};self.headers={};self.spends={};self.cursor=0;self.lock=threading.RLock();self.targets=None;self.hints={};self.unavailable=set();self.history_complete=bool(catalog.get('wallets')) and all('node_history' in w for w in catalog.get('wallets',[]))
        for w in catalog.get('wallets',[]):
            for row in w.get('node_history',[]):
                txid=row.get('txid')
                if txid:self.hints.setdefault(txid,[]).extend(w.get('node_sources',[]));self.metadata.setdefault(txid,{}).update({k:v for k,v in row.items() if k in ('blockhash','blockheight','blocktime','confirmations')})
    def nodes(self):
        nodes=self.rpc.nodes()
        if not nodes:raise NodeDataUnavailable('No connected, conflict-free Bitcoin node is available.')
        if any(n.get('network','main') not in ('main','mainnet') for n in nodes):
            # Agent metadata varies by protocol; Core's chain is also checked in scans.
            nodes=[n for n in nodes if n.get('network','main') in ('main','mainnet')]
        if not nodes:raise NodeDataUnavailable('A Bitcoin mainnet node is required.')
        return nodes
    def call(self,method,params=None,wallet=None,node_ids=None):
        nodes=self.nodes()
        if node_ids:nodes=[n for n in nodes if n['id'] in node_ids]
        with self.lock:
            at=self.cursor%max(1,len(nodes));self.cursor+=1
        errors=[]
        for n in nodes[at:]+nodes[:at]:
            try:return self.rpc.call(n['id'],method,params or [],wallet)
            except (ValueError,OSError) as exc:errors.append(n.get('hostname',n['id'])+': '+str(exc))
        raise NodeDataUnavailable(method+' unavailable from connected nodes. '+('; '.join(errors) or 'The wallet replicas are offline.'))
    def replicas(self):
        if self.targets is None:
            connected={n['id'] for n in self.nodes()};targets=[]
            for w in self.catalog.get('wallets',[]):
                for s in w.get('node_sources',[]):
                    key=(s['node_id'],s['wallet_name'])
                    if key[0] in connected and key not in targets:targets.append(key)
            # Includes newly loaded wallets not in the saved catalog.
            for n in self.nodes():
                try:names=self.rpc.call(n['id'],'listwallets',[])
                except (ValueError,OSError):continue
                for name in names:
                    if (n['id'],name) not in targets:targets.append((n['id'],name))
            self.targets=targets
        return self.targets
    def raw(self,txid):
        if not re.fullmatch('[0-9a-f]{64}',str(txid)):raise ValueError('Invalid transaction ID')
        if txid in self.raws:return self.raws[txid]
        if txid in self.unavailable:raise NodeDataUnavailable('Historical transaction unavailable from connected nodes: '+txid)
        errors=[];raw=None;meta=self.metadata.get(txid,{})
        params=[txid,2]+([meta['blockhash']] if meta.get('blockhash') else [])
        try:
            reply=self.call('getrawtransaction',params);raw=reply['hex'];self.metadata[txid]=dict(reply)
        except (ValueError,OSError,KeyError) as exc:errors.append(str(exc))
        if raw is None:
            hinted=list(dict.fromkeys((r['node_id'],r['wallet_name']) for r in self.hints.get(txid,[])))
            targets=hinted or ([] if self.history_complete else self.replicas())
            at=self.cursor%max(1,len(targets));self.cursor+=1
            for node,name in targets[at:]+targets[:at]:
                try:
                    reply=self.rpc.call(node,'gettransaction',[txid,True,True],name)
                    if int(reply.get('confirmations',0))<0:raise NodeDataUnavailable('Transaction is conflicted: '+txid)
                    raw=reply['hex'];self.metadata[txid]=dict(reply);break
                except (ValueError,OSError,KeyError) as exc:errors.append(str(exc))
        if raw is None:
            self.unavailable.add(txid)
            raise NodeDataUnavailable('Bitcoin transaction '+txid+' is unavailable from every connected node. A pruned node cannot supply arbitrary old transactions unless retained in its wallet. Load/rescan the relevant watch-only wallet or use a node with the required indexed history. No public explorer was queried. '+(errors[0] if errors else ''))
        tx=Transaction.parse(bytes.fromhex(raw))
        if tx.txid().hex()!=txid:raise ValueError('Node transaction does not match its TXID')
        self.raws[txid]=raw
        for i,v in enumerate(tx.vin):
            if v.txid!=b'\0'*32:self.spends[(v.txid.hex(),v.vout)]={'spent':True,'txid':txid,'vin':i}
        return raw
    def transaction(self,txid):return Transaction.parse(bytes.fromhex(self.raw(txid)))
    def header(self,height=None,blockhash=None):
        if blockhash is None:blockhash=self.call('getblockhash',[height])
        if blockhash not in self.headers:
            row=self.call('getblockheader',[blockhash,True])
            if row.get('hash')!=blockhash or type(row.get('time')) is not int or type(row.get('height')) is not int:raise ValueError('Invalid node block header')
            if int(row.get('confirmations',0))<0:raise NodeDataUnavailable('History changed during the run; refresh node wallets.')
            self.headers[blockhash]=row
        return self.headers[blockhash]
    def tx(self,txid):
        if txid in self.txs:return self.txs[txid]
        tx=self.transaction(txid);meta=self.metadata.get(txid,{});status={'confirmed':bool(meta.get('blockhash')) and int(meta.get('confirmations',1))>0}
        if status['confirmed']:
            h=self.header(blockhash=meta['blockhash']);status.update(block_hash=h['hash'],block_height=h['height'],block_time=h['time'])
        outputs=[{'value':o.value,'scriptpubkey':o.script_pubkey.data.hex(),'scriptpubkey_address':address(o.script_pubkey.data.hex())} for o in tx.vout]
        inputs=[];missing=False
        decoded=meta.get('decoded',meta);provided=decoded.get('vin',[]) if isinstance(decoded,dict) else []
        for i,v in enumerate(tx.vin):
            if v.txid==b'\0'*32:inputs.append({'is_coinbase':True});continue
            row={'txid':v.txid.hex(),'vout':v.vout};prev=None
            try:
                parent=self.transaction(v.txid.hex());o=parent.vout[v.vout];prev={'value':o.value,'scriptpubkey':o.script_pubkey.data.hex(),'scriptpubkey_address':address(o.script_pubkey.data.hex())}
            except NodeDataUnavailable:
                p=provided[i].get('prevout') if i<len(provided) else None
                if p and 'value' in p:
                    script=p['scriptPubKey']['hex'];prev={'value':satoshis(p['value']),'scriptpubkey':script,'scriptpubkey_address':address(script)}
            if prev:row['prevout']=prev
            else:missing=True
            inputs.append(row)
        fee=None
        if not missing and not any(v.get('is_coinbase') for v in inputs):fee=sum(v['prevout']['value'] for v in inputs)-sum(o['value'] for o in outputs)
        if fee is not None and fee<0:raise ValueError('Node transaction inputs do not reconcile')
        result={'txid':txid,'status':status,'vin':inputs,'vout':outputs,'source':'Connected Bitcoin Core nodes'}
        if fee is not None:result['fee']=fee
        self.txs[txid]=result;return result
    def seed_wallet_history(self):
        for w in self.catalog.get('wallets',[]):
            for row in w.get('node_history',[]):
                if not row.get('abandoned') and int(row.get('confirmations',0))>=0:self.raw(row['txid'])
    def outspend(self,txid,index):
        key=(txid,index)
        if key in self.spends:
            spend=dict(self.spends[key]);tx=self.tx(spend['txid'])
            if tx['status']['confirmed']:return spend
        live=self.call('gettxout',[txid,index,True])
        if live is not None:return {'spent':False}
        self.seed_wallet_history()
        if key in self.spends:return dict(self.spends[key])
        # Core has no arbitrary historical spent-output index. Never turn null
        # gettxout into an assertion of "unspent" or infer a recipient.
        raise NodeDataUnavailable('The node reports '+txid+':'+str(index)+' is spent or unavailable, but its spending transaction is absent from loaded wallet history. Load the historical watch-only wallet and rescan before retrying. No complete ledger can be established from gettxout alone.')
    def live_utxos(self,addr):
        # One scan covers exactly the supplied script, including pruned nodes.
        result=self.call('scantxoutset',['start',[{'desc':'addr('+addr+')'}]])
        if not result.get('success'):raise NodeDataUnavailable('Node UTXO scan did not complete')
        rows=[{'txid':r['txid'],'vout':r['vout'],'value':satoshis(r['amount']),'status':{'confirmed':True,'block_height':r['height']}} for r in result['unspents']]
        return rows,'Connected Core UTXO set (confirmed outputs)'
    def wallet_unspent(self,wallet):
        sources=wallet.get('node_sources',[]);nodes={n['id'] for n in self.nodes()};errors=[]
        with self.lock:at=self.cursor%max(1,len(sources));self.cursor+=1
        for s in sources[at:]+sources[:at]:
            if s['node_id'] not in nodes:continue
            try:return self.rpc.call(s['node_id'],'listunspent',[1,9999999,[],True],s['wallet_name'])
            except (ValueError,OSError) as exc:errors.append(str(exc))
        raise NodeDataUnavailable('No connected replica can provide current UTXOs for '+wallet['name']+'. '+'; '.join(errors))
