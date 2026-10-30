"""Agent Build 8 authoritative public lists. Listing is never transfer authority."""
import copy,re,secrets,time
from node_wallets import NodeWalletLoader


def modern(hub,node):
    version=next((c.get('agent_version') for c in hub.registry.connections.values() if c['node_id']==node),None)
    m=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:~dev(\d+))?',version or '')
    return bool(m and (*map(int,m.groups()[:3]),int(m[4]) if m[4] else 10**9)>=(2,0,0,8))


def keys(raw):return {NodeWalletLoader.descriptor_key(r['desc']) for r in raw['descriptors']}
def contains(wallets,raw):
    available=set().union(*(keys(w['raw']) for w in wallets)) if wallets else set()
    return keys(raw)<=available


def schedule(transfers,state):
    hub=transfers.hub
    ready={c['node_id']:c for c in list(hub.registry.connections.values()) if hub.registry.rpc._unique(c)}
    for rows in transfers.pairs(state).values():
        if len(rows)!=2:continue
        source=next(r for r in rows if r['direction']=='send');receiver=next(r for r in rows if r['direction']=='receive')
        nodes={r['node_id'] for r in rows}
        if not all(modern(hub,n) for n in nodes):
            for row in rows:row['queue_visible']=False
            continue
        if any(r.get('proposal') and r['proposal'].get('transfer_version')!=2 for r in rows):
            for row in rows:row.update(state='error',queue_visible=False,message='Old request protocol. Reconcile the local Agents and start a new transfer attempt.')
            continue
        if not any(r.get('queue_v2') for r in rows):
            challenge=secrets.token_hex(32)
            for row in rows:
                row.update(queue_v2=True,challenge=challenge,base_sent=0,active_pair=False)
        permission=hub.permissions.permits(receiver['node_id'],receiver['public_wallet'])
        active=permission is True and nodes<=ready.keys() and all(modern(hub,n) and 'wallet_transfer' in hub.caps.get(n,set()) and state['nodes'].get(n,{}).get('ipv6') for n in nodes)
        active=active and receiver['state']!='complete' and not any(r['state'] in ('denied','error') for r in rows)
        if active and not source.get('proposal'):
            active=all(hub.registry.health.status(ready[n]).get('state')=='ready' for n in nodes)
            if active:
                for row in rows:row['proposal']=hub.transfer_proposal(state,row)
        # Frozen endpoints cannot silently change when a node reconnects elsewhere.
        if active:
            active=all(row.get('proposal') and row['proposal']['node_ipv6']==state['nodes'][row['node_id']]['ipv6'] and row['proposal']['peer']['ipv6']==state['nodes'][row['peer_id']]['ipv6'] for row in rows)
        preview=source.get('signed_preview')
        if preview and not time.time()-900<=preview['preview']['generated_at']<=time.time()+120 and not any(r['state']=='recovery_required' for r in rows):
            active=False
            for row in rows:row['message']='Signed preview expired. Reconcile the local Agents and request a deliberate fresh attempt.'
        for row in rows:row['queue_visible']=bool(active)
        if receiver['state']=='complete':
            for row in rows:row['queue_visible']=False
            if not receiver.get('history_refresh_scheduled'):
                receiver['history_refresh_scheduled']=True
                transfers.refresh_nodes.update(nodes)
    # Persist revisions for every known capable node, including an empty withdrawal.
    for node,entry in state['nodes'].items():
        if not modern(hub,node):continue
        rows=[r for r in state['requests'].values() if r.get('queue_v2') and r['node_id']==node and r.get('queue_visible')]
        rows.sort(key=lambda r:(r['public_wallet']['wallet_name'].casefold(),r['transfer_id']))
        if len(rows)>128:raise ValueError('Too many wallet synchronisation rows; finish existing requests first.')
        snapshot=[copy.deepcopy(r['proposal']) for r in rows]
        if snapshot!=entry.get('sync_snapshot'):
            entry['sync_revision']=entry.get('sync_revision',-1)+1;entry['sync_snapshot']=snapshot
        for row in rows:row['queue_revision']=entry['sync_revision']


def proposals(transfers,state,node):
    entry=state['nodes'].get(node,{})
    if 'sync_revision' not in entry:return []
    result=[{'kind':'wallet_sync_queue','revision':entry['sync_revision'],'requests':copy.deepcopy(entry['sync_snapshot'])}]
    previews=[]
    for rows in transfers.pairs(state).values():
        row=next((r for r in rows if r['node_id']==node and r.get('queue_v2') and r.get('queue_visible')),None)
        if not row:continue
        row['base_sent']=row.get('base_sent',0)+1
        sender=next(r for r in rows if r['direction']=='send');payload=sender.get('signed_preview')
        if row['direction']=='receive' and row['state']=='pending' and payload and time.time()-900<=payload['preview']['generated_at']<=time.time()+120:
            previews.append({'kind':'wallet_transfer_preview',**copy.deepcopy(payload)})
    # Queue first, followed by a rotating bounded set of unchanged signed previews.
    if previews:
        cursor=entry.get('preview_cursor',0)%len(previews)
        ordered=previews[cursor:]+previews[:cursor];result+=ordered[:15];entry['preview_cursor']=(cursor+15)%len(previews)
    return result


def warnings(hub):
    state=hub.read();result=[]
    names={n['id']:n['hostname'] for n in hub.registry._read()['nodes']}
    for ident,rows in hub.transfers.pairs(state).items():
        if any(r['direction']=='receive' and r['state']=='complete' for r in rows):continue
        recovery=any(r['state']=='recovery_required' for r in rows)
        if not recovery and not any(r['state']=='pending' for r in rows):continue
        receiver=next((r for r in rows if r['direction']=='receive'),None)
        if receiver and hub.permissions.permits(receiver['node_id'],receiver['public_wallet']) is not True and not recovery:continue
        name=rows[0]['public_wallet']['wallet_name'];peers=', '.join(names.get(r['node_id'],'node') for r in rows)
        text=('Wallet data received, but history recovery is incomplete for ' if recovery else 'Wallet synchronisation needs local review for ')+name+' ('+peers+'). Open the local Agent’s Synchronisation page.'
        result.append({'id':'wallet-sync-'+ident,'kind':'wallet_sync','severity':'yellow','dismissible':False,'target':'nodes','created_utc':rows[0]['created_utc'],'text':text})
    return result
