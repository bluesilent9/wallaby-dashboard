"""Authenticated setup and threshold signing, using invented keys and isolated stores."""
import base64,copy,hashlib,json,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_build8 import Setup
from setup_signatures import fingerprint,verify
from wallet_builder import WalletBuilder,validate_archive
from wallet_setup_policy import policy
from vendor.embit import bip32
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from vendor.embit.script import Script
from vendor.embit.transaction import Transaction,TransactionInput,TransactionOutput
from signed_psbt import serialize,decode,utxos,final_transaction,combine
from multisig_psbt import validate_partial,progress,witness_policy
from policy_accounts import signing_account,cosigner_accounts
from descriptor_store import clean_wallet,DescriptorStore
from preferences import resource_id
from agent_transactions import eligible,queue_sign,signing_state
from agent_hub import digest

class Ceremony(Setup):
    def setUp(self):
        super().setUp();self.setup=self.hub.wallet_builder;self.keys={};self.folder=Path(self.tmp.name)
        for n in (self.a,self.b):
            self.registry.connections[n['connection_token']]['agent_version']='2.0.0~dev9';key=self.folder/n['node_id'];subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','','-f',str(key)],check=True);self.keys[n['node_id']]=key
        self.setup.discover()
        for n in (self.a,self.b):
            pub=self.pub(n);challenge=self.setup.read()['challenges'][n['node_id']];self.setup.accept(n['node_id'],'wallet_setup_identity',self.sign(n,{'version':1,'challenge':challenge,'public_key':pub,'fingerprint':fingerprint(pub),'hostname':'Example Agent'}))
    def pub(self,n):return ' '.join(self.keys[n['node_id']].with_suffix('.pub').read_text().split()[:2])
    def sign(self,n,message):
        p=self.folder/'message';p.write_text(json.dumps(message,sort_keys=True,separators=(',',':'),ensure_ascii=True));p.with_suffix('.sig').unlink(missing_ok=True);subprocess.run(['ssh-keygen','-Y','sign','-f',str(self.keys[n['node_id']]),'-n','wallaby-wallet-setup-v1',str(p)],capture_output=True,check=True)
        return {'message':message,'public_key':self.pub(n),'signature':base64.b64encode(p.with_suffix('.sig').read_bytes()).decode()}
    def start(self,disc=False,height=None):
        result=self.setup.start(self.user,{'name':'Example shared wallet','threshold':2,'height':height,'fingerprints_compared':True,'roles':[{'node_id':n['node_id'],'name':'Role '+str(i),'storage':'disc' if disc and i==1 else 'node'} for i,n in enumerate((self.a,self.b))]});h=result['sessions'][-1]['id'];return h,self.setup.read()['sessions'][h]
    def contribute(self,h,row):
        for i,n in enumerate((self.a,self.b)):
            master=bip32.HDKey.from_seed(bytes([i+1])*32).derive('m/84h/0h/0h');account=master.to_public().to_base58();prefix='['+master.my_fingerprint.hex()+'/84h/0h/0h]'+account
            self.setup.accept(n['node_id'],'wallet_setup_contribution',self.sign(n,{'session_id':row['proposal']['session_id'],'proposal_hash':h,'cosigner_id':'signer-'+str(i+1),'receive_key':prefix+'/0/*','change_key':prefix+'/1/*'}))
        return self.setup.read()['sessions'][h]
    def ack(self,h,row,n,status,**extra):return self.sign(n,{'version':1,'session_id':row['proposal']['session_id'],'proposal_hash':h,'policy_hash':row['policy_hash'],'status':status,**extra})
    def test_identity_fresh_challenge_and_signed_producer_binding(self):
        with self.assertRaises(ValueError):self.setup.accept(self.a['node_id'],'wallet_setup_identity',self.setup.read()['identities'][self.b['node_id']])
        h,row=self.start();p={'session_id':row['proposal']['session_id'],'proposal_hash':h,'cosigner_id':'signer-1','receive_key':'bad','change_key':'bad'}
        with self.assertRaises(ValueError):self.setup.accept(self.a['node_id'],'wallet_setup_contribution',self.sign(self.b,p))
        self.assertEqual(self.setup.read()['sessions'][h]['contributions'],{})
    def test_policy_hash_complete_proofs_and_one_disc_proof_per_role(self):
        h,row=self.start(True,1000000);row=self.contribute(h,row);self.assertIn('and_v(v:after(1000000),multi(2,',row['descriptors'][0]);self.assertEqual(len(self.setup.snapshot()['sessions'][0]['samples']),6)
        for n in (self.a,self.b):self.setup.accept(n['node_id'],'wallet_setup_ack',self.ack(h,row,n,'policy_approved',backup='pending' if n==self.b else 'not_required'))
        self.assertEqual(self.setup.read()['sessions'][h]['state'],'policy')
        backup=self.ack(h,row,self.b,'backup_verified',cosigner_file=hashlib.sha256(b'signer-2').hexdigest(),image_sha256='a'*64);self.setup.accept(self.b['node_id'],'wallet_setup_ack',backup)
        state=self.setup.read();self.assertEqual(state['sessions'][h]['state'],'ready');complete=self.setup.proposals(self.a['node_id'],self.a['connection_token'])[0];self.assertEqual(complete['kind'],'wallet_setup_complete');self.assertEqual(len(complete['acknowledgements']),3);validate_archive(self.config,state,{self.user})
        import os,sys
        source=os.environ.get('WALLABY_AGENT_SOURCE')
        if source:
            path=self.folder/'agent-check.json';path.write_text(json.dumps({'row':state['sessions'][h],'completion':complete}));result=json.loads(subprocess.check_output([sys.executable,str(Path(__file__).with_name('agent_setup_runner.py')),source,str(path)]));self.assertTrue(result['completion_authenticated'])
        bad=copy.deepcopy(state);bad['sessions'][h]['acks'].pop(digest(backup))
        with self.assertRaises(ValueError):validate_archive(self.config,bad,{self.user})
    def test_changed_contribution_and_ack_policy_rejected(self):
        h,row=self.start();row=self.contribute(h,row);e=copy.deepcopy(row['contributions']['signer-1']);e['message']['change_key']=e['message']['receive_key'];e=self.sign(self.a,e['message'])
        with self.assertRaises(ValueError):self.setup.accept(self.a['node_id'],'wallet_setup_contribution',e)
        ack=self.ack(h,row,self.a,'policy_approved',backup='not_required');ack['message']['policy_hash']='f'*64;ack=self.sign(self.a,ack['message'])
        with self.assertRaises(ValueError):self.setup.accept(self.a['node_id'],'wallet_setup_ack',ack)
    def test_restart_and_reconnect_cannot_regenerate_pending_keys(self):
        h,row=self.start();self.setup.proposals(self.a['node_id'],'connection-one');self.assertFalse(self.setup.proposals(self.a['node_id'],'connection-two'));self.assertEqual(self.setup.read()['sessions'][h]['state'],'interrupted')
        other=WalletBuilder(self.hub);self.assertFalse(other.proposals(self.a['node_id'],'connection-two'))
    def test_outbox_round_trip_and_deduplication(self):
        h,row=self.start();item=self.item('wallet_setup_ack',self.sign(self.a,{'version':1,'session_id':row['proposal']['session_id'],'proposal_hash':h,'status':'participating'}));r=self.exchange(self.a,[item]);self.assertIn(item['id'],r['accepted']);self.assertIn(item['id'],self.exchange(self.a,[item])['accepted']);self.assertEqual(len(self.setup.read()['sessions'][h]['acks']),1)
    def test_private_material_and_unconfirmed_fingerprints_rejected(self):
        with self.assertRaises(ValueError):self.setup.start(self.user,{'name':'No','threshold':1,'height':None,'roles':[],'fingerprints_compared':False})


def multisig_fixture(height=None,threshold=2):
    private=[bip32.HDKey.from_seed(bytes([i+1])*32).key for i in range(3)];pubs=[k.get_public_key().sec().hex() for k in private]
    body='multi('+str(threshold)+','+','.join(pubs)+')';body='wsh('+('and_v(v:after('+str(height)+'),'+body+')' if height else body)+')';d=Descriptor.from_string(body);ws=d.witness_script().data
    prev=Transaction(vin=[TransactionInput(b'\0'*32,0xffffffff,script_sig=Script(b'\x01\x01'))],vout=[TransactionOutput(100000,d.script_pubkey())]);tx=Transaction(version=2,vin=[TransactionInput(prev.txid(),0,sequence=0xfffffffd)],vout=[TransactionOutput(99000,Descriptor.from_string('wpkh('+pubs[0]+')').script_pubkey())],locktime=height or 0)
    doc={'global':{b'\x00':tx.serialize()},'tx':tx,'inputs':[{b'\x00':prev.serialize(),b'\x01':prev.vout[0].serialize(),b'\x05':ws}],'outputs':[{}]};original=base64.b64encode(serialize(doc)).decode()
    def signed(i):
        row=copy.deepcopy(doc);pub=private[i].get_public_key().sec();row['inputs'][0][b'\x02'+pub]=private[i].sign(tx.sighash_segwit(0,Script(ws),100000)).serialize()+b'\x01';return serialize(row)
    return original,doc,signed

class Thresholds(unittest.TestCase):
    def test_each_signature_counts_once_and_final_transaction_needs_threshold(self):
        for height in (None,1000000):
            original,doc,sign=multisig_fixture(height);one,new=validate_partial(original,sign(0),[0]);self.assertEqual(new,1);self.assertFalse(progress(original,one,[0])['complete']);duplicate,new=validate_partial(original,sign(0),[0],one);self.assertEqual(new,0);self.assertEqual(duplicate,one)
            with self.assertRaises(ValueError):combine(original,{'wallet':{'proofs':one}},['wallet'])
            two,new=validate_partial(original,sign(1),[0],one);self.assertTrue(progress(original,two,[0])['complete']);encoded=combine(original,{'wallet':{'proofs':two}},['wallet']);final=final_transaction(encoded);self.assertEqual(final.locktime,height or 0);self.assertEqual(len(final.vin[0].witness.items),4)
    def test_changed_transaction_foreign_key_wrong_script_and_missing_timelock_rejected(self):
        original,doc,sign=multisig_fixture(1000000);bad=decode(sign(0));bad['tx'].locktime=0;bad['global'][b'\x00']=bad['tx'].serialize()
        with self.assertRaises(ValueError):validate_partial(original,serialize(bad),[0])
        bad=decode(sign(0));key=next(k for k in bad['inputs'][0] if k[:1]==b'\x02');bad['inputs'][0][key]=b'broken'
        with self.assertRaises(ValueError):validate_partial(original,serialize(bad),[0])
        with self.assertRaises(ValueError):witness_policy(b'\x51')
    def test_larger_multisig_witness_is_supported(self):
        # A final witness with >4 items must not hit the old single-key parser bound.
        from signed_psbt import witness_items
        from vendor.embit.script import Witness
        self.assertEqual(len(witness_items(Witness([b'']*17).serialize())),17)
    def test_separate_accounts_match_shared_policy_by_keys_not_names(self):
        accounts=[];keys=[]
        for i in range(3):
            pub=bip32.HDKey.from_seed(bytes([i+1])*32).to_public().to_base58();keys.append(pub+'/0/*');body='wpkh('+keys[-1]+')';accounts.append({'name':'misleading_same_name','raw':{'descriptors':[{'desc':body+'#'+checksum(body)}]},'private_keys_enabled':True})
        body='wsh(sortedmulti(2,'+','.join(keys)+'))';monitor={'raw':{'descriptors':[{'desc':body+'#'+checksum(body)}]},'private_keys_enabled':False}
        self.assertTrue(signing_account(accounts[0],monitor));self.assertEqual(len(cosigner_accounts(accounts+[monitor])),3)
        foreign=copy.deepcopy(accounts[0]);foreign['raw']['descriptors'][0]['desc']='wpkh('+bip32.HDKey.from_seed(b'foreign different seed').to_public().to_base58()+'/0/*)';self.assertFalse(signing_account(foreign,monitor))

class Signing(Setup):
    def install_shared(self):
        original,doc,sign=multisig_fixture();self.shared_original=original;self.sign=sign
        ws=doc['inputs'][0][b'\x05'];p=witness_policy(ws);body='wsh(multi(2,'+','.join(k.hex() for k in p['keys'])+'))';raw={'wallet_name':'Shared policy','descriptors':[{'desc':body+'#'+checksum(body)}]};w=clean_wallet(raw);DescriptorStore(self.config).replace_node_wallets([w]);self.rid=resource_id('descriptor',w['id']);inv=[{'id':self.rid,'kind':'descriptor','source':w['id'],'name':'Shared policy'}];self.family.save_ownership([{'id':self.rid,'name':'Shared policy','owner_id':self.user,'external':False,'smsf':False}],self.family.snapshot()['revision'],inv)
        for i,n in enumerate((self.a,self.b)):
            keybody='wpkh('+p['keys'][i].hex()+')';self.hub.inventories[n['node_id']]={'complete':True,'wallets':[{'name':'Monitor','raw':raw,'id':w['id'],'private_keys_enabled':False,'scanning':False},{'name':'Independent key '+str(i),'raw':{'wallet_name':'Key','descriptors':[{'desc':keybody+'#'+checksum(keybody)}]},'private_keys_enabled':True,'scanning':False}]};self.hub.caps[n['node_id']]={'partial_signing','wallet_transfer'}
        ident=self.job();from transaction_workflows import WorkflowStore
        store=WorkflowStore(self.config);data=store._read();row=data['jobs'][ident];row.update(psbt=original,input_wallets=[self.rid],wallets=[{'id':self.rid,'name':'Shared policy','owner_id':self.user,'owner':'Example','colour':'green'}]);store._write(data);return ident
    def test_distinct_cosigners_do_not_sync_and_both_are_signing_choices(self):
        ident=self.install_shared();self.exchange(self.a,caps=['partial_signing','wallet_transfer']);self.exchange(self.b,caps=['partial_signing','wallet_transfer']);self.assertFalse([r for r in self.hub.read()['requests'].values() if r['kind']=='wallet_transfer']);choices=eligible(self.hub,self.rid);self.assertEqual(len(choices),2);self.assertNotEqual(choices[0]['signer_id'],choices[1]['signer_id'])
    def test_two_agents_accumulate_signatures_and_notify_once(self):
        ident=self.install_shared();session=type('Session',(),{'user_id':self.user})();choices=eligible(self.hub,self.rid)
        for i,choice in enumerate(choices):
            queued=queue_sign(self.hub,session,ident,self.rid,choice['id'],choice['signer_id'])
            with self.hub.edit() as state:self.hub.accept_result(state,choice['id'],'partial_sign_result',{'request_hash':queued['request_hash'],'status':'signed','psbt':base64.b64encode(self.sign(i)).decode()})
            public=signing_state(self.hub,self.user,ident);self.assertEqual(public['all_signed'],i==1);self.assertEqual(public['signing_wallets'][0]['signature_progress']['received'],i+1)
        self.assertTrue(public['all_signed']);self.assertFalse(public['signing_wallets'][0]['nodes'])
        from transaction_workflows import WorkflowStore
        notices=WorkflowStore(self.config)._read()['notices'][self.user];self.assertEqual(len(notices),1)

class CardPolicy(unittest.TestCase):
    def test_tooltip_threshold_comes_from_policy_not_display_name(self):
        from policy_accounts import multisig_summary
        original,doc,sign=multisig_fixture(1000000)
        keys=witness_policy(doc['inputs'][0][b'\x05'])['keys'];body='wsh(and_v(v:after(1000000),multi(2,'+','.join(k.hex() for k in keys)+')))'
        self.assertEqual(multisig_summary({'wallet_name':'Plain wallet','descriptors':[{'desc':body+'#'+checksum(body)}]}),[{'required':2,'total':3}])
        self.assertEqual(multisig_summary({'wallet_name':'2 of 3 Wallet','descriptors':[{'desc':'wpkh('+keys[0].hex()+')'}]}),[])

class Visibility(Signing):
    def test_hover_counts_distinct_private_accounts_not_watch_or_duplicate_copies(self):
        from wallet_views import annotate_signers
        ident=self.install_shared();catalog=DescriptorStore(self.config).read();uid=catalog['wallets'][0]['id'];report={'balances':{'wallets':[{'descriptor_id':uid,'multisig':[{'required':2,'total':3}]}]}}
        self.hub.inventories[self.a['node_id']]['wallets'].append(copy.deepcopy(self.hub.inventories[self.a['node_id']]['wallets'][1]));annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],2)
        self.registry.connections.pop(self.b['connection_token']);annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],1)
        self.hub.inventories[self.a['node_id']]['wallets']=[w for w in self.hub.inventories[self.a['node_id']]['wallets'] if not w['private_keys_enabled']];annotate_signers(report,self.hub);self.assertEqual(report['balances']['wallets'][0]['multisig'][0]['visible'],0)

class SetupInventoryWindow(Ceremony):
    def test_new_account_before_monitor_is_not_synchronized(self):
        h,row=self.start();row=self.contribute(h,row);m=row['contributions']['signer-1']['message'];rows=[]
        for f in ('receive_key','change_key'):
            body='wpkh('+m[f]+')';rows.append({'desc':body+'#'+checksum(body),'range':[0,99],'next_index':1})
        self.hub.inventories[self.a['node_id']]={'complete':True,'wallets':[{'name':'First private account before monitor','id':'example-key','raw':{'wallet_name':'Private account','descriptors':rows},'private_keys_enabled':True}]};self.hub.inventories[self.b['node_id']]={'complete':True,'wallets':[]};self.exchange(self.a);self.assertFalse(self.hub.read()['requests'])
    def test_public_monitor_waits_until_every_producer_core_is_ready(self):
        h,row=self.start();row=self.contribute(h,row);raw={'wallet_name':'Pending policy','descriptors':[{'desc':d,'range':[0,99],'next_index':1} for d in row['descriptors']]};self.hub.inventories[self.a['node_id']]={'complete':True,'wallets':[{'name':'Pending monitor','id':'example','raw':raw,'private_keys_enabled':False}]};self.hub.inventories[self.b['node_id']]={'complete':True,'wallets':[]};self.exchange(self.a);self.assertFalse(self.hub.read()['requests'])

# Reuse ceremony helpers without discovering the parent test methods twice.
for _name in vars(Ceremony):
    if _name.startswith('test_'):setattr(SetupInventoryWindow,_name,None)
