"""Run-local, whole-satoshi collaborative-payment allocation convention.

No allocation ledger or template is read or written. Historical ownership is
supplied by public descriptors/outpoints; recipient attribution is an explicit
accounting assumption, not an additional fact claimed to exist on-chain.
"""
from collections import defaultdict
from decimal import Decimal
from datetime import datetime,timezone,timedelta
from itertools import permutations,combinations,islice
import copy,json

SAT=Decimal('0.00000001')
def account_name(user):return 'Bitaroo Exchange · '+user['name']

def transaction_dependencies(core):
    result={h:{i.get('txid') for i in tx.get('vin',[])} for h,tx in getattr(core,'HISTORICAL_OUTPOINTS',{}).get('transactions',{}).items()}
    for h,tx in getattr(core,'DESCRIPTOR_SNAPSHOT',{}).get('transactions',{}).items():result.setdefault(h,{i.get('txid') for i in tx['inputs']})
    return result

def dependency_order(groups,dependencies):
    """Stable topological order of transaction groups at a shared timestamp."""
    remaining=dict(groups)
    while remaining:
        ready=[h for h in remaining if not dependencies.get(h,set()).intersection(remaining)]
        if not ready:raise ValueError('Cyclic blockchain transaction dependencies.')
        ident=min(ready,key=lambda h:remaining[h][0][0])
        yield ident,remaining.pop(ident)

def descriptor_outputs(chain):
    names={w['id']:w['name'] for w in chain['wallets']}
    result={tuple([key.rsplit(':',1)[0],int(key.rsplit(':',1)[1])]):names[uid] for key,uid in chain.get('wallet_outputs',{}).items()}
    # Old non-collaborative snapshots contain exact receiving/output records.
    for wallet in chain['wallets']:
        groups=defaultdict(list)
        for row in wallet['records']:groups[row['txid']].append(row)
        for ident,rows in groups.items():
            tx=chain['transactions'].get(ident)
            if not tx:continue
            for row in rows:
                if row['sats']>0 and row['output_index']>=0:result[(ident,row['output_index'])]=wallet['name']
            if not tx.get('joint') and any(row['sats']<0 for row in rows):
                sent={r['output_index'] for r in rows if r['sats']<0 and r['output_index']>=0}
                for n,out in enumerate(tx['outputs']):
                    if n not in sent and out['value']>0:result[(ident,n)]=wallet['name']
    return result

def validate_sources(core,chain):
    owners=descriptor_outputs(chain)
    historical=getattr(core,'HISTORICAL_OUTPOINTS',{})
    for key,name in historical.get('owners',{}).items():
        if key in owners:raise ValueError('A wallet output is supplied both through descriptors ('+owners[key]+') and Wallets ODS ('+name+'). Supply each wallet through one source only. If it is now historical, reload Wallet Descriptors without that retired wallet.')
        owners[key]=name
    return owners

def collect(core,chain,client,accounts,users,now=None,snapshot=None):
    now=now or datetime.now(timezone.utc)
    owners=validate_sources(core,chain)
    historical=getattr(core,'HISTORICAL_OUTPOINTS',{})
    transactions=dict(historical.get('transactions',{}))
    for ident,tx in chain['transactions'].items():
        if not tx.get('joint') and not all('txid' in i for i in tx['inputs']):continue
        if ident not in transactions:
            transactions[ident]={'txid':ident,'fee':tx['fee_sats'],'status':{'confirmed':True,'block_height':tx['height'],'block_time':int(datetime.fromisoformat(tx['utc'].replace('Z','+00:00')).timestamp())},'vin':[{'txid':i['txid'],'vout':i['vout'],'prevout':{'value':i['value'],'scriptpubkey':i['script']}} for i in tx['inputs']],'vout':[{'value':o['value'],'scriptpubkey':o['script'],'scriptpubkey_address':o.get('address')} for o in tx['outputs']]}
    result=[];profiles={u['id']:u for u in users}
    from preferences import resource_id
    identities={r['id']:r for r in (snapshot or {}).get('ownership',[]) if r.get('confirmed')}
    descriptor_ids={w['name']:w['id'] for w in chain['wallets']}
    def personal_source(name):
        kind='descriptor' if name in descriptor_ids else 'wallet'
        row=identities.get(resource_id(kind,descriptor_ids.get(name,name)),{})
        return row.get('owner_id') in profiles and not row.get('smsf') and not row.get('external')
    for ident,tx in sorted(transactions.items(),key=lambda item:(item[1]['status']['block_height'],item[0])):
        instant=datetime.fromtimestamp(tx['status']['block_time'],timezone.utc)
        if instant>now:continue
        day=core.TransactionDate(instant);fy=day.year-(day.month<7)
        cutoff=min(core.local_to_utc(datetime(fy+1,7,1)),now+timedelta(microseconds=1))
        inputs=tx.get('vin',[]);sources=sorted({owners[(i.get('txid'),i.get('vout'))] for i in inputs if (i.get('txid'),i.get('vout')) in owners})
        if not sources:continue
        if snapshot is not None and not any(personal_source(name) for name in sources):continue
        unknown=any((i.get('txid'),i.get('vout')) not in owners for i in inputs)
        if len(sources)==1 and not unknown:continue
        if unknown:raise ValueError('A collaborative transaction has inputs belonging to an unidentified wallet. Supply its descriptors or historical owned outputs before CGT. Transaction: '+ident)
        amounts=[sum(i['prevout']['value'] for i in inputs if owners[(i['txid'],i['vout'])]==name) for name in sources]
        fee=tx['fee']
        if sum(amounts)!=sum(o['value'] for o in tx['vout'])+fee:raise ValueError('Collaborative transaction amounts and fee do not reconcile: '+ident)
        # Equal fee contributions, rounded to whole sats using stable source order.
        fees=[fee//len(sources)+(n<fee%len(sources)) for n in range(len(sources))]
        if any(f>a for f,a in zip(fees,amounts)):raise ValueError('A sending wallet cannot fund its equal share of the network fee: '+ident)
        budgets=[a-f for a,f in zip(amounts,fees)];destinations=[];bridges=[]
        for n,out in enumerate(tx['vout']):
            value=out['value'];receiver=owners.get((ident,n));address=out.get('scriptpubkey_address') or ''
            if not value:continue
            matches=[]
            for uid,account in accounts.items():
                for row in account['reports'].btc_rows:
                    if core.utc_time(row.dt)<cutoff and row.tx_type=='Deposit' and int(row.amount/SAT)==out['value'] and abs(core.utc_time(row.dt).timestamp()-tx['status']['block_time'])<=core.MATCH_HOURS*3600:
                        matches.append((uid,row))
            if not receiver and len({uid for uid,_ in matches})==1:
                uid,deposit=matches[0];receiver=account_name(profiles[uid]);bridges.append({'owner_id':uid,'receiver':receiver,'index':n,'value':value,'deposit_utc':core.utc_time(deposit.dt).isoformat()})
            elif not receiver and len({uid for uid,_ in matches})>1:
                raise ValueError('A collaborative output matches more than one family Bitaroo account. Its receiving account must be established before allocation: '+ident)
            destinations.append({'index':n,'value':value,'address':address,'receiver':receiver})
        if sum(budgets)!=sum(o['value'] for o in destinations):raise ValueError('Collaborative payment budgets do not reconcile: '+ident)
        result.append({'txid':ident,'utc':datetime.fromtimestamp(tx['status']['block_time'],timezone.utc).isoformat(),'height':tx['status']['block_height'],'sources':sources,'budgets':budgets,'fees':fees,'destinations':destinations,'bridges':bridges})
    return result

def balanced(rows,columns):
    """Proportional baseline; exact row/column sums and deterministic rounding."""
    left=list(columns);matrix=[];total=sum(rows)
    for i,budget in enumerate(rows):
        if i==len(rows)-1:matrix.append(left);break
        values=[budget*c//total if total else 0 for c in left]
        remainder=budget-sum(values)
        order=sorted(range(len(left)),key=lambda j:(-(budget*left[j]%total if total else 0),j))
        for j in order:
            if remainder and values[j]<left[j]:values[j]+=1;remainder-=1
        if remainder:raise ValueError('Could not round the proportional allocation.')
        matrix.append(values);left=[c-v for c,v in zip(left,values)];total-=budget
    return matrix

def validate(joint,matrix):
    rows=joint['budgets'];cols=[o['value'] for o in joint['destinations']]
    if len(matrix)!=len(rows) or any(len(row)!=len(cols) for row in matrix):raise ValueError('Invalid allocation dimensions.')
    if any(type(v) is not int or v<0 for row in matrix for v in row):raise ValueError('Allocations must be nonnegative whole satoshis.')
    if [sum(r) for r in matrix]!=rows or [sum(r[j] for r in matrix) for j in range(len(cols))]!=cols:raise ValueError('Allocation does not conserve every sender and recipient amount.')

def optimise(joints,initial,evaluate,progress=lambda _:None,neutral=None):
    """Deterministic feasible-vertex search and integer cycle refinement.

    The evaluator returns annual taxable-gain objectives, not speculative income
    tax rates. Search statistics disclose that a global optimum is not certified.
    """
    neutral=neutral if neutral is not None else initial
    chosen=copy.deepcopy(initial);cache={};attempts=0;primary=None;primary_keys=set()
    def score(values):
        nonlocal attempts,primary
        key=json.dumps(values,sort_keys=True,separators=(',',':'))
        if key not in cache:
            cache[key]=evaluate(values);attempts+=1
            value=cache[key][0]
            if primary is None or value<primary:primary=value;primary_keys.clear()
            if value==primary:primary_keys.add(key)
            if attempts%10==0:progress(attempts)
        return cache[key]
    best=score(chosen)
    for sweep in range(3):
        changed=False
        for joint in joints:
            h=joint['txid'];m=chosen[h];rows=joint['budgets'];cols=[o['value'] for o in joint['destinations']]
            candidates=[balanced(rows,cols)]
            # Enumerate all greedy vertices for small diagrams; larger diagrams
            # use a deterministic prefix, followed by independent cycle moves.
            for ro in islice(permutations(range(len(rows))),24):
                for co in islice(permutations(range(len(cols))),120):
                    left=list(rows);need=list(cols);trial=[[0]*len(cols) for _ in rows]
                    for i in ro:
                        for j in co:
                            amount=min(left[i],need[j]);trial[i][j]=amount;left[i]-=amount;need[j]-=amount
                    candidates.append(trial)
            for trial in candidates:
                validate(joint,trial);values=dict(chosen,**{h:trial});candidate=score(values)
                if candidate<best:chosen[h]=trial;best=candidate;changed=True
            # A 2x2 cycle preserves all balances. Search interior amounts too:
            # annual losses/discount thresholds can make a vertex suboptimal.
            for a,b in combinations(range(len(rows)),2):
                for c,d in combinations(range(len(cols)),2):
                    m=chosen[h];lo=-min(m[a][c],m[b][d]);hi=min(m[a][d],m[b][c])
                    def at(x):
                        trial=copy.deepcopy(m);trial[a][c]+=x;trial[b][d]+=x;trial[a][d]-=x;trial[b][c]-=x
                        return trial,score(dict(chosen,**{h:trial}))
                    # A fixed parcel/matching region has affine tax arithmetic.
                    # Split at every exact neutral-distance kink of this cycle,
                    # and recognise straight regions before doing satoshi binary
                    # refinement. Nonlinear regions retain the previous search.
                    from fractions import Fraction
                    anchor=neutral[h]
                    breaks=sorted({lo,hi,0,*[max(lo,min(hi,x)) for x in (anchor[a][c]-m[a][c],anchor[b][d]-m[b][d],m[a][d]-anchor[a][d],m[b][c]-anchor[b][c])]})
                    points=set(breaks)
                    def collinear(left,middle,right):
                        if right-left<=2:return True
                        lv,mv,rv=at(left)[1],at(middle)[1],at(right)[1]
                        for l,m,r in zip(lv,mv,rv):
                            predicted=Fraction(l)+(Fraction(r)-Fraction(l))*Fraction(middle-left,right-left)
                            # Annual tax objectives are quantised to 1e-12 AUD.
                            # Integer tie-breaks must remain exactly affine.
                            tolerance=Fraction(1,100000000000) if any(isinstance(v,Decimal) for v in (l,m,r)) else 0
                            if abs(Fraction(m)-predicted)>tolerance:return False
                        return True
                    for low,high in zip(breaks,breaks[1:]):
                        if high<=low:continue
                        middle=(low+high)//2;points.add(middle)
                        if collinear(low,middle,high):continue
                        iteration=0
                        while high-low>2:
                            mid=(low+high)//2
                            # Intersect the two outer affine pieces to jump to
                            # likely loss/discount breakpoints. The direction is
                            # still checked at adjacent whole-satoshi values;
                            # periodic bisection guarantees shrinking brackets.
                            if iteration%3!=2 and high-low>8:
                                step=max(1,(high-low)//16)
                                l0,l1=at(low)[1],at(low+step)[1]
                                r0,r1=at(high-step)[1],at(high)[1]
                                for v0,v1,w0,w1 in zip(l0,l1,r0,r1):
                                    sl=(Fraction(v1)-Fraction(v0))/step
                                    sr=(Fraction(w1)-Fraction(w0))/step
                                    tolerance=Fraction(2,100000000000) if any(isinstance(v,Decimal) for v in (v0,v1,w0,w1)) else 0
                                    if abs(sl-sr)*(high-low)<=tolerance:continue
                                    crossing=(Fraction(w1)-Fraction(v0)+sl*low-sr*high)/(sl-sr)
                                    proposed=crossing.numerator//crossing.denominator
                                    if low<proposed<high-1:mid=proposed
                                    break
                            if at(mid)[1]<=at(mid+1)[1]:high=mid
                            else:low=mid+1
                            iteration+=1
                        points.update(range(low,high+1))
                    for x in sorted(points):
                        trial,candidate=at(x)
                        if candidate<best:validate(joint,trial);chosen[h]=trial;best=candidate;changed=True
        if not changed:break
    return chosen,{'evaluations':attempts,'sweeps':sweep+1,'equal_minimum_candidates':len(primary_keys),'global_optimum_certified':False,'method':'Deterministic feasible-vertex search with affine-breakpoint interpolation and whole-satoshi cycle refinement'}

def apply(core,base,joints,allocations,labels,users,current_user,accounts):
    """Replace net joint placeholders with conserved, linked payment fragments."""
    names={u['id']:u for u in users};joint_ids={j['txid'] for j in joints}
    wallets={name:[copy.copy(r) for r in rows if r.txid not in joint_ids] for name,rows in base.items()}
    synthetic={};special_rows=defaultdict(list)
    for uid,account in accounts.items():
        if uid==current_user['id']:continue
        name=account_name(names[uid]);synthetic[name]=uid
        for n,r in enumerate(account['reports'].btc_rows):
            if r.tx_type not in {'Withdrawal','Deposit'}:continue
            if r.tx_type=='Deposit' and any(b['owner_id']==uid and int(r.amount/SAT)==b['value'] and core.utc_time(r.dt).isoformat()==b['deposit_utc'] for j in joints for b in j['bridges']):continue
            special_rows[name].append(core.WalletTx(name,n+1,core.tax_date(r.dt),r.amount,Decimal(0),r.balance,'Family Bitaroo statement',txid=f'ACCOUNT:{uid}:{r.source_file}:{r.page}:{n}'))
    for joint in joints:
        h=joint['txid'];matrix=allocations[h];validate(joint,matrix);when=core.TransactionDate(datetime.fromisoformat(joint['utc']))
        after={name:max((r for r in rows if core.utc_time(r.dt)<=core.utc_time(when)),key=lambda r:(core.utc_time(r.dt),r.row_num)).balance
               if any(core.utc_time(r.dt)<=core.utc_time(when) for r in rows) else Decimal(0) for name,rows in base.items()}
        for i,physical in enumerate(joint['sources']):
            source=labels.get(physical,physical);fee=joint['fees'][i];charged=False
            for j,target in enumerate(joint['destinations']):
                amount=matrix[i][j]
                if not amount:continue
                receiver=labels.get(target['receiver'],target['receiver']);key=h+':'+str(i)+':'+str(j)
                is_ods=physical in getattr(core,'HISTORICAL_OUTPOINTS',{}).get('wallets',{})
                part_fee=fee if not charged else 0;charged=True
                row=core.WalletTx(source,0,when,-Decimal(amount+(part_fee if is_ods else 0))*SAT,Decimal(part_fee)*SAT,after.get(source,Decimal(0)),'Year-bounded collaborative payment accounting allocation',address=target['address'],txid=h,is_historical_ods=is_ods,output_index=target['index'],accounting_flow=key,onchain_output_amount=Decimal(amount)*SAT,onchain_fee_source='Equal fee contribution convention for jointly funded transaction')
                special_rows[source].append(row)
                if receiver:
                    target_ods=target['receiver'] in getattr(core,'HISTORICAL_OUTPOINTS',{}).get('wallets',{})
                    incoming=core.WalletTx(receiver,0,when,Decimal(amount)*SAT,Decimal(0),after.get(receiver,Decimal(target['value'])*SAT),'Year-bounded collaborative payment accounting allocation',address=target['address'],txid=h,is_historical_ods=target_ods,output_index=target['index'],accounting_flow=key)
                    special_rows[receiver].append(incoming)
            if not charged and fee:
                is_ods=physical in getattr(core,'HISTORICAL_OUTPOINTS',{}).get('wallets',{})
                special_rows[source].append(core.WalletTx(source,0,when,-Decimal(fee)*SAT,Decimal(fee)*SAT,after.get(source,Decimal(0)),'Joint transaction fee-only contribution',txid=h,is_historical_ods=is_ods,onchain_output_amount=Decimal(0),accounting_flow=h+':fee:'+str(i)))
        for bridge in joint['bridges']:
            name=bridge['receiver'];synthetic[name]=bridge['owner_id']
            if bridge['owner_id']==current_user['id']:
                posted=max(datetime.fromisoformat(bridge['deposit_utc']),core.utc_time(when)+timedelta(microseconds=1))
                special_rows[name].append(core.WalletTx(name,0,core.TransactionDate(posted),-Decimal(bridge['value'])*SAT,Decimal(0),Decimal(0),'On-chain deposit into this Bitaroo account',address=next(o['address'] for o in joint['destinations'] if o['index']==bridge['index']),txid=h,output_index=bridge['index'],accounting_flow=h+':bridge'))
    for name,rows in special_rows.items():wallets.setdefault(name,[]).extend(rows)
    for name,rows in wallets.items():
        # Preserve old row order except for explicitly constructed joint groups.
        rows.sort(key=lambda r:(core.utc_time(r.dt),0 if r.accounting_flow and r.outgoing and not r.accounting_flow.endswith(':bridge') else 1,r.row_num,r.accounting_flow))
        instants=defaultdict(list)
        for index,row in enumerate(rows):instants[core.utc_time(row.dt)].append((index,row))
        rows=[];dependencies=transaction_dependencies(core)
        for _,items in sorted(instants.items()):
            groups=defaultdict(list)
            for index,row in items:groups[row.txid or 'row:'+str(index)].append((index,row))
            if not set(groups).intersection(joint_ids):rows.extend(r for _,r in items);continue
            for _,group in dependency_order(groups,dependencies):rows.extend(r for _,r in group)
        wallets[name]=rows
        for n,row in enumerate(rows,1):row.row_num=n;row.sheet=name
    return wallets,synthetic
