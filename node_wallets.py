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
from labelled_wallets import expand

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
        replica={'node':node,'rpc_name':name,'wallet':wallet,'history':history,'private_keys_enabled':info.get('private_keys_enabled')}
        def address_info(address):
            value=self.rpc(node,'getaddressinfo',[address],wallet=name)
            if not isinstance(value,dict) or value.get('address')!=address or not (value.get('ismine') or value.get('iswatchonly')):raise ValueError('Core could not confirm its historical watched address '+address)
            return value
        return expand(replica,address_info)
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
        historical=first['wallet'].get('extinct',False)
        if any(r['wallet'].get('extinct',False)!=historical for r in group):raise ValueError('A historical address is also claimed by a live node wallet. Supply each wallet only once.')
        labels={}
        if historical:
            for replica in group:
                if replica['wallet']['name'].casefold()!=first['wallet']['name'].casefold():raise ValueError('Node replicas disagree on historical wallet labels. Correct the labels in Core and retry.')
                for address,metadata in replica['wallet']['address_labels'].items():
                    if address in labels and labels[address]['lost_utc']!=metadata['lost_utc']:raise ValueError('Node replicas disagree on a historical address loss timestamp. Correct the labels in Core and retry.')
                    labels.setdefault(address,deepcopy(metadata))
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
        if historical:wallet.update(extinct=True,address_labels=labels)
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
        wallet['public_history']={'exported_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'lastblock':first['history']['lastblock'],'outputs':[{'txid':t,'vout':v} for t,v in sorted(outputs)]}
        wallet['node_history']=list({(r['txid'],r.get('vout'),r.get('category')):deepcopy(r) for replica in group for r in replica['history']['transactions'] if not r.get('abandoned') and int(r.get('confirmations',0))>=0}.values())
        # Inspect only addresses Core actually received funds at, not its keypool.
        has_policy=any(self.vault_height(r['desc']) for r in wallet['raw']['descriptors'])
        evidence=[]
        if has_policy:
            for address,info in self.parallel(sorted(addresses),lambda a:self.address_info((group,a))):
                height=self.vault_height(str(info.get('desc','')))
                if height:
                    for (txid,vout),entry in outputs.items():
                        if entry.get('address')==address and int(entry.get('confirmations',0))>0:evidence.append({'txid':txid,'vout':vout,'address':address,'height':height})
        wallet['receiving_address']=None
        # Keep the Address Book's most recent receiving address; distinguish
        # change using Core metadata, including publicly underivable policies.
        candidates=sorted(outputs.values(),key=lambda r:(r.get('blocktime',r.get('time',0)),r['txid'],r['vout']),reverse=True)
        checked=set()
        for row in ([] if historical else candidates):
            addr=row.get('address')
            if not addr or addr in checked:continue
            checked.add(addr);_,info=self.address_info((group,addr))
            if not info.get('ischange',False):wallet['receiving_address']=addr;break
        wallet['vault_evidence']=evidence;wallet['has_vault']=bool(evidence)
        return wallet
    def collect(self):
        nodes=[n for n in self.registry.snapshot()['nodes'] if n['connected']]
        if not nodes:raise ValueError('Connect at least one Wallaby Agent before loading node wallets.')
        for node in nodes:
            missing=REQUIRED-set(node.get('rpc_methods',[]))
            if not node.get('rpc_ready') or missing:raise ValueError(node['hostname']+': resolve node conflicts or enable required Agent RPC methods: '+', '.join(sorted(missing)))
            self.limits[node['id']]=threading.BoundedSemaphore(4)
        replicas=[r for batch in self.parallel([r for batch in self.parallel(nodes,self.discover_node) for r in batch],self.export) for r in batch]
        # Retirement preserves the original local wallet. Count its history through
        # the node's verified labelled archive only after all received addresses
        # are present there; unrelated historical wallets never hide live accounts.
        retired=[r for r in self.registry.hub.read()['requests'].values() if r.get('kind')=='wallet_retire' and r.get('state')=='complete']
        kept=[]
        for replica in replicas:
            hidden=False
            if not replica['wallet'].get('extinct'):
                own=self.keys(replica)
                for request in retired:
                    public=request['proposal']['public_wallet'];wanted={self.descriptor_key(r['desc']) for r in public['descriptors']}
                    if not own or own!=wanted:continue
                    archives=[r for r in replicas if r['wallet'].get('extinct') and r['wallet']['name']==request['proposal']['archive_label']]
                    archived={address for r in archives for address in r['wallet'].get('address_labels',{})}
                    received={r.get('address') for r in replica['history']['transactions'] if r.get('category') in ('receive','generate','immature') and r.get('address')}
                    if archives and received and received<=archived:hidden=True;break
            if not hidden:kept.append(replica)
        replicas=kept
        # Independent private key accounts for a shared policy are signing roles,
        # not duplicate balance wallets. Preserve any account with its own funds/history.
        from policy_accounts import signing_account
        flat=[{'raw':r['wallet']['raw'],'private_keys_enabled':r['private_keys_enabled']} for r in replicas]
        replicas=[r for r,account in zip(replicas,flat) if r['history'].get('transactions') or not any(signing_account(account,m) for m in flat if m is not account)]
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
        self.check();self.progress(str(len(wallets))+' unique public wallets collected from all '+str(len(nodes))+' connected nodes. Connected-node wallet history reconstruction follows.')
        return wallets
