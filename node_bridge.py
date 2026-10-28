"""Private JSON bridge from isolated calculation workers to the Agent broker."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,secrets,threading,urllib.request

_ALLOWED=set('getblockchaininfo getblockcount getbestblockhash getblockhash getblockheader getblock getmempoolinfo getrawmempool getmempoolentry getrawtransaction gettxout getindexinfo scantxoutset listwallets gettransaction listsinceblock getaddressinfo listunspent estimatesmartfee'.split())

class NodeBridge:
 def __init__(self,registry,cancelled=lambda:False):
    self.registry=registry;self.key=secrets.token_urlsafe(32)
    bridge=self
    class Cancel:
     def is_set(self):return cancelled()
    self.cancel=Cancel()
    class Handler(BaseHTTPRequestHandler):
     def log_message(self,*args):pass
     def do_POST(self):
      if self.headers.get('Authorization')!='Bearer '+bridge.key:self.send_error(403);return
      try:
       size=int(self.headers.get('Content-Length','0'))
       if not 0<size<4*1024*1024:raise ValueError('Invalid node read request size')
       body=json.loads(self.rfile.read(size));method=body['method']
       if method=='nodes':value=[n for n in registry.snapshot()['nodes'] if n['connected']]
       else:
        if method not in _ALLOWED:raise ValueError('Only public read RPCs are enabled for calculation workers')
        state=registry.rpc.call(body['node_id'],method,body.get('params',[]),body.get('wallet'),timeout_seconds=120,consume=True,cancel=bridge.cancel)
        if state['state']!='succeeded':raise ValueError(str((state.get('error') or {}).get('message',state['state'])))
        value=state['result']
       result={'result':value}
      except Exception as exc:result={'error':str(exc)}
      raw=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
    threading.Thread(target=self.server.serve_forever,daemon=True).start()
 def settings(self):return {'url':'http://127.0.0.1:'+str(self.server.server_port),'key':self.key}
 def close(self):self.server.shutdown();self.server.server_close()

class WorkerRPC:
 def __init__(self,settings):self.url=settings['url'];self.key=settings['key']
 def request(self,body):
    req=urllib.request.Request(self.url,json.dumps(body).encode(),headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=130) as response:result=json.load(response)
    if 'error' in result:raise ValueError(result['error'])
    return result['result']
 def nodes(self):return self.request({'method':'nodes'})
 def call(self,node,method,params=None,wallet=None):return self.request({'node_id':node,'method':method,'params':params or [],'wallet':wallet})
