"""Authenticated stale notices cannot starve other wallets or connected receivers."""
import copy,time
from unittest.mock import patch
import test_build9 as previous
from test_build8 import Setup
from test_node_loading import public_wallet
from descriptor_store import clean_wallet
from agent_hub import digest

class PreviewLifecycle(previous.PreviewTests):
 def test_changed_signed_notice_retains_original_and_accepts_batch(self):
  original=self.signed_preview();self.send(original);new=copy.deepcopy(self.preview);new['node_tip']['height']+=1;new['node_tip']['hash']='b'*64;changed=self.signed_preview(new);item=self.item('wallet_transfer_preview',changed);reply=self.exchange(self.a,[item,self.item('node_identity',{'ipv6':'2001:db8::1'})],caps=['wallet_transfer']);self.assertIn(item['id'],reply['accepted']);sender=next(r for r in self.hub.read()['requests'].values() if r['direction']=='send');self.assertEqual(sender['signed_preview']['preview'],original['preview']);self.assertIn('original frozen',sender['preview_notice']);self.assertTrue(self.hub.transfers.summaries(self.hub.read(),self.a['node_id'])[0]['retryable'])
  with self.assertRaises(ValueError):self.hub.transfers.retry(sender['transfer_id'],False)
  self.hub.inventory_times={n['node_id']:time.monotonic() for n in (self.a,self.b)};retry=self.hub.transfers.retry(sender['transfer_id'],True);self.assertNotEqual(retry['transfer_id'],sender['transfer_id'])
  retained=next(r for r in self.hub.read()['requests'].values() if r['direction']=='send' and r['transfer_id']==sender['transfer_id']);self.assertEqual(retained['signed_preview']['preview'],original['preview'])
 def test_known_late_preview_after_withdrawal_is_acknowledged(self):
  payload=self.signed_preview()
  with self.hub.edit() as state:
   for row in state['requests'].values():row['queue_visible']=False;row['state']='denied'
  self.assertIn(self.item('wallet_transfer_preview',payload)['id'],self.send(payload)['accepted']);self.assertFalse(any(r.get('signed_preview') for r in self.hub.read()['requests'].values()))
 def test_older_bound_notice_retains_immutable_public_review(self):
  p=copy.deepcopy(self.preview);p['generated_at']=int(time.time())-3600;payload=self.signed_preview(p);self.assertIn(self.item('wallet_transfer_preview',payload)['id'],self.send(payload)['accepted']);self.assertTrue(any(r.get('signed_preview') for r in self.hub.read()['requests'].values()))
 def test_forged_changed_notice_is_still_rejected(self):
  payload=self.signed_preview();self.send(payload);payload['preview']['node_tip']['height']+=1
  with self.assertRaises(ValueError):self.send(payload)
 def test_changed_peer_binding_is_still_rejected(self):
  p=copy.deepcopy(self.preview);p['receiver_ipv6']='2001:db8::99'
  with self.assertRaises(ValueError):self.send(self.signed_preview(p))
 def test_unknown_request_is_still_rejected(self):
  p=self.signed_preview();p['request_hash']='f'*64
  with self.assertRaises(ValueError):self.send(p)

class MultipleReceivers(Setup):
 def test_both_cold_nodes_receive_every_missing_wallet(self):
  third=self.register('02:80:00:00:00:03','third');self.hub.refresh_inventory=lambda *a:None
  wallets=[]
  for i in range(11):
   w=clean_wallet(public_wallet('Public wallet '+str(i),i+20));wallets.append({'id':w['id'],'name':w['name'],'raw':w['raw'],'private_keys_enabled':False,'scanning':False})
  self.hub.inventories[self.a['node_id']]={'complete':True,'wallets':wallets}
  for n in (self.b,third):self.hub.inventories[n['node_id']]={'complete':True,'wallets':[]}
  for n,ip in ((self.a,'2001:db8::1'),(self.b,'2001:db8::2'),(third,'2001:db8::3')):self.exchange(n,caps=['wallet_transfer'],node_info={'ipv6':ip})
  for n in (self.b,third):
   reply=self.exchange(n,caps=['wallet_transfer']);q=next(p for p in reply['proposals'] if p['kind']=='wallet_sync_queue');self.assertEqual(len(q['requests']),11);self.assertTrue(all(p['direction']=='receive' for p in q['requests']))
 def test_rotation_advances_only_by_previews_actually_delivered(self):
  # Fifteen snapshots plus three mandatory control queues formerly repeated the
  # same first snapshots forever because the cursor advanced by fifteen.
  node=self.b['node_id'];now=int(time.time())
  with self.hub.edit() as state:
   state['nodes'][node]={'ipv6':'2001:db8::2','sync_revision':0,'sync_snapshot':[]}
   for i in range(15):
    ident=format(i,'064x');source={'kind':'wallet_transfer','direction':'send','node_id':self.a['node_id'],'peer_id':node,'transfer_id':ident,'state':'pending','public_wallet':self.wallet['raw'],'queue_v2':True,'queue_visible':True,'signed_preview':{'preview':{'generated_at':now,'transfer_id':ident},'signature':'test-fixture','public_key':'test-fixture'}};receiver={**source,'direction':'receive','node_id':node,'peer_id':self.a['node_id']};state['requests'][ident+'s']=source;state['requests'][ident+'r']=receiver
  self.hub.refresh_inventory=lambda *a:None
  with patch.object(self.hub,'pair_transfers'),patch.object(self.hub.transfers,'schedule_queues'),patch('wallet_policy_updates.proposals',return_value=[]):
   seen=set()
   for _ in range(3):
    reply=self.exchange(self.b,caps=['wallet_transfer','wallet_archive_queue_v1','wallet_rename_v1']);self.assertLessEqual(len(reply['proposals']),16);seen.update(p['preview']['transfer_id'] for p in reply['proposals'] if p['kind']=='wallet_transfer_preview')
   self.assertEqual(len(seen),15)

class ReenabledPermissions(Setup):
 def test_authenticated_changed_preview_does_not_block_fresh_permissions(self):
  import test_build12 as helpers
  from sync_permissions import policy_id
  case=helpers.Permissions('test_disable_reenable_is_agent_owned_no_persistent_dashboard_denial');case.setUp();self.addCleanup(case.doCleanups)
  req=case.identity(case.b);case.hub.inventories[case.b['node_id']]['wallets']=[];case.receive(case.b,case.response(case.b,req,[policy_id(case.wallet['raw'])]));req=case.poll_request(case.b);case.receive(case.b,case.response(case.b,req,[],2));case.exchange(case.a);self.assertEqual(len(case.hub.read()['requests']),2)
  req=case.identity(case.a);case.receive(case.a,case.response(case.a,req,[],1));case.hub.refresh_inventory=lambda *a:None
  for n,ip in ((case.a,'2001:db8::1'),(case.b,'2001:db8::2')):case.exchange(n,caps=['wallet_transfer'],node_info={'ipv6':ip})
  case.exchange(case.a,caps=['wallet_transfer'])
  source=next(r['proposal'] for r in case.hub.read()['requests'].values() if r['direction']=='send');key=case.keys[case.a['node_id']]
  preview={'version':2,'vaults':[],'address_labels':[],'transfer_id':source['transfer_id'],'challenge':source['preview_challenge'],'sender_ipv6':source['node_ipv6'],'receiver_ipv6':source['peer']['ipv6'],'public_wallet':copy.deepcopy(source['public_wallet']),'chain':'main','funded_outputs':[],'node_tip':{'height':100,'hash':'a'*64},'generated_at':int(time.time())}
  def payload(v):
   e=case.sign(case.a,v,'wallaby-wallet-preview-v1');return {'request_hash':digest(source),'preview':e['message'],'public_key':e['public_key'],'signature':e['signature']}
  original=payload(preview);case.exchange(case.a,[case.item('wallet_transfer_preview',original)],caps=['wallet_transfer']);req=case.poll_request(case.a);fresh=case.item('wallet_sync_preferences',case.response(case.a,req,[],2));preview['node_tip']['height']+=1;changed=case.item('wallet_transfer_preview',payload(preview));reply=case.exchange(case.a,[changed,fresh],caps=['wallet_transfer']);self.assertIn(changed['id'],reply['accepted']);self.assertIn(fresh['id'],reply['accepted']);self.assertEqual(case.hub.permissions.current(case.a['node_id'])['record']['revision'],2)
