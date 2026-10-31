# V1 Development Build 21 validation

230 Python regression tests passed with the independent Agent Build 20 source, plus a final focused rerun of 12 fee/paging/diagnostic cases after small logging changes. All 17 JavaScript test programs and asset syntax checks passed. The real block renderer test includes live page-progress text.

The new interoperability test runs Agent Build 20's actual immutable snapshot formatter and NodeClient HTTP transport in an independent Python namespace. A synthetic 38,000-transaction Core response exceeds 16 MiB before compact paging. Dashboard collects all 38 pages, retains exact modified fees and dependencies, and produces four pending-block estimates. Consumed RPC results are released. The installed user's node and SSH services are not contacted.

Adversarial cases cover missing/truncated/reordered pages, changed capture identity/timestamps/totals, duplicate transaction IDs, missing/duplicate dependencies, invalid sizes/fees, expiry, connection replacement, empty mempools, cyclic graphs and negative modified fees. Incomplete captures cannot become pending blocks. Simulated page failures retain all four fee priorities and mined blocks, with explicit errors and correlation measurements. A 38,000-entry synthetic projection took about 0.85 seconds locally, excluding transport; this is not a timing guarantee or an installed-node measurement.

Diagnostics tests check RPC/refresh IDs, hostname, method, page offset, elapsed time, byte boundaries and lower-bound counts; distinguish Agent response limits from Dashboard validation limits; verify build/Agent context in exports; redact credentials and valid private keys; and retain the existing seven-UTC-day/1-MiB-daily limits. No payload, descriptor, address or transaction ID is logged by the new fee diagnostics.

Existing builder persistence/cancellation, locally authorised signing, retirement, multisig role separation, vault monitoring, ownership and CGT regression tests remain passing. No Agent code, production wallets, Core settings, installed service, user configuration, private keys, optical media or SSH settings were changed here. Actual installation on the family machines remains deployment testing.

The architecture-all DEB is checked for source parity, excluded runtime records, source download, IPv4/IPv6 web and Agent-listener startup, and systemd enable/start-at-install packaging. It is not installed here. Use matching Agent V2 Build 20 for paged projections; there is no oversized full-response fallback or increase to the ordinary remote RPC limit.
