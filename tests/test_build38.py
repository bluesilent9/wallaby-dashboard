"""Readable labels are local metadata; only Core filename edits create Agent work."""
import copy
from unittest.mock import patch
import test_build8 as fixtures
from wallet_renames import RenameQueue
from archive_queue import ArchiveQueue
from wallet_names import dashboard_name,display_summary
from balance_store import BalanceStore
from report_store import signature
from descriptor_store import DescriptorStore
from preferences import FamilyStore
class SeparateNames(fixtures.Setup):
 def setUp(self):super().setUp();self.q=RenameQueue(self.hub)
 def key(self):return signature(self.family.snapshot(),DescriptorStore(self.config).summary()['fingerprint'])
 def test_local_label_only_no_queue_rpc_or_balance_invalidation(self):
  key=self.key();BalanceStore(self.config).save(key,{'balances':{'wallets':[{'name':'Example Wallet','descriptor_id':self.wallet['id'],'kind':'descriptor','owner_name':'Example','smsf':False,'btc':'0.001','value_aud':'10'}]}})
  with patch.object(self.hub,'rpc',side_effect=AssertionError('Local label must not call Core')):r=self.q.rename(self.user,self.rid,None,'Readable Treasury Name (2018 - 2026)')
  self.assertFalse(r['filename_queued']);self.assertFalse(self.q.path.exists());self.assertFalse(ArchiveQueue(self.hub).read()['requests']);self.assertEqual(self.key(),key)
  family=FamilyStore(self.config).snapshot();self.assertEqual(family['ownership'][0]['name'],'Example Wallet');self.assertEqual(dashboard_name(family['ownership'][0]),'Readable Treasury Name (2018 - 2026)')
  result=BalanceStore(self.config).personal(key,family['users'][0]);self.assertEqual(result['balances']['wallets'][0]['name'],'Readable Treasury Name (2018 - 2026)');self.assertEqual(result['balances']['wallets'][0]['btc'],'0.001')
  self.assertEqual(display_summary(DescriptorStore(self.config).summary(),family)['wallets'][0]['name'],'Readable Treasury Name (2018 - 2026)')
 def test_filename_only_preserves_readable_label_and_watch_suffix(self):
  r=self.q.rename(self.user,self.rid,'old_treasury_wallet_2018_2026','Example Wallet');self.assertTrue(r['filename_queued']);self.assertEqual(dashboard_name(self.family.snapshot()['ownership'][0]),'Example Wallet')
  self.assertEqual(self.q.proposal(self.b['node_id'])['requests'][0]['new_name'],'old_treasury_wallet_2018_2026_watch');self.assertEqual(self.q.proposal(self.a['node_id'])['requests'][0]['new_name'],'old_treasury_wallet_2018_2026')
 def test_both_fields_and_agent_receipt_does_not_replace_label(self):
  r=self.q.rename(self.user,self.rid,'treasury_old','Old Treasury Wallet (2018 - 2026)');o=self.q.read()['operations'][r['operations'][0]['id']];queue=self.q.proposal(self.a['node_id']);t=queue['requests'][0]
  p=dict(version=1,queue_id=queue['queue_id'],rename_id=o['id'],request_id=t['request_id'],wallet=t['wallet'],new_wallet=t['new_name'],storage='node',status='complete',identity_verified=True)
  with patch('wallet_policy_updates.verified',return_value=p):self.q.accept(self.a['node_id'],{})
  self.assertEqual(dashboard_name(self.family.snapshot()['ownership'][0]),'Old Treasury Wallet (2018 - 2026)')
 def test_invalid_label_never_queues_filename(self):
  for label in ('','bad\nname','Bitaroo'):
   with self.assertRaises(ValueError):self.q.rename(self.user,self.rid,'new_filename',label)
  self.assertFalse(self.q.path.exists())
 def test_wrong_owner_cannot_read_or_change_names(self):
  for call in (lambda:self.q.names('someone-else',self.rid),lambda:self.q.rename('someone-else',self.rid,None,'No')):
   with self.assertRaisesRegex(ValueError,'owner'):call()
 def test_readable_label_can_change_during_filename_rename(self):
  self.q.rename(self.user,self.rid,'new_filename','First Label');count=len(self.q.read()['operations']);self.q.rename(self.user,self.rid,None,'Second Label');self.assertEqual(len(self.q.read()['operations']),count);self.assertEqual(dashboard_name(self.family.snapshot()['ownership'][0]),'Second Label')
 def test_watch_only_default_does_not_offer_watch_suffix_as_base(self):
  self.hub.inventories[self.a['node_id']]['wallets']=[];w=copy.deepcopy(self.wallet);w['raw']['wallet_name']=w['name']='treasury_watch';DescriptorStore(self.config).replace_node_wallets([w]);self.assertEqual(self.q.names(self.user,self.rid)['filename'],'treasury')
 def test_saved_ownership_retains_local_label_without_rekeying_trace(self):
  self.q.rename(self.user,self.rid,None,'My Treasury');key=self.key();self.family.save_ownership([dict(id=self.rid,name='My Treasury',owner_id=self.user,external=False,smsf=False)],self.family.snapshot()['revision'],[dict(id=self.rid,kind='descriptor',source=self.wallet['id'],name='Example Wallet')]);self.assertEqual(self.key(),key);self.assertEqual(dashboard_name(self.family.snapshot()['ownership'][0]),'My Treasury')
 def test_local_names_http_endpoints(self):
  import http.client,json,threading
  from pathlib import Path
  from launch_wallaby_bitcoin_dashboard import create_server
  server,sessions=create_server(Path(self.tmp.name)/'cache',0,self.config);sessions.nodes.health.closed=True;session=sessions.create();session.user_id=self.user;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  def request(method,url,body=None):
   c=http.client.HTTPConnection('::1',server.server_port,timeout=5);c.request(method,url,body=json.dumps(body) if body else None,headers={'X-Session-Token':session.token,'Content-Type':'application/json'});r=c.getresponse();payload=json.loads(r.read());c.close();return r.status,payload
  try:
   code,p=request('GET','/api/wallets/rename-names?wallet_id='+self.rid);self.assertEqual(code,200);self.assertEqual(p['dashboard_name'],'Example Wallet')
   code,p=request('POST','/api/wallets/rename',dict(wallet_id=self.rid,filename=None,dashboard_name='Readable Local Name'));self.assertEqual(code,200,p);self.assertFalse(p['filename_queued']);self.assertFalse(self.q.path.exists())
   self.assertEqual(request('GET','/api/wallets/rename-names?wallet_id='+self.rid)[1]['dashboard_name'],'Readable Local Name')
  finally:server.shutdown();server.server_close();t.join(3)
