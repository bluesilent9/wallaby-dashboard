"""Unloaded folders cannot hide a loaded private wallet from archive choices."""
import copy
from unittest.mock import patch
import test_build35 as fixtures
from live_wallet_traits import watch_flags

class LoadedInventory(fixtures.Archives):
    def prepare(self,unloaded=('Old key folder','Old public policy folder'),private=True):
        self.node=self.a['node_id'];self.name=self.wallet['raw']['wallet_name'];self.calls=[]
        self.hub.inventories[self.node]={'complete':False,'wallets':[]}
        def rpc(node,method,params=None,wallet=None):
            self.calls.append((node,method,wallet))
            if method=='listwallets':return [self.name]
            if method=='listwalletdir':return {'wallets':[{'name':n} for n in (self.name,*unloaded)]}
            if method=='getwalletinfo':return {'walletname':self.name,'private_keys_enabled':private,'scanning':False}
            if method=='listdescriptors':return copy.deepcopy(self.wallet['raw'])
            raise AssertionError(method)
        self.hub.rpc=rpc;return rpc
    def test_loaded_wallet_can_be_selected_and_archived_with_unrelated_unloaded_folders(self):
        self.prepare();self.hub.refresh_inventory(self.node,self.a['connection_token'])
        inv=self.hub.inventories[self.node]
        self.assertFalse(inv['complete']);self.assertTrue(inv['loaded_complete']);self.assertEqual(len(inv['unloaded_wallets']),2)
        self.assertEqual([w['name'] for w in inv['wallets']],[self.name])
        options=self.queue.options(self.rid);source=next(r for r in options if r['node_id']==self.node)
        row=next(r for r in self.queue.destinations(self.rid,options) if r['node_id']==self.node);self.assertTrue(row['available'])
        self.queue.create(self.user,self.rid,[source['id']]);requests=list(self.queue.read()['requests'].values())
        self.assertEqual(len(requests),1);self.assertEqual(requests[0]['node_id'],self.node)
        self.assertEqual(requests[0]['request']['wallet'],self.name)
        self.assertFalse(watch_flags({'wallets':[self.wallet]},self.registry)[self.wallet['id']])
        self.assertFalse(self.queue.proposal(self.b['node_id'])['requests'])
    def test_partial_loaded_inventory_never_proves_a_wallet_missing_for_sync(self):
        self.prepare();self.hub.refresh_inventory(self.node,self.a['connection_token'])
        self.hub.inventories[self.b['node_id']]={'wallets':[],'complete':True}
        with self.hub.edit() as state:self.hub.pair_transfers(state)
        self.assertFalse(self.hub.read()['requests'])
    def test_watch_only_stays_unavailable_even_with_loaded_inventory(self):
        self.prepare(private=False);self.hub.refresh_inventory(self.node,self.a['connection_token'])
        options=self.queue.options(self.rid);self.assertFalse(any(r['node_id']==self.node for r in options))
        row=next(r for r in self.queue.destinations(self.rid,options) if r['node_id']==self.node)
        self.assertFalse(row['available']);self.assertIn('Watch-only',row['reason'])
    def test_disc_waiting_policy_refreshes_loaded_inventory_without_rewriting_ceremony(self):
        self.prepare()
        builder=self.hub.wallet_builder
        with builder.edit() as state:
            state['sessions']['a'*64]={'state':'policy','routes':{'one':self.node},'inventories':{self.node:{'public_evidence':'retained'}},'acks':{'local':'retained'},'contributions':{'key':'retained'}}
        before=builder.path.read_bytes();self.hub.prioritise([self.node])
        self.hub.refresh_inventory(self.node,self.a['connection_token'])
        self.assertTrue(self.hub.inventories[self.node]['loaded_complete'])
        self.assertEqual(before,builder.path.read_bytes())
    def test_explicit_refresh_is_allowed_during_identity_priority_window(self):
        self.prepare();self.hub.prioritise([self.node])
        self.hub.refresh_inventory(self.node,self.a['connection_token'],requested=True)
        self.assertTrue(self.hub.inventories[self.node]['loaded_complete'])
    def test_complete_inventory_still_becomes_complete_when_all_folders_loaded(self):
        self.prepare(unloaded=());self.hub.refresh_inventory(self.node,self.a['connection_token'])
        self.assertTrue(self.hub.inventories[self.node]['complete']);self.assertEqual(self.hub.inventories[self.node]['unloaded_wallets'],[])
    def test_background_discovery_collects_loaded_wallets_but_strict_scan_reports_unknown_folders(self):
        from node_wallets import NodeWalletLoader
        self.prepare()
        node=next(n for n in self.registry.presence()['nodes'] if n['id']==self.node);messages=[]
        loader=NodeWalletLoader(self.registry,messages.append,tolerate_errors=True)
        def rpc(n,method,params=None,wallet=None):return self.hub.rpc(n['id'],method,params,wallet)
        with patch.object(loader,'rpc',side_effect=rpc):
            self.assertEqual(loader.discover_node(node),[(node,self.name)])
            self.assertTrue(any('saved accounts are retained' in message for message in messages))
        strict=NodeWalletLoader(self.registry,messages.append)
        with patch.object(strict,'rpc',side_effect=rpc),self.assertRaisesRegex(ValueError,'load these wallets'):
            strict.discover_node(node)

    def test_malformed_directory_never_claims_loaded_inventory_success(self):
        original=self.prepare()
        def rpc(node,method,params=None,wallet=None):
            return {'wallets':[{'unexpected':'bad directory'}]} if method=='listwalletdir' else original(node,method,params,wallet)
        self.hub.rpc=rpc;self.hub.refresh_inventory(self.node,self.a['connection_token'])
        inv=self.hub.inventories[self.node];self.assertFalse(inv['complete']);self.assertFalse(inv.get('loaded_complete'))
        self.assertIn('directory',inv['error'])
    def test_failed_read_never_publishes_partial_wallet_list(self):
        original=self.prepare()
        def rpc(node,method,params=None,wallet=None):
            if method=='listdescriptors':raise ValueError('fixture unavailable')
            return original(node,method,params,wallet)
        self.hub.rpc=rpc;self.hub.refresh_inventory(self.node,self.a['connection_token'])
        self.assertFalse(self.hub.inventories[self.node].get('loaded_complete'));self.assertEqual(self.hub.inventories[self.node]['wallets'],[])

for name in vars(fixtures.Archives):
    if name.startswith('test_'):setattr(LoadedInventory,name,None)
