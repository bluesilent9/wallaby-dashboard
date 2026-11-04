"""Saved browsing survives fresh sessions, missing nodes and discovery refreshes."""
import copy,json
import test_build8 as fixtures
from balance_store import BalanceStore
from report_store import ReportStore
from launch_wallaby_bitcoin_dashboard import Session
from wallet_valuation import revalue

class SavedViews(fixtures.Setup):
 def session(self):
  s=Session(self.tmp.name+'/browser',self.config);s.node_registry=self.registry;self.addCleanup(s.close);return s
 def save(self):BalanceStore(self.config).save('previous-verified-context',{'balances':{'wallets':[],'price_aud_per_btc':'100000','valuation_utc':'2026-01-01T00:00:00Z'}})
 def test_returning_profile_can_browse_saved_balances_without_any_node(self):
  self.save()
  for node in (self.a,self.b):self.registry.heartbeat(node['connection_token'],disconnect=True)
  s=self.session();family=s.select_user(self.user);self.assertTrue(family['ownership_available']);self.assertTrue(family['balances_available']);self.assertFalse(family['ownership_ready']);self.assertFalse(family['node_ready']);self.assertTrue(family['saved_data']);self.assertEqual(family['balance_report']['balances']['wallets'],[]);self.assertEqual(s.status,'idle')
  with self.assertRaises(ValueError):s.start({},'cgt')
 def test_incomplete_new_trace_retains_previous_verified_display(self):
  self.save();balance=BalanceStore(self.config);balance.save_evidence('new-context',None,[]);self.assertFalse(balance.ready('new-context'));self.assertEqual(balance.latest()['signature'],'previous-verified-context');self.assertTrue(self.session().select_user(self.user)['balances_available'])
 def test_new_profile_without_verified_data_is_not_unlocked(self):
  s=self.session();family=s.select_user(self.user);self.assertFalse(family['ownership_available']);self.assertFalse(family['balances_available']);self.assertIsNone(family['balance_report'])
 def test_saved_report_is_viewable_after_signature_change_but_begin_discards_it(self):
  self.save();store=ReportStore(self.config);store._write(store.index,{'state':'ready','signature':'old','generation':'saved','users':[self.user]});store._write(store.root/(self.user+'.json'),{'owner_id':self.user,'financial_year':'2025-2026'})
  s=self.session();family=s.select_user(self.user);self.assertTrue(family['report_ready']);self.assertEqual(s.report['financial_year'],'2025-2026');self.assertIsNone(store.load(self.user,'new')[1]);self.assertTrue(store.load(self.user,'new',allow_saved=True)[1]);store.begin('replacement');self.assertIsNone(store.load(self.user,'new',allow_saved=True)[1])
 def test_saved_profile_roundtrip_returns_to_wallets_and_retains_verified_fallback(self):
  from pathlib import Path
  from ownership_workspace import save_workspace
  from profile_archive import ProfileArchive
  self.save();BalanceStore(self.config).save_evidence('incomplete',None,[])
  inputs=Path(self.tmp.name)/'inputs';inputs.mkdir();(inputs/'exchanges.ods').write_bytes(b'fixture');(inputs/'chain.json').write_text('{}')
  save_workspace(self.config,self.user,{'exchanges':inputs/'exchanges.ods','chain':inputs/'chain.json'},[],{'entries':[]},{},metadata={'descriptor_fingerprint':'old','family_accounts':{self.user:False}})
  browser=self.session();self.assertEqual(browser.select_user(self.user)['login_page'],'balances');browser.workspace_dirty=True;browser.address_book=None;self.assertIsNotNone(browser.family()['address_book'])
  package=ProfileArchive(self.config).save();target=Path(self.tmp.name)/'restored/config.cfg';fixtures.FamilyStore(target);ProfileArchive(target).load('profile.7z',package)
  self.assertEqual(BalanceStore(target).latest()['signature'],'previous-verified-context')
  restored=Session(Path(self.tmp.name)/'restored-browser',target);self.addCleanup(restored.close);family=restored.select_user(self.user);self.assertEqual(family['login_page'],'balances');self.assertTrue(family['balances_available']);self.assertFalse(family['node_ready'])
 def test_quote_refresh_changes_only_display_copy_and_preserves_unknown_basis(self):
  original={'balances':{'wallets':[{'btc':'0.5','cash_aud':'10','cost_basis_aud':'200','last_update_utc':'old','state':'loss'},{'btc':'1','cost_basis_aud':None,'state':'unavailable'}]}};before=copy.deepcopy(original);result=revalue(original,{'aud_per_btc':'1000','observed_utc':'now','source':'fixture'});self.assertEqual(original,before);self.assertEqual(result['balances']['wallets'][0]['value_aud'],'510.0');self.assertEqual(result['balances']['wallets'][0]['unrealised_gain_aud'],'300.0');self.assertEqual(result['balances']['wallets'][0]['last_update_utc'],'old');self.assertEqual(result['balances']['wallets'][1]['state'],'unavailable')
