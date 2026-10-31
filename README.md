<p align="center"><img src="assets/logo.svg" width="112" alt="Wallaby Dashboard"></p>
<h1 align="center">Wallaby Bitcoin Dashboard</h1>
<p align="center">Bitcoin tools, wallet insights and family workflows.</p>

**V1 Build 17 · Development DEB available for Ubuntu/Kubuntu 26.04 LTS.** [Download the DEB](dist/wallaby-dashboard_1.0.0~dev17_all.deb) and verify [SHA256SUMS.txt](dist/SHA256SUMS.txt). This resets the public version numbering following the earlier V156/V157 work; it does not reset saved data formats.

[Program page](https://bluesilent9.github.io/wallaby-suite/dashboard/) · [Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/) · [Issues](https://github.com/bluesilent9/wallaby-dashboard/issues) · [User guide](docs/USER_GUIDE.md)

## What it does

- Australian CGT working papers, financial year totals and visual transaction histories for supplied family records.
- Shared Ownership, Address Book and wallet balances.
- Node-driven Fee Estimates, pending/mined blocks and transaction tracking, with Bitcoin Core unit conversions.
- Shared transaction templates, unsigned PSBT preparation and verified partial-signature collection. Private keys remain in Bitcoin Core; private signing remains local to the Agent and broadcast requires local Agent authorisation.
- Node discovery through Wallaby Agent, querying every connected node and deduplicating wallet replicas by public descriptors.

Vault monitoring uses public vault records and connected Core nodes. Reports depend on complete source records, ownership and pricing evidence; review the working papers before relying on them for tax reporting.

## This development update

Build 17 repairs Fee Estimates after clearing or loading a profile: the old background service is closed and replaced with a fresh service, so node-backed estimates can refresh without restarting Dashboard. Deferred updates are polled promptly; an empty mempool and an unavailable projection have explicit messages instead of an indefinite waiting label.

There is no separate Dashboard page. Use the sidebar to open tools; login, transaction completion/cancellation and finished activity return to Ownership. Historical ownership rows display **Extinct Wallet** as their source. The SMSF icon depicts a person leaning on a cane.

Wallet Builder shows Role name only for multisignature policies. Single Signature and plain Vault still select a producing Agent and storage; their single signing role is assigned automatically. No Agent code or protocol change is needed.

## Earlier Build 16 fixes

Build 16 fixes the Fee Estimates block view: projected fee-rate labels are compact and stay inside each cube, with a separate sats/vB label. The full node estimate remains in hover details and source data. This is display formatting only; fee conversions and transaction calculations retain their original precision.

Ownership no longer contains the retired requirement for a Wallets ODS upload. Load Records and Review Ownership remains disabled until node-wallet loading (or the explicit skip choice), Exchanges ODS and every applicable user's Bitaroo PDFs have been supplied. Non Bitaroo user declarations still apply. The obsolete “Select Wallets ODS and Exchanges ODS first” error is removed.

Wallet Builder discovery now displays progress beside the button, including requests in progress, identity counts, waiting replies and no eligible Agents. Old GET responses cannot overwrite a newer discovery. Repeated clicks reuse an outstanding challenge. Previously queued, validly signed stale replies are acknowledged and discarded so they cannot block a new reply; they never mark an Agent verified. Signature checks and manual fingerprint comparison remain required. No Agent protocol change or fallback identity source is introduced.

Build 15's live-node builder gating and bounded diagnostic logs remain. This build changes no Agent code, private-key handling, transaction/tax algorithms or broadcast behavior.

## Agent compatibility

Use **[Agent V2 Development Build 16](https://github.com/bluesilent9/wallaby-agent/tree/Development)** with Dashboard V1 Build 17. Both sides now implement unsigned vault composition, separately authorised partial signatures and broadcast, and the signed initial wallet-preview relay for direct Agent-to-Agent synchronisation. The Dashboard verifies the sender's signature and frozen request bindings before relay. Receiving users must still compare the sender identity fingerprint directly between Agent screens; a fingerprint supplied solely through Dashboard is not trusted.

Agent Build 10 or later is required for the current synchronization-permission and disc workflow; Wallet Builder requires Build 9 or later; the existing exchange requires Build 8 or later; older synchronization protocols have been removed. Each Agent receives its complete alphabetically ordered missing-wallet list, including labelled historical address groups. The signed version-2 preview preserves public vault policies, address labels and LOST timestamps. Persistent revisions withdraw outdated lists when peers disconnect. `recovery_required` means received but incomplete; only final receiver completion clears both Agents’ lists. History recovery is locally authorised on the Agent and retains its pruning configuration. No remote restart/reindex command is sent.

**Wallets** now filters by owner and by **Active Wallets / Extinct Wallets** (at least one selected). Extinct cards are grey and show confirmed UTC activity years rather than balances/gains. Addresses sharing a historical label are one logical wallet; different labels remain separate ownership assignments even inside one Core archive. **Make Extinct** prepares a public local-review request; its execution requires an Agent advertising `wallet_retirement`. Agent V2 Build 9 implements this operation after local approval. Retirement never deletes a private wallet, infers lost keys or automatically claims a tax loss. See [the protocol](docs/AGENT_PROTOCOL.md) and [validation](VALIDATION.md).

## Run from source

Python 3.11 or later. Ubuntu/Kubuntu dependencies:

```sh
sudo apt install python3 python3-pypdf python3-odf tzdata 7zip openssh-client
python3 launch_wallaby_bitcoin_dashboard.py --no-browser
```

The shell launcher also defaults to no browser. The browser service uses **38951** and the Agent HTTP/JSON interface uses **56443**. Both bind to IPv4/IPv6 loopback. Reach them through authorised SSH tunnels; client-side forwarded ports may differ. Open `http://[::1]:38951/` on the tunnel client. No Bitcoin Core installation is needed on the server.

Create family users, connect Agents with the desired Core wallets loaded, then run Ownership → Load Node Wallets. This also reads fixed `addr()` policies from watch-only wallets and groups their addresses using Core labels. Upload each applicable profile's Bitaroo PDFs and the shared Exchanges ODS, then confirm ownership and select Verify Balances in Stage 3. The Extinct Wallets ODS is no longer uploaded. Wallets then becomes available. Generate the separate family CGT report for tax totals/history, which show the logged-in profile only.

This is a trusted-family service behind SSH, not a public password-authenticated website. Node identity hints are not substitutes for SSH authentication. Core RPC cookies stay on the node computers.

## DEB installation

The package is `wallaby-dashboard`, version `1.0.0~dev17`, architecture `all`. Its installation creates the dedicated `wallaby-dashboard` service user, starts `wallaby-dashboard.service` immediately and enables it at boot. Application files go under `/usr/lib/wallaby-dashboard`; private durable records go under `/var/lib/wallaby-dashboard`; temporary workspace files use `/var/cache/wallaby-dashboard`.

The installer does **not** remove an older manually installed service. For the first DEB installation, retire the exact legacy manual service before installing to avoid port conflicts. For an existing Dashboard DEB, install the new DEB with `sudo apt install ./wallaby-dashboard_1.0.0~dev17_all.deb`; it preserves records and restarts the service. [Clean-install instructions](docs/CLEAN_INSTALL.md) apply only to the older manual installation.

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

That Build 8 integration boundary is completed in Build 9 with Agent Build 7.

## Build 9

The Dashboard verifies and relays a frozen signed public wallet snapshot bound to both peers, addresses, wallet policy and a fresh challenge. Only one pair is dispatched at a time, alphabetically. Temporary synchronisation busy responses preserve node status and delay inventory refresh. Duplicate delivery is idempotent; expired snapshots are not renewed, and failed/denied operations require an explicit new attempt. Requests, previews and outcomes survive restart/profile backup. Private wallet files travel directly between Agents after local approval, never through the Dashboard. Existing tax and Balance Trace calculations are unchanged.
