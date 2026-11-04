"""Hash-sorted historical setups must not attach to a new wallet workspace."""
import copy,hashlib,json,time
from unittest.mock import patch
import test_build11 as fixtures
from builder_workspace import BuilderWorkspace
from agent_hub import digest
from setup_wallet_views import SetupWalletViews
from descriptor_store import clean_wallet
from vendor.embit.descriptor import Descriptor
from vendor.embit.networks import NETWORKS

class ExactWorkspace(fixtures.Ceremony):
    def data(self):
        return {'name':'Example shared wallet','threshold':2,'height':1000000,'fingerprints_compared':True,
                'roles':[{'node_id':n['node_id'],'name':'Role '+str(i),'storage':'node'} for i,n in enumerate((self.a,self.b))]}
    def two_setups(self):
        ws=BuilderWorkspace(self.config);old=ws.start(self.user,'wallet')
        with patch('wallet_builder.time.time',return_value=1800000000):
            first=self.setup.start(self.user,self.data(),old['id']);oldh=first['created_id']
            with self.setup.edit() as state:state['sessions'][oldh]['state']='cancelled'
            ws.finish(self.user,'wallet');new=ws.start(self.user,'wallet')
            proposal=copy.deepcopy(first['sessions'][0]['proposal'])
            for i in range(10000):
                nonce=hashlib.sha256(str(i).encode()).hexdigest();proposal['session_id']=nonce
                if digest(proposal)<oldh:break
            else:self.fail('Could not construct lower-sorted fixture hash')
            with patch('wallet_builder.secrets.token_hex',return_value=nonce):
                second=self.setup.start(self.user,self.data(),new['id'])
        return ws,oldh,second['created_id'],new
    def test_new_setup_returns_exact_id_when_old_hash_sorts_last(self):
        ws,oldh,newh,new=self.two_setups()
        self.assertNotEqual(oldh,newh);self.assertEqual(next(reversed(self.setup.read()['sessions'])),oldh)
        ws.attach(self.user,'wallet',newh)
        self.assertEqual(ws.owned(self.user,'wallet')['operations'],[newh])
    def test_mislinked_old_setup_is_excluded_from_display_and_cancellation(self):
        ws,oldh,newh,new=self.two_setups()
        state=ws.read();state['workspaces']['wallet']['operations']=[oldh,newh];ws.write(state)
        with self.setup.edit() as state:state['sessions'][newh]['delivered_nodes']=[self.a['node_id']]
        before=self.setup.path.read_bytes()
        self.assertEqual(ws.snapshot(self.user,'wallet')['workspace']['operations'],[newh])
        self.assertEqual(before,self.setup.path.read_bytes(),'Reading repaired linkage cannot rewrite signed setup evidence')
        result=ws.cancel(self.hub,self.user,'wallet',True)
        batch=ws.read()['cancellations'][result['cancellation']['id']]
        encoded=json.dumps(batch)
        self.assertIn(newh,encoded);self.assertNotIn(oldh,encoded)
        self.assertEqual(self.setup.read()['sessions'][oldh]['state'],'cancelled')
    def test_http_create_attaches_new_hash_not_last_sorted_history(self):
        import http.client,threading
        from launch_wallaby_bitcoin_dashboard import create_server
        ws,oldh,newh,new=self.two_setups()
        with self.setup.edit() as state:state['sessions'][newh]['state']='cancelled'
        ws.finish(self.user,'wallet');w=ws.start(self.user,'wallet')
        server,pool=create_server(self.folder/'http-cache',0,self.config,ipv6=False)
        pool.discovery.close();pool.nodes.health.closed=True;pool.nodes=self.registry
        session=pool.create();session.user_id=self.user
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        proposal=copy.deepcopy(self.setup.read()['sessions'][oldh]['proposal'])
        for i in range(10000,20000):
            nonce=hashlib.sha256(str(i).encode()).hexdigest();proposal['session_id']=nonce
            if digest(proposal)<oldh and digest(proposal)!=newh:break
        try:
            with patch('wallet_builder.time.time',return_value=1800000000),patch('wallet_builder.secrets.token_hex',return_value=nonce):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                conn.request('POST','/api/wallet-builder/create',json.dumps(self.data()),{'X-Session-Token':session.token,'Content-Type':'application/json'})
                response=conn.getresponse();result=json.loads(response.read());conn.close()
            self.assertEqual(response.status,200,result);created=result['created_id']
            self.assertLess(created,oldh);self.assertNotEqual(created,newh)
            self.assertEqual(ws.read()['workspaces']['wallet']['operations'],[created])
            self.assertEqual(self.setup.read()['sessions'][created]['workspace_id'],w['id'])
        finally:server.shutdown();server.server_close();thread.join(3)

    def test_progress_diagnostics_are_change_only_and_public(self):
        ws=BuilderWorkspace(self.config);w=ws.start(self.user,'wallet')
        with patch.object(self.registry.diagnostic_log,'append') as log:
            result=self.setup.start(self.user,self.data(),w['id'])
            once=log.call_count
            self.setup.snapshot();self.setup.snapshot()
            self.assertEqual(log.call_count,once)
            h=result['created_id']
            with self.setup.edit() as state:state['sessions'][h]['state']='interrupted'
            self.setup.snapshot();self.assertEqual(log.call_count,once+1)
            for call in log.call_args_list:
                line=call.args[1];self.assertNotIn('xpub',line);self.assertNotIn('descriptor',line);self.assertNotIn('passphrase',line)
    def test_combined_key_and_policy_inventory_confirms_and_remains_visible(self):
        h,row=self.start(height=1000000);row=self.contribute(h,row)
        for node in (self.a,self.b):
            name='Example shared wallet - Role '+node['node_id'][:4]
            message={'version':1,'session_id':row['proposal']['session_id'],'policy_hash':row['policy_hash'],'chain':'main',
                     'monitor_wallet':name,'signing_wallets':{'signer-1' if node==self.a else 'signer-2':name},
                     'receive_descriptor':row['descriptors'][0],'change_descriptor':row['descriptors'][1]}
            self.setup.accept(node['node_id'],'wallet_setup_inventory',self.sign(node,message))
            self.setup.accept(node['node_id'],'wallet_setup_ack',self.ack(h,row,node,'policy_approved',backup='not_required'))
        self.assertEqual(self.setup.read()['sessions'][h]['state'],'ready')
        for node in (self.a,self.b):
            name=self.setup.read()['sessions'][h]['inventories'][node['node_id']]['message']['monitor_wallet']
            raw={'wallet_name':name,'descriptors':[{'desc':d,'range':[0,999],'active':True,'internal':bool(i),'timestamp':1} for i,d in enumerate(row['descriptors'])]}
            wallet={'name':name,'private_keys_enabled':True,'signing_only':False,'raw':raw}
            with patch.object(self.hub,'rpc',return_value={'labels':['Wallaby setup ready:'+row['policy_hash']]}):
                self.setup.confirm_installed(node['node_id'],node['connection_token'],[wallet])
            self.assertFalse(__import__('policy_accounts').intentional_account(raw,self.setup.read()['sessions'].values(),[]))
        self.assertEqual(set(self.setup.read()['sessions'][h]['completion_confirmed']),{self.a['node_id'],self.b['node_id']})
        self.assertFalse(SetupWalletViews(self.config).hidden(raw))

for name in vars(fixtures.Ceremony):
    if name.startswith('test_'):setattr(ExactWorkspace,name,None)
