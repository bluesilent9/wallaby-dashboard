"""Separate signing roles with Core's full default descriptor inventory."""
import copy
import test_build11 as ceremony
from vendor.embit import bip32
from vendor.embit.descriptor.checksum import checksum
from policy_accounts import signing_account,intentional_account
from builder_workspace import BuilderWorkspace
from agent_hub import digest

class SignerSync(ceremony.Ceremony):
    def account(self,index):
        rows=[]
        # Real Core generated accounts include four independent script families,
        # not only the two BIP84 descriptors exported as a setup contribution.
        for purpose,wrapper in ((44,'pkh({})'),(49,'sh(wpkh({}))'),(84,'wpkh({})'),(86,'tr({})')):
            master=bip32.HDKey.from_seed(bytes([index+1])*32).derive('m/'+str(purpose)+'h/0h/0h')
            key='['+master.my_fingerprint.hex()+'/'+str(purpose)+'h/0h/0h]'+master.to_public().to_base58()
            for branch in (0,1):
                body=wrapper.format(key+'/'+str(branch)+'/*');rows.append({'desc':body+'#'+checksum(body),'range':[0,999],'timestamp':1,'active':True,'internal':bool(branch),'next_index':0})
        return {'id':'account-'+str(index),'name':'Uninformative label','raw':{'wallet_name':'Uninformative label','descriptors':rows},'private_keys_enabled':True,'scanning':False}
    def inventory(self):
        for i,n in enumerate((self.a,self.b)):
            self.hub.inventories[n['node_id']]={'complete':True,'wallets':[self.account(i)]}
        for i,n in enumerate((self.a,self.b)):
            self.exchange(n,caps=['wallet_transfer'],node_info={'ipv6':'fd12::'+str(i+1)})
        self.exchange(self.a,caps=['wallet_transfer'])
    def schedule(self):
        with self.hub.edit() as state:
            self.hub.pair_transfers(state);self.hub.transfers.schedule_queues(state)
        return self.hub.read()
    def test_full_core_accounts_before_monitors_are_not_replicated(self):
        h,row=self.start();row=self.contribute(h,row)
        workspace=BuilderWorkspace(self.config).start(self.user,'wallet')
        before=self.setup.path.read_bytes();workspaces=BuilderWorkspace(self.config).path.read_bytes()
        for i,n in enumerate((self.a,self.b)):self.hub.inventories[n['node_id']]={'complete':True,'wallets':[self.account(i)]}
        self.assertFalse(self.schedule()['requests'])
        self.assertEqual(before,self.setup.path.read_bytes());self.assertEqual(workspaces,BuilderWorkspace(self.config).path.read_bytes())
    def test_existing_invitations_withdraw_and_outcomes_remain_addressable(self):
        h,row=self.start();self.inventory();before=copy.deepcopy(self.hub.read()['requests'])
        self.assertEqual(len(before),4);self.assertTrue(all(r.get('queue_visible') for r in before.values()))
        self.contribute(h,row);setup=self.setup.path.read_bytes();after=self.schedule()
        self.assertEqual(set(after['requests']),set(before));self.assertEqual(setup,self.setup.path.read_bytes())
        for k,r in before.items():
            saved=after['requests'][k];self.assertTrue(saved['superseded_container']);self.assertFalse(saved['queue_visible'])
            for field in ('proposal','state','transfer_id','base_sent','challenge'):self.assertEqual(saved[field],r[field])
        for n in (self.a,self.b):
            self.assertEqual(after['nodes'][n['node_id']]['sync_snapshot'],[])
            self.assertGreater(after['nodes'][n['node_id']]['sync_revision'],0)
        self.assertTrue(all(not r['queue_visible'] for r in self.schedule()['requests'].values()))
        receiver=next(r for r in before.values() if r['direction']=='receive')
        with self.hub.edit() as state:self.hub.transfers.accept_result(state,receiver['node_id'],{'request_hash':digest(receiver['proposal']),'status':'recovery_required'})
        saved=next(r for r in self.hub.read()['requests'].values() if r['proposal']==receiver['proposal'])
        self.assertEqual(saved['state'],'recovery_required')
    def test_external_monitor_matches_core_account_but_not_other_account(self):
        account=self.account(0);wanted=[r for r in account['raw']['descriptors'] if r['desc'].startswith('wpkh(')]
        other=self.account(1);external=[r for r in other['raw']['descriptors'] if r['desc'].startswith('wpkh(')]
        rows=[]
        for a,b in zip(wanted,external):
            body='wsh(sortedmulti(2,'+a['desc'].split('#')[0][5:-1]+','+b['desc'].split('#')[0][5:-1]+'))';rows.append({'desc':body+'#'+checksum(body)})
        monitor={'raw':{'wallet_name':'not a name match','descriptors':rows},'private_keys_enabled':False,'id':'monitor','name':'Monitor'}
        self.assertTrue(signing_account(account,monitor));self.assertFalse(signing_account(self.account(2),monitor))
        self.hub.inventories[self.a['node_id']]={'complete':True,'wallets':[account,monitor,self.account(2)]}
        self.hub.inventories[self.b['node_id']]={'complete':True,'wallets':[monitor]}
        rows=self.schedule()['requests'].values();self.assertEqual(len(list(rows)),2)
        self.assertTrue(all(r['descriptor_id']=='account-2' for r in rows))
    def test_cached_public_monitor_still_protects_signers_when_offline(self):
        self.inventory();before=self.hub.read();self.assertEqual(len(before['requests']),4)
        account=self.account(0);ks=[r['desc'].split('#')[0][5:-1] for r in account['raw']['descriptors'] if r['desc'].startswith('wpkh(')]
        bodies=['wsh(and_v(v:after(1000000),pk('+k+')))' for k in ks]
        with self.hub.edit() as state:state['nodes'][self.a['node_id']].setdefault('known_wallets',{})['monitor']={'raw':{'wallet_name':'Cached vault','descriptors':[{'desc':b+'#'+checksum(b)} for b in bodies]}}
        self.hub.inventories.clear();after=self.schedule()
        self.assertTrue(all(r.get('superseded_container') for r in after['requests'].values() if r['descriptor_id']=='account-0'))
        self.assertTrue(all(not r.get('superseded_container') for r in after['requests'].values() if r['descriptor_id']=='account-1'))
    def test_three_of_five_active_creation_keeps_roles_and_approvals(self):
        import subprocess
        from setup_signatures import fingerprint
        c=self.register('02:80:00:00:00:03','third')
        self.registry.connections[c['connection_token']]['agent_version']='2.0.0~dev38'
        key=self.folder/c['node_id'];subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','','-f',str(key)],check=True);self.keys[c['node_id']]=key
        self.setup.discover()
        for n in (self.a,self.b,c):
            pub=self.pub(n);challenge=self.setup.read()['challenges'][n['node_id']]
            self.setup.accept(n['node_id'],'wallet_setup_identity',self.sign(n,{'version':1,'challenge':challenge,'public_key':pub,'fingerprint':fingerprint(pub),'hostname':'Producing Agent'}))
        w=BuilderWorkspace(self.config).start(self.user,'wallet');nodes=(self.a,self.b,c,self.a,self.b)
        result=self.setup.start(self.user,{'name':'Invented 3 of 5','threshold':3,'height':1000000,'fingerprints_compared':True,'roles':[{'node_id':n['node_id'],'name':'Independent role '+str(i+1),'storage':'node' if i<3 else 'disc'} for i,n in enumerate(nodes)]},w['id'])
        h=result['sessions'][-1]['id'];row=self.setup.read()['sessions'][h]
        for i,n in enumerate(nodes):
            master=bip32.HDKey.from_seed(bytes([i+1])*32).derive('m/84h/0h/0h')
            prefix='['+master.my_fingerprint.hex()+"/84'/0'/0']"+master.to_public().to_base58()
            self.setup.accept(n['node_id'],'wallet_setup_contribution',self.sign(n,{'session_id':row['proposal']['session_id'],'proposal_hash':h,'cosigner_id':'signer-'+str(i+1),'receive_key':prefix+'/0/*','change_key':prefix+'/1/*'}))
        row=self.setup.read()['sessions'][h]
        self.setup.accept(self.a['node_id'],'wallet_setup_ack',self.ack(h,row,self.a,'policy_approved',backup='pending'))
        before=self.setup.path.read_bytes();workspace=BuilderWorkspace(self.config).path.read_bytes()
        for i,n in enumerate(nodes[:3]):self.hub.inventories[n['node_id']]={'complete':True,'wallets':[self.account(i)]}
        self.assertFalse(self.schedule()['requests'])
        self.assertEqual(before,self.setup.path.read_bytes());self.assertEqual(workspace,BuilderWorkspace(self.config).path.read_bytes())

    def test_finished_policy_monitor_can_sync_without_its_private_role(self):
        h,row=self.start();row=self.contribute(h,row)
        with self.setup.edit() as state:state['sessions'][h].update(state='ready',completion_confirmed={self.a['node_id']:'now',self.b['node_id']:'now'})
        monitor={'name':'Public monitor','id':'public-monitor','private_keys_enabled':False,'raw':{'wallet_name':'Public monitor','descriptors':[{'desc':d,'range':[0,999],'timestamp':1} for d in row['descriptors']]}}
        self.hub.inventories[self.a['node_id']]={'complete':True,'wallets':[monitor,self.account(0)]}
        self.hub.inventories[self.b['node_id']]={'complete':True,'wallets':[]}
        rows=self.schedule()['requests'].values();self.assertEqual(len(rows),2)
        self.assertTrue(all(r['descriptor_id']=='public-monitor' and not r['source_has_private_keys'] for r in rows))

    def test_labels_and_unrelated_wpkh_keys_cannot_establish_role(self):
        h,row=self.start();row=self.contribute(h,row);a=self.account(2);a['name']=row['proposal']['name']
        self.assertFalse(intentional_account(a['raw'],[row],[]))
        mixed=self.account(0);mixed['raw']['descriptors']+=a['raw']['descriptors']
        self.assertFalse(intentional_account(mixed['raw'],[row],[]))

for name in vars(ceremony.Ceremony):
    if name.startswith('test_'):setattr(SignerSync,name,None)
