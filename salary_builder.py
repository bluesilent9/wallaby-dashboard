"""Saved visual salary calculations. No signing, Agent requests or transactions."""
import json,os
from pathlib import Path
from storage import data_directory,storage_lock
from transaction_templates import clean_diagram
from preferences import FamilyStore

def validate(value,users):
    if not isinstance(value,dict) or set(value)-set(users):raise ValueError('Invalid Salary Builder profile.')
    for row in value.values():
        if not isinstance(row,dict) or type(row.get('started')) is not bool or type(row.get('revision')) is not int or row['revision']<0:raise ValueError('Invalid Salary Builder state.')
        if row['started']:clean_diagram(row.get('diagram'))
    return value

class SalaryStore:
    def __init__(self,config):self.config=Path(config);self.path=data_directory(config)/'salary-builder.json'
    def read(self):return json.loads(self.path.read_text()) if self.path.exists() else {}
    def discard(self,user):
        with storage_lock(self.config):
            data=self.read()
            if user in data:
                del data[user];self.write(data)
    def write(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(data));tmp.chmod(0o600);os.replace(tmp,self.path)
    def snapshot(self,user):
        if not FamilyStore(self.config).user(user):raise ValueError('Select a family user first.')
        with storage_lock(self.config):return self.read().get(user,{'started':False,'revision':0})
    def change(self,user,action,value=None):
        if not FamilyStore(self.config).user(user):raise ValueError('Select a family user first.')
        with storage_lock(self.config):
            data=self.read();old=data.get(user,{'started':False,'revision':0})
            if action=='start':row=old if old['started'] else {'started':True,'revision':old['revision']+1,'diagram':{'nodes':[{'id':'network-fee','x':400,'y':330}],'edges':[]}}
            elif action=='reset':row={'started':False,'revision':old['revision']+1}
            elif action=='save':
                if not old['started'] or value.get('revision')!=old['revision']:raise ValueError('Salary diagram changed in another browser. Reopen it before saving.')
                row={'started':True,'revision':old['revision']+1,'diagram':clean_diagram(value.get('diagram'))}
            else:raise ValueError('Unknown salary calculation action.')
            data[user]=row;raw=json.dumps(data)
            if len(raw)>16*1024*1024:raise ValueError('Salary diagram storage is full.')
            self.write(data);return row
