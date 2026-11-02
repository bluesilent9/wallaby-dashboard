"""Automatic discovery, persistence and profile data regression tests."""
import copy,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from background_discovery import BackgroundDiscovery
from preferences import FamilyStore,resource_id,validate_birth_date
from descriptor_store import DescriptorStore
from descriptor_snapshot import history_ready,saved_history
from transaction_workflows import WorkflowStore
from profile_archive import ProfileGate
from test_build6 import CoreFixture

class Discovery(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.config=Path(self.tmp.name)/'config.cfg';store=FamilyStore(self.config);store.save_user('Example','green',revision=0,date_of_birth='29/02/2000');self.uid=store.snapshot()['users'][0]['id'];self.core=CoreFixture()
  for w in self.core.wallets:w['public_history']={'outputs':[],'exported_utc':'2026-01-01T00:00:00Z','lastblock':'a'*64}
  self.nodes=[dict(n,connected=True,rpc_ready=True,rpc_methods=['listwallets','listwalletdir','getwalletinfo','listdescriptors','listsinceblock','getaddressinfo'],blockchain={'state':'ready'}) for n in self.core.nodes()]
  reg=SimpleNamespace(snapshot=lambda:{'nodes':self.nodes},diagnostic_log=SimpleNamespace(append=lambda *a:None))
  reg.rpc=SimpleNamespace(call=lambda node,method,params=None,wallet=None,**kw:{'state':'succeeded','result':self.core.call(node,method,params,wallet)})
  reg.hub=SimpleNamespace(read=lambda:{'requests':{}})
  sessions=SimpleNamespace(nodes=reg,preferences_path=self.config,sessions={},profile_gate=ProfileGate())
  self.service=BackgroundDiscovery(sessions,start=False);self.addCleanup(self.service.close)
 def cycle(self,wallets=None):
  with patch('background_discovery.NodeWalletLoader.collect',return_value=copy.deepcopy(wallets if wallets is not None else self.core.wallets)):self.service.cycle()
 def test_complete_all_node_history_and_notice_persist_without_browser(self):
  self.cycle();catalog=DescriptorStore(self.config).read();self.assertTrue(history_ready(self.config,catalog));snapshot=saved_history(self.config,catalog);self.assertEqual(len(snapshot['wallets']),2);self.assertEqual(len(snapshot['transactions']),2)
  notices=WorkflowStore(self.config).notifications(self.uid);self.assertEqual(notices['unread_count'],1)
  fp=DescriptorStore.fingerprint(catalog);updated=copy.deepcopy(self.core.wallets)
  for w in updated:
   w['public_history']['exported_utc']='2026-02-02T00:00:00Z'
   for row in w['node_history']:row['confirmations']+=5
  self.cycle(updated);self.assertEqual(DescriptorStore.fingerprint(DescriptorStore(self.config).read()),fp);self.assertEqual(len(WorkflowStore(self.config).notifications(self.uid)['notifications']),1)
 def test_append_inventory_retains_files_assignments_and_offline_wallet(self):
  self.cycle([self.core.wallets[0]]);old=DescriptorStore(self.config).summary()['fingerprint']
  with FamilyStore(self.config).transaction() as data:data['ownership_runs']={self.uid:{'inventory':[{'id':'exchange','kind':'exchange','name':'Example','source':'Example'}],'files':[{'name':'keep.ods'}],'data_id':'kept','descriptor_fingerprint':old}}
  self.nodes=self.nodes[:1];self.cycle([self.core.wallets[1]])
  catalog=DescriptorStore(self.config).read();self.assertEqual(len(catalog['wallets']),2);meta=FamilyStore(self.config).snapshot()['ownership_runs'][self.uid];self.assertEqual(meta['files'],[{'name':'keep.ods'}]);self.assertEqual(meta['data_id'],'kept');self.assertEqual(len(meta['inventory']),3);self.assertEqual(meta['descriptor_fingerprint'],DescriptorStore.fingerprint(catalog))
 def test_failed_history_keeps_public_metadata_but_never_publishes_partial_ledger(self):
  with patch('background_discovery.NodeWalletLoader.collect',return_value=self.core.wallets),patch('background_discovery.scan',side_effect=ValueError('missing history')):
   with self.assertRaisesRegex(ValueError,'missing history'):self.service.cycle()
  catalog=DescriptorStore(self.config).read();self.assertTrue(DescriptorStore(self.config).summary()['loaded']);self.assertFalse(history_ready(self.config,catalog));self.assertFalse(self.service.activity()['active'])
 def test_read_on_ownership_delete_and_new_user_delivery(self):
  flow=WorkflowStore(self.config);flow.discovered([self.uid],['a'],[self.uid]);self.assertEqual(flow.notifications(self.uid)['unread_count'],0)
  notice=flow.notifications(self.uid)['notifications'][0];flow.update_notifications(self.uid,delete=notice['id']);flow.discovered([self.uid],['a']);self.assertEqual(flow.notifications(self.uid)['notifications'],[])
  flow.discovered(['other'],['a']);self.assertEqual(flow.notifications('other')['unread_count'],1);flow.read_discovery('other');self.assertEqual(flow.notifications('other')['unread_count'],0)
 def test_foreground_work_yields_until_cancelled(self):
  self.service.busy=lambda:True;self.service.stop.set()
  with self.assertRaisesRegex(ValueError,'stopped'):self.service.pause()
 def test_http_profile_creation_rejects_missing_birth_date(self):
  import urllib.request,urllib.error
  from launch_wallaby_bitcoin_dashboard import create_server
  server,pool=create_server(Path(self.tmp.name)/'http',0,self.config,ipv6=False)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   url='http://127.0.0.1:'+str(server.server_port)
   req=urllib.request.Request(url+'/api/session',method='POST',headers={'X-Wallaby-Request':'1'})
   with urllib.request.urlopen(req) as response:token=json.load(response)['token']
   payload={'name':'New Example','colour':'blue','gender':'female','revision':FamilyStore(self.config).snapshot()['revision']}
   def request():return urllib.request.urlopen(urllib.request.Request(url+'/api/users/save',json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-Session-Token':token}))
   with self.assertRaises(urllib.error.HTTPError) as error:request()
   self.assertEqual(error.exception.code,400);self.assertIn('date of birth',error.exception.read().decode())
   payload['date_of_birth']='30/06/1990'
   with request() as response:result=json.load(response)
   self.assertEqual(next(u for u in result['users'] if u['name']=='New Example')['date_of_birth'],'30/06/1990')
  finally:server.shutdown();server.server_close();thread.join(2)
 def test_birth_date_required_validation_and_persistence(self):
  for value in ('','1/1/2000','31/02/2000','01/01/2999',None):
   with self.assertRaises(ValueError):validate_birth_date(value)
  self.assertEqual(validate_birth_date('29/02/2000'),'29/02/2000');self.assertEqual(FamilyStore(self.config).user(self.uid)['date_of_birth'],'29/02/2000')
if __name__=='__main__':unittest.main()
