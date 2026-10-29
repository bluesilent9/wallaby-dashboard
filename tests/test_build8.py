"""Build 8 protocol, cryptographic and readiness regressions; invented keys/coins only."""
import base64,copy,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from node_health import classify
from vault_monitor import countdown
from nodes import NodeRegistry
from agent_rpc import METHODS,request
from agent_hub import digest,equivalent,ipv6,AgentHub
from preferences import FamilyStore,resource_id
from descriptor_store import clean_wallet,DescriptorStore
from descriptor_policy import parse_policy
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from vendor.embit import bip32
from vendor.embit.transaction import Transaction,TransactionInput,TransactionOutput
from vendor.embit.script import Script
from vendor.embit.networks import NETWORKS
from signed_psbt import serialize,decode,validate_upload,final_transaction
from vault_records import public_vault
from transaction_workflows import WorkflowStore
from agent_transactions import eligible,queue_sign,queue_broadcast,signing_state,validate_export,prepare_export
from test_node_loading import public_wallet


def fixture():
    master=bip32.HDKey.from_seed(b'Build 8 invented test seed only!!');public=master.to_public().to_base58();body='wpkh('+public+'/0/*)';raw={'wallet_name':'source_keys','descriptors':[{'desc':body+'#'+checksum(body),'range':[0,99],'next_index':1,'active':True,'internal':False,'timestamp':1}]};wallet=clean_wallet(raw);policy=parse_policy(body)[0].derive(0);priv=master.derive('m/0/0').key;pub=priv.get_public_key().sec();height=1000000;vb='wsh(and_v(v:pk('+pub.hex()+'),after('+str(height)+')))';v={'version':1,'chain':'main','descriptor':vb+'#'+checksum(vb),'address':Descriptor.from_string(vb).script_pubkey().address(NETWORKS['main']),'height':height,'source':{'descriptor':raw['descriptors'][0]['desc'],'index':0},'timestamp':1700000000}
    previous=Transaction(vin=[TransactionInput(b'\0'*32,0xffffffff,script_sig=Script(b'\x01\x01'))],vout=[TransactionOutput(100000,policy.script_pubkey())]);tx=Transaction(version=2,vin=[TransactionInput(previous.txid(),0,sequence=0xfffffffd)],vout=[TransactionOutput(99000,Descriptor.from_string(vb).script_pubkey())])
    doc={'global':{b'\x00':tx.serialize()},'tx':tx,'inputs':[{b'\x00':previous.serialize(),b'\x01':previous.vout[0].serialize()}],'outputs':[{}]};unsigned=base64.b64encode(serialize(doc)).decode();ph=policy.script_pubkey().data[2:];script=Script(b'\x76\xa9\x14'+ph+b'\x88\xac');sig=priv.sign(tx.sighash_segwit(0,script,100000)).serialize()+b'\x01';doc['inputs'][0][b'\x02'+pub]=sig;signed=base64.b64encode(serialize(doc)).decode()
    history={'wallets':[{'id':wallet['id'],'name':wallet['name'],'records':[{'txid':previous.txid().hex(),'sats':100000,'output_index':0}]}],'transactions':{previous.txid().hex():{'inputs':[],'outputs':[{'value':100000,'script':policy.script_pubkey().data.hex(),'address':policy.script_pubkey().address(NETWORKS['main'])}]}},'wallet_outputs':{previous.txid().hex()+':0':wallet['id']}}
    return wallet,master,v,previous,tx,unsigned,signed,history

class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.config=Path(self.tmp.name)/'config.cfg';self.family=FamilyStore(self.config);self.family.save_user('Example','green',revision=self.family.snapshot()['revision']);self.user=self.family.snapshot()['users'][0]['id'];self.registry=NodeRegistry(self.config);self.registry.health.refresh=lambda _:None;self.hub=self.registry.hub
        self.a=self.register('02:80:00:00:00:01','first');self.b=self.register('02:80:00:00:00:02','second');self.wallet,self.master,self.vault,self.prev,self.tx,self.unsigned,self.signed,self.history=fixture();DescriptorStore(self.config).replace_node_wallets([self.wallet]);self.rid=resource_id('descriptor',self.wallet['id'])
        inv=[{'id':self.rid,'kind':'descriptor','source':self.wallet['id'],'name':'Example Wallet'}];self.family.save_ownership([{'id':self.rid,'name':'Example Wallet','owner_id':self.user,'external':False,'smsf':False}],self.family.snapshot()['revision'],inv)
        self.node_wallet={'name':self.wallet['name'],'raw':self.wallet['raw'],'id':self.wallet['id'],'private_keys_enabled':True,'scanning':False}
        self.hub.inventories[self.a['node_id']]={'wallets':[copy.deepcopy(self.node_wallet)],'complete':True};self.hub.inventories[self.b['node_id']]={'wallets':[dict(copy.deepcopy(self.node_wallet),name='different_watch_name',private_keys_enabled=False)],'complete':True}
    def register(self,mac,host):
        c=self.registry.register(mac,host,{'version':1,'methods':sorted(METHODS)},'2.0.0~dev8');self.registry.health.values[c['connection_token']]=classify({'chain':'main','blocks':999000,'headers':999000,'initialblockdownload':False});return c
    def exchange(self,node,items=None,known=None,caps=None,**extra):
        return self.hub.exchange(node['connection_token'],{'version':1,'outbox':items or [],'known_vaults':known or [],'capabilities':caps or [],**extra})
    def item(self,kind,payload):return {'id':digest({'kind':kind,'payload':payload}),'kind':kind,'payload':payload}
    def job(self):
        ident='a'*32;row={'id':ident,'creator_id':self.user,'participants':[self.user],'created_utc':self.hub.utc(),'cancelled':False,'agent_signing':True,'psbt':self.unsigned,'input_wallets':[self.rid],'wallets':[{'id':self.rid,'name':'Example Wallet','owner_id':self.user,'owner':'Example','colour':'green'}],'diagram':{'nodes':[],'edges':[{'from':self.rid,'to':'output','amount_sats':'99000','fee_rate':'0'}]},'outpoints':[[self.prev.txid().hex(),0]]}
        store=WorkflowStore(self.config);data=store._read();data['jobs'][ident]=row;store._write(data);return ident

class HealthTests(unittest.TestCase):
    def test_transport_and_sync_are_separate(self):
        for ibd,blocks,headers in ((True,90,100),(False,99,100),(True,100,100)):
            state=classify({'chain':'main','blocks':blocks,'headers':headers,'initialblockdownload':ibd});self.assertEqual(state['state'],'downloading');self.assertEqual(state['label'],'Downloading Blockchain')
        self.assertEqual(classify({'chain':'main','blocks':100,'headers':100})['state'],'ready')
    def test_countdown_two_significant_units(self):
        self.assertEqual(countdown(7*365*86400+2*30*86400+7*86400+4*3600+26),'7 Years, 2 Months');self.assertEqual(countdown(252),'4 Minutes, 12 Seconds');self.assertEqual(countdown(1),'1 Second')
    def test_remote_wallet_writes_and_signing_are_not_rpc_capabilities(self):
        for name in ('createwallet','importdescriptors','walletpassphrase','walletprocesspsbt','sendrawtransaction','setlabel','loadwallet'):
            self.assertNotIn(name,METHODS)
            with self.assertRaises(ValueError):request(name,[],None)
    def test_peer_addresses_are_ipv6_hints_not_loopback_tunnel_addresses(self):
        self.assertEqual(ipv6('fd12::1'),'fd12::1')
        for value in ('::1','127.0.0.1','fe80::1','::ffff:1.2.3.4','ff02::1','::'):
            with self.assertRaises(ValueError):ipv6(value)

class ExchangeTests(Setup):
    def test_legacy_agent_exchange_receives_vault_sync_without_new_unsupported_proposals(self):
        item=self.item('vault_public',self.vault);out=self.exchange(self.a,[item]);self.assertEqual(set(out),{'version','accepted','proposals'});self.assertEqual(out['accepted'],[item['id']]);other=self.exchange(self.b);self.assertIn({'kind':'vault_sync','vault':self.vault},other['proposals']);self.assertEqual(self.exchange(self.a,[item])['accepted'],[item['id']]);self.assertEqual(len(self.hub.read()['vaults']),1)
    def test_content_hash_invalid_policy_and_private_material_fail_without_acknowledgement(self):
        item=self.item('vault_public',self.vault);item['id']='0'*64
        with self.assertRaises(ValueError):self.exchange(self.a,[item])
        bad=copy.deepcopy(self.vault);bad['height']+=1
        with self.assertRaises(ValueError):self.exchange(self.a,[self.item('vault_public',bad)])
        self.assertFalse(self.hub.read()['vaults'])
    def test_missing_wallet_pairs_requests_and_never_confuses_private_and_watch_replicas(self):
        self.exchange(self.a,caps=['wallet_transfer','node_identity'],node_info={'ipv6':'fd12::1'});self.exchange(self.b,caps=['wallet_transfer','node_identity'],node_info={'ipv6':'fd12::2'});self.assertFalse(self.hub.read()['requests'])
        self.hub.inventories[self.b['node_id']]['wallets']=[];out=self.exchange(self.b,caps=['wallet_transfer'],node_info={'ipv6':'fd12::2'});receive=next(p for p in out['proposals'] if p['kind']=='wallet_sync_queue')['requests'][0];send=next(p for p in self.exchange(self.a,caps=['wallet_transfer'],node_info={'ipv6':'fd12::1'})['proposals'] if p['kind']=='wallet_sync_queue')['requests'][0];self.assertEqual(receive['direction'],'receive');self.assertEqual(send['direction'],'send');self.assertEqual(send['peer']['ipv6'],'fd12::2');self.assertEqual(receive['peer']['ipv6'],'fd12::1');self.assertEqual(send['transfer_id'],receive['transfer_id']);self.assertNotIn('xprv',json.dumps(out));self.assertEqual(len(self.hub.read()['requests']),2)
    def test_names_do_not_establish_wallet_identity_and_unloaded_inventory_cannot_create_requests(self):
        other=clean_wallet(public_wallet(self.wallet['name'],6));self.assertFalse(equivalent(self.wallet['raw'],other['raw']));self.hub.inventories[self.b['node_id']]={'complete':False,'wallets':[]};self.exchange(self.a);self.assertFalse(self.hub.read()['requests'])
    def test_address_requested_automatically_only_from_capable_agents(self):
        self.assertFalse(any(p['kind']!='wallet_sync_queue' for p in self.exchange(self.a)['proposals']));self.assertIn({'kind':'node_identity'},self.exchange(self.a,caps=['node_identity'])['proposals'])
    def test_pruned_missing_history_flag_uses_retained_chain_time(self):
        self.hub.inventories[self.b['node_id']]['wallets']=[];self.registry.health.values[self.b['connection_token']].update(pruned=True,prune_time=1800000000,pruneheight=990000);self.exchange(self.b);rows=self.hub.read()['requests'].values();self.assertTrue(all(r['requires_blockchain_redownload'] for r in rows))
    def test_unknown_result_cannot_turn_a_signing_card_green(self):
        with self.assertRaises(ValueError):self.exchange(self.a,[self.item('partial_sign_result',{'request_hash':'0'*64,'status':'signed','psbt':self.signed})])
    def test_legacy_broadcast_result_is_not_a_partial_signature(self):
        with self.assertRaisesRegex(ValueError,'Legacy signing'):self.exchange(self.a,[self.item('sign_result',{'request_hash':'0'*64,'txid':'f'*64})])

class SigningTests(Setup):
    def test_only_ready_private_replica_with_partial_capability_is_eligible(self):
        self.hub.caps={self.a['node_id']:{'partial_signing'},self.b['node_id']:{'partial_signing'}};self.assertEqual([n['id'] for n in eligible(self.hub,self.rid)],[self.a['node_id']]);self.registry.health.values[self.a['connection_token']]['state']='downloading';self.assertFalse(eligible(self.hub,self.rid))
    def test_signatures_validated_and_one_wallet_can_complete_through_agent(self):
        ident=self.job();self.hub.caps[self.a['node_id']]={'partial_signing','broadcast'};session=type('Session',(),{'user_id':self.user})();queued=queue_sign(self.hub,session,ident,self.rid,self.a['node_id']);result=self.item('partial_sign_result',{'request_hash':queued['request_hash'],'status':'signed','psbt':self.signed});out=self.exchange(self.a,[result],caps=['partial_signing','broadcast']);self.assertIn(result['id'],out['accepted']);view=signing_state(self.hub,self.user,ident);self.assertTrue(view['all_signed']);self.assertFalse(view['can_complete']);revision=view['signing_revision'];self.exchange(self.a,[result],caps=['partial_signing','broadcast']);self.assertEqual(signing_state(self.hub,self.user,ident)['signing_revision'],revision)
        queued=queue_broadcast(self.hub,session,ident,self.a['node_id']);proposal=self.hub.read()['requests'][queued['request_hash']]['proposal'];raw=final_transaction(proposal['psbt']).serialize().hex()
        result=self.item('broadcast_result',{'request_hash':queued['request_hash'],'status':'broadcast','txid':queued['txid']})
        with patch.object(self.hub,'rpc',return_value=raw):self.exchange(self.a,[result],caps=['broadcast'])
        self.assertTrue(signing_state(self.hub,self.user,ident)['can_complete'])
    def test_changed_transaction_and_wrong_node_results_fail(self):
        ident=self.job();self.hub.caps[self.a['node_id']]={'partial_signing'};session=type('Session',(),{'user_id':self.user})();queued=queue_sign(self.hub,session,ident,self.rid,self.a['node_id']);bad=decode(base64.b64decode(self.signed));bad['global'][b'\x00']=Transaction(vin=self.tx.vin,vout=[TransactionOutput(98000,self.tx.vout[0].script_pubkey)]).serialize();bad=base64.b64encode(serialize(bad)).decode()
        for node,psbt in ((self.a,bad),(self.b,self.signed)):
            with self.subTest(node=node['node_id']),self.assertRaises(ValueError):self.exchange(node,[self.item('partial_sign_result',{'request_hash':queued['request_hash'],'status':'signed','psbt':psbt})])
        self.assertFalse(WorkflowStore(self.config).public_job(self.user,ident)['all_signed'])
    def test_no_legacy_sign_or_broadcast_fallback_when_capability_missing(self):
        ident=self.job();session=type('Session',(),{'user_id':self.user})()
        with self.assertRaisesRegex(ValueError,'partial-signing'):queue_sign(self.hub,session,ident,self.rid,self.a['node_id'])
        with self.assertRaisesRegex(ValueError,'broadcast approval'):queue_broadcast(self.hub,session,ident,self.a['node_id'])

class VaultTests(Setup):
    def test_cards_green_locked_red_unlocked_amber_unsynchronised_or_unvaulted(self):
        self.exchange(self.a,[self.item('vault_public',self.vault)]);key=digest(self.vault);self.exchange(self.b,known=[key])
        body=self.vault['descriptor'];monitor={'name':'monitor','id':'monitor','raw':{'descriptors':[{'desc':body}]},'private_keys_enabled':False,'scanning':False}
        self.hub.inventories[self.a['node_id']]['wallets'].append(monitor);self.hub.inventories[self.b['node_id']]['wallets'].append(copy.deepcopy(monitor))
        def rpc(node,method,params=None,wallet=None):return [{'txid':'c'*64,'vout':0,'address':self.vault['address'],'amount':'0.01'}] if wallet=='monitor' else []
        with patch.object(self.hub,'rpc',side_effect=rpc):
            card=self.registry.vaults.compute()['wallets'][0];self.assertEqual(card['state'],'green');self.assertEqual(card['height'],self.vault['height']);self.assertEqual(card['name'],'Example Wallet')
            for c in self.registry.connections.values():self.registry.health.values[c['token']]['blocks']=self.vault['height']
            card=self.registry.vaults.compute()['wallets'][0];self.assertEqual(card['state'],'red');self.assertEqual(card['countdown'],'Unlocked')
            self.hub.inventories[self.b['node_id']]['wallets'].pop();self.exchange(self.b)
            card=self.registry.vaults.compute()['wallets'][0];self.assertEqual(card['state'],'amber');self.assertTrue(any('authorisation' in i for i in card['issues']))
    def test_immutable_export_needs_a_unique_open_profile_node_workspace(self):
        payload={'version':1,'chain':'main','psbt':self.unsigned,'transaction_hash':'f'*64,'vault':self.vault,'review':{},'stage':2,'immutable':True};item=self.item('transaction_builder_psbt',payload)
        self.assertEqual(self.exchange(self.a,[item])['accepted'],[])
        lease={'node_id':self.a['node_id'],'user_id':self.user,'draft_id':'diagram-test','expires':time.monotonic()+20};self.hub.leases['fixture']=lease
        class Chain:
            def __init__(s,*a):s.rpc=type('RPC',(),{'call':lambda *a:{'confirmations':1}})()
            def raw(s,ident):return self.prev
        with patch('agent_transactions.saved_history',return_value=self.history),patch('agent_transactions.NodeChain',Chain):out=self.exchange(self.a,[item])
        self.assertEqual(out['accepted'],[item['id']]);plan=next(iter(self.hub.read()['exports'].values()));self.assertTrue(plan['immutable']);self.assertEqual(plan['estimate']['total_fee_sats'],'1000');self.assertTrue(all(e['locked'] for e in plan['diagram']['edges']));self.assertTrue(self.hub.leases['fixture']['consumed']);self.assertEqual(self.exchange(self.a,[item])['accepted'],[item['id']])
    def test_public_vault_schema_rejects_height_or_key_substitution(self):
        self.assertEqual(public_vault(self.vault),self.vault);bad=copy.deepcopy(self.vault);bad['source']['index']=2
        with self.assertRaises(ValueError):public_vault(bad)

if __name__=='__main__':unittest.main()
