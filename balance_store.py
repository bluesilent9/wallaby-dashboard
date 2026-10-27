"""Atomic shared Balance Trace snapshots and context-bound source decisions."""
from pathlib import Path
from copy import deepcopy
import json,os,uuid
from storage import data_directory,storage_lock

class BalanceStore:
    def __init__(self,config):self.config=Path(config);self.path=data_directory(self.config)/'balance-trace.json'
    def load(self,key):
        try:data=json.loads(self.path.read_text())
        except (OSError,ValueError):return None
        return data if data.get('schema')==1 and data.get('signature')==key else None
    def save(self,key,result):
        with storage_lock(self.config):
            self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_name(uuid.uuid4().hex+'.tmp')
            try:
                tmp.write_text(json.dumps({'schema':1,'signature':key,'result':result},ensure_ascii=True));tmp.chmod(0o600);os.replace(tmp,self.path)
            finally:tmp.unlink(missing_ok=True)
    def personal(self,key,user):
        saved=self.load(key)
        if not saved:return None
        balance=deepcopy(saved['result']['balances'])
        for row in balance['wallets']:
            owned=row.get('owner_name')==user['name'] and not row.get('smsf')
            row['ownership']='personal' if owned else 'external'
            if row['kind']=='exchange':row['name']='Bitaroo Exchange' if owned else 'Bitaroo Exchange · '+row.get('owner_name','Unknown')
        return {'taxpayer':user['name'],'balances':balance}
