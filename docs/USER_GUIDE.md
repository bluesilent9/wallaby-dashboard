# Wallaby Bitcoin Dashboard — V1 Build 1

Extract the complete ZIP. Start the service with `./Start_Wallaby_Bitcoin_Dashboard.sh`, or:

```sh
python3 launch_wallaby_bitcoin_dashboard.py --no-browser
```

Both default to port **38951**. Use your SSH tunnel and open `http://[::1]:38951/`, or your chosen client-side tunnel port. IPv4 loopback also works. `--port` overrides the server port. The shell launcher does not open a browser.

Python 3.11+, pypdf and odfpy are required. On Ubuntu/Kubuntu:

```sh
sudo apt install python3-pypdf python3-odf 7zip
```

The `tzdata` Python package in requirements.txt supplies IANA timezone rules on systems without a system timezone database. Ubuntu/Kubuntu normally provides these through its system `tzdata` package.

Public descriptor derivation and raw transaction parsing are bundled; no Bitcoin Core installation or wallet unlock is needed on the dashboard server.

## V1 Build 1 setup

1. Open **Administration**, then select or create a family user. The sidebar and header remain visible before login; other pages unlock after a user is selected.
2. Connect each node's Wallaby Agent, load the desired wallets in Bitcoin Core, then open **Ownership** and select **Load Node Wallets**. Every connected node is queried over read-only RPC. Identical public locking descriptors identify wallet copies, independently of wallet names; matching copies share RPC work. Ownership suggests the first reporting node's name. Different wallets with identical names stay separate. Public blockchain tracing then runs as before; after success Ownership opens at Stage 2. No console exports, private keys or wallet unlock are needed. Unloaded wallets or missing RPC capabilities stop discovery with an actionable error rather than accepting a partial collection.
3. Open **Ownership**. Provide historical complete-year and current-year statements together in the **Bitaroo PDFs** card for each family profile, plus shared Wallets ODS and Exchanges ODS. Any logged-in user can provide the whole family’s data. A missing card is red, a supplied card is green; select **Non Bitaroo user** to disable that profile’s PDF card in grey. All cards must be complete or nonapplicable before loading records. Current-year statements are optional when either your browser's local date or UTC date is 30 June or 1 July. Other inputs remain required. Each Bitaroo period needs matching AUD/BTC statements. Optional price quotes, CPI and tax-context files remain available.
4. Select **Load records and review ownership**. The dashboard reads the descriptor history already discovered, loads the other records and asks for each wallet's owner and SMSF status. Bitaroo account owners are fixed by their Stage 2 upload cards; exchanges cannot be marked as SMSF accounts. To refresh descriptor history, select Load Node Wallets in Stage 1; the old separate refresh checkbox is removed. Save all assignments.
5. Address Book, Wallet Key Explorer and CGT setup unlock. Choose a matching tolerance and build the report. It calculates all supplied family profiles and every financial year from the earliest record through the current year. Financial Year Totals and CGT Event History display **only the logged-in user**. Use their left/right year arrows; future years cannot be selected. The initial year uses the browser’s local year, with the previous-year exception on UTC 30 June / 1 July. Wallet Balances reuses each family owner’s known acquisition costs. Missing basis and SMSF costs can still be unavailable.

The Bitcoin Core wallet CSV importer has been removed. Existing CSV-based Ownership snapshots need to be replaced by loading descriptors and completing a V1 Build 1 Ownership run. Wallets ODS now uses the exact owned-output format described below. Exchanges ODS retains its established format. Descriptor wallets retain Bitcoin Core’s original source name and stable key-based ID, with an editable display name saved in Ownership. ODS wallet names come from row 1; exchange names come from their sheets. They are not inferred from names, notes or built-in identities.

## V1 Build 1 changes

- Ownership Stage 1 loads wallet names, complete public descriptors and legacy received-output metadata from every connected node. No manual descriptor/history paste workflow is shown.
- Wallet replicas are grouped by overlapping public locking descriptors, ignoring key-origin annotations and checksum spelling. Their descriptor ranges/cursors and received outputs are merged. Unrelated equal names are disambiguated for display, not combined.
- The Extinct Wallets ODS still uses one tab of wallet-name columns and exact owned `TXID:output_index` values. No broad historical date/amount search was restored.
- Ownership reserves three left-aligned icon slots: funded vault evidence, extinct fossil and wallet, or the exchange symbol as applicable. A vault descriptor alone is insufficient: positive received-output history must establish funding of its locked address. Historical funding remains evidence after spending or expiry. The Vaults page remains a placeholder.
- Requests to different nodes run concurrently. Additional address ownership reads rotate among replicas, with read-only fallback. Per-node concurrency is bounded and consumed internal RPC results are released to avoid filling the broker.
- Node metadata loading never contacts a public blockchain service. The existing public descriptor history tracing and historical ODS tracing remain in place for this release.


## Retained V154 behaviour

Terminal Activity is available only while a process is running or waiting for an answer, and its Dashboard card is hidden while idle. Administration → Logs downloads the browser workspace's UTF-8 `Wallaby_Dashboard.log`, including earlier jobs/profile switches. Fee Estimates refreshes on every visit, then every two minutes. Vaults remains the Ownership-gated **No Vaults Detected** placeholder. Remembered node identities survive profile save/load; live connections and RPC requests/results do not. Duplicate hostname/MAC notices remain red, non-dismissible, and clear when the conflict ends.

### Dashboard profile archives

**Save** creates a genuine `profile.7z`, using 7-Zip maximum compression (`-mx=9`). Install the system 7-Zip command for this feature; the other dashboard features do not require it. On Ubuntu/Kubuntu: `sudo apt install 7zip`. Windows/macOS can use an installed `7z` or `7zz` command on PATH.

The archive contains `config.cfg`, `descriptors.cfg`, `data/` and `templates/`, where present. This preserves family users, ownership, source records, public descriptor history, saved personal CGT reports, shared templates, prepared PSBT workflows and notifications. Browser tokens, unsaved diagrams, activity logs, application code and disposable public network/price caches are excluded. Original uploaded source files outside the dashboard are never modified.

Firefox uses its normal download location or Save dialog, with `profile.7z` as the supplied filename. Web pages cannot enforce a directory chooser or prevent the user/browser renaming downloads. The file picker filters `.7z`; the dashboard validates the exact basename `profile.7z` before uploading and the server validates it again. Rename a browser-created `profile (1).7z` back to `profile.7z` before loading.

**Load** checks archive type, paths, size limits, links, manifest hashes, config schema, public descriptor identities, saved ownership files, template diagrams, transaction structure and report ownership before replacing live files. Invalid archives leave the current profile unchanged. Installation errors roll back the whole replacement. Only Wallaby archives with the included manifest are accepted. Archives may be up to 256 MB compressed and 2 GB expanded.

**Load and Clear replace the entire shared family dashboard**, not just one logged-in user. The confirmation states this; connected browser sessions are reset to Administration. Clear removes the shared config, descriptor collection, data and templates. Save first to preserve them. Active discovery/calculation jobs must finish or be cancelled before saving, loading or clearing. Ordinary clients remain concurrent; profile operations obtain exclusive access to shared data during replacement/snapshotting.

### Historical owned-output format (introduced in V147)

Wallets ODS contains exactly one tab. Each populated column has its wallet name in row 1, followed by `TXID:output_index` text values for outputs owned by that wallet. Indexes start at zero. Include received outputs and change, whether spent or unspent. Each output may belong to only one supplied wallet; do not also supply that wallet through descriptors. Do not list recipient outputs under the sending wallet.

The importer retrieves each specified transaction directly and checks the output's spending link. It reads complete confirmed spending transactions to recover outgoing payments, network fees and change, then reconstructs UTC dates, amounts and balances. It does not search blocks, amounts or date windows to discover historical wallet transactions. Existing matching-window settings still apply where Bitaroo/exchange statements do not contain blockchain IDs. Supplied output indexes distinguish equal-sized outputs of the same transaction.

Confirmed transaction bodies are cached. Spend status is refreshed each run because an unspent output can subsequently be spent. One shared lookup client supplies Ownership, Address Book, CGT history and live balance evidence; opening Transaction Templates uses balances saved by Ownership rather than repeating ODS/network work. Live balances still check the known addresses for new activity. New wallet addresses/change outputs must be supplied through the ODS or covered by descriptors.

The old six-column per-wallet-tab ODS format is removed. Upload the new file and rerun Ownership after upgrading. No user records are bundled in the release.

### Joint-payment accounting convention

Public blockchain confirmation times are used, not exchange execution times. Wallet ownership comes from supplied public descriptors, owned outpoints and confirmed Ownership assignments. No identities, personal addresses or transaction IDs are built into the program. A retired wallet must not also be supplied through descriptors: reload the public descriptor catalogue without it when moving it into the historical ODS.

For jointly funded payments, the program uses the requested **accounting assumption**, not a claimed on-chain input/output mapping. The network fee is split equally between source wallets, with deterministic whole-satoshi rounding. Every input budget and every output amount, including wallet-controlled change, is conserved. Senders’ existing parcels are reserved before processing any of that transaction’s receipts; a new output cannot fund an input of the same transaction. Parent transactions sharing a block timestamp are processed before their children.

The search compares proportional allocations, deterministic transport vertices and whole-satoshi cycle refinements. It also compares proportional, holding-period, highest/lowest-cost and oldest/newest parcel policies for each source wallet. Candidate scores use the existing annual CGT calculation, including losses before applicable discounts. The primary objective is the sum of the family’s calculated taxable capital gains. If several tested minimum-gain allocations remain and multiple affected owners have same-year disposals, Terminal Activity asks for predicted-income ordering, highest to lowest. This is a tie-break, not a calculation of personal income tax. Remaining ties favour the proportional allocation.

The search is bounded (three coordinate sweeps per transport pass, with deterministic vertex limits on large diagrams). It is **not a certified global optimum**, and the report discloses the algorithm version, comparisons, policies, assumptions and satoshi matrix. A candidate with excluded disposal proceeds or unreconciled source quantities cannot be treated as a tax saving. Other assets’ CGT ledgers and unrelated income/losses are not invented.

Each transaction’s allocation is optimised using only economic activity up to the end of its **own financial year**, or the current instant for an open year. Matching statement entries and dependency planning are bounded too. Earlier-year decisions are recomputed first and never reoptimised using later-year activity. The open year can change as data arrives. A completed year reproduces its allocations given the same historical inputs, prices, ownership/settings, income-ranking answers and algorithm version. Corrected past records, different answers, revised valuation inputs or a different algorithm are changed inputs and may change results.

There is **no saved allocation ledger**, and neither saved transaction templates nor completed builder allocations are consulted. Choices exist in calculation memory; the generated report includes their audit details but is not read to seed another run. Profile-keyed opening capital losses apply before the earliest supplied FY; losses calculated within the loaded history carry forward automatically. The optional tax-year timezone overrides the browser timezone, which otherwise supplies the family replay calendar.

Optional whole-return tax context now needs explicit user/year scope, so another person's return or a different year is never reused accidentally. The JSON format is `{"users": {"User name or ID": {"2026-27": {"basic_tax": "0", "basic_tax_without_gain": "0"}}}}`, with the existing optional calculation fields inside each year object. Use `{"users": {}}` when no context is supplied. User names are matched without case sensitivity; stable profile IDs are also accepted.

V1 Build 1 does not establish that this accounting convention has ATO approval. The report identifies it as an assumption for review rather than representing it as a recovered fact.

## Descriptor wallet display names

In Ownership, Core descriptor wallets have an editable name field. Suggestions follow the earlier CSV naming rules: underscores become spaces, words are title-cased, and “Wallet” is appended if it is not already a word in the name. For example, `family_savings` suggests `Family Savings Wallet`; `emergency_wallet` suggests `Emergency Wallet`. Edit the suggestion and select **Save all ownership**. Existing custom labels are preserved.

Only descriptor wallets can be renamed. ODS wallet and exchange names remain fixed. Names must be non-empty and unique across the loaded wallets and exchanges. The selected label is shared through config.cfg and used in Address Book, Wallet Key Explorer, balances and CGT history. Changing a label invalidates an existing report, which can then be rebuilt with that label.

These are display aliases: Bitcoin Core’s wallet name, descriptor strings, stable wallet IDs and saved scan fingerprint do not change. Reloading unchanged node descriptors or rerunning Ownership retains the saved alias. RPC requests always retain the original wallet name for each node.

## Older migrated wallets

Load Node Wallets accepts Core's `combo(...)` public descriptors and recognises hardened child paths that cannot be derived from an extended public key. It automatically requests each wallet's `listsinceblock` history over RPC, including received change outputs. No additional console command or paste field is required.

Core supplies the output references belonging to the wallet. The existing public tracer obtains their scripts from blockchain transactions and queries their histories and balances. It does not attempt hardened public derivation or silently omit legacy policies. Reload Node Wallets to collect newly generated hardened outputs. Stage 2 remains locked until collection and tracing finish successfully. No private keys, wallet unlock, CSV exports or extra software are required.

`combo` policies cover P2PK, P2PKH and, for compressed keys, native and wrapped SegWit. P2PK has no standard address, so scanning it requires the TLS Electrum indexer rather than the address-only HTTPS fallback. Wallet Key Explorer lists the address-bearing forms and explains why hardened public descendants cannot be displayed.

This behaviour follows the [Bitcoin Core v31 public descriptor and wallet RPC implementation](https://github.com/bitcoin/bitcoin/tree/v31.0/src/wallet/rpc). Private exports remain rejected. No particular wallet name, key, transaction ID or address is built into the compatibility handling.

## Wallet Key Explorer

This page lists **only saved descriptor wallets**. Historical ODS wallets and exchanges do not appear. Select a wallet card, optionally enter the address Bitaroo wants verified, and select **Show descriptor breakdown**. Public account/provided keys, branches, derived public keys and address lists remain available, including range controls and target-address matching. Unsupported explorer policies are reported explicitly.

There is no descriptor paste field on this page; it uses the saved node descriptor collection. Ownership must be complete before it opens. Addresses appear in derivation order; the first address is not necessarily the first used address. Core's next-index cursor describes address generation, not proof of blockchain use.

## Blockchain discovery and accounting

V1 Build 1 scans every address index covered by Bitcoin Core’s `next_index` cursor, including unused addresses inside that generated prefix, then looks ahead through 20 consecutive unused indexes on each branch. Finding activity extends the scan until another 20-index unused gap is reached. It does not impose a 100- or 200-address hard limit: a wallet that actually generated thousands of addresses still needs those indexes checked. The thousands of unused addresses in Core’s pre-generated keypool are no longer treated as generated/used history. If a descriptor has no cursor, the scanner conservatively covers its entire exported range instead. Receiving, change and inactive descriptors remain included.

Let Core finish synchronising before loading node wallets. Core advances its cursor when it generates addresses or observes outputs at higher indexes, including during wallet restoration. Keep the node-loaded descriptors current if you generate many new addresses after the last node load; a 20-address gap cannot discover an arbitrary future jump beyond that gap. Reload Node Wallets when Core adds descriptors or generates more than 20 new unused addresses beyond the saved cursor. Legacy hardened paths use public received-output metadata collected automatically from Core. No wallet-specific shortcut or special limit is built in.

Within one descriptor discovery run, identical scripts are queried only once, including overlap with the legacy public-history supplement. Loading descriptors or explicitly refreshing in Ownership collects fresh network histories; ordinary Ownership runs reuse the completed descriptor snapshot. Raw immutable transactions remain cached. Scan coverage, Core cursors and the actual 20-index lookahead are recorded with each report. The 100,000-index per-branch safety budget remains; exhausting it stops the run without accepting a partial history.

Dropped TLS indexer connections now trigger a bounded reconnect and retry of the complete read-only query. Failed batches never contribute partial results. After three failed attempts the client tries the HTTPS explorer where its address interface supports the script. HTTPS lookups also have bounded retries. Network progress/retry messages appear in Terminal Activity. If no supported source can provide complete data, descriptor discovery or the requested Ownership refresh stops with an error instead of inventing a balance or silently skipping transactions.

Confirmed transaction times use **Bitcoin block timestamps in UTC**, rather than Core CSV first-seen times. These are confirmation times, not proof of exchange trade-execution times, and can differ from earlier CSV reports. Transaction IDs are checked against decoded raw transactions. Public indexers supply histories and block headers; this is not an independently validated full node.

Owned outputs, including change, are retained in the physical wallet balance. Ordinary single-source transactions exclude the sender’s change from external payments; joint transactions use the disclosed accounting convention above. Network fees come from decoded inputs minus outputs. Separate output indexes distinguish multiple payments within one transaction. Unconfirmed movements affect the displayed pending balance but are excluded from the CGT ledger until confirmed. They can make acquisition-cost/profit information temporarily unavailable.

Jointly funded transactions with identified family sources use the year-bounded accounting convention above. Unknown contributing inputs still stop a personal-tax allocation: provide the missing source descriptors or owned outpoints. Transactions involving only external/SMSF sources are not optimised as personal holdings. Public blockchain data alone does not prove input/output attribution. Builder planning data remains confined to its signing workflow and is not imported as tax allocation evidence. The dashboard does not broadcast transactions.

The tax arithmetic, loss ordering, discount rules and graph presentation from V131 are retained. The ingestion change can alter inputs where CSV timestamps or fee information differed. Real-wallet comparisons require importing the actual public descriptors; no user-specific descriptor, address or TXID associations are bundled.

## Saved data and freshness

Keep these together when upgrading, moving or backing up the service:

- `config.cfg`: family profiles, ownership, latest parsed Bitaroo ledger/coverage and per-profile Ownership metadata.
- `data/`: supplied records, reconstructed descriptor history, saved reports and remembered node identities (`nodes.json`).
- `descriptors.cfg`: the shared public descriptor collection, separate from config.cfg.

A fresh login restores the profile's last successful Ownership inputs and assignments. Completed family CGT reports are saved in `data/reports/` and reopen for each logged-in profile, including after a service restart. Activity logs and unresolved-question answers are not reused by a later calculation. Starting a new CGT run discards the previous reports for every profile before calculation begins; failure or cancellation does not restore them. Changing the source records or ownership requires a new report. Changed descriptors invalidate the old Ownership scan until it is rerun. Ownership is a shared family process: its saved inputs and complete assignments unlock CGT setup and Transaction Builder for every profile. Each profile sees its own totals and history; the saved family calculation is separated into personal responses. Clearing configuration also removes the saved descriptor collection; original source files are not changed.

Run-age warnings refer to completed shared processes, using the viewing browser’s local calendar and timezone. The newest successful Ownership process for any family profile refreshes the family Ownership age; any successful family CGT run refreshes the CGT age. Failed/cancelled runs do not make data fresh. Upgrading a V151 saved report without a completion timestamp requires one new CGT run to establish that timestamp.

On CGT Event History, CGT Report, Financial Year Totals, Wallet Balances, Transaction Builder and Vaults, one read notice is generated on visiting the page. Ownership warnings replace CGT warnings. The same notice appears on Notifications with its X disabled. A new run updates/removes the notice for each viewer; obsolete statement-coverage notices are retired when the new freshness check runs.

## Access and packaging

Family profile selection is password-free because access is controlled by your SSH tunnels. The service binds loopback, supports IPv4/IPv6 and different client-side tunnel ports, and separates browser workspaces. Shared configuration writes are locked and atomic. Public descriptors cannot sign transactions, but reveal wallet addresses/history and are private financial information. Only derived script hashes/addresses, not descriptor exports, are sent to public indexers. Never export with `true` or paste private keys, seeds or passphrases; private descriptors are rejected.

The fee estimator, history graphs, Address Book and offline HTML reports remain available. Zero-balance historical ODS wallets remain hidden from balance/address cards; descriptor wallets remain visible at zero. Historical exchanges are excluded from Address Book. Saved tax HTML does not contain the public descriptor collection.

This ZIP contains runtime files and bundled-library notices only: no user configuration, wallet exports, test records, reports or caches.

The builder uses the familiar separation of draggable nodes and connection handles described in the [React Flow handle documentation](https://reactflow.dev/learn/customization/handles). Its implementation uses the dashboard’s bundled JavaScript and SVG; no external UI library or online script is required.

The builder’s **Next** button becomes available only with at least one positive payment, a positive Network Fee contribution and a successful confirmed-UTXO funding estimate. Missing destinations, insufficient funds and payment outputs below standard dust policy keep it disabled. Next now prepares the complete unsigned transaction, rechecking confirmed inputs and deriving change from each funding wallet’s public internal descriptor. It opens Stage 3 below the diagram; the prepared diagram is fixed until its creator selects Reopen for editing. For multiple sending wallets, every sender, including a fee-only sender, receives the same PSBT for signing. Stage 3 accepts signed PSBTs from each wallet owner; Stage 4 combines verified signatures for the transaction creator to download. A single sending wallet instead uses three stages, including the template selector, with signing and broadcasting entirely in Bitcoin Core. The dashboard does not broadcast.

## Maximum spend and signing notifications

Select **Send maximum** in a payment connection to disable manual Bitcoin entry. One connection per source wallet can use maximum; other outgoing amounts are deducted first, along with that source’s allocated fee. All confirmed spendable inputs from that wallet are selected, and no change is returned to it. The amount recalculates after changes, on template loading, and again using fresh UTXOs before PSBT construction. Unconfirmed Bitcoin is excluded. A wallet cannot end empty if this same transaction also pays into it; that conflicting plan is rejected.

Prepared PSBTs contain one complete transaction, verified previous transactions and public change metadata. Change addresses are derived within the exported active internal range, checked for existing blockchain activity and advanced beyond dashboard reservations. Refresh the descriptor export if its range or change cursor is exhausted. Fee calculations use conservative signed sizes, so the final fee rate may be slightly higher after signing.

Stage 3 opens after preparation for the creator or sending owner. **Notifications** is also available through the bell beside the profile name. New signing requests mark the bell, Notifications and builder links with red dots for other sending owners. The transaction creator receives a read notification without a red dot. Opening Notifications marks requests as viewed without deleting them. Selecting a request in Notifications opens its prepared transaction at Stage 3. Sending participants can follow that request to sign even while their template-construction pages are locked. The X deletes only that user’s notification. Opening Transaction Builder resumes the latest accessible prepared transaction at that user’s permitted signing/combination stage, including after closing the browser or restarting the service. Unprepared diagram edits are discarded when leaving the builder; save a template to retain them.

The same binary `.psbt` file is served to every participant. Open it with Bitcoin Core’s **File → Load PSBT from file**, review its details, sign using the selected wallet and save the signed PSBT. For a multi-wallet transaction, repeat for each sending wallet you control. Upload the saved file using that wallet’s Stage 3 card. Only its owner can upload; other wallets remain visible but read-only. Cards turn green after all inputs for that wallet have valid signatures, and update live across connected browsers. Private keys remain in Bitcoin Core.

Prepared transactions, unsigned PSBTs, verified signature proofs, combined PSBTs, notification state and change reservations persist in **data/transactions/workflows.json**. Include the existing data directory in backups. Reopening a transaction supersedes its dashboard PSBT and releases its input reservation; files already downloaded are not cryptographically revoked and must not be used after replacement. Removing a profile cancels orphaned prepared plans. No notification or wallet/transaction identity is hardcoded.


## Verified signing and completion

The multi-wallet workflow has **Stage 1 — Transaction Templates**, **Stage 2 of 4**, **Stage 3 of 4** and **Stage 4 of 4**. Upload binary or Base64 PSBT files saved by Bitcoin Core. The dashboard verifies signatures against the original transaction and previous outputs, rejects changed transactions, and requires every selected input for that wallet to be signed. Bitcoin Core finalized inputs are accepted. Uploaded unrelated metadata is discarded; only verified signature proofs are retained.

Once every wallet card is green, the profile that started the transaction receives one completion notification linking to **Stage 3**. Repeated uploads do not duplicate it. Only that creator can click Next to combine signatures and download the combined PSBT from Stage 4. Other participants can see completion without advancing or receiving completion notifications. Reopening the prepared transaction invalidates its dashboard workflow. Previously downloaded files remain usable outside the dashboard and must be discarded after replacement.

Older prepared V143 transactions are checked against saved public descriptor history to establish input ownership. If that cannot be established unambiguously, reopen and prepare the transaction again.

V1 Build 1 uses the non-dismissible shared process-age notices described above. Fee Estimates and Administration do not show those banners.


## Cancelling construction

A red **Cancel** button appears from Stage 3 onward in both workflows, including Stage 4 of a multi-wallet transaction. The transaction creator can cancel; other participants see the control disabled. The confirmation offers Yes and No with: “Are you sure you want to cancel construction of the current transaction?” No preserves the current process. Yes closes the prepared workflow, releases dashboard reservations, marks its requests read and returns the creator to Dashboard. Transaction Builder remains available and opens at Stage 1 for a new template selection. Cancelling does not broadcast or revoke any file previously downloaded from Bitcoin Core or Wallaby.

## Combined pages and complete exchange disposals

Ownership contains all three stages, beginning with automatic node wallet collection. Saving complete ownership enables Transaction Builder immediately. Its template chooser is Stage 1, the diagram is Stage 2, signing is Stage 3, and multi-wallet combination is Stage 4. Existing signing-workflow file stage numbers remain compatible internally. The sidebar and Dashboard use the same unlock rules. Notifications is disabled only when its list is empty; read notifications still count.

Receipt validation checks all supplied historical wallet records, including receipts outside the target year's active dependency graph. It no longer reports those exhausted historical receipts as unmatched. Unresolved source/destination questions remain on Terminal Activity. A genuine reconciliation error blocks completion rather than producing a partial report with review flags.

A supplied crypto-to-BTC trade now records the sold asset's CGT as well as the BTC acquisition. AUD disposal proceeds come from the supplied BTC consideration and the existing timestamped BTC/AUD lookup. No additional crypto/crypto or USD conversion is needed. Network fees in the sold asset create separate disposal components; the existing BTC acquisition basis includes the fee's incidental acquisition cost once. Non-BTC amounts keep their own unit, without Bitcoin pipe notation.

The exchange's quantities establish consideration, not the disposed asset's acquisition cost or holding period. Earlier supplied purchases establish those automatically. If they are absent, Terminal Activity asks for acquisition parcels (date, quantity, AUD cost). For otherwise untraced BCH, V1 Build 1 assumes investment acquisition at the 1 August 2017 Bitcoin Cash fork with zero AUD cost, including BCH spent on network fees. It does not ask for BCH acquisition details. Known supplied purchases retain their actual cost and date. Other non-fiat assets still require acquisition facts when missing. USD, RUB and all other fiat conversions are unchanged. Different holding periods remain separate CGT components. These answers belong to the current calculation and its saved working paper; they are not wallet ownership settings or transaction templates. See [ATO crypto chain splits](https://www.ato.gov.au/individuals-and-families/investments-and-assets/crypto-asset-investments/transactions-acquiring-and-disposing-of-crypto-assets/crypto-chain-splits).

Prompt headings now use Exchange Tracer and Wallet Tracer. The wallaby logo retains its shape with a four-tile dashboard emblem.

## BCH fork acquisition default

The explicit BCH-only default avoids acquisition questions for BCH received from the Bitcoin fork and then sold directly. The amount sold and any BCH network fee still come entirely from the Exchanges ODS. Disposal proceeds still use the BTC received and transaction-time BTC/AUD quote; the BTC acquisition calculation is unchanged. The assumed acquisition date is 1 August 2017, so the usual holding-period test remains in place. This is a date-only acquisition fact, not a fabricated blockchain receipt time. The assumption is included in calculation notes and saved report facts. It does not depend on a profile, exchange or wallet name, and does not classify all non-Bitcoin assets as zero-cost.
