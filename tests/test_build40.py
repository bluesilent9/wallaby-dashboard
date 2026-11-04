"""Cancellation boundaries: private-key visibility, immutable history and signed disc cleanup."""
import copy
from unittest.mock import patch
import test_build8 as fixtures
import test_build18 as retire
import test_build35 as archives
from wallet_retirement import cancellation_visibility,cancel,snapshot,private_sources,suppress_sync
from wallet_renames import RenameQueue
from archive_queue import ArchiveQueue,validate,CANCEL_CAPABILITY
from transaction_workflows import WorkflowStore
from vendor.embit import bip32
from vendor.embit.descriptor.checksum import checksum

def multisig():
 keys=[bip32.HDKey.from_seed(bytes([i])*32).to_public().to_base58()+'/0/*' for i in range(1,4)]
 raw={'wallet_name':'Policy','descriptors':[{'desc':'wsh(multi(2,'+','.join(keys)+'))'}]}
 sources=[{'raw':{'wallet_name':'Role '+str(i),'descriptors':[{'desc':'wpkh('+k+')'}]}} for i,k in enumerate(keys)]
 return raw,sources
class Visibility(fixtures.unittest.TestCase):
 def test_every_multisig_role_required_even_above_spending_threshold(self):
  raw,sources=multisig();self.assertTrue(cancellation_visibility(raw,sources)[0]);self.assertFalse(cancellation_visibility(raw,sources[:2])[0]);self.assertFalse(cancellation_visibility(raw,[sources[0]]*3)[0]);self.assertFalse(cancellation_visibility(raw,[{'raw':raw}])[0])

class Extinction(retire.Retirement):
 def test_watch_only_or_disconnected_private_copy_cannot_cancel(self):
  key=self.request()['request_hash'];self.hub.inventories[self.a['node_id']]['wallets'][0]['private_keys_enabled']=False;self.assertFalse(snapshot(self.hub,self.user)[0]['can_cancel'])
  with self.assertRaisesRegex(ValueError,'private-key'):cancel(self.hub,self.user,key,True)
  self.hub.inventories[self.a['node_id']]['wallets'][0]['private_keys_enabled']=True;self.registry.heartbeat(self.a['connection_token'],disconnect=True);self.assertFalse(snapshot(self.hub,self.user)[0]['can_cancel'])
 def test_submit_rechecks_private_wallet_not_only_cached_inventory(self):
  key=self.request()['request_hash'];original=self.hub.rpc
  def changed(node,method,*args,**kwargs):return {'private_keys_enabled':False} if method=='getwalletinfo' else original(node,method,*args,**kwargs)
  self.hub.rpc=changed
  with self.assertRaisesRegex(ValueError,'fresh Agent'):cancel(self.hub,self.user,key,True)
  self.assertFalse(self.hub.read()['requests'][key].get('cancel_requested'))
 def test_cancelled_preservation_does_not_permanently_suppress_sync(self):
  key=self.delivered();self.preserve(key);cancel(self.hub,self.user,key,True);self.assertFalse(suppress_sync(self.hub.read(),self.wallet['raw']))
 def test_confirmed_deleted_stale_inventory_is_not_a_private_source(self):
  key=self.delivered();self.finish(key);self.assertEqual(private_sources(self.hub,self.wallet['raw']),[]);self.assertFalse(snapshot(self.hub,self.user)[0]['can_cancel'])
# Do not repeat the inherited baseline suite here.
for name in retire.Retirement.__dict__:
 if name.startswith('test_') and name not in Extinction.__dict__:setattr(Extinction,name,None)

class ArchiveCancellation(archives.Archives):
 def start(self):
  option=next(o for o in self.queue.options(self.rid) if o['node_id']==self.a['node_id']);self.queue.create(self.user,self.rid,[option['id']]);return self.queue.proposal(self.a['node_id'])['requests'][0]['request_id']
 def accept(self,ident,**extra):
  p=dict(version=1,queue_id=self.queue.read()['queue_id'],request_id=ident,status='complete',**extra);p['signed_receipt']={'message':copy.deepcopy(p)}
  with patch('wallet_policy_updates.verified',return_value={k:v for k,v in p.items() if k!='signed_receipt'}):self.queue.accept(self.a['node_id'],p)
 def child(self,parent):return self.queue.read()['requests'][parent]['cancellation_id']
 def test_unused_request_removed_new_cancel_task_capability_gated_and_idempotent(self):
  parent=self.start();self.queue.cancel(self.user,parent);child=self.child(parent);self.queue.cancel(self.user,parent);self.assertEqual(len(self.queue.read()['requests']),2);self.assertEqual(self.queue.proposal(self.a['node_id'])['requests'],[])
  self.assertIn('Agent update required',self.queue.snapshot()['requests'][0]['status']);self.hub.caps[self.a['node_id']].add(CANCEL_CAPABILITY);tasks=self.queue.proposal(self.a['node_id'])['requests'];self.assertEqual(tasks[0]['kind'],'archive_cancel');self.assertEqual(tasks[0]['archive_request']['request_id'],parent);validate(self.queue.read())
  self.accept(child,request_cancelled=True,disc_created=False);self.assertEqual(self.queue.snapshot()['requests'],[])
  store=WorkflowStore(self.config);store.sync_archive_events(self.user,self.queue.read()['requests'],'balances');self.assertEqual(store.notifications(self.user)['notifications'][0]['severity'],'red')
 def test_completed_or_racing_burn_requires_all_physical_cleanup_evidence(self):
  parent=self.start();self.accept(parent,readback_verified=True,image_sha256='a'*64,archive_id='b'*64);self.queue.cancel(self.user,parent);child=self.child(parent)
  with self.assertRaisesRegex(ValueError,'no disc'):self.accept(child,request_cancelled=True,disc_created=False)
  proof=dict(request_cancelled=True,disc_created=True,destruction_confirmed_locally=True,unrelated_members_preserved=True,burn_finished=True,verification_finished=True,archive_id='b'*64,disc_count=2,destroyed_disc_count=2,replacement_required=True,replacement_readback_verified=True,replacement_image_sha256='c'*64)
  for field in ('destruction_confirmed_locally','unrelated_members_preserved','verification_finished','replacement_readback_verified'):
   with self.assertRaises(ValueError):self.accept(child,**dict(proof,**{field:False}))
  with self.assertRaises(ValueError):self.accept(child,**dict(proof,destroyed_disc_count=1))
  self.accept(child,**proof);self.assertEqual(self.queue.snapshot()['requests'],[]);validate(self.queue.read())
 def test_completion_after_cancel_cannot_skip_destruction(self):
  parent=self.start();self.queue.cancel(self.user,parent);self.accept(parent,readback_verified=True,image_sha256='a'*64,archive_id='b'*64)
  with self.assertRaisesRegex(ValueError,'no disc'):self.accept(self.child(parent),request_cancelled=True,disc_created=False)
 def test_child_review_deletion_is_not_completion_and_wrong_agent_rejected(self):
  parent=self.start();self.queue.cancel(self.user,parent);child=self.child(parent);p=dict(version=1,queue_id=self.queue.read()['queue_id'],request_id=child,status='deleted',signed_receipt={})
  with patch('wallet_policy_updates.verified',return_value={k:v for k,v in p.items() if k!='signed_receipt'}):
   with self.assertRaises(ValueError):self.queue.accept(self.a['node_id'],p)
   with self.assertRaises(ValueError):self.queue.accept(self.b['node_id'],p)
for name in archives.Archives.__dict__:
 if name.startswith('test_') and name not in ArchiveCancellation.__dict__:setattr(ArchiveCancellation,name,None)

class RenameCancellation(fixtures.Setup):
 def test_partial_cancel_keeps_evidence_and_blocks_older_name_enforcement(self):
  q=RenameQueue(self.hub);first=q.create(self.user,self.rid,'First')['operations'][0]['id']
  with __import__('storage').storage_lock(self.config):
   s=q.read();s['operations'][first]['state']='complete';q.write(s)
  second=next(o['id'] for o in q.create(self.user,self.rid,'Second')['operations'] if o['new_name']=='Second')
  with __import__('storage').storage_lock(self.config):
   s=q.read();s['operations'][second]['locked']=True;q.write(s)
  q.cancel(self.user,second);s=q.read();self.assertFalse(q.latest(s['operations'][first],s['operations']));self.assertEqual(q.proposal(self.a['node_id'])['requests'],[]);self.assertTrue(s['operations'][second]['locked'])
