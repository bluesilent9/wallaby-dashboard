"""Public-only, authenticated two-approval wallet setup coordinator.

Keys are generated and encrypted by Agents. No secret, backup or passphrase crosses
this boundary. Restarts interrupt unfinished ceremonies instead of regenerating keys.
"""
import copy,json,os,re,secrets,time
from contextlib import contextmanager
from storage import storage_lock,data_directory
from setup_signatures import verify,fingerprint
from wallet_setup_policy import proposal,policy
from agent_hub import digest,canonical
from vendor.embit.descriptor import Descriptor
from vendor.embit.networks import NETWORKS

class WalletBuilder:
    def __init__(self,hub):
        self.hub=hub;self.config=hub.config;self.path=data_directory(self.config)/'wallet-setups.json';self.live={}
        if self.path.exists():
            with self.edit() as state:
                for row in state['sessions'].values():
                    if row['state'] not in ('ready','cancelled','interrupted'):row['state']='interrupted';row['message']='Dashboard restarted. Reconcile the existing setup locally on every Agent; start a new ceremony only after that.'
    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'version':1,'identities':{},'challenges':{},'sessions':{}}
    @contextmanager
    def edit(self):
        with storage_lock(self.config):
            state=self.read();yield state;raw=canonical(state)
            if len(raw)>16*1024*1024:raise ValueError('Wallet setup public evidence storage is full.')
            self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_suffix('.tmp');tmp.write_text(raw);tmp.chmod(0o600)
            with tmp.open('rb') as f:os.fsync(f.fileno())
            os.replace(tmp,self.path)
    def available(self):
        result=[]
        for node in self.hub.registry.snapshot()['nodes']:
            version=(node.get('agent') or {}).get('version') or next((c.get('agent_version','') for c in self.hub.registry.connections.values() if c['node_id']==node['id']),'')
            if node.get('rpc_ready') and node.get('blockchain',{}).get('state')=='ready' and re.fullmatch(r'2\.0\.0~dev(?:9|[1-9][0-9]+)',version or ''):result.append(node)
        return result
    def discover(self):
        nodes=self.available()
        if not nodes:raise ValueError('No eligible synchronised Agents are connected. Check Nodes for the connection, Core status and Agent version.')
        with self.edit() as s:
            for n in nodes:
                prior=s['challenges'].get(n['id']);reply=s['identities'].get(n['id'],{}).get('message',{}).get('challenge')
                # Checking again while a signed reply is in flight must not
                # invalidate the outstanding challenge and strand its outbox.
                if not prior or reply==prior:s['challenges'][n['id']]=secrets.token_hex(32)
        return self.snapshot()
    def start(self,user,data):
        if not isinstance(data,dict) or set(data)!={'name','threshold','height','roles','fingerprints_compared'} or data['fingerprints_compared'] is not True:raise ValueError('Compare each full Agent fingerprint locally before requesting creation.')
        roles=data['roles']
        if not isinstance(roles,list) or not 1<=len(roles)<=15:raise ValueError('Choose 1 to 15 independent signer roles.')
        ready={n['id']:n for n in self.available()}
        with self.edit() as s:
            selected={r.get('node_id') for r in roles if isinstance(r,dict)}
            if any(r['state'] in ('participation','policy') and selected & set(r['routes'].values()) for r in s['sessions'].values()):raise ValueError('Finish or stop the existing ceremony and reconcile locally before assigning these Agents again.')
            assigned=[];routes={}
            for index,r in enumerate(roles):
                if not isinstance(r,dict) or set(r)!={'node_id','name','storage'} or r['node_id'] not in ready or r['node_id'] not in s['identities']:raise ValueError('Discover and verify every selected Agent first.')
                identity=s['identities'][r['node_id']]
                if identity['message']['challenge']!=s['challenges'].get(r['node_id']):raise ValueError('Wait for the fresh identity challenge response.')
                rid='signer-'+str(index+1);routes[rid]=r['node_id'];assigned.append({'id':rid,'name':r['name'],'storage':r['storage'],'agent_key':identity['public_key']})
            p={'kind':'wallet_setup','version':1,'session_id':secrets.token_hex(32),'chain':'main','name':data['name'].strip(),'threshold':data['threshold'],'cosigners':assigned,'height':data['height'],'expires_at':int(time.time())+86400-5};proposal(p)
            h=digest(p);s['sessions'][h]={'proposal':p,'routes':routes,'creator_id':user,'state':'participation','contributions':{},'acks':{},'inventories':{},'created_utc':self.hub.utc(),'message':'Approve participation and key generation locally on each producing Agent.'}
        return self.snapshot()
    def cancel(self,ident):
        with self.edit() as s:
            row=s['sessions'].get(ident)
            if not row or row['state']=='ready':raise ValueError('Select an unfinished setup.')
            row['state']='cancelled';row['message']='Cancelled here. Reconcile or discard the pending setup locally on each Agent; this does not delete Core wallets.'
        return self.snapshot()
    def accept(self,node,kind,envelope):
        s=self.read()
        if kind=='wallet_setup_identity':
            key=envelope.get('public_key') if isinstance(envelope,dict) else None;m=verify(self.config,envelope,key)
            if not isinstance(m,dict) or set(m)!={'version','challenge','public_key','fingerprint','hostname'} or m['version']!=1 or not isinstance(m['challenge'],str) or not re.fullmatch('[0-9a-f]{64}',m['challenge']) or m['public_key']!=key or m['fingerprint']!=fingerprint(key) or not isinstance(m['hostname'],str) or not m['hostname'].strip() or len(m['hostname'])>253 or any(ord(c)<32 for c in m['hostname']):raise ValueError('Invalid Agent identity response.')
            if m['challenge']!=s['challenges'].get(node):
                known=s['identities'].get(node)
                if known and known['public_key']!=key:raise ValueError('Stale identity response comes from a different Agent identity.')
                # Authenticate then discard stale public evidence, without ever
                # making it eligible to create a wallet. Acknowledge its item so
                # an older queued reply cannot block the current challenge.
                self.hub.registry.diagnostic_log.append('Wallet Builder','Discarded a stale signed identity reply; current discovery remains pending.')
                return False
            with self.edit() as state:state['identities'][node]=envelope
            return
        if not isinstance(envelope,dict) or not isinstance(envelope.get('message'),dict):raise ValueError('Missing signed setup evidence.')
        m=envelope['message'];h=m.get('proposal_hash');row=s['sessions'].get(h)
        if not row and kind=='wallet_setup_inventory':
            matches=[(k,r) for k,r in s['sessions'].items() if r.get('policy_hash')==m.get('policy_hash') and r['proposal']['session_id']==m.get('session_id')]
            if len(matches)==1:h,row=matches[0]
        if not row or node not in row['routes'].values():raise ValueError('Unsolicited setup evidence.')
        roles=row['proposal']['cosigners'];keys={r['agent_key'] for r in roles if row['routes'][r['id']]==node}
        if envelope.get('public_key') not in keys:raise ValueError('Setup evidence has a different pinned Agent identity.')
        verify(self.config,envelope,envelope['public_key'])
        if kind=='wallet_setup_contribution':
            rid=m.get('cosigner_id');role=next((r for r in roles if r['id']==rid),None)
            if not role or row['routes'][rid]!=node or role['agent_key']!=envelope['public_key']:raise ValueError('Contribution role or producer differs.')
            if m.get('session_id')!=row['proposal']['session_id'] or h!=digest(row['proposal']):raise ValueError('Contribution belongs to a different setup.')
            from wallet_setup_policy import public_key
            if set(m)!={'session_id','proposal_hash','cosigner_id','receive_key','change_key'}:raise ValueError('Invalid contribution fields.')
            for b,k in ((0,'receive_key'),(1,'change_key')):public_key(m[k],b,row['proposal']['chain'])
            if rid in row['contributions'] and row['contributions'][rid]!=envelope:raise ValueError('A signer contribution cannot be replaced within this ceremony.')
        elif kind=='wallet_setup_ack':
            status=m.get('status')
            if m.get('version')!=1 or status not in ('participating','policy_approved','backup_verified','denied'):raise ValueError('Invalid setup acknowledgement.')
            fields={'version','proposal_hash','status'}
            if status!='denied':fields.add('session_id')
            if status=='policy_approved':fields|={'policy_hash','backup'}
            if status=='backup_verified':fields|={'policy_hash','cosigner_file','image_sha256'}
            if set(m)!=fields:raise ValueError('Unexpected setup acknowledgement fields.')
            if status!='denied' and m['session_id']!=row['proposal']['session_id']:raise ValueError('Acknowledgement session mismatch.')
            if status in ('policy_approved','backup_verified') and m['policy_hash']!=row.get('policy_hash'):raise ValueError('Acknowledgement policy mismatch.')
            if status=='policy_approved' and m['backup'] not in ('pending','not_required'):raise ValueError('Invalid backup status.')
            if status=='backup_verified':
                disc=[r for r in roles if r['storage']=='disc' and r['agent_key']==envelope['public_key'] and __import__('hashlib').sha256(r['id'].encode()).hexdigest()==m['cosigner_file']]
                if len(disc)!=1 or not re.fullmatch('[0-9a-f]{64}',str(m['image_sha256'])):raise ValueError('Backup proof does not match an assigned physical signer.')
        elif kind=='wallet_setup_inventory':
            if set(m)!={'version','session_id','policy_hash','chain','monitor_wallet','signing_wallets','receive_descriptor','change_descriptor'} or m['version']!=1 or m['chain']!='main' or m['session_id']!=row['proposal']['session_id'] or m['policy_hash']!=row.get('policy_hash') or [m['receive_descriptor'],m['change_descriptor']]!=row.get('descriptors'):raise ValueError('Installed public policy differs from the approved setup.')
            expected={r['id'] for r in roles if row['routes'][r['id']]==node and r['storage']=='node'}
            if not isinstance(m['signing_wallets'],dict) or set(m['signing_wallets'])!=expected or any(not isinstance(v,str) or not v or len(v)>256 for v in [m['monitor_wallet'],*m['signing_wallets'].values()]):raise ValueError('Installed signing-account roles differ.')
        else:raise ValueError('Unknown setup evidence kind.')
        with self.edit() as state:
            row=state['sessions'][h]
            if kind=='wallet_setup_contribution':row['contributions'].setdefault(m['cosigner_id'],envelope)
            elif kind=='wallet_setup_inventory':row['inventories'][node]=envelope
            else:
                same=[e for e in row['acks'].values() if e['public_key']==envelope['public_key'] and e['message']['status']==m['status'] and e['message'].get('cosigner_file')==m.get('cosigner_file')]
                if not same:row['acks'][digest(envelope)]=envelope
            if kind=='wallet_setup_ack' and m['status']=='denied':row['state']='interrupted';row['message']='An Agent denied this ceremony. Reconcile locally before creating a new setup.'
            if row['state'] in ('cancelled','interrupted'):return
            if len(row['contributions'])==len(roles) and not row.get('policy_hash'):
                envelopes=[row['contributions'][r['id']] for r in sorted(roles,key=lambda r:r['id'])];descs=policy(row['proposal'],[e['message'] for e in envelopes]);row['descriptors']=descs
                row['policy_hash']=digest({'proposal':row['proposal'],'receive_descriptor':descs[0],'change_descriptor':descs[1],'contributions':envelopes});row['state']='policy';row['message']='Compare the full policy hash and six sample addresses on every Agent, then approve locally. Complete each physical backup there.'
            if row.get('policy_hash'):
                approvals={e['public_key'] for e in row['acks'].values() if e['message']['status']=='policy_approved'};backups={e['message']['cosigner_file'] for e in row['acks'].values() if e['message']['status']=='backup_verified'}
                if {r['agent_key'] for r in roles}<=approvals and all(__import__('hashlib').sha256(r['id'].encode()).hexdigest() in backups for r in roles if r['storage']=='disc'):
                    row['state']='ready';row['message']='All producer policy approvals and required disc-backup proofs verified. Completion evidence is being delivered to the Agents. Reload Node Wallets before funding; confirm the local monitor is marked ready.'
    def proposals(self,node,token):
        output=[]
        with self.edit() as s:
            if node in s['challenges'] and s.get('identities',{}).get(node,{}).get('message',{}).get('challenge')!=s['challenges'][node]:output.append({'kind':'wallet_setup_identity','version':1,'challenge':s['challenges'][node]})
            for h,row in s['sessions'].items():
                if node not in row['routes'].values() or row['state'] in ('cancelled','interrupted'):continue
                if row['proposal']['expires_at']<=time.time() and row['state']!='ready':row['state']='interrupted';row['message']='Ceremony expired. Reconcile locally; existing keys must not be regenerated.';continue
                key=(h,node);prior=self.live.get(key)
                if prior is not None and prior!=token and row['state']!='ready':row['state']='interrupted';row['message']='Agent reconnected during setup. Reconcile its pending ceremony locally before starting another.';continue
                self.live[key]=token
                if row['state']=='ready':
                    if node in row.get('completion_confirmed',{}):continue
                    sent=row.setdefault('completion_sent',{}).get(node)
                    if sent and (prior is None or prior!=token):
                        row['message']='Completion was sent before a reconnection. Reload node wallets to check Core’s ready label; reconcile locally if it is still pending.';continue
                    if sent and time.time()-sent<30:continue
                    row['completion_sent'][node]=time.time()
                    proofs=[e for e in row['acks'].values() if e['message']['status'] in ('policy_approved','backup_verified')];output.append({'kind':'wallet_setup_complete','version':1,'proposal_hash':h,'policy_hash':row['policy_hash'],'acknowledgements':proofs});continue
                if row['state']=='participation':output.append(row['proposal'])
                elif row['state']=='policy':output.append({'kind':'wallet_setup_policy','version':1,'proposal':row['proposal'],'contributions':[row['contributions'][r['id']] for r in sorted(row['proposal']['cosigners'],key=lambda r:r['id'])],'receive_descriptor':row['descriptors'][0],'change_descriptor':row['descriptors'][1]})
        return output
    def confirm_installed(self,node,token,wallets):
        from node_wallets import NodeWalletLoader
        for h,row in self.read()['sessions'].items():
            if row['state']!='ready' or node not in row['routes'].values() or node in row.get('completion_confirmed',{}):continue
            expected={NodeWalletLoader.descriptor_key(d) for d in row['descriptors']}
            matches=[w for w in wallets if not w.get('signing_only') and {NodeWalletLoader.descriptor_key(r['desc']) for r in w['raw']['descriptors']}==expected]
            if not matches:continue
            address=Descriptor.from_string(row['descriptors'][0]).derive(0).address(NETWORKS['main'])
            labels=self.hub.rpc(node,'getaddressinfo',[address],matches[0]['name']).get('labels',[])
            if 'Wallaby setup ready:'+row['policy_hash'] not in [v if isinstance(v,str) else v.get('name') for v in labels]:continue
            if token not in self.hub.registry.connections:continue
            with self.edit() as state:
                current=state['sessions'][h];current.setdefault('completion_confirmed',{})[node]=self.hub.utc()
                if set(current['routes'].values())<=set(current['completion_confirmed']):current['message']='Every producing Core node confirms the exact public monitor is ready. Reload Node Wallets in Ownership to use it.'
    def snapshot(self):
        from node_wallets import NodeWalletLoader
        state=self.read();nodes=[]
        for n in self.available():
            identity=state['identities'].get(n['id']);nodes.append({'id':n['id'],'hostname':n['hostname'],'fingerprint':identity['message']['fingerprint'] if identity else None,'requested':n['id'] in state['challenges'],'fresh':bool(identity and identity['message']['challenge']==state['challenges'].get(n['id']))})
        sessions=[]
        for h,r in state['sessions'].items():
            row=copy.deepcopy(r);row['id']=h;row['samples']=[];row['producers']=[{'role_id':role['id'],'hostname':state['identities'].get(r['routes'][role['id']],{}).get('message',{}).get('hostname','Agent'),'fingerprint':fingerprint(role['agent_key'])} for role in r['proposal']['cosigners']]
            if r.get('descriptors'):
                for branch,desc in zip(('Receive','Change'),r['descriptors']):
                    for index in (0,1,10):row['samples'].append({'branch':branch,'index':index,'address':Descriptor.from_string(desc).derive(index).address(NETWORKS['main'])})
            required=set(r['routes'].values());confirmed=set(r.get('completion_confirmed',{}))
            status='awaiting_approvals';label='Waiting for local Agent approvals and required backups'
            if r['state'] in ('cancelled','interrupted'):status=r['state'];label='Stopped — reconcile the existing setup on the Agents before starting again'
            elif r['state']=='ready':
                status='awaiting_confirmation';label='Wallet created — waiting for Core confirmation'
                if required<=confirmed:
                    status='ready_to_fund';label='Ready to fund — use Transaction Builder'
                    monitor=getattr(self.hub.registry,'vaults',None)
                    for vault in (monitor.value.get('wallets',[]) if monitor else []):
                        bodies={NodeWalletLoader.descriptor_key(p['public'].get('descriptor','')) for p in vault.get('policies',[])}
                        if not bodies & {NodeWalletLoader.descriptor_key(d) for d in r.get('descriptors',[])}:continue
                        # A historical green card cannot establish current funding status.
                        from datetime import datetime,timezone
                        try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(vault.get('checked_utc','').replace('Z','+00:00'))).total_seconds()
                        except (ValueError,TypeError):age=10**9
                        if vault.get('stale') or age>45:status='stale';label='Funding status unavailable — check Vaults'
                        elif vault.get('funding_status')=='locked':status='locked';label='Funded and locked'
                        elif vault.get('funding_status')=='unlocked':status='unlocked';label='Unlocked — vault height reached'
                        elif vault.get('funding_status')=='needs_attention':status='needs_attention';label='Vault needs attention — check Vaults'
                        break
            row.update(setup_status=status,setup_label=label)
            sessions.append(row)
        return {'nodes':nodes,'sessions':sessions}


def validate_archive(config,state,users):
    """Reverify imported public ceremonies before trusting readiness or routing."""
    if not isinstance(state,dict) or set(state)!={'version','identities','challenges','sessions'} or state['version']!=1:raise ValueError('Invalid saved wallet setups.')
    canonical(state)
    if any(not isinstance(state[k],dict) or len(state[k])>1000 for k in ('identities','challenges','sessions')):raise ValueError('Invalid saved setup bounds.')
    for node,envelope in state['identities'].items():
        m=verify(config,envelope,envelope['public_key'])
        if m.get('public_key')!=envelope['public_key'] or m.get('fingerprint')!=fingerprint(envelope['public_key']):raise ValueError('Invalid saved Agent identity.')
    for h,row in state['sessions'].items():
        p=row['proposal'];proposal(p,fresh=False)
        if digest(p)!=h or row['creator_id'] not in users or row['state'] not in ('participation','policy','ready','cancelled','interrupted'):raise ValueError('Invalid saved ceremony identity.')
        roles={r['id']:r for r in p['cosigners']}
        if set(row['routes'])!=set(roles):raise ValueError('Missing saved signer routes.')
        for rid,e in row['contributions'].items():
            if rid not in roles:raise ValueError('Unknown saved cosigner.')
            m=verify(config,e,roles[rid]['agent_key'])
            if m.get('cosigner_id')!=rid or m.get('session_id')!=p['session_id'] or m.get('proposal_hash')!=h:raise ValueError('Saved contribution belongs to another setup.')
        if row.get('policy_hash'):
            envelopes=[row['contributions'][rid] for rid in sorted(roles)];descs=policy(p,[e['message'] for e in envelopes],fresh=False)
            expected=digest({'proposal':p,'receive_descriptor':descs[0],'change_descriptor':descs[1],'contributions':envelopes})
            if row['policy_hash']!=expected or row['descriptors']!=descs:raise ValueError('Saved exact policy differs from its contributions.')
        approved=set();backups=set()
        for ident,e in row['acks'].items():
            if ident!=digest(e) or e['public_key'] not in {r['agent_key'] for r in roles.values()}:raise ValueError('Invalid saved approval signer.')
            m=verify(config,e,e['public_key'])
            if m.get('proposal_hash')!=h or m.get('version')!=1:raise ValueError('Saved approval proposal mismatch.')
            if m['status'] in ('policy_approved','backup_verified'):
                if m.get('session_id')!=p['session_id'] or m.get('policy_hash')!=row.get('policy_hash'):raise ValueError('Saved approval policy mismatch.')
                if m['status']=='policy_approved':approved.add(e['public_key'])
                else:
                    matching=[r for r in roles.values() if r['storage']=='disc' and r['agent_key']==e['public_key'] and __import__('hashlib').sha256(r['id'].encode()).hexdigest()==m.get('cosigner_file')]
                    if len(matching)!=1 or not re.fullmatch('[0-9a-f]{64}',str(m.get('image_sha256'))):raise ValueError('Invalid saved physical backup evidence.')
                    backups.add(m['cosigner_file'])
        if row['state']=='ready' and (approved!={r['agent_key'] for r in roles.values()} or backups!={__import__('hashlib').sha256(r['id'].encode()).hexdigest() for r in roles.values() if r['storage']=='disc'}):raise ValueError('Saved ceremony is not fully approved and backed up.')
        for node,e in row['inventories'].items():
            if node not in row['routes'].values():raise ValueError('Unknown saved inventory producer.')
            m=verify(config,e,e['public_key'])
            if e['public_key'] not in {r['agent_key'] for rid,r in roles.items() if row['routes'][rid]==node} or m.get('policy_hash')!=row.get('policy_hash') or [m.get('receive_descriptor'),m.get('change_descriptor')]!=row.get('descriptors'):raise ValueError('Saved installed policy mismatch.')
    return state
