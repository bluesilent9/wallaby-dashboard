"""Low-priority, node-only fee and block observations; market FX stays external."""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import heapq,json,threading,time,urllib.request,re
from collections import deque
from node_chain import RegistryRPC,NodeChain,satoshis,NodeDataUnavailable

class FeeUnavailable(RuntimeError):pass
class Deferred(Exception):pass

def stamp(value):return datetime.fromtimestamp(value,timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
def number(value):
    n=Decimal(str(value))
    if not n.is_finite() or n<0:raise ValueError('Invalid fee quote')
    return format(n,'f')

def project(entries,limit=4,capacity=999000):
    """Ancestor-package greedy block estimates. Dependencies precede children.

    This is an estimate of a node's local mempool, not Core's exact block
    assembler policy or a guarantee. Re-evaluate a package after an ancestor
    enters a block; don't count its size/fee twice.
    """
    if len(entries)>50000:raise ValueError('Local mempool is too large for the bounded projection; fee recommendations remain available.')
    parents={t:{p for p in e.get('depends',[]) if p in entries} for t,e in entries.items()};anc={};visiting=set()
    def ancestry(t):
        if t in anc:return anc[t]
        if t in visiting:raise ValueError('Cyclic mempool dependency')
        if len(visiting)>500:raise ValueError('Mempool ancestry exceeds the projection complexity limit')
        visiting.add(t);value={t}
        for p in parents[t]:value.update(ancestry(p))
        visiting.remove(t)
        if len(value)>1000:raise ValueError('Mempool package exceeds projection complexity limit')
        anc[t]=value;return value
    values={t:(int(e['vsize']),satoshis(e['fees']['modified'])) for t,e in entries.items()}
    if any(s<=0 for s,f in values.values()):raise ValueError('Invalid mempool virtual size')
    heap=[];selected=set();blocks=[];versions={t:0 for t in entries};children={t:set() for t in entries}
    for t,ps in parents.items():
        for parent in ps:children[parent].add(t)
    def candidate(t):
        package=ancestry(t)-selected;size=sum(values[p][0] for p in package);fee=sum(values[p][1] for p in package)
        return (-(Decimal(fee)/size) if size else Decimal(0),t,size,fee,package,versions[t])
    for t in sorted(entries):heapq.heappush(heap,candidate(t))
    for number_ in range(limit):
        used=0;fees=0;rates=[];count=0;deferred=[]
        while heap:
            old=heapq.heappop(heap);t=old[1]
            if t in selected or old[5]!=versions[t]:continue
            value=candidate(t)
            # Removing selected ancestors can only reduce a descendant's
            # package rate after its best package was selected. Reinsert it.
            if value[4]!=old[4]:heapq.heappush(heap,value);continue
            rate,t,size,fee,package,version=value
            if used+size>capacity:deferred.append(value);continue
            selected.update(package);used+=size;fees+=fee;count+=len(package);rates.append(-rate)
            affected=set();pending=list(package)
            while pending:
                parent=pending.pop()
                for child in children[parent]:
                    if child not in selected and child not in affected:affected.add(child);pending.append(child)
            for child in sorted(affected):versions[child]+=1;heapq.heappush(heap,candidate(child))
        for value in deferred:heapq.heappush(heap,value)
        blocks.append({'index':number_,'vsize':used,'weight':used*4,'tx_count':count,'fees_sats':fees,'median_rate':number(sorted(rates)[len(rates)//2]) if rates else '0','minimum_rate':number(min(rates)) if rates else '0','maximum_rate':number(max(rates)) if rates else '0','estimated_minutes':(number_+1)*10})
        if not heap:break
    return blocks

class FeeService:
    REFRESH_SECONDS=120
    RETRY_SECONDS=30
    RPC_SECONDS=15
    def __init__(self,registry,busy=lambda:False,clock=time.time):
        self.registry=registry;self.busy=busy;self.clock=clock;self.lock=threading.RLock();self.data=None;self.refreshing=False;self.last_attempt=None;self.error=None;self.headers={};self.price=None;self.closed=False;self.phase='';self.diagnostics=deque(maxlen=100)
    def log(self,text):
        with self.lock:self.diagnostics.append(stamp(self.clock())+' · Fee Estimates · '+str(text)+'\n')
        if getattr(self.registry,'diagnostic_log',None):self.registry.diagnostic_log.append('Fee Estimates',text)
    def log_text(self):
        with self.lock:return ''.join(self.diagnostics)
    def activity(self):
        with self.lock:return {'active':self.refreshing,'phase':self.phase,'lines':list(self.diagnostics)[-20:]}
    def call(self,chain,method,params=None):
        if self.closed or self.busy():raise Deferred()
        with self.lock:self.phase='Requesting '+method+' from the connected node…'
        return chain.call(method,params)
    def fx(self):
        if self.price and self.clock()-self.price['unix_time']<120:return self.price
        url='https://api.kraken.com/0/public/Ticker?pair=XBTAUD'
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Wallaby Dashboard'}),timeout=10) as r:reply=json.load(r)
        if reply.get('error'):raise ValueError('BTC/AUD quote unavailable')
        aud=number(next(iter(reply['result'].values()))['c'][0]);observed=self.clock()
        if Decimal(aud)<=0:raise ValueError('Invalid BTC/AUD quote')
        self.price={'aud_per_btc':aud,'unix_time':observed,'observed_utc':stamp(observed),'source':'Kraken BTC/AUD ticker; retrieved time'};return self.price
    def publish(self,**fields):
        with self.lock:
            if self.data is not None:self.data.update(deepcopy(fields))
    def refresh(self):
        self.log('Refresh started.')
        try:
            if self.busy():raise Deferred()
            nodes=[n for n in RegistryRPC(self.registry).nodes() if n.get('rpc_ready')]
            if not nodes:raise FeeUnavailable('Connect a Bitcoin Core node through Wallaby Agent for fee estimates.')
            node=next((n for n in nodes if n.get('blockchain',{}).get('state')=='ready'),nodes[0]);service=self
            class SelectedRPC(RegistryRPC):
                def nodes(inner):return [node]
                def call(inner,node_id,method,params=None,wallet=None):
                    state=inner.registry.rpc.call(node_id,method,params or [],wallet,timeout_seconds=service.RPC_SECONDS,consume=True,priority=100)
                    if state['state']!='succeeded':raise NodeDataUnavailable(str((state.get('error') or {}).get('message',state['state'])))
                    return state['result']
            chain=NodeChain(SelectedRPC(self.registry,priority=100),{'wallets':[]})
            info=self.call(chain,'getblockchaininfo')
            if info.get('chain')!='main' or info.get('initialblockdownload') or info.get('blocks',0)<info.get('headers',0):raise FeeUnavailable('Wait for the connected mainnet node to synchronise.')
            self.log('Mainnet tip verified at height '+str(info['blocks'])+'. Reading mempool minimum and four confirmation targets.')
            mempool=self.call(chain,'getmempoolinfo');floor=max(Decimal(number(mempool.get('mempoolminfee',0))),Decimal(number(mempool.get('minrelaytxfee',0))))*100000
            rates=[];fallback=[]
            for label,target,mode in [('No Priority',144,'ECONOMICAL'),('Low Priority',6,'ECONOMICAL'),('Medium Priority',3,'CONSERVATIVE'),('High Priority',1,'CONSERVATIVE')]:
                estimate=self.call(chain,'estimatesmartfee',[target,mode]);rate=estimate.get('feerate')
                if rate is None:
                    value=floor;basis='Local mempool minimum; insufficient Core fee history';fallback.append(len(rates))
                else:value=max(floor,Decimal(number(rate))*100000);basis='Core estimatesmartfee · target '+str(estimate.get('blocks',target))+' blocks'
                rates.append({'label':label,'sat_per_vbyte':number(value),'basis':basis})
            for i in range(1,len(rates)):rates[i]['sat_per_vbyte']=number(max(Decimal(rates[i]['sat_per_vbyte']),Decimal(rates[i-1]['sat_per_vbyte'])))
            now=self.clock()
            # Publish the useful rates before optional, potentially expensive data.
            with self.lock:
                self.data={'rates':deepcopy(rates),'retrieved_utc':stamp(now),'source':node['hostname']+' · Bitcoin Core RPC','price':None,'price_error':None,'projected_blocks':[],'projection_error':None,'recent_blocks':[],'tip_height':info['blocks'],'mempool_transactions':mempool['size'],'cache_seconds':self.REFRESH_SECONDS,'_retrieved':now};self.error=None
            self.log('Four fee priorities ready from '+node['hostname']+'.')
            try:
                self.phase='Retrieving BTC/AUD market quote…';price=self.fx();self.publish(price=price);self.log('BTC/AUD quote ready.')
            except (ValueError,OSError,KeyError,StopIteration) as exc:
                self.publish(price_error='BTC/AUD valuation unavailable. '+str(exc));self.log('AUD quote unavailable: '+str(exc))
            try:
                projection=project(self.call(chain,'getrawmempool',[True]));self.publish(projected_blocks=projection);self.log('Mempool projection ready: '+str(len(projection))+' estimated blocks.')
                if projection and fallback:
                    for index in fallback:rates[index].update(sat_per_vbyte=number(max(floor,Decimal(projection[0]['minimum_rate']))),basis='Local mempool package estimate; insufficient Core fee history')
                    for i in range(1,len(rates)):rates[i]['sat_per_vbyte']=number(max(Decimal(rates[i]['sat_per_vbyte']),Decimal(rates[i-1]['sat_per_vbyte'])))
                    self.publish(rates=rates)
            except (ValueError,NodeDataUnavailable) as exc:
                self.publish(projection_error=str(exc));self.log('Block projection unavailable: '+str(exc))
            recent=[];h=info['bestblockhash']
            try:
                for _ in range(8):
                    if h in self.headers:block=self.headers[h]
                    else:
                        header=self.call(chain,'getblockheader',[h,True]);block=dict(header)
                        # Headers already include transaction count. Size/weight
                        # metadata is collected separately after all headers.
                        block.update(size=None,weight=None,tx_count=header.get('nTx'));self.headers[h]=block
                    recent.append({'hash':h,'height':block['height'],'timestamp':block['time'],'size':block.get('size'),'weight':block.get('weight'),'tx_count':block.get('tx_count')});self.publish(recent_blocks=list(recent));h=block.get('previousblockhash')
                    if not h:break
                # Size/weight are optional display metadata. Keep this separate
                # from rates and headers, and stop at the first unavailable block.
                for row in recent:
                    cached=self.headers[row['hash']]
                    if cached.get('size') is None:
                        full=self.call(chain,'getblock',[row['hash'],1]);cached.update(size=full.get('size'),weight=full.get('weight'))
                    row.update(size=cached.get('size'),weight=cached.get('weight'));self.publish(recent_blocks=recent)
                if len(self.headers)>32:self.headers={b['hash']:self.headers[b['hash']] for b in recent}
            except (ValueError,NodeDataUnavailable,KeyError) as exc:
                self.publish(block_error='Recent block data unavailable. '+str(exc));self.log('Recent blocks unavailable: '+str(exc))
            self.log('Refresh completed; '+str(len(recent))+' recent block headers loaded. Next automatic refresh in 2 minutes.')
        except Deferred:
            self.log('Refresh paused for higher-priority node work.')
        except Exception as exc:
            with self.lock:self.error='Fee refresh failed: '+str(exc)
            self.log(self.error)
        finally:
            with self.lock:self.refreshing=False;self.phase=''
    def snapshot(self,force=False):
        with self.lock:
            now=self.clock();waiting=self.busy();elapsed=float('inf') if self.last_attempt is None else now-self.last_attempt
            due=self.data is None and elapsed>=self.RETRY_SECONDS or self.data is not None and elapsed>=self.REFRESH_SECONDS or force and elapsed>=5
            if not self.closed and not waiting and not self.refreshing and due:
                self.last_attempt=now;self.refreshing=True;self.phase='Collecting node fee estimates…';threading.Thread(target=self.refresh,daemon=True).start()
            progress='Waiting for the node to finish higher-priority work.' if waiting else self.phase if self.refreshing else self.error
            if not self.data:return {'rates':[],'recent_blocks':[],'projected_blocks':[],'loading':self.refreshing,'waiting':waiting,'warning':self.error or progress or 'Fee estimates unavailable. Select Refresh estimates to try again.'}
            result=deepcopy(self.data);result.pop('_retrieved',None);result.update(cached=True,stale=now-self.data['_retrieved']>self.REFRESH_SECONDS+30,loading=self.refreshing,waiting=waiting,warning=progress)
            if result.get('price') and now-result['price']['unix_time']>1800:result.update(price=None,price_error='BTC/AUD quote is stale.')
            return result
    def track(self,txid):
        if not re.fullmatch('[0-9a-fA-F]{64}',str(txid)):raise ValueError('Enter a 64-character Bitcoin transaction ID.')
        if self.busy():raise FeeUnavailable('Tracking is waiting for the node to finish higher-priority work. Try again when that process completes.')
        chain=NodeChain(RegistryRPC(self.registry,priority=100),{'wallets':[]});chain.raw(txid.lower());meta=chain.metadata[txid.lower()]
        if meta.get('blockhash') and int(meta.get('confirmations',1))>0:
            header=chain.header(blockhash=meta['blockhash'])
            return {'txid':txid.lower(),'status':'confirmed','block_height':header['height'],'block_hash':header['hash']}
        chain.call('getmempoolentry',[txid.lower()])
        return {'txid':txid.lower(),'status':'mempool'}
    def close(self):self.closed=True
