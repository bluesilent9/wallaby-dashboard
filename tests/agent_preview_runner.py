"""Separate process keeps the Agent and Dashboard modules isolated from each other."""
import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from approval_store import Store,digest
from wallet_preview import sign,verify,receive
from peer_wallet import active
value=json.loads(Path(sys.argv[3]).read_text())
with tempfile.TemporaryDirectory() as folder:
    store=Store(Path(folder));store.receive(value['proposal']);key,row=active(store)
    if sys.argv[2]=='sign':
        payload=sign(store,value['preview']);proof=verify(store,payload)
        print(json.dumps({'payload':{'request_hash':key,**payload},'proof':proof}))
    else:
        proof=receive(store,value['payload']);print(json.dumps({'proof':proof,'stored':store.read()['transfer_previews'][key]}))
