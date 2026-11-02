# Dashboard Build 28 / Agent V2 Build 24

Ownership availability depends on validated public wallet metadata, not completion of all historical input lookups. The server publishes the public catalog and discovery notice first. Complete history is published separately and remains mandatory for balance/tax readiness. Record loading can finish the scan at foreground priority. No partial or missing ledger is presented as zero. Existing reports are invalidated by changed evidence as before.

Known wallet transactions are requested through gettransaction on their actual wallet replicas first, with a dedicated rotation counter unaffected by block-header reads. Failed replicas fall through to the others; unknown TXIDs still use all connected nodes. Inventory reads omit unrelated external-input ancestry, preserve unknown input values and unknown fees, and keep that limited view out of the full tracing cache. Wallet sends still resolve their input evidence and fail explicitly if it is missing. Progress is logged every 25 reconstructed transactions.

## One Extinct container

Use the existing wallet_sync_queue and transfer-version-2 wallet_transfer protocol. Dashboard aggregates its logical extinct children into public_wallet.wallet_name=Extinct, with all their addr() descriptors and source_has_private_keys=false. There is no new wire kind or private-wallet upload. A complete destination policy set needs no transfer; a subset is incomplete. The 256-descriptor bound is explicit; no address is silently omitted. Nodes that hold unrelated source archives may require separate source/receiver transfers; replicas of the same complete archive do not duplicate requests.

Agent independently reads and signs the public labels, funded outputs and preview identity. Labels before the LOST suffix identify the child wallets in its UI. Receiver creates or extends one private-key-disabled Wallaby Extinct wallet, preserves local addresses/labels, rejects conflicts and retains blockchain recovery requirements. Dashboard continues treating each label group as its own ownership/C1 entity, never assigns one owner to the entire archive, and never treats a label as signing authority.

## Old requests and reconnect

The full revised queue withdraws pending per-child requests superseded by the container. Stored operation outcomes are not deleted. An old public wallet_transfer_preview notice may be acknowledged only when it matches the authenticated sending node's original delivered frozen request, is superseded by the current address-only container, and has a valid SSH signature. Expiry alone is allowed for this notice-only cleanup; signature, public-policy, challenge and peer bindings are still required. The notice never completes a transfer or authorises a mutation. Transfer results and history-recovery records remain visible for reconciliation, including results arriving after withdrawal.

Agent Build 24 provides its own local Clear obsolete Extinct denials control. It matches exact policies, using the sender-signed labels where needed; Dashboard neither clears nor restores local exclusions. Deliberate whole-container denials and unrelated denials remain Agent-owned. Unknown capabilities remain rejected; bounded Dashboard diagnostics list the unsupported public capability names and application versions while the HTTP error category stays stable.
