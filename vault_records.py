"""Narrow public vault wire format. No server labels, paths, RPC names or secrets."""
import base64,re
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from vendor.embit.networks import NETWORKS
from agent_rpc import PRIVATE

NETWORK={'main':'main','test':'test','signet':'signet','regtest':'regtest'}

def text(value,limit=256):
    if not isinstance(value,str) or not 1<=len(value)<=limit or any(ord(c)<32 for c in value):raise ValueError('Invalid text field.')
    return value

def psbt_text(value):
    text(value,2*1024*1024)
    try:raw=base64.b64decode(value,validate=True)
    except Exception:raise ValueError('Invalid base64 PSBT.') from None
    if not raw.startswith(b'psbt\xff') or PRIVATE.search(value):raise ValueError('Invalid PSBT.')
    return value

def public_vault(v):
    if not isinstance(v,dict) or set(v)!={'version','chain','descriptor','address','height','source','timestamp'} or type(v['version']) is not int or v['version']!=1:raise ValueError('Unsupported public vault record.')
    if v['chain'] not in NETWORK or type(v['height']) is not int or not 0<v['height']<500000000:raise ValueError('Invalid vault network or height.')
    desc=text(v['descriptor'],1000);body,sep,check=desc.partition('#')
    match=re.fullmatch(r'wsh\(and_v\(v:pk\(([0-9a-f]{66})\),after\(([1-9][0-9]{0,8})\)\)\)',body)
    if not sep or check!=checksum(body) or not match or int(match[2])!=v['height']:raise ValueError('Vault must have one public key and one mandatory block-height lock, with no bypass.')
    d=Descriptor.from_string(body)
    if d.script_pubkey().address(NETWORKS[NETWORK[v['chain']]])!=v['address']:raise ValueError('Vault address does not match its public policy.')
    src=v['source']
    if not isinstance(src,dict) or set(src)!={'descriptor','index'} or type(src['index']) is not int or not 0<=src['index']<2**31:raise ValueError('Invalid key derivation reference.')
    original=text(src['descriptor'],2000)
    if PRIVATE.search(original):raise ValueError('Private keys are forbidden in public synchronisation.')
    base,sep,check=original.partition('#')
    if not sep or check!=checksum(base):raise ValueError('Invalid source descriptor checksum.')
    owner=Descriptor.from_string(base)
    if any(k.is_private for k in owner.keys) or len(owner.keys)!=1:raise ValueError('Only a single public spending key is supported.')
    derived=owner.derive(src['index']) if owner.is_wildcard else owner
    if derived.key.get_public_key().sec().hex()!=match[1]:raise ValueError('Vault key does not match its source descriptor.')
    if type(v['timestamp']) is not int or not 0<=v['timestamp']<=4102444800:raise ValueError('Invalid vault history timestamp.')
    return v
