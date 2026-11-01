"""Deterministic, read-only salary scenarios; no wallet RPC or transaction effects.

Money is Decimal throughout. Funding is a spending plan, not employment income.
Future prices, transition values, CPI and super access are explicit assumptions.
"""
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from preferences import resource_id

D=Decimal
ZERO=D(0)
CENT=D('.01')
SAT=D('.00000001')
MAX_MONEY=D('1000000000000000')

def number(value,label,low=ZERO,high=MAX_MONEY):
    try:n=D(str(value))
    except Exception as exc:raise ValueError(label+' must be a number.') from exc
    if not n.is_finite() or not low<=n<=high:raise ValueError(label+' is outside the allowed range.')
    return n

def integer(value,label,low,high):
    n=number(value,label,D(low),D(high))
    if n!=n.to_integral_value():raise ValueError(label+' must be a whole number.')
    return int(n)

def money(value):return str(D(value).quantize(CENT,rounding=ROUND_HALF_UP))

def clean_inputs(value):
    if not isinstance(value,dict):raise ValueError('Invalid salary inputs.')
    def text(key,limit=160):
        item=value.get(key,'')
        if not isinstance(item,str) or len(item)>limit or any(ord(c)<32 for c in item):raise ValueError('Invalid salary '+key+'.')
        return item
    result={k:text(k) for k in ('distribution_id','asset_base_id','anchor','timezone')}
    result['frequency']=value.get('frequency','fortnightly')
    result['fund_phase']=value.get('fund_phase','accumulation')
    if result['frequency'] not in ('daily','weekly','fortnightly','monthly') or result['fund_phase'] not in ('accumulation','pension'):raise ValueError('Invalid salary planning option.')
    for key,default,low,high in [('target_age',100,1,150),('access_age',65,60,100),('weekday',4,0,6)]:result[key]=integer(value.get(key,default),key,low,high)
    for key,default,low,high in [('price_aud','',ZERO,MAX_MONEY),('transition_price_aud','',ZERO,MAX_MONEY),('asset_multiple','10',ZERO,D(1000)),('other_income','0',ZERO,MAX_MONEY),('capital_losses','0',ZERO,MAX_MONEY),('cpi_percent','0',ZERO,D(100)),('medicare_percent','2',ZERO,D(10)),('medicare_threshold','0',ZERO,MAX_MONEY),('tax_adjustment','0',ZERO,MAX_MONEY),('network_fees_aud','0',ZERO,MAX_MONEY)]:
        raw=value.get(key,default)
        result[key]='' if raw=='' and key in ('price_aud','transition_price_aud') else str(number(raw,key,low,high))
    for key,default in [('asset_enabled',False),('access_confirmed',False),('include_lito',True),('schedule_set',False)]:
        item=value.get(key,default)
        if type(item) is not bool:raise ValueError('Invalid salary '+key+'.')
        result[key]=item
    rows=value.get('sources',[])
    if not isinstance(rows,list) or len(rows)>100:raise ValueError('Select at most 100 funding sources.')
    result['sources']=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'id','mode'} or not isinstance(row['id'],str) or not 1<=len(row['id'])<=160 or row['id'] in seen or row['mode'] not in ('lifetime','one_year'):raise ValueError('Invalid or repeated salary funding source.')
        seen.add(row['id']);result['sources'].append(dict(row))
    from salary_graph import clean_graph
    result['people'],result['allocations']=clean_graph(value,number)
    layout=value.get('layout',{})
    if not isinstance(layout,dict) or len(layout)>210:raise ValueError('Invalid salary card layout.')
    result['layout']={}
    for ident,position in layout.items():
        if not isinstance(ident,str) or len(ident)>200 or not isinstance(position,dict) or set(position)!={'x','y'}:raise ValueError('Invalid salary card position.')
        result['layout'][ident]={k:float(number(position[k],'Card position',ZERO,D(100000))) for k in ('x','y')}
    fees=value.get('fee_edges',[])
    if not isinstance(fees,list) or len(fees)>100:raise ValueError('Invalid salary fee connections.')
    result['fee_edges']=[];seen=set()
    for edge in fees:
        if not isinstance(edge,dict) or set(edge)!={'from','rate'} or edge['from'] not in {s['id'] for s in result['sources']} or edge['from'] in seen:raise ValueError('A fee connection must come from a selected funding source.')
        seen.add(edge['from']);result['fee_edges'].append({'from':edge['from'],'rate':format(number(edge['rate'],'Fee rate',ZERO,D(1000000)),'f')})
    if result['anchor']:
        try:date.fromisoformat(result['anchor'])
        except ValueError as exc:raise ValueError('Choose a valid fortnightly payday date.') from exc
    if result['timezone']:
        try:ZoneInfo(result['timezone'])
        except (ValueError,ZoneInfoNotFoundError) as exc:raise ValueError('Choose a valid timezone.') from exc
    return result

def birthday(born,year):return date(year,born.month,min(born.day,monthrange(year,born.month)[1]))

def age_at(born,when):return when.year-born.year-(when<birthday(born,when.year))

def paydays(start,end,inputs):
    frequency=inputs['frequency']
    if frequency=='monthly':
        result=[];y,m=start.year,start.month
        while date(y,m,1)<=end:
            day=date(y,m,1)
            if start<=day<=end:result.append(day)
            y,m=(y+1,1) if m==12 else (y,m+1)
        return result
    if frequency=='daily':first,step=start,1
    elif frequency=='weekly':first,step=start+timedelta(days=(inputs['weekday']-start.weekday())%7),7
    else:
        if not inputs['anchor']:raise ValueError('Choose one known fortnightly payday in the calendar.')
        anchor=date.fromisoformat(inputs['anchor']);first,step=start+timedelta(days=(anchor-start).days%14),14
    return [first+timedelta(days=n) for n in range(0,(end-first).days+1,step)]

def spread(total,count):
    units=int((D(total)/CENT).to_integral_value(rounding=ROUND_HALF_UP));base,extra=divmod(units,count)
    return [D(base+(i<extra))*CENT for i in range(count)]

def funding_catalog(snapshot,balance_report):
    """Identify every row by persisted ownership/descriptor IDs, never name matches."""
    ownership={r['id']:r for r in snapshot['ownership'] if r.get('confirmed')}
    rows=[]
    for entry in (balance_report or {}).get('balances',{}).get('wallets',[]):
        if entry.get('extinct'):continue
        ident=entry.get('resource_id')
        if not ident and entry.get('descriptor_id'):ident=resource_id('descriptor',entry['descriptor_id'])
        if not ident:continue
        record=ownership.get(ident)
        if not record:continue
        rows.append(dict(entry,id=ident,owner_id=record.get('owner_id'),smsf=bool(record.get('smsf')),external=bool(record.get('external')),name=record.get('name') or entry['name']))
    return rows

def context(snapshot,balance_report,user,settings=None,now=None):
    settings=settings or {};zone=settings.get('timezone') or settings.get('browser_timezone') or 'UTC'
    try:tz=ZoneInfo(zone)
    except (ValueError,ZoneInfoNotFoundError):zone='UTC';tz=ZoneInfo(zone)
    today=(now or datetime.now(timezone.utc)).astimezone(tz).date();fy=today.year+(today.month>=7)
    rows=funding_catalog(snapshot,balance_report)
    result={'user':user,'users':snapshot['users'],'timezone':zone,'next_year':fy,'start':date(fy,7,1).isoformat(),'end':date(fy+1,6,30).isoformat(),'financial_year':f'{fy}–{str(fy+1)[2:]}','wallets':rows,'price_aud':(balance_report or {}).get('balances',{}).get('price_aud_per_btc'),'valuation_utc':(balance_report or {}).get('balances',{}).get('valuation_utc')}
    return result

@lru_cache(maxsize=1)
def core_module():
    from worker import load_core
    return load_core()

def tax_liability(summary,inputs,fy,tables):
    """Incremental resident bracket reserve, not PAYG withholding or a tax return."""
    from resident_tax_tables import select,bracket_tax,offset_amount
    table=select(tables,fy)
    base=D(inputs['other_income']);gain=summary['net_gain'];minimum=summary['minimum_tax_gain']
    basic=lambda x:bracket_tax(x,table)
    def normal(x):return max(ZERO,basic(x)-(offset_amount(x,tables.get('offset')) if inputs['include_lito'] else ZERO))
    ordinary=max(ZERO,normal(base+gain)-normal(base))
    core=core_module();gap=core.minimum_tax_gap(minimum,basic(base+gain),basic(base+gain-minimum))
    # The threshold is explicit because the future FY's indexed thresholds and
    # family/SAPTO circumstances are not supplied by a Bitcoin ledger.
    threshold=D(inputs['medicare_threshold']);rate=D(inputs['medicare_percent'])/100
    def medicare(x):return min(x*rate,max(ZERO,x-threshold)*D('.10')) if threshold else x*rate
    levy=max(ZERO,medicare(base+gain)-medicare(base))
    return {'bracket_tax':ordinary,'minimum_tax':gap,'medicare_buffer':levy,'total':ordinary+gap+levy}

def personal_summary(disposals,inputs,fy,price,transition_price):
    """Use the report's own gain/holding/loss/indexation machinery, offline."""
    core=core_module()
    class Scenario(core.Engine):
        def _resolve_deferred_wallet_ownership(self,lot):pass
        def _resolve_deferred_source_ownership(self,lot):pass
        def indexed_cost(self,cost,acquisition,disposal,eligible=None):
            if eligible is False or eligible is None and not core.held_twelve_months(acquisition,disposal):return cost
            months=max(0,((disposal.year-acquisition.year)*4+(disposal.month-1)//3-(acquisition.month-1)//3)*3)
            factor=(D(1)+D(inputs['cpi_percent'])/100)**(D(months)/12)
            factor=factor.quantize(D('.001'),rounding=ROUND_HALF_UP)
            return cost*max(D(1),factor)
    engine=Scenario.__new__(Scenario);engine.events=[];engine.period_start=date(fy,7,1);engine.period_end=date(fy+1,6,30);engine.unreconciled_disposals=set();engine.review=[]
    # Combine dates only when all parcel holding thresholds and CPI quarter are
    # identical. This preserves annual tax maths without building daily reports.
    groups={}
    for wallet,when,quantity in disposals:
        lots=wallet['planning_lots'];held=tuple(core.held_twelve_months(date.fromisoformat(p['acquired'][:10]),when) for p in lots)
        key=(wallet['id'],when.year,(when.month-1)//3,held)
        if key in groups:groups[key][2]+=quantity
        else:groups[key]=[wallet,when,quantity]
    for wallet,when,quantity in groups.values():
        total=D(wallet['btc']);lots=[]
        for i,p in enumerate(wallet['planning_lots']):
            fraction=quantity/total;qty=D(p['quantity_btc'])*fraction;cost=D(p['cost_aud'])*fraction;acquired=date.fromisoformat(p['acquired'][:10]);mv=None;dg=None
            if when>=core.REFORM_DATE and acquired<=core.REFORM_EVE:
                mv=qty*transition_price;dg=mv-cost
            lots.append(core.Lot('salary-'+str(i),acquired,qty,cost,'Traced acquisition parcel',transition_mv=mv,transition_gain=dg))
        core.Engine._record_sale(engine,when,'Hypothetical salary spending',wallet['id'],lots,quantity*price,quantity)
    return core.annual_summary(engine.events,D(inputs['capital_losses']))

def fund_tax(disposals,phase,price):
    if phase=='pension':return ZERO
    core=core_module();short=long=loss=ZERO
    for wallet,when,quantity in disposals:
        for p in wallet['planning_lots']:
            ratio=quantity/D(wallet['btc']);gain=D(p['quantity_btc'])*ratio*price-D(p['cost_aud'])*ratio
            if gain<0:loss-=gain
            elif core.held_twelve_months(date.fromisoformat(p['acquired'][:10]),when):long+=gain
            else:short+=gain
    used=min(short,loss);short-=used;loss-=used;long=max(ZERO,long-loss)
    return (short+long*D(2)/3)*D('.15')

def evaluate(ctx,value):
    inputs=clean_inputs(value);user=ctx['user'];warnings=[]
    if not user.get('date_of_birth'):raise ValueError('Add your Date of Birth in Administration before building your salary.')
    born=datetime.strptime(user['date_of_birth'],'%d/%m/%Y').date();start=date.fromisoformat(ctx['start']);end=date.fromisoformat(ctx['end']);fy=start.year
    age=age_at(born,start);years=inputs['target_age']-age
    if years<=0:raise ValueError('Target age must be greater than your age on 1 July of the next financial year.')
    days=paydays(start,end,inputs);count=len(days)
    price=number(inputs['price_aud'] or '', 'Scenario BTC/AUD',SAT)
    transition=number(inputs['transition_price_aud'] or ctx.get('price_aud') or price,'Transition BTC/AUD',SAT)
    release=birthday(born,born.year+inputs['access_age']);catalog={w['id']:w for w in ctx['wallets']};users={u['id']:u for u in ctx['users']}
    selected=[];missing=[]
    for row in inputs['sources']:
        w=catalog.get(row['id'])
        if not w or w.get('owner_id')!=user['id'] or w.get('external'):raise ValueError('A selected funding source is unavailable or is not assigned to your profile. Select it again from the current wallet list.')
        w=deepcopy(w);w['mode']=row['mode'];btc=number(w.get('btc'),'Wallet balance');cash=number(w.get('cash_aud','0'),'Exchange AUD balance');w['value']=btc*price+cash
        if btc>0:
            lots=w.get('planning_lots')
            if w.get('cost_basis_aud') is None or not lots or abs(sum((number(p['quantity_btc'],'Parcel quantity') for p in lots),ZERO)-btc)>SAT or any(p.get('cost_aud') is None for p in lots):missing.append(w['name'])
        selected.append(w)
    if missing:warnings.append('Re-run Verify Balances to establish complete retained acquisition parcels for: '+', '.join(missing)+'. Tax and net-pay figures are unavailable until then.')
    if not selected:warnings.append('Add at least one funding source to calculate a salary.')
    if selected and not any(w['mode']=='lifetime' for w in selected):warnings.append('Only one-year funding is selected. No ongoing salary has been funded for later years.')
    if any(w['smsf'] for w in selected) and inputs['access_age']<65 and not inputs['access_confirmed']:warnings.append('SMSF access before 65 is conditional. Confirm the planned condition of release; the model will otherwise use age 65.')
    if inputs['access_age']<65 and not inputs['access_confirmed']:release=birthday(born,born.year+65)
    # Destination choices only label a calculation. They never create payments.
    for field in ('distribution_id',):
        ident=inputs[field]
        if not ident:continue
        w=catalog.get(ident);owner=user['id']
        if not w or w.get('extinct') or w.get('kind') in ('exchange','bitaroo'):raise ValueError('Choose an active Distributor Wallet. It may belong to any user.')
    pool=sum((w['value'] for w in selected if w['mode']=='lifetime'),ZERO)
    topups=sum((w['value'] for w in selected if w['mode']=='one_year'),ZERO)
    life_budget=(pool/years).quantize(CENT,rounding=ROUND_DOWN)
    topup_budget=topups.quantize(CENT,rounding=ROUND_DOWN)
    life_targets=spread(life_budget,count);topup_targets={w['id']:spread(w['value'].quantize(CENT,rounding=ROUND_DOWN),count) for w in selected if w['mode']=='one_year'}
    remaining={w['id']:w['value'].quantize(CENT,rounding=ROUND_DOWN) for w in selected};movements=[];periods=[]
    ordered=[w for fund in (False,True) for w in selected if w['mode']=='lifetime' and w['smsf']==fund]
    def take(w,amount,when,record=False):
        if w['smsf'] and when<release:return ZERO
        available=remaining[w['id']];paid=min(available,amount);remaining[w['id']]-=paid
        if record and paid:
            # Proportionally include AUD cash and BTC rather than invent a sale
            # of an exchange's existing AUD balance.
            qty=(paid/w['value']*D(w['btc'])) if w['value'] else ZERO
            movements.append((w,when,qty,paid));
        return paid
    for n,when in enumerate(days):
        need=life_targets[n];actual=ZERO
        for w in ordered:
            part=take(w,need,when,True);actual+=part;need-=part
            if need<=0:break
        life_paid=actual
        for w in selected:
            if w['mode']=='one_year':actual+=take(w,topup_targets[w['id']][n],when,True)
        target=life_targets[n]+sum((r[n] for r in topup_targets.values()),ZERO)
        periods.append({'date':when.isoformat(),'gross':actual,'lifetime_gross':life_paid,'shortfall':max(ZERO,target-actual)})
    gross=sum((r['gross'] for r in periods),ZERO);actual_life=sum((r['lifetime_gross'] for r in periods),ZERO)
    shortfall=max(ZERO,life_budget+topup_budget-gross)
    if shortfall>CENT:warnings.append('Some planned funding is not accessible on its payday. The plan does not borrow from locked SMSF balances.')
    personal=[(w,d,q) for w,d,q,a in movements if not w['smsf'] and q>0];fund=[(w,d,q) for w,d,q,a in movements if w['smsf'] and q>0]
    incomplete=bool(missing) or age<18;summary=None;tax=None;fund_reserve=None;net=None
    from resident_tax_tables import select
    tables=(ctx.get('tax_tables') or {}).get('data') or {}
    table=select(tables,fy)
    if (ctx.get('tax_tables') or {}).get('error'):warnings.append('The tax-data update was unavailable or incomplete. Any tax estimate uses the last validated table; check its source and date under Tax calculation.')
    if not table or inputs['include_lito'] and not tables.get('offset'):
        incomplete=True;warnings.append('Published tax data is not available for this financial year. Refresh the official tax data; tax and net pay remain unavailable.')
    if age<18:warnings.append('The personal tax estimate requires an adult resident profile; minor-income rules are not modelled here.')
    if not incomplete:
        summary=personal_summary(personal,inputs,fy,price,transition);tax=tax_liability(summary,inputs,fy,tables);fund_reserve=fund_tax(fund,inputs['fund_phase'],price)
        tax_total=(tax['total']+fund_reserve+D(inputs['tax_adjustment'])).quantize(CENT,rounding=ROUND_HALF_UP)
        net=max(ZERO,gross-tax_total-D(inputs['network_fees_aud'])).quantize(CENT,rounding=ROUND_DOWN)
        if tax_total+D(inputs['network_fees_aud'])>gross:warnings.append('The tax and network fee reserves exceed the funded gross allocation.')
    else:tax_total=None
    from salary_graph import allocate,divide
    allocation=allocate(net,inputs,ctx['users'])
    own=next((D(p['retained_aud']) for p in allocation['people'] if p['id']==user['id'] and p['retained_aud'] is not None),None)
    asset=allocation['asset_aud'];asset_target=allocation['asset_target_aud'];surplus=allocation['surplus_aud'];salary_shortfall=allocation['salary_shortfall_aud'];equivalents=None;recurring=None
    if net is not None:
        spent=sum((D(p['retained_aud']) for p in allocation['people']),ZERO)
        recurring_net=max(ZERO,actual_life-(tax_total*actual_life/gross if gross else ZERO))
        recurring=max(ZERO,min(surplus,recurring_net-spent-asset-D(inputs['network_fees_aud'])))
        reference=allocation['reference_salary_aud'];equivalents=int(recurring/reference) if reference else 0
        if salary_shortfall>CENT:warnings.append('Available pay does not fully fund the entered independent salary targets. Targets share the shortfall proportionally.')
        if asset+CENT<asset_target:warnings.append('Asset Allocation is only partly funded after the salary targets.')
        if inputs['asset_enabled'] and not any(p['id']==inputs['asset_base_id'] and not p['receiving'] and D(p['target_aud'] or '0')>0 for p in allocation['people']):warnings.append('Set an independent salary target and choose it as the Asset Allocation reference.')
        capacities=[r['gross'] for r in periods]
        totals=[('tax',min(tax_total,gross)),('fees',min(D(inputs['network_fees_aud']),max(ZERO,gross-tax_total)))]+[('person:'+p['id'],D(p['retained_aud'])) for p in allocation['people']]+[('asset',asset),('surplus',surplus)]
        for key,total in totals:
            parts=divide(total,capacities)
            for i,(row,part) in enumerate(zip(periods,parts)):
                if part>capacities[i]:raise ValueError('Payday allocations exceed funded capacity.')
                row[key]=part;capacities[i]-=part
    # Gross-only lifetime projection uses exactly the same private-first/access
    # gates. One-year top-ups are absent after the selected financial year.
    projection=[{'year':fy,'age':age,'gross':money(gross),'shortfall':money(shortfall),'personal_left':money(sum((remaining[w['id']] for w in selected if w['mode']=='lifetime' and not w['smsf']),ZERO)),'smsf_left':money(sum((remaining[w['id']] for w in selected if w['mode']=='lifetime' and w['smsf']),ZERO))}]
    first_gap=fy if shortfall>CENT else None
    for offset in range(1,years):
        y=fy+offset;future=paydays(date(y,7,1),date(y+1,6,30),inputs);targets=spread(life_budget,len(future));paid=ZERO
        for when,need in zip(future,targets):
            for w in ordered:
                part=take(w,need,when);paid+=part;need-=part
                if need<=0:break
        gap=max(ZERO,life_budget-paid)
        if gap>CENT and first_gap is None:first_gap=y
        projection.append({'year':y,'age':age+offset,'gross':money(paid),'shortfall':money(gap),'personal_left':money(sum((remaining[w['id']] for w in ordered if not w['smsf']),ZERO)),'smsf_left':money(sum((remaining[w['id']] for w in ordered if w['smsf']),ZERO))})
    if first_gap is not None:
        warnings.append(f'The funding runway first falls short in {first_gap}–{str(first_gap+1)[2:]}. SMSF is not treated as spendable before its planned release date.')
        equivalents=0 if equivalents is not None else None
    source_rows=[]
    for w in selected:
        drawn=sum((a for row,d,q,a in movements if row['id']==w['id']),ZERO)
        source_rows.append({'id':w['id'],'name':w['name'],'mode':w['mode'],'smsf':w['smsf'],'value_aud':money(w['value']),'annual_draw_aud':money(drawn),'annual_draw_btc':str(sum((q for row,d,q,a in movements if row['id']==w['id']),ZERO).quantize(SAT,rounding=ROUND_DOWN))})
    funding_by_day={when:[] for when in days}
    for wallet,when,quantity,amount in movements:
        funding_by_day[when].append({'id':wallet['id'],'btc':str(quantity.quantize(SAT,rounding=ROUND_DOWN)),'cash_aud':money(amount-quantity*price)})
    # Keep JSON numeric strings explicit and stable; no binary floating money.
    def packed(value):
        if isinstance(value,D):return money(value)
        if isinstance(value,list):return [packed(v) for v in value]
        if isinstance(value,dict):return {k:packed(v) for k,v in value.items()}
        return value
    return packed({'financial_year':ctx['financial_year'],'start':ctx['start'],'end':ctx['end'],'age':age,'years':years,'target_age':inputs['target_age'],'payday_count':count,'price_aud':price,'transition_price_aud':transition,'smsf_release':release.isoformat(),'pool_aud':pool,'topups_aud':topups,'lifetime_annual_aud':life_budget,'planned_gross_aud':life_budget+topup_budget,'gross_aud':gross,'unfunded_aud':shortfall,'tax_aud':tax_total,'network_fees_aud':D(inputs['network_fees_aud']),'fund_tax_aud':fund_reserve,'tax_details':tax,'capital_gains':summary,'net_aud':net,'own_aud':own,'asset_aud':asset,'asset_target_aud':asset_target,'surplus_aud':surplus,'salary_shortfall_aud':salary_shortfall,'additional_salary_equivalents':equivalents,'recurring_surplus_aud':recurring,'allocation':allocation,'first_unfunded_year':first_gap,'sources':source_rows,'funding_paydays':[{'date':when.isoformat(),'sources':funding_by_day[when]} for when in days],'paydays':periods,'projection':projection,'warnings':warnings,'btc_estimates':{k:(str((v/price).quantize(SAT,rounding=ROUND_HALF_UP)) if v is not None else None) for k,v in {'gross':gross,'tax':tax_total,'own':own,'asset':asset,'fees':D(inputs['network_fees_aud']),'surplus':surplus}.items()},'tax_complete':not incomplete,'tax_table':table,'tax_checked_utc':(table or {}).get('checked_utc') or tables.get('checked_utc')})
