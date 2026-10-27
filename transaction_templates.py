"""Shared family transaction plans. Files contain public planning data, never signatures."""
import json, os, re, uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from storage import storage_lock

class TemplateStore:
    def __init__(self,config):
        self.config=Path(config)
        self.root=self.config.parent/'templates'
    def path(self,ident):
        if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{32}',ident):raise ValueError('Invalid template ID.')
        return self.root/(ident+'.json')
    def listing(self):
        with storage_lock(self.config):
            rows=[json.loads(p.read_text(encoding='utf-8')) for p in self.root.glob('*.json')]
        return [{'id':r['id'],'name':r['name'],'updated_utc':r['updated_utc'],'revision':r['revision']} for r in sorted(rows,key=lambda r:(r['name'].casefold(),r['id']))]
    def read(self,ident):
        with storage_lock(self.config):
            p=self.path(ident)
            if not p.exists():raise ValueError('This template no longer exists.')
            return json.loads(p.read_text(encoding='utf-8'))
    def save(self,data):
        name=data.get('name');diagram=data.get('diagram')
        if not isinstance(name,str) or not name.strip() or len(name)>100:raise ValueError('Enter a template name of 1–100 characters.')
        clean=clean_diagram(diagram);clean_nodes=clean['nodes'];clean_edges=clean['edges']
        with storage_lock(self.config):
            ident=data.get('id') or uuid.uuid4().hex;p=self.path(ident);old=json.loads(p.read_text(encoding='utf-8')) if p.exists() else None
            if data.get('id') and (old is None or old['revision']!=data.get('revision')):raise ValueError('Template changed or was deleted in another browser. Reload it before saving.')
            if any(json.loads(f.read_text(encoding='utf-8'))['name'].casefold()==name.strip().casefold() for f in self.root.glob('*.json') if f!=p):raise ValueError('A template with that name already exists. Choose another name.')
            row={'id':ident,'name':name.strip(),'revision':(old['revision']+1) if old else 1,'updated_utc':datetime.now(timezone.utc).isoformat(),'diagram':{'nodes':clean_nodes,'edges':clean_edges}}
            self.root.mkdir(parents=True,exist_ok=True);temp=p.with_suffix('.tmp')
            try:
                temp.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(temp,0o600);os.replace(temp,p)
            finally:temp.unlink(missing_ok=True)
            return row
    def delete(self,ident,revision):
        with storage_lock(self.config):
            p=self.path(ident)
            if not p.exists():raise ValueError('This template no longer exists.')
            if json.loads(p.read_text(encoding='utf-8'))['revision']!=revision:raise ValueError('Template changed in another browser. Reload the list before deleting.')
            p.unlink()

def clean_diagram(diagram):
    if not isinstance(diagram,dict):raise ValueError('Invalid diagram.')
    nodes=diagram.get('nodes');edges=diagram.get('edges')
    if not isinstance(nodes,list) or not 1<=len(nodes)<=257 or not isinstance(edges,list) or len(edges)>256:raise ValueError('Invalid diagram size.')
    ids=set();clean_nodes=[];clean_edges=[]
    for n in nodes:
        if not isinstance(n,dict) or not isinstance(n.get('id'),str) or not 1<=len(n['id'])<=200 or n['id'] in ids:raise ValueError('Invalid diagram card.')
        ids.add(n['id'])
        if any(type(n.get(k)) not in (int,float) or not 0<=n[k]<=100000 for k in ('x','y')):raise ValueError('Invalid card position.')
        clean_nodes.append({k:n[k] for k in ('id','x','y')})
    if 'network-fee' not in ids:raise ValueError('The Network Fee card is required.')
    pairs=set()
    for e in edges:
        if not isinstance(e,dict):raise ValueError('Invalid connection.')
        a=e.get('from');b=e.get('to');amount=e.get('amount_sats');rate=e.get('fee_rate')
        if a not in ids or b not in ids or a==b or a=='network-fee' or (a,b) in pairs:raise ValueError('Invalid connection endpoints.')
        if not isinstance(amount,str) or not re.fullmatch(r'[0-9]{1,16}',amount) or int(amount)>2100000000000000:raise ValueError('Invalid Bitcoin amount.')
        if not isinstance(rate,str) or not re.fullmatch(r'[0-9]{1,12}(?:\.[0-9]{1,8})?',rate):raise ValueError('Invalid fee rate.')
        if b=='network-fee' and int(amount) or b!='network-fee' and Decimal(rate):raise ValueError('Use Bitcoin for payments and sats/vB for Network Fee.')
        if type(e.get('max',False)) is not bool or b=='network-fee' and e.get('max'):raise ValueError('Maximum is available only for Bitcoin payments.')
        pairs.add((a,b));clean_edges.append({'from':a,'to':b,'amount_sats':amount,'fee_rate':rate,**({'max':True} if e.get('max') else {})})
    return {'nodes':clean_nodes,'edges':clean_edges}
