"""Current Agent queues, independent archive ownership and wallet retirement boundary."""
import base64,copy,json,os,subprocess,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
import test_build9 as previous
from test_build8 import Setup
import test_build7 as historical
from test_build7 import addresses,fixed
from test_node_loading import public_wallet
from descriptor_store import clean_wallet,DescriptorStore
from preferences import resource_id,FamilyStore
from agent_hub import AgentHub,digest
from wallet_preview import validate
from wallet_views import _history,extinct_cards
from descriptor_snapshot import shared_history

# Reuse setup/signing helpers without discovering the inherited tests twice.
class Queues(Setup):
    setUp=previous.PreviewTests.setUp
    signed_preview=previous.PreviewTests.signed_preview
    send=previous.PreviewTests.send
    result=previous.PreviewTests.result
    def queue(self,node):return next(p for p in self.exchange(node,caps=['wallet_transfer'])['proposals'] if p['kind']=='wallet_sync_queue')
    def test_all_wallet_rows_delivered_and_same_snapshot_survives_restart(self):
        for index in range(2,5):
            w=clean_wallet(public_wallet('Wallet '+str(index),index));self.hub.inventories[self.a['node_id']]['wallets'].append({'id':w['id'],'raw':w['raw'],'name':w['name'],'private_keys_enabled':True,'scanning':False})
        before=self.queue(self.a);self.assertEqual(len(before['requests']),4);after=self.queue(self.a);self.assertEqual(before,after)
        self.assertFalse(self.hub.transfers.is_busy(self.a['node_id']))
        old=self.hub;self.hub=AgentHub(self.registry);self.registry.hub=self.hub;self.hub.caps=old.caps;self.hub.inventories=old.inventories;self.hub.refresh_inventory=lambda *a:None
        self.assertEqual(before,self.queue(self.a));self.assertEqual({r['transfer_version'] for r in before['requests']},{2})
    def test_source_disconnect_withdraws_and_reconnect_reuses_unmodified_request(self):
        before=self.queue(self.b);connection=self.registry.connections.pop(self.a['connection_token'])
        withdrawn=self.queue(self.b);self.assertEqual(withdrawn['requests'],[]);self.assertGreater(withdrawn['revision'],before['revision'])
        self.registry.connections[self.a['connection_token']]=connection
        again=self.queue(self.b);self.assertGreater(again['revision'],withdrawn['revision']);self.assertEqual(again['requests'],before['requests'])
    def test_queue_first_then_every_preview_and_real_agent_revision_rules(self):
        payload=self.signed_preview();self.send(payload);response=self.exchange(self.b,caps=['wallet_transfer']);self.assertEqual(response['proposals'][0]['kind'],'wallet_sync_queue');self.assertEqual(response['proposals'][1],{'kind':'wallet_transfer_preview',**{k:payload[k] for k in ('preview','public_key','signature')}})
        source=os.environ.get('WALLABY_AGENT_SOURCE')
        if not source:self.skipTest('Set WALLABY_AGENT_SOURCE for current Agent interoperability.')
        file=Path(self.tmp.name)/'queue.json';file.write_text(json.dumps({'proposal':self.target,'snapshot':response['proposals'][0],'payload':{k:payload[k] for k in ('preview','public_key','signature')}}))
        runner=Path(__file__).with_name('agent_queue_runner.py');result=json.loads(subprocess.check_output([sys.executable,str(runner),source,str(file)]));self.assertTrue(result['stable']);self.assertTrue(result['stale_rejected']);self.assertEqual(result['count'],1);self.assertEqual(result['withdrawn'],0)
    def test_recovery_is_not_success_and_receiver_completion_withdraws_both(self):
        self.result(self.a,self.source,'recovery_required');self.result(self.b,self.target,'recovery_required');self.assertEqual(len(self.queue(self.a)['requests']),1);self.assertEqual(len(self.queue(self.b)['requests']),1)
        self.assertTrue(any('history recovery' in w['text'] for w in self.hub.sync_warnings()))
        with self.assertRaises(ValueError):self.hub.transfers.retry(self.source['transfer_id'],True)
        self.result(self.b,self.target,'complete');self.assertFalse(self.queue(self.a)['requests']);self.assertFalse(self.queue(self.b)['requests']);self.assertFalse(self.hub.sync_warnings());self.assertEqual(self.hub.transfers.summaries(self.hub.read(),self.a['node_id'])[0]['state'],'complete')
        self.exchange(self.b,caps=['wallet_transfer']);self.assertEqual(len(self.hub.read()['requests']),2)
    def test_wrong_peer_unrequested_results_rejected(self):
        with self.assertRaises(ValueError):self.result(self.b,self.source,'recovery_required')
        with self.assertRaises(ValueError):self.result(self.a,dict(self.source,transfer_id='d'*64),'complete')
    def test_expired_preview_is_withdrawn_without_a_new_automatic_challenge(self):
        self.send(self.signed_preview());before=self.queue(self.b)
        with patch('wallet_sync_queue.time.time',return_value=time.time()+1000):self.assertEqual(self.queue(self.b)['requests'],[])
        rows=list(self.hub.read()['requests'].values());self.assertEqual({r['challenge'] for r in rows},{before['requests'][0]['preview_challenge']})
    def test_zero_balance_fixed_address_metadata_is_preserved_and_checked(self):
        a,b,_=addresses();preview=copy.deepcopy(self.preview);preview['public_wallet']={'wallet_name':'Old Group','descriptors':[fixed(a),fixed(b)]};preview['funded_outputs']=[];preview['address_labels']=[{'address':a,'label':'Old Group','is_change':False},{'address':b,'label':'Old Group | LOST:02/01/2020 00:00:00 UTC','is_change':True}]
        payload=self.signed_preview(preview);validate({k:payload[k] for k in ('preview','public_key','signature')});self.assertEqual(payload['preview']['address_labels'][1]['label'],preview['address_labels'][1]['label'])
        preview['address_labels'].append(preview['address_labels'][0]);bad=self.signed_preview(preview)
        with self.assertRaisesRegex(ValueError,'address label'):validate({k:bad[k] for k in ('preview','public_key','signature')})
    def test_missing_archive_subset_uses_complete_policy_coverage(self):
        a,b,_=addresses();one={'wallet_name':'A','descriptors':[fixed(a)]};both={'wallet_name':'B','descriptors':[fixed(a),fixed(b)]}
        self.assertFalse(self.hub.transfers.contains([{'raw':one}],both));self.assertTrue(self.hub.transfers.contains([{'raw':both}],one))
    def test_old_agent_version_rejected_instead_of_legacy_dispatch(self):
        with self.assertRaisesRegex(ValueError,'Update Wallaby Agent'):self.hub.exchange(self.a['connection_token'],{'version':1,'outbox':[],'known_vaults':[]},'2.0.0~dev7')

class ArchiveCards(unittest.TestCase):
    replica=historical.HistoricalGroups.replica
    # Additional grouping/owner regression uses the already covered physical archive.
    def test_same_core_parent_has_independent_ownership(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.cfg';family=FamilyStore(config)
            for name in ('Owner A','Owner B'):family.save_user(name,'green',revision=family.snapshot()['revision'])
            a,b,_=addresses();children=__import__('labelled_wallets').expand(self.replica(),lambda address:{'labels':['Old A' if address==a else 'Old B']});wallets=[c['wallet'] for c in children];DescriptorStore(config).replace_node_wallets(wallets)
            users=family.snapshot()['users'];inv=[{'id':resource_id('descriptor',w['id']),'kind':'descriptor','source':w['id'],'name':w['name']} for w in wallets]
            family.save_ownership([{'id':r['id'],'name':r['name'],'owner_id':u['id'],'external':False,'smsf':False} for r,u in zip(inv,users)],family.snapshot()['revision'],inv)
            result=extinct_cards(config);self.assertEqual([r['owner_name'] for r in result],['Owner A','Owner B']);self.assertNotIn('arbitrary_archive_name',[r['name'] for r in result])
    def test_activity_years_use_real_confirmed_transactions_not_loss_declarations(self):
        import tempfile,hashlib
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'history.json';snapshot={'catalog_fingerprint':'example','transactions':{'a':{},'b':{}},'wallets':[{'id':'old','records':[{'txid':'a','utc':'2022-11-28T00:00:00Z'},{'txid':'b','utc':'2024-04-05T00:00:00Z'},{'txid':'LOSS:test','utc':'2025-01-01T00:00:00Z'}]},{'id':'year','records':[{'txid':'a','utc':'2023-02-03T00:00:00Z'},{'txid':'b','utc':'2023-12-14T00:00:00Z'}]}]};payload=json.dumps(snapshot);p.write_text(json.dumps({'payload':payload,'sha256':hashlib.sha256(payload.encode()).hexdigest()}));stat=p.stat();result=_history(str(p),stat.st_mtime_ns,stat.st_size,'example');self.assertEqual(result,{'old':'2022 - 2024','year':'2023'})

class Retirement(Setup):
    def test_current_agent_cannot_receive_unsupported_retirement(self):
        from wallet_retirement import propose
        session=type('Session',(),{'user_id':self.user,'user':lambda s:{'id':self.user},'access':lambda s:{'ownership_ready':True}})()
        with self.assertRaisesRegex(ValueError,'Update the Agent'):propose(self.hub,session,self.rid,self.a['node_id'],True)
        self.assertFalse(self.hub.read()['requests'])
    def test_old_archive_only_agent_cannot_receive_deletion_request(self):
        from wallet_retirement import propose
        self.hub.caps[self.a['node_id']]={'wallet_retirement'}
        session=type('Session',(),{'user_id':self.user,'user':lambda s:{'id':self.user},'access':lambda s:{'ownership_ready':True}})()
        with self.assertRaisesRegex(ValueError,'Update the Agent'):propose(self.hub,session,self.rid,self.a['node_id'],True)
        self.assertFalse(self.hub.read()['requests'])
