"""Read-only vault monitoring from independently validated policies and local nodes."""
import copy,threading,time
from datetime import datetime,timezone
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

def utc_now():return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def stale_snapshot(previous,reason):
    value=copy.deepcopy(previous);value.update(error=reason,loading=False,stale=True)
    for row in value.get('wallets',[]):
        row.update(state='amber',stale=True,funding_status='stale',countdown='Verification unavailable',estimated_seconds=None)
        row['last_verified_height']=row.get('last_verified_height',row.get('current_height'));row['current_height']=None
        row['issues']=list(dict.fromkeys([*row.get('issues',[]),'Current verification failed. Any retained balance is last-known data. '+reason]))
    return value

class VaultMonitor:
    def __init__(self,registry):self.registry=registry;self.value={'wallets':[],'loading':True};self.pending=False;self.last=0;self.lock=threading.Lock()
    def snapshot(self):
        with self.lock:
            if not self.pending and time.monotonic()-self.last>15:
                self.pending=True;threading.Thread(target=self.refresh,daemon=True).start()
            return stale_snapshot(self.value,'Vault verification is overdue. Waiting for a fresh node response.') if self.last and time.monotonic()-self.last>45 else copy.deepcopy(self.value)
    def refresh(self):
        try:value=self.compute()
        except Exception as exc:value=stale_snapshot(self.value,str(exc) if isinstance(exc,(ValueError,OSError,KeyError,TypeError)) else 'The node response could not be verified.')
        with self.lock:self.value=value;self.pending=False;self.last=time.monotonic()
    def compute(self):
        hub=self.registry.hub;nodes=self.registry.snapshot()['nodes'];active=[n for n in nodes if n['connected']];healthy=[n for n in active if n.get('blockchain',{}).get('state')=='ready' and n['rpc_ready']]
        state=hub.read();catalog=DescriptorStore(hub.config).read();family=FamilyStore(hub.config).snapshot();owners={r['source']:r for r in family.get('ownership',[]) if r['kind']=='descriptor'}
        checked_utc=utc_now();previous={w['id']:w for w in self.value.get('wallets',[])}
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
                if source and hub.permissions.permits(n['id'],source['raw']) is False:continue
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
                try:
                    from vendor.embit.descriptor import Descriptor
                    from multisig_psbt import witness_policy
                    desc=Descriptor.from_string(row['desc']);probe=desc.derive(0) if desc.is_wildcard else desc
                    checked=witness_policy(probe.witness_script().data)
                    if not checked['height']:raise ValueError('No mandatory height lock.')
                    g['policies'].append({'id':digest(row['desc']),'public':{'descriptor':row['desc'],'height':checked['height']},'valid':True,'funds':0,'ranged':True})
                    for n in active:
                        if hub.permissions.permits(n['id'],wallet['raw']) is False:continue
                        inv=hub.inventories.get(n['id'],{})
                        if not any(equivalent(wallet['raw'],w['raw']) for w in inv.get('wallets',[])):g['issues'].append(n['hostname']+': public vault policy is awaiting local Agent synchronisation.')
                        if n.get('blockchain',{}).get('state')!='ready':g['issues'].append(n['hostname']+': node is downloading or its blockchain status is unavailable.')
                    ceremonies=getattr(hub,'wallet_builder',None)
                    if ceremonies:
                        for setup in ceremonies.read()['sessions'].values():
                            if row['desc'] in setup.get('descriptors',[]) and setup['state']!='ready':g['issues'].append('Wallet setup policy approvals or physical backup proofs are incomplete.')
                            if row['desc'] in setup.get('descriptors',[]):
                                for n in healthy:
                                    for w in hub.inventories.get(n['id'],{}).get('wallets',[]):
                                        if not equivalent(wallet['raw'],w['raw']):continue
                                        recv=Descriptor.from_string(setup['descriptors'][0]).derive(0).address(__import__('vendor.embit.networks',fromlist=['NETWORKS']).NETWORKS['main'])
                                        labels=hub.rpc(n['id'],'getaddressinfo',[recv],w['name']).get('labels',[])
                                        if 'Wallaby setup ready:'+setup['policy_hash'] not in [a if isinstance(a,str) else a.get('name') for a in labels]:g['issues'].append(n['hostname']+': final setup completion has not been confirmed by Core.')
                except (ValueError,AttributeError,TypeError) as exc:g['issues'].append('Vault policy could not be independently verified: '+str(exc))
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
                        matched=addresses.get(coin.get('address'))
                        if not matched and any(p.get('ranged') for p in policies):
                            try:
                                from vendor.embit.descriptor import Descriptor
                                from multisig_psbt import witness_policy
                                concrete=Descriptor.from_string(coin.get('desc',''));ws=concrete.witness_script();checked=witness_policy(ws.data)
                                if concrete.script_pubkey().data.hex()!=coin['scriptPubKey']:raise ValueError('Coin differs from the concrete public policy.')
                                matched=next((p for p in policies if p.get('ranged') and p['public']['height']==checked['height']),None)
                            except (ValueError,AttributeError,TypeError):matched=None
                        if matched:matched['funds']+=int(value);group['vault_sats']+=int(value)
                        elif contains_source:group['outside_sats']+=int(value)
            if tip is None:group['issues'].append('No connected, synchronised Bitcoin node is available.')
            if not successful:group['issues'].append('The node could not establish the current vault balance.')
            balance_verified=successful and tip is not None and not group['issues']
            ready_to_fund=balance_verified and not group['vault_sats'] and not group['outside_sats']
            if group['outside_sats']:group['issues'].append('Bitcoin remains outside the vault policy: '+str(group['outside_sats'])+' sats.')
            if not group['vault_sats']:group['issues'].append('No confirmed Bitcoin was found under the validated vault policy.')
            funded=[p for p in policies if p['funds']>0 and p['valid']];heights=[p['public']['height'] for p in funded] or [p['public']['height'] for p in policies if p['valid']];height=min(heights) if heights else None
            seconds=max(0,height-tip)*600 if tip is not None and height is not None else None
            group['issues']=list(dict.fromkeys(group['issues']));group.update(height=height,current_height=tip,countdown='Unlocked' if seconds==0 else countdown(seconds) if seconds is not None else 'Verification needed',state='amber' if group['issues'] else 'red' if seconds==0 else 'green',estimated_seconds=seconds)
            signatures=set()
            for p in policies:
                if not p['valid']:continue
                try:
                    from vendor.embit.descriptor import Descriptor
                    from multisig_psbt import witness_policy
                    desc=Descriptor.from_string(p['public']['descriptor']);derived=desc.derive(0) if desc.is_wildcard else desc
                    proof=witness_policy(derived.witness_script().data);signatures.add((proof['threshold'],len(proof['keys'])))
                except (ValueError,AttributeError,TypeError):continue
            group.update(balance_available=successful,balance_verified=balance_verified,stale=False,checked_utc=checked_utc,last_verified_utc=checked_utc if balance_verified else previous.get(group['id'],{}).get('last_verified_utc'),signatures=[{'required':m,'total':n} for m,n in sorted(signatures)],funding_status='ready_to_fund' if ready_to_fund else 'needs_attention' if group['issues'] else 'unlocked' if seconds==0 else 'locked')
            if ready_to_fund:group['countdown']='Not funded'
            group.pop('outpoints');group.pop('source_wallet')
        return {'wallets':sorted(groups.values(),key=lambda w:w['name'].casefold()),'loading':False,'height':tip,'checked_utc':checked_utc,'stale':False}
