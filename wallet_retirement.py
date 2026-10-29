"""Public retirement proposals only. Future Agents perform and authorise conversion."""
from agent_hub import digest,canonical
from descriptor_store import DescriptorStore
from preferences import FamilyStore
from agent_transactions import eligible


def candidates(hub,user_id):
    family=FamilyStore(hub.config).snapshot();catalog=DescriptorStore(hub.config).read();result={}
    for owner in family['ownership']:
        if owner.get('owner_id')!=user_id or not owner.get('confirmed') or owner['kind']!='descriptor':continue
        wallet=next((w for w in catalog['wallets'] if w['id']==owner['source'] and not w.get('extinct')),None)
        if wallet:result[owner['id']]=eligible(hub,owner['id'],'wallet_retirement')
    return result


def propose(hub,session,wallet_id,node,confirmed):
    if not session.user() or not session.access()['ownership_ready']:raise ValueError('Complete Ownership first.')
    if confirmed is not True:raise ValueError('Confirm the local Agent must review retirement.')
    option=next((n for n in candidates(hub,session.user_id).get(wallet_id,[]) if n['id']==node),None)
    if not option:raise ValueError('No connected Agent supports retirement for this owned wallet. Update the Agent first.')
    family=FamilyStore(hub.config).snapshot();owner=next(r for r in family['ownership'] if r['id']==wallet_id)
    wallet=next(w for w in DescriptorStore(hub.config).read()['wallets'] if w['id']==owner['source'])
    p={'kind':'wallet_retire','version':1,'wallet':option['wallet'],'public_wallet':wallet['raw'],'archive_label':owner['name'],'require_zero_balance':True,'delete_wallet':False}
    p['request_id']=digest(p);key=digest(p);canonical(p)
    with hub.edit() as state:
        state['requests'].setdefault(key,{'kind':'wallet_retire','node_id':node,'proposal':p,'state':'pending','created_utc':hub.utc()})
    return {'request_hash':key,'queued':True}


def result(state,node,p):
    if not isinstance(p,dict) or not {'request_hash','status'}<=set(p) or set(p)-{'request_hash','status','message'} or p['status'] not in ('complete','denied','error'):raise ValueError('Invalid retirement result.')
    row=state['requests'].get(p['request_hash'])
    if not row or row['kind']!='wallet_retire' or row['node_id']!=node or not row.get('delivered'):raise ValueError('Unsolicited retirement result.')
    if row['state']!='pending' and row['state']!=p['status']:raise ValueError('A retirement outcome cannot change.')
    row.update(state=p['status'],message=str(p.get('message',''))[:1000])
    # Actual labels/history must be read from Core on the next Ownership refresh.
