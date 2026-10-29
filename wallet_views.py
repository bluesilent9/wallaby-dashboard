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
