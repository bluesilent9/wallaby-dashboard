"""Editable descriptor display labels; Core names and descriptor IDs stay intact."""
import re
from pathlib import Path
from preferences import resource_id

def suggest_wallet_name(name):
    stem=Path(name).stem.strip()
    suggested=re.sub(r'\s+',' ',re.sub(r'_+',' ',stem)).strip().title() or 'Wallet'
    if not re.search(r'\bwallet\b',suggested,re.I):suggested+=' Wallet'
    return suggested

def display_inventory(inventory,catalog):
    sources={w['id']:w['name'] for w in catalog['wallets']}
    vaults={w['id']:bool(w.get('has_vault')) for w in catalog['wallets']}
    result=[]
    for item in inventory:
        item=dict(item)
        if item['kind']=='descriptor':
            raw=sources.get(item['source'],item.get('source_name',item['name']))
            item.update(source_name=raw,name=suggest_wallet_name(raw),has_vault=vaults.get(item['source'],False))
        result.append(item)
    return result

def display_summary(summary,snapshot):
    records={r['id']:r for r in snapshot['ownership']}
    summary=dict(summary);summary['wallets']=[dict(w) for w in summary['wallets']]
    for w in summary['wallets']:
        w['source_name']=w['name'];record=records.get(resource_id('descriptor',w['id']))
        w['name']=record['name'] if record and record.get('confirmed') else suggest_wallet_name(w['name'])
    return summary

def apply_display_names(core,wallets,resolved):
    descriptor_ids=getattr(core,'DESCRIPTOR_WALLETS',{})
    labels={uid:resolved[resource_id('descriptor',uid)]['name'] for uid in descriptor_ids.values()}
    renamed={}
    for name,rows in wallets.items():
        label=labels[descriptor_ids[name]] if name in descriptor_ids else name
        if label.casefold() in {n.casefold() for n in renamed}:raise ValueError('Wallet display names must be unique.')
        renamed[label]=rows
    for label,rows in renamed.items():
        for row in rows:row.sheet=label
    wallets.clear();wallets.update(renamed)
    core.DESCRIPTOR_WALLETS={labels[uid]:uid for uid in descriptor_ids.values()}
    # This snapshot is the CGT worker's private, in-memory copy, not descriptors.cfg.
    for w in getattr(core,'DESCRIPTOR_SNAPSHOT',{}).get('wallets',[]):
        if w['id'] in labels:w['name']=labels[w['id']]
