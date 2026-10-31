# Dashboard Build 20 — builder coordination and Agent cancellation

This document is the Dashboard-side contract for the next Agent implementation. Dashboard Build 20 changes no Agent code. No older-protocol fallback is implemented. Agent Build 18 does not implement this capability; until an Agent is updated, its cancellation delivery stays pending. Synchronisation exclusions and wallet retirement retain their separate protocols.

## Durable single-creator workspace

`data/builder-workspaces.json` stores one Wallet Builder and one Transaction Builder workspace. Each has an unpredictable ID, creator ID, revision, public draft and exact operation IDs. Different users cannot modify the same builder concurrently. The initiating Dashboard user routes every required signature, regardless of the source-wallet owner's family profile. Signing, broadcasting and wallet creation still require independent local Agent authorisation. No other family user must log into Dashboard to sign. Family messages and freshness/conflict notifications remain independent.

Wallet Builder starts only after **Start Building a Wallet**. Transaction Builder starts when a new or saved template is opened. Draft changes and visual positions are saved server-side; revision checks refuse stale browser writes. Returning to an active workspace resumes its draft or prepared/approval stage. An unavailable node does not prevent reopening or cancelling an existing workspace. Wallet setup proposals still keep their original expiry and local reconciliation checks; a browser reconnection never regenerates keys. After a Dashboard service restart, unfinished key ceremonies remain visible but are interrupted for local reconciliation; they do not silently restart or regenerate keys. Automatic replication of a newly built monitor is deferred until its creator completes the workspace, so cancellation cannot leave untracked mid-build sync copies.

## Cancellation proposal

Advertise `builder_cancellation_v1` in `/agent/v2/exchange` capabilities. Requests use that same authenticated exchange. One request per involved Agent is saved and retransmitted unchanged until its delivery receipt is accepted:

```json
{
  "kind": "builder_cancel",
  "version": 1,
  "builder": "wallet",
  "workspace_id": "32 lowercase hexadecimal characters",
  "cancellation_id": "32 lowercase hexadecimal characters",
  "operations": ["exact setup proposal hash or Dashboard transaction ID"],
  "request_hashes": ["SHA256 of an original setup/signing/broadcast proposal"],
  "exports": [],
  "require_local_authorisation": true,
  "backup_option": true,
  "request_id": "HASH_WITHOUT_THIS_FIELD"
}
```

Hash rules match the existing exchange: SHA256 of UTF-8 `json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)`. `request_id` hashes the proposal without that field; the delivery receipt's `request_hash` hashes the entire proposal including `request_id`.

`builder` is `wallet` or `transaction`. Setup `operations` are setup proposal hashes. Their `request_hashes` identify the original participation/setup proposal. Transaction `operations` include Dashboard prepared-job IDs; `request_hashes` identify only that Agent's actual signing/broadcast requests which Dashboard delivered. Node/Agent selection alone does not establish delivery.

A vault proposal originated on the Agent before signing may have no Dashboard signing request yet. Its cancellation `exports` identify the original public export using `{outbox_id, txid, psbt_sha256}`. `outbox_id` is the original `transaction_builder_psbt` outbox item ID, `txid` is the independently parsed unsigned transaction ID and `psbt_sha256` hashes the decoded PSBT bytes. Each must match the Agent's own retained record. Do not use a Dashboard wallet display name or arbitrary local pathname to identify deletion targets.

Dashboard first stops its own builder and withdraws its original pending requests. Offline nodes remain in a saved delivery queue. Late authenticated signing results are drained without reopening the cancelled job. A separately verified broadcast result must retain the fact of broadcast; cancellation cannot recall a transaction from the mempool or chain.

## Receipt — delivery only

After durably recording the cancellation request and arranging local review, send this existing-exchange outbox item:

```json
{
  "kind": "builder_cancel_receipt",
  "payload": {
    "version": 1,
    "cancellation_id": "the request's cancellation_id",
    "request_hash": "hash of the entire builder_cancel proposal",
    "status": "received"
  }
}
```

The outbox `id` is the existing hash of `{kind,payload}`. Receipt must survive Agent restart and be idempotent. Dashboard checks the connected node, exact cancellation ID/hash, and that it sent this proposal. Duplicates are accepted without changing a newer workspace. Receipt does **not** assert backup, deletion, approval or even that an artifact exists. Dashboard releases the workspace as soon as every involved Agent's receipt is durably accepted. A one-node receipt cannot complete another node's row.

The Dashboard modal lists hostname, stage and dot: **Agent online, sending request** (amber), **Agent offline** (red), **Complete** (green). It closes automatically when all receipts arrive. Leaving the page does not abandon delivery. No Agent with delivered work is silently considered complete because it disconnected.

## Required local Agent behaviour (implemented in the Agent chat)

Queue one Authorise review for the exact known build and all of its locally associated artifacts. If nothing was created, cancel its queued work after local authorisation, without inventing files. The user chooses:

- Red **Delete and Cancel**: cancel the local operation and remove only its verified local artifacts after approval.
- Green **Backup and Cancel**: back up every associated PSBT or wallet locally to the Agent's chosen hard-drive destination, verify success, then cancel/remove the original artifacts after approval. Backup failure must preserve originals. Wallet encryption/passphrases and backup files never pass through Dashboard.

Explain: “The Dashboard has requested that the Wallet/Transaction build be cancelled. If you were expecting this request, choose Delete and Cancel. If something is wrong, choose Backup and Cancel to protect against loss of funds.” Use Wallet or PSBT as appropriate.

For transaction builds, check all known transaction IDs against the local mempool/chain. Display **No broadcast PSBTs from this Transaction Build have been detected**, or red **Broadcast PSBTs from this Transaction Build have been detected**. “Not detected” is limited to the node's coverage, not proof of global non-broadcast. Deleting local PSBTs cannot undo existing signatures, broadcast or a future broadcast by someone retaining a copy.

Pin exact wallet/account identities and original request scopes, not just names. Review funded wallets, incoming-funding risks, changed files and in-flight signing/broadcast before destructive cleanup. A new Dashboard build may start after delivery receipt while local cancellation approval is still pending: keep separate build IDs and block conflicting local operations until reconciled. Receipt must never silently authorise deletion or disable the Agent's signing/secret protections.
