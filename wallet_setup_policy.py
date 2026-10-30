"""Public, authenticated setup messages. Policies are constructed, never interpreted as commands."""
import re,time
from agent_hub import digest
from setup_signatures import text
NETWORK={"main","test","signet","regtest"}
from setup_signatures import fingerprint
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.arguments import Key
from vendor.embit.descriptor.checksum import checksum

HEX=re.compile(r'[0-9a-f]{64}')

def proposal(p,fresh=True):
    if not isinstance(p,dict):raise ValueError('Invalid wallet setup message.')
    if p.get('kind')=='wallet_setup_complete':
        if set(p)!={'kind','version','proposal_hash','policy_hash','acknowledgements'} or p['version']!=1 or any(not HEX.fullmatch(str(p[k])) for k in ('proposal_hash','policy_hash')) or not isinstance(p['acknowledgements'],list) or len(p['acknowledgements'])>64:raise ValueError('Invalid completion proof fields.')
        return p
    if p.get('kind')=='wallet_setup_identity':
        if set(p)!={'kind','version','challenge'} or p['version']!=1 or not HEX.fullmatch(str(p['challenge'])):raise ValueError('Invalid identity challenge.')
        return p
    if p.get('kind')=='wallet_retire':
        from wallet_retirement import validate
        return validate(p)
    if p.get('kind')=='wallet_setup':
        if set(p)!={'kind','version','session_id','chain','name','threshold','cosigners','height','expires_at'} or p['version']!=1:raise ValueError('Invalid setup proposal fields.')
        if not HEX.fullmatch(str(p['session_id'])) or p['chain'] not in NETWORK or type(p['expires_at']) is not int or p['expires_at']<=0 or fresh and not time.time()<p['expires_at']<=time.time()+86400:raise ValueError('Expired or invalid setup session.')
        text(p['name'],128)
        if '|' in p['name']:raise ValueError('Wallet name must not contain archive separators.')
        if not isinstance(p['cosigners'],list) or not 1<=len(p['cosigners'])<=15 or type(p['threshold']) is not int or not 1<=p['threshold']<=len(p['cosigners']):raise ValueError('Use 1 to 15 independent cosigners and a valid threshold.')
        if p['height'] is not None and (type(p['height']) is not int or not 0<p['height']<500000000):raise ValueError('Vault height must be a block height, not a timestamp.')
        ids=set()
        for role in p['cosigners']:
            if not isinstance(role,dict) or set(role)!={'id','name','agent_key','storage'} or role['storage'] not in ('node','disc'):raise ValueError('Invalid cosigner assignment.')
            text(role['id'],64);text(role['name'],128);fingerprint(role['agent_key'])
            if role['id'] in ids:raise ValueError('Duplicate cosigner identity.')
            ids.add(role['id'])
        return p
    if p.get('kind')=='wallet_setup_policy':
        if set(p)!={'kind','version','proposal','contributions','receive_descriptor','change_descriptor'} or p['version']!=1:raise ValueError('Invalid complete policy message.')
        proposal(p['proposal'],fresh)
        if not isinstance(p['contributions'],list) or len(p['contributions'])!=len(p['proposal']['cosigners']):raise ValueError('Every cosigner contribution is required.')
        for desc in (p['receive_descriptor'],p['change_descriptor']):text(desc,20000)
        return p
    raise ValueError('Unsupported wallet setup request.')

def public_key(value,branch,chain):
    text(value,512)
    # A fixed, unhardened receive/change suffix; no arbitrary paths or multipaths.
    if not re.fullmatch(r'\[[0-9a-f]{8}/84[h\x27]/(?:0|1)[h\x27]/0[h\x27]\](?:xpub|tpub)[1-9A-HJ-NP-Za-km-z]{80,120}/'+str(branch)+r'/\*',value):raise ValueError('Unexpected account key origin or derivation.')
    key=Key.from_string(value)
    if key.is_private or not key.is_wildcard:raise ValueError('Only a public account contribution is accepted.')
    encoded=value.split(']')[1]
    if encoded.startswith('xpub')!=(chain=='main'):raise ValueError('Contribution network mismatch.')
    expected=0 if chain=='main' else 1
    if not value.split(']')[0].replace("'",'h').endswith('/84h/'+str(expected)+'h/0h'):raise ValueError('Contribution coin type mismatch.')
    return key

def policy(p,contributions,fresh=True):
    proposal(p,fresh);roles={r['id']:r for r in p['cosigners']};seen=set();keys={0:[],1:[]};account_ids=set()
    for c in contributions:
        if not isinstance(c,dict) or set(c)!={'session_id','proposal_hash','cosigner_id','receive_key','change_key'} or c['session_id']!=p['session_id'] or c['proposal_hash']!=digest(p) or c['cosigner_id'] not in roles or c['cosigner_id'] in seen:raise ValueError('Contribution identity or proposal binding differs.')
        seen.add(c['cosigner_id']);accounts=[]
        for branch,field in ((0,'receive_key'),(1,'change_key')):
            k=public_key(c[field],branch,p['chain']);accounts.append(k.key.to_base58());keys[branch].append((c['cosigner_id'],c[field]))
        if accounts[0]!=accounts[1] or accounts[0] in account_ids:raise ValueError('Cosigners must use independent keys and matching branch accounts.')
        account_ids.add(accounts[0])
    if seen!=set(roles):raise ValueError('Missing cosigner contribution.')
    result=[]
    for branch in (0,1):
        values=[v for _,v in sorted(keys[branch])]
        if len(values)==1:
            body='wpkh('+values[0]+')' if p['height'] is None else 'wsh(and_v(v:after('+str(p['height'])+'),pk('+values[0]+')))'
        else:
            spend=('sortedmulti(' if p['height'] is None else 'multi(')+str(p['threshold'])+','+','.join(values)+')'
            body='wsh('+spend+')' if p['height'] is None else 'wsh(and_v(v:after('+str(p['height'])+'),'+spend+'))'
        # The exact restricted construction has no alternative branch or Taproot key path.
        Descriptor.from_string(body);result.append(body+'#'+checksum(body))
    return result
