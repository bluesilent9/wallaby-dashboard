"""Independent Agent20 namespace: real page formatter and HTTP transport, fake Core."""
import sys,threading,time,json,signal
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
import node_client,approval_store
from mempool_snapshot import Snapshot,METHOD
approval_store.Store=lambda:SimpleNamespace(read=lambda:{})
raw={f'{i:064x}':{'vsize':150,'fees':{'modified':'0.00001234'},'depends':[f'{i-1:064x}'] if i%4 else [],'unused':'z'*512} for i in range(38000)}
assert len(json.dumps(raw).encode())>16*1024*1024
pages=Snapshot();stop=threading.Event();signal.signal(signal.SIGTERM,lambda *_:stop.set());client=node_client.NodeClient({},int(sys.argv[2]),stop,lambda *a:None)
def core(ident,method,params,wallet=None,timeout=30):
 if method==METHOD:r=pages.call(params,lambda:raw)
 elif method=='getblockchaininfo':r={'chain':'main','blocks':100,'headers':100,'initialblockdownload':False,'bestblockhash':'a'*64}
 elif method=='getmempoolinfo':r={'size':38000,'mempoolminfee':'0.000001','minrelaytxfee':'0.000001'}
 elif method=='estimatesmartfee':r={'feerate':'0.000002','blocks':params[0]}
 elif method=='getblock':r={'size':500000,'weight':1500000}
 elif method=='getblockheader':r={'height':100,'time':1700000000,'nTx':1}
 else:raise ValueError('Unexpected RPC '+method)
 return {'result':r}
client.core=SimpleNamespace(call=core)
r=client.post('register',{'mac':'02:00:00:00:00:21','hostname':'paged-transport-fixture','rpc':{'version':1,'methods':sorted(node_client.METHODS)}});client.token=r['connection_token'];client.consume(r);print('READY',flush=True)
end=time.monotonic()+90
try:
 while time.monotonic()<end and not client.failed.is_set() and not stop.is_set():client.consume(client.post('poll',{'wait_seconds':1},timeout=3))
 if client.error:raise client.error
finally:stop.set();client.pool.shutdown(wait=True,cancel_futures=True)
