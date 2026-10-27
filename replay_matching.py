"""Run-local evidence memoisation; no matching decisions survive a calculation."""
from bisect import bisect_left,bisect_right
from contextlib import contextmanager

@contextmanager
def matching_cache(core,shared=None):
    deposit=core.match_bitaroo_deposit_row;counterparty=core.find_loaded_wallet_counterparty
    deposits={};counterparties={};references=[];report_keys={};wallet_indexes={}
    shared=shared if shared is not None else {};shared_deposits=shared.setdefault('deposits',{});shared_pairs=shared.setdefault('pairs',{})
    def row_key(row):
        return (row.sheet,row.row_num,core.utc_time(row.dt),row.tx_total,row.fee,row.is_historical_ods,row.address,row.txid,row.output_index,row.accounting_flow,row.onchain_output_amount)
    def match_deposit(reports,row):
        key=(id(reports),id(row))
        if key not in deposits:
            references.extend((reports,row))
            if id(reports) not in report_keys:
                rs=reports.btc_rows
                signature=tuple((core.utc_time(r.dt),r.tx_type,r.amount,r.balance,r.asset,r.source_file,r.page) for r in rs)
                report_keys[id(reports)]=(signature,{id(r):i for i,r in enumerate(rs)})
            signature,index=report_keys[id(reports)];stable=(core.MATCH_HOURS,signature,row_key(row))
            if stable not in shared_deposits:
                result=deposit(reports,row)
                shared_deposits[stable]=None if result is None else (result[0],result[1],index[id(result[2])])
            result=shared_deposits[stable]
            deposits[key]=None if result is None else (result[0],result[1],reports.btc_rows[result[2]])
        return deposits[key]
    def match_counterparty(wallets,name,row):
        key=(id(wallets),name,id(row))
        if key not in counterparties:
            references.extend((wallets,row))
            if id(wallets) not in wallet_indexes:
                items=sorted((core.utc_time(r.dt).timestamp(),n,i,row_key(r)) for n,rs in wallets.items() for i,r in enumerate(rs))
                wallet_indexes[id(wallets)]=([item[0] for item in items],items)
            times,items=wallet_indexes[id(wallets)];stamp=core.utc_time(row.dt).timestamp();span=core.MATCH_HOURS*3600+1
            nearby=tuple(items[bisect_left(times,stamp-span):bisect_right(times,stamp+span)])
            stable=(core.MATCH_HOURS,name,row_key(row),nearby)
            if stable not in shared_pairs:shared_pairs[stable]=counterparty(wallets,name,row)
            counterparties[key]=shared_pairs[stable]
        return counterparties[key]
    core.match_bitaroo_deposit_row=match_deposit;core.find_loaded_wallet_counterparty=match_counterparty
    try:yield
    finally:
        core.match_bitaroo_deposit_row=deposit;core.find_loaded_wallet_counterparty=counterparty
