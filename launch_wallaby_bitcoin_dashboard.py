#!/usr/bin/env python3
"""Local Firefox GUI for Wallaby Bitcoin Dashboard. Standard-library HTTP bridge only."""
from ownership import saved_bitaroo_destinations
from ownership_workspace import save_workspace,restore_workspace
from datetime import datetime,timezone
from decimal import Decimal
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path,PurePosixPath
import secrets
import signal
import socket
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit,parse_qs
import webbrowser
from reporting import export_html,render_html,select_report_year
from report_store import ReportStore,signature as report_signature
from profile_archive import ProfileArchive,ProfileGate
from nodes import NodeRegistry,create_agent_server,DEFAULT_AGENT_PORT
from preferences import FamilyStore,MISSING_OWNERSHIP,MissingOwnership,resolve_ownership
from address_book import shared_address_book
from fees import FeeService,FeeUnavailable
from wallet_keys import analyse as analyse_keys
from descriptor_store import DescriptorStore
from node_wallets import NodeWalletLoader
from wallet_names import display_inventory,display_summary
from bitaroo_inputs import merge_saved_uploads
from family_inputs import account_slot,account_id,folder_slot,declarations
from matching_policy import matching_window, DEFAULT_WINDOW
from descriptor_snapshot import history_ready
from transaction_templates import TemplateStore,clean_diagram
from transaction_workflows import WorkflowStore
from transaction_builder import builder_catalog
from fee_planner import plan as estimate_fee_plan,live_coins

ROOT=Path(__file__).resolve().parent
SLOTS={'bitaroo':{'.pdf'},'bitaroo_history':{'.pdf'},'bitaroo_current':{'.pdf'},'historical':{'.ods'},'exchanges':{'.ods'},
       'quotes':{'.csv'},'cpi':{'.csv'},'tax_context':{'.json'}}
MAX_FILE=256*1024*1024
MISSING_CGT='Complete a CGT run after confirming Ownership to view wallet balances.'

class Session:
    def __init__(self,root,preferences_path=None):
        self.root=Path(root).expanduser().resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.preferences_path=Path(preferences_path).expanduser().resolve() if preferences_path else self.root/'config.cfg'
        FamilyStore(self.preferences_path)
        self.presence_seen=time.monotonic();self.presence_activity=self.presence_seen;self.presence_connected=False;self.token=secrets.token_urlsafe(32);self.lock=threading.RLock();self.events=[];self.pending=None
        self.status='idle';self.process=None;self.report=None;self.uploads={};self.settings={};self.last_job_error=None
        self.workspace_dirty=False;self.workspace_stamp=None;self.report_generation=None
        self.session_dir=self.root/('session-'+secrets.token_hex(8));self.session_dir.mkdir(mode=0o700)
        self.input_dir=self.session_dir/'inputs';self.input_dir.mkdir(mode=0o700)
        self.log_path=self.session_dir/'activity.log';self.log_path.touch(mode=0o600);self.active_operations=0;self.rpc_broker=None;self.rpc_logged=set();self.node_load_cancel=threading.Event();self.node_registry=None;self.node_load_lock=threading.Lock();self.profile_gate=ProfileGate()
        self.transaction_refresh_context=None;self.transaction_refresh_scanned=False;self.ownership_completed_utc=None;self.descriptor_fingerprint=None;self.bitaroo_as_of_date=None;self.restore_error='';self.sequence=0;self.activity_started=False;self.closed=False;self.user_id=None;self.mode='cgt';self.job_result=None;self.inventory=[];self.inventory_ready=False;self.address_book=None;self.report=None;self.report_context=None;self.invalidate_transaction_refresh()
    def user(self):
        return FamilyStore(self.preferences_path).user(self.user_id) if self.user_id else None
    def sync_workspace(self,data):
        if not self.user_id or self.workspace_dirty or self.status in {'running','waiting'}:return
        meta=data.get('ownership_runs',{}).get(self.user_id)
        stamp=meta.get('data_id') if meta else None
        if stamp==self.workspace_stamp:return
        self.inventory_ready=False;self.report=None;self.report_context=None;self.report_generation=None
        if not meta:self.workspace_stamp=None;return
        try:
            restored=restore_workspace(self.preferences_path,self.user_id,self.input_dir)
            if restored:
                meta,self.uploads=restored;merge_saved_uploads(self.uploads,self.input_dir)
                self.inventory=meta['inventory'];self.inventory_ready=True;self.address_book=meta.get('address_book');self.settings=meta['settings']
                self.ownership_completed_utc=meta['completed_utc'];self.descriptor_fingerprint=meta.get('descriptor_fingerprint');self.bitaroo_as_of_date=meta.get('bitaroo_as_of_date');self.restore_error=''
                if 'bitaroo' in self.uploads:self.uploads[account_slot(self.user_id)]=self.uploads.pop('bitaroo')
                if not meta.get('family_accounts'):self.inventory_ready=False;self.restore_error='Run family-wide Ownership again to refresh the Bitaroo declarations.'
                self.workspace_stamp=stamp
        except ValueError as error:self.restore_error=str(error)
    def access(self,snapshot=None):
        data=snapshot or FamilyStore(self.preferences_path).snapshot()
        user=next((u for u in data['users'] if u['id']==self.user_id),None)
        self.sync_workspace(data)
        descriptor_store=DescriptorStore(self.preferences_path)
        descriptors=descriptor_store.summary();descriptors['ready']=descriptors['ready'] and history_ready(self.preferences_path,descriptor_store.read())
        context=None
        if user and descriptors['ready'] and self.descriptor_fingerprint==descriptors['fingerprint'] and self.inventory_ready and self.inventory:
            try:
                resolved=resolve_ownership(data,self.inventory)
                fields=('id','kind','source','name','owner_id','external','smsf','confirmed')
                rows=[{k:resolved[key].get(k) for k in fields} for key in sorted(resolved)]
                context=hashlib.sha256(json.dumps([user['id'],user['name'],rows,saved_bitaroo_destinations(data)],sort_keys=True,ensure_ascii=True).encode()).hexdigest()
            except MissingOwnership:pass
        if context is not None:
            try:declarations(data['users'],self.settings,self.uploads)
            except ValueError:context=None
        ready=context is not None
        if ready and self.status not in {'running','waiting'}:
            generation,saved=ReportStore(self.preferences_path).load(self.user_id,report_signature(data,descriptors['fingerprint']),self.report_generation if self.report_context==context and self.report else None)
            if isinstance(saved,dict):self.report=saved;self.report_context=context
            elif saved is None:self.report=None;self.report_context=None
            self.report_generation=generation
        report_ready=ready and self.report is not None and self.report_context==context
        return {'descriptors_ready':descriptors['ready'],'bitaroo_as_of_date':self.bitaroo_as_of_date,'ownership_completed_utc':self.ownership_completed_utc,'activity_started':bool(user) and self.activity_started,'process_active':bool(user) and (self.status in {'running','waiting'} or self.active_operations>0 or self.rpc_broker is not None and self.rpc_broker.pending(self.session_dir.name+':'+self.user_id)),'ownership_ready':ready,'templates_ready':ready,'report_ready':report_ready,'report_generation':self.report_generation,'ownership_context':context}
    def invalidate_transaction_refresh(self):
        self.transaction_refresh_context=None;self.transaction_refresh_scanned=False
    def confirm_transaction_refresh(self):
        if self.access()['ownership_ready']:
            self.transaction_refresh_context=self.access()['ownership_context']
            
    def family(self):
        data=FamilyStore(self.preferences_path).snapshot();access=self.access(data);user=next((u for u in data['users'] if u['id']==self.user_id),None)
        active=WorkflowStore(self.preferences_path).active(self.user_id) if user else []
        latest=max(active,key=lambda row:row['created_utc'])['id'] if active else None
        return {'last_job_error':self.last_job_error,'active_transaction':latest,'users':data['users'],'revision':data['revision'],'ownership':data['ownership'],'user':user,
                'descriptors':dict(display_summary(DescriptorStore(self.preferences_path).summary(),data),ready=access['descriptors_ready']),'inventory':display_inventory(self.inventory,DescriptorStore(self.preferences_path).read()) if user else [],'inventory_ready':self.inventory_ready if user else False,
                **access,'address_book':shared_address_book(data,self.address_book) if access['ownership_ready'] else None,
                'restore_error':self.restore_error,'settings':self.settings if user else {},'loaded_slots':{slot:([p.name for p in sorted(path.rglob('*')) if p.is_file()] if path.is_dir() else [path.name]) for slot,path in self.uploads.items()} if user else {}}
    def select_user(self,user_id):
        with self.lock:
            if self.status in {'running','waiting'}:raise ValueError('Cancel the active job before switching users.')
            user=FamilyStore(self.preferences_path).user(user_id) if user_id else None
            if user_id and not user:raise ValueError('User no longer exists.')
            self.cancel();self.last_job_error=None;self.user_id=user_id;self.presence_seen=time.monotonic();self.presence_activity=self.presence_seen;self.presence_connected=bool(user_id);self.report=None;self.job_result=None;self.events=[];self.pending=None;self.status='idle';self.sequence=0;self.activity_started=False;self.inventory=[];self.inventory_ready=False;self.address_book=None;self.report=None;self.report_context=None;self.invalidate_transaction_refresh()
            self.transaction_refresh_context=None;self.transaction_refresh_scanned=False;self.ownership_completed_utc=None;self.descriptor_fingerprint=None;self.bitaroo_as_of_date=None;self.restore_error='';self.uploads={};self.settings={};shutil.rmtree(self.input_dir,ignore_errors=True);self.input_dir.mkdir(parents=True,exist_ok=True)
            self.workspace_dirty=False;self.workspace_stamp=None;self.report_generation=None
            return self.family()
    def close(self):
        with self.lock:
            if self.closed:return
            self.cancel();self.closed=True
            if self.process:
                try:self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
            shutil.rmtree(self.session_dir,ignore_errors=True)
    def emit(self,data):
        with self.lock:
            self.sequence+=1;event=dict(data,sequence=self.sequence);self.events.append(event)
            self.write_log(data)
            if data['type']=='prompt':self.pending=event;self.status='waiting'
            elif data['type']=='error':self.pending=None;self.status='error';self.last_job_error={'mode':self.mode,'message':str(data.get('message','The job stopped.'))}
            elif data['type']=='complete':self.pending=None;self.status='complete'
    def write_log(self,data):
        text=data.get('text') or data.get('message') or data.get('label') or data.get('prompt') or data['type']
        if data.get('context'):text=data['context']+'\n'+text
        user=self.user();stamp=datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M:%S UTC')
        with self.log_path.open('a',encoding='utf-8') as log:log.write(stamp+' · '+(user['name'] if user else 'Dashboard')+' · '+str(text)+'\n')
    def record_activity(self,message,user_id=None):
        with self.lock:
            if self.closed or user_id is not None and self.user_id!=user_id:return
            self.activity_started=True
            self.emit({'type':'log','text':message})
    def upload(self,slot,name,body):
        if slot not in SLOTS and account_id(slot) is None:raise ValueError('Unknown input type')
        if account_id(slot) and not FamilyStore(self.preferences_path).user(account_id(slot)):raise ValueError('Unknown Bitaroo account user')
        path=PurePosixPath(name)
        if path.is_absolute() or not path.parts or any(p in {'..','.'} for p in path.parts) or '\\' in name or '\x00' in name:raise ValueError('Invalid input filename')
        if path.suffix.lower() not in SLOTS.get(slot,{'.pdf'}):raise ValueError('This file type does not belong in this input')
        if not folder_slot(slot) and len(path.parts)!=1:raise ValueError('Select one input file')
        with self.lock:
            if self.closed:raise ValueError('This browser workspace is closed')
            if self.status in {'running','waiting'}:raise ValueError('Wait for the current calculation before changing inputs')
            dest=self.input_dir/slot/Path(*path.parts);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(body)
            self.write_log({'type':'log','text':'Reading '+name})
            self.uploads[slot]=self.input_dir/slot if folder_slot(slot) else dest
            self.workspace_dirty=True;self.inventory_ready=False;self.address_book=None;self.report=None;self.report_context=None;self.invalidate_transaction_refresh()
        return {'name':name,'bytes':len(body)}
    def clear_slot(self,slot):
        if slot not in SLOTS and account_id(slot) is None:raise ValueError('Unknown input type')
        if account_id(slot) and not FamilyStore(self.preferences_path).user(account_id(slot)):raise ValueError('Unknown Bitaroo account user')
        with self.lock:
            if self.closed:raise ValueError('This browser workspace is closed')
            if self.status in {'running','waiting'}:raise ValueError('Calculation is still active')
            shutil.rmtree(self.input_dir/slot,ignore_errors=True);self.uploads.pop(slot,None);self.workspace_dirty=True;self.inventory_ready=False;self.address_book=None;self.report=None;self.report_context=None;self.invalidate_transaction_refresh()
    def start_node_wallets(self):
        with self.lock:
            if self.closed:raise ValueError('This browser workspace is closed')
            if not self.user():raise ValueError('Select a family user first.')
            if self.status in {'running','waiting'}:raise ValueError('Wait for the active run before loading node wallets.')
            if not self.node_load_lock.acquire(blocking=False):raise ValueError('Another browser is already loading node wallets.')
            self.node_load_cancel=threading.Event();cancel=self.node_load_cancel;self.last_job_error=None
            self.events=[];self.pending=None;self.activity_started=True;self.status='running';self.mode='descriptors';self.job_result=None
            self.report=None;self.report_context=None;self.workspace_dirty=True;self.invalidate_transaction_refresh()
        def load():
            try:
                loader=NodeWalletLoader(self.node_registry,lambda text:self.record_activity(text),cancel)
                wallets=loader.collect()
                with self.profile_gate.access():
                    with self.lock:
                        if cancel.is_set() or self.closed:return
                        DescriptorStore(self.preferences_path).replace_node_wallets(wallets)
                        self.status='idle';self.start_descriptor_scan(preserve_events=True)
            except Exception as exc:
                with self.lock:
                    if not cancel.is_set() and not self.closed:self.emit({'type':'error','message':str(exc)})
            finally:self.node_load_lock.release()
        threading.Thread(target=load,daemon=True).start()

    def start_descriptor_scan(self,preserve_events=False):
        with self.lock:
            if self.status in {'running','waiting'}:raise ValueError('Wait for the active run before scanning descriptors.')
            ds=DescriptorStore(self.preferences_path)
            if not self.user() or not ds.summary()['ready']:raise ValueError('Run Load Node Wallets in Ownership first.')
            self.workspace_dirty=True;self.invalidate_transaction_refresh()
            if not preserve_events:self.events=[]
            self.pending=None;self.activity_started=True;self.status='running';self.mode='descriptors';self.job_result=None
            self.report=None;self.report_context=None
            self.result_path=self.session_dir/'report.json';self.result_path.unlink(missing_ok=True)
            config={'mode':'descriptors','user':self.user(),'family_snapshot':FamilyStore(self.preferences_path).snapshot(),'descriptor_catalog':ds.read(),'settings':{},'paths':{},'preferences':str(self.preferences_path),'cache':str(self.root/'historical-cache.sqlite3'),'result':str(self.result_path)}
            path=self.session_dir/'run.json';path.write_text(json.dumps(config))
            try:
                self.process=subprocess.Popen([sys.executable,str(ROOT/'worker.py'),str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',env=dict(os.environ,PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1'),cwd=self.session_dir)
            except OSError:
                self.status='error';raise
            threading.Thread(target=self.consume,args=(self.process,),daemon=True).start()
    def start(self,settings,mode='cgt'):
        with self.lock:
            if self.closed:raise ValueError('This browser workspace is closed')
            if self.status in {'running','waiting'}:raise ValueError('A calculation is already active')
            self.last_job_error=None
            user=self.user()
            if not user:raise ValueError('Select a family user first.')
            if mode=='balances':raise ValueError(MISSING_CGT+' Rebuild the CGT report to refresh its balances.')
            if mode not in ('cgt','inventory'):raise ValueError('Unknown job type.')
            snapshot=FamilyStore(self.preferences_path).snapshot()
            access=self.access(snapshot)
            if mode=='cgt' and not access['ownership_ready']:raise ValueError(MISSING_OWNERSHIP)
            self.run_context=access['ownership_context']
            self.run_refresh=False
            descriptor_store=DescriptorStore(self.preferences_path)
            if not descriptor_store.summary()['ready'] or not history_ready(self.preferences_path,descriptor_store.read()):raise ValueError('Wallet Descriptors must be run first before Ownership identification can be executed.')
            window=matching_window(settings.get('matching_window',DEFAULT_WINDOW))
            refresh=settings.get('refresh_descriptor_history',False)
            if not isinstance(refresh,bool):raise ValueError('Descriptor refresh must be Yes or No.')
            self.run_refresh=mode=='inventory' and refresh
            zone=settings.get('browser_timezone') or self.settings.get('browser_timezone') or 'UTC'
            merge_saved_uploads(self.uploads,self.input_dir)
            declarations(snapshot['users'],dict(self.settings,**settings),self.uploads)
            required=('historical','exchanges')
            if mode=='cgt':required+=('chain',)
            for slot in required:
                if slot not in self.uploads:raise ValueError('Select the required files in Ownership first.')
            if mode=='inventory' and 'opening_losses_by_user' not in settings:
                settings=dict(settings,opening_losses_by_user={user['id']:settings.get('carried_losses','0')})
            if mode=='inventory':self.settings={k:v for k,v in settings.items() if k not in {'taxpayer','financial_year','mode','matching_window','refresh_descriptor_history'}}
            settings=dict(self.settings,financial_year=settings.get('financial_year',''),taxpayer=user['name'],matching_window=window['key'],refresh_descriptor_history=refresh,display_timezone=zone)
            self.events=[];self.pending=None;self.activity_started=True;self.status='running';self.mode=mode;self.job_result=None
            if mode=='cgt':self.report=None;self.report_context=None
            if mode=='inventory':self.inventory_ready=False;self.address_book=None;self.report=None;self.report_context=None;self.invalidate_transaction_refresh()
            if mode=='cgt':
                self.run_signature=report_signature(snapshot,descriptor_store.summary()['fingerprint'])
                self.report_generation=ReportStore(self.preferences_path).begin(self.run_signature)
            self.result_path=self.session_dir/'report.json'
            self.result_path.unlink(missing_ok=True)
            config={'mode':mode,'user':user,'family_snapshot':snapshot,'ownership_completed_utc':self.ownership_completed_utc,'bitaroo_as_of_date':self.bitaroo_as_of_date,'descriptor_catalog':descriptor_store.read(),'descriptor_fingerprint':descriptor_store.summary()['fingerprint'],'settings':settings,'paths':{k:str(v) for k,v in self.uploads.items()},
                    'preferences':str(self.preferences_path),'cache':str(self.root/'historical-cache.sqlite3'),'result':str(self.result_path)}
            config_path=self.session_dir/'run.json';config_path.write_text(json.dumps(config))
            env=dict(os.environ,PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1')
            self.process=subprocess.Popen([sys.executable,str(ROOT/'worker.py'),str(config_path)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',env=env,cwd=self.session_dir)
            threading.Thread(target=self.consume,args=(self.process,),daemon=True).start()
    def consume(self,process):
        for line in process.stdout:
            try:data=json.loads(line)
            except ValueError:data={'type':'log','text':line.rstrip()}
            if data.get('type')=='complete' and process is self.process and self.status!='cancelled':
                try:
                    completed=json.loads(self.result_path.read_text())
                    if self.mode=='inventory':
                        book=completed.get('address_book')
                        if book:
                            for entry in book['entries']:
                                if entry.get('kind')=='bitaroo':FamilyStore(self.preferences_path).save_bitaroo_address(entry['source'],entry,book['generated_utc'])
                        self.inventory=completed['inventory'];self.inventory_ready=True;self.address_book=book
                        chain_path=Path(completed['chain_path'])
                        if chain_path!=self.result_path.with_name('descriptor_history.json') or not chain_path.is_file():raise ValueError('The descriptor history scan did not complete.')
                        self.uploads['chain']=chain_path
                        chain=json.loads(chain_path.read_text());self.descriptor_fingerprint=chain['catalog_fingerprint'];self.bitaroo_as_of_date=completed['bitaroo_as_of_date']
                        if {'historical','exchanges','chain'}.issubset(self.uploads):
                            for profile in FamilyStore(self.preferences_path).snapshot()['users']:
                                uid=profile['id'];coverage=completed.get('coverage_by_user',{}).get(uid,{})
                                saved=save_workspace(self.preferences_path,uid,self.uploads,self.inventory,book,self.settings,metadata={'descriptor_fingerprint':self.descriptor_fingerprint,'bitaroo_as_of_date':coverage.get('bitaroo_as_of_date'),'bitaroo_data':completed.get('bitaroo_data_by_user',{}).get(uid,{}),'historical_balances':completed.get('historical_balances',{}),'family_accounts':completed.get('family_accounts',{})})
                            self.ownership_completed_utc=saved['completed_utc'];self.restore_error='';self.workspace_dirty=False;self.workspace_stamp=None
                            with FamilyStore(self.preferences_path).transaction() as family_data:
                                family_data['bitaroo_accounts']=completed.get('family_accounts',{})
                                for uid,active in family_data['bitaroo_accounts'].items():
                                    if not active:family_data.get('bitaroo_addresses',{}).pop(uid,None)
                        if self.run_refresh:
                            self.transaction_refresh_scanned=True;self.confirm_transaction_refresh()
                    self.job_result=completed
                    if self.mode=='cgt':
                        snapshot=FamilyStore(self.preferences_path).snapshot()
                        current=report_signature(snapshot,DescriptorStore(self.preferences_path).summary()['fingerprint'])
                        if current!=self.run_signature:raise ValueError('Ownership or source records changed during this CGT run. Rebuild the report.')
                        ReportStore(self.preferences_path).publish(self.report_generation,self.run_signature,completed,snapshot['users'])
                        self.report=None;self.report_context=None
                except Exception as exc:data={'type':'error','message':'Could not read completed report: '+str(exc)}
            with self.lock:
                if process is self.process and self.status!='cancelled':self.emit(data)
        code=process.wait()
        with self.lock:
            if process is self.process and self.status not in {'complete','error','cancelled'}:
                self.emit({'type':'error','message':f'Calculation stopped (exit {code}). See the activity log for details.'})
    def answer(self,sequence,answer):
        with self.lock:
            if not self.pending or self.pending['sequence']!=sequence:raise ValueError('This question is no longer waiting for an answer')
            value=str(answer)
            if len(value)>(16384 if self.pending.get('kind') in {'ranking','asset_basis'} else 1000):raise ValueError('Answer is too long')
            self.process.stdin.write(json.dumps({'answer':value})+'\n');self.process.stdin.flush()
            self.pending=None;self.status='running'
    def cancel(self):
        with self.lock:
            self.node_load_cancel.set()
            active=self.status in {'running','waiting'} or self.process is not None and self.process.poll() is None
            if self.process and self.process.poll() is None:self.process.terminate()
            self.status='cancelled';self.pending=None
            if active:self.emit({'type':'cancelled'})


def loopback_authority(authority):
    """Accept loopback browser endpoints on any valid client-side tunnel port."""
    import re
    match=re.fullmatch(r'(127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?',authority or '')
    return bool(match and (match[2] is None or 1<=int(match[2])<=65535))


class Sessions:
    """Automatically issue isolated browser workspaces to SSH-reachable clients."""
    def __init__(self,root,preferences_path=None,max_sessions=32,idle_seconds=86400):
        self.root=Path(root).expanduser().resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.preferences_path=Path(preferences_path).expanduser().resolve() if preferences_path else self.root/'config.cfg'
        FamilyStore(self.preferences_path)
        self.sessions={};self.user_activity={};self.lock=threading.RLock();self.profile_gate=ProfileGate()
        self.max_sessions=max_sessions;self.idle_seconds=idle_seconds
        self.nodes=NodeRegistry(self.preferences_path);self.node_load_lock=threading.Lock()
    def create(self):
        self.expire()
        with self.lock:
            if len(self.sessions)>=self.max_sessions:raise ValueError('All browser workspaces are in use. Close an unused workspace and try again.')
            session=Session(self.root,self.preferences_path);session.rpc_broker=self.nodes.rpc;session.node_registry=self.nodes;session.node_load_lock=self.node_load_lock;session.profile_gate=self.profile_gate;session.last_seen=time.monotonic();self.sessions[session.token]=session;return session
    def get(self,token):
        with self.lock:
            session=self.sessions.get(token or '')
            if session:session.last_seen=time.monotonic()
            return session
    def close(self,token):
        with self.lock:
            session=self.sessions.pop(token,None)
            if session and session.user_id:
                self.user_activity[session.user_id]=max(self.user_activity.get(session.user_id,0),session.presence_activity)
        if session:session.close()
    def expire(self):
        now=time.monotonic()
        with self.lock:
            expired=[key for key,s in self.sessions.items() if now-s.last_seen>self.idle_seconds and s.status!='running']
        for key in expired:self.close(key)
    def presence(self):
        now=time.monotonic()
        with self.lock:
            connected=[s for s in self.sessions.values() if not s.closed and s.presence_connected and now-s.presence_seen<90]
            online={s.user_id for s in connected}
            for s in self.sessions.values():
                if s.user_id and not s.closed:self.user_activity[s.user_id]=max(self.user_activity.get(s.user_id,0),s.presence_activity)
            self.user_activity={uid:stamp for uid,stamp in self.user_activity.items() if now-stamp<300}
            active=set(self.user_activity)
        return {'users':[dict(u,online=u['id'] in online,presence='active' if u['id'] in active else 'idle' if u['id'] in online else 'offline') for u in FamilyStore(self.preferences_path).snapshot()['users']]}
    def close_all(self):
        with self.lock:keys=list(self.sessions)
        for key in keys:self.close(key)


def create_server(state_dir,port=0,preferences_path=None,max_sessions=32,idle_seconds=86400,ipv6=True,ipv4=True):
    sessions=Sessions(state_dir,preferences_path,max_sessions,idle_seconds)
    fee_service=FeeService()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def json(self,body,status=200):
            raw=json.dumps(body,ensure_ascii=True).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(raw)
        def safe_endpoint(self):
            hosts=self.headers.get_all('Host',[])
            if len(hosts)!=1 or not loopback_authority(hosts[0]):return False
            origins=self.headers.get_all('Origin',[])
            return not origins or len(origins)==1 and origins[0]=='http://'+hosts[0]
        def authorized(self):
            if not self.safe_endpoint():return None
            return sessions.get(self.headers.get('X-Session-Token'))
        def notices(self,session):
            result=WorkflowStore(session.preferences_path).notifications(session.user_id,session.session_dir.name)
            result['notifications']+=sessions.nodes.notifications(session.user_id)
            result['notifications'].sort(key=lambda n:n['created_utc'],reverse=True)
            result['unread_count']=sum(not n['read'] for n in result['notifications'])
            return result
        def do_GET(self):
            exclusive=urlsplit(self.path).path=='/api/profile/save'
            with sessions.profile_gate.access(exclusive):
                session=self.authorized() if urlsplit(self.path).path=='/api/fees' else None
                if session:
                    with session.lock:session.active_operations+=1
                try:self.handle_get()
                finally:
                    if session:
                        with session.lock:session.active_operations-=1
        def handle_get(self):
            path=urlsplit(self.path).path
            if not self.safe_endpoint():return self.json({'error':'This browser endpoint is not authorised'},403)
            if path.startswith('/api/'):
                # This remains public without a workspace. A logged-in browser may
                # optionally record generic activity; no private data enters the quote.
                if path=='/api/fees':
                    fee_session=sessions.get(self.headers.get('X-Session-Token'))
                    activity_user=fee_session.user_id if fee_session and fee_session.user() else None
                    if activity_user:fee_session.record_activity('Fee Estimates: refresh started.',activity_user)
                    try:quote=fee_service.snapshot(force=True)
                    except FeeUnavailable as exc:
                        if activity_user:fee_session.record_activity('Fee Estimates: refresh unavailable.',activity_user)
                        return self.json({'error':str(exc)},503)
                    if activity_user:fee_session.record_activity('Fee Estimates: refresh complete.',activity_user)
                    return self.json(quote)
                session=self.authorized()
                if not session:return self.json({'error':'This browser workspace is not authorised'},403)
                if path=='/api/logs':
                    with session.lock:body=session.log_path.read_bytes()
                    self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Content-Disposition','attachment; filename="Wallaby_Dashboard.log"');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body);return
                if path=='/api/nodes/rpc/result':
                    if not session.user():return self.json({'error':'Select a family user first.'},400)
                    try:
                        state=sessions.nodes.rpc.status(parse_qs(urlsplit(self.path).query).get('id',[''])[0],session.session_dir.name+':'+session.user_id)
                        if state['state'] not in ('queued','delivered','running') and state['id'] not in session.rpc_logged:
                            session.rpc_logged.add(state['id']);session.record_activity('Nodes: '+state['method']+' '+state['state']+' ('+state['id']+').',session.user_id)
                        return self.json(state)
                    except ValueError as exc:return self.json({'error':str(exc)},400)
                if path=='/api/nodes':
                    if not session.user():return self.json({'error':'Select a family user first.'},400)
                    state=sessions.nodes.snapshot();sessions.nodes.notifications(session.user_id,read=True)
                    return self.json(state)
                if path=='/api/profile/save':
                    try:
                        if any(s.status in {'running','waiting'} for s in list(sessions.sessions.values())):raise ValueError('Finish or cancel active processes before saving a profile.')
                        body=ProfileArchive(session.preferences_path).save()
                    except (ValueError,OSError) as exc:return self.json({'error':str(exc)},400)
                    self.send_response(200);self.send_header('Content-Type','application/x-7z-compressed');self.send_header('Content-Disposition','attachment; filename="profile.7z"');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body);return
                if path=='/api/presence':
                    if not session.user():return self.json({'error':'Select a family user first.'},400)
                    session.presence_seen=time.monotonic();session.presence_connected=True
                    # Heartbeats establish connectivity, not user interaction.
                    age=parse_qs(urlsplit(self.path).query).get('activity_age_ms',[None])[0]
                    if age is not None:
                        try:age=int(age)
                        except ValueError:return self.json({'error':'Invalid activity age'},400)
                        if age<0:return self.json({'error':'Invalid activity age'},400)
                        session.presence_activity=max(session.presence_activity,session.presence_seen-min(age/1000,86400*365))
                    return self.json(sessions.presence())
                if path=='/api/state':
                    try:after=int(parse_qs(urlsplit(self.path).query).get('after',['0'])[0])
                    except ValueError:return self.json({'error':'Invalid event cursor'},400)
                    with session.lock:return self.json({'last_job_error':session.last_job_error,'status':session.status,'events':[e for e in session.events if e['sequence']>after],
                                                        'pending':session.pending,**session.access(),'user':session.user(),'mode':session.mode,'job_ready':session.job_result is not None and (session.mode!='cgt' or session.access()['report_ready'])})
                if path in ('/api/notifications','/api/transactions','/api/transaction','/api/transaction/psbt','/api/transaction/status','/api/transaction/combined'):
                    if not session.user():return self.json({'error':'Select a family user first.'},400)
                    workflows=WorkflowStore(session.preferences_path)
                    try:
                        if path=='/api/notifications':
                            params=parse_qs(urlsplit(self.path).query)
                            workflows.sync_run_warning(session.user_id,params.get('local_date',[''])[0],params.get('timezone',['UTC'])[0],params.get('page',[''])[0])
                            if params.get('page',[''])[0]=='nodes':sessions.nodes.notifications(session.user_id,read=True)
                            return self.json(self.notices(session))
                        if path=='/api/transactions':return self.json({'transactions':workflows.active(session.user_id)})
                        ident=parse_qs(urlsplit(self.path).query).get('id',[None])[0];job=workflows.job(session.user_id,ident)
                        if path=='/api/transaction':return self.json(workflows.public_job(session.user_id,ident))
                        if path=='/api/transaction/status':return self.json(workflows.status(session.user_id,ident))
                        if job['cancelled'] or job.get('completed'):raise ValueError('This transaction is closed. Use Transaction Templates to prepare another transaction.')
                        import base64
                        combined=path=='/api/transaction/combined';body=base64.b64decode(workflows.combined(session.user_id,ident) if combined else job['psbt']);
                        if combined:workflows.record_combined_download(session.user_id,ident)
                        self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename="Wallaby_'+('combined_' if combined else 'unsigned_')+ident+'.psbt"');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body);return
                    except (ValueError,OSError) as exc:return self.json({'error':str(exc)},400)
                if path=='/api/templates':
                    if not session.access()['templates_ready']:return self.json({'error':'Complete Ownership and save all assignments first.'},400)
                    templates=TemplateStore(session.preferences_path)
                    ident=parse_qs(urlsplit(self.path).query).get('id',[None])[0]
                    try:
                        if ident and not session.access()['templates_ready']:raise ValueError('Complete Ownership and save all assignments before loading a template.')
                        return self.json(templates.read(ident) if ident else {'templates':templates.listing()})
                    except (ValueError,OSError) as exc:return self.json({'error':str(exc)},400)
                if path=='/api/builder':
                    if not session.access()['templates_ready']:return self.json({'error':'Complete Ownership and save all assignments first.'},400)
                    try:price=fee_service.snapshot().get('price')
                    except FeeUnavailable:price=None
                    return self.json(builder_catalog(session,price))
                if path=='/api/family':return self.json(session.family())
                if path=='/api/job':return self.json((session.report if session.mode=='cgt' else session.job_result) if session.job_result is not None and (session.mode!='cgt' or session.access()['report_ready']) else {},200 if session.job_result is not None and (session.mode!='cgt' or session.access()['report_ready']) else 404)
                if path=='/api/report':return self.json(session.report if session.access()['report_ready'] else {},200 if session.access()['report_ready'] else 404)
                if path=='/api/export' and session.access()['report_ready']:
                    try:personal=select_report_year(session.report,parse_qs(urlsplit(self.path).query).get('year',[None])[0])
                    except ValueError as exc:return self.json({'error':str(exc)},400)
                    body=export_html(personal).encode();self.send_response(200)
                    self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)))
                    self.send_header('Content-Disposition',f'attachment; filename="Wallaby_Bitcoin_Dashboard_{personal["financial_year"]}.html"')
                    self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body);return
                return self.json({'error':'No completed report is available'},404)
            files={'/':'index.html','/assets/builder_values.js':'assets/builder_values.js','/assets/builder.js':'assets/builder.js','/assets/style.css':'assets/style.css','/assets/app.js':'assets/app.js','/assets/history.js':'assets/history.js','/assets/fees.js':'assets/fees.js','/assets/wallet_keys.js':'assets/wallet_keys.js','/assets/console_json.js':'assets/console_json.js','/assets/family.js':'assets/family.js','/assets/descriptors.js':'assets/descriptors.js','/assets/logo.svg':'assets/logo.svg'}
            if path not in files:return self.json({'error':'Not found'},404)
            file=ROOT/files[path]
            body=render_html().encode() if path=='/' else file.read_bytes();self.send_response(200)
            self.send_header('Content-Type',('text/html' if path=='/' else mimetypes.guess_type(str(file))[0])+'; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY')
            self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def do_POST(self):
            exclusive=urlsplit(self.path).path in ('/api/profile/load','/api/profile/clear')
            with sessions.profile_gate.access(exclusive):
                session=self.authorized() if urlsplit(self.path).path in ('/api/keys/analyse','/api/descriptors/load','/api/descriptors/history') else None
                if session:
                    with session.lock:session.active_operations+=1
                try:self.handle_post()
                finally:
                    if session:
                        with session.lock:session.active_operations-=1
        def handle_post(self):
            if not self.safe_endpoint():return self.json({'error':'This browser endpoint is not authorised'},403)
            path=urlsplit(self.path).path
            if path=='/api/session':
                # Explicit browser header plus Host/Origin checks prevents cross-site form bootstrap.
                if self.headers.get('X-Wallaby-Request')!='1':return self.json({'error':'A same-origin dashboard request is required'},403)
                try:session=sessions.create()
                except ValueError as exc:return self.json({'error':str(exc)},429)
                return self.json({'token':session.token})
            session=self.authorized()
            if not session:return self.json({'error':'This browser workspace is not authorised'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if path in ('/api/keys/analyse','/api/descriptors/load','/api/descriptors/history') and length>4*1024*1024+10000:raise ValueError('Descriptor request is too large (maximum 4 MB of descriptor text)')
                if path=='/api/nodes/rpc' and length>4*1024*1024+4096:raise ValueError('Node RPC request is too large.')
                if path=='/api/messages/send' and length>20000:raise ValueError('Message request is too large.')
                if path=='/api/transaction/upload' and length>12*1024*1024:raise ValueError('Select a signed PSBT file up to 8 MB.')
                if length<0 or length>MAX_FILE:raise ValueError('File is too large (maximum 256 MB per file)')
                body=self.rfile.read(length)
                if len(body)!=length:raise ValueError('Incomplete upload')
                if path in ('/api/profile/load','/api/profile/clear'):
                    if any(s.status in {'running','waiting'} for s in list(sessions.sessions.values())):raise ValueError('Finish or cancel active processes before replacing or clearing the shared profile.')
                    manager=ProfileArchive(session.preferences_path)
                    if path=='/api/profile/load':manager.load(parse_qs(urlsplit(self.path).query).get('name',[''])[0],body)
                    else:manager.replace()
                    sessions.close_all();sessions.user_activity.clear();sessions.nodes.reset()
                    return self.json({'reset':True})
                if path.startswith('/api/upload/'):

                    slot=path.rsplit('/',1)[1];name=parse_qs(urlsplit(self.path).query).get('name',[''])[0]
                    return self.json(session.upload(slot,name,body))
                data=json.loads(body or b'{}',parse_float=Decimal) if path=='/api/nodes/rpc' else json.loads(body or b'{}')
                store=FamilyStore(session.preferences_path)
                if path=='/api/nodes/rpc':
                    if not session.user():raise ValueError('Select a family user first.')
                    if not isinstance(data,dict) or not {'node_id','method'}<=set(data) or set(data)-{'node_id','method','params','wallet','timeout_seconds'}:raise ValueError('Invalid node RPC request.')
                    queued=sessions.nodes.rpc.queue(data['node_id'],data['method'],data.get('params',[]),data.get('wallet'),data.get('timeout_seconds',30),session.session_dir.name+':'+session.user_id)
                    session.record_activity('Nodes: queued '+queued['method']+' ('+queued['id']+').',session.user_id)
                    return self.json(queued,202)
                if path=='/api/presence/leave':
                    session.presence_connected=False
                    return self.json({'ok':True})
                if path=='/api/messages/send':
                    if not session.user():raise ValueError('Select a family user first.')
                    return self.json(WorkflowStore(session.preferences_path).send_message(session.user_id,data.get('user_id'),data.get('text')))
                if path=='/api/login':return self.json(session.select_user(data.get('user_id')))
                if path=='/api/logout':return self.json(session.select_user(None))
                if path=='/api/users/save':
                    store.save_user(data.get('name',''),data.get('colour','green'),data.get('id'),data.get('revision'),gender=data.get('gender'));return self.json(session.family())
                if path in ('/api/users/delete','/api/config/reset'):
                    target=data.get('id')
                    if any(s.status in {'running','waiting'} and (path=='/api/config/reset' or s.user_id==target) for s in list(sessions.sessions.values())):raise ValueError('Cancel active jobs for affected users before deleting or resetting.')
                    if path=='/api/config/reset':store.reset(data.get('revision'))
                    else:store.delete_user(target,data.get('revision'))
                    WorkflowStore(session.preferences_path).prune_users([u['id'] for u in store.snapshot()['users']])
                    if path=='/api/config/reset':DescriptorStore(session.preferences_path).path.unlink(missing_ok=True)
                    for s in list(sessions.sessions.values()):
                        if s.user_id and not s.user():s.select_user(None)
                    return self.json(session.family())
                if path=='/api/descriptors/nodes':
                    session.start_node_wallets()
                    return self.json(session.family())
                if path=='/api/descriptors/load':
                    if not session.user():raise ValueError('Select a family user first.')
                    if session.status in {'running','waiting'}:raise ValueError('Wait for the active run before changing descriptors.')
                    session.record_activity('Wallet Descriptors: validating public exports.',session.user_id)
                    try:DescriptorStore(session.preferences_path).load(data.get('text',''))
                    except Exception:
                        session.record_activity('Wallet Descriptors: import failed.',session.user_id);raise
                    session.report=None;session.report_context=None
                    session.record_activity('Wallet Descriptors: public wallets saved.',session.user_id)
                    if DescriptorStore(session.preferences_path).summary()['ready']:session.start_descriptor_scan()
                    return self.json(session.family())
                if path=='/api/descriptors/history':
                    if not session.user():raise ValueError('Select a family user first.')
                    if session.status in {'running','waiting'}:raise ValueError('Wait for the active run before changing wallet history.')
                    DescriptorStore(session.preferences_path).load_history(data.get('wallet_id'),data.get('text',''))
                    session.report=None;session.report_context=None
                    session.record_activity('Wallet Descriptors: supplemental public history saved.',session.user_id)
                    if DescriptorStore(session.preferences_path).summary()['ready']:session.start_descriptor_scan()
                    return self.json(session.family())
                if path=='/api/transaction/abandon':
                    if not session.user():raise ValueError('Select a family user first.')
                    WorkflowStore(session.preferences_path).cancel(session.user_id,data.get('id'),reason='cancelled')
                    return self.json(session.family())
                if path=='/api/transaction/complete':
                    if not session.user():raise ValueError('Select a family user first.')
                    WorkflowStore(session.preferences_path).complete(session.user_id,data.get('id'))
                    return self.json(session.family())
                if path in ('/api/templates/save','/api/templates/delete'):
                    if not session.access()['templates_ready']:raise ValueError('Complete Ownership and save all assignments before using transaction templates.')
                    templates=TemplateStore(session.preferences_path)
                    if path.endswith('/save'):return self.json(templates.save(data))
                    templates.delete(data.get('id'),data.get('revision'));return self.json({'deleted':True})
                if path in ('/api/transaction/upload','/api/transaction/combine'):
                    if not session.user():raise ValueError('Select a family user first.')
                    workflows=WorkflowStore(session.preferences_path)
                    if path.endswith('/combine'):return self.json(workflows.combine(session.user_id,data.get('id')))
                    import base64
                    text=data.get('psbt')
                    if not isinstance(text,str) or len(text)>12*1024*1024:raise ValueError('Select a signed PSBT file up to 8 MB.')
                    try:raw=base64.b64decode(text,validate=True)
                    except Exception:raise ValueError('Invalid PSBT upload encoding.') from None
                    return self.json(workflows.upload(session.user_id,data.get('id'),data.get('wallet_id'),raw))
                if path in ('/api/notifications/read','/api/notifications/delete','/api/transaction/cancel'):
                    if not session.user():raise ValueError('Select a family user first.')
                    workflows=WorkflowStore(session.preferences_path)
                    if path=='/api/transaction/cancel':workflows.cancel(session.user_id,data.get('id'));return self.json({'cancelled':True})
                    if path.endswith('/delete') and str(data.get('id','')).startswith('node-conflict-'):raise ValueError('Resolve the node conflict to remove this notification.')
                    if path.endswith('/read'):sessions.nodes.notifications(session.user_id,read=True)
                    workflows.update_notifications(session.user_id,data.get('id') if path.endswith('/delete') else None)
                    return self.json(self.notices(session))
                if path in ('/api/builder/estimate','/api/builder/prepare'):
                    if not session.access()['templates_ready']:raise ValueError('Complete Ownership and save all assignments first.')
                    import re
                    diagram=clean_diagram(data.get('diagram')) if path.endswith('/prepare') else None
                    edges=diagram['edges'] if diagram else data.get('edges')
                    if not isinstance(edges,list) or not 1<=len(edges)<=256:raise ValueError('Supply 1–256 planned connections.')
                    for edge in edges:
                        if not isinstance(edge,dict) or not all(isinstance(edge.get(k),str) for k in ('from','to','amount_sats','fee_rate')):raise ValueError('Invalid planned connection.')
                        if not re.fullmatch(r'[0-9]{1,16}',edge['amount_sats']) or not re.fullmatch(r'[0-9]{1,12}(?:\.[0-9]{1,8})?',edge['fee_rate']):raise ValueError('Invalid payment amount or fee contribution.')
                    catalog=builder_catalog(session)
                    allowed={w['id'] for w in catalog['wallets'] if w['can_send']}
                    for edge in edges:
                        if type(edge.get('max',False)) is not bool or edge['to']=='network-fee' and edge.get('max'):raise ValueError('Maximum is available only for Bitcoin payments.')
                        if edge['to']=='network-fee' and int(edge['amount_sats']):raise ValueError('Network Fee connections accept only sats/vB contributions.')
                        if edge['to']!='network-fee' and Decimal(edge['fee_rate']):raise ValueError('Wallet and exchange connections accept only Bitcoin amounts.')
                    sources={edge['from'] for edge in edges if edge.get('max') or int(edge['amount_sats']) or Decimal(edge['fee_rate'])}
                    if not sources or not sources<=allowed:raise ValueError('Only loaded descriptor wallets can fund the plan.')
                    if diagram:
                        with session.lock:
                            # Ownership or profile may have changed during catalogue loading.
                            if not session.access()['templates_ready']:raise ValueError('Ownership changed. Complete Ownership and save all assignments before preparing it.')
                            workflow=WorkflowStore(session.preferences_path);created=workflow.create(session,builder_catalog(session),diagram)
                            return self.json(workflow.public_job(session.user_id,created['id']))
                    return self.json(estimate_fee_plan(edges,catalog,live_coins(session,catalog,sources)))
                if path=='/api/ownership/save':
                    if not session.user() or not session.inventory_ready:raise ValueError('Scan the current input files in Ownership first.')
                    store.save_ownership(data.get('rows'),data.get('revision'),session.inventory);session.confirm_transaction_refresh();return self.json(session.family())
                if path in ('/api/keys/analyse','/api/start') and not session.user():raise ValueError('Select a family user first.')
                if path=='/api/keys/analyse':
                    if not session.access()['ownership_ready']:raise ValueError('Complete Ownership Run and complete all ownership assignments to unlock the Wallet Key Explorer')
                    data={'text':json.dumps(DescriptorStore(session.preferences_path).wallet(data.get('wallet_id'))),'target':data.get('target',''),'start':data.get('start',0),'count':data.get('count',20)}
                    activity_user=session.user_id
                    session.record_activity('Wallet Key Explorer: analysing public descriptors.',activity_user)
                    try:result=analyse_keys(data)
                    except Exception:
                        session.record_activity('Wallet Key Explorer: analysis failed. See Wallet Key Explorer for details.',activity_user)
                        raise
                    session.record_activity('Wallet Key Explorer: analysis complete.',activity_user)
                    return self.json(result)
                if path=='/api/clear':session.clear_slot(data['slot'])
                elif path=='/api/start':session.start(data,data.get('mode','cgt'))
                elif path=='/api/answer':session.answer(int(data['sequence']),data['answer'])
                elif path=='/api/cancel':session.cancel()
                elif path=='/api/close':sessions.close(session.token)
                else:return self.json({'error':'Not found'},404)
                return self.json({'ok':True})
            except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
                session.record_activity('Operation stopped: '+str(exc))
                return self.json({'error':str(exc)},400)
            except Exception as exc:return self.json({'error':str(exc)},500)
    class Server(ThreadingHTTPServer):
        daemon_threads=True
    class IPv6Server(Server):
        address_family=socket.AF_INET6
        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
            super().server_bind()
    listeners=[];v6_error=None
    if ipv6:
        try:listeners.append(IPv6Server(('::1',port),Handler))
        except OSError as exc:v6_error=exc
    selected_port=listeners[0].server_port if listeners else port
    if ipv4:
        try:listeners.append(Server(('127.0.0.1',selected_port),Handler))
        except OSError:
            for listener in listeners:listener.server_close()
            raise
    if not listeners:raise OSError('No requested loopback listener is available: '+str(v6_error))
    class ServerGroup:
        server_port=listeners[0].server_port
        has_ipv6=any(listener.address_family==socket.AF_INET6 for listener in listeners)
        has_ipv4=any(listener.address_family==socket.AF_INET for listener in listeners)
        listeners_started=False
        closed=False
        def __init__(self):self.reaper_stop=threading.Event();self.threads=[]
        def serve_forever(self):
            self.listeners_started=True
            for listener in listeners[1:]:
                thread=threading.Thread(target=listener.serve_forever,daemon=True);thread.start();self.threads.append(thread)
            listeners[0].serve_forever()
        def shutdown(self):
            if self.listeners_started:
                for listener in listeners:listener.shutdown()
        def server_close(self):
            if self.closed:return
            self.closed=True;self.reaper_stop.set()
            # The primary serve_forever has returned on CLI shutdown; stop
            # secondary listeners from this thread before closing their sockets.
            for listener,thread in zip(listeners[1:],self.threads):
                if thread.is_alive():listener.shutdown();thread.join(2)
            for listener in listeners:listener.server_close()
            sessions.close_all()
    server=ServerGroup()
    def reap():
        while not server.reaper_stop.wait(60):sessions.expire()
    threading.Thread(target=reap,daemon=True).start()
    return server,sessions


def main():
    parser=argparse.ArgumentParser(description='Open Wallaby Bitcoin Dashboard in Firefox')
    parser.add_argument('--agent-port',type=int,default=DEFAULT_AGENT_PORT,help='SSH loopback agent protocol port (default: 56443)')
    parser.add_argument('--no-browser',action='store_true');parser.add_argument('--port',type=int,default=38951,help='HTTP port (default: 38951; use 0 for an automatically selected port)')
    family=parser.add_mutually_exclusive_group()
    family.add_argument('--ipv4-only',action='store_true',help='Listen on IPv4 loopback only')
    family.add_argument('--ipv6-only',action='store_true',help='Listen on IPv6 loopback only')
    parser.add_argument('--state-dir',default=str(Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'wallaby-bitcoin-dashboard'))
    parser.add_argument('--max-sessions',type=int,default=32,help='Maximum simultaneous browser workspaces')
    parser.add_argument('--session-idle-hours',type=float,default=24,help='Expire disconnected idle workspaces after this interval')
    parser.add_argument('--config',default=str(ROOT/'config.cfg'),help='UTF-8 saved wallet names and ownership')
    args=parser.parse_args()
    if not 0<=args.port<=65535 or not 0<=args.agent_port<=65535:parser.error('Ports must be from 0 to 65535')
    if args.port and args.port==args.agent_port:parser.error('Dashboard and agent listeners need different ports')
    if args.max_sessions<1 or args.session_idle_hours<=0:parser.error('Session limit and idle hours must be positive')
    server,sessions=create_server(args.state_dir,args.port,args.config,args.max_sessions,args.session_idle_hours*3600,ipv6=not args.ipv4_only,ipv4=not args.ipv6_only)
    try:agent=create_agent_server(sessions.nodes,args.agent_port,sessions.profile_gate,ipv6=not args.ipv4_only,ipv4=not args.ipv6_only)
    except OSError:
        server.server_close();raise
    agent.start()
    host='[::1]' if server.has_ipv6 else '127.0.0.1'
    url=f'http://{host}:{server.server_port}/'
    print('Wallaby Bitcoin Dashboard V1 Build 1 · '+str(ROOT),flush=True)
    print('Wallaby Bitcoin Dashboard: '+url,flush=True)
    print(f'Wallaby Agent listener: loopback port {agent.server_port} (wallaby-agent JSON/HTTP v1 over SSH)',flush=True)
    print(f'Fee estimates (bookmark): http://{host}:{server.server_port}/#/fees',flush=True)
    if not server.has_ipv6 and not args.ipv4_only:print('IPv6 loopback is unavailable; using IPv4 loopback.',flush=True)
    if not args.no_browser:
        def open_browser():
            try:webbrowser.get('firefox').open(url)
            except webbrowser.Error:webbrowser.open(url)
        threading.Thread(target=open_browser,daemon=True).start()
    def stop_service(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_service)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:agent.close();server.server_close()

if __name__=='__main__':main()
