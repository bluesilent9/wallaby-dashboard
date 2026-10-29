# Dashboard Build 9 — Agent integration contract

Dashboard Build 9 and Agent V2 Build 7 implement both sides of this contract. Older Agents can continue public vault synchronisation; they must not be used as a fallback for signing, because the old `sign_psbt` operation signs and broadcasts immediately.

All operations below use the existing token-authenticated, loopback-only Agent listener (default port **56443**, reached through SSH). The browser listener remains **38951**. Neither port should be publicly exposed. Only public wallet information and PSBTs enter the Dashboard. Private keys, encrypted wallet files, wallet passwords and RPC cookies must never be sent through it. Local Agent authorisation is mandatory for signing, broadcasting, receiving descriptor updates and either end of wallet transfer.

## Exchange envelope

POST `/agent/v2/exchange`, using the existing Agent connection token:

```json
{"version":1,"outbox":[],"known_vaults":[],"capabilities":["node_identity","partial_signing","broadcast","wallet_transfer"],"node_info":{"ipv6":"2001:db8::123"}}
```

`capabilities` and `node_info` are optional extensions. Advertise only implemented capabilities. The response keeps exactly `version`, `accepted`, `proposals`. At most 8 outbox items, 128 known vault hashes and 16 proposals per exchange; request body limit 20 MiB. Each outbox item is `{id,kind,payload}`. Its ID is SHA256 of UTF-8 `json.dumps({"kind":kind,"payload":payload},sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False)`. Hash public vault objects the same way for `known_vaults`. Acknowledgements follow durable storage. Retries use the same content ID and are idempotent.

Agents can report their routable IPv6 in `node_info` on every exchange. If absent and `node_identity` is advertised, the Dashboard sends `{"kind":"node_identity"}`; reply with outbox kind `node_identity`, payload `{"ipv6":"..."}`. No user IP-entry step is required. This address is a connection hint, never proof of identity. Peer SSH host-key authentication remains necessary. Link-local, loopback, scoped and IPv4-mapped addresses are rejected.

## Public vault record

Existing `vault_public` and `vault_sync` shapes remain supported. The policy is the supported mandatory-height, single-key P2WSH form `wsh(and_v(v:pk(COMPRESSED_PUBLIC_KEY),after(HEIGHT)))#CHECKSUM`. Record fields: `version:1`, `chain:"main"`, `descriptor`, `address`, integer `height`, `source:{descriptor,index}`, integer `timestamp`. Source is a public derivable account descriptor and the key's actual derivation index. An Agent monitor may include key-origin annotations: Dashboard policy comparisons disregard those annotations and checksums, while retaining the actual policy/branch. A detected `after()` policy without a validated record remains amber, never automatically green.

A policy that is valid is not thereby approved by the local user. The receiving Agent independently validates it and asks locally before import. Vaults which have no confirmed funds, funds outside the vault, missing node coverage or pending policy authorisation are amber with details. Height estimates use 600 seconds per block, not a time guarantee. Height reached means the timelock has ended; keys are still required.

## One PSBT format: compose first, sign second

Use BIP174 PSBT v0 for single-wallet sends, multi-wallet sends and vault creation/rollover. Unsigned, partly signed and fully signed are states of the same format. SIGHASH_ALL (or Taproot DEFAULT/ALL) commits signatures to the final transaction, so composition always happens **before signing**. Do not use ANYONECANPAY or discard existing signatures to permit later editing.

To export a vault intent, send outbox kind `transaction_builder_psbt`:

```json
{"version":1,"chain":"main","psbt":"BASE64_UNSIGNED_PSBT","transaction_hash":"64_lowercase_hex_advisory_hash","vault":{},"review":{},"stage":2,"immutable":true}
```

`vault` is the complete public record above. `transaction_hash` and `review` are advisory only; transaction identity and amounts are recalculated. `immutable:true` locks this component, not the entire final transaction. Include complete public signing metadata: previous transactions/UTXOs, witness/redeem scripts, BIP32 origins and derivations. Never include any signature/final-witness fields. Already signed exports are rejected with an Agent-update message.

The browser must have an explicitly open Stage 2 diagram, expected source node and logged-in owner. It renews a short-lived lease. No matching workspace, or several eligible workspaces, means no acknowledgement; the Agent retains its outbox. An acknowledged export is permanently bound to that draft/profile. Clearing/loading another diagram permits a new import; a retry after a lost acknowledgement remains acknowledged. Agents must not silently substitute a different proposal after acknowledgement.

The Dashboard checks inputs against Core, exact source ownership, unspent status, source public policy, the vault output and a positive fee. Fixed inputs, outputs and the component's absolute fee remain unchanged. Their arrows are red, their cards cannot be connected or deleted, and other wallets may add payments and fee contributions through the shared Network Fee card. Additional funders' fee rates apply to the combined transaction size. The displayed total rate is total fee divided by conservative combined vsize. The fixed vault component cannot be saved as an editable template. New/load discards it. Proposal inputs cannot also fund another component.

Next produces one unsigned PSBT including all components. Each signing request includes:

```json
{"kind":"sign_psbt_partial","request_id":"64hex","chain":"main","wallet":"exact_local_rpc_wallet_name","psbt":"BASE64","vault_intents":[{"vault":{},"proposal_psbt":"BASE64_ORIGINAL_UNSIGNED_COMPONENT"}]}
```

`vault_intents` is present only for vault transactions. The Agent must independently validate the final PSBT and all previous outputs; verify every required component input/output and its vault policy, and show the **complete final** payments, change and total fee for local review. The source wallet name is an RPC routing hint, not evidence of ownership. Verify keys/descriptors locally. Check the proposal's original input sequences and required locktime remain satisfied. Refuse any change to fixed intent. The Dashboard cannot authorise this review. Unlock and sign only after local user approval. Sign only owned inputs, return the PSBT, and **do not broadcast**.

Return outbox kind `partial_sign_result`, payload `{request_hash,status:"signed",psbt}`. `request_hash` is SHA256 of the complete received proposal, not its `request_id`. For refusal/failure return `{request_hash,status:"denied"|"error",message}`. Never send unlock passwords or private key material. A one-wallet result can already be fully signed; it still follows this same protocol. The Dashboard cryptographically verifies assigned inputs, unchanged transaction identity and permitted sighashes before turning that card green. Node eligibility requires connected, synchronised, matching private-key wallet, no wallet scan and `partial_signing` capability. Watch-only replicas remain excluded.

After every card is verified, the creator can request broadcast. Proposal:

```json
{"kind":"broadcast_transaction","request_id":"64hex","chain":"main","psbt":"BASE64_COMBINED_PSBT","txid":"64hex"}
```

The Agent locally reviews/authorises, independently finalises the PSBT and verifies the expected TXID before broadcasting. Return `broadcast_result` with `{request_hash,status:"broadcast",txid}` or denied/error with `message`. Dashboard also checks the exact raw transaction through the connected node before marking completion. A PSBT itself is not broadcast to Bitcoin's P2P network.

## Missing wallet: paired peer transfer requests

Public descriptor equality establishes matching wallets across nodes. Names never establish identity. A private-key wallet and its watch-only counterpart count as the same wallet; no duplicate is requested. Inventory must be complete (including loaded status of node wallets) before absence is inferred.

When a wallet is missing, Dashboard creates paired requests with shared `transfer_id`, opposite `direction:"send"|"receive"`, `peer:{node_id,hostname,ipv6}`, `public_wallet` (public descriptors only), `source_has_private_keys`, `earliest_timestamp`, `requires_blockchain_redownload`, and `kind:"wallet_transfer"`. They are delivered only after both peers advertise `wallet_transfer` and provide routable IPv6. Both addresses and requests are frozen during review. Changes require local cancellation and a deliberate fresh attempt; the old request is not silently rewritten.

The history flag conservatively warns when a pruned target cannot cover the public descriptor timestamp. It is a request for the Agent to evaluate recovery, not authority to reindex or proof that a transferred full wallet lacks saved history. Agent must inspect its local wallet/history/pruning before recommending re-download. Each side independently verifies identity, intent and local approval. The temporary Agent-to-Agent SSH connection transfers encrypted wallet data **directly between Agents**, outside Dashboard. No private descriptor/file upload endpoint exists here. Return `wallet_transfer_result` with `{request_hash,status:"complete"|"denied"|"error",message?}`. Dashboard implements public orchestration; Agent Build 7 implements the direct private transfer.

## Build 9 signed initial preview

Both wallet-transfer proposals add `preview_challenge` (one shared cryptographically random 64-hex nonce) and `node_ipv6` (that proposal's own address). Role, public wallet and both peers/addresses are frozen before delivery. One wallet pair is active globally. Ready candidates sort by wallet name case-insensitively, then descriptor identity/transfer ID. Watch/private counterparts count as the same wallet. Policies unsupported by the current Agent file-transfer contract are not queued as an invalid batch.

The sender's new outbox kind is `wallet_transfer_preview`; payload exactly `{request_hash,preview,public_key,signature}`. Request hash covers its complete sending proposal. The preview fields are exactly `version:1`, `transfer_id`, `challenge`, `sender_ipv6`, `receiver_ipv6`, `public_wallet`, `chain:"main"`, `funded_outputs`, `node_tip:{height,hash}`, `generated_at`. Each funded output contains `address`, `txid`, `vout`, integer-satoshi decimal string `amount_sats`, `time_received`, `blockheight`, `confirmations`. Nullable receipt facts are preserved. Limits: 10,000 outputs and 256 descriptors. Monetary floats, private descriptors, cookies and wallet files are forbidden.

The dedicated Agent identity is `ssh-ed25519 BASE64`, without a comment. Signature is base64 of the entire armored SSHSIG file. Signing input is canonical JSON of `preview`, using the existing envelope JSON rules; SSHSIG namespace `wallaby-wallet-preview-v1`. Dashboard verifies signature, full sending-request correlation, complete wallet policy set, challenge and addresses before durable acknowledgement. The receiving Agent independently verifies and requires the fingerprint obtained directly from the sender screen. A fingerprint supplied solely by Dashboard is not identity authority.

Receiver base request precedes preview relay. Subsequent responses alternate base-only and preview-only, allowing recovery if the initial base response was lost. Relay exactly `{kind:"wallet_transfer_preview",preview,public_key,signature}` without changing signed facts. Only identical retries are allowed. Expiry is fifteen minutes, with two minutes future-clock tolerance. Already acknowledged duplicates remain acknowledged after expiry; expired previews are not relayed or silently renewed.

While active, both nodes receive only the frozen transfer/base-preview work. New RPC/approval dispatch pauses; authenticated heartbeats and outbox/result delivery continue. `synchronisation_busy` is temporary and does not permanently fail the node. Inventory refresh resumes after both terminal outcomes. Pending requests and snapshots survive restart. Terminal outcomes cannot be changed. After a failed/denied/uncertain operation, users close both requests, reconcile Core and explicitly choose New transfer attempt. It creates a new transfer identity and challenge using refreshed public inventory. No automatic retry repeats a file operation.

The Agent implements direct encrypted wallet transfer and local review; Dashboard implements the public relay and coordination. Physical-machine SSH/firewall/user-approval testing remains separate from the automated protocol tests.
