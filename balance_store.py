"""Atomic balance snapshots and the shared context-bound trace evidence graph."""
from pathlib import Path
from copy import deepcopy
import json,os,uuid
from storage import data_directory,storage_lock

BALANCE_ALGORITHM=4

class BalanceStore:
    def __init__(self,config):self.config=Path(config);self.path=data_directory(self.config)/'balance-trace.json'
    def load(self,key):
        try:data=json.loads(self.path.read_text())
        except (OSError,ValueError):return None
        return data if data.get('schema')==1 and data.get('signature')==key else None
    def save(self,key,result):
        with storage_lock(self.config):
            previous=self.load(key)
            result=deepcopy(result)
            if 'balances' in result:result['basis_algorithm']=BALANCE_ALGORITHM
            if previous:
                old=previous['result']
                if 'balances' in old and 'balances' not in result:
                    result['balances']=deepcopy(old['balances'])
                    if 'basis_algorithm' in old:result['basis_algorithm']=old['basis_algorithm']
                graph=old.get('trace_graph')
                incoming=result.get('trace_graph')
                if graph and incoming:
                    for section in ('nodes','edges','claims'):
                        merged=dict(graph[section])
                        for ident,value in incoming[section].items():
                            if section=='claims' and ident in merged and merged[ident]!=value:
                                raise ValueError('Conflicting confirmed trace ownership evidence.')
                            merged[ident]=value
                        incoming[section]=merged
                elif graph:result['trace_graph']=deepcopy(graph)
                answers={tuple(row[:3]):row[3] for row in old.get('trace_answers',[])}
                answers.update({tuple(row[:3]):row[3] for row in result.get('trace_answers',[])})
                if result.get('trace_graph'):answers={k:v for k,v in answers.items() if k[1] not in ('source','destination')}
                if answers or 'trace_answers' in result:result['trace_answers']=[[*k,v] for k,v in answers.items()]
            self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_name(uuid.uuid4().hex+'.tmp')
            try:
                tmp.write_text(json.dumps({'schema':1,'signature':key,'result':result},ensure_ascii=True));tmp.chmod(0o600);os.replace(tmp,self.path)
            finally:tmp.unlink(missing_ok=True)
    def ready(self,key):
        saved=self.load(key)
        return bool(saved and saved['result'].get('balances') and saved['result'].get('basis_algorithm')==BALANCE_ALGORITHM)
    def save_evidence(self,key,graph,answers):
        # Save discoveries even when an incomplete trace cannot produce balances.
        # This must never unlock the balance/report UI by itself.
        self.save(key,{'trace_graph':graph,'trace_answers':answers})
    def personal(self,key,user):
        saved=self.load(key)
        if not saved or not saved['result'].get('balances') or saved['result'].get('basis_algorithm')!=BALANCE_ALGORITHM:return None
        balance=deepcopy(saved['result']['balances'])
        for row in balance['wallets']:
            owned=row.get('owner_name')==user['name'] and not row.get('smsf')
            row['ownership']='personal' if owned else 'external'
            if row['kind']=='exchange':row['name']='Bitaroo Exchange' if owned else 'Bitaroo Exchange · '+row.get('owner_name','Unknown')
        from wallet_views import extinct_cards
        balance['extinct_wallets']=extinct_cards(self.config)
        return {'taxpayer':user['name'],'balances':balance}
