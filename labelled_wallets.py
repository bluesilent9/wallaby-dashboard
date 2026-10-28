"""Core fixed-address labels define historical wallet groups, without identity guesses."""
from copy import deepcopy
from datetime import datetime,timezone
import re
from descriptor_policy import AddressPolicy,parse_policy
from descriptor_store import clean_wallet

def label_parts(label):
    if not isinstance(label,str) or not label.strip() or any(ord(c)<32 for c in label):raise ValueError('Historical addresses need a nonempty wallet label in Bitcoin Core.')
    name=label.strip();lost=None
    if 'LOST:' in name:
        match=re.fullmatch(r'(.+?)\s+\|\s+LOST:(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}) UTC',name)
        if not match:raise ValueError('Invalid historical loss label. Use Wallet name | LOST:DD/MM/YYYY HH:MM:SS UTC.')
        name=match[1].strip()
        try:lost=datetime.strptime(match[2],'%d/%m/%Y %H:%M:%S').replace(tzinfo=timezone.utc).isoformat().replace('+00:00','Z')
        except ValueError:raise ValueError('Invalid UTC date/time in historical loss label.') from None
    if not name or len(name)>200:raise ValueError('Historical wallet label must contain a wallet name of up to 200 characters.')
    return name,lost

def expand(replica,address_info):
    raw=replica['wallet']['raw'];fixed=[];regular=[]
    for row in raw['descriptors']:
        policy,_,_=parse_policy(row['desc'].split('#')[0])
        (fixed if isinstance(policy,AddressPolicy) else regular).append(row)
    if not fixed:return [replica]
    if replica['private_keys_enabled'] is not False:raise ValueError('Fixed-address historical imports require a Core wallet with private keys disabled.')
    groups={}
    for row in fixed:
        address=parse_policy(row['desc'].split('#')[0])[0].address
        info=address_info(address)
        labels=info.get('labels',[])
        if not isinstance(labels,list) or len(labels)!=1 or not isinstance(labels[0],str):raise ValueError('Set one wallet label for historical address '+address+' in Bitcoin Core, then Load Node Wallets again.')
        name,lost=label_parts(labels[0]);key=name.casefold()
        if key not in groups:groups[key]={'name':name,'rows':[],'addresses':{}}
        group=groups[key];group['rows'].append(row);group['addresses'][address]={'label':labels[0],'lost_utc':lost}
    result=[]
    if regular:
        normal=deepcopy(replica);normal['wallet']=clean_wallet({'wallet_name':raw['wallet_name'],'descriptors':regular})
        # Fixed-address receipts belong exclusively to their labelled groups.
        addresses={a for g in groups.values() for a in g['addresses']}
        normal['history']['transactions']=[r for r in normal['history']['transactions'] if r.get('category')=='send' or r.get('address') not in addresses]
        result.append(normal)
    for group in groups.values():
        child=deepcopy(replica);child['wallet']=clean_wallet({'wallet_name':group['name'],'descriptors':group['rows']})
        child['wallet'].update(extinct=True,address_labels=group['addresses'])
        child['history']['transactions']=[r for r in child['history']['transactions'] if r.get('category')=='send' or r.get('address') in group['addresses']]
        result.append(child)
    return result
