"""Presentation evidence for account existence/balance, separate from cost provenance."""
from pathlib import Path
from functools import lru_cache
import json
from descriptor_store import DescriptorStore
from storage import data_directory
from address_book import valid_address

@lru_cache(maxsize=8)
def history(config,fingerprint,stamp,size):
    root=data_directory(config)/'descriptor-history'/(fingerprint+'.json')
    try:
        envelope=json.loads(root.read_text());import hashlib
        if hashlib.sha256(envelope['payload'].encode()).hexdigest()!=envelope['sha256']:return {}
        return json.loads(envelope['payload'])
    except (OSError,ValueError,KeyError,TypeError):return {}

def annotate(report,catalog,snapshot,config):
    if not report:return
    fp=DescriptorStore.fingerprint(catalog);path=data_directory(config)/'descriptor-history'/(fp+'.json')
    try:stat=path.stat();chain=history(str(config),fp,stat.st_mtime_ns,stat.st_size)
    except OSError:chain={}
    txs=chain.get('transactions',{});spent={(v['txid'],v['vout']) for t in txs.values() for v in t.get('inputs',[]) if v.get('txid')}
    by_id={w['id']:w for w in catalog.get('wallets',[])};owned={}
    for outpoint,uid in chain.get('wallet_outputs',{}).items():
        ident,index=outpoint.rsplit(':',1);n=int(index)
        if (ident,n) in spent:continue
        outputs=txs.get(ident,{}).get('outputs',[])
        if n<len(outputs) and valid_address(outputs[n].get('address')):owned.setdefault(uid,set()).add(outputs[n]['address'])
    ownership={r['id']:r for r in snapshot.get('ownership',[])}
    for row in report.get('balances',{}).get('wallets',[]):
        uid=row.get('descriptor_id');w=by_id.get(uid)
        if w:
            addresses=set(owned.get(uid,()))
            pending={r['address'] for r in w.get('node_history',[]) if r.get('category') in ('receive','generate','immature') and r.get('address') and int(r.get('confirmations',0))==0}
            addresses.update(a for a in pending if valid_address(a))
            note='Wallet balance from your node.'
            if not addresses:
                addresses={r['address'] for r in w.get('node_history',[]) if r.get('category') in ('receive','generate','immature') and valid_address(r.get('address'))}
                note='Addresses recorded for this wallet.'
            if not addresses and valid_address(w.get('receiving_address')):addresses.add(w['receiving_address']);note='Receiving address; no funded output is implied.'
            nodes=', '.join(dict.fromkeys(s.get('hostname',s['node_id']) for s in w.get('node_sources',[])))
            row['balance_sources']=[row['name']+' — '+a+(' · pending ledger' if a in pending else '') for a in sorted(addresses)]
            row['balance_evidence_note']=note+(' Node: '+nodes+'.' if nodes else '')
        elif row.get('kind')=='exchange':
            record=ownership.get(row.get('resource_id'),{});owner=row.get('owner_id');meta=snapshot.get('ownership_runs',{}).get(owner,{})
            if record.get('kind')=='bitaroo':
                ledger=meta.get('bitaroo_smsf_data' if row.get('smsf') else 'bitaroo_data',{});rows=ledger.get('btc_rows',[])+ledger.get('aud_rows',[])
                sources={r.get('source_file') for r in rows if r.get('source_file')}
                # Canonical statement names encode their FY; latest FY is the
                # balance evidence even when it contains no new trade rows.
                import re
                period=max(ledger.get('periods',{}),default='')
                fy=re.match(r'(20\d\d)',period)
                if fy:
                    year=int(fy[1]);latest=[f for f in sources if str(year)+'-'+str(year+1) in f or str(year)+'-'+str(year+1)[2:] in f]
                    if latest:sources=set(latest)
                slot=('bitaroo_smsf_' if row.get('smsf') else 'bitaroo_user_')+str(owner)
                uploaded=[f['name'] for f in meta.get('files',[]) if f.get('slot')==slot and f.get('name','').lower().endswith('.pdf')]
                if uploaded:
                    def rank(name):
                        fy=re.search(r'FY\s*(\d{4}|\d{2})',name,re.I);years=re.findall(r'20\d{2}',name)
                        return int(fy[1])+(2000 if len(fy[1])==2 else 0) if fy else max(map(int,years),default=0)
                    newest=max(map(rank,uploaded));sources={name for name in uploaded if rank(name)==newest}
                if sources:
                    by_asset={}
                    for source in sorted(sources):
                        asset='AUD' if 'AUD' in source.upper() else 'BTC';by_asset[asset]=Path(source).name
                    row['balance_sources']=list(by_asset.values())
            row['balance_evidence_note']='Latest supplied account statements; not a live exchange balance query.'
        row.setdefault('balance_sources',[])
        row.setdefault('balance_evidence_note','Balance evidence is unavailable. Refresh node wallet history and Ownership.')
