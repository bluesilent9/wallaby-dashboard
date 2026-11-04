"""Signed archive outcomes, persistent per-profile notices and queue removal."""
import copy
from unittest.mock import patch
from test_build27 import SignedProtocol
from archive_queue import ArchiveQueue,validate
from transaction_workflows import WorkflowStore

class ArchiveOutcomes(SignedProtocol):
 def proposal(self):
  queue=ArchiveQueue(self.hub);self.hub.caps[self.a['node_id']]={'wallet_archive_queue_v1'}
  self.hub.rpc=lambda n,m,p=None,w=None: {'private_keys_enabled':True,'walletname':self.wallet['name']} if m=='getwalletinfo' else self.wallet['raw']
  option=queue.options(self.rid)[0];queue.create(self.user,self.rid,[option['id']]);return queue,queue.proposal(self.a['node_id'])
 def result(self,p,status,**extra):
  body=dict(version=1,queue_id=p['queue_id'],request_id=p['requests'][0]['request_id'],status=status,**extra)
  return dict(body,signed_receipt=self.sign(body))
 def test_deleted_is_terminal_signed_and_not_offered_again(self):
  q,p=self.proposal();r=self.result(p,'deleted');q.accept(self.a['node_id'],r);q.accept(self.a['node_id'],r)
  self.assertEqual(q.snapshot()['requests'],[]);self.assertEqual(q.proposal(self.a['node_id'])['requests'],[]);validate(q.read())
  self.assertEqual(q.read()['requests'][r['request_id']]['state'],'deleted')
  with self.assertRaisesRegex(ValueError,'terminal'):q.accept(self.a['node_id'],self.result(p,'queued'))
 def test_complete_requires_readback_and_is_green(self):
  q,p=self.proposal()
  with self.assertRaisesRegex(ValueError,'read-back'):q.accept(self.a['node_id'],self.result(p,'complete'))
  q.accept(self.a['node_id'],self.result(p,'complete',readback_verified=True,image_sha256='a'*64))
  store=WorkflowStore(self.config);store.sync_archive_events(self.user,q.read()['requests'],'balances');n=store.notifications(self.user)['notifications'][0]
  self.assertEqual(n['severity'],'green');self.assertTrue(n['read']);self.assertTrue(q.snapshot()['requests'][0]['can_cancel'])
 def test_notice_red_read_on_either_page_and_dismissal_persists(self):
  q,p=self.proposal();q.accept(self.a['node_id'],self.result(p,'deleted'));store=WorkflowStore(self.config)
  store.sync_archive_events(self.user,q.read()['requests'],'nodes');n=store.notifications(self.user)['notifications'][0]
  self.assertEqual(n['severity'],'red');self.assertFalse(n['read']);self.assertNotIn('completed',n)
  store.sync_archive_events(self.user,q.read()['requests'],'balances');self.assertEqual(store.notifications(self.user)['unread_count'],0)
  store.update_notifications(self.user,delete=n['id']);store=WorkflowStore(self.config);store.sync_archive_events(self.user,q.read()['requests'],'nodes');self.assertEqual(store.notifications(self.user)['notifications'],[])
  store.sync_archive_events('other-profile',q.read()['requests'],'notifications');self.assertEqual(store.notifications('other-profile')['unread_count'],0);self.assertEqual(len(store.notifications('other-profile')['notifications']),1)
 def test_wallet_page_does_not_mark_other_notices_read(self):
  q,p=self.proposal();q.accept(self.a['node_id'],self.result(p,'deleted'));store=WorkflowStore(self.config)
  with __import__('storage').storage_lock(self.config):
   s=store._read();s['notices'][self.user]=[dict(id='unrelated',kind='message',text='Example',read=False)];store._write(s)
  store.sync_archive_events(self.user,q.read()['requests'],'balances');self.assertEqual(store.notifications(self.user)['unread_count'],1)
 def test_queued_error_remain_and_disabled_rejected(self):
  q,p=self.proposal()
  for status in ('queued','error'):
   q.accept(self.a['node_id'],self.result(p,status));self.assertEqual(len(q.proposal(self.a['node_id'])['requests']),1)
  with self.assertRaisesRegex(ValueError,'Invalid'):q.accept(self.a['node_id'],self.result(p,'disabled'))
 def test_wrong_node_and_tampered_deleted_result_rejected(self):
  q,p=self.proposal();r=self.result(p,'deleted')
  with self.assertRaises(ValueError):q.accept(self.b['node_id'],r)
  r['status']='complete'
  with self.assertRaises(ValueError):q.accept(self.a['node_id'],r)
 def test_late_local_outcome_after_withdrawal_retained(self):
  q,p=self.proposal();q.cancel(self.user,p['requests'][0]['request_id']);q.accept(self.a['node_id'],self.result(p,'deleted'));self.assertEqual(q.read()['requests'][p['requests'][0]['request_id']]['state'],'deleted')
