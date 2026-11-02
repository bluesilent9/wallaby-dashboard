"""Dashboard-owned public archive tasks; local approval and physical evidence stay on Agent."""
import copy,json,os,secrets,re
from storage import storage_lock,data_directory
from agent_hub import digest,canonical,equivalent
from descriptor_store import DescriptorStore
from preferences import resource_id
from policy_accounts import signing_account

CAPABILITY='wallet_archive_queue_v1'

def wire(raw):
    return {'wallet_name':raw['wallet_name'],'descriptors':[{k:v for k,v in row.items() if v is not None} for row in raw['descriptors']]}
class ArchiveQueue:
    def __init__(self,hub):self.hub=hub;self.config=hub.config;self.path=data_directory(self.config)/'archive-queue.json'
    def read(self):
        if self.path.exists():return json.loads(self.path.read_text())
        return {'version':1,'queue_id':None,'revision':0,'requests':{}}
    def write(self,s):
        if s['queue_id'] is None:s['queue_id']=secrets.token_hex(32)
        raw=canonical(s)
        if len(raw)>16*1024*1024:raise ValueError('Archive request storage is full.')
        self.path.parent.mkdir(parents=True,exist_ok=True);p=self.path.with_suffix('.tmp');p.write_text(raw);p.chmod(0o600)
        with p.open('rb') as f:os.fsync(f.fileno())
        os.replace(p,self.path)
    def options(self,resource):
        wanted=next((w for w in DescriptorStore(self.config).read()['wallets'] if resource_id('descriptor',w['id'])==resource),None)
        if not wanted or wanted.get('extinct'):return []
        result=[]
        for node in self.hub.registry.snapshot()['nodes']:
            if not node.get('rpc_ready') or node.get('blockchain',{}).get('state')!='ready':continue
            inv=self.hub.inventories.get(node['id'],{})
            if not inv.get('complete'):continue
            for w in inv.get('wallets',[]):
                if not w.get('private_keys_enabled') or w.get('signing_only') or w.get('extinct') or w.get('scanning'):continue
                if not equivalent(w['raw'],wanted['raw']) and not signing_account(w,{'raw':wanted['raw']}):continue
                from policy_lineage import related
                policies=[wire(m['raw']) for m in inv['wallets'] if not m.get('private_keys_enabled') and not m.get('signing_only') and not m.get('extinct') and related(self.hub,wanted['raw'],m['raw'])]
                result.append({'id':digest({'node':node['id'],'wallet':w['raw']}),'node_id':node['id'],'hostname':node['hostname'],'wallet':w['name'],'compatible':CAPABILITY in self.hub.caps.get(node['id'],set()),'public_wallet':wire(w['raw']),'related_policies':policies})
        return result
    def create(self,user,resource,choices):
        options={r['id']:r for r in self.options(resource)}
        if not isinstance(choices,list) or not choices or len(choices)!=len(set(choices)) or any(i not in options for i in choices):raise ValueError('Select a connected persistent private-key wallet. Watch-only, extinct and disc-loaded accounts cannot be archived from Dashboard.')
        selected=[options[i] for i in choices]
        from wallet_renames import RenameQueue
        if any(o['state']=='active' and any(equivalent(o['raw'],r['public_wallet']) or signing_account({'raw':r['public_wallet'],'private_keys_enabled':True},{'raw':o['raw']}) for r in selected) for o in RenameQueue(self.hub).read()['operations'].values()):raise ValueError('Finish the wallet rename before creating additional archives.')
        for r in selected:
            if not r['compatible']:raise ValueError('Install Agent Build 23 with wallet_archive_queue_v1 before requesting archival.')
            info=self.hub.rpc(r['node_id'],'getwalletinfo',[],r['wallet']);raw=self.hub.rpc(r['node_id'],'listdescriptors',[False],r['wallet'])
            if info.get('private_keys_enabled') is not True or info.get('scanning') or info.get('walletname')!=r['wallet'] or {x['desc'].split('#')[0] for x in raw['descriptors']}!={x['desc'].split('#')[0] for x in r['public_wallet']['descriptors']}:raise ValueError('The selected persistent signing wallet changed. Refresh node inventory before archiving.')
        with storage_lock(self.config):
            s=self.read()
            for r in selected:
                if any(t['node_id']==r['node_id'] and t['state']=='active' and t['request']['kind']=='archive_wallet' and equivalent(t['request']['public_wallet'],r['public_wallet']) for t in s['requests'].values()):continue
                if sum(t['node_id']==r['node_id'] and t['state']=='active' for t in s['requests'].values())>=128:raise ValueError('This Agent’s archive queue is full. Cancel unneeded requests first.')
                p={'kind':'archive_wallet','version':1,'nonce':secrets.token_hex(32),'wallet':r['wallet'],'public_wallet':r['public_wallet'],'related_policies':r['related_policies'],'require_local_authorisation':True}
                ident=digest(p);p['request_id']=ident;s['requests'][ident]={'node_id':r['node_id'],'hostname':r['hostname'],'user_id':user,'resource_id':resource,'created_utc':self.hub.utc(),'state':'active','status':'Awaiting local Agent approval','request':p}
            s['revision']+=1;self.write(s)
        return self.snapshot()
    def snapshot(self):
        s=self.read();return {'requests':[{'id':i,'node_id':r['node_id'],'hostname':r['hostname'],'kind':r['request']['kind'],'name':r['request'].get('wallet') or r['request'].get('label','Old archive disc'),'state':r['state'],'status':r['status'],'created_utc':r['created_utc'],'can_cancel':r['state']=='active'} for i,r in s['requests'].items()]}
    def cancel(self,user,ident):
        with storage_lock(self.config):
            s=self.read();r=s['requests'].get(ident)
            if not r:raise ValueError('Unknown archive request.')
            if r['state']=='active':r.update(state='withdrawn',status='Withdrawn by Dashboard; this does not claim that a disc was destroyed.');s['revision']+=1;self.write(s)
        return self.snapshot()
    def proposal(self,node):
        with storage_lock(self.config):
            s=self.read()
            if s['queue_id'] is None:self.write(s)
            return {'kind':'wallet_archive_queue','version':1,'queue_id':s['queue_id'],'revision':s['revision'],'requests':[copy.deepcopy(r['request']) for r in s['requests'].values() if r['node_id']==node and r['state']=='active']}
    def accept(self,node,p):
        from wallet_policy_updates import verified
        body={k:v for k,v in p.items() if k!='signed_receipt'}
        if verified(self.hub,node,p.get('signed_receipt'))!=body:raise ValueError('Archive receipt signature differs from its result.')
        if not isinstance(p,dict) or p.get('version')!=1 or not isinstance(p.get('request_id'),str) or p.get('status') not in ('queued','disabled','complete','error'):raise ValueError('Invalid archive queue result.')
        with storage_lock(self.config):
            s=self.read();r=s['requests'].get(p['request_id'])
            if p.get('queue_id')!=s['queue_id'] or not r or r['node_id']!=node:raise ValueError('Archive result does not match this Agent and request.')
            if r['state']=='withdrawn':return
            if r['state']=='complete' and r.get('result')!=p:raise ValueError('Archive completion differs from its original signed receipt.')
            if p['status']=='complete':
                if r['request']['kind']=='archive_wallet' and (p.get('readback_verified') is not True or not re.fullmatch('[0-9a-f]{64}',str(p.get('image_sha256')))):raise ValueError('Archive completion requires verified disc read-back evidence.')
                if r['request']['kind']=='destroy_disc' and p.get('destruction_confirmed_locally') is not True:raise ValueError('Only the Agent can confirm physical disc destruction.')
                r.update(state='complete',status='Archive verified on Agent' if r['request']['kind']=='archive_wallet' else 'Destruction confirmed locally on Agent',result=copy.deepcopy(p));s['revision']+=1
            else:r['status']={'queued':'Queued on Agent — awaiting completion','disabled':'Disabled locally on Agent','error':'Agent reported an archive error; review locally'}[p['status']];r['result']=copy.deepcopy(p)
            self.write(s)
        if p['status']=='complete' and r['request']['kind']=='archive_wallet':self.replacements(node,p,p['signed_receipt'])

    def followup(self,node,envelope):
        from wallet_policy_updates import verified
        p=verified(self.hub,node,envelope)
        self.inventory(node,p,envelope)
        self.replacements(node,p,envelope)
    def inventory(self,node,p,envelope):
        if 'members' not in p:return
        if p.get('version')!=1 or p.get('event') not in ('archive_verified','replacement_verified') or p.get('readback_verified') is not True or not re.fullmatch('[0-9a-f]{64}',str(p.get('archive_id'))) or not re.fullmatch('[0-9a-f]{64}',str(p.get('image_sha256'))) or not isinstance(p['members'],list) or not 1<=len(p['members'])<=128:raise ValueError('Invalid verified public archive inventory.')
        for member in p['members']:
            if not re.fullmatch('[0-9a-f]{64}',str(member.get('member_id'))):raise ValueError('Invalid archive member identity.')
            __import__('descriptor_store').clean_wallet(member['public_wallet'])
            for policy in member.get('related_policies',[]):
                check=copy.deepcopy(policy)
                for row in check['descriptors']:
                    if '*' in row['desc'] and 'range' not in row:row['range']=[0,999]
                __import__('descriptor_store').clean_wallet(check)
        with self.hub.edit() as state:state['nodes'].setdefault(node,{}).setdefault('archive_inventory',{})[p['archive_id']]={'message':copy.deepcopy(p),'envelope':copy.deepcopy(envelope)}
    def replacements(self,node,p,envelope):
        if p.get('readback_verified') is not True or not re.fullmatch('[0-9a-f]{64}',str(p.get('image_sha256'))):raise ValueError('A replacement requires verified archive read-back.')
        if not isinstance(p.get('replacements',[]),list) or len(p.get('replacements',[]))>128:raise ValueError('Invalid replacement coverage.')
        hostname=next((n['hostname'] for n in self.hub.registry.snapshot()['nodes'] if n['id']==node),node)
        with storage_lock(self.config):
            s=self.read();changed=False
            for old in p.get('replacements',[]):
                if not re.fullmatch('[0-9a-f]{64}',str(old.get('archive_id'))) or old.get('readback_verified') is not True or old.get('old_policies_retained') is not True or old.get('image_sha256')!=p['image_sha256'] or not old.get('members'):raise ValueError('Replacement does not prove complete old-disc coverage.')
                if any(r['node_id']==node and r['request'].get('archive_id')==old['archive_id'] for r in s['requests'].values()):continue
                task={'kind':'destroy_disc','version':1,'nonce':secrets.token_hex(32),'archive_id':old['archive_id'],'label':'Replaced archive '+old['archive_id'][:12],'replacement_image_sha256':p['image_sha256'],'replacement_receipt':envelope}
                ident=digest(task);task['request_id']=ident
                if sum(r['state']=='active' and r['node_id']==node for r in s['requests'].values())>=128:raise ValueError('Archive queue is full. Withdraw completed work before adding another task.')
                s['requests'][ident]={'node_id':node,'hostname':hostname,'user_id':None,'resource_id':None,'created_utc':self.hub.utc(),'state':'active','status':'Awaiting physical destruction confirmation on Agent','request':task};changed=True
            if changed:s['revision']+=1;self.write(s)

def validate(s):
    if not isinstance(s,dict) or set(s)!={'version','queue_id','revision','requests'} or s['version']!=1 or not re.fullmatch('[0-9a-f]{64}',str(s['queue_id'])) or type(s['revision']) is not int or s['revision']<0 or not isinstance(s['requests'],dict) or len(s['requests'])>100000:raise ValueError('Invalid archive queue profile data.')
    from descriptor_store import clean_wallet
    counts={}
    for ident,row in s['requests'].items():
        p=dict(row['request']);key=p.pop('request_id',None)
        if ident!=key or digest(p)!=key or row['state'] not in ('active','withdrawn','complete') or p.get('kind') not in ('archive_wallet','destroy_disc'):raise ValueError('Invalid archived request identity.')
        if p['kind']=='archive_wallet':
            clean_wallet(p['public_wallet'])
            if p.get('require_local_authorisation') is not True:raise ValueError('Archive request requires local authorisation.')
            for raw in p.get('related_policies',[]):clean_wallet(raw)
        elif not re.fullmatch('[0-9a-f]{64}',str(p.get('archive_id'))) or not re.fullmatch('[0-9a-f]{64}',str(p.get('replacement_image_sha256'))):raise ValueError('Invalid old-disc destruction request.')
        if row['state']=='active':counts[row['node_id']]=counts.get(row['node_id'],0)+1
    if any(n>128 for n in counts.values()):raise ValueError('Too many active archive requests for one Agent.')
    return s
