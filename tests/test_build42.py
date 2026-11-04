"""Busy local Agents must not cause a health retry storm or stale-ready claims."""
import threading,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from node_health import Health
class Inline:
 def __init__(self,target,args=(),**kw):self.target=target;self.args=args
 def start(self):self.target(*self.args)
class BusyHealth(unittest.TestCase):
 def setUp(self):
  self.rpc=Mock(return_value={'state':'failed','error':{'code':'synchronisation_busy'}});self.reg=SimpleNamespace(lock=threading.RLock(),connections={'token':{}},rpc=SimpleNamespace(call=self.rpc),hub=SimpleNamespace(foreground_active=lambda *a:False,transfers=SimpleNamespace(is_busy=lambda node:False),refresh_inventory=Mock()));self.health=Health(self.reg);self.claim={'token':'token','node_id':'node'}
 def test_busy_retry_is_bounded_without_refreshing_verified_health(self):
  self.health.values['token']={'checked_at':1,'state':'ready','label':'Connected'}
  with patch('node_health.threading.Thread',Inline),patch('node_health.time.monotonic') as clock:
   clock.return_value=100;self.health.refresh([self.claim]);self.assertEqual(self.rpc.call_count,1)
   for step in range(1,150):clock.return_value=100+step/10;self.health.refresh([self.claim])
   self.assertEqual(self.rpc.call_count,1);self.assertEqual(self.health.values['token']['checked_at'],1);self.assertEqual(self.health.status(self.claim)['state'],'checking')
   clock.return_value=115;self.health.refresh([self.claim]);self.assertEqual(self.rpc.call_count,2)
   self.rpc.return_value={'state':'succeeded','result':{'chain':'main','blocks':100,'headers':100,'initialblockdownload':False}}
   clock.return_value=130;self.health.refresh([self.claim]);self.assertEqual(self.health.status(self.claim)['state'],'ready');self.reg.hub.refresh_inventory.assert_called_once()
 def test_unknown_busy_node_is_not_marked_ready_and_removed_routes_are_pruned(self):
  with patch('node_health.threading.Thread',Inline),patch('node_health.time.monotonic',return_value=100):
   self.health.refresh([self.claim]);self.assertEqual(self.health.status(self.claim)['state'],'checking');self.health.refresh([]);self.assertFalse(self.health.attempted)
