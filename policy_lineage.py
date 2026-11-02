"""Connect policy generations only through locally approved exact transitions."""
from node_wallets import NodeWalletLoader

def expanded(hub,raw):
    values={NodeWalletLoader.descriptor_key(r['desc']) for r in raw['descriptors']}
    transitions=[]
    for r in hub.read()['requests'].values():
        if r.get('kind')=='wallet_policy_refresh' and r.get('state') in ('complete','recovery_required'):
            p=r['proposal'];transitions.append({NodeWalletLoader.descriptor_key(d) for d in [*p['previous_descriptors'],p['receive_descriptor'],p['change_descriptor']]})
    while True:
        prior=len(values)
        for group in transitions:
            if values & group:values|=group
        if len(values)==prior:return values

def related(hub,a,b):return bool(expanded(hub,a)&expanded(hub,b))
