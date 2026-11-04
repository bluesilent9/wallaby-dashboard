"""UI reads cannot take writer locks; explicit setup work preempts idle requests."""
import copy,threading,time
from unittest.mock import patch
from pathlib import Path
import test_build11 as fixtures
from preferences import FamilyStore
from descriptor_store import DescriptorStore
from ownership_workspace import restore_workspace,save_workspace
from agent_hub import AgentHub
from builder_workspace import BuilderWorkspace

class Responsiveness(fixtures.Ceremony):
    def test_profile_snapshot_reads_last_atomic_commit_without_waiting_for_writer(self):
        entered=threading.Event();release=threading.Event();done=threading.Event();read=[]
        old=self.family.snapshot()
        def writer():
            with self.family.transaction() as state:
                state['users'][0]['name']='New name';entered.set();release.wait(3)
        thread=threading.Thread(target=writer);thread.start();self.assertTrue(entered.wait(1))
        def reader():read.append(FamilyStore(self.config).snapshot());done.set()
        worker=threading.Thread(target=reader);worker.start()
        try:
            self.assertTrue(done.wait(.4),'Read-only UI snapshot waited on an unrelated write transaction')
            self.assertEqual(read[0]['users'][0]['name'],old['users'][0]['name'])
        finally:release.set();thread.join(3);worker.join(3)
        self.assertEqual(self.family.snapshot()['users'][0]['name'],'New name')
    def test_real_login_does_not_wait_for_unrelated_hub_transaction(self):
        import http.client,json
        from launch_wallaby_bitcoin_dashboard import create_server
        server,pool=create_server(self.folder/'http-cache',0,self.config,ipv6=False)
        pool.discovery.close();pool.nodes.health.closed=True
        session=pool.create();entered=threading.Event();release=threading.Event()
        def hold():
            with pool.nodes.hub.edit():entered.set();release.wait(3)
        writer=threading.Thread(target=hold);writer.start();self.assertTrue(entered.wait(1))
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=.6);started=time.monotonic()
            c.request('POST','/api/login',json.dumps({'user_id':self.user}),{'X-Session-Token':session.token,'Content-Type':'application/json'})
            response=c.getresponse();result=json.loads(response.read());elapsed=time.monotonic()-started;c.close()
            self.assertEqual(response.status,200,result);self.assertEqual(result['user']['id'],self.user)
            self.assertLess(elapsed,.6)
            print('Isolated Login while hub writer is held: '+str(round(elapsed*1000))+' ms')
        except TimeoutError:
            import sys,traceback
            for frame in sys._current_frames().values():
                stack=traceback.extract_stack(frame)
                if any('select_user'==f.name or 'handle_post'==f.name for f in stack):
                    print('LOGIN BLOCKED:', ''.join(traceback.format_list(stack[-14:])))
            raise
        finally:release.set();writer.join(3);server.shutdown();server.server_close();serving.join(3)

    def test_presence_never_builds_sync_history_or_queues_agent_reads(self):
        with patch.object(self.hub,'sync_warnings',side_effect=AssertionError('history on fast path')),patch.object(self.hub,'node_summary',side_effect=AssertionError('full node detail on fast path')),patch.object(self.registry.health,'refresh',side_effect=AssertionError('RPC on fast path')):
            nodes=self.registry.presence()['nodes'];self.assertEqual(len(nodes),2);self.assertTrue(all(n['rpc_ready'] for n in nodes))
            self.setup.available()
    def test_node_cards_endpoint_does_not_build_diagnostic_history(self):
        with patch.object(self.hub,'sync_warnings',side_effect=AssertionError('Removed node history was rebuilt')),patch.object(self.hub,'node_summary',side_effect=AssertionError('Removed node history was rebuilt')):
            state=self.registry.card_snapshot()
        self.assertEqual(len(state['nodes']),2);self.assertEqual(state['warnings'],[])
        self.assertTrue(all(set(n['agent'])=={'version'} for n in state['nodes']))

    def test_explicit_discovery_pauses_idle_rpc_but_preserves_requests_and_allows_foreground(self):
        self.setup.discover(foreground=True)
        self.assertTrue(self.hub.foreground_active())
        background=self.registry.rpc.queue(self.a['node_id'],'getblockcount',[],priority=100)
        explicit=self.registry.rpc.queue(self.a['node_id'],'getblockchaininfo',[],priority=0)
        commands=self.registry.heartbeat(self.a['connection_token'])['commands']
        self.assertEqual([c['id'] for c in commands],[explicit['id']])
        self.assertEqual(self.registry.rpc.status(background['id'])['state'],'queued')
        self.registry.rpc.result(self.a['connection_token'],explicit['id'],{'result':{'chain':'main','blocks':999000,'headers':999000}})
        self.hub.foreground_until.clear()
        commands=self.registry.heartbeat(self.a['connection_token'])['commands']
        self.assertEqual([c['id'] for c in commands],[background['id']])
    def test_identity_reply_is_accepted_without_running_routine_sync_reconciliation(self):
        from setup_signatures import fingerprint
        self.setup.discover(foreground=True)
        challenge=self.setup.read()['challenges'][self.a['node_id']]
        envelope=self.sign(self.a,{'version':1,'challenge':challenge,'public_key':self.pub(self.a),'fingerprint':fingerprint(self.pub(self.a)),'hostname':'Foreground Agent'})
        item=self.item('wallet_setup_identity',envelope)
        with patch.object(self.hub,'pair_transfers',side_effect=AssertionError('Routine sync reconciliation delayed identity')):
            result=self.exchange(self.a,items=[item])
        self.assertIn(item['id'],result['accepted'])
        self.assertTrue(next(n for n in self.setup.snapshot()['nodes'] if n['id']==self.a['node_id'])['fresh'])

    def test_idle_inventory_is_deferred_during_identity_checks(self):
        self.hub.prioritise([self.a['node_id']])
        before=copy.deepcopy(self.hub.inventories)
        with patch.object(self.hub,'rpc',side_effect=AssertionError('idle inventory ran during explicit discovery')):
            self.hub.refresh_inventory(self.a['node_id'],self.a['connection_token'])
        self.assertEqual(self.hub.inventories,before)
    def test_descriptor_summary_cache_invalidates_on_atomic_replacement(self):
        store=DescriptorStore(self.config);first=store.summary()
        with patch.object(DescriptorStore,'_summary',side_effect=AssertionError('Repeated expensive summary')):
            self.assertEqual(store.summary(),first)
        data=store.read();data['wallets'][0]['setup_name']='New logical name'
        temporary=store.path.with_suffix('.new');temporary.write_text(__import__('json').dumps(data));temporary.replace(store.path)
        self.assertEqual(store.summary()['wallets'][0]['setup_name'],'New logical name')
    def test_saved_record_hashing_does_not_hold_profile_transaction(self):
        inputs=self.folder/'inputs';inputs.mkdir();(inputs/'exchanges.ods').write_bytes(b'fixture');(inputs/'chain.json').write_bytes(b'{}')
        save_workspace(self.config,self.user,{'exchanges':inputs/'exchanges.ods','chain':inputs/'chain.json'},[],{'entries':[]},{},metadata={'family_accounts':{self.user:False}})
        from ownership_workspace import digest
        with patch.object(FamilyStore,'transaction',side_effect=AssertionError('Read-only restore acquired profile transaction')):
            self.assertIsNotNone(restore_workspace(self.config,self.user,self.folder/'restored'))
    def test_record_replacement_waits_only_for_generation_reader_not_profile_readers(self):
        from ownership_workspace import digest,data_root
        inputs=self.folder/'saved-inputs';inputs.mkdir();(inputs/'exchanges.ods').write_bytes(b'old records');(inputs/'chain.json').write_bytes(b'{}')
        uploads={'exchanges':inputs/'exchanges.ods','chain':inputs/'chain.json'}
        original=save_workspace(self.config,self.user,uploads,[],{'entries':[]},{},metadata={'family_accounts':{self.user:False}})
        entered=threading.Event();release=threading.Event();committed=threading.Event();results=[];errors=[]
        def slow_digest(path):
            if threading.current_thread().name=='restorer' and not entered.is_set():entered.set();release.wait(3)
            return digest(path)
        def reader():
            try:results.append(restore_workspace(self.config,self.user,self.folder/'reader'))
            except Exception as exc:errors.append(exc)
        def writer():
            try:save_workspace(self.config,self.user,uploads,[],{'entries':[]},{},metadata={'family_accounts':{self.user:False}})
            except Exception as exc:errors.append(exc)
        with patch('ownership_workspace.digest',side_effect=slow_digest):
            reading=threading.Thread(target=reader,name='restorer');reading.start();self.assertTrue(entered.wait(1))
            writing=threading.Thread(target=writer);writing.start()
            try:
                end=time.monotonic()+1
                while time.monotonic()<end:
                    if self.family.snapshot()['ownership_runs'][self.user]['data_id']!=original['data_id']:committed.set();break
                    time.sleep(.005)
                self.assertTrue(committed.is_set(),'Record cleanup held the profile writer lock')
                self.assertTrue((data_root(self.config)/original['data_id']).exists())
            finally:release.set();reading.join(3);writing.join(3)
        self.assertFalse(errors);self.assertEqual(results[0][0]['data_id'],original['data_id'])
        self.assertFalse((data_root(self.config)/original['data_id']).exists())

    def test_unchanged_hub_edit_does_not_rewrite_durable_file(self):
        with self.hub.edit() as state:state['nodes'].setdefault(self.a['node_id'],{})
        original=self.hub.path.stat()
        with self.hub.edit():pass
        after=self.hub.path.stat();self.assertEqual((original.st_mtime_ns,original.st_ino),(after.st_mtime_ns,after.st_ino))

for name in vars(fixtures.Ceremony):
    if name.startswith('test_'):setattr(Responsiveness,name,None)
