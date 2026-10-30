"""Check exact public messages against the companion Agent's independent validators."""
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from setup_policy import proposal,policy
from setup_messages import verify
from setup_completion import apply
value=json.loads(Path(sys.argv[2]).read_text());row=value['row'];p=row['proposal'];proposal(p);envelopes=[row['contributions'][r['id']] for r in sorted(p['cosigners'],key=lambda r:r['id'])]
class Store:
 root=Path(sys.argv[2]).parent
 def read(self):return {'approved_wallets':{row['policy_hash']:{'proposal':p,'receive_descriptor':row['descriptors'][0],'change_descriptor':row['descriptors'][1]}}}
s=Store();messages=[verify(s,e,e['public_key']) for e in envelopes];assert policy(p,messages)==row['descriptors'];proposal(value['completion'])
# Stub only Core mutation. The Agent must authenticate all completion proofs first.
from unittest.mock import patch
with patch('setup_completion.mark'),patch.object(Store,'edit',create=True,return_value=__import__('contextlib').nullcontext({'approved_wallets':{row['policy_hash']:{}}})):assert apply(None,s,value['completion'])
print(json.dumps({'policy_matches':True,'completion_authenticated':True}))
