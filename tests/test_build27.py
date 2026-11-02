"""Public-policy refresh, receive-only destinations and durable archive boundaries."""
import copy,json,sys,tempfile,time,unittest
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_build8 import Setup
from builder_destinations import public_rows,Destinations,validate_record
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from descriptor_store import clean_wallet,DescriptorStore
from preferences import resource_id
from fiat_amount import convert
from builder_workspace import BuilderWorkspace
from archive_queue import ArchiveQueue,validate as validate_queue
from agent_hub import digest
from live_wallet_traits import disc_flags
from vault_refresh import start,cancel,finish,remove
from wallet_policy_updates import schedule,destination_ready

def pair(wallet):
    w=copy.deepcopy(wallet);row=w['raw']['descriptors'][0];body=row['desc'].split('#')[0].replace('/0/*','/1/*');w['raw']['descriptors'].append(dict(row,desc=body+'#'+checksum(body),internal=True));return clean_wallet(w['raw'])
class Fiat(unittest.TestCase):
 def test_shortest_rounding_bounded_and_satoshis_only(self):
  for aud in ('1','15','5000','12345.67','1000000000'):
   price=Decimal('109835.40');r=convert(aud,{'aud_per_btc':str(price),'observed_utc':'2026-11-02T00:00:00Z'});bound=max(min(Decimal(aud)*Decimal('.0001'),Decimal('.50')),price*Decimal('.000000005'))
   self.assertLessEqual(abs(Decimal(r['difference_aud'])),bound);self.assertEqual(Decimal(r['btc'])*100000000,int(r['sats']))
 def test_invalid_and_tiny_inputs(self):
  for v in ('0','-1','nan','Infinity','not-a-number','0.00000000001'):
   with self.assertRaises(ValueError):convert(v,{'aud_per_btc':'100000'})

class DestinationsTest(Setup):
 def setUp(self):
  super().setUp();self.wallet=pair(self.wallet);self.wallet['node_sources']=[{'node_id':self.a['node_id'],'hostname':'first','wallet_name':self.wallet['name']}]
  DescriptorStore(self.config).replace_node_wallets([self.wallet]);self.rid=resource_id('descriptor',self.wallet['id'])
  for inv in self.hub.inventories.values():inv['wallets']=[dict(self.node_wallet,raw=self.wallet['raw'],private_keys_enabled=True)]
  self.registry.rpc.call=lambda *a,**k:{'state':'succeeded','result':[] if a[1]=='listreceivedbyaddress' else self.wallet['raw']}
 def test_repeated_receive_cards_have_unique_reserved_indices_zero_balance(self):
  store=Destinations(self.config);a=store.create(self.registry,self.user,self.rid);b=store.create(self.registry,self.user,self.rid);self.assertEqual(b['index'],a['index']+1);self.assertNotEqual(a['address'],b['address'])
  c=store.enrich({'wallets':[{'id':self.rid,'btc':'1','can_send':True}]},[a['id'],b['id']]);self.assertEqual(len(c['wallets']),3)
  for w in c['wallets'][1:]:self.assertFalse(w['can_send']);self.assertEqual(w['btc'],'0')
 def test_corrupted_reserved_address_cannot_redirect_a_payment(self):
  d=Destinations(self.config).create(self.registry,self.user,self.rid);d['address']='attacker';
  with self.assertRaises(ValueError):validate_record(d)
 def test_public_policy_preserves_keys_and_increases_height(self):
  rows=public_rows(self.wallet,1000000);w={'raw':{'wallet_name':'v','descriptors':rows}};nextrows=public_rows(w,1000100);self.assertIn('after(1000100)',nextrows[0]['desc']);self.assertEqual([str(k) for k in Descriptor.from_string(rows[0]['desc']).keys],[str(k) for k in Descriptor.from_string(nextrows[0]['desc']).keys])
  with self.assertRaises(ValueError):public_rows(w,1000000)
 def transaction(self):
  store=Destinations(self.config);d=store.create(self.registry,self.user,self.rid);w=BuilderWorkspace(self.config);row=w.start(self.user,'transaction');w.save(self.user,'transaction',{'id':row['id'],'revision':row['revision'],'draft':{'nodes':[{'id':'network-fee','x':0,'y':0},{'id':self.rid,'x':100,'y':0},{'id':d['id'],'x':400,'y':0}],'edges':[{'from':self.rid,'to':d['id'],'amount_sats':'50000','fee_rate':'0'}]}});return w,d
 def test_linked_cancel_preserves_original_transaction(self):
  w,d=self.transaction();before=w.read()['workspaces']['transaction'];start(self.hub,self.user,self.rid,d['id']);cancel(self.hub,self.user);self.assertEqual(w.read()['workspaces']['transaction'],before)
 def test_linked_refresh_preserves_payment_and_no_new_private_ceremony(self):
  w,d=self.transaction();start(self.hub,self.user,self.rid,d['id']);result=finish(self.hub,self.user,1000100);tx=w.read()['workspaces']['transaction'];self.assertFalse(tx['operations']);self.assertEqual(tx['draft']['edges'][0]['amount_sats'],'50000');self.assertEqual(tx['draft']['edges'][0]['to'],result['destination']);self.assertNotIn('wallet',w.read()['workspaces']);p=next(r['proposal'] for r in self.hub.read()['requests'].values() if r['kind']=='wallet_policy_refresh');self.assertEqual(p['kind'],'wallet_policy_refresh');self.assertNotIn('private',str(p));self.assertEqual(len(p['previous_descriptors']),2)
  remove(self.hub,self.user,result['destination']);self.assertNotEqual(w.read()['workspaces']['transaction']['draft']['edges'][0]['to'],result['destination']);self.assertEqual(w.read()['workspaces']['transaction']['draft']['edges'][0]['amount_sats'],'50000')
 def test_prepared_or_busy_builder_cannot_replace_policy(self):
  w,d=self.transaction();w.start(self.user,'wallet')
  with self.assertRaises(ValueError):start(self.hub,self.user,self.rid,d['id'])
 def test_locked_name_and_signers_cannot_be_changed(self):
  w,d=self.transaction();start(self.hub,self.user,self.rid,d['id']);row=w.owned(self.user,'wallet');draft=copy.deepcopy(row['draft']);draft['name']='Replacement'
  with self.assertRaises(ValueError):w.save(self.user,'wallet',{'id':row['id'],'revision':row['revision'],'draft':draft})
 def test_disc_trait_removed_when_any_persistent_replica_visible(self):
  for inv in self.hub.inventories.values():inv['wallets'][0]['signing_only']=True
  self.assertTrue(disc_flags({'wallets':[self.wallet]},self.registry)[self.wallet['id']]);self.hub.inventories[self.b['node_id']]['wallets'][0].update(signing_only=False,private_keys_enabled=False);self.assertFalse(disc_flags({'wallets':[self.wallet]},self.registry)[self.wallet['id']])

 def test_resource_migration_preserves_reserved_script_and_draft_amount(self):
  from builder_identity import migrate,resolve
  work,d=self.transaction();oldid=self.wallet['id'];newid='f'*64;newresource=resource_id('descriptor',newid);migrate(self.config,{oldid:newid});after=work.read()['workspaces']['transaction'];self.assertEqual(after['draft']['edges'][0]['from'],newresource);self.assertEqual(after['draft']['edges'][0]['amount_sats'],'50000');record=Destinations(self.config).get(d['id']);self.assertEqual(record['address'],d['address']);self.assertEqual(record['base'],newresource);self.assertEqual(resolve(self.config,self.rid),newresource)
 def test_policy_lineage_never_merges_an_unapproved_same_key_policy(self):
  from policy_lineage import related
  original=self.wallet['raw'];fresh={'descriptors':public_rows(self.wallet,1000000)};self.assertFalse(related(self.hub,original,fresh));store=Destinations(self.config);dest=store.create(self.registry,self.user,self.rid,1000000);schedule(self.hub,dest['id']);self.assertFalse(related(self.hub,original,fresh))
  with self.hub.edit() as s:
   for r in s['requests'].values():
    if r['kind']=='wallet_policy_refresh':r['state']='complete'
  self.assertTrue(related(self.hub,original,fresh))
 def test_output_binding_carries_independent_policy_hash_and_exact_amount(self):
  from agent_transactions import output_bindings
  from node_wallets import NodeWalletLoader
  dest=Destinations(self.config).create(self.registry,self.user,self.rid,1000000);catalog=Destinations(self.config).enrich({'wallets':[{'id':self.rid,'btc':'1'}]},[dest['id']]);w=next(x for x in catalog['wallets'] if x['id']==dest['id']);self.assertEqual(len(w['output_policy_hash']),64)
  binding={'policy_hash':w['output_policy_hash'],'descriptor':w['output_descriptor'],'index':w['output_index'],'address':w['address'],'amount_sats':'50000'};self.assertEqual(output_bindings(self.hub,self.a['node_id'],{'output_policies':[binding]}),[])
  self.hub.inventories[self.a['node_id']]['wallets'].append({'raw':{'descriptors':dest['descriptors']}});self.assertEqual(output_bindings(self.hub,self.a['node_id'],{'output_policies':[binding]}),[binding])
 def test_old_address_history_does_not_replace_current_receiving_address(self):
  from address_book import ordered_history
  from vendor.embit.networks import NETWORKS
  desc=Descriptor.from_string(self.wallet['raw']['descriptors'][0]['desc']);a=desc.derive(0).address(NETWORKS['main']);b=desc.derive(1).address(NETWORKS['main']);latest=[{'address':b,'observed_utc':'2026-01-01'}];history=ordered_history([{'address':a,'observed_utc':'2020-01-01'},latest[0]],latest);self.assertEqual([r['address'] for r in history],[a,b]);self.assertEqual(latest[0]['address'],b)


class Archives(Setup):
 def setUp(self):
  super().setUp();self.queue=ArchiveQueue(self.hub);self.hub.caps[self.a['node_id']]={'wallet_archive_queue_v1'};self.hub.rpc=lambda n,m,p=None,w=None: {'private_keys_enabled':True,'walletname':self.wallet['name']} if m=='getwalletinfo' else self.wallet['raw']
 def create(self):
  option=self.queue.options(self.rid)[0];self.queue.create(self.user,self.rid,[option['id']]);return self.queue.proposal(self.a['node_id'])
 def test_disconnected_agent_reconnect_receives_authoritative_withdrawal(self):
  p=self.create();ident=p['requests'][0]['request_id'];self.queue.cancel(self.user,ident);q=self.queue.proposal(self.a['node_id']);self.assertEqual(p['queue_id'],q['queue_id']);self.assertGreater(q['revision'],p['revision']);self.assertEqual(q['requests'],[]);validate_queue(self.queue.read())
 def test_watch_and_disc_sources_not_archivable(self):
  self.assertEqual(len(self.queue.options(self.rid)),1)
  self.hub.inventories[self.a['node_id']]['wallets'][0]['signing_only']=True;self.assertEqual(self.queue.options(self.rid),[])
 def test_unsigned_or_wrong_agent_receipt_rejected(self):
  p=self.create();ident=p['requests'][0]['request_id'];message={'version':1,'queue_id':p['queue_id'],'request_id':ident,'status':'complete','readback_verified':True,'image_sha256':'a'*64}
  with self.assertRaises(ValueError):self.queue.accept(self.a['node_id'],message)
 def test_replacement_tasks_can_be_withdrawn_but_not_confirmed_in_dashboard(self):
  p={'readback_verified':True,'image_sha256':'a'*64,'replacements':[{'archive_id':'b'*64,'image_sha256':'a'*64,'members':['x'],'readback_verified':True,'old_policies_retained':True}]};self.queue.replacements(self.a['node_id'],p,{'signed':'Agent evidence'});task=self.queue.proposal(self.a['node_id'])['requests'][0];self.assertEqual(task['kind'],'destroy_disc');self.assertNotIn('confirmed',task);self.queue.cancel(self.user,task['request_id']);self.assertEqual(self.queue.proposal(self.a['node_id'])['requests'],[])
 def test_incomplete_old_disc_coverage_cannot_create_destruction_task(self):
  with self.assertRaises(ValueError):self.queue.replacements(self.a['node_id'],{'readback_verified':True,'image_sha256':'a'*64,'replacements':[{'archive_id':'b'*64,'members':[]}]},{})

class SignedProtocol(Setup):
 def setUp(self):
  super().setUp()
  import subprocess,base64
  self.key=Path(self.tmp.name)/'identity';subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','','-f',str(self.key)],check=True);self.pub=' '.join(self.key.with_suffix('.pub').read_text().split()[:2])
  with self.hub.wallet_builder.edit() as s:s['identities'][self.a['node_id']]={'public_key':self.pub}
 def sign(self,p):
  import subprocess,base64
  path=Path(self.tmp.name)/'message';path.write_text(json.dumps(p,sort_keys=True,separators=(',',':'),ensure_ascii=True));path.with_suffix('.sig').unlink(missing_ok=True);subprocess.run(['ssh-keygen','-Y','sign','-f',str(self.key),'-n','wallaby-wallet-setup-v1',str(path)],check=True,capture_output=True);return {'message':p,'public_key':self.pub,'signature':base64.b64encode(path.with_suffix('.sig').read_bytes()).decode()}
 def test_actual_agent_capability_list_is_accepted(self):
  self.hub.refresh_inventory=lambda *a:None
  result=self.exchange(self.a,caps=['node_identity','partial_signing','broadcast','wallet_transfer','wallet_retirement','wallet_retirement_v2','wallet_retirement_cancel_v1','builder_cancellation_v1','wallet_archive_queue_v1','wallet_policy_refresh_v1','archive_inventory_v1'])
  self.assertTrue(any(p['kind']=='wallet_archive_queue' for p in result['proposals']))
 def test_signed_archive_result_generates_only_agent_destruction_task_and_deduplicates(self):
  queue=ArchiveQueue(self.hub);self.hub.caps[self.a['node_id']]={'wallet_archive_queue_v1'};self.hub.rpc=lambda n,m,p=None,w=None:{'private_keys_enabled':True,'walletname':self.wallet['name']} if m=='getwalletinfo' else self.wallet['raw'];option=queue.options(self.rid)[0];queue.create(self.user,self.rid,[option['id']]);proposal=queue.proposal(self.a['node_id']);task=proposal['requests'][0]
  body={'version':1,'queue_id':proposal['queue_id'],'request_id':task['request_id'],'status':'complete','readback_verified':True,'image_sha256':'a'*64,'archive_members':['c'*64],'replacements':[{'archive_id':'b'*64,'image_sha256':'a'*64,'members':['c'*64],'readback_verified':True,'old_policies_retained':True}]};message={**body,'signed_receipt':self.sign(body)};queue.accept(self.a['node_id'],message);queue.accept(self.a['node_id'],message);pending=queue.proposal(self.a['node_id'])['requests'];self.assertEqual(len(pending),1);self.assertEqual(pending[0]['kind'],'destroy_disc');self.assertEqual(pending[0]['replacement_receipt'],message['signed_receipt']);validate_queue(queue.read())
  wrong=copy.deepcopy(message);wrong['image_sha256']='d'*64
  with self.assertRaises(ValueError):queue.accept(self.a['node_id'],wrong)

class PolicyCoinCoverage(unittest.TestCase):
 def chain(self,fail=None,duplicate=False):
  from node_chain import NodeChain
  self.old='wpkh(old-key)';self.new='wpkh(new-key)';self.coin={'txid':'a'*64,'vout':0,'scriptPubKey':'0014'+'00'*20,'amount':'0.01'}
  inventories={'n':{'complete':True,'wallets':[{'name':'old','raw':{'descriptors':[{'desc':self.old}]}},{'name':'new','raw':{'descriptors':[{'desc':self.new}]}}]}}
  class RPC:
   registry=SimpleNamespace(hub=SimpleNamespace(inventories=inventories))
   def nodes(s):return [{'id':'n'}]
   def call(s,node,method,params,wallet):
    if wallet==fail:raise ValueError('Unavailable')
    return [copy.deepcopy(self.coin)] if wallet=='new' or duplicate else []
  wallet={'id':'w','name':'logical','raw':{'descriptors':[{'desc':self.old},{'desc':self.new}]},'node_sources':[{'node_id':'n','wallet_name':'old'},{'node_id':'n','wallet_name':'new'}]}
  return NodeChain(RPC(),{'wallets':[wallet]}),wallet
 def test_empty_old_monitor_does_not_hide_funds_in_refreshed_monitor(self):
  chain,w=self.chain();self.assertEqual(chain.wallet_unspent(w),[self.coin])
 def test_identical_utxos_from_monitor_replicas_are_counted_once(self):
  chain,w=self.chain(duplicate=True);self.assertEqual(len(chain.wallet_unspent(w)),1)
 def test_missing_generation_is_not_returned_as_partial_or_zero_balance(self):
  from node_chain import NodeDataUnavailable
  chain,w=self.chain(fail='new')
  with self.assertRaisesRegex(NodeDataUnavailable,'not a zero balance'):chain.wallet_unspent(w)

class VaultSpendWarnings(unittest.TestCase):
 def test_locked_and_mixed_balances_and_manual_maximum(self):
  from vault_spending import decision,BLOCKED,PARTIAL
  locked={'available_sats':'0','locked_sats':'90000'}
  self.assertTrue(decision(locked,'connection')['blocked']);self.assertEqual(decision(locked,'connection')['warning'],BLOCKED)
  mixed={'available_sats':'10000','locked_sats':'90000'}
  self.assertFalse(decision(mixed,'connection')['blocked']);self.assertIsNone(decision(mixed,'connection')['warning'])
  self.assertEqual(decision(mixed,'maximum')['warning'],PARTIAL)
  self.assertEqual(decision(mixed,'amount',9000,1000)['warning'],PARTIAL)
  self.assertFalse(decision(mixed,'amount',9000,1000)['exceeds_available'])
  self.assertTrue(decision(mixed,'amount',9001,1000)['exceeds_available'])
  self.assertIsNone(decision(mixed,'amount',8000,1000)['warning'])
  self.assertIsNone(decision({'available_sats':'10000','locked_sats':'0'},'maximum')['warning'])
 def test_notice_is_immediately_read_timestamped_and_deletable(self):
  from transaction_workflows import WorkflowStore
  from datetime import datetime
  with tempfile.TemporaryDirectory() as tmp:
   store=WorkflowStore(Path(tmp)/'settings.json');n=store.vault_warning('any-user','Vault funds remain locked.')
   self.assertTrue(n['read']);self.assertTrue(n['dismissible']);self.assertEqual(n['target'],'builder');self.assertEqual(datetime.fromisoformat(n['created_utc']).utcoffset().total_seconds(),0)
   self.assertEqual(store.notifications('any-user')['unread_count'],0)
   self.assertEqual(store.update_notifications('any-user',n['id'])['notifications'],[])
