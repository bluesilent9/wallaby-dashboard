# Build 31: visible stalled reviews and deliberate recovery

The supplied deployment log confirms empty outbound lists while all three nodes were present. Earlier previews and attempts survive service restarts. Expiry, changed frozen endpoints, disabled or unknown Agent permissions and earlier outcomes can correctly withhold a live transfer request; the old UI misleadingly told users to find it on the Agent anyway.

Nodes now explains those conditions. For an expired or changed *uncompleted* review, close/cancel the old attempt on both Agents and inspect Core first, then choose **Start fresh sync reviews**. One confirmation requests up to 128 distinct paired reviews. New attempts receive new identities and challenges and still require local Agent approval. No wallet file is transferred by that Dashboard action. Stale inventories, existing destination wallets, races and individual failures remain visible; a partially received wallet needs its existing history-recovery process rather than another transfer.

Supersession is coordination metadata, not a fabricated error outcome. Old attempts keep their actual status, signatures and results and remain addressable by their original request hash. A late completion or history-recovery response is retained. Superseded requests cannot become live queue entries again. Existing denial preferences are never restored from Dashboard or erased.

Diagnostics emit per-node changed blocker counts even if the actual queue remains empty, distinguishing preview expiry/change, endpoint change, peer absence, permission checks and local outcomes. Retention remains bounded. No payloads, private material or credentials are logged. Genuine hostname/MAC conflicts remain protected.

Agent V2 Build 27 fixes the separately reported close/status-refresh contention and guarded cleanup retry. This Dashboard change uses the existing Agent request protocol and does not modify Agent code.
