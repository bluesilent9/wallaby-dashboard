import base64,copy,unittest
from unittest.mock import patch
from test_build8 import Setup
from agent_transactions import validate_export
from vault_composition import combine_unsigned,context,prepare
from signed_psbt import decode,serialize,vault_witness_policy,validate_upload,final_transaction
from vendor.embit.transaction import Transaction,TransactionInput,TransactionOutput
from vendor.embit.script import Script
from vendor.embit.descriptor import Descriptor
from fee_planner import known_scripts,plan

class CompositionTests(Setup):
    def proposal(self):
        payload={'psbt':self.unsigned,'vault':self.vault,'transaction_hash':'f'*64}
        class Chain:
            def __init__(s,*a):s.rpc=type('RPC',(),{'call':lambda *a:{'confirmations':1}})()
            def raw(s,ident):return self.prev
        with patch('agent_transactions.saved_history',return_value=self.history),patch('agent_transactions.NodeChain',Chain):value=validate_export(self.hub,payload,{'node_id':self.a['node_id'],'user_id':self.user})
        value.update(user_id=self.user,node_id=self.a['node_id'],draft_id='diagram-test')
        with self.hub.edit() as data:data['exports']['e'*64]=value
        return value
    def session(self):return type('Session',(),{'user_id':self.user,'preferences_path':self.config,'node_registry':self.registry,'family':lambda s:self.family.snapshot(),'access':lambda s:{'templates_ready':True}})()
    def test_fixed_proposal_unsigned_until_signing_and_no_component_changes(self):
        value=self.proposal();from agent_state import validate;validate(self.hub.read(),{self.user});self.assertTrue(value['unsigned']);self.assertFalse(value['signed_proofs']);self.assertEqual(combine_unsigned(value['psbt'],None)[2],1000)
        with patch('transaction_builder.builder_catalog',return_value={'wallets':value['wallets']}),patch.object(self.hub,'rpc',return_value={'confirmations':1}):
            row=prepare(self.hub,self.session(),'e'*64);self.assertFalse(row['all_signed']);self.assertTrue(row['agent_signing']);self.assertEqual(row['psbt'],value['psbt']);self.assertEqual(row['vault_intents'][0]['vault'],self.vault)
            diagram=copy.deepcopy(value['diagram']);diagram['edges'][0]['amount_sats']='98000'
            with self.assertRaisesRegex(ValueError,'immutable'):context(self.hub,self.session(),'e'*64,diagram)
            diagram=copy.deepcopy(value['diagram']);diagram['edges'].pop()
            with self.assertRaisesRegex(ValueError,'remain'):context(self.hub,self.session(),'e'*64,diagram)
    def test_composition_preserves_vault_outputs_locktime_metadata_and_rejects_duplicates_or_signatures(self):
        fixed=decode(base64.b64decode(self.unsigned));fixed['tx'].locktime=100;fixed['global'][b'\x00']=fixed['tx'].serialize();fixed['inputs'][0][b'\xfcintent']=b'public evidence';a=base64.b64encode(serialize(fixed)).decode()
        extra=decode(base64.b64decode(self.unsigned));extra['tx'].vin[0].txid=b'\xaa'*32
        previous=copy.deepcopy(self.prev);previous.vin[0].script_sig=Script(b'\x01\x02');extra['tx'].vin[0].txid=previous.txid();extra['inputs'][0][b'\x00']=previous.serialize();extra['global'][b'\x00']=extra['tx'].serialize();b=base64.b64encode(serialize(extra)).decode()
        encoded,tx,fee=combine_unsigned(a,b);doc=decode(base64.b64decode(encoded));self.assertEqual(tx.locktime,100);self.assertEqual(fee,2000);self.assertEqual(tx.vout[0].serialize(),self.tx.vout[0].serialize());self.assertEqual(doc['inputs'][0][b'\xfcintent'],b'public evidence');self.assertEqual(len(tx.vin),2)
        with self.assertRaisesRegex(ValueError,'also fund'):combine_unsigned(self.unsigned,self.unsigned)
        with self.assertRaisesRegex(ValueError,'unsigned'):combine_unsigned(self.signed,None)
    def test_size_estimate_accounts_for_both_components(self):
        value=self.proposal();coin={'txid':'a'*64,'vout':0,'value':100000,'weight':273,'witness':True};catalog={'wallets':[{'id':'x','name':'Other','can_send':True,'address':self.prev.vout[0].script_pubkey.address()},{'id':'y','name':'Receive','can_receive':True,'address':self.tx.vout[0].script_pubkey.address()}]};edges=[{'from':'x','to':'y','amount_sats':'50000','fee_rate':'0'},{'from':'x','to':'network-fee','amount_sats':'0','fee_rate':'1'}];normal=plan(edges,catalog,{'x':[coin]});combined=plan(edges,catalog,{'x':[coin]},fixed={'inputs':value['input_sizes'],'outputs':[o.script_pubkey.data for o in self.tx.vout]});self.assertGreater(combined['vbytes'],normal['vbytes']);self.assertEqual(int(combined['total_fee_sats']),combined['vbytes']);edges.pop();covered=plan(edges,catalog,{'x':[coin]},fixed={'inputs':value['input_sizes'],'outputs':[o.script_pubkey.data for o in self.tx.vout],'payments':{self.tx.vout[0].script_pubkey.address():99000},'fee_sats':1000});self.assertTrue(covered['can_continue']);self.assertEqual(covered['total_fee_sats'],'0')
    def test_exact_outpoints_ignore_joint_input_overclaim_and_c1_economic_rows(self):
        chain=copy.deepcopy(self.history);chain['wallets'].append({'id':'other','records':[{'txid':'C1:lost','sats':-10}]});self.assertEqual(known_scripts(chain)['other'],set())
    def test_vault_input_signature_requires_exact_height_policy_and_final_transaction(self):
        pub=self.master.derive('m/0/0').key.get_public_key();body='wsh(and_v(v:pk('+pub.sec().hex()+'),after(100)))';d=Descriptor.from_string(body);script=d.witness_script();prev=Transaction(vin=self.prev.vin,vout=[TransactionOutput(100000,d.script_pubkey())]);tx=Transaction(version=2,vin=[TransactionInput(prev.txid(),0,sequence=0xfffffffd)],vout=self.tx.vout,locktime=100);doc={'global':{b'\x00':tx.serialize()},'tx':tx,'inputs':[{b'\x00':prev.serialize(),b'\x01':prev.vout[0].serialize(),b'\x05':script.data}],'outputs':[{}]};original=base64.b64encode(serialize(doc)).decode();sig=self.master.derive('m/0/0').key.sign(tx.sighash_segwit(0,script,100000)).serialize()+b'\x01';doc['inputs'][0][b'\x02'+pub.sec()]=sig;signed=base64.b64encode(serialize(doc)).decode();self.assertEqual(vault_witness_policy(script.data)[1],100);self.assertTrue(validate_upload(original,signed.encode(),[0]));self.assertEqual(final_transaction(signed).txid(),tx.txid())
        doc['tx'].locktime=99;doc['global'][b'\x00']=doc['tx'].serialize()
        with self.assertRaisesRegex(ValueError,'height lock'):final_transaction(base64.b64encode(serialize(doc)).decode())

class ExchangeHTTPTests(Setup):
    def test_authenticated_exchange_duplicate_fields_origin_and_old_response_shape(self):
        import http.client,json
        from nodes import create_agent_server
        server=create_agent_server(self.registry,0,ipv6=False);server.start();self.addCleanup(server.close)
        def send(body,auth=True,origin=False):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);headers={'X-Wallaby-Agent':'1','Content-Type':'application/json'}
            if auth:headers['Authorization']='Bearer '+self.a['connection_token']
            if origin:headers['Origin']='http://example.invalid'
            c.request('POST','/agent/v2/exchange',body,headers);r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result
        body=json.dumps({'version':1,'outbox':[],'known_vaults':[]})
        self.assertEqual(send(body,auth=False)[0],401);self.assertEqual(send(body,origin=True)[0],403)
        self.assertEqual(send('{"version":1,"version":1,"outbox":[],"known_vaults":[]}')[0],400)
        status,response=send(body);self.assertEqual(status,200);self.assertEqual(set(response),{'version','accepted','proposals'})
    def test_profile_archive_public_state_rejects_signature_or_owner_substitution(self):
        from agent_state import validate
        value=self.hub.read();self.assertEqual(validate(value,{self.user}),value)
        value['exports']['x']={'user_id':'not-a-user'}
        with self.assertRaisesRegex(ValueError,'unknown profile'):validate(value,{self.user})
