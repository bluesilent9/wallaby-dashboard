"""Public policy relationships; a cosigner key account is not a missing wallet replica."""
from vendor.embit.descriptor import Descriptor

from functools import lru_cache

@lru_cache(maxsize=4096)
def _descriptor_keys(text):
    try:d=Descriptor.from_string(text.split('#')[0])
    except Exception:return frozenset()
    return frozenset(k.to_string().split(']')[-1].replace("'",'h') for k in d.keys)

def keys(raw):
    return set().union(*(_descriptor_keys(row['desc']) for row in raw.get('descriptors',[])))

@lru_cache(maxsize=4096)
def _shared(text):
    try:
        d=Descriptor.from_string(text.split('#')[0])
        return bool(len(d.keys)>1 or d.witness_script() and 'after(' in text)
    except Exception:return False

def shared(raw):
    return any(_shared(row['desc']) for row in raw.get('descriptors',[]))

def account_matches(raw,wanted):
    # Core's generated account also contains pkh/sh(wpkh)/tr descriptors.
    # Only its BIP84 receive/change branches are contributed to this policy.
    branches={'descriptors':[r for r in raw.get('descriptors',[]) if r['desc'].startswith('wpkh(')]}
    own=keys(branches)
    return bool(own and own<=wanted)

def signing_account(account,monitor):
    return bool(account.get('private_keys_enabled') and shared(monitor['raw']) and account_matches(account['raw'],keys(monitor['raw'])))

def intentional_account(raw,setups,monitors):
    if any(shared(m['raw']) and account_matches(raw,keys(m['raw'])) for m in monitors):return True
    # Contributions were signature-checked against the immutable creation roles.
    # Use each role separately: unrelated accounts must not be combined to match.
    return any(account_matches(raw,{e['message'][f].split(']')[-1].replace("'",'h') for f in ('receive_key','change_key')})
               for setup in setups for e in setup['contributions'].values())


def cosigner_accounts(wallets):
    monitors=[w for w in wallets if shared(w['raw'])]
    return [w for w in wallets if any(signing_account(w,m) for m in monitors if w is not m)]


def multisig_summary(raw):
    """Read thresholds from descriptor structure, never wallet names or labels."""
    from vendor.embit.descriptor.miniscript import Multi
    found=set()
    def walk(node):
        if isinstance(node,Multi):found.add((node.args[0].num,len(node.args)-1))
        for child in getattr(node,'args',[]):walk(child)
    for row in raw.get('descriptors',[]):
        try:
            d=Descriptor.from_string(row['desc'].split('#')[0]);walk(d.miniscript)
        except (ValueError,AttributeError):continue
    return [{'required':m,'total':n} for m,n in sorted(found) if n>1]
