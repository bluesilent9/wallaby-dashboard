# Wallaby Bitcoin Dashboard

Bitcoin tools, wallet insights, Australian CGT working papers and shared family workflows.

**V1 Build 11 — Development.** [Download the DEB](https://github.com/bluesilent9/wallaby-dashboard/raw/refs/heads/Development/dist/wallaby-dashboard_1.0.0~dev11_all.deb) · [Checksums](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/dist/SHA256SUMS.txt) · [Development source](https://github.com/bluesilent9/wallaby-dashboard/tree/Development) · [Program page](https://bluesilent9.github.io/wallaby-suite/dashboard/)

Dashboard Build 11 and **Agent V2 Build 9** implement Wallet Builder for Single Signature, Multi-Signature, mandatory-height Vault and Multi-Signature Vault policies. Agents independently approve participation, create and encrypt their assigned keys locally, verify the complete shared policy, and sign their confirmations. Dashboard verifies and relays those public proofs, including one verified backup for each physical role. Users compare full Agent fingerprints locally; Dashboard is not an identity authority.

Transaction Builder collects distinct valid signatures per input and waits for every threshold before enabling separately authorised Agent broadcast. Shared public monitors and their independent private signing accounts are identified by descriptors and keys, not names. Intentional cosigners do not trigger missing-wallet synchronization. Wallets hover details show required signatures and distinct signing keys visible through connected Agents; visibility does not guarantee local signing will succeed.

Agent Build 9 creates encrypted disc backups but does not yet offer a guided disc-restore signing screen. Dashboard supports signature routing from a locally restored matching account; private backups and passphrases never go to Dashboard. [Wallet Builder guide](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/WALLET_BUILDER.md).

Authoritative synchronization queues, signed public previews, labelled historical-wallet transfers, retirement, vault monitoring, family balances, C1 losses, CGT working papers, fee estimates and saved profiles remain available. Wallet files travel directly between Agents after local approval. No private wallet payload travels through Dashboard.

The DEB starts and enables the service on installation. Access remains through authorised SSH tunnels. [User guide](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/USER_GUIDE.md) · [Protocol](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/AGENT_PROTOCOL.md) · [Validation](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/VALIDATION.md).

Validation: 120 Python tests, seven interface regression programs, actual Agent setup/completion verification, four policy types independently accepted by isolated Bitcoin Core 31, and packaged IPv4/IPv6 startup checks. Real optical media, production local approvals and physical backup restoration require deployment testing. Tax working papers depend on complete supplied evidence and review.

[Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/) · [Report an issue](https://github.com/bluesilent9/wallaby-dashboard/issues). GNU AGPLv3; bundled components retain their notices. Application source remains on **Development**; this is not a Stable release.
