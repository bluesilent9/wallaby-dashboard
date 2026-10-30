"""Private UTF-8 operational logs: seven UTC days, bounded daily storage.

Diagnostics are deliberately separate from saved profile/tax evidence. Reads and
writes both prune expired days, including after a service restart.
"""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import re,threading,time

class DiagnosticLog:
    DAYS=7
    DAY_BYTES=1024*1024
    def __init__(self,root,clock=time.time):
        self.root=Path(root);self.clock=clock;self.lock=threading.RLock()
        with self.lock:self.prune()
    def prune(self):
        cutoff=datetime.fromtimestamp(self.clock(),timezone.utc).date()-timedelta(days=self.DAYS-1)
        if not self.root.exists():return
        for path in self.root.glob('diagnostics-*.log'):
            m=re.fullmatch(r'diagnostics-(\d{4}-\d{2}-\d{2})\.log',path.name)
            if m and m[1]<cutoff.isoformat():path.unlink(missing_ok=True)
    def append(self,source,message):
        with self.lock:
            self.prune();self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            now=datetime.fromtimestamp(self.clock(),timezone.utc);path=self.root/('diagnostics-'+now.strftime('%Y-%m-%d')+'.log')
            # One record per line; neither command polls nor credential payloads
            # are logged. Length bounds also contain repeated malformed errors.
            clean=lambda v:' '.join(str(v).split())[:1600]
            raw=(now.strftime('%d/%m/%Y %H:%M:%S UTC')+' · '+clean(source)+' · '+clean(message)+'\n').encode('utf-8')
            if path.exists() and path.stat().st_size+len(raw)>self.DAY_BYTES:
                with path.open('rb') as file:
                    file.seek(max(0,path.stat().st_size-self.DAY_BYTES//2));file.readline();tail=file.read(self.DAY_BYTES//2)
                path.write_bytes(tail)
            with path.open('ab') as file:file.write(raw)
            path.chmod(0o600)
    def text(self):
        with self.lock:
            self.prune()
            if not self.root.exists():return ''
            chunks=[]
            for path in sorted(self.root.glob('diagnostics-*.log')):
                if not re.fullmatch(r'diagnostics-\d{4}-\d{2}-\d{2}\.log',path.name):continue
                with path.open('rb') as file:
                    size=path.stat().st_size
                    if size>self.DAY_BYTES:file.seek(size-self.DAY_BYTES);file.readline()
                    chunks.append(file.read(self.DAY_BYTES).decode('utf-8',errors='replace'))
            return ''.join(chunks)
