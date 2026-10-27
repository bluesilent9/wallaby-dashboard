"""Age date-only UTC statements at 23:59 UTC on the browser's local calendar."""
from datetime import date,datetime,time,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

def statement_age(stamp,local_date,zone='UTC'):
    try:
        end=datetime.combine(date.fromisoformat(stamp),time(23,59),timezone.utc)
        local=end.astimezone(ZoneInfo(zone)).date()
        days=(date.fromisoformat(local_date)-local).days
        return days,local.isoformat()
    except (TypeError,ValueError,ZoneInfoNotFoundError):return None,None
