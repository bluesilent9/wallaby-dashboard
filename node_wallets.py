"""Read-only wallet discovery across every connected Agent; no public metadata lookup."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import re,threading
from descriptor_store import clean_wallet
from descriptor_policy import needs_history,parse_policy
from vendor.embit.descriptor.miniscript import Wrapper
from vendor.embit.base import EmbitError

REQUIRED={'listwallets','listwalletdir','getwalletinfo','listdescriptors','listsinceblock','getaddressinfo'}

class NodeWalletLoader:
    def __init__(self,registry,progress=print,cancel=None):
        self.registry=registry;self.progress=progress;self.cancel=cancel or threading.Event()
        self.limits={};self.cursor={};self.lock=threading.Lock()
    def check(self):
        if self.cancel.is_set():raise ValueError('Node wallet discovery cancelled.')
    def rpc(self,node,method,params=None,wallet=None):
        self.check()
        with self.limits[node['id']]:
            self.check();state=self.registry.rpc.call(node['id'],method,params,wallet,timeout_seconds=120,consume=True,cancel=self.cancel)
        self.check()
        if state['state']!='succeeded':
            error=state.get('error') or {}
            target=node['hostname']+(' / '+(wallet or 'Default Core Wallet') if wallet is not None else '')
            message=str(error.get('message',state['state']))
            if str(error.get('code'))=='-18':message+=' Update Wallaby Agent to V2 Development Build 4 or later; earlier builds append a slash to the wallet RPC endpoint.'
            raise ValueError(target+' · '+method+': '+message)
        return state.get('result')
    def parallel(self,items,fn):
        with ThreadPoolExecutor(max_workers=min(16,max(1,len(items)))) as pool:
            jobs={pool.submit(fn,item):i for i,item in enumerate(items)}
            ordered={}
            for future in as_completed(jobs):ordered[jobs[future]]=future.result()
        return [ordered[i] for i in range(len(items))]
    def discover_node(self,node):
        names=self.rpc(node,'listwallets')
        directory=self.rpc(node,'listwalletdir')
        if not isinstance(names,list) or any(not isinstance(n,str) for n in names):raise ValueError('Invalid listwallets response from '+node['hostname'])
        if not isinstance(directory,dict) or not isinstance(directory.get('wallets'),list):raise ValueError('Invalid listwalletdir response from '+node['hostname'])
        unavailable=[w.get('name') for w in directory['wallets'] if w.get('name') not in names]
        if unavailable:raise ValueError(node['hostname']+': load these wallets in Bitcoin Core and retry: '+', '.join(str(n) for n in unavailable))
        self.progress(node['hostname']+': '+str(len(names))+' loaded wallets discovered.')
        return [(node,name) for name in sorted(set(names))]
    def export(self,replica):
        node,name=replica
        info=self.rpc(node,'getwalletinfo',wallet=name)
        if not isinstance(info,dict) or info.get('walletname')!=name:raise ValueError('Wallet identity response did not match '+node['hostname']+' / '+name)
        if info.get('scanning'):raise ValueError(node['hostname']+' / '+name+': wait for the Core wallet rescan to finish.')
        raw=self.rpc(node,'listdescriptors',[False],wallet=name)
        if not isinstance(raw,dict) or raw.get('wallet_name')!=name:raise ValueError('Invalid public descriptor export from '+node['hostname']+' / '+name)
        wallet=clean_wallet(raw)
        history=self.rpc(node,'listsinceblock',['',1,True,False,True],wallet=name)
        if not isinstance(history,dict) or not isinstance(history.get('transactions'),list) or not re.fullmatch('[0-9a-f]{64}',str(history.get('lastblock'))):raise ValueError('Invalid Core public history for '+name)
        self.progress(node['hostname']+' / '+(name or 'Default Core Wallet')+': public descriptors and wallet history received.')
        return {'node':node,'rpc_name':name,'wallet':wallet,'history':history,'private_keys_enabled':info.get('private_keys_enabled')}
    @staticmethod
    def descriptor_key(text):
        # Origins describe provenance, not the public locking policy. Core may
        # export equivalent keys with different origins or h/apostrophe spelling.
        return re.sub(r'\[[^\]]*\]','',text.split('#')[0]).replace("'",'h')
    @classmethod
    def keys(cls,replica):return {cls.descriptor_key(r['desc']) for r in replica['wallet']['raw']['descriptors']}
    def address_info(self,task):
        group,address=task
        with self.lock:
            key=id(group);idx=self.cursor.get(key,0);self.cursor[key]=idx+1
        replicas=group[idx%len(group):]+group[:idx%len(group)]
        errors=[]
        for replica in replicas:
            try:
                value=self.rpc(replica['node'],'getaddressinfo',[address],wallet=replica['rpc_name'])
                if not isinstance(value,dict) or value.get('address')!=address or not (value.get('ismine') or value.get('iswatchonly')):continue
                return address,value
            except ValueError as exc:errors.append(str(exc))
        raise ValueError('No wallet replica could establish ownership of received address '+address+('. '+errors[0] if errors else ''))
    @staticmethod
    def vault_height(desc):
        """Conservative mandatory-height analysis of the public locking policy.

        AND requires both branches; OR requires the least restrictive branch.
        A Taproot key path must use BIP341's standard NUMS point before script
        locks are considered mandatory. This is metadata for an Ownership badge,
        not a vault monitor or a proof of unknown private-key destruction.
        """
        def bound(ms):
            name=getattr(ms,'NAME','')
            if name=='after':
                height=ms.args[0].num
                return height if 0<height<500000000 else 0
            if isinstance(ms,Wrapper):return bound(ms.args[0])
            if name in ('and_v','and_b','and_n'):return max(bound(a) for a in ms.args)
            if name in ('or_b','or_c','or_d','or_i'):return min(bound(a) for a in ms.args)
            if name=='andor':return min(max(bound(ms.args[0]),bound(ms.args[1])),bound(ms.args[2]))
            if name=='thresh':
                k=ms.args[0].num;values=sorted(bound(a) for a in ms.args[1:])
                return values[k-1] if 0<k<=len(values) else 0
            return 0
        def leaves(tree):
            if isinstance(tree,tuple):return leaves(tree[0])+leaves(tree[1])
            return [bound(tree.miniscript)] if getattr(tree,'miniscript',None) is not None else [0]
        try:
            descriptor,_,_=parse_policy(desc.split('#')[0])
            if descriptor.taproot:
                nums='50929b74c1a04954b78b4b6035e97a5e078a5a0f28ec96d547bfee9ace803ac0'
                if descriptor.key.is_extended or descriptor.key.get_public_key().xonly().hex()!=nums:return None
                return min(leaves(descriptor.taptree.tree)) or None
            return bound(descriptor.miniscript) or None
        except (ValueError,TypeError,AttributeError,EmbitError):return None
    def merge(self,group):
        first=group[0];rows={}
        for replica in group:
            for row in replica['wallet']['raw']['descriptors']:
                key=self.descriptor_key(row['desc'])
                if key not in rows:rows[key]=deepcopy(row);continue
                saved=rows[key]
                if 'range' in row:
                    saved['range']=[min(saved['range'][0],row['range'][0]),max(saved['range'][1],row['range'][1])]
                    values=[n for n in (saved.get('next_index'),row.get('next_index')) if n is not None]
                    # An unknown cursor must retain the scanner's conservative coverage.
                    saved['next_index']=None if saved.get('next_index') is None or row.get('next_index') is None else max(values)
                if 'timestamp' in row:saved['timestamp']=min(saved.get('timestamp',row['timestamp']),row['timestamp'])
        wallet=clean_wallet({'wallet_name':first['wallet']['name'],'descriptors':list(rows.values())})
        wallet['watch_only']=all(r.get('private_keys_enabled') is False for r in group)
        wallet['node_sources']=[{'node_id':r['node']['id'],'hostname':r['node']['hostname'],'wallet_name':r['rpc_name']} for r in group]
        outputs={};addresses=set()
        for replica in group:
            for row in replica['history']['transactions']:
                if row.get('category') not in ('receive','generate','immature') or row.get('abandoned') or int(row.get('confirmations',0))<0:continue
                if Decimal(str(row.get('amount',0)))<=0:continue
                txid=row.get('txid');vout=row.get('vout')
                if not re.fullmatch('[0-9a-f]{64}',str(txid)) or type(vout) is not int or vout<0:raise ValueError('Invalid Core received output.')
                previous=outputs.get((txid,vout))
                if previous is None or int(row.get('confirmations',0))>int(previous.get('confirmations',0)):outputs[(txid,vout)]=row
                if row.get('address'):addresses.add(row['address'])
        if needs_history(wallet):wallet['public_history']={'exported_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'lastblock':first['history']['lastblock'],'outputs':[{'txid':t,'vout':v} for t,v in sorted(outputs)]}
        # Inspect only addresses Core actually received funds at, not its keypool.
        has_policy=any(self.vault_height(r['desc']) for r in wallet['raw']['descriptors'])
        evidence=[]
        if has_policy:
            for address,info in self.parallel(sorted(addresses),lambda a:self.address_info((group,a))):
                height=self.vault_height(str(info.get('desc','')))
                if height:
                    for (txid,vout),entry in outputs.items():
                        if entry.get('address')==address and int(entry.get('confirmations',0))>0:evidence.append({'txid':txid,'vout':vout,'address':address,'height':height})
        wallet['vault_evidence']=evidence;wallet['has_vault']=bool(evidence)
        return wallet
    def collect(self):
        nodes=[n for n in self.registry.snapshot()['nodes'] if n['connected']]
        if not nodes:raise ValueError('Connect at least one Wallaby Agent before loading node wallets.')
        for node in nodes:
            missing=REQUIRED-set(node.get('rpc_methods',[]))
            if not node.get('rpc_ready') or missing:raise ValueError(node['hostname']+': resolve node conflicts or enable required Agent RPC methods: '+', '.join(sorted(missing)))
            self.limits[node['id']]=threading.BoundedSemaphore(4)
        replicas=self.parallel([r for batch in self.parallel(nodes,self.discover_node) for r in batch],self.export)
        if not replicas:raise ValueError('The connected nodes have no loaded descriptor wallets.')
        groups=[]
        for replica in replicas:
            keys=self.keys(replica);matches=[g for g in groups if any(keys & self.keys(r) for r in g)]
            merged=[replica]
            for group in matches:groups.remove(group);merged=group+merged
            groups.append(merged)
        # Keep discovery/node order for a reproducible suggested name.
        order={id(r):i for i,r in enumerate(replicas)}
        for group in groups:group.sort(key=lambda r:order[id(r)])
        wallets=[self.merge(group) for group in groups]
        # Different descriptors with identical Core labels remain separate wallets.
        used=set()
        for wallet in wallets:
            base=wallet['name'];name=base
            if name.casefold() in used:name=base+' · '+wallet['node_sources'][0]['hostname']
            if name.casefold() in used:name+=' · '+wallet['id'][:8]
            wallet['name']=name;wallet['raw']['wallet_name']=name;used.add(name.casefold())
        self.check();self.progress(str(len(wallets))+' unique public wallets collected from all '+str(len(nodes))+' connected nodes. Public blockchain tracing follows.')
        return wallets
