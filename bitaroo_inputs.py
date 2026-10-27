"""Detect Bitaroo statement periods from one combined upload; retain saved-input compatibility."""
from datetime import datetime,timezone,date
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib,shutil

def boundary_day(zone,now=None):
    now=now or datetime.now(timezone.utc)
    try:local=now.astimezone(ZoneInfo(zone))
    except Exception:raise ValueError('Invalid browser timezone.') from None
    return (now.month,now.day) in ((6,30),(7,1)) or (local.month,local.day) in ((6,30),(7,1))

def _prepare_legacy(core,paths,dest,zone,now=None):
    now=now or datetime.now(timezone.utc);optional=boundary_day(zone,now);records={};dates={}
    local=now.astimezone(ZoneInfo(zone));allowed={now.year-(now.month<7),local.year-(local.month<7)}
    if optional:allowed|={y-1 for y in allowed}
    for slot in ('bitaroo_history','bitaroo_current'):
        root=paths.get(slot)
        if not root:
            if slot=='bitaroo_current' and optional:continue
            raise ValueError('Select '+('historical' if slot=='bitaroo_history' else 'current')+' Bitaroo PDFs in Ownership first.')
        files=sorted(Path(root).rglob('*.pdf'))+sorted(Path(root).rglob('*.PDF'))
        if not files:raise ValueError('No Bitaroo PDFs were provided in '+slot+'.')
        seen={}
        for path in files:
            side,fy,start,end=core.inspect_bitaroo_statement(path)
            if slot=='bitaroo_history' and end!=date(fy+1,6,30):raise ValueError('Historical Bitaroo PDFs must cover a complete 1 July–30 June financial year. Move partial/YTD statements into Current Bitaroo PDFs.')
            if slot=='bitaroo_current' and fy not in allowed:raise ValueError('Current Bitaroo PDFs must cover the current financial year.')
            key=(fy,side);content=hashlib.sha256(path.read_bytes()).hexdigest();candidate=(end,slot=='bitaroo_current',path,content)
            if key in seen and seen[key][0]==end and seen[key][3]!=content:raise ValueError('Conflicting Bitaroo PDFs cover the same period and asset.')
            seen[key]=candidate
            if key not in records or candidate[:2]>records[key][:2]:records[key]=candidate
        # Every supplied folder must contain complete AUD/BTC pairs with equal ranges.
        for fy in {key[0] for key in seen}:
            if (fy,'AUD') not in seen or (fy,'BTC') not in seen or seen[(fy,'AUD')][0]!=seen[(fy,'BTC')][0]:raise ValueError('Each Bitaroo directory requires matching AUD and BTC PDF date ranges for every financial year.')
    if Path(dest).exists():shutil.rmtree(dest)
    Path(dest).mkdir(parents=True,exist_ok=True)
    for (fy,side),(end,current,path,_) in records.items():shutil.copyfile(path,Path(dest)/(f'Bitaroo_{side}_{fy}-{fy+1}.pdf'))
    latest=max(fy for fy,_ in records);as_of=min(records[(latest,side)][0] for side in ('AUD','BTC'))
    return {'bitaroo_as_of_date':as_of.isoformat(),'latest_fy':latest,'current_optional':optional}

def merge_saved_uploads(paths,input_dir):
    """Expose pre-V155 saved PDF folders through the single current upload control."""
    legacy=[slot for slot in ('bitaroo_history','bitaroo_current') if slot in paths]
    if legacy and 'bitaroo' not in paths:
        combined=Path(input_dir)/'bitaroo';combined.mkdir(parents=True,exist_ok=True)
        for slot in legacy:
            source=Path(paths[slot])
            if source.is_dir():shutil.copytree(source,combined/slot,dirs_exist_ok=True)
        paths['bitaroo']=combined
    for slot in legacy:paths.pop(slot,None)

def prepare(core,paths,dest,zone,now=None):
    if 'bitaroo' not in paths:return _prepare_legacy(core,paths,dest,zone,now)
    now=now or datetime.now(timezone.utc);optional=boundary_day(zone,now)
    local=now.astimezone(ZoneInfo(zone));allowed={now.year-(now.month<7),local.year-(local.month<7)}
    if optional:allowed|={y-1 for y in allowed}
    files=sorted(p for p in Path(paths['bitaroo']).rglob('*') if p.is_file() and p.suffix.lower()=='.pdf')
    if not files:raise ValueError('No Bitaroo PDFs were provided.')
    periods={};records={}
    for path in files:
        side,fy,start,end=core.inspect_bitaroo_statement(path)
        if end!=date(fy+1,6,30) and fy not in allowed:raise ValueError('Partial Bitaroo statements must cover the current financial year. Supply complete historical financial years.')
        digest=hashlib.sha256(path.read_bytes()).hexdigest();key=(fy,start,end,side)
        if key in periods and periods[key][1]!=digest:raise ValueError('Conflicting Bitaroo PDFs cover the same period and asset.')
        periods[key]=(path,digest)
        if (fy,side) not in records or end>records[(fy,side)][0]:records[(fy,side)]=(end,path)
    for fy,start,end,side in periods:
        if (fy,start,end,'BTC' if side=='AUD' else 'AUD') not in periods:raise ValueError('Bitaroo PDFs require matching AUD and BTC statement date ranges for every supplied period.')
    if not optional and not any(fy in allowed for fy,side in records):raise ValueError('Include current financial-year AUD and BTC statements in Bitaroo PDFs.')
    dest=Path(dest)
    if dest.exists():shutil.rmtree(dest)
    dest.mkdir(parents=True,exist_ok=True)
    for (fy,side),(end,path) in records.items():shutil.copyfile(path,dest/f'Bitaroo_{side}_{fy}-{fy+1}.pdf')
    latest=max(fy for fy,side in records);as_of=min(records[(latest,side)][0] for side in ('AUD','BTC'))
    return {'bitaroo_as_of_date':as_of.isoformat(),'latest_fy':latest,'current_optional':optional}

def ledger_snapshot(core,reports):
    def row(r):return {'utc':core.display_time(r.dt),'type':r.tx_type,'amount':str(r.amount),'balance':str(r.balance),'asset':r.asset,'source_file':Path(r.source_file).name,'page':r.page,'trade_occurrence':r.trade_occurrence}
    return {'schema':132,'aud_rows':[row(r) for r in reports.aud_rows],'btc_rows':[row(r) for r in reports.btc_rows],
            'periods':{core.fmt_fy(fy):{'start':start.isoformat(),'end':end.isoformat(),'opening_btc':str(reports.start_bal.get(fy)),'closing_btc':str(reports.close_bal.get(fy))} for fy,(start,end) in reports.ranges.items()}}
