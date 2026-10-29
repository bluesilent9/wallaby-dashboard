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
    for row in state['exports'].values():
        if row.get('user_id') not in users:raise ValueError('Saved vault proposal refers to an unknown profile.')
        public_vault(row['vault']);clean_diagram(row['diagram']);doc=decode(base64.b64decode(row['psbt'],validate=True))
        if len(doc['inputs'])!=len(row['input_wallets']):raise ValueError('Invalid saved vault funding assignments.')
        if any(any(k[0] in (2,7,8,19,20) for k in f) for f in doc['inputs']):raise ValueError('A composable vault proposal must remain unsigned.')
    for row in state['requests'].values():
        if row.get('kind') not in ('wallet_transfer','sign_psbt_partial','broadcast_transaction') or row.get('state') not in ('pending','complete','denied','error'):raise ValueError('Invalid saved Agent request.')
        if row.get('proposal'):canonical(row['proposal'])
        if row.get('kind')=='wallet_transfer' and row.get('signed_preview'):
            from wallet_preview import validate as validate_preview,verify,policies
            payload=row['signed_preview'];p=row['proposal'];v=payload['preview']
            proof=verify(config,payload,fresh=False) if config else validate_preview(payload,fresh=False)
            if row['direction']!='send' or (v['transfer_id'],v['challenge'],v['sender_ipv6'],v['receiver_ipv6'])!=(p['transfer_id'],p['preview_challenge'],p['node_ipv6'],p['peer']['ipv6']) or policies(v['public_wallet'])!=policies(p['public_wallet']):raise ValueError('Saved preview does not match its frozen request.')
    return state
