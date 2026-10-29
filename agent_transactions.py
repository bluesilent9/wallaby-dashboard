"""PSBT identity, local-node eligibility and correlated approval results."""
import base64,copy,re,time,uuid
from decimal import Decimal
from signed_psbt import decode,utxos,serialize,validate_upload,combine,final_transaction
from unsigned_psbt import record
from descriptor_store import DescriptorStore
from descriptor_snapshot import saved_history
from preferences import FamilyStore,resource_id
from node_chain import NodeChain,RegistryRPC
from vendor.embit.networks import NETWORKS
from agent_hub import digest,equivalent


def current_wallets(hub):
    return DescriptorStore(hub.config).read()

def eligible(hub,wallet_id,capability='partial_signing'):
    # Resource IDs are hashes, not reversible; resolve through the saved Ownership inventory.
    snapshot=FamilyStore(hub.config).snapshot();owned=next((r for r in snapshot.get('ownership',[]) if r['id']==wallet_id and r['kind']=='descriptor'),None)
    if not owned:return []
    catalog=current_wallets(hub);wanted=next((w for w in catalog['wallets'] if w['id']==owned['source']),None)
    if not wanted:return []
    result=[]
    for node in hub.registry.snapshot()['nodes']:
        if not node['rpc_ready'] or node.get('blockchain',{}).get('state')!='ready' or capability not in hub.caps.get(node['id'],set()):continue
        inventory=hub.inventories.get(node['id'],{})
        if not inventory.get('complete'):continue
        replicas=[w for w in inventory['wallets'] if w['private_keys_enabled'] and not w['scanning'] and equivalent(wanted['raw'],w['raw'])]
        if replicas:result.append({'id':node['id'],'hostname':node['hostname'],'wallet':replicas[0]['name']})
    return result


def validate_export(hub,payload,lease):
    from fee_planner import known_scripts
    from vault_records import public_vault
    public_vault(payload['vault'])
    text=payload['psbt']
    if not isinstance(text,str) or len(text)>2*1024*1024:raise ValueError('Invalid exported PSBT size.')
    if not re.fullmatch('[0-9a-f]{64}',str(payload['transaction_hash'])):raise ValueError('Invalid advisory transaction hash.')
    document=decode(base64.b64decode(text,validate=True));catalog=current_wallets(hub);history=saved_history(hub.config,catalog)
    chain=NodeChain(RegistryRPC(hub.registry),catalog);seen=set();input_wallets=[];total=0
    scripts=known_scripts(history);snapshot=FamilyStore(hub.config).snapshot();owners={r['source']:r for r in snapshot['ownership'] if r['kind']=='descriptor' and r.get('confirmed')}
    wallets={w['id']:w for w in catalog['wallets']};cards={};source_totals={};unspent=[]
    for index,vin in enumerate(document['tx'].vin):
        key=(vin.txid.hex(),vin.vout)
        if key in seen:raise ValueError('Duplicate exported transaction input.')
        seen.add(key);prev=chain.raw(key[0]);output=prev.vout[vin.vout] if vin.vout<len(prev.vout) else None
        if output is None:raise ValueError('Invalid exported previous output.')
        claimed=document['inputs'][index].get(b'\x00')
        if claimed:
            from signed_psbt import bounded_transaction
            checked=bounded_transaction(claimed)
            if checked.txid()!=prev.txid() or vin.vout>=len(checked.vout) or checked.vout[vin.vout].serialize()!=output.serialize():raise ValueError('Exported PSBT previous transaction differs from the node.')
        document['inputs'][index][b'\x00']=prev.serialize()
        if b'\x01' in document['inputs'][index] and document['inputs'][index][b'\x01']!=output.serialize():raise ValueError('Exported PSBT input value differs from the node.')
        matches=[uid for uid,values in scripts.items() if output.script_pubkey.data.hex() in values]
        if len(matches)!=1:raise ValueError('Refresh Ownership: an exported funding input cannot be identified uniquely.')
        uid=matches[0];owner=owners.get(uid)
        if not owner or owner.get('owner_id')!=lease['user_id']:raise ValueError('The open diagram profile does not own this exported transaction source.')
        rid=resource_id('descriptor',uid);input_wallets.append(rid);source_totals[rid]=source_totals.get(rid,0)+output.value;total+=output.value
        cards[rid]={'id':rid,'name':owner['name'],'owner_id':owner['owner_id'],'owner':next(u['name'] for u in snapshot['users'] if u['id']==owner['owner_id']),'colour':'neutral','kind':'descriptor','btc':str(Decimal(source_totals[rid])/100000000),'aud':None,'can_send':True,'can_receive':True,'address':output.script_pubkey.address(NETWORKS['main'])}
        coin=chain.rpc.call(lease['node_id'],'gettxout',[key[0],key[1],True]);unspent.append(isinstance(coin,dict) and coin.get('confirmations',0)>0)
    if not input_wallets:raise ValueError('A vault proposal needs funding inputs.')
    from node_wallets import NodeWalletLoader
    root=next((w for w in catalog['wallets'] if any(NodeWalletLoader.descriptor_key(r['desc'])==NodeWalletLoader.descriptor_key(payload['vault']['source']['descriptor']) for r in w['raw']['descriptors'])),None)
    if not root or owners.get(root['id'],{}).get('owner_id')!=lease['user_id']:raise ValueError('The vault source policy does not belong to this profile.')
    if not all(unspent):raise ValueError('An exported transaction input is no longer unspent.')
    fee=total-sum(o.value for o in document['tx'].vout)
    if not 0<fee<=total:raise ValueError('The vault transaction needs a positive, valid network fee.')
    # Composition is permitted only before anybody signs. Never discard an
    # existing signature or relax its sighash to make a proposal composable.
    if any(any(k[0] in (2,7,8,19,20) for k in fields) for fields in document['inputs']):raise ValueError('Send an unsigned vault PSBT from the updated Agent before combining payments.')
    if document['tx'].version!=2:raise ValueError('Vault proposals require transaction version 2.')
    original=base64.b64encode(serialize(document)).decode();proofs={}
    vault_address=payload['vault']['address'];vault_value=0;edges=[]
    # Preserve the signed transaction as a whole. Display edges do not claim
    # that Bitcoin itself maps a particular input to a particular output.
    source=sorted(cards)[0]
    for n,output in enumerate(document['tx'].vout):
        address=output.script_pubkey.address(NETWORKS['main']);ident='vault-output-'+str(n)
        if address==vault_address:vault_value+=output.value
        matches=[uid for uid,values in scripts.items() if output.script_pubkey.data.hex() in values]
        owner=owners.get(matches[0]) if len(matches)==1 else None
        name=owner['name'] if owner else (owners[root['id']]['name']+' Vault' if address==vault_address else address)
        cards[ident]={'id':ident,'name':name,'kind':'wallet','owner_id':owner.get('owner_id') if owner else lease['user_id'] if address==vault_address else None,'owner':next((u['name'] for u in snapshot['users'] if owner and u['id']==owner.get('owner_id')),''),'colour':'neutral','btc':'0','aud':None,'address':address,'can_send':False,'can_receive':True}
        edges.append({'from':source,'to':ident,'amount_sats':str(output.value),'fee_rate':'0','locked':True,'output_index':n})
    if not vault_value:raise ValueError('The exported PSBT does not fund the declared vault address.')
    from fee_planner import input_weight,estimate_size
    sizes=[]
    for output,fields in zip(utxos(document),document['inputs']):
        try:weight,witness=input_weight(output.script_pubkey.data.hex())
        except ValueError:
            witness_script=fields.get(b'\x05')
            if not witness_script:raise ValueError('Vault input lacks its witness policy.')
            import hashlib
            if output.script_pubkey.data!=b'\x00\x20'+hashlib.sha256(witness_script).digest():raise ValueError('Vault input witness script does not match its previous output.')
            # Only the supported mandatory-height policy can enter this workflow.
            from signed_psbt import vault_witness_policy
            pub,height=vault_witness_policy(witness_script)
            if not height<=document['tx'].locktime<500000000 or any(i.sequence==0xffffffff for i in document['tx'].vin):raise ValueError('Vault input locktime is not satisfied by the proposal.')
            weight,witness=164+1+1+73+len(witness_script)+1,True
        sizes.append({'weight':weight,'witness':witness})
    size=estimate_size(sizes,[o.script_pubkey.data for o in document['tx'].vout]);rate=Decimal(fee)/size
    from fee_planner import continuation_status
    payments={}
    for o in document['tx'].vout:
        address=o.script_pubkey.address(NETWORKS['main']);payments[address]=payments.get(address,0)+o.value
    ready,message=continuation_status(payments,fee)
    if not ready or size>100000:raise ValueError(message if not ready else 'The vault proposal exceeds standard transaction size.')
    edges.append({'from':source,'to':'network-fee','amount_sats':'0','fee_rate':format(rate.quantize(Decimal('.00000001')),'f'),'locked':True,'fee_sats':str(fee)})
    original_edges=edges;edges=[];ids=sorted(source_totals)
    fee_parts={u:fee*source_totals[u]//total for u in ids}
    for u in ids[:fee-sum(fee_parts.values())]:fee_parts[u]+=1
    budgets={u:source_totals[u]-fee_parts[u] for u in ids}
    for e in original_edges:
        if e['to']=='network-fee':continue
        remaining=int(e['amount_sats'])
        for u in ids:
            amount=min(remaining,budgets[u])
            if amount:edges.append(dict(e,**{'from':u,'amount_sats':str(amount)}));budgets[u]-=amount;remaining-=amount
        if remaining:raise ValueError('Vault proposal display allocation failed conservation.')
    for u in ids:
        if fee_parts[u]:edges.append({'from':u,'to':'network-fee','amount_sats':'0','fee_rate':format((Decimal(fee_parts[u])/size).quantize(Decimal('.00000001')),'f'),'locked':True,'fee_sats':str(fee_parts[u])})
    nodes=[{'id':uid,'x':40 if uid in input_wallets else 500,'y':40+n*210} for n,uid in enumerate(cards)]+[{'id':'network-fee','x':880,'y':40}]
    return {'psbt':original,'signed_proofs':proofs,'input_wallets':input_wallets,'wallets':list(cards.values()),'diagram':{'nodes':nodes,'edges':edges},'estimate':{'can_continue':True,'total_fee_sats':str(fee),'vbytes':size,'combined_rate':format(rate,'f'),'validation_message':'Unsigned vault proposal verified. Its fixed payments can be combined with other wallets before signing.'},'vault':payload['vault'],'immutable':True,'txid':document['tx'].txid().hex(),'input_sizes':sizes,'unsigned':True,'outpoints':[list(k) for k in sorted(seen)]}


def queue_sign(hub,session,job_id,wallet_id,node):
    from transaction_workflows import WorkflowStore
    row=WorkflowStore(hub.config).job(session.user_id,job_id);wallet=next((w for w in row['wallets'] if w['id']==wallet_id),None)
    if not wallet or wallet.get('owner_id')!=session.user_id:raise ValueError('Only this sending wallet’s owner can request its signature.')
    if row.get('cancelled') or row.get('completed') or wallet_id in row.get('signed_uploads',{}):raise ValueError('This signing card is already complete or the transaction is closed.')
    destination=next((n for n in eligible(hub,wallet_id) if n['id']==node),None)
    if not destination:raise ValueError('This node is offline, downloading, watch-only, or needs Agent partial-signing support.')
    proposal={'kind':'sign_psbt_partial','request_id':digest({'job':job_id,'wallet':wallet_id,'node':node}),'chain':'main','wallet':destination['wallet'],'psbt':row['psbt'],**({'vault_intents':row['vault_intents']} if row.get('vault_intents') else {})};ident=digest(proposal)
    with hub.edit() as state:
        prior=next((r for r in state['requests'].values() if r.get('job_id')==job_id and r.get('wallet_id')==wallet_id and r['state']=='pending'),None)
        if prior and prior['node_id']!=node:raise ValueError('A signing request is already pending on another Agent. Resolve it there first.')
        old=state['requests'].get(ident)
        if old and old['state'] in ('denied','error'):old['state']='pending'  # Explicit human retry; Agent denial still applies.
        state['requests'].setdefault(ident,{'kind':proposal['kind'],'node_id':node,'proposal':proposal,'job_id':job_id,'wallet_id':wallet_id,'state':'pending','created_utc':hub.utc()})
    return {'queued':True,'request_hash':ident,'node_id':node}


def accept_result(hub,state,node,kind,p):
    from transaction_workflows import WorkflowStore
    if not isinstance(p,dict) or not {'request_hash','status'}<=set(p) or set(p)-{'request_hash','status','psbt','txid','message'} or p['status'] not in ('signed','broadcast','denied','error'):raise ValueError('Invalid Agent transaction result.')
    request=state['requests'].get(p['request_hash'])
    expected='sign_psbt_partial' if kind=='partial_sign_result' else 'broadcast_transaction'
    if not request or request['node_id']!=node or request['kind']!=expected:raise ValueError('Unsolicited Agent transaction result.')
    workflows=WorkflowStore.__new__(WorkflowStore);workflows.config=hub.config;workflows.path=data_directory(hub.config)/'transactions'/'workflows.json'
    data=workflows._read();row=data['jobs'].get(request['job_id'])
    # A local denial must still clear an outstanding Agent request after its
    # Dashboard job was cancelled; it never revives or signs that job.
    if p['status'] in ('denied','error'):
        request.update(state=p['status'],message=str(p.get('message',''))[:1000]);return
    if not row or row.get('cancelled') or row.get('completed'):raise ValueError('The requested transaction is closed.')
    if kind=='partial_sign_result':
        if p['status']!='signed' or set(p)-{'request_hash','status','psbt'} or not isinstance(p.get('psbt'),str) or len(p['psbt'])>2*1024*1024:raise ValueError('Return a bounded, partially signed PSBT.')
        assigned=row['input_wallets'];uid=request['wallet_id'];indices=[i for i,w in enumerate(assigned) if w==uid]
        if not indices:raise ValueError('This wallet has no inputs in the transaction.')
        proofs=validate_upload(row['psbt'],p['psbt'].encode(),indices)
        if row.get('signed_uploads',{}).get(uid,{}).get('request_hash')!=p['request_hash']:
            row.setdefault('signed_uploads',{})[uid]={'proofs':proofs,'uploaded_by':next(w['owner_id'] for w in row['wallets'] if w['id']==uid),'uploaded_utc':hub.utc(),'request_hash':p['request_hash'],'node_id':node};row['signing_revision']=row.get('signing_revision',0)+1
            if all(w in row['signed_uploads'] for w in set(assigned)):
                data['notices'].setdefault(row['creator_id'],[]).append({'id':uuid.uuid4().hex,'job_id':row['id'],'text':'All Agent signatures have been verified. Review and broadcast the combined PSBT.','created_utc':hub.utc(),'read':False})
    else:
        if p['status']!='broadcast' or p.get('txid')!=request['txid']:raise ValueError('Broadcast result does not match the approved transaction.')
        row['broadcast_txid']=p['txid'];row['broadcast_utc']=hub.utc();row['signing_revision']=row.get('signing_revision',0)+1
    request['state']='complete';workflows._write(data)


def queue_broadcast(hub,session,job_id,node):
    from transaction_workflows import WorkflowStore
    from storage import storage_lock
    store=WorkflowStore(hub.config);row=store.job(session.user_id,job_id)
    if row['creator_id']!=session.user_id or row.get('cancelled') or row.get('completed') or row.get('broadcast_txid'):raise ValueError('Only the creator can broadcast this active transaction once.')
    destination=next((n for n in hub.registry.snapshot()['nodes'] if n['id']==node and n['rpc_ready'] and n.get('blockchain',{}).get('state')=='ready' and 'broadcast' in hub.caps.get(node,set())),None)
    if not destination:raise ValueError('Select a synchronised Agent with local broadcast approval support.')
    encoded=combine(row['psbt'],row.get('signed_uploads',{}),row['input_wallets']);tx=final_transaction(encoded);txid=tx.txid().hex();proposal={'kind':'broadcast_transaction','request_id':digest({'job':job_id,'node':node}),'chain':'main','psbt':encoded,'txid':txid};ident=digest(proposal)
    with hub.edit() as state:
        old=state['requests'].get(ident)
        if old and old['state'] in ('denied','error'):old['state']='pending'
        state['requests'].setdefault(ident,{'kind':proposal['kind'],'node_id':node,'proposal':proposal,'job_id':job_id,'state':'pending','txid':txid,'created_utc':hub.utc()})
    return {'queued':True,'request_hash':ident,'txid':txid}


def verify_broadcast_result(hub,node,p):
    state=hub.read();request=state['requests'].get(p.get('request_hash')) if isinstance(p,dict) else None
    if not request or request['node_id']!=node or request['kind']!='broadcast_transaction':raise ValueError('Unsolicited broadcast result.')
    if p.get('status') in ('denied','error'):return
    if p.get('txid')!=request['txid']:raise ValueError('Unexpected broadcast transaction identity.')
    result=hub.rpc(node,'getrawtransaction',[p['txid'],False])
    expected=final_transaction(request['proposal']['psbt']).serialize().hex()
    if result!=expected:raise ValueError('The connected node cannot confirm the approved broadcast transaction.')

from storage import data_directory


def signing_state(hub,user,job_id):
    from transaction_workflows import WorkflowStore
    row=WorkflowStore(hub.config).public_job(user,job_id);state=hub.read()
    for wallet in row['signing_wallets']:
        wallet['nodes']=eligible(hub,wallet['id']) if wallet['owner_id']==user and not wallet['complete'] else []
        requests=[dict(r,request_hash=k) for k,r in state['requests'].items() if r.get('job_id')==job_id and r.get('wallet_id')==wallet['id']]
        wallet['request']=requests[-1] if requests else None
        if wallet['request']:
            wallet['request']={k:wallet['request'].get(k) for k in ('state','node_id','message','request_hash')}
    row['broadcast_nodes']=[{'id':n['id'],'hostname':n['hostname']} for n in hub.registry.snapshot()['nodes'] if n['rpc_ready'] and n.get('blockchain',{}).get('state')=='ready' and 'broadcast' in hub.caps.get(n['id'],set())]
    pending=next((dict(r,request_hash=k) for k,r in state['requests'].items() if r.get('job_id')==job_id and r['kind']=='broadcast_transaction'),None)
    row['broadcast_request']={k:pending.get(k) for k in ('state','node_id','message','txid')} if pending else None
    return row


def prepare_export(hub,session,export_id,diagram=None,estimate=False):
    from vault_composition import prepare
    return prepare(hub,session,export_id,diagram,estimate)
