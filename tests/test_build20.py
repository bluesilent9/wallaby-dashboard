"""Real Dashboard stores/exchange; fake Agents acknowledge durable receipt only."""
import copy,concurrent.futures,json,unittest
from unittest.mock import patch
from test_build8 import Setup
from builder_workspace import BuilderWorkspace,validate,digest
from transaction_workflows import WorkflowStore
from agent_transactions import queue_sign,signing_state

class Builders(Setup):
 def setUp(self):
  super().setUp();self.ws=BuilderWorkspace(self.config);self.family.save_user('Other','blue',revision=self.family.snapshot()['revision']);self.other=self.family.snapshot()['users'][1]['id']
 def ceremony(self,delivered=True):
  w=self.ws.start(self.user,'wallet');h='c'*64
  with self.hub.wallet_builder.edit() as s:s['sessions'][h]={'creator_id':self.user,'state':'participation','routes':{'one':self.a['node_id'],'two':self.b['node_id']},'delivered_nodes':[self.a['node_id'],self.b['node_id']] if delivered else []}
  self.ws.attach(self.user,'wallet',h);return w,h
 def receipt(self,node,p):return self.item('builder_cancel_receipt',{'version':1,'cancellation_id':p['cancellation_id'],'request_hash':digest(p),'status':'received'})
 def test_owner_lock_draft_revision_and_reopen_survive_new_store(self):
  w=self.ws.start(self.user,'wallet');self.ws.save(self.user,'wallet',{'id':w['id'],'revision':0,'draft':{'name':'Saved Test','type':'3','count':'3','threshold':'2','roles':[],'vault':{'date':'2027-01-01','zone':'Australia/Darwin','result':None}}})
  self.assertEqual(BuilderWorkspace(self.config).snapshot(self.user,'wallet')['workspace']['draft']['name'],'Saved Test')
  blocked=self.ws.snapshot(self.other,'wallet');self.assertTrue(blocked['blocked']);self.assertNotIn('draft',blocked['workspace']);self.assertNotIn('operations',blocked['workspace'])
  with self.assertRaises(ValueError):self.ws.start(self.other,'wallet')
  with self.assertRaises(ValueError):self.ws.save(self.user,'wallet',{'id':w['id'],'revision':0,'draft':{}})
  with self.assertRaises(ValueError):self.ws.save(self.other,'wallet',{'id':w['id'],'revision':1,'draft':{}})
  # Each builder is exclusive independently, not an unnecessary global family lock.
  self.ws.start(self.other,'transaction')
 def test_concurrent_start_has_only_one_creator(self):
  def start(u):
   try:return self.ws.start(u,'transaction')['owner_id']
   except ValueError:return None
  with concurrent.futures.ThreadPoolExecutor(2) as pool:r=list(pool.map(start,[self.user,self.other]))
  self.assertEqual(sum(x is not None for x in r),1)
 def test_cancel_before_any_agent_delivery_is_immediate_and_never_deletes(self):
  w,h=self.ceremony(False);result=self.ws.cancel(self.hub,self.user,'wallet',True)
  self.assertIsNone(result['workspace']);self.assertFalse(self.ws.read()['cancellations'][next(iter(self.ws.read()['cancellations']))]['targets']);self.assertEqual(self.hub.wallet_builder.read()['sessions'][h]['state'],'cancelled')
 def test_offline_nodes_receipts_idempotence_and_profile_restore(self):
  w,h=self.ceremony();self.registry.connections.pop(self.b['connection_token']);s=self.ws.cancel(self.hub,self.user,'wallet',True);self.assertEqual({n['colour'] for n in s['cancellation']['targets']},{'amber','red'})
  validate(copy.deepcopy(self.ws.read()),{self.user,self.other});self.assertEqual(BuilderWorkspace(self.config).snapshot(self.user,'wallet',self.registry)['cancellation']['id'],s['cancellation']['id'])
  # Agent 18 is not sent an unsupported cleanup proposal; receipt capability is explicit.
  old=self.exchange(self.a,caps=['partial_signing']);self.assertFalse(any(p['kind']=='builder_cancel' for p in old['proposals']))
  p=next(p for p in self.exchange(self.a,caps=['builder_cancellation_v1'])['proposals'] if p['kind']=='builder_cancel')
  self.assertTrue(p['require_local_authorisation']);self.assertTrue(p['backup_option']);self.assertNotIn('delete_wallet',p);self.assertIn(h,p['request_hashes'])
  item=self.receipt(self.a,p);result=self.exchange(self.a,[item],caps=['builder_cancellation_v1']);self.assertIn(item['id'],result['accepted']);self.exchange(self.a,[item],caps=['builder_cancellation_v1']);self.assertTrue(self.ws.snapshot(self.user,'wallet',self.registry)['workspace'])
  # The last receipt closes Dashboard coordination without claiming local deletion.
  proposals=self.ws.proposals(self.b['node_id']);self.ws.receipt(self.b['node_id'],self.receipt(self.b,proposals[0])['payload']);self.assertIsNone(self.ws.snapshot(self.user,'wallet')['workspace'])
  self.ws.start(self.other,'wallet');self.ws.receipt(self.a['node_id'],item['payload']);self.assertEqual(self.ws.owned(self.other,'wallet')['owner_id'],self.other)
 def test_cancel_refuses_other_owner_and_false_confirmation_and_wrong_receipt(self):
  self.ceremony()
  with self.assertRaises(ValueError):self.ws.cancel(self.hub,self.other,'wallet',True)
  with self.assertRaises(ValueError):self.ws.cancel(self.hub,self.user,'wallet',False)
  self.ws.cancel(self.hub,self.user,'wallet',True);p=self.ws.proposals(self.a['node_id'])[0];payload=self.receipt(self.a,p)['payload']
  with self.assertRaises(ValueError):self.ws.receipt('wrong-node',payload)
  with self.assertRaises(ValueError):self.ws.receipt(self.a['node_id'],dict(payload,request_hash='0'*64))
  with self.assertRaises(ValueError):self.ws.receipt(self.a['node_id'],dict(payload,status='deleted'))
 def test_transaction_creator_can_coordinate_another_owners_signatures(self):
  ident=self.job();store=WorkflowStore(self.config);s=store._read();s['jobs'][ident]['wallets'][0]['owner_id']=self.other;store._write(s)
  self.hub.caps[self.a['node_id']]={'partial_signing'};session=type('Session',(),{'user_id':self.user})()
  self.assertTrue(signing_state(self.hub,self.user,ident)['signing_wallets'][0]['nodes']);queue_sign(self.hub,session,ident,self.rid,self.a['node_id'])
  with self.assertRaises(ValueError):store.job(self.other,ident)
  self.assertFalse(store.active(self.other));self.assertFalse(store.notifications(self.other)['notifications'])
 def test_cancel_transaction_includes_delivered_signers_and_excludes_unsent(self):
  self.ws.start(self.user,'transaction');ident=self.job();self.ws.attach(self.user,'transaction',ident)
  self.hub.caps[self.a['node_id']]={'partial_signing'};session=type('Session',(),{'user_id':self.user})();queue_sign(self.hub,session,ident,self.rid,self.a['node_id'])
  self.exchange(self.a,caps=['partial_signing']);key=next(iter(self.hub.read()['requests']))
  result=self.ws.cancel(self.hub,self.user,'transaction',True);self.assertEqual([n['node_id'] for n in result['cancellation']['targets']],[self.a['node_id']]);self.assertTrue(WorkflowStore(self.config).job(self.user,ident)['cancelled'])
  self.assertFalse(any(p['kind']=='sign_psbt_partial' for p in self.exchange(self.a,caps=['partial_signing','builder_cancellation_v1'])['proposals']))
  # A late authenticated result is drained, never used to revive/sign the cancelled job.
  self.exchange(self.a,[self.item('partial_sign_result',{'request_hash':key,'status':'signed','psbt':self.signed})],caps=['builder_cancellation_v1']);self.assertTrue(WorkflowStore(self.config).job(self.user,ident)['cancelled'])
 def test_operation_identity_recovers_attachment_after_interrupted_http_response(self):
  w=self.ws.start(self.user,'wallet');h='d'*64
  with self.hub.wallet_builder.edit() as s:s['sessions'][h]={'workspace_id':w['id'],'creator_id':self.user,'state':'participation','routes':{},'delivered_nodes':[self.a['node_id']]}
  self.assertEqual(BuilderWorkspace(self.config).owned(self.user,'wallet')['operations'],[h])
  result=self.ws.cancel(self.hub,self.user,'wallet',True);self.assertEqual(len(result['cancellation']['targets']),1)
 def test_interrupted_cancellation_withdraws_work_before_next_agent_exchange(self):
  self.ws.start(self.user,'transaction');ident=self.job();self.ws.attach(self.user,'transaction',ident)
  self.hub.caps[self.a['node_id']]={'partial_signing'};session=type('Session',(),{'user_id':self.user})();queue_sign(self.hub,session,ident,self.rid,self.a['node_id']);self.exchange(self.a,caps=['partial_signing'])
  with patch.object(BuilderWorkspace,'reconcile',side_effect=RuntimeError('Simulated interruption')):
   with self.assertRaises(RuntimeError):self.ws.cancel(self.hub,self.user,'transaction',True)
  self.assertFalse(WorkflowStore(self.config).job(self.user,ident)['cancelled'])
  reply=self.exchange(self.a,caps=['partial_signing','builder_cancellation_v1'])
  self.assertTrue(WorkflowStore(self.config).job(self.user,ident)['cancelled']);self.assertFalse(any(p['kind']=='sign_psbt_partial' for p in reply['proposals']));self.assertTrue(any(p['kind']=='builder_cancel' for p in reply['proposals']))
 def test_private_material_and_bad_diagram_are_rejected_in_saved_drafts(self):
  w=self.ws.start(self.user,'transaction')
  with self.assertRaises(ValueError):self.ws.save(self.user,'transaction',{'id':w['id'],'revision':0,'draft':{'nodes':[],'edges':[]}})
  w=self.ws.start(self.user,'wallet')
  from vendor.embit import bip32
  private=bip32.HDKey.from_seed(b'Public draft must never accept these keys').to_base58()
  with self.assertRaises(ValueError):self.ws.save(self.user,'wallet',{'id':w['id'],'revision':0,'draft':{'name':private}})

class BuilderHTTP(unittest.TestCase):
 def test_server_enforces_creator_drafts_and_cancel_even_without_live_nodes(self):
  import http.client,tempfile,threading
  from pathlib import Path
  from preferences import FamilyStore
  from launch_wallaby_bitcoin_dashboard import create_server
  with tempfile.TemporaryDirectory() as d:
   cfg=Path(d)/'config.cfg';family=FamilyStore(cfg);family.save_user('Alice','green',revision=0);family.save_user('Bob','blue',revision=family.snapshot()['revision'])
   server,sessions=create_server(Path(d)/'cache',0,cfg);sessions.nodes.health.closed=True;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   users=family.snapshot()['users'];a=sessions.create();b=sessions.create();a.user_id=users[0]['id'];b.user_id=users[1]['id']
   for session in (a,b):session.access=lambda:{'ownership_ready':True,'templates_ready':True}
   def request(session,path,body=None):
    c=http.client.HTTPConnection('::1',server.server_port,timeout=5);c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,{'X-Session-Token':session.token,'Content-Type':'application/json'});r=c.getresponse();out=json.loads(r.read());c.close();return r.status,out
   try:
    with patch.object(sessions.nodes,'ready_for_builders',return_value=True):
     code,data=request(a,'/api/builders/wallet/start',{});self.assertEqual(code,200);w=data['workspace']
     self.assertEqual(request(b,'/api/builders/wallet/start',{})[0],400)
     code,blocked=request(b,'/api/builders/wallet');self.assertTrue(blocked['blocked']);self.assertNotIn('draft',blocked['workspace'])
     self.assertEqual(request(b,'/api/builders/wallet/save',{'id':w['id'],'revision':0,'draft':{}})[0],400)
     self.assertEqual(request(b,'/api/wallet-builder/create',{})[0],400)
     self.assertEqual(request(a,'/api/builders/wallet/save',{'id':w['id'],'revision':0,'draft':{'name':'Remember me','type':'0'}})[0],200)
    with patch.object(sessions.nodes,'ready_for_builders',return_value=False):
     self.assertEqual(request(a,'/api/builders/wallet')[1]['workspace']['draft']['name'],'Remember me')
     self.assertEqual(request(b,'/api/builders/wallet/cancel',{'confirmed':True})[0],400)
     code,data=request(a,'/api/builders/wallet/cancel',{'confirmed':True});self.assertEqual(code,200);self.assertIsNone(data['workspace'])
   finally:server.shutdown();server.server_close();thread.join(3)
