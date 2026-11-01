"""Explain accounting references without mistaking them for blockchain IDs."""
import re

def source_label(reference):
    text=str(reference or '')
    if text.startswith('ACCOUNT:'):
        match=re.fullmatch(r'ACCOUNT:([^:]+):(.+):(\d+):(\d+)',text)
        if match:
            _,path,page,row=match.groups();name=path.replace('\\','/').rsplit('/',1)[-1]
            return f'Bitaroo statement: {name} · page {page} · account record {int(row)+1} (statement evidence, not a TXID)'
        return 'Bitaroo statement transfer (statement evidence, not a TXID)'
    if text.startswith(('ODS:','PDF:')):
        kind,path=text.split(':',1)
        return kind+':'+path.replace('\\','/').rsplit('/',1)[-1]
    return text
