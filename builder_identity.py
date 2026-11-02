"""Carry saved planning references across approved public-policy generations."""
import copy,json
from pathlib import Path
from builder_workspace import BuilderWorkspace,LOCK
from builder_destinations import Destinations
from preferences import resource_id
from storage import storage_lock

def migrate(config,superseded):
    if not superseded:return
    mapping={resource_id('descriptor',a):resource_id('descriptor',b) for a,b in superseded.items()}
    def translate(value):
        if isinstance(value,str):return mapping.get(value,value)
        if isinstance(value,list):return [translate(v) for v in value]
        if isinstance(value,dict):return {mapping.get(k,k):translate(v) for k,v in value.items()}
        return value
    with LOCK,storage_lock(config):
        store=Destinations(config);state=store.read()
        for d in state['destinations'].values():
            d['base']=mapping.get(d['base'],d['base']);d['base_descriptor_id']=superseded.get(d['base_descriptor_id'],d['base_descriptor_id'])
        if state['destinations']:store.write(state)
        work=BuilderWorkspace(config);s=work.read()
        for w in s['workspaces'].values():
            draft=translate(w['draft'])
            if draft!=w['draft']:
                w['draft']=draft;w['revision']+=1;w['catalog_revision']=w.get('catalog_revision',0)+1;w['identity_changes']=mapping
            if w.get('renewal'):w['renewal']=translate(w['renewal'])
        if s['workspaces']:work.write(s)
        # PSBT bytes and receipt identities are immutable. Resolve old resource
        # IDs at signing eligibility time rather than rewriting signed requests.
        path=Path(config).with_name('data')/'builder-resource-aliases.json';aliases=json.loads(path.read_text()) if path.exists() else {}
        aliases={a:mapping.get(b,b) for a,b in aliases.items()};aliases.update(mapping);tmp=path.with_suffix('.tmp');tmp.parent.mkdir(parents=True,exist_ok=True);tmp.write_text(json.dumps(aliases));tmp.chmod(0o600);tmp.replace(path)
        for p in Path(config).with_name('templates').glob('*.json'):
            t=json.loads(p.read_text());diagram=translate(t['diagram'])
            if diagram!=t['diagram']:
                t['diagram']=diagram;t['revision']+=1;tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(t));tmp.chmod(0o600);tmp.replace(p)

def resolve(config,resource):
    path=Path(config).with_name('data')/'builder-resource-aliases.json'
    return json.loads(path.read_text()).get(resource,resource) if path.exists() else resource
