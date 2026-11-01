"""Family-wide source declarations; financial data is never inferred from labels."""
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import re
import json

PREFIX='bitaroo_user_'
def account_slot(uid):return PREFIX+uid
def smsf_slot(uid):return 'bitaroo_smsf_'+uid
def account_source(uid,smsf=False):return uid+(':smsf' if smsf else '')
def source_user(source):return source.removesuffix(':smsf')
def is_smsf(source):return source.endswith(':smsf')
def account_id(slot):
    prefix='bitaroo_smsf_' if slot.startswith('bitaroo_smsf_') else PREFIX
    value=slot[len(prefix):] if slot.startswith(prefix) else ''
    return value if re.fullmatch('[0-9a-f]{32}',value) else None
def folder_slot(slot):return slot in {'bitaroo','bitaroo_history','bitaroo_current'} or account_id(slot) is not None
def declarations(users,settings,paths,smsf=False):
    skipped=settings.get('non_bitaroo_smsf',[u['id'] for u in users]) if smsf else settings.get('non_bitaroo',[]);ids={u['id'] for u in users}
    if not isinstance(skipped,list) or len(skipped)!=len(set(skipped)) or any(uid not in ids for uid in skipped):raise ValueError('Invalid Non Bitaroo user selection.')
    missing=[u['name'] for u in users if u['id'] not in skipped and not paths.get(smsf_slot(u['id']) if smsf else account_slot(u['id']))]
    if missing:raise ValueError(('Upload Bitaroo SMSF PDFs or select Non-Bitaroo SMSF User for: ' if smsf else 'Upload Bitaroo PDFs or select Non-Bitaroo User for: ')+', '.join(missing))
    return {u['id']:u['id'] not in skipped for u in users}
def display_year(zone,now=None):
    now=now or datetime.now(timezone.utc);local=now.astimezone(ZoneInfo(zone));year=local.year-(local.month<7)
    if (now.month,now.day) in ((6,30),(7,1)):year=min(year,now.year-1)
    return year
def prepare_family(core,users,settings,paths,dest,now=None,smsf=False):
    from bitaroo_inputs import prepare
    active=declarations(users,settings,paths,smsf=smsf);result={};root=Path(dest)
    for user in sorted(users,key=lambda u:u['id']):
        uid=user['id']
        if not active[uid]:result[uid]={'reports':core.ReportSet(),'coverage':{'bitaroo_as_of_date':None},'directory':None};continue
        target=root/uid
        try:
            coverage=prepare(core,{'bitaroo':paths[smsf_slot(uid) if smsf else account_slot(uid)]},target,settings.get('browser_timezone','UTC'),now)
            reports=core.ReportSet();core.load_bitaroo_reports_from_directory(reports,str(target),coverage['latest_fy'])
        except (ValueError,RuntimeError) as exc:raise ValueError(user['name']+' · '+str(exc)) from exc
        result[uid]={'reports':reports,'coverage':coverage,'directory':str(target)}
    return result

def tax_contexts(core,path,users):
    """A whole-return calculation belongs to one person and one year only."""
    if not path:return {}
    data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or set(data)!={'users'} or not isinstance(data['users'],dict):raise ValueError('V155 tax-context JSON must identify each user and year: {"users": {"User name or ID": {"2026-27": {"basic_tax": "0", "basic_tax_without_gain": "0"}}}}. A single unlabelled return cannot be applied to every family member and financial year.')
    result={}
    for label,years in data['users'].items():
        matches=[u for u in users if label==u['id'] or label.casefold()==u['name'].casefold()]
        if len(matches)!=1 or not isinstance(years,dict):raise ValueError('Tax context identifies an unknown user or invalid year map: '+label)
        uid=matches[0]['id']
        for year,context in years.items():
            fy=core.parse_fy(year)
            if fy in result.setdefault(uid,{}):raise ValueError('Duplicate whole-return tax context for one person/year.')
            core.minimum_tax_gap(core.D(0),**context);result[uid][fy]=context
    return result
