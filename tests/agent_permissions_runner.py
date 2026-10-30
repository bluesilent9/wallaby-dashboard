"""Exercise the Agent's actual permission request validator and record generation."""
import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from approval_store import Store
from vault_policy import validate_proposal
from sync_preferences import exchange,policy_id
from setup_messages import sign,verify
v=json.loads(Path(sys.argv[2]).read_text());validate_proposal(v['request'])
with tempfile.TemporaryDirectory() as tmp:
 s=Store(Path(tmp));__import__('shutil').copy2(v['identity'],s.root/'preview_identity');__import__('shutil').copy2(v['identity']+'.pub',s.root/'preview_identity.pub');exchange(s,v['request']);out=next(r for r in s.read()['outbox'].values() if r['kind']=='wallet_sync_preferences')['payload'];m=verify(s,out['snapshot'],out['snapshot']['public_key'],namespace='wallaby-sync-preferences-v1');print(json.dumps({'payload':out,'policy_id':policy_id({'public_wallet':v['wallet']})}))
