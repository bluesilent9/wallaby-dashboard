"""Correlated public synchronization records; private files travel only between Agents."""
import copy,secrets,time
from wallet_preview import policies

TERMINAL={'complete','denied','error'}
class SynchronisationBusy(ValueError):pass
class Transfers:
    def __init__(self,hub):
        self.hub=hub;self.refresh_nodes=set();self.busy=set()
    @staticmethod
    def pairs(state):
        result={}
        for key,row in state['requests'].items():
            if row.get('kind')=='wallet_transfer':result.setdefault(row['transfer_id'],[]).append(row)
        return result
    def modern(self,node):
        from wallet_sync_queue import modern
        return modern(self.hub,node)
    @staticmethod
    def contains(wallets,raw):
        from wallet_sync_queue import contains
        return contains(wallets,raw)
    def schedule_queues(self,state):
        from wallet_sync_queue import schedule
        schedule(self,state)
    def is_busy(self,node):return node in self.busy
    def proposals(self,state,node):
        from wallet_sync_queue import proposals
        return proposals(self,state,node)
    def accept_preview(self,state,node,payload,proof):
        from agent_hub import digest
        if not isinstance(payload,dict) or set(payload)!={'request_hash','preview','public_key','signature'}:raise ValueError('Invalid wallet transfer preview outbox.')
        rows=next((rows for rows in self.pairs(state).values() if any(r.get('proposal') and digest(r['proposal'])==payload['request_hash'] for r in rows)),None)
        sender=next((r for r in rows or [] if r['direction']=='send' and r['node_id']==node),None)
        if not sender or digest(sender.get('proposal',{}))!=payload['request_hash'] or not sender.get('base_sent') or not sender.get('queue_visible'):raise ValueError('Unsolicited preview or not the paired sending Agent.')
        wire={k:payload[k] for k in ('preview','public_key','signature')};v=wire['preview'];p=sender['proposal']
        if sender.get('signed_preview') and sender['signed_preview']!=wire:raise ValueError('A frozen preview cannot be replaced. Cancel and start a fresh attempt.')
        if sender['state']!='pending' or any(r['state'] in ('denied','error') for r in rows):raise ValueError('This transfer is closed or failed. Do not replay it.')
        if (v['transfer_id'],v['challenge'],v['sender_ipv6'],v['receiver_ipv6'])!=(p['transfer_id'],p['preview_challenge'],p['node_ipv6'],p['peer']['ipv6']) or v['version']!=2 or policies(v['public_wallet'],True)!=policies(p['public_wallet'],True):raise ValueError('Preview wallet, challenge or peer addresses differ from the frozen request.')
        sender['signed_preview']=copy.deepcopy(wire);sender['preview_proof']=proof
    def accept_result(self,state,node,p):
        from agent_hub import digest
        if not isinstance(p,dict) or not {'request_hash','status'}<=set(p) or set(p)-{'request_hash','status','message'} or p['status'] not in TERMINAL|{'recovery_required'}:raise ValueError('Invalid transfer result.')
        row=next((r for r in state['requests'].values() if r.get('kind')=='wallet_transfer' and r.get('proposal') and digest(r['proposal'])==p['request_hash'] and r['node_id']==node),None)
        if not row or not row.get('base_sent'):raise ValueError('Unsolicited transfer result.')
        if p['status']=='recovery_required' and not row.get('queue_v2'):raise ValueError('History recovery requires Agent Build 8 or later.')
        if row['state'] in TERMINAL:
            if row['state']!=p['status']:raise ValueError('A terminal transfer outcome cannot be changed.')
            return
        if row['state']=='recovery_required' and p['status'] not in ('recovery_required','complete','error'):raise ValueError('History recovery must finish or report an error.')
        row.update(state=p['status'],message=str(p.get('message',''))[:1000],finished_utc=self.hub.utc())
        self.refresh_nodes.add(node)
    def retry(self,transfer,confirmed):
        from agent_hub import digest,equivalent
        if confirmed is not True:raise ValueError('Confirm both Agents are closed and Core has been reconciled before a new attempt.')
        with self.hub.edit() as state:
            rows=self.pairs(state).get(transfer)
            expired=bool(rows and all(r.get('message','').startswith('Signed preview expired.') for r in rows))
            if not rows or len(rows)!=2 or any(r['state']=='recovery_required' for r in rows) or (not expired and any(r['state'] not in TERMINAL for r in rows)) or all(r['state']=='complete' for r in rows):raise ValueError('Resolve or cancel this attempt on both Agents before retrying.')
            old=next(r for r in rows if r['direction']=='receive');inv=self.hub.inventories.get(old['node_id'],{})
            if not inv.get('complete') or time.monotonic()-self.hub.inventory_times.get(old['node_id'],0)>60:raise ValueError('Wait for the destination wallet inventory to refresh before retrying.')
            if any(equivalent(old['public_wallet'],w['raw']) for w in inv['wallets']):raise ValueError('The destination already has this wallet. No retry is needed.')
            sender=next(r for r in rows if r['direction']=='send');source=self.hub.inventories.get(sender['node_id'],{})
            if not source.get('complete') or time.monotonic()-self.hub.inventory_times.get(sender['node_id'],0)>60:raise ValueError('Wait for the source wallet inventory to refresh before retrying.')
            wallet=next((w for w in source['wallets'] if equivalent(w['raw'],sender['public_wallet'])),None)
            if not wallet:raise ValueError('The source wallet is no longer present. Reload node wallets.')
            if any(r.get('retry_of')==transfer for r in state['requests'].values()):raise ValueError('A new attempt already exists.')
            for previous in rows:previous.update(state='error',queue_visible=False,message='Superseded by a deliberate fresh attempt after local reconciliation.')
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
                complete=any(x['direction']=='receive' and x['state']=='complete' for x in rows)
                result.append({'transfer_id':ident,'wallet_name':r['public_wallet']['wallet_name'],'direction':r['direction'],'state':'complete' if complete else r['state'],'message':'Receiver verified wallet data and history.' if complete else r.get('message',''),'active':bool(r.get('active_pair')),'preview_ready':any(x.get('signed_preview') for x in rows),'retryable':(terminal or all(x.get('message','').startswith('Signed preview expired.') for x in rows)) and not any(x['state']=='recovery_required' for x in rows) and not any(x['direction']=='receive' and x['state']=='complete' for x in rows) and not any(x.get('retry_of')==ident for x in state['requests'].values())})
        return result
