"""Expired public evidence must not erase outstanding paired sync invitations."""
import copy,json,os,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
from test_build8 import Setup
from descriptor_store import clean_wallet
from test_node_loading import public_wallet
from wallet_sync_queue import schedule
from agent_hub import digest

class ThreeAgentVisibility(Setup):
 def test_reconnect_after_expiry_restores_all_eleven_rows_to_both_actual_agent_stores(self):
  source=os.environ.get('WALLABY_AGENT_SOURCE')
  if not source:self.fail('WALLABY_AGENT_SOURCE is required for the companion integration test.')
  third=self.register('02:80:00:00:00:03','third');nodes=(self.a,self.b,third);self.hub.refresh_inventory=lambda *a:None
  wallets=[]
  for i in range(11):
   w=clean_wallet(public_wallet('Fixture '+str(i),100+i));wallets.append({'name':w['name'],'id':w['id'],'raw':w['raw'],'private_keys_enabled':False,'scanning':False})
  self.hub.inventories[self.a['node_id']]={'complete':True,'wallets':wallets}
  for n in nodes[1:]:self.hub.inventories[n['node_id']]={'complete':True,'wallets':[]}
  for i,n in enumerate(nodes):self.exchange(n,caps=['wallet_transfer'],node_info={'ipv6':'2001:db8::'+str(i+1)})
  def queues():
   replies=[self.exchange(n,caps=['wallet_transfer']) for n in nodes]
   return [next(p for p in reply['proposals'] if p['kind']=='wallet_sync_queue') for reply in replies],replies
  before,_=queues();pairs=copy.deepcopy(self.hub.transfers.pairs(self.hub.read()));self.assertEqual(len(pairs),22)
  with self.hub.edit() as state:
   for row in state['requests'].values():
    if row['direction']=='send':row['signed_preview']={'preview':{'generated_at':int(time.time())-3600,'transfer_id':row['transfer_id']}}
   schedule(self.hub.transfers,state)
  after,replies=queues()
  self.assertEqual([q['requests'] for q in before],[q['requests'] for q in after]);self.assertTrue(all(any(p['kind']=='wallet_transfer_preview' for p in r['proposals']) for r in replies[1:]))
  # Replay the old-build empty withdrawal then the corrected restored snapshot
  # through the real Agent's persistent queue code; no wallet operation is mocked
  # as approved and no private file is transferred by this visibility test.
  value=[]
  for n,q in zip(nodes,after):
   value.append({'node':n['node_id'],'snapshots':[q,{'revision':q['revision']+1,'requests':[]},{'revision':q['revision']+2,'requests':q['requests']}]})
  file=Path(self.tmp.name)/'queues.json';file.write_text(json.dumps(value));out=json.loads(subprocess.check_output([sys.executable,str(Path(__file__).with_name('agent_sync_visibility_runner.py')),source,str(file)]))
  self.assertEqual([r['counts'][-1]['rows'] for r in out],[22,11,11]);self.assertEqual([r['counts'][-1]['send'] for r in out],[22,0,0]);self.assertEqual([r['counts'][1]['rows'] for r in out],[0,0,0])
  state=self.hub.read();self.assertEqual(set(pairs),set(self.hub.transfers.pairs(state)));self.assertTrue(all(r['state']=='pending' for r in state['requests'].values()));self.assertEqual({r['proposal']['preview_challenge'] for r in state['requests'].values()},{r['proposal']['preview_challenge'] for pair in pairs.values() for r in pair})
 def test_expired_evidence_does_not_override_disabled_policy_or_offline_peer(self):
  from test_build31 import StalledReviews
  case=StalledReviews('test_visible_stale_queue_remains_available_without_restarting_a_transfer');case.setUp();self.addCleanup(case.doCleanups);case.expire()
  with patch.object(case.hub.permissions,'permits',return_value=False):
   with case.hub.edit() as state:schedule(case.hub.transfers,state)
   self.assertFalse(case.hub.read()['nodes'][case.b['node_id']]['sync_snapshot'])

import test_build9 as previous
class RetainedSignedReviews(previous.PreviewTests):
 def test_old_original_snapshot_reaches_real_receiver_without_reset_or_replacement(self):
  source=os.environ['WALLABY_AGENT_SOURCE'];v=copy.deepcopy(self.preview);v['generated_at']=int(time.time())-86400;payload=self.signed_preview(v);self.send(payload)
  wire=next(p for p in self.exchange(self.b,caps=['wallet_transfer'])['proposals'] if p['kind']=='wallet_transfer_preview');self.assertEqual(wire['preview'],v)
  data={'proposal':self.target,'payload':{k:wire[k] for k in ('preview','public_key','signature')}};file=Path(self.tmp.name)/'retained.json';file.write_text(json.dumps(data));result=json.loads(subprocess.check_output([sys.executable,str(Path(__file__).with_name('agent_preview_runner.py')),source,'receive',str(file)]));self.assertEqual(result['stored'],data['payload'])
  self.assertEqual(set(self.hub.transfers.pairs(self.hub.read())),{self.source['transfer_id']});self.assertTrue(all(r['state']=='pending' for r in self.hub.read()['requests'].values()))
 def test_future_dated_review_is_rejected_even_when_bound(self):
  v=copy.deepcopy(self.preview);v['generated_at']=int(time.time())+3600
  with self.assertRaisesRegex(ValueError,'timestamp'):self.send(self.signed_preview(v))
