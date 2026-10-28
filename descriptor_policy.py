"""Core public descriptor compatibility, including migrated combo policies."""
import re
_PRIVATE=re.compile(r'\b[xtyzuYZUV]prv[1-9A-HJ-NP-Za-km-z]+|(?<=[(\s])[5KLc9][1-9A-HJ-NP-Za-km-z]{50,51}(?=[/)\s])')
from vendor.embit.descriptor import Descriptor
from vendor.embit import script
from vendor.embit.networks import NETWORKS
from vendor.embit.base import EmbitError

class AddressPolicy:
    """A fixed mainnet script for monitoring; never a spend/change policy."""
    keys=();is_wildcard=False;num_branches=1;taproot=False;miniscript=None
    def __init__(self,address):
        self.address=address
        try:self.output=script.Script.from_address(address)
        except (ValueError,EmbitError):raise ValueError('A valid Bitcoin mainnet address is required.') from None
        if self.output.address(NETWORKS['main'])!=address:raise ValueError('A canonical Bitcoin mainnet address is required.')
    def script_pubkey(self):return self.output

def parse_policy(body):
    if body.startswith('addr(') and body.endswith(')'):
        return AddressPolicy(body[5:-1]),False,False
    combo=body.startswith('combo(') and body.endswith(')')
    parsed='pkh('+body[6:-1]+')' if combo else body
    # embit has no hardened-wildcard parser. Preserve the original descriptor;
    # this substitution is for structural validation only, never derivation.
    suffixes=re.sub(r'\[[^]\n]*\]','',parsed)
    hardened=bool(re.search(r"(?:/|<|;)(?:[0-9]+|\*)[h'](?=[/);>]|$)",suffixes))
    if hardened:
        parsed=parsed.replace('*h','*').replace("*'",'*')
        parsed=re.sub(r"([0-9]+)[h'](?=[/);>]|$)",r'\1',parsed)
    d=Descriptor.from_string(parsed)
    return d,combo,hardened

def needs_history(wallet):
    return any(parse_policy(r['desc'].split('#')[0])[2] for r in wallet['raw']['descriptors'])

def output_scripts(derived,combo=False):
    if not combo:return [derived.script_pubkey()]
    pub=derived.key.get_public_key();sec=pub.sec()
    outputs=[script.Script(bytes([len(sec)])+sec+b'\xac'),script.p2pkh(pub)]
    if len(sec)==33:outputs.extend([script.p2wpkh(pub),script.p2sh(script.p2wpkh(pub))])
    return outputs
