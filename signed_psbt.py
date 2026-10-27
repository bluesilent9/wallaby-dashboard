"""Strict BIP174 upload validation for the single-key policies the builder creates.

All value/script data comes from the original unsigned PSBT. Uploaded metadata
is never trusted for signing hashes or copied into the combined document.
"""
import base64
from vendor.embit import compact,ec,hashes
from vendor.embit.script import Script,Witness
from vendor.embit.transaction import Transaction
from unsigned_psbt import record

MAX_BYTES=8*1024*1024
class Reader:
    def __init__(self,data):self.data=data;self.pos=0
    def take(self,n):
        if n<0 or self.pos+n>len(self.data):raise ValueError('Truncated PSBT data.')
        value=self.data[self.pos:self.pos+n];self.pos+=n;return value
    def number(self,limit=MAX_BYTES):
        first=self.take(1)[0]
        if first<253:value=first
        else:
            n={253:2,254:4,255:8}[first];value=int.from_bytes(self.take(n),'little')
            if value<({2:253,4:65536,8:4294967296}[n]):raise ValueError('Non-canonical PSBT length.')
        if value>limit:raise ValueError('PSBT field exceeds its size limit.')
        return value
    def field(self):return self.take(self.number())
    def end(self):
        if self.pos!=len(self.data):raise ValueError('Unexpected data after the PSBT.')

def bounded_transaction(raw,unsigned=False):
    r=Reader(raw);r.take(4);count=r.number(10000);witness=False
    if count==0:
        if unsigned or r.take(1)!=b'\x01':raise ValueError('Invalid unsigned transaction.')
        witness=True;count=r.number(10000)
    if not count:raise ValueError('Transaction has no inputs.')
    for _ in range(count):
        r.take(36);sc=r.field();r.take(4)
        if unsigned and sc:raise ValueError('The PSBT transaction must have empty input scripts.')
    out=r.number(10000)
    if not out:raise ValueError('Transaction has no outputs.')
    for _ in range(out):r.take(8);r.field()
    if witness:
        for _ in range(count):
            for _ in range(r.number(10000)):r.field()
    r.take(4);r.end();tx=Transaction.parse(raw)
    if tx.serialize()!=raw:raise ValueError('Non-canonical transaction encoding.')
    return tx

def read_map(r):
    fields={}
    while True:
        key=r.field()
        if not key:return fields
        if key in fields:raise ValueError('Duplicate PSBT field.')
        if len(fields)>=10000:raise ValueError('Too many PSBT fields.')
        fields[key]=r.field()

def decode(data):
    if not isinstance(data,bytes) or not data or len(data)>MAX_BYTES:raise ValueError('Select a PSBT file up to 8 MB.')
    if not data.startswith(b'psbt\xff'):
        try:data=base64.b64decode(b''.join(data.split()),validate=True)
        except Exception:raise ValueError('The file is not a binary or Base64 PSBT.') from None
    r=Reader(data)
    if r.take(5)!=b'psbt\xff':raise ValueError('The file is not a PSBT.')
    glob=read_map(r)
    if b'\x00' not in glob or glob.get(b'\xfb',b'\x00'*4)!=b'\x00'*4:raise ValueError('Upload a version 0 PSBT saved from this transaction in Bitcoin Core.')
    tx=bounded_transaction(glob[b'\x00'],unsigned=True)
    inputs=[read_map(r) for _ in tx.vin];outputs=[read_map(r) for _ in tx.vout];r.end()
    return {'global':glob,'tx':tx,'inputs':inputs,'outputs':outputs}

def serialize(document):
    def m(fields):return b''.join(record(k,v) for k,v in sorted(fields.items()))+b'\x00'
    return b'psbt\xff'+m(document['global'])+b''.join(m(i) for i in document['inputs'])+b''.join(m(o) for o in document['outputs'])

def utxos(document):
    result=[]
    for i,inp in enumerate(document['inputs']):
        if b'\x00' not in inp:raise ValueError('The original PSBT is missing a previous transaction.')
        prev=bounded_transaction(inp[b'\x00']);vin=document['tx'].vin[i]
        if prev.txid()!=vin.txid or vin.vout>=len(prev.vout):raise ValueError('Previous transaction does not match its input.')
        value=prev.vout[vin.vout]
        if b'\x01' in inp and inp[b'\x01']!=value.serialize():raise ValueError('Conflicting previous-output data.')
        result.append(value)
    return result

def pushes(raw):
    r=Reader(raw);items=[]
    while r.pos<len(raw):
        n=r.take(1)[0]
        if not 1<=n<=75:raise ValueError('Unsupported final input script.')
        items.append(r.take(n))
    return items

def push(value):
    if not 1<=len(value)<=75:raise ValueError('Invalid signature stack item.')
    return bytes([len(value)])+value

def witness_items(raw):
    r=Reader(raw);items=[r.field() for _ in range(r.number(4))];r.end();return items

def ecdsa(sig,pub,digest):
    if not 9<=len(sig)<=73 or sig[-1]!=1:raise ValueError('Sign every input with SIGHASH_ALL (Bitcoin Core’s default).')
    parsed=ec.Signature.parse(sig[:-1]);key=ec.PublicKey.parse(pub)
    if parsed.serialize()!=sig[:-1]:raise ValueError('Non-canonical ECDSA signature.')
    # Core produces and requires low-S signatures under standard relay policy.
    der=sig[:-1];offset=4+der[3]
    if offset+2>len(der) or der[offset]!=2:raise ValueError('Invalid ECDSA signature.')
    s=int.from_bytes(der[offset+2:],'big')
    if s>0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0 or not key.verify(parsed,digest):raise ValueError('Invalid transaction signature.')

def verify_input(base,index,fields,spent):
    """Return only validated final script/witness fields, never uploaded metadata."""
    tx=base['tx'];spk=spent[index].script_pubkey;raw=spk.data;kind=spk.script_type()
    final_script=fields.get(b'\x07',b'');final_witness=fields.get(b'\x08');partial=[(k[1:],v) for k,v in fields.items() if k[:1]==b'\x02']
    if kind=='p2tr':
        if final_script or partial:raise ValueError('Unexpected Taproot signature fields.')
        stack=witness_items(final_witness) if final_witness is not None else None
        if stack is not None and len(stack)!=1:raise ValueError('Only key-path Taproot signatures are supported.')
        candidates=([stack[0]] if stack else [])+([fields[b'\x13']] if b'\x13' in fields else [])
        if not candidates:raise ValueError('This wallet has an unsigned input.')
        for sig in candidates:
            if len(sig) not in (64,65) or len(sig)==65 and sig[-1]!=1:raise ValueError('Use Taproot SIGHASH_DEFAULT or SIGHASH_ALL.')
            sighash=0 if len(sig)==64 else 1
            if b'\x03' in fields and fields[b'\x03']!=sighash.to_bytes(4,'little'):raise ValueError('Signature hash type does not match its signature.')
            digest=tx.sighash_taproot(index,[v.script_pubkey for v in spent],[v.value for v in spent],sighash=sighash)
            if not ec.PublicKey.from_xonly(raw[2:]).schnorr_verify(ec.SchnorrSig(sig[:64]),digest):raise ValueError('Invalid Taproot signature.')
        return {b'\x08':Witness([candidates[0]]).serialize()}
    if b'\x13' in fields or b'\x03' in fields and fields[b'\x03']!=b'\x01\x00\x00\x00':raise ValueError('Unexpected signature hash or Taproot data.')
    pairs=list(partial);redeem=None
    if kind in ('p2wpkh','p2sh'):
        if final_witness is not None:
            stack=witness_items(final_witness)
            if len(stack)!=2:raise ValueError('Invalid SegWit signature witness.')
            pairs.append((stack[1],stack[0]))
        if kind=='p2wpkh' and final_script:raise ValueError('Native SegWit inputs must have empty scriptSig.')
        if kind=='p2sh' and final_script:
            values=pushes(final_script)
            if len(values)!=1:raise ValueError('Only wrapped native SegWit is supported.')
            redeem=values[0]
    elif kind=='p2pkh':
        if final_witness is not None:raise ValueError('Legacy input cannot have a witness.')
        if final_script:
            values=pushes(final_script)
            if len(values)!=2:raise ValueError('Invalid legacy signature script.')
            pairs.append((values[1],values[0]))
    elif len(raw) in (35,67) and raw[-1:]==b'\xac' and raw[0]==len(raw)-2:
        if final_witness is not None:raise ValueError('Legacy input cannot have a witness.')
        if final_script:
            values=pushes(final_script)
            if len(values)!=1:raise ValueError('Invalid public-key signature script.')
            pairs.append((raw[1:-1],values[0]))
    else:raise ValueError('This input policy is not supported for signed uploads.')
    if not pairs:raise ValueError('This wallet has an unsigned input.')
    canonical=None
    for pub,sig in pairs:
        public=ec.PublicKey.parse(pub);ph=hashes.hash160(pub)
        if kind in ('p2wpkh','p2sh'):
            if len(pub)!=33:raise ValueError('SegWit requires a compressed public key.')
            expected=b'\x00\x14'+ph
            if kind=='p2wpkh' and raw!=expected:raise ValueError('Signature public key does not control this input.')
            if kind=='p2sh':
                if raw!=b'\xa9\x14'+hashes.hash160(expected)+b'\x87' or redeem is not None and redeem!=expected or b'\x04' in fields and fields[b'\x04']!=expected:raise ValueError('Wrapped SegWit script does not match its public key.')
            digest=tx.sighash_segwit(index,Script(b'\x76\xa9\x14'+ph+b'\x88\xac'),spent[index].value)
            ecdsa(sig,pub,digest);proof={b'\x08':Witness([sig,pub]).serialize()}
            if kind=='p2sh':proof[b'\x07']=push(expected)
        else:
            expected=b'\x76\xa9\x14'+ph+b'\x88\xac' if kind=='p2pkh' else bytes([len(pub)])+pub+b'\xac'
            if raw!=expected:raise ValueError('Signature public key does not control this input.')
            ecdsa(sig,pub,tx.sighash_legacy(index,spk));proof={b'\x07':push(sig)+(push(pub) if kind=='p2pkh' else b'')}
        canonical=canonical or proof
    return canonical

def validate_upload(original,uploaded,indices):
    base=decode(base64.b64decode(original,validate=True));candidate=decode(uploaded);spent=utxos(base)
    if candidate['global'][b'\x00']!=base['global'][b'\x00']:raise ValueError('The uploaded PSBT changes the transaction. Sign the downloaded PSBT without changing inputs, recipients, amounts, change or fees.')
    proofs={}
    for i,fields in enumerate(candidate['inputs']):
        if b'\x00' in fields:
            prev=bounded_transaction(fields[b'\x00']);vin=base['tx'].vin[i]
            if prev.txid()!=vin.txid or vin.vout>=len(prev.vout) or prev.vout[vin.vout].serialize()!=spent[i].serialize():raise ValueError('Uploaded previous-transaction data does not match the prepared transaction.')
        if b'\x01' in fields and fields[b'\x01']!=spent[i].serialize():raise ValueError('Uploaded input value or script was changed.')
        signed=any(k[:1] in (b'\x02',b'\x13',b'\x07',b'\x08') for k in fields)
        if signed or i in indices:
            try:proof=verify_input(base,i,fields,spent)
            except Exception as exc:raise ValueError('Input '+str(i+1)+': '+str(exc)) from None
            if i in indices:proofs[str(i)]={k.hex():v.hex() for k,v in proof.items()}
    if len(proofs)!=len(indices):raise ValueError('Sign every input for the selected wallet before uploading.')
    return proofs

def combine(original,uploads,input_wallets):
    base=decode(base64.b64decode(original,validate=True));spent=utxos(base)
    if len(input_wallets)!=len(base['inputs']):raise ValueError('Missing input ownership assignments.')
    for i,uid in enumerate(input_wallets):
        fields=uploads.get(uid,{}).get('proofs',{}).get(str(i))
        if not fields:raise ValueError('Every sending wallet must provide its signed PSBT first.')
        fields={bytes.fromhex(k):bytes.fromhex(v) for k,v in fields.items()};proof=verify_input(base,i,fields,spent);base['inputs'][i].update(proof)
    return base64.b64encode(serialize(base)).decode()
