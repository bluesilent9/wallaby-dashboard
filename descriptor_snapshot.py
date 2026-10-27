"""Share a complete public blockchain scan across profiles without sharing ownership.

A fresh descriptor import changes the catalog fingerprint if any derivation cursor,
range or legacy public-history supplement changes. Wallet Descriptors publishes history; Ownership reuses it until the user
explicitly requests a refresh. Other callers can apply a 24-hour freshness limit. Each caller
gets an independent JSON object, so profile aliases cannot modify the shared copy.
"""
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from descriptor_store import DescriptorStore
from storage import data_directory

MAX_AGE_SECONDS = 24 * 3600

@contextmanager
def exclusive(path):
    # Serialise worker processes, including concurrent browsers and server instances.
    with path.open('a+b') as handle:
        os.chmod(path, 0o600)
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            import msvcrt
            if path.stat().st_size == 0:handle.write(b'0');handle.flush()
            handle.seek(0);msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:yield
        finally:
            try:fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except NameError:
                handle.seek(0);msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

def valid(snapshot, fingerprint, now):
    if not isinstance(snapshot, dict) or snapshot.get('catalog_fingerprint') != fingerprint:return False
    if not all(key in snapshot for key in ('wallets','transactions','coverage','timestamp_basis','source','scanned_utc')):return False
    try:
        stamp = datetime.fromisoformat(snapshot['scanned_utc'].replace('Z','+00:00'))
        age = (now - stamp).total_seconds()
    except (ValueError, TypeError, AttributeError):return False
    return 0 <= age < MAX_AGE_SECONDS

def shared_history(config, catalog, discover, *, force=False, now=None, require_existing=False, allow_stale=False):
    """Return (complete snapshot, reused); failed refreshes never publish partial data."""
    fingerprint = DescriptorStore.fingerprint(catalog)
    root = data_directory(config)/'descriptor-history'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root/(fingerprint+'.json')
    print('Checking shared descriptor history; waiting if another browser is scanning these wallets.')
    with exclusive(root/'scan.lock'):
        check_now = now or datetime.now(timezone.utc)
        if not force:
            try:
                envelope = json.loads(path.read_text(encoding='utf-8'))
                payload = envelope['payload']
                if hashlib.sha256(payload.encode()).hexdigest() == envelope['sha256']:
                    snapshot = json.loads(payload)
                    if valid(snapshot, fingerprint, check_now) or allow_stale and snapshot.get("catalog_fingerprint")==fingerprint and all(k in snapshot for k in ("wallets","transactions","coverage","timestamp_basis","source","scanned_utc")):return snapshot, True
            except (OSError, ValueError, KeyError, TypeError):pass
        if require_existing:raise ValueError('Run Load Node Wallets in Ownership and complete its blockchain tracing first.')
        snapshot = discover()
        # Validate against completion time, since discovery can take several minutes.
        if not valid(snapshot, fingerprint, max(check_now, datetime.now(timezone.utc))):
            raise ValueError('Incomplete or outdated descriptor snapshot; no shared history was saved.')
        payload = json.dumps(snapshot, ensure_ascii=True, separators=(',',':'))
        envelope = {'sha256':hashlib.sha256(payload.encode()).hexdigest(),'payload':payload}
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, prefix='.scan-', delete=False) as f:
                temp = Path(f.name);json.dump(envelope, f);f.flush();os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            if temp:temp.unlink(missing_ok=True)
        return json.loads(payload), False

def saved_history(config,catalog):
    return shared_history(config,catalog,lambda:None,require_existing=True,allow_stale=True)[0]

def history_ready(config,catalog):
    try:
        fingerprint=DescriptorStore.fingerprint(catalog)
        path=data_directory(config)/'descriptor-history'/(fingerprint+'.json')
        envelope=json.loads(path.read_text(encoding='utf-8'));payload=envelope['payload']
        if hashlib.sha256(payload.encode()).hexdigest()!=envelope['sha256']:return False
        value=json.loads(payload)
        return value.get('catalog_fingerprint')==fingerprint and all(k in value for k in ('wallets','transactions','coverage','timestamp_basis','source','scanned_utc'))
    except (OSError,ValueError,KeyError,TypeError):return False
