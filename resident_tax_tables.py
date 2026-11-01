"""Validated official resident brackets, refreshed on entry and saved in profiles.

No bundled bracket amounts or percentages. ATO tables are preferred; enacted
future tables are read from the current Income Tax Rates Act when not yet on
the ATO's historical/current table page. Failed downloads never erase a cache.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import io,json,os,re,threading,time
from storage import data_directory,storage_lock

D=Decimal
ATO='https://www.ato.gov.au/tax-rates-and-codes/tax-rates-australian-residents'
LAW='https://www.legislation.gov.au/C2004A03348/latest/text'
OFFSET='https://www.ato.gov.au/api/public/content/0-1b51c2a4-d678-43de-bb9e-0d2ffc632bd5'

def normal(text):return re.sub(r'\s+',' ',text.replace('\xa0',' ').replace('‑','-').replace('–','-')).strip()

class Page(HTMLParser):
    def __init__(self):super().__init__();self.tables=[];self.links=[];self.text=[];self.table=None;self.row=None;self.cell=None;self.depth=0
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.links.append(dict(attrs).get('href',''))
        if tag=='table':
            self.depth+=1
            if self.depth==1:self.table={'rows':[],'text':[]}
        if self.table is not None and self.depth==1:
            if tag=='tr':self.row=[]
            if tag in ('td','th'):self.cell=[]
    def handle_data(self,text):
        self.text.append(text)
        if self.table is not None:self.table['text'].append(text)
        if self.cell is not None:self.cell.append(text)
    def handle_endtag(self,tag):
        if self.table is not None and self.depth==1:
            if tag in ('td','th') and self.cell is not None:
                if self.row is not None:self.row.append(normal(''.join(self.cell)))
                self.cell=None
            if tag=='tr' and self.row is not None:self.table['rows'].append(self.row);self.row=None
        if tag=='table':
            if self.depth==1 and self.table is not None:self.tables.append(self.table);self.table=None
            self.depth=max(0,self.depth-1)

def dollar_values(text):return [D(v.replace(',','')) for v in re.findall(r'\$([0-9][0-9,]*(?:\.[0-9]+)?)',text)]

def valid_bands(bands):
    if not isinstance(bands,list) or not 2<=len(bands)<=20:raise ValueError('Unrecognised resident bracket count.')
    previous=D(0)
    for n,b in enumerate(bands):
        if set(b)!={'upper','rate'}:raise ValueError('Invalid resident bracket.')
        rate=D(b['rate']);upper=D(b['upper']) if b['upper'] is not None else None
        if not rate.is_finite() or not 0<=rate<=1 or upper is None and n!=len(bands)-1 or upper is not None and (not upper.is_finite() or upper<=previous):raise ValueError('Invalid resident bracket bounds.')
        if upper is not None:previous=upper
    if bands[-1]['upper'] is not None or D(bands[0]['rate'])!=0:raise ValueError('Resident table lacks a tax-free or open-ended band.')
    return bands

def parse_ato(html):
    page=Page();page.feed(html);tables=[]
    for table in page.tables:
        label=normal(' '.join(table['text']));year=re.match(r'Resident tax rates(?: for)? (20\d\d)\s*-\s*(\d{2})\b',label)
        if not year:continue
        fy=int(year[1])
        if fy<datetime.now(timezone.utc).year-1:continue
        if int(year[2])!=(fy+1)%100:raise ValueError('ATO financial-year caption does not reconcile.')
        bands=[];previous=D(0);total=D(0)
        for row in table['rows']:
            if len(row)!=2 or not re.match(r'(?:\$?0|\$[0-9])',row[0]):continue
            amounts=dollar_values(row[0]);upper=None if 'over' in row[0].lower() else amounts[-1] if amounts else None
            text=row[1];match=re.search(r'([0-9]+(?:\.[0-9]+)?)\s*c(?:ents)?\b',text)
            if text.lower()=='nil':rate=D(0)
            elif match:rate=D(match[1])/100
            else:raise ValueError('Unrecognised ATO marginal rate.')
            lower_values=dollar_values(text)
            if rate and (not lower_values or lower_values[-1]!=previous):raise ValueError('ATO formula lower bound differs from its bracket.')
            if 'plus' in text.lower() and abs(lower_values[0]-total)>D('.01'):raise ValueError('ATO cumulative base tax does not reconcile.')
            bands.append({'upper':str(upper) if upper is not None else None,'rate':str(rate)})
            if upper is not None:total+=(upper-previous)*rate;previous=upper
        tables.append({'year':fy,'until':fy,'source':ATO,'label':f'ATO resident rates {fy}-{str(fy+1)[2:]}','brackets':valid_bands(bands)})
    if not tables:raise ValueError('No complete ATO resident tax table was found.')
    return tables

def parse_law(html,url):
    page=Page();page.feed(html);text=normal(''.join(page.text))
    threshold=re.search(r'tax\s*-\s*free threshold means \$([0-9,]+)',text,re.I)
    if not threshold:raise ValueError('The Act tax-free threshold was not found.')
    tables=[]
    for table in page.tables:
        text=normal(''.join(table['text']));year=re.match(r'Tax rates for resident taxpayers for the (20\d\d)\s*-\s*(\d{2}) year of income',text)
        if not year:continue
        fy=int(year[1]);previous=D(threshold[1].replace(',',''));bands=[{'upper':str(previous),'rate':'0'}]
        for row in table['rows']:
            if len(row)!=3 or not row[0].isdigit():continue
            bounds=dollar_values(row[1]);rate=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)%',row[2])
            if not rate or not bounds:raise ValueError('Unrecognised statutory resident bracket.')
            if 'tax-free threshold' not in row[1] and bounds[0]!=previous:raise ValueError('Statutory resident bands do not join.')
            upper=bounds[-1] if 'does not exceed' in row[1] else None
            bands.append({'upper':str(upper) if upper is not None else None,'rate':str(D(rate[1])/100)})
            if upper is not None:previous=upper
        tables.append({'year':fy,'until':None if 'or a later year of income' in text else fy,'source':url,'label':f'Income Tax Rates Act · resident {fy}-{str(fy+1)[2:]}'+(' onwards' if 'or a later year of income' in text else ''),'brackets':valid_bands(bands)})
    if not tables:raise ValueError('No statutory resident tax table was found.')
    return tables

def parse_offset(body):
    from pypdf import PdfReader
    text=normal(' '.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(body)).pages)).replace('\x00',' ')
    text=text[text.index('If you earned:'):];end=text.find('We will work out your offset');text=text[:end]
    first=re.search(r'\$([0-9,]+) or less.*?maximum offset of \$([0-9,]+)',text)
    bands=[]
    if not first:raise ValueError('ATO low-income offset maximum was not found.')
    bands.append({'lower':'0','upper':first[1].replace(',',''),'base':first[2].replace(',',''),'rate':'0'})
    for match in re.finditer(r'between \$([0-9,]+) and \$([0-9,]+).*?\$([0-9,]+) minus ([0-9.]+) cents for every \$1 above \$([0-9,]+)',text):
        lower,upper,base,rate,formula=match.groups()
        if D(formula.replace(',',''))!=D(bands[-1]['upper']):raise ValueError('ATO offset formula does not reconcile.')
        bands.append({'lower':formula.replace(',',''),'upper':upper.replace(',',''),'base':base.replace(',',''),'rate':str(D(rate)/100)})
    if len(bands)!=3:raise ValueError('Incomplete ATO low-income offset formula.')
    return {'source':OFFSET,'label':'ATO low income tax offset','bands':bands}

def validate(data):
    if not isinstance(data,dict) or data.get('schema')!=1 or not isinstance(data.get('tables'),list) or not 1<=len(data['tables'])<=150:raise ValueError('Invalid saved resident tax tables.')
    datetime.fromisoformat(data['checked_utc'].replace('Z','+00:00'))
    for t in data['tables']:
        if type(t['year']) is not int or not 1900<=t['year']<=2200 or t['until'] is not None and t['until']!=t['year'] or urlparse(t['source']).scheme!='https' or urlparse(t['source']).hostname not in ('www.ato.gov.au','www.legislation.gov.au'):raise ValueError('Invalid saved tax table source or financial year.')
        valid_bands(t['brackets'])
    offset=data.get('offset')
    if offset:
        if offset.get('source')!=OFFSET or not isinstance(offset.get('bands'),list) or not 1<=len(offset['bands'])<=10:raise ValueError('Invalid saved offset.')
        previous=D(0)
        for row in offset['bands']:
            values=[D(row[k]) for k in ('lower','upper','base','rate')]
            if any(not v.is_finite() or v<0 for v in values) or values[0]!=previous or values[1]<=previous or values[3]>1:raise ValueError('Invalid saved offset bands.')
            previous=values[1]
    return data

def select(data,fy):
    choices=[t for t in data.get('tables',[]) if t['year']<=fy and (t['until'] is None or fy<=t['until'])]
    if not choices:return None
    return max(choices,key=lambda t:(t['source']==ATO,t['year']))

def fetch(url):
    with urlopen(Request(url,headers={'User-Agent':'WallabyDashboard/1.0 public tax-table reader','Accept':'text/html,application/pdf'}),timeout=8) as r:
        if urlparse(r.url).scheme!='https' or urlparse(r.url).hostname not in ('www.ato.gov.au','www.legislation.gov.au'):raise ValueError('Unexpected tax source redirect.')
        raw=r.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ValueError('Official tax source exceeds the document size limit.')
        return raw

class ResidentTaxTables:
    def __init__(self,config,download=fetch):self.config=Path(config);self.path=data_directory(config)/'resident-tax-tables.json';self.download=download;self.lock=threading.Lock();self.busy=False;self.error='';self.last_attempt=0
    def read(self):
        try:return validate(json.loads(self.path.read_text()))
        except (OSError,ValueError,TypeError,KeyError):return None
    def snapshot(self,refresh=False):
        if refresh:self.request_refresh()
        data=self.read()
        return {'data':data,'refreshing':self.busy,'error':self.error,'cached':bool(data)}
    def request_refresh(self):
        with self.lock:
            # Coalesce simultaneous browsers and repeated page openings. Never
            # fetch on each keystroke; each new visit rechecks after this guard.
            if self.busy or time.monotonic()-self.last_attempt<30:return
            self.busy=True;self.last_attempt=time.monotonic()
        threading.Thread(target=self.refresh,daemon=True,name='resident-tax-tables').start()
    def refresh(self):
        old=self.read();errors=[]
        def ato():return parse_ato(self.download(ATO).decode('utf-8-sig'))
        def law():
            page=Page();page.feed(self.download(LAW).decode('utf-8-sig'))
            url=next((u.split('#')[0] for u in page.links if re.fullmatch(r'https://www\.legislation\.gov\.au/C2004A03348/[0-9-]+/[0-9-]+/text/original/epub/OEBPS/document_1/document_1\.html(?:#.*)?',u)),None)
            if not url:raise ValueError('Current statutory document link was not found.')
            return parse_law(self.download(url).decode('utf-8-sig'),url)
        def offset():return parse_offset(self.download(OFFSET))
        tables=[];offset_value=(old or {}).get('offset');successful=False
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                jobs=[('ATO',pool.submit(ato)),('Act',pool.submit(law)),('Offset',pool.submit(offset))]
                for kind,job in jobs:
                    try:
                        result=job.result()
                        if kind=='Offset':offset_value=result
                        else:
                            stamp=datetime.now(timezone.utc).isoformat()
                            tables.extend(dict(t,checked_utc=stamp) for t in result);successful=True
                    except Exception as exc:
                        errors.append(kind+': '+str(exc)[:240])
                        if old and kind!='Offset':tables.extend(t for t in old['tables'] if (t['source']==ATO)==(kind=='ATO'))
            if tables and successful:
                stamp=datetime.now(timezone.utc).isoformat();digest=sha256(json.dumps([[{k:v for k,v in t.items() if k!='checked_utc'} for t in tables],offset_value],sort_keys=True).encode()).hexdigest()
                data={'schema':1,'checked_utc':stamp,'updated_utc':stamp if not old or old.get('digest')!=digest else old.get('updated_utc',stamp),'digest':digest,'tables':tables,'offset':offset_value}
                validate(data)
                with storage_lock(self.config):
                    self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(data));tmp.chmod(0o600);os.replace(tmp,self.path)
            self.error='; '.join(errors)
        except Exception as exc:self.error='Official tax data was not replaced: '+str(exc)[:240]
        finally:
            self.busy=False

def bracket_tax(income,table):
    if not table:raise ValueError('No validated resident tax table covers this financial year.')
    income=max(D(0),D(income));lower=D(0);total=D(0)
    for band in valid_bands(table['brackets']):
        upper=D(band['upper']) if band['upper'] is not None else income
        total+=max(D(0),min(income,upper)-lower)*D(band['rate']);lower=upper
    return total

def offset_amount(income,offset):
    if not offset:raise ValueError('The official low-income offset is unavailable. Disable that offset or refresh the tax data.')
    for band in offset['bands']:
        if income<=D(band['upper']):return max(D(0),D(band['base'])-(income-D(band['lower']))*D(band['rate']))
    return D(0)
