"""Verify public setup evidence using the Agent's domain-separated Ed25519 identity."""
import base64,hashlib,json,subprocess,tempfile
from pathlib import Path
from storage import data_directory
NAMESPACE='wallaby-wallet-setup-v1'
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def text(value,limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 for c in value):raise ValueError('Invalid public setup text.')
    return value
def fingerprint(value):
    text(value,256)
    parts=value.split(' ')
    try:raw=base64.b64decode(parts[1],validate=True) if len(parts)==2 and parts[0]=='ssh-ed25519' else b''
    except ValueError:raw=b''
    if len(raw)!=51 or raw[:19]!=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':raise ValueError('Invalid Agent Ed25519 identity.')
    return 'SHA256:'+base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
def verify(store,envelope,expected,namespace=NAMESPACE):
    if not isinstance(envelope,dict) or set(envelope)!={'message','public_key','signature'} or envelope['public_key']!=expected:raise ValueError('Contribution comes from a different Agent identity.')
    fingerprint(expected)
    root=data_directory(store);root.mkdir(parents=True,exist_ok=True)
    if not isinstance(envelope['signature'],str) or len(envelope['signature'])>4096:raise ValueError('Invalid contribution signature.')
    with tempfile.TemporaryDirectory(dir=root) as path:
        allow=Path(path)/'allowed';sig=Path(path)/'signature';allow.write_text('wallaby '+expected+'\n')
        try:sig.write_bytes(base64.b64decode(envelope['signature'],validate=True))
        except Exception:raise ValueError('Invalid contribution signature encoding.') from None
        r=subprocess.run(['/usr/bin/ssh-keygen','-Y','verify','-f',str(allow),'-I','wallaby','-n',namespace,'-s',str(sig)],input=canonical(envelope['message']),capture_output=True,timeout=10)
        if r.returncode:raise ValueError('Authenticated contribution verification failed.')
    return envelope['message']
