"""Durable public rename coordination. All filesystem changes are approved on Agent."""
import copy,json,os,re,secrets
from storage import data_directory,storage_lock
from agent_hub import digest,canonical,equivalent
from archive_queue import wire
from descriptor_store import DescriptorStore,clean_wallet
from preferences import resource_id
from policy_accounts import signing_account
CAPABILITY='wallet_rename_v1'
HEX=re.compile('[0-9a-f]{64}')

def name(value):
    if not isinstance(value,str) or not 1<=len(value)<=100 or len(value.encode())>200 or value.strip()!=value or value.casefold() in ('.','..','extinct','wallaby extinct','bitaroo') or '/' in value or chr(92) in value or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('Use 1–100 characters without paths, surrounding spaces or reserved wallet names.')
    return value

def matches(raw,w):
    return equivalent(raw,w['raw']) or signing_account(w,{'raw':raw})

class RenameQueue:
    def __init__(self,hub):self.hub=hub;self.config=hub.config;self.path=data_directory(self.config)/'wallet-renames.json'
    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'version':1,'queue_id':secrets.token_hex(32),'revision':0,'operations':{}}
    def write(self,s):
        raw=canonical(s)
        if len(raw)>16*1024*1024:raise ValueError('Rename storage is full.')
        self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_suffix('.tmp');tmp.write_text(raw);tmp.chmod(0o600)
        with tmp.open('rb') as f:os.fsync(f.fileno())
        os.replace(tmp,self.path)
    def wallet(self,resource):
        w=next((w for w in DescriptorStore(self.config).read()['wallets'] if resource_id('descriptor',w['id'])==resource),None)
        if not w or w.get('extinct'):raise ValueError('Only active descriptor wallets can be renamed. Exchanges and extinct address groups cannot be renamed here.')
        return w
    def holders(self,raw):
        from wallet_retirement import local_targets
        nodes={n['id']:n for n in self.hub.registry._read()['nodes']};found={}
        saved=self.hub.read()['nodes']
        for node in sorted(set(saved)|set(self.hub.inventories)):
            entry=saved.get(node,{})
            known=list(entry.get('known_wallets',{}).values());inv=self.hub.inventories.get(node,{})
            # Complete live inventory replaces stale filesystem names.
            wallets=inv.get('wallets',[]) if inv.get('complete') else known
            for w in wallets:
                if w.get('extinct') or w.get('signing_only') or not matches(raw,w):continue
                # Do not rename a private account used by an unrelated policy.
                if not equivalent(raw,w['raw']) and inv.get('complete'):
                    if w['name'] not in {r['wallet'] for r in local_targets(self.hub,raw,node)}:continue
                key=digest({'node':node,'wallet':w['name'],'raw':sorted(r['desc'].split('#')[0] for r in w['raw']['descriptors'])})
                found[key]={'node_id':node,'hostname':nodes.get(node,{}).get('hostname',node),'wallet':w['name'],'raw':wire(w['raw']),'storage':'node','private':w.get('private_keys_enabled') is True}
        return found
    def archives(self,raw):
        from archive_queue import ArchiveQueue
        found={};archive_rows=ArchiveQueue(self.hub).read()['requests'].values()
        destroyed={r['request']['archive_id'] for r in archive_rows if r['request']['kind']=='destroy_disc' and r['state']=='complete' and r.get('result',{}).get('destruction_confirmed_locally') is True}
        for row in archive_rows:
            p=row['request'];r=row.get('result',{})
            if p['kind']!='archive_wallet' or row['state']!='complete' or not matches(raw,{'raw':p['public_wallet'],'private_keys_enabled':True}):continue
            archive=r.get('archive_id')
            if HEX.fullmatch(str(archive)):found[archive+digest(p['public_wallet'])]={'archive_id':archive,'raw':p['public_wallet'],'storage':'disc','node_id':None,'hostname':'Any Agent — load this archive disc','wallet':p['wallet']}
        # Public disc-route evidence is retained even after an optical reader disconnects.
        for node,entry in self.hub.read()['nodes'].items():
            for w in entry.get('known_archives',{}).values():
                if matches(raw,{'raw':w['raw'],'private_keys_enabled':True}):found[w['archive_id']+digest(w['raw'])]={'archive_id':w['archive_id'],'raw':wire(w['raw']),'storage':'disc','node_id':None,'hostname':'Any Agent — load this archive disc','wallet':w['name']}
            for record in entry.get('archive_inventory',{}).values():
                message=record['message']
                for m in message['members']:
                    if matches(raw,{'raw':m['public_wallet'],'private_keys_enabled':True}):found[message['archive_id']+digest(m['public_wallet'])]={'archive_id':message['archive_id'],'raw':wire(m['public_wallet']),'storage':'disc','node_id':None,'hostname':'Any Agent — load this archive disc','wallet':m['name']}
        # Creation receipts can predate package-ID reporting. Keep that role pending,
        # and bind the real package ID only after its exact account is read by Agent.
        from vendor.embit.descriptor.checksum import checksum
        for setup in self.hub.wallet_builder.read()['sessions'].values():
            for role in setup['proposal']['cosigners']:
                contribution=setup.get('contributions',{}).get(role['id'],{}).get('message',{})
                proofs=[a['message'] for a in setup.get('acks',{}).values() if a['message'].get('status')=='backup_verified' and a['message'].get('cosigner_file')==__import__('hashlib').sha256(role['id'].encode()).hexdigest()]
                if role.get('storage')!='disc' or not proofs or not contribution:continue
                rows=[]
                for internal,field in ((False,'receive_key'),(True,'change_key')):
                    body='wpkh('+contribution[field]+')';rows.append({'desc':body+'#'+checksum(body),'internal':internal,'active':True,'range':[0,999],'next_index':0,'timestamp':1})
                account={'wallet_name':role['name'],'descriptors':rows}
                if not matches(raw,{'raw':account,'private_keys_enabled':True}):continue
                if any(equivalent(v['raw'],account) for v in found.values()):continue
                found['backup:'+digest({'session':setup['proposal']['session_id'],'role':role['id']})]={'archive_id':None,'raw':account,'storage':'disc','node_id':None,'hostname':'Any Agent — read the original signer archive','wallet':role['name']}
        return {key:value for key,value in found.items() if value.get('archive_id') not in destroyed}
    def add_targets(self,op):
        candidates={**self.holders(op['raw']),**self.archives(op['raw'])}
        for key,t in candidates.items():
            # A freshly verified replacement is the rename's output, not another old disc.
            replacement_ids={old['result'].get('archive_id') for old in op['targets'].values() if old['storage']=='disc' and old.get('result') and old['result'].get('readback_verified') is True and old['state']=='complete'}
            if t['storage']=='disc' and (t['wallet']==op['new_name'] or t.get('archive_id') in replacement_ids):continue
            if key in op['targets'] or any(t['storage']=='disc' and old['storage']=='disc' and equivalent(t['raw'],old['raw']) and (t.get('archive_id') is None or old.get('archive_id') is None or t['archive_id']==old['archive_id']) for old in op['targets'].values()) or any(t['storage']=='node' and t['node_id']==old['node_id'] and t['storage']==old['storage'] and equivalent(t['raw'],old['raw']) and t['wallet'] in (old['wallet'],(old.get('result') or {}).get('new_wallet')) for old in op['targets'].values()):continue
            same=[x for x in candidates.values() if x['node_id']==t['node_id'] and x['storage']=='node']
            suffix='' if len(same)<=1 or equivalent(t['raw'],op['raw']) else ' · '+t['wallet']
            target=name(op['new_name']+suffix)
            if t['storage']=='node' and not t.get('private') and not target.endswith('_watch'):target=name(target+'_watch')
            used={x['new_name'] for x in op['targets'].values() if x['node_id']==t['node_id'] and x['storage']==t['storage']}
            if t['storage']=='node' and t['wallet']==target:continue
            if target in used:target=name(op['new_name']+' · '+t['wallet']+('_watch' if not t.get('private') and not t['wallet'].endswith('_watch') else ''))
            t.update(new_name=target,status='Awaiting local Agent approval' if t['storage']=='node' else 'Load the original disc on any Agent, then select that Agent',state='pending',request=None,result=None)
            op['targets'][key]=t
    def names(self,user,resource):
        from preferences import FamilyStore
        from wallet_names import dashboard_name
        w=self.wallet(resource)
        owner=next((r for r in FamilyStore(self.config).snapshot()['ownership'] if r['id']==resource),None)
        if not owner or owner.get('owner_id')!=user:raise ValueError('Select this wallet’s owner profile before requesting a rename.')
        holders=list(self.holders(w['raw']).values())
        # Prefer a persistent private-key copy, then the imported source. Strip
        # the routing suffix only from a proven watch copy, not arbitrary text.
        private=sorted((t for t in holders if t['private']),key=lambda t:(t['wallet'],t['node_id']))
        current=private[0]['wallet'] if private else w['name']
        if not private and holders and all(not t['private'] for t in holders) and current.endswith('_watch'):current=current[:-6]
        return {'filename':current,'dashboard_name':dashboard_name(owner)}
    def rename(self,user,resource,filename,label):
        from preferences import FamilyStore
        current=self.names(user,resource)
        if filename is not None:filename=name(filename)
        # Validate both fields before queueing any Agent work.
        if not isinstance(label,str) or not label.strip() or len(label)>1000 or any(ord(c)<32 or ord(c)==127 for c in label):raise ValueError('Enter a Dashboard name of 1–1,000 characters without control characters.')
        from wallet_names import dashboard_name
        family=FamilyStore(self.config).snapshot();label=label.strip()
        if label.casefold()=='bitaroo' or any(r['id']!=resource and dashboard_name(r).casefold()==label.casefold() for r in family['ownership']):raise ValueError('Dashboard wallet names must be unique; Bitaroo is reserved.')
        queued=filename is not None and filename!=current['filename']
        if queued:self.create(user,resource,filename)
        if label!=current['dashboard_name']:FamilyStore(self.config).rename_dashboard(user,resource,label)
        return dict(self.snapshot(),filename_queued=queued,dashboard_name=label)
    def create(self,user,resource,new):
        new=name(new);w=self.wallet(resource)
        if any(r.get('kind')=='wallet_transfer' and r.get('proposal') and r.get('state') in ('pending','recovery_required') and equivalent(r['public_wallet'],w['raw']) for r in self.hub.read()['requests'].values()):raise ValueError('Finish or reconcile this wallet’s in-progress direct sync before renaming it.')
        # Frozen builders/signing requests must not lose their local wallet routes.
        from builder_workspace import BuilderWorkspace
        if BuilderWorkspace(self.config).read()['workspaces']:raise ValueError('Finish or cancel active wallet and transaction builds before renaming wallets.')
        from archive_queue import ArchiveQueue
        if any(r['state']=='active' and r['request']['kind']=='archive_wallet' and matches(w['raw'],{'raw':r['request']['public_wallet'],'private_keys_enabled':True}) for r in ArchiveQueue(self.hub).read()['requests'].values()):raise ValueError('Finish or cancel this wallet’s pending archive requests before renaming it.')
        from preferences import FamilyStore
        owner=next((r for r in FamilyStore(self.config).snapshot()['ownership'] if r['id']==resource),None)
        if not owner or owner.get('owner_id')!=user:raise ValueError('Select this wallet’s owner profile before requesting a rename.')
        if any(other['id']!=w['id'] and other['name'].casefold()==new.casefold() for other in DescriptorStore(self.config).read()['wallets']):raise ValueError('Another wallet already uses that name.')
        if new==self.names(user,resource)['filename']:raise ValueError('Choose a different wallet filename.')
        with storage_lock(self.config):
            s=self.read()
            if any(o['state']=='active' and equivalent(o['raw'],w['raw']) for o in s['operations'].values()):raise ValueError('This wallet already has an active rename.')
            op={'id':secrets.token_hex(32),'user_id':user,'resource_id':resource,'old_name':w['name'],'new_name':new,'raw':wire(w['raw']),'created_utc':self.hub.utc(),'state':'active','locked':False,'targets':{},'sequence':max((v.get('sequence',0) for v in s['operations'].values()),default=0)+1}
            self.add_targets(op)
            if not op['targets']:raise ValueError('Wait for node wallet discovery before requesting a rename.')
            s['operations'][op['id']]=op;s['revision']+=1;self.write(s)
        return self.snapshot()
    def task(self,op,t):
        p={'kind':'wallet_rename','version':1,'nonce':secrets.token_hex(32),'rename_id':op['id'],'chain':'main','wallet':t['wallet'],'new_name':t['new_name'],'public_wallet':t['raw'],'storage':t['storage']}
        if t['storage']=='disc':p['archive_id']=t['archive_id']
        p['request_id']=digest(p);return p
    def route_archive(self,user,ident,target,node):
        with storage_lock(self.config):
            s=self.read();o=s['operations'].get(ident)
            if not o or o['state']!='active':raise ValueError('Rename is not active.')
            if o['user_id']!=user:raise ValueError('Select the profile that started this rename.')
            t=o['targets'].get(target)
            if not t or t['storage']!='disc' or t['request']:raise ValueError('This archive request is already routed.')
            if CAPABILITY not in self.hub.caps.get(node,set()):raise ValueError('Connect an Agent supporting wallet renaming.')
            inv=self.hub.inventories.get(node,{})
            routes=[w for w in inv.get('wallets',[]) if w.get('signing_only') and w.get('private_keys_enabled') and equivalent(w['raw'],t['raw'])]
            routes=[w for w in routes if w.get('archive_id') and (not t.get('archive_id') or w['archive_id']==t['archive_id'])]
            if not routes:raise ValueError('Load the matching archive disc on this Agent and refresh its wallet discovery first.')
            t.update(archive_id=routes[0]['archive_id'],node_id=node,wallet=routes[0]['name'],hostname=next(n['hostname'] for n in self.hub.registry._read()['nodes'] if n['id']==node));t['request']=self.task(o,t);s['revision']+=1;self.write(s)
        return self.snapshot()
    def latest(self,op,operations):
        # A later rename is authoritative: never move a late old disc back to an
        # intermediate name. Sequence avoids clock/second-resolution ties.
        order=lambda o:(o.get('sequence',0),o['created_utc'],o['id'])
        return not any((other['state']!='withdrawn' or other.get('locked')) and other['id']!=op['id'] and equivalent(other['raw'],op['raw']) and order(other)>order(op) for other in operations.values())
    def auto_route(self,op,node,nodes):
        if CAPABILITY not in self.hub.caps.get(node,set()):return False
        n=next((n for n in nodes if n['id']==node and n.get('rpc_ready')),None)
        inv=self.hub.inventories.get(node,{})
        if not n or not inv.get('complete'):return False
        changed=False
        for t in op['targets'].values():
            if t['storage']!='disc' or t['request'] or t['state']=='complete':continue
            # Match the actually loaded physical package and public account,
            # not its mutable display name or an old cached disc observation.
            routes=[w for w in inv.get('wallets',[]) if w.get('signing_only') and w.get('private_keys_enabled') and w.get('archive_id') and (not t.get('archive_id') or w['archive_id']==t['archive_id']) and equivalent(w['raw'],t['raw'])]
            if len(routes)!=1:continue
            route=routes[0];t.update(archive_id=route['archive_id'],node_id=node,wallet=route['name'],hostname=n['hostname']);t['request']=self.task(op,t);changed=True
        return changed
    def proposal(self,node):
        # Snapshot health before taking the durable storage lock: node warnings
        # can themselves read the rename queue.
        nodes=self.hub.registry.presence()['nodes'] if self.read()['operations'] else []
        with storage_lock(self.config):
            s=self.read();requests=[];changed=False
            for o in s['operations'].values():
                if o['state']=='withdrawn' or not self.latest(o,s['operations']):continue
                before=len(o['targets']);self.add_targets(o)
                if before!=len(o['targets']):
                    changed=True
                    if o['state']=='complete':o['state']='active'
                if o['state']!='active':continue
                changed|=self.auto_route(o,node,nodes)
                for t in o['targets'].values():
                    if t['node_id']!=node or t['state']=='complete':continue
                    if not t['request']:
                        t['request']=self.task(o,t);changed=True
                    requests.append(copy.deepcopy(t['request']))
            if len(requests)>128:raise ValueError('Finish outstanding renames before requesting more on this Agent.')
            if changed:s['revision']+=1
            if changed or not self.path.exists():self.write(s)
            return {'kind':'wallet_rename_queue','version':1,'queue_id':s['queue_id'],'revision':s['revision'],'requests':requests}
    def cancel(self,user,ident):
        with storage_lock(self.config):
            s=self.read();o=s['operations'].get(ident)
            if not o:raise ValueError('Unknown rename.')
            if o['user_id']!=user:raise ValueError('Select the profile that started this rename.')
            if o['state']!='active':raise ValueError('Only an unfinished rename can be cancelled.')
            o['state']='withdrawn';s['revision']+=1;self.write(s)
        return self.snapshot()
    def accept(self,node,envelope):
        from wallet_policy_updates import verified
        p=verified(self.hub,node,envelope)
        with storage_lock(self.config):
            s=self.read();o=s['operations'].get(p.get('rename_id'))
            if not o or p.get('version')!=1 or p.get('queue_id')!=s['queue_id'] or p.get('status') not in ('complete','archive_required','denied','error'):raise ValueError('Rename result does not match this queue.')
            t=next((t for t in o['targets'].values() if t['node_id']==node and t.get('request',{} ) and t['request']['request_id']==p.get('request_id')),None)
            if not t or p.get('wallet')!=t['request']['wallet'] or p.get('storage')!=t['storage']:raise ValueError('Rename result does not match this Agent and wallet.')
            success=p['status'] in ('complete','archive_required')
            if t['state']=='complete':
                if t.get('result')!=p:raise ValueError('A completed rename receipt cannot be replaced by a contradictory result.')
                return
            if success:
                if p.get('identity_verified') is not True:raise ValueError('Rename lacks locally verified identity evidence.')
                if t['storage']=='node' and p.get('new_wallet')!=t['new_name']:raise ValueError('Unexpected renamed node wallet.')
                if t['storage']=='disc' and p['status']=='complete' and p.get('archive_skipped') is not True and (p.get('readback_verified') is not True or not HEX.fullmatch(str(p.get('image_sha256')))):raise ValueError('Archive rename needs verified replacement disc evidence or explicit local duplicate approval.')
                # Late success after withdrawal wins. Never claim an applied rename was undone.
                o['locked']=True
                if o['state']!='withdrawn':o['state']='active'
                t['state']='complete' if p['status']=='complete' else 'pending'
            elif p.get('attempted') is True or p.get('outcome_uncertain') is True:o['locked']=True
            elif t['state']=='complete':return
            t['result']=copy.deepcopy(p);t['status']={'complete':'Renamed and verified on Agent','archive_required':'Renamed — replacement archive awaiting verified read-back','denied':'Denied locally on Agent','error':'Agent reports failure or an uncertain outcome — inspect locally'}[p['status']]
            if o['state']!='withdrawn' and all(t['state']=='complete' for t in o['targets'].values()):o['state']='complete'
            s['revision']+=1;self.write(s)
        if p['status']=='complete' and t['storage']=='disc' and p.get('replacements'):
            __import__('archive_queue').ArchiveQueue(self.hub).replacements(node,p,envelope)
        if success:
            self.hub.inventory_times[node]=0
    def snapshot(self):
        nodes={n['id']:n for n in self.hub.registry.presence()['nodes']}
        s=self.read();rows=[]
        for o in s['operations'].values():
            rows.append({k:o[k] for k in ('id','resource_id','old_name','new_name','created_utc','state')}|{'can_cancel':o['state']=='active','targets':[{'id':i,'node_id':t['node_id'],'hostname':nodes.get(t['node_id'],{}).get('hostname',t['hostname']),'colour':'green' if t['state']=='complete' else 'red' if t['node_id'] and not nodes.get(t['node_id'],{}).get('rpc_ready') else 'amber','wallet':t['wallet'],'new_name':t['new_name'],'storage':t['storage'],'state':t['state'],'status':t['status'] if t['state']=='complete' or t.get('result') else ('Agent offline' if t['node_id'] and not nodes.get(t['node_id'],{}).get('rpc_ready') else t['status']),'needs_route':t['storage']=='disc' and t['request'] is None} for i,t in o['targets'].items() if not t.get('node_deleted')]})
        return {'operations':rows,'agents':[{'id':n['id'],'hostname':n['hostname']} for n in nodes.values() if n.get('rpc_ready') and CAPABILITY in self.hub.caps.get(n['id'],set())]}

def validate(s):
    if not isinstance(s,dict) or set(s)!={'version','queue_id','revision','operations'} or s['version']!=1 or not HEX.fullmatch(str(s['queue_id'])) or type(s['revision']) is not int or s['revision']<0 or not isinstance(s['operations'],dict) or len(s['operations'])>10000:raise ValueError('Invalid rename profile data.')
    for ident,o in s['operations'].items():
        if not HEX.fullmatch(ident) or o['id']!=ident or o['state'] not in ('active','withdrawn','complete') or type(o['locked']) is not bool:raise ValueError('Invalid rename operation.')
        name(o['new_name']);clean_wallet(o['raw'])
        if 'sequence' in o and (type(o['sequence']) is not int or not 1<=o['sequence']<2**63):raise ValueError('Invalid rename ordering.')
        if not isinstance(o['targets'],dict) or len(o['targets'])>10000:raise ValueError('Invalid rename targets.')
        for t in o['targets'].values():
            clean_wallet(t['raw']);name(t['new_name'])
            if t['storage'] not in ('node','disc') or t['state'] not in ('pending','complete'):raise ValueError('Invalid rename target.')
            if t['storage']=='disc' and t['archive_id'] is not None and not HEX.fullmatch(str(t['archive_id'])):raise ValueError('Invalid archive identity.')
            if t['request']:
                p=dict(t['request']);key=p.pop('request_id')
                if digest(p)!=key or p['rename_id']!=ident or p['storage']!=t['storage'] or p['new_name']!=t['new_name']:raise ValueError('Invalid bound rename task.')
                clean_wallet(p['public_wallet'])
    return s
