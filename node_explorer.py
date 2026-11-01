"""Compact Core-only explorer. Missing/pruned evidence is never a zero balance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from decimal import Decimal
import re,threading,time
from descriptor_store import DescriptorStore
from balance_evidence import history
from storage import data_directory
from node_chain import NodeChain,RegistryRPC,NodeDataUnavailable,address
from vendor.embit.script import address_to_scriptpubkey
from address_book import valid_address

class InteractiveRPC(RegistryRPC):
    def __init__(self,registry):super().__init__(registry,priority=0);self.deadline=time.monotonic()+35
    def call(self,node,method,params=None,wallet=None):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise NodeDataUnavailable('The interactive lookup timed out. Try another connected node or load the required wallet history.')
        result=self.registry.rpc.call(node,method,params or [],wallet,timeout_seconds=min(15,remaining),consume=True,priority=0)
        if result['state']!='succeeded':raise NodeDataUnavailable(str((result.get('error') or {}).get('message',result['state'])))
        return result['result']

class NodeExplorer:
    def __init__(self,registry,config):self.registry=registry;self.config=config;self.lock=threading.Lock();self.cursor=0;self.slots=threading.BoundedSemaphore(4)
    def snapshot(self,catalog):
        fp=DescriptorStore.fingerprint(catalog);p=data_directory(self.config)/'descriptor-history'/(fp+'.json')
        try:s=p.stat();return history(str(self.config),fp,s.st_mtime_ns,s.st_size)
        except OSError:return {}
    def search(self,query):
        if not isinstance(query,str) or not 1<=len(query.strip())<=180:raise ValueError('Enter a Bitcoin TXID, address or TXID:output_index.')
        query=query.strip();match=re.fullmatch(r'([0-9a-fA-F]{64})(?::(\d{1,10}))?',query)
        if not match and not valid_address(query):raise ValueError('Enter a valid Bitcoin mainnet address, TXID or TXID:output_index.')
        if not self.slots.acquire(blocking=False):raise ValueError('Other node searches are in progress. Try again shortly.')
        try:
            catalog=DescriptorStore(self.config).read();chain=NodeChain(InteractiveRPC(self.registry),catalog)
            with self.lock:chain.cursor=self.cursor;self.cursor+=1
            chain.nodes()
            if match:return self.transaction(chain,match[1].lower(),int(match[2]) if match[2] is not None else None,query)
            return self.address(chain,query,self.snapshot(catalog),catalog)
        finally:self.slots.release()
    def transaction(self,chain,txid,index,query):
        try:tx=chain.transaction(txid)
        except (NodeDataUnavailable,ValueError,OSError) as exc:return {'kind':'outpoint' if index is not None else 'transaction','query':query,'available':False,'message':str(exc),'source':'Connected Bitcoin Core nodes'}
        if len(tx.vin)+len(tx.vout)>20000:raise ValueError('This transaction is too large for the compact explorer.')
        if index is not None and index>=len(tx.vout):raise ValueError('That output index does not exist in this transaction.')
        meta=chain.metadata.get(txid,{});status={'confirmed':bool(meta.get('blockhash')) and int(meta.get('confirmations',1))>0,'confirmations':max(0,int(meta.get('confirmations',0)))}
        if status['confirmed']:
            try:h=chain.header(blockhash=meta['blockhash']);status.update(block_height=h['height'],block_hash=h['hash'],block_time=h['time'])
            except (NodeDataUnavailable,ValueError):status.update(block_hash=meta['blockhash'],block_height=meta.get('blockheight'),block_time=meta.get('blocktime'))
        decoded=meta.get('decoded',meta);provided=decoded.get('vin',[]) if isinstance(decoded,dict) else [];inputs=[];missing=[]
        for n,v in enumerate(tx.vin):
            if v.txid==b'\0'*32:inputs.append({'is_coinbase':True});continue
            row={'txid':v.txid.hex(),'vout':v.vout};p=provided[n].get('prevout') if n<len(provided) else None
            if p and p.get('value') is not None and p.get('scriptPubKey',{}).get('hex'):
                from node_chain import satoshis
                script=p['scriptPubKey']['hex'];row['prevout']={'value':satoshis(p['value']),'scriptpubkey':script,'scriptpubkey_address':address(script)}
            else:missing.append(n)
            inputs.append(row)
        def parent(n):
            try:
                v=tx.vin[n];out=chain.transaction(v.txid.hex()).vout[v.vout];return n,{'value':out.value,'scriptpubkey':out.script_pubkey.data.hex(),'scriptpubkey_address':address(out.script_pubkey.data.hex())}
            except (ValueError,OSError,IndexError):return n,None
        with ThreadPoolExecutor(max_workers=4) as pool:
            for n,p in pool.map(parent,missing[:8]):
                if p:inputs[n]['prevout']=p
        outputs=[]
        for n,out in enumerate(tx.vout):
            outputs.append({'value':out.value,'scriptpubkey':out.script_pubkey.data.hex(),'scriptpubkey_address':address(out.script_pubkey.data.hex()),'scriptpubkey_type':out.script_pubkey.script_type() or 'nonstandard','index':n})
        coinbase=any(r.get('is_coinbase') for r in inputs);known=all('prevout' in r or r.get('is_coinbase') for r in inputs)
        fee=sum(r['prevout']['value'] for r in inputs if r.get('prevout'))-sum(o['value'] for o in outputs) if known and not coinbase else None
        if fee is not None and fee<0:raise ValueError('Node transaction inputs and outputs do not reconcile.')
        stripped=type(tx)(version=tx.version,vin=[type(v)(txid=v.txid,vout=v.vout,script_sig=v.script_sig,sequence=v.sequence) for v in tx.vin],vout=tx.vout,locktime=tx.locktime)
        size=len(tx.serialize());weight=len(stripped.serialize())*3+size;vsize=(weight+3)//4
        selected=None
        if index is not None:
            selected=dict(outputs[index],outpoint=txid+':'+str(index),spend_status='Unknown')
            try:unspent=chain.call('gettxout',[txid,index,True]);selected['spend_status']='Unspent' if unspent is not None else 'Spent or unavailable (Core does not return an arbitrary spending-transaction index)'
            except (ValueError,OSError) as exc:selected['spend_note']=str(exc)
        return {'kind':'outpoint' if index is not None else 'transaction','query':query,'available':True,'transaction':{'txid':txid,'version':tx.version,'locktime':tx.locktime,'size':size,'weight':weight,'vsize':vsize,'fee':fee,'fee_rate':str(Decimal(fee)/vsize) if fee is not None else None,'status':status,'vin':inputs,'vout':outputs},'selected_output':selected,'coverage':None if known else 'Some previous outputs are unavailable from the connected nodes. Fee is not inferred. Only eight missing parent transactions are requested per search.','source':'Connected Bitcoin Core nodes'}
    def address(self,chain,addr,snapshot,catalog):
        verified=chain.call('validateaddress',[addr])
        if not verified.get('isvalid'):raise ValueError('The connected node does not recognise this Bitcoin address.')
        script=address_to_scriptpubkey(addr).data.hex();txs=snapshot.get('transactions',{});found=[];received=0;sent=0;unspent=0
        spends={(i['txid'],i['vout']) for t in txs.values() for i in t.get('inputs',[]) if i.get('txid')}
        for ident,t in txs.items():
            incoming=sum(o['value'] for o in t.get('outputs',[]) if o.get('script')==script);outgoing=sum(i['value'] or 0 for i in t.get('inputs',[]) if i.get('script')==script)
            if not incoming and not outgoing:continue
            received+=incoming;sent+=outgoing
            unspent+=sum(o['value'] for n,o in enumerate(t.get('outputs',[])) if o.get('script')==script and (ident,n) not in spends)
            found.append({'txid':ident,'block_height':t.get('height'),'utc':t.get('utc'),'received_sats':incoming,'sent_sats':outgoing,'net_sats':incoming-outgoing})
        found.sort(key=lambda t:(t.get('block_height') or 0,t['txid']),reverse=True)
        names=[w['name'] for w in catalog.get('wallets',[]) if any(r.get('address')==addr and r.get('category') in ('receive','generate','immature') for r in w.get('node_history',[]))]
        # A saved snapshot can predate a newly connected node. Ask every
        # connected node, including wallets not yet in the discovery catalog.
        checks=[];live=[]
        def inspect(node):
            errors=[];matches=[];node_id=node['id'];label=node.get('hostname',node_id)
            names={s['wallet_name'] for w in catalog.get('wallets',[]) for s in w.get('node_sources',[]) if s['node_id']==node_id}
            try:names.update(chain.rpc.call(node_id,'listwallets',[]))
            except (ValueError,OSError) as exc:errors.append(str(exc))
            for wallet in sorted(names):
                try:
                    info=chain.rpc.call(node_id,'getaddressinfo',[addr],wallet)
                    if not (info.get('ismine') or info.get('iswatchonly')):continue
                    coins=chain.rpc.call(node_id,'listunspent',[0,9999999,[addr],True],wallet)
                    from node_chain import satoshis
                    outputs={c['txid']+':'+str(c['vout']):satoshis(c['amount']) for c in coins if c.get('address')==addr}
                    matches.append({'wallet':wallet,'outputs':outputs})
                except (ValueError,OSError) as exc:errors.append(wallet+': '+str(exc))
            return {'node':label,'complete':not errors,'errors':errors},matches
        with ThreadPoolExecutor(max_workers=4) as pool:
            for check,matches in pool.map(inspect,chain.nodes()):checks.append(check);live.extend(matches)
        live_outputs={key:value for item in live for key,value in item['outputs'].items()}
        names=list(dict.fromkeys(names+[item['wallet'] for item in live]))
        if live:unspent=sum(live_outputs.values())
        return {'kind':'address','query':addr,'available':True,'address':addr,'scriptpubkey':script,'wallets':list(dict.fromkeys(names)),'transactions':found[:500],'total_known_transactions':len(found),'observed_received_sats':received if found else None,'observed_sent_sats':sent if found else None,'known_unspent_sats':unspent if found or live else None,'node_checks':checks,'live_unspent':bool(live),'source':'Connected Bitcoin Core nodes and their saved wallet history','snapshot_utc':snapshot.get('scanned_utc'),'coverage':'Transaction history is limited to the loaded Core wallet snapshot, not a complete arbitrary-address index. Every connected node was checked for this address in its loaded wallets; current known unspent outputs are shown when a wallet recognises it. '+('No matching transaction history is available; received/sent totals are unknown, not zero.' if not found else 'Activity outside the loaded wallets may be missing.')}
