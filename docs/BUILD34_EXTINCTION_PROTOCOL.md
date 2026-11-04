# Dashboard Build 34 / Agent extinction contract

This replaces new retirement requests with `wallet_retirement_v3`. There is no fallback to the combined v2 archive-and-delete action. Every preserve, delete and disc action requires local Agent authorisation. Dashboard never executes a wallet-changing RPC.

## Inventory

Dashboard records `archive_wallets` separately from labelled extinct subwallets. Existing non-private Core wallets containing only `addr(...)` descriptors qualify regardless of wallet name. An empty existing container can be identified by the Agent adding `wallaby_extinct: true` to getwalletinfo; it must also report `private_keys_enabled: false`. This metadata must reflect an existing local container, not a Dashboard request to create one. Cold laptops without one stay without one.

## Wire request

`kind: wallet_retire`, integer `version: 3`, capability `wallet_retirement_v3`.

Exact fields: `kind, version, operation_id, phase, wallet, public_wallet, archive_label, require_zero_balance, delete_wallet, nonce, manifest, manifest_hash, delete_wallets, preservation, request_id`.

- `operation_id` and `nonce`: independently random 64 lowercase hex characters.
- `request_id`: SHA256 of canonical JSON of all other fields. The outbox `request_hash` is SHA256 of the complete proposal including request_id (the existing retirement convention).
- `phase`: `preserve` or `delete`.
- `manifest`: `{addresses: [{address, label}], transactions: [{txid, blockhash}]}`. Arrays sorted by address / txid, respectively. All used receive/change addresses and confirmed wallet transactions are unioned across available complete holders and local related policies. LOST labels are retained; conflicts block the operation. Maximum canonical manifest size 512 KiB. No private keys, encrypted wallet files or seeds pass through Dashboard.
- `manifest_hash`: SHA256 of canonical manifest JSON.
- `public_wallet`: retiring public descriptors, not the destination archive descriptors. Names do not establish identity.
- `require_zero_balance: true` in both phases. Agent independently verifies source identity, relevant funds and evidence; Dashboard's statement alone is never authority.

Preserve phase: `wallet` is the EXISTING local archive route, `delete_wallet: false`, `delete_wallets: []`, `preservation: null`. Agent verifies/imports the complete public historical manifest into that existing archive only. It must not create an archive or delete originals. Missing pruned history is an incomplete preservation, never success. A source wallet need not exist on the preserving Agent; public chain evidence must be independently established through Core. Preserve all existing address/loss labels, rejecting conflicts.

Delete phase: `wallet` is the local retiring source route; `delete_wallet: true`; `delete_wallets` contains exact independently checked `{wallet, public_wallet, private_keys_enabled}` local copies, related monitors and dedicated key accounts. Shared keys needed by other live policies must remain. `preservation` contains `{node_id, request_hash, archive_wallet, manifest_hash, verified_utc, receipt}`. The receipt is the preserving Agent's signed envelope described below. No archive creation, import or historical reconstruction is performed on a delete-only node. Independently recheck identity, all related confirmed/pending/immature/locked balances and pending activity before local deletion. Refuse uncertain identities/balances. Local user reviews preservation evidence and explicitly authorises deletion.

## Outbox results

Existing kind `wallet_retirement_result`; payload includes `request_hash, status, phase, manifest_hash`. Status is complete/denied/cancelled/error; optional message. Exact phase/hash must match the delivered request.

Successful preserve adds `archive_verified: true, history_verified: true, wallet_deleted: false, deleted_wallets: [], archive_wallet: <exact requested route>, address_count: <manifest address count>, archive_receipt: <signed envelope>`.

The Ed25519 signed envelope uses the existing setup-message signer. Its message is EXACTLY:

```
{version: 1, kind: "wallet_retirement_preservation", request_hash,
 operation_id, manifest_hash, archive_wallet, address_count, wallet_deleted: false}
```

Dashboard verifies against the authenticated/pinned preserving Agent identity. Then Dashboard reads back every address/label and confirmed transaction/blockhash through Core in that archive, checks the archive is public-only, caught up and not rescanning, and checks each block remains active. Merely receiving a success result does not release deletion. At least one existing preserving node must be connected and pass these reads before new deletion requests are generated.

Successful delete adds `zero_balance_verified: true, wallet_deleted: true, deleted_wallets: [exact names]`. Do not report a nonempty archive_wallet, because this phase must not create/import one. Every expected holder needs its own deletion confirmation; offline/unsupported holders remain pending. Requests are durable and idempotent. Completed outcomes cannot be rewritten. Cancellation uses existing `wallet_retire_cancel` v1 and requires correlated v3 phase/hash on its result; races report the actual result, never pretend deletion was undone.

## Late copies and old discs

Completed retirement operations remain as tombstones. When an old wallet is later discovered, add that holder to the checklist, reverify an online preserving archive, check current source history adds nothing beyond the preserved manifest and recheck zero funds, then request local deletion. If the reconnecting node already has an Extinct container, also request preservation there. New history or nonzero funds block deletion for reconciliation. A cancelled extinction is not automatically resumed.

Existing archive queue (`wallet_archive_queue_v1`) carries a new `destroy_disc` v2 request when the receiving Agent also supports wallet_retirement_v3. Exact request fields:

`kind, version, reason, nonce, operation_id, archive_id, label, archive_inventory, retired_wallet, retired_member_ids, manifest_hash, preservation, require_local_authorisation, require_replacement_if_live_members, require_verified_readback_before_destruction, request_id`.

`reason: wallet_extinction`; all three require flags true. archive_inventory is the original verified signed archive-inventory envelope. preservation is the independently verified signed receipt bundle above. Each retired member is bound to the public retired-wallet descriptors; a name match is insufficient.

Agent independently reads the actual disc and identifies every member. If it contains current/live members, offer to make a replacement containing their current verified information, include every other live member, verify replacement readback, THEN request destruction of the original. A failed/cancelled replacement must NEVER allow destruction of the only backup. Do not destroy a mixed disc solely because one member is extinct. Locally report `archive_queue_result` with its existing signed_receipt. Complete requires `destruction_confirmed_locally: true`; the Dashboard cannot confirm this. The Dashboard can withdraw its request. Local disable/dismiss follows the existing authoritative archive queue rules.

Dashboard matches signed archive inventory whenever it is reported or an Agent reconnects; no wallet key data is transferred. A reconnecting archive may be old even when its live wallet copy is gone. New disc tasks require a currently connected preserving Extinct node to verify the manifest. The user may postpone/withdraw requests, but this is not evidence of physical destruction.

## Authenticated old-disc observation

Accept signed archive_inventory with `version:1, event:archive_observed, archive_id, payload_sha256:archive_id, payload_verified:true, readback_verified:false, replacements:[], members`. This proves an authenticated payload was read, NOT that a new disc passed a burn/readback check. No image_sha256 is required for this observation. Store it for identity/member matching and extinction review; never use it as replacement verification evidence. Existing archive_verified/replacement_verified messages retain their full readback rules.

## Historical wallet names versus local routes

`archive_label` is the confirmed Ownership display name, never the selected Core route, raw wallet_name, hostname, or watch-only replica name. Every ordinary manifest address uses archive_label; an existing LOST declaration retains its exact UTC timestamp but is grouped under archive_label. Watch/private copies are matched by descriptors. Names such as `treasury_watch` remain only local RPC routes and deletion targets. Do not replace manifest labels with a source/watch wallet name when importing historical addresses.
