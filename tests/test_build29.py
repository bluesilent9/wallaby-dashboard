"""Rename identity, authorization receipts, withdrawal races and archive evidence."""
import copy,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_build8 import Setup
from wallet_renames import RenameQueue,validate,name
from archive_queue import ArchiveQueue
from builder_workspace import BuilderWorkspace
class Renames(Setup):
 def setUp(self):
  super().setUp();self.q=RenameQueue(self.hub)
 def start(self):return self.q.create(self.user,self.rid,'Renamed Wallet')['operations'][0]['id']
 def reply(self,ident,node,status='complete',**extra):
  self.q.proposal(node);s=self.q.read();t=next(t for t in s['operations'][ident]['targets'].values() if t['node_id']==node);p={'version':1,'queue_id':s['queue_id'],'rename_id':ident,'request_id':t['request']['request_id'],'wallet':t['wallet'],'new_wallet':t['new_name'],'storage':t['storage'],'status':status,'identity_verified':True,**extra}
  with patch('wallet_policy_updates.verified',return_value=p):self.q.accept(node,{'signed':'fixture'})
  return p
 def test_all_descriptor_copies_watch_suffix_public_only(self):
  ident=self.start();p=self.q.proposal(self.b['node_id']);self.assertEqual(len(p['requests']),1);self.assertEqual(p['requests'][0]['new_name'],'Renamed Wallet_watch');self.assertEqual(len(self.q.read()['operations'][ident]['targets']),2);validate(self.q.read());self.assertNotIn('xprv',str(p))
 def test_late_success_after_cancel_restores_remaining(self):
  ident=self.start();self.q.proposal(self.a['node_id']);self.q.cancel(self.user,ident);self.assertFalse(self.q.proposal(self.a['node_id'])['requests']);self.reply(ident,self.a['node_id']);o=self.q.snapshot()['operations'][0];self.assertFalse(o['can_cancel']);self.assertEqual(o['state'],'active');self.assertTrue(self.q.proposal(self.b['node_id'])['requests'])
 def test_first_success_locks_cancel_all_holders_complete(self):
  ident=self.start();self.reply(ident,self.a['node_id'])
  with self.assertRaisesRegex(ValueError,'already renamed'):self.q.cancel(self.user,ident)
  self.reply(ident,self.b['node_id']);self.assertEqual(self.q.snapshot()['operations'][0]['state'],'complete');self.assertEqual(self.family.snapshot()['ownership'][0]['name'],'Renamed Wallet')
 def test_wrong_node_cannot_complete(self):
  ident=self.start();p=self.reply(ident,self.a['node_id'])
  with patch('wallet_policy_updates.verified',return_value=p):
   with self.assertRaises(ValueError):self.q.accept(self.b['node_id'],{})
 def test_live_renamed_copy_does_not_duplicate(self):
  ident=self.start();self.reply(ident,self.a['node_id']);w=self.hub.inventories[self.a['node_id']]['wallets'][0];w['name']='Renamed Wallet';w['raw']['wallet_name']='Renamed Wallet';self.q.proposal(self.b['node_id']);self.assertEqual(len(self.q.read()['operations'][ident]['targets']),2)
 def test_matching_private_and_watch_same_node_both_included(self):
  self.hub.inventories[self.a['node_id']]['wallets'].append(dict(copy.deepcopy(self.node_wallet),name='local_watch',private_keys_enabled=False));ident=self.start();tasks=self.q.proposal(self.a['node_id'])['requests'];self.assertEqual(len(tasks),2);self.assertEqual(len({p['new_name'] for p in tasks}),2)
 def test_busy_builder_blocks_stale_routes(self):
  BuilderWorkspace(self.config).start(self.user,'wallet')
  with self.assertRaisesRegex(ValueError,'builds'):self.start()
 def test_archive_pending_until_local_verified_replacement_or_waiver(self):
  a=ArchiveQueue(self.hub);s=a.read();s['requests']['f'*64]={'state':'complete','request':{'kind':'archive_wallet','public_wallet':self.wallet['raw'],'wallet':self.wallet['name']},'result':{'archive_id':'e'*64}};a.write(s);ident=self.start();o=self.q.read()['operations'][ident];self.assertEqual(len(o['targets']),3);key,t=next((k,t) for k,t in o['targets'].items() if t['storage']=='disc');self.assertIsNone(t['request']);self.hub.caps[self.a['node_id']]={'wallet_rename_v1'};self.hub.inventories[self.a['node_id']]['wallets'].append(dict(copy.deepcopy(self.node_wallet),name='Disc original',signing_only=True,archive_id='e'*64));self.q.route_archive(self.user,ident,key,self.a['node_id']);s=self.q.read();t=s['operations'][ident]['targets'][key];p={'version':1,'queue_id':s['queue_id'],'rename_id':ident,'request_id':t['request']['request_id'],'wallet':t['wallet'],'new_wallet':'Disc renamed','storage':'disc','status':'archive_required','identity_verified':True}
  with patch('wallet_policy_updates.verified',return_value=p):self.q.accept(self.a['node_id'],{})
  self.assertTrue(self.q.read()['operations'][ident]['locked']);p['status']='complete'
  with patch('wallet_policy_updates.verified',return_value=p):
   with self.assertRaisesRegex(ValueError,'replacement'):self.q.accept(self.a['node_id'],{})
  p.update(archive_skipped=True,duplicate_wallet='independently verified local signer')
  with patch('wallet_policy_updates.verified',return_value=p):self.q.accept(self.a['node_id'],{})
  self.assertEqual(self.q.read()['operations'][ident]['targets'][key]['state'],'complete')
 def test_uncertain_attempt_locks_cancellation(self):
  ident=self.start();self.reply(ident,self.a['node_id'],'error',identity_verified=False,attempted=True,outcome_uncertain=True)
  with self.assertRaisesRegex(ValueError,'already renamed'):self.q.cancel(self.user,ident)
 def test_verified_creation_archive_inventory_is_included(self):
  m={'version':1,'event':'archive_verified','archive_id':'c'*64,'image_sha256':'b'*64,'readback_verified':True,'disc_count':1,'replacements':[],'members':[{'member_id':'d'*64,'policy_hash':'a'*64,'name':'Original Archive','role_id':'role','public_wallet':self.wallet['raw'],'related_policies':[]}]}
  with patch('wallet_policy_updates.verified',return_value=m):ArchiveQueue(self.hub).followup(self.a['node_id'],{'signed':'fixture'})
  ident=self.start();self.assertEqual(len(self.q.read()['operations'][ident]['targets']),3)
  m['readback_verified']=False
  with patch('wallet_policy_updates.verified',return_value=m):
   with self.assertRaises(ValueError):ArchiveQueue(self.hub).followup(self.a['node_id'],{})
 def test_replacement_disc_not_requeued_for_rename(self):
  ident=self.start();o=self.q.read()['operations'][ident]
  with patch.object(self.q,'archives',return_value={'replacement':{'archive_id':'c'*64,'raw':self.wallet['raw'],'storage':'disc','node_id':None,'hostname':'Agent','wallet':'Renamed Wallet'}}):self.q.add_targets(o)
  self.assertEqual(len(o['targets']),2)
 def test_new_build_cannot_start_during_rename(self):
  self.start()
  with self.assertRaisesRegex(ValueError,'renames'):BuilderWorkspace(self.config).start(self.user,'transaction')
 def test_invalid_names_and_tampered_profile(self):
  for n in ('../a','a/b','a\\b','Extinct','Wallaby Extinct',' a','a\x00','x'*101):
   with self.assertRaises(ValueError):name(n)
  self.start();self.q.proposal(self.a['node_id']);s=self.q.read();validate(s);next(t for t in next(iter(s['operations'].values()))['targets'].values() if t['request'])['request']['new_name']='tampered'
  with self.assertRaises(ValueError):validate(s)
from test_build27 import SignedProtocol
class AuthenticatedRename(SignedProtocol):
 def test_signed_rename_and_forgery_rejection(self):
  m={'version':1,'event':'archive_verified','archive_id':'c'*64,'image_sha256':'b'*64,'readback_verified':True,'disc_count':1,'replacements':[],'members':[{'member_id':'d'*64,'name':'Disc','public_wallet':self.wallet['raw'],'related_policies':[]}]}
  ArchiveQueue(self.hub).followup(self.a['node_id'],self.sign(m))
  from agent_state import validate as validate_agent
  validate_agent(self.hub.read(),{self.user},self.config)
  q=RenameQueue(self.hub);ident=q.create(self.user,self.rid,'Renamed')['operations'][0]['id'];queue=q.proposal(self.a['node_id']);t=queue['requests'][0];p={'version':1,'queue_id':queue['queue_id'],'request_id':t['request_id'],'rename_id':ident,'wallet':t['wallet'],'new_wallet':t['new_name'],'storage':'node','status':'complete','identity_verified':True};envelope=self.sign(p);q.accept(self.a['node_id'],envelope);q.accept(self.a['node_id'],envelope);bad=copy.deepcopy(envelope);bad['message']['new_wallet']='Forged'
  with self.assertRaises(ValueError):q.accept(self.a['node_id'],bad)
 def test_actual_agent25_accepts_exact_public_task_and_rejects_changed_name(self):
  import os,subprocess,json
  source=os.environ.get('WALLABY_AGENT_SOURCE')
  if not source:self.skipTest('Set WALLABY_AGENT_SOURCE to Agent Build 25 src')
  if not (Path(source)/'rename_requests.py').exists():self.skipTest('Agent rename implementation is required')
  q=RenameQueue(self.hub);q.create(self.user,self.rid,'Renamed');queue=q.proposal(self.a['node_id'])
  code="import sys,json;sys.path.insert(0,sys.argv[1]);from rename_requests import validate_queue;p=json.load(sys.stdin);validate_queue(p);p['requests'][0]['new_name']='Changed';\ntry:validate_queue(p)\nexcept ValueError:print('bound')\nelse:raise AssertionError('accepted changed name')"
  result=subprocess.run([sys.executable,'-c',code,source],input=json.dumps(queue),text=True,capture_output=True);self.assertEqual(result.returncode,0,result.stderr);self.assertIn('bound',result.stdout)
if __name__=='__main__':unittest.main()
