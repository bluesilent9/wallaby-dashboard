"""CGT on the non-fiat asset exchanged for BTC, independently of BTC tracing.

Trade consideration comes from the supplied exchange quantities and BTC/AUD.
Supplied purchases establish acquisition facts first. For untraced BCH only,
the V155 user-requested convention assumes the 2017 investment chain split
and zero AUD acquisition cost. Other assets retain the existing questions.
"""
from copy import deepcopy
from decimal import Decimal
from datetime import date,datetime


# Explicit asset-level convention requested for V155, not a wallet/exchange
# identity or evidence that every possible BCH holding was fork-acquired.
# Known purchase parcels always retain their supplied cost and acquisition date.
FORK_ACQUISITION_DEFAULTS = {'BCH': date(2017,8,1)}


def assumed_fork_basis(core,row,quantity):
    acquired=FORK_ACQUISITION_DEFAULTS.get(row.currency)
    if acquired is None:return None
    if row.dt<acquired:
        raise ValueError(f'{row.currency} disposal predates its configured fork acquisition date {acquired}. Correct the exchange record.')
    note=f'V155 accounting assumption: otherwise untraced {row.currency} was received as an investment at the {acquired} chain split with zero AUD acquisition cost.'
    answer={'assumption':note,'lots':[{'acquired':acquired.isoformat(),'quantity':str(quantity),'cost_aud':'0','chain_split':True}]}
    parcel={'acquired':acquired,'quantity':quantity,'cost':Decimal(0),'chain_split':True,'transition':None}
    return [parcel],answer,note


def validate_basis(core, answer, quantity, disposal):
    if not isinstance(answer,dict) or not isinstance(answer.get('lots'),list) or not 1<=len(answer['lots'])<=100:
        raise ValueError('Supply at least one acquisition parcel for this asset.')
    result=[]
    for item in answer['lots']:
        when=core.TransactionDate(datetime.fromisoformat(str(item['acquired']).replace('Z','+00:00')))
        qty=Decimal(str(item['quantity']));cost=Decimal(str(item['cost_aud']))
        if not qty.is_finite() or qty<=0 or not cost.is_finite() or cost<0 or core.utc_time(when)>core.utc_time(disposal):
            raise ValueError('Acquisitions need a positive quantity, nonnegative AUD cost and a date no later than the disposal.')
        split=item.get('chain_split') is True
        if split and cost!=0:raise ValueError('An investment received from a chain split has a zero acquisition cost base.')
        transition=None
        if disposal>=core.REFORM_DATE and when<=core.REFORM_EVE:
            transition=Decimal(str(item.get('transition_aud_per_unit','')))
            if not transition.is_finite() or transition<0:raise ValueError('Supply the asset’s transition AUD value per unit.')
        result.append(dict(acquired=when,quantity=qty,cost=cost,chain_split=split,transition=transition))
    if sum((r['quantity'] for r in result),Decimal(0))!=quantity:
        raise ValueError('Acquisition parcel quantities must equal the untraced sold quantity, including the asset’s network fee.')
    return result


def calculate(core,engine,rows,request_basis,transition_quotes=None):
    """Return asset events, paths and valuations for this owned annual prefix."""
    pools={};events=[];paths={};facts=[]
    transition_quotes={} if transition_quotes is None else transition_quotes
    def take(unit,quantity):
        pool=pools.setdefault(unit,[]);total=sum((p.qty for p in pool),Decimal(0))
        if quantity>total:raise ValueError('Incomplete non-Bitcoin acquisition records.')
        moved=[];remaining=[]
        for lot in pool:
            qty=quantity*lot.qty/total
            part=core.replace(lot,qty=qty,cost=lot.cost*qty/lot.qty)
            if lot.transition_mv is not None:
                part.transition_mv=lot.transition_mv*qty/lot.qty;part.transition_gain=part.transition_mv-part.cost
            moved.append(part)
            if lot.qty>qty:
                kept=core.replace(lot,qty=lot.qty-qty,cost=lot.cost-part.cost)
                if lot.transition_mv is not None:
                    kept.transition_mv=lot.transition_mv-part.transition_mv;kept.transition_gain=kept.transition_mv-kept.cost
                remaining.append(kept)
        pools[unit]=remaining;return moved
    def record(row,lots,proceeds,description):
        if not engine.period_start<=row.dt<=engine.period_end:return
        if row.dt>=core.REFORM_DATE:
            for lot in lots:
                if lot.acquired<=core.REFORM_EVE and lot.transition_mv is None:
                    if row.currency not in transition_quotes:
                        print('\nASSET TRANSITION VALUATION — '+row.currency+'\nSupply this asset’s AUD value per unit at '+str(core.REFORM_DATE)+'. Do not supply a Bitcoin price for another asset.')
                        quote=Decimal(core.input('Transition AUD value per '+row.currency+': '))
                        if not quote.is_finite() or quote<0:raise ValueError('The transition value must be a finite nonnegative AUD amount.')
                        transition_quotes[row.currency]=quote
                    lot.transition_mv=lot.qty*transition_quotes[row.currency];lot.transition_gain=lot.transition_mv-lot.cost
        start=len(engine.events);total=sum((p.qty for p in lots),Decimal(0))
        # Reuse the same holding-period, discount, indexation and annual-loss
        # arithmetic; these asset parcels never enter Bitcoin holdings.
        core.Engine._record_sale(engine,row.dt,description,row.exchange,lots,proceeds,total)
        created=engine.events[start:];del engine.events[start:]
        for event in created:
            event.asset_unit=row.currency
            event.notes=[note.replace(' BTC', ' '+row.currency).replace('BTC/AUD',row.currency+'/AUD') for note in event.notes]
            event.notes.insert(1,'EXCHANGE CONSIDERATION: supplied BTC received × transaction-time BTC/AUD; no inferred crypto/crypto execution rate.')
            events.append(event)
            group=[]
            for lot in lots:
                if event.bucket=='held >= 12 months' and not core.held_twelve_months(lot.acquired,row.dt):continue
                if event.bucket=='held < 12 months' and core.held_twelve_months(lot.acquired,row.dt):continue
                acquired=core.display_time(lot.acquired) if core.has_precise_time(lot.acquired) else lot.acquired.isoformat();disposed=core.display_time(row.dt)
                origin={'record_type':'Asset acquisition','asset_unit':row.currency,'asset_quantity':str(lot.qty),'cost_base_aud':str(lot.cost),'utc':acquired,'location':lot.source}
                trade={'record_type':'Crypto exchange disposal','asset_unit':row.currency,'asset_quantity':str(lot.qty),'btc_received':str(row.btc),'utc':disposed,'location':row.exchange,'source_row':row.row_num,'proceeds_aud':str(proceeds),'network_fee':str(row.network_fee),'network_fee_unit':row.network_fee_unit}
                group.append({'asset_unit':row.currency,'btc':str(lot.qty),'allocated_btc':str(lot.qty),'allocated_cost_aud':str(lot.cost),'nodes':[
                    {'label':lot.source,'kind':'wallet','stage':'acquisition','utc':acquired,'details':origin,'incoming':origin,'outgoing':None},
                    {'label':row.exchange,'kind':'exchange','stage':'disposal','utc':disposed,'details':trade,'incoming':trade,'outgoing':None}]})
            paths[id(event)]=group
    ordered=sorted((r for r in rows if not core.exchange_asset_is_fiat(r) and r.dt<=engine.period_end),key=lambda r:(core.utc_time(r.dt),r.exchange,r.row_num))
    for row in ordered:
        unit=row.currency
        if row.sell:
            # The spreadsheet's received amount is net. Its market value is the
            # already-valued BTC consideration attributed to that acquisition.
            _,value,_=engine.other_exchange_aud_value(row)
            pools.setdefault(unit,[]).append(core.Lot('asset:'+repr(row.ref),row.dt,row.fiat,value,row.exchange+' acquisition'))
            continue
        if not row.buy:continue
        quantity=abs(row.fiat);fee=sum((abs(amount) for _,amount,fee_unit in row.fees if fee_unit==unit),Decimal(0))
        if not quantity:raise ValueError('A crypto-to-BTC exchange must specify the quantity sold.')
        needed=quantity+fee;available=sum((p.qty for p in pools.get(unit,[])),Decimal(0))
        if available<needed:
            missing=needed-available
            automatic=assumed_fork_basis(core,row,missing)
            if automatic is not None:
                parcels,answer,assumption=automatic
            else:
                answer=request_basis(row,missing)
                parcels=validate_basis(core,answer,missing,row.dt)
                assumption=''

            for index,p in enumerate(parcels):
                label=unit+(' investment chain split' if p['chain_split'] else ' acquisition')
                lot=core.Lot('asset:'+repr(row.ref)+':'+str(index),p['acquired'],p['quantity'],p['cost'],label,trace=assumption)
                if p['transition'] is not None:lot.transition_mv=p['transition']*lot.qty;lot.transition_gain=lot.transition_mv-lot.cost
                pools.setdefault(unit,[]).append(lot)
            facts.append({'exchange':row.exchange,'row':row.row_num,'asset_unit':unit,'answer':deepcopy(answer)})
        if row.dt<engine.period_start:
            take(unit,needed);continue
        price=engine.market_price(row.dt,row.exchange+' '+unit+' disposal consideration')
        proceeds=(row.btc+row.btc_fee_amount)*price
        record(row,take(unit,quantity),proceeds,row.exchange+' '+unit+' sale for BTC')
        if fee:
            value=fee*proceeds/quantity
            record(row,take(unit,fee),value,row.exchange+' '+unit+' network fee')
    return events,paths,facts
