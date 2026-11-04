"""Wallet cards from saved confirmed history; archive labels remain distinct owners."""
import hashlib,json
from datetime import datetime,timezone
from functools import lru_cache
from pathlib import Path
from descriptor_store import DescriptorStore
from preferences import FamilyStore
from storage import data_directory

@lru_cache(maxsize=8)
def _history(path,mtime,size,fingerprint):
    envelope=json.loads(Path(path).read_text());payload=envelope['payload']
    if hashlib.sha256(payload.encode()).hexdigest()!=envelope['sha256']:raise ValueError('Invalid saved history checksum.')
    history=json.loads(payload)
    if history['catalog_fingerprint']!=fingerprint:raise ValueError('History changed.')
    result={}
    for wallet in history['wallets']:
        years=[]
        for row in wallet.get('records',[]):
            # C1 declarations are not blockchain activity.
            if row.get('txid') not in history['transactions']:continue
            dt=datetime.fromisoformat(row['utc'].replace('Z','+00:00')).astimezone(timezone.utc)
            years.append(dt.year)
        owned={outpoint for outpoint,uid in history.get('wallet_outputs',{}).items() if uid==wallet['id']}
        relevant={point.rsplit(':',1)[0] for point in owned}
        relevant.update(txid for txid,tx in history['transactions'].items() if any(str(i.get('txid',''))+':'+str(i.get('vout',-1)) in owned for i in tx.get('inputs',[])))
        for txid in relevant:
            stamp=history['transactions'].get(txid,{}).get('utc')
            if stamp:years.append(datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(timezone.utc).year)
        result[wallet['id']]=str(min(years)) if years and min(years)==max(years) else str(min(years))+' - '+str(max(years)) if years else 'History unavailable'
    return result

def extinct_cards(config):
    catalog=DescriptorStore(config).read();fingerprint=DescriptorStore.fingerprint(catalog);spans={}
    path=data_directory(config)/'descriptor-history'/(fingerprint+'.json')
    try:
        stat=path.stat();spans=_history(str(path),stat.st_mtime_ns,stat.st_size,fingerprint)
    except (OSError,ValueError,KeyError,TypeError):pass
    family=FamilyStore(config).snapshot();owners={r['source']:r for r in family['ownership'] if r['kind']=='descriptor'};users={u['id']:u['name'] for u in family['users']}
    result=[]
    for wallet in catalog['wallets']:
        if not wallet.get('extinct'):continue
        owner=owners.get(wallet['id'],{});result.append({'id':wallet['id'],'name':owner.get('name') or wallet['name'],'owner_name':users.get(owner.get('owner_id'),'Someone else / external'),'extinct':True,'activity_years':spans.get(wallet['id'],'History unavailable')})
    return sorted(result,key=lambda w:w['name'].casefold())


def combined_signers(hub,raw,active):
    """A complete public descriptor does not reveal which private role is local."""
    from policy_accounts import keys
    wanted=keys(raw);visible=set();roles={};saved=hub.read().get('nodes',{})
    for setup_id,setup in sorted(hub.wallet_builder.read()['sessions'].items(),key=lambda pair:pair[1].get('created_utc','')):
        if setup.get('state') not in ('ready','policy'):continue
        descs={'descriptors':[{'desc':d} for d in setup.get('descriptors',[])]}
        if not wanted or keys(descs)!=wanted:continue
        for role in setup['proposal']['cosigners']:
            evidence=setup.get('contributions',{}).get(role['id'])
            if not evidence:continue
            own={evidence['message'][f].split(']')[-1].replace("'",'h') for f in ('receive_key','change_key')}
            if not own<=wanted:continue
            found=False
            for node,inv in list(hub.inventories.items()):
                if node not in active or not (inv.get('complete') or inv.get('loaded_complete')):continue
                signed=setup.get('inventories',{}).get(node,{})
                target=signed.get('message',{}).get('signing_wallets',{}).get(role['id'])
                for account in inv.get('wallets',[]):
                    if not account.get('private_keys_enabled') or account.get('extinct') or account.get('scanning'):continue
                    if not wanted<=keys(account['raw']):continue
                    managed=account.get('managed',{})
                    named=(target is not None and account['name']==target and signed.get('public_key')==role['agent_key'])
                    identified=(managed.get('kind')=='combined' and managed.get('setup_id')==setup_id and
                                managed.get('policy_hash')==setup.get('policy_hash') and managed.get('role_id')==role['id'] and
                                setup['routes'].get(role['id'])==node)
                    archive=saved.get(node,{}).get('archive_inventory',{}).get(account.get('archive_id'),{}).get('message',{})
                    archived=(account.get('signing_only') and role['storage']=='disc' and any(
                        m.get('role_id')==role['id'] and keys(m.get('public_wallet',{}))==own for m in archive.get('members',[])))
                    if named or identified or archived:found=True;break
                if found:break
            if found:visible.update(own)
            ident=tuple(sorted(own));previous=roles.get(ident,{})
            roles[ident]={'name':role['name'],'storage':role['storage'],'visible':found or previous.get('visible',False)}
    return visible,list(roles.values())

def annotate_signers(report,hub):
    """Visibility is public-account evidence, not proof a locked signer can sign."""
    if not report or hub is None:return report
    from policy_accounts import signing_account,keys as public_keys
    from vendor.embit.descriptor import Descriptor
    from vendor.embit.descriptor.miniscript import Multi
    active={n['id'] for n in hub.registry.presence()['nodes'] if n.get('connected') and n.get('rpc_ready')}
    accounts=[w for node,inv in list(hub.inventories.items()) if node in active and (inv.get('complete') or inv.get('loaded_complete')) for w in inv.get('wallets',[]) if w.get('private_keys_enabled')]
    catalog={w['id']:w for w in DescriptorStore(hub.config).read()['wallets']}
    for card in report['balances']['wallets']:
        wallet=catalog.get(card.get('descriptor_id'))
        if not wallet or not card.get('multisig'):continue
        monitor={'raw':wallet['raw']};visible=set().union(*(public_keys(a['raw']) for a in accounts if signing_account(a,monitor))) if accounts else set()
        combined,roles=combined_signers(hub,wallet['raw'],active);visible.update(combined);card['signers']=roles
        counts={}
        def walk(node):
            if isinstance(node,Multi):
                values=[k.to_string().split(']')[-1].replace("'",'h') for k in node.args[1:]];key=(node.args[0].num,len(values));counts[key]=max(counts.get(key,0),sum(v in visible for v in values))
            for child in getattr(node,'args',[]):walk(child)
        for row in wallet['raw']['descriptors']:
            try:walk(Descriptor.from_string(row['desc'].split('#')[0]).miniscript)
            except (ValueError,AttributeError):continue
        for item in card['multisig']:item['visible']=counts.get((item['required'],item['total']),0)
    return report
