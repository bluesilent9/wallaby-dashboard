"""Discover Core-generated address ranges and reconstruct output-level wallet ledgers."""
import hashlib,json,os,socket,ssl,sqlite3,time
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from vendor.embit.descriptor import Descriptor
from vendor.embit.networks import NETWORKS
from vendor.embit.transaction import Transaction
from watch import ChainClient
from descriptor_policy import parse_policy,output_scripts,needs_history

SAT=Decimal('0.00000001');LOOKAHEAD=20;MAX_SCAN=100000

def address_for(script):
    try:return script.address(NETWORKS['main'])
    except ValueError:return None

def script_hash(raw):return hashlib.sha256(raw).digest()[::-1].hex()

class IndexerDisconnected(ConnectionError):
    pass

class Electrum:
    def __init__(self,host,port=50002):
        self.socket=ssl.create_default_context().wrap_socket(socket.create_connection((host,port),timeout=15),server_hostname=host);self.socket.settimeout(30);self.file=self.socket.makefile('rb');self.counter=0
        try:self.call('server.version',['Wallaby Bitcoin Dashboard V1 Build 4','1.4'])
        except BaseException:
            self.close();raise
    def close(self):
        try:self.file.close()
        finally:self.socket.close()
    def batch(self,method,args):
        result=[]
        for offset in range(0,len(args),20):
            requests=[]
            for params in args[offset:offset+20]:
                self.counter+=1;requests.append({'jsonrpc':'2.0','id':self.counter,'method':method,'params':params})
            self.socket.sendall((json.dumps(requests)+'\n').encode());waiting={r['id'] for r in requests};answers={}
            while waiting:
                line=self.file.readline(16*1024*1024)
                if not line:raise IndexerDisconnected('Blockchain indexer closed the connection.')
                if len(line)>=16*1024*1024:raise ValueError('Blockchain indexer response exceeds the size limit.')
                reply=json.loads(line)
                for item in reply if isinstance(reply,list) else [reply]:
                    if item.get('id') not in waiting:continue
                    if item.get('error'):raise ValueError('Blockchain indexer could not answer a history request.')
                    ident=item['id'];answers[ident]=item.get('result');waiting.remove(ident)
            result.extend(answers[r['id']] for r in requests)
        return result
    def call(self,method,params):return self.batch(method,[params])[0]

class HistoryClient:
    """Batch script hashes through Electrum; HTTPS Esplora is an automatic fallback."""
    def __init__(self,core,cache):
        self.core=core;self.chain=ChainClient(core);self.electrum=None;self.source='HTTPS Esplora';self.headers={};self.txs={}
        self.db=sqlite3.connect(cache);self.db.execute('CREATE TABLE IF NOT EXISTS raw_transactions(txid TEXT PRIMARY KEY, raw TEXT NOT NULL)')
        override=os.environ.get('WALLABY_ELECTRUM','')
        self.providers=[override] if override else ['electrum.blockstream.info:50002','electrum.emzy.de:50002']
        self.provider_index=0;self.tls_disabled=False
        if not self._connect(0):
            self.tls_disabled=True
            print('TLS blockchain indexers unavailable; using the HTTPS blockchain explorer.')
    def _disconnect(self):
        connection=self.electrum;self.electrum=None
        if connection:
            try:connection.close()
            except OSError:pass
    def _connect(self,first):
        for offset in range(len(self.providers)):
            idx=(first+offset)%len(self.providers);endpoint=self.providers[idx]
            try:
                host,port=endpoint.rsplit(':',1);connection=Electrum(host,int(port))
                self.electrum=connection;self.provider_index=idx;self.source='TLS Electrum '+host;return True
            except (OSError,ValueError,KeyError):continue
        return False
    def _request(self,method,args,batch=False):
        if self.tls_disabled:return False,None
        for attempt in range(3):
            try:
                if not self.electrum and not self._connect(self.provider_index):raise IndexerDisconnected('No TLS indexer connection.')
                result=self.electrum.batch(method,args) if batch else self.electrum.call(method,args)
                return True,result
            except (OSError,EOFError,json.JSONDecodeError) as exc:
                self._disconnect()
                if attempt<2:
                    if attempt:self.provider_index=(self.provider_index+1)%len(self.providers)
                    print(f'Blockchain connection interrupted; reconnecting and retrying this query ({attempt+2}/3).')
        self.tls_disabled=True;self.source='HTTPS Esplora (TLS retries exhausted)'
        print('TLS blockchain queries could not be completed after 3 attempts; trying the HTTPS explorer.')
        return False,None
    def close(self):
        try:self._disconnect()
        finally:self.db.close()
    def _http(self,url,text=False):
        import urllib.request,urllib.error
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url,timeout=30) as response:return response.read().decode() if text else json.load(response)
            except (OSError,EOFError,json.JSONDecodeError) as exc:
                if isinstance(exc,urllib.error.HTTPError) and exc.code not in (408,429) and exc.code<500:raise
                if attempt==2:raise ValueError('Blockchain explorer request failed after 3 attempts. No partial history was accepted; retry Ownership when the connection is available.') from exc
                print(f'HTTPS blockchain lookup interrupted; retrying this query ({attempt+2}/3).')
    def histories(self,items):
        if not items:return []
        ok,result=self._request('blockchain.scripthash.get_history',[[script_hash(bytes.fromhex(i['script']))] for i in items],batch=True)
        if ok:
            if any(not isinstance(r,list) for r in result):raise ValueError('Invalid history response.')
            return result
        import urllib.request
        result=[]
        for item in items:
            address=item['address'];history=[];last=''
            if not address:raise ValueError('This legacy public-key script requires the TLS Electrum indexer; an address-only explorer cannot scan it.')
            while True:
                url='https://blockstream.info/api/address/'+address+'/txs/chain'+('/'+last if last else '')
                page=self._http(url)
                if not isinstance(page,list):raise ValueError('Invalid explorer history response.')
                history.extend({'tx_hash':r['txid'],'height':r['status']['block_height']} for r in page)
                if len(page)<25:break
                if page[-1]['txid']==last:raise ValueError('Explorer history pagination did not advance.')
                last=page[-1]['txid']
            pending=self._http('https://blockstream.info/api/address/'+address+'/txs/mempool')
            if len(pending)>=50:raise ValueError('Explorer mempool history is truncated; retry using a TLS Electrum indexer.')
            history.extend({'tx_hash':r['txid'],'height':0} for r in pending);result.append(history)
        return result
    def tx(self,ident):
        if ident in self.txs:return self.txs[ident]
        row=self.db.execute('SELECT raw FROM raw_transactions WHERE txid=?',(ident,)).fetchone()
        if row:raw=row[0]
        else:
            ok,raw=self._request('blockchain.transaction.get',[ident,False])
            if not ok:raw=self._http('https://blockstream.info/api/tx/'+ident+'/hex',text=True)
        tx=Transaction.parse(bytes.fromhex(raw))
        if tx.txid().hex()!=ident:raise ValueError('Blockchain transaction ID does not match its raw transaction.')
        if not row:self.db.execute('INSERT OR REPLACE INTO raw_transactions VALUES(?,?)',(ident,raw));self.db.commit()
        self.txs[ident]=tx;return tx
    def block(self,height):
        if height not in self.headers:
            ok,result=self._request('blockchain.block.header',[height])
            if ok:raw=bytes.fromhex(result)
            else:
                block_hash=self._http('https://blockstream.info/api/block-height/'+str(height),text=True)
                raw=bytes.fromhex(self._http('https://blockstream.info/api/block/'+block_hash+'/header',text=True))
                if hashlib.sha256(hashlib.sha256(raw).digest()).digest()[::-1].hex()!=block_hash:raise ValueError('Invalid block header.')
            if len(raw)!=80:raise ValueError('Invalid Bitcoin block header.')
            self.headers[height]={'hash':hashlib.sha256(hashlib.sha256(raw).digest()).digest()[::-1].hex(),'timestamp':int.from_bytes(raw[68:72],'little')}
        return self.headers[height]

def scan(catalog,client,progress=print):
    scripts={};wallets=[];histories={};coverage=[];queried={}
    def query(items):
        missing={item['script']:item for item in items if item['script'] not in queried}
        batch=list(missing.values())
        if batch:
            responses=client.histories(batch)
            if not isinstance(responses,list) or len(responses)!=len(batch) or any(not isinstance(r,list) for r in responses):raise ValueError('Incomplete blockchain history response. No partial history was accepted.')
            queried.update((item['script'],value) for item,value in zip(batch,responses))
        return [queried[item['script']] for item in items]
    for wallet in catalog['wallets']:
        uid=wallet['id'];wallets.append({'id':uid,'name':wallet['name'],'records':[],'pending_btc':'0','receiving_address':None,'has_vault':bool(wallet.get('has_vault')),'vault_evidence':wallet.get('vault_evidence',[])})
        receiving=[]
        if needs_history(wallet):
            supplement=wallet.get('public_history')
            if not supplement:raise ValueError('Additional public history is required for '+wallet['name']+'. Run Load Node Wallets in Ownership to collect the public history from Core. No partial wallet ledger was accepted.')
            batch=[]
            for record in supplement['outputs']:
                tx=client.tx(record['txid']);n=record['vout']
                if n>=len(tx.vout):raise ValueError('Core public-history output is outside its transaction.')
                spk=tx.vout[n].script_pubkey;raw=spk.data.hex();existing=scripts.get(raw)
                if existing and existing['wallet_id']!=uid:raise ValueError('Overlapping public history belongs to two wallets.')
                item={'wallet_id':uid,'script':raw,'address':address_for(spk),'index':-1,'internal':None,'descriptor':0,'branch':0}
                if raw not in scripts:batch.append(item)
                scripts[raw]=item
            found=query(batch)
            if len(found)!=len(batch):raise ValueError('Incomplete legacy public-history lookup.')
            for history in found:
                for entry in history:
                    ident=entry.get('tx_hash');height=entry.get('height')
                    if not isinstance(ident,str) or len(ident)!=64 or any(c not in '0123456789abcdef' for c in ident) or type(height) is not int:raise ValueError('Invalid legacy blockchain history entry.')
                    if ident in histories and histories[ident]!=height:raise ValueError('History changed during discovery. Run Ownership again.')
                    histories[ident]=height
            coverage.append({'wallet_id':uid,'descriptor':0,'branch':0,'first':None,'last':None,'last_used':None,'exported_range':None,'lookahead':0,'public_history_utc':supplement['exported_utc'],'scripts':len(batch),'basis':'Core listsinceblock public received outputs, including change; only exported legacy scripts can be monitored.'})
        for row_number,row in enumerate(wallet['raw']['descriptors'],1):
            descriptor,combo,hardened=parse_policy(row['desc'].split('#')[0])
            if hardened:continue
            for branch_no in range(descriptor.num_branches):
                branch=descriptor.branch(branch_no);ranged=branch.is_wildcard;start=row.get('range',[0,0])[0] if ranged else 0
                end=row['range'][1] if ranged else 0;cursor=row.get('next_index') if ranged else None
                # Core's range includes its unused keypool. next_index advances past
                # generated addresses AND observed outputs, including restored keys.
                # Always scan that entire known prefix; a gap cannot truncate it.
                minimum=max(start-1,cursor-1) if cursor is not None else end
                last_used=start-1;index=start;scanned=0
                target=minimum+LOOKAHEAD if ranged else 0
                basis='Core next_index plus adaptive unused gap' if cursor is not None else 'Full exported range (no Core cursor) plus adaptive unused gap'
                if ranged:progress(f"Scanning {wallet['name']} · descriptor {row_number}, branch {branch_no+1} · {max(0,minimum-start+1):,} known generated indexes + {LOOKAHEAD} unused lookahead")
                while index<=target:
                    if scanned>=MAX_SCAN:raise ValueError('Address discovery reached its scan budget. No partial history was accepted; refresh the descriptor export or reduce its explicit range.')
                    stop=min(target+1,index+20,start+MAX_SCAN);batch=[]
                    for n in range(index,stop):
                        d=branch.derive(n) if ranged else branch
                        for spk in output_scripts(d,combo):
                            raw=spk.data;script=raw.hex();address=address_for(spk)
                            existing=scripts.get(script)
                            if existing and existing['wallet_id']!=uid:raise ValueError('Two wallets contain the same script. Overlapping descriptors must be assigned to a single wallet before history can be calculated.')
                            item={'wallet_id':uid,'script':script,'address':address,'index':n,'internal':row.get('internal'),'descriptor':row_number,'branch':branch_no}
                            scripts[script]=item;batch.append(item)
                            if row.get('internal') is False and row.get('active') is True and n==cursor and address:receiving.append(address)
                    found=query(batch)
                    if len(found)!=len(batch):raise ValueError('Incomplete blockchain history response.')
                    for item,history in zip(batch,found):
                        if history:last_used=max(last_used,item['index'])
                        for entry in history:
                            ident=entry.get('tx_hash');height=entry.get('height')
                            if not isinstance(ident,str) or len(ident)!=64 or any(c not in '0123456789abcdef' for c in ident) or type(height) is not int:raise ValueError('Invalid blockchain history entry.')
                            old=histories.get(ident)
                            if old is not None and old!=height:raise ValueError('History changed during discovery. Run Ownership again for a consistent chain snapshot.')
                            histories[ident]=height
                    scanned+=stop-index;index=stop
                    if ranged:target=max(minimum,last_used)+LOOKAHEAD
                    progress(f"Scanning {wallet['name']} · descriptor {row_number}, branch {branch_no+1} · {scanned:,} addresses checked")
                coverage.append({'wallet_id':uid,'descriptor':row_number,'branch':branch_no,'first':start,'last':index-1,'last_used':last_used if last_used>=start else None,'exported_range':row.get('range'),'lookahead':LOOKAHEAD if ranged else 0,'core_next_index':cursor,'scan_basis':basis})
        # Prefer the active native SegWit/Taproot receiving descriptor in Core's export order.
        wallets[-1]['receiving_address']=receiving[-1] if receiving else None
    by_id={w['id']:w for w in wallets};known_tx={};pending={w['id']:0 for w in wallets};events=[]
    progress(f'Reading {len(histories):,} discovered transactions and their inputs')
    # Dependency order is needed when a child confirms in its parent's block.
    ordered=[];visiting=set();visited=set()
    def visit(ident):
        if ident in visited:return
        if ident in visiting:raise ValueError('Cyclic transaction ancestry returned by indexer.')
        visiting.add(ident)
        for vin in client.tx(ident).vin:
            parent=vin.txid.hex()
            if parent in histories:visit(parent)
        visiting.remove(ident);visited.add(ident);ordered.append(ident)
    for ident in sorted(histories,key=lambda ident:(histories[ident] if histories[ident]>0 else 2**32,ident)):visit(ident)
    tx_order={ident:n for n,ident in enumerate(ordered)}
    for ident in ordered:
        height=histories[ident]
        tx=client.tx(ident);inputs=[];coinbase=False
        for vin in tx.vin:
            if vin.txid==b'\x00'*32:coinbase=True;continue
            parent=client.tx(vin.txid.hex())
            if vin.vout>=len(parent.vout):raise ValueError('Invalid previous transaction output.')
            prev=parent.vout[vin.vout];inputs.append({'txid':vin.txid.hex(),'vout':vin.vout,'value':prev.value,'script':prev.script_pubkey.data.hex()})
        outputs=[{'value':o.value,'script':o.script_pubkey.data.hex(),'address':address_for(o.script_pubkey)} for o in tx.vout]
        fee=sum(i['value'] for i in inputs)-sum(o['value'] for o in outputs) if not coinbase else 0
        if fee<0:raise ValueError('Transaction spends more than its inputs.')
        input_owners={scripts[i['script']]['wallet_id'] for i in inputs if i['script'] in scripts}
        external_inputs=any(i['script'] not in scripts for i in inputs)
        joint=bool(input_owners and (len(input_owners)>1 or external_inputs))
        own_sender=next(iter(input_owners),None) if not joint else None
        if height<=0:
            for uid in by_id:pending[uid]+=sum(o['value'] for o in outputs if scripts.get(o['script'],{}).get('wallet_id')==uid)-sum(i['value'] for i in inputs if scripts.get(i['script'],{}).get('wallet_id')==uid)
            continue
        block=client.block(height);when=datetime.fromtimestamp(block['timestamp'],timezone.utc).isoformat().replace('+00:00','Z')
        info={'txid':ident,'fee_sats':fee,'height':height,'block_hash':block['hash'],'utc':when,'inputs':inputs,'outputs':outputs};known_tx[ident]=info
        info['joint']=joint
        if joint:
            for uid in sorted(by_id):
                delta=sum(o['value'] for o in outputs if scripts.get(o['script'],{}).get('wallet_id')==uid)-sum(i['value'] for i in inputs if scripts.get(i['script'],{}).get('wallet_id')==uid)
                if delta:events.append((uid,{'txid':ident,'utc':when,'output_index':-1,'sats':delta,'fee_sats':0,'address':'','fee_only':False,'height':height,'joint':True}))
            continue

        if own_sender:
            sent=[(n,o) for n,o in enumerate(outputs) if o['value']>0 and scripts.get(o['script'],{}).get('wallet_id')!=own_sender]
            if sent:
                for n,o in sent:events.append((own_sender,{'txid':ident,'utc':when,'output_index':n,'sats':-o['value'],'fee_sats':fee,'address':o['address'] or '','fee_only':False,'height':height}))
            elif fee:events.append((own_sender,{'txid':ident,'utc':when,'output_index':-1,'sats':-fee,'fee_sats':fee,'address':'','fee_only':True,'height':height}))
        for n,o in enumerate(outputs):
            receiver=scripts.get(o['script'],{}).get('wallet_id')
            if receiver and receiver!=own_sender:events.append((receiver,{'txid':ident,'utc':when,'output_index':n,'sats':o['value'],'fee_sats':0,'address':o['address'] or '','fee_only':False,'height':height}))
    for uid,row in sorted(events,key=lambda pair:(tx_order[pair[1]['txid']],pair[1]['output_index'])):by_id[uid]['records'].append(row)
    for w in wallets:
        w['pending_btc']=str(Decimal(pending[w['id']])*SAT)
        receipts=[r for r in w['records'] if r['sats']>0 and r['address'] and any(i['address']==r['address'] and i['internal'] is False for i in scripts.values())]
        if receipts:w['receiving_address']=receipts[-1]['address']
    return {'schema':132,'catalog_fingerprint':hashlib.sha256(json.dumps(catalog['wallets'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),'scanned_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'source':client.source,'wallets':wallets,'wallet_outputs':{ident+':'+str(n):scripts[o['script']]['wallet_id'] for ident,tx in known_tx.items() for n,o in enumerate(tx['outputs']) if o['script'] in scripts},'coverage':coverage,'transactions':known_tx,'timestamp_basis':'Confirmed Bitcoin block timestamp UTC; pending transactions are excluded from CGT.'}

def wallet_rows(core,snapshot):
    out={};core.DESCRIPTOR_SOURCE_NAMES={w['id']:w['name'] for w in snapshot['wallets']};core.DESCRIPTOR_WALLETS={w['name']:w['id'] for w in snapshot['wallets']};core.DESCRIPTOR_SNAPSHOT=snapshot
    for wallet in snapshot['wallets']:
        rows=[];balance=Decimal(0);groups={}
        for r in wallet['records']:groups.setdefault(r['txid'],[]).append(r)
        for group in groups.values():
            fee=max(r['fee_sats'] for r in group);delta=sum(r['sats'] for r in group)
            if not any(r['fee_only'] for r in group):delta-=fee
            balance+=Decimal(delta)*SAT
            for r in group:
                row=core.WalletTx(wallet['name'],len(rows)+1,core.tax_date(datetime.fromisoformat(r['utc'].replace('Z','+00:00'))),Decimal(r['sats'])*SAT,Decimal(r['fee_sats'])*SAT,balance,'Confirmed blockchain transaction',address=r['address'],txid=r['txid'],fee_known=True,onchain_output_amount=Decimal(0) if r['fee_only'] else None,onchain_fee_source='Decoded Bitcoin transaction',descriptor_id=wallet['id'],output_index=r['output_index'],accounting_joint=r.get('joint',False))
                rows.append(row)
        if balance<0:raise ValueError('Descriptor history has an unreconciled negative balance. No partial ledger was accepted.')
        out[wallet['name']]=rows
    return out

def install_evidence(core,snapshot):
    """Use this run's decoded transactions for fee/output/source lookups."""
    original=core.lookup_transaction_online
    from vendor.embit.script import Script
    def lookup(ident):
        item=snapshot['transactions'].get(ident)
        if not item:return original(ident)
        addresses=[]
        for inp in item['inputs']:
            address=address_for(Script(bytes.fromhex(inp['script'])))
            if address and address not in addresses:addresses.append(address)
        return core.ChainTxInfo(ident,Decimal(item['fee_sats'])*SAT,[core.ChainTxOutput(n,Decimal(o['value'])*SAT,o.get('address') or '') for n,o in enumerate(item['outputs'])],addresses,'Decoded descriptor history transaction')
    core.lookup_transaction_online=lookup

def annotate_balances(report,snapshot):
    price=Decimal(report.get('price_aud_per_btc') or '0');by_name={w['name']:w for w in snapshot['wallets']}
    for entry in report['wallets']:
        wallet=by_name.get(entry['name'])
        if not wallet:continue
        confirmed=Decimal(entry['btc'] or '0');pending=Decimal(wallet['pending_btc']);total=confirmed+pending
        entry.update(confirmed_btc=str(confirmed),unconfirmed_btc=str(pending),btc=str(total),balance_mode='Descriptor blockchain history',watch_checked_utc=snapshot['scanned_utc'],watch_sources=[snapshot['source']])
        if pending:
            entry.update(cost_basis_aud=None,unrealised_gain_aud=None,state='unavailable',cost_basis_note='Unconfirmed movements are excluded from CGT; acquisition costs for the pending balance are not final.')
        if price:
            entry['btc_value_aud']=entry['value_aud']=str(total*price)
