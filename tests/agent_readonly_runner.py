"""Use the real Agent transport in a separate import namespace, no local Core/secrets."""
import sys,threading,time,json,signal
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
import node_client,approval_store
approval_store.Store=lambda:SimpleNamespace(read=lambda:{})
stop=threading.Event();signal.signal(signal.SIGTERM,lambda *_:stop.set());client=node_client.NodeClient({},int(sys.argv[2]),stop,lambda *a:None)
def core(ident,method,params,wallet=None,timeout=30):
 if method=='getblockchaininfo':r={'chain':'main','blocks':100,'headers':100,'initialblockdownload':False,'bestblockhash':'a'*64}
 elif method=='getmempoolinfo':r={'size':0,'mempoolminfee':'0.000001','minrelaytxfee':'0.000001'}
 elif method=='estimatesmartfee':r={'feerate':'0.000002','blocks':params[0]}
 elif method=='getblock':r={'size':500000,'weight':1500000}
 elif method=='getrawmempool':r={}
 elif method=='getblockheader':r={'height':100,'time':1700000000,'nTx':1}
 else:raise ValueError(method)
 return {'result':r}
client.core=SimpleNamespace(call=core)
r=client.post('register',{'mac':'02:00:00:00:00:01','hostname':'transport-fixture','rpc':{'version':1,'methods':sorted(node_client.METHODS)}});client.token=r['connection_token'];client.consume(r);print('READY',flush=True)
end=time.monotonic()+30
try:
 while time.monotonic()<end and not client.failed.is_set() and not stop.is_set():client.consume(client.post('poll',{'wait_seconds':1},timeout=3))
 if client.error:raise client.error
finally:stop.set();client.pool.shutdown(wait=True,cancel_futures=True)
