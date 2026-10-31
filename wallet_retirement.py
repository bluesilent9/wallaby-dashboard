"""Zero-funds retirement coordination; archival and deletion require local Agent approval."""
import secrets
from decimal import Decimal,InvalidOperation
from agent_hub import digest,canonical,equivalent
from descriptor_store import DescriptorStore
from preferences import FamilyStore
from node_chain import RegistryRPC
from node_wallets import NodeWalletLoader
from policy_accounts import keys,signing_account

CAPABILITY='wallet_retirement_v2'

def candidates(hub,user_id):
    family=FamilyStore(hub.config).snapshot();catalog=DescriptorStore(hub.config).read();result={}
    for owner in family['ownership']:
        if owner.get('owner_id')!=user_id or not owner.get('confirmed') or owner['kind']!='descriptor':continue
        wallet=next((w for w in catalog['wallets'] if w['id']==owner['source'] and not w.get('extinct')),None)
        if wallet:result[owner['id']]=holder_options(hub,wallet['raw'])
    return result

def holder_options(hub,raw):
    options=[]
    for n in hub.registry.snapshot()['nodes']:
        if not n.get('rpc_ready') or n.get('blockchain',{}).get('state')!='ready' or CAPABILITY not in hub.caps.get(n['id'],set()):continue
        inv=hub.inventories.get(n['id'],{})
        if not inv.get('complete'):continue
        replicas=[w for w in inv['wallets'] if not w.get('signing_only') and not w.get('extinct') and not w.get('scanning') and equivalent(w['raw'],raw)]
        if replicas:options.append({'id':n['id'],'hostname':n['hostname'],'wallet':replicas[0]['name']})
    return options

def related(hub,raw):
    policies=[raw];descs={NodeWalletLoader.descriptor_key(r['desc']) for r in raw['descriptors']}
    # Vault records link derived, fixed-key vault scripts to their original source descriptor.
    for row in hub.read()['vaults'].values():
        v=row['public']
        if NodeWalletLoader.descriptor_key(v['source']['descriptor']) in descs:policies.append({'descriptors':[{'desc':v['descriptor']}]})
    return policies

def zero_balances(hub,raw):
    """Fresh Core accounting includes locked, immature, unsafe and watch-only coins.

    Check policy monitors as well as their key accounts: listunspent alone omits
    locked coins and must never establish that a wallet is empty.
    """
    policies=related(hub,raw);rpc=RegistryRPC(hub.registry);checked=[]
    nodes=hub.registry.snapshot()['nodes']
    for node in nodes:
        if not node.get('connected'):continue
        inventory=hub.inventories.get(node['id'],{})
        if not inventory.get('complete'):raise ValueError('Complete node wallet discovery before retirement.')
        matches=[w for w in inventory['wallets'] if not w.get('signing_only') and any(equivalent(w['raw'],p) or signing_account(w,{'raw':p}) or signing_account({'raw':p,'private_keys_enabled':True},w) for p in policies)]
        if not matches:continue
        if hub.transfers.is_busy(node['id']):raise ValueError('Finish wallet synchronisation before retirement.')
        if not node.get('rpc_ready') or node.get('blockchain',{}).get('state')!='ready':raise ValueError('Synchronise every connected replica before retirement.')
        chain=rpc.call(node['id'],'getblockchaininfo',[])
        if chain.get('chain')!='main' or chain.get('initialblockdownload') or chain.get('blocks')!=chain.get('headers'):raise ValueError('Wait for current mainnet block synchronisation before retirement.')
        for wallet in matches:
            info=rpc.call(node['id'],'getwalletinfo',[],wallet['name'])
            if info.get('scanning') or info.get('lastprocessedblock',{}).get('height',-1)<chain['blocks']:raise ValueError('Finish the wallet rescan and catch up its history before retirement.')
            balances=rpc.call(node['id'],'getbalances',[],wallet['name'])
            groups=[balances[k] for k in ('mine','watchonly') if k in balances]
            if not groups or any(not isinstance(g,dict) or not {'trusted','untrusted_pending','immature'}<=set(g) for g in groups):raise ValueError('Core returned incomplete wallet balances; retirement is blocked.')
            for group in groups:
                for value in group.values():
                    try:amount=Decimal(str(value))
                    except InvalidOperation:raise ValueError('Invalid wallet balance; retirement is blocked.')
                    if not amount.is_finite() or amount<0:raise ValueError('Invalid wallet balance; retirement is blocked.')
                    if amount:raise ValueError('Only zero-balance wallets can become extinct, including vault, multisig, pending and immature funds.')
            checked.append({'node_id':node['id'],'wallet':wallet['name']})
    if not checked:raise ValueError('No current wallet balance could be verified.')
    return checked

def local_targets(hub,raw,node):
    policies=related(hub,raw);wallets=hub.inventories[node]['wallets'];targets=[]
    for w in wallets:
        if w.get('signing_only') or w.get('extinct'):continue
        direct=any(equivalent(w['raw'],p) for p in policies)
        signer=any(signing_account(w,{'raw':p}) for p in policies)
        monitor=any(signing_account({'raw':p,'private_keys_enabled':True},w) for p in policies)
        if signer and not direct:
            # Dedicated keys can be removed; a key serving another policy is retained.
            if any(signing_account(w,m) and not any(equivalent(m['raw'],p) for p in policies) for m in wallets):continue
        if direct or signer or monitor:targets.append({'wallet':w['name'],'public_wallet':w['raw'],'private_keys_enabled':w.get('private_keys_enabled') is True})
    return targets

def known_holders(hub,raw,wallet=None):
    found={}
    if wallet:
        for source in wallet.get('node_sources',[]):found[source['node_id']]=source.get('hostname',source['node_id'])
    for node,inv in hub.inventories.items():
        if any(not w.get('signing_only') and not w.get('extinct') and equivalent(w['raw'],raw) for w in inv.get('wallets',[])):found[node]=node
    for node,entry in hub.read()['nodes'].items():
        if any(equivalent(w['raw'],raw) for w in entry.get('known_wallets',{}).values()):found[node]=node
    names={n['id']:n['hostname'] for n in hub.registry._read()['nodes']}
    return {n:names.get(n,label) for n,label in found.items()}

def _proposal(hub,owner,option,raw):
    targets=local_targets(hub,raw,option['id'])
    local=next((w for w in hub.inventories[option['id']]['wallets'] if w['name']==option['wallet']),None)
    if not targets or not local:raise ValueError('No verified local retirement targets.')
    p={'kind':'wallet_retire','version':2,'wallet':option['wallet'],'public_wallet':local['raw'],'archive_label':owner,'require_zero_balance':True,'delete_wallet':True,'nonce':secrets.token_hex(32),'delete_wallets':targets}
    p['request_id']=digest(p);canonical(p);return p

def propose(hub,session,wallet_id,node,confirmed):
    if not session.user() or not session.access()['ownership_ready']:raise ValueError('Complete Ownership first.')
    if confirmed is not True:raise ValueError('Confirm local Agent review of archival and deletion on every known holder.')
    options=candidates(hub,session.user_id).get(wallet_id,[])
    if not any(n['id']==node for n in options):raise ValueError('Update the Agent: verified retirement and deletion support is required.')
    owner=next(r for r in FamilyStore(hub.config).snapshot()['ownership'] if r['id']==wallet_id)
    wallet=next(w for w in DescriptorStore(hub.config).read()['wallets'] if w['id']==owner['source'])
    checked=zero_balances(hub,wallet['raw']);holders=known_holders(hub,wallet['raw'],wallet)
    ordered=sorted({o['id']:o for o in options}.values(),key=lambda o:(o['id']!=node,o['id']))
    proposals=[(o,_proposal(hub,owner['name'],o,wallet['raw'])) for o in ordered];root=digest(proposals[0][1])
    with hub.edit() as state:
        if any(r['kind']=='wallet_retire' and r.get('wallet_id')==wallet_id and r.get('expected_holders') for r in state['requests'].values()):raise ValueError('This wallet already has a pending or completed retirement. Review its holder checklist.')
        for option,p in proposals:
            key=digest(p);state['requests'][key]={'kind':'wallet_retire','node_id':option['id'],'user_id':session.user_id,'wallet_id':wallet_id,'proposal':p,'state':'pending','created_utc':hub.utc(),'balance_checks':checked,'disc_status':'unconfirmed','root_hash':root}
            if key==root:state['requests'][key].update(expected_holders=holders,retirement_wallet=wallet['raw'])
    return {'request_hash':root,'queued':True,'holder_count':len(holders),'requests_sent':len(proposals)}

def group_rows(state,key):
    return [r for ident,r in state['requests'].items() if r['kind']=='wallet_retire' and (ident==key or r.get('root_hash')==key)]

def group_complete(state,key):
    root=state['requests'][key];rows=group_rows(state,key)
    if root.get('cancel_requested'):return False
    return bool(root.get('expected_holders')) and all(any(r['node_id']==node and r['state']=='complete' for r in rows) for node in root['expected_holders'])

def advance(hub):
    # Reconnection may make a previously offline holder available. Never retry a terminal outcome.
    for key,root in list(hub.read()['requests'].items()):
        if root['kind']!='wallet_retire' or not root.get('expected_holders') or root.get('cancel_requested'):continue
        raw=root['retirement_wallet'];holders=known_holders(hub,raw)
        with hub.edit() as state:state['requests'][key]['expected_holders'].update(holders)
        for option in holder_options(hub,raw):
            state=hub.read()
            if any(r['node_id']==option['id'] for r in group_rows(state,key)):continue
            try:
                checked=zero_balances(hub,raw);p=_proposal(hub,root['proposal']['archive_label'],option,raw)
                with hub.edit() as state:
                    if any(r['node_id']==option['id'] for r in group_rows(state,key)):continue
                    state['requests'][digest(p)]={'kind':'wallet_retire','node_id':option['id'],'user_id':root['user_id'],'wallet_id':root['wallet_id'],'proposal':p,'state':'pending','created_utc':hub.utc(),'balance_checks':checked,'disc_status':'unconfirmed','root_hash':key}
            except (ValueError,OSError,KeyError) as exc:
                with hub.edit() as state:state['requests'][key]['coordination_error']=str(exc)[:1000]

def result(state,node,p):
    allowed={'request_hash','status','message','archive_verified','history_verified','zero_balance_verified','wallet_deleted','archive_wallet','address_count','deleted_wallets'}
    if not isinstance(p,dict) or not {'request_hash','status'}<=set(p) or set(p)-allowed or p['status'] not in ('complete','denied','cancelled','error'):raise ValueError('Invalid retirement result.')
    row=state['requests'].get(p['request_hash'])
    if not row or row['kind']!='wallet_retire' or row['node_id']!=node or not row.get('delivered') or row['proposal'].get('version')!=2:raise ValueError('Unsolicited or obsolete retirement result.')
    if row['state']!='pending':
        if row.get('result')==p:return
        raise ValueError('A retirement outcome cannot change.')
    if p['status']=='complete':
        if not all(p.get(k) is True for k in ('archive_verified','history_verified','zero_balance_verified','wallet_deleted')):raise ValueError('Retirement completion requires archival and deletion confirmation.')
        if not isinstance(p.get('archive_wallet'),str) or not p['archive_wallet'] or type(p.get('address_count')) is not int or p['address_count']<0:raise ValueError('Missing verified historical archive details.')
        deleted=p.get('deleted_wallets')
        if not isinstance(deleted,list) or not deleted or any(not isinstance(n,str) or not n or len(n)>256 for n in deleted) or set(deleted)!={w['wallet'] for w in row['proposal']['delete_wallets']} or p['archive_wallet'] in deleted:raise ValueError('The original wallet deletion was not confirmed.')
    row.update(state=p['status'],result=p,message=str(p.get('message',''))[:1000])

def snapshot(hub,user):
    state=hub.read();result=[]
    for key,r in state['requests'].items():
        if r['kind']!='wallet_retire' or not r.get('expected_holders'):continue
        rows=group_rows(state,key);holders=[]
        for node,hostname in r['expected_holders'].items():
            row=next((w for w in rows if w['node_id']==node),None)
            holders.append({'node_id':node,'hostname':hostname,'state':row['state'] if row else 'cancelled' if r.get('cancel_requested') else 'waiting_node','message':row.get('message','') if row else 'Not requested; process cancelled.' if r.get('cancel_requested') else 'Waiting for this holder to connect, synchronise and support approved deletion.','archive_verified':bool((row or {}).get('result',{}).get('archive_verified')),'wallet_deleted':bool((row or {}).get('result',{}).get('wallet_deleted')),'cancel_requested':bool((row or {}).get('cancel_requested'))})
        pending=any(h['state']=='pending' for h in holders)
        problem=next((h['state'] for h in holders if h['state'] in ('denied','cancelled','error')),None)
        result.append(dict(request_hash=key,name=r['proposal']['archive_label'],state='complete' if group_complete(state,key) else 'cancelling' if r.get('cancel_requested') and pending else 'cancelled' if r.get('cancel_requested') else problem or 'pending',created_utc=r['created_utc'],disc_status=r.get('disc_status','unconfirmed'),message=r.get('coordination_error',''),holders=holders,cancel_requested=bool(r.get('cancel_requested')),can_cancel=r.get('user_id')==user and not r.get('cancel_requested') and not group_complete(state,key),can_confirm_discs=r.get('user_id')==user))
    return result

def confirm_discs(hub,user,key,status):
    if status not in ('destroyed','none'):raise ValueError('Confirm destruction or that no archival discs existed.')
    with hub.edit() as state:
        row=state['requests'].get(key)
        if not row or row['kind']!='wallet_retire' or row.get('user_id')!=user or not row.get('expected_holders'):raise ValueError('Retirement is unavailable for this user.')
        if not group_complete(state,key):raise ValueError('Wait for every known holder to confirm archival and deletion first.')
        row.update(disc_status=status,disc_confirmed_utc=hub.utc())
    return {'confirmed':True}

def suppress_sync(state,raw):
    return any(r['kind']=='wallet_retire' and r.get('proposal',{}).get('version')==2 and r['state'] in ('pending','complete','error') and equivalent(raw,r['proposal']['public_wallet']) for r in state['requests'].values())

def warnings(hub):
    state=hub.read();result=[]
    for key,r in state['requests'].items():
        if r['kind']!='wallet_retire' or not r.get('expected_holders'):continue
        rows=group_rows(state,key);problem=next((v for v in rows if v['state'] in ('denied','cancelled','error')),None);name=r['proposal']['archive_label']
        if r.get('cancel_requested'):text='Wallet extinction cancellation requested for '+name+'. Completed deletions cannot be undone; delivered requests require Agent cancellation or local denial.';severity='red'
        elif problem:text='Wallet retirement '+problem['state']+' for '+name+'. Not all holders have confirmed archival and deletion. Review the local Agent.';severity='red'
        elif not group_complete(state,key):text='Wallet retirement pending for '+name+'. Waiting for archival and deletion confirmation from every known holder, including watch-only or offline nodes.';severity='yellow'
        elif r.get('disc_status') not in ('destroyed','none'):text='Every known holder confirmed retirement for '+name+'. Confirm archival-disc destruction on Wallets.';severity='yellow'
        else:continue
        result.append({'id':'retirement-'+key,'kind':'wallet_retirement','severity':severity,'dismissible':False,'target':'balances','created_utc':r['created_utc'],'text':text})
    return result

def readiness(hub,user,wallet_id):
    family=FamilyStore(hub.config).snapshot();owner=next((r for r in family['ownership'] if r['id']==wallet_id and r.get('owner_id')==user and r.get('confirmed') and r['kind']=='descriptor'),None)
    if not owner:raise ValueError('No confirmed owned descriptor wallet matches this card. Refresh Ownership and Verify Balances.')
    wallet=next((w for w in DescriptorStore(hub.config).read()['wallets'] if w['id']==owner['source'] and not w.get('extinct')),None)
    if not wallet:raise ValueError('The card refers to an outdated wallet identity. Refresh Ownership and Verify Balances.')
    rows=[]
    for n in hub.registry.snapshot()['nodes']:
        inv=hub.inventories.get(n['id'],{});matches=[w for w in inv.get('wallets',[]) if equivalent(w['raw'],wallet['raw'])];reasons=[]
        if not n.get('connected'):reasons.append('Agent disconnected')
        elif not n.get('rpc_ready'):reasons.append('Core RPC is unavailable or the node identity conflicts')
        if n.get('blockchain',{}).get('state')!='ready':reasons.append(n.get('blockchain',{}).get('label') or 'Core synchronisation/health verification is not ready')
        if CAPABILITY not in hub.caps.get(n['id'],set()):reasons.append('Agent has not advertised wallet_retirement_v2; check the running protected worker, not only the installed package')
        if not inv.get('complete'):reasons.append('Wallet discovery incomplete: '+str(inv.get('error') or 'awaiting Agent wallet inventory'))
        elif not matches:reasons.append('No loaded wallet matches the public descriptors; wallet names are not used for matching')
        elif not any(not w.get('signing_only') and not w.get('extinct') and not w.get('scanning') for w in matches):reasons.append('Matching wallet is scanning, temporary signing-only or already extinct')
        rows.append({'id':n['id'],'hostname':n['hostname'],'agent_version':n.get('agent',{}).get('version'),'eligible':not reasons,'reasons':reasons,'matching_wallets':[w['name'] for w in matches]})
    return {'nodes':rows,'options':holder_options(hub,wallet['raw'])}

def cancel(hub,user,key,confirmed):
    if confirmed is not True:raise ValueError('Confirm cancellation first.')
    with hub.edit() as state:
        root=state['requests'].get(key)
        if not root or root['kind']!='wallet_retire' or root.get('user_id')!=user or not root.get('expected_holders'):raise ValueError('Retirement is unavailable for this user.')
        if group_complete(state,key):raise ValueError('All holders already completed retirement; deletion cannot be undone.')
        root.update(cancel_requested=True,cancel_requested_utc=hub.utc())
        for row in group_rows(state,key):
            if row['state']!='pending':continue
            row['cancel_requested']=True
            if not row.get('delivered'):row.update(state='cancelled',cancelled_before_delivery=True,message='Cancelled before delivery; no Agent operation was requested.')
    return {'cancel_requested':True,'message':'Undelivered requests stopped. Delivered requests await local Agent cancellation or denial; completed deletion is not undone.'}
