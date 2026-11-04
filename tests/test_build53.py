"""Logical naming, shared policies, and role visibility for combined signer wallets."""
import copy
from unittest.mock import patch
import test_build52 as fixture
from descriptor_store import DescriptorStore,clean_wallet
from wallet_names import display_inventory,display_summary
from wallet_views import annotate_signers
from setup_wallet_views import SetupWalletViews
from node_wallets import NodeWalletLoader
from preferences import resource_id

class CombinedNaming(fixture.ExactWorkspace):
    def install(self,height):
        h,row=self.start(height=height);row=self.contribute(h,row)
        names={}
        for i,node in enumerate((self.a,self.b)):
            name=row['proposal']['name']+' '+row['proposal']['cosigners'][i]['name']+' - Signer';names[node['node_id']]=name
            message={'version':1,'session_id':row['proposal']['session_id'],'policy_hash':row['policy_hash'],'chain':'main','monitor_wallet':name,
                     'signing_wallets':{'signer-'+str(i+1):name},'receive_descriptor':row['descriptors'][0],'change_descriptor':row['descriptors'][1]}
            self.setup.accept(node['node_id'],'wallet_setup_inventory',self.sign(node,message))
            self.setup.accept(node['node_id'],'wallet_setup_ack',self.ack(h,row,node,'policy_approved',backup='not_required'))
        with self.setup.edit() as state:state['sessions'][h]['completion_confirmed']={n['node_id']:'now' for n in (self.a,self.b)}
        for node in (self.a,self.b):
            name=names[node['node_id']]
            raw={'wallet_name':name,'descriptors':[{'desc':d,'range':[0,999],'active':True,'internal':bool(i),'timestamp':1} for i,d in enumerate(row['descriptors'])]}
            self.hub.inventories[node['node_id']]={'complete':True,'wallets':[{'name':name,'raw':raw,'private_keys_enabled':True}]}
        replicas=[]
        nodes={n['id']:n for n in self.registry.snapshot()['nodes']}
        for node,inv in self.hub.inventories.items():
            local=inv['wallets'][0];replicas.append({'node':nodes[node],'rpc_name':local['name'],'wallet':clean_wallet(local['raw']),'history':{'transactions':[],'lastblock':'a'*64},'private_keys_enabled':True})
        loader=NodeWalletLoader(self.registry,lambda _:None)
        wallet=SetupWalletViews(self.config).annotate(loader.merge(replicas))
        DescriptorStore(self.config).replace_node_wallets([wallet])
        rid=resource_id('descriptor',wallet['id'])
        self.family.save_ownership([{'id':rid,'name':row['proposal']['name'],'owner_id':self.user,'external':False,'smsf':False}],self.family.snapshot()['revision'],[{'id':rid,'kind':'descriptor','source':wallet['id'],'name':row['proposal']['name']}])
        for inv in self.hub.inventories.values():
            for item in inv['wallets']:item.update(id=wallet['id'],scanning=False)
        report={'balances':{'wallets':[{'descriptor_id':wallet['id'],'multisig':[{'required':2,'total':2}]}]}}
        return h,row,wallet,report
    def test_regular_and_vault_wallets_have_one_logical_name_and_correct_two_visible_roles(self):
        for height in (None,1000000):
            with self.subTest(height=height):
                # Each iteration uses an independent profile/keys.
                if height is not None:self.tearDown();self.setUp()
                h,row,wallet,report=self.install(height)
                catalog=DescriptorStore(self.config).read();summary=display_summary(DescriptorStore(self.config).summary(),{'ownership':[]})
                self.assertEqual(len(summary['wallets']),1);self.assertEqual(summary['wallets'][0]['name'],row['proposal']['name'])
                self.assertNotIn('Signer',summary['wallets'][0]['name']);self.assertEqual(NodeWalletLoader.vault_height(row['descriptors'][0]),height)
                self.assertEqual(summary['wallets'][0]['multisig'],[{'required':2,'total':2}])
                item={'kind':'descriptor','source':wallet['id'],'name':'technical','id':resource_id('descriptor',wallet['id'])}
                self.assertEqual(display_inventory([item],catalog)[0]['name'],row['proposal']['name'])
                self.assertEqual(len(wallet['node_sources']),2)
                annotate_signers(report,self.hub);card=report['balances']['wallets'][0]
                self.assertEqual(card['multisig'][0]['visible'],2);self.assertEqual([r['name'] for r in card['signers']],[r['name'] for r in row['proposal']['cosigners']])
                self.registry.connections.pop(self.b['connection_token'])
                annotate_signers(report,self.hub);self.assertEqual(card['multisig'][0]['visible'],1)
                self.hub.inventories[self.a['node_id']]['wallets'][0]['private_keys_enabled']=False
                annotate_signers(report,self.hub);self.assertEqual(card['multisig'][0]['visible'],0)
    def test_public_policy_does_not_count_all_keys_and_managed_role_can_survive_filename_change(self):
        h,row,wallet,report=self.install(1000000)
        self.hub.inventories[self.b['node_id']]['wallets']=[]
        local=self.hub.inventories[self.a['node_id']]['wallets'][0];local['name']='Changed filename'
        annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],0)
        local['managed']={'kind':'combined','setup_id':h,'policy_hash':row['policy_hash'],'role_id':'signer-1'}
        annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],1)
        local['managed']['role_id']='signer-2'
        annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],0)
    def test_disc_role_requires_loaded_private_account_and_matching_authenticated_membership(self):
        from archive_queue import ArchiveQueue
        from vendor.embit.descriptor.checksum import checksum
        h,row=self.start(disc=True,height=1000000);row=self.contribute(h,row)
        raw={'wallet_name':'Loaded public policy','descriptors':[{'desc':d,'range':[0,999],'active':True,'internal':bool(i),'timestamp':1} for i,d in enumerate(row['descriptors'])]}
        wallet=clean_wallet(raw);DescriptorStore(self.config).replace_node_wallets([wallet])
        for inv in self.hub.inventories.values():inv['wallets']=[]
        archive='d'*64
        self.hub.inventories[self.a['node_id']]['wallets']=[{'name':'Loaded disc role','raw':raw,'private_keys_enabled':True,'signing_only':True,'archive_id':archive}]
        report={'balances':{'wallets':[{'descriptor_id':wallet['id'],'multisig':[{'required':2,'total':2}]}]}}
        annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],0)
        contribution=row['contributions']['signer-2']['message'];branches=[]
        for i,f in enumerate(('receive_key','change_key')):
            body='wpkh('+contribution[f]+')';branches.append({'desc':body+'#'+checksum(body),'range':[0,999],'active':True,'internal':bool(i),'timestamp':1})
        message={'version':1,'event':'archive_observed','archive_id':archive,'payload_sha256':archive,'payload_verified':True,'readback_verified':False,'members':[{'member_id':'e'*64,'role_id':'signer-2','public_wallet':{'wallet_name':'Disc role','descriptors':branches}}]}
        ArchiveQueue(self.hub).followup(self.a['node_id'],self.sign(self.a,message))
        annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],1)
        self.hub.inventories[self.a['node_id']]['wallets']=[]
        annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],0)

    def test_distinct_combined_signing_accounts_on_same_agent_have_distinct_request_ids(self):
        from agent_transactions import eligible
        h,row,wallet,report=self.install(None)
        self.hub.caps[self.a['node_id']]={'partial_signing'}
        copy_role=copy.deepcopy(self.hub.inventories[self.a['node_id']]['wallets'][0]);copy_role['name']='Other local signing role';copy_role['raw']['wallet_name']=copy_role['name']
        self.hub.inventories[self.a['node_id']]['wallets'].append(copy_role)
        sources=eligible(self.hub,resource_id('descriptor',wallet['id']))
        self.assertEqual(len(sources),2);self.assertEqual(len({s['signer_id'] for s in sources}),2)
    def test_missing_node_receives_only_public_policy_never_a_private_cosigner_clone(self):
        h,row,wallet,report=self.install(1000000)
        self.hub.inventories[self.b['node_id']]['wallets']=[]
        with self.hub.edit() as state:self.hub.pair_transfers(state)
        rows=list(self.hub.read()['requests'].values());self.assertEqual(len(rows),2)
        self.assertTrue(all(not r['source_has_private_keys'] for r in rows))
        self.assertTrue(self.hub.inventories[self.a['node_id']]['wallets'][0]['private_keys_enabled'])
        with self.hub.edit() as state:
            for r in state['requests'].values():r['source_has_private_keys']=True
            self.hub.transfers.schedule_queues(state)
        self.assertTrue(all(r.get('superseded_container') for r in self.hub.read()['requests'].values()))

    def test_duplicate_same_role_copy_never_counts_twice(self):
        h,row,wallet,report=self.install(None)
        self.hub.inventories[self.b['node_id']]['wallets']=[]
        local=self.hub.inventories[self.a['node_id']]['wallets'][0]
        self.hub.inventories[self.a['node_id']]['wallets'].append(copy.deepcopy(local))
        annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],1)

for name in vars(fixture.ExactWorkspace):
    if name.startswith('test_'):setattr(CombinedNaming,name,None)
