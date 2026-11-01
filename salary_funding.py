"""Public, node-backed first-payday template preview; never prepare a PSBT."""
from decimal import Decimal,ROUND_DOWN,ROUND_HALF_UP
from copy import deepcopy
from salary_planning import clean_inputs,evaluate
from transaction_templates import clean_diagram
D=Decimal;SAT=D('100000000')

def funding_preview(result,inputs,catalog,coins):
    inputs=clean_inputs(inputs)
    cards={w['id']:w for w in catalog['wallets']};destination=inputs['distribution_id'];first=result['funding_paydays'][0]
    if not destination or destination not in cards or not cards[destination]['can_receive']:raise ValueError('Assign a Distributor Wallet with a verified receiving address.')
    if not inputs['people']:raise ValueError('Add at least one person in Stage 2 to calculate allocations.')
    if not result['tax_complete']:raise ValueError('Complete acquisition parcels and official tax data before saving a funded template.')
    if D(result['unfunded_aud'])>D('.01'):raise ValueError('Review the funding shortfall before saving a transaction template.')
    sources={r['id']:r for r in first['sources']};budgets={}
    for uid,row in sources.items():
        if D(row['cash_aud'])>D('.01'):raise ValueError('Exchange AUD cash cannot be sent as Bitcoin in this funding template.')
        if uid not in cards or not cards[uid]['can_send']:raise ValueError('Funding templates require source wallets available through a connected node, not exchange balances.')
        budget=int((D(row['btc'])*SAT).to_integral_value(rounding=ROUND_DOWN))
        if budget:budgets[uid]=budget
    if not budgets:raise ValueError('The first payday has no Bitcoin funding available.')
    if destination in budgets:raise ValueError('The Distributor Wallet must be different from every funding source.')
    rates={e['from']:e['rate'] for e in inputs['fee_edges'] if D(e['rate'])>0}
    if not rates or not set(rates)<=set(budgets):raise ValueError('Connect a funded source to Network Fee and set a positive sats/vB rate. Fee payers must fund the first payday.')
    from fee_planner import plan
    shares={};previous=None
    for _ in range(12):
        payments={u:n-int(shares.get(u,'0')) for u,n in budgets.items()}
        if any(n<=0 for n in payments.values()):raise ValueError('A source’s first payday allocation is not large enough to cover its fee.')
        edges=[{'from':u,'to':destination,'amount_sats':str(n),'fee_rate':'0'} for u,n in payments.items()]+[{'from':u,'to':'network-fee','amount_sats':'0','fee_rate':r} for u,r in rates.items()]
        estimate=plan(edges,catalog,coins);shares=estimate['wallet_fees_sats']
        if shares==previous:break
        previous=dict(shares)
    else:raise ValueError('The first payday fee estimate did not converge. Adjust the sources or fee rates.')
    if not estimate['can_continue']:raise ValueError(estimate['validation_message'])
    layout=inputs['layout'];nodes=[]
    for i,u in enumerate(budgets):nodes.append({'id':u,**layout.get(u,{'x':25,'y':25+i*210})})
    nodes.extend([{'id':destination,**layout.get('distributor',{'x':390,'y':25})},{'id':'network-fee',**layout.get('network-fee',{'x':710,'y':235})}])
    return {'ready':True,'reason':'First payday '+first['date']+' checked against current confirmed UTXOs. Future sizes and fees can change.','date':first['date'],'estimate':estimate,'payments':{u:str(D(n)/SAT) for u,n in payments.items()},'annual_fee_aud':str((D(estimate['total_fee_sats'])/SAT*D(result['price_aud'])*result['payday_count']).quantize(D('.01'),rounding=ROUND_HALF_UP)),'diagram':clean_diagram({'nodes':nodes,'edges':edges})}

def calculate(session,ctx,value,check_node,force=False):
    inputs=clean_inputs(value);result=evaluate(ctx,inputs)
    result['funding_template']={'ready':False,'reason':'Assign a Distributor Wallet, add salary people, and connect Network Fee to estimate the first payday transaction.'}
    if not inputs['distribution_id'] or not inputs['fee_edges'] or not inputs['people']:return result
    try:
        check_node()
        from transaction_builder import builder_catalog
        from fee_planner import live_coins
        catalog=builder_catalog(session);ids={r['id'] for r in result['funding_paydays'][0]['sources']}
        preview=funding_preview(result,inputs,catalog,live_coins(session,catalog,ids,force=force))
        reserved=deepcopy(inputs);reserved['network_fees_aud']=str(D(inputs['network_fees_aud'])+D(preview['annual_fee_aud']))
        result=evaluate(ctx,reserved);result['funding_template']=preview
        result['warnings'].append('Network fee reserve includes '+preview['annual_fee_aud']+' AUD for the FY, assuming each payday has the first payday’s current estimated size/rate. Actual future UTXOs and fees can differ.')
    except (ValueError,RuntimeError,OSError) as exc:result['funding_template']={'ready':False,'reason':str(exc)}
    return result
