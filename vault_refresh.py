"""Linked public-policy renewal; no keys or PSBT signatures enter Wallet Builder."""
import copy
from storage import storage_lock
from builder_workspace import BuilderWorkspace,LOCK
from builder_destinations import Destinations,base_wallet,public_rows
from vendor.embit.descriptor import Descriptor
from multisig_psbt import witness_policy
from policy_accounts import keys

def start(hub,user,source,target):
    work=BuilderWorkspace(hub.config)
    with LOCK:
        tx=work.owned(user,'transaction')
        if tx['operations']:raise ValueError('A PSBT has already been prepared. Cancel this build before changing its vault policy.')
        if work.read()['workspaces'].get('wallet'):raise ValueError('Wallet Builder is already in use. Finish or cancel it first.')
        dest=Destinations(hub.config).get(target)
        if dest['base']!=source or dest['height'] is not None:raise ValueError('Choose an ordinary new receiving copy of the same wallet.')
        draft=tx['draft']
        if not any(e['from']==source and e['to']==target for e in draft.get('edges',[])):raise ValueError('Save a payment from this wallet to its own new address first.')
        wallet=base_wallet(hub.config,source);rows=public_rows(wallet,pair=True);desc=Descriptor.from_string(rows[0]['desc']);total=len(desc.keys)
        policy=witness_policy(desc.derive(0).witness_script().data) if desc.witness_script() else {'threshold':1,'height':None}
        roles=[]
        ready={n['id'] for n in hub.wallet_builder.available()}
        for key in desc.keys:
            own=keys({'descriptors':[{'desc':'wpkh('+str(key)+')'}]});found=[]
            for node in ready:
                for account in hub.inventories.get(node,{}).get('wallets',[]):
                    if account.get('private_keys_enabled') and own & keys(account['raw']):found.append((node,account))
            chosen=found[0] if len({n for n,a in found})==1 else None
            label='Signing key '+str(len(roles)+1)
            for setup in hub.wallet_builder.read()['sessions'].values():
                for rid,contribution in setup.get('contributions',{}).items():
                    message=contribution.get('message',{})
                    if str(key).split(']')[-1].rsplit('/',2)[0] in str(message):
                        role=next((r for r in setup['proposal']['cosigners'] if r['id']==rid),None)
                        if role:label=role['name']
            roles.append({'name':label,'node_id':chosen[0] if chosen else '', 'storage':'disc' if chosen and chosen[1].get('signing_only') else 'node','node_locked':bool(chosen)})
        row=work.start(user,'wallet');state=work.read();row=state['workspaces']['wallet']
        row['renewal']={'transaction_workspace':tx['id'],'source':source,'target':target,'previous_height':policy['height'],'roles':copy.deepcopy(roles)}
        row['draft']={'name':wallet['name'],'type':'3' if total>1 else '2','threshold':str(policy['threshold']),'count':str(total),'roles':[{k:v for k,v in r.items() if k!='node_locked'} for r in roles]};row['revision']+=1;work.write(state)
    return work.snapshot(user,'wallet',hub.registry)

def cancel(hub,user):
    work=BuilderWorkspace(hub.config)
    with LOCK:
        w=work.owned(user,'wallet')
        if not w.get('renewal') or w['operations']:raise ValueError('This is not an editable linked vault renewal.')
        work.finish(user,'wallet')
    return {'return_to':'builder'}

def finish(hub,user,height):
    work=BuilderWorkspace(hub.config)
    with LOCK:
        w=work.owned(user,'wallet');r=w.get('renewal');tx=work.owned(user,'transaction')
        if not r or tx['id']!=r['transaction_workspace'] or tx['operations']:raise ValueError('The linked transaction changed. No policy was sent.')
        if type(height) is not int or height<=(r['previous_height'] or 0):raise ValueError('The new vault height must be strictly later than the previous vault height.')
        for chosen,known in zip(w['draft']['roles'],r['roles']):
            if known['storage']=='disc' and chosen['storage']=='node':raise ValueError('An existing disc key cannot be moved to persistent node storage by Dashboard. Load/import it locally on the Agent first, or retain physical disc storage.')
        d=Destinations(hub.config).create(hub.registry,user,r['source'],height)
        store=Destinations(hub.config);saved=store.read();receive_keys=Descriptor.from_string(d['descriptors'][0]['desc']).keys;saved['destinations'][d['id']]['workspace_id']=tx['id'];saved['destinations'][d['id']]['archive_roles']=[{'node_id':chosen['node_id'],'receive_key':str(receive_keys[i]).split(']')[-1].replace("'",'h')} for i,(chosen,known) in enumerate(zip(w['draft']['roles'],r['roles'])) if chosen['storage']=='disc' and known['storage']=='node' and chosen.get('node_id')];store.write(saved)
        # Persist the public destination and exact transition before delivery.
        from wallet_policy_updates import schedule
        schedule(hub,d['id'])
        state=work.read();tx=state['workspaces']['transaction'];swap(tx['draft'],r['target'],d['id']);tx['revision']+=1
        state['workspaces'].pop('wallet');work.write(state)
    return {'return_to':'builder','destination':d['id']}

def swap(draft,old,new):
    for node in draft['nodes']:
        if node['id']==old:node['id']=new
    for e in draft['edges']:
        for field in ('from','to'):
            if e[field]==old:e[field]=new

def remove(hub,user,target):
    work=BuilderWorkspace(hub.config)
    with LOCK:
        tx=work.owned(user,'transaction')
        if tx['operations']:raise ValueError('A prepared transaction cannot change its destination.')
        old=Destinations(hub.config).get(target)
        if not old['height']:raise ValueError('This destination is not vaulted.')
        fresh=Destinations(hub.config).create(hub.registry,user,old['base'])
        state=work.read();tx=state['workspaces']['transaction'];swap(tx['draft'],target,fresh['id']);tx['revision']+=1;work.write(state)
        # Undelivered/delivered public additions may already be approved. Never
        # remove policies from Core. Retain their audit evidence, stop reoffering.
        with hub.edit() as s:
            for row in s['requests'].values():
                if row.get('destination_id')==target and row['state']=='pending':row.update(state='cancelled')
        store=Destinations(hub.config);s=store.read();s['destinations'][target]['policy_requests_started']=False;store.write(s)
    return {'return_to':'builder'}
