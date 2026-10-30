"""Agent-owned permissions and volatile signing aliases. Invented public accounts only."""
import base64,copy,json,os,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_build8 import Setup
from agent_hub import digest,AgentHub
from sync_permissions import policy_id,NAMESPACE
from setup_signatures import fingerprint
from node_wallets import NodeWalletLoader
from agent_transactions import eligible

class Permissions(Setup):
    def setUp(self):
        super().setUp();self.keys={}
        for n in (self.a,self.b):
            self.registry.connections[n['connection_token']]['agent_version']='2.0.0~dev10';key=Path(self.tmp.name)/n['node_id'];subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True);self.keys[n['node_id']]=key
    def pub(self,n):return ' '.join(self.keys[n['node_id']].with_suffix('.pub').read_text().split()[:2])
    def sign(self,n,m,namespace):
        path=Path(self.tmp.name)/'public-message';path.write_text(json.dumps(m,sort_keys=True,separators=(',',':'),ensure_ascii=True));path.with_suffix('.sig').unlink(missing_ok=True);subprocess.run(['ssh-keygen','-Y','sign','-f',str(self.keys[n['node_id']]),'-n',namespace,str(path)],check=True,capture_output=True);return {'message':m,'public_key':self.pub(n),'signature':base64.b64encode(path.with_suffix('.sig').read_bytes()).decode()}
    def identity(self,n):
        reply=self.exchange(n,caps=['wallet_transfer','partial_signing']);p=next(p for p in reply['proposals'] if p['kind']=='wallet_setup_identity');e=self.sign(n,{'version':1,'challenge':p['challenge'],'public_key':self.pub(n),'fingerprint':fingerprint(self.pub(n)),'hostname':'Example node'},'wallaby-wallet-setup-v1');reply=self.exchange(n,[self.item('wallet_setup_identity',e)],caps=['wallet_transfer','partial_signing']);return next(p for p in reply['proposals'] if p['kind']=='wallet_sync_preferences')
    def response(self,n,request,policies,revision=1):
        m={'version':1,'purpose':'wallet_sync_preferences','agent_key':self.pub(n),'revision':revision,'policies':policies};return {'version':1,'challenge':request['challenge'],'snapshot':self.sign(n,m,NAMESPACE)}
    def receive(self,n,p):return self.exchange(n,[self.item('wallet_sync_preferences',p)],caps=['wallet_transfer','partial_signing'])
    def poll_request(self,n):
        self.hub.permissions.live[n['node_id']]['checked']=0
        return next(p for p in self.exchange(n,caps=['wallet_transfer','partial_signing'])['proposals'] if p['kind']=='wallet_sync_preferences')
    def test_disable_reenable_is_agent_owned_no_persistent_dashboard_denial(self):
        req=self.identity(self.b);self.assertIsNone(req['previous']);self.hub.inventories[self.b['node_id']]['wallets']=[];self.receive(self.b,self.response(self.b,req,[policy_id(self.wallet['raw'])]));self.exchange(self.a)
        self.assertFalse(self.hub.read()['requests']);self.assertFalse(self.hub.sync_warnings());self.assertFalse(self.hub.permissions.permits(self.b['node_id'],self.wallet['raw']));self.assertEqual(self.hub.node_summary(self.b['node_id'])['sync_permissions']['disabled'][0]['wallet_name'],self.wallet['name'])
        self.exchange(self.a,[self.item('vault_public',self.vault)]);self.assertFalse(any(p['kind']=='vault_sync' for p in self.exchange(self.b)['proposals']))
        req=self.poll_request(self.b);self.assertIsNone(req['previous']);self.receive(self.b,self.response(self.b,req,[],2));self.exchange(self.a);self.assertEqual(len(self.hub.read()['requests']),2);self.assertTrue(self.hub.sync_warnings());self.assertTrue(self.hub.permissions.permits(self.b['node_id'],self.wallet['raw']))
        saved=self.hub.path.read_text();self.assertNotIn('sync_exclusions',saved);self.assertNotIn('"policies"',saved)
    def test_wait_for_current_permissions_before_offering_missing_wallet(self):
        self.hub.inventories[self.b['node_id']]['wallets']=[];self.exchange(self.a);self.assertFalse(self.hub.read()['requests']);self.assertFalse(self.hub.sync_warnings())
        p=self.identity(self.b);self.receive(self.b,self.response(self.b,p,[]));self.assertEqual(len(self.hub.read()['requests']),2)
    def test_disabling_withdraws_both_queued_rows_and_warning_without_closing_audit(self):
        p=self.identity(self.b);self.receive(self.b,self.response(self.b,p,[]));self.hub.inventories[self.b['node_id']]['wallets']=[];self.exchange(self.b,node_info={'ipv6':'fd12::2'},caps=['wallet_transfer']);self.exchange(self.a,node_info={'ipv6':'fd12::1'},caps=['wallet_transfer']);self.assertTrue(self.hub.sync_warnings())
        p=self.poll_request(self.b);self.receive(self.b,self.response(self.b,p,[policy_id(self.wallet['raw'])],2));self.assertFalse(self.hub.sync_warnings());self.assertTrue(all(not r['queue_visible'] for r in self.hub.read()['requests'].values()));self.assertTrue(all(r['state']=='pending' for r in self.hub.read()['requests'].values()))
    def test_signature_challenge_foreign_identity_revision_and_duplicate_bounds(self):
        p=self.identity(self.b);valid=self.response(self.b,p,[]);bad=copy.deepcopy(valid);bad['challenge']='e'*64
        with self.assertRaises(ValueError):self.receive(self.b,bad)
        bad=copy.deepcopy(valid);bad['snapshot']=self.sign(self.a,valid['snapshot']['message'],NAMESPACE)
        with self.assertRaises(ValueError):self.receive(self.b,bad)
        bad=copy.deepcopy(valid);bad['snapshot']=self.sign(self.b,valid['snapshot']['message'],'wallaby-wallet-setup-v1')
        with self.assertRaises(ValueError):self.receive(self.b,bad)
        self.receive(self.b,valid);p=self.poll_request(self.b)
        for policies,rev in (([],0),(['a'*64],1),(['a'*64,'a'*64],2)):
            with self.assertRaises(ValueError):self.receive(self.b,self.response(self.b,p,policies,rev))
    def test_connection_restart_reads_new_agent_list_never_restores_dashboard_copy(self):
        p=self.identity(self.b);self.receive(self.b,self.response(self.b,p,[policy_id(self.wallet['raw'])]));old=self.hub.permissions.live[self.b['node_id']];self.hub.permissions.live.clear();p=self.identity(self.b);self.assertIsNone(p['previous']);self.assertIsNone(self.hub.permissions.permits(self.b['node_id'],self.wallet['raw']));self.receive(self.b,self.response(self.b,p,[],0));self.assertTrue(self.hub.permissions.permits(self.b['node_id'],self.wallet['raw']))
    def test_repeated_current_queries_have_bounded_volatile_receipts(self):
        p=self.identity(self.b);count=len(self.hub.read()['receipts'])
        for i in range(35):
            if i:p=self.poll_request(self.b)
            response=self.response(self.b,p,[],i);self.receive(self.b,response);self.receive(self.b,response)
        self.assertEqual(len(self.hub.read()['receipts']),count);self.assertLessEqual(len(self.hub.permissions.live[self.b['node_id']]['receipts']),32)
    def test_old_outbox_after_restart_is_authenticated_and_discarded_not_restored(self):
        p=self.identity(self.b);old=self.response(self.b,p,[policy_id(self.wallet['raw'])],4);self.hub.permissions.live.clear()
        reply=self.receive(self.b,old);self.assertNotIn(self.item('wallet_sync_preferences',old)['id'],reply['accepted']);p=self.identity(self.b)
        reply=self.receive(self.b,old);self.assertIn(self.item('wallet_sync_preferences',old)['id'],reply['accepted']);self.assertIsNone(self.hub.permissions.permits(self.b['node_id'],self.wallet['raw']))
        self.receive(self.b,self.response(self.b,p,[],5));self.assertTrue(self.hub.permissions.permits(self.b['node_id'],self.wallet['raw']))
    def test_normalisation_matches_real_agent_and_names_are_not_policy_identity(self):
        renamed=copy.deepcopy(self.wallet['raw']);renamed['wallet_name']='Different name';self.assertEqual(policy_id(renamed),policy_id(self.wallet['raw']))
        source=os.environ.get('WALLABY_AGENT_SOURCE')
        if not source:self.skipTest('Set WALLABY_AGENT_SOURCE for independent Agent validation.')
        p=self.identity(self.b);file=Path(self.tmp.name)/'interop.json';file.write_text(json.dumps({'request':p,'wallet':self.wallet['raw'],'identity':str(self.keys[self.b['node_id']])}));result=json.loads(subprocess.check_output([__import__('sys').executable,str(Path(__file__).with_name('agent_permissions_runner.py')),source,str(file)]));self.assertEqual(result['policy_id'],policy_id(self.wallet['raw']));self.receive(self.b,result['payload'])

class DiscAliases(Setup):
    def test_inventory_accepts_actual_agent_alias_without_trusting_names_as_policy(self):
        raw=copy.deepcopy(self.wallet['raw']);raw['wallet_name']='temporary-local-account'
        def rpc(node,method,params=None,wallet=None):
            if method=='listwallets':return ['Public routing alias']
            if method=='listwalletdir':return {'wallets':[{'name':'Public routing alias'}]}
            if method=='getwalletinfo':return {'walletname':'temporary-local-account','private_keys_enabled':True,'scanning':False}
            if method=='listdescriptors':return raw
            raise AssertionError('Signing inventory must not request history: '+method)
        self.hub.rpc=rpc;self.hub.refresh_inventory(self.a['node_id'],self.a['connection_token']);inventory=self.hub.inventories[self.a['node_id']];self.assertTrue(inventory['complete']);self.assertTrue(inventory['wallets'][0]['signing_only']);self.assertEqual(inventory['wallets'][0]['name'],'Public routing alias');self.hub.caps[self.a['node_id']]={'partial_signing'};self.assertEqual(eligible(self.hub,self.rid)[0]['wallet'],'Public routing alias')
        self.hub.inventories[self.b['node_id']]['wallets']=[];self.exchange(self.a);self.assertFalse(self.hub.read()['requests'])
    def test_node_history_skips_alias_before_listsinceblock(self):
        loader=NodeWalletLoader(self.registry,lambda _:None);raw=copy.deepcopy(self.wallet['raw']);raw['wallet_name']='local-account'
        calls=[]
        def rpc(node,method,params=None,wallet=None):
            calls.append(method)
            if method=='getwalletinfo':return {'walletname':'local-account','private_keys_enabled':True}
            if method=='listdescriptors':return raw
            raise AssertionError('No history is available in the offline signing Core.')
        loader.rpc=rpc;self.assertEqual(loader.export(({'id':self.a['node_id'],'hostname':'example'},'Public alias')),[]);self.assertNotIn('listsinceblock',calls)

class PrivateKeyFilter(unittest.TestCase):
    def test_wif_requires_a_real_checksum_and_signatures_are_not_keys(self):
        from agent_rpc import PRIVATE,clean_json
        alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
        for version in (0x80,0xef):
            for compressed in (False,True):
                body=bytes([version])+bytes([1])*32+(b'\x01' if compressed else b'')
                import hashlib
                raw=body+hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4];n=int.from_bytes(raw,'big');encoded=''
                while n:n,r=divmod(n,58);encoded=alphabet[r]+encoded
                self.assertIsNotNone(PRIVATE.search(encoded))
                with self.assertRaises(ValueError):clean_json({'key':encoded},1000)
        self.assertIsNone(PRIVATE.search('L'+'a'*51))
        self.assertIsNone(PRIVATE.search('l'+'a'*51))
        self.assertIsNone(PRIVATE.search('xprv'+'a'*100))
        from vendor.embit import bip32
        from vendor.embit.networks import NETWORKS
        for version in (NETWORKS['main']['xprv'],NETWORKS['test']['xprv']):
            actual=bip32.HDKey.from_seed(b'fictional private filter fixture',version=version).to_base58()
            self.assertIsNotNone(PRIVATE.search(actual))
            with self.assertRaises(ValueError):clean_json({'key':actual},1000)
