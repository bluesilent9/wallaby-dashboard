<p align="center"><img src="assets/logo.svg" width="112" alt="Wallaby Dashboard"></p>
<h1 align="center">Wallaby Bitcoin Dashboard</h1>
<p align="center">Bitcoin tools, wallet insights and family workflows.</p>

**V1 Build 8 · Development DEB available for Ubuntu/Kubuntu 26.04 LTS.** [Download the DEB](dist/wallaby-dashboard_1.0.0~dev8_all.deb) and verify [SHA256SUMS.txt](dist/SHA256SUMS.txt). This resets the public version numbering following the earlier V156/V157 work; it does not reset saved data formats.

[Program page](https://bluesilent9.github.io/wallaby-suite/dashboard/) · [Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/) · [Issues](https://github.com/bluesilent9/wallaby-dashboard/issues) · [User guide](docs/USER_GUIDE.md)

## What it does

- Australian CGT working papers, financial year totals and visual transaction histories for supplied family records.
- Shared Ownership, Address Book and wallet balances.
- Node-driven Fee Estimates, pending/mined blocks and transaction tracking, with Bitcoin Core unit conversions.
- Shared transaction templates, unsigned PSBT preparation and verified partial-signature collection. Private keys remain in Bitcoin Core; private signing remains local to the Agent and broadcast requires local Agent authorisation.
- Node discovery through Wallaby Agent, querying every connected node and deduplicating wallet replicas by public descriptors.

Vault monitoring uses public vault records and connected Core nodes. Reports depend on complete source records, ownership and pricing evidence; review the working papers before relying on them for tax reporting.

## This development update

All blockchain lookups now use connected Bitcoin Core nodes through Wallaby Agent. Core wallet history replaces address-range scanning. Builder inputs and change-cursor checks also use RPC. There is no public blockchain fallback. A pruned node must retain the required transactions in a loaded wallet; missing creating/spending evidence stops the trace rather than accepting an incomplete ledger. Load Node Wallets, review Ownership and Verify Balances after upgrading. Saved graph evidence is retained, but old balance snapshots require verification.

Verify Balances opens Wallet Balances. Cards share Ownership's vault/wallet/watch/SMSF icons; the new S-shaped SMSF mark replaces the bank symbol. Shorter balance tooltips show gain/loss, owner, SMSF, retained purchase cost and source records. Wallet Key Explorer has been removed; public descriptor validation remains where needed by node loading and PSBT construction.

Fee Estimates use Core at the lowest request priority, pausing behind foreground jobs. The simplified pending/mined block view adapts Mempool's AGPL component code and includes TXID tracking. BTC/AUD and historical currency/market price services remain external; CPI, when needed, comes from the supplied file. Signing and broadcasting use capability-gated local Agent approvals, not unrestricted remote RPC.

## Run from source

Python 3.11 or later. Ubuntu/Kubuntu dependencies:

```sh
sudo apt install python3 python3-pypdf python3-odf tzdata 7zip
python3 launch_wallaby_bitcoin_dashboard.py --no-browser
```

The shell launcher also defaults to no browser. The browser service uses **38951** and the Agent HTTP/JSON interface uses **56443**. Both bind to IPv4/IPv6 loopback. Reach them through authorised SSH tunnels; client-side forwarded ports may differ. Open `http://[::1]:38951/` on the tunnel client. No Bitcoin Core installation is needed on the server.

Create family users, connect Agents with the desired Core wallets loaded, then run Ownership → Load Node Wallets. This also reads fixed `addr()` policies from watch-only wallets and groups their addresses using Core labels. Upload each applicable profile's Bitaroo PDFs and the shared Exchanges ODS, then confirm ownership and select Verify Balances in Stage 3. The Extinct Wallets ODS is no longer uploaded. Wallet Balances then becomes available. Generate the separate family CGT report for tax totals/history, which show the logged-in profile only.

This is a trusted-family service behind SSH, not a public password-authenticated website. Node identity hints are not substitutes for SSH authentication. Core RPC cookies stay on the node computers.

## DEB installation

The package is `wallaby-dashboard`, version `1.0.0~dev8`, architecture `all`. Its installation creates the dedicated `wallaby-dashboard` service user, starts `wallaby-dashboard.service` immediately and enables it at boot. Application files go under `/usr/lib/wallaby-dashboard`; private durable records go under `/var/lib/wallaby-dashboard`; temporary workspace files use `/var/cache/wallaby-dashboard`.

The installer does **not** remove an older manually installed service. For the first DEB installation, retire the exact legacy manual service before installing to avoid port conflicts. For an existing Dashboard DEB, install the new DEB with `sudo apt install ./wallaby-dashboard_1.0.0~dev8_all.deb`; it preserves records and restarts the service. [Clean-install instructions](docs/CLEAN_INSTALL.md) apply only to the older manual installation.

## Source checks and build

```sh
python3 -m unittest discover -s tests -v
```

The cross-project integration test requires `WALLABY_AGENT_SOURCE` pointing to the matching Agent's `src` directory; otherwise that one test is skipped. The browser timing regression can be run with `node tests/ownership_navigation.js assets/app.js`. Other tests run independently. See [VALIDATION.md](VALIDATION.md).

To rebuild from source with Python 3 and `dpkg-dev` installed:

```sh
python3 packaging/build.py
```

The build writes the DEB and SHA256SUMS under `dist/`; it does not install or start services. Runtime family records are excluded from the package and repository. Preserve the bundled dependency licences under `vendor/`.

### Build 4 corrections

Balance verification now captures the Core-name-to-descriptor mapping before applying display labels, matching the CGT replay path. Joint transactions therefore reuse the already assigned wallets instead of creating unassigned raw-name duplicates. Unresolved transaction sources and destinations continue to use the shared ownership questions and evidence graph; confirming an owned counterparty without its records still stops the run. The extinct-wallet icon is a shared solid black T-Rex fossil skull. No tax formulas or annual allocation rules changed.

### Build 5

Balance Trace resolves deferred historical prices before optimised joint-parcel selection, using the same annual allocation and parcel policies as CGT. Name-based owner suggestions use immutable imported labels and unique whole-name matches; saved assignments win and suggested owners remain editable. A node wallet with an SMSF token retains uppercase SMSF, is forced to SMSF in both browser and saved ownership, and shows a retirement-fund icon. Four fixed icon slots accommodate vault, wallet, watch-only and SMSF indicators in that priority. The extinct symbol is a live T-Rex side silhouette. Unresolved traces show separate Date UTC and Time UTC lines; date-only input explicitly has no supplied time.

Exchange unit columns can now contain both trading directions: one signed negative asset and one positive asset. Fees remain nonpositive and use the actual outgoing crypto unit (or incoming BTC for a fiat purchase). Supplied BTC sales are uniquely linked to matching wallet sends or Bitaroo withdrawals so the funding transfer and sale are not counted as two disposals. Ambiguous matches stop for corrected records. No venue, person, transaction or address is embedded in this matching logic.

After upgrading to Build 5, run Verify Balances again. Earlier balance-cost snapshots remain available only as historical stored data and do not unlock current balances; confirmed trace evidence is retained for reuse.

## Build 7

Extinct wallets now come from labelled fixed-address `addr()` descriptors in a Core wallet with private keys disabled. Labels group addresses into logical wallets, independently of the parent Core wallet's name. `Load Node Wallets` queries all connected nodes and merges matching replicas. The obsolete historical-wallet ODS upload is removed; exchange ODS input remains unchanged. Historical group labels are read-only in Ownership and cannot be used as receiving/payment destinations.

A label `Earlier Wallet | LOST:DD/MM/YYYY HH:MM:SS UTC` declares irreversible loss of access at that time, without compensation. Build 7 records the affected retained outputs as a separate CGT event C1, consumes their quantity once, and calculates a loss from the traced reduced cost base using the existing proportional parcel convention. It does not use loss-date market value, indexation or a 50% loss discount. Existing annual loss application and carry-forward apply. The original blockchain outputs remain in the evidence graph. A label is a user declaration, not blockchain proof that a key was lost. Later receipts/spends conflicting with the declaration, missing history and unresolved acquisition cost require correction rather than an invented deduction.

After upgrading, run **Load Node Wallets**, load the exchange statements and **Verify Balances**, then regenerate CGT reports. Older descriptor scans and balance costs do not unlock the revised workflow. Your node must have completed its relevant watch-wallet rescan.

## Licence

This combined distribution is provided under GNU AGPL version 3. [Full licence](LICENSE.txt). Mempool component copyright and original pinned sources are retained in [vendor/mempool](vendor/mempool/UPSTREAM.md); other bundled libraries retain their notices. The running service provides corresponding source through its Source code link (`/source.zip`).

## Build 8

Nodes now distinguish connection from blockchain readiness. Vault cards use verified public policies and live node balances, with green/amber/red status and short unlock estimates. The Builder accepts fixed unsigned vault proposals, composes other wallets' payments before signing, and uses locally authorised Agent signature requests followed by combined-PSBT broadcast. Single and multiple senders use the same PSBT protocol.

**Agent update required:** partial signing, local broadcast approval and peer wallet transfers are capability gated. Older Agents do not expose those actions; there is no unsafe fallback to their old sign-and-broadcast operation. Only the Dashboard is updated in this build. See [Agent protocol and integration requirements](docs/AGENT_PROTOCOL.md). Missing-wallet orchestration exchanges public manifests and peer IPv6 hints; private wallet transfers never pass through the Dashboard. Public vault records and pending requests are included in profile backups.
