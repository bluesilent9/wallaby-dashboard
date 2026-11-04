"""Late replicas, mixed archive discs and withdrawal safety."""
import copy
import test_build18 as previous
from wallet_retirement import advance,result,group_rows,group_complete,CAPABILITY,latest_delete
from archive_queue import ArchiveQueue,validate

class Late(previous.Retirement):
 def disc(self,mixed=True):
  member={'member_id':'d'*64,'public_wallet':self.wallet['raw']};other=copy.deepcopy(member);other['member_id']='e'*64
  if mixed:
   from test_node_loading import public_wallet
   other['public_wallet']=public_wallet('other',7)
  message={'version':1,'event':'archive_verified','readback_verified':True,'archive_id':'a'*64,'image_sha256':'f'*64,'members':[member]+([other] if mixed else [])}
  with self.hub.edit() as s:s['nodes'].setdefault(self.a['node_id'],{})['archive_inventory']={'a'*64:{'message':message,'envelope':{'message':message}}}
  self.hub.caps[self.a['node_id']].add('wallet_archive_queue_v1')
  return message
 def test_late_holder_added_to_completed_tombstone(self):
  key=self.delivered();self.finish(key);self.assertTrue(group_complete(self.hub.read(),key));self.hub.inventories[self.b['node_id']]=self.watch_copy;self.hub.caps[self.b['node_id']]={CAPABILITY};advance(self.hub)
  self.assertIsNotNone(self.deletion(key,self.b['node_id']));self.assertFalse(group_complete(self.hub.read(),key))
 def test_deleted_wallet_reappearing_on_same_node_gets_new_review(self):
  key=self.delivered();self.finish(key);original=copy.deepcopy(self.hub.inventories[self.a['node_id']]['wallets']);self.hub.inventories[self.a['node_id']]['wallets']=[];advance(self.hub)
  self.hub.inventories[self.a['node_id']]['wallets']=original;advance(self.hub)
  deletes=[r for r in group_rows(self.hub.read(),key) if r['proposal']['phase']=='delete'];self.assertEqual(len(deletes),2);self.assertEqual(latest_delete(deletes,self.a['node_id'])['state'],'pending');self.assertFalse(group_complete(self.hub.read(),key))
 def test_late_holder_waits_if_preserving_archive_disconnected(self):
  key=self.delivered();self.finish(key);self.hub.inventories[self.b['node_id']]=self.watch_copy;self.hub.caps[self.b['node_id']]={CAPABILITY};self.hub.inventories[self.a['node_id']]['archive_wallets']=[];advance(self.hub);self.assertIsNone(self.deletion(key,self.b['node_id']))
 def test_late_existing_archive_receives_preservation(self):
  key=self.delivered();self.finish(key);self.hub.inventories[self.b['node_id']]['archive_wallets']=['other_history'];self.hub.caps[self.b['node_id']]={CAPABILITY};advance(self.hub)
  self.assertTrue(any(r['node_id']==self.b['node_id'] and r['proposal']['phase']=='preserve' for r in group_rows(self.hub.read(),key)))
 def test_mixed_disc_requires_verified_replacement_and_local_destruction(self):
  self.disc();key=self.delivered();self.preserve(key);q=ArchiveQueue(self.hub);s=q.read();self.assertEqual(len(s['requests']),1);ident,task=next(iter(s['requests'].items()));p=task['request'];self.assertEqual(p['version'],2);self.assertTrue(p['require_replacement_if_live_members']);self.assertTrue(p['require_local_authorisation']);validate(s)
  body={'version':1,'queue_id':s['queue_id'],'request_id':ident,'status':'complete','destruction_confirmed_locally':True}
  with self.assertRaisesRegex(ValueError,'replacement'):q.accept(self.a['node_id'],{**body,'signed_receipt':{'message':body}})
  body.update(replacement_readback_verified=True,replacement_image_sha256='1'*64);q.accept(self.a['node_id'],{**body,'signed_receipt':{'message':body}});self.assertEqual(q.read()['requests'][ident]['state'],'complete')
 def test_old_disc_discovered_after_completion_is_queued_once(self):
  key=self.delivered();self.finish(key);self.disc(False);advance(self.hub);advance(self.hub);q=ArchiveQueue(self.hub);self.assertEqual(len(q.read()['requests']),1)
  ident=next(iter(q.read()['requests']));q.cancel(self.user,ident);advance(self.hub);self.assertEqual(len(q.read()['requests']),1);self.assertEqual(q.read()['requests'][ident]['state'],'withdrawn')
 def test_no_disc_destruction_request_before_preservation(self):
  self.disc(False);self.delivered();advance(self.hub);self.assertFalse(ArchiveQueue(self.hub).read()['requests'])
 def test_completed_tombstone_does_not_repeat_expensive_history_reads(self):
  key=self.delivered();self.finish(key);self.calls=[];advance(self.hub);self.assertFalse([r for r in self.calls if r[1] in ('gettransaction','getaddressinfo','listtransactions')])
 def test_authenticated_old_disc_observation_is_not_replacement_evidence(self):
  message=self.disc(False);message.update(event='archive_observed',payload_sha256=message['archive_id'],payload_verified=True,readback_verified=False);message.pop('image_sha256');q=ArchiveQueue(self.hub)
  q.followup(self.a['node_id'],{'message':message})
  from agent_state import validate as validate_profile
  validate_profile(self.hub.read(),{self.user})
  key=self.delivered();self.preserve(key);self.assertEqual(len(q.read()['requests']),1)
  bad=copy.deepcopy(message);bad['payload_verified']=False
  with self.assertRaises(ValueError):q.followup(self.a['node_id'],{'message':bad})
 def test_wallet_names_accept_years_spaces_and_parentheses(self):
  from wallet_renames import name
  self.assertEqual(name('Old Treasury Wallet (2017 - 2026)'),'Old Treasury Wallet (2017 - 2026)')
 def test_real_agent_accepts_generated_phases_and_its_signature_verifies(self):
  import os,sys,subprocess,json,tempfile
  from pathlib import Path
  source=os.environ.get('WALLABY_AGENT_PHASE_SOURCE')
  if not source:self.skipTest('Agent phased source not supplied')
  key=self.delivered();preserve=self.hub.read()['requests'][key]['proposal'];proof=self.proof(key)
  with tempfile.TemporaryDirectory() as temp:
   keyfile=Path(temp)/'identity';subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(keyfile)],check=True)
   message=proof['archive_receipt']['message'];payload=Path(temp)/'message';payload.write_bytes(__import__('setup_signatures').canonical(message));subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(keyfile),'-n','wallaby-wallet-setup-v1',str(payload)],check=True,capture_output=True)
   envelope={'message':message,'public_key':' '.join(keyfile.with_suffix('.pub').read_text().split()[:2]),'signature':__import__('base64').b64encode(payload.with_suffix('.sig').read_bytes()).decode()}
   __import__('setup_signatures').verify(self.config,envelope,envelope['public_key']);proof['archive_receipt']=envelope
   with self.hub.edit() as state:result(state,self.a['node_id'],proof)
   advance(self.hub);delete=self.hub.read()['requests'][self.deletion(key)]['proposal']
   cmd="import sys,json;sys.path.insert(0,sys.argv[1]);from retirement_phases import validate;from wallet_rename import name;name('Old Treasury Wallet (2017 - 2026)');[validate(p) for p in json.load(sys.stdin)];print('accepted')"
   r=subprocess.run([sys.executable,'-B','-c',cmd,source],input=json.dumps([preserve,delete]),text=True,capture_output=True,timeout=15);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout.strip(),'accepted')
 def test_queued_cleanup_waits_when_archive_goes_offline_before_delivery(self):
  self.disc(False);key=self.delivered();self.preserve(key);q=ArchiveQueue(self.hub);before=q.proposal(self.a['node_id']);self.assertEqual(len(before['requests']),1)
  self.hub.inventories[self.a['node_id']]['archive_wallets']=[]
  wire=self.exchange(self.a,caps=[CAPABILITY])['proposals'];self.assertFalse([p for p in wire if p.get('phase')=='delete'])
  hidden=q.proposal(self.a['node_id']);self.assertEqual(hidden['requests'],[]);self.assertGreater(hidden['revision'],before['revision'])
  self.hub.inventories[self.a['node_id']]['archive_wallets']=['historical_container'];again=q.proposal(self.a['node_id']);self.assertEqual(len(again['requests']),1);self.assertGreater(again['revision'],hidden['revision'])
 def test_watch_route_name_never_becomes_extinct_wallet_label(self):
  watch=copy.deepcopy(self.watch_copy);watch['wallets'][0]['name']='treasury_watch (2017 - 2026)';watch['wallets'][0]['raw']['wallet_name']='treasury_watch (2017 - 2026)'
  self.hub.inventories[self.b['node_id']]=watch;self.hub.caps[self.b['node_id']]={CAPABILITY};key=self.delivered();self.preserve(key)
  for row in group_rows(self.hub.read(),key):
   p=row['proposal'];self.assertEqual(p['archive_label'],'Example Wallet');self.assertTrue(all(a['label']=='Example Wallet' for a in p['manifest']['addresses']))
  deletion=self.hub.read()['requests'][self.deletion(key,self.b['node_id'])]['proposal'];self.assertEqual(deletion['wallet'],'treasury_watch (2017 - 2026)');self.assertEqual(deletion['archive_label'],'Example Wallet')
 def test_holder_with_existing_archive_preserves_locally_before_its_delete(self):
  self.hub.inventories[self.b['node_id']]=self.watch_copy;self.hub.inventories[self.b['node_id']]['archive_wallets']=['historical_container'];self.hub.caps[self.b['node_id']]={CAPABILITY}
  key=self.delivered();self.preserve(key);self.assertIsNotNone(self.deletion(key,self.a['node_id']));self.assertIsNone(self.deletion(key,self.b['node_id']))
  child=next(k for k,r in self.hub.read()['requests'].items() if r.get('root_hash')==key and r['node_id']==self.b['node_id'] and r['proposal']['phase']=='preserve');self.exchange(self.b,caps=[CAPABILITY])
  with self.hub.edit() as state:result(state,self.b['node_id'],self.proof(child))
  advance(self.hub);self.assertIsNotNone(self.deletion(key,self.b['node_id']))
 def test_explicit_restart_after_safe_cancel_uses_fresh_operation(self):
  from wallet_retirement import cancel
  key=self.request()['request_hash'];cancel(self.hub,self.user,key,True);new=self.request()['request_hash'];self.assertNotEqual(key,new);self.assertNotEqual(self.hub.read()['requests'][key]['proposal']['operation_id'],self.hub.read()['requests'][new]['proposal']['operation_id'])
for name in previous.Retirement.__dict__:
 if name.startswith('test_') and name not in Late.__dict__:setattr(Late,name,None)
