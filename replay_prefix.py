"""Conservative in-memory checkpoints for unchanged annual scoring prefixes.

Only score replays use these; final reports always replay in full. Cache keys
include active graph memberships, exact prefix events and the matching lookahead.
No checkpoint or allocation is persisted to disk.
"""
from copy import deepcopy
from datetime import timedelta

class PrefixCache:
    def __init__(self,core,entries,boundary):
        self.core=core;self.entries=entries;self.boundary=boundary;self.key=None
    def prepare(self,engine,events):
        c=self.core
        lookahead=self.boundary+timedelta(hours=c.MATCH_HOURS,seconds=1)
        def rows():
            for name,values in engine.wallets.items():
                for r in values:
                    if c.utc_time(r.dt)<=lookahead:yield repr(r)
        prefix=tuple(repr(e) for e in events if c.utc_time(e.when)<self.boundary)
        memberships=tuple(tuple(sorted(getattr(engine,key) or (),key=repr)) for key in ('active_wallet_rows','primary_wallet_rows','active_bitaroo_rows','primary_bitaroo_rows'))
        self.key=(prefix,tuple(rows()),memberships,tuple(sorted(engine.ownership.wallet_statuses.items())))
        saved=self.entries.get(self.key)
        if saved is None:return False
        state,fees,opening,ownership=deepcopy(saved)
        engine.__dict__.update(state);engine.reports.fee_rows=fees;engine.reports.opening_lots=opening
        engine.ownership.__dict__.update(ownership);engine.ownership.bind_wallets(engine.wallets)
        return True
    def save(self,engine):
        if self.key in self.entries:return
        inputs={'wallets','reports','ownership','active_wallet_rows','active_bitaroo_rows','primary_wallet_rows','primary_bitaroo_rows','_joint_groups','basis_engine_class','synthetic_accounts','scoring_prefix','visual_context'}
        state={k:v for k,v in engine.__dict__.items() if k not in inputs}
        ownership={k:v for k,v in engine.ownership.__dict__.items() if k!='_wallets'}
        self.entries[self.key]=deepcopy((state,engine.reports.fee_rows,engine.reports.opening_lots,ownership))
