# Wallaby Bitcoin Dashboard

Bitcoin tools, wallet insights, Australian CGT working papers and shared family workflows.

**V1 Build 9 — Development.** [Download the DEB](https://github.com/bluesilent9/wallaby-dashboard/raw/refs/heads/Development/dist/wallaby-dashboard_1.0.0~dev9_all.deb) · [Checksums](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/dist/SHA256SUMS.txt) · [Development source](https://github.com/bluesilent9/wallaby-dashboard/tree/Development) · [Program page](https://bluesilent9.github.io/wallaby-suite/dashboard/)

Dashboard Build 9 and **Agent V2 Build 7** implement the signed initial wallet-preview relay, paired transfer scheduling and explicit fresh-attempt handling. Wallet files travel directly between Agents after local review; Dashboard receives only public coordination data. Users must compare the sender identity and snapshot codes directly between Agent screens.

Unsigned vault proposals, single/multiple-wallet partial signing and separately authorised broadcast use one PSBT workflow. Node readiness, vault monitoring, labelled historical watch wallets, balances, C1 losses, CGT working papers, fee estimates and saved family profiles remain available. Private keys and passwords stay off the Dashboard.

The DEB starts and enables the Dashboard service on installation. Access remains through authorised tunnels. [Setup and guide](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/USER_GUIDE.md) · [Agent protocol](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/AGENT_PROTOCOL.md) · [Validation](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/VALIDATION.md)

98 Python tests and five interface regressions passed, including actual Agent Build 7 preview-signing/receiving interoperability. Physical two-laptop SSH/firewall checks remain separate; this is a Development release. Tax working papers depend on complete supplied evidence and require review.

[Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/) · [Issues](https://github.com/bluesilent9/wallaby-dashboard/issues) · [AGPL-3.0 source and licence](https://github.com/bluesilent9/wallaby-dashboard/tree/Development). Do not post private wallet material or personal financial records in public issues.
