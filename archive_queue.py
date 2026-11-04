"""Dashboard-owned public archive tasks; local approval and physical evidence stay on Agent."""
import copy,json,os,secrets,re
from storage import storage_lock,data_directory
from agent_hub import digest,canonical,equivalent
from descriptor_store import DescriptorStore
from preferences import resource_id
from policy_accounts import signing_account

CAPABILITY='wallet_archive_queue_v1'
CANCEL_CAPABILITY='wallet_archive_cancel_v1'

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
        for node in self.hub.registry.presence()['nodes']:
            # An archive is a locally approved backup, not a spend. Chain
            # readiness may lapse during a protected wallet/disc ceremony.
            # create() still checks the actual wallet through fresh public RPC.
            if not node.get('connected') or not node.get('rpc_ready'):continue
            inv=self.hub.inventories.get(node['id'],{})
            if not (inv.get('complete') or inv.get('loaded_complete')):continue
            for w in inv.get('wallets',[]):
                if not w.get('private_keys_enabled') or w.get('signing_only') or w.get('extinct') or w.get('scanning'):continue
                if not equivalent(w['raw'],wanted['raw']) and not signing_account(w,{'raw':wanted['raw']}):continue
                from policy_lineage import related
                policies=[wire(m['raw']) for m in inv['wallets'] if not m.get('private_keys_enabled') and not m.get('signing_only') and not m.get('extinct') and related(self.hub,wanted['raw'],m['raw'])]
                result.append({'id':digest({'node':node['id'],'wallet':w['raw']}),'node_id':node['id'],'hostname':node['hostname'],'wallet':w['name'],'compatible':CAPABILITY in self.hub.caps.get(node['id'],set()),'public_wallet':wire(w['raw']),'related_policies':policies})
        return result
    def destinations(self,resource,options):
        """Explain connected destinations; availability never authorises a backup."""
        wanted=next((w for w in DescriptorStore(self.config).read()['wallets'] if resource_id('descriptor',w['id'])==resource),None)
        result=[]
        for node in self.hub.registry.presence()['nodes']:
            if not node.get('connected'):continue
            sources=[r for r in options if r['node_id']==node['id']]
            available=any(r['compatible'] for r in sources)
            reason='Available for local archive approval'
            inv=self.hub.inventories.get(node['id'],{})
            if not available:
                if sources:reason='Agent update required for archive requests'
                elif not node.get('rpc_ready'):reason='Waiting for the Agent’s Core connection'
                elif not (inv.get('complete') or inv.get('loaded_complete')):reason='Waiting for wallet inventory'
                else:
                    matches=[w for w in inv.get('wallets',[]) if wanted and (equivalent(w['raw'],wanted['raw']) or signing_account(w,{'raw':wanted['raw']}))]
                    if matches and all(not w.get('private_keys_enabled') for w in matches):reason='Watch-only copy — a private signing wallet is required'
                    elif any(w.get('signing_only') for w in matches):reason='Disc-loaded account — use local Disc Operations'
                    elif any(w.get('scanning') for w in matches):reason='Wallet rescan in progress'
                    else:reason='No persistent private signing copy of this wallet'
            result.append({'node_id':node['id'],'hostname':node['hostname'],'available':available,'reason':reason})
        return sorted(result,key=lambda r:(r['hostname'].casefold(),r['node_id']))
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
        s=self.read();rows=[];nodes={n['id']:n for n in self.hub.registry.presence()['nodes']}
        for ident,r in s['requests'].items():
            kind=r['request']['kind']
            if r.get('node_deleted'):continue
            if r['state']!='active' and not (kind=='archive_wallet' and r['state']=='complete' and not r.get('cancel_requested')):continue
            status=r['status'];colour='green' if r['state']=='complete' else 'amber'
            if r['node_id'] is None:status='Choose an Agent with this archive disc loaded';colour='amber'
            elif r['state']=='active' and not nodes.get(r['node_id'],{}).get('connected'):status='Agent offline';colour='red'
            elif r['node_id'] is not None and kind=='archive_cancel' and CANCEL_CAPABILITY not in self.hub.caps.get(r['node_id'],set()):status='Agent update required: archive cancellation with local disc cleanup';colour='amber'
            rows.append({'id':ident,'node_id':r['node_id'],'hostname':nodes.get(r['node_id'],{}).get('hostname',r['hostname']),'kind':kind,'name':r['request'].get('wallet') or r['request'].get('label','Old archive disc'),'state':r['state'],'status':status,'colour':colour,'created_utc':r['created_utc'],'needs_route':r['node_id'] is None,'route_options':self.route_options(r) if r['node_id'] is None else [],'can_cancel':kind!='archive_cancel' and (r['state']=='active' or kind=='archive_wallet' and r['state']=='complete')})
        return {'requests':rows}
    def cancel(self,user,ident):
        with storage_lock(self.config):
            s=self.read();r=s['requests'].get(ident)
            if not r:raise ValueError('Unknown archive request.')
            if r['request']['kind']=='archive_cancel':raise ValueError('Disc cleanup cancellation cannot be silently discarded. Review it locally on the Agent.')
            if r.get('cancel_requested'):pass
            elif r['request']['kind']=='archive_wallet' and r['state'] in ('active','complete'):
                if r['state']=='complete' and sum(v['state']=='active' and v['node_id']==r['node_id'] for v in s['requests'].values())>=128:raise ValueError('This Agent’s archive queue is full. Finish an outstanding request before requesting disc cleanup.')
                task={'kind':'archive_cancel','version':1,'nonce':secrets.token_hex(32),'wallet':r['request']['wallet'],'archive_request':copy.deepcopy(r['request']),'completed_receipt':copy.deepcopy(r.get('result',{}).get('signed_receipt')),'finish_inflight_burn_and_verify':True,'require_local_destruction_confirmation':True,'preserve_unrelated_members':True}
                key=digest(task);task['request_id']=key
                s['requests'][key]={'node_id':r['node_id'],'hostname':r['hostname'],'user_id':user,'created_utc':self.hub.utc(),'state':'active','status':'Cancellation awaiting Agent receipt and local disc cleanup','request':task}
                r.update(cancel_requested=True,cancellation_id=key)
                if r['state']=='active':r.update(state='withdrawn',status='Archive cancellation requested; any started burn must finish verification.')
                s['revision']+=1;self.write(s)
            elif r['state']=='active':r.update(state='withdrawn',status='Withdrawn by Dashboard; this does not claim that a disc was destroyed.');s['revision']+=1;self.write(s)
        return self.snapshot()
    def route_options(self,row):
        task=row['request'];archive=task.get('archive_id') or (task.get('completed_receipt') or {}).get('message',{}).get('archive_id')
        if not archive:return []
        names={n['id']:n['hostname'] for n in self.hub.registry._read()['nodes']};connected={c['node_id'] for c in list(self.hub.registry.connections.values())};found=[]
        for node,inv in list(self.hub.inventories.items()):
            cap=CANCEL_CAPABILITY if task['kind']=='archive_cancel' else CAPABILITY
            if node not in connected or node not in names or cap not in self.hub.caps.get(node,set()) or not inv.get('complete'):continue
            if any(w.get('signing_only') and w.get('archive_id')==archive for w in inv.get('wallets',[])):found.append({'id':node,'hostname':names[node]})
        return found
    def route(self,user,ident,node):
        row=self.read()['requests'].get(ident)
        if not row or row['state']!='active' or row['node_id'] is not None:raise ValueError('This disc request is not awaiting an Agent assignment.')
        if node not in {n['id'] for n in self.route_options(row)}:raise ValueError('Load the matching archive disc on a compatible connected Agent first.')
        refreshed=None
        if row['request'].get('reason')=='wallet_extinction':
            from wallet_retirement import preservation_live,verify_archive
            if not preservation_live(self.hub,row['request'].get('preservation')):
                for proof in self.hub.read()['requests'].values():
                    if proof.get('node_deleted') or proof.get('kind')!='wallet_retire' or proof.get('state')!='complete' or proof.get('proposal',{}).get('phase')!='preserve' or proof['proposal'].get('operation_id')!=row['request']['operation_id']:continue
                    try:refreshed=verify_archive(self.hub,proof);break
                    except (ValueError,OSError,KeyError):continue
                if refreshed is None:raise ValueError('Connect an Agent with the preserved Extinct history and verify it before routing physical destruction.')
        with storage_lock(self.config):
            s=self.read();r=s['requests'][ident]
            if r['node_id'] is not None:raise ValueError('The disc request was assigned in another browser.')
            if refreshed is not None:
                old=r;r=copy.deepcopy(r);old.update(state='withdrawn',status='Superseded by a freshly verified preservation review.');task=dict(r['request'],nonce=secrets.token_hex(32),preservation=refreshed);task.pop('request_id');ident=digest(task);task['request_id']=ident;r['request']=task;s['requests'][ident]=r
            r.update(node_id=node,hostname=next(n['hostname'] for n in self.hub.registry._read()['nodes'] if n['id']==node),status='Awaiting local Agent review');s['revision']+=1;self.write(s)
        return self.snapshot()
    def proposal(self,node):
        from wallet_retirement import preservation_live
        visible={}
        for ident,row in self.read()['requests'].items():
            if row['node_id']==node and row['state']=='active':visible[ident]=row['request'].get('reason')!='wallet_extinction' or preservation_live(self.hub,row['request'].get('preservation'))
        with storage_lock(self.config):
            s=self.read();changed=False
            for ident,allow in visible.items():
                if ident in s['requests'] and s['requests'][ident].get('queue_visible',True)!=allow:s['requests'][ident]['queue_visible']=allow;changed=True
            if changed:s['revision']+=1
            if changed or s['queue_id'] is None:self.write(s)
            return {'kind':'wallet_archive_queue','version':1,'queue_id':s['queue_id'],'revision':s['revision'],'requests':[copy.deepcopy(r['request']) for r in s['requests'].values() if r['node_id']==node and r['state']=='active' and r.get('queue_visible',True) and (r['request']['kind']!='archive_cancel' or CANCEL_CAPABILITY in self.hub.caps.get(node,set()))]}
    def accept(self,node,p):
        from wallet_policy_updates import verified
        body={k:v for k,v in p.items() if k!='signed_receipt'}
        if verified(self.hub,node,p.get('signed_receipt'))!=body:raise ValueError('Archive receipt signature differs from its result.')
        if not isinstance(p,dict) or p.get('version')!=1 or not isinstance(p.get('request_id'),str) or p.get('status') not in ('queued','deleted','complete','error'):raise ValueError('Invalid archive queue result.')
        replayed=False
        with storage_lock(self.config):
            s=self.read();r=s['requests'].get(p['request_id'])
            if p.get('queue_id')!=s['queue_id'] or not r or r['node_id']!=node:raise ValueError('Archive result does not match this Agent and request.')
            if r['state'] in ('complete','deleted'):
                if r.get('result')!=p:raise ValueError('A terminal archive outcome cannot change.')
                replayed=True
            elif r['state']=='withdrawn' and p['status'] not in ('complete','deleted'):return
            elif p['status']=='complete':
                if r['request']['kind']=='archive_wallet' and (p.get('readback_verified') is not True or not re.fullmatch('[0-9a-f]{64}',str(p.get('image_sha256')))):raise ValueError('Archive completion requires verified disc read-back evidence.')
                if r['request']['kind']=='destroy_disc' and p.get('destruction_confirmed_locally') is not True:raise ValueError('Only the Agent can confirm physical disc destruction.')
                if r['request']['kind']=='destroy_disc' and r['request'].get('version')==2:
                    task=r['request'];members=task['archive_inventory']['message']['members']
                    if len(task['retired_member_ids'])<len(members) and (p.get('replacement_readback_verified') is not True or not re.fullmatch('[0-9a-f]{64}',str(p.get('replacement_image_sha256')))):raise ValueError('Mixed-disc destruction requires verified replacement readback for the remaining current wallets.')
                if r['request']['kind']=='archive_cancel':
                    if p.get('request_cancelled') is not True or type(p.get('disc_created')) is not bool:raise ValueError('Archive cancellation requires a signed local outcome.')
                    if p['disc_created'] and (p.get('destruction_confirmed_locally') is not True or p.get('unrelated_members_preserved') is not True or p.get('burn_finished') is not True or p.get('verification_finished') is not True):raise ValueError('A created disc requires local destruction confirmation and preservation of unrelated members.')
                    original=s['requests'][r['request']['archive_request']['request_id']]
                    if original.get('result',{}).get('readback_verified') is True and p['disc_created'] is not True:raise ValueError('A verified completed archive cannot be reported as no disc created.')
                    if p['disc_created'] and (not re.fullmatch('[0-9a-f]{64}',str(p.get('archive_id'))) or type(p.get('disc_count')) is not int or p['disc_count']<1 or type(p.get('destroyed_disc_count')) is not int or p.get('destroyed_disc_count')!=p['disc_count']):raise ValueError('Every created disc must be identified and locally confirmed destroyed.')
                    if p['disc_created'] and original.get('result',{}).get('archive_id') and p['archive_id']!=original['result']['archive_id']:raise ValueError('Cancelled disc identity differs from its completed archive.')
                    if p['disc_created'] and type(p.get('replacement_required')) is not bool:raise ValueError('Report whether unrelated members required a replacement disc.')
                    if p.get('replacement_required') and (p.get('replacement_readback_verified') is not True or not re.fullmatch('[0-9a-f]{64}',str(p.get('replacement_image_sha256')))):raise ValueError('Verify the replacement disc before completing cancellation.')
                r.update(state='complete',status='Archive cancellation confirmed locally' if r['request']['kind']=='archive_cancel' else 'Archive verified on Agent' if r['request']['kind']=='archive_wallet' else 'Destruction confirmed locally on Agent',result=copy.deepcopy(p),outcome_utc=self.hub.utc());s['revision']+=1
            elif p['status']=='deleted':
                if r['request']['kind']=='archive_cancel':raise ValueError('An archive cancellation needs an explicit signed completion; deleting its review is not proof of cleanup.')
                r.update(state='deleted',status='Archive request deleted locally on Agent',result=copy.deepcopy(p),outcome_utc=self.hub.utc());s['revision']+=1
            else:r['status']={'queued':'Queued on Agent — awaiting completion','error':'Agent reported an archive error; review locally'}[p['status']];r['result']=copy.deepcopy(p)
            if not replayed:self.write(s)
        if p['status']=='complete' and r['request']['kind']=='archive_wallet':self.replacements(node,p,p['signed_receipt'])

    def followup(self,node,envelope):
        from wallet_policy_updates import verified
        p=verified(self.hub,node,envelope)
        self.inventory(node,p,envelope)
        if p.get('event')!='archive_observed':self.replacements(node,p,envelope)
    def inventory(self,node,p,envelope):
        if 'members' not in p:return
        observed=p.get('event')=='archive_observed'
        valid_evidence=(p.get('payload_verified') is True and p.get('payload_sha256')==p.get('archive_id') and p.get('readback_verified') is False) if observed else (p.get('readback_verified') is True and bool(re.fullmatch('[0-9a-f]{64}',str(p.get('image_sha256')))))
        if p.get('version')!=1 or p.get('event') not in ('archive_verified','replacement_verified','archive_observed') or not valid_evidence or not re.fullmatch('[0-9a-f]{64}',str(p.get('archive_id'))) or not isinstance(p.get('members'),list) or not 1<=len(p['members'])<=128:raise ValueError('Invalid authenticated public archive inventory.')
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
        hostname=next((n['hostname'] for n in self.hub.registry.presence()['nodes'] if n['id']==node),node)
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

    def needs_retired_disc(self,root):
        connected={n['id'] for n in self.hub.registry.presence()['nodes'] if n.get('connected')}
        queued=self.read()['requests'].values()
        for node,entry in self.hub.read()['nodes'].items():
            if node not in connected or 'wallet_retirement_v3' not in self.hub.caps.get(node,set()) or CAPABILITY not in self.hub.caps.get(node,set()):continue
            for archive,record in entry.get('archive_inventory',{}).items():
                if any(equivalent(m['public_wallet'],root['retirement_wallet']) for m in record['message']['members']) and not any(row['node_id']==node and row['request'].get('archive_id')==archive and row['request'].get('operation_id')==root['proposal']['operation_id'] for row in queued):return True
        return False

    def retired_discs(self,root,preservation):
        """Mixed discs require verified replacement before local destruction.

        Public inventory is evidence for a review request, never authority to erase.
        Withdrawn tasks stay withdrawn; local dismissal alone is not withdrawal.
        """
        connected={n['id']:n for n in self.hub.registry.presence()['nodes'] if n.get('connected')}
        state=self.hub.read();changed=False
        with storage_lock(self.config):
            s=self.read()
            for node,entry in state['nodes'].items():
                if node not in connected or 'wallet_retirement_v3' not in self.hub.caps.get(node,set()) or CAPABILITY not in self.hub.caps.get(node,set()):continue
                for archive,record in entry.get('archive_inventory',{}).items():
                    message=record['message'];members=[m for m in message['members'] if equivalent(m['public_wallet'],root['retirement_wallet'])]
                    if not members:continue
                    # One continuing review per disc and extinction operation.
                    operation=root['proposal']['operation_id']
                    if any(row['node_id'] in (node,None) and row['request'].get('archive_id')==archive and row['request'].get('operation_id')==operation for row in s['requests'].values()):continue
                    task={'kind':'destroy_disc','version':2,'reason':'wallet_extinction','nonce':secrets.token_hex(32),'operation_id':operation,'archive_id':archive,'label':'Retired wallet archive: '+root['proposal']['archive_label'],'archive_inventory':record['envelope'],'retired_wallet':wire(root['retirement_wallet']),'retired_member_ids':[m['member_id'] for m in members],'manifest_hash':root['proposal']['manifest_hash'],'preservation':copy.deepcopy(preservation),'require_local_authorisation':True,'require_replacement_if_live_members':True,'require_verified_readback_before_destruction':True}
                    ident=digest(task);task['request_id']=ident
                    if sum(row['node_id']==node and row['state']=='active' for row in s['requests'].values())>=128:raise ValueError('Archive review queue is full; extinction disc review remains outstanding.')
                    s['requests'][ident]={'node_id':node,'hostname':connected[node]['hostname'],'user_id':root['user_id'],'resource_id':root['wallet_id'],'created_utc':self.hub.utc(),'state':'active','status':'Review retired archive locally; preserve current wallets on a verified replacement disc before destruction','request':task};changed=True
            if changed:s['revision']+=1;self.write(s)

def validate(s):
    if not isinstance(s,dict) or set(s)!={'version','queue_id','revision','requests'} or s['version']!=1 or not re.fullmatch('[0-9a-f]{64}',str(s['queue_id'])) or type(s['revision']) is not int or s['revision']<0 or not isinstance(s['requests'],dict) or len(s['requests'])>100000:raise ValueError('Invalid archive queue profile data.')
    from descriptor_store import clean_wallet
    counts={}
    for ident,row in s['requests'].items():
        p=dict(row['request']);key=p.pop('request_id',None)
        if ident!=key or digest(p)!=key or row['state'] not in ('active','withdrawn','complete','deleted') or p.get('kind') not in ('archive_wallet','destroy_disc','archive_cancel'):raise ValueError('Invalid archived request identity.')
        if p['kind']=='archive_wallet':
            clean_wallet(p['public_wallet'])
            if p.get('require_local_authorisation') is not True:raise ValueError('Archive request requires local authorisation.')
            for raw in p.get('related_policies',[]):clean_wallet(raw)
        elif p['kind']=='archive_cancel':
            original=p.get('archive_request',{});body=dict(original);original_id=body.pop('request_id',None)
            if original.get('kind')!='archive_wallet' or digest(body)!=original_id or any(p.get(k) is not True for k in ('finish_inflight_burn_and_verify','require_local_destruction_confirmation','preserve_unrelated_members')):raise ValueError('Invalid archive cancellation identity or safeguards.')
            source=s['requests'].get(original_id)
            if not source or (source['node_id']!=row['node_id'] and not row.get('previous_node_id')) or source['request']!=original or source.get('cancellation_id')!=ident:raise ValueError('Archive cancellation belongs to another request or Agent.')
            clean_wallet(original['public_wallet'])
        elif p.get('version')==2 and p.get('reason')=='wallet_extinction':
            clean_wallet(p['retired_wallet'])
            if not re.fullmatch('[0-9a-f]{64}',str(p.get('archive_id'))) or p.get('require_local_authorisation') is not True or p.get('require_replacement_if_live_members') is not True or p.get('require_verified_readback_before_destruction') is not True or not p.get('preservation',{}).get('receipt') or not p.get('retired_member_ids'):raise ValueError('Invalid retired-disc review request.')
        elif not re.fullmatch('[0-9a-f]{64}',str(p.get('archive_id'))) or not re.fullmatch('[0-9a-f]{64}',str(p.get('replacement_image_sha256'))):raise ValueError('Invalid old-disc destruction request.')
        if row['state']=='active':counts[row['node_id']]=counts.get(row['node_id'],0)+1
    if any(n>128 for n in counts.values()):raise ValueError('Too many active archive requests for one Agent.')
    return s
