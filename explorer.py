"""Attach corroborated blockchain references to display records only."""
import re
from watch import discover_outputs,ChainClient


def apply_evidence(report,evidence):
    """Preserve original references and amounts. Add separate verified metadata."""
    def enrich(record,label):
        if not record:return
        row=record.get('source_row')
        item=evidence.get((record.get('location') or label,row))
        if item:record.update({key:value for key,value in item.items() if not key.startswith('_')})
    def actual(record):
        if not record:return None
        value=record.get('blockchain_txid') or record.get('transaction_id','')
        return value.lower() if re.fullmatch('[0-9a-fA-F]{64}',value) else None
    for event in report['events']:
        for path in event['paths']:
            for node in path['nodes']:
                for key in ('details','incoming','outgoing'):enrich(node.get(key),node['label'])
            for left,right in zip(path['nodes'],path['nodes'][1:]):
                outgoing=left.get('outgoing');incoming=right.get('incoming')
                if outgoing and incoming and outgoing.get('transfer_to')==right['label']:
                    tx=actual(incoming);source_tx=actual(outgoing)
                    if tx and not source_tx:outgoing.update(blockchain_txid=tx,blockchain_source='https://blockstream.info/tx/'+tx)
                    if source_tx and not tx:
                        incoming.update(blockchain_txid=source_tx,blockchain_source='https://blockstream.info/tx/'+source_tx)
                        # For a matched Core send, this is the recipient's
                        # address, never the sending wallet's own address.
                        if outgoing.get('address'):incoming['wallet_addresses']=[outgoing['address']]
        if event.get('disposal'):
            for key in ('details','incoming','outgoing'):enrich(event['disposal'].get(key),event['disposal']['label'])


def enrich_history(core,engine,report,client=None):
    client=client or ChainClient(core)
    evidence=getattr(client,'wallet_evidence',{});errors={}
    # Only historical wallets actually represented in this report need extra
    # discovery. Existing watch evidence is reused, including its cached tx graph.
    wanted={n['label'] for event in report['events'] for path in event['paths'] for n in path['nodes'] if n['label'] in engine.wallets}
    for name in sorted(wanted):
        rows=engine.wallets.get(name,[])
        if not rows or not all(row.is_historical_ods for row in rows):continue
        if all((name,row.row_num) in evidence for row in rows):continue
        try:discover_outputs(core,engine,name,client,evidence)
        except (RuntimeError,ValueError,OSError,KeyError) as exc:errors[name]=str(exc)
    # Descriptor incoming addresses are supplied receiving addresses. Outgoing descriptor
    # address fields refer to recipients and must never become wallet links.
    for name,rows in engine.wallets.items():
        for row in rows:
            if row.is_historical_ods or not re.fullmatch('[0-9a-fA-F]{64}',row.txid or ''):continue
            evidence[(name,row.row_num)]={'blockchain_txid':row.txid,'_utc':core.display_time(row.dt),'_direction':'incoming' if row.incoming else 'outgoing','wallet_addresses':[row.address] if row.incoming and row.address else []}
    apply_evidence(report,evidence)
    attach_wallet_addresses(report,evidence)
    report['explorer']={'verified_historical_wallets':sorted({name for name,row in evidence if any(r.is_historical_ods for r in engine.wallets.get(name,[]))}),
                        'unavailable_historical_wallets':errors,
                        'note':'Only actual Bitcoin TXIDs and corroborated wallet addresses receive explorer links. Statement and spreadsheet references are retained for audit.'}
    return evidence


def attach_wallet_addresses(report,evidence):
    by_name={}
    for (name,_),item in evidence.items():by_name.setdefault(name,[]).append(item)
    def attach(node):
        if not node or node['label'] not in by_name:return
        node['wallet_identity']=True
        txids=set()
        for side in ('incoming','outgoing'):
            record=node.get(side) or {};value=record.get('blockchain_txid') or record.get('transaction_id')
            if value:txids.add(value)
        node['wallet_address_records']=[{'utc':item.get('_utc'),'direction':item.get('_direction'),'addresses':item.get('wallet_addresses',[])} for item in sorted(by_name[node['label']],key=lambda i:i.get('_utc') or '') if item.get('blockchain_txid') in txids]
    for event in report['events']:
        for path in event['paths']:
            for node in path['nodes']:attach(node)
        attach(event.get('disposal'))
