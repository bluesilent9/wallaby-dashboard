"""Signed creation identities stay separate from logical financial wallets."""
import copy
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch
import test_build11 as ceremonies
import test_build46 as core_accounts
from node_wallets import NodeWalletLoader
from descriptor_store import DescriptorStore,clean_wallet
from setup_wallet_views import SetupWalletViews
from background_discovery import BackgroundDiscovery
from preferences import FamilyStore,resource_id
from profile_archive import ProfileGate
from wallet_names import display_inventory,display_summary

class LogicalWallets(ceremonies.Ceremony):
    account=core_accounts.SignerSync.account
    def start_policy(self):
        h,row=self.start();row=self.contribute(h,row);return h,row
    def monitor(self,row):
        return {'wallet_name':'Technical policy name','descriptors':[{'desc':d,'range':[0,99],'next_index':0,'timestamp':1,'active':True,'internal':bool(i)} for i,d in enumerate(row['descriptors'])]}
    def collect(self,extra=(),funded=False):
        nodes=self.registry.snapshot()['nodes'];loader=NodeWalletLoader(self.registry,lambda _:None,nodes=nodes)
        wallets={n['id']:[self.account(i)['raw'],*extra] for i,n in enumerate(nodes)}
        def rpc(node,method,params=None,wallet=None):
            rows=wallets[node['id']]
            if method=='listwallets':return [r['wallet_name'] for r in rows]
            if method=='listwalletdir':return {'wallets':[{'name':r['wallet_name']} for r in rows]}
            raw=next(r for r in rows if r['wallet_name']==wallet)
            if method=='getwalletinfo':return {'walletname':wallet,'private_keys_enabled':raw['descriptors'][0]['desc'].startswith('pkh('),'scanning':False}
            if method=='listdescriptors':return raw
            if method=='listsinceblock':return {'lastblock':'a'*64,'transactions':[{'txid':'b'*64,'vout':0,'category':'send','confirmations':1,'amount':'-0.001'}] if funded and raw['descriptors'][0]['desc'].startswith('pkh(') else []}
            raise AssertionError(method)
        with patch.object(loader,'rpc',side_effect=rpc):return loader.collect()
    def cached(self,raw):
        w=clean_wallet(raw);w.update(node_history=[],public_history={'outputs':[],'lastblock':'a'*64},node_sources=[]);return w
    def test_roles_hidden_before_monitor_and_partial_policy_while_creation_unchanged(self):
        h,row=self.start_policy();before=self.setup.path.read_bytes()
        self.assertEqual(self.collect(),[]);self.assertEqual(self.collect([self.monitor(row)]),[])
        self.assertEqual(before,self.setup.path.read_bytes())
    def test_completed_multinode_policy_has_one_proposed_name_and_real_routes(self):
        h,row=self.start_policy()
        with self.setup.edit() as s:s['sessions'][h].update(state='ready',completion_confirmed={self.a['node_id']:'now',self.b['node_id']:'now'})
        before=self.setup.path.read_bytes();wallets=self.collect([self.monitor(row)]);self.assertEqual(len(wallets),1)
        w=wallets[0];self.assertEqual(w['setup_name'],row['proposal']['name']);self.assertEqual(len(w['node_sources']),2)
        self.assertTrue(all(n['wallet_name']=='Technical policy name' for n in w['node_sources']));self.assertEqual(before,self.setup.path.read_bytes())
        DescriptorStore(self.config).replace_node_wallets(wallets);summary=DescriptorStore(self.config).summary()
        self.assertEqual(display_summary(summary,{'ownership':[]})['wallets'][0]['name'],row['proposal']['name'])
        rid=resource_id('descriptor',w['id']);inv=[{'id':rid,'kind':'descriptor','source':w['id'],'name':'old'}]
        self.assertEqual(display_inventory(inv,{'wallets':wallets})[0]['name'],row['proposal']['name'])
        record={'id':rid,'name':'User chosen name','confirmed':True}
        self.assertEqual(display_summary(summary,{'ownership':[record]})['wallets'][0]['name'],'User chosen name')
    def test_real_history_and_unknown_cache_are_never_discarded(self):
        h,row=self.start_policy();wallets=self.collect([self.monitor(row)],funded=True);self.assertEqual(len(wallets),2)
        w=self.cached(self.account(0)['raw']);views=SetupWalletViews(self.config)
        self.assertTrue(views.hide_cached(w));w['node_history']=[{'txid':'a'*64}];self.assertFalse(views.hide_cached(w))
        w['node_history']=[];w['public_history']['outputs']=[{'txid':'a'*64,'vout':0}];self.assertFalse(views.hide_cached(w))
        del w['public_history'];self.assertFalse(views.hide_cached(w))
    def test_prunes_old_empty_ownership_rows_preserving_all_ceremony_evidence(self):
        h,row=self.start_policy();key=self.cached(self.account(0)['raw']);monitor=self.cached(self.monitor(row));ordinary=self.cached(self.wallet['raw'])
        store=DescriptorStore(self.config);store.replace_node_wallets([ordinary,key,monitor]);ids={key['id'],monitor['id']}
        with self.family.transaction() as data:
            data['ownership'] += [{'id':resource_id('descriptor',w['id']),'kind':'descriptor','source':w['id'],'name':w['name']} for w in (key,monitor)]
            data['ownership_runs']={self.user:{'inventory':[{'id':resource_id('descriptor',w['id']),'kind':'descriptor','source':w['id'],'name':w['name']} for w in (ordinary,key,monitor)],'files':[{'name':'keep.ods'}]}}
        before=self.setup.path.read_bytes();hub_before=self.hub.path.read_bytes() if self.hub.path.exists() else None
        sessions=SimpleNamespace(nodes=self.registry,preferences_path=self.config,sessions={},profile_gate=ProfileGate());service=BackgroundDiscovery(sessions,start=False);self.addCleanup(service.close)
        def scan(catalog,*args):return dict(catalog_fingerprint=store.fingerprint(catalog),scanned_utc=datetime.now(timezone.utc).isoformat(),wallets=[],transactions={},coverage=[],timestamp_basis='UTC',source='fixture')
        with patch('background_discovery.NodeWalletLoader.collect',return_value=[ordinary]),patch('background_discovery.configure'),patch('background_discovery.HistoryClient'),patch('background_discovery.scan',side_effect=scan):service.cycle()
        self.assertEqual([w['id'] for w in store.read()['wallets']],[ordinary['id']]);saved=self.family.snapshot()
        self.assertFalse([r for r in saved['ownership'] if r.get('source') in ids]);meta=saved['ownership_runs'][self.user]
        self.assertFalse([r for r in meta['inventory'] if r['source'] in ids]);self.assertEqual(meta['files'],[{'name':'keep.ods'}])
        self.assertEqual(before,self.setup.path.read_bytes());self.assertEqual(hub_before,self.hub.path.read_bytes() if self.hub.path.exists() else None)
    def test_cancelled_policy_and_roles_can_clear_only_an_empty_internal_catalog(self):
        h,row=self.start_policy();monitor=self.cached(self.monitor(row));store=DescriptorStore(self.config);store.replace_node_wallets([monitor])
        with self.setup.edit() as s:s['sessions'][h]['state']='cancelled'
        sessions=SimpleNamespace(nodes=self.registry,preferences_path=self.config,sessions={},profile_gate=ProfileGate());service=BackgroundDiscovery(sessions,start=False);self.addCleanup(service.close)
        before=self.setup.path.read_bytes()
        with patch('background_discovery.NodeWalletLoader.collect',return_value=[]),patch('background_discovery.scan',side_effect=AssertionError('Do not publish an empty financial ledger')):service.cycle()
        self.assertEqual(store.read()['wallets'],[]);self.assertFalse(store.summary()['ready']);self.assertEqual(before,self.setup.path.read_bytes())
    def test_new_real_history_prevents_cached_role_removal_and_ownership_loss(self):
        h,row=self.start_policy();key=self.cached(self.account(0)['raw']);store=DescriptorStore(self.config);store.replace_node_wallets([key])
        with self.family.transaction() as data:data['ownership'].append({'id':resource_id('descriptor',key['id']),'kind':'descriptor','source':key['id'],'name':'Deliberately used key account'})
        fresh=copy.deepcopy(key);fresh['node_history']=[{'txid':'a'*64,'vout':0,'category':'send','amount':'-0.001','confirmations':1}]
        sessions=SimpleNamespace(nodes=self.registry,preferences_path=self.config,sessions={},profile_gate=ProfileGate());service=BackgroundDiscovery(sessions,start=False);self.addCleanup(service.close)
        def scan(catalog,*args):return dict(catalog_fingerprint=store.fingerprint(catalog),scanned_utc=datetime.now(timezone.utc).isoformat(),wallets=[],transactions={},coverage=[],timestamp_basis='UTC',source='fixture')
        with patch('background_discovery.NodeWalletLoader.collect',return_value=[fresh]),patch('background_discovery.configure'),patch('background_discovery.HistoryClient'),patch('background_discovery.scan',side_effect=scan):service.cycle()
        self.assertEqual(store.read()['wallets'][0]['node_history'],fresh['node_history'])
        self.assertTrue(any(r['source']==key['id'] for r in self.family.snapshot()['ownership']))

    def test_display_hint_never_changes_policy_fingerprint(self):
        w=self.cached(self.wallet['raw']);old=DescriptorStore.fingerprint({'wallets':[w]});w['setup_name']='Readable label'
        self.assertEqual(old,DescriptorStore.fingerprint({'wallets':[w]}))

for name in vars(ceremonies.Ceremony):
    if name.startswith('test_'):setattr(LogicalWallets,name,None)
