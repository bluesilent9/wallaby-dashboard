"""Live archive eligibility, selected Agent routing and the options endpoint."""
import copy,http.client,json,threading
from unittest.mock import patch
from test_build8 import Setup
from archive_queue import ArchiveQueue
class Archives(Setup):
 def setUp(self):
  super().setUp();self.queue=ArchiveQueue(self.hub)
  for node in (self.a,self.b):self.hub.caps[node['node_id']]={'wallet_archive_queue_v1'}
  w=self.hub.inventories[self.b['node_id']]['wallets'][0];w['private_keys_enabled']=True;w['raw']['wallet_name']=w['name']
  def rpc(node,method,params=None,wallet=None):
   local=next(w for w in self.hub.inventories[node]['wallets'] if w['name']==wallet)
   return {'private_keys_enabled':True,'walletname':wallet} if method=='getwalletinfo' else local['raw']
  self.hub.rpc=rpc
 def test_only_selected_agent_gets_request(self):
  options=self.queue.options(self.rid);self.assertEqual(len(options),2);chosen=next(r for r in options if r['node_id']==self.b['node_id']);self.queue.create(self.user,self.rid,[chosen['id']]);self.assertEqual(self.queue.proposal(self.a['node_id'])['requests'],[]);self.assertEqual(len(self.queue.proposal(self.b['node_id'])['requests']),1)
 def test_disconnected_selected_agent_is_rejected_before_queueing(self):
  selected=self.queue.options(self.rid)[0];self.registry.heartbeat(next(n['connection_token'] for n in (self.a,self.b) if n['node_id']==selected['node_id']),disconnect=True)
  with self.assertRaisesRegex(ValueError,'connected'):self.queue.create(self.user,self.rid,[selected['id']])
  self.assertFalse(self.queue.read()['requests'])
 def test_options_http_requires_login_and_exposes_only_selection_metadata(self):
  from launch_wallaby_bitcoin_dashboard import create_server
  server,sessions=create_server(__import__('pathlib').Path(self.tmp.name)/'cache',0,self.config);sessions.nodes.health.closed=True;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();session=sessions.create()
  def get():
   c=http.client.HTTPConnection('::1',server.server_port,timeout=5);c.request('GET','/api/wallets/archive-options?wallet_id=fixture',headers={'X-Session-Token':session.token});r=c.getresponse();data=json.loads(r.read());c.close();return r.status,data
  try:
   self.assertEqual(get()[0],400);session.user_id=self.user
   with patch.object(ArchiveQueue,'options',return_value=[{'id':'selection','node_id':'agent','hostname':'Fixture','wallet':'Local wallet','compatible':True,'public_wallet':self.wallet['raw'],'related_policies':[]}]) as options:
    code,data=get();self.assertEqual(code,200);self.assertEqual(data['sources'][0]['id'],'selection');self.assertNotIn('public_wallet',data['sources'][0]);self.assertNotIn('related_policies',data['sources'][0]);options.assert_called_once_with('fixture')
  finally:server.shutdown();server.server_close();thread.join(3)
