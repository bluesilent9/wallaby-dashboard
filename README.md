<p align="center"><img src="assets/logo.svg" width="112" alt="Wallaby Dashboard"></p>
<h1 align="center">Wallaby Bitcoin Dashboard</h1>
<p align="center">Bitcoin tools, wallet insights and family workflows.</p>

**V1 Build 1 · Development source. The DEB build is pending.** This resets the public version numbering following the earlier V156/V157 work; it does not reset saved data formats.

[Program page](https://bluesilent9.github.io/wallaby-suite/dashboard/) · [Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/) · [Issues](https://github.com/bluesilent9/wallaby-dashboard/issues) · [User guide](docs/USER_GUIDE.md)

## What it does

- Australian CGT working papers, financial year totals and visual transaction histories for supplied family records.
- Shared Ownership, Address Book, wallet balances and public Wallet Key Explorer.
- Fee Estimates with Bitcoin Core unit conversions.
- Shared transaction templates, unsigned PSBT preparation and verified partial-signature collection. Private keys remain in Bitcoin Core; the Dashboard does not sign or broadcast.
- Node discovery through Wallaby Agent, querying every connected node and deduplicating wallet replicas by public descriptors.

Vault monitoring remains a placeholder. Reports depend on complete source records, ownership and pricing evidence; review the working papers before relying on them for tax reporting.

## This development update

Use **Wallaby Agent V2 Development Build 4 or later** with this Dashboard. Agent Build 4 corrects wallet RPC URL construction: Core wallet names are encoded exactly, without an added trailing slash. It also handles bounded work without dropping its connection. The Dashboard limits each Agent to two active commands, shares follow-up work across equivalent wallet replicas, and keeps fast loading errors visible on Ownership after Terminal Activity locks again. No wallet names or transaction identities are embedded as exceptions.

The production Agent client is exercised end to end against a strict simulated Core HTTP endpoint, including twelve loaded wallets and empty, Unicode and punctuation-containing names. Public blockchain history tracing remains in use; this update changes node metadata collection, not CGT arithmetic.

## Run from source

Python 3.11 or later. Ubuntu/Kubuntu dependencies:

```sh
sudo apt install python3 python3-pypdf python3-odf tzdata 7zip
python3 launch_wallaby_bitcoin_dashboard.py --no-browser
```

The shell launcher also defaults to no browser. The browser service uses **38951** and the Agent HTTP/JSON interface uses **56443**. Both bind to IPv4/IPv6 loopback. Reach them through authorised SSH tunnels; client-side forwarded ports may differ. Open `http://[::1]:38951/` on the tunnel client. No Bitcoin Core installation is needed on the server.

Create family users, connect Agents with the desired Core wallets loaded, then run Ownership → Load Node Wallets. Upload each applicable profile's Bitaroo PDFs, shared Extinct Wallets ODS and Exchanges ODS, and confirm ownership. Extinct Wallets ODS has one sheet, wallet names in row 1, and owned `TXID:output_index` entries beneath each name. Then generate the family CGT report. Totals/history show the logged-in profile only.

This is a trusted-family service behind SSH, not a public password-authenticated website. Node identity hints are not substitutes for SSH authentication. Core RPC cookies stay on the node computers.

## Planned DEB installation

The prepared package is `wallaby-dashboard`, version `1.0.0~dev1`, architecture `all`. Its installation creates the dedicated `wallaby-dashboard` service user, starts `wallaby-dashboard.service` immediately and enables it at boot. Application files go under `/usr/lib/wallaby-dashboard`; private durable records go under `/var/lib/wallaby-dashboard`; temporary workspace files use `/var/cache/wallaby-dashboard`.

The installer does **not** remove an older manually installed service. Retire the exact legacy service before installing the new package to avoid port conflicts. Build/install instructions will accompany the tested DEB; no V1 Build 1 binary has been published yet.

## Source checks and build

```sh
python3 -m unittest discover -s tests -v
```

The cross-project integration test requires `WALLABY_AGENT_SOURCE` pointing to the matching Agent's `src` directory; otherwise that one test is skipped. Other tests run independently. See [VALIDATION.md](VALIDATION.md).

After the explicit pre-build checkpoint, build with Python 3 and `dpkg-dev` installed:

```sh
python3 packaging/build.py
```

The build writes the DEB and SHA256SUMS under `dist/`; it does not install or start services. Runtime family records are excluded from the package and repository. Preserve the bundled dependency licences under `vendor/`.
