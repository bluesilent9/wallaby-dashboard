"""Locally approved public-policy additions; never replace keys or prior scripts."""
import copy,time
from storage import storage_lock
from agent_hub import digest,equivalent
from builder_destinations import Destinations
from policy_accounts import keys,signing_account
from node_wallets import NodeWalletLoader
CAPABILITY='wallet_policy_refresh_v1'

def verified(hub,node,envelope):
    from setup_signatures import verify
    live=hub.permissions.current(node)
    identity=hub.wallet_builder.read()['identities'].get(node)
    key=(live or {}).get('identity') or (identity or {}).get('public_key')
    if not key:raise ValueError('Wait for this Agent’s signed identity before accepting public archive or policy evidence.')
    return verify(hub.config,envelope,key)

def schedule(hub,ident):
    store=Destinations(hub.config);d=store.get(ident)
    if not d['height']:raise ValueError('This destination has no vault policy.')
    wanted={'wallet_name':d['source_wallet']['wallet_name'],'descriptors':d['descriptors']}
    targets=[]
    for node in hub.registry.snapshot()['nodes']:
        if not node.get('rpc_ready') or node.get('blockchain',{}).get('state')!='ready':continue
        inv=hub.inventories.get(node['id'],{})
        if not inv.get('complete'):continue
        previous=__import__('builder_destinations').public_rows({'raw':d['source_wallet']},pair=True)
        required={NodeWalletLoader.descriptor_key(r['desc']) for r in previous}
        matches=[w for w in inv['wallets'] if required<={NodeWalletLoader.descriptor_key(r['desc']) for r in w['raw']['descriptors']}]
        if not matches:continue
        matches.sort(key=lambda w:(w.get('signing_only',False),not equivalent(w['raw'],d['source_wallet']),w['name']))
        route=matches[0]
        targets.append((node,route))
    if not targets:raise ValueError('No connected Agent has the source wallet’s verified public signing accounts.')
    existing=hub.read()['requests'];targets=[(node,route) for node,route in targets if digest({'destination':ident,'node':node['id']}) not in existing]
    if not targets and d.get('policy_requests_started'):return [r for r in existing.values() if r.get('destination_id')==ident]
    with hub.edit() as state:
        for node,route in targets:
            key=digest({'destination':ident,'node':node['id']})
            if key in state['requests']:continue
            previous=__import__('builder_destinations').public_rows({'raw':d['source_wallet']},pair=True)
            # Route only to a wallet holding the old policy, not merely one of
            # its signing accounts. A signing account is not a policy monitor.
            if not {NodeWalletLoader.descriptor_key(r['desc']) for r in previous}<={NodeWalletLoader.descriptor_key(r['desc']) for r in route['raw']['descriptors']}:continue
            proposal={'kind':'wallet_policy_refresh','version':1,'request_id':key,'chain':'main','wallet':route['name'],'previous_descriptors':[r['desc'] for r in previous],'receive_descriptor':d['descriptors'][0]['desc'],'change_descriptor':d['descriptors'][1]['desc'],'timestamp':d['policy_timestamp'],'expires_at':int(time.time())+86390}
            state['requests'][key]={'kind':'wallet_policy_refresh','node_id':node['id'],'destination_id':ident,'workspace_id':d.get('workspace_id'),'state':'pending','proposal':proposal,'created_utc':hub.utc()}

    if not d.get('policy_requests_started'):
        with storage_lock(hub.config):
            state=store.read();state['destinations'][ident]['policy_requests_started']=True;store.write(state)
    return [r for r in hub.read()['requests'].values() if r.get('destination_id')==ident]

def proposals(hub,node):
    # A connected node may appear after the destination was first prepared.
    for ident,d in Destinations(hub.config).read()['destinations'].items():
        if d.get('policy_requests_started'):
            try:schedule(hub,ident)
            except ValueError:pass
            if d.get('archive_roles') and not d.get('archive_submitted') and destination_ready(hub,d):
                from archive_queue import ArchiveQueue
                queue=ArchiveQueue(hub);options=queue.options(d['base']);selected=[r['id'] for r in options if r['compatible'] and any(role['node_id']==r['node_id'] and role['receive_key'] in keys(r['public_wallet']) for role in d['archive_roles'])]
                if selected:
                    queue.create(d['user_id'],d['base'],selected)
                    with storage_lock(hub.config):
                        store=Destinations(hub.config);saved=store.read();saved['destinations'][ident]['archive_submitted']=True;store.write(saved)
    with hub.edit() as s:
        result=[]
        for key,r in s['requests'].items():
            if r['kind']=='wallet_policy_refresh' and r['node_id']==node and r['state']=='pending':
                if r['proposal']['expires_at']<=time.time():r.update(state='error',message='Policy request expired. Review the existing local outcome before requesting another refresh.');continue
                r['delivered']=True;result.append(r['proposal'])
        return result

def accept(hub,node,envelope):
    p=verified(hub,node,envelope)
    if not isinstance(p,dict) or p.get('version')!=1 or p.get('status') not in ('approved','history_required','denied','error'):raise ValueError('Invalid public policy refresh result.')
    key=p.get('request_id');row=hub.read()['requests'].get(key)
    if not row or row.get('kind')!='wallet_policy_refresh' or row['node_id']!=node or p.get('request_hash')!=digest(row['proposal']):raise ValueError('Policy result does not match this Agent’s exact request.')
    proposal=row['proposal']
    if p['status'] in ('denied','error'):
        with hub.edit() as state:
            current=state['requests'][key]
            if current['state']!='pending' and current.get('result')!=envelope:raise ValueError('Contradictory public policy result.')
            current.update(state=p['status'],result=copy.deepcopy(envelope),message='Declined locally on Agent' if p['status']=='denied' else 'Agent reported a public-policy error. Reconcile locally before retrying.')
        return
    if any(p.get(k)!=proposal[k] for k in ('wallet','previous_descriptors','receive_descriptor','change_descriptor')):raise ValueError('Approved policy differs from the requested transition.')
    transition={k:proposal[k] for k in ('previous_descriptors','receive_descriptor','change_descriptor','timestamp')}
    if p.get('policy_hash')!=digest(transition):raise ValueError('Agent approved a different public policy transition.')
    if not isinstance(p.get('monitor_wallet'),str) or not p['monitor_wallet']:raise ValueError('The locally installed monitor is missing.')
    # Signed acknowledgement is not sufficient: independently ask Core whether
    # both exact new public descriptors were installed, preserving old history.
    raw=hub.rpc(node,'listdescriptors',[False],p['monitor_wallet'])
    expected={NodeWalletLoader.descriptor_key(proposal[k]) for k in ('receive_descriptor','change_descriptor')}
    if not expected<={NodeWalletLoader.descriptor_key(r['desc']) for r in raw['descriptors']}:raise ValueError('Core did not confirm both refreshed public policies.')
    with hub.edit() as s:
        r=s['requests'][key]
        if r['state']!='pending':
            if r.get('result')!=envelope:raise ValueError('Contradictory public policy result.')
            return
        r.update(state='complete' if p['status']=='approved' else 'recovery_required',result=copy.deepcopy(envelope),verified_utc=hub.utc())
    hub.inventory_times[node]=0

def destination_ready(hub,d):
    rows=[r for r in hub.read()['requests'].values() if r.get('destination_id')==d['id']]
    connected={n['id'] for n in hub.registry.snapshot()['nodes'] if n.get('rpc_ready') and n.get('blockchain',{}).get('state')=='ready'}
    current=[r for r in rows if r['node_id'] in connected]
    if not current or any(r['state']!='complete' for r in current):return False
    # Require one persistent Core monitor; a transient disc alias alone cannot
    # establish the durable watch policy needed to rediscover future funds.
    return any(any(not w.get('signing_only') and equivalent(w['raw'],{'descriptors':[{'desc':r['proposal'][k]} for k in ('receive_descriptor','change_descriptor')]}) for w in hub.inventories.get(r['node_id'],{}).get('wallets',[])) for r in current)

def destination_issues(hub,d):
    names={n['id']:n['hostname'] for n in hub.registry.snapshot()['nodes']}
    rows=[r for r in hub.read()['requests'].values() if r.get('destination_id')==d['id'] and r['state']!='complete']
    return '; '.join(names.get(r['node_id'],'Agent')+': '+('declined locally; reconcile on Authorise' if r['state']=='denied' else 'history recovery required' if r['state']=='recovery_required' else 'error; reconcile locally' if r['state']=='error' else 'awaiting local approval or reconciliation') for r in rows)
