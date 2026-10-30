# Wallaby Bitcoin Dashboard

Bitcoin tools, wallet insights, Australian CGT working papers and shared family workflows.

**V1 Build 13 — Development.** [Download the DEB](https://github.com/bluesilent9/wallaby-dashboard/raw/refs/heads/Development/dist/wallaby-dashboard_1.0.0~dev13_all.deb) · [Checksums](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/dist/SHA256SUMS.txt) · [Development source](https://github.com/bluesilent9/wallaby-dashboard/tree/Development) · [Program page](https://bluesilent9.github.io/wallaby-suite/dashboard/)

Build 13 fixes stale green Vault cards: failed or overdue verification shows amber last-known data, a last-verified UTC timestamp and no current countdown. Vaults show actual signature thresholds and distinguish ready-to-fund, funded/locked and unlocked states. Wallet Builder separates approvals/backups, Core confirmation and funding readiness; each producing Agent identity is shown once per ceremony. Transaction Builder adds total-fee/percentage/rate review, explicit high-fee acknowledgement and clearer request/retry statuses. The existing broadcast workflow is unchanged. Agent-side presentation and approval follow-up is documented separately; no Agent code or protocol changed.

Dashboard Build 13 and **Agent V2 Build 11** implement Wallet Builder for Single Signature, Multi-Signature, mandatory-height Vault and Multi-Signature Vault policies. Agents independently approve participation, create and encrypt their assigned keys locally, verify the complete shared policy, and sign their confirmations. Dashboard verifies and relays those public proofs, including one verified backup for each physical role. Users compare full Agent fingerprints locally; Dashboard is not an identity authority.

Transaction Builder collects distinct valid signatures per input and waits for every threshold before enabling separately authorised Agent broadcast. Shared public monitors and their independent private signing accounts are identified by descriptors and keys, not names. Intentional cosigners do not trigger missing-wallet synchronization. Wallets hover details show required signatures and distinct signing keys visible through connected Agents; visibility does not guarantee local signing will succeed.

Agent Build 11 Disc Operations restores encrypted archival accounts into protected RAM. Dashboard supports its public routing aliases and provides Refresh signing accounts; temporary accounts stay out of historical loading, retirement and synchronization. Private backups and passphrases never go to Dashboard. [Wallet Builder guide](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/WALLET_BUILDER.md).

Dashboard now reads current, authenticated Agent-owned synchronization permissions. Disabled receiving policies show a read-only explanation rather than repeated offers or missing-wallet warnings. Agent Build 11 owns persistence across reboots; re-enable locally and Dashboard observes the change. Dashboard never restores an old denial list or adds a second control.

Authoritative synchronization queues, signed public previews, labelled historical-wallet transfers, retirement, vault monitoring, family balances, C1 losses, CGT working papers, fee estimates and saved profiles remain available. Wallet files travel directly between Agents after local approval. No private wallet payload travels through Dashboard.

The DEB starts and enables the service on installation. Access remains through authorised SSH tunnels. [User guide](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/USER_GUIDE.md) · [Protocol](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/AGENT_PROTOCOL.md) · [Validation](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/VALIDATION.md).

Validation: 152 Python tests, nine interface regression programs, actual Agent setup/completion and current-permission verification, four policy types independently accepted by isolated Bitcoin Core 31, and packaged IPv4/IPv6 startup checks. Real optical media, production local approvals and physical backup restoration require deployment testing. Tax working papers depend on complete supplied evidence and review.

[Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/) · [Report an issue](https://github.com/bluesilent9/wallaby-dashboard/issues). GNU AGPLv3; bundled components retain their notices. Application source remains on **Development**; this is not a Stable release.
