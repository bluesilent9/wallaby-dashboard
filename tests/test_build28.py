"""Discovery unlock, bounded ancestry and grouped historical sync regressions."""
import copy,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_build8 import Setup
from test_build6 import CoreFixture
from test_build7 import addresses,fixed
from descriptor_store import clean_wallet,DescriptorStore
from descriptor_snapshot import history_ready
from descriptor_chain import scan,HistoryClient
from node_chain import configure
from extinct_sync import containers
from vendor.embit.transaction import Transaction,TransactionInput,TransactionOutput
from transaction_workflows import WorkflowStore
import test_build21_revision as discovery

class EarlyDiscovery(unittest.TestCase):
 setUp=discovery.Discovery.setUp
 def test_public_catalog_and_notification_exist_before_history_finishes(self):
  def blocked_scan(*args):
   catalog=DescriptorStore(self.config).read();self.assertEqual(len(catalog['wallets']),2);self.assertFalse(history_ready(self.config,catalog));self.assertEqual(WorkflowStore(self.config).notifications(self.uid)['unread_count'],1)
   from launch_wallaby_bitcoin_dashboard import Session
   session=Session(Path(self.tmp.name)/'runtime',self.config);session.user_id=self.uid
   self.assertTrue(session.access()['descriptors_ready']);self.assertFalse(session.access()['descriptor_history_ready']);self.assertFalse(session.access()['ownership_ready']);raise ValueError('History intentionally unavailable')
  with patch('background_discovery.NodeWalletLoader.collect',return_value=self.core.wallets),patch('background_discovery.scan',side_effect=blocked_scan):
   with self.assertRaisesRegex(ValueError,'intentionally unavailable'):self.service.cycle()
  self.assertTrue(DescriptorStore(self.config).summary()['ready']);self.assertFalse(history_ready(self.config,DescriptorStore(self.config).read()))

class HistoryReads(unittest.TestCase):
 def test_hinted_reads_are_balanced_and_skip_raw_failures(self):
  rpc=CoreFixture();configure(rpc,{'wallets':rpc.wallets});result=scan({'wallets':rpc.wallets},HistoryClient(None),lambda _:None);self.assertEqual(result['transactions'][rpc.send.txid().hex()]['fee_sats'],1000);self.assertEqual({n for n,m,p,w in rpc.calls if m=='gettransaction'},{'node0','node1'});self.assertFalse([c for c in rpc.calls if c[1]=='getrawtransaction'])
 def receipt(self,n):
  rpc=CoreFixture();wallet=copy.deepcopy(rpc.wallets[0]);tx=Transaction(vin=[TransactionInput((i+100).to_bytes(32,'big'),0) for i in range(n)],vout=[TransactionOutput(100000,rpc.fund.vout[0].script_pubkey)]);ident=tx.txid().hex();rpc.raws={ident:tx};rpc.meta={ident:('a'*64,1)};wallet['node_history']=[{'txid':ident,'vout':0,'category':'receive','amount':'.001','confirmations':10,'blockhash':'a'*64}];rpc.wallets=[wallet];return rpc,wallet,ident
 def test_large_receipt_skips_external_parent_fanout(self):
  rpc,wallet,ident=self.receipt(300);configure(rpc,{'wallets':[wallet]});snapshot=scan({'wallets':[wallet]},HistoryClient(None),lambda _:None);self.assertEqual(len(snapshot['transactions'][ident]['inputs']),300);self.assertFalse(snapshot['transactions'][ident]['fee_known']);self.assertEqual(len([c for c in rpc.calls if c[1]=='gettransaction']),1);self.assertFalse([c for c in rpc.calls if c[1]=='getrawtransaction'])
 def test_wallet_description_does_not_poison_full_trace_cache(self):
  rpc,wallet,ident=self.receipt(2);chain=configure(rpc,{'wallets':[wallet]});chain.wallet_tx(ident);self.assertNotIn(ident,chain.txs);before=len(rpc.calls);chain.tx(ident);self.assertGreater(len(rpc.calls),before);self.assertIn(ident,chain.txs)
 def test_wallet_failure_tries_other_replica(self):
  rpc=CoreFixture();original=rpc.call
  def call(node,method,params=None,wallet=None):
   if node=='node0' and method=='gettransaction':raise ValueError('Unavailable')
   return original(node,method,params,wallet)
  rpc.call=call;chain=configure(rpc,{'wallets':rpc.wallets});self.assertEqual(chain.transaction(rpc.fund.txid().hex()).txid(),rpc.fund.txid())

class ExtinctSync(Setup):
 def children(self):
  values=[]
  for i,address in enumerate(addresses()[:2]):
   name='Earlier '+str(i);w=clean_wallet({'wallet_name':name,'descriptors':[fixed(address)]});values.append({'id':w['id'],'name':'any_local_container','raw':w['raw'],'private_keys_enabled':False,'extinct':True,'address_labels':{address:{'label':name,'lost_utc':None}}})
  return values
 def prepare(self,children):
  self.hub.inventories[self.a['node_id']]={'wallets':children,'complete':True};self.hub.inventories[self.b['node_id']]={'wallets':[],'complete':True};self.exchange(self.a,caps=['wallet_transfer'],node_info={'ipv6':'fd12::1'});return self.exchange(self.b,caps=['wallet_transfer'],node_info={'ipv6':'fd12::2'})
 def test_children_form_one_watch_only_request_and_stay_separate_for_ownership(self):
  self.prepare(self.children());rows=[r for r in self.hub.read()['requests'].values() if not r.get('superseded_container')];self.assertEqual(len(rows),2);r=next(r for r in rows if r['direction']=='receive');self.assertEqual(r['public_wallet']['wallet_name'],'Extinct');self.assertEqual(len(r['public_wallet']['descriptors']),2);self.assertFalse(r['source_has_private_keys']);self.assertEqual(r['subwallets'],['Earlier 0','Earlier 1']);self.assertEqual(r['proposal']['transfer_version'],2);self.assertEqual(len(self.hub.inventories[self.a['node_id']]['wallets']),2)
 def test_partial_existing_container_is_not_mistaken_for_complete(self):
  children=self.children();self.hub.inventories[self.a['node_id']]={'wallets':children,'complete':True};self.hub.inventories[self.b['node_id']]={'wallets':children[:1],'complete':True};self.exchange(self.a,caps=['wallet_transfer'],node_info={'ipv6':'fd12::1'});self.exchange(self.b,caps=['wallet_transfer'],node_info={'ipv6':'fd12::2'});incoming=[r for r in self.hub.read()['requests'].values() if r.get('direction')=='receive' and r['node_id']==self.b['node_id']];self.assertEqual(len(incoming),1);self.assertEqual(len(incoming[0]['public_wallet']['descriptors']),2)
 def test_complete_copy_needs_no_transfer_despite_different_names(self):
  children=self.children();self.hub.inventories[self.a['node_id']]={'wallets':children,'complete':True};other=copy.deepcopy(children)
  for w in other:w['name']='another name';w['raw']['wallet_name']='different child label'
  self.hub.inventories[self.b['node_id']]={'wallets':other,'complete':True};self.exchange(self.a);self.assertFalse(self.hub.read()['requests'])
 def test_old_child_rows_withdraw_without_invented_completion(self):
  children=self.children();self.prepare(children[:1]);old=self.hub.read()['requests'];self.hub.inventories[self.a['node_id']]['wallets']=children;self.exchange(self.a,caps=['wallet_transfer']);state=self.hub.read();self.assertTrue(all(state['requests'][k]['superseded_container'] for k in old));self.assertTrue(all(state['requests'][k]['state']=='pending' for k in old));self.assertEqual(len([r for r in state['requests'].values() if r.get('queue_visible')]),2)
 def test_private_historical_import_is_rejected(self):
  child=self.children()[0];child['private_keys_enabled']=True
  with self.assertRaises(ValueError):containers([child])
 def test_mismatch_diagnostic_names_public_capability_and_version(self):
  with patch.object(self.registry.diagnostic_log,'append') as log:
   with self.assertRaisesRegex(ValueError,'Unsupported Agent capabilities'):self.exchange(self.a,caps=['unknown_feature'])
  self.assertIn('unknown_feature',str(log.call_args));self.assertIn('Dashboard V1 Build 28',str(log.call_args))

 def signed_old_preview(self):
  import base64,time,subprocess
  from wallet_preview import canonical
  from agent_hub import digest
  children=self.children();self.prepare(children[:1]);self.exchange(self.a,caps=['wallet_transfer'])
  old=next(r for r in self.hub.read()['requests'].values() if r['direction']=='send');p=old['proposal']
  preview={'version':2,'vaults':[],'address_labels':[{'address':next(iter(children[0]['address_labels'])),'label':'Earlier 0','is_change':False}],'transfer_id':p['transfer_id'],'challenge':p['preview_challenge'],'sender_ipv6':p['node_ipv6'],'receiver_ipv6':p['peer']['ipv6'],'public_wallet':copy.deepcopy(p['public_wallet']),'chain':'main','funded_outputs':[],'node_tip':{'height':100,'hash':'a'*64},'generated_at':int(time.time())-3600}
  key=Path(self.tmp.name)/'identity';subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True,capture_output=True);file=Path(self.tmp.name)/'preview';file.write_bytes(canonical(preview));subprocess.run(['ssh-keygen','-Y','sign','-f',str(key),'-n','wallaby-wallet-preview-v1',str(file)],check=True,capture_output=True)
  payload={'request_hash':digest(p),'preview':preview,'public_key':' '.join(key.with_suffix('.pub').read_text().split()[:2]),'signature':base64.b64encode(file.with_suffix('.sig').read_bytes()).decode()};self.hub.inventories[self.a['node_id']]['wallets']=children;return payload,old
 def test_expired_obsolete_preview_is_acknowledged_and_new_group_delivered(self):
  payload,old=self.signed_old_preview();item=self.item('wallet_transfer_preview',payload);reply=self.exchange(self.a,[item],caps=['wallet_transfer']);self.assertIn(item['id'],reply['accepted']);queue=next(p for p in reply['proposals'] if p['kind']=='wallet_sync_queue');self.assertEqual(len(queue['requests']),1);self.assertEqual(len(queue['requests'][0]['public_wallet']['descriptors']),2);stored=next(r for r in self.hub.read()['requests'].values() if r['direction']=='send' and r['transfer_id']==old['transfer_id']);self.assertEqual(stored['state'],'pending');self.assertNotIn('signed_preview',stored)
 def test_forged_obsolete_preview_is_not_silently_accepted(self):
  payload,old=self.signed_old_preview();payload['preview']['node_tip']['height']+=1;item=self.item('wallet_transfer_preview',payload)
  with self.assertRaisesRegex(ValueError,'signature'):self.exchange(self.a,[item],caps=['wallet_transfer'])
  self.assertNotIn(self.a['node_id']+':'+item['id'],self.hub.read()['receipts'])
 def test_late_operation_result_is_preserved_after_notice_withdrawal(self):
  from agent_hub import digest
  payload,old=self.signed_old_preview();self.exchange(self.a,[self.item('wallet_transfer_preview',payload)],caps=['wallet_transfer']);receiver=next(r for r in self.hub.read()['requests'].values() if r['transfer_id']==old['transfer_id'] and r['direction']=='receive');item=self.item('wallet_transfer_result',{'request_hash':digest(receiver['proposal']),'status':'recovery_required'});self.exchange(self.b,[item],caps=['wallet_transfer']);saved=self.hub.read();row=next(r for r in saved['requests'].values() if r['transfer_id']==old['transfer_id'] and r['direction']=='receive');self.assertEqual(row['state'],'recovery_required');self.assertTrue(any(t['state']=='recovery_required' for t in self.hub.transfers.summaries(saved,self.b['node_id'])))

 def test_retry_partial_archive_keeps_full_group_and_children(self):
  import time
  children=self.children();self.prepare(children);self.exchange(self.a,caps=['wallet_transfer']);state=self.hub.read();transfer=next(iter(state['requests'].values()))['transfer_id']
  with self.hub.edit() as saved:
   for r in saved['requests'].values():r['state']='error'
  self.hub.inventories[self.b['node_id']]['wallets']=children[:1]
  for node in (self.a,self.b):self.hub.inventory_times[node['node_id']]=time.monotonic()
  result=self.hub.transfers.retry(transfer,True);rows=[r for r in self.hub.read()['requests'].values() if r.get('transfer_id')==result['transfer_id']];self.assertEqual(len(rows),2);self.assertTrue(all(len(r['public_wallet']['descriptors'])==2 for r in rows));self.assertTrue(all(len(r['subwallets'])==2 for r in rows))
