"""Archive requests can join a live creation ceremony without chain readiness."""
import copy,json
from pathlib import Path
from unittest.mock import patch
import test_build35 as fixtures
from builder_workspace import BuilderWorkspace
class ArchiveDuringSetup(fixtures.Archives):
 def test_checking_or_busy_chain_allows_verified_archive_without_changing_setup(self):
  workspace=BuilderWorkspace(self.config);w=workspace.start(self.user,'wallet');state=workspace.read();state['workspaces']['wallet']['operations']=['ceremony'];workspace.write(state)
  path=Path(self.config).parent/'data/wallet-setups.json';path.write_text(json.dumps({'version':1,'sessions':{'ceremony':{'state':'policy','message':'Awaiting verified disc'}}}));before=path.read_bytes();build=workspace.path.read_bytes()
  original=self.registry.snapshot
  for status in ('checking','synchronising','downloading','error'):
   def snapshot():
    value=original()
    for n in value['nodes']:n['blockchain']={'state':status}
    return value
   with patch.object(self.registry,'snapshot',side_effect=snapshot):
    options=self.queue.options(self.rid);self.assertEqual(len(options),2);self.queue.create(self.user,self.rid,[options[0]['id']])
   self.assertEqual(path.read_bytes(),before);self.assertEqual(workspace.path.read_bytes(),build)
  self.assertEqual(len(self.queue.read()['requests']),1,'Repeated clicks do not duplicate the same active archive')
 def test_busy_chain_does_not_bypass_watch_disc_scan_or_live_identity_checks(self):
  original=self.registry.snapshot
  def snapshot():
   value=original()
   for n in value['nodes']:n['blockchain']={'state':'checking'}
   return value
  with patch.object(self.registry,'snapshot',side_effect=snapshot):
   a=self.hub.inventories[self.a['node_id']]['wallets'][0];b=self.hub.inventories[self.b['node_id']]['wallets'][0];a['private_keys_enabled']=False;b['signing_only']=True;self.assertEqual(self.queue.options(self.rid),[])
   a['private_keys_enabled']=True;a['scanning']=True;self.assertEqual(self.queue.options(self.rid),[]);a['scanning']=False;option=self.queue.options(self.rid)[0]
   with patch.object(self.hub,'rpc',return_value={'private_keys_enabled':False,'walletname':option['wallet']}):
    with self.assertRaisesRegex(ValueError,'changed'):self.queue.create(self.user,self.rid,[option['id']])
   self.assertFalse(self.queue.read()['requests'])
for name in fixtures.Archives.__dict__:
 if name.startswith('test_') and name not in ArchiveDuringSetup.__dict__:setattr(ArchiveDuringSetup,name,None)
