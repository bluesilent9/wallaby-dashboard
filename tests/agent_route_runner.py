import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from approval_store import Store,digest
from sync_queue import apply_snapshot,choose
from wallet_preview import receive
v=json.loads(Path(sys.argv[2]).read_text())
with tempfile.TemporaryDirectory() as folder:
 s=Store(Path(folder));old=v['old'];new=v['new'];apply_snapshot(s,old['revision'],old['requests']);receive(s,v['old_preview']);key=digest(old['requests'][0])
 with s.edit() as state:state['active_transfer']=key
 apply_snapshot(s,new['revision'],new['requests']);receive(s,v['new_preview']);blocked=False
 try:choose(s,digest(new['requests'][0]),True,True)
 except ValueError:blocked=True
 state=s.read();print(json.dumps({'active_preserved':state['active_transfer']==key and state['pending'][key]['withdrawn'],'new_preview':state['transfer_previews'][digest(new['requests'][0])]==v['new_preview'],'blocked_parallel':blocked}))
