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
        Setup.setUp(self);self.hub.refresh_inventory=lambda *a:None
        self.hub.inventories[self.b['node_id']]={'complete':True,'wallets':[]}
        self.hub.caps={n['node_id']:{'wallet_transfer'} for n in (self.a,self.b)}
        with self.hub.edit() as state:
            for n,ip in ((self.a,'2001:db8::1'),(self.b,'2001:db8::2')):state['nodes'][n['node_id']]={'ipv6':ip}
        self.first=self.exchange(self.b,caps=['wallet_transfer']);self.target=next(p for p in self.first['proposals'] if p['kind']=='wallet_sync_queue')['requests'][0]
        self.source=next(p for p in self.exchange(self.a,caps=['wallet_transfer'])['proposals'] if p['kind']=='wallet_sync_queue')['requests'][0]
        self.preview={'version':2,'vaults':[],'address_labels':[],'transfer_id':self.source['transfer_id'],'challenge':self.source['preview_challenge'],'sender_ipv6':self.source['node_ipv6'],'receiver_ipv6':self.source['peer']['ipv6'],'public_wallet':copy.deepcopy(self.source['public_wallet']),'chain':'main','funded_outputs':[{'address':self.prev.vout[0].script_pubkey.address(),'txid':self.prev.txid().hex(),'vout':0,'amount_sats':'100000','time_received':1700000000,'blockheight':100,'confirmations':1}],'node_tip':{'height':100,'hash':'a'*64},'generated_at':int(time.time())}
        self.key=Path(self.tmp.name)/'identity';subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(self.key)],check=True,capture_output=True)
    def signed_preview(self,p=None,key=None):
        p=copy.deepcopy(p or self.preview);file=Path(self.tmp.name)/'preview';file.write_bytes(canonical(p));file.with_suffix('.sig').unlink(missing_ok=True)
        subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key or self.key),'-n','wallaby-wallet-preview-v1',str(file)],check=True,capture_output=True)
        return {'request_hash':digest(self.source),'preview':p,'public_key':' '.join((key or self.key).with_suffix('.pub').read_text().split()[:2]),'signature':base64.b64encode(file.with_suffix('.sig').read_bytes()).decode()}
    def send(self,p,node=None):return self.exchange(node or self.a,[self.item('wallet_transfer_preview',p)],caps=['wallet_transfer'])
    def result(self,node,p,status):return self.exchange(node,[self.item('wallet_transfer_result',{'request_hash':digest(p),'status':status})],caps=['wallet_transfer'])
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
    def test_profile_snapshot_checks_saved_signature_without_expiry_rewrite(self):
        from agent_state import validate
        self.send(self.signed_preview());state=self.hub.read();validate(state,{self.user},self.config)
        row=next(r for r in state['requests'].values() if r.get('signed_preview'));row['signed_preview']['preview']['funded_outputs'][0]['amount_sats']='99999'
        with self.assertRaisesRegex(ValueError,'signature'):validate(state,{self.user},self.config)
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
        result=json.loads(subprocess.check_output([sys.executable,str(runner),source,'sign',str(file)]));payload=result['payload'];self.send(payload);wire=next(p for p in self.exchange(self.b,caps=['wallet_transfer'])['proposals'] if p['kind']=='wallet_transfer_preview');self.assertEqual(wire['kind'],'wallet_transfer_preview')
        file.write_text(json.dumps({'proposal':self.target,'payload':{k:wire[k] for k in ('preview','public_key','signature')}}));received=json.loads(subprocess.check_output([sys.executable,str(runner),source,'receive',str(file)]));self.assertEqual(received['proof'],result['proof']);self.assertEqual(received['stored'],{k:payload[k] for k in ('preview','public_key','signature')})
