from pathlib import Path
import sys,base64,json
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
import tempfile,subprocess,socket,time,urllib.request,os,shutil
class Regtest:
 def __init__(self):
  self.root=tempfile.TemporaryDirectory(prefix='dashboard-regtest-');p=Path(self.root.name)
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
  self.core=self;self.log=(p/'process.log').open('w');self.process=subprocess.Popen([os.environ.get('WALLABY_TEST_BITCOIND') or shutil.which('bitcoind') or 'bitcoind','-regtest','-server','-listen=0','-connect=0','-dnsseed=0','-discover=0','-txindex=1','-fallbackfee=0.00002','-datadir='+str(p),'-rpcport='+str(self.port)],stdout=self.log,stderr=subprocess.STDOUT)
  for _ in range(100):
   try:rpc(self,'getblockchaininfo');break
   except Exception:time.sleep(.1)
  else:raise RuntimeError('Isolated node did not start.')
 def close(self):
  try:rpc(self,'stop')
  except Exception:pass
  self.process.wait(timeout=20);self.log.close();self.root.cleanup()
def rpc(core,method,params=None,wallet=None):
 from urllib.parse import quote
 cookie=(Path(core.root.name)/'regtest/.cookie').read_text();auth=base64.b64encode(cookie.encode()).decode();url='http://127.0.0.1:'+str(core.port)+('/wallet/'+quote(wallet,safe='') if wallet else '/')
 req=urllib.request.Request(url,json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params or []}).encode(),headers={'Authorization':'Basic '+auth,'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=60) as response:result=json.load(response)
 if result.get('error'):raise RuntimeError(str(result['error']))
 return result['result']
from vault_composition import combine_unsigned
from signed_psbt import validate_upload,combine,final_transaction
from vendor.embit.descriptor import Descriptor
r=Regtest()
try:
 for name in ('one','two'):rpc(r.core,'createwallet',[name])
 def call(name,method,params=[]):return rpc(r.core,method,params,name)
 a=call('one','getnewaddress');rpc(r.core,'generatetoaddress',[101,a]);b=call('two','getnewaddress');call('one','sendtoaddress',[b,1]);rpc(r.core,'generatetoaddress',[1,a])
 inputs=[call(name,'listunspent',[1])[0] for name in ('one','two')];pub=call('one','getaddressinfo',[a])['pubkey'];height=rpc(r.core,'getblockcount')+20;vault=Descriptor.from_string('wsh(and_v(v:pk('+pub+'),after('+str(height)+')))').script_pubkey().address(__import__('vendor.embit.networks',fromlist=['NETWORKS']).NETWORKS['regtest'])
 def component(name,coin,to):
  text=rpc(r.core,'createpsbt',[[{'txid':coin['txid'],'vout':coin['vout']}],[{to:float(coin['amount'])-.00001}]])
  return call(name,'walletprocesspsbt',[text,False,'ALL',True,False])['psbt']
 p=component('one',inputs[0],vault);q=component('two',inputs[1],call('two','getnewaddress'))
 original,tx,fee=combine_unsigned(p,q);parts={}
 for i,name in enumerate(('one','two')):
  signed=call(name,'walletprocesspsbt',[original,True,'ALL',True,False]);assert not signed['complete'];parts[name]={'proofs':validate_upload(original,signed['psbt'].encode(),[i])}
 encoded=combine(original,parts,['one','two']);raw=final_transaction(encoded).serialize().hex();accepted=rpc(r.core,'testmempoolaccept',[[raw]])[0];assert accepted['allowed'],accepted
 txid=rpc(r.core,'sendrawtransaction',[raw]);assert txid==tx.txid().hex()
 print(json.dumps({'core':rpc(r.core,'getnetworkinfo')['version'],'chain':'isolated regtest','inputs':2,'outputs':2,'fee_sats':fee,'both_partial_signatures_verified':True,'core_mempool_acceptance':True,'test_coin_broadcast':txid},indent=2))
finally:r.close()
