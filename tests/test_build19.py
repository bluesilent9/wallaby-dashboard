"""Cancellation at both phase boundaries and estimator regressions."""
import test_build18 as previous
from wallet_retirement import cancel,snapshot,result,advance,readiness,CAPABILITY
from agent_state import validate
class Cancellation(previous.Retirement):
 def test_cancel_before_preservation_delivery(self):
  key=self.request()['request_hash'];cancel(self.hub,self.user,key,True);state=self.hub.read();self.assertEqual(state['requests'][key]['state'],'cancelled');validate(state,{self.user})
  self.assertFalse([p for p in self.exchange(self.a,caps=[CAPABILITY])['proposals'] if p['kind']=='wallet_retire'])
 def test_cancel_delivered_preservation_does_not_release_deletion(self):
  key=self.delivered();cancel(self.hub,self.user,key,True);wire=self.exchange(self.a,caps=[CAPABILITY,'wallet_retirement_cancel_v1'])['proposals'];self.assertTrue(any(p['kind']=='wallet_retire_cancel' for p in wire));self.preserve(key);self.assertIsNone(self.deletion(key));self.assertEqual(snapshot(self.hub,self.user)[0]['state'],'cancelled')
 def test_cancel_after_preservation_stops_undelivered_deletion(self):
  key=self.delivered();self.preserve(key);d=self.deletion(key);cancel(self.hub,self.user,key,True);self.assertEqual(self.hub.read()['requests'][d]['state'],'cancelled');validate(self.hub.read(),{self.user})
 def test_delivered_delete_completion_racing_cancel_is_recorded(self):
  key=self.delivered();self.preserve(key);d=self.deletion(key);self.exchange(self.a,caps=[CAPABILITY]);cancel(self.hub,self.user,key,True)
  with self.hub.edit() as state:result(state,self.a['node_id'],self.proof(d))
  self.assertTrue(snapshot(self.hub,self.user)[0]['holders'][0]['wallet_deleted'])
 def test_cancellation_requires_initiator_and_confirmation(self):
  key=self.delivered()
  for user,confirmed in [('foreign',True),(self.user,False)]:
   with self.assertRaises(ValueError):cancel(self.hub,user,key,confirmed)
 def test_readiness_requires_archive_and_current_capability(self):
  self.assertTrue(readiness(self.hub,self.user,self.rid)['options']);self.hub.caps[self.a['node_id']]={'wallet_retirement_v2'};report=readiness(self.hub,self.user,self.rid);self.assertFalse(report['options']);self.assertTrue(any('wallet_retirement_v3' in v for n in report['nodes'] for v in n['reasons']))
for name in previous.Retirement.__dict__:
 if name.startswith('test_') and name not in Cancellation.__dict__:setattr(Cancellation,name,None)

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
