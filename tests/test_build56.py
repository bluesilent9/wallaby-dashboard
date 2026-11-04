import copy,hashlib,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wallet_builder import setup_card_progress

class CardProgressTests(unittest.TestCase):
 def setUp(self):
  self.row={'state':'policy','proposal':{'cosigners':[{'id':'disc1','agent_key':'a','storage':'disc'},{'id':'disc2','agent_key':'a','storage':'disc'},{'id':'node','agent_key':'b','storage':'node'}]},'routes':{'disc1':'A','disc2':'A','node':'B'},'contributions':{},'acks':{},'inventories':{},'completion_confirmed':{}}
 def ack(self,key,status,role=None):
  m={'status':status}
  if role:m['cosigner_file']=hashlib.sha256(role.encode()).hexdigest()
  self.row['acks'][str(len(self.row['acks']))]={'public_key':key,'message':m}
 def colours(self):return [p['colour'] for p in setup_card_progress(self.row)['signers'].values()]
 def test_waiting_and_participation_are_not_complete(self):
  self.assertEqual(self.colours(),['red']*3)
  self.ack('a','participating');self.assertEqual(self.colours(),['amber','amber','red'])
  self.row['contributions']['node']={};self.assertEqual(self.colours(),['amber']*3)
  self.assertEqual(setup_card_progress(self.row)['wallet']['colour'],'red')
 def test_each_disc_requires_its_own_verified_proof(self):
  self.ack('a','policy_approved');self.ack('a','backup_verified','disc1')
  self.assertEqual(self.colours(),['green','amber','red'])
  self.assertEqual(setup_card_progress(self.row)['wallet']['colour'],'amber')
  self.ack('b','backup_verified','disc2');self.assertEqual(self.colours(),['green','amber','red'])
 def test_node_requires_installation_and_approval(self):
  self.row['inventories']['B']={'message':{'signing_wallets':{'node':'Example - Signer'}}}
  self.assertEqual(self.colours()[-1],'red')
  self.ack('b','policy_approved');self.assertEqual(self.colours()[-1],'green')
 def test_ready_alone_is_not_final_green(self):
  self.row['state']='ready';self.assertEqual(setup_card_progress(self.row)['wallet']['colour'],'red')
  self.ack('a','policy_approved');self.ack('b','policy_approved')
  for role in ('disc1','disc2'):self.ack('a','backup_verified',role)
  self.row['inventories']['B']={'message':{'signing_wallets':{'node':'Example - Signer'}}}
  self.assertEqual(self.colours(),['green']*3)
  self.assertEqual(setup_card_progress(self.row)['wallet']['colour'],'amber')
  self.row['completion_confirmed']={'A':'now','B':'now'}
  self.assertEqual(setup_card_progress(self.row)['wallet']['colour'],'green')
 def test_stopped_ceremony_cannot_appear_complete_and_read_is_pure(self):
  self.ack('a','policy_approved');self.ack('a','backup_verified','disc1');self.row['state']='interrupted'
  before=copy.deepcopy(self.row);p=setup_card_progress(self.row)
  self.assertEqual(p['signers']['disc1']['colour'],'amber');self.assertNotEqual(p['wallet']['colour'],'green');self.assertEqual(before,self.row)
 def test_single_sig_and_single_vault_node_finish(self):
  self.row['proposal']['cosigners']=self.row['proposal']['cosigners'][2:];self.row['routes']={'node':'B'}
  self.ack('b','policy_approved');self.row['inventories']['B']={'message':{'signing_wallets':{'node':'Example'}}};self.row['state']='ready';self.row['completion_confirmed']={'B':'now'}
  for height in (None,1000000):
   self.row['proposal']['height']=height;self.assertEqual(setup_card_progress(self.row)['wallet']['colour'],'green')
if __name__=='__main__':unittest.main()
