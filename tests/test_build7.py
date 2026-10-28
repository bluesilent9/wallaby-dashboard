"""Historical addr() groups and C1 loss regressions, using invented fixtures only."""
import copy,sys,unittest
from pathlib import Path
from datetime import datetime,timezone,date
from decimal import Decimal as D
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vendor.embit.descriptor.checksum import checksum
from descriptor_policy import parse_policy
from descriptor_store import clean_wallet,DescriptorStore
from labelled_wallets import expand,label_parts
from node_wallets import NodeWalletLoader
from descriptor_chain import scan,HistoryClient,wallet_rows
from node_chain import configure
from capital_loss import append_loss_records
from worker import load_core
from provenance import make_engine
from test_build6 import CoreFixture
from test_node_loading import public_wallet
from wallet_names import display_inventory,display_summary
from ownership import inventory_for

NOW=datetime(2025,1,1,tzinfo=timezone.utc)
def fixed(address):
    body='addr('+address+')';return {'desc':body+'#'+checksum(body),'timestamp':0,'active':False,'internal':False}
def addresses():return [parse_policy(clean_wallet(public_wallet('Fixture',n))['raw']['descriptors'][0]['desc'].split('#')[0])[0].derive(0).script_pubkey().address() for n in range(3)]
def fixture_wallet():
    address=addresses()[0]
    return {'id':'example-id','name':'Earlier Wallet','extinct':True,'address_labels':{address:{'label':'Earlier Wallet | LOST:02/01/2020 00:00:00 UTC','lost_utc':'2020-01-02T00:00:00Z'}},'records':[{'txid':'a'*64,'utc':'2020-01-01T00:00:00Z','output_index':0,'sats':10000,'fee_sats':0,'address':address,'fee_only':False}], 'pending_btc':'0'}
def transactions():
    return {'a'*64:{'utc':'2020-01-01T00:00:00Z','inputs':[],'outputs':[{'value':10000,'address':addresses()[0]}]}}

class HistoricalGroups(unittest.TestCase):
 def test_fixed_public_policy_and_nonmainnet_rejection(self):
    addr=addresses()[0];w=clean_wallet({'wallet_name':'history','descriptors':[fixed(addr)]});p,combo,hard=parse_policy(w['raw']['descriptors'][0]['desc'].split('#')[0]);self.assertEqual(p.address,addr);self.assertFalse(combo or hard)
    with self.assertRaises(ValueError):parse_policy('addr(not-an-address)')
 def replica(self):
    a,b,_=addresses();raw={'wallet_name':'arbitrary_archive_name','descriptors':[fixed(a),fixed(b)]}
    return {'wallet':clean_wallet(raw),'private_keys_enabled':False,'rpc_name':raw['wallet_name'],'node':{'id':'node','hostname':'computer'},'history':{'lastblock':'f'*64,'transactions':[{'txid':'a'*64,'vout':0,'category':'receive','address':a,'amount':'0.1'},{'txid':'b'*64,'vout':1,'category':'receive','address':b,'amount':'0.2'},{'txid':'c'*64,'category':'send','vout':1}]}}
 def test_labels_group_addresses_and_keep_rpc_parent_identity(self):
    replica=self.replica();a,b,_=addresses()
    children=expand(replica,lambda addr:{'labels':['Earlier Wallet'+(' | LOST:02/01/2020 00:00:00 UTC' if addr==b else '')]})
    self.assertEqual(len(children),1);child=children[0];self.assertEqual(child['wallet']['name'],'Earlier Wallet');self.assertEqual(child['rpc_name'],'arbitrary_archive_name');self.assertEqual(set(child['wallet']['address_labels']),{a,b});self.assertTrue(child['wallet']['extinct'])
 def test_distinct_groups_filter_receipts_but_keep_shared_spends(self):
    replica=self.replica();a,b,_=addresses();children=expand(replica,lambda addr:{'labels':['Earlier A' if addr==a else 'Earlier B']})
    self.assertEqual(len(children),2)
    for child in children:self.assertEqual(len(child['history']['transactions']),2);self.assertEqual(child['history']['transactions'][-1]['category'],'send')
 def test_missing_labels_private_wallet_and_invalid_loss_dates_fail(self):
    with self.assertRaisesRegex(ValueError,'one wallet label'):expand(self.replica(),lambda addr:{'labels':[]})
    r=self.replica();r['private_keys_enabled']=True
    with self.assertRaisesRegex(ValueError,'private keys disabled'):expand(r,lambda addr:{'labels':['Earlier']})
    for label in ['Earlier | LOST:32/01/2020 00:00:00 UTC','Earlier | LOST:yesterday']:
      with self.assertRaises(ValueError):label_parts(label)
 def test_replicas_merge_once_and_disagreeing_loss_metadata_fails(self):
    replica=expand(self.replica(),lambda addr:{'labels':['Earlier']})[0];other=copy.deepcopy(replica);other['node']={'id':'other','hostname':'other'}
    loader=NodeWalletLoader(None);result=loader.merge([replica,other]);self.assertEqual(len(result['node_history']),3);self.assertEqual(len(result['node_sources']),2)
    a=addresses()[0];other['wallet']['address_labels'][a]['lost_utc']='2020-01-01T00:00:00Z'
    with self.assertRaisesRegex(ValueError,'loss timestamp'):loader.merge([replica,other])
 def test_node_scan_fixed_address_change_and_loss_without_any_explorer(self):
    rpc=CoreFixture();w=rpc.wallets[0];address=rpc.fund.vout[0].script_pubkey.address();row=fixed(address)
    w.update(clean_wallet({'wallet_name':w['name'],'descriptors':[row]}));w.update(extinct=True,watch_only=True,address_labels={address:{'label':'First | LOST:14/11/2023 22:23:20 UTC','lost_utc':'2023-11-14T22:23:20Z'}})
    catalog={'wallets':rpc.wallets};configure(rpc,catalog)
    with patch('urllib.request.urlopen',side_effect=AssertionError('Public lookup forbidden')):
      result=scan(catalog,HistoryClient(None),lambda x:None);rows=wallet_rows(load_core(),result)
    first=rows['First'];self.assertEqual(first[-1].balance,D(0));self.assertTrue(first[-1].cgt_loss);self.assertEqual(first[-1].principal,D('.00039'));self.assertEqual(first[-1].loss_outpoints,(rpc.send.txid().hex()+':1',));self.assertEqual(len(result['transactions']),2);self.assertEqual(result['catalog_fingerprint'],DescriptorStore.fingerprint(catalog))
    self.assertEqual(rows['Second'][-1].balance,D('.0006'))
 def test_label_names_remain_immutable_in_inventory_and_summary(self):
    c=load_core();w=fixture_wallet();c.DESCRIPTOR_WALLETS={w['name']:w['id']};c.DESCRIPTOR_SOURCE_NAMES={w['id']:w['name']};c.DESCRIPTOR_SNAPSHOT={'wallets':[w]}
    inv=inventory_for(c,{w['name']:[]},[],{'id':'user','name':'Example'});item=next(i for i in inv if i['kind']=='descriptor');self.assertTrue(item['extinct'])
    item=display_inventory([item],{'wallets':[w]})[0];self.assertEqual(item['name'],'Earlier Wallet')
    self.assertEqual(display_summary({'wallets':[w]},{'ownership':[]})['wallets'][0]['name'],'Earlier Wallet')

class LossRecords(unittest.TestCase):
 def test_separate_idempotent_loss_keeps_physical_record_and_outpoint(self):
    w=fixture_wallet();txs=transactions();append_loss_records([w],txs,NOW);append_loss_records([w],txs,NOW)
    self.assertEqual(len(w['records']),2);self.assertTrue(w['records'][-1]['cgt_loss']);self.assertEqual(w['records'][-1]['sats'],-10000);self.assertEqual(len(txs),1)
    rows=wallet_rows(load_core(),{'wallets':[w]})['Earlier Wallet'];self.assertEqual(rows[-1].balance,D(0));self.assertEqual(rows[-1].fee,D(0))
 def test_spent_after_loss_future_receipts_and_future_labels_are_errors(self):
    for mode in ('spend','receipt','future'):
      w=fixture_wallet();txs=transactions()
      if mode=='spend':txs['b'*64]={'utc':'2020-02-01T00:00:00Z','inputs':[{'txid':'a'*64,'vout':0}],'outputs':[]}
      if mode=='receipt':txs['b'*64]={'utc':'2020-02-01T00:00:00Z','inputs':[],'outputs':[{'value':1,'address':addresses()[0]}]}
      if mode=='future':w['address_labels'][addresses()[0]]['lost_utc']='2030-01-01T00:00:00Z'
      with self.subTest(mode=mode),self.assertRaises(ValueError):append_loss_records([w],txs,NOW)
 def test_prior_spent_outputs_are_not_lost_twice(self):
    w=fixture_wallet();txs=transactions();txs['b'*64]={'utc':'2020-01-01T12:00:00Z','inputs':[{'txid':'a'*64,'vout':0}],'outputs':[]}
    with self.assertRaisesRegex(ValueError,'No retained output'):append_loss_records([w],txs,NOW)

class C1Tax(unittest.TestCase):
 def setup_engine(self,year=2020,visual=False):
    c=load_core();own=c.WalletOwnership('Example');own.wallet_statuses['Earlier Wallet']=own.PERSONAL
    typ=make_engine(c) if visual else c.Engine;e=typ('Example',year,{},c.ReportSet(),ownership=own)
    return c,e
 def loss(self,c,e,year=2020,qty='0.25'):
    when=c.TransactionDate(datetime(year,8,1,tzinfo=timezone.utc));w=c.WalletTx('Earlier Wallet',1,when,-D(qty),D(0),D(0),'',cgt_loss=True,txid='C1:fixture',loss_outpoints=('a'*64+':0',),loss_label='Declared fixture loss');return c.LedgerEvent(c.utc_time(when).replace(tzinfo=None),'wallet_loss',w)
 def test_zero_proceeds_reduced_cost_no_discount_and_no_market_lookup(self):
    for year in (2020,2028):
      c,e=self.setup_engine(year);e.holdings['Earlier Wallet']=[c.Lot('one',date(2018,1,1),D(1),D(1000),'Fixture',D(3000),D(2000))]
      ev=self.loss(c,e,year)
      with patch.object(e,'market_price',side_effect=AssertionError('No loss-date market price')):e.process_event(ev);e.process_event(ev)
      self.assertEqual(len(e.events),1);loss=e.events[0];self.assertEqual((loss.proceeds,loss.cost,loss.gain_loss,loss.discounted_gain),(D(0),D(250),D(-250),D(0)));self.assertEqual(e.qty('Earlier Wallet'),D('.75'));self.assertEqual(c.annual_summary(e.events)['net_loss'],D(250))
 def test_loss_offsets_gains_before_discount_and_carries_forward(self):
    c,e=self.setup_engine();e.holdings['Earlier Wallet']=[c.Lot('one',date(2018,1,1),D(1),D(1000),'Fixture')];e.process_event(self.loss(c,e))
    gain=c.EventComponent(date(2020,9,1),'Sale','Bitaroo',D(1),D(1300),D(1000),D(300),'held >= 12 months',D(150),'Fixture',[],D(1),D(1),D(1300),gross_gain=D(300))
    s=c.annual_summary([*e.events,gain]);self.assertEqual(s['net_gain'],D(25));self.assertEqual(s['current_year_losses'],D(250))
    carry=c.annual_summary(e.events)['net_loss'];self.assertEqual(c.annual_summary([gain],carry)['net_gain'],D(25))
 def test_unknown_or_missing_holdings_fail_instead_of_claiming_invented_loss(self):
    for unknown in (False,True):
      c,e=self.setup_engine()
      if unknown:e.holdings['Earlier Wallet']=[c.Lot('unknown',date(2018,1,1),D(1),D(0),'Unknown',basis_known=False)]
      with self.subTest(unknown=unknown),self.assertRaises(ValueError):e.process_event(self.loss(c,e))
 def test_no_counterparty_or_exchange_matching_and_loss_has_own_visual_endpoint(self):
    c,e=self.setup_engine(visual=True);self.addCleanup(e.close_observer);ev=self.loss(c,e);w=ev.payload
    self.assertIsNone(c.find_loaded_wallet_counterparty({'Earlier Wallet':[w]},w.sheet,w));self.assertIsNone(c.match_bitaroo_deposit_row(c.ReportSet(),w))
    e.holdings[w.sheet]=[c.Lot('one',date(2018,1,1),D(1),D(1000),'Fixture')];e.process_event(ev)
    paths=e.visual_events[id(e.events[0])];self.assertEqual(paths[0]['nodes'][-1]['label'],'Lost access — CGT event C1');details=paths[0]['nodes'][-1]['details'];self.assertEqual(details['cgt_event'],'C1');self.assertNotIn('transaction_id',details)
 def test_build_events_keeps_loss_out_of_exchange_transfer_matching(self):
    c,e=self.setup_engine();w=self.loss(c,e).payload;e.wallets={w.sheet:[w]};e.ownership.bind_wallets(e.wallets)
    self.assertEqual([ev.kind for ev in e.build_events()],['wallet_loss'])

class C1Replay(unittest.TestCase):
 def test_balance_trace_consumes_loss_using_the_same_parcels(self):
    from balance_trace import trace_current_basis
    c=load_core();name='Earlier Wallet';stamp=lambda d:c.TransactionDate(datetime(2020,8,d,tzinfo=timezone.utc))
    received=c.WalletTx(name,1,stamp(1),D(1),D(0),D(1),'')
    lost=c.WalletTx(name,2,stamp(2),D('-.25'),D(0),D('.75'),'',cgt_loss=True,txid='C1:fixture',loss_outpoints=('a'*64+':0',))
    own=c.WalletOwnership('Example');own.wallet_statuses[name]=own.PERSONAL;own.bind_wallets({name:[received,lost]});own.decide_incoming_source=lambda *a,**k:False
    own.decide_external_address=lambda *a,**k:self.fail('A loss is not a destination')
    e=c.Engine('Example',2020,{name:[received,lost]},c.ReportSet(),ownership=own)
    with patch.object(c.Engine,'market_price',return_value=D(1000)):
      basis=trace_current_basis(c,e)
      for event in e.build_events():e.process_event(event)
    self.assertEqual(basis[name]['qty'],D('.75'));self.assertEqual(basis[name]['cost'],D(750));self.assertEqual(e.events[0].cost,D(250));self.assertEqual(e.qty(name),D('.75'))
 def test_loss_graph_links_existing_outpoints_without_fake_chain_transaction(self):
    from trace_graph import TraceGraph
    c=load_core();w=fixture_wallet();append_loss_records([w],transactions(),NOW);row=wallet_rows(c,{'wallets':[w]})[w['name']][-1];graph=TraceGraph();graph.record(c,row);before=graph.export();graph.record(c,row)
    self.assertEqual(before,graph.export());self.assertFalse(any(n['kind']=='transaction' for n in graph.nodes.values()));self.assertTrue(any(e['relation']=='declares_lost' for e in graph.edges.values()))
 def test_same_instant_transfer_precedes_loss_and_other_owner_is_not_charged(self):
    c=load_core();name='Earlier Wallet';when=c.TransactionDate(datetime(2020,8,1,tzinfo=timezone.utc));out=c.WalletTx(name,1,when,D('-.75'),D(0),D('.25'),'',txid='b'*64)
    loss=c.WalletTx(name,2,when,D('-.25'),D(0),D(0),'',cgt_loss=True,txid='C1:fixture');own=c.WalletOwnership('Example');own.wallet_statuses[name]=own.PERSONAL;own.bind_wallets({name:[out,loss]})
    e=c.Engine('Example',2020,{name:[out,loss]},c.ReportSet(),ownership=own);self.assertEqual([v.kind for v in e.build_events()],['wallet_out','wallet_loss'])
    own.wallet_statuses[name]=own.EXTERNAL;self.assertFalse(e.build_events())

if __name__=='__main__':unittest.main()
