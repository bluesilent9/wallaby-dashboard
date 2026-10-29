"""Durable shared signing workflows and per-user notifications."""
import base64,copy,json,os,re,uuid
from datetime import date,datetime,timezone
from pathlib import Path
from storage import data_directory,storage_lock
from unsigned_psbt import construct
from signed_psbt import decode,utxos,validate_upload,combine
from preferences import FamilyStore
from bitaroo_freshness import statement_age

class WorkflowStore:
    def __init__(self,config):
        self.config=Path(config);self.path=data_directory(self.config)/'transactions'/'workflows.json'
        # V145's session-refresh notices are retired for every profile on upgrade.
        if self.path.exists():
            with storage_lock(self.config):
                data=self._read();changed=False
                for user,rows in data['notices'].items():
                    kept=[n for n in rows if n.get('kind')!='template_refresh']
                    if len(kept)!=len(rows):data['notices'][user]=kept;changed=True
                if changed:self._write(data)
    def _read(self):return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'jobs':{},'notices':{},'change_cursors':{}}
    def _write(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp')
        try:temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');os.chmod(temp,0o600);os.replace(temp,self.path)
        finally:temp.unlink(missing_ok=True)
    def prune_users(self,users):
        with storage_lock(self.config):
            data=self._read();valid=set(users)
            for row in data['jobs'].values():
                if row['creator_id'] not in valid or any(u not in valid for u in row['participants']):row['cancelled']=True
            data['notices']={u:rows for u,rows in data['notices'].items() if u in valid}
            data['dismissed_bitaroo_warnings']={u:stamp for u,stamp in data.get('dismissed_bitaroo_warnings',{}).items() if u in valid}
            self._write(data)
    def sync_bitaroo_warning(self,user,stamp,local_date,zone="UTC"):
        # The browser supplies its calendar date; statement coverage comes only
        # from the restored server workspace, never from client input.
        days,report_date=statement_age(stamp,local_date,zone)
        days=days if days is not None else 0
        with storage_lock(self.config):
            state=self._read();before=json.dumps(state,sort_keys=True);rows=state['notices'].setdefault(user,[])
            current=next((n for n in rows if n.get('kind')=='bitaroo_warning' and n.get('statement_date')==stamp),None)
            rows[:]=[n for n in rows if n.get('kind')!='bitaroo_warning' or n is current and days>=3]
            dismissed=state.setdefault('dismissed_bitaroo_warnings',{}).get(user)
            if days>=3 and dismissed!=stamp:
                if current is None:
                    current={'id':uuid.uuid4().hex,'kind':'bitaroo_warning','statement_date':stamp,'text':'Warning: Bitaroo data may be out of date. Update the Bitaroo PDFs and run Ownership to remove this warning','created_utc':datetime.now(timezone.utc).isoformat(),'read':False}
                    rows.append(current)
                current['age_days']=days;current['report_local_date']=report_date;current['severity']='red' if days>=7 else 'yellow'
            if json.dumps(state,sort_keys=True)!=before:self._write(state)
    def sync_run_warning(self,user,local_date,zone,page):
        from run_freshness import warning,PAGES
        existing=self._read().get('notices',{}).get(user,[])
        if page not in PAGES and not any(n.get('kind') in ('run_freshness','bitaroo_warning') for n in existing):return
        value=warning(self.config,FamilyStore(self.config).snapshot(),local_date,zone)
        with storage_lock(self.config):
            state=self._read();rows=state['notices'].setdefault(user,[]);before=json.dumps(rows,sort_keys=True)
            previous=next((n for n in rows if n.get('kind')=='run_freshness'),None)
            rows[:]=[n for n in rows if n.get('kind') not in ('run_freshness','bitaroo_warning')]
            if value:
                same=previous and previous.get('run_kind')==value['run_kind'] and previous.get('run_stamp')==value['run_stamp']
                rows.append(dict(value,id=previous['id'] if same else uuid.uuid4().hex,created_utc=previous['created_utc'] if same else datetime.now(timezone.utc).isoformat()))
            if json.dumps(rows,sort_keys=True)!=before:self._write(state)
    def send_message(self,sender_id,target_id,text):
        snapshot=FamilyStore(self.config).snapshot();users={u['id']:u for u in snapshot['users']}
        if sender_id not in users or target_id not in users or sender_id==target_id:raise ValueError('Select another existing user to message.')
        if not isinstance(text,str) or not text.strip() or len(text)>4000:raise ValueError('Enter a message of 1–4,000 characters.')
        notice={'id':uuid.uuid4().hex,'kind':'message','sender_id':sender_id,'sender_name':users[sender_id]['name'],'text':'From '+users[sender_id]['name']+': '+text.strip(),'created_utc':datetime.now(timezone.utc).isoformat(),'read':False}
        with storage_lock(self.config):
            data=self._read();data['notices'].setdefault(target_id,[]).append(notice);self._write(data)
        return {'sent':True}
    def notifications(self,user,scope=None):
        with storage_lock(self.config):
            state=self._read();rows=[dict(n,cancelled=state['jobs'].get(n.get('job_id'),{}).get('cancelled',False),cancel_reason=state['jobs'].get(n.get('job_id'),{}).get('cancel_reason')) for n in state['notices'].get(user,[])]
        return {'notifications':list(reversed(rows)),'unread_count':sum(not n['read'] for n in rows)}
    def update_notifications(self,user,delete=None):
        with storage_lock(self.config):
            data=self._read();rows=data['notices'].get(user,[])
            if delete is not None:
                if any(n['id']==delete and n.get('kind')=='run_freshness' for n in rows):raise ValueError('Refresh the relevant process to remove this notification.')
                warning=next((n for n in rows if n['id']==delete and n.get('kind')=='bitaroo_warning'),None)
                if warning: data.setdefault('dismissed_bitaroo_warnings',{})[user]=warning['statement_date']
                data['notices'][user]=[n for n in rows if n['id']!=delete]
            else:
                for n in rows:n['read']=True
            self._write(data)
        return self.notifications(user)
    def active(self,user):
        with storage_lock(self.config):
            return [{'id':j['id'],'created_utc':j['created_utc']} for j in self._read()['jobs'].values() if not j.get('cancelled') and not j.get('completed') and user in j['participants']+[j['creator_id']]]
    def job(self,user,ident):
        if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{32}',ident):raise ValueError('Invalid transaction ID.')
        with storage_lock(self.config):
            row=self._read()['jobs'].get(ident)
            if not row or user not in row['participants']+[row['creator_id']]:raise ValueError('This transaction is not available for this profile.')
            return copy.deepcopy(row)
    def input_wallets(self,row):
        original=decode(base64.b64decode(row['psbt'],validate=True));assigned=row.get('input_wallets')
        sources={e['from'] for e in row['diagram']['edges'] if e.get('max') or int(e['amount_sats']) or e['fee_rate'].strip('0.')}
        if isinstance(assigned,list) and len(assigned)==len(original['inputs']) and all(uid in sources for uid in assigned):return assigned
        # V143 prepared jobs predate explicit per-input assignments. Corroborate
        # their actual previous-output scripts against the supplied wallet history.
        from descriptor_store import DescriptorStore
        from descriptor_snapshot import saved_history
        from fee_planner import known_scripts
        from preferences import resource_id
        catalog=DescriptorStore(self.config).read();history=saved_history(self.config,catalog);scripts={resource_id('descriptor',uid):values for uid,values in known_scripts(history).items()}
        assigned=[]
        for prev in utxos(original):
            candidates=[uid for uid in sources if prev.script_pubkey.data.hex() in scripts.get(uid,set())]
            if len(candidates)!=1:raise ValueError('Reopen this V143 transaction for editing and prepare it again to establish its signing-wallet assignments.')
            assigned.append(candidates[0])
        return assigned
    def public_job(self,user,ident):
        row=self.job(user,ident);assigned=self.input_wallets(row);uploads=row.get('signed_uploads',{});wallets={w['id']:w for w in row['wallets']}
        signing=[]
        for uid in sorted(set(assigned)):
            w=wallets[uid];received=uploads.get(uid,{})
            signing.append({k:w.get(k) for k in ('id','name','owner_id','owner','colour')})
            signing[-1].update(complete=bool(received),uploaded_utc=received.get('uploaded_utc'),input_count=assigned.count(uid))
        all_signed=bool(signing) and all(w['complete'] for w in signing)
        row.pop('signed_uploads',None);combined=bool(row.pop('combined_psbt',None));row['signing_wallets']=signing;row['all_signed']=all_signed;row['combined_ready']=combined
        row['stage_count']=2 if row.get('agent_signing') else 2 if len(set(assigned))==1 else 3
        row['completed']=bool(row.get('completed'));row['combined_downloaded']=bool(row.get('combined_downloaded_utc'))
        row['can_combine']=row['stage_count']==3 and user==row['creator_id'] and all_signed and not row['cancelled'] and not row['completed']
        row['can_complete']=user==row['creator_id'] and not row['cancelled'] and not row['completed'] and (row['stage_count']==2 or combined and row['combined_downloaded'])
        if row.get('agent_signing'):row['can_complete']=user==row['creator_id'] and bool(row.get('broadcast_txid')) and not row['cancelled'] and not row['completed']
        row['stage']=3 if combined and user==row['creator_id'] else 2;row['signing_revision']=row.get('signing_revision',0)
        return row
    def status(self,user,ident):
        row=self.public_job(user,ident)
        return {k:row[k] for k in ('id','cancelled','creator_id','signing_wallets','all_signed','combined_ready','can_combine','stage','stage_count','completed','combined_downloaded','can_complete','signing_revision','broadcast_txid') if k in row}
    def upload(self,user,ident,wallet_id,raw,filename=''):
        row=self.job(user,ident)
        if row.get('agent_signing'):raise ValueError('This transaction accepts signed results only from its selected Agent.')
        assigned=self.input_wallets(row);wallet=next((w for w in row['wallets'] if w['id']==wallet_id),None)
        if wallet_id not in assigned or not wallet or wallet.get('owner_id')!=user:raise ValueError('Only the owner of this sending wallet can upload its signed PSBT.')
        if row['cancelled'] or row.get('completed') or row.get('combined_psbt'):raise ValueError('This transaction is closed for signed uploads. Reopen it to prepare a replacement.')
        if len(set(assigned))==1:raise ValueError('Single-wallet transactions are signed and broadcast in Bitcoin Core; no upload is required.')
        indices=[i for i,uid in enumerate(assigned) if uid==wallet_id];proofs=validate_upload(row['psbt'],raw,indices)
        with storage_lock(self.config):
            state=self._read();current=state['jobs'].get(ident)
            if not current or current['cancelled'] or current.get('completed') or current.get('combined_psbt'):raise ValueError('The transaction was closed while the PSBT was being checked.')
            current['input_wallets']=assigned
            current.setdefault('signed_uploads',{})[wallet_id]={'proofs':proofs,'uploaded_by':user,'uploaded_utc':datetime.now(timezone.utc).isoformat()}
            current['signing_revision']=current.get('signing_revision',0)+1
            if all(uid in current['signed_uploads'] for uid in set(assigned)) and not current.get('signing_complete_notified'):
                current['signing_complete_notified']=True
                state['notices'].setdefault(current['creator_id'],[]).append({'id':uuid.uuid4().hex,'job_id':ident,'text':'All signed PSBTs have been uploaded. Continue your Bitcoin Transaction.','created_utc':datetime.now(timezone.utc).isoformat(),'read':user==current['creator_id']})
            self._write(state)
        return self.status(user,ident)
    def combine(self,user,ident):
        row=self.job(user,ident)
        if user!=row['creator_id']:raise ValueError('Only the profile that started this transaction can continue to Stage 4.')
        if row['cancelled'] or row.get('completed'):raise ValueError('This transaction is closed.')
        if row.get('combined_psbt'):return self.status(user,ident)
        assigned=self.input_wallets(row)
        if len(set(assigned))==1:raise ValueError('Single-wallet transactions do not have a combination stage.')
        combined=combine(row['psbt'],row.get('signed_uploads',{}),assigned)
        with storage_lock(self.config):
            state=self._read();current=state['jobs'].get(ident)
            if not current or current['cancelled'] or current.get('completed') or current.get('signing_revision',0)!=row.get('signing_revision',0):raise ValueError('Signing data changed during combination. Refresh the transaction and try again.')
            current['combined_psbt']=combined;current['signing_revision']=current.get('signing_revision',0)+1
            for notice in state['notices'].get(user,[]):
                if notice.get('job_id')==ident:notice['read']=True
            self._write(state)
        return self.status(user,ident)
    def combined(self,user,ident):
        row=self.job(user,ident)
        if user!=row['creator_id'] or row['cancelled'] or row.get('completed') or not row.get('combined_psbt'):raise ValueError('The combined PSBT is available only to its creator after Stage 4 is complete.')
        return row['combined_psbt']
    def record_combined_download(self,user,ident):
        self.combined(user,ident)
        with storage_lock(self.config):
            state=self._read();row=state['jobs'][ident]
            if row.get('completed') or row['cancelled']:raise ValueError('This transaction is closed.')
            if not row.get('combined_downloaded_utc'):
                row['combined_downloaded_utc']=datetime.now(timezone.utc).isoformat();row['signing_revision']=row.get('signing_revision',0)+1;self._write(state)
    def complete(self,user,ident):
        row=self.public_job(user,ident)
        if not row['can_complete']:raise ValueError('Only the creator can complete the final stage; download the combined PSBT first for a multi-wallet transaction.')
        with storage_lock(self.config):
            state=self._read();current=state['jobs'][ident]
            if current['cancelled'] or current.get('completed'):raise ValueError('This transaction is already closed.')
            current['completed']=True;current['completed_utc']=datetime.now(timezone.utc).isoformat();current['signing_revision']=current.get('signing_revision',0)+1
            for notices in state['notices'].values():
                for n in notices:
                    if n.get('job_id')==ident:n['read']=True;n['completed']=True
            self._write(state)
        return {'completed':True}
    def cancel(self,user,ident,reason='reopened'):
        self.job(user,ident)
        with storage_lock(self.config):
            data=self._read();row=data['jobs'][ident]
            if row['creator_id']!=user or row.get('completed'):raise ValueError('Only the creator can reopen an active transaction for editing.')
            row['cancelled']=True;row['cancel_reason']=reason;row['cancelled_utc']=datetime.now(timezone.utc).isoformat();row['signing_revision']=row.get('signing_revision',0)+1
            for rows in data['notices'].values():
                for n in rows:
                    if n.get('job_id')==ident:n['read']=True
            self._write(data)
    def create(self,session,catalog,diagram):
        users={u['id'] for u in FamilyStore(self.config).snapshot()['users']};wallets={w['id']:w for w in catalog['wallets']}
        sources={e['from'] for e in diagram['edges'] if e.get('max') or int(e['amount_sats']) or e['fee_rate'].strip('0.')}
        participants=sorted({wallets[s].get('owner_id') for s in sources if s in wallets}-{None})
        if not participants or any(wallets[s].get('owner_id') not in users for s in sources):raise ValueError('Assign every sending wallet to a family user before preparing the transaction.')
        with storage_lock(self.config):base_cursors=dict(self._read()['change_cursors'])
        reserved=dict(base_cursors)
        result=construct(session,catalog,diagram['edges'],reserved)
        with storage_lock(self.config):
            data=self._read()
            for key,value in reserved.items():
                if value!=base_cursors.get(key,0) and data['change_cursors'].get(key,0)!=base_cursors.get(key,0):raise ValueError('Another transaction reserved a change address during preparation. Refresh the estimate and try again.')
            active={tuple(point) for job in data['jobs'].values() if not job.get('cancelled') and not job.get('completed') for point in job.get('outpoints',[])}
            if any(tuple(p) in active for p in result['outpoints']):raise ValueError('An input is already used by another prepared transaction. Reopen that transaction for editing before preparing a replacement.')
            now=datetime.now(timezone.utc).isoformat();ident=uuid.uuid4().hex
            row={'id':ident,'creator_id':session.user_id,'participants':participants,'created_utc':now,'cancelled':False,'agent_signing':True,**result,'wallets':catalog['wallets'],'diagram':{'nodes':copy.deepcopy(diagram['nodes']),'edges':result['edges']}}
            data['change_cursors'].update({k:v for k,v in reserved.items() if v!=base_cursors.get(k,0)})
            data['jobs'][ident]=row
            for user in sorted(set(participants+[session.user_id])):
                data['notices'].setdefault(user,[]).append({'id':uuid.uuid4().hex,'job_id':ident,'text':'Your actions are required to complete a Bitcoin Transaction','created_utc':now,'read':user==session.user_id})
            self._write(data)
        return row
