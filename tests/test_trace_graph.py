import sys,unittest,json,tempfile
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trace_graph import TraceGraph,identity
from worker import load_core,install_trace_answers
from balance_trace import trace_current_basis
from balance_store import BalanceStore

class GraphTests(unittest.TestCase):
 def test_convergence_split_and_idempotent_import(self):
    c=load_core();tx='a'*64;parent='b'*64
    chain={'transactions':{tx:{'inputs':[{'txid':parent,'vout':0,'value':10}], 'outputs':[{'value':4,'address':'one'},{'value':5,'address':'two'}],'fee_sats':1}},'wallet_outputs':{tx+':0':'wallet-a',tx+':1':'wallet-b'}}
    g=TraceGraph();g.seed(c,{},chain,{'ownership':[]},{},[]);first=g.export();encoded=json.dumps(first,sort_keys=True)
    g.seed(c,{},chain,{'ownership':[]},{},[]);self.assertEqual(json.dumps(g.export(),sort_keys=True),encoded)
    self.assertEqual(len([n for n in g.nodes.values() if n['kind']=='transaction']),1)
    transaction=identity('transaction',tx);self.assertEqual(len([e for e in g.edges.values() if e['source']==transaction and e['relation']=='creates']),2)
    self.assertTrue(any(e['relation']=='spent_by' and e['target']==transaction for e in g.edges.values()))
 def test_source_destination_share_address_fact_but_profiles_do_not(self):
    g=TraceGraph();g.remember('owner','source',('tx',('address',)),False)
    self.assertIs(g.lookup('owner','destination',('other-tx','wallet','address','2','row')),False)
    self.assertIsNone(g.lookup('other','destination',('other-tx','wallet','address','2','row')))
 def test_group_no_never_becomes_individual_ownership_and_unknown_never_false(self):
    g=TraceGraph();g.remember('owner','source',('tx',('a','b')),False)
    self.assertIsNone(g.lookup('owner','source',('other',('a',))))
    self.assertIsNone(g.lookup('owner','source',('other',('a','b','c'))))
    g.remember('owner','source',('tx',('c',)),False);g.remember('owner','source',('tx',('d',)),False)
    self.assertIs(g.lookup('owner','source',('other',('c','d'))),False)
    with self.assertRaises(ValueError):g.remember('owner','destination',('tx','wallet','c','1','row'),True)
 def test_context_store_and_legacy_migration(self):
    g=TraceGraph();g.migrate([['owner','source',repr(('tx',('a',))),False]])
    with tempfile.TemporaryDirectory() as d:
      store=BalanceStore(Path(d)/'config.cfg');store.save('same',{'trace_graph':g.export()})
      loaded=TraceGraph(store.load('same')['result']['trace_graph']);self.assertIs(loaded.lookup('owner','source',('tx',('a',))),False);self.assertIsNone(store.load('changed'))
 def test_two_tracers_share_real_question_and_preserve_tax_arithmetic(self):
    c=load_core();g=TraceGraph();console=type('Console',(),{'profile':'owner'})();g.install(c,console);install_trace_answers(c,console,g)
    row=c.WalletTx('Generic Wallet',1,date(2022,1,1),Decimal(2),Decimal(0),Decimal(2),'',txid='a'*64)
    own=c.WalletOwnership('Generic Owner');own.wallet_statuses[row.sheet]=own.PERSONAL;own.bind_wallets({row.sheet:[row]})
    e=c.Engine('Generic Owner',2026,{row.sheet:[row]},c.ReportSet(),ownership=own)
    with patch.object(c,'lookup_transaction_input_addresses',return_value=['source']),patch('builtins.input',return_value='n') as prompt,patch.object(c.Engine,'market_price',return_value=Decimal(100)):
      basis=trace_current_basis(c,e)
      # A fresh tracer/ownership object should resolve the same relationship.
      again=c.WalletOwnership('Generic Owner');again.decide_incoming_source(row,trace_source='Exchange Tracer')
      self.assertEqual(prompt.call_count,1);self.assertEqual(basis[row.sheet]['cost'],Decimal(200))
    self.assertTrue(any(edge['relation']=='explored' for edge in g.edges.values()))
    self.assertFalse(e.events)
 def test_plain_records_are_retained_but_synthetic_allocations_are_not(self):
    c=load_core();g=TraceGraph();row=c.WalletTx('Any Wallet',1,date(2020,1,1),Decimal(1),Decimal(0),Decimal(1),'')
    record=g.record(c,row);self.assertEqual(g.nodes[record]['btc'],'1')
    row.accounting_flow='synthetic-choice';self.assertIsNone(g.record(c,row))

if __name__=='__main__':unittest.main()

class GraphPersistenceTests(unittest.TestCase):
 def test_two_workers_extend_graph_without_losing_branches(self):
    with tempfile.TemporaryDirectory() as d:
      store=BalanceStore(Path(d)/'config.cfg');a=TraceGraph();b=TraceGraph()
      a.remember('owner','source',('tx-a',('a',)),False);b.remember('owner','source',('tx-b',('b',)),False)
      store.save('same',{'trace_graph':a.export()});store.save('same',{'trace_graph':b.export()})
      combined=TraceGraph(store.load('same')['result']['trace_graph'])
      self.assertIs(combined.lookup('owner','source',('new',('a',))),False);self.assertIs(combined.lookup('owner','source',('new',('b',))),False)
 def test_actual_output_ownership_can_resolve_new_trace(self):
    c=load_core();g=TraceGraph();tx='f'*64
    chain={'transactions':{tx:{'outputs':[{'value':1,'address':'public-address'}]}},'wallet_outputs':{tx+':0':'descriptor-id'}}
    snapshot={'ownership':[{'id':'resource-id','kind':'descriptor','source':'descriptor-id','name':'Unrelated Name','confirmed':True,'owner_id':'person-a'}]}
    g.seed(c,{},chain,snapshot,{},[])
    self.assertIs(g.lookup('person-a','source',('later',('public-address',))),True)
    self.assertIs(g.lookup('person-b','destination',('later','other','public-address','1','row')),False)

class RecordIdentityTests(unittest.TestCase):
 def test_statements_have_stable_account_identity_and_keep_distinct_occurrences(self):
    from datetime import datetime,timezone
    c=load_core();g=TraceGraph();stamp=datetime(2020,1,1,tzinfo=timezone.utc)
    def reports(prefix):
      r=c.ReportSet();r.btc_rows=[c.BitarooRow(stamp,'Deposit',Decimal(1),Decimal(balance),'BTC',prefix+'/account/file.pdf',1) for balance in (1,2)];return r
    g.seed(c,{}, {},{'ownership':[]},{'profile':{'reports':reports('/run-one')}},[])
    before=json.dumps(g.export(),sort_keys=True)
    g.seed(c,{}, {},{'ownership':[]},{'profile':{'reports':reports('/run-two')}},[])
    self.assertEqual(json.dumps(g.export(),sort_keys=True),before)
    self.assertEqual(len([n for n in g.nodes.values() if n['kind']=='record']),2)

class PartialGraphTests(unittest.TestCase):
 def test_partial_evidence_does_not_unlock_balances_or_discard_existing_snapshot(self):
    with tempfile.TemporaryDirectory() as d:
      store=BalanceStore(Path(d)/'config.cfg');graph=TraceGraph();graph.remember('p','source',('tx',('a',)),False)
      store.save_evidence('context',graph.export(),[])
      self.assertFalse(store.ready('context'));self.assertIsNone(store.personal('context',{'name':'Any'}))
      store.save('context',{'balances':{'wallets':[],'price':'123'}})
      store.save_evidence('context',graph.export(),[])
      self.assertTrue(store.ready('context'));self.assertEqual(store.load('context')['result']['balances']['price'],'123')
