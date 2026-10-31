"""Current record inputs and retry-safe authenticated Agent discovery."""
import copy,re,subprocess,sys
from pathlib import Path
from unittest.mock import patch
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from family_inputs import declarations,account_slot
from test_build8 import Setup
import test_build11 as signed
from setup_signatures import fingerprint

class Records(unittest.TestCase):
 def test_current_family_records_do_not_require_retired_wallet_ods(self):
  users=[{'id':'a'*32,'name':'One'},{'id':'b'*32,'name':'Two'}];paths={'exchanges':['exchanges.ods'],**{account_slot(u['id']):['statement.pdf'] for u in users}}
  self.assertEqual(declarations(users,{},paths),{'a'*32:True,'b'*32:True});self.assertNotIn('historical',paths)
 def test_non_bitaroo_declaration_still_works_without_legacy_ods(self):
  users=[{'id':'a'*32,'name':'One'}];self.assertEqual(declarations(users,{'non_bitaroo':['a'*32]},{'exchanges':['exchanges.ods']}),{'a'*32:False})

class Discovery(Setup):
 def setUp(self):
  super().setUp();self.setup=self.hub.wallet_builder;self.folder=Path(self.tmp.name);self.keys={}
  for n in (self.a,self.b):
   self.registry.connections[n['connection_token']]['agent_version']='2.0.0~dev9';key=self.folder/n['node_id'];subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','','-f',str(key)],check=True);self.keys[n['node_id']]=key
 def pub(self,n):return signed.Ceremony.pub(self,n)
 def sign(self,n,m):return signed.Ceremony.sign(self,n,m)
 def identity(self,n,challenge):return self.sign(n,{'version':1,'challenge':challenge,'public_key':self.pub(n),'fingerprint':fingerprint(self.pub(n)),'hostname':'Example Agent'})
 def test_repeated_clicks_keep_pending_challenge_and_delayed_reply_still_works(self):
  first=self.setup.discover();challenge=self.setup.read()['challenges'][self.a['node_id']];self.assertTrue(all(n['requested'] and not n['fresh'] for n in first['nodes']))
  for _ in range(4):self.setup.discover()
  self.assertEqual(self.setup.read()['challenges'][self.a['node_id']],challenge);self.setup.accept(self.a['node_id'],'wallet_setup_identity',self.identity(self.a,challenge));self.assertTrue(next(n for n in self.setup.snapshot()['nodes'] if n['id']==self.a['node_id'])['fresh'])
 def test_old_signed_reply_is_acknowledged_without_satisfying_new_discovery(self):
  self.setup.discover();old=self.setup.read()['challenges'][self.a['node_id']];envelope=self.identity(self.a,old);self.setup.accept(self.a['node_id'],'wallet_setup_identity',envelope);self.setup.discover();new=self.setup.read()['challenges'][self.a['node_id']];self.assertNotEqual(new,old)
  item=self.item('wallet_setup_identity',envelope);reply=self.exchange(self.a,[item]);self.assertIn(item['id'],reply['accepted']);self.assertTrue(any(p['kind']=='wallet_setup_identity' and p['challenge']==new for p in reply['proposals']));self.assertFalse(next(n for n in self.setup.snapshot()['nodes'] if n['id']==self.a['node_id'])['fresh'])
  self.exchange(self.a,[self.item('wallet_setup_identity',self.identity(self.a,new))]);self.assertTrue(next(n for n in self.setup.snapshot()['nodes'] if n['id']==self.a['node_id'])['fresh'])
 def test_invalid_signature_is_never_acknowledged_as_a_stale_reply(self):
  self.setup.discover();challenge=self.setup.read()['challenges'][self.a['node_id']];bad=self.identity(self.a,challenge);bad['message']['challenge']='f'*64
  with self.assertRaises(ValueError):self.exchange(self.a,[self.item('wallet_setup_identity',bad)])
  self.assertFalse(self.setup.read()['identities'])
 def test_foreign_identity_does_not_replace_a_known_node_via_stale_reply(self):
  self.setup.discover();challenge=self.setup.read()['challenges'][self.a['node_id']];self.setup.accept(self.a['node_id'],'wallet_setup_identity',self.identity(self.a,challenge));foreign=self.identity(self.b,self.setup.read()['challenges'][self.b['node_id']])
  with self.assertRaisesRegex(ValueError,'different Agent identity'):self.setup.accept(self.a['node_id'],'wallet_setup_identity',foreign)
  self.assertEqual(self.setup.read()['identities'][self.a['node_id']]['public_key'],self.pub(self.a))
 def test_no_eligible_agents_explains_why_instead_of_silent_empty_result(self):
  with patch.object(self.setup,'available',return_value=[]):
   with self.assertRaisesRegex(ValueError,'No eligible synchronised Agents'):self.setup.discover()
 def test_pending_discovery_survives_reload_without_new_challenges(self):
  from wallet_builder import WalletBuilder
  self.setup.discover();before=self.setup.read()['challenges'];other=WalletBuilder(self.hub);other.discover();self.assertEqual(before,other.read()['challenges'])

if __name__=='__main__':unittest.main()
