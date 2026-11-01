"""Financial-entity separation, live traits and calculator-only salary state."""
import sys,tempfile,unittest,copy
from pathlib import Path
from datetime import datetime,timezone,date
from decimal import Decimal as D
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from preferences import FamilyStore,resource_id
from family_inputs import declarations,account_id,smsf_slot
from descriptor_store import clean_wallet,DescriptorStore
from live_wallet_traits import watch_flags
from trace_graph import TraceGraph
from nodes import NodeRegistry
from agent_rpc import METHODS
from salary_builder import SalaryStore
from worker import load_core
from family_tax import FamilyCalculation
from balance_trace import run
from smsf_balances import add_smsf_balances,entity_snapshot
from test_node_loading import public_wallet

class Build23(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.cfg=Path(self.tmp.name)/'config.cfg';self.family=FamilyStore(self.cfg);self.family.save_user('Example','green',revision=0,date_of_birth='01/01/1990');self.user=self.family.snapshot()['users'][0];self.uid=self.user['id']
 def test_bundled_renderer_includes_tooltips_and_salary_before_app(self):
  from reporting import render_html
  html=render_html();self.assertIn('window.WallabySalary=',html);self.assertIn('window.WallabyIconHelp=',html);self.assertNotIn('src="/assets/salary.js"',html);self.assertNotIn('src="/assets/icon_tooltips.js"',html)
 def test_salary_http_start_save_restore_and_no_agent_changes(self):
  import http.client,json,threading
  from launch_wallaby_bitcoin_dashboard import create_server
  server,pool=create_server(Path(self.tmp.name)/'http',0,self.cfg,ipv6=False);pool.nodes.health.closed=True
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();session=pool.create();session.user_id=self.uid;session.access=lambda:{'balances_ready':True}
  def request(path,body=None):
   c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);c.request('POST' if body is not None else 'GET',path,json.dumps(dict(body,user_id=self.uid)) if body is not None else None,{'X-Session-Token':session.token,'Content-Type':'application/json'});r=c.getresponse();out=json.loads(r.read());c.close();self.assertEqual(r.status,200,out);return out
  try:
   row=request('/api/salary/start',{});self.assertTrue(row['started']);row=request('/api/salary/save',{'revision':row['revision'],'inputs':{'base_pay':'24000'}});self.assertEqual(request('/api/salary')['inputs'],row['inputs']);self.assertFalse(request('/api/salary/reset',{})['started']);self.assertEqual(pool.nodes.hub.read()['requests'],{})
  finally:server.shutdown();server.server_close();thread.join(3)
 def test_personal_and_fund_upload_declarations_are_independent(self):
  users=[self.user];paths={'bitaroo_user_'+self.uid:'personal'}
  self.assertTrue(declarations(users,{},paths)[self.uid]);self.assertFalse(declarations(users,{},paths,smsf=True)[self.uid])
  with self.assertRaisesRegex(ValueError,'SMSF'):declarations(users,{'non_bitaroo_smsf':[]},paths,smsf=True)
  paths[smsf_slot(self.uid)]='fund';self.assertTrue(declarations(users,{'non_bitaroo_smsf':[]},paths,smsf=True)[self.uid]);self.assertEqual(account_id(smsf_slot(self.uid)),self.uid)
 def test_fund_exchange_owner_and_smsf_are_fixed_from_upload(self):
  source=self.uid+':smsf';item={'id':resource_id('bitaroo',source),'kind':'bitaroo','source':source,'owner_id':self.uid,'smsf':True,'name':'Bitaroo SMSF Exchange · Example'}
  self.family.save_ownership([dict(item,external=False,smsf=False)],self.family.snapshot()['revision'],[item]);row=self.family.snapshot()['ownership'][0];self.assertEqual(row['owner_id'],self.uid);self.assertTrue(row['smsf'])
 def test_watch_badge_tracks_connected_private_replicas_without_invalidating_history(self):
  raw=public_wallet('Public policy',0);w=clean_wallet(raw);w.update(watch_only=True,node_sources=[{'node_id':'watch','wallet_name':'Public policy'}]);live={'watch','private'}
  hub=SimpleNamespace(inventories={'watch':{'complete':True,'wallets':[{'raw':raw,'private_keys_enabled':False}]},'private':{'complete':True,'wallets':[{'raw':raw,'private_keys_enabled':True}]}})
  reg=SimpleNamespace(hub=hub,snapshot=lambda:{'nodes':[{'id':n,'connected':True,'rpc_ready':True} for n in live]})
  self.assertFalse(watch_flags({'wallets':[w]},reg)[w['id']]);live.remove('private');self.assertTrue(watch_flags({'wallets':[w]},reg)[w['id']])
  altered=copy.deepcopy(w);altered.update(watch_only=False,node_sources=[]);self.assertEqual(DescriptorStore.fingerprint({'wallets':[w]}),DescriptorStore.fingerprint({'wallets':[altered]}));altered['raw']['descriptors'][0]['next_index']+=1;self.assertNotEqual(DescriptorStore.fingerprint({'wallets':[w]}),DescriptorStore.fingerprint({'wallets':[altered]}))
 def test_smsf_and_personal_answers_cannot_cross_entity_boundary(self):
  g=TraceGraph();g.address_owners={'fund':(self.uid,True),'personal':(self.uid,False)}
  self.assertFalse(g.lookup(self.uid,'source',('tx',('fund',))));self.assertTrue(g.lookup(self.uid+':smsf','source',('tx',('fund',))));self.assertFalse(g.lookup(self.uid+':smsf','source',('tx',('personal',))))
 def test_authenticated_result_and_exchange_traffic_keep_connection_alive(self):
  clock=[0];reg=NodeRegistry(self.cfg,clock=lambda:clock[0]);reg.health.closed=True;connection=reg.register('02:00:00:00:00:23','example',{'version':1,'methods':list(METHODS)});token=connection['connection_token'];clock[0]=40;reg.rpc._connection(token);clock[0]=80;self.assertTrue(reg.snapshot()['nodes'][0]['connected']);reg.hub.connection(token);clock[0]=120;self.assertTrue(reg.snapshot()['nodes'][0]['connected']);clock[0]=126;self.assertFalse(reg.snapshot()['nodes'][0]['connected']);self.assertIn('authenticated_traffic_age_s=46',reg.log_text())
 def test_every_ready_node_receives_diagram_lease_without_a_picker(self):
  reg=NodeRegistry(self.cfg);reg.health.closed=True
  nodes=[{'id':str(n),'hostname':'node'+str(n),'rpc_ready':True,'blockchain':{'state':'ready'}} for n in range(2)]
  session=SimpleNamespace(user=lambda:self.user,user_id=self.uid,access=lambda:{'templates_ready':True},session_dir=Path('example-browser'))
  with patch.object(reg,'snapshot',return_value={'nodes':nodes}):self.assertEqual(reg.hub.lease_all(session,'diagram-example'),{'exports':[]})
  self.assertEqual({l['node_id'] for l in reg.hub.leases.values()},{'0','1'});reg.hub.clear_lease(session);self.assertFalse(reg.hub.leases)
 def test_salary_diagram_is_scoped_saved_and_has_no_transaction_side_effects(self):
  s=SalaryStore(self.cfg);self.assertFalse(s.snapshot(self.uid)['started']);state=s.change(self.uid,'start');plan={'base_pay':'24000'};saved=s.change(self.uid,'save',{'revision':state['revision'],'inputs':plan});self.assertEqual(SalaryStore(self.cfg).snapshot(self.uid)['inputs']['base_pay'],'24000')
  with self.assertRaisesRegex(ValueError,'changed'):s.change(self.uid,'save',{'revision':state['revision'],'inputs':plan})
  self.assertFalse((self.cfg.parent/'data/transactions').exists());self.assertFalse((self.cfg.parent/'data/agent-hub.json').exists());self.assertFalse((self.cfg.parent/'data/builder-workspaces.json').exists());self.family.delete_user(self.uid,self.family.snapshot()['revision']);self.assertNotIn(self.uid,s.read())
 def test_balance_trace_costs_smsf_purchase_without_fund_cgt_or_personal_contamination(self):
  c=load_core();stamp=datetime(2025,8,1,tzinfo=timezone.utc);withdraw=datetime(2025,8,2,tzinfo=timezone.utc)
  def reports(cost,quantity,source):
   r=c.ReportSet();r.years={2025:('aud','btc')};r.ranges={2025:(date(2025,7,1),date(2026,6,30))};r.start_bal={2025:D(0)};r.close_bal={2025:1-quantity};r.btc_rows=[c.BitarooRow(stamp.replace(tzinfo=None),'Buy Bitcoin',D(1),D(1),'BTC',source,1),c.BitarooRow(withdraw.replace(tzinfo=None),'Withdrawal',-quantity,1-quantity,'BTC',source,1)];r.trades=[c.Trade(stamp.replace(tzinfo=None),'buy',D(1),cost,D(0),source,0)];return r
  wallets={'Fund Wallet':[c.WalletTx('Fund Wallet',1,c.TransactionDate(withdraw),D('.9'),D(0),D('.9'),'')],'Personal Wallet':[c.WalletTx('Personal Wallet',1,c.TransactionDate(withdraw),D('.2'),D(0),D('.2'),'')]};snapshot={'users':[self.user],'ownership':[]}
  for name,fund in [('Fund Wallet',True),('Personal Wallet',False)]:snapshot['ownership'].append({'id':resource_id('wallet',name),'kind':'wallet','source':name,'name':name,'owner_id':self.uid,'external':False,'smsf':fund,'confirmed':True})
  for source,fund in [(self.uid,False),(self.uid+':smsf',True)]:snapshot['ownership'].append({'id':resource_id('bitaroo',source),'kind':'bitaroo','source':source,'name':('Bitaroo SMSF Exchange' if fund else 'Bitaroo Exchange')+' · Example','owner_id':self.uid,'external':False,'smsf':fund,'confirmed':True})
  personal={self.uid:{'directory':'personal','reports':reports(D(200),D('.2'),'personal')}};funds={self.uid:{'directory':'fund','reports':reports(D(100),D('.9'),'fund')}};console=SimpleNamespace(profile=self.uid);emit=lambda *a,**k:None;chain={'wallets':[],'transactions':{}};calc=FamilyCalculation(c,chain,wallets,personal,[],snapshot,{},console,emit,lambda *a:[])
  with patch.object(FamilyCalculation,'run',side_effect=AssertionError('No fund tax reports')),patch.object(c.INTRADAY,'rate',return_value=(D(150),'fixture')),patch.object(c.Engine,'market_price',return_value=D(500)),patch('builtins.input',side_effect=AssertionError('Both source statements are complete')):
   result=run(c,calc,[],snapshot,self.user,console,emit,None);add_smsf_balances(result,c,chain,wallets,funds,[],snapshot,{},console,emit,lambda *a:[],None,calc.base_ownership)
  by_name={r['name']:r for r in result['wallets']};self.assertEqual(D(by_name['Fund Wallet']['cost_basis_aud']),D(90));self.assertEqual(D(by_name['Fund Wallet']['unrealised_gain_aud']),D(45));self.assertEqual(D(by_name['Personal Wallet']['cost_basis_aud']),D(40));self.assertEqual(D(by_name['Personal Wallet']['unrealised_gain_aud']),D(-10));self.assertEqual(D(by_name['Bitaroo SMSF Exchange · Example']['cost_basis_aud']),D(10));self.assertTrue(by_name['Fund Wallet']['smsf']);self.assertEqual(sum((D(p['quantity_btc']) for p in by_name['Fund Wallet']['planning_lots']),D(0)),D('.9'));self.assertEqual(sum((D(p['cost_aud']) for p in by_name['Personal Wallet']['planning_lots']),D(0)),D(40));self.assertEqual(by_name['Bitaroo SMSF Exchange · Example']['owner_id'],self.uid)

if __name__=='__main__':unittest.main()
