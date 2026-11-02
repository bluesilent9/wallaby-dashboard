import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from approval_store import Store
from sync_queue import apply_snapshot,rows
value=json.loads(Path(sys.argv[2]).read_text())
with tempfile.TemporaryDirectory() as d:
 store=Store(Path(d));p=value['proposal'];apply_snapshot(store,15,[p],session='old-session');apply_snapshot(store,0,[p],session='new-session');apply_snapshot(store,2,[p],session='new-session');rollback=retired=False
 try:apply_snapshot(store,1,[],session='new-session')
 except ValueError:rollback=True
 try:apply_snapshot(store,16,[p],session='old-session')
 except ValueError:retired=True
 print(json.dumps({'rejected_rollback':rollback,'rejected_retired':retired,'visible':len(rows(store))}))
