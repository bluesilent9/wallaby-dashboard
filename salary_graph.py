"""Salary intent graphs: one upstream allowance per person, no circular money."""
from decimal import Decimal,ROUND_DOWN,ROUND_HALF_UP
D=Decimal;CENT=D('.01');ZERO=D(0)

def clean_graph(value,number):
    targets=value.get('people',[]);edges=value.get('allocations',[])
    if not isinstance(targets,list) or len(targets)>100 or not isinstance(edges,list) or len(edges)>100:raise ValueError('Invalid salary people diagram.')
    people=[];ids=set()
    for p in targets:
        if not isinstance(p,dict) or not {'id','target'}<=set(p) or bool(set(p)-{'id','target','multiplier'}) or not isinstance(p['id'],str) or not 1<=len(p['id'])<=160 or p['id'] in ids:raise ValueError('Invalid or repeated salary person.')
        ids.add(p['id']);people.append({'id':p['id'],'target':str(number(p['target'],'Annual salary target')),'multiplier':str(number(p.get('multiplier','1'),'Personal pay rise multiplier',D('.01'),D(100)))})
    multipliers={p['id']:D(p['multiplier']) for p in people}
    parents={};out={};clean=[]
    for edge in edges:
        if not isinstance(edge,dict) or set(edge)!={'from','to','share'} or edge['from'] not in ids or edge['to'] not in ids or edge['from']==edge['to'] or edge['to'] in parents:raise ValueError('Each allocation recipient needs exactly one upstream person. Self allocations are not allowed.')
        share=number(edge['share'],'Allocation share',ZERO,D(100));parents[edge['to']]=edge['from'];out[edge['from']]=out.get(edge['from'],ZERO)+share*multipliers[edge['to']]
        if out[edge['from']]>100:raise ValueError('Outgoing allocation shares cannot exceed 100%.')
        clean.append({'from':edge['from'],'to':edge['to'],'share':str(share)})
    for ident in ids:
        visited=set();node=ident
        while node in parents:
            if node in visited:raise ValueError('Salary allocations cannot contain a loop.')
            visited.add(node);node=parents[node]
    return people,clean

def divide(total,weights):
    """Largest remainder, deterministic card order, exact cents."""
    total=D(total).quantize(CENT,rounding=ROUND_DOWN);weight=sum(weights,ZERO)
    if weight<=0:return [ZERO]*len(weights)
    exact=[total*w/weight for w in weights];parts=[x.quantize(CENT,rounding=ROUND_DOWN) for x in exact];left=int((total-sum(parts,ZERO))/CENT)
    for i in sorted(range(len(parts)),key=lambda i:(-(exact[i]-parts[i]),i))[:left]:parts[i]+=CENT
    return parts

def allocate(net,inputs,users):
    people=inputs['people'];edges=inputs['allocations'];known={u['id']:u for u in users}
    if any(p['id'] not in known for p in people):raise ValueError('A salary person is no longer available. Remove that card or select another profile.')
    incoming={e['to']:e for e in edges};roots=[p for p in people if p['id'] not in incoming];children={p['id']:[e for e in edges if e['from']==p['id']] for p in people}
    multipliers={p['id']:D(p['multiplier']) for p in people};targets={p['id']:(D(p['target'])*multipliers[p['id']]).quantize(CENT,rounding=ROUND_HALF_UP) for p in roots}
    base_id=inputs['asset_base_id'];base=targets.get(base_id,ZERO)
    reference=base or max(targets.values(),default=ZERO)
    result=[{'id':p['id'],'name':known[p['id']]['name'],'receiving':p['id'] in incoming,'target_aud':None if p['id'] in incoming else str(targets[p['id']]),'multiplier':p['multiplier'],'share':incoming[p['id']]['share'] if p['id'] in incoming else None,'effective_share':str(D(incoming[p['id']]['share'])*multipliers[p['id']]) if p['id'] in incoming else None,'parent_id':incoming[p['id']]['from'] if p['id'] in incoming else None,'incoming_aud':None,'retained_aud':None,'sent_aud':None} for p in people]
    if net is None:return {'people':result,'edges':edges,'asset_aud':None,'asset_target_aud':base*D(inputs['asset_multiple']) if inputs['asset_enabled'] else ZERO,'surplus_aud':None,'salary_shortfall_aud':None,'reference_salary_aud':reference}
    net=D(net);fixed=[p for p in roots if targets[p['id']]>0];automatic=[p for p in roots if targets[p['id']]==0];need=sum(targets.values(),ZERO);budget=min(net,need);amounts=dict(zip([p['id'] for p in fixed],divide(budget,[targets[p['id']] for p in fixed])))
    leftover=max(ZERO,net-budget)
    if automatic:
        amounts.update(zip([p['id'] for p in automatic],divide(leftover,[D(1)]*len(automatic))));leftover=ZERO
    reserve_target=(base*D(inputs['asset_multiple'])).quantize(CENT,rounding=ROUND_HALF_UP) if inputs['asset_enabled'] else ZERO
    reserve=min(leftover,reserve_target);leftover-=reserve
    rows={p['id']:p for p in result};pending=[p['id'] for p in roots]
    while pending:
        ident=pending.pop(0);amount=amounts.get(ident,ZERO);branches=children[ident];parts=divide(amount,[D(e['share'])*multipliers[e['to']] for e in branches]+[D(100)-sum((D(e['share'])*multipliers[e['to']] for e in branches),ZERO)])
        rows[ident].update(incoming_aud=str(amount),retained_aud=str(parts[-1]),sent_aud=str(sum(parts[:-1],ZERO)))
        for edge,part in zip(branches,parts):amounts[edge['to']]=part;pending.append(edge['to'])
    return {'people':result,'edges':[dict(e,effective_share=str(D(e['share'])*multipliers[e['to']]),amount_aud=str(amounts[e['to']])) for e in edges],'asset_aud':reserve,'asset_target_aud':reserve_target,'surplus_aud':leftover,'salary_shortfall_aud':max(ZERO,need-net),'reference_salary_aud':reference}
