"""Explain why known sync work is absent from an Agent's live review list."""
import time

def reason(hub,state,rows):
    source=next((r for r in rows if r['direction']=='send'),None);target=next((r for r in rows if r['direction']=='receive'),None)
    if not source or not target:return 'invalid_pair','The paired request is incomplete; reconcile locally.'
    if target['state']=='complete':return None,None
    if any(r['state']=='recovery_required' for r in rows):return 'history_recovery','Wallet data was received; finish history recovery on the Agent. No file transfer will be repeated.'
    if any(r['state'] in ('denied','error') for r in rows):return 'local_outcome','A previous attempt was declined or failed. Reconcile both Agents before starting a new review.'
    if any(r.get('preview_notice') for r in rows):return 'preview_changed','The sender reported a different snapshot. Reconcile both Agents, then start a fresh review.'
    preview=source.get('signed_preview',{}).get('preview')
    if preview and not time.time()-900<=preview['generated_at']<=time.time()+120:return 'preview_expired','The earlier public preview expired. Reconcile both Agents, then start a fresh review; no wallet operation has been retried.'
    permission=hub.permissions.permits(target['node_id'],target['public_wallet'])
    if permission is False:return 'disabled','Incoming synchronisation is disabled on the receiving Agent.'
    if permission is None:return 'permissions_pending','Waiting for the receiving Agent’s current signed synchronisation permissions.'
    live={c['node_id']:c for c in list(hub.registry.connections.values()) if hub.registry.rpc._unique(c)}
    if not {source['node_id'],target['node_id']}<=live.keys():return 'peer_offline','Waiting for both paired Agents to be connected without identity conflicts.'
    for row in rows:
        p=row.get('proposal')
        if p and (p['node_ipv6']!=state['nodes'].get(row['node_id'],{}).get('ipv6') or p['peer']['ipv6']!=state['nodes'].get(row['peer_id'],{}).get('ipv6')):return 'peer_changed','A paired IPv6 address changed. Reconcile both Agents, then start a fresh review; the old endpoints were not silently replaced.'
    if not all(r.get('proposal') for r in rows):return 'discovery_pending','Waiting for both node inventories, blockchain checks and peer addresses before preparing local reviews.'
    if not all(r.get('queue_visible') for r in rows):return 'review_paused','The review is paused by node readiness, policy, rename or retirement checks.'
    return None,None
