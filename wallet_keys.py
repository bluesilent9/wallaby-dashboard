"""Stateless, offline public-descriptor explorer. Never reads a Core wallet or chain API."""
import json,re,threading
from descriptor_policy import parse_policy,output_scripts
from vendor.embit import bip32
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from vendor.embit.networks import NETWORKS

MAX_TEXT=262144
_GATE=threading.BoundedSemaphore(2)
_PRIVATE=re.compile(r'\b[xtyzuYZUV]prv[1-9A-HJ-NP-Za-km-z]+|(?<=[(\s])[5KLc9][1-9A-HJ-NP-Za-km-z]{50,51}(?=[/)\s])')
_FORMS=[('combo(',')','Legacy combo (multiple script types)','xpub'),('sh(wpkh(', '))','Wrapped SegWit','ypub'),('wpkh(',')','Native SegWit','zpub'),('pkh(',')','Legacy','xpub'),('tr(',')','Taproot (key path)','xpub')]

def path(indices):
    return ''.join('/'+str(i&0x7fffffff)+('h' if i>=0x80000000 else '') for i in indices)

def variants(key,kind,network):
    result={'xpub' if network=='main' else 'tpub':key.to_base58(NETWORKS[network]['xpub'])}
    if kind in ('ypub','zpub'):
        name=kind if network=='main' else {'ypub':'upub','zpub':'vpub'}[kind]
        result[name]=key.to_base58(NETWORKS[network][kind])
    return result

def extract_descriptor_json(text):
    """Find one complete descriptor response without depending on translated console text."""
    decoder=json.JSONDecoder();position=0;attempts=0;matches=[]
    while True:
        begin=text.find('{',position)
        if begin<0:break
        attempts+=1
        if attempts>128:raise ValueError('Too many JSON fragments. Clear the Core console, run listdescriptors false once, and copy its output again.')
        try:value,end=decoder.raw_decode(text,begin)
        except (ValueError,RecursionError):
            position=begin+1;continue
        position=end
        if isinstance(value,dict) and isinstance(value.get('descriptors'),list):matches.append(value)
        if len(matches)>1:raise ValueError('Multiple descriptor responses found. Clear the Core console and run listdescriptors false only once.')
    if not matches:raise ValueError('No complete descriptor JSON response found. Clear the Core console, run listdescriptors false, wait for completion, and copy its output again.')
    return matches[0]

def analyse(data):
    if not isinstance(data,dict):raise ValueError('Expected an analysis request.')
    text=data.get('text','');start=data.get('start',0);count=data.get('count',20);target=data.get('target','')
    if not isinstance(text,str) or not text.strip():raise ValueError('Paste the console output after running listdescriptors false.')
    if len(text.encode('utf-8'))>MAX_TEXT:raise ValueError('Descriptor input is too large (maximum 256 KB).')
    if _PRIVATE.search(text):raise ValueError('Private key material rejected. Clear this input and use listdescriptors false; never paste private descriptors.')
    if type(start) is not int or type(count) is not int or start<0 or count<1 or count>100 or start+count>0x80000000:raise ValueError('Choose a non-hardened start index and between 1 and 100 addresses per branch.')
    if not isinstance(target,str) or len(target)>100:raise ValueError('The target address is too long.')
    raw=extract_descriptor_json(text)
    if _PRIVATE.search(json.dumps(raw)):raise ValueError('Private key material rejected. Use listdescriptors false.')
    if not isinstance(raw,dict) or not isinstance(raw.get('descriptors'),list) or not 1<=len(raw['descriptors'])<=32:raise ValueError('Expected a descriptors array containing between 1 and 32 records.')
    # Validate all key expressions before doing any cryptography, including unsupported policies.
    for row in raw['descriptors']:
        if not isinstance(row,dict) or not isinstance(row.get('desc'),str) or len(row['desc'])>4096:raise ValueError('Each descriptor record must contain a descriptor of at most 4,096 characters.')
    if not _GATE.acquire(blocking=False):raise ValueError('The explorer is busy. Please retry shortly.')
    try:return _analyse(raw,start,count,target.strip())
    finally:_GATE.release()

def _analyse(raw,start,count,target):
    groups={};issues=[];matches=[]
    for row_number,row in enumerate(raw['descriptors'],1):
        text=row['desc'];parts=text.split('#');body=parts[0]
        if len(parts)!=2 or parts[1]!=checksum(body):
            issues.append({'record':row_number,'message':'Missing or invalid descriptor checksum. Re-copy this record from Core.'});continue
        form=next((f for f in _FORMS if body.startswith(f[0]) and body.endswith(f[1])),None)
        if not form:
            issues.append({'record':row_number,'message':'This policy is not supported by Wallet Key Explorer. Supported: combo, single-key pkh, wpkh, sh(wpkh), and key-path tr. Multisig and script trees require their full policy.'});continue
        key_expr=body[len(form[0]):-len(form[1])]
        if re.search(r"(?:/|<|;)(?:[0-9]+|\*)[h'](?=[/);>]|$)",re.sub(r'\[[^]\n]*\]','',key_expr)):
            issues.append({'record':row_number,'message':'Hardened child public keys cannot be derived from this xpub. The blockchain scanner uses the supplemental Core public history for this descriptor.'});continue
        if not re.fullmatch(r'(?:\[[0-9a-fA-F]{8}(?:/\d+[h\x27]?)*\])?(?:(?:[xtyzu]pub|[YZUV]pub)[1-9A-HJ-NP-Za-km-z]{100,110}|[0-9a-fA-F]{64}|[0-9a-fA-F]{66})(?:/(?:\d+[h\x27]?|\*|<\d+(?:;\d+)+>))*',key_expr):
            issues.append({'record':row_number,'message':'Not a supported public key expression. Use public descriptors only; this expression cannot be analysed.'});continue
        try:
            descriptor,is_combo,hardened=parse_policy(body)
            if hardened:raise ValueError('Hardened descendants require Core public history; their child public keys cannot be derived from an xpub.')
            if len(descriptor.keys)!=1 or descriptor.taptree:raise ValueError('Script trees are not supported.')
            k=descriptor.key
            if k.is_private:raise ValueError('Private key material is not permitted.')
            if k.origin and k.is_extended and (len(k.origin.derivation)!=k.key.depth or (k.key.depth and k.origin.derivation[-1]!=k.key.child_number)):
                raise ValueError('Key origin does not agree with the extended key depth or child number.')
            if k.allowed_derivation and k.allowed_derivation.has_hardend:raise ValueError('Hardened descendants cannot be derived from a public key.')
            if descriptor.num_branches>8:raise ValueError('Too many branches (maximum 8 per descriptor).')
            if k.is_extended:
                network='main' if k.key.version in [NETWORKS['main'][v] for v in ('xpub','ypub','zpub','Ypub','Zpub')] else 'test'
                canonical=k.key.to_base58(NETWORKS[network]['xpub']);origin='m'+path(k.derivation) if k.origin else None
                gid=canonical+'|'+str(origin)
                account=bool(k.origin and len(k.derivation)==3 and all(i>=0x80000000 for i in k.derivation) and k.derivation[0]&0x7fffffff in (44,49,84,86))
                title='Account extended public key' if account else 'Provided extended public key · depth '+str(k.key.depth)
                group={'id':gid,'title':title,'origin':origin,'fingerprint':k.fingerprint.hex(),'depth':k.key.depth,'network':'Bitcoin mainnet' if network=='main' else 'Test network (testnet/signet; regtest addresses differ)','xpub':canonical,'branches':[]}
            else:
                network='main';gid='fixed:'+body;origin=None;group={'id':gid,'title':'Fixed public key','origin':None,'fingerprint':None,'depth':None,'network':'Bitcoin mainnet (assumed; raw public key has no network)','xpub':None,'branches':[]}
            new_branches=[]
            for branch_index in range(descriptor.num_branches):
                branch=descriptor.branch(branch_index);bk=branch.key
                indexes=bk.allowed_derivation.indexes if bk.allowed_derivation else []
                if any(isinstance(i,list) for i in indexes):raise ValueError('Unsupported branch structure.')
                prefix=indexes[:-1] if indexes and indexes[-1] is None else indexes
                if None in prefix:raise ValueError('Wildcard must be at the end of the derivation.')
                suffix=path(prefix);has_wildcard=branch.is_wildcard
                internal=row.get('internal');label='Branch role unknown';evidence='No authoritative branch role metadata.'
                # Core internal metadata applies to a single branch, not both sides of a multipath policy.
                if descriptor.num_branches==1 and type(internal) is bool:
                    label='Change branch' if internal else 'Receiving branch';evidence='Confirmed by Bitcoin Core internal='+str(internal).lower()+'.'
                elif has_wildcard and prefix and prefix[-1] in (0,1):
                    label=('Receiving' if prefix[-1]==0 else 'Change')+' branch (inferred)';evidence='Inferred from the conventional /'+str(prefix[-1])+'/* path; custom wallets may differ.'
                last=row.get('next_index',row.get('next'));last=last if type(last) is int and 0<=last<0x80000000 else None
                imported_range=row.get('range');imported_range=imported_range if isinstance(imported_range,list) and len(imported_range)==2 and all(type(i) is int for i in imported_range) else None
                addresses=[]
                for idx in range(start,start+count) if has_wildcard else [0]:
                    derived=branch.derive(idx) if has_wildcard else branch
                    relative=suffix+('/'+str(idx) if has_wildcard else '')
                    for spk in output_scripts(derived,is_combo):
                        try:address=spk.address(NETWORKS[network])
                        except ValueError:continue # P2PK has a script, not a standard address.
                        entry={'index':idx if has_wildcard else None,'address':address,'path':(origin or 'provided key')+relative+(' · '+str(spk.script_type()) if is_combo else ''),'match':bool(target and (target==address or address.startswith(('bc1','tb1')) and target==target.upper() and target.lower()==address))}
                        addresses.append(entry)
                        if entry['match']:matches.append({'record':row_number,'branch':label,**entry})
                branchkey=bk.key.derive(prefix) if bk.is_extended else None
                new_branches.append({'record':row_number,'label':label,'evidence':evidence,'type':form[2],'relative_path':suffix+('/*' if has_wildcard else ''),'keys':variants(branchkey,form[3],network) if branchkey else {},'provided_keys':variants(k.key,form[3],network) if k.is_extended else {},'next_index':last,'imported_range':imported_range,'active':row.get('active') if type(row.get('active')) is bool else None,'ranged':has_wildcard,'addresses':addresses})
            groups.setdefault(gid,group)['branches'].extend(new_branches)
        except Exception as exc:
            # Never reflect parser exceptions: they may contain the user's raw input.
            known=('Key origin does not agree','Hardened descendants','Too many branches','Wildcard must','Script trees')
            message=str(exc) if str(exc).startswith(known) else 'Invalid or unsupported public descriptor. Check the exported record and key/network version.'
            issues.append({'record':row_number,'message':message})
    return {'wallet_name':str(raw.get('wallet_name',''))[:200],'groups':list(groups.values()),'issues':issues,'matches':matches,'target':target,'start':start,'count':count,'records':len(raw['descriptors'])}
