# Required retirement update for Dashboard Build 18

Build 18 changes Make Extinct into a zero-funds archival-and-deletion request. Dashboard checks fresh Core balances on connected replicas, including known related vault monitors and multisig signing accounts. Pending, immature, watch-only or locked funds block retirement; unavailable balances and scans also block it. The Agent must independently repeat the checks and obtain approval on its local Authorise page before changing anything.

Dashboard requires the new `wallet_retirement_v2` Agent capability. Agent Build 18 implements the matching local operation. Earlier archive-only Agents cannot execute this request; upgrade each holder before it can confirm retirement. Dashboard never deletes wallet files, unlocks wallets or invokes mutating Core RPCs.

Agent completion must explicitly confirm verified historical archival, complete history, zero funds and deletion of every listed local wallet account. Cancelled, denied and failed requests remain flagged. After every known holder confirms archival and deletion, the initiating user confirms that archival discs were destroyed or that none existed; this is a recorded user statement, not a physical-media verification. Requests cover every known private and watch-only holder. Each Agent needs separate local approval. Offline or unsupported holders remain pending, with durable per-holder completion tracking; one node's success cannot complete the group. Retirement records survive profile backup/restore, and normal sync is suppressed for policies being retired or already retired.

Agent Build 18 implements the version 2 retirement operation. No Agent code is changed by this Dashboard build.

# Agent follow-up after Dashboard V1 Build 13

Dashboard Build 13 requires no new Agent protocol. Its changes are presentation, freshness handling, fee review and removal of obsolete current-UI manual-signing screens. The user explicitly keeps the existing signature collection and broadcast workflow.

For a later Agent update:

1. **FEE-01:** label the fee in the local transaction diagram “Total network fee”. Do not label the full joint fee as paid from the currently signing wallet. The total comes from verified inputs minus outputs; the blockchain does not assign each wallet a fee share.
2. Native signing passphrase prompts should name the exact locally verified Core wallet and action. Clearly distinguish Core wallet passphrases, disc-backup passphrases and peer computer login passwords. Do not take the authoritative wallet name from a Dashboard display label.
3. Compare one independently checked fingerprint per distinct producing Agent within a wallet ceremony, then bind all its roles to that verified identity. Keep full fingerprints and exact policy/address checks; do not accept a remote “already verified” claim.
4. Add an independent local high-fee review/acknowledgement based on the final Core-verified transaction. Dashboard warns at a fee of at least 10% of planned payments or a rate of at least 100 sats/vB. Those browser thresholds and acknowledgement do not authorise the Agent. The Agent must determine its own meaningful denominator for payments/change; do not infer it blindly from Dashboard labels. Keep the ability to deliberately approve a high fee.
5. Keep unsent, pending, attempted/outcome-uncertain and completed statuses clear. Never automatically repeat irreversible operations after lost responses, failed burns or restarts.
6. After checking callers, remove retired full-wallet Vault Builder entry points/helpers and obsolete tests. Preserve current Wallet Builder creation, policy checks, archive restoration and PSBT signing capabilities.

Do not implement withholding the last signature or a new broadcast security barrier: the user declined that change. No Agent files or repository were changed by Dashboard Build 13.

Physical optical media and the installed protected Qt/systemd/AppArmor/Snap lifecycle still require deployment testing. No new guarantee about hardware or operating-system behaviour is implied.

## Dashboard Build 19: cancellation requests

The creator may cancel a retirement group. Dashboard stops queuing new holder requests. Undelivered requests can be marked cancelled locally. Delivered requests remain awaiting an actual Agent outcome; a Dashboard click never establishes that deletion was stopped or reversed. Agent Build 18 supports local denial but does not implement remote cancellation.

A future Agent implementing `wallet_retirement_cancel_v1` receives `{kind:"wallet_retire_cancel",version:1,request_hash:ORIGINAL_HASH}`. It should withdraw the matching pending approval safely and reply using `wallet_retirement_result` with the original hash and `status:"cancelled"` only when execution was stopped. Already-completed deletion must still report its real result. An in-flight uncertain operation must not be reported as cancelled. Agents without this capability receive no incompatible cancellation proposal; users can deny the original request locally.

## Pending-block data failure: Agent follow-up

The local Agent `node_client.NodeClient.execute` replaces every exception from Core with `local_rpc_failed`. The supplied Dashboard log confirms that this is the response for getrawmempool verbose, while smaller fee/header calls succeed. Failures occur in about a second, not near the 15-second Dashboard deadline. Inspect `Core.call`, `http_json` response bounds (16 MiB), `clean_json` result bounds (16 MiB), the protected-worker transport (20 MiB), and exact sanitized exception categories. Do not expose raw RPC credentials or unrestricted responses in logs. Reproduce with a realistic large mempool, including ancestor/dependency information, and preserve bounded transport. Dashboard cannot reconstruct exact pending packages from getmempoolinfo alone. No replacement Agent operation has been invented in this build.
