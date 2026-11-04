"""Reconnect recovery can resume approvals but cannot regenerate signing keys."""
import copy,hashlib
import test_build11 as fixture
import test_build46 as multisig_fixture
from wallet_builder import WalletBuilder
from builder_workspace import BuilderWorkspace

class Recovery(fixture.Ceremony):
    def active(self,disc=False):
        w=BuilderWorkspace(self.config).start(self.user,'wallet');h,row=self.start(disc)
        with self.setup.edit() as s:s['sessions'][h]['workspace_id']=w['id']
        BuilderWorkspace(self.config).attach(self.user,'wallet',h)
        return h,self.setup.read()['sessions'][h]
    def interrupted(self,row):
        r=copy.deepcopy(row);r['state']='interrupted';r['message']='Agent reconnected during setup. Reconcile its pending ceremony locally before starting another.';return r
    def test_dashboard_restart_delivers_same_policy_without_participation(self):
        h,row=self.active(True);row=self.contribute(h,row);workspace=BuilderWorkspace(self.config).path.read_bytes()
        other=WalletBuilder(self.hub);saved=other.read()['sessions'][h]
        self.assertEqual(saved['state'],'policy')
        for field in ('proposal','contributions','policy_hash','descriptors','routes','acks'):self.assertEqual(saved[field],row[field])
        self.assertEqual(workspace,BuilderWorkspace(self.config).path.read_bytes())
        p=other.proposals(self.a['node_id'],'new-token');self.assertEqual([x['kind'] for x in p],['wallet_setup_policy'])
        self.assertEqual(p[0]['proposal'],row['proposal']);self.assertEqual(p[0]['receive_descriptor'],row['descriptors'][0])
    def test_transport_reconnect_does_not_stop_existing_policy(self):
        h,row=self.active();row=self.contribute(h,row)
        first=self.setup.proposals(self.a['node_id'],'old');second=self.setup.proposals(self.a['node_id'],'new')
        self.assertEqual(first,second);self.assertEqual(self.setup.read()['sessions'][h]['state'],'policy')
        self.assertNotIn('wallet_setup',[p['kind'] for p in second])
    def test_delayed_final_contribution_recovers_old_interrupted_build(self):
        h,row=self.active();row=self.contribute(h,row);expected=copy.deepcopy(row)
        with self.setup.edit() as s:
            saved=s['sessions'][h];saved['contributions'].pop('signer-2');saved.pop('policy_hash');saved.pop('descriptors');saved.update(state='interrupted',message='Agent reconnected during setup. Reconcile its pending ceremony locally before starting another.')
        self.setup.accept(self.b['node_id'],'wallet_setup_contribution',row['contributions']['signer-2'])
        saved=self.setup.read()['sessions'][h];self.assertEqual(saved['state'],'policy')
        for field in ('proposal','contributions','policy_hash','descriptors'):self.assertEqual(saved[field],expected[field])
    def test_partial_restart_waits_for_existing_key_evidence(self):
        h,row=self.active();row=self.contribute(h,row)
        with self.setup.edit() as s:
            r=s['sessions'][h];r['contributions'].pop('signer-2');r.pop('policy_hash');r.pop('descriptors');r['state']='participation'
        other=WalletBuilder(self.hub);self.assertEqual(other.read()['sessions'][h]['state'],'interrupted');self.assertFalse(other.proposals(self.a['node_id'],'new'))
        other.accept(self.b['node_id'],'wallet_setup_contribution',row['contributions']['signer-2']);self.assertEqual(other.read()['sessions'][h]['state'],'policy')
    def test_already_contributed_node_never_receives_participation_again(self):
        h,row=self.active();row=self.contribute(h,row)
        with self.setup.edit() as s:
            r=s['sessions'][h];r['contributions'].pop('signer-2');r.pop('policy_hash');r.pop('descriptors');r['state']='participation'
        self.assertFalse(self.setup.proposals(self.a['node_id'],'old'));self.assertFalse(self.setup.proposals(self.a['node_id'],'new'))
        self.assertEqual(self.setup.read()['sessions'][h]['state'],'participation')
        self.assertEqual(self.setup.proposals(self.b['node_id'],'first')[0]['kind'],'wallet_setup')
    def test_cancellation_denial_expiry_and_unbound_workspaces_never_resume(self):
        h,row=self.active();row=self.contribute(h,row)
        for state,reason in (('cancelled','restart'),('interrupted','denied'),('interrupted','expired')):
            candidate=self.interrupted(row);candidate.update(state=state,interruption_reason=reason);self.assertFalse(self.setup.recover_policy(h,candidate))
        denied=self.interrupted(row);e=self.ack(h,row,self.a,'denied');denied['acks']['denial']=e;self.assertFalse(self.setup.recover_policy(h,denied))
        candidate=self.interrupted(row);candidate['workspace_id']='other';self.assertFalse(self.setup.recover_policy(h,candidate))
        with self.setup.edit() as s:s['sessions'][h].update(state='interrupted',interruption_reason='denied')
        self.assertEqual(WalletBuilder(self.hub).read()['sessions'][h]['state'],'interrupted')
    def test_tampered_keys_or_retained_policy_never_replaced(self):
        h,row=self.active();row=self.contribute(h,row)
        for mutate in ('signature','policy'):
            r=self.interrupted(row)
            if mutate=='signature':r['contributions']['signer-1']['signature']='bad'
            else:r['descriptors'][0]=r['descriptors'][1]
            before=copy.deepcopy(r);self.assertFalse(self.setup.recover_policy(h,r));self.assertEqual(r['state'],'interrupted')
            for field in ('proposal','contributions','policy_hash','descriptors','routes'):self.assertEqual(r[field],before[field])
            self.assertIn('resume_error',r)
    account=multisig_fixture.SignerSync.account
    schedule=multisig_fixture.SignerSync.schedule
    def test_three_agents_five_existing_roles_resume_the_same_vault(self):
        multisig_fixture.SignerSync.test_three_of_five_active_creation_keeps_roles_and_approvals(self)
        h,row=next(reversed(self.setup.read()['sessions'].items()));workspace=BuilderWorkspace(self.config).path.read_bytes()
        other=WalletBuilder(self.hub);saved=other.read()['sessions'][h]
        self.assertEqual(saved['state'],'policy');self.assertEqual(saved['proposal']['threshold'],3);self.assertEqual(len(saved['contributions']),5)
        for field in ('proposal','contributions','acks','routes','policy_hash','descriptors'):self.assertEqual(saved[field],row[field])
        for node in set(saved['routes'].values()):
            proposals=other.proposals(node,'reconnected-'+node)
            self.assertEqual([p['kind'] for p in proposals],['wallet_setup_policy'])
            self.assertEqual(proposals[0]['receive_descriptor'],row['descriptors'][0])
        self.assertEqual(workspace,BuilderWorkspace(self.config).path.read_bytes())

    def test_existing_approvals_survive_but_disc_receipts_are_still_required(self):
        h,row=self.active(True);row=self.contribute(h,row)
        for n in (self.a,self.b):self.setup.accept(n['node_id'],'wallet_setup_ack',self.ack(h,row,n,'policy_approved',backup='pending' if n==self.b else 'not_required'))
        before=self.setup.read()['sessions'][h];other=WalletBuilder(self.hub);saved=other.read()['sessions'][h];self.assertEqual(saved['state'],'policy');self.assertEqual(saved['acks'],before['acks'])
        proof=self.ack(h,row,self.b,'backup_verified',cosigner_file=hashlib.sha256(b'signer-2').hexdigest(),image_sha256='a'*64);other.accept(self.b['node_id'],'wallet_setup_ack',proof)
        self.assertEqual(other.read()['sessions'][h]['state'],'ready')

for name in vars(fixture.Ceremony):
    if name.startswith('test_'):setattr(Recovery,name,None)
