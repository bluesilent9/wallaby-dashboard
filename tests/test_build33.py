import copy,json,os,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
import test_build9 as previous
from agent_hub import digest
from wallet_sync_queue import schedule
from transaction_workflows import WorkflowStore
from preferences import FamilyStore

class Routes(previous.PreviewTests):
 def change(self):
  self.hub.inventory_times={n['node_id']:time.monotonic() for n in (self.a,self.b)}
  with self.hub.edit() as state:
   state['nodes'][self.b['node_id']]['ipv6']='2001:db8::99';schedule(self.hub.transfers,state)
  rows=[r for r in self.hub.read()['requests'].values() if not r.get('superseded_attempt')];self.assertEqual(len(rows),2);return rows
 def test_new_route_renews_public_invitation_without_changing_identity_or_replaying_approval(self):
  self.send(self.signed_preview());rows=self.change();self.assertTrue(all(r['state']=='pending' and not r.get('signed_preview') for r in rows));self.assertEqual({r['node_id'] for r in rows},{self.a['node_id'],self.b['node_id']});self.assertNotEqual(rows[0]['transfer_id'],self.source['transfer_id']);self.assertTrue(all(r['proposal']['preview_challenge']!=self.source['preview_challenge'] for r in rows));self.assertEqual(next(r for r in rows if r['direction']=='receive')['proposal']['node_ipv6'],'2001:db8::99')
  again=self.exchange(self.b,caps=['wallet_transfer']);self.assertEqual(len(next(p for p in again['proposals'] if p['kind']=='wallet_sync_queue')['requests']),1);self.assertEqual(len(self.hub.transfers.pairs(self.hub.read())),2)
 def test_renewed_signed_route_keeps_active_agent_operation_and_requires_new_local_selection(self):
  original=self.signed_preview();self.send(original);old=next(p for p in self.exchange(self.b,caps=['wallet_transfer'])['proposals'] if p['kind']=='wallet_sync_queue');rows=self.change();self.source=next(p for p in self.exchange(self.a,caps=['wallet_transfer'])['proposals'] if p['kind']=='wallet_sync_queue')['requests'][0];v=copy.deepcopy(self.preview);v.update(transfer_id=self.source['transfer_id'],challenge=self.source['preview_challenge'],sender_ipv6=self.source['node_ipv6'],receiver_ipv6=self.source['peer']['ipv6']);fresh=self.signed_preview(v);self.send(fresh);reply=self.exchange(self.b,caps=['wallet_transfer']);new=next(p for p in reply['proposals'] if p['kind']=='wallet_sync_queue');wire=next(p for p in reply['proposals'] if p['kind']=='wallet_transfer_preview');data={'old':old,'new':new,'old_preview':{k:original[k] for k in ('preview','public_key','signature')},'new_preview':{k:wire[k] for k in ('preview','public_key','signature')}};file=Path(self.tmp.name)/'routes.json';file.write_text(json.dumps(data));out=json.loads(subprocess.check_output([sys.executable,str(Path(__file__).with_name('agent_route_runner.py')),os.environ['WALLABY_AGENT_SOURCE'],str(file)]));self.assertEqual(out,{'active_preserved':True,'new_preview':True,'blocked_parallel':True})
 def test_missing_source_is_not_reoffered_just_because_its_address_changed(self):
  self.hub.inventories[self.a['node_id']]['wallets']=[];self.hub.inventory_times={n['node_id']:time.monotonic() for n in (self.a,self.b)}
  with self.hub.edit() as state:state['nodes'][self.b['node_id']]['ipv6']='2001:db8::99';schedule(self.hub.transfers,state)
  self.assertEqual(len(self.hub.transfers.pairs(self.hub.read())),1)
 def test_late_old_completion_is_kept_and_stops_the_replacement_invitation(self):
  self.change();self.result(self.b,self.target,'complete');state=self.hub.read();old=next(r for r in state['requests'].values() if r['direction']=='receive' and r['transfer_id']==self.source['transfer_id']);self.assertEqual(old['state'],'complete');fresh=[r for r in state['requests'].values() if r.get('route_refresh')];self.assertTrue(all(r['state']=='pending' and not r['queue_visible'] for r in fresh));self.assertEqual(state['nodes'][self.b['node_id']]['sync_snapshot'],[])
 def test_history_recovery_and_uncertain_operations_are_not_automatically_restarted(self):
  self.result(self.b,self.target,'recovery_required');self.hub.inventory_times={n['node_id']:time.monotonic() for n in (self.a,self.b)}
  with self.hub.edit() as state:state['nodes'][self.b['node_id']]['ipv6']='2001:db8::99';schedule(self.hub.transfers,state)
  self.assertEqual(len(self.hub.transfers.pairs(self.hub.read())),1)
 def test_mac_identity_survives_hostname_and_connection_address_change(self):
  self.registry.heartbeat(self.b['connection_token'],disconnect=True);new=self.register('02:80:00:00:00:02','renamed-machine');self.assertEqual(new['node_id'],self.b['node_id']);self.assertEqual(len(self.registry._read()['nodes']),2)
 def test_discovery_is_independent_of_dashboard_owners_and_logged_in_users(self):
  with FamilyStore(self.config).transaction() as data:data['ownership']=[];data['users']=[]
  reply=self.exchange(self.b,caps=['wallet_transfer']);self.assertEqual(len(next(p for p in reply['proposals'] if p['kind']=='wallet_sync_queue')['requests']),1)
 def test_nodes_never_claim_wallets_checked_when_inventory_is_missing(self):
  self.hub.inventories.pop(self.b['node_id']);self.assertEqual(self.hub.node_summary(self.a['node_id'])['synchronisation']['state'],'checking')
 def test_ownership_reminder_reappears_until_assignment_then_clears(self):
  flow=WorkflowStore(self.config)
  with FamilyStore(self.config).transaction() as data:data['ownership']=[]
  flow.discovered([self.user],[self.wallet['id']],[self.user]);notice=flow.notifications(self.user)['notifications'][0];self.assertTrue(notice['read']);flow.update_notifications(self.user,delete=notice['id']);flow.discovered([self.user],[self.wallet['id']]);self.assertEqual(flow.notifications(self.user)['unread_count'],1)
  with FamilyStore(self.config).transaction() as data:data['ownership']=[{'id':self.rid,'owner_id':self.user}]
  flow.discovered([self.user],[self.wallet['id']]);self.assertEqual(flow.notifications(self.user)['notifications'],[])
 def test_agent_accepts_zero_revision_in_new_session_but_rejects_same_session_rollback(self):
  source=os.environ['WALLABY_AGENT_SOURCE'];file=Path(self.tmp.name)/'session.json';file.write_text(json.dumps({'proposal':self.target}));runner=Path(__file__).with_name('agent_session_runner.py');out=json.loads(subprocess.check_output([sys.executable,str(runner),source,str(file)]));self.assertTrue(out['rejected_rollback']);self.assertTrue(out['rejected_retired']);self.assertEqual(out['visible'],1)

class PermissionRefresh(previous.PreviewTests):
 def test_verified_offer_survives_poll_delay_without_erasing_local_disable_choices(self):
  import test_build12 as helpers
  from sync_permissions import policy_id
  case=helpers.Permissions('test_wait_for_current_permissions_before_offering_missing_wallet');case.setUp();self.addCleanup(case.doCleanups)
  case.hub.refresh_inventory=lambda *a:None;case.hub.inventories[case.b['node_id']]['wallets']=[]
  p=case.identity(case.b);case.receive(case.b,case.response(case.b,p,[]))
  for n,ip in ((case.a,'2001:db8::1'),(case.b,'2001:db8::2')):case.exchange(n,caps=['wallet_transfer'],node_info={'ipv6':ip})
  before=copy.deepcopy(case.hub.read()['nodes'][case.b['node_id']]['sync_snapshot']);self.assertEqual(len(before),1)
  with patch('sync_permissions.time.monotonic',return_value=time.monotonic()+46):
   self.assertIsNone(case.hub.permissions.permits(case.b['node_id'],case.wallet['raw']))
   with case.hub.edit() as state:schedule(case.hub.transfers,state)
   self.assertEqual(case.hub.read()['nodes'][case.b['node_id']]['sync_snapshot'],before)
  p=case.poll_request(case.b);case.receive(case.b,case.response(case.b,p,[policy_id(case.wallet['raw'])],2))
  with patch('sync_permissions.time.monotonic',return_value=time.monotonic()+46):
   self.assertFalse(case.hub.permissions.allows_offer(case.b['node_id'],case.wallet['raw']))
   with case.hub.edit() as state:schedule(case.hub.transfers,state)
   self.assertEqual(case.hub.read()['nodes'][case.b['node_id']]['sync_snapshot'],[])

import unittest
class PublicIdentityCache(unittest.TestCase):
 def test_unchanged_records_reuse_validation_but_mutations_revalidate_and_reject(self):
  import sync_permissions as permissions
  from test_node_loading import public_wallet
  raw=public_wallet('Fixture',111);permissions._cached_policy.cache_clear()
  with patch.object(permissions,'_derive_policy',wraps=permissions._derive_policy) as derive:
   expected=permissions.policy_id(raw);self.assertEqual(permissions.policy_id(copy.deepcopy(raw)),expected);self.assertEqual(derive.call_count,1)
   renamed=copy.deepcopy(raw);renamed['wallet_name']='Renamed';self.assertEqual(permissions.policy_id(renamed),expected);self.assertEqual(derive.call_count,2)
   bad=copy.deepcopy(raw);bad['descriptors'][0]['desc']+='bad'
   with self.assertRaises(ValueError):permissions.policy_id(bad)
   self.assertEqual(derive.call_count,3);self.assertEqual(permissions.policy_id(raw),expected);self.assertEqual(derive.call_count,3)
