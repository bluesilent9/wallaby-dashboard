"""Read-only vault spendability checks and immediately-read user notices."""
from fee_planner import live_coins
from transaction_builder import builder_catalog
from transaction_workflows import WorkflowStore

BLOCKED='You cannot spend funds from a vault whose height restriction is still in effect.'
PARTIAL='Some funds remain inaccessible because a vault height restriction is still in effect. Only the available funds can be sent; the network fee is also deducted from those funds.'

def decision(availability,action,amount=0,reserved=0):
    available=int(availability['available_sats']);locked=int(availability['locked_sats'])
    blocked=locked>0 and available==0
    exceeds=action=='amount' and amount+reserved>available
    warning=BLOCKED if blocked else PARTIAL if locked and (action=='maximum' or action=='amount' and amount+reserved>=available) else None
    return dict(availability,blocked=blocked,exceeds_available=exceeds,warning=warning)

def check(session,data):
    source=data.get('source');action=data.get('action','connection')
    if action not in ('connection','inspect','maximum','amount'):raise ValueError('Invalid vault spendability action.')
    values=[]
    for key in ('amount_sats','reserved_sats'):
        value=data.get(key,'0')
        if not isinstance(value,str) or not value.isascii() or not value.isdigit() or len(value)>16:raise ValueError('Invalid payment amount.')
        values.append(int(value))
    catalog=builder_catalog(session)
    if not any(w['id']==source and w['can_send'] for w in catalog['wallets']):raise ValueError('Choose a funding wallet from the current Ownership records.')
    coins=live_coins(session,catalog,{source},force=True)
    result=decision(coins.availability[source],action,*values)
    if result['warning'] and action!='inspect':result['notice']=WorkflowStore(session.preferences_path).vault_warning(session.user_id,result['warning'])
    return result
