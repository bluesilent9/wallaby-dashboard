"""Salary schedules, lifetime funding, official data refresh and exact allocations."""
import copy,io,json,sys,tempfile,threading,unittest
from pathlib import Path
from datetime import date,datetime,timezone
from decimal import Decimal as D
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from salary_planning import clean_inputs,paydays,evaluate,context,funding_catalog
from resident_tax_tables import ATO,LAW,OFFSET,parse_ato,parse_law,parse_offset,select,validate,bracket_tax,ResidentTaxTables
from salary_builder import SalaryStore
from preferences import FamilyStore,resource_id

FIXTURES=Path(__file__).with_name('fixtures')
LAW_DOC='https://www.legislation.gov.au/C2004A03348/2026-07-01/2026-07-01/text/original/epub/OEBPS/document_1/document_1.html'
OFFSET_TEXT='If you earned: $37,500 or less, you will get the maximum offset of $700 between $37,501 and $45,000, you will get $700 minus 5 cents for every $1 above $37,500 between $45,001 and $66,667, you will get $325 minus 1.5 cents for every $1 above $45,000. We will work out your offset'

def tax_data():
 return {'schema':1,'checked_utc':'2026-11-01T00:00:00+00:00','tables':parse_law((FIXTURES/'resident-act.html').read_text(),LAW_DOC),'offset':None}

def wallet(ident='source',btc='62',fund=False,owner='a',cost='6200'):
 return {'id':ident,'resource_id':ident,'owner_id':owner,'external':False,'name':'Unrelated display '+ident,'kind':'wallet','btc':btc,'cash_aud':'0','cost_basis_aud':cost,'smsf':fund,'planning_lots':[{'quantity_btc':btc,'cost_aud':cost,'acquired':'2018-01-01'}] if D(btc) else []}

def ctx(wallets=None):
 user={'id':'a','name':'Profile A','date_of_birth':'13/10/1988'}
 return {'user':user,'users':[user,{'id':'b','name':'Profile B'}],'start':'2027-07-01','end':'2028-06-30','financial_year':'2027–28','price_aud':'10000','wallets':wallets or [wallet()],'tax_tables':{'data':tax_data()}}

def inputs(**values):
 return dict({'sources':[{'id':'source','mode':'lifetime'}],'frequency':'weekly','weekday':4,'include_lito':False,'medicare_percent':'0','anchor':'2027-07-01'},**values)

class Schedules(unittest.TestCase):
 def test_actual_dates_include_leap_year_and_53_weekdays(self):
  start,end=date(2027,7,1),date(2028,6,30)
  self.assertEqual(len(paydays(start,end,clean_inputs({'frequency':'daily'}))),366)
  fri=paydays(start,end,clean_inputs({'frequency':'weekly','weekday':4}));self.assertEqual(len(fri),53);self.assertEqual(fri[0],date(2027,7,2));self.assertEqual(fri[-1],end)
 def test_fortnight_anchor_can_precede_or_follow_year_without_changing_phase(self):
  start,end=date(2027,7,1),date(2028,6,30)
  a=paydays(start,end,clean_inputs({'anchor':'2027-07-01'}));b=paydays(start,end,clean_inputs({'anchor':'2028-07-13'}));self.assertEqual(a,b);self.assertEqual(len(a),27)
 def test_monthly_last_day_clamp(self):
  rows=paydays(date(2027,7,1),date(2028,6,30),clean_inputs({'frequency':'monthly','monthday':31}));self.assertEqual(len(rows),12);self.assertIn(date(2028,2,29),rows);self.assertIn(date(2028,4,30),rows)
 def test_reject_invalid_age_amounts_duplicate_sources_and_dates(self):
  for value in [{'target_age':1.5},{'price_aud':'NaN'},{'pay_multiplier':'Infinity'},{'anchor':'2027-02-30'},{'frequency':'every minute'},{'sources':[{'id':'x','mode':'one_year'}]*2}]:
   with self.assertRaises(ValueError):clean_inputs(value)

class Funding(unittest.TestCase):
 def test_profile_age_on_next_july_and_exact_denominator(self):
  r=evaluate(ctx(),inputs());self.assertEqual(r['age'],38);self.assertEqual(r['years'],62);self.assertEqual(r['gross_aud'],'10000.00');self.assertEqual(r['projection'][-1]['personal_left'],'0.00')
 def test_combined_fund_pool_draws_personal_first_instead_of_each_wallet_divided(self):
  c=ctx([wallet(btc='31',cost='3100'),wallet('fund','31',True,cost='3100')]);r=evaluate(c,inputs(sources=[{'id':'source','mode':'lifetime'},{'id':'fund','mode':'lifetime'}]));self.assertEqual(r['gross_aud'],'10000.00');by_id={s['id']:s for s in r['sources']};self.assertEqual(by_id['source']['annual_draw_aud'],'10000.00');self.assertEqual(by_id['fund']['annual_draw_aud'],'0.00');self.assertIsNone(r['first_unfunded_year'])
 def test_inaccessible_fund_does_not_cover_an_early_bridge_shortfall(self):
  c=ctx([wallet(btc='.1',cost='10'),wallet('fund','61.9',True,cost='6190')]);r=evaluate(c,inputs(sources=[{'id':'source','mode':'lifetime'},{'id':'fund','mode':'lifetime'}]));self.assertEqual(r['gross_aud'],'1000.00');self.assertEqual(r['unfunded_aud'],'9000.00');self.assertEqual(r['first_unfunded_year'],2027)
 def test_one_year_topup_is_not_counted_as_recurring_salary_capacity(self):
  c=ctx([wallet(),wallet('extra','10',cost='1000')]);r=evaluate(c,inputs(sources=[{'id':'source','mode':'lifetime'},{'id':'extra','mode':'one_year'}],base_pay='10000',partner_mode='independent'));self.assertEqual(r['gross_aud'],'110000.00');self.assertEqual(r['projection'][1]['gross'],'10000.00');self.assertEqual(r['additional_salary_equivalents'],0)
 def test_optional_target_age_changes_pool_division(self):
  r=evaluate(ctx(),inputs(target_age=69));self.assertEqual(r['years'],31);self.assertEqual(r['gross_aud'],'20000.00')
 def test_ownership_uses_ids_and_rejects_a_same_name_owned_by_someone_else(self):
  foreign=wallet('foreign',owner='b');foreign['name']='Treasury Wallet'
  with self.assertRaisesRegex(ValueError,'not assigned'):evaluate(ctx([foreign]),inputs(sources=[{'id':'foreign','mode':'lifetime'}]))
  a=evaluate(ctx(),inputs());c=ctx();c['wallets'][0]['name']='Any renamed wallet';c['user']['name']='Any renamed user';b=evaluate(c,inputs());self.assertEqual(a['gross_aud'],b['gross_aud']);self.assertEqual(a['tax_aud'],b['tax_aud'])
 def test_missing_parcels_do_not_invent_zero_cost_or_discount(self):
  c=ctx();c['wallets'][0]['planning_lots']=[];r=evaluate(c,inputs());self.assertEqual(r['gross_aud'],'10000.00');self.assertIsNone(r['tax_aud']);self.assertIsNone(r['own_aud']);self.assertFalse(r['tax_complete'])
 def test_old_or_missing_birth_date_is_actionable(self):
  c=ctx();c['user'].pop('date_of_birth')
  with self.assertRaisesRegex(ValueError,'Date of Birth'):evaluate(c,inputs())
  with self.assertRaisesRegex(ValueError,'Target age'):evaluate(ctx(),inputs(target_age=38))
 def test_release_age_requires_confirmation_below_65(self):
  a=evaluate(ctx(),inputs(access_age=60));b=evaluate(ctx(),inputs(access_age=60,access_confirmed=True));self.assertEqual(a['smsf_release'],'2053-10-13');self.assertEqual(b['smsf_release'],'2048-10-13')
 def test_shared_allowance_salary_targets_payrise_and_asset_reserve(self):
  shared=evaluate(ctx(),inputs(partner_id='b'));self.assertEqual(D(shared['partner_aud']),D(shared['net_aud'])*D('.10'));self.assertEqual(D(shared['own_aud'])+D(shared['partner_aud']),D(shared['net_aud']))
  r=evaluate(ctx([wallet(btc='62000',cost='6200000')]),inputs(partner_id='b',partner_mode='independent',base_pay='60000',partner_pay='50000',pay_multiplier='1.2',asset_enabled=True,asset_multiple='10'));self.assertEqual(r['own_aud'],'72000.00');self.assertEqual(r['partner_aud'],'60000.00');self.assertEqual(r['asset_aud'],'720000.00');self.assertGreater(r['additional_salary_equivalents'],0)
 def test_every_payday_and_annual_total_reconcile_to_cents(self):
  r=evaluate(ctx(),inputs(partner_id='b',base_pay='3000',asset_enabled=True,asset_multiple='1.3',network_fees_aud='53.11'))
  for p in r['paydays']:self.assertEqual(sum((D(p[k]) for k in ['tax','fees','own','partner','asset','surplus']),D(0)),D(p['gross']))
  for key,total in [('tax','tax_aud'),('fees','network_fees_aud'),('own','own_aud'),('partner','partner_aud'),('asset','asset_aud'),('surplus','surplus_aud')]:self.assertEqual(sum((D(p[key]) for p in r['paydays']),D(0)),D(r[total]))
  values=[D(p['tax']) for p in r['paydays']];self.assertLessEqual(max(values)-min(values),D('.01'))
 def test_tax_engine_discounts_gains_and_future_growth_enters_minimum_tax(self):
  old=evaluate(ctx(),inputs());self.assertEqual(old['capital_gains']['net_gain'],'4950.00')
  grown=evaluate(ctx(),inputs(price_aud='20000'));self.assertGreater(D(grown['capital_gains']['minimum_tax_gain']),0);self.assertGreater(D(grown['tax_details']['minimum_tax']),0)
 def test_financial_year_uses_saved_timezone_and_july_boundary(self):
  u=ctx()['user'];snapshot={'users':[u],'ownership':[]}
  before=context(snapshot,{},u,{'timezone':'Australia/Darwin'},datetime(2026,6,30,14,0,tzinfo=timezone.utc));after=context(snapshot,{},u,{'timezone':'Australia/Darwin'},datetime(2026,6,30,15,0,tzinfo=timezone.utc));self.assertEqual(before['next_year'],2026);self.assertEqual(after['next_year'],2027)

class OfficialTables(unittest.TestCase):
 def test_actual_official_html_extracts_current_and_future_rates(self):
  ato=parse_ato((FIXTURES/'resident-ato.html').read_text());law=tax_data()['tables'];self.assertEqual(select({'tables':ato+law},2027)['source'],LAW_DOC);self.assertEqual(bracket_tax(D(45000),select({'tables':law},2027)),D(3752));self.assertEqual(bracket_tax(D(60000),select({'tables':law},2027)),D(8252))
 def test_source_changed_numbers_are_used_without_an_application_change(self):
  raw=(FIXTURES/'resident-act.html').read_text().replace('14%','12%');table=parse_law(raw,LAW_DOC)[0];self.assertEqual(bracket_tax(D(45000),table),D(3216));self.assertNotEqual(table,tax_data()['tables'][0])
 def test_changed_markup_or_inconsistent_base_tax_is_rejected(self):
  raw=(FIXTURES/'resident-ato.html').read_text().replace('$4,020 plus','$9,999 plus')
  with self.assertRaisesRegex(ValueError,'reconcile'):parse_ato(raw)
  with self.assertRaises(ValueError):parse_law('<p>rate 14%</p>',LAW_DOC)
 def test_offset_is_read_from_the_official_document_not_runtime_constants(self):
  with patch('pypdf.PdfReader',return_value=SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda:OFFSET_TEXT)])):
   result=parse_offset(b'fixture');self.assertEqual(result['bands'][0]['base'],'700');self.assertEqual(result['bands'][-1]['rate'],'0.015')
 def test_refresh_overwrites_changed_data_and_network_failure_keeps_cache(self):
  with tempfile.TemporaryDirectory() as d:
   raw=(FIXTURES/'resident-act.html').read_text();root=Path(d)/'config.cfg';source=[raw]
   def download(url):
    if url==ATO:return (FIXTURES/'resident-ato.html').read_bytes()
    if url==LAW:return ('<a href="'+LAW_DOC+'">Current text</a>').encode()
    if url==OFFSET:return b'offset'
    return source[0].encode()
   with patch('resident_tax_tables.parse_offset',return_value={'source':OFFSET,'bands':[{'lower':'0','upper':'37500','base':'700','rate':'0'}]}):
    service=ResidentTaxTables(root,download);service.refresh();a=service.read();self.assertIsNotNone(a);source[0]=raw.replace('14%','12%');service.refresh();b=service.read();self.assertNotEqual(a['digest'],b['digest']);self.assertEqual(select(b,2027)['brackets'][1]['rate'],'0.12')
    service.download=lambda u:(_ for _ in ()).throw(OSError('offline'));service.refresh();self.assertEqual(service.read(),b);self.assertIn('offline',service.error)
 def test_no_table_for_the_requested_year_does_not_reuse_a_wrong_year(self):
  c=ctx();c['tax_tables']={'data':{'tables':parse_ato((FIXTURES/'resident-ato.html').read_text())}};r=evaluate(c,inputs());self.assertIsNone(r['tax_aud']);self.assertFalse(r['tax_complete'])


class HttpAndProfiles(unittest.TestCase):
 def test_live_http_is_profile_scoped_restores_inputs_and_never_queues_agent_work(self):
  import http.client
  from launch_wallaby_bitcoin_dashboard import create_server
  with tempfile.TemporaryDirectory() as folder:
   cfg=Path(folder)/'config.cfg';family=FamilyStore(cfg);family.save_user('Arbitrary owner','green',revision=0,date_of_birth='13/10/1988');family.save_user('Another profile','blue',revision=family.snapshot()['revision'],date_of_birth='01/01/1990');a,b=family.snapshot()['users'];source=resource_id('descriptor','x');record={'id':source,'kind':'descriptor','source':'x','name':'Completely arbitrary source','owner_id':a['id'],'external':False,'smsf':False,'confirmed':True}
   with family.transaction() as data:data['ownership']=[record]
   rates=ResidentTaxTables(cfg);rates.path.parent.mkdir(exist_ok=True);rates.path.write_text(json.dumps(tax_data()))
   server,pool=create_server(Path(folder)/'http',0,cfg,ipv6=False);pool.nodes.health.closed=True;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();session=pool.create();session.user_id=a['id'];session.access=lambda:{'balances_ready':True};session.family=lambda:{'balance_report':{'balances':{'price_aud_per_btc':'10000','wallets':[wallet(source,owner=a['id'])]}}}
   def request(path,body=None):
    c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,{'X-Session-Token':session.token,'Content-Type':'application/json'});r=c.getresponse();out=json.loads(r.read());c.close();return r.status,out
   try:
    _,row=request('/api/salary/start',{'user_id':a['id']});plan=inputs(sources=[{'id':source,'mode':'lifetime'}]);code,saved=request('/api/salary/save',{'user_id':a['id'],'revision':row['revision'],'inputs':plan});self.assertEqual(code,200,saved);self.assertEqual(request('/api/salary')[1]['inputs'],saved['inputs']);code,result=request('/api/salary/calculate',{'user_id':a['id'],'inputs':plan});self.assertEqual(code,200,result);self.assertGreater(D(result['gross_aud']),0);self.assertEqual(pool.nodes.hub.read()['requests'],{});self.assertEqual(pool.nodes.rpc.rows,{})
    session.user_id=b['id'];code,error=request('/api/salary/save',{'user_id':a['id'],'revision':saved['revision'],'inputs':plan});self.assertEqual(code,400);self.assertIn('profile changed',error['error']);self.assertFalse(request('/api/salary')[1]['started'])
   finally:server.shutdown();server.server_close();thread.join(3)
 def test_profile_archive_includes_validated_tax_tables_and_salary_inputs(self):
  from profile_archive import ProfileArchive
  with tempfile.TemporaryDirectory() as folder:
   cfg=Path(folder)/'source/config.cfg';family=FamilyStore(cfg);family.save_user('Sample profile','green',revision=0,date_of_birth='01/01/1980');uid=family.snapshot()['users'][0]['id'];store=SalaryStore(cfg);row=store.change(uid,'start');store.change(uid,'save',{'revision':row['revision'],'inputs':{'base_pay':'52000'}});rates=ResidentTaxTables(cfg);rates.path.write_text(json.dumps(tax_data()));archive=ProfileArchive(cfg).save();target=Path(folder)/'restored/config.cfg';FamilyStore(target);ProfileArchive(target).load('profile.7z',archive);self.assertEqual(SalaryStore(target).snapshot(uid)['inputs']['base_pay'],'52000');self.assertEqual(ResidentTaxTables(target).read()['tables'],rates.read()['tables'])

if __name__=='__main__':unittest.main()
