import sys,unittest,tempfile,io
from pathlib import Path
from datetime import datetime,date,timezone
from decimal import Decimal as D
from unittest.mock import patch
from contextlib import redirect_stdout
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wallet_names import source_suggestions,suggest_wallet_name
from preferences import FamilyStore,resource_id
from worker import load_core
from family_tax import joint_engine
from balance_trace import trace_current_basis

class Build5Tests(unittest.TestCase):
 def test_owner_suggestions_use_original_names_and_require_one_unambiguous_match(self):
    users=[{'id':'a','name':'Alice'},{'id':'b','name':'Bob'}]
    item={'kind':'descriptor','source':'opaque-id','source_name':'alice_smsf_watch','name':'Bob Wallet'}
    self.assertEqual(source_suggestions(item,users),{'suggested_owner_id':'a','smsf_detected':True})
    self.assertEqual(suggest_wallet_name('alice_smsf_watch'),'Alice SMSF Wallet')
    self.assertIsNone(source_suggestions(dict(item,source_name='malice'),users)['suggested_owner_id'])
    self.assertIsNone(source_suggestions(dict(item,source_name='alice_bob_shared'),users)['suggested_owner_id'])
    self.assertFalse(source_suggestions(dict(item,source_name='alice',name='Bob SMSF Wallet'),users)['smsf_detected'])
 def test_detected_smsf_is_enforced_by_server_even_if_checkbox_payload_is_false(self):
    with tempfile.TemporaryDirectory() as d:
      store=FamilyStore(Path(d)/'config.cfg');store.save_user('Alice','green',revision=store.snapshot()['revision']);data=store.snapshot();uid=data['users'][0]['id']
      item={'id':resource_id('descriptor','key'),'kind':'descriptor','source':'key','source_name':'alice_smsf','name':'Alice SMSF Wallet'}
      store.save_ownership([{'id':item['id'],'name':'Edited label','owner_id':uid,'external':False,'smsf':False}],data['revision'],[item])
      self.assertTrue(store.snapshot()['ownership'][0]['smsf'])
 def test_trace_dates_show_precise_utc_and_do_not_invent_date_only_time(self):
    c=load_core();own=c.WalletOwnership('Owner');stamp=c.TransactionDate(datetime(2021,5,13,3,45,56,tzinfo=timezone.utc));row=c.WalletTx('Wallet',1,stamp,D(1),D(0),D(1),'')
    output=io.StringIO()
    with redirect_stdout(output),patch('builtins.input',return_value='n'):own.decide_incoming_source(row)
    self.assertIn('Date UTC: 13/05/2021\nTime UTC: 03:45:56',output.getvalue())
    output=io.StringIO()
    with redirect_stdout(output),patch('builtins.input',return_value='n'):own.decide_unresolved_destination('Wallet',date(2021,5,14),D(1),'another')
    self.assertIn('Date UTC: 14/05/2021\nTime UTC: Not supplied',output.getvalue())
 def test_balance_joint_parcel_policy_matches_cgt_with_deferred_historical_prices(self):
    c=load_core();stamp=lambda day:c.TransactionDate(datetime(2022,1,day,tzinfo=timezone.utc));tx='f'*64;flow=tx+':0:0'
    rows={'Sender':[c.WalletTx('Sender',1,stamp(1),D(1),D(0),D(1),''),c.WalletTx('Sender',2,stamp(2),D(1),D(0),D(2),''),c.WalletTx('Sender',3,stamp(3),D(-1),D(0),D(1),'',txid=tx,accounting_flow=flow,onchain_output_amount=D(1))],'Receiver':[c.WalletTx('Receiver',1,stamp(3),D(1),D(0),D(1),'',txid=tx,accounting_flow=flow)]}
    own=c.WalletOwnership('Owner');own.wallet_statuses.update(Sender=own.PERSONAL,Receiver=own.PERSONAL);own.bind_wallets(rows)
    Engine=joint_engine(c,{tx+':source:0':'high-cost'});current=Engine('Owner',2021,rows,c.ReportSet(),ownership=own);current.basis_engine_class=Engine
    price=lambda dt,*a,**k:D(50 if dt.day==1 else 150)
    try:
      with patch('builtins.input',return_value='n'),patch.object(c.Engine,'market_price',side_effect=price):
        balances=trace_current_basis(c,current)
        for event in current.build_events():current.process_event(event)
      expected={name:sum((l.cost for l in lots),D(0)) for name,lots in current.holdings.items()}
      self.assertEqual(balances['Sender']['cost'],D(50));self.assertEqual(balances['Receiver']['cost'],D(150))
      self.assertEqual({name:v['cost'] for name,v in balances.items()},expected)
    finally:current.close_observer()

if __name__=='__main__':unittest.main()

class ExchangeSaleTests(unittest.TestCase):
 def test_bitaroo_withdrawal_funds_one_supplied_sale_without_an_unresolved_prompt(self):
    c=load_core();buy=datetime(2023,9,30,12);sale=datetime(2023,10,1,0,0,58)
    reports=c.ReportSet();reports.btc_rows=[c.BitarooRow(buy,'Buy Bitcoin',D('.01'),D('.01'),'BTC','statement.pdf',1),c.BitarooRow(sale,'Withdrawal',D('-.0022'),D('.0078'),'BTC','statement.pdf',1)]
    reports.trades=[c.Trade(buy,'buy',D('.01'),D(1000),D(0),'statement.pdf',0)]
    row=c.OtherExchangeRow(c.tax_date(sale),D('5838.4018'),D('-.0022'),'RUB','Generic Venue','input.ods',2)
    e=c.Engine('Owner',2023,{},reports,other_exchange_rows=[row]);e.ownership.decide_unresolved_destination=lambda *a,**k:self.fail('A supplied sale is not an unknown destination')
    with patch.object(c.Engine,'other_exchange_aud_value',return_value=(row.fiat,D(100),'fixture')):
      for event in e.build_events():e.process_event(event)
    self.assertEqual(len(e.events),1);self.assertEqual(e.events[0].cost,D(220));self.assertEqual(e.events[0].proceeds,D(100));self.assertEqual(e.qty('Generic Venue'),D(0));self.assertEqual(e.qty('Bitaroo'),D('.0078'))
 def test_signed_columns_accept_buys_sales_and_infer_fee_unit_from_actual_outgoing_asset(self):
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table,TableRow,TableCell
    from odf.text import P
    with tempfile.TemporaryDirectory() as d:
      doc=OpenDocumentSpreadsheet();table=Table(name='Generic');doc.spreadsheet.addElement(table)
      for vals in [('Date (UTC)','Time (UTC)','RUB','Network Fee','BTC'),('2023-09-01','12:00:00','-100','0','.01'),('2023-10-01','12:00:00','200','-.0001','-.01')]:
        r=TableRow()
        for v in vals:
          cell=TableCell(valuetype='string');cell.addElement(P(text=v));r.addElement(cell)
        table.addElement(r)
      path=Path(d)/'test.ods';doc.save(str(path));rows=load_core().load_other_exchange_ods(str(path));self.assertTrue(rows[0].buy);self.assertTrue(rows[1].sell);self.assertEqual(rows[1].network_fee_unit,'BTC')

class WalletSaleTests(unittest.TestCase):
 def test_wallet_send_carries_cost_into_one_supplied_sale(self):
    c=load_core();stamp=lambda day:c.tax_date(datetime(2023,10,day,12));name='Source Wallet'
    incoming=c.WalletTx(name,1,stamp(1),D(2),D(0),D(2),'')
    outgoing=c.WalletTx(name,2,stamp(2),D('-.5'),D(0),D('1.5'),'',txid='c'*64,fee_known=True,onchain_output_amount=D('.5'))
    row=c.OtherExchangeRow(stamp(2),D(75),D('-.5'),'AUD','Generic Exchange','data.ods',2)
    own=c.WalletOwnership('Owner');own.wallet_statuses[name]=own.PERSONAL;own.bind_wallets({name:[incoming,outgoing]});own.decide_external_address=lambda *a,**k:self.fail('Supplied sale must not trigger destination question')
    e=c.Engine('Owner',2023,{name:[incoming,outgoing]},c.ReportSet(),ownership=own,other_exchange_rows=[row])
    with patch('builtins.input',return_value='n'),patch.object(c,'lookup_transaction_online',return_value=None),patch.object(c.Engine,'market_price',return_value=D(100)),patch.object(c.Engine,'other_exchange_aud_value',return_value=(D(75),D(75),'fixture')):
      for event in e.build_events():e.process_event(event)
    self.assertEqual(len(e.events),1);self.assertEqual(e.events[0].cost,D(50));self.assertEqual(e.events[0].proceeds,D(75));self.assertEqual(e.qty(name),D('1.5'));self.assertFalse(e.review)

class OldBalanceSnapshotTests(unittest.TestCase):
 def test_old_cost_snapshots_do_not_unlock_or_become_current_when_only_evidence_is_saved(self):
    from balance_store import BalanceStore
    import json
    with tempfile.TemporaryDirectory() as d:
      store=BalanceStore(Path(d)/'config.cfg');store.path.parent.mkdir(parents=True,exist_ok=True)
      store.path.write_text(json.dumps({'schema':1,'signature':'context','result':{'balances':{'wallets':[]},'trace_graph':{'schema':1,'nodes':{},'edges':{},'claims':{}}}}))
      self.assertFalse(store.ready('context'));self.assertIsNone(store.personal('context',{'name':'Owner'}))
      store.save_evidence('context',{'schema':1,'nodes':{},'edges':{},'claims':{}},[])
      self.assertFalse(store.ready('context'));self.assertIsNotNone(store.load('context')['result']['trace_graph'])
      store.save('context',{'balances':{'wallets':[],'price':'1'}});self.assertTrue(store.ready('context'))
