"""Public Core descriptor collection. No private keys or console history are retained."""
import copy,hashlib,json,os,re,tempfile
from pathlib import Path
from datetime import datetime,timezone
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from vendor.embit.networks import NETWORKS
from preferences import FamilyStore
from storage import migrate_storage
from descriptor_policy import parse_policy,needs_history
from wallet_keys import _PRIVATE

MAX_TEXT=4*1024*1024

def extract_all(text):
    if not isinstance(text,str) or not text.strip() or len(text.encode())>MAX_TEXT:raise ValueError('Paste up to 4 MB of public descriptor console output.')
    if _PRIVATE.search(text):raise ValueError('Private key material rejected. Clear the console and run listdescriptors false.')
    decoder=json.JSONDecoder();pos=0;attempts=0;values=[]
    while True:
        begin=text.find('{',pos)
        if begin<0:break
        attempts+=1
        if attempts>4096:raise ValueError('Too many unrelated JSON fragments. Clear the console before copying.')
        try:value,end=decoder.raw_decode(text,begin)
        except (ValueError,RecursionError):pos=begin+1;continue
        pos=end
        if isinstance(value,dict) and isinstance(value.get('descriptors'),list):values.append(value)
    if not values:raise ValueError('No complete listdescriptors response found. Copy the complete console output for each wallet.')
    return values

def clean_wallet(raw):
    name=raw.get('wallet_name')
    if not isinstance(name,str) or len(name)>200 or any(ord(c)<32 for c in name):raise ValueError('Every response must include its Bitcoin Core wallet_name.')
    name=name or 'Default Core Wallet'
    if name.casefold()=='bitaroo':raise ValueError('Bitaroo is reserved for the exchange ledger. Rename this Core wallet before importing it.')
    rows=raw.get('descriptors')
    if not isinstance(rows,list) or not 1<=len(rows)<=256:raise ValueError('A wallet must contain between 1 and 256 public descriptor records.')
    cleaned=[];bodies=set()
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('desc'),str):raise ValueError('Every descriptor record needs a desc string.')
        text=row['desc'];parts=text.split('#')
        if len(text)>16384 or len(parts)!=2 or checksum(parts[0])!=parts[1]:raise ValueError('Invalid descriptor checksum. Re-copy the full public export from Core.')
        if _PRIVATE.search(text):raise ValueError('Private descriptors are not accepted.')
        try:
            d,combo,hardened=parse_policy(parts[0])
            if any(k.is_private for k in d.keys):raise ValueError()
            for k in d.keys:
                if k.is_extended and k.key.version not in [NETWORKS['main'][v] for v in ('xpub','ypub','zpub','Ypub','Zpub')]:raise ValueError()
            if d.num_branches>8:raise ValueError()
        except Exception:raise ValueError('Unsupported descriptor or non-mainnet public key. Re-copy the complete Bitcoin Core public export; private keys are never accepted.') from None
        if parts[0] in bodies:continue
        bodies.add(parts[0]);item={'desc':text}
        for field in ('active','internal'):
            if field in row:
                if type(row[field]) is not bool:raise ValueError('Invalid descriptor '+field+' flag.')
                item[field]=row[field]
        if d.is_wildcard:
            bounds=row.get('range');nxt=row.get('next_index',row.get('next'))
            if not isinstance(bounds,list) or len(bounds)!=2 or not all(type(x) is int for x in bounds) or not 0<=bounds[0]<=bounds[1]<0x80000000:raise ValueError('Ranged descriptors require their complete Core range metadata.')
            if bounds[1]-bounds[0]>100000:raise ValueError('Descriptor range exceeds the 100,000-address scan budget per branch. Reduce the range explicitly before using this import.')
            if nxt is not None and (type(nxt) is not int or not 0<=nxt<0x80000000):raise ValueError('Invalid next_index.')
            item['range']=bounds;item['next_index']=nxt
        if type(row.get('timestamp')) is int and row['timestamp']>=0:item['timestamp']=row['timestamp']
        cleaned.append(item)
    identity=hashlib.sha256('\n'.join(sorted(bodies)).encode()).hexdigest()
    return {'id':identity,'name':name,'raw':{'wallet_name':name,'descriptors':cleaned}}

class DescriptorStore:
    def __init__(self,config):
        self.config=Path(config).resolve();migrate_storage(self.config);self.path=self.config.with_name('descriptors.cfg')
    def read(self):
        if not self.path.exists():return {'schema':132,'wallets':[],'loaded_utc':None}
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('schema')!=132 or not isinstance(data.get('wallets'),list):raise ValueError('Unsupported descriptors.cfg format.')
        return data
    @staticmethod
    def fingerprint(data):return hashlib.sha256(json.dumps(data.get('wallets',[]),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def summary(self):
        data=self.read();pending=[w for w in data['wallets'] if needs_history(w) and not w.get('public_history')]
        return {'ready':bool(data['wallets']) and not pending,'loaded':bool(data['wallets']),'fingerprint':self.fingerprint(data),'loaded_utc':data.get('loaded_utc'),'wallets':[{'id':w['id'],'name':w['name'],'records':len(w['raw']['descriptors']),'has_vault':bool(w.get('has_vault')),'watch_only':bool(w.get('watch_only')),'node_sources':w.get('node_sources',[]),'needs_public_history':needs_history(w),'public_history_loaded':bool(w.get('public_history')),'public_history_utc':w.get('public_history',{}).get('exported_utc')} for w in data['wallets']]}
    def load(self,text):
        incoming={};names={}
        for raw in extract_all(text):
            wallet=clean_wallet(raw);uid=wallet['id'];name=wallet['name'].casefold()
            if name in names and names[name]!=uid:raise ValueError('Different wallets share the same name. Give each Core wallet a unique name before importing.')
            if uid in incoming and incoming[uid]!=wallet:raise ValueError('Conflicting responses for the same wallet. Paste one current export per wallet.')
            incoming[uid]=wallet;names[name]=uid
        with FamilyStore(self.config).transaction():
            data=self.read();merged={w['id']:w for w in data['wallets']}
            for uid,wallet in incoming.items():
                if uid in merged and 'public_history' in merged[uid]:wallet['public_history']=merged[uid]['public_history']
                merged[uid]=wallet
            labels=[w['name'].casefold() for w in merged.values()]
            if len(labels)!=len(set(labels)):raise ValueError('A saved wallet already uses this name with different descriptors. Rename the new wallet in Core to distinguish it.')
            data={'schema':132,'loaded_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'wallets':sorted(merged.values(),key=lambda w:w['name'].casefold())}
            with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=self.path.parent,prefix='.descriptors-',delete=False) as f:
                tmp=Path(f.name);json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            try:os.replace(tmp,self.path)
            finally:tmp.unlink(missing_ok=True)
        return self.summary()
    def replace_node_wallets(self,wallets):
        """Publish a complete all-node discovery atomically, after all reads succeed."""
        if not wallets:raise ValueError('No node wallets discovered.')
        for wallet in wallets:
            checked=clean_wallet(wallet['raw'])
            if checked['id']!=wallet['id'] or checked['name']!=wallet['name']:raise ValueError('Invalid node wallet identity.')
            if needs_history(wallet) and not wallet.get('public_history'):raise ValueError('Missing node public history.')
        data={'schema':132,'loaded_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'wallets':sorted(wallets,key=lambda w:w['name'].casefold())}
        with FamilyStore(self.config).transaction():
            with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=self.path.parent,prefix='.descriptors-',delete=False) as f:
                tmp=Path(f.name);json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            try:os.replace(tmp,self.path)
            finally:tmp.unlink(missing_ok=True)
        return self.summary()

    def wallet(self,uid):
        wallet=next((w for w in self.read()['wallets'] if w['id']==uid),None)
        if not wallet:raise ValueError('Select a saved descriptor wallet.')
        return copy.deepcopy(wallet['raw'])

    def load_history(self,uid,text):
        if not isinstance(text,str) or len(text.encode())>MAX_TEXT:raise ValueError('Paste up to 4 MB of public history console output.')
        if _PRIVATE.search(text):raise ValueError('Private key material rejected.')
        decoder=json.JSONDecoder();pos=0;values=[];attempts=0
        while (begin:=text.find('{',pos))>=0:
            attempts+=1
            if attempts>4096:raise ValueError('Too many unrelated JSON fragments.')
            try:value,end=decoder.raw_decode(text,begin)
            except (ValueError,RecursionError):pos=begin+1;continue
            pos=end
            if isinstance(value,dict) and isinstance(value.get('transactions'),list) and 'lastblock' in value:values.append(value)
        if len(values)!=1:raise ValueError('Copy one complete listsinceblock response for the selected wallet, with include_change=true.')
        value=values[0]
        if _PRIVATE.search(json.dumps(value)):raise ValueError('Private key material rejected.')
        last=value['lastblock']
        if not re.fullmatch('[0-9a-f]{64}',str(last)):raise ValueError('Invalid Core lastblock hash.')
        if len(value['transactions'])>100000:raise ValueError('Public history export is too large.')
        outputs=set()
        for row in value['transactions']:
            if not isinstance(row,dict):raise ValueError('Invalid Core transaction entry.')
            if row.get('category') not in ('receive','generate','immature','orphan'):continue
            ident=row.get('txid');vout=row.get('vout')
            if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{64}',ident) or type(vout) is not int or not 0<=vout<0x100000000:raise ValueError('Invalid Core received output.')
            outputs.add((ident,vout))
        history={'exported_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'lastblock':last,'outputs':[{'txid':t,'vout':v} for t,v in sorted(outputs)]}
        with FamilyStore(self.config).transaction():
            data=self.read();wallet=next((w for w in data['wallets'] if w['id']==uid),None)
            if not wallet:raise ValueError('Select a saved descriptor wallet.')
            if not needs_history(wallet):raise ValueError('This wallet does not require a legacy public-history supplement.')
            wallet['public_history']=history
            with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=self.path.parent,prefix='.descriptors-',delete=False) as f:
                tmp=Path(f.name);json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            try:os.replace(tmp,self.path)
            finally:tmp.unlink(missing_ok=True)
        return self.summary()
