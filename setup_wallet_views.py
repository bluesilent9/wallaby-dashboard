"""Public setup identities for discovery/presentation; no ceremony or wallet writes."""
import json
from storage import data_directory
from policy_accounts import intentional_account

class SetupWalletViews:
    def __init__(self,config,monitors=()):
        path=data_directory(config)/'wallet-setups.json'
        self.setups=list(json.loads(path.read_text())['sessions'].values()) if path.exists() else []
        self.monitors=list(monitors)
    def setup(self,raw):
        from node_wallets import NodeWalletLoader
        keys={NodeWalletLoader.descriptor_key(r['desc']) for r in raw.get('descriptors',[])}
        return next((s for s in self.setups if keys and keys=={NodeWalletLoader.descriptor_key(d) for d in s.get('descriptors',[])}),None)
    def hidden(self,raw):
        if intentional_account(raw,self.setups,self.monitors):return True
        s=self.setup(raw)
        return bool(s and (s['state']!='ready' or not set(s['routes'].values())<=set(s.get('completion_confirmed',{}))))
    def hide_cached(self,wallet):
        # Missing history is unknown, never proof an account is empty. A key
        # account with its own receipts/sends is a real financial wallet too.
        return (isinstance(wallet.get('public_history'),dict) and 'outputs' in wallet['public_history']
                and not wallet['public_history']['outputs'] and wallet.get('node_history')==[]
                and not wallet.get('vault_evidence') and self.hidden(wallet['raw']))
    def annotate(self,wallet):
        s=self.setup(wallet['raw'])
        if s:wallet['setup_name']=s['proposal']['name']
        return wallet
