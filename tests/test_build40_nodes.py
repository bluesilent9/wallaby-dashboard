"""Remembered offline nodes, removal of obligations, and portable disc routing."""
import copy,json
from pathlib import Path
from unittest.mock import patch
import test_build8 as fixtures
import test_build18 as retirement
import test_build35 as archives
from node_removal import remove
from wallet_renames import RenameQueue
from archive_queue import ArchiveQueue,validate
from builder_workspace import BuilderWorkspace
from preferences import FamilyStore
from profile_archive import ProfileArchive
from nodes import NodeRegistry

class Forget(fixtures.Setup):
 def offline(self):self.registry.heartbeat(self.a['connection_token'],disconnect=True)
 def test_connected_node_requires_disconnect_and_confirmation(self):
  for confirmed in (False,True):
   with self.assertRaises(ValueError):remove(self.registry,self.a['node_id'],self.user,confirmed)
  self.assertEqual(len(self.registry._read()['nodes']),2)
 def test_rename_pending_target_removed_history_and_profile_remain(self):
  q=RenameQueue(self.hub);q.create(self.user,self.rid,'new_filename');q.proposal(self.a['node_id']);self.offline()
  with patch.object(self.hub,'rpc',side_effect=AssertionError('No remote wallet operations')):remove(self.registry,self.a['node_id'],self.user,True)
  self.assertEqual([n['id'] for n in self.registry._read()['nodes']],[self.b['node_id']]);self.assertEqual(len(q.snapshot()['operations'][0]['targets']),1);self.assertTrue(q.proposal(self.b['node_id'])['requests']);self.assertTrue(any(t.get('node_deleted') for o in q.read()['operations'].values() for t in o['targets'].values()))
  archive=ProfileArchive(self.config).save();target=Path(self.tmp.name)/'restored/config.cfg';FamilyStore(target);ProfileArchive(target).load('profile.7z',archive);restored=NodeRegistry(target);self.assertEqual(len(restored._read()['nodes']),1);self.assertEqual(len(RenameQueue(restored.hub).snapshot()['operations'][0]['targets']),1)
 def test_portable_destroy_disc_is_unassigned_and_can_be_routed_to_loaded_agent(self):
  q=ArchiveQueue(self.hub);q.replacements(self.a['node_id'],{'readback_verified':True,'image_sha256':'a'*64,'replacements':[{'archive_id':'b'*64,'image_sha256':'a'*64,'members':['c'*64],'readback_verified':True,'old_policies_retained':True}]},{'signed':'fixture'});original=next(iter(q.read()['requests'].values()))['request'];self.offline();remove(self.registry,self.a['node_id'],self.user,True);ident=original['request_id'];self.assertIsNone(q.read()['requests'][ident]['node_id'])
  with self.assertRaisesRegex(ValueError,'Load'):q.route(self.user,ident,self.b['node_id'])
  self.hub.caps[self.b['node_id']]={'wallet_archive_queue_v1'};self.hub.inventories[self.b['node_id']]['wallets'].append(dict(copy.deepcopy(self.node_wallet),signing_only=True,archive_id='b'*64));self.assertEqual(q.snapshot()['requests'][0]['route_options'][0]['id'],self.b['node_id']);q.route(self.user,ident,self.b['node_id']);self.assertEqual(q.proposal(self.b['node_id'])['requests'][0],original);validate(q.read())
 def test_cancellation_delivery_drops_only_deleted_node_and_unblocks_finished_batch(self):
  ws=BuilderWorkspace(self.config);w=ws.start(self.user,'wallet');s=ws.read();s['workspaces']['wallet']['cancellation_id']='cancel';s['cancellations']['cancel']={'kind':'wallet','owner_id':self.user,'workspace_id':w['id'],'targets':{self.a['node_id']:{'hostname':'First'},self.b['node_id']:{'hostname':'Second','received_utc':'2026-11-03T00:00:00Z'}},'complete':False};ws.write(s);self.offline();remove(self.registry,self.a['node_id'],self.user,True);result=ws.read();self.assertNotIn('wallet',result['workspaces']);self.assertTrue(result['cancellations']['cancel']['complete']);self.assertIn(self.a['node_id'],result['cancellations']['cancel']['removed_targets'])

class ForgetArchive(archives.Archives):
 def test_unstarted_node_bound_archival_is_withdrawn_not_falsely_completed(self):
  option=next(o for o in self.queue.options(self.rid) if o['node_id']==self.a['node_id']);self.queue.create(self.user,self.rid,[option['id']]);ident=next(iter(self.queue.read()['requests']));self.registry.heartbeat(self.a['connection_token'],disconnect=True);remove(self.registry,self.a['node_id'],self.user,True);row=self.queue.read()['requests'][ident];self.assertEqual(row['state'],'withdrawn');self.assertTrue(row['node_deleted']);self.assertEqual(self.queue.snapshot()['requests'],[]);validate(self.queue.read())
for name in archives.Archives.__dict__:
 if name.startswith('test_') and name not in ForgetArchive.__dict__:setattr(ForgetArchive,name,None)

class ForgetExtinction(retirement.Retirement):
 def test_offline_holder_removed_but_remaining_extinction_retains_its_coordinator(self):
  from wallet_retirement import snapshot
  self.hub.inventories[self.b['node_id']]=self.watch_copy;key=self.request()['request_hash'];self.registry.heartbeat(self.a['connection_token'],disconnect=True);remove(self.registry,self.a['node_id'],self.user,True);state=self.hub.read();self.assertNotIn(self.a['node_id'],state['requests'][key]['expected_holders']);self.assertEqual([h['node_id'] for h in snapshot(self.hub,self.user)[0]['holders']],[self.b['node_id']])
  from agent_state import validate
  validate(state,{self.user},self.config)
 def test_removal_keeps_completed_preservation_on_another_node(self):
  key=self.request()['request_hash']
  with self.hub.edit() as state:state['requests']['completed-proof']=dict(copy.deepcopy(state['requests'][key]),root_hash=key,node_id=self.b['node_id'],state='complete',result={'history_verified':True});state['requests']['completed-proof'].pop('expected_holders',None)
  self.registry.heartbeat(self.a['connection_token'],disconnect=True);remove(self.registry,self.a['node_id'],self.user,True)
  proof=self.hub.read()['requests']['completed-proof'];self.assertEqual(proof['state'],'complete');self.assertFalse(proof.get('node_deleted'))
 def test_removal_of_last_holder_keeps_valid_parked_history(self):
  from wallet_retirement import snapshot
  from agent_state import validate
  key=self.request()['request_hash'];self.registry.heartbeat(self.a['connection_token'],disconnect=True);remove(self.registry,self.a['node_id'],self.user,True);state=self.hub.read();validate(state,{self.user},self.config);self.assertTrue(state['requests'][key]['node_removal_parked']);self.assertEqual(snapshot(self.hub,self.user),[])
for name in retirement.Retirement.__dict__:
 if name.startswith('test_') and name not in ForgetExtinction.__dict__:setattr(ForgetExtinction,name,None)

class Http(fixtures.Setup):
 def test_remembered_nodes_process_list_and_confirmed_delete_endpoint(self):
  import http.client,threading
  from launch_wallaby_bitcoin_dashboard import create_server
  server,sessions=create_server(Path(self.tmp.name)/'cache',0,self.config);sessions.nodes.health.closed=True;session=sessions.create();session.user_id=self.user;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  def request(method,url,body=None):
   c=http.client.HTTPConnection('::1',server.server_port,timeout=5);c.request(method,url,body=json.dumps(body) if body else None,headers={'Content-Type':'application/json','X-Session-Token':session.token});r=c.getresponse();raw=r.read();p=json.loads(raw) if 'json' in r.getheader('Content-Type','') else raw.decode();c.close();return r.status,p
  try:
   code,p=request('GET','/api/wallets/processes');self.assertEqual(code,200,p);self.assertEqual(len(p['nodes']),2);self.assertTrue(all(not n['connected'] for n in p['nodes']))
   self.assertEqual(request('POST','/api/nodes/delete',dict(node_id=self.a['node_id'],confirmed=False))[0],400)
   code,p=request('POST','/api/nodes/delete',dict(node_id=self.a['node_id'],confirmed=True));self.assertEqual(code,200,p);self.assertTrue(p['deleted']);self.assertEqual(len(request('GET','/api/wallets/processes')[1]['nodes']),1)
   self.assertEqual(request('GET','/assets/wallet_processes.js')[0],200)
  finally:server.shutdown();server.server_close();t.join(3)
