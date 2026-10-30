"""Fresh Core health controls builder access; diagnose without logging credentials."""
import http.client,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nodes import NodeRegistry,create_agent_server
from agent_rpc import METHODS
from node_health import classify
from preferences import FamilyStore
from launch_wallaby_bitcoin_dashboard import create_server

class Readiness(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.reg=NodeRegistry(Path(self.tmp.name)/'config.cfg');self.reg.health.closed=True
 def register(self,mac='02:00:00:00:00:01',host='fixture',rpc=True):return self.reg.register(mac,host,{'version':1,'methods':sorted(METHODS)} if rpc else None,'2.0.0~dev14')
 def health(self,n,**override):self.reg.health.values[n['connection_token']]=classify(dict({'chain':'main','blocks':100,'headers':100,'initialblockdownload':False},**override))
 def test_presence_and_rpc_are_not_sufficient_without_fresh_core_health(self):
  self.assertFalse(self.reg.ready_for_builders());n=self.register();self.assertFalse(self.reg.ready_for_builders());self.health(n);self.assertTrue(self.reg.ready_for_builders())
 def test_downloading_stale_failed_syncing_and_disconnected_are_blocked(self):
  n=self.register();token=n['connection_token'];self.health(n,headers=101);self.assertFalse(self.reg.ready_for_builders());self.health(n,initialblockdownload=True);self.assertFalse(self.reg.ready_for_builders());self.health(n);self.reg.health.values[token]['checked_at']-=61;self.assertFalse(self.reg.ready_for_builders());self.reg.health.values[token]={'state':'error','checked_at':time.monotonic()};self.assertFalse(self.reg.ready_for_builders());self.health(n);self.reg.hub.transfers.busy.add(n['node_id']);self.assertFalse(self.reg.ready_for_builders());self.reg.hub.transfers.busy.clear();self.assertTrue(self.reg.ready_for_builders());self.reg.heartbeat(token,disconnect=True);self.assertFalse(self.reg.ready_for_builders())
 def test_conflict_blocks_both_claims_and_another_healthy_node_can_unlock(self):
  a=self.register();self.health(a);b=self.register(host='duplicate');self.health(b);self.assertFalse(self.reg.ready_for_builders());c=self.register('02:00:00:00:00:02','healthy');self.health(c);self.assertTrue(self.reg.ready_for_builders())
 def test_presence_only_cannot_unlock_even_with_a_cached_healthy_status(self):
  n=self.register(rpc=False);self.health(n);self.assertFalse(self.reg.ready_for_builders())
 def test_registration_records_version_but_not_session_token(self):
  n=self.register();text=self.reg.log_text();self.assertIn('Registration accepted',text);self.assertIn('2.0.0~dev14',text);self.assertNotIn(n['connection_token'],text)
 def test_exception_diagnostic_has_code_location_not_request_payload(self):
  try:raise TypeError('Fixture validation error')
  except TypeError as e:self.reg.protocol_error('/agent/v1/poll',e)
  text=self.reg.log_text();self.assertIn('test_build15.py:',text);self.assertIn('Fixture validation error',text)

class Endpoints(unittest.TestCase):
 def test_builder_guard_is_enforced_server_side_and_ownership_remains_required(self):
  with tempfile.TemporaryDirectory() as d:
   cfg=Path(d)/'config.cfg';store=FamilyStore(cfg);store.save_user('Example','green',revision=0)
   server,sessions=create_server(Path(d)/'cache',0,cfg);sessions.nodes.health.closed=True;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
   session=sessions.create();session.user_id=store.snapshot()['users'][0]['id'];session.access=lambda:{'ownership_ready':False,'templates_ready':False}
   def post(path,body={}):
    c=http.client.HTTPConnection('::1',server.server_port,timeout=5);c.request('POST',path,json.dumps(body),{'X-Session-Token':session.token,'Content-Type':'application/json'});r=c.getresponse();data=json.loads(r.read());c.close();return r.status,data
   try:
    with patch.object(sessions.nodes,'ready_for_builders',return_value=False):
     code,data=post('/api/wallet-builder/discover');self.assertEqual(code,400);self.assertIn('synchronised',data['error'])
    with patch.object(sessions.nodes,'ready_for_builders',return_value=True),patch.object(sessions.nodes.hub.wallet_builder,'discover',return_value={'ok':True}):
     self.assertEqual(post('/api/wallet-builder/discover'),(200,{'ok':True}));code,data=post('/api/builder/prepare');self.assertEqual(code,400);self.assertIn('Ownership',data['error'])
   finally:server.shutdown();server.server_close();t.join(3)
 def test_invalid_agent_headers_and_unexpected_failures_enter_bounded_log(self):
  with tempfile.TemporaryDirectory() as d:
   reg=NodeRegistry(Path(d)/'config.cfg');reg.health.closed=True;server=create_agent_server(reg,0);server.start()
   def post(headers,body):
    c=http.client.HTTPConnection('::1',server.server_port,timeout=5);c.request('POST','/agent/v1/register',json.dumps(body),headers);r=c.getresponse();out=json.loads(r.read());c.close();return r.status,out
   try:
    self.assertEqual(post({'Content-Type':'application/json'},{} )[0],403);self.assertIn('loopback Host',reg.log_text())
    with patch.object(reg,'register',side_effect=RuntimeError('Fixture internal fault')):
     code,data=post({'Content-Type':'application/json','X-Wallaby-Agent':'1'},{'mac':'02:00:00:00:00:01','hostname':'example'});self.assertEqual(code,500);self.assertNotIn('Fixture internal fault',data['error']);self.assertIn('Fixture internal fault',reg.log_text())
   finally:server.close()

if __name__=='__main__':unittest.main()
