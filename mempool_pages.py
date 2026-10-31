"""Collect one immutable Agent fee snapshot; incomplete pages are never projected."""
import re,time
from decimal import Decimal,InvalidOperation

METHOD='wallaby_mempool_snapshot'
PAGE=1000
MAX_ENTRIES=100000
MAX_DEPENDENCIES=2000000

class InvalidSnapshot(ValueError):pass

def parameters(value):
 if value in ([],{}):return
 if not isinstance(value,dict) or set(value)!={'snapshot','offset'} or not isinstance(value['snapshot'],str) or not re.fullmatch('[0-9a-f]{32}',value['snapshot']) or type(value['offset']) is not int or not 0<=value['offset']<=MAX_ENTRIES or value['offset']%PAGE:raise ValueError('Invalid mempool snapshot page request.')

def collect(fetch,progress=lambda *a:None,clock=time.monotonic):
 result={};expected=0;identity=None;metadata=None;previous='';dependencies=0;started=clock()
 while True:
  if metadata and clock()-started>=metadata[1]-metadata[0]:raise InvalidSnapshot('Mempool snapshot expired during collection. Incomplete pages were discarded; refresh to capture a new snapshot.')
  page=fetch([] if identity is None else {'snapshot':identity,'offset':expected})
  fields={'version','snapshot','created_at','expires_at','total','offset','next_offset','entries'}
  if not isinstance(page,dict) or set(page)!=fields or type(page['version']) is not int or page['version']!=1 or not isinstance(page['snapshot'],str) or not re.fullmatch('[0-9a-f]{32}',page['snapshot']):raise InvalidSnapshot('Invalid mempool snapshot envelope.')
  if any(type(page[k]) is not int for k in ('created_at','expires_at','total','offset')) or page['created_at']<0 or not 0<page['expires_at']-page['created_at']<=300 or not 0<=page['total']<=MAX_ENTRIES:raise InvalidSnapshot('Invalid or excessive mempool snapshot bounds.')
  current=(page['created_at'],page['expires_at'],page['total'])
  if identity is None:identity=page['snapshot'];metadata=current
  if page['snapshot']!=identity or current!=metadata or page['offset']!=expected:raise InvalidSnapshot('Mempool snapshot identity, totals or page offset changed. Incomplete pages were discarded.')
  entries=page['entries'];size=min(PAGE,page['total']-expected);end=expected+size;next_=end if end<page['total'] else None
  if not isinstance(entries,dict) or len(entries)!=size or page['next_offset']!=next_ or next_ is not None and type(page['next_offset']) is not int:raise InvalidSnapshot('Missing, repeated or truncated mempool page. Incomplete pages were discarded.')
  if any(not isinstance(k,str) or not re.fullmatch('[0-9a-f]{64}',k) for k in entries):raise InvalidSnapshot('Invalid mempool transaction ID.')
  for txid,entry in sorted(entries.items()):
   if not isinstance(txid,str) or not re.fullmatch('[0-9a-f]{64}',txid) or txid<=previous or txid in result or not isinstance(entry,dict) or set(entry)!={'vsize','fees','depends'}:raise InvalidSnapshot('Invalid or duplicate mempool transaction entry.')
   fees=entry['fees'];parents=entry['depends']
   if type(entry['vsize']) is not int or not 1<=entry['vsize']<=4000000 or not isinstance(fees,dict) or set(fees)!={'modified'} or not isinstance(fees['modified'],str) or not re.fullmatch(r'-?\d{1,8}(?:\.\d{1,80})?',fees['modified']):raise InvalidSnapshot('Invalid mempool size or modified fee.')
   try:fee=Decimal(str(fees['modified']))
   except InvalidOperation:raise InvalidSnapshot('Invalid mempool modified fee.') from None
   if not fee.is_finite() or abs(fee)>21000000 or fee*100000000!=(fee*100000000).to_integral_value():raise InvalidSnapshot('Invalid mempool modified fee.')
   if not isinstance(parents,list) or len(parents)>MAX_ENTRIES or any(not isinstance(p,str) or not re.fullmatch('[0-9a-f]{64}',p) for p in parents) or len(set(parents))!=len(parents):raise InvalidSnapshot('Invalid mempool dependency list.')
   dependencies+=len(parents)
   if dependencies>MAX_DEPENDENCIES:raise InvalidSnapshot('Mempool dependency graph exceeds the bounded projection capacity.')
   result[txid]=entry;previous=txid
  expected=end;progress(expected,page['total'],identity,page['created_at'])
  if next_ is None:break
 if len(result)!=metadata[2] or any(p not in result for e in result.values() for p in e['depends']):raise InvalidSnapshot('Incomplete mempool dependency graph. No partial projection was used.')
 return result
