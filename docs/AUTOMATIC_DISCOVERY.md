# Automatic public wallet discovery

The server checks all connected, conflict-free, synchronised Core nodes. Discovery queues public read-only RPC work at priority 100 and checks for foreground work before each request. Interactive fee/Mempool reads use priority 10; foreground reports/builders retain their existing priorities. An in-flight RPC must finish or time out before it can yield.

Complete descriptor/history exports are grouped by locking policy rather than names. Unavailable nodes do not remove remembered wallets. Incomplete node exports are logged; no partial wallet ledger is published. A complete history reconstruction must succeed before a new catalog is made visible. Changed origin annotations alone preserve existing wallet identity. Export timestamps, current tip and positive confirmation-count increments alone do not invalidate saved reports.

Scanning runs outside profile/snapshot publication locks. Complete history is published atomically before its matching catalog. Browser metadata is refreshed using the catalog fingerprint as well as the saved upload generation, retaining the uploaded records. Newly discovered rows require ownership assignment. Per-user discovery notices remember already announced wallet IDs even after deletion of a notice.

No keys, signing operations, public blockchain fallback or Agent protocol extension is introduced. A missing transaction on the remaining connected pruned nodes defers the update; reconnect the node retaining the required history. Full scans repeat at idle intervals and when node availability changes.
