"""Observe Agent-owned receive permissions. No Dashboard denial list or restore authority."""
import re,secrets,time,threading
from agent_hub import digest
from setup_signatures import verify,fingerprint
from node_wallets import NodeWalletLoader

NAMESPACE='wallaby-sync-preferences-v1'
def policy_id(raw):
    from wallet_preview import policies
    policies(raw,allow_addresses=True)
    return digest(sorted({NodeWalletLoader.descriptor_key(r['desc']) for r in raw['descriptors']}))

class Permissions:
    def __init__(self,hub):self.hub=hub;self.live={};self.lock=threading.RLock()
    def supported(self,node):
        v=next((c.get('agent_version','') for c in self.hub.registry.connections.values() if c['node_id']==node),'')
        if not v and node in self.live:return True
        m=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:~dev(\d+))?',v or '')
        return bool(m and (*map(int,m.groups()[:3]),int(m[4]) if m[4] else 10**9)>=(2,0,0,10))
    def current(self,node):
        tokens=[k for k,c in list(self.hub.registry.connections.items()) if c['node_id']==node]
        if len(tokens)!=1:return None
        row=self.live.get(node)
        return row if row and row['token']==tokens[0] else None
    def permits(self,node,raw):
        if not self.supported(node):return True
        row=self.current(node)
        if not row or row.get('record') is None or time.monotonic()-row.get('checked',0)>45:return None
        return policy_id(raw) not in row['record']['policies']
    def proposals(self,node,token):
        if not self.supported(node):return []
        with self.lock:
            row=self.current(node)
            if row is None:
                row={'token':token,'identity_challenge':secrets.token_hex(32),'challenge':None,'record':None,'checked':0,'receipts':[],'deferred':[]};self.live[node]=row
            if not row.get('identity'):return [{'kind':'wallet_setup_identity','version':1,'challenge':row['identity_challenge']}]
            if row['challenge'] is None and time.monotonic()-row['checked']<10:return []
            if row['challenge'] is None:row['challenge']=secrets.token_hex(32)
            # Current Agent state is authoritative. Never restore an old Dashboard
            # snapshot over a locally enabled or deleted Agent preference.
            return [{'kind':'wallet_sync_preferences','version':1,'challenge':row['challenge'],'previous':None}]
    def identity_reply(self,node,envelope):
        row=self.current(node);m=envelope.get('message',{}) if isinstance(envelope,dict) else {}
        if not row or m.get('challenge')!=row['identity_challenge']:return False
        key=envelope.get('public_key');verify(self.hub.config,envelope,key)
        if set(m)!={'version','challenge','public_key','fingerprint','hostname'} or m['version']!=1 or m['public_key']!=key or m['fingerprint']!=fingerprint(key):raise ValueError('Invalid Agent permission identity response.')
        known=self.hub.wallet_builder.read()['identities'].get(node)
        if known and known['public_key']!=key:raise ValueError('Agent identity differs from the previously observed setup identity. Reconcile locally.')
        with self.lock:row['identity']=key
        return True
    def accept(self,node,p,item_id=None):
        row=self.current(node)
        if not row or not isinstance(p,dict) or set(p)!={'version','challenge','snapshot'} or p['version']!=1:raise ValueError('Invalid Agent synchronization permission response.')
        if item_id and item_id in row['receipts']:return True
        if not row.get('identity'):
            if item_id:row['deferred']=(row['deferred']+[item_id])[-32:]
            return None
        stale=row['challenge'] is None or p['challenge']!=row['challenge']
        if stale and item_id not in row['deferred']:raise ValueError('Unsolicited or stale Agent synchronization permission response.')
        m=verify(self.hub.config,p['snapshot'],row['identity'],namespace=NAMESPACE)
        if not isinstance(m,dict) or set(m)!={'version','purpose','agent_key','revision','policies'} or m['version']!=1 or m['purpose']!='wallet_sync_preferences' or m['agent_key']!=row['identity'] or type(m['revision']) is not int or m['revision']<0 or not isinstance(m['policies'],list) or len(m['policies'])>128 or any(not isinstance(k,str) or not re.fullmatch('[0-9a-f]{64}',k) for k in m['policies']) or len(set(m['policies']))!=len(m['policies']):raise ValueError('Invalid Agent synchronization permission record.')
        if stale:
            if item_id:row['receipts']=(row['receipts']+[item_id])[-32:]
            return False
        prior=row['record']
        if prior is not None and (m['revision']<prior['revision'] or m['revision']==prior['revision'] and m!=prior):raise ValueError('Agent synchronization permission rollback or conflicting revision.')
        with self.lock:
            row.update(record=m,checked=time.monotonic(),challenge=None)
            if item_id:row['receipts']=(row['receipts']+[item_id])[-32:]
        return True
    def summary(self,node):
        if not self.supported(node):return None
        row=self.current(node);record=row.get('record') if row else None
        fresh=bool(record is not None and time.monotonic()-row.get('checked',0)<=45)
        names={}
        for inv in self.hub.inventories.values():
            for w in inv.get('wallets',[]):
                try:names.setdefault(policy_id(w['raw']),w['raw']['wallet_name'])
                except ValueError:continue
        return {'ready':fresh,'fingerprint':fingerprint(row['identity']) if row and row.get('identity') else None,'disabled':[{'policy_id':p,'wallet_name':names.get(p,'Public policy '+p[:16])} for p in record['policies']] if fresh else [],'text':'Synchronization permissions are controlled on this Agent.' if fresh else 'Checking this Agent’s synchronization permissions…'}
