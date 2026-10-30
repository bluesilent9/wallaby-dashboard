"""Bounded native SegWit policies and per-input, distinct-key signature proofs."""
from vendor.embit import hashes
from vendor.embit.descriptor import Descriptor
from vendor.embit.script import Script,Witness

def witness_policy(script):
    from signed_psbt import vault_witness_policy
    # Legacy Wallaby one-key vault remains verifiable from its exact script.
    try:
        pub,height=vault_witness_policy(script)
        return {'keys':[pub],'threshold':1,'height':height,'dummy':False}
    except ValueError:pass
    rest=script;height=None
    if rest and (1<=rest[0]<=5 or 0x51<=rest[0]<=0x60):
        size=rest[0]+1 if rest[0]<=5 else 1;num=rest[:size]
        if rest[size:size+2]==b'\xb1\x69':
            height=(num[0]-0x50) if len(num)==1 else int.from_bytes(num[1:],'little',signed=True);rest=rest[size+2:]
            if not 0<height<500000000:raise ValueError('Invalid mandatory vault height lock.')
    if len(rest)==35 and rest[0]==33 and rest[-1]==0xac:
        keys=[rest[1:34]];threshold=1;body='pk('+keys[0].hex()+')';dummy=False
    elif len(rest)>3 and 0x51<=rest[0]<=0x5f and rest[-1]==0xae and 0x51<=rest[-2]<=0x5f:
        threshold=rest[0]-0x50;count=rest[-2]-0x50;raw=rest[1:-2]
        if len(raw)!=count*34 or any(raw[i]!=33 for i in range(0,len(raw),34)):raise ValueError('Unsupported multisignature script.')
        keys=[raw[i+1:i+34] for i in range(0,len(raw),34)];body='multi('+str(threshold)+','+','.join(k.hex() for k in keys)+')';dummy=True
        if not 1<=threshold<=count or len(set(keys))!=count:raise ValueError('Invalid or duplicate multisignature keys.')
    else:raise ValueError('Unsupported native SegWit policy; use Wallet Builder’s restricted policies.')
    body='wsh('+('and_v(v:after('+str(height)+'),'+body+')' if height else body)+')'
    if Descriptor.from_string(body).witness_script().data!=script:raise ValueError('Non-canonical witness policy.')
    return {'keys':keys,'threshold':threshold,'height':height,'dummy':dummy}

def verified(base,index,fields,spent,complete=True):
    from signed_psbt import witness_items,ecdsa
    raw=spent[index].script_pubkey.data;stack=witness_items(fields[b'\x08']) if b'\x08' in fields else None
    script=base['inputs'][index].get(b'\x05') or fields.get(b'\x05') or (stack[-1] if stack else b'')
    if fields.get(b'\x07') or hashes.sha256(script)!=raw[2:] or b'\x05' in fields and fields[b'\x05']!=script:raise ValueError('Witness policy differs from the funded output.')
    if b'\x03' in fields and fields[b'\x03']!=b'\x01\x00\x00\x00':raise ValueError('Use SIGHASH_ALL for native SegWit.')
    p=witness_policy(script);tx=base['tx'];height=p['height']
    if height and (not height<=tx.locktime<500000000 or tx.vin[index].sequence==0xffffffff):raise ValueError('The transaction does not satisfy its mandatory vault height lock.')
    digest=tx.sighash_segwit(index,Script(script),spent[index].value);pairs={}
    for key,sig in fields.items():
        if key[:1]==b'\x02':
            pub=key[1:]
            if pub not in p['keys']:raise ValueError('Signature key is outside this input’s policy.')
            ecdsa(sig,pub,digest);pairs[pub]=sig
    if stack is not None:
        if not stack or stack[-1]!=script:raise ValueError('Final witness has a different script.')
        sigs=stack[:-1]
        if p['dummy']:
            if not sigs or sigs[0]!=b'':raise ValueError('CHECKMULTISIG requires its empty dummy item.')
            sigs=sigs[1:]
        if len(sigs)!=p['threshold']:raise ValueError('Final witness does not have the exact required signatures.')
        cursor=0
        for sig in sigs:
            matched=False
            while cursor<len(p['keys']):
                pub=p['keys'][cursor];cursor+=1
                try:ecdsa(sig,pub,digest)
                except ValueError:continue
                pairs[pub]=sig;matched=True;break
            if not matched:raise ValueError('Invalid or out-of-order multisignature witness.')
    if complete:
        if len(pairs)<p['threshold']:raise ValueError('This input needs more distinct cosigner signatures.')
        sigs=[pairs[k] for k in p['keys'] if k in pairs][:p['threshold']]
        return {b'\x08':Witness(([b''] if p['dummy'] else [])+sigs+[script]).serialize()}
    if not pairs:raise ValueError('No valid signature was returned for this input.')
    return {b'\x05':script,**{b'\x02'+k:v for k,v in pairs.items()}}

def validate_partial(original,uploaded,indices,previous=None):
    import base64
    from signed_psbt import decode,utxos,verify_input
    base=decode(base64.b64decode(original,validate=True));candidate=decode(uploaded)
    if candidate['global'][b'\x00']!=base['global'][b'\x00']:raise ValueError('The signed PSBT changes the prepared transaction.')
    spent=utxos(base);result={};new=0
    for i in indices:
        incoming=candidate['inputs'][i];old={bytes.fromhex(k):bytes.fromhex(v) for k,v in (previous or {}).get(str(i),{}).items()}
        if not any(k[0] in (2,7,8,19,20) for k in incoming):
            if old:result[str(i)]={k.hex():v.hex() for k,v in old.items()}
            continue
        if spent[i].script_pubkey.script_type()=='p2wsh':
            proof=verified(base,i,incoming,spent,False);new+=sum(1 for k in proof if k[:1]==b'\x02' and k not in old)
            merged=dict(old);merged.update(proof);proof=verified(base,i,merged,spent,False)
        else:proof=verify_input(base,i,incoming,spent);new+=not bool(old)
        result[str(i)]={k.hex():v.hex() for k,v in proof.items()}
    # Signatures for other inputs are never adopted. Validate any present ones as well.
    for i,fields in enumerate(candidate['inputs']):
        if i in indices or not any(k[0] in (2,7,8,19,20) for k in fields):continue
        verified(base,i,fields,spent,False) if spent[i].script_pubkey.script_type()=='p2wsh' else verify_input(base,i,fields,spent)
    if not result:raise ValueError('No valid signature for the selected sending wallet.')
    return result,new

def progress(original,proofs,indices):
    import base64
    from signed_psbt import decode,utxos,verify_input
    base=decode(base64.b64decode(original,validate=True));spent=utxos(base);rows=[]
    for i in indices:
        fields={bytes.fromhex(k):bytes.fromhex(v) for k,v in proofs.get(str(i),{}).items()};required=1;received=0
        if spent[i].script_pubkey.script_type()=='p2wsh':
            script=base['inputs'][i].get(b'\x05') or fields.get(b'\x05')
            if not script:raise ValueError('The original PSBT is missing its witness policy.')
            required=witness_policy(script)['threshold']
            if fields:received=min(required,sum(1 for k in verified(base,i,fields,spent,False) if k[:1]==b'\x02'))
        elif fields:verify_input(base,i,fields,spent);received=1
        rows.append({'input':i,'received':received,'required':required,'complete':received>=required})
    return {'inputs':rows,'complete':bool(rows) and all(r['complete'] for r in rows),'received':min((r['received'] for r in rows),default=0),'required':max((r['required'] for r in rows),default=1)}
