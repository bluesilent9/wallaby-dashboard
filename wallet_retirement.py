"""Zero-funds retirement coordination; archival and deletion require local Agent approval."""
import secrets,time
from decimal import Decimal,InvalidOperation
from agent_hub import digest,canonical,equivalent
from descriptor_store import DescriptorStore
from preferences import FamilyStore
from node_chain import RegistryRPC
from node_wallets import NodeWalletLoader
from policy_accounts import keys,signing_account

CAPABILITY='wallet_retirement_v3'

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

def archive_options(hub):
    """Existing local containers only; disabled sync is not permission to create one."""
    result=[]
    for node in hub.registry.snapshot()['nodes']:
        inv=hub.inventories.get(node['id'],{})
        if not node.get('rpc_ready') or node.get('blockchain',{}).get('state')!='ready' or not inv.get('complete') or CAPABILITY not in hub.caps.get(node['id'],set()):continue
        archives=inv.get('archive_wallets',[])
        for name in sorted(set(archives)):
            result.append({'id':node['id'],'hostname':node['hostname'],'wallet':name})
    return result

def history_manifest(hub,raw,label):
    """Union of confirmed used addresses, including change, across current holders.

    Only public chain records leave Core. Never infer completeness from a zero
    balance or omit a replica whose history cannot be read.
    """
    addresses={};transactions={}
    from labelled_wallets import label_parts
    for option in holder_options(hub,raw):
        node=option['id']
        for target in local_targets(hub,raw,node):
            wallet=target['wallet'];start=0;seen=set()
            while True:
                rows=hub.rpc(node,'listtransactions',['*',1000,start,True],wallet)
                if not isinstance(rows,list):raise ValueError('Incomplete retirement history.')
                for row in rows:
                    txid=row['txid']
                    if row.get('confirmations',0)<=0:raise ValueError('Confirm or resolve every pending/conflicted transaction before extinction.')
                    if txid in seen:continue
                    seen.add(txid);tx=hub.rpc(node,'gettransaction',[txid,True],wallet)
                    if tx.get('confirmations',0)<=0 or not tx.get('blockhash'):raise ValueError('Retirement history contains an unconfirmed transaction.')
                    if hub.rpc(node,'getblockheader',[tx['blockhash']]).get('confirmations',0)<=0:raise ValueError('Retirement history is outside the active chain.')
                    item={'txid':txid,'blockhash':tx['blockhash']}
                    if txid in transactions and transactions[txid]!=item:raise ValueError('Connected nodes disagree about retirement history.')
                    transactions[txid]=item
                    decoded=hub.rpc(node,'decoderawtransaction',[tx['hex']])
                    for output in decoded['vout']:
                        address=output['scriptPubKey'].get('address')
                        if not address:continue
                        info=hub.rpc(node,'getaddressinfo',[address],wallet)
                        if not (info.get('ismine') or info.get('iswatchonly')):continue
                        lost=[v for v in info.get('labels',[]) if ' | LOST:' in v]
                        if len(lost)>1:raise ValueError('Ambiguous historical loss labels.')
                        assigned=label+' | LOST:'+lost[0].split(' | LOST:',1)[1] if lost else label;label_parts(assigned)
                        prior=addresses.get(address)
                        if prior and ' | LOST:' in prior['label']:
                            if lost and assigned!=prior['label']:raise ValueError('Conflicting loss labels.')
                            assigned=prior['label']
                        addresses[address]={'address':address,'label':assigned}
                start+=len(rows)
                if len(rows)<1000:break
                if start>=100000:raise ValueError('Retirement history exceeds the review limit; no records were omitted.')
    if not addresses or not transactions:raise ValueError('No complete confirmed history is available to preserve.')
    manifest={'addresses':sorted(addresses.values(),key=lambda r:r['address']),'transactions':sorted(transactions.values(),key=lambda r:r['txid'])}
    if len(canonical(manifest).encode())>512*1024:raise ValueError('Retirement evidence exceeds the safe request size; no partial archive was requested.')
    return manifest

def proposal(raw,label,operation,phase,option,manifest,preservation=None,targets=None):
    p={'kind':'wallet_retire','version':3,'operation_id':operation,'phase':phase,'wallet':option['wallet'],'public_wallet':raw,'archive_label':label,'require_zero_balance':True,'delete_wallet':phase=='delete','nonce':secrets.token_hex(32),'manifest':manifest,'manifest_hash':digest(manifest),'delete_wallets':targets or [],'preservation':preservation}
    p['request_id']=digest(p);canonical(p);return p

def _row(hub,p,node,user,wallet_id,root,checked):
    return {'kind':'wallet_retire','node_id':node,'user_id':user,'wallet_id':wallet_id,'proposal':p,'state':'pending','created_utc':hub.utc(),'balance_checks':checked,'disc_status':'unconfirmed','root_hash':root,'generation':1}

def propose(hub,session,wallet_id,node,confirmed):
    if not session.user() or not session.access()['ownership_ready']:raise ValueError('Complete Ownership first.')
    if confirmed is not True:raise ValueError('Confirm local Agent review of preservation and deletion.')
    options=candidates(hub,session.user_id).get(wallet_id,[])
    if not any(n['id']==node for n in options):raise ValueError('Update the Agent: wallet_retirement_v3 local approval support is required.')
    owner=next(r for r in FamilyStore(hub.config).snapshot()['ownership'] if r['id']==wallet_id)
    owner=dict(owner,name=__import__('wallet_names').dashboard_name(owner))
    wallet=next(w for w in DescriptorStore(hub.config).read()['wallets'] if w['id']==owner['source'])
    from wallet_renames import RenameQueue
    if any(o['state']=='active' and equivalent(o['raw'],wallet['raw']) for o in RenameQueue(hub).read()['operations'].values()):raise ValueError('Finish the wallet rename before making it extinct.')
    archives=archive_options(hub)
    if not archives:raise ValueError('Connect an up-to-date Agent with an existing Extinct wallet and wallet_retirement_v3 support. No deletion was requested.')
    checked=zero_balances(hub,wallet['raw']);manifest=history_manifest(hub,wallet['raw'],owner['name']);holders=known_holders(hub,wallet['raw'],wallet)
    operation=secrets.token_hex(32)
    proposals=[(o,proposal(wallet['raw'],owner['name'],operation,'preserve',o,manifest)) for o in archives];root=digest(proposals[0][1])
    with hub.edit() as state:
        for old_key,old in state['requests'].items():
            if old['kind']!='wallet_retire' or old.get('wallet_id')!=wallet_id or not old.get('expected_holders'):continue
            prior=group_rows(state,old_key)
            safely_cancelled=old.get('cancel_requested') and all(r['state']!='pending' for r in prior) and not any(r.get('result',{}).get('wallet_deleted') for r in prior)
            if not safely_cancelled:raise ValueError('This wallet already has a pending or completed retirement. Finish cancellation and reconcile any completed deletion before starting again.')
        for option,p in proposals:
            key=digest(p);state['requests'][key]=_row(hub,p,option['id'],session.user_id,wallet_id,root,checked)
            if key==root:state['requests'][key].update(expected_holders=holders,retirement_wallet=wallet['raw'])
    return {'request_hash':root,'queued':True,'holder_count':len(holders),'requests_sent':len(proposals)}

def group_rows(state,key):
    return [r for ident,r in state['requests'].items() if r['kind']=='wallet_retire' and not r.get('node_deleted') and (ident==key or r.get('root_hash')==key)]

def group_complete(state,key):
    root=state['requests'][key];rows=group_rows(state,key)
    if root.get('cancel_requested'):return False
    preserved=any(r['proposal'].get('phase')=='preserve' and r.get('archive_checked') and r['state']=='complete' for r in rows)
    return preserved and bool(root.get('expected_holders')) and all((latest_delete(rows,node) or {}).get('state')=='complete' for node in root['expected_holders'])

def verify_archive(hub,row):
    """Read back the exact addresses, labels and confirmed transaction records."""
    p=row['proposal'];node=row['node_id'];name=p['wallet']
    if not any(o['id']==node and o['wallet']==name for o in archive_options(hub)):raise ValueError('Wait for the preserving Extinct wallet to reconnect and finish discovery.')
    from wallet_policy_updates import verified
    receipt=verified(hub,node,row['result'].get('archive_receipt'))
    expected={'version':1,'kind':'wallet_retirement_preservation','request_hash':digest(p),'operation_id':p['operation_id'],'manifest_hash':p['manifest_hash'],'archive_wallet':name,'address_count':len(p['manifest']['addresses']),'wallet_deleted':False}
    if receipt!=expected:raise ValueError('The Agent preservation signature does not match the exact retirement evidence.')
    info=hub.rpc(node,'getwalletinfo',[],name)
    chain=hub.rpc(node,'getblockchaininfo',[])
    if info.get('private_keys_enabled') is not False or info.get('scanning') or info.get('lastprocessedblock',{}).get('height',-1)<chain['blocks']:raise ValueError('The preserving Extinct wallet is not fully verified and current.')
    for item in p['manifest']['addresses']:
        own=hub.rpc(node,'getaddressinfo',[item['address']],name)
        if not (own.get('ismine') or own.get('iswatchonly')) or own.get('labels')!=[item['label']]:raise ValueError('Extinct address or label readback does not match the preserved manifest.')
    for item in p['manifest']['transactions']:
        tx=hub.rpc(node,'gettransaction',[item['txid'],True],name)
        if tx.get('confirmations',0)<=0 or tx.get('blockhash')!=item['blockhash']:raise ValueError('Extinct transaction history is incomplete or changed.')
        if hub.rpc(node,'getblockheader',[tx['blockhash']]).get('confirmations',0)<=0:raise ValueError('Preserved history is outside the active chain.')
    connections=tuple(sorted(k for k,c in hub.registry.connections.items() if c['node_id']==node))
    cache=getattr(hub,'retirement_readbacks',None)
    if cache is None:hub.retirement_readbacks={};cache=hub.retirement_readbacks
    cache[digest(p)]=(time.monotonic(),connections)
    return {'node_id':node,'request_hash':digest(p),'archive_wallet':name,'manifest_hash':p['manifest_hash'],'verified_utc':hub.utc(),'receipt':row['result']['archive_receipt']}

def advance(hub):
    # Only this coordinator releases deletion requests after independent readback.
    for key,root in list(hub.read()['requests'].items()):
        if root['kind']!='wallet_retire' or root.get('proposal',{}).get('version')!=3 or not root.get('expected_holders') and not root.get('node_removal_parked') or root.get('cancel_requested'):continue
        try:
            raw=root['retirement_wallet'];holders=known_holders(hub,raw)
            if root.get('node_removal_parked') and not holders:
                portable=any(v['state']=='active' and v['request'].get('operation_id')==root['proposal']['operation_id'] for v in __import__('archive_queue').ArchiveQueue(hub).read()['requests'].values())
                if not portable or not archive_options(hub):continue
            with hub.edit() as state:state['requests'][key]['expected_holders'].update(holders)
            rows=group_rows(hub.read(),key)
            options=holder_options(hub,raw);online={n['id'] for n in hub.registry.snapshot()['nodes'] if n.get('rpc_ready') and hub.inventories.get(n['id'],{}).get('complete')};present={o['id'] for o in options}
            with hub.edit() as state:
                absent=state['requests'][key].setdefault('absent_holders',[])
                for node in online-present:
                    if (latest_delete(rows,node) or {}).get('state')=='complete' and node not in absent:absent.append(node)
            absent=set(hub.read()['requests'][key].get('absent_holders',[]))
            needs_delete=any(latest_delete(rows,o['id']) is None or o['id'] in absent for o in options)
            needs_archive=any(not any(r['node_id']==o['id'] and r['proposal']['phase']=='preserve' and r['proposal']['wallet']==o['wallet'] for r in rows) for o in archive_options(hub))
            from archive_queue import ArchiveQueue
            if not needs_delete and not needs_archive and not ArchiveQueue(hub).needs_retired_disc(root):continue
            preservation=None;verification_error=None
            for row in rows:
                if row['proposal']['phase']!='preserve' or row['state']!='complete':continue
                try:verified=verify_archive(hub,row)
                except (ValueError,OSError,KeyError) as exc:
                    verification_error=str(exc)[:1000];continue
                with hub.edit() as state:state['requests'][digest(row['proposal'])]['archive_checked']=True
                if preservation is None:preservation=verified
            if preservation is None:
                if verification_error:
                    with hub.edit() as state:state['requests'][key]['coordination_error']=verification_error
                continue
            queue_late_archives(hub,key,root,preservation)
            from archive_queue import ArchiveQueue
            ArchiveQueue(hub).retired_discs(root,preservation)
            if group_complete(hub.read(),key) and not needs_delete:continue
            # Preserve completion cannot authorise destruction of new activity.
            checked=zero_balances(hub,raw)
            current=history_manifest(hub,raw,root['proposal']['archive_label'])
            frozen=root['proposal']['manifest']
            if any(r not in frozen[k] for k in ('addresses','transactions') for r in current[k]):raise ValueError('Wallet history changed after preservation. No additional deletions requested; reconcile the extinction.')
            for option in holder_options(hub,raw):
                state=hub.read()
                if latest_delete(group_rows(state,key),option['id']) and option['id'] not in absent:continue
                local_archives=[r for r in group_rows(state,key) if r['node_id']==option['id'] and r['proposal']['phase']=='preserve']
                if any(r['state']!='complete' or not r.get('archive_checked') for r in local_archives):continue
                targets=local_targets(hub,raw,option['id'])
                if not targets:continue
                p=proposal(raw,root['proposal']['archive_label'],root['proposal']['operation_id'],'delete',option,frozen,preservation,targets)
                with hub.edit() as state:
                    if state['requests'][key].get('cancel_requested'):break
                    if latest_delete(group_rows(state,key),option['id']) and option['id'] not in absent:continue
                    row=_row(hub,p,option['id'],root['user_id'],root['wallet_id'],key,checked);prior=latest_delete(group_rows(state,key),option['id']);row['generation']=(prior or {}).get('generation',0)+1
                    state['requests'][digest(p)]=row
                    if option['id'] in state['requests'][key].get('absent_holders',[]):state['requests'][key]['absent_holders'].remove(option['id'])
            with hub.edit() as state:state['requests'][key].pop('coordination_error',None)
        except (ValueError,OSError,KeyError) as exc:
            with hub.edit() as state:state['requests'][key]['coordination_error']=str(exc)[:1000]

def result(state,node,p):
    allowed={'request_hash','status','message','phase','manifest_hash','archive_verified','history_verified','zero_balance_verified','wallet_deleted','archive_wallet','address_count','deleted_wallets','archive_receipt'}
    if not isinstance(p,dict) or not {'request_hash','status','phase','manifest_hash'}<=set(p) or set(p)-allowed or p['status'] not in ('complete','denied','cancelled','error'):raise ValueError('Invalid retirement result.')
    row=state['requests'].get(p['request_hash'])
    if not row or row['kind']!='wallet_retire' or row['node_id']!=node or not row.get('delivered') or row['proposal'].get('version')!=3:raise ValueError('Unsolicited or obsolete retirement result.')
    proposal=row['proposal']
    if p['phase']!=proposal['phase'] or p['manifest_hash']!=proposal['manifest_hash']:raise ValueError('Retirement result does not match its exact preservation phase and manifest.')
    if row['state']!='pending':
        if row.get('result')==p:return
        raise ValueError('A retirement outcome cannot change.')
    if p['status']=='complete':
        if proposal['phase']=='preserve':
            if not isinstance(p.get('archive_receipt'),dict) or p.get('archive_verified') is not True or p.get('history_verified') is not True or p.get('wallet_deleted') is not False or p.get('deleted_wallets')!=[] or p.get('archive_wallet')!=proposal['wallet'] or p.get('address_count')!=len(proposal['manifest']['addresses']):raise ValueError('Preservation must retain every original wallet and confirm the complete existing archive.')
        else:
            if p.get('zero_balance_verified') is not True or p.get('wallet_deleted') is not True or not proposal.get('preservation'):raise ValueError('Deletion requires preserved history and freshly verified zero balances.')
            deleted=p.get('deleted_wallets')
            if not isinstance(deleted,list) or len(deleted)!=len(set(deleted)) or set(deleted)!={w['wallet'] for w in proposal['delete_wallets']}:raise ValueError('The exact original wallet deletion was not confirmed.')
            if p.get('archive_wallet'):raise ValueError('Deletion must not create or populate a historical archive.')
    row.update(state=p['status'],result=p,message=str(p.get('message',''))[:1000],outcome_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())

def private_sources(hub,raw,state=None):
    """Live descriptor identities only; watch copies and deleted cached routes do not count."""
    from policy_accounts import signing_account
    state=state if state is not None else hub.read();found=[]
    # Also called while NodeRegistry holds the storage lock for warnings.
    # Read a frozen connection view rather than recursively taking a snapshot.
    from nodes import DISCONNECT_SECONDS
    connections=[c for c in list(hub.registry.connections.values()) if hub.registry.clock()-c['seen']<DISCONNECT_SECONDS]
    live={c['node_id'] for c in connections if c.get('rpc_methods') and sum(x['mac']==c['mac'] for x in connections)==1 and sum(x['hostname']==c['hostname'] for x in connections)==1}
    for n in hub.registry._read()['nodes']:
        if n['id'] not in live:continue
        inv=hub.inventories.get(n['id'],{})
        if not inv.get('complete'):continue
        for w in inv.get('wallets',[]):
            if not w.get('private_keys_enabled') or w.get('extinct') or w.get('scanning'):continue
            if not (equivalent(raw,w['raw']) or signing_account(w,{'raw':raw})):continue
            deleted=[v for v in state['requests'].values() if v.get('kind')=='wallet_retire' and v['node_id']==n['id'] and w['name'] in v.get('result',{}).get('deleted_wallets',[])]
            if deleted:
                from datetime import datetime
                if any(not inv.get('scanned_utc') or not v.get('outcome_utc') or datetime.fromisoformat(inv['scanned_utc'].replace('Z','+00:00'))<=datetime.fromisoformat(v['outcome_utc'].replace('Z','+00:00')) for v in deleted):continue
            found.append({'node_id':n['id'],'hostname':n['hostname'],'wallet':w['name'],'raw':w['raw']})
    return found

def cancellation_visibility(raw,sources):
    from policy_accounts import signing_account,keys
    from vendor.embit.descriptor import Descriptor
    from vendor.embit.descriptor.miniscript import Multi
    visible=set()
    for source in sources:
        if signing_account({'raw':source['raw'],'private_keys_enabled':True},{'raw':raw}):visible.update(keys(source['raw']))
    groups=[]
    def walk(node):
        if isinstance(node,Multi):groups.append({k.to_string().split(']')[-1].replace("'",'h') for k in node.args[1:]})
        for child in getattr(node,'args',[]):walk(child)
    for row in raw['descriptors']:
        try:walk(Descriptor.from_string(row['desc'].split('#')[0]).miniscript)
        except (ValueError,AttributeError):continue
    if groups and any(not group<=visible for group in groups):return False,'Every multisig signer must remain visible to cancel extinction. At least one private-key signer is missing or its ownership cannot be established.'
    if not sources:return False,'No connected matching private-key wallet is visible. Watch-only copies cannot restore deleted signing keys.'
    return True,''

def snapshot(hub,user):
    state=hub.read();result=[];node_names={n['id']:n['hostname'] for n in hub.registry._read()['nodes']};online={c['node_id'] for c in list(hub.registry.connections.values())}
    for key,r in state['requests'].items():
        if r['kind']!='wallet_retire' or 'expected_holders' not in r:continue
        if r['proposal'].get('version')!=3:
            result.append(dict(request_hash=key,name=r['proposal']['archive_label'],state='error',created_utc=r['created_utc'],disc_status=r.get('disc_status','unconfirmed'),message='This older combined retirement request is not executed by the new two-phase protocol. Review any local outcome before starting a new extinction.',holders=[],archives=[],can_cancel=False,can_confirm_discs=False));continue
        rows=group_rows(state,key);holders=[]
        if not r['expected_holders'] and not any(w['state']=='pending' for w in rows):continue
        for node,hostname in r['expected_holders'].items():
            row=latest_delete(rows,node)
            holders.append({'node_id':node,'hostname':node_names.get(node,hostname),'colour':'green' if (row or {}).get('result',{}).get('wallet_deleted') else 'amber' if node in online else 'red','state':row['state'] if row else 'cancelled' if r.get('cancel_requested') else 'waiting_node','message':row.get('message','') if row else 'Waiting for verified preservation before local deletion approval.','archive_verified':any(w['proposal']['phase']=='preserve' and w.get('archive_checked') and w['state']=='complete' for w in rows),'wallet_deleted':bool((row or {}).get('result',{}).get('wallet_deleted')),'cancel_requested':bool((row or {}).get('cancel_requested'))})
        archives=[{'node_id':w['node_id'],'colour':'green' if w.get('archive_checked') else 'amber' if w['node_id'] in online else 'red','hostname':next((n['hostname'] for n in hub.registry._read()['nodes'] if n['id']==w['node_id']),w['node_id']),'wallet':w['proposal']['wallet'],'state':w['state'],'verified':bool(w.get('archive_checked')),'message':w.get('message','')} for w in rows if w['proposal']['phase']=='preserve']
        pending=any(w['state']=='pending' for w in rows)
        problem=next((w['state'] for w in rows if w['state'] in ('denied','cancelled','error')),None)
        available,reason=False,''
        if not r.get('cancel_requested') and not group_complete(state,key):available,reason=cancellation_visibility(r['retirement_wallet'],private_sources(hub,r['retirement_wallet'],state))
        result.append(dict(cancel_block_reason=reason,request_hash=key,name=r['proposal']['archive_label'],state='complete' if group_complete(state,key) else 'cancelling' if r.get('cancel_requested') and pending else 'cancelled' if r.get('cancel_requested') else problem or 'pending',created_utc=r['created_utc'],disc_status=r.get('disc_status','unconfirmed'),message=r.get('coordination_error',''),holders=holders,archives=archives,cancel_requested=bool(r.get('cancel_requested')),can_cancel=r.get('user_id')==user and available and not r.get('cancel_requested') and not group_complete(state,key),can_confirm_discs=r.get('user_id')==user))
    return result

def confirm_discs(hub,user,key,status):
    if status not in ('destroyed','none'):raise ValueError('Confirm destruction or that no archival discs existed.')
    with hub.edit() as state:
        row=state['requests'].get(key)
        if not row or row['kind']!='wallet_retire' or row.get('user_id')!=user or not row.get('expected_holders'):raise ValueError('Retirement is unavailable for this user.')
        if not group_complete(state,key):raise ValueError('Wait for every known holder to confirm deletion first.')
        row.update(disc_status=status,disc_confirmed_utc=hub.utc())
    return {'confirmed':True}

def suppress_sync(state,raw):
    for key,r in state['requests'].items():
        if r.get('node_deleted') or r['kind']!='wallet_retire' or r['state'] not in ('pending','complete','error') or not equivalent(raw,r['proposal']['public_wallet']):continue
        root=state['requests'].get(r.get('root_hash'),r)
        if root.get('cancel_requested') and not any(v['state']=='pending' for v in group_rows(state,r.get('root_hash') or key)):continue
        return True
    return False

def warnings(hub):
    result=[]
    for r in snapshot(hub,None):
        if r['state']=='complete':continue
        result.append({'id':'retirement-'+r['request_hash'],'kind':'wallet_retirement','severity':'red' if r['state'] in ('error','denied','cancelled','cancelling') or r['message'] else 'yellow','dismissible':False,'target':'balances','created_utc':r['created_utc'],'text':'Wallet extinction '+r['state']+' for '+r['name']+'. History must be preserved on an existing Extinct wallet before locally authorised deletion. '+r['message']})
    return result

def readiness(hub,user,wallet_id):
    family=FamilyStore(hub.config).snapshot();owner=next((r for r in family['ownership'] if r['id']==wallet_id and r.get('owner_id')==user and r.get('confirmed') and r['kind']=='descriptor'),None)
    if not owner:raise ValueError('No confirmed owned descriptor wallet matches this card. Refresh Ownership and Verify Balances.')
    wallet=next((w for w in DescriptorStore(hub.config).read()['wallets'] if w['id']==owner['source'] and not w.get('extinct')),None)
    if not wallet:raise ValueError('The card refers to an outdated wallet identity. Refresh Ownership and Verify Balances.')
    rows=[];archives=archive_options(hub)
    for n in hub.registry.snapshot()['nodes']:
        inv=hub.inventories.get(n['id'],{});matches=[w for w in inv.get('wallets',[]) if equivalent(w['raw'],wallet['raw'])];reasons=[]
        if not n.get('connected'):reasons.append('Agent disconnected')
        elif not n.get('rpc_ready'):reasons.append('Core RPC is unavailable or the node identity conflicts')
        if n.get('blockchain',{}).get('state')!='ready':reasons.append('Core blockchain is not ready')
        if CAPABILITY not in hub.caps.get(n['id'],set()):reasons.append('Agent must advertise wallet_retirement_v3; preservation and deletion are separate local approvals')
        if not inv.get('complete'):reasons.append('Wallet discovery incomplete: '+str(inv.get('error') or 'awaiting inventory'))
        elif not matches:reasons.append('No loaded wallet matches the public descriptors')
        elif not any(not w.get('signing_only') and not w.get('extinct') and not w.get('scanning') for w in matches):reasons.append('Matching wallet is scanning, temporary signing-only or extinct')
        if not archives:reasons.append('No connected Agent has an existing verified Extinct wallet')
        rows.append({'id':n['id'],'hostname':n['hostname'],'eligible':not reasons,'reasons':reasons,'matching_wallets':[w['name'] for w in matches]})
    return {'nodes':rows,'options':holder_options(hub,wallet['raw']) if archives else [],'archives':archives}

def cancel(hub,user,key,confirmed):
    if confirmed is not True:raise ValueError('Confirm cancellation first.')
    state=hub.read();root=state['requests'].get(key)
    if not root or root['kind']!='wallet_retire' or root.get('user_id')!=user or not root.get('expected_holders'):raise ValueError('Retirement is unavailable for this user.')
    sources=private_sources(hub,root['retirement_wallet'],state);allowed,reason=cancellation_visibility(root['retirement_wallet'],sources)
    if not allowed:raise ValueError('Cancellation is unavailable: '+reason)
    verified=[]
    for source in sources:
        try:
            info=hub.rpc(source['node_id'],'getwalletinfo',[],source['wallet']);raw=hub.rpc(source['node_id'],'listdescriptors',[False],source['wallet'])
            if info.get('private_keys_enabled') is True and not info.get('scanning') and equivalent(raw,source['raw']):verified.append(dict(source,raw=raw))
            if cancellation_visibility(root['retirement_wallet'],verified)[0]:break
        except (ValueError,OSError):continue
    allowed,reason=cancellation_visibility(root['retirement_wallet'],verified)
    if not allowed:raise ValueError('Cancellation is unavailable after fresh Agent verification: '+reason)
    with hub.edit() as state:
        root=state['requests'].get(key)
        if group_complete(state,key):raise ValueError('All holders already completed retirement; deletion cannot be undone.')
        root.update(cancel_requested=True,cancel_requested_utc=hub.utc())
        for row in group_rows(state,key):
            if row['state']!='pending':continue
            row['cancel_requested']=True
            if not row.get('delivered'):row.update(state='cancelled',cancelled_before_delivery=True,message='Cancelled before delivery; no Agent operation was requested.')
    from archive_queue import ArchiveQueue
    queue=ArchiveQueue(hub)
    for ident,row in list(queue.read()['requests'].items()):
        if row['request'].get('operation_id')==root['proposal'].get('operation_id') and row['state']=='active':queue.cancel(user,ident)
    return {'cancel_requested':True,'message':'Undelivered requests stopped. Delivered requests await local Agent cancellation or denial; completed deletion is not undone.'}


def deliverable(state,key):
    row=state['requests'][key];root=state['requests'].get(row.get('root_hash'),{})
    if root.get('cancel_requested') or row.get('cancel_requested'):return False
    if row['proposal'].get('version')!=3:return False
    if row['proposal']['phase']=='preserve':return True
    proof=row['proposal'].get('preservation') or {}
    archive=state['requests'].get(proof.get('request_hash'),{})
    return bool(archive.get('state')=='complete' and archive.get('archive_checked') and archive.get('root_hash')==row.get('root_hash') and archive['proposal']['phase']=='preserve' and archive['proposal']['manifest_hash']==row['proposal']['manifest_hash'] and proof.get('receipt')==archive['result'].get('archive_receipt'))


def validate_saved(state,ident,row,users):
    """Validate the phase barrier again when a profile is restored."""
    import copy,re
    p=row['proposal'];fields={'kind','version','operation_id','phase','wallet','public_wallet','archive_label','require_zero_balance','delete_wallet','nonce','manifest','manifest_hash','delete_wallets','preservation','request_id'}
    if set(p)!=fields or p['version']!=3 or p['kind']!='wallet_retire' or p['phase'] not in ('preserve','delete') or p['require_zero_balance'] is not True or p['delete_wallet'] is not (p['phase']=='delete'):raise ValueError('Invalid phased retirement proposal.')
    if row.get('user_id') not in users or ident!=digest(p) or p['request_id']!=digest({k:v for k,v in p.items() if k!='request_id'}):raise ValueError('Invalid saved retirement binding.')
    for key in ('operation_id','nonce'):
        if not re.fullmatch('[0-9a-f]{64}',str(p[key])):raise ValueError('Invalid saved retirement identity.')
    from descriptor_store import clean_wallet
    clean_wallet(p['public_wallet'])
    from labelled_wallets import label_parts
    label_parts(p['archive_label'])
    manifest=p['manifest']
    if not isinstance(manifest,dict) or set(manifest)!={'addresses','transactions'} or p['manifest_hash']!=digest(manifest) or len(canonical(manifest).encode())>512*1024:raise ValueError('Invalid saved historical manifest.')
    for group,fields in (('addresses',{'address','label'}),('transactions',{'txid','blockhash'})):
        rows=manifest[group]
        if not isinstance(rows,list) or not 1<=len(rows)<=100000 or any(not isinstance(v,dict) or set(v)!=fields for v in rows):raise ValueError('Invalid historical manifest records.')
        key='address' if group=='addresses' else 'txid'
        if len({v[key] for v in rows})!=len(rows):raise ValueError('Duplicate historical manifest records.')
        for item in rows:
            if group=='addresses':
                label_parts(item['label'])
                from vendor.embit.script import address_to_scriptpubkey
                address_to_scriptpubkey(item['address'])
            elif any(not re.fullmatch('[0-9a-f]{64}',str(item[f])) for f in fields):raise ValueError('Invalid public transaction identity.')
    if not isinstance(p['wallet'],str) or not p['wallet'] or len(p['wallet'])>200:raise ValueError('Invalid retirement wallet route.')
    targets=p['delete_wallets']
    if p['phase']=='preserve':
        if targets or p['preservation'] is not None:raise ValueError('Preservation cannot delete wallets.')
    else:
        if not isinstance(targets,list) or not 1<=len(targets)<=128 or len({t['wallet'] for t in targets})!=len(targets) or p['wallet'] not in {t['wallet'] for t in targets}:raise ValueError('Invalid deletion targets.')
        for target in targets:
            if set(target)!={'wallet','public_wallet','private_keys_enabled'} or type(target['private_keys_enabled']) is not bool:raise ValueError('Invalid deletion target metadata.')
            clean_wallet(target['public_wallet'])
        proof=p['preservation'];archive=state['requests'].get((proof or {}).get('request_hash'),{})
        if not archive or archive.get('root_hash')!=row.get('root_hash') or archive['proposal']['phase']!='preserve' or archive['proposal']['manifest_hash']!=p['manifest_hash'] or archive.get('state')!='complete' or not archive.get('archive_checked') or proof.get('node_id')!=archive['node_id'] or proof.get('archive_wallet')!=archive['proposal']['wallet'] or proof.get('manifest_hash')!=p['manifest_hash'] or proof.get('receipt')!=archive.get('result',{}).get('archive_receipt'):raise ValueError('Deletion has no verified preservation barrier.')
    root=state['requests'].get(row.get('root_hash'),{})
    if ('expected_holders' not in root or not root['expected_holders'] and not root.get('node_removal_parked')) or root['proposal']['operation_id']!=p['operation_id'] or root['proposal']['manifest_hash']!=p['manifest_hash']:raise ValueError('Invalid retirement group.')
    if row.get('cancelled_before_delivery'):
        if row['state']!='cancelled' or row.get('delivered') or not row.get('cancel_requested'):raise ValueError('Invalid undelivered cancellation.')
    elif row['state']!='pending':
        test=copy.deepcopy(row);test['state']='pending';result({'requests':{ident:test}},row['node_id'],row.get('result'))
        if test['state']!=row['state']:raise ValueError('Saved retirement outcome mismatch.')
    if row.get('archive_checked') and (p['phase']!='preserve' or row['state']!='complete'):raise ValueError('Invalid archive verification flag.')
    if ident==row.get('root_hash') and row.get('disc_status')!='unconfirmed' and not row.get('node_removal_parked') and not group_complete(state,ident):raise ValueError('Incomplete retirement disc confirmation.')


def queue_late_archives(hub,key,root,preservation):
    # An existing Extinct container on a newly connected node receives data too.
    for option in archive_options(hub):
        state=hub.read()
        if any(r['node_id']==option['id'] and r['proposal']['phase']=='preserve' and r['proposal']['wallet']==option['wallet'] for r in group_rows(state,key)):continue
        p=proposal(root['retirement_wallet'],root['proposal']['archive_label'],root['proposal']['operation_id'],'preserve',option,root['proposal']['manifest'])
        with hub.edit() as state:
            if state['requests'][key].get('cancel_requested'):return
            if any(r['node_id']==option['id'] and r['proposal']['phase']=='preserve' and r['proposal']['wallet']==option['wallet'] for r in group_rows(state,key)):continue
            state['requests'][digest(p)]=_row(hub,p,option['id'],root['user_id'],root['wallet_id'],key,root['balance_checks'])


def latest_delete(rows,node):
    matches=[r for r in rows if r['node_id']==node and r['proposal'].get('phase')=='delete']
    return max(matches,key=lambda r:r.get('generation',1),default=None)


def preservation_live(hub,proof):
    """No stale disconnected receipt releases newly delivered cleanup requests."""
    row=hub.read()['requests'].get((proof or {}).get('request_hash'))
    if not row or row.get('node_deleted') or row['state']!='complete' or not row.get('archive_checked'):return False
    node=row['node_id'];name=row['proposal']['wallet']
    if not any(o['id']==node and o['wallet']==name for o in archive_options(hub)):return False
    connections=tuple(sorted(k for k,c in hub.registry.connections.items() if c['node_id']==node))
    cached=getattr(hub,'retirement_readbacks',{}).get(proof['request_hash'])
    if cached and cached[1]==connections and time.monotonic()-cached[0]<30:return True
    try:verify_archive(hub,row);return True
    except (ValueError,OSError,KeyError):return False

def delivery_checks(hub,node):
    state=hub.read();ready=set()
    for key,row in state['requests'].items():
        if row['kind']=='wallet_retire' and row['node_id']==node and row['state']=='pending' and row['proposal'].get('version')==3 and row['proposal']['phase']=='delete' and not row.get('cancel_requested') and deliverable(state,key) and preservation_live(hub,row['proposal']['preservation']):ready.add(key)
    return ready
