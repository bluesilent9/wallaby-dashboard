"""Renames refresh routes without revoking verified ownership or changing tasks."""
import copy
from types import SimpleNamespace
from unittest.mock import patch
import test_build21_revision as discovery_fixtures
import test_build8 as fixtures
from background_discovery import BackgroundDiscovery
from descriptor_store import DescriptorStore
from descriptor_snapshot import history_ready,saved_history,publish_history
from report_store import signature
from balance_store import BalanceStore
from balance_store import BALANCE_ALGORITHM
from preferences import FamilyStore
from profile_archive import ProfileGate
from wallet_renames import RenameQueue

class Discovery(discovery_fixtures.Discovery):
 def test_rename_watch_role_and_host_change_reuses_exact_verified_ledger(self):
  self.cycle();store=DescriptorStore(self.config);old=store.read();key=signature(FamilyStore(self.config).snapshot(),store.fingerprint(old));balance=BalanceStore(self.config);balance.save(key,{'basis_algorithm':BALANCE_ALGORITHM,'balances':{'wallets':[]}})
  fresh=copy.deepcopy(old['wallets'])
  for w in fresh:
   w['name']='Renamed '+w['name'];w['raw']['wallet_name']=w['name'];w['node_sources']=[{'node_id':'same-node','hostname':'changed-host','wallet_name':w['name']}];w['watch_only']=not w.get('watch_only',False);w['public_history'].update(exported_utc='2026-11-03T00:00:00Z',lastblock='f'*64)
   for tx in w['node_history']:tx['confirmations']+=10
  with patch('background_discovery.scan',side_effect=AssertionError('A rename must not rescan the ledger')):self.cycle(fresh)
  new=store.read();self.assertEqual(store.fingerprint(old),store.fingerprint(new));self.assertNotEqual(store.revision(old),store.revision(new));self.assertTrue(history_ready(self.config,new));self.assertEqual(saved_history(self.config,old),saved_history(self.config,new));self.assertTrue(balance.ready(signature(FamilyStore(self.config).snapshot(),store.fingerprint(new))))
  for w in new['wallets']:
   prior=next(r for r in old['wallets'] if r['id']==w['id']);self.assertEqual(w['name'],prior['name']);self.assertEqual(w['node_history'],prior['node_history']);self.assertEqual(w['node_sources'][0]['hostname'],'changed-host')
 def test_new_transaction_requires_history_check_and_keeps_failed_scan_unverified(self):
  self.cycle();fresh=DescriptorStore(self.config).read()['wallets'];fresh[0]['node_history'].append({'txid':'f'*64,'category':'send','vout':0,'confirmations':0})
  with patch('background_discovery.scan',side_effect=ValueError('new transaction requires verification')):
   with self.assertRaisesRegex(ValueError,'requires verification'):self.cycle(fresh)
  self.assertFalse(history_ready(self.config,DescriptorStore(self.config).read()))
for name in discovery_fixtures.Discovery.__dict__:
 if name.startswith('test_') and name not in Discovery.__dict__:setattr(Discovery,name,None)

class RenameIsolation(fixtures.Setup):
 def test_partial_rename_queue_is_unchanged_by_background_route_discovery(self):
  store=DescriptorStore(self.config);wallet=copy.deepcopy(self.wallet);wallet.update(node_history=[],public_history={'outputs':[],'lastblock':'a'*64,'exported_utc':'2026-01-01T00:00:00Z'},node_sources=[{'node_id':self.a['node_id'],'hostname':'first','wallet_name':wallet['name']}]);store.replace_node_wallets([wallet]);catalog=store.read()
  from datetime import datetime,timezone
  publish_history(self.config,catalog,dict(catalog_fingerprint=store.fingerprint(catalog),scanned_utc=datetime.now(timezone.utc).isoformat(),wallets=[],transactions={},coverage=[],timestamp_basis='UTC',source='fixture'))
  q=RenameQueue(self.hub);q.create(self.user,self.rid,'New name (2022 - 2026)');task=q.proposal(self.a['node_id'])['requests'][0]
  with patch('wallet_policy_updates.verified',side_effect=lambda hub,node,e:e['message']):q.accept(self.a['node_id'],{'message':dict(version=1,queue_id=q.read()['queue_id'],rename_id=task['rename_id'],request_id=task['request_id'],status='complete',wallet=task['wallet'],storage='node',identity_verified=True,new_wallet=task['new_name'])})
  frozen=copy.deepcopy(q.read());family=FamilyStore(self.config).snapshot();fresh=copy.deepcopy(wallet);fresh['name']=task['new_name'];fresh['raw']['wallet_name']=task['new_name'];fresh['node_sources'][0]['wallet_name']=task['new_name']
  sessions=SimpleNamespace(nodes=self.registry,preferences_path=self.config,sessions={},profile_gate=ProfileGate());service=BackgroundDiscovery(sessions,start=False);self.addCleanup(service.close)
  with patch('background_discovery.NodeWalletLoader.collect',return_value=[fresh]),patch('background_discovery.scan',side_effect=AssertionError('No ledger rebuild for a rename')):service.cycle()
  self.assertEqual(q.read(),frozen);self.assertEqual(FamilyStore(self.config).snapshot(),family);self.assertEqual(q.proposal(self.b['node_id'])['requests'][0]['wallet'],'different_watch_name');self.assertTrue(history_ready(self.config,store.read()))
