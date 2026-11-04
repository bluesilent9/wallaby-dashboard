"""Portable, validated family profiles. Archives never contain application code or browser tokens."""
from contextlib import contextmanager
from pathlib import Path,PurePosixPath
import hashlib,json,os,re,shutil,subprocess,tempfile,threading,uuid
from preferences import FamilyStore,COLOURS
from descriptor_store import DescriptorStore,clean_wallet
from storage import storage_lock
from ownership_workspace import restore_workspace
from transaction_templates import TemplateStore,clean_diagram
from transaction_workflows import WorkflowStore
from signed_psbt import decode,utxos,verify_input,validate_upload

MAX_BYTES=2*1024**3
MAX_FILES=20000
ROOTS=('config.cfg','descriptors.cfg','data','templates')

class ProfileGate:
    """Concurrent ordinary requests; an archive replacement gets exclusive access."""
    def __init__(self):self.cv=threading.Condition();self.readers=0;self.writer=False;self.waiting=0
    @contextmanager
    def access(self,exclusive=False):
        with self.cv:
            if exclusive:
                self.waiting+=1
                try:
                    while self.writer or self.readers:self.cv.wait()
                    self.writer=True
                finally:self.waiting-=1
            else:
                while self.writer or self.waiting:self.cv.wait()
                self.readers+=1
        try:yield
        finally:
            with self.cv:
                if exclusive:self.writer=False
                else:self.readers-=1
                self.cv.notify_all()

def executable():
    binary=next((p for n in ('7zz','7z','7za') if (p:=shutil.which(n))),None)
    if not binary:raise ValueError('Profile archives require 7-Zip. On Ubuntu/Kubuntu install it with: sudo apt install 7zip')
    return binary

def run(args,cwd=None):
    process=subprocess.Popen([executable(),*args],cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    expired=threading.Event()
    def stop():expired.set();process.kill()
    timer=threading.Timer(180,stop);timer.daemon=True;timer.start()
    try:
        output=process.stdout.read(32*1024*1024+1)
        if len(output)>32*1024*1024:
            process.kill();raise ValueError('Profile archive directory output is too large.')
        code=process.wait()
        if expired.is_set():raise ValueError('The profile archive operation timed out. No profile was replaced.')
        if code:raise ValueError('Invalid, encrypted or damaged profile archive; no profile was replaced.')
        return output.decode('utf-8',errors='replace').replace('\r\n','\n')
    finally:
        timer.cancel()
        if process.poll() is None:process.kill()
        process.wait();process.stdout.close()

def safe_name(name):
    path=PurePosixPath(name)
    if not name or '\\' in name or '\x00' in name or any(ord(c)<32 for c in name) or path.is_absolute() or any(p in {'','..','.'} for p in name.split('/')) or ':' in name:raise ValueError('Unsafe filename in profile archive.')
    if path.parts[0] not in (*ROOTS,'profile.json'):raise ValueError('Unexpected file in profile archive.')
    if len(path.parts)>1 and path.parts[0] not in ('data','templates'):raise ValueError('Unexpected profile directory.')
    return path

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def validate(root):
    for name in ROOTS:
        path=root/name
        if path.exists() and (path.is_file() if name in ('data','templates') else not path.is_file()):raise ValueError('Invalid profile storage layout.')
    manifest=json.loads((root/'profile.json').read_text(encoding='utf-8'))
    if manifest.get('format')!='wallaby-profile' or manifest.get('schema')!=152 or not isinstance(manifest.get('files'),dict):raise ValueError('Unsupported Wallaby profile format.')
    actual={p.relative_to(root).as_posix():p for p in root.rglob('*') if p.is_file() and p!=root/'profile.json'}
    if any(p.is_symlink() for p in root.rglob('*')) or set(actual)!=set(manifest['files']):raise ValueError('Profile contents do not match its manifest.')
    for name,p in actual.items():
        safe_name(name)
        if digest(p)!=manifest['files'][name]:raise ValueError('A profile file failed its integrity check.')
    raw=json.loads((root/'config.cfg').read_text(encoding='utf-8-sig'))
    if raw.get('schema')!=124 or not isinstance(raw.get('users'),list) or not isinstance(raw.get('ownership'),list):raise ValueError('Invalid family configuration in profile.')
    ids=set();names=set()
    for u in raw['users']:
        if not re.fullmatch('[0-9a-f]{32}',str(u.get('id',''))) or u['id'] in ids or not isinstance(u.get('name'),str) or not u['name'].strip() or len(u['name'])>100 or any(ord(c)<32 for c in u['name']) or u['name'].casefold() in names or u.get('colour') not in COLOURS or u.get('gender','male') not in ('male','female'):raise ValueError('Invalid user in profile.')
        if 'date_of_birth' in u:__import__('preferences').validate_birth_date(u['date_of_birth'])
        ids.add(u['id']);names.add(u['name'].casefold())
    seen=set()
    for r in raw['ownership']:
        if not isinstance(r,dict) or not isinstance(r.get('id'),str) or r['id'] in seen or not isinstance(r.get('name'),str) or not isinstance(r.get('source'),str) or r.get('kind') not in ('descriptor','wallet','exchange','bitaroo') or r.get('owner_id') is not None and r['owner_id'] not in ids:raise ValueError('Invalid ownership record in profile.')
        if 'dashboard_name' in r and (not isinstance(r['dashboard_name'],str) or not r['dashboard_name'].strip() or len(r['dashboard_name'])>1000 or any(ord(c)<32 or ord(c)==127 for c in r['dashboard_name'])):raise ValueError('Invalid Dashboard wallet name in profile.')
        if 'dashboard_name_history' in r:
            history=r['dashboard_name_history']
            if not isinstance(history,list) or len(history)>10000:raise ValueError('Invalid Dashboard rename history.')
            previous=None
            for event in history:
                if not isinstance(event,dict) or set(event)-{'old_name','new_name','changed_utc','user_id'}:raise ValueError('Invalid Dashboard rename event.')
                for field in ('old_name','new_name'):
                    v=event.get(field)
                    if not isinstance(v,str) or not v.strip() or len(v)>1000 or any(ord(c)<32 or ord(c)==127 for c in v):raise ValueError('Invalid historical Dashboard name.')
                from datetime import datetime
                if not isinstance(event.get('changed_utc'),str) or datetime.fromisoformat(event['changed_utc']).utcoffset()!=__import__('datetime').timedelta(0):raise ValueError('Invalid Dashboard rename UTC timestamp.')
                if previous is not None and event['old_name']!=previous:raise ValueError('Broken Dashboard rename lineage.')
                previous=event['new_name']
            if history and previous!=r.get('dashboard_name',r['name']):raise ValueError('Dashboard rename history differs from its current name.')
        seen.add(r['id'])
    cfg=root/'config.cfg';FamilyStore(cfg)
    if (root/'descriptors.cfg').exists():
        for w in DescriptorStore(cfg).read()['wallets']:
            clean=clean_wallet(w['raw'])
            if clean['id']!=w.get('id') or clean['name']!=w.get('name'):raise ValueError('Descriptor identity check failed.')
    if (root/'data/nodes.json').exists():
        from nodes import NodeRegistry
        NodeRegistry.validate(json.loads((root/'data/nodes.json').read_text(encoding='utf-8')))
    if (root/'data/agent-hub.json').exists():
        from agent_state import validate as validate_agent_state
        validate_agent_state(json.loads((root/'data/agent-hub.json').read_text(encoding='utf-8')),ids,cfg)
    if (root/'data/wallet-setups.json').exists():
        from wallet_builder import validate_archive
        validate_archive(cfg,json.loads((root/'data/wallet-setups.json').read_text()),ids)
    if (root/'data/builder-workspaces.json').exists():
        from builder_workspace import validate as validate_workspaces
        validate_workspaces(json.loads((root/'data/builder-workspaces.json').read_text()),ids)
    if (root/'data/builder-resource-aliases.json').exists():
        aliases=json.loads((root/'data/builder-resource-aliases.json').read_text())
        if not isinstance(aliases,dict) or len(aliases)>100000 or any(a==b or not isinstance(b,str) or any(not isinstance(v,str) or len(v)>250 or not v.startswith('[\"descriptor\",') for v in (a,b)) for a,b in aliases.items()):raise ValueError('Invalid saved builder wallet identity mapping.')
    if (root/'data/receiving-destinations.json').exists():
        from builder_destinations import validate as validate_destinations
        validate_destinations(json.loads((root/'data/receiving-destinations.json').read_text()),ids)
    if (root/'data/wallet-renames.json').exists():
        from wallet_renames import validate as validate_renames
        validate_renames(json.loads((root/'data/wallet-renames.json').read_text()))
    if (root/'data/archive-queue.json').exists():
        from archive_queue import validate as validate_archive_queue
        validate_archive_queue(json.loads((root/'data/archive-queue.json').read_text()))
    if (root/'data/resident-tax-tables.json').exists():
        from resident_tax_tables import validate as validate_rates
        validate_rates(json.loads((root/'data/resident-tax-tables.json').read_text()))
    if (root/'data/salary-builder.json').exists():
        from salary_builder import validate as validate_salary
        validate_salary(json.loads((root/'data/salary-builder.json').read_text()),ids)
    with tempfile.TemporaryDirectory(dir=root,prefix='.verify-') as temp:
        for uid in raw.get('ownership_runs',{}):
            if uid not in ids:raise ValueError('Unknown Ownership profile.')
            restore_workspace(cfg,uid,Path(temp)/uid)
    templates=TemplateStore(cfg)
    for item in templates.listing():clean_diagram(templates.read(item['id'])['diagram'])
    workflows=WorkflowStore(cfg)
    state=workflows._read()
    for ident,row in state['jobs'].items():
        if not re.fullmatch('[0-9a-f]{32}',ident) or row.get('id')!=ident:raise ValueError('Invalid saved transaction ID.')
        clean_diagram(row['diagram']);base=decode(row['psbt'].encode());spent=utxos(base);assigned=workflows.input_wallets(row)
        for wallet,upload in row.get('signed_uploads',{}).items():
            indices=[i for i,w in enumerate(assigned) if w==wallet]
            if not indices:raise ValueError('A saved signature has no sending wallet.')
            if set(upload['proofs'])-set(map(str,indices)):raise ValueError('Saved signature belongs to another wallet input.')
            for i in indices:
                if str(i) not in upload['proofs']:
                    if row.get('agent_signing'):continue
                    raise ValueError('Missing saved input signature.')
                proof=upload['proofs'][str(i)];fields={bytes.fromhex(k):bytes.fromhex(v) for k,v in proof.items()}
                if spent[i].script_pubkey.script_type()=='p2wsh':
                    from multisig_psbt import verified
                    verified(base,i,fields,spent,False)
                else:verify_input(base,i,fields,spent)
        if row.get('combined_psbt'):validate_upload(row['psbt'],row['combined_psbt'].encode(),list(range(len(assigned))))
    reports=root/'data/reports'
    if reports.exists():
        for p in reports.glob('*.json'):
            item=json.loads(p.read_text())
            if p.name=='index.json':continue
            if not re.fullmatch('[0-9a-f]{32}',p.stem) or item.get('owner_id')!=p.stem or 'family_reports' in item or any(a.get('owner_id')!=p.stem for a in item.get('annual_reports',{}).values()):raise ValueError('Invalid personal report in profile.')
    # JSON is parsed as data; imported files are never executed.
    return raw

def portable_state(stage):
    """Prune only disposable calculations from the copied export, never live state.

    A draft with Agent involvement is an operation, even if the diagram is
    still visible. Retain that operation and its cancellation/renewal links.
    """
    data=stage/'data';(data/'salary-builder.json').unlink(missing_ok=True)
    path=data/'builder-workspaces.json'
    if not path.exists():return
    state=json.loads(path.read_text());tx=state['workspaces'].get('transaction')
    if not tx:return
    def read(name):
        p=data/name
        return json.loads(p.read_text()) if p.exists() else {}
    jobs=read('transactions/workflows.json').get('jobs',{})
    setups=read('wallet-setups.json').get('sessions',{})
    recovered=[ident for rows in (jobs,setups) for ident,row in rows.items() if row.get('workspace_id')==tx['id']]
    for ident in recovered:
        if ident not in tx['operations']:tx['operations'].append(ident)
    hub=read('agent-hub.json')
    related=lambda row:row.get('workspace_id')==tx['id'] or (row.get('proposal') or {}).get('workspace_id')==tx['id']
    linked=any(w.get('renewal',{}).get('transaction_workspace')==tx['id'] for w in state['workspaces'].values())
    protected=(tx['operations'] or tx.get('cancellation_id') or tx.get('draft',{}).get('imported_id') or linked or any(related(row) for row in hub.get('requests',{}).values()))
    if not protected:state['workspaces'].pop('transaction')
    path.write_text(json.dumps(state,ensure_ascii=True))

class ProfileArchive:
    def __init__(self,config):self.config=Path(config).resolve()
    def save(self):
        with tempfile.TemporaryDirectory(prefix='.profile-save-',dir=self.config.parent) as folder:
            stage=Path(folder)/'contents';stage.mkdir(mode=0o700)
            with storage_lock(self.config),FamilyStore(self.config).transaction():
                for name in ROOTS:
                    source=self.config if name=='config.cfg' else self.config.with_name(name)
                    if not source.exists():continue
                    paths=[source] if source.is_file() else list(source.rglob('*'))
                    if source.is_symlink() or any(p.is_symlink() for p in paths):raise ValueError('Profile data cannot contain symbolic links.')
                    for p in paths:
                        if not p.is_file() or p.name.endswith(('.lock','.tmp')):continue
                        relative=Path(name) if source.is_file() else Path(name)/p.relative_to(source);safe_name(relative.as_posix());dest=stage/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);dest.chmod(0o600)
            portable_state(stage)
            files={p.relative_to(stage).as_posix():digest(p) for p in stage.rglob('*') if p.is_file()}
            if len(files)>MAX_FILES or sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())>MAX_BYTES:raise ValueError('Profile is too large to archive.')
            (stage/'profile.json').write_text(json.dumps({'format':'wallaby-profile','schema':152,'files':files}),encoding='utf-8')
            archive=Path(folder)/'profile.7z';run(['a','-t7z','-mx=9','-mmt=2',str(archive),'.'],cwd=stage)
            if archive.stat().st_size>256*1024*1024:raise ValueError('The compressed profile exceeds the 256 MB upload limit.')
            return archive.read_bytes()
    def load(self,name,body):
        if name!='profile.7z':raise ValueError('Select the file named exactly profile.7z.')
        with tempfile.TemporaryDirectory(prefix='.profile-load-',dir=self.config.parent) as folder:
            archive=Path(folder)/'profile.7z';archive.write_bytes(body);stage=Path(folder)/'contents';stage.mkdir(mode=0o700)
            listing=run(['l','-slt',str(archive)])
            if '\nType = 7z\n' not in listing:raise ValueError('The profile must be a genuine 7z archive.')
            blocks=listing.split('\n----------\n')
            if len(blocks)!=2:raise ValueError('Invalid archive directory.')
            entries=[];total=0
            for block in blocks[1].strip().split('\n\n'):
                lines=block.splitlines()
                if not lines:continue
                if any(' = ' not in line for line in lines):raise ValueError('Invalid archive directory record.')
                pairs=[line.split(' = ',1) for line in lines];fields=dict(pairs)
                if len(fields)!=len(pairs) or not {'Path','Size','Attributes','Encrypted'}.issubset(fields):raise ValueError('Ambiguous archive directory record.')
                safe_name(fields.get('Path',''))
                if any('link' in key.lower() or 'reparse' in key.lower() for key in fields) or (fields.get('Attributes','').split() and fields['Attributes'].split()[-1].startswith('l')) or fields.get('Encrypted')=='+' or fields.get('Anti')=='+':raise ValueError('Links and encrypted entries are not accepted in profiles.')
                total+=int(fields.get('Size','0'));entries.append(fields['Path'])
            if len(entries)>MAX_FILES or total>MAX_BYTES or len(entries)!=len(set(entries)) or 'config.cfg' not in entries or 'profile.json' not in entries:raise ValueError('Invalid or oversized profile contents.')
            run(['x','-y','-o'+str(stage),str(archive)])
            try:validate(stage)
            except (KeyError,TypeError,AttributeError,RecursionError,UnicodeError) as exc:raise ValueError('Invalid data in profile archive; no profile was replaced.') from exc
            self.replace(stage)
    def replace(self,stage=None):
        # Stage validation finishes before any live data is moved. Roll back the
        # complete set on an installation error; never mix two family profiles.
        with tempfile.TemporaryDirectory(prefix='.profile-backup-',dir=self.config.parent) as folder:
            backup=Path(folder);installed=[];moved=[]
            with storage_lock(self.config):
                try:
                    for name in ROOTS:
                        target=self.config if name=='config.cfg' else self.config.with_name(name)
                        if target.exists():os.replace(target,backup/name);moved.append((name,target))
                        if stage is not None and (stage/name).exists():os.replace(stage/name,target);installed.append(target)
                    FamilyStore(self.config)
                except BaseException:
                    for p in installed:
                        if p.is_dir():shutil.rmtree(p)
                        else:p.unlink(missing_ok=True)
                    for name,target in moved:os.replace(backup/name,target)
                    raise
