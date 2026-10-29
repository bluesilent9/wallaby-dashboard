# Wallaby Bitcoin Dashboard

Bitcoin tools, wallet insights, Australian CGT working papers and shared family workflows.

**V1 Build 10 — Development.** [Download the DEB](https://github.com/bluesilent9/wallaby-dashboard/raw/refs/heads/Development/dist/wallaby-dashboard_1.0.0~dev10_all.deb) · [Checksums](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/dist/SHA256SUMS.txt) · [Development source](https://github.com/bluesilent9/wallaby-dashboard/tree/Development) · [Program page](https://bluesilent9.github.io/wallaby-suite/dashboard/)

Dashboard Build 10 and **Agent V2 Build 8** implement complete synchronization lists, signed version-2 public metadata previews, labelled historical-wallet transfers and explicit incomplete-history recovery states. Older synchronization protocols have been removed. Wallet files travel directly between Agents after local review; Dashboard receives only public coordination data. Users must compare the sender identity and snapshot codes directly between Agent screens.

Unsigned vault proposals, single/multiple-wallet partial signing and separately authorised broadcast use one PSBT workflow. Node readiness, vault monitoring, labelled historical watch wallets, balances, C1 losses, CGT working papers, fee estimates and saved family profiles remain available. Private keys and passwords stay off the Dashboard.

The DEB starts and enables the Dashboard service on installation. Access remains through authorised tunnels. [Setup and guide](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/USER_GUIDE.md) · [Agent protocol](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/AGENT_PROTOCOL.md) · [Validation](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/VALIDATION.md)

104 Python tests and six interface regressions passed, including actual Agent Build 8 preview-signing/receiving interoperability. Physical two-laptop SSH/firewall checks remain separate; this is a Development release. Tax working papers depend on complete supplied evidence and require review.

[Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/) · [Issues](https://github.com/bluesilent9/wallaby-dashboard/issues) · [AGPL-3.0 source and licence](https://github.com/bluesilent9/wallaby-dashboard/tree/Development). Do not post private wallet material or personal financial records in public issues.

Wallets replaces Wallet Balances, with owner and Active/Extinct filters. Historical cards are grey and show confirmed UTC activity years. Different address labels in one historical Core archive retain separate ownership. Make Extinct is a public local-review request for a future compatible Agent; Agent Build 8 does not execute retirement. Wallet Builder/multisig creation remains a proposal only.
