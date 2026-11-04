"""Two-phase extinction; invented wallets only."""
import copy
from unittest.mock import patch
from test_build8 import Setup
from wallet_retirement import propose,result,zero_balances,confirm_discs,snapshot,warnings,suppress_sync,CAPABILITY,advance,group_rows,group_complete,deliverable
class Retirement(Setup):
 def setUp(self):
  super().setUp();self.hub.caps[self.a['node_id']]={CAPABILITY};self.session=type('Session',(),{'user_id':self.user,'user':lambda s:{'id':self.user},'access':lambda s:{'ownership_ready':True}})()
  self.watch_copy=copy.deepcopy(self.hub.inventories[self.b['node_id']]);self.hub.inventories[self.b['node_id']]['wallets']=[];self.hub.inventories[self.a['node_id']]['archive_wallets']=['historical_container']
  self.calls=[];self.balances={};self.scan=False;self.missing_address=False;self.missing_tx=False;self.new_tx=False
  from vendor.embit.descriptor import Descriptor
  from vendor.embit.networks import NETWORKS
  self.address=Descriptor.from_string(self.wallet['raw']['descriptors'][0]['desc']).derive(0).script_pubkey().address(NETWORKS['main']);self.txid=self.prev.txid().hex();self.blockhash='b'*64
  def call(node,method,params=None,wallet=None):
   self.calls.append((node,method,wallet));params=params or []
   if method=='getblockchaininfo':return {'chain':'main','blocks':999000,'headers':999000,'initialblockdownload':False}
   if method=='getwalletinfo':return {'private_keys_enabled':wallet!='historical_container','scanning':self.scan,'lastprocessedblock':{'height':999000}}
   if method=='getbalances':return copy.deepcopy(self.balances.get((node,wallet),{'mine':{'trusted':'0','untrusted_pending':'0','immature':'0'}}))
   if method=='listtransactions':return [{'txid':self.txid,'confirmations':12}]+([{'txid':'c'*64,'confirmations':1}] if self.new_tx else [])
   if method=='gettransaction':
    if wallet=='historical_container' and self.missing_tx:raise ValueError('History missing')
    return {'hex':self.prev.serialize().hex(),'confirmations':12,'blockhash':self.blockhash}
   if method=='getblockheader':return {'confirmations':12}
   if method=='decoderawtransaction':return {'vout':[{'scriptPubKey':{'address':self.address}}]}
   if method=='getaddressinfo':return {'ismine':not self.missing_address or wallet!='historical_container','labels':['Example Wallet']}
   if method=='listdescriptors':return copy.deepcopy(self.wallet['raw'])
   raise AssertionError(method)
  self.call=call;self.hub.rpc=call;self.patch=patch('wallet_retirement.RegistryRPC.call',side_effect=call);self.patch.start();self.addCleanup(self.patch.stop)
  self.signature=patch('wallet_policy_updates.verified',side_effect=lambda hub,node,e:e['message']);self.signature.start();self.addCleanup(self.signature.stop)
 def request(self):return propose(self.hub,self.session,self.rid,self.a['node_id'],True)
 def delivered(self):
  r=self.request();self.exchange(self.a,caps=list(self.hub.caps[self.a['node_id']]));return r['request_hash']
 def proof(self,key,**extra):
  p=self.hub.read()['requests'][key]['proposal'];phase=p['phase'];v=dict(request_hash=key,status='complete',phase=phase,manifest_hash=p['manifest_hash'])
  if phase=='preserve':
   receipt={'version':1,'kind':'wallet_retirement_preservation','request_hash':key,'operation_id':p['operation_id'],'manifest_hash':p['manifest_hash'],'archive_wallet':p['wallet'],'address_count':len(p['manifest']['addresses']),'wallet_deleted':False}
   v.update(archive_verified=True,history_verified=True,wallet_deleted=False,archive_wallet=p['wallet'],address_count=len(p['manifest']['addresses']),deleted_wallets=[],archive_receipt={'message':receipt})
  else:v.update(zero_balance_verified=True,wallet_deleted=True,deleted_wallets=[t['wallet'] for t in p['delete_wallets']])
  v.update(extra);return v
 def preserve(self,key):
  with self.hub.edit() as s:result(s,self.a['node_id'],self.proof(key))
  advance(self.hub)
 def deletion(self,key,node=None):return next((k for k,r in self.hub.read()['requests'].items() if r.get('root_hash')==key and r['proposal']['phase']=='delete' and (node is None or r['node_id']==node)),None)
 def finish(self,key):
  self.preserve(key);d=self.deletion(key);self.exchange(self.a,caps=[CAPABILITY])
  with self.hub.edit() as s:result(s,self.a['node_id'],self.proof(d))
  return d
 def test_requires_existing_connected_archive(self):
  self.hub.inventories[self.a['node_id']]['archive_wallets']=[]
  with self.assertRaisesRegex(ValueError,'existing Extinct'):self.request()
  self.assertFalse(self.hub.read()['requests'])
 def test_preservation_is_separate_from_deletion(self):
  key=self.delivered();p=self.hub.read()['requests'][key]['proposal'];self.assertEqual(p['version'],3);self.assertFalse(p['delete_wallet']);self.assertEqual(p['delete_wallets'],[])
  advance(self.hub);self.assertIsNone(self.deletion(key));self.preserve(key);self.assertIsNotNone(self.deletion(key))
 def test_non_archive_holder_receives_delete_only(self):
  self.hub.inventories[self.b['node_id']]=self.watch_copy;self.hub.caps[self.b['node_id']]={CAPABILITY};key=self.delivered();self.preserve(key)
  p=next(p for p in self.exchange(self.b,caps=[CAPABILITY])['proposals'] if p['kind']=='wallet_retire');self.assertEqual(p['phase'],'delete');self.assertEqual(p['wallet'],'different_watch_name');self.assertNotIn('archive_wallet',p)
 def test_bad_archive_readback_cannot_release_deletion(self):
  for failure in ('missing_address','missing_tx'):
   with self.hub.edit() as s:s['requests']={}
   key=self.delivered();setattr(self,failure,True);self.preserve(key);self.assertIsNone(self.deletion(key));setattr(self,failure,False)
 def test_forged_receipt_cannot_release_deletion(self):
  key=self.delivered();p=self.proof(key);p['archive_receipt']['message']['manifest_hash']='f'*64
  with self.hub.edit() as s:result(s,self.a['node_id'],p)
  advance(self.hub);self.assertIsNone(self.deletion(key))
 def test_wrong_phase_manifest_node_and_deleting_preserve_rejected(self):
  key=self.delivered()
  for changes in ({'phase':'delete'},{'manifest_hash':'f'*64},{'wallet_deleted':True},{'deleted_wallets':['source_keys']}):
   with self.hub.edit() as s:
    with self.assertRaises(ValueError):result(s,self.a['node_id'],self.proof(key,**changes))
  with self.hub.edit() as s:
   with self.assertRaises(ValueError):result(s,self.b['node_id'],self.proof(key))
 def test_funds_arriving_after_preservation_block_deletion(self):
  key=self.delivered();self.balances={(self.a['node_id'],self.node_wallet['name']):{'mine':{'trusted':'1','untrusted_pending':'0','immature':'0'}}};self.preserve(key);self.assertIsNone(self.deletion(key));self.assertTrue(snapshot(self.hub,self.user)[0]['message'])
 def test_new_history_after_preservation_blocks_deletion(self):
  key=self.delivered();self.new_tx=True;self.preserve(key);self.assertIsNone(self.deletion(key));self.assertIn('history changed',snapshot(self.hub,self.user)[0]['message'])
 def test_exact_deleted_wallets_and_zero_confirmation_required(self):
  key=self.delivered();self.preserve(key);d=self.deletion(key);self.exchange(self.a,caps=[CAPABILITY])
  for changes in ({'deleted_wallets':['wrong']},{'zero_balance_verified':False},{'archive_wallet':'created_unwanted_archive'}):
   with self.hub.edit() as s:
    with self.assertRaises(ValueError):result(s,self.a['node_id'],self.proof(d,**changes))
 def test_group_finishes_only_after_deletion_and_survives_restore(self):
  key=self.delivered();self.preserve(key);self.assertFalse(group_complete(self.hub.read(),key));d=self.deletion(key);self.exchange(self.a,caps=[CAPABILITY])
  with self.hub.edit() as s:result(s,self.a['node_id'],self.proof(d));result(s,self.a['node_id'],self.proof(d))
  from agent_state import validate
  validate(self.hub.read(),{self.user});self.assertTrue(group_complete(self.hub.read(),key));self.assertTrue(suppress_sync(self.hub.read(),self.wallet['raw']))
 def test_offline_preserver_cannot_release_deletions(self):
  key=self.delivered();self.hub.inventories[self.a['node_id']]['archive_wallets']=[];self.preserve(key);self.assertIsNone(self.deletion(key))
 def test_profile_tamper_cannot_bypass_barrier(self):
  key=self.delivered();self.preserve(key);d=self.deletion(key);state=self.hub.read();state['requests'][key]['archive_checked']=False
  from agent_state import validate
  with self.assertRaises(ValueError):validate(state,{self.user})
  self.assertFalse(deliverable(state,d))
 def test_outdated_agent_does_not_receive_new_protocol(self):
  self.request();self.assertFalse([p for p in self.exchange(self.a,caps=['wallet_retirement_v2'])['proposals'] if p['kind']=='wallet_retire'])
 def test_all_existing_archive_holders_receive_preserve_first(self):
  self.hub.inventories[self.b['node_id']]=self.watch_copy;self.hub.inventories[self.b['node_id']]['archive_wallets']=['family_history'];self.hub.caps[self.b['node_id']]={CAPABILITY};key=self.delivered();rows=group_rows(self.hub.read(),key);self.assertEqual(len(rows),2);self.assertTrue(all(r['proposal']['phase']=='preserve' for r in rows))

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


 def test_retirement_withdraws_already_queued_sync_instead_of_recreating_wallet(self):
  from wallet_sync_queue import schedule
  key=self.delivered();state=self.hub.read()
  for direction,node,peer in [('send',self.a,self.b),('receive',self.b,self.a)]:
   state['requests']['sync-'+direction]={'kind':'wallet_transfer','transfer_id':'pair','direction':direction,'node_id':node['node_id'],'peer_id':peer['node_id'],'public_wallet':self.wallet['raw'],'state':'pending','queue_visible':True}
  schedule(self.hub.transfers,state)
  self.assertFalse(state['requests']['sync-send']['queue_visible']);self.assertFalse(state['requests']['sync-receive']['queue_visible'])


 def test_key_account_cannot_retire_when_its_shared_monitor_still_has_funds(self):
  from vendor.embit.descriptor.checksum import checksum
  from vendor.embit.bip32 import HDKey
  key=self.wallet['raw']['descriptors'][0]['desc'].split('wpkh(')[1].split(')')[0];other=HDKey.from_seed(b'another fixture key').to_public().to_base58()+'/0/*';body='wsh(sortedmulti(2,'+key+','+other+'))'
  raw={'wallet_name':'shared','descriptors':[{'desc':body+'#'+checksum(body)}]};self.hub.inventories[self.a['node_id']]['wallets'].append({'name':'shared','raw':raw,'private_keys_enabled':False,'scanning':False})
  self.balances={(self.a['node_id'],'shared'):{'watchonly':{'trusted':'1','untrusted_pending':'0','immature':'0'}}}
  with self.assertRaisesRegex(ValueError,'zero-balance'):self.request()

