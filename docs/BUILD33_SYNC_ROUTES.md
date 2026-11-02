# Build 33: stable node identity, changing network routes

The supplied Dashboard 32 and Agent 28 logs show two separate failures: `peer_changed:22` before profile clearing, then repeated Agent `exchange_sync_revision` failures when the cleared Dashboard starts again at revision zero. Node refresh reads blockchain status; that is not evidence of complete wallet synchronisation.

## Dashboard changes

NodeRegistry continues to match the reported MAC address to its retained node ID. Hostnames are display information. IPv6 addresses are transport endpoints, never wallet/node identity. A pending public invitation whose endpoint changes receives a fresh transfer ID and challenge for the same node IDs. Old proposals and signed reviews are not mutated. Both peers must approve locally and obtain new bound signatures; the Dashboard does not execute a wallet transfer.

Automatic renewal requires unique live peers, current complete inventories, the source policy still present, the destination policy missing, current receiving permission, ready blockchain state and no known busy synchronisation. Recovery, complete or uncertain terminal operations are not automatically restarted. Historical attempts remain addressable. A late successful/delivery-recovery result takes priority and prevents a replacement invitation from proceeding until reconciled.

Once a connection has supplied signed permissions, a routine refresh delay keeps its most recent verified offer choice instead of withdrawing pending invitations. A reconnect still needs a new verified permission response; explicit disable choices remain effective. The Agent independently filters its current local exclusions and is the sole approval authority.

Ownership jobs are separate from sync pairing. Discovery never navigates the browser. Only a foreground job still being followed on Terminal Activity returns to Ownership/Wallets. Access changes render a local explanation rather than forcibly navigating to Ownership. Notifications are yellow, dismissible, linked to Ownership, regenerated while wallets remain unassigned, and removed once those assignments are present. Nodes explicitly reports checking/permissions/outstanding requests separately from blockchain status.

## Responsiveness

Profiling the three-node/13-wallet fixture showed a detailed Nodes snapshot taking 4.65 seconds under the profiler: 628 repeated public-policy identity derivations dominated the work while shared locks were held. A bounded cache now reuses only exact canonical public records after full descriptor validation, while modified data is revalidated. Keys are limited to 32 KiB and the cache to 512 entries; larger records are processed without caching. Empty local exclusion lists do not rebuild wallet-name tables, and status checks reuse already-computed summaries. The same profiled fixture takes 0.15 seconds after the change. This is an isolated measurement, not a timing guarantee for production/network traffic. Background discovery retains idle RPC priority and yields to foreground work.

## Companion Agent

Agent Build 29 scopes queue revisions to the authenticated Dashboard session and restores locally dismissed pending requests from an unchanged authoritative snapshot. Profile clearing is not a replay-authority grant: local approvals and operation records remain Agent-owned. These changes are implemented in the Agent chat, not this Dashboard source tree.

## Validation

The regression suite includes request delivery without family users/owners, MAC identity retention through hostname changes, changed IPv6 endpoints, fresh challenge/identity creation, real Agent verification of replacement signatures, preservation of an active older operation and refusal of parallel local selection, and late old result precedence. Real Agent queue storage accepts revision zero in a new session while rejecting rollback within the same session and retired sessions. Browser polling tests verify no navigation theft while explicitly followed foreground completion still returns to Ownership. No production wallet files, nodes or configuration were modified.
