"""Storage names and resumable migration of pre-V155 layouts; no source files lost."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import os
import threading

_MUTEX=threading.RLock()

@contextmanager
def storage_lock(config):
    with _MUTEX,config.with_name('.wallaby-storage.lock').open('a+b') as handle:
        try:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
        except ImportError:
            import msvcrt
            if handle.seek(0,2)==0:handle.write(b'0');handle.flush()
            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
        try:yield
        finally:
            try:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
            except NameError:
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)

def same_file(a,b):
    if a.stat().st_size!=b.stat().st_size:return False
    def digest(path):
        h=hashlib.sha256()
        with path.open('rb') as f:
            for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
        return h.digest()
    return digest(a)==digest(b)

def migrate_storage(config):
    config=Path(config).expanduser().resolve()
    old_keys=config.with_name('descriptors.txt');keys=config.with_name('descriptors.cfg')
    old_data=Path(str(config)+'.data');data=config.with_name('data')
    if not old_keys.exists() and not old_data.exists():return
    config.parent.mkdir(parents=True,exist_ok=True)
    with storage_lock(config):
        for path in (old_keys,keys,old_data,data):
            if path.is_symlink():raise ValueError('Storage migration cannot rename a symbolic link: '+str(path))
        if old_keys.exists() and not old_keys.is_file():raise ValueError('Legacy descriptors.txt must be a regular file.')
        # Check all conflicts before moving anything. Equal files are safe to deduplicate.
        if old_keys.exists() and keys.exists() and (not keys.is_file() or not same_file(old_keys,keys)):
            raise ValueError('Both descriptors.cfg and legacy descriptors.txt exist with different contents. Keep the correct collection before restarting; neither was overwritten.')
        pending=[]
        if old_data.exists():
            if not old_data.is_dir() or data.exists() and not data.is_dir():raise ValueError('The wallet data path must be a directory.')
            for source in old_data.rglob('*'):
                if source.is_symlink():raise ValueError('Storage migration cannot move symbolic links: '+str(source))
                target=data/source.relative_to(old_data)
                if target.is_symlink() or target.exists() and (source.is_dir()!=target.is_dir() or source.is_file() and not same_file(source,target)):
                    raise ValueError('Conflicting legacy and current wallet data: '+str(target)+'. Neither copy was overwritten.')
                pending.append((source,target))
        if old_keys.exists():
            if keys.exists():old_keys.unlink()
            else:os.replace(old_keys,keys)
        if old_data.exists() and not data.exists():os.replace(old_data,data)
        elif old_data.exists():
            for source,target in sorted(pending,key=lambda pair:len(pair[0].parts)):
                if source.is_dir():target.mkdir(exist_ok=True,mode=0o700)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    if target.exists():source.unlink()
                    else:os.replace(source,target)
            for path in sorted(old_data.rglob('*'),key=lambda p:len(p.parts),reverse=True):
                if path.is_dir():path.rmdir()
            old_data.rmdir()

def data_directory(config):
    config=Path(config).expanduser().resolve();migrate_storage(config)
    return config.with_name('data')
