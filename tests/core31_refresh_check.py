"""Real Core 31: two consecutive renewals, exact scripts, expiry and threshold signing.

Uses an isolated regtest chain (test coins), never production nodes or wallets.
"""
from pathlib import Path
import sys,base64,copy,json,os,subprocess,tempfile,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
ns={'__file__':str(Path(__file__).with_name('core31_psbt_check.py'))};src=Path(ns['__file__']).read_text();exec(src[:src.index('from vault_composition import')],ns);Regtest,rpc=ns['Regtest'],ns['rpc']
from builder_destinations import public_rows
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from vendor.embit.networks import NETWORKS
from unsigned_psbt import encode_psbt
from signed_psbt import combine,final_transaction
from multisig_psbt import validate_partial,progress,witness_policy
from setup_signatures import verify
from agent_hub import digest
agent=Path(os.environ['WALLABY_AGENT_SOURCE']);r=Regtest();results=[]

def dashboard_state(monitors,history,expected_sats,expected_state,outside_sats=0):
 from types import SimpleNamespace
 from unittest.mock import patch
 from node_chain import NodeChain
 from vault_monitor import VaultMonitor
 inventory=[{'name':n,'raw':rpc(r,'listdescriptors',[False],n),'private_keys_enabled':False,'signing_only':False} for n in monitors]
 logical={'id':'fixture-wallet','name':'Logical test vault','raw':{'wallet_name':'Logical test vault','descriptors':history},'node_sources':[{'node_id':'node','wallet_name':n} for n in monitors]}
 node={'id':'node','hostname':'Core 31 test node','connected':True,'rpc_ready':True,'blockchain':{'state':'ready','blocks':rpc(r,'getblockcount')}}
 hub=SimpleNamespace(config=Path(r.root.name)/'dashboard.cfg',inventories={'node':{'complete':True,'wallets':inventory}},permissions=SimpleNamespace(permits=lambda *a:True),read=lambda:{'vaults':{},'nodes':{},'requests':{}},rpc=lambda n,m,p=None,w=None:rpc(r,m,p,w));registry=SimpleNamespace(hub=hub,snapshot=lambda:{'nodes':[node]});hub.registry=registry
 class RPC:
  def __init__(self):self.registry=registry
  def nodes(self):return [node]
  def call(self,n,m,p=None,w=None):return rpc(r,m,p,w)
 coins=NodeChain(RPC(),{'wallets':[logical]}).wallet_unspent(logical);assert sum(int(round(c['amount']*100000000)) for c in coins)==expected_sats
 with patch('vault_monitor.DescriptorStore.read',return_value={'wallets':[logical]}):
  status=VaultMonitor(registry).compute()['wallets'][0]
 assert status['vault_sats']==expected_sats-outside_sats and status['outside_sats']==outside_sats and status['state']==expected_state,status
 from fee_planner import live_coins
 from preferences import resource_id
 logical['records']=[];chain={'wallets':[logical],'transactions':{},'scanned_utc':str(time.time())};session=SimpleNamespace(preferences_path=hub.config,node_registry=registry);uid=resource_id('descriptor',logical['id'])
 with patch('fee_planner.DescriptorStore.read',return_value={'wallets':[logical]}),patch('fee_planner.saved_history',return_value=chain),patch('node_chain.RegistryRPC',return_value=RPC()):
  available=live_coins(session,{'wallets':[]},{uid},force=True).availability[uid]
 locked_sats=expected_sats-outside_sats if status['height']>node['blockchain']['blocks'] else 0
 assert int(available['locked_sats'])==locked_sats,available
 assert int(available['available_sats'])==expected_sats-locked_sats,available
 return status

# Execute Agent code in its own interpreter to preserve its import boundaries.
child=r'''import sys,json,urllib.request,base64,copy
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from approval_store import Store,digest
from policy_refresh import Refresh
p=json.loads(Path(sys.argv[2]).read_text());port=int(sys.argv[3]);cookie=Path(sys.argv[4]).read_text()
class Core:
 def call(self,ident,method,params,wallet,timeout=30):
  from urllib.parse import quote
  req=urllib.request.Request('http://127.0.0.1:'+str(port)+('/wallet/'+quote(wallet,safe='') if wallet else '/'),json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Authorization':'Basic '+base64.b64encode(cookie.encode()).decode(),'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=timeout) as response:return json.load(response)
store=Store(Path(sys.argv[2]).parent/'agent-state');key=digest(p)
with store.edit() as s:s['pending'][key]={'proposal':p,'state':'pending'}
if p['kind']=='sign_psbt_partial':
 from wallet_ops import WalletOps
 facts=WalletOps(Core(),store).prepare_request(key);print(json.dumps({'review':facts}));sys.exit(0)
ops=Refresh(Core(),store,SimpleNamespace(loaded={}));facts=ops.review(key);ops.approve(key)
items=store.read()['outbox'].values();proof=next(x['payload'] for x in reversed(list(items)) if x['kind']=='wallet_policy_refresh_result' and x['payload']['message']['request_id']==p['request_id'])
print(json.dumps({'review':facts,'receipt':proof}))
'''
try:
 for name in ('fund','key0','key1','key2'):rpc(r,'createwallet',[name])
 call=lambda name,method,params=[]:rpc(r,method,params,name)
 mine=call('fund','getnewaddress');rpc(r,'generatetoaddress',[101,mine]);key_rows=[]
 for i in range(3):key_rows.append([x for x in call('key'+str(i),'listdescriptors',[False])['descriptors'] if x['desc'].startswith('wpkh(')])
 for count in (1,3):
  threshold=2 if count==3 else 1;name='vault-'+str(count);height=rpc(r,'getblockcount')+4;rows=[]
  for internal in (False,True):
   keys=[next(x for x in key_rows[i] if x['internal']==internal)['desc'].split('#')[0][5:-1] for i in range(count)];spend='pk('+keys[0]+')' if count==1 else 'multi(2,'+','.join(keys)+')';body='wsh(and_v(v:after('+str(height)+'),'+spend+'))';rows.append({'desc':body+'#'+checksum(body),'range':[0,999],'active':False,'internal':internal,'timestamp':0,'next_index':0})
  rpc(r,'createwallet',[name,True,True]);call(name,'importdescriptors',[[{k:v for k,v in row.items() if k!='next_index'}|{'timestamp':'now'} for row in rows]])
  monitors=[name];original_desc=rows[0]['desc'];d=Descriptor.from_string(original_desc).derive(0);address=d.address(NETWORKS['regtest']);fund=call('fund','sendtoaddress',[address,.0001]);rpc(r,'generatetoaddress',[1,mine]);history=copy.deepcopy(rows)
  for cycle in range(3):
   extra_coins=[]
   if cycle==2 and count==3:
    large_tx=call('fund','sendtoaddress',[address,.0099]);rpc(r,'generatetoaddress',[1,mine]);extra_coins=[c for c in call(name,'listunspent',[1]) if c['txid']==large_tx];assert len(extra_coins)==1
   coin=min((c for c in call(name,'listunspent',[1]) if c['address']==address),key=lambda c:c['amount']);assert int(coin['amount']*100000000)>0
   current=rpc(r,'getblockcount');assert current<height,(current,height)
   observed=sum(int(round(c['amount']*100000000)) for c in call(name,'listunspent',[1]));dashboard_state(monitors,history,observed,'green')
   prev=call(name,'gettransaction',[coin['txid']])['hex'];newheight=height+5
   if cycle<2:
    fresh=public_rows({'raw':{'wallet_name':name,'descriptors':history}},newheight);newd=Descriptor.from_string(fresh[0]['desc']).derive(cycle+1);to=newd.address(NETWORKS['regtest'])
    proposal={'kind':'wallet_policy_refresh','version':1,'request_id':digest({'count':count,'cycle':cycle}),'chain':'regtest','wallet':name,'previous_descriptors':[x['desc'] for x in rows],'receive_descriptor':fresh[0]['desc'],'change_descriptor':fresh[1]['desc'],'timestamp':int(time.time()),'expires_at':int(time.time())+86390}
    path=Path(r.root.name)/'proposal.json';path.write_text(json.dumps(proposal));reply=subprocess.run([sys.executable,'-c',child,str(agent),str(path),str(r.port),str(Path(r.root.name)/'regtest/.cookie')],capture_output=True,text=True,timeout=180)
    assert reply.returncode==0,reply.stderr;reply=json.loads(reply.stdout);proof=reply['receipt'];nextname=proof['message']['monitor_wallet'];monitors.append(nextname);verify(Path(r.root.name)/'dashboard.cfg',proof,proof['public_key']);assert reply['review']['refresh']['height']==newheight
    installed=call(name,'listdescriptors',[False])['descriptors'];assert all(any(x['desc']==y['desc'] for y in installed) for x in rows),'Old policy disappeared'
   else:fresh=None;newd=None;to=call('fund','getnewaddress')
   derived=Descriptor.from_string(rows[0]['desc']).derive(cycle);fields={'05':derived.witness_script().data.hex()}
   for key in derived.keys:
    if key.origin:fields['06'+key.get_public_key().sec().hex()]=(key.origin.fingerprint+b''.join(i.to_bytes(4,'little') for i in key.origin.derivation)).hex()
   value=int(round(coin['amount']*100000000));result={'selected':{'source':[{'raw_tx':prev,'txid':coin['txid'],'vout':coin['vout'],'value':value,'script':coin['scriptPubKey'],'witness':True,'psbt_fields':fields,'height':height}]},'outputs':{to:value-1000},'changes':{},'total_fee_sats':'1000'}
   original,tx=encode_psbt(result,{}, {newd.script_pubkey().data.hex():newd} if newd else {});proofs={}
   if newd:
    request={'kind':'sign_psbt_partial','request_id':digest({'count':count,'cycle':cycle,'signing':'review'}),'chain':'regtest','wallet':'key0','psbt':original,'output_policies':[{'policy_hash':proof['message']['policy_hash'],'descriptor':fresh[0]['desc'],'index':cycle+1,'address':to,'amount_sats':str(value-1000)}]};path.write_text(json.dumps(request));reviewed=subprocess.run([sys.executable,'-c',child,str(agent),str(path),str(r.port),str(Path(r.root.name)/'regtest/.cookie')],capture_output=True,text=True,timeout=180);assert reviewed.returncode==0,reviewed.stderr;facts=json.loads(reviewed.stdout)['review']['review'];assert any(o.get('vault_policy',{}).get('height')==newheight for o in facts['outputs']),facts
   for i in range(threshold):
    signed=call('key'+str(i),'walletprocesspsbt',[original,True,'ALL',True,False])['psbt'];proofs,n=validate_partial(original,signed.encode(),[0],proofs);assert n==1
   assert progress(original,proofs,[0])['complete'];raw=final_transaction(combine(original,{'source':{'proofs':proofs}},['source'])).serialize().hex();before=rpc(r,'testmempoolaccept',[[raw]])[0];assert not before['allowed'] and 'non-final' in before['reject-reason'],before
   extra_raw=[]
   for other in extra_coins:
    amount=int(round(other['amount']*100000000));data={'selected':{'source':[{'raw_tx':call(name,'gettransaction',[other['txid']])['hex'],'txid':other['txid'],'vout':other['vout'],'value':amount,'script':other['scriptPubKey'],'witness':True,'psbt_fields':fields,'height':height}]},'outputs':{call('fund','getnewaddress'):amount-1000},'changes':{},'total_fee_sats':'1000'};unsigned,transaction=encode_psbt(data,{});signatures={}
    for i in range(threshold):
     signed=call('key'+str(i),'walletprocesspsbt',[unsigned,True,'ALL',True,False])['psbt'];signatures,n=validate_partial(unsigned,signed.encode(),[0],signatures)
    extra=final_transaction(combine(unsigned,{'source':{'proofs':signatures}},['source'])).serialize().hex();check=rpc(r,'testmempoolaccept',[[extra]])[0];assert not check['allowed'] and 'non-final' in check['reject-reason'];extra_raw.append(extra)
   rpc(r,'generatetoaddress',[height-rpc(r,'getblockcount'),mine]);dashboard_state(monitors,history,observed,'red');accepted=rpc(r,'testmempoolaccept',[[raw]])[0];assert accepted['allowed'],accepted;txid=rpc(r,'sendrawtransaction',[raw]);
   for extra in extra_raw:
    assert rpc(r,'testmempoolaccept',[[extra]])[0]['allowed'];rpc(r,'sendrawtransaction',[extra])
   rpc(r,'generatetoaddress',[1,mine])
   assert not any(c['address']==address for c in call(name,'listunspent',[1])),'Spent old-address balance reappeared'
   if newd:
    actual=call(nextname,'getaddressinfo',[to]);assert actual.get('ismine') or actual.get('iswatchonly') or actual.get('solvable'),{'actual':actual,'to':to,'receipt':reply,'installed':call(name,'listdescriptors',[False]),'fresh':fresh,'cycle':cycle,'count':count};coins=call(nextname,'listunspent',[1]);assert sum(int(round(c['amount']*100000000)) for c in coins)==value-1000
    assert actual['scriptPubKey']==newd.script_pubkey().data.hex();history.extend(fresh);rows=fresh;address=to;height=newheight;name=nextname;dashboard_state(monitors,history,value-1000,'green')
   results.append({'signers':count,'required':threshold,'generation':cycle+1,'expiry_rejected_before_height':True,'accepted_after_height':True,'old_history_retained':True,'new_address_detected':bool(newd),'additional_large_deposit_to_same_third_generation_address':bool(extra_coins),'both_third_generation_outputs_locked_before_height':bool(extra_coins),'txid':txid})
 # A later ordinary payment to the exact same vault address inherits its policy.
 height=rpc(r,'getblockcount')+5;fresh=public_rows({'raw':{'wallet_name':'test','descriptors':rows}},height);desc=Descriptor.from_string(fresh[0]['desc']).derive(0);address=desc.address(NETWORKS['regtest']);monitor='two-deposits';rpc(r,'createwallet',[monitor,True,True]);call(monitor,'importdescriptors',[[{k:v for k,v in x.items() if k!='next_index'}|{'timestamp':'now'} for x in fresh]])
 txids=[call('fund','sendtoaddress',[address,amount]) for amount in (.0001,.0099)];rpc(r,'generatetoaddress',[1,mine]);coins=call(monitor,'listunspent',[1]);assert len(coins)==2 and {c['txid'] for c in coins}==set(txids);prepared=[]
 for coin in coins:
  fields={'05':desc.witness_script().data.hex()}
  for key in desc.keys:
   if key.origin:fields['06'+key.get_public_key().sec().hex()]=(key.origin.fingerprint+b''.join(i.to_bytes(4,'little') for i in key.origin.derivation)).hex()
  value=int(round(coin['amount']*100000000));result={'selected':{'source':[{'raw_tx':call(monitor,'gettransaction',[coin['txid']])['hex'],'txid':coin['txid'],'vout':coin['vout'],'value':value,'script':coin['scriptPubKey'],'witness':True,'psbt_fields':fields,'height':height}]},'outputs':{call('fund','getnewaddress'):value-1000},'changes':{},'total_fee_sats':'1000'};unsigned,tx=encode_psbt(result,{});proofs={}
  for i in range(2):
   signed=call('key'+str(i),'walletprocesspsbt',[unsigned,True,'ALL',True,False])['psbt'];proofs,new=validate_partial(unsigned,signed.encode(),[0],proofs)
  raw=final_transaction(combine(unsigned,{'source':{'proofs':proofs}},['source'])).serialize().hex();assert not rpc(r,'testmempoolaccept',[[raw]])[0]['allowed'];prepared.append(raw)
 rpc(r,'generatetoaddress',[height-rpc(r,'getblockcount'),mine]);assert all(rpc(r,'testmempoolaccept',[[raw]])[0]['allowed'] for raw in prepared)
 for raw in prepared:rpc(r,'sendrawtransaction',[raw])
 results.append({'case':'small test deposit, then larger ordinary payment to same vault address','separate_deposits':2,'both_rejected_before_height':True,'both_accepted_after_height':True})
 # Mixed wallet: ordinary inputs remain selectable while vault inputs are excluded.
 height=rpc(r,'getblockcount')+5;fresh=public_rows({'raw':{'wallet_name':'mixed','descriptors':rows}},height);ordinary=key_rows[0];history=fresh+ordinary;monitor='mixed-availability';rpc(r,'createwallet',[monitor,True,True]);call(monitor,'importdescriptors',[[{k:v for k,v in x.items() if k!='next_index'}|{'timestamp':'now'} for x in history]])
 for policy,amount in ((fresh[0],.001),(next(x for x in ordinary if not x['internal']),.002)):
  destination=Descriptor.from_string(policy['desc']).derive(0).address(NETWORKS['regtest']);call('fund','sendtoaddress',[destination,amount])
 rpc(r,'generatetoaddress',[1,mine]);dashboard_state([monitor],history,300000,'amber',outside_sats=200000)
 results.append({'case':'mixed ordinary and locked vault outputs','available_sats':200000,'locked_sats':100000,'locked_coins_excluded_from_selection':True})
 print(json.dumps({'chain':'isolated regtest','core':rpc(r,'getnetworkinfo')['subversion'],'transitions':results},indent=2))
finally:r.close()
