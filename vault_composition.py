"""Compose a fixed unsigned vault intent before collecting any signatures."""
import base64,copy,uuid
from decimal import Decimal
from signed_psbt import decode,serialize,utxos
from transaction_templates import clean_diagram
from unsigned_psbt import construct
from fee_planner import plan,live_coins,continuation_status,estimate_size
from storage import storage_lock


def combine_unsigned(fixed,extra):
    a=decode(base64.b64decode(fixed));b=decode(base64.b64decode(extra)) if extra else None
    if b:
        if b['tx'].version!=a['tx'].version or b['tx'].locktime:raise ValueError('Additional payments must use the common transaction version and locktime.')
        for key,value in b['global'].items():
            if key==b'\x00':continue
            if key in a['global'] and a['global'][key]!=value:raise ValueError('Conflicting public PSBT metadata.')
            a['global'][key]=value
        a['tx'].vin.extend(b['tx'].vin);a['tx'].vout.extend(b['tx'].vout)
        a['inputs'].extend(b['inputs']);a['outputs'].extend(b['outputs'])
    points=[(i.txid,i.vout) for i in a['tx'].vin]
    if len(points)!=len(set(points)):raise ValueError('A vault input cannot also fund another payment.')
    if any(any(k[0] in (2,7,8,19,20) for k in fields) for fields in a['inputs']):raise ValueError('Only unsigned PSBTs may be composed.')
    a['global'][b'\x00']=a['tx'].serialize()
    fee=sum(o.value for o in utxos(a))-sum(o.value for o in a['tx'].vout)
    if fee<=0:raise ValueError('The combined transaction requires a positive fee.')
    return base64.b64encode(serialize(a)).decode(),a['tx'],fee


def context(hub,session,ident,diagram):
    if not session.access()['templates_ready']:raise ValueError('Refresh Ownership before preparing this vault proposal.')
    value=hub.read()['exports'].get(ident)
    if not value or value['user_id']!=session.user_id or not value.get('unsigned'):raise ValueError('An unsigned vault proposal bound to this profile is required.')
    diagram=clean_diagram(diagram or value['diagram']);locked={n['id'] for n in value['diagram']['nodes']}-{'network-fee'}
    if not locked<={n['id'] for n in diagram['nodes']}:raise ValueError('The fixed vault cards cannot be removed.')
    required={(e['from'],e['to']):e for e in value['diagram']['edges']};normal=[];found=set()
    for e in diagram['edges']:
        pair=(e['from'],e['to'])
        if pair in required:
            r=required[pair]
            if any(e.get(k)!=r.get(k) for k in ('from','to','amount_sats','fee_rate')) or e.get('max'):raise ValueError('Vault proposal payments and fees are immutable.')
            found.add(pair)
        elif e['from'] in locked or e['to'] in locked:raise ValueError('Additional payments cannot connect to fixed vault cards.')
        else:normal.append(e)
    if found!=set(required):raise ValueError('Every fixed vault connection must remain present.')
    from transaction_builder import builder_catalog
    catalog=builder_catalog(session);cards={w['id']:w for w in catalog['wallets']};cards.update({w['id']:w for w in value['wallets']});catalog['wallets']=list(cards.values())
    if any(int(e['amount_sats']) or Decimal(e['fee_rate']) or e.get('max') for e in normal):
        sources={e['from'] for e in normal};users={u['id'] for u in session.family()['users']}
        if any(cards.get(s,{}).get('owner_id') not in users for s in sources):raise ValueError('Assign every funding wallet to a family user.')
    else:normal=[]
    doc=decode(base64.b64decode(value['psbt']));fixed={'inputs':value['input_sizes'],'outputs':[o.script_pubkey.data for o in doc['tx'].vout],'fee_sats':int(value['estimate']['total_fee_sats']),'payments':{o.script_pubkey.address():o.value for o in doc['tx'].vout}}
    return value,diagram,normal,catalog,fixed


def summary(value,other,fixed):
    result=copy.deepcopy(other or value['estimate'])
    fee=int(value['estimate']['total_fee_sats'])+(int(other['total_fee_sats']) if other else 0)
    size=other['vbytes'] if other else value['estimate']['vbytes']
    result.update(total_fee_sats=str(fee),combined_rate=format(Decimal(fee)/size,'f'),vbytes=size)
    if other:result['wallet_fees_sats'].update({k:str(v) for k,v in fixed_fee_shares(value).items()})
    else:result['wallet_fees_sats']={k:str(v) for k,v in fixed_fee_shares(value).items()}
    result['validation_message']='Unsigned vault payments verified; all signatures will cover the final combined transaction.'
    return result


def fixed_fee_shares(value):
    # Display attribution is proportionate; no on-chain input/output attribution is implied.
    return {e['from']:int(e['fee_sats']) for e in value['diagram']['edges'] if e['to']=='network-fee'}


def prepare(hub,session,ident,diagram=None,estimate=False):
    from transaction_workflows import WorkflowStore
    value,diagram,normal,catalog,fixed=context(hub,session,ident,diagram)
    for txid,index in value['outpoints']:
        if hub.rpc(value['node_id'],'gettxout',[txid,index,True]) is None:raise ValueError('A proposed vault input has already been spent.')
    store=WorkflowStore(hub.config)
    # Pending local authorisation is not enough: funding nodes must be ready.
    nodes={n['id']:n for n in hub.registry.snapshot()['nodes']}
    if nodes.get(value['node_id'],{}).get('blockchain',{}).get('state')!='ready':raise ValueError('The vault funding node is not synchronised.')
    if estimate:
        result=plan(normal,catalog,live_coins(session,catalog,{e['from'] for e in normal}),fixed=fixed) if normal else None
        return summary(value,result,fixed)
    with storage_lock(hub.config):base_cursors=dict(store._read()['change_cursors'])
    cursors=dict(base_cursors);other=construct(session,catalog,normal,cursors,fixed=fixed) if normal else None
    encoded,tx,fee=combine_unsigned(value['psbt'],other['psbt'] if other else None)
    outputs={}
    for o in tx.vout:outputs[o.script_pubkey.address()]=outputs.get(o.script_pubkey.address(),0)+o.value
    ready,message=continuation_status(outputs,fee)
    if not ready:raise ValueError(message)
    result=summary(value,other['estimate'] if other else None,fixed)
    if int(result['total_fee_sats'])!=fee:raise ValueError('The combined fee differs from the reviewed plan.')
    assigned=value['input_wallets']+(other['input_wallets'] if other else []);points=value['outpoints']+(other['outpoints'] if other else [])
    owners={w['id']:w.get('owner_id') for w in catalog['wallets']};participants={owners.get(x) for x in assigned}
    if None in participants:raise ValueError('Every signing wallet needs an owner.')
    participants=sorted(participants)
    with storage_lock(hub.config):
        state=store._read();active={tuple(p) for j in state['jobs'].values() if not j.get('cancelled') and not j.get('completed') for p in j.get('outpoints',[])}
        if any(tuple(p) in active for p in points):raise ValueError('An input is already reserved by another prepared transaction.')
        if any(state['change_cursors'].get(k)!=base_cursors.get(k) for k in cursors):raise ValueError('A change reservation changed during preparation. Try again.')
        job=uuid.uuid4().hex
        row={'id':job,'creator_id':session.user_id,'participants':participants,'created_utc':hub.utc(),'cancelled':False,'agent_signing':True,'psbt':encoded,'input_wallets':assigned,'outpoints':points,'wallets':catalog['wallets'],'diagram':diagram,'estimate':result,'unsigned_txid':tx.txid().hex(),'vault_export_id':ident,'vault_intents':[{'vault':value['vault'],'proposal_psbt':value['psbt']}],'signed_uploads':{}}
        state['jobs'][job]=row;state['change_cursors'].update(cursors)
        for user in sorted(set(participants+[session.user_id])):state['notices'].setdefault(user,[]).append({'id':uuid.uuid4().hex,'job_id':job,'text':'Your actions are required to sign a Bitcoin transaction.','created_utc':hub.utc(),'read':user==session.user_id})
        store._write(state)
    return store.public_job(session.user_id,job)
