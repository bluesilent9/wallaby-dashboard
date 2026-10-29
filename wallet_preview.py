"""Verify a signed public wallet snapshot; Dashboard is a courier, not identity authority."""
import base64,hashlib,json,re,subprocess,tempfile,time
from pathlib import Path
from descriptor_store import clean_wallet
from vendor.embit.descriptor import Descriptor
from node_wallets import NodeWalletLoader
from address_book import valid_address
from storage import data_directory

NAMESPACE='wallaby-wallet-preview-v1'
HEX=re.compile('[0-9a-f]{64}')
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def policies(wallet):
    # Full policy set, not wallet names or merely one overlapping key/branch.
    clean_wallet(wallet)
    rows=wallet['descriptors']
    for row in rows:
        d=Descriptor.from_string(row['desc'].split('#')[0])
        if not d.keys or any(k.is_private for k in d.keys):raise ValueError('File synchronisation requires public-key descriptors.')
    return {NodeWalletLoader.descriptor_key(r['desc']) for r in rows}

def validate(payload,now=None,fresh=True):
    from agent_hub import ipv6
    if not isinstance(payload,dict) or set(payload)!={'preview','public_key','signature'}:raise ValueError('Invalid signed wallet preview envelope.')
    p=payload['preview'];fields={'version','transfer_id','challenge','sender_ipv6','receiver_ipv6','public_wallet','chain','funded_outputs','node_tip','generated_at'}
    if not isinstance(p,dict) or set(p)!=fields or type(p['version']) is not int or p['version']!=1 or p['chain']!='main':raise ValueError('Invalid mainnet wallet preview.')
    for key in ('transfer_id','challenge'):
        if not isinstance(p[key],str) or not HEX.fullmatch(p[key]):raise ValueError('Invalid preview identity or challenge.')
    ipv6(p['sender_ipv6']);ipv6(p['receiver_ipv6']);policies(p['public_wallet'])
    tip=p['node_tip']
    if not isinstance(tip,dict) or set(tip)!={'height','hash'} or type(tip['height']) is not int or not 0<=tip['height']<500000000 or not isinstance(tip['hash'],str) or not HEX.fullmatch(tip['hash']):raise ValueError('Invalid preview node tip.')
    stamp=p['generated_at'];now=time.time() if now is None else now
    if type(stamp) is not int or not 0<=stamp<=4102444800:raise ValueError('Invalid preview timestamp.')
    if fresh and not now-900<=stamp<=now+120:raise ValueError('Wallet preview expired or has a future timestamp. Reconcile Core and start a fresh attempt.')
    rows=p['funded_outputs'];seen=set();total=0
    if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Invalid preview output count.')
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'address','txid','vout','amount_sats','time_received','blockheight','confirmations'}:raise ValueError('Invalid preview funded output.')
        if not isinstance(row['txid'],str) or not HEX.fullmatch(row['txid']) or type(row['vout']) is not int or not 0<=row['vout']<100000:raise ValueError('Invalid preview outpoint.')
        if not isinstance(row['amount_sats'],str) or not re.fullmatch('[0-9]{1,16}',row['amount_sats']) or not 0<int(row['amount_sats'])<=2100000000000000:raise ValueError('Preview amounts must be exact integer satoshi strings.')
        if not valid_address(row['address']):raise ValueError('Invalid preview Bitcoin address.')
        for field in ('time_received','blockheight','confirmations'):
            v=row[field]
            if v is not None and (type(v) is not int or not 0<=v<=4102444800):raise ValueError('Invalid preview receipt time, height or confirmations.')
        key=(row['txid'],row['vout'])
        if key in seen:raise ValueError('Duplicate funded preview outpoint.')
        seen.add(key);total+=int(row['amount_sats'])
    if total>2100000000000000:raise ValueError('Invalid preview total supply.')
    pub=payload['public_key']
    if not isinstance(pub,str) or len(pub)>256:raise ValueError('Invalid Agent public identity.')
    parts=pub.split(' ')
    try:key=base64.b64decode(parts[1],validate=True) if len(parts)==2 and parts[0]=='ssh-ed25519' else b''
    except ValueError:raise ValueError('Invalid Ed25519 identity encoding.') from None
    if len(key)!=51 or key[:19]!=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':raise ValueError('Invalid Ed25519 identity key.')
    sig=payload['signature']
    if not isinstance(sig,str) or len(sig)>4096:raise ValueError('Invalid preview signature size.')
    try:raw=base64.b64decode(sig,validate=True)
    except ValueError:raise ValueError('Invalid preview signature encoding.') from None
    if not raw.startswith(b'-----BEGIN SSH SIGNATURE-----'):raise ValueError('Invalid SSH signature file.')
    return {'fingerprint':'SHA256:'+base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('='),'preview_hash':hashlib.sha256(canonical(p)).hexdigest()}

def verify(config,payload,now=None,fresh=True):
    proof=validate(payload,now,fresh);root=data_directory(config);root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.verify-preview-',dir=root) as folder:
        allowed=Path(folder)/'allowed';signature=Path(folder)/'signature'
        allowed.write_text('wallaby-preview '+payload['public_key']+'\n');signature.write_bytes(base64.b64decode(payload['signature'],validate=True))
        result=subprocess.run(['/usr/bin/ssh-keygen','-Y','verify','-f',str(allowed),'-I','wallaby-preview','-n',NAMESPACE,'-s',str(signature)],input=canonical(payload['preview']),capture_output=True,timeout=10)
        if result.returncode:raise ValueError('The sending Agent public preview signature is invalid.')
    return proof
