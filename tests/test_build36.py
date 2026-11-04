"""Late archive rename discovery uses the latest name and existing Agent actions."""
import copy
from unittest.mock import patch
import test_build29 as previous
from archive_queue import ArchiveQueue
class LateRenames(previous.Renames):
 def complete(self):
  ident=self.start();self.reply(ident,self.a['node_id']);self.reply(ident,self.b['node_id']);self.assertEqual(self.q.read()['operations'][ident]['state'],'complete');return ident
 def observe(self,archive='c'*64,label='Original archive',loaded=True,raw=None):
  raw=copy.deepcopy(raw or self.wallet['raw']);message={'version':1,'event':'archive_observed','archive_id':archive,'payload_sha256':archive,'payload_verified':True,'readback_verified':False,'replacements':[],'members':[{'member_id':'d'*64,'name':label,'public_wallet':raw,'related_policies':[]}]}
  with patch('wallet_policy_updates.verified',return_value=message):ArchiveQueue(self.hub).followup(self.a['node_id'],{'signed':'fixture'})
  self.hub.caps[self.a['node_id']]={'wallet_rename_v1'}
  if loaded:self.hub.inventories[self.a['node_id']]['wallets'].append(dict(copy.deepcopy(self.node_wallet),raw=raw,name='Disc old loaded',signing_only=True,archive_id=archive))
 def test_old_disc_after_completed_rename_reopens_and_routes(self):
  ident=self.complete();self.observe();wire=self.q.proposal(self.a['node_id']);p=next(p for p in wire['requests'] if p['storage']=='disc');self.assertEqual(p['new_name'],'Renamed Wallet');self.assertEqual(p['archive_id'],'c'*64);self.assertEqual(p['wallet'],'Disc old loaded');self.assertEqual(self.q.read()['operations'][ident]['state'],'active');self.assertTrue(self.q.snapshot()['operations'][0]['can_cancel'])
 def test_observation_without_loaded_wallet_stays_pending_without_guessing_route(self):
  ident=self.complete();self.observe(loaded=False);self.assertFalse(self.q.proposal(self.a['node_id'])['requests']);disc=next(t for t in self.q.read()['operations'][ident]['targets'].values() if t['storage']=='disc');self.assertIsNone(disc['request']);self.assertIsNone(disc['node_id'])
 def test_already_current_archive_does_not_reopen(self):
  ident=self.complete();self.observe(label='Renamed Wallet');self.assertFalse(self.q.proposal(self.a['node_id'])['requests']);self.assertEqual(self.q.read()['operations'][ident]['state'],'complete')
 def test_repeated_observation_is_one_request(self):
  ident=self.complete();self.observe();first=self.q.proposal(self.a['node_id']);second=self.q.proposal(self.a['node_id']);self.assertEqual(first,second);self.assertEqual(len(first['requests']),1)
 def test_unrelated_wallet_on_disc_is_not_renamed(self):
  from test_node_loading import public_wallet
  ident=self.complete();self.observe(raw=public_wallet('Unrelated',8));self.assertFalse(self.q.proposal(self.a['node_id'])['requests']);self.assertEqual(self.q.read()['operations'][ident]['state'],'complete')
 def test_successive_renames_use_latest_name_for_old_disc(self):
  old=self.complete();new=self.q.create(self.user,self.rid,'Latest Wallet')['operations'];latest=next(o['id'] for o in new if o['new_name']=='Latest Wallet');self.reply(latest,self.a['node_id']);self.reply(latest,self.b['node_id']);self.observe();wire=self.q.proposal(self.a['node_id']);self.assertEqual(len(wire['requests']),1);self.assertEqual(wire['requests'][0]['new_name'],'Latest Wallet');self.assertEqual(wire['requests'][0]['rename_id'],latest);self.assertEqual(self.q.read()['operations'][old]['state'],'complete')
 def test_replacement_readback_queues_old_disc_destruction(self):
  ident=self.complete();self.observe();p=self.q.proposal(self.a['node_id'])['requests'][0];q=self.q.read();proof={'version':1,'queue_id':q['queue_id'],'rename_id':ident,'request_id':p['request_id'],'wallet':p['wallet'],'new_wallet':'Disc new loaded','storage':'disc','status':'complete','identity_verified':True,'archive_skipped':False,'readback_verified':True,'archive_id':'e'*64,'image_sha256':'f'*64,'replacements':[{'archive_id':'c'*64,'image_sha256':'f'*64,'members':['d'*64],'readback_verified':True,'old_policies_retained':True}]}
  with patch('wallet_policy_updates.verified',return_value=proof):self.q.accept(self.a['node_id'],{'signed':'replacement receipt'})
  tasks=ArchiveQueue(self.hub).read()['requests'];self.assertEqual(len(tasks),1);task=next(iter(tasks.values()));self.assertEqual(task['request']['kind'],'destroy_disc');self.assertEqual(task['request']['archive_id'],'c'*64);self.assertEqual(task['state'],'active');self.assertNotIn('destruction_confirmed_locally',task)
 def test_readback_is_required_before_rename_completion(self):
  ident=self.complete();self.observe();p=self.q.proposal(self.a['node_id'])['requests'][0];proof={'version':1,'queue_id':self.q.read()['queue_id'],'rename_id':ident,'request_id':p['request_id'],'wallet':p['wallet'],'new_wallet':'Disc new','storage':'disc','status':'complete','identity_verified':True,'archive_skipped':False}
  with patch('wallet_policy_updates.verified',return_value=proof):
   with self.assertRaisesRegex(ValueError,'replacement'):self.q.accept(self.a['node_id'],{})
  self.assertFalse(ArchiveQueue(self.hub).read()['requests'])
for name in previous.Renames.__dict__:
 if name.startswith('test_') and name not in LateRenames.__dict__:setattr(LateRenames,name,None)
