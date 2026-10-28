"""Stage-one public planning catalogue. No coin selection, signing or broadcasting."""
from decimal import Decimal
from threading import RLock

# PSBT construction also uses this lock while loading the isolated tax module.
_HISTORICAL_LOCK=RLock()
from descriptor_store import DescriptorStore
from descriptor_snapshot import saved_history
from preferences import FamilyStore,resource_id
from address_book import valid_address

def builder_catalog(session,price=None):
    if not session.access()['ownership_ready']:raise ValueError('Complete Ownership and save all assignments first.')
    family=session.family();store=FamilyStore(session.preferences_path).snapshot()
    public_store=DescriptorStore(session.preferences_path).read();chain=saved_history(session.preferences_path,public_store)
    owners={r['id']:r for r in family['ownership']};users={u['id']:u for u in family['users']}
    entries={e['id']:e for e in (family.get('address_book') or {}).get('entries',[]) if e.get('kind') in ('descriptor','wallet','bitaroo')}
    balances={}
    # Ownership persisted these exact, reconstructed balances. Opening a
    # template must not parse the ODS or repeat blockchain requests.
    run=store.get('ownership_runs',{}).get(session.user_id,{})
    balances={resource_id('wallet',name):amount for name,amount in run.get('historical_balances',{}).items()}
    cards={}
    def add(ident,kind,name,balance,send=False):
        entry=entries.get(ident,{})
        address=next((a['address'] for a in entry.get('addresses',[]) if valid_address(a.get('address'))),None) if entry.get('status')=='available' else None
        own=owners.get(ident,{})
        owner_id=entry.get('owner_id') or own.get('owner_id');user=users.get(owner_id,{})
        if kind=='bitaroo':name='Bitaroo Exchange'
        amount=Decimal(balance) if balance is not None else None
        aud=str(amount*Decimal(price['aud_per_btc'])) if amount is not None and price else None
        cards[ident]={'id':ident,'kind':kind,'name':name,'owner_id':owner_id,'owner':user.get('name','Someone else / external'),'colour':user.get('colour','neutral'),'btc':str(amount) if amount is not None else None,'aud':aud,'address':address,'can_send':send,'can_receive':bool(address)}
    for wallet in chain['wallets']:
        if wallet.get('extinct'):continue
        ident=resource_id('descriptor',wallet['id']);own=owners.get(ident,{})
        groups={}
        for r in wallet['records']:groups.setdefault(r['txid'],[]).append(r)
        sats=sum(sum(r['sats'] for r in rows)-(0 if any(r['fee_only'] for r in rows) else max(r['fee_sats'] for r in rows)) for rows in groups.values())
        balance=Decimal(sats)/Decimal(100000000)+Decimal(wallet.get('pending_btc','0'))
        add(ident,'descriptor',own.get('name') or wallet['name'],str(balance),True)
    for ident,entry in entries.items():
        if ident in cards or entry['kind']=='descriptor':continue
        if entry['kind']=='wallet':
            balance=balances.get(ident)
            if balance is None or Decimal(balance)==0:continue
            add(ident,'wallet',owners.get(ident,{}).get('name') or entry['source'],balance)
        elif entry['kind']=='bitaroo':
            uid=entry.get('owner_id') or entry['source'];rows=store.get('ownership_runs',{}).get(uid,{}).get('bitaroo_data',{}).get('btc_rows',[])
            last=max(enumerate(rows),key=lambda x:(x[1]['utc'],x[0]))[1] if rows else None
            add(ident,'bitaroo','Bitaroo Exchange',last['balance'] if last else None)
    from unsigned_psbt import public_change_candidate
    for w in chain['wallets']:
        if w.get('extinct'):continue
        ident=resource_id('descriptor',w['id'])
        try:cards[ident]['change_script_size']=len(public_change_candidate(store=public_store,chain=chain,wallet_id=w['id'])[0].data)
        except ValueError:pass
    return {'wallets':list(cards.values()),'price':price,'scanned_utc':chain['scanned_utc']}
