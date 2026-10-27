"""Public mempool.space recommendations, separate from taxpayer and tax pricing data."""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import json,threading,time,urllib.request

FEES_URL='https://mempool.space/api/v1/fees/precise'
PRICE_URL='https://mempool.space/api/v1/prices'
PRIORITIES=(('No Priority','economyFee'),('Low Priority','hourFee'),('Medium Priority','halfHourFee'),('High Priority','fastestFee'))

class FeeUnavailable(RuntimeError):pass

def stamp(value):return datetime.fromtimestamp(value,timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def number(value,positive=False):
    if isinstance(value,bool):raise ValueError('Invalid numeric quote')
    try:n=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('Invalid numeric quote') from None
    if not n.is_finite() or n<0 or positive and n<=0 or abs(n.adjusted())>20 or len(n.as_tuple().digits)>30:raise ValueError('Invalid numeric quote')
    return format(n,'f')

def fetch_json(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Wallaby-Bitcoin-Dashboard/124','Accept':'application/json','Cache-Control':'no-cache'})
    with urllib.request.urlopen(request,timeout=10) as response:
        body=response.read(262145)
    if len(body)>262144:raise ValueError('Provider response is too large')
    result=json.loads(body,parse_float=Decimal)
    if not isinstance(result,dict):raise ValueError('Unexpected provider response')
    return result

class FeeService:
    """Share only public quotes between browser workspaces; never wallet information."""
    def __init__(self,fetch=fetch_json,clock=time.time,cache_seconds=120,price_max_age=1800):
        self.fetch=fetch;self.clock=clock;self.cache_seconds=cache_seconds;self.price_max_age=price_max_age
        self.lock=threading.Lock();self.data=None;self.next_attempt=0;self.error=None

    def _view(self,now,cached):
        result=deepcopy(self.data);result['cached']=cached;result['stale']=bool(self.error) or now-result['_retrieved']>120
        result['warning']=self.error
        if result['price'] and now-result['price']['unix_time']>self.price_max_age:
            result['price']=None;result['price_error']='The BTC/AUD quote is too old; AUD estimates are unavailable.'
        result.pop('_retrieved',None)
        return result

    def snapshot(self,force=False):
        requested=self.clock()
        with self.lock:
            now=self.clock()
            if now<self.next_attempt and (not force or self.error or self.data and self.data['_retrieved']>=requested):
                if self.data:return self._view(now,True)
                raise FeeUnavailable(self.error or 'Fee estimates are temporarily unavailable.')
            try:
                raw=self.fetch(FEES_URL)
                rates=[{'label':label,'sat_per_vbyte':number(raw[key])} for label,key in PRIORITIES]
            except Exception as exc:
                self.error='Could not refresh fee estimates from mempool.space. '+str(exc)
                self.next_attempt=self.clock()+15
                if self.data:return self._view(self.clock(),True)
                raise FeeUnavailable(self.error) from exc
            retrieved=self.clock();price=None;price_error=None
            try:
                raw_price=self.fetch(PRICE_URL);observed=int(number(raw_price['time'],positive=True))
                aud=number(raw_price['AUD'],positive=True)
                if observed>self.clock()+120 or self.clock()-observed>self.price_max_age:raise ValueError('BTC/AUD quote timestamp is not current')
                price={'aud_per_btc':aud,'observed_utc':stamp(observed),'unix_time':observed,'source':PRICE_URL}
            except Exception as exc:price_error='BTC/AUD price unavailable; fee rates remain available. '+str(exc)
            self.data={'rates':rates,'retrieved_utc':stamp(retrieved),'source':FEES_URL,'price':price,'price_error':price_error,'cache_seconds':self.cache_seconds,'_retrieved':retrieved}
            self.error=None;self.next_attempt=self.clock()+self.cache_seconds
            return self._view(self.clock(),False)
