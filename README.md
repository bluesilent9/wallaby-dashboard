# Wallaby Bitcoin Dashboard

Bitcoin tools, wallet insights, Australian CGT working papers and shared family transaction workflows.

**[Explore the program page](https://bluesilent9.github.io/wallaby-suite/dashboard/)** · **[Browse Development source](https://github.com/bluesilent9/wallaby-dashboard/tree/Development)** · [Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/)

V1 Build 8 source is on the **Development** branch. The [Development DEB](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/dist/wallaby-dashboard_1.0.0~dev8_all.deb) and [SHA256 checksum](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/dist/SHA256SUMS.txt) are available. See the [fresh server installation guide](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/CLEAN_INSTALL.md). Corrected Agent wallet routing was introduced in V2 Build 4. The new signing and peer-transfer capabilities require a separate Agent update.

The DEB starts and enables the Dashboard system service on installation. Browser and Agent interfaces bind to loopback for SSH-tunnel access. See the Development README for setup, validation and build instructions.

Please use [GitHub Issues](https://github.com/bluesilent9/wallaby-dashboard/issues) for feedback. Do not include private keys, descriptors or personal financial records in public issues.

Build 7 reads labelled historical watch-only wallet groups directly from Core and records declared no-compensation C1 losses using traced acquisition costs. Blockchain history and fee/block observations use connected nodes. The adapted Mempool component and combined distribution are AGPL-3.0; see [licence and source](https://github.com/bluesilent9/wallaby-dashboard/tree/Development).


## Current Build 8 capabilities

- Discover every connected Core node through Agent, deduplicate public descriptor wallet replicas and load labelled fixed-address historical watch-wallet groups. Historical-wallet ODS uploads and Wallet Key Explorer have been removed.
- Verify balances and retained acquisition costs from node wallet evidence; trace source/destination records and family ownership, including watch-only and SMSF wallets. Blockchain history and fees use connected nodes, with no public-blockchain fallback. Required pruned history must be retained in a loaded wallet.
- Generate per-user Australian CGT working papers and financial-year totals from Bitaroo PDFs, Exchanges ODS, ownership and prices. Declared `LOST:` records support no-compensation C1 losses from traced reduced cost base; a label is a user declaration, not proof of loss or tax eligibility.
- Build reusable payment diagrams, prepare unsigned PSBTs, collect and verify participants’ Core signatures and download the combined result. The Dashboard never unlocks or signs; signing and broadcast require local Agent authorisation.
- Explore node-driven fee estimates, pending/mined block views, transaction tracking, Address Book, notifications and profile archives. Market/currency prices remain external; supplied CPI data is used where required.

## Build 8 and Agent integration

Node cards distinguish blockchain download from connection. Vaults shows verified policies, balances, unlock estimates and amber explanations. The Builder composes fixed unsigned vault proposals with other payments before signing, then requests each wallet's signatures through an eligible local Agent. One PSBT protocol handles single and multiple senders; a fully signed result is finalised and locally authorised for broadcast.

The Dashboard implements paired missing-wallet transfer requests with public descriptor manifests and peer IPv6 hints. Private wallet files transfer directly between Agents in a future Agent update, never through the Dashboard. Matching watch/private counterparts are not duplicated.

**The Agent is updated separately.** New operations are capability gated, with no fallback to its older sign-and-broadcast request. See [the integration protocol](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/docs/AGENT_PROTOCOL.md) and [validation](https://github.com/bluesilent9/wallaby-dashboard/blob/Development/VALIDATION.md).
