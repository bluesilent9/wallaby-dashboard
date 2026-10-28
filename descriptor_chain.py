"""Reconstruct exact output ledgers from Core wallet history via connected Agents."""
import hashlib,json
from datetime import datetime,timezone
from decimal import Decimal
from vendor.embit.script import Script
from vendor.embit.networks import NETWORKS
from descriptor_policy import AddressPolicy,parse_policy,output_scripts
from node_chain import current,NodeDataUnavailable
from descriptor_store import DescriptorStore
SAT=Decimal('0.00000001')
def address_for(script):
    try:return script.address(NETWORKS['main'])
    except ValueError:return None
def script_hash(raw):return hashlib.sha256(raw).digest()[::-1].hex()
def declared_lost_address(catalog,uid,address):
    return any(w['id']==uid and w.get('address_labels',{}).get(address,{}).get('lost_utc') for w in catalog['wallets'])
class HistoryClient:
    def __init__(self,core,cache=None):self.core=core;self.chain=current();self.source='Connected Bitcoin Core nodes'
    def close(self):pass
    def tx(self,ident):return self.chain.transaction(ident)
    def block(self,height):
        row=self.chain.header(height=height);return {'hash':row['hash'],'timestamp':row['time']}

def scan(catalog,client,progress=print):
    scripts={};wallets=[];histories={};coverage=[]
    chain=client.chain
    for node in chain.nodes():
        info=chain.rpc.call(node['id'],'getblockchaininfo',[])
        if info.get('chain')!='main' or info.get('initialblockdownload'):raise ValueError('Wait for a fully synchronised Bitcoin mainnet node before loading wallet history.')
    for wallet in catalog['wallets']:
        uid=wallet['id'];history=wallet.get('node_history')
        if history is None:raise ValueError('Load Node Wallets again to collect current Core wallet history for '+wallet['name'])
        wallets.append({'id':uid,'name':wallet['name'],'records':[],'pending_btc':'0','receiving_address':wallet.get('receiving_address'),'has_vault':bool(wallet.get('has_vault')),'vault_evidence':wallet.get('vault_evidence',[]),'watch_only':bool(wallet.get('watch_only')),'extinct':bool(wallet.get('extinct')),'address_labels':wallet.get('address_labels',{})})
        # Register fixed watched scripts even when their receipt is absent: this
        # makes missing owned-input history an error rather than an external gift.
        for row in wallet['raw']['descriptors']:
            policy,_,_=parse_policy(row['desc'].split('#')[0])
            if isinstance(policy,AddressPolicy):
                key=policy.script_pubkey().data.hex();previous=scripts.get(key)
                if previous and previous['wallet_id']!=uid:raise ValueError('Two distinct wallets claim the same script.')
                scripts[key]={'wallet_id':uid,'script':key,'address':policy.address,'index':-1,'internal':None,'descriptor':0,'branch':0}
        # Core's wallet ledger includes every known received output, including
        # change. It is authoritative even for hardened legacy policies.
        for row in history:
            ident=row['txid'];tx=chain.tx(ident);status=tx['status'];height=status.get('block_height',0)
            if ident in histories and histories[ident]!=height:raise ValueError('Wallet replicas disagree on confirmation state; refresh node wallets.')
            histories[ident]=height
            if row.get('category') not in ('receive','generate','immature') or Decimal(str(row.get('amount',0)))<=0:continue
            n=row.get('vout')
            if type(n) is not int or not 0<=n<len(tx['vout']):raise ValueError('Core wallet output index is invalid')
            output=tx['vout'][n];script=output['scriptpubkey'];previous=scripts.get(script)
            if previous and previous['wallet_id']!=uid:raise ValueError('Two distinct wallets claim the same script.')
            scripts[script]={'wallet_id':uid,'script':script,'address':output.get('scriptpubkey_address'),'index':-1,'internal':None,'descriptor':0,'branch':0}
        # Derive only Core's next receiving address for the Address Book. No
        # online address probing and no advancement of the Core keypool.
        for number,row in enumerate(wallet['raw']['descriptors'],1):
            d,combo,hardened=parse_policy(row['desc'].split('#')[0])
            if isinstance(d,AddressPolicy) or hardened or combo or not row.get('active') or row.get('internal') is not False:continue
            if d.num_branches!=1:continue
            candidate=d.derive(row.get('next_index',0)) if d.is_wildcard else d
            if not wallets[-1]['receiving_address']:wallets[-1]['receiving_address']=address_for(candidate.script_pubkey())
        coverage.append({'wallet_id':uid,'basis':'Core listsinceblock complete loaded-wallet history','transactions':len({r['txid'] for r in history})})
        progress(wallet['name']+': '+str(len({r['txid'] for r in history}))+' transactions read from Core wallet history; no address-range scan.')
    lost_scripts={key for key,value in scripts.items() if declared_lost_address(catalog,value['wallet_id'],value.get('address'))}
    sending_ids={r['txid'] for w in catalog['wallets'] for r in w['node_history'] if r.get('category')=='send'}
    by_id={w['id']:w for w in wallets};known_tx={};pending={w['id']:0 for w in wallets};events=[]
    progress(f'Reading {len(histories):,} discovered transactions and their inputs')
    # Dependency order is needed when a child confirms in its parent's block.
    ordered=[];visiting=set();visited=set()
    def visit(ident):
        if ident in visited:return
        if ident in visiting:raise ValueError('Cyclic transaction ancestry returned by node.')
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
            evidence=chain.tx(ident)['vin'][len(inputs)].get('prevout')
            if evidence is None:
                # Missing external ancestry cannot affect an incoming output's
                # value; preserve unknown inputs instead of fabricating a fee.
                inputs.append({'txid':vin.txid.hex(),'vout':vin.vout,'value':None,'script':''})
            else:inputs.append({'txid':vin.txid.hex(),'vout':vin.vout,'value':evidence['value'],'script':evidence['scriptpubkey']})
        outputs=[{'value':o.value,'script':o.script_pubkey.data.hex(),'address':address_for(o.script_pubkey)} for o in tx.vout]
        missing=any(i['value'] is None for i in inputs)
        owned_input=any(i['script'] in scripts for i in inputs)
        if missing and (owned_input or ident in sending_ids):raise NodeDataUnavailable('A wallet spend has unavailable input evidence. Load/rescan every funding wallet before tracing '+ident)
        fee=sum(i['value'] for i in inputs)-sum(o['value'] for o in outputs) if not coinbase and not missing else 0
        if fee<0:raise ValueError('Transaction spends more than its inputs.')
        input_owners={scripts[i['script']]['wallet_id'] for i in inputs if i['script'] in scripts}
        if ident in sending_ids and not input_owners:raise NodeDataUnavailable('Core reports a wallet send without its corresponding received input history. Rescan the funding wallet before tracing '+ident)
        external_inputs=any(i['script'] not in scripts for i in inputs)
        joint=bool(input_owners and (len(input_owners)>1 or external_inputs))
        own_sender=next(iter(input_owners),None) if not joint else None
        if height<=0:
            if any(i['script'] in lost_scripts for i in inputs) or any(o['script'] in lost_scripts for o in outputs):raise ValueError('Unconfirmed movement conflicts with a declared lost address. Review the Core LOST label before continuing.')
            for uid in by_id:pending[uid]+=sum(o['value'] for o in outputs if scripts.get(o['script'],{}).get('wallet_id')==uid)-sum(i['value'] for i in inputs if scripts.get(i['script'],{}).get('wallet_id')==uid)
            continue
        block=client.block(height);when=datetime.fromtimestamp(block['timestamp'],timezone.utc).isoformat().replace('+00:00','Z')
        info={'txid':ident,'fee_sats':fee,'fee_known':not missing,'height':height,'block_hash':block['hash'],'utc':when,'inputs':inputs,'outputs':outputs};known_tx[ident]=info
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
    from capital_loss import append_loss_records
    append_loss_records(wallets,known_tx)
    return {'schema':132,'catalog_fingerprint':DescriptorStore.fingerprint(catalog),'scanned_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'source':client.source,'wallets':wallets,'wallet_outputs':{ident+':'+str(n):scripts[o['script']]['wallet_id'] for ident,tx in known_tx.items() for n,o in enumerate(tx['outputs']) if o['script'] in scripts},'coverage':coverage,'transactions':known_tx,'timestamp_basis':'Confirmed Bitcoin block timestamp UTC; pending transactions are excluded from CGT.'}

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
                row=core.WalletTx(wallet['name'],len(rows)+1,core.tax_date(datetime.fromisoformat(r['utc'].replace('Z','+00:00'))),Decimal(r['sats'])*SAT,Decimal(r['fee_sats'])*SAT,balance,'Declared lost access — CGT event C1' if r.get('cgt_loss') else 'Confirmed blockchain transaction',address=r['address'],txid=r['txid'],fee_known=True,onchain_output_amount=Decimal(0) if r['fee_only'] else None,onchain_fee_source='No network fee: declared access loss' if r.get('cgt_loss') else 'Decoded Bitcoin transaction',descriptor_id=wallet['id'],output_index=r['output_index'],accounting_joint=r.get('joint',False),cgt_loss=bool(r.get('cgt_loss')),loss_outpoints=tuple(r.get('loss_outpoints',[])),loss_label=r.get('loss_label',''))
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
        return core.ChainTxInfo(ident,Decimal(item['fee_sats'])*SAT if item.get('fee_known',True) else None,[core.ChainTxOutput(n,Decimal(o['value'])*SAT,o.get('address') or '') for n,o in enumerate(item['outputs'])],addresses,'Decoded descriptor history transaction')
    core.lookup_transaction_online=lookup

def annotate_balances(report,snapshot):
    price=Decimal(report.get('price_aud_per_btc') or '0');by_name={w['name']:w for w in snapshot['wallets']};by_id={w['id']:w for w in snapshot['wallets']}
    for entry in report['wallets']:
        wallet=by_id.get(entry.get('descriptor_id')) or by_name.get(entry['name'])
        if not wallet:continue
        confirmed=Decimal(entry['btc'] or '0');pending=Decimal(wallet['pending_btc']);total=confirmed+pending
        entry.update(extinct=bool(wallet.get('extinct')),declared_losses=wallet.get('losses',[]),has_vault=wallet.get('has_vault',False),watch_only=wallet.get('watch_only',False),confirmed_btc=str(confirmed),unconfirmed_btc=str(pending),btc=str(total),balance_mode='Descriptor blockchain history',watch_checked_utc=snapshot['scanned_utc'],watch_sources=[snapshot['source']])
        if pending:
            entry.update(cost_basis_aud=None,unrealised_gain_aud=None,state='unavailable',cost_basis_note='Unconfirmed movements are excluded from CGT; acquisition costs for the pending balance are not final.')
        if price:
            entry['btc_value_aud']=entry['value_aud']=str(total*price)
