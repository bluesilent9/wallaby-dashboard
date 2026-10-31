"""Public chain height and model-based calendar estimates; no vault creation or signing."""
from datetime import datetime,timezone,timedelta
import math

TARGET_SPACING=600
CONFIDENCE=.99

def poisson_cdf_left(n,mean):
    """P[N <= n], evaluated in the left half without a statistics dependency."""
    if n<0:return 0.
    if mean==0:return 1.
    if n>mean:raise ValueError('This routine requires a left-tail count.')
    term=total=1.;j=n
    while j>0:
        term*=j/mean;total+=term;j-=1
        if term<total*1e-15:break
    log_mass=-mean+n*math.log(mean)-math.lgamma(n+1)
    return min(1.,math.exp(log_mass)*total)

def estimate(height,target,now=None):
    now=now or datetime.now(timezone.utc)
    try:end=datetime.fromisoformat(target.replace('Z','+00:00'))
    except (AttributeError,ValueError):raise ValueError('Choose a valid future date and time.') from None
    if end.tzinfo is None:raise ValueError('The target date/time must specify its timezone.')
    seconds=(end-now).total_seconds()
    if not 0<seconds<=366*86400*100:raise ValueError('Choose a future date within the next 100 years.')
    if type(height) is not int or not 0<height<500000000:raise ValueError('Invalid current block height.')
    mean=seconds/TARGET_SPACING;lo=0;hi=math.floor(mean)+1
    # k new blocks put the tip strictly above H+k-1. The next higher
    # candidate must fail the confidence threshold; no normal approximation.
    while lo<hi:
        mid=(lo+hi+1)//2
        if poisson_cdf_left(mid-1,mean)<=1-CONFIDENCE-1e-12:lo=mid
        else:hi=mid-1
    k=lo;block=height+k-1
    if block>=500000000:raise ValueError('The predicted height exceeds the block-height timelock range.')
    return {'most_likely_utc':(now+timedelta(seconds=max(0,k-1)*TARGET_SPACING)).isoformat(),'height':block,'current_height':height,'required_new_blocks':k,'confidence':1-poisson_cdf_left(k-1,mean),'expected_blocks':mean,'target_utc':end.astimezone(timezone.utc).isoformat(),'calculated_utc':now.astimezone(timezone.utc).isoformat(),'future_height_available':block>height,'model':'Poisson arrivals at a constant 600 seconds per block; hashrate changes, difficulty adjustments, reorgs and observation delays are not modelled.'}

def target_at_seven(day,zone):
    """Resolve local 07:00, rejecting skipped/ambiguous wall times rather than shifting them."""
    from datetime import date,time
    from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
    try:
        if not isinstance(day,str) or len(day)!=10:raise ValueError()
        local=datetime.combine(date.fromisoformat(day),time(7));tz=ZoneInfo(zone)
    except (TypeError,ValueError,ZoneInfoNotFoundError):raise ValueError('Choose a valid date and IANA timezone.') from None
    candidates=set()
    for fold in (0,1):
        utc=local.replace(tzinfo=tz,fold=fold).astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None)==local:candidates.add(utc)
    if len(candidates)!=1:raise ValueError('7:00 AM is skipped or occurs twice on that date in this timezone. Choose another date.')
    return candidates.pop().isoformat()


def timezones_with_offsets(year=None):
    from zoneinfo import available_timezones,ZoneInfo
    from functools import lru_cache
    return _timezone_details(int(year or datetime.now(timezone.utc).year))

from functools import lru_cache
@lru_cache(maxsize=8)
def _timezone_details(year):
    from zoneinfo import available_timezones,ZoneInfo
    from datetime import date
    if not 1900<=year<=9998:raise ValueError('Choose a timezone year between 1900 and 9998.')
    aliases={}
    try:
        from zoneinfo import TZPATH
        for base in TZPATH:
            source=__import__('pathlib').Path(base)/'tzdata.zi'
            if source.exists():
                for line in source.read_text().splitlines():
                    if line.startswith(('L ','Link ')):
                        _,target,alias=line.split()[:3];aliases[alias]=target
                break
    except OSError:pass
    def canonical(z):
        seen=set()
        while z in aliases and z not in seen:seen.add(z);z=aliases[z]
        return z
    def offset(s):
        n=int(s)//60;return ('+' if n>=0 else '-')+f'{abs(n)//60:02}:{abs(n)%60:02}'
    zones=sorted(available_timezones()|{'UTC'});details={}
    for z in zones:
        tz=ZoneInfo(z);pairs=set()
        for day in range((date(year+1,1,1)-date(year,1,1)).days):
            dt=datetime(year,1,1,12,tzinfo=tz)+timedelta(days=day)
            pairs.add((int(dt.utcoffset().total_seconds()),bool(dt.dst())))
        offsets=sorted({n for n,dst in pairs});parts=[]
        for n in offsets:
            # Some regions use negative DST or temporary seasonal suspensions.
            roles={dst for value,dst in pairs if value==n}
            parts.append('UTC'+offset(n)+(' DST' if roles=={True} else ''))
        details[z]={'label':z+' ('+' / '.join(parts)+')','canonical':canonical(z),'offsets':offsets}
    zones.sort(key=lambda z:(details[z]['offsets'][0],z.casefold()))
    return {'timezones':zones,'details':details,'year':year}


def from_nodes(registry,day,zone,now=None):
    from node_chain import RegistryRPC,NodeDataUnavailable
    rpc=RegistryRPC(registry);errors=[]
    for node in rpc.nodes():
        try:
            info=rpc.call(node['id'],'getblockchaininfo')
            if info.get('chain')!='main' or info.get('initialblockdownload') or info.get('blocks',0)<info.get('headers',0):raise ValueError('Wait for a mainnet node to synchronise.')
            result=estimate(info['blocks'],target_at_seven(day,zone),now)
            if not result['future_height_available']:raise ValueError('Choose a later date; no future unlock height meets the 99% threshold.')
            result.update(target_timezone=zone,source=node['hostname']);return result
        except (ValueError,NodeDataUnavailable) as exc:errors.append(str(exc))
    raise ValueError('; '.join(errors) or 'Connect a synchronised mainnet node first.')
