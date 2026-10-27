"""Durable per-profile Ownership inputs; CGT runs and answers are never saved here."""
import copy,hashlib,re,shutil,uuid
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
from preferences import FamilyStore
from family_inputs import account_id,folder_slot
from storage import data_directory

FOLDERS={'bitaroo','bitaroo_history','bitaroo_current'}
SLOTS={'bitaroo','bitaroo_history','bitaroo_current','historical','exchanges','quotes','cpi','tax_context','chain'}

def data_root(config):return data_directory(config)/'ownership'
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()

def save_workspace(config,user_id,uploads,inventory,address_book,settings,completed_utc=None,metadata=None):
    """Publish files and metadata under the same config lock used by readers."""
    store=FamilyStore(config);root=data_root(config);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    generation=uuid.uuid4().hex;dest=root/generation;dest.mkdir(mode=0o700)
    meta={'data_id':generation,'completed_utc':completed_utc or datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
          'inventory':copy.deepcopy(inventory),'address_book':copy.deepcopy(address_book),
          'settings':{k:v for k,v in settings.items() if k not in {'financial_year','taxpayer','mode','matching_window','refresh_descriptor_history'}},'files':[]}
    meta.update({k:v for k,v in (metadata or {}).items() if k in {'descriptor_fingerprint','bitaroo_as_of_date','bitaroo_data','historical_balances','family_accounts'}})
    try:
        for slot,path in uploads.items():
            if slot not in SLOTS and account_id(slot) is None:raise ValueError('Unknown saved input type.')
            path=Path(path);files=sorted(p for p in path.rglob('*') if p.is_file()) if folder_slot(slot) else [path]
            for source in files:
                name=source.relative_to(path).as_posix() if folder_slot(slot) else source.name
                target=dest/slot/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);target.chmod(0o600)
                meta['files'].append({'slot':slot,'name':name,'sha256':digest(target)})
        with store.transaction() as data:
            if not any(u['id']==user_id for u in data['users']):raise ValueError('User no longer exists.')
            previous=data.setdefault('ownership_runs',{}).get(user_id,{})
            data['ownership_runs'][user_id]=meta
        # Remove the prior generation only after the config commit succeeds.
        # Loaders hold that same lock until their private copies are complete.
        old=previous.get('data_id','')
        if re.fullmatch('[0-9a-f]{32}',old) and old!=generation:shutil.rmtree(root/old,ignore_errors=True)
    except BaseException:
        shutil.rmtree(dest,ignore_errors=True);raise
    return meta

def restore_workspace(config,user_id,input_dir):
    """Restore verified inputs into a browser's private working directory."""
    store=FamilyStore(config);input_dir=Path(input_dir)
    with store.transaction() as data:
        meta=copy.deepcopy(data.get('ownership_runs',{}).get(user_id))
        if not meta:return None
        generation=meta.get('data_id','')
        if not re.fullmatch('[0-9a-f]{32}',generation):raise ValueError('Invalid saved Ownership workspace. Run Ownership again.')
        root=data_root(config)/generation;uploads={};seen=set()
        try:
            files=meta['files']
            if not isinstance(files,list) or len(files)>100000:raise ValueError('Invalid Ownership input manifest.')
            for item in files:
                slot=item['slot'];name=PurePosixPath(item['name'])
                if (slot not in SLOTS and account_id(slot) is None) or name.is_absolute() or not name.parts or any(p in {'.','..'} for p in name.parts) or '\\' in str(name) or (not folder_slot(slot) and len(name.parts)!=1):raise ValueError('Invalid saved Ownership filename.')
                if (slot,str(name)) in seen or (not folder_slot(slot) and slot in uploads):raise ValueError('Duplicate saved Ownership input.')
                seen.add((slot,str(name)));source=root/slot/Path(*name.parts)
                if not source.is_file() or source.is_symlink() or digest(source)!=item['sha256']:raise ValueError('Saved Ownership inputs are missing or changed. Run Ownership again.')
                target=input_dir/slot/Path(*name.parts);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);target.chmod(0o600)
                uploads[slot]=input_dir/slot if folder_slot(slot) else target
            if not {'historical','exchanges','chain'}.issubset(uploads):raise ValueError('Saved Ownership inputs are incomplete. Run Ownership again.')
            datetime.fromisoformat(meta['completed_utc'].replace('Z','+00:00'))
            if not isinstance(meta.get('inventory'),list) or not isinstance(meta.get('settings'),dict):raise ValueError('Invalid saved Ownership metadata.')
        except (KeyError,TypeError,ValueError,OSError) as exc:
            shutil.rmtree(input_dir,ignore_errors=True);input_dir.mkdir(parents=True,exist_ok=True)
            raise ValueError('Could not restore saved Ownership data. Run Ownership again. '+str(exc)) from exc
        return meta,uploads
