"""Exercise the installed companion source in isolation, without a live node."""
import sys,json,tempfile,copy
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from approval_store import Store,digest
from sync_queue import apply_snapshot
from wallet_preview import receive
value=json.loads(Path(sys.argv[2]).read_text())
with tempfile.TemporaryDirectory() as folder:
    store=Store(Path(folder));snapshot=value['snapshot'];apply_snapshot(store,snapshot['revision'],snapshot['requests']);before=store.read();apply_snapshot(store,snapshot['revision'],snapshot['requests']);stable=before==store.read();rejected=False
    try:apply_snapshot(store,max(0,snapshot['revision']-1),[])
    except ValueError:rejected=True
    receive(store,value['payload']);count=len(store.read()['pending']);apply_snapshot(store,snapshot['revision']+1,[])
    print(json.dumps({'stable':stable,'stale_rejected':rejected,'count':count,'withdrawn':len(store.read()['pending'])}))
