"""Conservative planning estimate only: public UTXOs, no PSBT or wallet signing."""
import hashlib,json,threading,time
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
from pathlib import Path
from descriptor_store import DescriptorStore
from descriptor_snapshot import saved_history
from descriptor_chain import address_for
from vendor.embit.script import Script,address_to_scriptpubkey
from preferences import resource_id
from address_book import valid_address

_CACHE={};_LOCK=threading.Lock()

def known_scripts(chain):
    result={w['id']:set() for w in chain['wallets']}
    if 'wallet_outputs' in chain:
        for outpoint,uid in chain['wallet_outputs'].items():
            ident,index=outpoint.rsplit(':',1);tx=chain['transactions'].get(ident)
            if uid in result and tx:result[uid].add(tx['outputs'][int(index)]['script'])
        return result
    for w in chain['wallets']:
        for r in w['records']:
            tx=chain['transactions'].get(r['txid'])
            if not tx:continue
            if r['sats']>0:result[w['id']].add(tx['outputs'][r['output_index']]['script'])
        groups={}
        for r in w['records']:groups.setdefault(r['txid'],[]).append(r)
        for ident,rows in groups.items():
            if not any(r['sats']<0 for r in rows):continue
            tx=chain['transactions'].get(ident)
            if not tx:continue  # Economic C1 records are not Bitcoin transactions.
            result[w['id']].update(i['script'] for i in tx['inputs'])
            sent={r['output_index'] for r in rows if r['sats']<0 and not r['fee_only']}
            result[w['id']].update(o['script'] for n,o in enumerate(tx['outputs']) if n not in sent and o['value']>0)
    return result

def input_weight(raw):
    # Upper-bound ECDSA input sizes. Key-path Taproot assumes a 65-byte signature.
    script=Script(bytes.fromhex(raw));kind=script.script_type()
    if kind=='p2wpkh':return 273,True
    if kind=='p2tr':return 231,True
    if kind=='p2pkh':return 180*4,False # also covers legacy uncompressed keys
    if kind=='p2sh':return 365,True # permitted here only for known sh(wpkh) / combo exports
    if raw.endswith('ac') and len(raw) in (70,134):return 115*4,False
    raise ValueError('This input script needs more signing-size information before fees can be estimated.')

def compact_size(n):return 1 if n<253 else 3 if n<=65535 else 5

def estimate_size(coins,outputs):
    witness=any(c['witness'] for c in coins)
    weight=(8+compact_size(len(coins))+compact_size(len(outputs)))*4
    weight+=sum(c['weight'] for c in coins)+sum((8+compact_size(len(s))+len(s))*4 for s in outputs)
    if witness:weight+=2+sum(not c['witness'] for c in coins)
    return (weight+3)//4

def fee_shares(rates,size):
    exact={uid:rate*size for uid,rate in rates.items()};parts={uid:int(n.to_integral_value(rounding=ROUND_FLOOR)) for uid,n in exact.items()}
    remaining=int(sum(exact.values(),Decimal(0)).to_integral_value(rounding=ROUND_CEILING))-sum(parts.values())
    order=sorted(exact,key=lambda uid:(-(exact[uid]-parts[uid]),uid))
    for uid in order[:remaining]:parts[uid]+=1
    return parts

def continuation_status(outputs,total_fee):
    if not outputs:return False,'Add at least one Bitcoin payment to a wallet or exchange.'
    if total_fee<=0:return False,'Add a positive Network Fee contribution.'
    if sum(outputs.values())>2100000000000000:return False,'Payment outputs exceed Bitcoin’s maximum supply.'
    for address,value in outputs.items():
        script=address_to_scriptpubkey(address);raw=script.data
        # Core's standard dust policy uses 3 sat/vB and a typical future spend size.
        spend=67 if script.script_type() in ('p2wpkh','p2wsh','p2tr') else 148
        threshold=3*(8+compact_size(len(raw))+len(raw)+spend)
        if value<threshold:return False,'A payment is below the standard dust limit for its receiving address.'
    return True,'Plan checked against confirmed funds and a conservative fee estimate.'

def dust_limit(script):
    raw=script.data;spend=67 if script.script_type() in ('p2wpkh','p2wsh','p2tr') else 148
    return 3*(8+compact_size(len(raw))+len(raw)+spend)

def plan(edges,catalog,coins,change_scripts=None,details=False,fixed=None):
    wallets={w['id']:w for w in catalog['wallets']};payments={};rates={};maxima={};incoming=set();pairs=set()
    edges=[dict(e) for e in edges]
    for index,edge in enumerate(edges):
        source=edge['from'];target=edge['to'];amount=int(edge['amount_sats']);rate=Decimal(edge['fee_rate']);maximum=edge.get('max',False)
        if source not in wallets or not wallets[source]['can_send']:raise ValueError('Only the loaded descriptor wallets can fund the plan.')
        if source==target or (source,target) in pairs:raise ValueError('Duplicate or self-directed connections are not allowed.')
        pairs.add((source,target))
        if type(maximum) is not bool or amount<0 or amount>2100000000000000 or not rate.is_finite() or rate<0:raise ValueError('Invalid payment or fee contribution.')
        if target=='network-fee':
            if amount or maximum:raise ValueError('Network Fee connections accept only sats/vB contributions.')
        else:
            if rate:raise ValueError('Wallet and exchange connections accept only Bitcoin amounts.')
            if target not in wallets or not wallets[target]['can_receive']:raise ValueError('Choose a verified receiving destination.')
            if maximum:
                if source in maxima:raise ValueError('Choose only one maximum payment per funding wallet.')
                maxima[source]=index
            else:payments[source]=payments.get(source,0)+amount
            if amount or maximum:incoming.add(target)
        rates[source]=rates.get(source,Decimal(0))+rate
    if any(uid in incoming for uid in maxima):raise ValueError('A wallet cannot be emptied while this same transaction also pays into it. Remove its incoming payments or turn off maximum.')
    sources=sorted(uid for uid in set(payments)|set(rates)|set(maxima) if payments.get(uid,0) or rates.get(uid,0) or uid in maxima)
    if not sources:raise ValueError('Enter a payment amount or fee contribution first.')
    payment_addresses={wallets[e['to']]['address'] for e in edges if e['to']!='network-fee' and (int(e['amount_sats']) or e.get('max'))}
    out_scripts=[address_to_scriptpubkey(a).data for a in sorted(payment_addresses)]
    change_scripts=change_scripts or {};change={}
    for uid in sources:
        if uid in maxima:
            change[uid]=Script(b'');continue
        address=wallets[uid].get('address')
        if not valid_address(address):raise ValueError('A funding wallet needs a known receiving/change script for this estimate.')
        change[uid]=change_scripts.get(uid) or Script(bytes(wallets[uid].get('change_script_size',max(34,len(address_to_scriptpubkey(address).data)))))
    ordered={uid:sorted(coins.get(uid,[]),key=lambda c:(-c['value'],c['txid'],c['vout'])) for uid in sources}
    seen=set()
    for rows in ordered.values():
        for coin in rows:
            key=(coin['txid'],coin['vout'])
            if key in seen:raise ValueError('Overlapping wallet UTXOs cannot be counted twice.')
            seen.add(key)
    selected={uid:list(ordered[uid]) if uid in maxima else [] for uid in sources};shares={uid:0 for uid in sources}
    for _ in range(1000):
        for uid in sources:
            need=payments.get(uid,0)+shares.get(uid,0)+(1 if uid in maxima else 0)
            while not selected[uid] or sum(c['value'] for c in selected[uid])<need:
                if uid in maxima or len(selected[uid])>=len(ordered[uid]):raise ValueError('Not enough confirmed, unspent funds for '+wallets[uid]['name']+' under this estimate. Refresh descriptor history if balances have changed.')
                selected[uid].append(ordered[uid][len(selected[uid])])
        # A conservative change slot stays in the size bound even when an exact-spend
        # normal wallet ends with zero change. Maximum wallets have no change slot.
        size=estimate_size([c for rows in selected.values() for c in rows]+(fixed or {}).get('inputs',[]),out_scripts+[change[u].data for u in sources if u not in maxima]+(fixed or {}).get('outputs',[]))
        updated=fee_shares({uid:rates.get(uid,Decimal(0)) for uid in sources},size)
        if not all(sum(c['value'] for c in selected[u])>=payments.get(u,0)+updated.get(u,0)+(1 if u in maxima else 0) for u in sources):
            shares=updated;continue
        dust_wallet=None
        for uid in sources:
            remainder=sum(c['value'] for c in selected[uid])-payments.get(uid,0)-updated.get(uid,0)
            threshold=dust_limit(change[uid]) if uid in change_scripts else 546
            if uid not in maxima and 0<remainder<threshold:
                if len(selected[uid])<len(ordered[uid]):selected[uid].append(ordered[uid][len(selected[uid])]);dust_wallet=uid;break
                raise ValueError('Change for '+wallets[uid]['name']+' would be dust. Adjust its payment or select maximum.')
        if dust_wallet:shares=updated;continue
        shares=updated;break
    else:raise ValueError('The fee calculation did not converge; simplify the plan before continuing.')
    changes={};outputs={};maximum_amounts={}
    for uid in sources:
        remainder=sum(c['value'] for c in selected[uid])-payments.get(uid,0)-shares.get(uid,0)
        if uid in maxima:
            edges[maxima[uid]]['amount_sats']=str(remainder);maximum_amounts[uid]=str(remainder)
        elif remainder:changes[uid]=remainder
    for e in edges:
        if e['to']!='network-fee' and int(e['amount_sats']):
            address=wallets[e['to']]['address'];outputs[address]=outputs.get(address,0)+int(e['amount_sats'])
    ready,message=continuation_status(outputs or (fixed or {}).get('payments',{}),sum(shares.values())+(fixed or {}).get('fee_sats',0))
    if sum(outputs.values())+sum((fixed or {}).get('payments',{}).values())>2100000000000000:ready,message=False,'Payment outputs exceed Bitcoin’s maximum supply.'
    if size>100000:ready,message=False,'The transaction exceeds the standard size limit. Reduce its inputs or outputs.'
    result={'can_continue':ready,'validation_message':message,'vbytes':size,'total_fee_sats':str(sum(shares.values())),'wallet_fees_sats':{uid:str(n) for uid,n in shares.items()},'maximum_amounts':maximum_amounts,'confirmed_balances_sats':{u:str(sum(c['value'] for c in ordered[u])) for u in sources},'combined_rate':str(sum(rates.values(),Decimal(0))),'input_count':sum(len(c) for c in selected.values()),'change_outputs':len(changes),'estimated':True,'basis':'Confirmed public UTXOs; conservative signed input sizes. Maximum spends every confirmed spendable input and has no change.'}
    if details:result.update(selected=selected,changes=changes,outputs=outputs,resolved_edges=edges)
    return result

def live_coins(session,catalog,source_ids,force=False):
    store=DescriptorStore(session.preferences_path);chain=saved_history(session.preferences_path,store.read());scripts=known_scripts(chain)
    raw_wallets={w['id']:w for w in store.read()['wallets']}
    by_resource={resource_id('descriptor',w['id']):dict(w,raw=raw_wallets[w['id']]['raw']) for w in chain['wallets']}
    key=(str(session.preferences_path),chain['scanned_utc'],tuple(sorted(source_ids)))
    with _LOCK:
        cached=_CACHE.get(key)
        if not force and cached and time.monotonic()-cached[0]<30:return cached[1]
    from node_chain import NodeChain,RegistryRPC,satoshis
    client=NodeChain(RegistryRPC(session.node_registry),store.read())
    result={};seen=set()
    for uid in source_ids:
        wallet=by_resource.get(uid)
        if not wallet:raise ValueError('Only descriptor wallets can fund this transaction.')
        wallet['node_sources']=raw_wallets[wallet['id']].get('node_sources',[])
        utxos=[]
        for row in client.wallet_unspent(wallet):
            if int(row.get('confirmations',0))<1 or not row.get('safe',True):continue
            txid=row['txid'];vout=row['vout'];value=satoshis(row['amount']);raw=row['scriptPubKey'];keyout=(txid,vout)
            if type(vout) is not int or vout<0 or value<=0:raise ValueError('Invalid node UTXO')
            client.hints[txid]=wallet['node_sources']
            tx=client.transaction(txid)
            if not vout<len(tx.vout) or tx.vout[vout].value!=value or tx.vout[vout].script_pubkey.data.hex()!=raw:raise ValueError('Node UTXO did not match its raw transaction.')
            # listunspent is Core's authoritative wallet ownership check. Do not
            # restrict it to scripts in an earlier Dashboard snapshot.
            if not row.get('solvable',False):continue
            if len(tx.vin)==1 and tx.vin[0].txid==b'\0'*32 and int(row['confirmations'])<100:continue
            if keyout in seen:raise ValueError('Overlapping wallet UTXOs cannot be counted twice.')
            seen.add(keyout)
            if Script(bytes.fromhex(raw)).script_type()=='p2sh':
                policies=[r['desc'] for r in wallet['raw']['descriptors']]
                if not any(d.startswith(('sh(wpkh(','combo(')) for d in policies) or any(d.startswith('sh(') and not d.startswith('sh(wpkh(') for d in policies):raise ValueError('Wrapped input policy needs more size information.')
            if Script(bytes.fromhex(raw)).script_type()=='p2tr':
                from descriptor_policy import parse_policy
                if any(bool(parse_policy(r['desc'].split('#')[0])[0].taptree) for r in wallet['raw']['descriptors'] if r['desc'].startswith('tr(')):raise ValueError('Taproot script-path inputs need signing-size information before estimating fees.')
            if Script(bytes.fromhex(raw)).script_type()=='p2wsh':raise ValueError('Vault or multisignature inputs need signing-size information before estimating fees.')
            weight,witness=input_weight(raw);utxos.append({'txid':txid,'vout':vout,'value':value,'weight':weight,'witness':witness,'script':raw,'raw_tx':tx.serialize().hex()})
        result[uid]=utxos
    with _LOCK:
        if len(_CACHE)>64:_CACHE.clear()
        _CACHE[key]=(time.monotonic(),result)
    return result
