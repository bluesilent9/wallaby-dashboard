"""User-declared irrecoverable losses, separate from physical Bitcoin movements.

A LOST label asserts the loss and its discovery time. Core can confirm the
outputs and spending history, not the destruction of a private key. This path
assumes no compensation and uses the engine's existing parcel-cost convention.
"""
from datetime import datetime,timezone
import hashlib

def instant(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)

def append_loss_records(wallets,transactions,now=None):
    now=now or datetime.now(timezone.utc)
    spent={}
    for tx in transactions.values():
        for item in tx['inputs']:
            key=item['txid']+':'+str(item['vout'])
            if key in spent:raise ValueError('Conflicting confirmed spends in node history.')
            spent[key]=instant(tx['utc'])
    for wallet in wallets:
        # Rebuilding a snapshot never appends a second economic loss.
        wallet['records']=[r for r in wallet['records'] if not r.get('cgt_loss')]
        losses=[]
        for address,meta in sorted(wallet.get('address_labels',{}).items()):
            if not meta.get('lost_utc'):continue
            when=instant(meta['lost_utc'])
            if when>now:raise ValueError('Declared loss time is in the future for '+address)
            outputs=[]
            for ident,tx in transactions.items():
                for n,out in enumerate(tx['outputs']):
                    if out.get('address')!=address:continue
                    key=ident+':'+str(n);created=instant(tx['utc']);used=spent.get(key)
                    if created>when:raise ValueError('Address received Bitcoin after its declared loss time: '+address+'. Review the Core LOST label.')
                    if used is not None and used>when:raise ValueError('Output was spent after its declared loss time: '+key+'. Review the Core LOST label.')
                    if used is None:outputs.append((key,out['value']))
            if not outputs:raise ValueError('No retained output supports the declared loss for '+address+'. Review the Core LOST label and rescan history.')
            qty=sum(amount for _,amount in outputs)
            if qty<=0:raise ValueError('Declared loss quantity must be positive.')
            identity=hashlib.sha256((wallet['id']+'|'+address+'|'+meta['lost_utc']).encode()).hexdigest()
            row={'txid':'C1:'+identity,'utc':meta['lost_utc'],'output_index':-1,'sats':-qty,'fee_sats':0,
                 'address':address,'fee_only':False,'height':0,'cgt_loss':True,'loss_outpoints':[key for key,_ in sorted(outputs)],'loss_label':meta['label']}
            losses.append(row)
        wallet['records'].extend(losses)
        # Stable physical dependency order is retained for equal block times;
        # losses occur after all physical movements at that instant.
        wallet['records'].sort(key=lambda r:(instant(r['utc']),bool(r.get('cgt_loss'))))
        wallet['losses']=[{'utc':r['utc'],'address':r['address'],'sats':-r['sats'],'outpoints':r['loss_outpoints'],'label':r['loss_label']} for r in losses]
