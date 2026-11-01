"""Presentation evidence, personal salary settings and node-only explorer coverage."""
import copy,sys,tempfile,unittest
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from salary_planning import clean_inputs,evaluate
from salary_graph import allocate
from salary_funding import calculate
from test_build24 import ctx,inputs,wallet
from balance_evidence import annotate
from node_explorer import NodeExplorer
from node_chain import NodeChain,NodeDataUnavailable
from vendor.embit.transaction import Transaction,TransactionInput,TransactionOutput
from vendor.embit.script import Script

SCRIPT=Script(b'\x00\x14'+b'\x12'*20);ADDRESS=SCRIPT.address()
class Salaries(unittest.TestCase):
 def test_other_profile_distributor_allowed_but_other_profile_funding_forbidden(self):
  c=ctx([wallet(),wallet('distributor','0',owner='b',cost='0')]);self.assertEqual(evaluate(c,inputs(distribution_id='distributor'))['gross_aud'],'10000.00')
  with self.assertRaises(ValueError):evaluate(c,inputs(sources=[{'id':'distributor','mode':'one_year'}]))
 def test_individual_multipliers_and_chained_percentages_conserve_cents(self):
  p=clean_inputs({'people':[{'id':'a','target':'100','multiplier':'2'},{'id':'b','target':'0','multiplier':'1.5'}],'allocations':[{'from':'a','to':'b','share':'10'}]});r=allocate(Decimal(200),p,ctx()['users']);self.assertEqual([x['retained_aud'] for x in r['people']],['170.00','30.00']);self.assertEqual(r['people'][1]['effective_share'],'15.0')
 def test_effective_shares_over_hundred_rejected(self):
  with self.assertRaises(ValueError):clean_inputs({'people':[{'id':'a','target':'0'},{'id':'b','target':'0','multiplier':'2'}],'allocations':[{'from':'a','to':'b','share':'60'}]})
 def test_schedule_must_be_confirmed_before_template_node_requests(self):
  r=calculate(None,ctx(),inputs(schedule_set=False),lambda: self.fail('Unconfirmed schedule must not query coins'));self.assertFalse(r['funding_template']['ready']);self.assertIn('schedule',r['funding_template']['reason'].lower())
 def test_scenario_price_is_explicit_not_silently_replaced_by_quote(self):
  with self.assertRaises(ValueError):evaluate(ctx(),inputs(price_aud=''))

class Evidence(unittest.TestCase):
 def test_owned_unspent_address_not_cost_provenance_or_counterparty(self):
  other=Script(b'\x00\x14'+b'\x13'*20).address();chain={'wallet_outputs':{'a:0':'w'},'transactions':{'a':{'inputs':[],'outputs':[{'address':ADDRESS},{'address':other}]}}};report={'balances':{'wallets':[{'name':'Any wallet','descriptor_id':'w','sources':['SECRET COST SOURCE']}]}};catalog={'wallets':[{'id':'w','node_sources':[{'node_id':'n','hostname':'Family node'}]}]}
  with tempfile.TemporaryDirectory() as d,patch('balance_evidence.history',return_value=chain),patch.object(Path,'stat',return_value=SimpleNamespace(st_mtime_ns=1,st_size=1)):annotate(report,catalog,{},Path(d)/'config')
  row=report['balances']['wallets'][0];self.assertEqual(row['balance_sources'],['Any wallet — '+ADDRESS]);self.assertEqual(row['sources'],['SECRET COST SOURCE']);self.assertNotIn(other,str(row['balance_sources']))
 def test_latest_uploaded_statements_even_with_no_recent_trade(self):
  row={'kind':'exchange','name':'Account','owner_id':'u','resource_id':'r'};snap={'ownership':[{'id':'r','kind':'bitaroo'}],'ownership_runs':{'u':{'files':[{'slot':'bitaroo_user_u','name':n} for n in ['Statement BTC FY25.pdf','Statement AUD FY27.pdf','Statement BTC FY27.pdf']]}}}
  with tempfile.TemporaryDirectory() as d:annotate({'balances':{'wallets':[row]}},{'wallets':[]},snap,Path(d)/'config')
  self.assertEqual(set(row['balance_sources']),{'Statement AUD FY27.pdf','Statement BTC FY27.pdf'})

class RPC:
 def __init__(self,tx=None):self.calls=[];self.tx=tx;self.fail_first=False;self.missing=False
 def nodes(self):return [{'id':'a','hostname':'A'},{'id':'b','hostname':'B'}]
 def call(self,node,method,params=None,wallet=None):
  self.calls.append((node,method,params,wallet))
  if method=='getrawtransaction':
   if self.missing or self.fail_first and node=='a':raise NodeDataUnavailable('Pruned')
   return {'hex':self.tx.serialize().hex(),'confirmations':0,'vin':[{'prevout':{'value':'0.00002','scriptPubKey':{'hex':SCRIPT.data.hex()}}}]}
  if method=='listwallets':return ['newly-connected'] if node=='b' else ['unrelated']
  if method=='getaddressinfo':return {'ismine':node=='b'}
  if method=='listunspent':return [{'txid':'11'*32,'vout':0,'amount':'0.00001','address':ADDRESS}]
  if method=='validateaddress':return {'isvalid':True}
  if method=='gettxout':return None
  raise NodeDataUnavailable('Not found')
class Explorer(unittest.TestCase):
 def setUp(self):self.tx=Transaction(vin=[TransactionInput(b'\x34'*32,0)],vout=[TransactionOutput(1000,SCRIPT)]);self.rpc=RPC(self.tx);self.chain=NodeChain(self.rpc,{'wallets':[]});self.explorer=NodeExplorer(None,None)
 def test_transaction_fee_and_weight_and_tries_other_node(self):
  self.rpc.fail_first=True;r=self.explorer.transaction(self.chain,self.tx.txid().hex(),None,self.tx.txid().hex());self.assertEqual(r['transaction']['fee'],1000);self.assertEqual(r['transaction']['weight'],len(self.tx.serialize())*4);self.assertEqual([n for n,m,_,_ in self.rpc.calls if m=='getrawtransaction'],['a','b'])
 def test_null_gettxout_does_not_claim_unspent(self):
  r=self.explorer.transaction(self.chain,self.tx.txid().hex(),0,'query');self.assertIn('Spent or unavailable',r['selected_output']['spend_status'])
 def test_missing_transaction_checks_every_node_before_unavailable(self):
  self.rpc.missing=True;r=self.explorer.transaction(self.chain,'a'*64,None,'a'*64);self.assertFalse(r['available']);self.assertEqual({n for n,m,_,_ in self.rpc.calls if m=='getrawtransaction'},{'a','b'})
 def test_address_checks_all_nodes_and_new_wallet_without_saved_catalog(self):
  r=self.explorer.address(self.chain,ADDRESS,{}, {'wallets':[]});self.assertEqual({n['node'] for n in r['node_checks']},{'A','B'});self.assertEqual(r['known_unspent_sats'],1000);self.assertIsNone(r['observed_received_sats']);self.assertEqual(r['wallets'],['newly-connected']);self.assertTrue(r['live_unspent'])
 def test_replicated_unspent_outputs_not_double_counted(self):
  original=self.rpc.call
  def call(n,m,p=None,w=None):return {'ismine':True} if m=='getaddressinfo' else original(n,m,p,w)
  self.rpc.call=call;r=self.explorer.address(self.chain,ADDRESS,{}, {'wallets':[]});self.assertEqual(r['known_unspent_sats'],1000)
 def test_invalid_queries_no_node_request(self):
  for query in ['', 'x'*181,'not an address','1'*64+':-1']:
   with self.assertRaises(ValueError):self.explorer.search(query)
 def test_wrong_output_index_rejected(self):
  with self.assertRaisesRegex(ValueError,'index'):self.explorer.transaction(self.chain,self.tx.txid().hex(),1,'query')
 def test_unknown_parent_leaves_fee_unknown(self):
  original=self.rpc.call
  def call(n,m,p=None,w=None):
   if m=='getrawtransaction' and p[0]==self.tx.txid().hex():return {'hex':self.tx.serialize().hex(),'confirmations':0}
   if m=='getrawtransaction':raise NodeDataUnavailable('Missing parent')
   return original(n,m,p,w)
  self.rpc.call=call;r=self.explorer.transaction(self.chain,self.tx.txid().hex(),None,'query');self.assertIsNone(r['transaction']['fee']);self.assertIn('not inferred',r['coverage'])
if __name__=='__main__':unittest.main()
