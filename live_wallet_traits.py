"""Presentation traits derived from currently connected public Agent inventories."""
from node_wallets import NodeWalletLoader
from policy_accounts import signing_account

def watch_flags(catalog,registry):
    if registry is None:return {}
    active={n['id'] for n in registry.presence()['nodes'] if n.get('connected') and n.get('rpc_ready')}
    inventories=[w for node,inv in list(registry.hub.inventories.items()) if node in active and (inv.get('complete') or inv.get('loaded_complete')) for w in inv.get('wallets',[])]
    result={}
    for wallet in catalog['wallets']:
        keys={NodeWalletLoader.descriptor_key(r['desc']) for r in wallet['raw']['descriptors']}
        matches=[w for w in inventories if keys & {NodeWalletLoader.descriptor_key(r['desc']) for r in w['raw']['descriptors']} or signing_account(w,{'raw':wallet['raw']})]
        if matches:result[wallet['id']]=not any(w.get('private_keys_enabled') for w in matches)
        else:
            sources=[s for s in wallet.get('node_sources',[]) if s['node_id'] in active]
            # Last discovery is a fallback only when the connected node has not
            # yet supplied a complete signed inventory. Never infer private keys
            # from a disconnected replica.
            if sources:result[wallet['id']]=bool(wallet.get('watch_only'))
    return result

def annotate(report,catalog,registry):
    flags=watch_flags(catalog,registry);discs=disc_flags(catalog,registry)
    if report:
        for row in report.get('balances',{}).get('wallets',[]):
            if row.get('descriptor_id') in flags:row['watch_only']=flags[row['descriptor_id']]
            row['disc_only']=discs.get(row.get('descriptor_id'),False)
    return flags

def disc_flags(catalog,registry):
    """Temporary aliases do not establish a persistent node copy."""
    if registry is None:return {}
    active={n['id'] for n in registry.presence()['nodes'] if n.get('connected') and n.get('rpc_ready')}
    inventories=[w for node,inv in list(registry.hub.inventories.items()) if node in active and (inv.get('complete') or inv.get('loaded_complete')) for w in inv.get('wallets',[])]
    result={}
    for wallet in catalog['wallets']:
        keys={NodeWalletLoader.descriptor_key(r['desc']) for r in wallet['raw']['descriptors']}
        matches=[w for w in inventories if keys & {NodeWalletLoader.descriptor_key(r['desc']) for r in w['raw']['descriptors']} or signing_account(w,{'raw':wallet['raw']})]
        result[wallet['id']]=bool(matches) and all(w.get('signing_only') for w in matches)
    return result
