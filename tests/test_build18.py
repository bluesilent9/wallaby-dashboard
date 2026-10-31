"""Read-only zero-balance checks and correlated, locally approved retirement outcomes."""
import copy
from unittest.mock import patch
from test_build8 import Setup
from wallet_retirement import propose,result,zero_balances,confirm_discs,snapshot,warnings,suppress_sync,CAPABILITY

class Retirement(Setup):
 def setUp(self):
  super().setUp();self.hub.caps[self.a['node_id']]={CAPABILITY};self.session=type('Session',(),{'user_id':self.user,'user':lambda s:{'id':self.user},'access':lambda s:{'ownership_ready':True}})()
  self.watch_copy=copy.deepcopy(self.hub.inventories[self.b['node_id']]);self.hub.inventories[self.b['node_id']]['wallets']=[]
  self.calls=[];self.balances={};self.scan=False
  def call(node,method,params,wallet=None):
   self.calls.append((node,method,wallet))
   if method=='getblockchaininfo':return {'chain':'main','blocks':999000,'headers':999000,'initialblockdownload':False}
   if method=='getwalletinfo':return {'scanning':self.scan,'lastprocessedblock':{'height':999000}}
   if method=='getbalances':return copy.deepcopy(self.balances.get((node,wallet),{'mine':{'trusted':'0','untrusted_pending':'0','immature':'0'}}))
   raise AssertionError(method)
  self.patch=patch('wallet_retirement.RegistryRPC.call',side_effect=call);self.patch.start();self.addCleanup(self.patch.stop)
 def request(self):return propose(self.hub,self.session,self.rid,self.a['node_id'],True)
 def delivered(self):
  r=self.request();self.exchange(self.a,caps=[CAPABILITY]);return r['request_hash']
 def proof(self,key,**extra):return dict(request_hash=key,status='complete',archive_verified=True,history_verified=True,zero_balance_verified=True,wallet_deleted=True,archive_wallet='extinct_watch',address_count=4,deleted_wallets=[self.node_wallet['name']],**extra)
 def test_checks_all_replicas_pending_immature_watchonly_and_locked_balances(self):
  self.hub.inventories[self.b['node_id']]=self.watch_copy
  for group,field in [('mine','trusted'),('mine','untrusted_pending'),('mine','immature'),('watchonly','trusted')]:
   self.balances={(self.b['node_id'],'different_watch_name'):{group:{'trusted':'0','untrusted_pending':'0','immature':'0',field:'0.00000001'}}}
   with self.assertRaisesRegex(ValueError,'zero-balance'):self.request()
   self.assertFalse(self.hub.read()['requests'])
  self.balances={};r=self.request();self.assertTrue(r['queued']);self.assertEqual({n for n,m,w in self.calls},{self.a['node_id'],self.b['node_id']});self.assertTrue(all(m in ('getblockchaininfo','getwalletinfo','getbalances') for n,m,w in self.calls))
 def test_unknown_or_rescanning_balance_fails_closed(self):
  self.scan=True
  with self.assertRaisesRegex(ValueError,'rescan'):self.request()
  self.scan=False;self.balances={(self.a['node_id'],self.node_wallet['name']):{'mine':{'trusted':'0'}}}
  with self.assertRaisesRegex(ValueError,'incomplete'):self.request()
 def test_vault_balance_is_checked_through_source_relationship(self):
  from descriptor_store import clean_wallet
  raw={'wallet_name':'vault_monitor','descriptors':[{'desc':self.vault['descriptor']}]};w=clean_wallet(raw)
  with self.hub.edit() as s:s['vaults']['test']={'public':self.vault,'sources':[]}
  self.hub.inventories[self.a['node_id']]['wallets'].append({'name':'vault_monitor','raw':raw,'id':w['id'],'private_keys_enabled':False,'scanning':False})
  self.balances={(self.a['node_id'],'vault_monitor'):{'watchonly':{'trusted':'0.001','untrusted_pending':'0','immature':'0'}}}
  with self.assertRaisesRegex(ValueError,'vault'):self.request()
 def test_multisig_monitor_and_its_signing_accounts_are_checked(self):
  from vendor.embit.descriptor.checksum import checksum
  key=self.wallet['raw']['descriptors'][0]['desc'].split('wpkh(')[1].split(')')[0];other=__import__('vendor.embit.bip32',fromlist=['HDKey']).HDKey.from_seed(b'distinct multisig fixture seed').to_public().to_base58()+'/0/*';body='wsh(sortedmulti(2,'+key+','+other+'))'
  raw={'wallet_name':'shared','descriptors':[{'desc':body+'#'+checksum(body)}]}
  self.hub.inventories[self.a['node_id']]['wallets'].append({'name':'shared','raw':raw,'private_keys_enabled':False,'scanning':False})
  # Check a policy itself, including its private key account.
  self.balances={(self.a['node_id'],self.node_wallet['name']):{'mine':{'trusted':'0','untrusted_pending':'1','immature':'0'}}}
  with self.assertRaisesRegex(ValueError,'zero-balance'):zero_balances(self.hub,raw)
 def test_public_request_requires_v2_local_deletion_and_blocks_duplicate(self):
  key=self.delivered();p=self.hub.read()['requests'][key]['proposal'];self.assertEqual(p['version'],2);self.assertTrue(p['delete_wallet']);self.assertTrue(p['require_zero_balance']);self.assertNotIn('xprv',str(p))
  with self.assertRaisesRegex(ValueError,'pending'):self.request()
 def test_complete_requires_archive_and_deletion_and_matching_node(self):
  key=self.delivered()
  for changes in ({'wallet_deleted':False},{'archive_verified':False},{'history_verified':False},{'zero_balance_verified':False},{'deleted_wallets':['other']}):
   with self.hub.edit() as s:
    p=self.proof(key);p.update(changes)
    with self.assertRaises(ValueError):result(s,self.a['node_id'],p)
  with self.hub.edit() as s:
   with self.assertRaises(ValueError):result(s,self.b['node_id'],self.proof(key))
   result(s,self.a['node_id'],self.proof(key));result(s,self.a['node_id'],self.proof(key))
  self.assertEqual(snapshot(self.hub,self.user)[0]['state'],'complete');self.assertTrue(warnings(self.hub))
  with self.assertRaises(ValueError):confirm_discs(self.hub,'another',key,'destroyed')
  confirm_discs(self.hub,self.user,key,'destroyed');self.assertFalse(warnings(self.hub));self.assertTrue(suppress_sync(self.hub.read(),self.wallet['raw']))
 def test_cancellation_denial_errors_remain_flagged_and_cannot_be_rewritten(self):
  for status in ('cancelled','denied','error'):
   with self.hub.edit() as state:state['requests']={}
   key=self.delivered()
   with self.hub.edit() as s:result(s,self.a['node_id'],{'request_hash':key,'status':status})
   self.assertEqual(warnings(self.hub)[-1]['severity'],'red')
   with self.assertRaises(ValueError):confirm_discs(self.hub,self.user,key,'none')
   with self.hub.edit() as s:
    with self.assertRaises(ValueError):result(s,self.a['node_id'],self.proof(key))
 def test_all_related_local_signing_accounts_must_have_deletion_confirmation(self):
  key=self.delivered()
  with self.hub.edit() as state:
   state['requests'][key]['proposal']['delete_wallets'].append({'wallet':'Related signer','public_wallet':self.wallet['raw'],'private_keys_enabled':True})
   with self.assertRaisesRegex(ValueError,'deletion'):result(state,self.a['node_id'],self.proof(key))
   p=self.proof(key);p['deleted_wallets'].append('Related signer');result(state,self.a['node_id'],p)
 def test_disc_confirmation_cannot_precede_completion(self):
  key=self.delivered()
  with self.assertRaises(ValueError):confirm_discs(self.hub,self.user,key,'destroyed')

 def test_cancelled_records_survive_validated_profile_restore(self):
  from agent_state import validate
  key=self.delivered()
  with self.hub.edit() as state:result(state,self.a['node_id'],{'request_hash':key,'status':'cancelled'})
  state=self.hub.read();validate(state,{self.user});self.assertEqual(state['requests'][key]['state'],'cancelled')

 def test_retirement_withdraws_already_queued_sync_instead_of_recreating_wallet(self):
  from wallet_sync_queue import schedule
  key=self.delivered();state=self.hub.read()
  for direction,node,peer in [('send',self.a,self.b),('receive',self.b,self.a)]:
   state['requests']['sync-'+direction]={'kind':'wallet_transfer','transfer_id':'pair','direction':direction,'node_id':node['node_id'],'peer_id':peer['node_id'],'public_wallet':self.wallet['raw'],'state':'pending','queue_visible':True}
  schedule(self.hub.transfers,state)
  self.assertFalse(state['requests']['sync-send']['queue_visible']);self.assertFalse(state['requests']['sync-receive']['queue_visible'])

 def test_private_and_watch_holders_each_require_confirmation(self):
  self.hub.inventories[self.b['node_id']]=self.watch_copy;self.hub.caps[self.b['node_id']]={CAPABILITY};key=self.delivered();state=self.hub.read();children=[(k,r) for k,r in state['requests'].items() if r.get('root_hash')==key];self.assertEqual(len(children),2)
  self.exchange(self.b,caps=[CAPABILITY])
  with self.hub.edit() as state:result(state,self.a['node_id'],self.proof(key))
  self.assertEqual(snapshot(self.hub,self.user)[0]['state'],'pending')
  with self.assertRaises(ValueError):confirm_discs(self.hub,self.user,key,'none')
  other=next(k for k,r in children if r['node_id']==self.b['node_id']);p=self.proof(other);p['deleted_wallets']=['different_watch_name']
  with self.hub.edit() as state:result(state,self.b['node_id'],p)
  self.assertEqual(snapshot(self.hub,self.user)[0]['state'],'complete');confirm_discs(self.hub,self.user,key,'none')
 def test_offline_known_holder_is_retained_and_queued_after_reconnection(self):
  from wallet_retirement import advance
  with self.hub.edit() as state:state['nodes'].setdefault(self.b['node_id'],{})['known_wallets']={'known':{'raw':self.wallet['raw'],'name':'different_watch_name'}}
  key=self.delivered();self.assertEqual(len(snapshot(self.hub,self.user)[0]['holders']),2)
  with self.hub.edit() as state:result(state,self.a['node_id'],self.proof(key))
  self.assertEqual(snapshot(self.hub,self.user)[0]['state'],'pending');self.hub.inventories[self.b['node_id']]=self.watch_copy;self.hub.caps[self.b['node_id']]={CAPABILITY};advance(self.hub);advance(self.hub)
  self.assertEqual(len([r for r in self.hub.read()['requests'].values() if r.get('root_hash')==key]),2)

 def test_key_account_cannot_retire_when_its_shared_monitor_still_has_funds(self):
  from vendor.embit.descriptor.checksum import checksum
  from vendor.embit.bip32 import HDKey
  key=self.wallet['raw']['descriptors'][0]['desc'].split('wpkh(')[1].split(')')[0];other=HDKey.from_seed(b'another fixture key').to_public().to_base58()+'/0/*';body='wsh(sortedmulti(2,'+key+','+other+'))'
  raw={'wallet_name':'shared','descriptors':[{'desc':body+'#'+checksum(body)}]};self.hub.inventories[self.a['node_id']]['wallets'].append({'name':'shared','raw':raw,'private_keys_enabled':False,'scanning':False})
  self.balances={(self.a['node_id'],'shared'):{'watchonly':{'trusted':'1','untrusted_pending':'0','immature':'0'}}}
  with self.assertRaisesRegex(ValueError,'zero-balance'):self.request()

 def test_generated_proposal_matches_actual_agent_validator(self):
  import os,sys,subprocess,json
  source=os.environ.get('WALLABY_AGENT_SOURCE')
  if not source:self.skipTest('Set WALLABY_AGENT_SOURCE for Agent protocol verification')
  key=self.delivered();p=self.hub.read()['requests'][key]['proposal']
  code="import sys,json;sys.path.insert(0,sys.argv[1]);from wallet_retirement import validate;validate(json.load(sys.stdin));print('accepted')"
  r=subprocess.run([sys.executable,'-B','-c',code,source],input=json.dumps(p),text=True,capture_output=True,timeout=10)
  self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout.strip(),'accepted')
