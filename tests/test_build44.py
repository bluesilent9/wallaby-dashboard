"""Read-only wallet ceremony progress remains accessible during node checks."""
import http.client,json,threading,tempfile,unittest
from pathlib import Path
from launch_wallaby_bitcoin_dashboard import create_server
from preferences import FamilyStore
class Progress(unittest.TestCase):
 def test_progress_get_needs_login_but_not_a_ready_node_and_actions_stay_guarded(self):
  with tempfile.TemporaryDirectory() as tmp:
   cfg=Path(tmp)/'config.cfg';family=FamilyStore(cfg);family.save_user('Example','green',revision=0);uid=family.snapshot()['users'][0]['id'];server,pool=create_server(Path(tmp)/'cache',0,cfg,ipv6=False);pool.nodes.health.closed=True;session=pool.create();thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   def req(method,url):
    c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);c.request(method,url,'{}' if method=='POST' else None,{'X-Session-Token':session.token,'Content-Type':'application/json'});r=c.getresponse();body=json.loads(r.read());c.close();return r.status,body
   try:
    self.assertEqual(req('GET','/api/wallet-builder')[0],400);session.user_id=uid
    self.assertFalse(pool.nodes.ready_for_builders());code,data=req('GET','/api/wallet-builder');self.assertEqual(code,200,data);self.assertEqual(data['nodes'],[]);self.assertEqual(data['sessions'],[])
    code,data=req('POST','/api/wallet-builder/discover');self.assertEqual(code,400);self.assertIn('synchronised',data['error'])
   finally:server.shutdown();server.server_close();thread.join(3)
