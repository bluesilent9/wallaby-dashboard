"""Apply real Dashboard snapshots to three isolated companion Agent stores."""
import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from approval_store import Store
from sync_queue import apply_snapshot,rows
value=json.loads(Path(sys.argv[2]).read_text());result=[]
with tempfile.TemporaryDirectory() as folder:
 for node in value:
  store=Store(Path(folder)/node['node']);counts=[]
  for snapshot in node['snapshots']:
   apply_snapshot(store,snapshot['revision'],snapshot['requests']);visible=rows(store)
   counts.append({'rows':len(visible),'send':sum(r['direction']=='send' for r in visible),'receive':sum(r['direction']=='receive' for r in visible)})
  result.append({'node':node['node'],'counts':counts,'ids':sorted(r['id'] for r in rows(store))})
print(json.dumps(result))
