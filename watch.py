"""Corroborate ODS UTXOs, then read fresh balances for those addresses only."""
from datetime import datetime,timezone
from decimal import Decimal
import json,re,urllib.request,urllib.parse

SAT=Decimal('0.00000001')


def sats(value):
    n=Decimal(value)*100000000
    if n!=n.to_integral_value():raise ValueError('Blockchain amounts must be whole satoshis')
    return int(n)


class ChainClient:
    def __init__(self,core):self.core=core;self.transactions={};self.blocks={};self.wallet_evidence={}
    def immutable(self,path,binary=False):
        errors=[]
        for base in ('https://mempool.space/api/','https://blockstream.info/api/'):
            try:
                data=self.core.cached_http(base+path,{'User-Agent':'Wallaby-Bitcoin-Dashboard/132'},timeout=20)
                if binary:return data
                text=data.decode().strip()
                return json.loads(text) if text[:1] in '[{' else text
            except (ValueError,OSError) as exc:errors.append(str(exc))
        raise RuntimeError('Blockchain evidence lookup failed: '+'; '.join(errors))
    def tx(self,txid):
        txid=txid.lower()
        if not re.fullmatch('[0-9a-fA-F]{64}',txid):raise ValueError('Invalid transaction ID')
        if txid not in self.transactions:
            tx=self.immutable('tx/'+txid)
            if not isinstance(tx,dict) or tx.get('txid')!=txid:raise ValueError('Unexpected transaction returned by explorer')
            if not tx.get('status',{}).get('confirmed'):
                # An older cached mempool response must not hide a confirmation.
                errors=[]
                for base in ('https://mempool.space/api/','https://blockstream.info/api/'):
                    try:
                        request=urllib.request.Request(base+'tx/'+txid,headers={'Cache-Control':'no-cache','Accept':'application/json'})
                        with urllib.request.urlopen(request,timeout=20) as response:tx=json.load(response)
                        if not isinstance(tx,dict) or tx.get('txid')!=txid:raise ValueError('Unexpected transaction returned by explorer')
                        break
                    except (ValueError,OSError) as exc:errors.append(str(exc))
                else:raise RuntimeError('Fresh transaction lookup failed: '+'; '.join(errors))
            self.transactions[txid]=tx
        return self.transactions[txid]
    def outspend(self,txid,index):
        """Mutable spend status is fresh each run, shared across all consumers."""
        key=(txid,index)
        if not hasattr(self,'outspends'):self.outspends={}
        if key not in self.outspends:
            errors=[]
            for base in ('https://mempool.space/api/','https://blockstream.info/api/'):
                try:
                    req=urllib.request.Request(base+f'tx/{txid}/outspend/{index}',headers={'User-Agent':'Wallaby-Bitcoin-Dashboard/151','Cache-Control':'no-cache','Accept':'application/json'})
                    with urllib.request.urlopen(req,timeout=20) as response:data=json.load(response)
                    if not isinstance(data,dict) or type(data.get('spent')) is not bool:raise ValueError('Invalid output spend response')
                    if data['spent'] and (not re.fullmatch('[0-9a-f]{64}',data.get('txid','')) or type(data.get('vin')) is not int or data['vin']<0):raise ValueError('Invalid spending transaction reference')
                    self.outspends[key]=data;break
                except (ValueError,OSError) as exc:errors.append(str(exc))
            else:raise RuntimeError('Output spend lookup failed: '+'; '.join(errors))
        return self.outspends[key]
    def live_utxos(self,address):
        errors=[]
        for base in ('https://mempool.space/api/','https://blockstream.info/api/'):
            url=base+'address/'+urllib.parse.quote(address,safe='')+'/utxo'
            try:
                # Address state changes. Never use the permanent historical HTTP cache.
                req=urllib.request.Request(url,headers={'User-Agent':'Wallaby-Bitcoin-Dashboard/132','Cache-Control':'no-cache','Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=20) as response:data=json.load(response)
                if not isinstance(data,list):raise ValueError('Invalid UTXO response')
                for output in data:
                    if not re.fullmatch('[0-9a-fA-F]{64}',output.get('txid','')) or not isinstance(output.get('vout'),int) or not isinstance(output.get('value'),int) or output['value']<0:raise ValueError('Invalid UTXO in address response')
                return data,base+'address/'+address+'/utxo'
            except (ValueError,OSError) as exc:errors.append(str(exc))
        raise RuntimeError('Live address lookup failed: '+'; '.join(errors))


def discover_outputs(core,engine,name,client,evidence=None):
    """Reuse exact ownership and spending evidence produced by the ODS importer."""
    data=getattr(core,'HISTORICAL_OUTPOINTS',{})
    if name not in data.get('unspent',{}):
        raise ValueError('Historical wallet output evidence is missing. Reload the TXID:output_index Wallets ODS in Ownership.')
    if evidence is not None:evidence.update({key:value for key,value in data['evidence'].items() if key[0]==name})
    return dict(data['unspent'][name])


def watch_wallets(core,engine,client=None):
    client=client or ChainClient(core);result={}
    for name,rows in engine.wallets.items():
        if not rows or not all(row.is_historical_ods for row in rows):continue
        last=max(rows,key=lambda row:(core.utc_time(row.dt),row.row_num))
        if last.balance==0:continue
        entry={'checked_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'recorded_btc':str(last.balance),'addresses':[]}
        try:
            recorded=discover_outputs(core,engine,name,client,getattr(client,'wallet_evidence',None))
            addresses=sorted({o['address'] for o in recorded.values()});live={};sources=[]
            for address in addresses:
                outputs,source=client.live_utxos(address);sources.append(source)
                for output in outputs:
                    key=(output['txid'],output['vout'])
                    if key in live:raise ValueError('Explorer returned a duplicate unspent output')
                    live[key]=output
            total=sum(o['value'] for o in live.values());confirmed=sum(o['value'] for o in live.values() if o.get('status',{}).get('confirmed'))
            entry.update(status='live',btc=str(Decimal(total)*SAT),confirmed_btc=str(Decimal(confirmed)*SAT),unconfirmed_btc=str(Decimal(total-confirmed)*SAT),addresses=addresses,sources=sources,
                original_outputs_only=all(key in recorded and o['value']==recorded[key]['value'] for key,o in live.items()),unspent_outputs=len(live),recorded_outputs=len(recorded))
        except (RuntimeError,ValueError,OSError,KeyError) as exc:entry.update(status='unavailable',btc=None,error=str(exc))
        entry['checked_utc']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        result[name]=entry
    return result
