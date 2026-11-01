# Dashboard Build 22 — complete paged mempool projections

Agent V2 Build 20 adds the read-only `wallaby_mempool_snapshot` adapter, advertised in the existing RPC registration method list. Dashboard Build 22 allows that method and uses it exclusively for Fee Estimates projections. There is no full-response fallback and no increase to the ordinary 16 MiB remote response limit.

The supplied logs identified `local_rpc_response_limit` for 37,963 transactions at 31 October 2026 21:32:40 UTC and 38,108 at 21:33:41 UTC. Core fee priorities, quotes and mined blocks succeeded. The Agent's local capture now has a separate 64 MiB/100,000-entry limit; its public pages remain subject to existing private-material and response-size checks.

Request `wallaby_mempool_snapshot []` on one selected node. The response has exactly:

```json
{"version":1,"snapshot":"32 lowercase hex characters","created_at":1793480000,"expires_at":1793480300,"total":38000,"offset":0,"next_offset":1000,"entries":{"64-character txid":{"vsize":150,"fees":{"modified":"0.00001234"},"depends":[]}}}
```

Request each subsequent page with `{"snapshot":"same id","offset":1000}`. Offsets advance in blocks of 1,000; the final next_offset is null. An empty capture has total/offset 0, entries {}, next_offset null. A new capture replaces the Agent's one cached snapshot, which expires after 300 seconds. This is a fee-planning adapter, not a Bitcoin Core method or wallet command.

Dashboard pins the node's connection and verifies version, capture ID, timestamps, constant total, contiguous page offsets, exact page lengths, ordered unique transaction IDs, integral virtual sizes, exact decimal modified fees and dependency links. Every page must arrive before projection starts. A disconnected/replaced connection, expiry, missing page, altered total, duplicate, invalid amount or incomplete dependency graph discards the whole assembly. There is no partial graph or mixing of captures/nodes. A later scheduled refresh (two minutes) or explicit refresh creates new RPC jobs; an acknowledged job is never replayed. Background reads yield to foreground wallet/history work.

The projection accepts up to 100,000 transactions with bounded ancestry/dependency complexity; it refuses an over-complex graph explicitly instead of truncating it. Core modified fees can be negative after local fee prioritisation: a positive combined ancestor package can still be selected, but a negative standalone package is not presented as a mineable block. Empty mempools produce no fabricated pending block. These remain estimates of the local mempool, not a guarantee of miner selection or confirmation time.

Fee priorities are published before paging. The pending-block area shows collection counts while pages arrive. Failed projection leaves rates and mined blocks available with the specific projection error. A new refresh clears the old projection while collecting; its final log explicitly distinguishes complete, empty and unavailable outcomes.

Diagnostics correlate refresh ID, selected hostname/Agent version, RPC job ID, method, page offset, elapsed milliseconds, capture identifier prefix, page/entry counts, reported capture age and final outcome. Successful page progress is sampled at the first, each 10,000 entries and last page; slow calls and failures are logged individually. No transaction IDs, descriptors, addresses, payloads or credentials are written. Export includes Dashboard build, UTC export time and current Agent versions. Credential/private-key redaction is an additional safeguard. Retention stays seven UTC days, capped at 1 MiB per day.

Failure measurements preserve `boundary`, `limit_bytes`, `observed_bytes` and `observed_bytes_at_least` when supplied. The last field is a lower bound, not a complete response size. Local Core/public-result caps, Dashboard HTTP/result/validation/storage failures and authentication events retain distinct categories. No raw error.data dump is logged.

Interoperability tests use the independent Agent Build 20 snapshot formatter and HTTP NodeClient, a 38,000-entry synthetic Core fixture whose unfiltered JSON exceeds 16 MiB, all 38 pages, exact fees and dependency links. No production Core node, wallet, credentials or Agent installation is modified by these tests.
