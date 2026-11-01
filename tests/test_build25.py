"""Generic allowance graphs, first-payday templates and provenance presentation."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from decimal import Decimal as D
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from salary_planning import clean_inputs,evaluate
from salary_graph import allocate
from salary_funding import funding_preview,calculate
from transaction_templates import TemplateStore
from source_display import source_label
from test_build24 import ctx,inputs,wallet
from vendor.embit.script import Script

class AllocationGraphs(unittest.TestCase):
 def context(self):
  c=ctx();c['users']+=[{'id':'c','name':'Any third user'}];return c
 def test_chain_shares_incoming_not_the_remaining_net_and_conserves_money(self):
  p=clean_inputs({'people':[{'id':'a','target':'1000'},{'id':'b','target':'99999'},{'id':'c','target':'0'}],'allocations':[{'from':'a','to':'b','share':'10'},{'from':'b','to':'c','share':'25'}]});r=allocate(D(1000),p,self.context()['users']);self.assertEqual([D(p['retained_aud']) for p in r['people']],[D(900),D(75),D(25)]);self.assertIsNone(r['people'][1]['target_aud']);self.assertEqual(r['surplus_aud'],0)
 def test_independent_targets_share_a_shortfall_proportionally(self):
  p=clean_inputs({'people':[{'id':'a','target':'200'},{'id':'b','target':'100'}]});r=allocate(D(150),p,self.context()['users']);self.assertEqual([D(x['retained_aud']) for x in r['people']],[D(100),D(50)]);self.assertEqual(r['salary_shortfall_aud'],150)
 def test_zero_targets_share_remaining_after_fixed_targets(self):
  p=clean_inputs({'people':[{'id':'a','target':'100'},{'id':'b','target':'0'},{'id':'c','target':'0'}]});r=allocate(D('201.01'),p,self.context()['users']);self.assertEqual([D(x['retained_aud']) for x in r['people']],[D(100),D('50.51'),D('50.50')])
 def test_asset_is_a_multiplier_of_selected_independent_target(self):
  p=clean_inputs({'people':[{'id':'a','target':'100'},{'id':'b','target':'200'}],'pay_multiplier':'1.5','asset_base_id':'b','asset_multiple':'2','asset_enabled':True});r=allocate(D(2000),p,self.context()['users']);self.assertEqual(r['asset_aud'],600);self.assertEqual(r['surplus_aud'],950)
 def test_reject_cycles_two_parents_and_outgoing_shares_over_100(self):
  people=[{'id':i,'target':'0'} for i in 'abc']
  for edges in [[{'from':'a','to':'b','share':'1'},{'from':'b','to':'a','share':'1'}],[{'from':'a','to':'c','share':'1'},{'from':'b','to':'c','share':'1'}],[{'from':'a','to':'b','share':'80'},{'from':'a','to':'c','share':'21'}]]:
   with self.assertRaises(ValueError):clean_inputs({'people':people,'allocations':edges})
 def test_missing_user_is_not_replaced_by_a_matching_name(self):
  p=clean_inputs({'people':[{'id':'unknown','target':'100'}]})
  with self.assertRaisesRegex(ValueError,'no longer'):allocate(D(100),p,self.context()['users'])
 def test_full_evaluation_conserves_annual_and_every_payday(self):
  p=inputs(people=[{'id':x,'target':'0' if x!='a' else '3000'} for x in 'abc'],allocations=[{'from':'a','to':'b','share':'33.3333'},{'from':'b','to':'c','share':'22.2222'}],network_fees_aud='13.11');r=evaluate(self.context(),p)
  for row in r['paydays']:self.assertEqual(sum(D(row[k]) for k in ['tax','fees','asset','surplus','person:a','person:b','person:c']),D(row['gross']))
  for person in r['allocation']['people']:self.assertEqual(sum(D(p['person:'+person['id']]) for p in r['paydays']),D(person['retained_aud']))
 def test_removed_allowance_restores_target_and_saved_layout_survives(self):
  p={'people':[{'id':'a','target':'100'},{'id':'b','target':'50'}],'allocations':[{'from':'a','to':'b','share':'10'}],'layout':{'a':{'x':12,'y':75}}};a=allocate(D(200),clean_inputs(p),self.context()['users']);p['allocations']=[];b=allocate(D(200),clean_inputs(p),self.context()['users']);self.assertIsNone(a['people'][1]['target_aud']);self.assertEqual(b['people'][1]['target_aud'],'50.00');self.assertEqual(clean_inputs(p)['layout']['a'],{'x':12,'y':75})

class FundingTemplates(unittest.TestCase):
 def scenario(self):
  c=ctx();c['wallets'].append(wallet('distributor','0',cost='0'));p=inputs(distribution_id='distributor',fee_edges=[{'from':'source','rate':'1.2'}]);r=evaluate(c,p);catalog={'wallets':[{'id':w['id'],'name':w['name'],'can_send':True,'can_receive':True,'address':Script(b'\x00\x14'+bytes([n+1])*20).address()} for n,w in enumerate(c['wallets'])]};coins={'source':[{'txid':'ab'*32,'vout':0,'value':10000000000,'weight':273,'witness':True}]};return c,p,r,catalog,coins
 def test_budget_covers_payment_plus_fee_exactly_and_template_roundtrip(self):
  c,p,r,catalog,coins=self.scenario();preview=funding_preview(r,p,catalog,coins);self.assertTrue(preview['ready']);self.assertGreater(int(preview['estimate']['total_fee_sats']),0);budget=int(D(r['funding_paydays'][0]['sources'][0]['btc'])*100000000);payment=next(e for e in preview['diagram']['edges'] if e['to']=='distributor');self.assertEqual(int(payment['amount_sats'])+int(preview['estimate']['total_fee_sats']),budget)
  with tempfile.TemporaryDirectory() as folder:
   store=TemplateStore(Path(folder)/'config.cfg');row=store.save({'name':'Any chosen name','diagram':preview['diagram']});self.assertEqual(store.read(row['id'])['diagram'],preview['diagram']);self.assertEqual(store.listing()[0]['name'],'Any chosen name')
 def test_first_payday_not_average_is_used_and_layout_maps_distributor(self):
  c,p,r,catalog,coins=self.scenario();p['layout']={'distributor':{'x':500,'y':280}};preview=funding_preview(r,p,catalog,coins);self.assertEqual(preview['date'],r['paydays'][0]['date']);self.assertEqual(next(n for n in preview['diagram']['nodes'] if n['id']=='distributor')['x'],500)
 def test_incomplete_amounts_missing_address_exchange_cash_and_no_fee_block_export(self):
  for changed in ('fee','person','address','cash','basis','balance'):
   c,p,r,catalog,coins=self.scenario()
   if changed=='fee':p['fee_edges']=[]
   elif changed=='person':p['people']=[]
   elif changed=='address':catalog['wallets'][-1]['can_receive']=False
   elif changed=='cash':r['funding_paydays'][0]['sources'][0]['cash_aud']='10'
   elif changed=='basis':r['tax_complete']=False
   elif changed=='balance':coins['source'][0]['value']=1
   with self.subTest(changed=changed),self.assertRaises(ValueError):funding_preview(r,p,catalog,coins)
 def test_offline_calculation_keeps_gross_and_explicit_template_reason(self):
  c,p,_,_,_=self.scenario();r=calculate(None,c,p,lambda:(_ for _ in ()).throw(ValueError('No synchronised node.')));self.assertEqual(r['gross_aud'],'10000.00');self.assertFalse(r['funding_template']['ready']);self.assertIn('No synchronised',r['funding_template']['reason'])
 def test_live_preview_adds_fee_reserve_once_and_never_builds_a_psbt(self):
  c,p,_,catalog,coins=self.scenario()
  with patch('transaction_builder.builder_catalog',return_value=catalog),patch('fee_planner.live_coins',return_value=coins):
   a=calculate(None,c,p,lambda:None);b=calculate(None,c,p,lambda:None,force=True)
  self.assertTrue(a['funding_template']['ready']);self.assertEqual(a['network_fees_aud'],b['network_fees_aud']);self.assertGreater(D(a['network_fees_aud']),0);self.assertNotIn('psbt',a['funding_template'])

class Presentation(unittest.TestCase):
 def test_account_is_statement_evidence_not_node_identity_and_drops_cache_path(self):
  r=source_label('ACCOUNT:random:/var/cache/wallaby/session-x/Bitaroo_BTC_2026-2027.pdf:1:431');self.assertIn('Bitaroo_BTC_2026-2027.pdf',r);self.assertIn('record 432',r);self.assertIn('not a TXID',r);self.assertNotIn('/var/',r);self.assertNotIn('ACCOUNT:',r);self.assertEqual(source_label('ab'*32+':0'),'ab'*32+':0')
 def test_bundled_salary_canvas_loads_before_salary_and_has_no_missing_external_script(self):
  from reporting import render_html
  s=render_html();self.assertLess(s.index('window.WallabySalaryDiagram='),s.index('window.WallabySalary='));self.assertNotIn('src="/assets/salary_diagram.js"',s)

if __name__=='__main__':unittest.main()
