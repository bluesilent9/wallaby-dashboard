# Wallaby Bitcoin Dashboard

Bitcoin tools, wallet insights, Australian CGT working papers and shared family transaction workflows.

**[Explore the program page](https://bluesilent9.github.io/wallaby-suite/dashboard/)** · **[Browse Development source](https://github.com/bluesilent9/wallaby-dashboard/tree/Development)** · [Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/)

V1 Build 7 source is on the **Development** branch. The [Development DEB](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/dist/wallaby-dashboard_1.0.0~dev7_all.deb) and [SHA256 checksum](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/dist/SHA256SUMS.txt) are available. See the [fresh server installation guide](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/CLEAN_INSTALL.md). Use [Wallaby Agent V2 Development Build 5](https://github.com/bluesilent9/wallaby-agent/tree/Development) for the current Agent; corrected wallet routing was introduced in Build 4.

The DEB starts and enables the Dashboard system service on installation. Browser and Agent interfaces bind to loopback for SSH-tunnel access. See the Development README for setup, validation and build instructions.

Please use [GitHub Issues](https://github.com/bluesilent9/wallaby-dashboard/issues) for feedback. Do not include private keys, descriptors or personal financial records in public issues.

Build 7 reads labelled historical watch-only wallet groups directly from Core and records declared no-compensation C1 losses using traced acquisition costs. Blockchain history and fee/block observations use connected nodes. The adapted Mempool component and combined distribution are AGPL-3.0; see [licence and source](https://github.com/bluesilent9/wallaby-dashboard/tree/Development).


## Current Build 7 capabilities

- Discover every connected Core node through Agent, deduplicate public descriptor wallet replicas and load labelled fixed-address historical watch-wallet groups. Historical-wallet ODS uploads and Wallet Key Explorer have been removed.
- Verify balances and retained acquisition costs from node wallet evidence; trace source/destination records and family ownership, including watch-only and SMSF wallets. Blockchain history and fees use connected nodes, with no public-blockchain fallback. Required pruned history must be retained in a loaded wallet.
- Generate per-user Australian CGT working papers and financial-year totals from Bitaroo PDFs, Exchanges ODS, ownership and prices. Declared `LOST:` records support no-compensation C1 losses from traced reduced cost base; a label is a user declaration, not proof of loss or tax eligibility.
- Build reusable payment diagrams, prepare unsigned PSBTs, collect and verify participants’ Core signatures and download the combined result. The Dashboard does not unlock wallets, sign or broadcast.
- Explore node-driven fee estimates, pending/mined block views, transaction tracking, Address Book, notifications and profile archives. Market/currency prices remain external; supplied CPI data is used where required.

## Agent compatibility and pending integration

Use an Agent with the corrected wallet RPC routing introduced in V2 Development Build 4. The current Agent is **[V2 Development Build 5](https://github.com/bluesilent9/wallaby-agent/tree/Development)**. Build 5 keeps ordinary public/read-only node queries working but removes direct remote wallet creation and descriptor imports. Preconfigure required watch wallets locally; do not expect Dashboard V1 Build 7 to create or synchronise them through unrestricted RPC.

The Agent now contains its own local Vault Builder and Authorise pages. Dashboard V1 Build 7 has **not** implemented the Agent’s [approval/exchange protocol](https://github.com/bluesilent9/wallaby-agent/blob/Development/APPROVAL_PROTOCOL.md), signing-request submission or immutable Transaction Builder Stage 2 receiver. Agent vault records and signed PSBT handoffs therefore queue locally; they are not yet an end-to-end Dashboard feature. The existing manual Core PSBT signing/upload workflow remains available. Dashboard vault monitoring is still a placeholder.

