import base64,copy,json,os,subprocess,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_build8 import Setup
from test_node_loading import public_wallet
from descriptor_store import clean_wallet
from agent_hub import AgentHub,digest
from wallet_preview import verify,canonical
from wallet_transfers import SynchronisationBusy,Transfers

class PreviewTests(Setup):
    def setUp(self):
        super().setUp();self.hub.refresh_inventory=lambda *a:None
        self.hub.inventories[self.b['node_id']]={'complete':True,'wallets':[]}
        self.hub.caps={n['node_id']:{'wallet_transfer'} for n in (self.a,self.b)}
        with self.hub.edit() as state:
            for n,ip in ((self.a,'2001:db8::1'),(self.b,'2001:db8::2')):state['nodes'][n['node_id']]={'ipv6':ip}
        self.first=self.exchange(self.b,caps=['wallet_transfer']);self.target=next(p for p in self.first['proposals'] if p['kind']=='wallet_transfer')
        self.source=next(p for p in self.exchange(self.a,caps=['wallet_transfer'])['proposals'] if p['kind']=='wallet_transfer')
        self.preview={'version':1,'transfer_id':self.source['transfer_id'],'challenge':self.source['preview_challenge'],'sender_ipv6':self.source['node_ipv6'],'receiver_ipv6':self.source['peer']['ipv6'],'public_wallet':copy.deepcopy(self.source['public_wallet']),'chain':'main','funded_outputs':[{'address':self.prev.vout[0].script_pubkey.address(),'txid':self.prev.txid().hex(),'vout':0,'amount_sats':'100000','time_received':1700000000,'blockheight':100,'confirmations':1}],'node_tip':{'height':100,'hash':'a'*64},'generated_at':int(time.time())}
        self.key=Path(self.tmp.name)/'identity';subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(self.key)],check=True,capture_output=True)
    def signed_preview(self,p=None,key=None):
        p=copy.deepcopy(p or self.preview);file=Path(self.tmp.name)/'preview';file.write_bytes(canonical(p));file.with_suffix('.sig').unlink(missing_ok=True)
        subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key or self.key),'-n','wallaby-wallet-preview-v1',str(file)],check=True,capture_output=True)
        return {'request_hash':digest(self.source),'preview':p,'public_key':' '.join((key or self.key).with_suffix('.pub').read_text().split()[:2]),'signature':base64.b64encode(file.with_suffix('.sig').read_bytes()).decode()}
    def send(self,p,node=None):return self.exchange(node or self.a,[self.item('wallet_transfer_preview',p)],caps=['wallet_transfer'])
    def result(self,node,p,status):return self.exchange(node,[self.item('wallet_transfer_result',{'request_hash':digest(p),'status':status})],caps=['wallet_transfer'])
    def test_shared_fresh_nonce_frozen_pair_and_base_before_preview(self):
        self.assertEqual(self.source['preview_challenge'],self.target['preview_challenge']);self.assertEqual(len(self.source['preview_challenge']),64);self.assertEqual(self.source['peer']['ipv6'],self.target['node_ipv6'])
        payload=self.signed_preview();item=self.item('wallet_transfer_preview',payload);self.assertEqual(self.send(payload)['accepted'],[item['id']]);response=self.exchange(self.b,caps=['wallet_transfer']);wire=response['proposals'][0];self.assertEqual(wire,{'kind':'wallet_transfer_preview',**{k:payload[k] for k in ('preview','public_key','signature')}})
        self.assertEqual(self.exchange(self.b,caps=['wallet_transfer'])['proposals'],[self.target]);self.assertEqual(self.exchange(self.b,caps=['wallet_transfer'])['proposals'],[wire]);self.assertEqual(self.send(payload)['accepted'],[item['id']])
    def test_bad_signature_wrong_source_wallet_challenge_and_private_material_rejected(self):
        good=self.signed_preview();bad=copy.deepcopy(good);bad['preview']['funded_outputs'][0]['amount_sats']='100001'
        with self.assertRaisesRegex(ValueError,'signature'):self.send(bad)
        with self.assertRaisesRegex(ValueError,'paired sending'):self.send(good,self.b)
        for key,value in (('challenge','b'*64),('receiver_ipv6','2001:db8::9'),('transfer_id','c'*64)):
            wrong=copy.deepcopy(self.preview);wrong[key]=value
            with self.assertRaisesRegex(ValueError,'frozen request'):self.send(self.signed_preview(wrong))
        wrong=copy.deepcopy(self.preview);wrong['public_wallet']=public_wallet('Other',30)
        with self.assertRaisesRegex(ValueError,'frozen request'):self.send(self.signed_preview(wrong))
        wrong=copy.deepcopy(self.preview);wrong['funded_outputs'][0]['amount_sats']=100000
        with self.assertRaisesRegex(ValueError,'satoshi'):self.send(self.signed_preview(wrong))
    def test_preview_frozen_freshness_no_silent_renewal_and_restart(self):
        payload=self.signed_preview();self.send(payload);other=copy.deepcopy(self.preview);other['funded_outputs'][0]['amount_sats']='90000'
        with self.assertRaisesRegex(ValueError,'cannot be replaced'):self.send(self.signed_preview(other))
        expired=copy.deepcopy(self.preview);expired['generated_at']=int(time.time())-901
        with self.assertRaisesRegex(ValueError,'expired'):self.send(self.signed_preview(expired))
        restart=AgentHub(self.registry);self.assertTrue(restart.transfers.is_busy(self.a['node_id']));self.assertTrue(next(r for r in restart.read()['requests'].values() if r['direction']=='send')['signed_preview'])
        with patch('wallet_transfers.time.time',return_value=time.time()+1000):self.assertTrue(all(p['kind']=='wallet_transfer' for p in self.exchange(self.b,caps=['wallet_transfer'])['proposals']))
    def test_one_pair_alphabetical_and_no_opposite_wallet_deadlock(self):
        # Active pair cannot be replaced by later discovery, even a name sorting first.
        earlier=clean_wallet(public_wallet('AAA Wallet',22));self.hub.inventories[self.b['node_id']]['wallets'].append({'id':earlier['id'],'name':earlier['name'],'raw':earlier['raw'],'private_keys_enabled':True,'scanning':False})
        self.exchange(self.b,caps=['wallet_transfer']);self.assertEqual(self.exchange(self.a,caps=['wallet_transfer'])['proposals'],[self.source]);active=[r for r in self.hub.read()['requests'].values() if r.get('active_pair')];self.assertEqual(len(active),2);self.assertEqual({r['transfer_id'] for r in active},{self.source['transfer_id']})
    def test_initial_missing_wallet_batch_sorts_alphabetically_as_one_shared_pair(self):
        with self.hub.edit() as state:state['requests'].clear()
        self.hub.transfers.busy.clear()
        earlier=clean_wallet(public_wallet('AAA Wallet',22));self.hub.inventories[self.b['node_id']]['wallets']=[{'id':earlier['id'],'name':earlier['name'],'raw':earlier['raw'],'private_keys_enabled':True,'scanning':False}]
        receive=self.exchange(self.a,caps=['wallet_transfer'])['proposals'][0];send=self.exchange(self.b,caps=['wallet_transfer'])['proposals'][0]
        self.assertEqual(receive['public_wallet']['wallet_name'],'AAA Wallet');self.assertEqual(send['transfer_id'],receive['transfer_id']);self.assertEqual(receive['direction'],'receive');self.assertEqual(send['direction'],'send')
    def test_profile_snapshot_checks_saved_signature_without_expiry_rewrite(self):
        from agent_state import validate
        self.send(self.signed_preview());state=self.hub.read();validate(state,{self.user},self.config)
        row=next(r for r in state['requests'].values() if r.get('signed_preview'));row['signed_preview']['preview']['funded_outputs'][0]['amount_sats']='99999'
        with self.assertRaisesRegex(ValueError,'signature'):validate(state,{self.user},self.config)
    def test_busy_pauses_rpc_inventory_preserves_health_and_results_continue(self):
        with self.assertRaisesRegex(ValueError,'synchronisation is active'):self.registry.rpc.queue(self.a['node_id'],'getblockchaininfo',[])
        self.assertEqual(self.registry.health.status(self.registry.connections[self.a['connection_token']])['state'],'synchronising')
        self.hub.refresh_inventory=AgentHub.refresh_inventory.__get__(self.hub)
        with patch.object(self.hub,'rpc') as rpc:self.hub.refresh_inventory(self.a['node_id'],self.a['connection_token']);rpc.assert_not_called()
        self.result(self.a,self.source,'denied');self.assertTrue(self.hub.transfers.is_busy(self.b['node_id']));self.result(self.b,self.target,'denied');self.assertFalse(self.hub.transfers.is_busy(self.b['node_id']));self.assertEqual(self.registry.health.status(self.registry.connections[self.a['connection_token']])['state'],'ready')
    def test_denial_no_automatic_retry_new_attempt_requires_confirmation_and_fresh_challenge(self):
        self.result(self.a,self.source,'error')
        with self.assertRaisesRegex(ValueError,'both Agents'):self.hub.transfers.retry(self.source['transfer_id'],True)
        self.result(self.b,self.target,'denied');self.hub.inventory_times[self.b['node_id']]=time.monotonic();self.hub.inventory_times[self.a['node_id']]=time.monotonic()
        with self.assertRaisesRegex(ValueError,'Confirm'):self.hub.transfers.retry(self.source['transfer_id'],False)
        retried=self.hub.transfers.retry(self.source['transfer_id'],True);self.assertNotEqual(retried['transfer_id'],self.source['transfer_id']);self.assertFalse(self.exchange(self.b,caps=['wallet_transfer'])['proposals'][0]['preview_challenge']==self.source['preview_challenge'])
    def test_legacy_pair_requires_explicit_retry_instead_of_silent_nonce_upgrade(self):
        with self.hub.edit() as state:
            for row in state['requests'].values():
                row['proposal'].pop('preview_challenge');row['proposal'].pop('node_ipv6')
        reply=self.exchange(self.a,caps=['wallet_transfer']);self.assertFalse(any(p['kind']=='wallet_transfer' for p in reply['proposals']));self.assertTrue(all(r['state']=='error' for r in self.hub.read()['requests'].values()))
    def test_cancelled_signing_job_accepts_local_denial_without_reopening(self):
        from agent_transactions import accept_result
        from transaction_workflows import WorkflowStore
        ident=self.job();store=WorkflowStore(self.config);data=store._read();data['jobs'][ident]['cancelled']=True;store._write(data)
        request={'kind':'sign_psbt_partial','node_id':self.a['node_id'],'job_id':ident,'wallet_id':self.rid,'state':'pending'}
        with self.hub.edit() as state:
            state['requests']['f'*64]=request;accept_result(self.hub,state,self.a['node_id'],'partial_sign_result',{'request_hash':'f'*64,'status':'denied'})
        self.assertTrue(store.job(self.user,ident)['cancelled']);self.assertEqual(self.hub.read()['requests']['f'*64]['state'],'denied')
    def test_agent7_real_signed_preview_and_receiver_interoperate(self):
        source=os.environ.get('WALLABY_AGENT_SOURCE')
        if not source:self.skipTest('Set WALLABY_AGENT_SOURCE to Agent Build 7 src.')
        file=Path(self.tmp.name)/'agent-input.json';file.write_text(json.dumps({'proposal':self.source,'preview':self.preview}));runner=Path(__file__).with_name('agent_preview_runner.py')
        result=json.loads(subprocess.check_output([sys.executable,str(runner),source,'sign',str(file)]));payload=result['payload'];self.send(payload);wire=self.exchange(self.b,caps=['wallet_transfer'])['proposals'][0];self.assertEqual(wire['kind'],'wallet_transfer_preview')
        file.write_text(json.dumps({'proposal':self.target,'payload':{k:wire[k] for k in ('preview','public_key','signature')}}));received=json.loads(subprocess.check_output([sys.executable,str(runner),source,'receive',str(file)]));self.assertEqual(received['proof'],result['proof']);self.assertEqual(received['stored'],{k:payload[k] for k in ('preview','public_key','signature')})
