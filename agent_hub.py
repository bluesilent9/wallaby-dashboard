"""Durable public Agent exchange. Incoming proposals never authorise wallet operations.

The v1 Agent exchange is supported for public vault records and unsigned fixed vault
proposals. New partial-signing/peer-transfer operations are capability gated.
"""
import base64,copy,hashlib,ipaddress,json,os,re,threading,time,uuid
from pathlib import Path
from contextlib import contextmanager
from storage import storage_lock,data_directory
from vault_records import public_vault
from descriptor_store import clean_wallet,DescriptorStore
from node_wallets import NodeWalletLoader
from preferences import FamilyStore,resource_id
from agent_rpc import PRIVATE

CAPS={'node_identity','partial_signing','broadcast','wallet_transfer','wallet_retirement'}
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
def canonical(value):
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
    if PRIVATE.search(raw):raise ValueError('Private key material is forbidden in the Dashboard exchange.')
    return raw

def equivalent(a,b):
    return bool({NodeWalletLoader.descriptor_key(r['desc']) for r in a['descriptors']} & {NodeWalletLoader.descriptor_key(r['desc']) for r in b['descriptors']})

def ipv6(value):
    if not isinstance(value,str) or '%' in value:raise ValueError('A routable IPv6 address is required.')
    address=ipaddress.IPv6Address(value)
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.ipv4_mapped:raise ValueError('A peer IPv6 address cannot be a loopback, link-local or mapped address.')
    return str(address)

class AgentHub:
    def __init__(self,registry):
        self.registry=registry;self.config=registry.config;self.path=data_directory(self.config)/'agent-hub.json';self.lock=threading.RLock();self.leases={};self.caps={};self.inventories={};self.inventory_times={};self.inventory_busy=set();self.retired=set()
        from wallet_transfers import Transfers
        self.transfers=Transfers(self)
        from wallet_builder import WalletBuilder
        self.wallet_builder=WalletBuilder(self)
    def reset_live(self):
        with self.lock:
            self.leases.clear();self.retired.clear();self.caps.clear();self.inventories.clear();self.inventory_times.clear()
            from wallet_transfers import Transfers
            self.transfers=Transfers(self)
            from wallet_builder import WalletBuilder
            self.wallet_builder=WalletBuilder(self)
    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'version':1,'vaults':{},'nodes':{},'requests':{},'exports':{},'receipts':{}}
    @contextmanager
    def edit(self):
        with storage_lock(self.config),self.lock:
            state=self.read();yield state
            raw=canonical(state)
            if len(raw.encode())>64*1024*1024:raise ValueError('Agent exchange storage is full.')
            self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp');temp.write_text(raw,encoding='utf-8');temp.chmod(0o600)
            with temp.open('rb') as f:os.fsync(f.fileno())
            os.replace(temp,self.path)
    def node_summary(self,node):
        with self.lock:
            state=self.read();entry=state['nodes'].get(node,{})
            return {'ipv6':entry.get('ipv6'),'capabilities':sorted(self.caps.get(node,set())), 'inventory_ready':bool(self.inventories.get(node,{}).get('complete')),'inventory_error':self.inventories.get(node,{}).get('error'),'transfers':self.transfers.summaries(state,node),'version':next((c.get('agent_version') for c in self.registry.connections.values() if c['node_id']==node),None)}
    def rpc(self,node,method,params=None,wallet=None):
        from wallet_transfers import SynchronisationBusy
        if self.transfers.is_busy(node):raise SynchronisationBusy('Wallet synchronisation is active; node reads resume after both Agents finish.')
        try:row=self.registry.rpc.call(node,method,params or [],wallet,timeout_seconds=30,consume=True,priority=1)
        except ValueError:
            if self.transfers.is_busy(node):raise SynchronisationBusy('Wallet synchronisation is active; node reads resume afterwards.') from None
            raise
        if (row.get('error') or {}).get('code')=='synchronisation_busy':raise SynchronisationBusy('Agent wallet synchronisation is temporarily busy.')
        if row['state']!='succeeded':raise ValueError(str((row.get('error') or {}).get('message',row['state'])))
        return row['result']
    def refresh_inventory(self,node,token):
        if self.transfers.is_busy(node):return
        with self.lock:
            if node in self.inventory_busy or time.monotonic()-self.inventory_times.get(node,0)<60:return
            self.inventory_busy.add(node);self.inventory_times[node]=time.monotonic()
        try:
            names=self.rpc(node,'listwallets');directory=self.rpc(node,'listwalletdir');wallets=[]
            if not isinstance(names,list) or not all(isinstance(n,str) for n in names) or len(names)>256:raise ValueError('Invalid node wallet list.')
            if any(w.get('name') not in names for w in directory.get('wallets',[])):raise ValueError('Load every node wallet before checking missing wallets.')
            for name in names:
                info=self.rpc(node,'getwalletinfo',wallet=name);raw=self.rpc(node,'listdescriptors',[False],name)
                if info.get('walletname')!=name or raw.get('wallet_name')!=name:raise ValueError('Node wallet identity changed during discovery.')
                checked=clean_wallet(raw)
                item={'name':name,'id':checked['id'],'raw':checked['raw'],'private_keys_enabled':info.get('private_keys_enabled') is True,'scanning':bool(info.get('scanning'))}
                from labelled_wallets import expand
                replica={'wallet':checked,'private_keys_enabled':item['private_keys_enabled'],'history':{'transactions':[]}}
                for child in expand(replica,lambda address:self.rpc(node,'getaddressinfo',[address],name)):
                    w=child['wallet'];wallets.append(dict(item,id=w['id'],raw=w['raw'],extinct=bool(w.get('extinct')),address_labels=w.get('address_labels',{})))
            with self.lock:
                if token in self.registry.connections:self.inventories[node]={'wallets':wallets,'complete':True}
            self.wallet_builder.confirm_installed(node,token,wallets)
        except __import__('wallet_transfers').SynchronisationBusy:
            with self.lock:self.inventory_times[node]=0
        except (ValueError,OSError,KeyError,TypeError) as exc:
            with self.lock:self.inventories[node]={'wallets':[],'complete':False,'error':str(exc)}
        finally:
            with self.lock:self.inventory_busy.discard(node)
    def connection(self,token):
        with self.registry.lock:
            c=self.registry.rpc._connection(token)
            if not self.registry.rpc._unique(c):raise PermissionError('Resolve the node identity conflict first.')
            return dict(c)
    def lease(self,session,node,draft):
        if not session.user() or not session.access()['templates_ready']:raise ValueError('Complete Ownership before receiving a vault transaction.')
        if not isinstance(draft,str) or not re.fullmatch('[a-zA-Z0-9-]{1,80}',draft):raise ValueError('Invalid open diagram identity.')
        candidates=self.registry.snapshot()['nodes'];n=next((n for n in candidates if n['id']==node and n['rpc_ready'] and n.get('blockchain',{}).get('state')=='ready'),None)
        if not n:raise ValueError('Select a connected, synchronised node for this diagram.')
        key=session.session_dir.name
        with self.lock:
            if (key,draft) in self.retired:raise ValueError('This diagram was closed. Open a new diagram.')
            rows=[dict(v,id=k) for k,v in self.read()['exports'].items() if v['user_id']==session.user_id and v['draft_id']==draft and v['node_id']==node]
            self.leases[key]={'node_id':node,'user_id':session.user_id,'draft_id':draft,'expires':time.monotonic()+20,'consumed':bool(rows)}
        return {'exports':rows}
    def clear_lease(self,session):
        with self.lock:
            key=session.session_dir.name;prior=self.leases.pop(key,None)
            if prior:self.retired.add((key,prior['draft_id']))
    def pair_transfers(self,state):
        ready={c['node_id']:c for c in list(self.registry.connections.values()) if self.registry.rpc._unique(c)}
        from policy_accounts import cosigner_accounts,keys as account_keys
        setups=list(self.wallet_builder.read()['sessions'].values())
        intentional=cosigner_accounts([w for inv in self.inventories.values() for w in inv.get('wallets',[])])
        for target in sorted(ready):
            dest=self.inventories.get(target,{})
            if not dest.get('complete'):continue
            for source in sorted(ready):
                if source==target:continue
                origin=self.inventories.get(source,{})
                if not origin.get('complete'):continue
                for wallet in origin['wallets']:
                    try:
                        from wallet_preview import policies
                        policies(wallet['raw'],allow_addresses=True)
                    except ValueError:continue
                    if any(NodeWalletLoader.descriptor_key(r['desc']) in {NodeWalletLoader.descriptor_key(v['public']['descriptor']) for v in state['vaults'].values()} for r in wallet['raw']['descriptors']):continue
                    if wallet in intentional:continue
                    own=account_keys(wallet['raw'])
                    # A producing Agent installs a private role before its public
                    # monitor. Public signed contributions already identify that
                    # intentional key account during this short inventory window.
                    if wallet.get('private_keys_enabled') and own and any(own<={e['message'][f].split(']')[-1].replace("'",'h') for f in ('receive_key','change_key')} for setup in setups for e in setup['contributions'].values()):continue
                    policy_keys={NodeWalletLoader.descriptor_key(r['desc']) for r in wallet['raw']['descriptors']}
                    if any(policy_keys=={NodeWalletLoader.descriptor_key(d) for d in setup.get('descriptors',[])} and (setup['state']!='ready' or not set(setup['routes'].values())<=set(setup.get('completion_confirmed',{}))) for setup in setups):continue
                    if self.transfers.contains(dest['wallets'],wallet['raw']):continue
                    # One canonical source per missing public wallet, independent of names/private-key flags.
                    if any(r.get('kind')=='wallet_transfer' and r.get('direction')=='receive' and r['node_id']==target and equivalent(r['public_wallet'],wallet['raw']) for r in state['requests'].values()):continue
                    ident=digest({'target':target,'descriptor_id':wallet['id']})
                    earliest=min((r.get('timestamp',0) for r in wallet['raw']['descriptors']),default=0)
                    health=self.registry.health.status(ready[target]);requires=bool(health.get('pruned') and (not earliest or not health.get('prune_time') or earliest<health['prune_time']))
                    for node,peer,direction in ((source,target,'send'),(target,source,'receive')):
                        key=digest({'transfer':ident,'direction':direction})
                        if key in state['requests']:continue
                        state['requests'][key]={'kind':'wallet_transfer','node_id':node,'peer_id':peer,'direction':direction,'transfer_id':ident,'state':'pending','descriptor_id':wallet['id'],'public_wallet':wallet['raw'],'source_has_private_keys':wallet['private_keys_enabled'],'earliest_timestamp':earliest,'requires_blockchain_redownload':requires,'created_utc':self.utc()}
    @staticmethod
    def utc():
        from datetime import datetime,timezone
        return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    def exchange(self,token,data,agent_version=None):
        if not isinstance(data,dict) or not {'version','outbox','known_vaults'}<=set(data) or set(data)-{'version','outbox','known_vaults','capabilities','node_info'} or type(data['version']) is not int or data['version']!=1:raise ValueError('Unsupported Agent exchange schema.')
        c=self.connection(token);node=c['node_id'];items=data['outbox'];known=data['known_vaults']
        if agent_version is not None:
            if not isinstance(agent_version,str) or not re.fullmatch(r'[0-9A-Za-z.+~_-]{1,64}',agent_version):raise ValueError('Invalid Agent version header.')
            with self.registry.lock:self.registry.connections[token]['agent_version']=agent_version
        if not self.transfers.modern(node):raise ValueError('Update Wallaby Agent to V2 Development Build 8 or later.')
        if not isinstance(items,list) or len(items)>8 or not isinstance(known,list) or len(known)>128 or any(not isinstance(k,str) or not re.fullmatch('[0-9a-f]{64}',k) for k in known):raise ValueError('Invalid Agent exchange bounds.')
        caps=data.get('capabilities',[])
        if not isinstance(caps,list) or len(caps)>16 or any(k not in CAPS for k in caps):raise ValueError('Unsupported Agent capabilities.')
        canonical(data)
        verified=[]
        for item in items:
            if not isinstance(item,dict) or set(item)!={'id','kind','payload'} or item['id']!=digest({'kind':item['kind'],'payload':item['payload']}):raise ValueError('Agent item content hash does not match.')
            kind=item['kind'];p=item['payload']
            if kind=='vault_public':
                public_vault(p)
                if p['chain']!='main':raise ValueError('Dashboard vault monitoring requires mainnet.')
            elif kind=='transaction_builder_psbt':
                if not isinstance(p,dict) or set(p)!={'version','chain','psbt','transaction_hash','vault','review','stage','immutable'} or type(p['version']) is not int or p['version']!=1 or p['chain']!='main' or p['stage']!=2 or p['immutable'] is not True:raise ValueError('Invalid immutable Vault Builder export.')
                public_vault(p['vault'])
            elif kind not in ('partial_sign_result','broadcast_result','node_identity','sign_result','wallet_transfer_result','wallet_transfer_preview','wallet_retirement_result','wallet_setup_identity','wallet_setup_contribution','wallet_setup_ack','wallet_setup_inventory'):raise ValueError('Unsupported Agent outbox kind.')
            verified.append(item)
        # Validate expensive PSBTs before taking the durable-store lock.
        exported={}
        for item in verified:
            if item['kind']=='transaction_builder_psbt':
                leases=[v for v in self.leases.values() if v['node_id']==node and v['expires']>time.monotonic() and not v.get('consumed')]
                if len(leases)==1 and not exported:
                    from agent_transactions import validate_export
                    exported[item['id']]=(dict(leases[0]),validate_export(self,item['payload'],leases[0]))
        for item in verified:
            if item['kind']=='broadcast_result':
                from agent_transactions import verify_broadcast_result
                verify_broadcast_result(self,node,item['payload'])
        previews={}
        previous=self.read()
        for item in verified:
            if item['kind']=='wallet_transfer_preview' and node+':'+item['id'] not in previous['receipts']:
                payload=item['payload']
                if not isinstance(payload,dict) or set(payload)!={'request_hash','preview','public_key','signature'}:raise ValueError('Invalid preview outbox fields.')
                from wallet_preview import verify
                previews[item['id']]=verify(self.config,{k:payload[k] for k in ('preview','public_key','signature')})
        for item in verified:
            if item['kind'].startswith('wallet_setup_') and node+':'+item['id'] not in previous['receipts']:
                self.wallet_builder.accept(node,item['kind'],item['payload'])
        setup_proposals=self.wallet_builder.proposals(node,token)
        accepted=[]
        with self.edit() as state:
            if token not in self.registry.connections:raise PermissionError('Agent reconnected during exchange validation. Retry on the new connection.')
            self.caps[node]=set(caps)
            entry=state['nodes'].setdefault(node,{})
            if 'node_info' in data:
                info=data['node_info']
                if not isinstance(info,dict) or set(info)!={'ipv6'}:raise ValueError('Invalid Agent peer identity.')
                entry['ipv6']=ipv6(info['ipv6'])
            entry['known_vaults']=known;entry['last_exchange_utc']=self.utc()
            for item in verified:
                p=item['payload'];kind=item['kind'];receipt=node+':'+item['id']
                if kind=='transaction_builder_psbt':
                    if item['id'] not in exported:
                        if any(v.get('outbox_id')==item['id'] and v['node_id']==node for v in state['exports'].values()):accepted.append(item['id'])
                        continue
                    lease,plan=exported[item['id']]
                    live=[v for v in self.leases.values() if v['node_id']==node and v['expires']>time.monotonic() and not v.get('consumed')]
                    if len(live)!=1 or any(live[0][k]!=lease[k] for k in ('node_id','user_id','draft_id')):
                        if any(v.get('outbox_id')==item['id'] and v['node_id']==node for v in state['exports'].values()):accepted.append(item['id'])
                        continue
                    ident=digest({'item':item['id'],'node':node,'user':lease['user_id'],'draft':lease['draft_id']})
                    for v in self.leases.values():
                        if v['node_id']==node and v['user_id']==lease['user_id'] and v['draft_id']==lease['draft_id']:v['consumed']=True
                    state['exports'].setdefault(ident,dict(plan,outbox_id=item['id'],node_id=node,user_id=lease['user_id'],draft_id=lease['draft_id'],received_utc=self.utc()))
                elif receipt in state['receipts']:accepted.append(item['id']);continue
                elif kind.startswith('wallet_setup_'):pass
                elif kind=='vault_public':
                    ident=digest(p);row=state['vaults'].setdefault(ident,{'public':p,'sources':[]})
                    if node not in row['sources']:row['sources'].append(node)
                elif kind=='node_identity':
                    if not isinstance(p,dict) or set(p)!={'ipv6'}:raise ValueError('Invalid node identity result.')
                    entry['ipv6']=ipv6(p['ipv6'])
                elif kind in ('partial_sign_result','broadcast_result'):
                    self.accept_result(state,node,kind,p)
                elif kind=='wallet_retirement_result':
                    from wallet_retirement import result
                    result(state,node,p)
                    if p.get('status')=='complete':self.transfers.refresh_nodes.add(node)
                elif kind=='wallet_transfer_result':self.transfers.accept_result(state,node,p)
                elif kind=='wallet_transfer_preview':self.transfers.accept_preview(state,node,p,previews[item['id']])
                # Legacy sign_result means already broadcast; never interpret it as a partial signature.
                elif kind=='sign_result':raise ValueError('Legacy signing broadcasts immediately and cannot complete this partial-signing workflow.')
                state['receipts'][receipt]=self.utc();accepted.append(item['id'])
            self.pair_transfers(state)
            self.transfers.schedule_queues(state)
            transfer_proposals=self.transfers.proposals(state,node)
            proposals=list(transfer_proposals) if not self.transfers.is_busy(node) else []
            if self.transfers.is_busy(node):proposals=transfer_proposals
            else:
                if not entry.get('ipv6') and 'node_identity' in caps:proposals.append({'kind':'node_identity'})
                for ident,row in sorted(state['vaults'].items()):
                    has=any(any(NodeWalletLoader.descriptor_key(r['desc'])==NodeWalletLoader.descriptor_key(row['public']['descriptor']) for r in w['raw']['descriptors']) for w in self.inventories.get(node,{}).get('wallets',[]))
                    if ident not in known and node not in row['sources'] and not has:proposals.append({'kind':'vault_sync','vault':row['public']})
                for key,row in sorted(state['requests'].items()):
                    if row['node_id']!=node or row['state']!='pending' or row['kind']=='wallet_transfer':continue
                    if row['kind']=='wallet_retire' and 'wallet_retirement' in caps:
                        row['delivered']=True;proposals.append(row['proposal']);continue
                    if row['kind']=='sign_psbt_partial' and 'partial_signing' in caps or row['kind']=='broadcast_transaction' and 'broadcast' in caps:
                        row['delivered']=True;proposals.append(row['proposal'])
        # Read-only inventory refresh is outside the durable lock and waits for
        # both sides to finish. Presence, outbox and results continue throughout.
        for changed in list(self.transfers.refresh_nodes):
            if self.transfers.is_busy(changed):continue
            self.transfers.refresh_nodes.discard(changed);self.inventory_times[changed]=0
            connection=next((c for c in list(self.registry.connections.values()) if c['node_id']==changed),None)
            if connection:threading.Thread(target=self.refresh_inventory,args=(changed,connection['token']),daemon=True).start()
        queues=[p for p in proposals if p['kind']=='wallet_sync_queue']
        others=[p for p in proposals if p['kind']!='wallet_sync_queue']
        return {'version':1,'accepted':accepted,'proposals':(queues+setup_proposals+others)[:16]}
    def transfer_proposal(self,state,row):
        peer=row['peer_id'];record=next((c for c in self.registry.connections.values() if c['node_id']==peer),{})
        proposal={'kind':'wallet_transfer','direction':row['direction'],'transfer_id':row['transfer_id'],'peer':{'node_id':peer,'hostname':record.get('hostname',''),'ipv6':state['nodes'][peer]['ipv6']},'public_wallet':row['public_wallet'],'source_has_private_keys':row['source_has_private_keys'],'earliest_timestamp':row['earliest_timestamp'],'requires_blockchain_redownload':row['requires_blockchain_redownload'],'preview_challenge':row['challenge'],'node_ipv6':state['nodes'][row['node_id']]['ipv6']}
        proposal['transfer_version']=2
        return proposal
    def sync_warnings(self):
        from wallet_sync_queue import warnings
        return warnings(self)
    def accept_result(self,state,node,kind,p):
        from agent_transactions import accept_result
        return accept_result(self,state,node,kind,p)
