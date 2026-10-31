"""Validate public Agent records before accepting a profile archive."""
import base64,re
from agent_hub import canonical,ipv6,digest
from vault_records import public_vault
from signed_psbt import decode
from transaction_templates import clean_diagram

def validate(state,users,config=None):
    if not isinstance(state,dict) or set(state)!={'version','vaults','nodes','requests','exports','receipts'} or type(state['version']) is not int or state['version']!=1:raise ValueError('Invalid saved Agent exchange schema.')
    canonical(state)
    if any(not isinstance(state[k],dict) or len(state[k])>100000 for k in ('vaults','nodes','requests','exports','receipts')):raise ValueError('Invalid saved Agent record bounds.')
    for ident,row in state['vaults'].items():
        public_vault(row['public'])
        if ident!=digest(row['public']) or not isinstance(row['sources'],list):raise ValueError('Invalid saved vault identity.')
    for row in state['nodes'].values():
        if row.get('ipv6'):ipv6(row['ipv6'])
        if 'sync_revision' in row:
            if type(row['sync_revision']) is not int or row['sync_revision']<0 or not isinstance(row.get('sync_snapshot'),list) or len(row['sync_snapshot'])>128:raise ValueError('Invalid saved synchronization list.')
            seen=set()
            for p in row['sync_snapshot']:
                if p.get('kind')!='wallet_transfer' or p.get('transfer_version')!=2 or p.get('transfer_id') in seen:raise ValueError('Invalid saved synchronization row.')
                seen.add(p['transfer_id']);canonical(p)
    for row in state['exports'].values():
        if row.get('user_id') not in users:raise ValueError('Saved vault proposal refers to an unknown profile.')
        public_vault(row['vault']);clean_diagram(row['diagram']);doc=decode(base64.b64decode(row['psbt'],validate=True))
        if len(doc['inputs'])!=len(row['input_wallets']):raise ValueError('Invalid saved vault funding assignments.')
        if any(any(k[0] in (2,7,8,19,20) for k in f) for f in doc['inputs']):raise ValueError('A composable vault proposal must remain unsigned.')
    for ident,row in state['requests'].items():
        if row.get('kind') not in ('wallet_transfer','sign_psbt_partial','broadcast_transaction','wallet_retire') or row.get('state') not in ('pending','complete','denied','error','recovery_required','cancelled'):raise ValueError('Invalid saved Agent request.')
        if row.get('state')=='cancelled' and row.get('kind')!='wallet_retire':raise ValueError('Invalid cancelled request kind.')
        if row.get('proposal'):canonical(row['proposal'])
        if row.get('kind')=='wallet_retire' and row.get('proposal',{}).get('version')==2:
            from wallet_retirement import result
            p=row['proposal'];body=dict(p);request_id=body.pop('request_id',None)
            if ident!=digest(p) or request_id!=digest(body) or row.get('user_id') not in users or p.get('delete_wallet') is not True or p.get('require_zero_balance') is not True or row.get('disc_status') not in ('unconfirmed','destroyed','none'):raise ValueError('Invalid saved retirement request.')
            if row.get('cancelled_before_delivery'):
                if row['state']!='cancelled' or row.get('delivered') or not row.get('cancel_requested'):raise ValueError('Invalid cancellation-before-delivery record.')
            elif row['state']!='pending':
                import copy
                test=copy.deepcopy(row);test['state']='pending';result({'requests':{ident:test}},row['node_id'],row.get('result'))
                if test['state']!=row['state']:raise ValueError('Saved retirement outcome mismatch.')
            if row.get('disc_status')!='unconfirmed' and (row['state']!='complete' or not row.get('disc_confirmed_utc')):raise ValueError('Unverified saved disc confirmation.')
            root=row.get('root_hash')
            if root not in state['requests'] or not state['requests'][root].get('expected_holders'):raise ValueError('Invalid retirement holder group.')
            if ident==root:
                holders=row['expected_holders']
                if not isinstance(holders,dict) or not 1<=len(holders)<=256 or row['node_id'] not in holders or any(not isinstance(k,str) or not isinstance(v,str) for k,v in holders.items()):raise ValueError('Invalid expected retirement holders.')
                if row.get('disc_status')!='unconfirmed':
                    from wallet_retirement import group_complete
                    if not group_complete(state,ident):raise ValueError('Not every holder confirmed retirement.')
        if row.get('kind')=='wallet_transfer' and row.get('signed_preview'):
            from wallet_preview import validate as validate_preview,verify,policies
            payload=row['signed_preview'];p=row['proposal'];v=payload['preview']
            proof=verify(config,payload,fresh=False) if config else validate_preview(payload,fresh=False)
            if row['direction']!='send' or (v['transfer_id'],v['challenge'],v['sender_ipv6'],v['receiver_ipv6'])!=(p['transfer_id'],p['preview_challenge'],p['node_ipv6'],p['peer']['ipv6']) or policies(v['public_wallet'],True)!=policies(p['public_wallet'],True):raise ValueError('Saved preview does not match its frozen request.')
    return state
