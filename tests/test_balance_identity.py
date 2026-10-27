"""Balance Trace uses the same stable identity and unresolved questions as CGT."""
import sys,unittest
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal as D
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worker import load_core
from balance_trace import run,trace_current_basis
from family_tax import FamilyCalculation
from preferences import resource_id

class BalanceIdentityTests(unittest.TestCase):
 def test_joint_payment_uses_assigned_display_names_for_every_family_profile(self):
    c=load_core();profiles=[{'id':'1'*32,'name':'First Owner'},{'id':'2'*32,'name':'Second Owner'}]
    raw=['source_one','source_two_watch','recipient_three'];labels=['One Wallet','Two Wallet','Three Wallet']
    chain={'wallets':[{'id':str(i)*32,'name':name,'records':[],'pending_btc':'0'} for i,name in enumerate(raw,3)],'transactions':{}}
    records=[{'id':resource_id('descriptor',w['id']),'kind':'descriptor','source':w['id'],'name':label,'owner_id':profiles[i%2]['id'],'external':False,'smsf':False,'confirmed':True} for i,(w,label) in enumerate(zip(chain['wallets'],labels))]
    snapshot={'users':profiles,'ownership':records};accounts={u['id']:{'directory':None,'reports':c.ReportSet()} for u in profiles};console=type('Console',(),{'profile':None})()
    calc=FamilyCalculation(c,chain,{},accounts,[],snapshot,{},console,lambda *a,**k:None,lambda *a:[],now=datetime(2026,10,27,tzinfo=timezone.utc))
    tx='e'*64;joint={'txid':tx,'utc':'2026-01-01T00:00:00+00:00','sources':raw[:2],'fees':[1,1],'budgets':[99,99],'destinations':[{'receiver':raw[2],'value':198,'index':0,'address':'fixture'}],'bridges':[]};calc.allocations={tx:[[99],[99]]}
    checked=[]
    def check(core,engine):
      for name in engine.wallets:engine.ownership.planning_wallet_status(name,require_ownership=True)
      self.assertFalse(set(raw)&set(engine.wallets),'Raw Core names must not create extra unassigned wallets')
      for name in labels:
        self.assertIn(name,engine.wallets)
        engine.ownership.planning_wallet_status(name,require_ownership=True)
        self.assertTrue(all(row.sheet==name for row in engine.wallets[name]))
      self.assertEqual(sum(r.principal for r in engine.wallets[labels[2]]),D('0.00000198'))
      checked.append(engine.taxpayer);return {}
    with patch.object(calc,'run',return_value=({},{})),patch('balance_trace.trace_current_basis',side_effect=check),patch('watch.watch_wallets',return_value={}),patch('balance_trace.build_balances',return_value={'wallets':[]}):
      run(c,calc,[joint],snapshot,profiles[0],console,lambda *a,**k:None,None)
    self.assertEqual(checked,[u['name'] for u in profiles])

 def scenario(self,answers):
    c=load_core();name='Assigned Wallet';stamp=lambda day:c.TransactionDate(datetime(2022,1,day,tzinfo=timezone.utc))
    incoming=c.WalletTx(name,1,stamp(1),D(2),D(0),D(2),'',txid='a'*64)
    outgoing=c.WalletTx(name,2,stamp(2),D(-1),D(0),D(1),'',address='unresolved-destination',txid='b'*64)
    own=c.WalletOwnership('Owner');own.wallet_statuses[name]=own.PERSONAL;own.bind_wallets({name:[incoming,outgoing]})
    engine=c.Engine('Owner',2026,{name:[incoming,outgoing]},c.ReportSet(),ownership=own)
    return c,engine,patch('builtins.input',side_effect=answers)

 def test_unknown_source_and_destination_ask_before_finishing_balances(self):
    c,e,answer=self.scenario(['n','n'])
    with answer as prompt,patch.object(c,'lookup_transaction_input_addresses',return_value=[]),patch.object(c,'lookup_transaction_online',return_value=None),patch.object(c.Engine,'market_price',return_value=D(100)):
      result=trace_current_basis(c,e)
    self.assertEqual(prompt.call_count,2);self.assertEqual(result['Assigned Wallet']['cost'],D(100));self.assertEqual(result['Assigned Wallet']['qty'],D(1))

 def test_confirmed_owned_missing_source_stops_after_the_question(self):
    c,e,answer=self.scenario(['y'])
    with answer as prompt,patch.object(c,'lookup_transaction_input_addresses',return_value=[]),self.assertRaises(c.MissingWalletDataError):trace_current_basis(c,e)
    self.assertEqual(prompt.call_count,1)

 def test_confirmed_owned_missing_destination_stops_after_the_question(self):
    c,e,answer=self.scenario(['n','y'])
    with answer as prompt,patch.object(c,'lookup_transaction_input_addresses',return_value=[]),patch.object(c,'lookup_transaction_online',return_value=None),self.assertRaises(c.MissingDestinationDataError):trace_current_basis(c,e)
    self.assertEqual(prompt.call_count,2)

if __name__=='__main__':unittest.main()
