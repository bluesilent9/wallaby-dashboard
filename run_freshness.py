"""One read-only freshness notice, based on completed shared runs and local calendar days."""
from calendar import monthrange
from datetime import date,datetime,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from report_store import ReportStore

PAGES={'history','setup','totals','balances','builder','vaults'}

def warning(config,snapshot,local_date,zone='UTC'):
    try:today=date.fromisoformat(local_date);tz=ZoneInfo(zone)
    except (TypeError,ValueError,ZoneInfoNotFoundError):raise ValueError('Invalid local date or timezone.') from None
    def parsed(stamp):
        try:
            value=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if value.tzinfo is None:return None
            return value
        except (AttributeError,ValueError):return None
    ownership=[d for r in snapshot.get('ownership_runs',{}).values() if (d:=parsed(r.get('completed_utc')))]
    owned=max(ownership) if ownership else None
    cgt=parsed(snapshot.get('cgt_completed_utc')) or parsed(ReportStore(config)._index().get('completed_utc'))
    for kind,stamp in [('ownership',owned),('cgt',cgt)]:
        if stamp is None:continue
        local=stamp.astimezone(tz).date();age=(today-local).days
        month=local.month%12+1;year=local.year+(local.month==12);next_month=date(year,month,min(local.day,monthrange(year,month)[1]))
        if age<(3 if kind=='ownership' else 7):continue
        red=age>=7 if kind=='ownership' else today>=next_month
        text=('Warning: Ownership data may be out of date. Run the Ownership process to refresh it.' if kind=='ownership' else 'Warning: CGT report data may be out of date. Run a new CGT Report to refresh it.')
        return {'kind':'run_freshness','run_kind':kind,'run_stamp':stamp.astimezone(timezone.utc).isoformat(),'age_days':age,'severity':'red' if red else 'yellow','text':text,'target':'ownership' if kind=='ownership' else 'setup','dismissible':False,'read':True}
    return None
