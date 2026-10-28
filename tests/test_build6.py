"""Node-only evidence, private worker transport and background priorities."""
import sys,unittest,tempfile,threading,json,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vendor.embit.transaction import Transaction,TransactionInput,TransactionOutput
from vendor.embit.script import Script
from descriptor_store import clean_wallet
from descriptor_policy import parse_policy
from test_node_loading import public_wallet
from node_chain import NodeChain,NodeDataUnavailable,configure
from descriptor_chain import HistoryClient,scan,wallet_rows
from worker import load_core
from fees import project,FeeService
from nodes import NodeRegistry
from agent_rpc import METHODS
from node_bridge import NodeBridge,WorkerRPC

class CoreFixture:
 def __init__(self):
    self.wallets=[clean_wallet(public_wallet('First',0)),clean_wallet(public_wallet('Second',1))];self.calls=[]
    scripts=[parse_policy(w['raw']['descriptors'][0]['desc'].split('#')[0])[0].derive(0).script_pubkey() for w in self.wallets]
    self.fund=Transaction(vin=[TransactionInput(b'\0'*32,0xffffffff,script_sig=Script(b'\x01\x01'))],vout=[TransactionOutput(100000,scripts[0])]);self.send=Transaction(vin=[TransactionInput(self.fund.txid(),0)],vout=[TransactionOutput(60000,scripts[1]),TransactionOutput(39000,scripts[0])])
    self.raws={t.txid().hex():t for t in [self.fund,self.send]};self.meta={self.fund.txid().hex():('a'*64,1),self.send.txid().hex():('b'*64,2)}
    for n,w in enumerate(self.wallets):
      w['node_sources']=[{'node_id':'node'+str(i),'hostname':'host'+str(i),'wallet_name':w['name']} for i in range(2)];w['node_history']=[]
      for txid,tx in self.raws.items():
        for index,o in enumerate(tx.vout):
          if o.script_pubkey.data==scripts[n].data:w['node_history'].append({'txid':txid,'vout':index,'category':'receive','amount':str(Decimal(o.value)/100000000),'confirmations':10,'blockhash':self.meta[txid][0]})
        if n==0 and tx is self.send:w['node_history'].append({'txid':txid,'vout':0,'category':'send','amount':'-.0006','confirmations':10,'blockhash':'b'*64})
 def nodes(self):return [{'id':'node0','hostname':'host0'},{'id':'node1','hostname':'host1'}]
 def call(self,node,method,params=None,wallet=None):
    params=params or [];self.calls.append((node,method,params,wallet))
    if method=='getblockchaininfo':return {'chain':'main','initialblockdownload':False}
    if method=='listwallets':return [w['name'] for w in self.wallets]
    if method=='getrawtransaction':raise NodeDataUnavailable('Pruned without txindex')
    if method=='gettransaction':
      t=self.raws.get(params[0]);owned=next(w for w in self.wallets if w['name']==wallet)
      if not t or not any(r['txid']==params[0] for r in owned['node_history']):raise NodeDataUnavailable('Not in wallet')
      return {'hex':t.serialize().hex(),'blockhash':self.meta[params[0]][0],'confirmations':10}
    if method=='getblockhash':return 'a'*64 if params[0]==1 else 'b'*64
    if method=='getblockheader':return {'hash':params[0],'height':1 if params[0]=='a'*64 else 2,'time':1700000000 if params[0]=='a'*64 else 1700000600,'confirmations':10}
    if method=='gettxout':return None
    raise AssertionError(method)

class NodeEvidence(unittest.TestCase):
 def test_pruned_wallet_raw_history_reconstructs_change_and_transfers_without_public_lookup(self):
    rpc=CoreFixture();catalog={'wallets':rpc.wallets};chain=configure(rpc,catalog)
    with patch('urllib.request.urlopen',side_effect=AssertionError('Public HTTP forbidden')):
      snapshot=scan(catalog,HistoryClient(None),lambda x:None);rows=wallet_rows(load_core(),snapshot)
    self.assertEqual(rows['First'][-1].balance,Decimal('.00039'));self.assertEqual(rows['Second'][-1].balance,Decimal('.0006'))
    self.assertEqual(sum(Decimal(w['pending_btc']) for w in snapshot['wallets']),0)
    self.assertEqual(snapshot['transactions'][rpc.send.txid().hex()]['fee_sats'],1000)
    self.assertEqual(chain.outspend(rpc.fund.txid().hex(),0)['txid'],rpc.send.txid().hex())
    used={n for n,m,p,w in rpc.calls if m=='gettransaction'};self.assertEqual(used,{'node0','node1'})
 def test_incomplete_core_send_cannot_be_misread_as_an_external_receipt(self):
    rpc=CoreFixture();wallet=rpc.wallets[0];fund=rpc.fund.txid().hex();wallet['node_history']=[r for r in wallet['node_history'] if r['txid']!=fund]
    catalog={'wallets':rpc.wallets};configure(rpc,catalog)
    with self.assertRaisesRegex(NodeDataUnavailable,'input evidence'):
      scan(catalog,HistoryClient(None),lambda x:None)
 def test_missing_pruned_history_and_unknown_spender_stop_explicitly(self):
    rpc=CoreFixture();chain=NodeChain(rpc,{'wallets':rpc.wallets})
    with self.assertRaisesRegex(NodeDataUnavailable,'No public explorer'):chain.raw('f'*64)
    with self.assertRaisesRegex(NodeDataUnavailable,'spending transaction is absent'):chain.outspend(rpc.send.txid().hex(),0)
 def test_txid_is_verified_before_accepting_core_hex(self):
    rpc=CoreFixture();rpc.raws['c'*64]=rpc.fund;rpc.meta['c'*64]=('a'*64,1);rpc.wallets[0]['node_history'].append({'txid':'c'*64});chain=NodeChain(rpc,{'wallets':rpc.wallets})
    with self.assertRaisesRegex(ValueError,'does not match'):chain.raw('c'*64)
 def test_fresh_unconfirmed_reply_replaces_stale_saved_confirmation_metadata(self):
    rpc=CoreFixture();original=rpc.call;txid=rpc.send.txid().hex()
    def call(node,method,params=None,wallet=None):
      if method=='getrawtransaction' and params[0]==txid:return {'hex':rpc.send.serialize().hex(),'txid':txid}
      return original(node,method,params,wallet)
    rpc.call=call;chain=NodeChain(rpc,{'wallets':rpc.wallets})
    self.assertFalse(chain.tx(txid)['status']['confirmed']);self.assertNotIn('blockhash',chain.metadata[txid])
 def test_worker_bridge_requires_secret_and_rejects_signing(self):
    with tempfile.TemporaryDirectory() as d:
      reg=NodeRegistry(Path(d)/'config.cfg');bridge=NodeBridge(reg);self.addCleanup(bridge.close);client=WorkerRPC(bridge.settings());self.assertEqual(client.nodes(),[])
      with self.assertRaisesRegex(ValueError,'Only public read'):client.call('node','walletprocesspsbt',['payload'])
      req=urllib.request.Request(client.url,b'{"method":"nodes"}',headers={'Content-Type':'application/json'})
      with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(req)
      self.assertEqual(err.exception.code,403)
 def test_queued_foreground_preempts_background_and_background_waits_for_running_work(self):
    with tempfile.TemporaryDirectory() as d:
      reg=NodeRegistry(Path(d)/'config.cfg');c=reg.register('02:00:00:00:00:19','fixture',{'version':1,'methods':list(METHODS)});rpc=reg.rpc;token=c['connection_token'];node=c['node_id']
      background=rpc.queue(node,'getmempoolinfo',priority=100);foreground=rpc.queue(node,'getblockchaininfo')
      self.assertEqual(rpc.poll(token,0)['commands'][0]['id'],foreground['id']);rpc.ack(token,foreground['id']);self.assertEqual(rpc.poll(token,0)['commands'],[])
      rpc.result(token,foreground['id'],result={});self.assertEqual(rpc.poll(token,0)['commands'][0]['id'],background['id'])
 def test_fee_service_does_not_query_node_when_an_ownership_job_is_active(self):
    service=FeeService(None,busy=lambda:True);view=service.snapshot();self.assertTrue(view['waiting']);self.assertFalse(service.refreshing);self.assertEqual(view['rates'],[])
 def test_projected_packages_keep_low_fee_parent_with_its_paying_child(self):
    entries={'parent':{'vsize':100,'fees':{'modified':'.00000001'},'depends':[]},'child':{'vsize':100,'fees':{'modified':'.00001999'},'depends':['parent']},'other':{'vsize':200,'fees':{'modified':'.00001'},'depends':[]}}
    blocks=project(entries,capacity=200);self.assertEqual(blocks[0]['tx_count'],2);self.assertEqual(blocks[0]['fees_sats'],2000);self.assertEqual(blocks[1]['fees_sats'],1000)
 def test_block_projection_conserves_fees_size_and_transaction_count(self):
    entries={str(i):{'vsize':100,'fees':{'modified':str(Decimal(i+1)/100000000)},'depends':[str(i-1)] if i%3 else []} for i in range(20)};blocks=project(entries,limit=20,capacity=300)
    self.assertEqual(sum(b['tx_count'] for b in blocks),20);self.assertEqual(sum(b['vsize'] for b in blocks),2000);self.assertEqual(sum(b['fees_sats'] for b in blocks),210)

if __name__=='__main__':unittest.main()
