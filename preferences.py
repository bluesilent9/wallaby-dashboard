"""UTF-8 family ownership preferences and run metadata; no transaction decisions stored."""
from contextlib import contextmanager
from pathlib import Path
import json,os,tempfile,threading

SECTIONS=('Other Exchanges','Other Wallets','Core CSVs')
_MUTEX=threading.RLock()


def _fields(text):
    parts=[];buf='';quoted=False;escaped=False
    for char in text:
        if char=='=' and not quoted:parts.append(buf.strip());buf='';continue
        buf+=char
        if escaped:escaped=False
        elif char=='\\' and quoted:escaped=True
        elif char=='"':quoted=not quoted
    if quoted:raise ValueError('Unclosed quoted name')
    parts.append(buf.strip())
    return [json.loads(p) if p.startswith('"') else p for p in parts]


def _name(value):
    return json.dumps(value,ensure_ascii=False).replace('\x85','\\u0085').replace('\u2028','\\u2028').replace('\u2029','\\u2029') if not value or value!=value.strip() or (value.startswith('#') or any(c in value for c in '=\n\r"\x85\u2028\u2029')) else value


def parse_config(text):
    users={};current=None;section=None
    for number,line in enumerate(text.splitlines(),1):
        stripped=line.strip()
        if not stripped or stripped.startswith('#'):continue
        try:
            if stripped.startswith('[') and stripped.endswith(']'):
                title=stripped[1:-1]
                if line[:1].isspace():
                    if current is None or title not in SECTIONS:raise ValueError('Unknown subsection')
                    section=title
                else:
                    if not title:raise ValueError('Empty user name')
                    current=next((name for name in users if name.casefold()==title.casefold()),title)
                    users.setdefault(current,{s:{} for s in SECTIONS});section=None
                continue
            if current is None or section is None:raise ValueError('Entry outside a user subsection')
            fields=_fields(stripped);expected=3 if section=='Core CSVs' else 2
            if len(fields)!=expected or any(not isinstance(v,str) or not v for v in fields):raise ValueError('Invalid entry')
            answer=fields[-1].casefold()
            if answer not in ('yes','no'):raise ValueError('Ownership must be Yes or No')
            users[current][section][fields[0]]=(fields[1],answer=='yes') if expected==3 else answer=='yes'
        except (ValueError,TypeError) as exc:raise ValueError(f'config.cfg line {number}: {exc}') from exc
    return users


def serialize_config(users):
    lines=[]
    for name,sections in users.items():
        if any(c in name for c in '\n\r\v\f\x85\u2028\u2029'):raise ValueError('User name cannot contain line breaks')
        lines.append('['+name+']')
        for section in SECTIONS:
            lines.append('    ['+section+']')
            for key,value in sections[section].items():
                fields=[_name(key)]
                if section=='Core CSVs':fields.append(_name(value[0]));owned=value[1]
                else:owned=value
                fields.append('Yes' if owned else 'No')
                lines.append('        '+' = '.join(fields))
        lines.append('')
    return '\n'.join(lines)


class Preferences:
    def __init__(self,path):
        self.path=Path(path).expanduser().resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._transaction() as data:pass
    @contextmanager
    def _transaction(self):
        with _MUTEX:
            lock=self.path.with_name('.'+self.path.name+'.lock')
            with lock.open('a+b') as handle:
                try:
                    import fcntl
                    fcntl.flock(handle,fcntl.LOCK_EX)
                except ImportError:
                    import msvcrt
                    if lock.stat().st_size==0:handle.write(b'0');handle.flush()
                    handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
                try:
                    original=self.path.read_text(encoding='utf-8-sig') if self.path.exists() else ''
                    data=parse_config(original)
                    yield data
                    updated=serialize_config(data)
                    if updated!=original or not self.path.exists():
                        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.path.parent,prefix='.config-',delete=False) as tmp:
                            temp=Path(tmp.name)
                            try:tmp.write(updated);tmp.flush();os.fsync(tmp.fileno())
                            except BaseException:temp.unlink(missing_ok=True);raise
                        try:os.replace(temp,self.path)
                        finally:temp.unlink(missing_ok=True)
                finally:
                    try:fcntl.flock(handle,fcntl.LOCK_UN)
                    except NameError:
                        handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    @staticmethod
    def _user(data,name):
        canonical=next((key for key in data if key.casefold()==name.casefold()),name)
        data.setdefault(canonical,{s:{} for s in SECTIONS})
        return data[canonical]
    def reconcile(self,user,inventory):
        """Legacy compatibility: inventory changes never remove ownership records."""
        return None
    def get(self,user,section,key):
        with self._transaction() as data:return self._user(data,user)[section].get(key)
    def put(self,user,section,key,value):
        with self._transaction() as data:self._user(data,user)[section][key]=value

# V124 family profiles and global ownership. Legacy parsing above is used only for migration.
import copy,uuid
from storage import migrate_storage,data_directory
COLOURS=('green','blue','amber','purple','red','teal')
MISSING_OWNERSHIP='Error: Missing wallet or exchange ownership information. Run the Ownership Feature to fix this'

class MissingOwnership(ValueError):pass

def _fresh():return {'schema':124,'revision':0,'users':[],'ownership':[],'bitaroo_addresses':{}}
def resource_id(kind,source):return json.dumps([kind,source],ensure_ascii=False,separators=(',',':'))

class FamilyStore:
    def __init__(self,path):
        self.path=Path(path).expanduser().resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        migrate_storage(self.path)
        with self.transaction():pass
    @contextmanager
    def transaction(self):
        with _MUTEX:
            with self.path.with_name('.'+self.path.name+'.lock').open('a+b') as handle:
                try:
                    import fcntl
                    fcntl.flock(handle,fcntl.LOCK_EX)
                except ImportError:
                    import msvcrt
                    if handle.seek(0,2)==0:handle.write(b'0');handle.flush()
                    handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
                try:
                    original=self.path.read_text(encoding='utf-8-sig') if self.path.exists() else ''
                    if not original.strip():data=_fresh()
                    elif original.lstrip().startswith('{'):
                        data=json.loads(original)
                        if data.get('schema')!=124 or not isinstance(data.get('users'),list) or not isinstance(data.get('ownership'),list):raise ValueError('Unsupported family config.cfg format')
                    else:
                        legacy=parse_config(original);data=_fresh();resources={}
                        for name,sections in legacy.items():
                            user={'id':uuid.uuid4().hex,'name':name,'colour':COLOURS[len(data['users'])%len(COLOURS)]};data['users'].append(user)
                            for section,entries in sections.items():
                                kind={'Core CSVs':'csv','Other Wallets':'wallet','Other Exchanges':'exchange'}[section]
                                for source,value in entries.items():
                                    title,owned=value if kind=='csv' else (source,value)
                                    key=resource_id(kind,source);r=resources.setdefault(key,{'id':key,'kind':kind,'source':source,'name':title,'owner_id':None,'external':False,'smsf':False,'confirmed':False,'migration_review':True})
                                    if owned:r.setdefault('_claims',[]).append(user['id'])
                        for r in resources.values():
                            claims=r.pop('_claims',[]);r['owner_id']=claims[0] if len(claims)==1 else None
                            data['ownership'].append(r)
                        backup=self.path.with_name(self.path.name+'.pre-v124.bak')
                        if not backup.exists():
                            with backup.open('x',encoding='utf-8') as f:f.write(original)
                    data['ownership']=[r for r in data['ownership'] if r.get('kind')!='csv']
                    before=copy.deepcopy(data)
                    yield data
                    if data!=before:data['revision']=before.get('revision',0)+1
                    updated=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
                    if updated!=original:
                        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=self.path.parent,prefix='.family-',delete=False) as tmp:
                            temp=Path(tmp.name)
                            try:tmp.write(updated);tmp.flush();os.fsync(tmp.fileno())
                            except BaseException:temp.unlink(missing_ok=True);raise
                        try:os.replace(temp,self.path)
                        finally:temp.unlink(missing_ok=True)
                finally:
                    try:fcntl.flock(handle,fcntl.LOCK_UN)
                    except NameError:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    def snapshot(self):
        with self.transaction() as data:return copy.deepcopy(data)
    def user(self,user_id):return next((u for u in self.snapshot()['users'] if u['id']==user_id),None)
    @staticmethod
    def revision(data,revision):
        if type(revision) is not int or revision!=data['revision']:raise ValueError('Configuration changed in another browser. Refresh this page before saving.')
    def save_user(self,name,colour,user_id=None,revision=None,gender=None):
        name=str(name).strip()
        if not name or len(name)>100 or any(ord(c)<32 for c in name):raise ValueError('Enter a user name of 1–100 characters without control characters.')
        if colour not in COLOURS:raise ValueError('Choose one of the supplied profile colours.')
        if gender is not None and gender not in ('male','female'):raise ValueError('Choose Male or Female.')
        with self.transaction() as data:
            self.revision(data,revision)
            if any(u['name'].casefold()==name.casefold() and u['id']!=user_id for u in data['users']):raise ValueError('A user with that name already exists.')
            if user_id:
                user=next((u for u in data['users'] if u['id']==user_id),None)
                if not user:raise ValueError('User no longer exists.')
                user.update(name=name,colour=colour,gender=gender or user.get("gender","male"))
            else:
                if len(data['users'])>=128:raise ValueError('Maximum 128 family users.')
                data['users'].append({'id':uuid.uuid4().hex,'name':name,'colour':colour,'gender':gender or 'male'})
        return self.snapshot()
    def delete_user(self,user_id,revision):
        with self.transaction() as data:
            self.revision(data,revision)
            if not any(u['id']==user_id for u in data['users']):raise ValueError('User no longer exists.')
            data['users']=[u for u in data['users'] if u['id']!=user_id]
            data.get('bitaroo_addresses',{}).pop(user_id,None)
            previous=data.get('ownership_runs',{}).pop(user_id,None)
            for r in data['ownership']:
                if r.get('owner_id')==user_id:r.update(owner_id=None,external=False,confirmed=False)
        self._discard_ownership_files(previous)
        return self.snapshot()
    def _discard_ownership_files(self,meta):
        import re,shutil
        generation=(meta or {}).get('data_id','')
        if re.fullmatch('[0-9a-f]{32}',generation):shutil.rmtree(data_directory(self.path)/'ownership'/generation,ignore_errors=True)
    def reset(self,revision):
        with self.transaction() as data:
            self.revision(data,revision)
            previous=list(data.get('ownership_runs',{}).values())
            rev=data['revision'];data.clear();data.update(_fresh());data['revision']=rev
        for meta in previous:self._discard_ownership_files(meta)
        import shutil
        shutil.rmtree(data_directory(self.path)/'reports',ignore_errors=True)
        return self.snapshot()
    def save_bitaroo_address(self,user_id,entry,checked_utc):
        """Replace one profile's discovered destination; an unavailable result clears it.

        Runs use the same cross-process lock as administration. No browser revision
        is required: this updates only the importing profile's address record.
        """
        import re
        from datetime import datetime
        from address_book import valid_address
        def utc(value):
            if not isinstance(value,str) or len(value)>50 or not value.endswith('Z'):raise ValueError('Invalid address observation time.')
            datetime.fromisoformat(value[:-1]+'+00:00');return value
        if not isinstance(entry,dict) or entry.get('kind')!='bitaroo' or entry.get('source')!=user_id or entry.get('id')!=resource_id('bitaroo',user_id):raise ValueError('Invalid Bitaroo address identity.')
        status=entry.get('status');addresses=entry.get('addresses')
        if status not in ('available','unavailable') or not isinstance(addresses,list) or len(addresses)!=(1 if status=='available' else 0):raise ValueError('Invalid Bitaroo address result.')
        clean={'id':resource_id('bitaroo',user_id),'kind':'bitaroo','source':user_id,'status':status,'addresses':[],'checked_utc':utc(checked_utc)}
        if entry.get('observed_utc'):clean['observed_utc']=utc(entry['observed_utc'])
        if status=='available':
            item=addresses[0]
            if not isinstance(item,dict) or not valid_address(item.get('address')) or not re.fullmatch('[0-9a-f]{64}',item.get('txid','')):raise ValueError('Invalid Bitaroo receiving address evidence.')
            clean['addresses']=[{'address':item['address'],'txid':item['txid'],'observed_utc':utc(item.get('observed_utc'))}]
        else:clean['reason']=str(entry.get('reason') or 'The latest supplied Bitaroo deposit could not be identified.')[:2000]
        with self.transaction() as data:
            if not any(u['id']==user_id for u in data['users']):raise ValueError('User no longer exists.')
            data.setdefault('bitaroo_addresses',{})[user_id]=clean
        return self.snapshot()
    def save_ownership(self,rows,revision,inventory):
        if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Invalid ownership entries.')
        expected={r['id']:r for r in inventory}
        if len(rows)!=len(expected) or len({r.get('id') for r in rows})!=len(rows) or {r.get('id') for r in rows}!=set(expected):raise ValueError('Save ownership for every imported wallet and exchange.')
        with self.transaction() as data:
            self.revision(data,revision);users={u['id'] for u in data['users']};existing={r['id']:r for r in data['ownership']}
            for row in rows:
                src=expected[row['id']];name=str(row.get('name','')).strip();owner=row.get('owner_id');external=row.get('external')
                row=dict(row)
                from wallet_names import source_suggestions
                if source_suggestions(src,data['users'])['smsf_detected']:row['smsf']=True
                if src['kind']=='bitaroo':
                    if src['source'] not in users:raise ValueError('Bitaroo account owner no longer exists.')
                    owner=src['source'];external=False;row['smsf']=False
                elif src['kind']=='exchange':row['smsf']=False
                if type(row.get('smsf')) is not bool or type(external) is not bool or (owner not in users if not external else owner is not None):raise ValueError('Choose a user or Someone else / external for every entry.')
                if not name or len(name)>1000 or any(ord(c)<32 for c in name):raise ValueError('Every wallet needs a name (maximum 1,000 characters).')
                if (src['kind']!='descriptor' or src.get('extinct')) and name!=src['name']:raise ValueError('Wallet and exchange names must match their source.')
                existing[row['id']]={'display_name_set':True,'id':row['id'],'kind':src['kind'],'source':src['source'],'name':name,'owner_id':owner,'external':external,'smsf':row['smsf'],'confirmed':True}
            names=[existing[r['id']]['name'].casefold() for r in inventory if r['kind'] in ('descriptor','wallet','exchange')]
            if len(set(names))!=len(names) or 'bitaroo' in names:raise ValueError('Wallet names must be unique; Bitaroo is reserved for the exchange ledger.')
            data['ownership']=list(existing.values())
        return self.snapshot()

def resolve_ownership(snapshot,inventory):
    users={u['id'] for u in snapshot['users']};records={r['id']:r for r in snapshot['ownership']};result={}
    for item in inventory:
        r=records.get(item['id'])
        if not r or not r.get('confirmed') or not r.get('name') or type(r.get('smsf')) is not bool or not (r.get('external') and r.get('owner_id') is None or not r.get('external') and r.get('owner_id') in users):raise MissingOwnership(MISSING_OWNERSHIP)
        if item['kind'] in ('wallet','exchange') and r['name']!=item['name']:raise MissingOwnership(MISSING_OWNERSHIP)
        result[item['id']]=r
    names=[result[i['id']]['name'].casefold() for i in inventory if i['kind'] in ('descriptor','wallet','exchange')]
    if len(set(names))!=len(names) or 'bitaroo' in names:raise MissingOwnership(MISSING_OWNERSHIP)
    return result
