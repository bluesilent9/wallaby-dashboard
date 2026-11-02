"""Blocked reviews remain explainable and can be refreshed without replaying outcomes."""
import copy,time,unittest
from unittest.mock import patch
import test_build9 as previous
from agent_hub import digest
from wallet_sync_queue import schedule
from sync_status import reason

class StalledReviews(previous.PreviewTests):
 def expire(self):
  preview=copy.deepcopy(self.preview);preview['generated_at']=int(time.time())-3600;p=self.signed_preview(preview)
  with patch('wallet_transfers.time.time',return_value=time.time()-3600):self.send(p)
  with self.hub.edit() as state:schedule(self.hub.transfers,state)
  self.hub.inventory_times={n['node_id']:time.monotonic() for n in (self.a,self.b)}
  return self.source['transfer_id']
 def test_visible_stale_queue_remains_available_without_restarting_a_transfer(self):
  ident=self.expire();state=self.hub.read();row=self.hub.transfers.summaries(state,self.b['node_id'])[0]
  self.assertEqual(len(state['nodes'][self.b['node_id']]['sync_snapshot']),1);self.assertIsNone(row['blocked_code']);self.assertTrue(row['review_available']);self.assertFalse(row['fresh_review_available']);self.assertEqual(state['nodes'][self.b['node_id']]['sync_blockers'],{})
 def test_confirmed_refresh_creates_fresh_requests_and_preserves_late_outcome(self):
  ident=self.expire()
  with self.hub.edit() as state:
   next(r for r in state['requests'].values() if r['direction']=='send')['preview_notice']='Changed snapshot requires reconciliation.'
  before=copy.deepcopy(self.hub.read()['requests'])
  with self.assertRaises(ValueError):self.hub.transfers.refresh_reviews([ident],False)
  self.assertEqual(before,self.hub.read()['requests']);result=self.hub.transfers.refresh_reviews([ident],True);self.assertEqual(result['created'],1);self.assertFalse(any(w['id']=='wallet-sync-'+ident for w in self.hub.sync_warnings()));state=self.hub.read();old=next(r for r in state['requests'].values() if r['direction']=='receive' and r['transfer_id']==ident);self.assertTrue(old['superseded_attempt']);self.assertEqual(old['state'],'pending')
  self.result(self.b,self.target,'complete');state=self.hub.read();self.assertEqual(next(r for r in state['requests'].values() if r['direction']=='receive' and r['transfer_id']==ident)['state'],'complete')
 def test_changed_peer_explains_zero_queue_and_allows_deliberate_refresh(self):
  with self.hub.edit() as state:
   state['nodes'][self.b['node_id']]['ipv6']='2001:db8::99';schedule(self.hub.transfers,state)
  row=self.hub.transfers.summaries(self.hub.read(),self.b['node_id'])[0];self.assertEqual(row['blocked_code'],'peer_changed');self.assertTrue(row['fresh_review_available']);self.hub.inventory_times={n['node_id']:time.monotonic() for n in (self.a,self.b)};self.assertEqual(self.hub.transfers.refresh_reviews([self.source['transfer_id']],True)['created'],1)
 def test_received_wallet_needing_history_is_never_bulk_retried(self):
  self.result(self.b,self.target,'recovery_required');r=self.hub.transfers.refresh_reviews([self.source['transfer_id']],True);self.assertEqual(r['created'],0);self.assertEqual(self.hub.transfers.summaries(self.hub.read(),self.b['node_id'])[0]['blocked_code'],'history_recovery')
 def test_refresh_validation_and_race_does_not_claim_success(self):
  for value in ([],[{}],['bad'],['a'*64,'a'*64]):
   with self.assertRaises(ValueError):self.hub.transfers.refresh_reviews(value,True)
  self.assertEqual(self.hub.transfers.refresh_reviews(['f'*64],True)['created'],0)
 def test_stale_permissions_remain_visible_as_checking_not_missing(self):
  with patch.object(self.hub.permissions,'permits',return_value=None):
   rows=self.hub.transfers.summaries(self.hub.read(),self.b['node_id']);self.assertEqual(rows[0]['blocked_code'],'permissions_pending');self.assertFalse(rows[0]['fresh_review_available'])

from test_build8 import Setup
from descriptor_store import clean_wallet
from test_node_loading import public_wallet
class BulkThreeNodes(Setup):
 def test_eleven_stalled_wallets_refresh_for_both_receivers_without_changing_old_outcomes(self):
  third=self.register('02:80:00:00:00:03','third');self.hub.refresh_inventory=lambda *a:None;wallets=[]
  for i in range(11):
   w=clean_wallet(public_wallet('Fixture '+str(i),100+i));wallets.append({'name':w['name'],'id':w['id'],'raw':w['raw'],'private_keys_enabled':False,'scanning':False})
  self.hub.inventories[self.a['node_id']]={'complete':True,'wallets':wallets}
  for n in (self.b,third):self.hub.inventories[n['node_id']]={'complete':True,'wallets':[]}
  for n,ip in ((self.a,'2001:db8::1'),(self.b,'2001:db8::2'),(third,'2001:db8::3')):self.exchange(n,caps=['wallet_transfer'],node_info={'ipv6':ip})
  with self.hub.edit() as state:
   # Scheduler fixture only; signature activation is covered separately by real
   # SSH-signed tests. Changed snapshots require deliberate new attempts.
   for row in state['requests'].values():
    if row['direction']=='send':row['signed_preview']={'preview':{'generated_at':int(time.time())-3600,'transfer_id':row['transfer_id']}}
   schedule(self.hub.transfers,state)
   for row in state['requests'].values():
    if row['direction']=='send':row['preview_notice']='Changed snapshot requires reconciliation.'
  old=set(self.hub.transfers.pairs(self.hub.read()));self.assertEqual(len(old),22)
  for n in (self.b,third):
   self.assertEqual(len([r for r in self.hub.transfers.summaries(self.hub.read(),n['node_id']) if r['fresh_review_available']]),11)
  self.hub.inventory_times={n['node_id']:time.monotonic() for n in (self.a,self.b,third)};self.assertEqual(self.hub.transfers.refresh_reviews(sorted(old),True)['created'],22)
  for n in (self.b,third):
   q=next(p for p in self.exchange(n,caps=['wallet_transfer'])['proposals'] if p['kind']=='wallet_sync_queue');self.assertEqual(len(q['requests']),11);self.assertTrue(all(r['transfer_id'] not in old for r in q['requests']))
  self.assertTrue(all(r['state']=='pending' and r['superseded_attempt'] for r in self.hub.read()['requests'].values() if r['transfer_id'] in old))
