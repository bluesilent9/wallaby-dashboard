"""Review-only Vault page status matrix; production functions, invented public records."""
import copy,sys,unittest
from pathlib import Path
from unittest.mock import patch
D=Path(__file__).resolve().parents[1];sys.path[:0]=[str(D),str(D/'tests')]
from test_build8 import Setup
from test_build11 import multisig_fixture
from agent_hub import digest
from descriptor_store import DescriptorStore,clean_wallet
from preferences import resource_id
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from multisig_psbt import witness_policy
from vault_monitor import countdown

class Display(Setup):
 def setUp(self):
  super().setUp();self.coin={'txid':'c'*64,'vout':0,'address':self.vault['address'],'amount':'0.01'};self.coins=[self.coin];self.rpc_error=None
  self.exchange(self.a,[self.item('vault_public',self.vault)]);self.exchange(self.b,known=[digest(self.vault)])
  monitor={'name':'monitor','id':'monitor','raw':{'descriptors':[{'desc':self.vault['descriptor']}]},'private_keys_enabled':False,'scanning':False}
  for n in (self.a,self.b):self.hub.inventories[n['node_id']]['wallets'].append(copy.deepcopy(monitor))
  self.fake=patch.object(self.hub,'rpc',side_effect=self.rpc);self.fake.start();self.addCleanup(self.fake.stop)
 def rpc(self,node,method,params=None,wallet=None):
  if self.rpc_error:raise ValueError(self.rpc_error)
  if method=='listunspent':return copy.deepcopy(self.coins) if wallet=='monitor' else []
  if method=='getaddressinfo':return {'labels':[]}
  raise AssertionError(method)
 def card(self):return self.registry.vaults.compute()['wallets'][0]
 def test_single_vault_locked_green_name_amount_and_countdown(self):
  c=self.card();self.assertEqual(c['state'],'green');self.assertEqual(c['name'],'Example Wallet');self.assertEqual(c['vault_sats'],1000000);self.assertEqual(c['height'],1000000);self.assertEqual(c['countdown'],countdown((1000000-999000)*600))
 def test_replicas_do_not_double_count_bitcoin(self):self.assertEqual(self.card()['vault_sats'],1000000)
 def test_exact_unlock_height_and_later_are_red_unlocked(self):
  for h in (1000000,1000010):
   for c in self.registry.connections.values():self.registry.health.values[c['token']]['blocks']=h
   card=self.card();self.assertEqual(card['state'],'red');self.assertEqual(card['countdown'],'Unlocked')
 def test_empty_ordinary_wallets_are_not_vault_cards(self):
  with self.hub.edit() as s:s['vaults']={}
  self.assertEqual(self.registry.vaults.compute()['wallets'],[])
 def test_pending_authorisation_is_amber(self):
  self.hub.inventories[self.b['node_id']]['wallets']=[self.hub.inventories[self.b['node_id']]['wallets'][0]]
  with self.hub.edit() as s:s['nodes'][self.b['node_id']]['known_vaults']=[]
  c=self.card();self.assertEqual(c['state'],'amber');self.assertTrue(any('authorisation' in x for x in c['issues']))
 def test_no_live_node_is_amber_without_claiming_current_height(self):
  self.registry.connections.clear();c=self.card();self.assertEqual(c['state'],'amber');self.assertIsNone(c['current_height']);self.assertEqual(c['countdown'],'Verification needed')
 def test_all_nodes_downloading_are_amber(self):
  for c in self.registry.connections.values():self.registry.health.values[c['token']]['state']='downloading'
  c=self.card();self.assertEqual(c['state'],'amber');self.assertTrue(any('downloading' in x for x in c['issues']))
 def test_rpc_failure_is_amber_with_an_explanation(self):
  self.rpc_error='Synthetic node unavailable';c=self.card();self.assertEqual(c['state'],'amber');self.assertTrue(any(self.rpc_error in x for x in c['issues']))
 def test_inventory_not_ready_is_amber(self):
  for inv in self.hub.inventories.values():inv['complete']=False
  self.assertEqual(self.card()['state'],'amber')
 def test_zero_vault_balance_is_amber(self):
  self.coins=[];c=self.card();self.assertEqual(c['state'],'amber');self.assertTrue(any('No confirmed Bitcoin' in x for x in c['issues']))
 def test_funds_outside_the_vault_are_amber(self):
  old=self.hub.rpc.side_effect
  def outside(node,method,params=None,wallet=None):
   return [{'txid':'d'*64,'vout':0,'amount':'0.02','address':'ordinary-source-output'}] if wallet==self.wallet['name'] else old(node,method,params,wallet)
  self.hub.rpc.side_effect=outside;c=self.card();self.assertEqual(c['state'],'amber');self.assertEqual(c['outside_sats'],2000000)
 def test_corrupted_coin_refresh_must_not_keep_a_green_card(self):
  m=self.registry.vaults;m.refresh();self.assertEqual(m.value['wallets'][0]['state'],'green');self.coins=[self.coin|{'amount':'NaN'}];m.refresh()
  self.assertIn('error',m.value)
  self.assertEqual(m.value['wallets'][0]['state'],'amber','Stale green remains after current balance verification failed')

class NewPolicyDisplay(Setup):
 def test_wallet_builder_single_vault_and_multisig_vault_render_from_descriptors(self):
  for multi in (False,True):
   original,doc,_=multisig_fixture(1000000);keys=[k.hex() for k in witness_policy(doc['inputs'][0][b'\x05'])['keys']]
   body='wsh(and_v(v:after(1000000),'+('multi(2,'+','.join(keys)+')' if multi else 'pk('+keys[0]+')')+'))';desc=body+'#'+checksum(body);parsed=Descriptor.from_string(desc)
   raw={'wallet_name':'Multisig Vault' if multi else 'Single Vault','descriptors':[{'desc':desc,'timestamp':1,'active':False,'internal':False}]};w=clean_wallet(raw);DescriptorStore(self.config).replace_node_wallets([w])
   for n in (self.a,self.b):self.hub.inventories[n['node_id']]={'complete':True,'wallets':[{'name':raw['wallet_name'],'raw':raw,'id':w['id'],'private_keys_enabled':False}]}
   coin={'txid':'e'*64,'vout':0,'amount':'0.005','desc':desc,'scriptPubKey':parsed.script_pubkey().data.hex(),'address':parsed.address()}
   with patch.object(self.hub,'rpc',return_value=[coin]):
    c=self.registry.vaults.compute()['wallets'][0];self.assertEqual(c['name'],raw['wallet_name']);self.assertEqual(c['state'],'green');self.assertEqual(c['vault_sats'],500000);self.assertEqual(c['height'],1000000);self.assertEqual(c['signatures'],[{'required':2 if multi else 1,'total':3 if multi else 1}])
    for con in self.registry.connections.values():self.registry.health.values[con['token']]['blocks']=1000000
    c=self.registry.vaults.compute()['wallets'][0];self.assertEqual(c['state'],'red');self.assertEqual(c['countdown'],'Unlocked')
   for con in self.registry.connections.values():self.registry.health.values[con['token']]['blocks']=999000
 def test_countdown_always_two_nonzero_units(self):
  for seconds,expected in [(7*365*86400+2*30*86400+4*86400+36*60,'7 Years, 2 Months'),(252,'4 Minutes, 12 Seconds'),(60,'1 Minute'),(1,'1 Second'),(3601,'1 Hour, 1 Second')]:self.assertEqual(countdown(seconds),expected)

class Freshness(Display):
 # Reuse the fixture, not its inherited tests a second time.
 pass
for _name in tuple(vars(Display)):
 if _name.startswith('test_'):setattr(Freshness,_name,None)
def ready_empty(self):
 self.coins=[];c=self.card();self.assertEqual(c['funding_status'],'ready_to_fund');self.assertEqual(c['countdown'],'Not funded');self.assertTrue(c['last_verified_utc']);self.assertTrue(c['balance_verified']);self.assertEqual(c['state'],'amber')
def recover(self):
 m=self.registry.vaults;m.refresh();old=m.value['wallets'][0]['last_verified_utc'];self.coins=[self.coin|{'amount':'NaN'}];m.refresh();c=m.value['wallets'][0];self.assertTrue(c['stale']);self.assertEqual(c['last_verified_utc'],old);self.assertIsNone(c['current_height']);self.assertEqual(c['countdown'],'Verification unavailable');self.coins=[self.coin];m.refresh();self.assertFalse(m.value['wallets'][0]['stale']);self.assertEqual(m.value['wallets'][0]['state'],'green')
def unexpected(self):
 m=self.registry.vaults;m.refresh();m.pending=True
 with patch.object(m,'compute',side_effect=RuntimeError('fixture')):m.refresh()
 self.assertFalse(m.pending);self.assertEqual(m.value['wallets'][0]['state'],'amber')
def overdue(self):
 import time
 m=self.registry.vaults;m.refresh();m.last=time.monotonic()-46;m.pending=True;c=m.snapshot();self.assertTrue(c['stale']);self.assertEqual(c['wallets'][0]['state'],'amber');self.assertEqual(m.value['wallets'][0]['state'],'green')
Freshness.test_empty_verified_vault_is_ready_to_fund=ready_empty
Freshness.test_failed_refresh_preserves_timestamp_and_later_recovers=recover
Freshness.test_unexpected_failure_releases_refresh_and_marks_stale=unexpected
Freshness.test_cached_status_is_a_copy_and_expires_after_45_seconds=overdue

class SetupStatus(unittest.TestCase):
 def test_readiness_waits_for_core_and_reports_fresh_funding_state(self):
  import hashlib
  from test_build11 import Ceremony
  from vault_monitor import utc_now
  c=Ceremony();c.setUp();self.addCleanup(c.doCleanups);h,row=c.start(True,1000000);self.assertEqual(c.setup.snapshot()['sessions'][0]['setup_status'],'awaiting_approvals');row=c.contribute(h,row)
  for n in (c.a,c.b):c.setup.accept(n['node_id'],'wallet_setup_ack',c.ack(h,row,n,'policy_approved',backup='pending' if n==c.b else 'not_required'))
  proof=c.ack(h,row,c.b,'backup_verified',cosigner_file=hashlib.sha256(b'signer-2').hexdigest(),image_sha256='a'*64);c.setup.accept(c.b['node_id'],'wallet_setup_ack',proof);self.assertEqual(c.setup.snapshot()['sessions'][0]['setup_status'],'awaiting_confirmation')
  with c.setup.edit() as state:state['sessions'][h]['completion_confirmed']={c.a['node_id']:'fixture'}
  self.assertEqual(c.setup.snapshot()['sessions'][0]['setup_status'],'awaiting_confirmation')
  with c.setup.edit() as state:state['sessions'][h]['completion_confirmed'][c.b['node_id']]='fixture'
  self.assertEqual(c.setup.snapshot()['sessions'][0]['setup_status'],'ready_to_fund')
  c.registry.vaults.value={'wallets':[{'policies':[{'public':{'descriptor':d}} for d in c.setup.read()['sessions'][h]['descriptors']],'checked_utc':utc_now(),'stale':False,'funding_status':'locked'}]}
  self.assertEqual(c.setup.snapshot()['sessions'][0]['setup_status'],'locked');c.registry.vaults.value['wallets'][0]['funding_status']='unlocked';self.assertEqual(c.setup.snapshot()['sessions'][0]['setup_status'],'unlocked');c.registry.vaults.value['wallets'][0]['stale']=True;self.assertEqual(c.setup.snapshot()['sessions'][0]['setup_status'],'stale')

if __name__=='__main__':unittest.main(verbosity=2)
