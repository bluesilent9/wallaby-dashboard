"""Balance-only superannuation ledgers; never add funds to personal tax replays."""
from copy import deepcopy
from preferences import resource_id
from family_tax import FamilyCalculation
from joint_allocations import collect
from balance_trace import run

BASIS_FIELDS=('cost_basis_aud','unrealised_gain_aud','state','cost_basis_note','sources','planning_lots')
def entity_snapshot(snapshot,accounts):
    """Use separate stable entity IDs so fund facts cannot answer personal questions."""
    owners={r['owner_id'] for r in snapshot['ownership'] if r.get('confirmed') and r.get('smsf') and not r.get('external')}
    owners.update(uid for uid,a in accounts.items() if a.get('directory'))
    users=[dict(u,id=u['id']+':smsf',owner_id=u['id']+':smsf',name=u['name']+' SMSF') for u in snapshot['users'] if u['id'] in owners]
    transformed=deepcopy(snapshot);transformed['users']=users
    for row in transformed['ownership']:
        if row.get('smsf') and not row.get('external'):
            row['owner_id']+=':smsf';row['smsf']=False
        else:row.update(owner_id=None,external=True,smsf=False)
    return transformed,{uid+':smsf':a for uid,a in accounts.items() if uid in owners}

def add_smsf_balances(merged,core,chain,historical,accounts,other,snapshot,settings,console,emit,rank,client,base_ownership,shared_allocations=None,shared_policies=None):
    fund_snapshot,fund_accounts=entity_snapshot(snapshot,accounts)
    if not fund_snapshot['users']:return
    core.WalletOwnership=base_ownership
    calculation=FamilyCalculation(core,chain,historical,fund_accounts,other,fund_snapshot,settings,console,emit,rank)
    calculation.balance_only=True;calculation.policies=deepcopy(shared_policies or {})
    # This invokes the same cost/parcel engine as personal Balance Trace, in an
    # isolated entity namespace. It does not run a fund CGT report or apply
    # personal tax rates/discounts to fund holdings.
    core.FAMILY_TAX_CONTEXTS={}
    joints=collect(core,chain,client,fund_accounts,calculation.users,snapshot=fund_snapshot)
    from joint_allocations import balanced,validate
    for transaction in joints:
        matrix=deepcopy((shared_allocations or {}).get(transaction['txid']))
        try:validate(transaction,matrix)
        except (ValueError,TypeError):matrix=balanced(transaction['budgets'],[d['value'] for d in transaction['destinations']])
        calculation.allocations[transaction['txid']]=matrix
    fund_result=run(core,calculation,joints,fund_snapshot,calculation.users[0],console,emit,client)
    originals=[r for r in snapshot['ownership'] if r.get('kind') in ('descriptor','wallet') and r.get('smsf') and not r.get('external')]
    ids={r['source'] for r in originals if r['kind']=='descriptor'};names={r['name'] for r in originals}
    funds={('descriptor',r['descriptor_id']) if r.get('descriptor_id') else ('name',r['name']):r for r in fund_result['wallets'] if r.get('kind')!='exchange'}
    for entry in merged['wallets']:
        if entry.get('descriptor_id') not in ids and entry['name'] not in names:continue
        key=('descriptor',entry['descriptor_id']) if entry.get('descriptor_id') else ('name',entry['name'])
        source=funds.get(key)
        if source:
            for field in BASIS_FIELDS:entry[field]=deepcopy(source.get(field,[] if field=='sources' else None))
    # Fund exchange cash/Bitcoin are separate cards, never merged with a member's account.
    for entry in fund_result['wallets']:
        if entry.get('kind')!='exchange':continue
        owner=entry.get('owner_name','').removesuffix(' SMSF')
        fund_uid=entry.get('owner_id','');uid=fund_uid.removesuffix(':smsf')
        merged['wallets'].append(dict(entry,resource_id=resource_id('bitaroo',fund_uid),owner_id=uid,name='Bitaroo SMSF Exchange · '+owner,owner_name=owner,smsf=True,ownership='external'))
