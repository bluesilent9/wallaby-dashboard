"""Public-only BIP174 construction. Private keys and broadcasting are never accepted."""
import base64,hashlib
from vendor.embit import compact
from vendor.embit.script import Script
from vendor.embit.transaction import Transaction,TransactionInput,TransactionOutput
from descriptor_policy import parse_policy
from descriptor_store import DescriptorStore
from descriptor_snapshot import saved_history
from preferences import resource_id
from fee_planner import plan,live_coins


def public_change_candidate(store,chain,wallet_id,reserved=None):
    wallet=next((w for w in store['wallets'] if w['id']==wallet_id),None)
    if not wallet:raise ValueError('The funding wallet descriptor is missing.')
    choices=[]
    for number,row in enumerate(wallet['raw']['descriptors'],1):
        if row.get('internal') is not True or row.get('active') is not True:continue
        d,combo,hardened=parse_policy(row['desc'].split('#')[0])
        if hardened or combo or d.num_branches!=1 or d.taptree or not d.is_wildcard:continue
        key=wallet_id+':'+hashlib.sha256(row['desc'].encode()).hexdigest()
        used=max((c.get('last_used') if c.get('last_used') is not None else -1 for c in chain.get('coverage',[]) if c['wallet_id']==wallet_id and c.get('descriptor')==number),default=-1)
        index=max(row.get('next_index',0),used+1,(reserved or {}).get(key,0),row.get('range',[0,0])[0])
        if index>row.get('range',[0,0])[1]:continue
        derived=d.derive(index);spk=derived.script_pubkey();kind=spk.script_type()
        if kind not in ('p2wpkh','p2tr','p2sh','p2pkh'):continue
        priority={'p2wpkh':0,'p2tr':1,'p2sh':2,'p2pkh':3}[kind]
        choices.append((priority,number,spk,derived,key,index,bool(d.key and d.key.origin)))
    if not choices:raise ValueError('A current, publicly derivable active change descriptor is needed for '+wallet['name']+'. Refresh listdescriptors false before constructing a PSBT.')
    _,_,spk,derived,key,index,origin=min(choices,key=lambda c:c[:2])
    return spk,derived,key,index,origin


def record(key,value):return compact.to_bytes(len(key))+key+compact.to_bytes(len(value))+value

def output_metadata(derived,has_origin):
    fields=[]
    if derived.redeem_script():fields.append((b'\x00',derived.redeem_script().data))
    if derived.witness_script():fields.append((b'\x01',derived.witness_script().data))
    if derived.is_taproot:
        fields.append((b'\x05',derived.key.get_public_key().xonly()))
    if has_origin:
        for key in derived.keys:
            if not key.origin:continue
            pub=key.get_public_key();path=key.origin.fingerprint+b''.join(i.to_bytes(4,'little') for i in key.origin.derivation)
            fields.append((b'\x07'+pub.xonly(),b'\x00'+path) if derived.is_taproot else (b'\x02'+pub.sec(),path))
    return b''.join(record(k,v) for k,v in sorted(fields))+b'\x00'


def encode_psbt(result,change):
    inputs=[];input_maps=[];used=set();total=0
    for uid in sorted(result['selected']):
        for coin in result['selected'][uid]:
            prev=Transaction.parse(bytes.fromhex(coin['raw_tx']));out=coin['vout'];txid=prev.txid().hex()
            if txid!=coin['txid'] or not 0<=out<len(prev.vout) or prev.vout[out].value!=coin['value'] or prev.vout[out].script_pubkey.data.hex()!=coin['script']:raise ValueError('An input does not match its verified previous transaction.')
            if (txid,out) in used:raise ValueError('Duplicate transaction input.')
            used.add((txid,out));total+=coin['value'];inputs.append(TransactionInput(bytes.fromhex(txid),out,sequence=0xfffffffd))
            fields=record(b'\x00',prev.serialize())
            if coin['witness']:fields+=record(b'\x01',prev.vout[out].serialize())
            input_maps.append(fields+b'\x00')
    from vendor.embit.script import address_to_scriptpubkey
    outputs=[TransactionOutput(value,address_to_scriptpubkey(address)) for address,value in sorted(result['outputs'].items())];maps=[b'\x00' for _ in outputs]
    for uid,value in sorted(result['changes'].items()):
        spk,derived,key,index,origin=change[uid];outputs.append(TransactionOutput(value,spk));maps.append(output_metadata(derived,origin))
    tx=Transaction(version=2,vin=inputs,vout=outputs,locktime=0)
    if total-sum(o.value for o in outputs)!=int(result['total_fee_sats']):raise ValueError('PSBT input/output values do not match the allocated fee.')
    if tx.is_segwit or any(i.script_sig.data for i in inputs):raise ValueError('PSBT must be completely unsigned.')
    payload=b'psbt\xff'+record(b'\x00',tx.serialize())+b'\x00'+b''.join(input_maps)+b''.join(maps)
    if len(payload)>4*1024*1024:raise ValueError('This PSBT is too large for the signing workflow.')
    return base64.b64encode(payload).decode(),tx


def construct(session,catalog,edges,reserved,fixed=None):
    store=DescriptorStore(session.preferences_path).read();chain=saved_history(session.preferences_path,store)
    ids={resource_id('descriptor',w['id']):w['id'] for w in store['wallets']}
    sources={e['from'] for e in edges if e.get('max') or int(e['amount_sats']) or e['fee_rate'].strip('0.')}
    coins=live_coins(session,catalog,sources,force=True);change={}
    # Refresh Core's change cursors, including all replicas, immediately before
    # deriving change. No private keys, generated address RPC or explorer call.
    from node_chain import NodeChain,RegistryRPC
    from node_wallets import NodeWalletLoader
    client=NodeChain(RegistryRPC(session.node_registry),store)
    maximum_sources={e['from'] for e in edges if e.get('max')}
    for uid in sorted(sources):
        if uid in maximum_sources:continue
        if uid not in ids:raise ValueError('A funding descriptor is unavailable.')
        wallet=next(w for w in store['wallets'] if w['id']==ids[uid]);connected={n['id'] for n in client.nodes()};found=False
        for replica in wallet.get('node_sources',[]):
            if replica['node_id'] not in connected:continue
            raw=client.rpc.call(replica['node_id'],'listdescriptors',[False],replica['wallet_name']);found=True
            by_policy={NodeWalletLoader.descriptor_key(r['desc']):r for r in raw['descriptors']}
            for row in wallet['raw']['descriptors']:
                live=by_policy.get(NodeWalletLoader.descriptor_key(row['desc']))
                if live is None:raise ValueError('Core change policy changed. Reload node wallets before constructing a transaction.')
                row['next_index']=max(row.get('next_index') or 0,live.get('next_index') or live.get('next') or 0)
        if not found:raise ValueError('Connect the funding wallet node before constructing a transaction.')
        choice=public_change_candidate(store,chain,ids[uid],reserved);spk,derived,key,index,origin=choice
        reserved[key]=index+1;change[uid]=choice
    result=plan(edges,catalog,coins,{u:v[0] for u,v in change.items()},details=True,fixed=fixed)
    if not result['can_continue']:raise ValueError(result['validation_message'])
    psbt,tx=encode_psbt(result,change)
    summary={k:v for k,v in result.items() if k not in ('selected','changes','outputs','resolved_edges')}
    return {'input_wallets':[uid for uid in sorted(result['selected']) for _ in result['selected'][uid]],'outpoints':[[c['txid'],c['vout']] for rows in result['selected'].values() for c in rows],'psbt':psbt,'unsigned_txid':tx.txid().hex(),'estimate':summary,'edges':result['resolved_edges'],'change':[{'wallet_id':u,'address':change[u][0].address(),'sats':str(n)} for u,n in result['changes'].items()]}
