"""One frozen paired public request at a time; private files travel only between Agents."""
import copy,secrets,time
from wallet_preview import policies,verify

TERMINAL={'complete','denied','error'}
class SynchronisationBusy(ValueError):pass
class Transfers:
    def __init__(self,hub):
        self.hub=hub;self.refresh_nodes=set();self.busy=set()
        for rows in self.pairs(hub.read()).values():
            if any(r.get('active_pair') for r in rows) and not all(r['state'] in TERMINAL for r in rows):self.busy.update(r['node_id'] for r in rows)
    @staticmethod
    def pairs(state):
        result={}
        for key,row in state['requests'].items():
            if row.get('kind')=='wallet_transfer':result.setdefault(row['transfer_id'],[]).append(row)
        return result
    def is_busy(self,node):return node in self.busy
    def schedule(self,state):
        pairs=self.pairs(state);self.busy=set()
        for rows in pairs.values():
            # Build 8 requests cannot negotiate a preview; do not replay them.
            if any(r.get('proposal') and not r['proposal'].get('preview_challenge') for r in rows):
                for r in rows:
                    if r['state']=='pending':r.update(state='error',message='Older transfer request needs local cancellation and a deliberate fresh attempt.')
            if any(r.get('active_pair') for r in rows):
                if all(r['state'] in TERMINAL for r in rows):
                    for r in rows:r['active_pair']=False;self.refresh_nodes.add(r['node_id'])
                else:self.busy.update(r['node_id'] for r in rows)
        if self.busy:return
        candidates=[];ready={c['node_id']:c for c in list(self.hub.registry.connections.values()) if self.hub.registry.rpc._unique(c)}
        # Do not interrupt approval work already in flight or foreground read jobs.
        occupied={r['node_id'] for r in state['requests'].values() if r.get('kind')!='wallet_transfer' and r.get('delivered') and r['state']=='pending'}
        occupied.update(r['node_id'] for r in list(self.hub.registry.rpc.rows.values()) if r['state'] in ('delivered','running') or r['state']=='queued' and r.get('priority',0)==0)
        for ident,rows in pairs.items():
            if len(rows)!=2 or any(r['state']!='pending' for r in rows):continue
            nodes={r['node_id'] for r in rows}
            if nodes&occupied or not nodes<=ready.keys():continue
            if any('wallet_transfer' not in self.hub.caps.get(n,set()) or not state['nodes'].get(n,{}).get('ipv6') for n in nodes):continue
            if any(self.hub.registry.health.status(ready[n]).get('state')!='ready' for n in nodes):continue
            candidates.append((rows[0]['public_wallet']['wallet_name'].casefold(),rows[0]['descriptor_id'],ident,rows))
        if not candidates:return
        rows=min(candidates,key=lambda x:x[:3])[3];challenge=secrets.token_hex(32)
        for r in rows:
            r['challenge']=challenge;r['active_pair']=True;r['proposal']=self.hub.transfer_proposal(state,r);r['base_sent']=0;r['preview_turn']=False
        self.busy={r['node_id'] for r in rows}
    def proposals(self,state,node):
        rows=next((rows for rows in self.pairs(state).values() if any(r.get('active_pair') for r in rows) and any(r['node_id']==node for r in rows)),None)
        if not rows:return []
        row=next(r for r in rows if r['node_id']==node)
        if row['state']!='pending':return []
        sender=next(r for r in rows if r['direction']=='send')
        # Never put preview in the same initial response as the base request.
        # Alternate base-only/preview-only thereafter, so a lost base response
        # cannot leave the receiver rejecting an unsolicited preview forever.
        payload=sender.get('signed_preview')
        if row['direction']=='receive' and row['base_sent'] and payload:
            if not time.time()-900<=payload['preview']['generated_at']<=time.time()+120:
                row['message']='Signed preview expired. Cancel on both Agents, reconcile Core, then start a fresh attempt.'
            else:
                row['preview_turn']=not row.get('preview_turn',False)
                if row['preview_turn']:return [{'kind':'wallet_transfer_preview',**copy.deepcopy(payload)}]
        row['base_sent']+=1
        return [copy.deepcopy(row['proposal'])]
    def accept_preview(self,state,node,payload,proof):
        from agent_hub import digest
        if not isinstance(payload,dict) or set(payload)!={'request_hash','preview','public_key','signature'}:raise ValueError('Invalid wallet transfer preview outbox.')
        rows=next((rows for rows in self.pairs(state).values() if any(r.get('proposal') and digest(r['proposal'])==payload['request_hash'] for r in rows)),None)
        sender=next((r for r in rows or [] if r['direction']=='send' and r['node_id']==node),None)
        if not sender or digest(sender.get('proposal',{}))!=payload['request_hash'] or not sender.get('base_sent') or not sender.get('active_pair'):raise ValueError('Unsolicited preview or not the paired sending Agent.')
        wire={k:payload[k] for k in ('preview','public_key','signature')};v=wire['preview'];p=sender['proposal']
        if sender.get('signed_preview') and sender['signed_preview']!=wire:raise ValueError('A frozen preview cannot be replaced. Cancel and start a fresh attempt.')
        if sender['state']!='pending' or any(r['state'] in ('denied','error') for r in rows):raise ValueError('This transfer is closed or failed. Do not replay it.')
        if (v['transfer_id'],v['challenge'],v['sender_ipv6'],v['receiver_ipv6'])!=(p['transfer_id'],p['preview_challenge'],p['node_ipv6'],p['peer']['ipv6']) or policies(v['public_wallet'])!=policies(p['public_wallet']):raise ValueError('Preview wallet, challenge or peer addresses differ from the frozen request.')
        sender['signed_preview']=copy.deepcopy(wire);sender['preview_proof']=proof
    def accept_result(self,state,node,p):
        from agent_hub import digest
        if not isinstance(p,dict) or not {'request_hash','status'}<=set(p) or set(p)-{'request_hash','status','message'} or p['status'] not in TERMINAL:raise ValueError('Invalid transfer result.')
        row=next((r for r in state['requests'].values() if r.get('kind')=='wallet_transfer' and r.get('proposal') and digest(r['proposal'])==p['request_hash'] and r['node_id']==node),None)
        if not row or not row.get('base_sent'):raise ValueError('Unsolicited transfer result.')
        if row['state'] in TERMINAL:
            if row['state']!=p['status']:raise ValueError('A terminal transfer outcome cannot be changed.')
            return
        row.update(state=p['status'],message=str(p.get('message',''))[:1000],finished_utc=self.hub.utc())
        self.refresh_nodes.add(node)
    def retry(self,transfer,confirmed):
        from agent_hub import digest,equivalent
        if confirmed is not True:raise ValueError('Confirm both Agents are closed and Core has been reconciled before a new attempt.')
        with self.hub.edit() as state:
            rows=self.pairs(state).get(transfer)
            if not rows or len(rows)!=2 or any(r['state'] not in TERMINAL for r in rows) or all(r['state']=='complete' for r in rows):raise ValueError('Resolve or cancel this attempt on both Agents before retrying.')
            old=next(r for r in rows if r['direction']=='receive');inv=self.hub.inventories.get(old['node_id'],{})
            if not inv.get('complete') or time.monotonic()-self.hub.inventory_times.get(old['node_id'],0)>60:raise ValueError('Wait for the destination wallet inventory to refresh before retrying.')
            if any(equivalent(old['public_wallet'],w['raw']) for w in inv['wallets']):raise ValueError('The destination already has this wallet. No retry is needed.')
            sender=next(r for r in rows if r['direction']=='send');source=self.hub.inventories.get(sender['node_id'],{})
            if not source.get('complete') or time.monotonic()-self.hub.inventory_times.get(sender['node_id'],0)>60:raise ValueError('Wait for the source wallet inventory to refresh before retrying.')
            wallet=next((w for w in source['wallets'] if equivalent(w['raw'],sender['public_wallet'])),None)
            if not wallet:raise ValueError('The source wallet is no longer present. Reload node wallets.')
            if any(r.get('retry_of')==transfer for r in state['requests'].values()):raise ValueError('A new attempt already exists.')
            ident=secrets.token_hex(32)
            for old in rows:
                row={k:copy.deepcopy(old[k]) for k in ('kind','node_id','peer_id','direction','descriptor_id','public_wallet','source_has_private_keys','earliest_timestamp','requires_blockchain_redownload')}
                row.update(transfer_id=ident,state='pending',retry_of=transfer,created_utc=self.hub.utc(),public_wallet=copy.deepcopy(wallet['raw']),source_has_private_keys=wallet['private_keys_enabled']);state['requests'][digest({'transfer':ident,'direction':row['direction']})]=row
        return {'queued':True,'transfer_id':ident}
    def summaries(self,state,node):
        result=[]
        for ident,rows in self.pairs(state).items():
            for r in rows:
                if r['node_id']!=node:continue
                terminal=all(x['state'] in TERMINAL for x in rows)
                result.append({'transfer_id':ident,'wallet_name':r['public_wallet']['wallet_name'],'direction':r['direction'],'state':r['state'],'message':r.get('message',''),'active':bool(r.get('active_pair')),'preview_ready':any(x.get('signed_preview') for x in rows),'retryable':terminal and not all(x['state']=='complete' for x in rows) and not any(x.get('retry_of')==ident for x in state['requests'].values())})
        return result
