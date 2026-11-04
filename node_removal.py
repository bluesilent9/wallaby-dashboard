"""Forget a Dashboard route and its obligations; never delete remote wallet files.

Keep applied evidence for audit. Node-bound pending work is withdrawn, not
reported successful. Physical disc tasks can remain unassigned for another Agent.
"""
import copy,json,os,secrets
from pathlib import Path
from storage import storage_lock,data_directory

def remove(registry,node,user,confirmed):
    if confirmed is not True:raise ValueError('Confirm deletion of this remembered node first.')
    hub=registry.hub;data=data_directory(registry.config)
    with registry.lock,storage_lock(registry.config):
        from nodes import DISCONNECT_SECONDS
        if any(c['node_id']==node and registry.clock()-c['seen']<DISCONNECT_SECONDS for c in registry.connections.values()):raise ValueError('Disconnect the Agent before deleting its remembered node. Remote wallet files are not deleted.')
        nodes=registry._read();record=next((n for n in nodes['nodes'] if n['id']==node),None)
        if not record:raise ValueError('This node is no longer remembered.')
        paths=['agent-hub.json','wallet-renames.json','archive-queue.json','builder-workspaces.json','wallet-setups.json','node-removals.json']
        states={name:json.loads((data/name).read_text()) for name in paths if (data/name).exists()}
        state=states.get('agent-hub.json',{});requests=state.get('requests',{})
        pairs={r['transfer_id'] for r in requests.values() if r.get('kind')=='wallet_transfer' and (r.get('node_id')==node or r.get('peer_id')==node)}
        removed=[]
        for ident,row in requests.items():
            if row.get('node_id')==node or row.get('transfer_id') in pairs:
                row.update(node_deleted=True,queue_visible=False);removed.append(ident)
                if row.get('kind')=='wallet_transfer':row.update(superseded_container=True,superseded_message='A node was removed. This review is no longer requested; actual local outcomes remain recorded.')
        for root_key,root in requests.items():
            if 'expected_holders' in root:
                root['expected_holders'].pop(node,None)
                if not root['expected_holders']:
                    root['node_removal_parked']=True
                    for ident,row in requests.items():
                        if (row is root or row.get('root_hash')==root_key) and row.get('state')=='pending':row['node_deleted']=True
        state.get('nodes',{}).pop(node,None)
        for value in state.get('exports',{}).values():
            if value.get('node_id')==node:value['node_deleted']=True
        for vault in state.get('vaults',{}).values():vault['sources']=[n for n in vault.get('sources',[]) if n!=node]
        renames=states.get('wallet-renames.json',{})
        for op in renames.get('operations',{}).values():
            for t in op['targets'].values():
                if t.get('node_id')==node:
                    if t['storage']=='disc' and t['state']!='complete':t.update(node_id=None,hostname='Choose Agent with matching archive disc',request=None)
                    else:t['node_deleted']=True
            remaining=[t for t in op['targets'].values() if not t.get('node_deleted')]
            if op['state']=='active' and (not remaining or all(t['state']=='complete' for t in remaining)):op['state']='complete' if remaining else 'withdrawn'
        if renames:renames['revision']+=1
        archive=states.get('archive-queue.json',{})
        for row in archive.get('requests',{}).values():
            if row.get('node_id')!=node:continue
            task=row['request']
            portable=task['kind']=='destroy_disc' or task['kind']=='archive_cancel' and bool(task.get('completed_receipt'))
            if portable and row['state']=='active':row.update(node_id=None,previous_node_id=node,hostname='Choose Agent with matching archive disc',status='Choose an Agent with this disc loaded')
            else:
                row['node_deleted']=True
                if row['state']=='active':row.update(state='withdrawn',status='Node removed; Dashboard no longer requests this operation. Unknown local disc outcomes require local review.')
        if archive:archive['revision']+=1
        work=states.get('builder-workspaces.json',{})
        for batch in work.get('cancellations',{}).values():
            removed_target=batch['targets'].pop(node,None)
            if removed_target:batch.setdefault('removed_targets',{})[node]=removed_target
            batch['complete']=all(t.get('received_utc') for t in batch['targets'].values())
        for kind,w in list(work.get('workspaces',{}).items()):
            batch=work.get('cancellations',{}).get(w.get('cancellation_id'))
            if batch and batch['complete']:work['workspaces'].pop(kind)
        setups=states.get('wallet-setups.json',{});setups.get('challenges',{}).pop(node,None)
        for session in setups.get('sessions',{}).values():
            session['delivered_nodes']=[n for n in session.get('delivered_nodes',[]) if n!=node]
            if node in session.get('routes',{}).values() and session['state'] in ('participation','policy'):
                session.update(state='interrupted',message='A producing node was removed. Existing keys and approvals are retained; reconcile locally before starting another wallet creation.')
        audit=states.setdefault('node-removals.json',{'version':1,'events':[]})
        if len(audit['events'])>=10000:raise ValueError('Node-removal history is full.')
        audit['events'].append({'id':secrets.token_hex(16),'node':record,'user_id':user,'removed_utc':hub.utc(),'withdrawn_requests':removed})
        nodes['nodes']=[n for n in nodes['nodes'] if n['id']!=node]
        # Registry is committed last. Roll back every local file on I/O failure.
        writes={data/name:json.dumps(value,ensure_ascii=True).encode() for name,value in states.items()};writes[registry.path]=json.dumps(nodes,ensure_ascii=True).encode()
        originals={p:p.read_bytes() if p.exists() else None for p in writes}
        def write(path,body):
            path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_bytes(body);tmp.chmod(0o600);os.replace(tmp,path)
        try:
            for path,body in writes.items():write(path,body)
        except BaseException:
            for path,body in originals.items():
                if body is None:path.unlink(missing_ok=True)
                else:write(path,body)
            raise
        for token,c in list(registry.connections.items()):
            if c['node_id']==node:registry.rpc.disconnected(token);registry.connections.pop(token,None)
        for cache in (hub.inventories,hub.inventory_times,hub.caps):cache.pop(node,None)
        hub.transfers.busy.difference_update({node}|{r['node_id'] for r in requests.values() if r.get('transfer_id') in pairs})
        hub.transfers.refresh_nodes.discard(node)
    registry.diagnostic_log.append('Nodes','Node removed: '+record['hostname']+'; remote wallets were not deleted.')
    return {'deleted':True,'node_id':node}
