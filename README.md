<p align="center"><img src="assets/logo.svg" width="112" alt="Wallaby Dashboard"></p>
<h1 align="center">Wallaby Bitcoin Dashboard</h1>
<p align="center">Bitcoin tools, wallet insights and family workflows.</p>

**V1 Build 4 · Development DEB available for Ubuntu/Kubuntu 26.04 LTS.** [Download the DEB](dist/wallaby-dashboard_1.0.0~dev4_all.deb) and verify [SHA256SUMS.txt](dist/SHA256SUMS.txt). This resets the public version numbering following the earlier V156/V157 work; it does not reset saved data formats.

[Program page](https://bluesilent9.github.io/wallaby-suite/dashboard/) · [Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/) · [Issues](https://github.com/bluesilent9/wallaby-dashboard/issues) · [User guide](docs/USER_GUIDE.md)

## What it does

- Australian CGT working papers, financial year totals and visual transaction histories for supplied family records.
- Shared Ownership, Address Book, wallet balances and public Wallet Key Explorer.
- Fee Estimates with Bitcoin Core unit conversions.
- Shared transaction templates, unsigned PSBT preparation and verified partial-signature collection. Private keys remain in Bitcoin Core; the Dashboard does not sign or broadcast.
- Node discovery through Wallaby Agent, querying every connected node and deduplicating wallet replicas by public descriptors.

Vault monitoring remains a placeholder. Reports depend on complete source records, ownership and pricing evidence; review the working papers before relying on them for tax reporting.

## This development update

Ownership Stage 3 now ends with **Verify Balances**. It saves every ownership assignment, opens Terminal Activity and runs the new **Balance Trace** for the family. Nonzero personal wallet holdings are traced back to funding, including receive-only wallets; SMSFs are excluded from personal cost tracing. Unknown sources require an explicit answer, and a missing owned source stops the process until its records are provided. Retained lots use the existing transfer/fee and joint-payment accounting rules. Ambiguous joint-payment history reuses the established annual allocation engine; it does not publish a CGT report during balance verification.

Wallet Balances unlocks after Balance Trace, independently of tax reports. Snapshots and a shared evidence graph are stored under `data/balance-trace.json`, included in profile backups. The graph merges transactions, owned outputs, source records, ownership assignments and explored trace branches using stable identities. Balance, Exchange and Wallet traces extend that structure; confirmed ownership relationships can resolve later questions about the same addresses. Group answers remain group facts, and unknown ownership is never treated as an external source. Evidence is scoped to the family ownership/record signature and profile; changing records or owners invalidates it. Earlier Build 3 ownership answers are migrated into graph facts automatically. Income rankings and asset-basis forms retain their existing specialised storage. Concurrent runs merge evidence rather than overwriting another trace’s branches. If an incomplete trace stops with an error, its confirmed discoveries are preserved without marking balances as verified. Derived event classifications are labelled as recomputed interpretations; parcel allocations and tax arithmetic are not stored as blockchain facts. CGT no longer performs a separate current-balance replay; it uses the saved Balance Trace snapshot. Financial Year Totals and CGT Event History still require the CGT report.

Reopening a browser restores all completed Ownership stages from server data. The shared extinct icon is now a larger solid-black T-Rex skull. Suggested descriptor-wallet display names omit the standalone word “watch”; exact Core RPC names remain unchanged. An eye icon denotes confirmed watch-only metadata from `getwalletinfo` (`private_keys_enabled=false` across wallet replicas), not an assumption based on the name. Reload node wallets once to collect this metadata from older installations.

DEB upgrades preserve records and restart the service. Wallaby Agent V2 Development Build 4 or later is required. Public blockchain tracing and actual CGT calculation rules remain in place.

## Run from source

Python 3.11 or later. Ubuntu/Kubuntu dependencies:

```sh
sudo apt install python3 python3-pypdf python3-odf tzdata 7zip
python3 launch_wallaby_bitcoin_dashboard.py --no-browser
```

The shell launcher also defaults to no browser. The browser service uses **38951** and the Agent HTTP/JSON interface uses **56443**. Both bind to IPv4/IPv6 loopback. Reach them through authorised SSH tunnels; client-side forwarded ports may differ. Open `http://[::1]:38951/` on the tunnel client. No Bitcoin Core installation is needed on the server.

Create family users, connect Agents with the desired Core wallets loaded, then run Ownership → Load Node Wallets. Upload each applicable profile's Bitaroo PDFs, shared Extinct Wallets ODS and Exchanges ODS, and confirm ownership. Extinct Wallets ODS has one sheet, wallet names in row 1, and owned `TXID:output_index` entries beneath each name. Select Verify Balances in Stage 3. Wallet Balances then becomes available. Generate the separate family CGT report for tax totals/history, which show the logged-in profile only.

This is a trusted-family service behind SSH, not a public password-authenticated website. Node identity hints are not substitutes for SSH authentication. Core RPC cookies stay on the node computers.

## DEB installation

The package is `wallaby-dashboard`, version `1.0.0~dev4`, architecture `all`. Its installation creates the dedicated `wallaby-dashboard` service user, starts `wallaby-dashboard.service` immediately and enables it at boot. Application files go under `/usr/lib/wallaby-dashboard`; private durable records go under `/var/lib/wallaby-dashboard`; temporary workspace files use `/var/cache/wallaby-dashboard`.

The installer does **not** remove an older manually installed service. For the first DEB installation, retire the exact legacy manual service before installing to avoid port conflicts. For an existing Dashboard DEB, install the new DEB with `sudo apt install ./wallaby-dashboard_1.0.0~dev4_all.deb`; it preserves records and restarts the service. [Clean-install instructions](docs/CLEAN_INSTALL.md) apply only to the older manual installation.

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
