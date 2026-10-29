"""SSH-carried agent presence. Claimed MAC/hostname are identity hints, not credentials."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,re,secrets,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
import socket
from storage import storage_lock
from agent_rpc import RPCBroker,capabilities,METHODS,MAX_RESULT_BYTES
from decimal import Decimal

DEFAULT_AGENT_PORT=56443
HEARTBEAT_SECONDS=15
DISCONNECT_SECONDS=45
MAX_CONNECTIONS=256

def utc():return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def identity(mac,hostname):
    if not isinstance(mac,str) or not re.fullmatch(r'[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}',mac):raise ValueError('Provide a six-byte MAC address.')
    mac=mac.lower().replace('-',':')
    if mac in ('00:00:00:00:00:00','ff:ff:ff:ff:ff:ff') or int(mac[:2],16)&1:raise ValueError('Provide a unicast device MAC address.')
    if not isinstance(hostname,str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?',hostname):raise ValueError('Provide a valid client hostname.')
    return mac,hostname.rstrip('.').lower()

class NodeRegistry:
    def __init__(self,config,clock=time.monotonic):
        self.config=Path(config);self.path=self.config.with_name('data')/'nodes.json';self.clock=clock
        self.lock=threading.RLock();self.connections={};self.warnings={};self.read_by={};self.rpc=RPCBroker(self)
        from node_health import Health
        from agent_hub import AgentHub
        self.health=Health(self);self.hub=AgentHub(self)
        from vault_monitor import VaultMonitor
        self.vaults=VaultMonitor(self)
    @staticmethod
    def validate(data):
        if not isinstance(data,dict) or set(data)!={'version','nodes'} or data['version']!=1 or not isinstance(data['nodes'],list) or len(data['nodes'])>10000:raise ValueError('Invalid node registry.')
        ids=set();macs=set()
        for row in data['nodes']:
            if not isinstance(row,dict) or set(row)!={'id','mac','hostname','first_seen_utc','last_seen_utc'}:raise ValueError('Invalid node record.')
            if not re.fullmatch('[0-9a-f]{32}',str(row['id'])) or row['id'] in ids:raise ValueError('Invalid node ID.')
            mac,host=identity(row['mac'],row['hostname'])
            if mac!=row['mac'] or host!=row['hostname'] or mac in macs:raise ValueError('Invalid node identity.')
            ids.add(row['id']);macs.add(mac)
            for key in ('first_seen_utc','last_seen_utc'):
                try:datetime.strptime(row[key],'%Y-%m-%dT%H:%M:%SZ')
                except (TypeError,ValueError):raise ValueError('Invalid node timestamp.') from None
        return data
    def _read(self):
        return self.validate(json.loads(self.path.read_text(encoding='utf-8'))) if self.path.exists() else {'version':1,'nodes':[]}
    def _write(self,data):
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.chmod(0o600);temp.replace(self.path)
    def reset(self):
        # Live connections never survive clearing/loading a profile.
        with self.lock:self.rpc.reset();self.connections.clear();self.warnings.clear();self.read_by.clear();self.health.values.clear();self.hub.reset_live();self.vaults.value={'wallets':[],'loading':True};self.vaults.last=0
    def _expire(self):
        now=self.clock()
        for token in list(self.connections):
            if now-self.connections[token]['seen']>=DISCONNECT_SECONDS:
                self.rpc.disconnected(token);del self.connections[token]
    def register(self,mac,hostname,rpc=None):
        mac,hostname=identity(mac,hostname);methods=capabilities(rpc)
        with self.lock,storage_lock(self.config):
            self._expire()
            if len(self.connections)>=MAX_CONNECTIONS:raise ValueError('Agent connection limit reached.')
            data=self._read();node=next((n for n in data['nodes'] if n['mac']==mac),None);stamp=utc()
            if node is None:
                if len(data['nodes'])>=10000:raise ValueError('Node registry is full.')
                node={'id':uuid.uuid4().hex,'mac':mac,'hostname':hostname,'first_seen_utc':stamp,'last_seen_utc':stamp};data['nodes'].append(node)
            elif not any(c['mac']==mac for c in self.connections.values()):node['hostname']=hostname
            node['last_seen_utc']=stamp;self._write(data)
            token=secrets.token_urlsafe(32);self.connections[token]={'node_id':node['id'],'mac':mac,'hostname':hostname,'seen':self.clock(),'rpc_methods':methods,'token':token}
            self._conflicts()
            return {'protocol':'wallaby-agent','version':1,'connection_token':token,'node_id':node['id'],'heartbeat_seconds':HEARTBEAT_SECONDS,'disconnect_after_seconds':DISCONNECT_SECONDS,'capabilities':['presence']+(['rpc'] if methods else []),'rpc_methods':methods,'commands':[]}
    def heartbeat(self,token,disconnect=False):
        with self.lock,storage_lock(self.config):
            self._expire();connection=self.connections.get(token)
            if not connection:raise PermissionError('Connection expired. Register again.')
            if disconnect:self.rpc.disconnected(token);del self.connections[token]
            else:
                connection['seen']=self.clock();data=self._read()
                for node in data['nodes']:
                    if node['id']==connection['node_id']:node['last_seen_utc']=utc()
                self._write(data)
            self._conflicts()
            return {'ok':True,'commands':[] if disconnect else self.rpc._commands(token)}
    def _conflicts(self):
        groups={}
        for c in self.connections.values():
            for kind in ('mac','hostname'):groups.setdefault((kind,c[kind]),[]).append(c)
        active={}
        for (kind,value),rows in sorted(groups.items()):
            if len(rows)<2:continue
            key='node-conflict-'+hashlib.sha256((kind+':'+value).encode()).hexdigest()[:24]
            prior=self.warnings.get(key)
            active[key]=prior or {'id':key,'kind':'node_conflict','severity':'red','dismissible':False,'target':'nodes','created_utc':utc(),'text':'Warning: Conflicting node '+('MAC address' if kind=='mac' else 'hostname')+' ('+value+'). This could either be spoofing or something is misconfigured.'}
        self.warnings=active
        self.rpc.reconcile()
        active_ids=set(active)
        for user in self.read_by:self.read_by[user].intersection_update(active_ids)
    def snapshot(self):
        with self.lock,storage_lock(self.config):
            self._expire();self._conflicts();data=self._read();rows=[]
            for n in data['nodes']:
                claims=[c for c in self.connections.values() if c['node_id']==n['id']]
                rows.append(dict(n,blockchain=self.health.status(claims[0]) if len(claims)==1 else {},agent=self.hub.node_summary(n['id']),connected=bool(claims),connection_count=len(claims),reported_hostnames=sorted({c['hostname'] for c in claims}),rpc_ready=len(claims)==1 and bool(claims[0].get('rpc_methods')) and self.rpc._unique(claims[0]),rpc_methods=claims[0].get('rpc_methods',[]) if len(claims)==1 and self.rpc._unique(claims[0]) else []))
            self.health.refresh([c for c in self.connections.values() if c.get('rpc_methods') and self.rpc._unique(c)])
            return {'nodes':sorted(rows,key=lambda r:(r['hostname'],r['mac'])),'warnings':[dict(w) for w in self.warnings.values()]}
    def notifications(self,user,read=False):
        with self.lock:
            self._expire();self._conflicts();seen=self.read_by.setdefault(user,set())
            if read:seen.update(self.warnings)
            return [dict(n,read=n['id'] in seen) for n in self.warnings.values()]

def create_agent_server(registry,port=DEFAULT_AGENT_PORT,gate=None,ipv6=True,ipv4=True):
    from contextlib import nullcontext
    class Handler(BaseHTTPRequestHandler):
        def setup(self):super().setup();self.connection.settimeout(10)
        def log_message(self,*args):pass
        def reply(self,data,status=200):
            body=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
        def safe(self):
            from launch_wallaby_bitcoin_dashboard import loopback_authority
            return len(self.headers.get_all('Host',[]))==1 and loopback_authority(self.headers['Host']) and not self.headers.get_all('Origin')
        def do_GET(self):
            if not self.safe():return self.reply({'error':'SSH loopback agent endpoint required.'},403)
            if self.path!='/agent/v1/health':return self.reply({'error':'Not found'},404)
            return self.reply({'protocol':'wallaby-agent','version':1,'capabilities':['presence','rpc'],'rpc_methods':sorted(METHODS)})
        def do_POST(self):
            if not self.safe() or self.headers.get('X-Wallaby-Agent')!='1':return self.reply({'error':'An agent protocol request is required.'},403)
            if self.headers.get_content_type()!='application/json' or self.headers.get('Transfer-Encoding'):return self.reply({'error':'Send a length-delimited JSON request.'},400)
            try:
                sizes=self.headers.get_all('Content-Length',[])
                if len(sizes)!=1:raise ValueError('Provide one Content-Length.')
                length=int(sizes[0])
                limit=20*1024*1024 if self.path=='/agent/v2/exchange' else MAX_RESULT_BYTES+4096 if self.path=='/agent/v1/result' else 4096
                if not 0<length<=limit:raise ValueError('Agent request exceeds its size limit.')
                raw=self.rfile.read(length)
                if len(raw)!=length:raise ValueError('Incomplete agent request.')
                def unique(pairs):
                    data={}
                    for k,v in pairs:
                        if k in data:raise ValueError('Duplicate JSON field.')
                        data[k]=v
                    return data
                data=json.loads(raw,object_pairs_hook=unique,parse_float=float if self.path=='/agent/v2/exchange' else Decimal,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Non-finite JSON number.')))
                if not isinstance(data,dict):raise ValueError('Expected an object.')
                with gate.access() if gate else nullcontext():
                    if self.path=='/agent/v1/register':
                        if not {'mac','hostname'}<=set(data) or set(data)-{'mac','hostname','rpc'}:raise ValueError('Expected mac, hostname and optional RPC capabilities.')
                        return self.reply(registry.register(data['mac'],data['hostname'],data.get('rpc')))
                    if self.path not in ('/agent/v1/heartbeat','/agent/v1/disconnect','/agent/v1/poll','/agent/v1/ack','/agent/v1/result','/agent/v2/exchange'):return self.reply({'error':'Not found'},404)
                    auth=self.headers.get('Authorization','')
                    if not auth.startswith('Bearer '):raise PermissionError('Connection token required.')
                    token=auth[7:]
                    if self.path=='/agent/v2/exchange':return self.reply(registry.hub.exchange(token,data))
                    if self.path=='/agent/v1/poll':
                        if set(data)-{'wait_seconds'}:raise ValueError('Unexpected poll fields.')
                        # Do not hold the profile gate during a long poll: reset
                        # invalidates its token and wakes it immediately.
                        pass
                    elif self.path=='/agent/v1/ack':
                        if set(data)!={'id'} or not isinstance(data['id'],str):raise ValueError('Expected an RPC request ID.')
                        return self.reply(registry.rpc.ack(token,data['id']))
                    elif self.path=='/agent/v1/result':
                        if set(data) not in ({'id','result'},{'id','error'}) or not isinstance(data['id'],str):raise ValueError('Expected id and exactly one of result/error.')
                        if 'error' in data and data['error'] is None:raise ValueError('An error response must describe the error.')
                        return self.reply(registry.rpc.result(token,data['id'],result=data.get('result'),error=data.get('error')))
                    else:
                        if data:raise ValueError('Heartbeat/disconnect body must be {}.')
                        return self.reply(registry.heartbeat(token,self.path.endswith('/disconnect')))
                return self.reply(registry.rpc.poll(token,data.get('wait_seconds',20)))
            except PermissionError as exc:self.reply({'error':str(exc)},401)
            except (ValueError,TypeError,RecursionError,UnicodeError) as exc:self.reply({'error':str(exc)},400)
    class Server(ThreadingHTTPServer):daemon_threads=True;allow_reuse_address=True
    class IPv6Server(Server):
        address_family=socket.AF_INET6
        def server_bind(self):self.socket.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1);super().server_bind()
    listeners=[]
    try:
        if ipv6:
            try:listeners.append(IPv6Server(('::1',port),Handler))
            except OSError:
                if not ipv4:raise
        if ipv4:listeners.append(Server(('127.0.0.1',listeners[0].server_port if listeners else port),Handler))
        if not listeners:raise OSError('No agent listeners requested.')
    except BaseException:
        for listener in listeners:listener.server_close()
        raise
    class Group:
        server_port=listeners[0].server_port
        has_ipv6=any(s.address_family==socket.AF_INET6 for s in listeners)
        def __init__(self):self.threads=[]
        def start(self):
            for listener in listeners:
                t=threading.Thread(target=listener.serve_forever,daemon=True);t.start();self.threads.append(t)
        def close(self):
            for listener,t in zip(listeners,self.threads):listener.shutdown();t.join(3)
            for listener in listeners:listener.server_close()
            registry.reset()
    return Group()
