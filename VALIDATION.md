# V1 Build 26 validation

302 Python regression tests and 23 JavaScript suites pass. New tests cover per-person multipliers, effective-share limits and cent conservation, another profile’s distributor, an explicitly confirmed schedule, and retaining acquisition records while presenting wallet-owned addresses/latest statements separately.

Explorer tests use invented transactions and two independent fake nodes: pruned-first-node fallback, every-node attempts, discovering a newly connected wallet, deduplicating replicated unspent outputs, missing parents/unknown fees, null gettxout ambiguity, invalid inputs and invalid output indexes. Interface tests cover negative BTC amounts, click-through searches, old transactions without a block marker, exact half-up BTC/vB rounding and the automatic Ownership verification latch.

Live browser QA uses an isolated local profile and invented balances, public parser fixtures and fabricated node responses. It covers the distributor list across owners, moving assumptions into Stage 1, monthly schedule gating, personal multiplier dialogs, diagram spacing, Mempool search placement and compact transaction details. No family nodes or source documents are involved.

Debian package checks verify metadata, installed-source parity, exclusion of private/runtime inputs, source-download bundling, systemd enable/start instructions, and isolated IPv4/IPv6 web/Agent-health startup. The package is not installed on this machine. No Agent source, private wallet, signing request, transaction broadcast, SSH setting or production configuration is changed.

# V1 Build 25 validation

All 287 Python regression tests pass after network permission was restored, including the 15 previously blocked socket cases. New tests cover chained recipient shares, proportional shortfalls, automatic remainder targets, target restoration, card layouts, missing identities, cycles, duplicate parents, excessive shares and exact annual/payday conservation.

First-payday template tests use invented confirmed UTXOs and verify payment plus fee equals the source budget, layout conversion, ordinary TemplateStore round trips, missing tax/address/funds/rate rejection, offline status and applying the annual fee reserve once. No real node or Agent is contacted and no PSBT is created. Statement-source labels retain the evidence type and remove temporary paths without changing trace accounting.

The real Salary component and canvas run in DOM/API simulations: modal target/share controls, chained users, persisted dragging, naming cancellation, template save, revision scoping and preserving active inputs across delayed saves/results. Static checks cover bundled script order, graphical notification colours and pending-cube timing. Live browser checks cover funding/user diagrams, modal target edits, recalculation, template naming and the saved template appearing in Transaction Builder. Connection dots were measured at 12 × 12 CSS pixels after overriding shared form-button sizing. The HTML preview contains invented data and a fixed calculated-result snapshot.

Debian packaging is checked for source parity, absence of private/runtime inputs, source-download bundling and systemd enable/start maintainer instructions. Packaged-service startup over IPv4/IPv6 is tested in an isolated temporary profile; the package is not installed here. No production configuration, Agent code, wallet files, private keys, signing requests, broadcasts or SSH settings are changed.

## Earlier validation

# V1 Build 24 validation

The Salary Builder uses invented profile/account fixtures, actual official-source parser fixtures and the shared CGT engine. Coverage includes leap years, 53 weekly / 27 fortnightly paydays, monthly day clamping, combined personal/SMSF pools, inaccessible fund bridges, annual top-ups, birth-date horizons, user-ID ownership, cent reconciliation, shared/independent partner targets, asset reserves and recurring salary equivalents.

Tax tests cover ATO and statutory tables, changed source rates without application changes, malformed cumulative formulas, wrong-year rejection, failed refresh/cache retention, the official offset parser and profile backup/restore. HTTP tests exercise user-scoped start/save/calculate and reject stale cross-profile saves. Balance integration verifies retained personal/fund acquisition parcels. No personal source records are packaged or published.

The real Salary interface component is exercised with DOM/API simulations, including schedule controls, source selection, delayed saves/calculations and preserving active input nodes. Browser control is unavailable in this session: no browser-verified screenshot or visual-QA claim is made. The supplied HTML preview renders the actual component with invented read-only data.

272 Python regression tests and 21 JavaScript interface suites pass. Debian checks are recorded in the Build 24 review output. Packaging checks include source parity, excluded runtime inputs, IPv4/IPv6 web and Agent-health startup, and systemd enable/start-at-install instructions. The DEB is not installed here. Actual family-node deployment and visual confirmation remain user testing. No Agent code, real wallets, transactions, SSH configuration or installed service is changed.

## Earlier validation

# V1 Development Build 21 validation

230 Python regression tests passed with the independent Agent Build 20 source, plus a final focused rerun of 12 fee/paging/diagnostic cases after small logging changes. All 17 JavaScript test programs and asset syntax checks passed. The real block renderer test includes live page-progress text.

The new interoperability test runs Agent Build 20's actual immutable snapshot formatter and NodeClient HTTP transport in an independent Python namespace. A synthetic 38,000-transaction Core response exceeds 16 MiB before compact paging. Dashboard collects all 38 pages, retains exact modified fees and dependencies, and produces four pending-block estimates. Consumed RPC results are released. The installed user's node and SSH services are not contacted.

Adversarial cases cover missing/truncated/reordered pages, changed capture identity/timestamps/totals, duplicate transaction IDs, missing/duplicate dependencies, invalid sizes/fees, expiry, connection replacement, empty mempools, cyclic graphs and negative modified fees. Incomplete captures cannot become pending blocks. Simulated page failures retain all four fee priorities and mined blocks, with explicit errors and correlation measurements. A 38,000-entry synthetic projection took about 0.85 seconds locally, excluding transport; this is not a timing guarantee or an installed-node measurement.

Diagnostics tests check RPC/refresh IDs, hostname, method, page offset, elapsed time, byte boundaries and lower-bound counts; distinguish Agent response limits from Dashboard validation limits; verify build/Agent context in exports; redact credentials and valid private keys; and retain the existing seven-UTC-day/1-MiB-daily limits. No payload, descriptor, address or transaction ID is logged by the new fee diagnostics.

Existing builder persistence/cancellation, locally authorised signing, retirement, multisig role separation, vault monitoring, ownership and CGT regression tests remain passing. No Agent code, production wallets, Core settings, installed service, user configuration, private keys, optical media or SSH settings were changed here. Actual installation on the family machines remains deployment testing.

The architecture-all DEB is checked for source parity, excluded runtime records, source download, IPv4/IPv6 web and Agent-listener startup, and systemd enable/start-at-install packaging. It is not installed here. Use matching Agent V2 Build 20 for paged projections; there is no oversized full-response fallback or increase to the ordinary remote RPC limit.


## V1 Build 23

- 247 Python regression checks and 19 JavaScript interface regression programs pass, including current Agent V2 Build 22 read-only transport.
- Separate personal/SMSF statement declarations, fixed fund exchange ownership, isolated entity trace facts, live watch/private replica detection, authenticated-traffic liveness, all-node proposal leasing and Salary start/save/restore HTTP are exercised.
- An isolated purchase/withdrawal fixture proves personal and fund acquisition cost and unrealised P/L remain separate. Fund replay cannot invoke annual fund CGT. Shared CGT/Balance Trace allocation and parcel logic remains covered by the existing joint-transaction fixtures.
- The six supplied SMSF AUD/BTC statements parse with complete coverage and paired trades. Personal source documents and logs are not packaged or published.
- Browser checks cover calendar-only DOB, saved birth dates, independent upload controls, Salary connection/amount entry and restored projections, multisig icon explanations and Mempool display rounding. Workspace tests include GET responses delayed across local autosaves.
- Debian source parity, absence of financial inputs, startup over IPv4/IPv6, Agent health and service enable/start maintainer instructions are checked. The package is not installed on this development machine.
- Production node disconnect/recovery and an end-to-end run against the user's actual wallet histories still need deployment testing. No real transactions, wallet mutation or private-key access occurs in these checks.
