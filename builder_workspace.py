"""Single-creator builder workspaces and receipt-tracked local cleanup requests.

A receipt proves durable delivery only. Dashboard never deletes node artifacts,
requests secrets, or treats delivery as local approval/deletion confirmation.
"""
import copy,hashlib,json,os,re,threading,uuid
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from storage import storage_lock,data_directory

LOCK=threading.RLock()
KINDS={'wallet':'Wallet Builder','transaction':'Transaction Builder'}
def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
def serialised(fn):
 def wrapped(*args,**kwargs):
  with LOCK:return fn(*args,**kwargs)
 return wrapped

def clean_draft(kind,value):
 from agent_rpc import clean_json
 if not isinstance(value,dict):raise ValueError('Invalid builder draft.')
 value,_=clean_json(value,2*1024*1024)
 allowed={'name','type','threshold','count','roles','vault'} if kind=='wallet' else {'draftId','nodes','edges','template','imported_id'}
 if set(value)-allowed:raise ValueError('Unexpected builder draft fields.')
 if kind=='transaction' and 'nodes' in value:
  from transaction_templates import clean_diagram
  clean=clean_diagram({'nodes':value['nodes'],'edges':value.get('edges',[])})
  value.update(clean)
 if kind=='wallet':
  if value.get('type','0') not in ('0','1','2','3'):raise ValueError('Invalid wallet policy choice.')
  if not isinstance(value.get('roles',[]),list) or len(value.get('roles',[]))>15:raise ValueError('Invalid signer drafts.')
 return value

class BuilderWorkspace:
 def __init__(self,config):self.config=Path(config);self.path=data_directory(config)/'builder-workspaces.json'
 def read(self):
  s=json.loads(self.path.read_text()) if self.path.exists() else {'version':1,'workspaces':{},'cancellations':{}}
  # The operation contains its workspace identity in the same atomic write as
  # its artifacts. Recover attachment if the server stopped before attach().
  for kind,w in s['workspaces'].items():
   path=data_directory(self.config)/('wallet-setups.json' if kind=='wallet' else 'transactions/workflows.json')
   rows=json.loads(path.read_text()).get('sessions' if kind=='wallet' else 'jobs',{}) if path.exists() else {}
   for ident,row in rows.items():
    if row.get('workspace_id')==w['id'] and ident not in w['operations']:w['operations'].append(ident)
  return s
 def write(self,s):
  raw=json.dumps(s,ensure_ascii=True,allow_nan=False)
  if len(raw)>32*1024*1024:raise ValueError('Builder workspace storage is full.')
  self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_suffix('.tmp');tmp.write_text(raw);tmp.chmod(0o600)
  with tmp.open('rb') as f:os.fsync(f.fileno())
  os.replace(tmp,self.path)
 @serialised
 def start(self,user,kind):
  if kind not in KINDS:raise ValueError('Unknown builder.')
  renames=data_directory(self.config)/'wallet-renames.json'
  if renames.exists() and any(o['state']=='active' for o in json.loads(renames.read_text())['operations'].values()):raise ValueError('Finish or cancel pending wallet renames before starting a build.')
  with storage_lock(self.config):
   s=self.read();row=s['workspaces'].get(kind)
   if row:
    if row['owner_id']!=user or row.get('cancellation_id'):raise ValueError('Another builder process is already active.')
    return copy.deepcopy(row)
   row={'id':uuid.uuid4().hex,'kind':kind,'owner_id':user,'created_utc':stamp(),'revision':0,'draft':{},'operations':[]};s['workspaces'][kind]=row;self.write(s);return copy.deepcopy(row)
 def owned(self,user,kind,ident=None):
  row=self.read()['workspaces'].get(kind)
  if not row or row['owner_id']!=user or ident is not None and row['id']!=ident:raise ValueError('This builder belongs to another user or is no longer active.')
  if row.get('cancellation_id'):raise ValueError('Cancellation delivery is still in progress.')
  return row
 @serialised
 def save(self,user,kind,data):
  clean=clean_draft(kind,data.get('draft'))
  with storage_lock(self.config):
   row=self.owned(user,kind,data.get('id'));s=self.read();row=s['workspaces'][kind]
   if row['revision']!=data.get('revision'):raise ValueError('Builder changed in another browser. Reload it before editing.')
   if kind=='wallet' and row.get('renewal'):
    prior=row['draft'];r=row['renewal']
    if any(clean.get(k)!=prior.get(k) for k in ('name','type','threshold','count')) or len(clean.get('roles',[]))!=len(r['roles']):raise ValueError('Renewal preserves the existing wallet name, quorum and signing roles.')
    for proposed,fixed in zip(clean['roles'],r['roles']):
     if proposed.get('name')!=fixed['name'] or fixed['node_locked'] and proposed.get('node_id')!=fixed['node_id']:raise ValueError('The existing signing role cannot be replaced during a vault refresh.')
   row['draft']=clean;row['revision']+=1;row['updated_utc']=stamp();self.write(s);return copy.deepcopy(row)
 @serialised
 def attach(self,user,kind,operation):
  with storage_lock(self.config):
   self.owned(user,kind);s=self.read();row=s['workspaces'][kind]
   if operation not in row['operations']:row['operations'].append(operation);self.write(s)
 @serialised
 def finish(self,user,kind):
  with storage_lock(self.config):
   self.owned(user,kind);s=self.read();s['workspaces'].pop(kind);self.write(s)
 def snapshot(self,user,kind,registry=None):
  from preferences import FamilyStore
  if kind not in KINDS:raise ValueError('Unknown builder.')
  s=self.read();row=s['workspaces'].get(kind);users={u['id']:u['name'] for u in FamilyStore(self.config).snapshot()['users']}
  if not row:return {'workspace':None,'blocked':False,'cancellation':None}
  blocked=row['owner_id']!=user;public={k:copy.deepcopy(v) for k,v in row.items() if not blocked or k in ('id','kind','owner_id','created_utc')};public['owner_name']=users.get(row['owner_id'],'another user')
  result={'workspace':public,'blocked':blocked,'cancellation':None}
  if row.get('cancellation_id'):
   batch=s['cancellations'][row['cancellation_id']];connected={n['id'] for n in registry.snapshot()['nodes'] if n['connected']} if registry else set();targets=[]
   for node,t in batch['targets'].items():
    received=bool(t.get('received_utc'));online=node in connected;targets.append({'node_id':node,'hostname':t['hostname'],'status':'Complete' if received else 'Agent online, sending request' if online else 'Agent offline','colour':'green' if received else 'amber' if online else 'red'})
   result['cancellation']={'id':row['cancellation_id'],'targets':targets,'complete':all(t.get('received_utc') for t in batch['targets'].values())}
  return result
 @serialised
 def cancel(self,hub,user,kind,confirmed=False):
  if confirmed is not True:raise ValueError('Confirm cancellation first.')
  s=self.read();row=s['workspaces'].get(kind)
  if not row or row['owner_id']!=user:raise ValueError('Only the builder creator can cancel it.')
  if row.get('cancellation_id'):return self.snapshot(user,kind,hub.registry)
  targets={};requests=hub.read()['requests'];setups=hub.wallet_builder.read()['sessions'];exports=hub.read()['exports']
  nodes={n['id']:n['hostname'] for n in hub.registry.snapshot()['nodes']}
  def add(node,operation,request_hash=None):
   t=targets.setdefault(node,{'hostname':nodes.get(node,node),'operations':[],'request_hashes':[],'exports':[]})
   if operation not in t['operations']:t['operations'].append(operation)
   if request_hash and request_hash not in t['request_hashes']:t['request_hashes'].append(request_hash)
  if kind=='wallet':
   for operation in row['operations']:
    setup=setups.get(operation,{})
    for node in setup.get('delivered_nodes',[]):add(node,operation,operation)
  else:
   for key,r in requests.items():
    if r.get('job_id') in row['operations'] and r.get('delivered'):add(r['node_id'],r['job_id'],key)
   for key,r in requests.items():
    if r.get('workspace_id')==row['id'] and r.get('kind')=='wallet_policy_refresh' and r.get('delivered'):add(r['node_id'],r['destination_id'].removeprefix('receive-'),digest(r['proposal']))
   imported=row.get('draft',{}).get('imported_id')
   if imported and imported in exports:add(exports[imported]['node_id'],imported)
   # Include imported proposals incorporated in any prepared job.
   from transaction_workflows import WorkflowStore
   jobs=WorkflowStore(self.config)._read()['jobs']
   for operation in row['operations']:
    job=jobs.get(operation,{})
    for export_id,e in exports.items():
     if job.get('vault_export_id')==export_id:add(e['node_id'],export_id)
  for t in targets.values():
   for operation in t['operations']:
    if operation in exports:
     e=exports[operation];t['exports'].append({'outbox_id':e['outbox_id'],'txid':e['txid'],'psbt_sha256':hashlib.sha256(__import__('base64').b64decode(e['psbt'],validate=True)).hexdigest()})
  batch_id=uuid.uuid4().hex
  for node,t in targets.items():
   p={'kind':'builder_cancel','version':1,'builder':kind,'workspace_id':row['id'],'cancellation_id':batch_id,'operations':sorted(t['operations']),'request_hashes':sorted(t['request_hashes']),'exports':t['exports'],'require_local_authorisation':True,'backup_option':True}
   p['request_id']=digest(p);t['proposal']=p;t['request_hash']=digest(p)
  # Stop Dashboard coordination durably before withdrawing any original requests.
  with storage_lock(self.config):
   s=self.read();current=s['workspaces'][kind];current['cancellation_id']=batch_id;s['cancellations'][batch_id]={'kind':kind,'workspace_id':row['id'],'owner_id':user,'created_utc':stamp(),'operations':list(row['operations']),'targets':targets,'complete':not targets}
   if not targets:s['workspaces'].pop(kind)
   self.write(s)
  self.reconcile(hub)
  return self.snapshot(user,kind,hub.registry)
 @serialised
 def reconcile(self,hub):
  # Resume withdrawal after a crash between durable cancellation and updates to
  # separate job stores, before any Agent can receive the original work again.
  for ident,batch in self.read()['cancellations'].items():
   if batch.get('withdrawn'):continue
   operations=batch.get('operations',[]);user=batch['owner_id']
   if batch['kind']=='transaction':
    from builder_destinations import Destinations
    destinations=Destinations(self.config)
    with storage_lock(self.config):
     state=destinations.read()
     for d in state['destinations'].values():
      if d.get('workspace_id')==batch['workspace_id']:d['policy_requests_started']=False
     if state['destinations']:destinations.write(state)
   if batch['kind']=='wallet':
    with hub.wallet_builder.edit() as state:
     for operation in operations:
      if operation in state['sessions']:state['sessions'][operation].update(state='cancelled',message='Cancelled on Dashboard. Local Agent cleanup requires separate authorisation.')
   else:
    from transaction_workflows import WorkflowStore
    store=WorkflowStore(self.config)
    for operation in operations:
     job=store._read()['jobs'].get(operation)
     if job and not job.get('completed') and not job.get('cancelled'):store.cancel(user,operation,reason='cancelled')
    with hub.edit() as state:
     for request in state['requests'].values():
      if (request.get('job_id') in operations or request.get('workspace_id')==batch['workspace_id']) and request.get('state')=='pending':request.update(state='cancelled',message='Dashboard build cancelled; local Agent cleanup requires authorisation.')
    for key,lease in list(hub.leases.items()):
     if lease['user_id']==user:hub.leases.pop(key,None)
   with storage_lock(self.config):
    s=self.read();s['cancellations'][ident]['withdrawn']=True;self.write(s)
 @serialised
 def proposals(self,node):
  with storage_lock(self.config):
   s=self.read();out=[]
   for batch in s['cancellations'].values():
    t=batch['targets'].get(node)
    if not t or t.get('received_utc'):continue
    t['sent_utc']=stamp();out.append(copy.deepcopy(t['proposal']))
   if out:self.write(s)
   return out
 @serialised
 def receipt(self,node,payload):
  if not isinstance(payload,dict) or set(payload)!={'version','cancellation_id','request_hash','status'} or type(payload['version']) is not int or payload['version']!=1 or payload['status']!='received':raise ValueError('Invalid cancellation delivery receipt.')
  with storage_lock(self.config):
   s=self.read();batch=s['cancellations'].get(payload['cancellation_id']);t=batch.get('targets',{}).get(node) if batch else None
   if not t or t['request_hash']!=payload['request_hash'] or not t.get('sent_utc'):raise ValueError('Cancellation receipt does not match a request delivered to this Agent.')
   t.setdefault('received_utc',stamp());batch['complete']=all(v.get('received_utc') for v in batch['targets'].values())
   row=s['workspaces'].get(batch['kind'])
   if batch['complete'] and row and row.get('cancellation_id')==payload['cancellation_id']:s['workspaces'].pop(batch['kind'])
   self.write(s)

def validate(state,users):
 from agent_rpc import clean_json
 clean_json(state,32*1024*1024)
 if not isinstance(state,dict) or set(state)!={'version','workspaces','cancellations'} or state['version']!=1:raise ValueError('Invalid builder workspace archive.')
 if set(state['workspaces'])-KINDS.keys() or len(state['cancellations'])>10000:raise ValueError('Invalid builder workspace bounds.')
 for kind,w in state['workspaces'].items():
  if w.get('owner_id') not in users or not re.fullmatch('[0-9a-f]{32}',str(w.get('id',''))) or w.get('kind')!=kind or type(w.get('revision')) is not int or w['revision']<0:raise ValueError('Invalid builder owner or identity.')
  clean_draft(kind,w.get('draft'));c=w.get('cancellation_id')
  if c and (c not in state['cancellations'] or state['cancellations'][c]['workspace_id']!=w['id']):raise ValueError('Missing builder cancellation.')
 for ident,b in state['cancellations'].items():
  if b.get('kind') not in KINDS or b.get('owner_id') not in users or not re.fullmatch('[0-9a-f]{32}',ident) or len(b.get('targets',{}))>256:raise ValueError('Invalid cancellation archive.')
  for node,t in b['targets'].items():
   p=t['proposal'];body=dict(p);request_id=body.pop('request_id',None)
   if t['request_hash']!=digest(p) or request_id!=digest(body) or p['cancellation_id']!=ident or p['workspace_id']!=b['workspace_id'] or p['require_local_authorisation'] is not True or p['backup_option'] is not True:raise ValueError('Invalid archived cancellation request.')
   if t.get('received_utc') and not t.get('sent_utc'):raise ValueError('Unsent cancellation cannot have a receipt.')
 return state
