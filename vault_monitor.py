"""Read-only vault monitoring from independently validated policies and local nodes."""
import threading,time
from decimal import Decimal
from agent_hub import digest,equivalent
from vault_records import public_vault
from descriptor_store import DescriptorStore
from node_wallets import NodeWalletLoader
from preferences import FamilyStore,resource_id

UNITS=(('Year',365*86400),('Month',30*86400),('Week',7*86400),('Day',86400),('Hour',3600),('Minute',60),('Second',1))
def countdown(seconds):
    remaining=max(0,int(seconds));parts=[]
    for label,size in UNITS:
        n,remaining=divmod(remaining,size)
        if n:parts.append(str(n)+' '+label+('s' if n!=1 else ''))
        if len(parts)==2:break
    return ', '.join(parts) or 'Less than 1 Second'

class VaultMonitor:
    def __init__(self,registry):self.registry=registry;self.value={'wallets':[],'loading':True};self.pending=False;self.last=0;self.lock=threading.Lock()
    def snapshot(self):
        with self.lock:
            if not self.pending and time.monotonic()-self.last>15:
                self.pending=True;threading.Thread(target=self.refresh,daemon=True).start()
            return self.value
    def refresh(self):
        try:value=self.compute()
        except (ValueError,OSError,KeyError,TypeError) as exc:value={'wallets':self.value.get('wallets',[]),'error':str(exc),'loading':False}
        with self.lock:self.value=value;self.pending=False;self.last=time.monotonic()
    def compute(self):
        hub=self.registry.hub;nodes=self.registry.snapshot()['nodes'];active=[n for n in nodes if n['connected']];healthy=[n for n in active if n.get('blockchain',{}).get('state')=='ready' and n['rpc_ready']]
        state=hub.read();catalog=DescriptorStore(hub.config).read();family=FamilyStore(hub.config).snapshot();owners={r['source']:r for r in family.get('ownership',[]) if r['kind']=='descriptor'}
        tip=max((n['blockchain']['blocks'] for n in healthy),default=None);groups={}
        def add(v,ident,source_nodes):
            try:public_vault(v);valid=True;reason=None
            except ValueError as exc:valid=False;reason=str(exc)
            source=next((w for w in catalog['wallets'] if any(NodeWalletLoader.descriptor_key(r['desc'])==NodeWalletLoader.descriptor_key(v.get('source',{}).get('descriptor','')) for r in w['raw']['descriptors'])),None)
            uid=source['id'] if source else ident;name=owners.get(uid,{}).get('name') or (source or {}).get('name') or 'Vault '+v.get('address','unknown')[:18]
            g=groups.setdefault(uid,{'id':uid,'name':name,'policies':[],'issues':[],'source_wallet':source,'vault_sats':0,'outside_sats':0,'outpoints':set()})
            if source is None:g['issues'].append('The vault source has not been matched to a loaded Ownership wallet. Refresh node wallets and Ownership.')
            g['policies'].append({'id':ident,'public':v,'valid':valid,'funds':0})
            if reason:g['issues'].append(reason)
            for n in active:
                known=state['nodes'].get(n['id'],{}).get('known_vaults',[])
                inventory=hub.inventories.get(n['id'],{})
                has=any(any(NodeWalletLoader.descriptor_key(r['desc'])==NodeWalletLoader.descriptor_key(v.get('descriptor','')) for r in w['raw']['descriptors']) for w in inventory.get('wallets',[]))
                if ident not in known and n['id'] not in source_nodes and not has:g['issues'].append(n['hostname']+': latest vault policy is awaiting local Agent authorisation.')
                if n.get('blockchain',{}).get('state')!='ready':g['issues'].append(n['hostname']+': node is downloading or its blockchain status is unavailable.')
        for ident,row in state['vaults'].items():add(row['public'],ident,row['sources'])
        # A policy seen directly on a node must remain visible even if its Agent
        # has not yet published a verified public record, or it contains a bypass.
        known={NodeWalletLoader.descriptor_key(p['public'].get('descriptor','')) for g in groups.values() for p in g['policies']}
        for wallet in catalog['wallets']:
            for row in wallet['raw']['descriptors']:
                if 'after(' not in row['desc'] or NodeWalletLoader.descriptor_key(row['desc']) in known:continue
                uid=wallet['id'];g=groups.setdefault(uid,{'id':uid,'name':owners.get(uid,{}).get('name') or wallet['name'],'policies':[],'issues':[],'source_wallet':wallet,'vault_sats':0,'outside_sats':0,'outpoints':set()})
                g['issues'].append('Vault-like policy detected, but a validated Agent public vault record is missing. Its mandatory lock and key ownership have not been established.')
        for group in groups.values():
            policies=group['policies'];addresses={p['public'].get('address'):p for p in policies};source=group['source_wallet'];seen_wallets=set();successful=False
            for n in healthy:
                inv=hub.inventories.get(n['id'],{})
                if not inv.get('complete'):group['issues'].append(n['hostname']+': wallet inventory is not ready.');continue
                for w in inv['wallets']:
                    contains_source=source and equivalent(source['raw'],w['raw'])
                    contains_policy=any(NodeWalletLoader.descriptor_key(r['desc']) in {NodeWalletLoader.descriptor_key(p['public'].get('descriptor','')) for p in policies} for r in w['raw']['descriptors'])
                    if not contains_source and not contains_policy:continue
                    try:coins=hub.rpc(n['id'],'listunspent',[1,9999999,[],True],w['name']);successful=True
                    except ValueError as exc:group['issues'].append(n['hostname']+': '+str(exc));continue
                    if not isinstance(coins,list):raise ValueError('Invalid vault coin list.')
                    for coin in coins:
                        key=(coin['txid'],coin['vout'])
                        if key in group['outpoints']:continue
                        group['outpoints'].add(key);value=Decimal(str(coin['amount']))*100000000
                        if not value.is_finite() or value!=value.to_integral_value() or value<0:raise ValueError('Invalid vault balance amount.')
                        if coin.get('address') in addresses:addresses[coin['address']]['funds']+=int(value);group['vault_sats']+=int(value)
                        elif contains_source:group['outside_sats']+=int(value)
            if tip is None:group['issues'].append('No connected, synchronised Bitcoin node is available.')
            if not successful:group['issues'].append('The node could not establish the current vault balance.')
            if group['outside_sats']:group['issues'].append('Bitcoin remains outside the vault policy: '+str(group['outside_sats'])+' sats.')
            if not group['vault_sats']:group['issues'].append('No confirmed Bitcoin was found under the validated vault policy.')
            funded=[p for p in policies if p['funds']>0 and p['valid']];heights=[p['public']['height'] for p in funded] or [p['public']['height'] for p in policies if p['valid']];height=min(heights) if heights else None
            seconds=max(0,height-tip)*600 if tip is not None and height is not None else None
            group['issues']=list(dict.fromkeys(group['issues']));group.update(height=height,current_height=tip,countdown='Unlocked' if seconds==0 else countdown(seconds) if seconds is not None else 'Verification needed',state='amber' if group['issues'] else 'red' if seconds==0 else 'green',estimated_seconds=seconds)
            group.pop('outpoints');group.pop('source_wallet')
        return {'wallets':sorted(groups.values(),key=lambda w:w['name'].casefold()),'loading':False,'height':tip}
