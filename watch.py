"""Corroborate ODS UTXOs, then read fresh balances for those addresses only."""
from datetime import datetime,timezone
from decimal import Decimal


SAT=Decimal('0.00000001')


def sats(value):
    n=Decimal(value)*100000000
    if n!=n.to_integral_value():raise ValueError('Blockchain amounts must be whole satoshis')
    return int(n)


class ChainClient:
    def __init__(self,core):
        self.core=core;self.wallet_evidence={}
    @property
    def chain(self):
        from node_chain import current
        return current()
    def tx(self,txid):return self.chain.tx(txid)
    def outspend(self,txid,index):return self.chain.outspend(txid,index)
    def live_utxos(self,address):return self.chain.live_utxos(address)


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
