# V1 Build 6 — validation

The Python regressions include receive-only personal balance provenance, zero/SMSF exclusion, complete non-joint family balance verification without generating tax reports, context-bound persistent decisions, profile isolation and watch display naming. The actual Agent transport fixture confirms that a wallet with a normal name is recognised as watch-only from Core metadata, while names containing Watch are not classified as watch-only when private keys are enabled.

JavaScript regressions cover the Build 2 poll race and restored descriptor readiness in a fresh page session. Existing ownership/transport/service checks are retained. Package inspection and IPv4/IPv6 extracted-runtime smoke checks are performed before publication. No supplied personal diagnostic logs or wallet records are included in the source/package.

Balance Trace reuses the established historical lot/accounting logic; real family input completeness and online quote availability remain live-installation checks. Unknown owned sources fail rather than invent a cost base. CGT no longer invokes the removed portfolio current-basis replay.

Corrected Build 3 adds graph regressions for converging/splitting transaction topology, idempotent import, source/destination fact reuse, profile isolation, conservative group answers, exact-output ownership resolution, migration and concurrent branch merging. A real Balance Trace then fresh Exchange Tracer asks once for a shared relationship and preserves its expected cost basis. Synthetic allocation records are excluded from factual graph nodes. The existing tax formulas and financial-year optimisation engine are unchanged.

Partial evidence persistence is tested separately from balance readiness: an incomplete trace cannot unlock Wallet Balances, and saving further evidence retains an existing verified snapshot. Statement identities are independent of temporary job directories and distinguish separate equal-amount entries by their recorded balance/occurrence.

Build 4 adds a two-owner, three-renamed-descriptor joint-payment fixture through the real ownership adapter and joint expansion. The Build 3 implementation fails with MissingOwnership; Build 4 preserves assigned identities and payment quantities. Real Balance Trace planner tests cover unresolved source/destination No/No continuation, Yes-to-source failure, and Yes-to-destination failure after the relevant question. The icon shares one SVG definition across all placements; its 128×96 silhouette is visually inspected.

Build 5 regressions compare retained joint-parcel costs against a CGT replay with deferred prices; validate immutable source-name hints, ambiguity handling, forced server-side SMSF, UTC prompt precision, signed two-way exchange rows and a Bitaroo-funded single sale without duplicate disposal or an unresolved-destination question. GUI fixtures cover saved-owner precedence, editable suggestions and ordered SMSF markers. Personal research and spreadsheets are kept outside the application/package and public repository.

## Build 6 checks

43 Python tests pass, including node-only reconstruction through pruned-wallet `gettransaction`, exact change/transfer/fee conservation, multi-node routing, unavailable historical spend failure, raw TXID verification, stale confirmation replacement, incomplete sending-wallet history rejection, private worker bridge authentication, signing-method rejection, foreground/background broker ordering, deferred fee work, dependency packages and projection conservation. Three JavaScript suites pass, including concise balance tooltip fields, negative gain formatting, metadata icon priority and fee-unit/marker behaviour. Existing balance-versus-CGT joint-parcel checks continue to pass.

The block view was checked in a desktop browser using clearly synthetic node/market responses: all four fee cards, sub-satoshi copying restrictions, pending/mined blocks, controls and a tracked mempool marker. Actual family-node availability and complete extinct-wallet history were not verified from this development environment.

The DEB is extracted and compared byte-for-byte with the runtime source; its browser and Agent endpoints are exercised on IPv4 and IPv6. Source-download contents and the no-node fee response are checked. No service is installed on the developer machine.
