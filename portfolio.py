"""Read-only latest-record balance snapshots, separate from FY tax calculations."""
from datetime import datetime,timezone,date
from decimal import Decimal
import re
from pathlib import Path

ZERO=Decimal(0)
SAT=Decimal('0.00000001')


def retained_basis(core,engine,name,rows,balance):
    if balance==0:return ZERO,'Zero Bitcoin balance.'
    lots=engine.holdings.get(name,[])
    qty=sum((lot.qty for lot in lots),ZERO)
    if not lots or any(not lot.basis_known or lot.deferred_market_price for lot in lots):
        return None,'A complete acquisition cost is not established for this wallet.'
    historical=[row for row in rows if (core.tax_date(row.dt) if isinstance(row.dt,datetime) else row.dt)<=engine.period_end]
    if not historical or abs(historical[-1].balance-qty)>SAT:
        return None,'The tax trace covers only part of the wallet balance.'
    cost=sum((lot.cost for lot in lots),ZERO)
    for row in rows:
        if (core.tax_date(row.dt) if isinstance(row.dt,datetime) else row.dt)<=engine.period_end:continue
        if row.incoming:return None,'New receipts after this financial year have not been costed by this tax trace.'
        if row.balance<0 or qty<=0:return None,'Later records do not reconcile to the retained lots.'
        cost*=row.balance/qty;qty=row.balance
    if abs(qty-balance)>SAT:return None,'The latest reported balance does not reconcile to the retained lots.'
    return cost,'Retained acquisition costs from the tax trace; later outflows allocated proportionally.'


def exchange_basis(core,engine,balance):
    """Known recent exchange episode, never invent the cost of an unmatched deposit."""
    reports=engine.reports
    qty=(reports.start_bal.get(min(reports.years)) or ZERO) if reports.years else ZERO
    cost=ZERO if qty==0 else None
    for row in reports.btc_rows:
        if qty==0:cost=ZERO
        if row.amount>0:
            if row.buy and cost is not None:
                trade=engine._trade_for(row,'buy')
                cost=cost+trade.aud if trade else None
            else:cost=None
        elif row.amount<0:
            if cost is not None and qty>0:
                if abs(row.amount)>qty+SAT:cost=None
                else:cost*=max(ZERO,qty-abs(row.amount))/qty
        qty=row.balance
        if qty==0:cost=ZERO
    if abs(qty-balance)>SAT:cost=None
    return cost,('Complete costs from the latest reconciled exchange funding episode.' if cost is not None else 'The latest exchange funding episode includes receipts without a complete acquisition cost.')


def build_balances(core,engine,quote=None,now=None,basis=None,watched=None):
    """Never mutate engine lots, events, ownership, or tax price usage."""
    when=now or datetime.now(timezone.utc)
    error=None
    try:
        price,source=quote if quote is not None else core.INTRADAY.rate('BTC',when,context='Current dashboard valuation')
        price=Decimal(price)
        if not price.is_finite() or price<=0:raise ValueError('Invalid current BTC/AUD price')
    except Exception as exc:price=None;source='';error=str(exc)
    observed=re.search(r'observed ([^;]+)',source)
    available=re.search(r'available by ([^;]+)',source)
    age=re.search(r'age ([0-9.]+)s',source)
    snapshot={'basis':'latest supplied records, current BTC/AUD quote','valuation_utc':when.isoformat().replace('+00:00','Z'),
              'price_aud_per_btc':str(price) if price is not None else None,'quote_source':source,
              'quote_observed_utc':observed.group(1) if observed else None,'quote_available_utc':available.group(1) if available else None,'quote_age_seconds':age.group(1) if age else None,'valuation_error':error,'wallets':[]}
    def choose_basis(name,balance,cost,reason):
        candidate=(basis or {}).get(name)
        if balance==0:return ZERO,'Zero Bitcoin balance.'
        if candidate and abs(candidate['qty']-balance)<=SAT:
            return candidate['cost'],candidate['reason']
        return cost,reason
    def add(name,kind,balance,last,rows,cost,reason,cash=ZERO,ownership='unknown',record=''):
        market=None if balance is None else balance*price if price is not None else (ZERO if balance==0 else None)
        value=market+cash if market is not None else None
        gain=market-cost if cost is not None and market is not None else None
        status='unavailable' if gain is None else ('profit' if core.qmoney(gain)>0 else 'loss' if core.qmoney(gain)<0 else 'neutral')
        snapshot['wallets'].append({'name':name,'kind':kind,'btc':str(balance) if balance is not None else None,'cash_aud':str(cash),
           'value_aud':str(value) if value is not None else None,'btc_value_aud':str(market) if market is not None else None,
           'cost_basis_aud':str(cost) if cost is not None else None,'unrealised_gain_aud':str(gain) if gain is not None else None,
           'state':status,'cost_basis_note':reason,'last_update_utc':core.display_time(last) if last is not None else None,
           'ownership':ownership,'record_count':rows,'last_record':record})
    for name,original in sorted(engine.wallets.items(),key=lambda item:item[0].casefold()):
        if name in getattr(engine,'synthetic_accounts',{}):continue
        if not original:
            if name in engine.ownership.descriptor_wallet_ids:add(name,'wallet',ZERO,None,0,ZERO,'No confirmed blockchain transactions for this descriptor wallet.',ownership=engine.ownership.wallet_statuses.get(name,'unknown'))
            continue
        rows=sorted(original,key=lambda row:(core.utc_time(row.dt),row.row_num))
        last=rows[-1]
        is_ods=all(getattr(row,'is_historical_ods',False) for row in rows)
        if is_ods and last.balance==0:continue
        cost,reason=retained_basis(core,engine,name,rows,last.balance)
        cost,reason=choose_basis(name,last.balance,cost,reason)
        balance=last.balance
        watch=(watched or {}).get(name) if is_ods and watched is not None else None
        if is_ods and watched is not None:
            if watch and watch['status']=='live':
                balance=Decimal(watch['btc'])
                if cost is not None and watch['original_outputs_only']:cost=cost*balance/last.balance
                elif balance==0:cost=ZERO
                else:cost=None;reason='Live addresses contain outputs not represented in the supplied acquisition records.'
            else:balance=None;cost=None;reason='Live watched-address balance unavailable; recorded ODS values are not presented as live.'
        display_name=name
        add(display_name,'wallet',balance,last.dt,len(rows),cost,reason,ownership=engine.ownership.wallet_statuses.get(name,'unknown'),record=last.txid)
        entry=snapshot['wallets'][-1]
        entry['balance_mode']='Live watched addresses' if is_ods and watched is not None else 'Confirmed descriptor blockchain history' if not is_ods else 'Supplied ODS entry'
        if watch:entry.update(watch_checked_utc=watch['checked_utc'],recorded_btc=watch['recorded_btc'],watched_addresses=watch.get('addresses',[]),watch_error=watch.get('error'),confirmed_btc=watch.get('confirmed_btc'),unconfirmed_btc=watch.get('unconfirmed_btc'),watch_sources=watch.get('sources',[]))
    reports=engine.reports
    if reports.btc_rows or reports.years:
        row=reports.btc_rows[-1] if reports.btc_rows else None
        latest=max(reports.years) if reports.years else None
        balance=reports.close_bal.get(latest) if latest is not None else None
        if balance is None:balance=row.balance if row else ZERO
        cash=reports.aud_rows[-1].balance if reports.aud_rows else ZERO
        cost,reason=exchange_basis(core,engine,balance)
        cost,reason=choose_basis('Bitaroo',balance,cost,reason)
        entries=[r for r in reports.btc_rows+reports.aud_rows if getattr(r,'dt',None) is not None]
        last=max(entries,key=lambda r:core.utc_time(r.dt)) if entries else row
        reference=(engine.bitaroo_transaction_reference(last) if any(last is r for r in reports.btc_rows) else f'PDF:{Path(last.source_file).name}:page{last.page} (AUD ledger)') if last else ''
        add('Bitaroo Exchange','exchange',balance,last.dt if last else None,len(entries),cost,reason,cash=cash,ownership='personal',record=reference)
    return snapshot


def replay_current_basis(core,engine):
    """Reuse core lot movements across all supplied dates without recording tax events.

    The selected-FY engine is never advanced. This independent ledger omits tax
    reform events: unrealised performance compares current value with acquisition
    costs, rather than projecting a tax liability. External wallets stay uncosted.
    """
    from datetime import timedelta
    dates=[w.dt for rows in engine.wallets.values() for w in rows]
    dates += [core.tax_date(r.dt) for r in engine.reports.btc_rows]
    dates += [r.dt for r in engine.other_exchange_rows]
    if not dates:return {}
    newest=max(dates)
    latest=date(newest.year,newest.month,newest.day)
    earliest=min(engine.reports.years) if engine.reports.years else min(dates).year
    before_replace=core.replace
    shadow=getattr(engine,'basis_engine_class',core.Engine)(engine.taxpayer,earliest,engine.wallets,engine.reports,ownership=engine.ownership,
                       other_exchange_rows=engine.other_exchange_rows)
    # Reconstruct the first supplied statement's opening episode, once only.
    shadow.reset_bitaroo_to_target_opening()
    shadow.period_start=latest+timedelta(days=1)
    shadow.period_end=latest
    core.replace=getattr(engine,'_original_replace',core.replace)
    try:
        ledger=shadow.build_events()
        for event in ledger:
            if event.kind=='transition_marker':continue
            day=core.tax_date(event.when)
            shadow.seed_other_exchange_acquisitions(through_date=day,through_instant=core.utc_time(event.when))
            if event.kind=='bitaroo_fee':
                _,fee_row=event.payload
                shadow._take('Bitaroo',abs(fee_row.amount),'Current balance withdrawal fee',core.tax_date(fee_row.dt))
            else:shadow.process_event(event)
        # The last acquisition may have no subsequent transfer/disposal event.
        shadow.seed_other_exchange_acquisitions(through_date=latest)
        result={}
        for name,lots in shadow.holdings.items():
            if engine.ownership.wallet_statuses.get(name)==engine.ownership.EXTERNAL:continue
            known=True;reasons=[]
            for lot in lots:
                if not lot.basis_known:known=False;reasons.append('Some receipt acquisition costs are unresolved.');continue
                shadow._resolve_deferred_wallet_ownership(lot)
                shadow._resolve_deferred_source_ownership(lot)
                if lot.deferred_market_price:
                    try:
                        price=shadow.market_price(lot.acquired,'Current balance acquisition value for '+name)
                        lot.cost=lot.qty*price;lot.deferred_market_price=False
                    except (RuntimeError,ValueError,OSError) as exc:
                        known=False;reasons.append(str(exc))
            qty=sum((lot.qty for lot in lots),ZERO)
            cost=sum((lot.cost for lot in lots),ZERO) if known else None
            result[name]={'qty':qty,'cost':cost,'reason':'Acquisition costs replayed across all supplied ledger entries.' if known else ' '.join(dict.fromkeys(reasons))}
        return result
    finally:
        if hasattr(shadow,'close_observer'):shadow.close_observer()
        core.replace=before_replace
