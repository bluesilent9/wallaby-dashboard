"""Specific readiness diagnostics and cancellation without invented deletion reversals."""
import test_build18 as previous
from wallet_retirement import cancel,snapshot,result,advance,readiness
from agent_state import validate

class Cancellation(previous.Retirement):
 def test_unsent_cancel_stops_delivery_and_survives_profile_validation(self):
  key=self.request()['request_hash'];cancel(self.hub,self.user,key,True);state=self.hub.read();row=state['requests'][key]
  self.assertEqual(row['state'],'cancelled');self.assertFalse(row.get('delivered'));self.assertTrue(row['cancelled_before_delivery']);validate(state,{self.user})
  self.assertFalse([p for p in self.exchange(self.a,caps=['wallet_retirement_v2'])['proposals'] if p['kind']=='wallet_retire'])
 def test_delivered_cancel_waits_for_capable_agent_and_preserves_actual_completion(self):
  key=self.delivered();cancel(self.hub,self.user,key,True);self.assertEqual(snapshot(self.hub,self.user)[0]['state'],'cancelling')
  old=self.exchange(self.a,caps=['wallet_retirement_v2']);self.assertFalse([p for p in old['proposals'] if p['kind'].startswith('wallet_retire')])
  current=self.exchange(self.a,caps=['wallet_retirement_v2','wallet_retirement_cancel_v1']);wire=next(p for p in current['proposals'] if p['kind']=='wallet_retire_cancel');self.assertEqual(wire['request_hash'],key)
  # If deletion won the race, record the truth rather than pretending cancellation undid it.
  with self.hub.edit() as state:result(state,self.a['node_id'],self.proof(key))
  view=snapshot(self.hub,self.user)[0];self.assertEqual(view['state'],'cancelled');self.assertTrue(view['holders'][0]['wallet_deleted']);self.assertTrue(view['holders'][0]['archive_verified'])
 def test_cancellation_requires_initiator_and_explicit_confirmation(self):
  key=self.delivered()
  for user,confirmed in [('foreign',True),(self.user,False)]:
   with self.assertRaises(ValueError):cancel(self.hub,user,key,confirmed)
  view=snapshot(self.hub,'foreign')[0];self.assertFalse(view['can_cancel']);self.assertFalse(view['can_confirm_discs'])
 def test_cancelled_group_does_not_queue_new_offline_holders(self):
  with self.hub.edit() as state:state['nodes'].setdefault(self.b['node_id'],{})['known_wallets']={'known':{'raw':self.wallet['raw'],'name':'different_watch_name'}}
  key=self.request()['request_hash'];cancel(self.hub,self.user,key,True);self.hub.inventories[self.b['node_id']]=self.watch_copy;self.hub.caps[self.b['node_id']]={'wallet_retirement_v2'};advance(self.hub)
  self.assertEqual(len([r for r in self.hub.read()['requests'].values() if r.get('root_hash')==key]),1)
 def test_eligible_private_and_watch_nodes_are_identified_and_failures_explained(self):
  self.hub.inventories[self.b['node_id']]=self.watch_copy;self.hub.caps[self.b['node_id']]={'wallet_retirement_v2'}
  report=readiness(self.hub,self.user,self.rid);self.assertEqual(len(report['options']),2);self.assertTrue(all(n['eligible'] for n in report['nodes']))
  self.hub.caps[self.a['node_id']]=set();self.hub.inventories[self.b['node_id']]={'complete':False,'error':'A node wallet is unloaded'}
  report=readiness(self.hub,self.user,self.rid);self.assertFalse(report['options']);self.assertTrue(any('wallet_retirement_v2' in reason for n in report['nodes'] for reason in n['reasons']));self.assertTrue(any('unloaded' in reason for n in report['nodes'] for reason in n['reasons']))
 def test_outdated_card_is_not_misreported_as_agent_upgrade(self):
  with self.assertRaisesRegex(ValueError,'Ownership'):readiness(self.hub,self.user,'missing-id')

 def test_build17_capability_rejection_explains_connected_but_no_retirement(self):
  from unittest.mock import patch
  old={'node_identity','partial_signing','broadcast','wallet_transfer','wallet_retirement'}
  with patch('agent_hub.CAPS',old):
   with self.assertRaisesRegex(ValueError,'Unsupported Agent capabilities'):self.exchange(self.a,caps=['wallet_retirement','wallet_retirement_v2'])
  self.assertTrue(self.registry.snapshot()['nodes'][0]['connected'])
  self.exchange(self.a,caps=['wallet_retirement','wallet_retirement_v2'])
  self.assertTrue(readiness(self.hub,self.user,self.rid)['options'])

for name in previous.Retirement.__dict__:
 if name.startswith("test_") and name not in Cancellation.__dict__:setattr(Cancellation,name,None)


class Estimator(__import__('unittest').TestCase):
 def test_offsets_dst_and_timezone_order(self):
  from vault_estimator import timezones_with_offsets
  data=timezones_with_offsets(2026);self.assertEqual(data['details']['Australia/Darwin']['offsets'],[34200]);self.assertIn('UTC+09:30',data['details']['Australia/Darwin']['label']);self.assertIn('DST',data['details']['Australia/Sydney']['label'])
  self.assertEqual(data['timezones'],sorted(data['timezones'],key=lambda z:(data['details'][z]['offsets'][0],z.casefold())))
 def test_seven_local_and_future_independent_probability(self):
  from vault_estimator import target_at_seven,estimate,poisson_cdf_left
  from datetime import datetime,timezone
  self.assertEqual(target_at_seven('2027-01-01','Australia/Darwin'),'2026-12-31T21:30:00+00:00')
  for days in (1,30,365,3650):
   now=datetime(2026,10,31,tzinfo=timezone.utc);target=now+__import__('datetime').timedelta(days=days);r=estimate(973934,target.isoformat(),now)
   self.assertGreaterEqual(r['confidence'],.99);k=r['required_new_blocks'];self.assertGreater(poisson_cdf_left(k,r['expected_blocks']),.01)
   self.assertLess(datetime.fromisoformat(r['most_likely_utc']),target)
 def test_node_only_sync_and_invalid_date_guards(self):
  from vault_estimator import from_nodes,target_at_seven
  from unittest.mock import patch
  from types import SimpleNamespace
  from datetime import datetime,timezone
  info={'chain':'main','initialblockdownload':False,'blocks':973934,'headers':973934}
  registry=SimpleNamespace(snapshot=lambda:{'nodes':[{'id':'node','hostname':'Example','connected':True,'rpc_ready':True}]},rpc=SimpleNamespace(call=lambda *a,**k:{'state':'succeeded','result':info}))
  r=from_nodes(registry,'2027-01-01','Australia/Darwin',datetime(2026,10,31,tzinfo=timezone.utc));self.assertGreater(r['height'],973934)
  info['initialblockdownload']=True
  with self.assertRaisesRegex(ValueError,'synchronise'):from_nodes(registry,'2027-01-01','UTC')
  with self.assertRaises(ValueError):target_at_seven('bad','bad')
