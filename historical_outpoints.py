"""Exact, user-supplied historical wallet ownership: one column per wallet.

Only explicitly listed outputs belong to an ODS wallet. Never infer change,
ownership or payment allocations from input co-occurrence or output amounts.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import re

SAT = Decimal('0.00000001')
OUTPOINT = re.compile(r'([0-9a-fA-F]{64}):(0|[1-9][0-9]*)')


def read_columns(core, path):
    from odf.table import Table, TableRow, TableCell, CoveredTableCell
    from odf import teletype
    tables = core.load_ods(path).spreadsheet.getElementsByType(Table)
    if len(tables) != 1:
        raise ValueError('Wallets ODS must contain one tab: wallet names in row 1 and TXID:output_index entries below each name.')
    rows = tables[0].getElementsByType(TableRow)
    if not rows:
        raise ValueError('Wallets ODS has no wallet headings.')
    def populated(row):
        index = 0
        for cell in row.childNodes:
            if getattr(cell, 'qname', None) not in (TableCell().qname, CoveredTableCell().qname):
                continue
            repeat = int(cell.getAttribute('numbercolumnsrepeated') or 1)
            text = teletype.extractText(cell).strip()
            if text:
                if index + repeat > 512:
                    raise ValueError('Wallets ODS exceeds 512 populated wallet columns.')
                for n in range(repeat):
                    yield index + n, text
            index += repeat
    headings = dict(populated(rows[0]))
    if not headings or len({n.casefold() for n in headings.values()}) != len(headings):
        raise ValueError('Wallets ODS needs distinct, nonempty wallet names in row 1.')
    columns = {name: [] for name in headings.values()}
    owners = {}; count = 0
    for row_number, row in enumerate(rows[1:], 2):
        values = list(populated(row))
        if values and int(row.getAttribute('numberrowsrepeated') or 1) != 1:
            raise ValueError(f'Wallets ODS row {row_number}: repeated populated rows duplicate ownership entries.')
        for index, text in values:
            where = f'{Path(path).name} row {row_number}, column {index + 1}'
            if index not in headings:
                raise ValueError(where + ': missing wallet name in row 1.')
            match = OUTPOINT.fullmatch(text)
            if not match:
                raise ValueError(where + ': use TXID:output_index (the output index starts at zero). The old date/amount Wallets ODS format is no longer supported.')
            key = (match[1].lower(), int(match[2])); name = headings[index]
            if key in owners:
                raise ValueError(where + ': this output is already assigned to ' + owners[key] + '.')
            owners[key] = name; columns[name].append(key); count += 1
            if count > 100000:
                raise ValueError('Wallets ODS exceeds 100,000 outputs.')
    if any(not values for values in columns.values()):
        raise ValueError('Every named Wallets ODS column must contain at least one owned TXID:output_index.')
    return columns


def load_wallets(core, path, client=None, defer_joint=False):
    from watch import ChainClient
    client = client or ChainClient(core)
    columns = read_columns(core, path)
    snapshot = getattr(core, 'DESCRIPTOR_SNAPSHOT', {})
    # Reject overlapping historical/current wallet definitions when an exact
    # descriptor record already claims one of the supplied receiving outputs.
    descriptor_outputs = {(r['txid'], r['output_index']): w['name'] for w in snapshot.get('wallets', []) for r in w['records'] if r['sats'] > 0}
    owners = {key: name for name, keys in columns.items() for key in keys}
    transactions = {}
    def transaction(ident):
        if ident not in transactions:
            tx = client.tx(ident)
            if not tx.get('status', {}).get('confirmed'):
                raise ValueError('Wallets ODS requires confirmed transactions: ' + ident)
            if type(tx['status'].get('block_time')) is not int or type(tx['status'].get('block_height')) is not int:
                raise ValueError('Missing confirmation timestamp/height: ' + ident)
            if any(type(o.get('value')) is not int or o['value'] < 0 for o in tx.get('vout', [])):
                raise ValueError('Invalid transaction output amount: ' + ident)
            transactions[ident] = tx
        return transactions[ident]
    print(f'Wallets ODS: reading {len(owners):,} exact owned outputs across {len(columns):,} wallets.', flush=True)
    for ident, _ in owners: transaction(ident)
    known_spends = {}
    for ident, tx in transactions.items():
        for n, vin in enumerate(tx.get('vin', [])):
            key = (vin.get('txid'), vin.get('vout'))
            if key not in owners: continue
            if key in known_spends:
                raise ValueError('Two confirmed transactions claim to spend the same historical output.')
            known_spends[key] = {'spent': True, 'txid': ident, 'vin': n}
    spend_queries = 0
    for key, name in owners.items():
        ident, index = key
        if key in descriptor_outputs:
            raise ValueError(f'{name}: output {ident}:{index} is also supplied by descriptor wallet {descriptor_outputs[key]}. Supply each wallet only once.')
        tx = transaction(ident)
        if index >= len(tx['vout']):
            raise ValueError(f'{name}: transaction {ident} has no output {index}.')
        output = tx['vout'][index]
        if not output.get('scriptpubkey_address') or output['value'] <= 0:
            raise ValueError(f'{name}: {ident}:{index} is not a supported positive address output.')
        status = known_spends.get(key)
        if status is None:
            status = client.outspend(ident, index); spend_queries += 1
        if status['spent']:
            spending = client.tx(status['txid'])
            inputs = [v for v in spending.get('vin', []) if v.get('txid') == ident and v.get('vout') == index]
            if len(inputs) != 1 or inputs[0].get('prevout', {}).get('value') != output['value'] or inputs[0]['prevout'].get('scriptpubkey') != output.get('scriptpubkey'):
                raise ValueError('Explorer spending link does not match the supplied output: ' + ident + ':' + str(index))
            if status['vin'] >= len(spending['vin']) or spending['vin'][status['vin']] is not inputs[0]:
                raise ValueError('Explorer spending input index does not match the supplied output.')
            if spending.get('status', {}).get('confirmed'):
                transaction(spending['txid'])
    records = {name: [] for name in columns}; evidence = {}; balances = defaultdict(int)
    unspent = {name: {} for name in columns}
    # Block height plus dependency order handles multiple transactions in one block.
    ordered = []; visiting = set(); visited = set()
    def visit(ident):
        if ident in visited: return
        if ident in visiting: raise ValueError('Cyclic blockchain transaction history.')
        visiting.add(ident)
        for inp in transactions[ident].get('vin', []):
            if inp.get('txid') in transactions: visit(inp['txid'])
        visiting.remove(ident); visited.add(ident); ordered.append(ident)
    for ident in sorted(transactions, key=lambda t: (transactions[t]['status']['block_height'], t)): visit(ident)
    for ident in ordered:
        tx = transactions[ident]; inputs = tx.get('vin', []); outputs = tx['vout']
        input_owners = {owners[(v['txid'], v['vout'])] for v in inputs if (v.get('txid'), v.get('vout')) in owners}
        joint=bool(input_owners and (len(input_owners) != 1 or any((v.get('txid'), v.get('vout')) not in owners for v in inputs)))
        if joint and not defer_joint:
            raise ValueError('A transaction combines inputs from multiple wallets or unknown participants. Its per-wallet payments and fee allocation cannot be established from public history alone; no inferred tax ledger was accepted. Transaction: ' + ident)
        sender = next(iter(input_owners), None) if not joint else None
        fee = tx.get('fee')
        if sender:
            if type(fee) is not int or fee < 0 or sum(v['prevout']['value'] for v in inputs) != sum(o['value'] for o in outputs) + fee:
                raise ValueError('Transaction inputs, outputs and fee do not reconcile: ' + ident)
        when = core.TransactionDate(datetime.fromtimestamp(tx['status']['block_time'], timezone.utc))
        for name in columns:
            owned_inputs = [v for v in inputs if owners.get((v.get('txid'), v.get('vout'))) == name]
            received = [(n, o) for n, o in enumerate(outputs) if owners.get((ident, n)) == name]
            if not owned_inputs and not received: continue
            for v in owned_inputs:
                key = (v['txid'], v['vout'])
                if key not in unspent[name]: raise ValueError('Incomplete historical wallet input ledger: ' + name)
                del unspent[name][key]
            for n, o in received: unspent[name][(ident, n)] = {'value': o['value'], 'address': o['scriptpubkey_address']}
            balances[name] += sum(o['value'] for _, o in received) - sum(v['prevout']['value'] for v in owned_inputs)
            assert balances[name] == sum(o['value'] for o in unspent[name].values())
            if joint:
                delta=sum(o['value'] for _,o in received)-sum(v['prevout']['value'] for v in owned_inputs)
                if delta:
                    row=core.WalletTx(name,len(records[name])+1,when,Decimal(delta)*SAT,Decimal(0),Decimal(balances[name])*SAT,'Joint transaction net balance; recipient allocation is calculated during CGT',txid=ident,is_historical_ods=True,fee_known=False,accounting_joint=True)
                    records[name].append(row)
                    evidence[row.ref]={'blockchain_txid':ident,'_utc':core.display_time(when),'_direction':'outgoing' if delta<0 else 'incoming','wallet_addresses':list(dict.fromkeys([v['prevout'].get('scriptpubkey_address') for v in owned_inputs]+[o['scriptpubkey_address'] for _,o in received])),'blockchain_source':'https://blockstream.info/tx/'+ident}
            elif sender == name:
                payments = [(n, o) for n, o in enumerate(outputs) if o['value'] > 0 and owners.get((ident, n)) != name]
                items = [(n, o['value'], o.get('scriptpubkey_address', '')) for n, o in payments] or [(-1, 0, '')]
                for i, (n, amount, address) in enumerate(items):
                    charged_fee = fee if i == 0 else 0
                    if not amount and not charged_fee: continue
                    row = core.WalletTx(name, len(records[name]) + 1, when, -Decimal(amount + charged_fee) * SAT, Decimal(charged_fee) * SAT, Decimal(balances[name]) * SAT, 'Blockchain spending transaction from supplied owned outputs', address=address, txid=ident, is_historical_ods=True, output_index=n)
                    records[name].append(row)
                    addresses = list(dict.fromkeys([v['prevout'].get('scriptpubkey_address') for v in owned_inputs] + [o['scriptpubkey_address'] for _, o in received]))
                    evidence[row.ref] = {'blockchain_txid': ident, '_utc': core.display_time(when), '_direction': 'outgoing', 'wallet_addresses': [a for a in addresses if a], 'blockchain_source': 'https://blockstream.info/tx/' + ident}
            else:
                for n, o in received:
                    row = core.WalletTx(name, len(records[name]) + 1, when, Decimal(o['value']) * SAT, Decimal(0), Decimal(balances[name]) * SAT, 'Explicit wallet-owned blockchain output', address=o['scriptpubkey_address'], txid=ident, is_historical_ods=True, output_index=n)
                    records[name].append(row)
                    evidence[row.ref] = {'blockchain_txid': ident, '_utc': core.display_time(when), '_direction': 'incoming', 'wallet_addresses': [row.address], 'blockchain_source': 'https://blockstream.info/tx/' + ident}
    client.wallet_evidence.update(evidence)
    client.owned_outputs = unspent
    core.HISTORICAL_OUTPOINTS = {'wallets': columns, 'unspent': unspent, 'evidence': evidence,'transactions':transactions,'owners':owners}
    original_lookup = core.lookup_transaction_online
    def lookup(ident):
        tx = transactions.get(ident)
        if tx is None: return original_lookup(ident)
        addresses = list(dict.fromkeys(v.get('prevout', {}).get('scriptpubkey_address') for v in tx.get('vin', [])))
        return core.ChainTxInfo(ident, Decimal(tx['fee']) * SAT if 'fee' in tx else None,
            [core.ChainTxOutput(n, Decimal(o['value']) * SAT, o.get('scriptpubkey_address', '')) for n, o in enumerate(tx['vout'])],
            [a for a in addresses if a], 'Exact supplied historical wallet outpoints and verified spending transactions')
    core.lookup_transaction_online = lookup
    print(f'Wallets ODS: reconstructed {sum(map(len, records.values())):,} records from {len(transactions):,} exact transactions and {spend_queries:,} spend-status checks; no date/amount searches.', flush=True)
    return records
