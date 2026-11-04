"""Archive destinations are explicit, explained, and separate from a live build."""
import json
from pathlib import Path
import test_build35 as fixtures
from builder_workspace import BuilderWorkspace
from archive_queue import CAPABILITY,CANCEL_CAPABILITY

class ArchiveDestinations(fixtures.Archives):
    def test_watch_only_agent_is_visible_but_never_an_archive_source(self):
        self.hub.inventories[self.a['node_id']]['wallets'][0]['private_keys_enabled']=False
        options=self.queue.options(self.rid);self.assertEqual(len(options),1)
        destinations=self.queue.destinations(self.rid,options);self.assertEqual(len(destinations),2)
        watch=next(r for r in destinations if r['node_id']==self.a['node_id']);self.assertFalse(watch['available']);self.assertIn('Watch-only',watch['reason'])
        self.assertTrue(next(r for r in destinations if r['node_id']==self.b['node_id'])['available'])
        self.assertFalse(self.queue.read()['requests'],'Listing options must never queue a request')
    def test_inventory_and_capability_problems_are_explained_without_writes(self):
        self.hub.inventories[self.a['node_id']]['complete']=False;self.hub.caps[self.b['node_id']]=set()
        options=self.queue.options(self.rid);rows=self.queue.destinations(self.rid,options)
        self.assertIn('inventory',next(r for r in rows if r['node_id']==self.a['node_id'])['reason'])
        self.assertIn('update',next(r for r in rows if r['node_id']==self.b['node_id'])['reason'])
        self.assertFalse(any(r['available'] for r in rows));self.assertFalse(self.queue.path.exists())
    def test_selected_request_and_cancel_cannot_modify_active_wallet_creation(self):
        workspace=BuilderWorkspace(self.config);workspace.start(self.user,'wallet');state=workspace.read();state['workspaces']['wallet']['operations']=['existing-setup'];workspace.write(state)
        path=Path(self.config).parent/'data/wallet-setups.json';path.write_text(json.dumps({'version':1,'sessions':{'existing-setup':{'state':'policy','proposal':{'name':'Existing vault'},'contributions':{'unchanged':'signed'},'acks':{'keep':'approval'}}}}))
        setup_before=path.read_bytes();workspace_before=workspace.path.read_bytes()
        for n in (self.a,self.b):self.hub.caps[n['node_id']]={CAPABILITY,CANCEL_CAPABILITY}
        options=self.queue.options(self.rid);self.queue.destinations(self.rid,options)
        chosen=next(r for r in options if r['node_id']==self.a['node_id']);self.queue.create(self.user,self.rid,[chosen['id']])
        self.assertEqual(self.queue.proposal(self.b['node_id'])['requests'],[])
        task=next(iter(self.queue.read()['requests']));self.queue.cancel(self.user,task)
        archive=self.queue.read()['requests'];self.assertEqual(archive[task]['state'],'withdrawn');cancel=archive[archive[task]['cancellation_id']]
        self.assertEqual(cancel['request']['kind'],'archive_cancel');self.assertEqual(cancel['node_id'],self.a['node_id']);self.assertTrue(cancel['request']['finish_inflight_burn_and_verify'])
        self.assertEqual(setup_before,path.read_bytes());self.assertEqual(workspace_before,workspace.path.read_bytes())

for name in vars(fixtures.Archives):
    if name.startswith('test_'):setattr(ArchiveDestinations,name,None)
