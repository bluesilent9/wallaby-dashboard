"""Family-wide annual replay with prefix-bounded, recomputed allocations.

Nothing in this module reads or writes an allocation ledger. Each year's search
sees only that year's historical prefix. Previous-year choices live in RAM for
this calculation and are recomputed from scratch on the next calculation.
"""
from copy import deepcopy,copy
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from collections import defaultdict
from time import perf_counter
from descriptor_chain import wallet_rows
from ownership import apply_ownership
from preferences import resource_id
from provenance import make_engine
from reporting import build_report
from family_inputs import display_year
from replay_matching import matching_cache
from replay_prefix import PrefixCache
import joint_allocations as joint

ALGORITHM='1.7'

def financial_year(core,when):
    day=core.tax_date(when) if isinstance(when,datetime) else when
    return day.year-(day.month<7)

def year_cutoff(core,fy,now):
    end=core.local_to_utc(datetime(fy+1,7,1))
    return min(end,now+timedelta(microseconds=1))

def clone_reports(reports):
    """Copy mutable containers; statement/trade records are read-only in replay."""
    value=copy(reports)
    for field in ('btc_rows','aud_rows','trades'):setattr(value,field,list(getattr(reports,field)))
    for field in ('years','ranges','start_bal','close_bal'):setattr(value,field,dict(getattr(reports,field)))
    value.fee_rows=defaultdict(list,{k:list(v) for k,v in reports.fee_rows.items()})
    value.opening_lots=defaultdict(list,{k:list(v) for k,v in reports.opening_lots.items()})
    return value

def prefix_reports(core,reports,cutoff):
    """Strip later economic facts BEFORE any dependency planning or scoring."""
    value=clone_reports(reports)
    for field in ('btc_rows','aud_rows','trades'):
        setattr(value,field,[r for r in getattr(value,field) if core.utc_time(r.dt)<cutoff])
    value.fee_rows=defaultdict(list,{d:f for d,f in value.fee_rows.items() if core.utc_time(d)<cutoff})
    years={y for y in value.years if core.local_to_utc(datetime(y,7,1))<cutoff}
    for field in ('years','ranges','start_bal','close_bal','opening_lots'):
        setattr(value,field,{y:v for y,v in getattr(value,field).items() if y in years})
    value.opening_lots=defaultdict(list,value.opening_lots)
    return value

def joint_engine(core,policies,visual=True):
    """Optimised parcel selection is confined to ambiguous joint payments.

    Ordinary disposals and transfers retain the accepted proportional policy.
    Every split preserves cost, quantity, transition amounts and provenance.
    """
    if visual:
        Visual=make_engine(core)
    else:
        # Scoring needs the same tax arithmetic and event context, but none of
        # the graph paths, inspector dictionaries or Python profiling hooks.
        class Visual(core.Engine):
            visual_context=None
            def close_observer(self):pass
            def context_records(self):
                def flatten(value):
                    if isinstance(value,(tuple,list)):
                        for item in value:yield from flatten(item)
                    else:yield value
                return list(flatten(self.visual_context.payload)) if self.visual_context else []
            def process_event(self,event):
                previous=self.visual_context;self.visual_context=event
                try:return super().process_event(event)
                finally:self.visual_context=previous
    class AnnualEngine(Visual):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self._joint_groups={};self._joint_reserves={};self._prepared_joints=set();self._reserving=False
            for name,rows in self.wallets.items():
                for row in rows:
                    if row.outgoing and row.accounting_flow and not row.accounting_flow.endswith(':bridge'):
                        self._joint_groups.setdefault(row.txid,{}).setdefault(name,[]).append(row)
        def _reserve_key(self,wallet):
            row=next((r for r in self.context_records() if isinstance(r,core.WalletTx)),None)
            return (row.txid,wallet) if row and row.accounting_flow and not row.accounting_flow.endswith(':bridge') else None
        def qty(self,wallet):
            key=self._reserve_key(wallet)
            if not self._reserving and key in self._joint_reserves:
                return sum((l.qty for l in self._joint_reserves[key]),Decimal(0))
            return super().qty(wallet)
        def build_events(self):
            events=super().build_events()
            if not self._joint_groups:return events
            dependencies=joint.transaction_dependencies(core)
            def flatten(value):
                if isinstance(value,(tuple,list)):
                    for item in value:yield from flatten(item)
                else:yield value
            ordered=[];instants=defaultdict(list)
            for index,event in enumerate(events):instants[core.utc_time(event.when)].append((index,event))
            for instant,items in sorted(instants.items()):
                groups=defaultdict(list)
                for index,event in items:
                    row=next((r for r in flatten(event.payload) if isinstance(r,core.WalletTx)),None)
                    groups[row.txid if row and row.txid else 'event:'+str(index)].append((index,event))
                if not set(groups).intersection(self._joint_groups):ordered.extend(event for _,event in items);continue
                # Parent and child transactions can share one block timestamp.
                # Complete each parent group first, retaining stable event order
                # for unrelated transactions and within every group.
                for _,group in joint.dependency_order(groups,dependencies):ordered.extend(e for _,e in group)
            return ordered
        def process_event(self,event):
            # Reserve every sender's pre-transaction parcels before *any* receipt
            # from this joint transaction. Newly received outputs cannot fund
            # another input of the same Bitcoin transaction.
            def flatten(value):
                if isinstance(value,(tuple,list)):
                    for item in value:yield from flatten(item)
                else:yield value
            identities={r.txid for r in flatten(event.payload) if isinstance(r,core.WalletTx) and r.accounting_flow and not r.accounting_flow.endswith(':bridge')}
            for ident in sorted(identities-self._prepared_joints):
                self._prepared_joints.add(ident)
                for name,rows in sorted(self._joint_groups[ident].items()):
                    if self.ownership.wallet_statuses.get(name)!=self.ownership.PERSONAL:continue
                    if self.active_wallet_rows is not None and not any(r.ref in self.active_wallet_rows for r in rows):continue
                    quantity=sum((r.principal+abs(r.fee) for r in rows),Decimal(0));previous=self.visual_context
                    self.visual_context=core.LedgerEvent(rows[0].dt,'wallet_out',rows[0]);self._reserving=True
                    try:self._joint_reserves[(ident,name)]=self._select_take(name,quantity,'Reserve inputs for jointly funded transaction',rows[0].dt)
                    finally:self._reserving=False;self.visual_context=previous
            return super().process_event(event)
        def _take(self,wallet,qty,reason,dt,allow_short=False):
            key=self._reserve_key(wallet)
            if self._reserving or key not in self._joint_reserves:return self._select_take(wallet,qty,reason,dt,allow_short)
            physical=self.holdings[wallet];self.holdings[wallet]=self._joint_reserves[key]
            try:
                moved=self._select_take(wallet,qty,reason,dt,allow_short)
                self._joint_reserves[key]=self.holdings[wallet]
                return moved
            finally:self.holdings[wallet]=physical
        def _select_take(self,wallet,qty,reason,dt,allow_short=False):
            row=next((r for r in self.context_records() if isinstance(r,core.WalletTx)),None)
            key=row.txid+':source:'+row.accounting_flow.split(':')[1] if row and row.accounting_flow and not row.accounting_flow.endswith(':bridge') else ''
            policy=policies.get(key,policies.get(row.txid,'proportional')) if key else 'proportional'
            lots=list(self.holdings.get(wallet,[]));total=sum((l.qty for l in lots),Decimal(0))
            if policy!='proportional' and qty>core.BTC_EPS and qty<=total:
                for lot in lots:
                    if not lot.basis_known:continue
                    self._resolve_deferred_wallet_ownership(lot);self._resolve_deferred_source_ownership(lot)
                    if lot.deferred_market_price:self._resolve_deferred_market_price(lot,wallet,dt)
            if policy=='proportional' or qty<=core.BTC_EPS or qty>total or any(not l.basis_known or l.deferred_market_price or l.source_ownership_unresolved for l in lots):
                return super()._take(wallet,qty,reason,dt,allow_short)
            def order(lot):
                cost=lot.cost/lot.qty if lot.qty else Decimal(0)
                long=core.held_twelve_months(lot.acquired,dt)
                if policy=='long-first':return (not long,-cost,str(lot.acquired),lot.id)
                if policy=='short-first':return (long,-cost,str(lot.acquired),lot.id)
                if policy=='high-cost':return (-cost,not long,str(lot.acquired),lot.id)
                if policy=='low-cost':return (cost,not long,str(lot.acquired),lot.id)
                if policy=='oldest-first':return (core.utc_time(lot.acquired),-cost,lot.id)
                if policy=='newest-first':return (-core.utc_time(lot.acquired).timestamp(),-cost,lot.id)
                raise ValueError('Unknown joint parcel selection policy.')
            moved=[];kept=[];remaining=qty
            for lot in sorted(lots,key=order):
                take=min(lot.qty,remaining);remaining-=take
                def portion(amount,suffix):
                    fraction=amount/lot.qty
                    changes={'id':lot.id+suffix,'qty':amount,'cost':lot.cost*fraction}
                    for key in ('transition_mv','transition_gain'):
                        value=getattr(lot,key)
                        if value is not None:changes[key]=value*fraction
                    return core.replace(lot,**changes)
                if take>core.BTC_EPS:moved.append(portion(take,'*'))
                if lot.qty-take>core.BTC_EPS:kept.append(portion(lot.qty-take,''))
            self.holdings[wallet]=kept
            return moved
    return AnnualEngine

class FamilyCalculation:
    def __init__(self,core,chain,historical,accounts,other,snapshot,settings,console,emit,rank,now=None):
        self.core=core;self.chain=chain;self.historical=historical;self.accounts=accounts;self.other=other
        self.snapshot=snapshot;self.settings=settings;self.console=console;self.emit=emit;self.rank=rank
        self.users=sorted(snapshot['users'],key=lambda u:u['id'])
        self.now=now or datetime.now(timezone.utc);self.base_ownership=core.WalletOwnership
        core.FAMILY_ACCOUNT_USERS=[u for u in self.users if accounts[u['id']].get('directory') is not None]
        self.joints=[];self.allocations={};self.policies={};self.audit=[]
        self._physical_template=None;self._report_prefixes={};self._matching_evidence={};self._scoring_prefixes={};self.use_scoring_prefixes=True
        self._asset_basis={};self._asset_results={};self._asset_transition={}
        self._historical_evidence=deepcopy(getattr(core,'HISTORICAL_OUTPOINTS',{}))

    def physical_rows(self,cutoff):
        c=self.core
        if self._physical_template is None:
            self._physical_template=c.merge_wallets(wallet_rows(c,deepcopy(self.chain)),deepcopy(self.historical))
        # Display-name application mutates these run-local mappings. Reset just
        # the wallet headers; immutable decoded transactions can be shared.
        c.DESCRIPTOR_SOURCE_NAMES={w['id']:w['name'] for w in self.chain['wallets']}
        c.DESCRIPTOR_WALLETS={w['name']:w['id'] for w in self.chain['wallets']}
        c.DESCRIPTOR_SNAPSHOT=dict(self.chain,wallets=[dict(w) for w in self.chain['wallets']])
        return {name:[copy(r) for r in values if c.utc_time(r.dt)<cutoff] for name,values in self._physical_template.items()}

    def account_prefixes(self,cutoff):
        if cutoff not in self._report_prefixes:
            self._report_prefixes[cutoff]={uid:dict(a,reports=prefix_reports(self.core,a['reports'],cutoff)) for uid,a in self.accounts.items()}
        # Fee queues and opening-lot reconstruction belong to a single replay.
        return {uid:dict(a,reports=clone_reports(a['reports'])) for uid,a in self._report_prefixes[cutoff].items()}

    def replay(self,user,fy,allocations,policies,losses,cutoff,serialise=False):
        with matching_cache(self.core,self._matching_evidence):
            return self._replay(user,fy,allocations,policies,losses,cutoff,serialise)

    def _replay(self,user,fy,allocations,policies,losses,cutoff,serialise=False):
        c=self.core;c.WalletOwnership=self.base_ownership;c.CARRIED_LOSSES=losses
        c.TAX_CONTEXT=getattr(c,'FAMILY_TAX_CONTEXTS',{}).get(user['id'],{}).get(fy)
        self.console.profile=user['id']
        accounts=self.account_prefixes(cutoff)
        wallets=self.physical_rows(cutoff)
        descriptor_names=dict(getattr(c,'DESCRIPTOR_WALLETS',{}))
        ownership,other=apply_ownership(c,self.snapshot,user,wallets,[copy(r) for r in self.other if c.utc_time(r.dt)<cutoff],require_bitaroo=bool(self.accounts[user['id']].get('directory')))
        labels={name:self.snapshot_record('descriptor',uid)['name'] for name,uid in descriptor_names.items()}
        eligible=[j for j in self.joints if datetime.fromisoformat(j['utc'])<cutoff]
        wallets,synthetic=joint.apply(c,wallets,eligible,allocations,labels,self.users,user,accounts)
        for name,uid in synthetic.items():
            ownership.wallet_statuses[name]=ownership.PERSONAL if uid==user['id'] else ownership.EXTERNAL
            ownership.family_records[name]={'owner_id':uid,'smsf':False,'external':False}
        ownership.bind_wallets(wallets)
        # Row numbers can change after a joint transaction is expanded. Re-key
        # exact historical evidence from TXID/output identity, never old position.
        supplied=self._historical_evidence
        old_evidence=supplied.get('evidence',{})
        by_tx=defaultdict(list)
        for (name,_),item in old_evidence.items():by_tx[(name,item.get('blockchain_txid'))].append(item)
        rebuilt={}
        for name,rows in wallets.items():
            for r in rows:
                matches=by_tx.get((name,r.txid),[])
                if matches:rebuilt[r.ref]=deepcopy(matches[0])
        c.HISTORICAL_OUTPOINTS=dict(supplied,evidence=rebuilt)
        reports=accounts[user['id']]['reports']
        planner=c.trace_history(reports,fy,wallets,ownership,other);reports.reconstruct_opening_lots()
        Engine=joint_engine(c,policies,visual=serialise)
        engine=Engine(user['name'],fy,wallets,reports,planner.required_wallet_rows,planner.required_bitaroo_rows,ownership,other,
                      primary_wallet_rows=planner.primary_required_wallet_rows,primary_bitaroo_rows=planner.primary_required_bitaroo_rows)
        engine.basis_engine_class=Engine
        engine.synthetic_accounts=synthetic
        current=[j for j in eligible if financial_year(c,c.TransactionDate(datetime.fromisoformat(j['utc'])))==fy]
        if current and not serialise and self.use_scoring_prefixes:
            earliest=min(datetime.fromisoformat(j['utc']) for j in current)
            boundary=max(c.local_to_utc(datetime(fy,7,1)),earliest-timedelta(hours=c.MATCH_HOURS,seconds=2))
            # A separate cache namespace for every policy/annual context prevents
            # earlier joint parcel choices or changed losses from being reused.
            policy_key=tuple(sorted(policies.items()))
            entries=self._scoring_prefixes.setdefault((user['id'],fy,losses,cutoff,policy_key),{})
            engine.scoring_prefix=PrefixCache(c,entries,boundary)
        try:
            engine.run()
            from asset_disposals import calculate
            asset_key=(user['id'],fy,cutoff,tuple(repr(r) for r in other))
            if asset_key not in self._asset_results:
                def request_basis(row,quantity):
                    key=(user['id'],row.ref,str(quantity))
                    if key not in self._asset_basis:
                        self._asset_basis[key]=c.request_asset_basis(row,quantity)
                    return self._asset_basis[key]
                self._asset_results[asset_key]=calculate(c,engine,other,request_basis,self._asset_transition.setdefault(user['id'],{}))
            asset_events,asset_paths,asset_facts=self._asset_results[asset_key]
            engine.events.extend(asset_events)
            if serialise:engine.visual_events.update(asset_paths)
            engine.asset_acquisition_facts=asset_facts
            events=[e for e in engine.events if engine.period_start<=e.date<=engine.period_end]
            summary=c.annual_summary(events,losses)
            summary['_unreconciled']=any('excluded' in text.lower() or 'source quantity is missing' in text.lower() for text in engine.review)
            if serialise and engine.review:
                raise ValueError('Calculation needs corrected source records before it can finish: ' + ' | '.join(dict.fromkeys(engine.review)))
            report=build_report(c,engine) if serialise else None
            if report:
                report.update(owner_id=user['id'],owner_name=user['name'])
                for event in report['events']:event.update(owner_id=user['id'],owner_name=user['name'])
            return summary,report,engine
        except BaseException:
            engine.close_observer();raise

    def snapshot_record(self,kind,source):
        ident=resource_id(kind,source)
        return next(r for r in self.snapshot['ownership'] if r['id']==ident)

    def family_replay(self,fy,values,policies,losses,cutoff,serialise=False,keep=None):
        summaries={};reports={};held={} if keep=='all' else None
        for user in self.users:
            summary,report,engine=self.replay(user,fy,values,policies,losses[user['id']],cutoff,serialise)
            summaries[user['id']]=summary
            if report:reports[user['id']]=report
            engine.close_observer()
            if keep=='all':held[user['id']]=engine
            elif keep==user['id']:held=engine
        return summaries,reports,held

    def run(self,joints,selected_user):
        c=self.core;self.joints=joints
        dates=[r.dt for rows in self.historical.values() for r in rows]
        dates += [c.TransactionDate(datetime.fromisoformat(r['utc'].replace('Z','+00:00'))) for w in self.chain['wallets'] for r in w['records']]
        dates += [r.dt for a in self.accounts.values() for r in a['reports'].btc_rows+a['reports'].aud_rows]
        dates += [r.dt for r in self.other]
        dates=[d for d in dates if c.utc_time(d)<=self.now]
        if not dates:raise ValueError('No confirmed transactions were supplied for a CGT report.')
        first=min(financial_year(c,d) for d in dates)
        last=financial_year(c,c.TransactionDate(self.now))
        default=display_year(self.settings.get('display_timezone') or self.settings.get('browser_timezone','UTC'),self.now)
        first=min(first,default)
        years={u['id']:{} for u in self.users}
        # Opening losses are explicit profile-keyed settings, never applied to
        # everybody from one person's old command-line setting.
        opening=self.settings.get('opening_losses_by_user',{})
        if not isinstance(opening,dict):raise ValueError('Opening capital losses must identify each family profile.')
        if self.settings.get('carried_losses') not in (None,'',0,'0') and not opening:
            opening={selected_user['id']:self.settings['carried_losses']}
        losses={u['id']:Decimal(str(opening.get(u['id'],0))) for u in self.users}
        if any(not v.is_finite() or v<0 for v in losses.values()):raise ValueError('Opening capital losses must be finite, nonnegative AUD amounts.')
        final_engine=None
        for fy in range(first,last+1):
            self._matching_evidence.clear();self._scoring_prefixes.clear()
            cutoff=year_cutoff(c,fy,self.now)
            self.emit('stage',stage='calculating',label='Calculating every user · '+c.fmt_fy(fy))
            print('Family CGT calculation · '+c.fmt_fy(fy))
            current=[j for j in joints if financial_year(c,c.TransactionDate(datetime.fromisoformat(j['utc'])))==fy and datetime.fromisoformat(j['utc'])<cutoff]
            initial={j['txid']:joint.balanced(j['budgets'],[o['value'] for o in j['destinations']]) for j in current}
            self.allocations.update(initial)
            stats={'evaluations':0,'global_optimum_certified':False,'method':'No ambiguous jointly funded payments in this year'}
            rankings=[]
            if current:
                search_started=perf_counter()
                visible_prices=set(c.PRICE_MESSAGES_SHOWN)
                self.console.silent=True
                try:
                    neutral,neutral_reports,_=self.family_replay(fy,self.allocations,self.policies,losses,cutoff,True)
                    ids={j['txid'] for j in current};affected=[]
                    for user in self.users:
                        events=neutral_reports[user['id']]['events']
                        if any(any(n.get(side,{}).get('transaction_id') in ids for p in e['paths'] for n in p['nodes'] for side in ('details','incoming','outgoing') if isinstance(n.get(side),dict)) for e in events):affected.append(user)
                    ambiguous=any(sum(v>0 for v in j['budgets'])>1 and sum(o['value']>0 for o in j['destinations'])>1 for j in current)
                    base=deepcopy(self.allocations)
                    evaluations=[0];tax_cache={}
                    def objective(values,policies=None):
                        from json import dumps
                        policies=policies if policies is not None else self.policies
                        key=dumps([values,policies],sort_keys=True,separators=(',',':'))
                        if key not in tax_cache:
                            result,_,_=self.family_replay(fy,values,policies,losses,cutoff)
                            tax_cache[key]=result;evaluations[0]+=1
                            if evaluations[0]%10==0:
                                self.emit('stage',stage='calculating',label=f'{c.fmt_fy(fy)} · {evaluations[0]} annual allocation comparisons · {perf_counter()-search_started:.1f}s')
                        result=tax_cache[key]
                        if any(s['_unreconciled'] for s in result.values()):raise ValueError('Joint-payment tax optimisation requires reconciled source quantities and cost bases for every affected annual calculation. Correct the missing history before rebuilding the report; missing proceeds are never treated as a tax saving.')
                        unit=Decimal('.000000000001')
                        net={uid:s['net_gain'].quantize(unit) for uid,s in result.items()}
                        # Minimise annual taxable gains, then put tied gains away
                        # from higher ranked incomes; finally stay near neutral.
                        distance=sum(abs(v-initial[h][i][j]) for h in initial for i,row in enumerate(values[h]) for j,v in enumerate(row))
                        return (sum(net.values()),*(net[uid] for uid in rankings),distance)
                    selected,stats=joint.optimise(current,base,objective,neutral=base)
                    self.allocations=selected
                    if ambiguous and len(affected)>1 and stats['equal_minimum_candidates']>1:
                        rankings=self.rank(fy,affected)
                        self.allocations,ranked=joint.optimise(current,self.allocations,objective,neutral=base)
                        ranked['evaluations']+=stats['evaluations'];stats=ranked
                    # Parcel choices and transport choices interact. Compare
                    # alternative policies with the full annual tax calculation,
                    # then refine transport once more using the winning policies.
                    best=objective(self.allocations)
                    for item in current:
                        h=item['txid']
                        for i,source in enumerate(item['sources']):
                            key=h+':source:'+str(i)
                            for policy in ('long-first','short-first','high-cost','low-cost','oldest-first','newest-first'):
                                trial=dict(self.policies,**{key:policy});value=objective(self.allocations,trial)
                                if value<best:self.policies=trial;best=value
                    if any(k.startswith(j['txid']+':source:') for j in current for k in self.policies):
                        self.allocations,extra=joint.optimise(current,self.allocations,objective,neutral=base)
                        stats['evaluations']+=extra['evaluations']
                    stats['parcel_policies']={j['txid']:{source:self.policies.get(j['txid']+':source:'+str(i),'proportional') for i,source in enumerate(j['sources'])} for j in current}
                    stats['evaluations']=evaluations[0]
                finally:
                    self.console.silent=False
                    c.PRICE_MESSAGES_SHOWN.clear();c.PRICE_MESSAGES_SHOWN.update(visible_prices)
                print(f"Joint-payment optimisation complete · {c.fmt_fy(fy)} · {stats['evaluations']} annual comparisons in {perf_counter()-search_started:.2f}s")
            keep='all' if fy==last else None
            summaries,reports,engine=self.family_replay(fy,self.allocations,self.policies,losses,cutoff,True,keep)
            if engine:final_engine=engine
            convention={'algorithm':ALGORITHM,'financial_year':c.fmt_fy(fy),'cutoff_exclusive_utc':cutoff.isoformat(),
                        'income_ranking_high_to_low':rankings,'search':stats,
                        'transactions':[dict(j,allocation_sats=self.allocations[j['txid']],parcel_policies={source:self.policies.get(j['txid']+':source:'+str(i),'proportional') for i,source in enumerate(j['sources'])}) for j in current]}
            self.audit.append(convention)
            for user in self.users:
                uid=user['id'];report=reports[uid]
                report['allocation_convention']=deepcopy(self.audit)
                report['conventions'] += ['Jointly funded payments use a recomputed, financial-year-bounded accounting convention. Input/output attribution and equal sender fee shares are assumptions, not blockchain facts. No allocation ledger or transaction template is consulted.',
                                         'The search compares feasible allocations and parcel policies using annual capital losses and applicable discounts. It does not certify a global minimum or calculate personal income tax from income rankings.']
                years[uid][str(fy)]=report;losses[uid]=summaries[uid]['net_loss']
        selected=min(last,max(first,default))
        result=deepcopy(years[selected_user['id']][str(selected)])
        result.update(annual_reports=years[selected_user['id']],family_reports=years,selected_year=selected,maximum_year=selected,minimum_year=first,
                      calculation_scope='All supplied family users and financial years',allocation_algorithm=ALGORITHM)
        return result,final_engine
