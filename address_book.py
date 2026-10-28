"""Receiving destinations inferred afresh from supplied records, never from identity notes.

An export describes addresses that were used, not the wallet's next unused address.
No address is claimed to remain valid at a custodian after its latest supplied deposit.
"""
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
from vendor.embit.script import address_to_scriptpubkey
from vendor.embit.base import EmbitError
from vendor.embit.networks import NETWORKS
from preferences import resource_id
from watch import ChainClient,discover_outputs,sats


def valid_address(value):
    if not isinstance(value,str) or not value:return False
    try:
        script=address_to_scriptpubkey(value)
        if script is None or script.script_type() is None:return False
        expected=value.lower() if value.lower().startswith(('bc1','tb1','bcrt1')) else value
        return any(script.address(network)==expected for network in NETWORKS.values())
    except (EmbitError,ValueError,TypeError,IndexError,KeyError):return False


def build_address_book(core,wallets,reports,other,user,client=None):
    client=client or ChainClient(core);evidence={};entries=[]
    engine=SimpleNamespace(wallets=wallets);discovered=set()
    def historical_evidence(name):
        if name not in discovered:
            discovered.add(name)
            discover_outputs(core,engine,name,client,evidence)
    def stamp(row):return core.utc_time(row.dt)
    def iso(row):return stamp(row).isoformat().replace('+00:00','Z')
    def txid(row):
        import re
        return row.txid.lower() if re.fullmatch('[0-9a-fA-F]{64}',row.txid or '') else None
    def output_address(row):
        # The address on a Core send is the recipient; on a receive it is this wallet.
        if valid_address(row.address):return row.address
        ident=txid(row)
        if not ident:return None
        tx=client.tx(ident)
        matches=[o.get('scriptpubkey_address') for o in tx.get('vout',[]) if o.get('value')==sats(row.principal)]
        return matches[0] if len(matches)==1 and valid_address(matches[0]) else None
    for name,rows in wallets.items():
        descriptor=name in getattr(core,'DESCRIPTOR_WALLETS',{})
        if descriptor and any(w['id']==core.DESCRIPTOR_WALLETS[name] and w.get('extinct') for w in core.DESCRIPTOR_SNAPSHOT['wallets']):continue
        source=core.DESCRIPTOR_WALLETS[name] if descriptor else name
        kind='descriptor' if descriptor else 'wallet'
        # This visibility rule does not alter the tax ledger or ownership inventory.
        if not descriptor and (not rows or max(rows,key=lambda row:(stamp(row),row.row_num)).balance==0):continue
        entry={'id':resource_id(kind,source),'kind':kind,'source':source,'addresses':[],
               'status':'unavailable','last_record_utc':iso(max(rows,key=stamp)) if rows else None}
        if descriptor:
            saved=next(w for w in core.DESCRIPTOR_SNAPSHOT['wallets'] if w['id']==source)
            address=saved.get('receiving_address')
            if address:entry.update(status='available',addresses=[{'address':address,'observed_utc':core.DESCRIPTOR_SNAPSHOT['scanned_utc'],'txid':None}],observed_utc=core.DESCRIPTOR_SNAPSHOT['scanned_utc'],address_basis='Latest observed receiving address, or active descriptor next_index when unused. Check Bitcoin Core before sending.')
            else:entry['reason']='No active receiving descriptor with a next address was supplied.'
            entries.append(entry);continue
        receipts=[r for r in rows if r.incoming]
        try:
            if not receipts:raise ValueError('No incoming transaction in the supplied wallet records.')
            latest=max(map(stamp,receipts));selected=[r for r in receipts if stamp(r)==latest]
            entry['observed_utc']=latest.isoformat().replace('+00:00','Z')
            if not descriptor:
                print('Address Book: corroborating historical receiving addresses for '+name,flush=True)
                historical_evidence(name)
            result=[]
            for row in selected:
                addresses=[output_address(row)] if descriptor else evidence.get((name,row.row_num),{}).get('wallet_addresses',[])
                addresses=[a for a in addresses if valid_address(a)]
                if not addresses:raise ValueError('The latest receipt could not be linked uniquely to a receiving address.')
                ident=txid(row) if descriptor else evidence.get((name,row.row_num),{}).get('blockchain_txid')
                for address in addresses:
                    if not any(a['address']==address for a in result):result.append({'address':address,'txid':ident,'observed_utc':iso(row)})
            entry.update(status='available',addresses=result)
        except (ValueError,RuntimeError,OSError,KeyError) as exc:entry['reason']=str(exc)
        entries.append(entry)

    def bitaroo_entry(source,receipts):
        kind='bitaroo'
        entry={'id':resource_id(kind,source),'kind':kind,'source':source,'addresses':[],'status':'unavailable'}
        if not receipts:
            entry['reason']='No Bitcoin deposit is present in the supplied exchange records.';return entry
        latest=max(map(stamp,receipts));deposits=[r for r in receipts if stamp(r)==latest]
        entry['observed_utc']=latest.isoformat().replace('+00:00','Z')
        try:
            destinations=[]
            for deposit in deposits:
                # Exact BTC output amounts only: tax tracing's approximate fee tolerance
                # is deliberately not sufficient evidence for a payment destination.
                amount=abs(deposit.amount)
                candidates=[r for rows in wallets.values() for r in rows if r.outgoing
                            and abs((stamp(r)-stamp(deposit)).total_seconds())<=core.MATCH_HOURS*3600
                            and r.principal==amount]
                found=[]
                for row in candidates:
                    address=output_address(row);ident=txid(row)
                    if not ident:
                        for name,rows in wallets.items():
                            if any(r is row for r in rows):
                                historical_evidence(name)
                                item=evidence.get((name,row.row_num),{});ident=item.get('blockchain_txid');break
                    if not ident:continue
                    tx=client.tx(ident)
                    outputs=[o.get('scriptpubkey_address') for o in tx.get('vout',[]) if o.get('value')==sats(amount) and (not address or o.get('scriptpubkey_address')==address)]
                    if len(outputs)==1 and valid_address(outputs[0]):found.append((outputs[0],ident,iso(row)))
                # A jointly funded payment has one physical output but several
                # accounting senders. Ownership discovery uses the decoded output
                # directly and does not need an allocation convention first.
                if not found:
                    known=dict(getattr(core,'HISTORICAL_OUTPOINTS',{}).get('transactions',{}))
                    for ident,tx in getattr(core,'DESCRIPTOR_SNAPSHOT',{}).get('transactions',{}).items():
                        if ident in known:continue
                        known[ident]={'status':{'block_time':datetime.fromisoformat(tx['utc'].replace('Z','+00:00')).timestamp()},
                                      'vout':[{'value':o['value'],'scriptpubkey_address':o.get('address')} for o in tx['outputs']]}
                    for ident,tx in known.items():
                        instant=tx.get('status',{}).get('block_time')
                        if instant is None or abs(instant-stamp(deposit).timestamp())>core.MATCH_HOURS*3600:continue
                        for out in tx.get('vout',[]):
                            address=out.get('scriptpubkey_address')
                            if out.get('value')==sats(amount) and valid_address(address):found.append((address,ident,datetime.fromtimestamp(instant,timezone.utc).isoformat().replace('+00:00','Z')))
                unique={(a,t) for a,t,_ in found}
                if len(unique)!=1:raise ValueError('The latest Bitcoin deposit cannot be linked uniquely to a supplied sender and blockchain output. Older deposit addresses are not substituted.')
                address,ident,when=found[0]
                if not any(a['address']==address for a in destinations):destinations.append({'address':address,'txid':ident,'observed_utc':when})
            if len(destinations)!=1:raise ValueError('Multiple destinations share the latest deposit timestamp; a single latest receiving address cannot be established. Older addresses are not substituted.')
            entry.update(status='available',addresses=destinations)
        except (ValueError,RuntimeError,OSError,KeyError) as exc:entry['reason']=str(exc)
        return entry
    if reports.btc_rows:
        print('Address Book: identifying the latest Bitaroo Bitcoin deposit destination',flush=True)
    entries.append(bitaroo_entry(user['id'],[r for r in reports.btc_rows if r.tx_type=='Deposit' and r.amount>0]))
    return {'entries':entries,'generated_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}


def shared_address_book(snapshot,local=None):
    """Combine this browser's current wallets with persisted, profile-scoped accounts."""
    users={u['id'] for u in snapshot['users']}
    entries=[dict(e) for e in (local or {}).get('entries',[]) if e.get('kind') in ('descriptor','wallet')]
    checked=[(local or {}).get('generated_utc') or '']
    for user_id,entry in snapshot.get('bitaroo_addresses',{}).items():
        if user_id not in users:continue
        entries.append(dict(entry,owner_id=user_id,shared=True));checked.append(entry.get('checked_utc') or '')
    if not entries and not local:return None
    return {'entries':entries,'generated_utc':max(checked) or None}
