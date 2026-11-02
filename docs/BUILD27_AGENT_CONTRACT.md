# Dashboard Build 27 / Agent V2 Build 23

All messages use the existing authenticated loopback exchange over SSH. Dashboard sends only public descriptors, metadata and PSBTs. Private wallet bytes, passphrases, RPC cookies and private descriptors never enter Dashboard.

## Archive queue

Capability `wallet_archive_queue_v1`. Proposal: `{kind:"wallet_archive_queue",version:1,queue_id,revision,requests}`. Queue ID is stable, revisions increase monotonically, and `requests` is the complete current per-Agent list, including an empty list. A disconnect or failed response is not an empty snapshot. Dashboard controls task lifetime; it can withdraw tasks but cannot confirm destruction. Agent may persistently disable archival, or delete an item locally and receive it again while Dashboard retains it.

Archive task: `{kind:"archive_wallet",version:1,nonce,request_id,wallet,public_wallet,related_policies,require_local_authorisation:true}`. Request ID is SHA256 of canonical JSON before adding `request_id`. Only a persistent private-key wallet/account qualifies. Related policies are exact local public monitors, with every prior approved generation retained. Fields with absent cursor values are omitted on the wire.

Agent replies with `archive_queue_result`: `{version,queue_id,request_id,status,...evidence,signed_receipt}`. The signed receipt authenticates the exact body without `signed_receipt`, under the Agent setup identity. `complete` for archival requires verified read-back and the image SHA256; merely receiving or staging an archive is not completion. A signed `archive_inventory` envelope can independently announce verified replacements.

Replacement evidence identifies the old archive, replacement image, all covered members, retained old policies and verified read-back. It creates a Dashboard-owned `destroy_disc` task with nonce, content-bound ID, old archive ID/label, replacement image SHA256 and the signed replacement receipt. Agent must independently verify complete replacement coverage. Only its local physical confirmation may complete that task. Dashboard exposes only cancellation; reconnect snapshots quietly withdraw absent tasks.

## Public policy renewal

Capability `wallet_policy_refresh_v1`. Proposal fields: `kind:"wallet_policy_refresh"`, `version:1`, `request_id`, `chain`, local `wallet`, the paired `previous_descriptors`, `receive_descriptor`, `change_descriptor`, `timestamp`, `expires_at`. Preserve identical public account keys, threshold and branches; require a strictly later mandatory block height. New addresses are durably reserved, checked against Core receiving cursors and previously used addresses, and constrained to Agent's independently supported scan range 0–999.

Agent returns a signed `wallet_policy_refresh_result` envelope with request ID/hash and status (`approved` or `history_required`); Dashboard also accepts authenticated `denied`/`error` results when sent. Local denials without an outcome message remain blocked pending reconciliation. The envelope includes source/monitor names, transition policy hash and the exact old/new descriptors. Dashboard verifies the signature against the connection/setup identity, the exact request, transition hash and both installed descriptors via Core. Unresolved approvals/history block funding. New policy generations merge only through approved exact descriptor transitions, never names or merely shared keys. Existing policy monitors/history remain intact. Incomplete older-policy coverage defers history publication rather than overwriting it with a partial wallet.

A linked Wallet Builder returns public output information to Transaction Builder; no funded PSBT yet exists. Payment/fee amounts remain editable until preparation. Clearing Vault drops its destination binding and reserves an ordinary new destination; already approved public monitors are retained. Cancellation includes delivered policy requests in the Agent delivery/approval workflow. Node-to-disc storage choices queue archival after policy verification; already loaded discs must be refreshed through local Disc Operations. Disc-to-persistent storage is not a Dashboard operation.

## PSBT output bindings

Signing/broadcast proposals may include `output_policies`, each `{policy_hash,descriptor,index,address,amount_sats}`. The hash covers the old/new paired descriptors and timestamp, independently approved on the Agent. Script/index/address and exact aggregate output amount must match. Agents already knowing a destination receive this proof; other funding Agents review an external address, without a Dashboard ownership claim. Input witness scripts and key origins, and output script/origin metadata, are carried by the standard PSBT. The same keys can sign later generations; policy knowledge and archive recovery data still need to be retained.

## Address semantics

The P2WSH address commits to the complete spending policy. Separate deposits to the exact same vault address share its same absolute unlock height and threshold. A later deposit does not restart that height. Another address in the same wallet is not automatically the same policy; derive and independently verify it. Historic Address Book browsing never changes the active receiving address used by builders.
