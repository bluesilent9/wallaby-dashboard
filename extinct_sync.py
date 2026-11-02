"""One public historical container per sync request; ownership stays per label."""
from copy import deepcopy
from descriptor_store import clean_wallet
from node_wallets import NodeWalletLoader

def containers(wallets):
    ordinary=[];historical=[]
    for w in wallets:
        if not w.get('extinct'):ordinary.append(w);continue
        if w.get('private_keys_enabled') or any(not r['desc'].startswith('addr(') for r in w['raw']['descriptors']):raise ValueError('Historical synchronisation requires address-only wallets with private keys disabled.')
        historical.append(w)
    if not historical:return ordinary
    rows={};labels={};names=set()
    for w in historical:
        names.add(w['raw']['wallet_name'])
        for r in w['raw']['descriptors']:
            key=NodeWalletLoader.descriptor_key(r['desc']);prior=rows.get(key)
            if prior is not None and r!=prior:raise ValueError('Conflicting public historical descriptor metadata; refresh the node inventory.')
            rows[key]=deepcopy(r)
        for address,label in w.get('address_labels',{}).items():
            if address in labels and labels[address]!=label:raise ValueError('Conflicting historical address labels; resolve them on the local Agents.')
            labels[address]=deepcopy(label)
    if len(rows)>256:raise ValueError('The Extinct archive exceeds the 256-descriptor sync bound. No addresses were omitted or split into silent partial requests.')
    checked=clean_wallet({'wallet_name':'Extinct','descriptors':[rows[k] for k in sorted(rows)]})
    ordinary.append({'id':checked['id'],'name':'Extinct','raw':checked['raw'],'private_keys_enabled':False,'signing_only':False,'extinct':True,'address_labels':labels,'subwallets':sorted(names,key=str.casefold)})
    return ordinary

def supersede_children(state,target,group):
    """Withdraw obsolete per-child queue entries, retaining their actual outcomes."""
    keys={NodeWalletLoader.descriptor_key(r['desc']) for r in group['raw']['descriptors']}
    old=set()
    for row in state['requests'].values():
        if row.get('kind')!='wallet_transfer' or row.get('direction')!='receive' or row['node_id']!=target:continue
        descriptors=row['public_wallet']['descriptors'];child={NodeWalletLoader.descriptor_key(r['desc']) for r in descriptors}
        if child and child<=keys and (child<keys or row['public_wallet']['wallet_name'] not in ('Extinct','Wallaby Extinct')) and all(r['desc'].startswith('addr(') for r in descriptors) and row.get('state')=='pending':old.add(row['transfer_id'])
    for row in state['requests'].values():
        if row.get('transfer_id') in old:row.update(superseded_container=True,queue_visible=False,message='Replaced in the current request list by the combined Extinct archive; prior local outcomes remain recorded.')
