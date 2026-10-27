"""Independent balance provenance; never publish a CGT report from this process."""
from datetime import datetime,timezone,date
from decimal import Decimal
from copy import deepcopy
from portfolio import build_balances
ZERO=Decimal(0)


def trace_current_basis(core,engine):
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
    core.refresh_wallet_metadata(engine.wallets,engine.reports)
    planner=core.HistoryPlanner(engine.wallets,earliest,engine.ownership,engine.other_exchange_rows,engine.reports)
    first=min(dates)
    planner.start=date(first.year if first.month>=7 else first.year-1,7,1);planner.end=latest
    for name,rows in engine.wallets.items():
        if rows and abs(rows[-1].balance)>core.BTC_EPS and engine.ownership.wallet_statuses.get(name)==engine.ownership.PERSONAL:
            planner.enqueue_wallet(name,len(rows)-1,trace_source='Balance Trace')
    # Existing exchange episodes supply purchase cost for retained BTC.
    exchange_rows=engine.reports.btc_rows
    last_zero=max((i for i,row in enumerate(exchange_rows[:-1]) if abs(row.balance)<=core.BTC_EPS),default=-1)
    seed_exchange=bool(exchange_rows and exchange_rows[-1].balance>core.BTC_EPS)
    for row in (exchange_rows[last_zero+1:] if seed_exchange else []):
        if row.amount<0:
            kind='withdrawal' if row.tx_type=='Withdrawal' else 'fee' if row.tx_type=='Withdrawal Fee' else 'sale' if row.sell else None
            if kind:planner.enqueue_bitaroo(planner.fy_for_date(core.tax_date(row.dt)),row.dt,abs(row.amount),kind,trace_source='Balance Trace')
    while planner.wallet_queue or planner.bitaroo_queue:
        planner.process_wallet_queue();planner.process_bitaroo_queue(engine.reports)
    before_replace=core.replace
    shadow=getattr(engine,'basis_engine_class',core.Engine)(engine.taxpayer,earliest,engine.wallets,engine.reports,ownership=engine.ownership,
                       other_exchange_rows=engine.other_exchange_rows,active_wallet_rows=planner.required_wallet_rows)
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
            result[name]={'qty':qty,'cost':cost,'reason':'Acquisition costs established by Balance Trace for retained holdings.' if known else ' '.join(dict.fromkeys(reasons))}
        return result
    finally:
        if hasattr(shadow,'close_observer'):shadow.close_observer()
        core.replace=before_replace

def run(core,calculation,joints,snapshot,user,console,emit,client):
    """Cost live holdings for all owners, sharing the existing joint-accounting rules."""
    from ownership import apply_ownership
    from family_tax import joint_engine,financial_year
    from watch import watch_wallets
    from descriptor_chain import annotate_balances as descriptor_balances
    import joint_allocations as joint
    class BalanceOwnership(calculation.base_ownership):
        def decide_incoming_source(self,row,trace_source='Balance Trace'):
            return super().decide_incoming_source(row,trace_source='Balance Trace')
        def decide_unresolved_destination(self,origin,dt,amount,transaction_id,address='',trace_source='Balance Trace',decision_ref=None):
            return super().decide_unresolved_destination(origin,dt,amount,transaction_id,address,'Balance Trace',decision_ref)
    calculation.base_ownership=BalanceOwnership
    calculation.joints=joints
    if joints:
        # Resolve the established annual-prefix convention once for ambiguous
        # jointly funded history. These working replays are not published as tax reports.
        _,initial=calculation.run(joints,user)
        for engine in initial.values():engine.close_observer()
    snapshots={};quote=None;watched=None
    for profile in calculation.users:
        uid=profile['id'];console.profile=uid
        emit('stage',stage='balances',label='Balance Trace · '+profile['name'])
        core.WalletOwnership=calculation.base_ownership
        wallets=calculation.physical_rows(calculation.now)
        # Joint sources/recipients retain Core names. Capture their stable-ID
        # mapping before display-name application mutates DESCRIPTOR_WALLETS.
        descriptor_names=dict(getattr(core,'DESCRIPTOR_WALLETS',{}))
        own,other=apply_ownership(core,snapshot,profile,wallets,calculation.other,require_bitaroo=bool(calculation.accounts[uid].get('directory')))
        labels={name:calculation.snapshot_record('descriptor',ident)['name'] for name,ident in descriptor_names.items()}
        accounts=calculation.account_prefixes(calculation.now)
        wallets,synthetic=joint.apply(core,wallets,joints,calculation.allocations,labels,calculation.users,profile,accounts)
        for name,owner in synthetic.items():own.wallet_statuses[name]=own.PERSONAL if owner==uid else own.EXTERNAL
        own.bind_wallets(wallets)
        # Expanded joint rows can move row numbers. Match public ODS evidence
        # by the owned transaction identity, as the tax replay already does.
        supplied=calculation._historical_evidence;rebuilt={};by_tx={}
        for (source,_),item in supplied.get('evidence',{}).items():by_tx.setdefault((source,item.get('blockchain_txid')),item)
        for name,rows in wallets.items():
            for row in rows:
                evidence=by_tx.get((name,row.txid))
                if evidence:rebuilt[row.ref]=deepcopy(evidence)
        core.HISTORICAL_OUTPOINTS=dict(supplied,evidence=rebuilt)
        Engine=joint_engine(core,calculation.policies)
        current=Engine(profile['name'],financial_year(core,core.TransactionDate(calculation.now)),wallets,accounts[uid]['reports'],ownership=own,other_exchange_rows=other)
        current.basis_engine_class=Engine;current.synthetic_accounts=synthetic
        try:
            if watched is None:watched=watch_wallets(core,current,client=client)
            basis=trace_current_basis(core,current)
            balance=build_balances(core,current,basis=basis,watched=watched,quote=quote)
            if balance.get('price_aud_per_btc'):quote=(balance['price_aud_per_btc'],balance['quote_source'])
            for entry in balance['wallets']:
                record=own.family_records.get(entry['name'])
                if record:entry.update(owner_name=own.family_users.get(record.get('owner_id'),'Someone else / external'),smsf=record.get('smsf',False))
                elif entry['kind']=='exchange':entry.update(owner_name=profile['name'],smsf=False)
            descriptor_balances(balance,core.DESCRIPTOR_SNAPSHOT);snapshots[uid]=balance
        finally:current.close_observer()
    merged=snapshots[user['id']]
    owners={r['name']:r for r in snapshot['ownership'] if r.get('confirmed')}
    for entry in merged['wallets']:
        identity=owners.get(entry['name'])
        if not identity or identity.get('smsf') or identity.get('external'):continue
        source=next((r for r in snapshots.get(identity.get('owner_id'),{}).get('wallets',[]) if r['name']==entry['name']),None)
        if source:
            for key in ('cost_basis_aud','unrealised_gain_aud','state','cost_basis_note'):entry[key]=source[key]
    for profile in calculation.users:
        if profile['id']==user['id']:continue
        entry=next((r for r in snapshots[profile['id']]['wallets'] if r['kind']=='exchange'),None)
        if entry:merged['wallets'].append(dict(entry,name='Bitaroo Exchange · '+profile['name'],owner_name=profile['name'],ownership='external'))
    return merged
