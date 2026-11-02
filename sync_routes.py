"""Renew public invitations when an existing MAC-identified peer changes route.

A new invitation is never a retry/approval of an earlier wallet operation. Old
requests remain addressable so delayed real operation outcomes are retained.
"""
import copy,secrets,time
from agent_hub import digest

def renew(transfers,state):
    hub=transfers.hub
    live={c['node_id']:c for c in list(hub.registry.connections.values()) if hub.registry.rpc._unique(c)}
    for ident,rows in list(transfers.pairs(state).items()):
        if len(rows)!=2 or any(r['state']!='pending' or r.get('superseded_attempt') or r.get('superseded_container') for r in rows):continue
        if any(r.get('retry_of')==ident for r in state['requests'].values()):continue
        if not all(r.get('proposal') for r in rows):continue
        nodes={r['node_id'] for r in rows}
        if not nodes<=live.keys() or any(transfers.is_busy(n) for n in nodes):continue
        if any(not state['nodes'].get(n,{}).get('ipv6') for n in nodes):continue
        changed=any(r['proposal']['node_ipv6']!=state['nodes'][r['node_id']]['ipv6'] or r['proposal']['peer']['ipv6']!=state['nodes'][r['peer_id']]['ipv6'] for r in rows)
        if not changed:continue
        receiver=next(r for r in rows if r['direction']=='receive')
        if hub.permissions.permits(receiver['node_id'],receiver['public_wallet']) is not True:continue
        if any(not hub.inventories.get(n,{}).get('complete') or time.monotonic()-hub.inventory_times.get(n,0)>60 for n in nodes):continue
        if any(hub.registry.health.status(live[n]).get('state')!='ready' for n in nodes):continue
        sender=next(r for r in rows if r['direction']=='send')
        origin=[w for w in hub.inventories[sender['node_id']]['wallets'] if not w.get('signing_only')]
        if not transfers.contains(origin,sender['public_wallet']):continue
        from wallet_preview import policies
        private=any(w.get('private_keys_enabled') and policies(sender['public_wallet'],True)<=policies(w['raw'],True) for w in origin)
        dest=hub.inventories[receiver['node_id']]['wallets']
        if transfers.contains([w for w in dest if not w.get('signing_only')],receiver['public_wallet']):continue
        fresh=secrets.token_hex(32)
        for old in rows:
            old.update(superseded_attempt=True,queue_visible=False,superseded_message='Peer network address changed; replaced by a new public invitation. Earlier approvals and outcomes were not reused.')
            row={k:copy.deepcopy(old[k]) for k in ('kind','node_id','peer_id','direction','descriptor_id','public_wallet','source_has_private_keys','earliest_timestamp','requires_blockchain_redownload')}
            if old.get('subwallets'):row['subwallets']=copy.deepcopy(old['subwallets'])
            row.update(transfer_id=fresh,state='pending',retry_of=ident,route_refresh=True,created_utc=hub.utc(),source_has_private_keys=private)
            state['requests'][digest({'transfer':fresh,'direction':row['direction']})]=row
        hub.registry.diagnostic_log.append('Wallet sync','Peer route changed; new public review requested · previous='+ident[:12]+' · request='+fresh[:12]+'. Local approval is required.')

def prior_in_progress(transfers,state,rows):
    """A late successful/uncertain old operation takes priority over a new invite."""
    pairs=transfers.pairs(state);row=rows[0];seen=set()
    while row.get('route_refresh') and row.get('retry_of') not in seen:
        ident=row['retry_of'];seen.add(ident);prior=pairs.get(ident,[])
        if not prior:return False
        if any(r['state'] in ('complete','recovery_required') for r in prior):return True
        row=prior[0]
    return False

def summary(hub,node,state,transfers=None):
    live={c['node_id'] for c in list(hub.registry.connections.values()) if hub.registry.rpc._unique(c)}
    if node not in live:return {'state':'offline','text':'Wallet synchronisation: Agent offline.'}
    if not live or any(not hub.inventories.get(n,{}).get('complete') for n in live):return {'state':'checking','text':'Wallet synchronisation: waiting for complete wallet inventories from all connected Agents.'}
    if any(hub.permissions.supported(n) and not hub.permissions.fresh(n) for n in live):return {'state':'checking','text':'Wallet synchronisation: checking the Agents’ current permissions.'}
    rows=[r for r in (transfers if transfers is not None else hub.transfers.summaries(state,node)) if not r['superseded'] and r['state']!='complete']
    if rows:return {'state':'pending','count':len(rows),'text':'Wallet synchronisation: '+str(len(rows))+' outstanding requests; review the details below.'}
    return {'state':'checked','text':'Wallet synchronisation: no outstanding requests among currently checked connected Agents. Blockchain status is separate.'}
