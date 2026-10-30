"""Exercise all four Dashboard policies and threshold PSBTs with real Core 31."""
from pathlib import Path
import sys,base64,copy,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
# Load only the reusable harness, not core31_psbt_check's top-level integration run.
namespace={'__file__':str(Path(__file__).with_name('core31_psbt_check.py'))};source=Path(__file__).with_name('core31_psbt_check.py').read_text();exec(source[:source.index('from vault_composition import')],namespace)
Regtest,rpc=namespace['Regtest'],namespace['rpc']
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from vendor.embit.networks import NETWORKS
from signed_psbt import decode,serialize,combine,final_transaction
from multisig_psbt import validate_partial,progress
from unsigned_psbt import encode_psbt
r=Regtest();results=[]
try:
 for name in ('fund','key0','key1','key2'):rpc(r,'createwallet',[name])
 call=lambda name,method,params=[]:rpc(r,method,params,name)
 mine=call('fund','getnewaddress');rpc(r,'generatetoaddress',[101,mine]);keys=[]
 for i in range(3):keys.append(next(v['desc'].split('#')[0][5:-1] for v in call('key'+str(i),'listdescriptors',[False])['descriptors'] if v['desc'].startswith('wpkh(') and not v['internal']))
 for mode in ('single','multi','vault','multi-vault'):
  height=rpc(r,'getblockcount')+2 if 'vault' in mode else None;count=3 if 'multi' in mode else 1;threshold=2 if count==3 else 1
  inner=('multi' if height else 'sortedmulti')+'('+str(threshold)+','+','.join(keys[:count])+')' if count>1 else 'pk('+keys[0]+')'
  body='wsh(and_v(v:after('+str(height)+'),'+inner+'))' if height else 'wsh('+inner+')' if count>1 else 'wpkh('+keys[0]+')';desc=body+'#'+checksum(body);d=Descriptor.from_string(desc).derive(0);addr=d.address(NETWORKS['regtest']);monitor='monitor-'+mode
  rpc(r,'createwallet',[monitor,True,True]);call(monitor,'importdescriptors',[[{'desc':desc,'timestamp':'now','range':[0,10],'active':False}]])
  call('fund','sendtoaddress',[addr,.01]);rpc(r,'generatetoaddress',[3,mine]);coin=call(monitor,'listunspent',[1])[0];prev=call(monitor,'gettransaction',[coin['txid']])['hex'];fields={}
  if d.witness_script():fields['05']=d.witness_script().data.hex()
  for key in d.keys:
   if key.origin:fields[('06'+key.get_public_key().sec().hex())]=(key.origin.fingerprint+b''.join(i.to_bytes(4,'little') for i in key.origin.derivation)).hex()
  to=call('fund','getnewaddress');result={'selected':{'wallet':[{'raw_tx':prev,'vout':coin['vout'],'txid':coin['txid'],'value':1000000,'script':coin['scriptPubKey'],'witness':True,'psbt_fields':fields,'height':height or 0}]},'outputs':{to:990000},'changes':{},'total_fee_sats':'10000'};original,tx=encode_psbt(result,{})
  # The local monitor enriches public signing origins; this is the Agent's normal path.
  original=call(monitor,'walletprocesspsbt',[original,False,'ALL',True,False])['psbt'];proofs={}
  for i in range(threshold):
   signed=call('key'+str(i),'walletprocesspsbt',[original,True,'ALL',True,False])['psbt'];proofs,new=validate_partial(original,signed.encode(),[0],proofs);assert new==1
   assert progress(original,proofs,[0])['complete']==(i+1==threshold)
  encoded=combine(original,{'wallet':{'proofs':proofs}},['wallet']);raw=final_transaction(encoded).serialize().hex();accepted=rpc(r,'testmempoolaccept',[[raw]])[0];assert accepted['allowed'],(mode,accepted);rpc(r,'sendrawtransaction',[raw]);results.append({'policy':mode,'required_signatures':threshold,'Core31_accepted':True})
 print(json.dumps(results,indent=2))
finally:r.close()
