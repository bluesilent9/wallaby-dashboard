import sys,tempfile,unittest,json
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worker import load_core,install_trace_answers
from balance_trace import trace_current_basis
from balance_store import BalanceStore
from wallet_names import suggest_wallet_name,display_inventory

class BalanceTraceTests(unittest.TestCase):
 def test_receive_only_owned_wallet_is_questioned_and_costed_without_a_disposal(self):
    c=load_core();name='Receiving Wallet';r=c.WalletTx(name,1,date(2022,12,21),Decimal('2'),Decimal(0),Decimal('2'),'');own=c.WalletOwnership('Owner');own.wallet_statuses[name]=own.PERSONAL;own.bind_wallets({name:[r]});asked=[];own.decide_incoming_source=lambda row,trace_source:asked.append((row.ref,trace_source)) or False
    e=c.Engine('Owner',2026,{name:[r]},c.ReportSet(),ownership=own)
    with patch.object(c.Engine,'market_price',return_value=Decimal('100')):basis=trace_current_basis(c,e)
    self.assertEqual(asked,[(r.ref,'Balance Trace')]);self.assertEqual(basis[name]['cost'],Decimal('200'));self.assertFalse(e.events);self.assertFalse(e.holdings)
 def test_balance_trace_follows_a_zero_balance_ancestor_and_preserves_original_basis(self):
    c=load_core();old='Earlier Wallet';new='Current Wallet';D=Decimal
    from datetime import datetime,timezone
    stamp=lambda y,m,d:c.TransactionDate(datetime(y,m,d,tzinfo=timezone.utc))
    incoming=c.WalletTx(old,1,stamp(2018,1,2),D(2),D(0),D(2),'')
    sent=c.WalletTx(old,2,stamp(2022,12,21),D(-2),D(0),D(0),'',txid='f'*64)
    received=c.WalletTx(new,1,stamp(2022,12,21),D(2),D(0),D(2),'',txid='f'*64)
    rows={old:[incoming,sent],new:[received]};own=c.WalletOwnership('Owner');own.wallet_statuses.update({old:own.PERSONAL,new:own.PERSONAL});own.bind_wallets(rows);asked=[];own.decide_incoming_source=lambda row,trace_source:asked.append(row.ref) or False
    e=c.Engine('Owner',2026,rows,c.ReportSet(),ownership=own)
    with patch.object(c.Engine,'market_price',return_value=D(50)):basis=trace_current_basis(c,e)
    self.assertEqual(asked,[incoming.ref]);self.assertEqual(basis[new]['cost'],D(100));self.assertEqual(basis[new]['qty'],D(2))
 def test_zero_and_smsf_external_wallets_do_not_seed_balance_dependencies(self):
    c=load_core();rows={name:[c.WalletTx(name,1,date(2022,1,1),Decimal(1),Decimal(0),balance,'')] for name,balance in [('Closed',Decimal(0)),('SMSF',Decimal(1))]};own=c.WalletOwnership('Owner');own.wallet_statuses.update(Closed=own.PERSONAL,SMSF=own.EXTERNAL);own.bind_wallets(rows);own.decide_incoming_source=lambda *a,**k:self.fail('Unrelated wallet was traced')
    e=c.Engine('Owner',2026,rows,c.ReportSet(),ownership=own);self.assertEqual(trace_current_basis(c,e),{})
 def test_balances_and_answers_persist_only_for_the_same_input_context(self):
    with tempfile.TemporaryDirectory() as d:
      store=BalanceStore(Path(d)/'config.cfg');result={'balances':{'wallets':[{'name':'Wallet','kind':'wallet','owner_name':'Owner','smsf':False}]},'trace_answers':[['id','source',"('tx', ())",False]]};store.save('context',result)
      self.assertEqual(BalanceStore(store.config).load('context')['result'],result);self.assertIsNone(store.load('changed'))
      self.assertEqual(store.personal('context',{'name':'Owner'})['balances']['wallets'][0]['ownership'],'personal')
      self.assertEqual(store.personal('context',{'name':'Other'})['balances']['wallets'][0]['ownership'],'external')
 def test_answer_keys_are_profile_scoped_and_serialisable(self):
    c=load_core();console=type('Console',(),{'profile':'a'})();answers=install_trace_answers(c,console);own=c.WalletOwnership('Owner');key=('tx',('address',));own._run_trace_remember('source',key,False)
    restored=json.loads(json.dumps([[*k,v] for k,v in answers.items()]));answers.clear();answers.update({tuple(row[:3]):row[3] for row in restored})
    self.assertIs(own._run_trace_lookup('source',key),False);console.profile='b';self.assertIsNone(own._run_trace_lookup('source',key))
 def test_family_balance_job_costs_receive_only_holdings_without_generating_tax_reports(self):
    from balance_trace import run
    from family_tax import FamilyCalculation
    from preferences import resource_id
    from datetime import datetime,timezone
    c=load_core();name='Unspent';uid='a'*32;profile={'id':uid,'name':'Owner'};row=c.WalletTx(name,1,c.TransactionDate(datetime(2022,12,21,tzinfo=timezone.utc)),Decimal(2),Decimal(0),Decimal(2),'')
    snapshot={'users':[profile],'ownership':[{'id':resource_id('wallet',name),'kind':'wallet','source':name,'name':name,'owner_id':uid,'external':False,'smsf':False,'confirmed':True}]}
    accounts={uid:{'directory':None,'reports':c.ReportSet()}};console=type('Console',(),{'profile':uid})()
    calc=FamilyCalculation(c,{'wallets':[]}, {name:[row]},accounts,[],snapshot,{},console,lambda *a,**k:None,lambda *a:[],now=datetime(2026,10,27,tzinfo=timezone.utc))
    with patch.object(calc,'run',side_effect=AssertionError('No jointly funded history requires annual allocation replay')),patch('builtins.input',return_value='n'),patch.object(c.Engine,'market_price',return_value=Decimal(100)),patch.object(c.INTRADAY,'rate',return_value=(Decimal(150),'fixture')):
      balances=run(c,calc,[],snapshot,profile,console,lambda *a,**k:None,None)
    entry=balances['wallets'][0];self.assertEqual(entry['cost_basis_aud'],'200');self.assertEqual(Decimal(entry['unrealised_gain_aud']),Decimal(100));self.assertEqual(entry['owner_name'],'Owner')
 def test_watch_is_removed_only_as_a_name_token_and_status_comes_from_metadata(self):
    self.assertEqual(suggest_wallet_name('treasury_watch'),'Treasury Wallet');self.assertEqual(suggest_wallet_name('watchtower'),'Watchtower Wallet')
    rows=display_inventory([{'id':'row','kind':'descriptor','source':'key','name':'raw'}],{'wallets':[{'id':'key','name':'treasury_watch','watch_only':True}]});self.assertTrue(rows[0]['watch_only']);self.assertEqual(rows[0]['source_name'],'treasury_watch')

if __name__=='__main__':unittest.main()
