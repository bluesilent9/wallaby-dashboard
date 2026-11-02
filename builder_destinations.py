"""Reserved public receiving scripts. A duplicate diagram card never owns UTXOs."""
import copy,json,os,uuid,time
from pathlib import Path
from storage import storage_lock,data_directory
from descriptor_store import DescriptorStore
from preferences import resource_id
from vendor.embit.descriptor import Descriptor
from vendor.embit.descriptor.checksum import checksum
from vendor.embit.networks import NETWORKS
from policy_accounts import keys
from node_wallets import NodeWalletLoader

def base_wallet(config,resource):
    wallet=next((w for w in DescriptorStore(config).read()['wallets'] if resource_id('descriptor',w['id'])==resource),None)
    if not wallet or wallet.get('extinct'):raise ValueError('Choose an active descriptor wallet for a new receiving address.')
    return wallet

def public_rows(wallet,height=None,pair=False):
    candidates={False:[],True:[]}
    def rank(row):
        from multisig_psbt import witness_policy
        d=Descriptor.from_string(row['desc']);height=0
        try:height=witness_policy(d.derive(0).witness_script().data)['height'] or 0
        except (ValueError,AttributeError):pass
        return (-height,not row['desc'].startswith('wpkh('),row['desc'])
    for row in wallet['raw']['descriptors']:
        d=Descriptor.from_string(row['desc'])
        if not d.is_wildcard or d.num_branches!=1 or d.is_taproot:continue
        internal=row.get('internal')
        if internal not in candidates:continue
        if height is None and not pair and row.get('active') is not True:continue
        if height is not None:
            from multisig_psbt import witness_policy
            if d.witness_script():
                check=witness_policy(d.derive(0).witness_script().data);threshold=check['threshold']
                if check['height'] and height<=check['height']:continue
            elif len(d.keys)==1 and row['desc'].startswith('wpkh('):threshold=1
            else:continue
            values=[k.to_string() for k in d.keys]
            spend='pk('+values[0]+')' if len(values)==1 else 'multi('+str(threshold)+','+','.join(values)+')'
            body='wsh(and_v(v:after('+str(height)+'),'+spend+'))';row=dict(row,desc=body+'#'+checksum(body),active=False)
        candidates[internal].append(row)
    if not candidates[False]:raise ValueError('This wallet has no supported publicly derivable receiving policy. No new address was reserved.')
    # Prefer native SegWit, then a complete mandatory-height policy. Never
    # silently replace a vault with an unrestricted policy during renewal.
    chosen=[sorted(candidates[False],key=rank)[0]]
    if height is not None or pair:
        if not candidates[True]:raise ValueError('A paired public change policy is required for a vault refresh.')
        receive_keys=keys({'descriptors':[chosen[0]]})
        paired=[r for r in candidates[True] if {k.rsplit('/',2)[0] for k in keys({'descriptors':[r]})}=={k.rsplit('/',2)[0] for k in receive_keys}]
        if not paired:raise ValueError('Receive and change policies do not use the same signing accounts.')
        chosen.append(sorted(paired,key=rank)[0])
    return chosen

class Destinations:
    def __init__(self,config):self.config=config;self.path=data_directory(config)/'receiving-destinations.json'
    def read(self):return json.loads(self.path.read_text()) if self.path.exists() else {'version':1,'destinations':{}}
    def write(self,data):
        from agent_hub import canonical
        raw=canonical(data)
        if len(raw)>32*1024*1024:raise ValueError('Reserved address storage is full.')
        self.path.parent.mkdir(parents=True,exist_ok=True);p=self.path.with_suffix('.tmp');p.write_text(raw);p.chmod(0o600)
        with p.open('rb') as f:os.fsync(f.fileno())
        os.replace(p,self.path)
    def create(self,registry,user,resource,height=None):
        if height is not None and (type(height) is not int or not 0<height<500000000):raise ValueError('Calculate a valid vault block height first.')
        wallet=base_wallet(self.config,resource);rows=public_rows(wallet,height);nodes={n['id']:n for n in registry.snapshot()['nodes'] if n.get('rpc_ready') and n.get('blockchain',{}).get('state')=='ready'}
        replica_sources=[s for s in wallet.get('node_sources',[]) if s['node_id'] in nodes]
        if not replica_sources:raise ValueError('Connect a persistent node copy of this wallet before reserving a new address.')
        maximum=0;used_addresses=set();live_policies=set()
        for source in replica_sources:
            response=registry.rpc.call(source['node_id'],'listdescriptors',[False],source['wallet_name'],timeout_seconds=60,consume=True,priority=0)
            if response['state']!='succeeded':raise ValueError('A connected wallet replica could not confirm its current receiving cursor. No address was reserved.')
            live=response['result'];known={NodeWalletLoader.descriptor_key(r['desc']) for r in wallet['raw']['descriptors']};present={NodeWalletLoader.descriptor_key(r['desc']) for r in live['descriptors']}
            if not known & present:raise ValueError('Connected wallet policies changed. Wait for node discovery before continuing.')
            live_policies|=present
            receipts=registry.rpc.call(source['node_id'],'listreceivedbyaddress',[0,False,True],source['wallet_name'],timeout_seconds=60,consume=True,priority=0)
            if receipts['state']!='succeeded' or not isinstance(receipts['result'],list):raise ValueError('Core could not verify previously used receiving addresses. No new address was reserved.')
            used_addresses.update(r['address'] for r in receipts['result'])
            for row in live['descriptors']:
                if row.get('internal') is False:maximum=max(maximum,row.get('next_index') or row.get('next') or 0)
        required=public_rows(wallet,pair=True) if height is not None else rows
        if not {NodeWalletLoader.descriptor_key(r['desc']) for r in required}<=live_policies:raise ValueError('No connected Core replica confirmed the complete source receiving policy.')
        family_key=sorted(k.rsplit('/',2)[0] for k in keys(wallet['raw']))
        with storage_lock(self.config):
            data=self.read()
            for d in data['destinations'].values():
                if d['family_keys']==family_key:maximum=max(maximum,d['index']+1)
            descriptor=Descriptor.from_string(rows[0]['desc'])
            while maximum<=rows[0].get('range',[0,0])[1] and descriptor.derive(maximum).address(NETWORKS['main']) in used_addresses:maximum+=1
            if height is not None and maximum>999:raise ValueError('This vault renewal exceeds the Agent’s independently scanned receiving range (0–999). Extend the protocol before funding another address.')
            if maximum>rows[0].get('range',[0,0])[1]:raise ValueError('The public receiving range is exhausted. Extend it locally on the Agent first.')
            descriptor=Descriptor.from_string(rows[0]['desc']);derived=descriptor.derive(maximum);address=derived.address(NETWORKS['main']);ident='receive-'+uuid.uuid4().hex
            record={'id':ident,'user_id':user,'base':resource,'base_descriptor_id':wallet['id'],'family_keys':family_key,'source_wallet':copy.deepcopy(wallet['raw']),'descriptors':rows,'index':maximum,'address':address,'height':height,'policy_timestamp':int(time.time()),'policy_requests':[]}
            data['destinations'][ident]=record;self.write(data);return copy.deepcopy(record)
    def get(self,ident):
        d=self.read()['destinations'].get(ident)
        if not d:raise ValueError('A reserved receiving address is missing. Remove that card and add it again.')
        validate_record(d)
        return copy.deepcopy(d)
    def enrich(self,catalog,ids,hub=None,require_policy=False):
        result=copy.deepcopy(catalog);wallets={w['id']:w for w in result['wallets']}
        for ident in set(ids):
            if not str(ident).startswith('receive-'):continue
            d=self.get(ident);base=wallets.get(d['base'])
            if not base:raise ValueError('The source wallet for a new address is no longer available.')
            if d['height'] and require_policy:
                from wallet_policy_updates import destination_ready,destination_issues
                if not hub or not destination_ready(hub,d):raise ValueError('The new vault policy is awaiting local Agent approval and Core verification. No PSBT has been prepared. '+(destination_issues(hub,d) if hub else 'Connect a verified node.'))
            policy_hash=None
            if d['height']:
                from agent_hub import digest
                old=public_rows({'raw':d['source_wallet']},pair=True)
                policy_hash=digest({'previous_descriptors':[r['desc'] for r in old],'receive_descriptor':d['descriptors'][0]['desc'],'change_descriptor':d['descriptors'][1]['desc'],'timestamp':d['policy_timestamp']})
            wallets[ident]=dict(base,output_policy_hash=policy_hash,id=ident,base_wallet_id=d['base'],can_send=False,can_receive=True,btc='0',aud='0',address=d['address'],new_address=True,vault_destination=d['height'] is not None,vault_height=d['height'],output_descriptor=d['descriptors'][0]['desc'],output_index=d['index'])
        result['wallets']=list(wallets.values());return result

def validate_record(d):
    import re
    from descriptor_store import clean_wallet
    if not isinstance(d,dict) or not re.fullmatch(r'receive-[0-9a-f]{32}',str(d.get('id'))) or type(d.get('index')) is not int or not 0<=d['index']<2**31:raise ValueError('Invalid reserved receiving address.')
    clean_wallet(d['source_wallet'])
    if not isinstance(d.get('descriptors'),list) or not 1<=len(d['descriptors'])<=2:raise ValueError('Invalid reserved public policy.')
    for row in d['descriptors']:clean_wallet({'wallet_name':'Reserved public policy','descriptors':[row]})
    desc=Descriptor.from_string(d['descriptors'][0]['desc'])
    if not keys({'descriptors':d['descriptors']})<=keys(d['source_wallet']) or desc.derive(d['index']).address(NETWORKS['main'])!=d.get('address'):raise ValueError('Reserved destination changes signing keys or its address.')
    expected=public_rows({'raw':d['source_wallet']},d.get('height'))
    if {NodeWalletLoader.descriptor_key(r['desc']) for r in expected}!={NodeWalletLoader.descriptor_key(r['desc']) for r in d['descriptors']}:raise ValueError('Reserved destination changes the original spending threshold, keys or policy.')
    if d.get('height') is not None:
        if type(d.get('policy_timestamp')) is not int or not 0<=d['policy_timestamp']<=4102444800:raise ValueError('Invalid vault policy creation time.')
        from multisig_psbt import witness_policy
        if witness_policy(desc.derive(d['index']).witness_script().data)['height']!=d['height']:raise ValueError('Reserved vault height differs from its script.')
    return d

def validate(state,users):
    if not isinstance(state,dict) or set(state)!={'version','destinations'} or state['version']!=1 or len(state['destinations'])>100000:raise ValueError('Invalid receiving destinations archive.')
    for ident,d in state['destinations'].items():
        validate_record(d)
        if ident!=d['id'] or d['user_id'] not in users:raise ValueError('Invalid receiving destination owner.')
    return state
