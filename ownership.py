"""Source-driven ownership inventory and fail-closed adapter for the unchanged tax engine."""
from wallet_names import suggest_wallet_name,apply_display_names
from preferences import resource_id,resolve_ownership,MissingOwnership,MISSING_OWNERSHIP

def saved_bitaroo_destinations(snapshot):
    """Unambiguous account destinations supported by saved Ownership evidence.

    Never infer identity from a name, transaction ID or address prefix. Conflicting
    account claims are left unresolved rather than silently choosing an owner.
    """
    from address_book import valid_address
    users={u['id'] for u in snapshot.get('users',[])}
    accounts={r['source']:r for r in snapshot.get('ownership',[])
              if r.get('kind')=='bitaroo' and r.get('confirmed')}
    claims={}
    for uid,entry in snapshot.get('bitaroo_addresses',{}).items():
        account=accounts.get(uid)
        if uid not in users or not account or account.get('owner_id')!=uid or account.get('external') or account.get('smsf'):continue
        if entry.get('status')!='available' or entry.get('source')!=uid or entry.get('id')!=resource_id('bitaroo',uid):continue
        for item in entry.get('addresses',[]):
            address=item.get('address')
            if valid_address(address):claims.setdefault(address,set()).add(uid)
    return {address:next(iter(owners)) for address,owners in claims.items() if len(owners)==1}

def inventory_for(core,wallets,other,user):
    rows=[]
    for name in wallets:
        uid=getattr(core,'DESCRIPTOR_WALLETS',{}).get(name)
        kind='descriptor' if uid else 'wallet';source=uid or name
        evidence=next((w for w in getattr(core,'DESCRIPTOR_SNAPSHOT',{}).get('wallets',[]) if w['id']==uid),{})
        original=getattr(core,'DESCRIPTOR_SOURCE_NAMES',{}).get(uid,name) if uid else name
        rows.append({'id':resource_id(kind,source),'kind':kind,'source':source,'extinct':bool(evidence.get('extinct')) or not bool(uid),'has_vault':bool(evidence.get('has_vault')),'watch_only':bool(evidence.get('watch_only')),'name':suggest_wallet_name(original) if uid and not evidence.get('extinct') else name,**({'source_name':original} if uid else {})})
    rows += [{'id':resource_id('exchange',name),'kind':'exchange','source':name,'name':name} for name in sorted({r.exchange for r in other})]
    for profile in getattr(core,'FAMILY_ACCOUNT_USERS',[user]):
        rows.append({'id':resource_id('bitaroo',profile['id']),'kind':'bitaroo','source':profile['id'],'name':'Bitaroo Exchange · '+profile['name']})
    return sorted(rows,key=lambda r:(r['kind'],r['source'].casefold()))

def ownership_class(core,snapshot,user,inventory):
    resolved=resolve_ownership(snapshot,inventory)
    by_source={(r['kind'],r['source']):r for r in resolved.values()};by_name={r['name']:r for r in resolved.values()}
    def personal(r):return r['owner_id']==user['id'] and not r['smsf'] and not r['external']
    destinations=saved_bitaroo_destinations(snapshot)
    class FixedOwnership(core.WalletOwnership):
        def describe_bitaroo_destination(self,address):
            owner=destinations.get(address)
            profile=next((u for u in snapshot['users'] if u['id']==owner),None)
            if not profile:return None
            return {'label':'Bitaroo Exchange · '+profile['name'],'owner_name':profile['name'],
                    'owner_id':owner,'address':address,'ownership':'Owned by '+profile['name']}
        def decide_unresolved_destination(self,origin,dt,amount,transaction_id,address='',trace_source='Exchange Tracer',decision_ref=None):
            if amount==0:return False
            owner=destinations.get(address)
            if owner is not None and owner!=user['id']:
                # Saved account identity establishes an external counterparty;
                # the core still records the transfer as a taxable disposal.
                return False
            # Keep the existing completeness check for an unmatched destination
            # in the current user's own account, and questions for unknowns.
            return super().decide_unresolved_destination(origin,dt,amount,transaction_id,address,trace_source,decision_ref)
        def _ask_wallet_status(self,wallet,trace_source=''):
            row=by_name.get(wallet)
            if not row:raise MissingOwnership(MISSING_OWNERSHIP)
            return self.PERSONAL if personal(row) else self.EXTERNAL
    return FixedOwnership,resolved,personal

def apply_ownership(core,snapshot,user,wallets,other,require_bitaroo=True):
    inventory=inventory_for(core,wallets,other,user);cls,resolved,personal=ownership_class(core,snapshot,user,inventory)
    if require_bitaroo and not personal(resolved[resource_id('bitaroo',user['id'])]):raise ValueError('The selected Bitaroo account must belong to the logged-in user and must not be an SMSF for a personal report.')
    apply_display_names(core,wallets,resolved)
    core.WalletOwnership=cls;own=cls(user['name']);own.bind_wallets(wallets)
    own.descriptor_wallet_ids=dict(getattr(core,'DESCRIPTOR_WALLETS',{}))
    for item in inventory:
        row=resolved[item['id']]
        if item['kind']!='bitaroo':own.wallet_statuses[row['name']]=own.PERSONAL if personal(row) else own.EXTERNAL
    own.family_records={r['name']:r for r in resolved.values()};own.family_users={u['id']:u['name'] for u in snapshot['users']}
    return own,[r for r in other if personal(resolved[resource_id('exchange',r.exchange)])]
