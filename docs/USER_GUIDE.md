> Build 3 workflow: Ownership Stage 3 ends with Verify Balances. Wallets and its saved acquisition-cost provenance now come from Balance Trace, not the CGT report. The current README describes this update; historical feature notes below remain for context.

# Wallaby Bitcoin Dashboard — V1 Build 10

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

## V1 Build 10 setup

1. Open **Administration**, then select or create a family user. The sidebar and header remain visible before login; other pages unlock after a user is selected.
2. Connect each node's Wallaby Agent, load the desired wallets in Bitcoin Core, then open **Ownership** and select **Load Node Wallets**. Every connected node is queried over read-only RPC. Identical public locking descriptors identify wallet copies, independently of wallet names; matching copies share RPC work. Ownership suggests the first reporting node's name. Different wallets with identical names stay separate. Core wallet history is reconstructed through the same connected nodes; after success Ownership opens at Stage 2. No console exports, private keys or wallet unlock are needed. Unloaded wallets or missing RPC capabilities stop discovery with an actionable error rather than accepting a partial collection.
3. Open **Ownership**. Provide historical complete-year and current-year statements together in the **Bitaroo PDFs** card for each family profile, plus the shared Exchanges ODS. Any logged-in user can provide the whole family’s data. A missing card is red, a supplied card is green; select **Non Bitaroo user** to disable that profile’s PDF card in grey. All cards must be complete or nonapplicable before loading records. Current-year statements are optional when either your browser's local date or UTC date is 30 June or 1 July. Other inputs remain required. Each Bitaroo period needs matching AUD/BTC statements. Optional price quotes, CPI and tax-context files remain available.
4. Select **Load records and review ownership**. The dashboard reads the descriptor history already discovered, loads the other records and asks for each wallet's owner and SMSF status. Bitaroo account owners are fixed by their Stage 2 upload cards; exchanges cannot be marked as SMSF accounts. To refresh descriptor history, select Load Node Wallets in Stage 1; the old separate refresh checkbox is removed. Select Verify Balances to save assignments and trace the family’s current holdings.
5. Address Book and CGT setup unlock. Choose a matching tolerance and build the report. It calculates all supplied family profiles and every financial year from the earliest record through the current year. Financial Year Totals and CGT Event History display **only the logged-in user**. Use their left/right year arrows; future years cannot be selected. The initial year uses the browser’s local year, with the previous-year exception on UTC 30 June / 1 July. Wallets reuses each family owner’s known acquisition costs. Missing basis and SMSF costs can still be unavailable.

The Bitcoin Core CSV and historical-wallet ODS upload workflows have been replaced by node wallet discovery. Live descriptor wallets retain Bitcoin Core's original source name and stable descriptor-based ID, with an editable display name saved in Ownership. Historical group names come from Core address labels and are read-only; exchange names come from their sheets.

## Shared tracing behaviour

- Ownership Stage 1 loads wallet names, complete public descriptors and legacy received-output metadata from every connected node. No manual descriptor/history paste workflow is shown.
- Wallet replicas are grouped by overlapping public locking descriptors, ignoring key-origin annotations and checksum spelling. Their descriptor ranges/cursors and received outputs are merged. Unrelated equal names are disambiguated for display, not combined.
- Fixed-address `addr()` descriptors in private-key-disabled Core wallets are grouped by address label. The Core parent wallet name is retained for RPC requests; the group label identifies the historical wallet in Ownership. No historical-wallet ODS is requested.
- Ownership reserves three left-aligned icon slots: funded vault evidence, extinct fossil and wallet, or the exchange symbol as applicable. A vault descriptor alone is insufficient: positive received-output history must establish funding of its locked address. Historical funding remains evidence after spending or expiry. The Vaults page remains a placeholder.
- Requests to different nodes run concurrently. Additional address ownership reads rotate among replicas, with read-only fallback. Per-node concurrency is bounded and consumed internal RPC results are released to avoid filling the broker.
- All blockchain data is read through connected Agents. No public blockchain indexer or explorer API is queried. Price and FX feeds remain external. If indexation is required, supply the verified CPI file; CPI is no longer fetched externally.


## Retained V154 behaviour

Terminal Activity is available only while a process is running or waiting for an answer, and its Dashboard card is hidden while idle. Administration → Logs downloads the browser workspace's UTF-8 `Wallaby_Dashboard.log`, including earlier jobs/profile switches. Fee Estimates refreshes on every visit, then every two minutes. Vaults remains the Ownership-gated **No Vaults Detected** placeholder. Remembered node identities survive profile save/load; live connections and RPC requests/results do not. Duplicate hostname/MAC notices remain red, non-dismissible, and clear when the conflict ends.

### Dashboard profile archives

**Save** creates a genuine `profile.7z`, using 7-Zip maximum compression (`-mx=9`). Install the system 7-Zip command for this feature; the other dashboard features do not require it. On Ubuntu/Kubuntu: `sudo apt install 7zip`. Windows/macOS can use an installed `7z` or `7zz` command on PATH.

The archive contains `config.cfg`, `descriptors.cfg`, `data/` and `templates/`, where present. This preserves family users, ownership, source records, public descriptor history, saved personal CGT reports, shared templates, prepared PSBT workflows and notifications. Browser tokens, unsaved diagrams, activity logs, application code and disposable public network/price caches are excluded. Original uploaded source files outside the dashboard are never modified.

Firefox uses its normal download location or Save dialog, with `profile.7z` as the supplied filename. Web pages cannot enforce a directory chooser or prevent the user/browser renaming downloads. The file picker filters `.7z`; the dashboard validates the exact basename `profile.7z` before uploading and the server validates it again. Rename a browser-created `profile (1).7z` back to `profile.7z` before loading.

**Load** checks archive type, paths, size limits, links, manifest hashes, config schema, public descriptor identities, saved ownership files, template diagrams, transaction structure and report ownership before replacing live files. Invalid archives leave the current profile unchanged. Installation errors roll back the whole replacement. Only Wallaby archives with the included manifest are accepted. Archives may be up to 256 MB compressed and 2 GB expanded.

**Load and Clear replace the entire shared family dashboard**, not just one logged-in user. The confirmation states this; connected browser sessions are reset to Administration. Clear removes the shared config, descriptor collection, data and templates. Save first to preserve them. Active discovery/calculation jobs must finish or be cancelled before saving, loading or clearing. Ordinary clients remain concurrent; profile operations obtain exclusive access to shared data during replacement/snapshotting.

### Historical watch-only address groups

Use a Core wallet with private keys disabled and imported `addr(address)` descriptors. Every address needs one nonempty label naming its historical wallet. The same base label on several addresses groups them into one wallet. Ordinary descriptor wallets remain separate. No parent wallet name, person, address or transaction is hardcoded.

For an irreversible loss with no compensation, use exactly:

```text
Earlier Wallet | LOST:DD/MM/YYYY HH:MM:SS UTC
```

The suffix applies only to that address; its base label still groups it with the other addresses of Earlier Wallet. Supply the actual date/time when the loss was discovered, supported by your records. Core reports output and spending history, not proof that private keys were lost. The Dashboard records retained outputs at that instant as CGT event C1, with zero proceeds and the reduced acquisition cost traced using the existing wallet parcel convention. It does not apply a market-value disposal, indexation or a 50% loss discount. Unused losses carry forward through the annual report calculations. Conflicting later receipts/spends, future loss dates or missing acquisition evidence stop the process for correction. Compensation/recovery needs separate review; this label workflow does not infer it.

Original output identities remain in the shared trace graph. The loss is an economic record, never a fabricated blockchain transaction. Lost quantity is removed once from retained holdings; zero-balance historical wallets are hidden from balances. Historical groups are excluded from Address Book and transaction construction. Names must be changed at their source labels in Core, followed by Load Node Wallets.

Reference: [ATO crypto asset loss guidance](https://www.ato.gov.au/individuals-and-families/investments-and-assets/crypto-asset-investments/crypto-asset-transactions-with-capital-gains-tax/loss-or-theft-of-crypto-assets), [ITAA 1997 section 104-20](https://www.legislation.gov.au/C2004A05138/latest/text).


### Joint-payment accounting convention

Public blockchain confirmation times are used, not exchange execution times. Wallet ownership comes from supplied public descriptors, owned outpoints and confirmed Ownership assignments. No identities, personal addresses or transaction IDs are built into the program. A historical address must not also be claimed by a distinct live descriptor wallet; duplicate script ownership is rejected.

For jointly funded payments, the program uses the requested **accounting assumption**, not a claimed on-chain input/output mapping. The network fee is split equally between source wallets, with deterministic whole-satoshi rounding. Every input budget and every output amount, including wallet-controlled change, is conserved. Senders’ existing parcels are reserved before processing any of that transaction’s receipts; a new output cannot fund an input of the same transaction. Parent transactions sharing a block timestamp are processed before their children.

The search compares proportional allocations, deterministic transport vertices and whole-satoshi cycle refinements. It also compares proportional, holding-period, highest/lowest-cost and oldest/newest parcel policies for each source wallet. Candidate scores use the existing annual CGT calculation, including losses before applicable discounts. The primary objective is the sum of the family’s calculated taxable capital gains. If several tested minimum-gain allocations remain and multiple affected owners have same-year disposals, Terminal Activity asks for predicted-income ordering, highest to lowest. This is a tie-break, not a calculation of personal income tax. Remaining ties favour the proportional allocation.

The search is bounded (three coordinate sweeps per transport pass, with deterministic vertex limits on large diagrams). It is **not a certified global optimum**, and the report discloses the algorithm version, comparisons, policies, assumptions and satoshi matrix. A candidate with excluded disposal proceeds or unreconciled source quantities cannot be treated as a tax saving. Other assets’ CGT ledgers and unrelated income/losses are not invented.

Each transaction’s allocation is optimised using only economic activity up to the end of its **own financial year**, or the current instant for an open year. Matching statement entries and dependency planning are bounded too. Earlier-year decisions are recomputed first and never reoptimised using later-year activity. The open year can change as data arrives. A completed year reproduces its allocations given the same historical inputs, prices, ownership/settings, income-ranking answers and algorithm version. Corrected past records, different answers, revised valuation inputs or a different algorithm are changed inputs and may change results.

There is **no saved allocation ledger**, and neither saved transaction templates nor completed builder allocations are consulted. Choices exist in calculation memory; the generated report includes their audit details but is not read to seed another run. Profile-keyed opening capital losses apply before the earliest supplied FY; losses calculated within the loaded history carry forward automatically. The optional tax-year timezone overrides the browser timezone, which otherwise supplies the family replay calendar.

Optional whole-return tax context now needs explicit user/year scope, so another person's return or a different year is never reused accidentally. The JSON format is `{"users": {"User name or ID": {"2026-27": {"basic_tax": "0", "basic_tax_without_gain": "0"}}}}`, with the existing optional calculation fields inside each year object. Use `{"users": {}}` when no context is supplied. User names are matched without case sensitivity; stable profile IDs are also accepted.

V1 Build 10 does not establish that this accounting convention has ATO approval. The report identifies it as an assumption for review rather than representing it as a recovered fact.

## Descriptor wallet display names

In Ownership, Core descriptor wallets have an editable name field. Suggestions follow the earlier CSV naming rules: underscores become spaces, words are title-cased, and “Wallet” is appended if it is not already a word in the name. For example, `family_savings` suggests `Family Savings Wallet`; `emergency_wallet` suggests `Emergency Wallet`. Edit the suggestion and select **Verify Balances**. Existing custom labels are preserved.

Only live descriptor wallets can be renamed. Historical group labels and exchange names remain fixed. Names must be non-empty and unique across the loaded wallets and exchanges. The selected label is shared through config.cfg and used in Address Book, balances and CGT history. Changing a label invalidates an existing report, which can then be rebuilt with that label.

These are display aliases: Bitcoin Core’s wallet name, descriptor strings, stable wallet IDs and saved scan fingerprint do not change. Reloading unchanged node descriptors or rerunning Ownership retains the saved alias. RPC requests always retain the original wallet name for each node.

## Node-only wallet history

Every loaded wallet supplies `listsinceblock` with change outputs included. Core-provided output references establish its scripts without probing thousands of addresses. This also covers migrated combo and hardened public paths. Raw transactions come from `getrawtransaction` or the relevant wallet's `gettransaction`; transaction IDs are verified locally. Confirmed UTC times come from Core's block headers. Unconfirmed movements are excluded from CGT. Public descriptors and exact historical owned outputs remain the basis of wallet identity.

Load Node Wallets again after upgrading from earlier builds to collect the complete node-history metadata. Refresh it when Core learns new transactions or policies. Current builder inputs use fresh wallet `listunspent` and verified raw transactions. Change selection refreshes the public internal-descriptor cursors across connected replicas, then respects Dashboard reservations. No RPC generates private keys, signs, unlocks a wallet or broadcasts.

A pruned node can still return transactions retained by its loaded wallets, even if their blocks have been pruned. It cannot return arbitrary historical wallet transactions it never retained. Load the watch-only address descriptors and rescan with the necessary blocks available before running Load Node Wallets. Missing evidence stops reconstruction with an actionable error; there is no public fallback and no automatic blockchain re-download. Ancestors of external incoming payments may remain unknown without changing the explicitly received amount; the source remains unresolved, and no network fee is invented. Unknown input values on an owned spend stop the trace.

Historical address monitoring uses the loaded Core watch-only wallet's reconstructed history. Import all relevant addresses and complete the rescan; the Dashboard cannot discover unknown change addresses or recover arbitrary pruned blocks.

## Fee Estimates

Fee and block observations use connected Bitcoin Core nodes via Agent, at the lowest broker priority. Ownership, Balance Trace and CGT work defer new fee requests. An RPC already executing cannot be interrupted; subsequent requests wait. While viewing the page, lightweight browser polling collects server snapshots; node refreshes occur at most every five seconds while idle. Deferred or stale results are labelled. BTC/AUD valuations use an external Kraken market quote.

Four priority cards use Core's `estimatesmartfee`, bounded below by local mempool/relay policy. If Core lacks estimation history, local package estimates are labelled as the fallback. Projected blocks select ancestor packages and account for parents before children; they are not guarantees of miner inclusion or exact replicas of Core's block assembler. A large/unsupported mempool response leaves projections unavailable rather than presenting a truncated projection as complete. Mined blocks come from node headers and available block bodies.

Enter a TXID and select Track Transaction. A mempool transaction marks the next projected block, without promising next-block confirmation. A confirmed transaction marks its visible block; older transactions produce a yellow notice. Tracking follows the selected node's knowledge and cannot locate arbitrary pruned transactions.

The block component is adapted from Mempool's AGPL code, with Wallaby's local data adapter, simplified controls and colours. Original pinned component sources and attribution are under `vendor/mempool`. The running service offers corresponding application source at `/source.zip`. No Mempool logos or public API connections are included.

## Wallets

Verify Balances opens Wallets after completing the family Balance Trace. Current holdings use the same joint-payment policies and cost reconstruction as CGT, without publishing a tax report. The card icon row shows vault, wallet/exchange, watch-only and SMSF metadata in the same priority order as Ownership. The tooltip shows only AUD Gain/Loss, Owner, SMSF, Purchase Price and acquisition Sources beneath the wallet name. Purchase Price is the retained AUD cost of the current Bitcoin, not the wallet's lifetime purchases. Missing basis remains unavailable, not zero. SMSFs are excluded from personal cost tracing.

## Saved data and freshness

Keep these together when upgrading, moving or backing up the service:

- `config.cfg`: family profiles, ownership, latest parsed Bitaroo ledger/coverage and per-profile Ownership metadata.
- `data/`: supplied records, reconstructed descriptor history, saved reports and remembered node identities (`nodes.json`).
- `descriptors.cfg`: the shared public descriptor collection, separate from config.cfg.

A fresh login restores the profile's last successful Ownership inputs and assignments. Completed family CGT reports are saved in `data/reports/` and reopen for each logged-in profile, including after a service restart. Activity logs and unresolved-question answers are not reused by a later calculation. Starting a new CGT run discards the previous reports for every profile before calculation begins; failure or cancellation does not restore them. Changing the source records or ownership requires a new report. Changed descriptors invalidate the old Ownership scan until it is rerun. Ownership is a shared family process: its saved inputs and complete assignments unlock CGT setup and Transaction Builder for every profile. Each profile sees its own totals and history; the saved family calculation is separated into personal responses. Clearing configuration also removes the saved descriptor collection; original source files are not changed.

Run-age warnings refer to completed shared processes, using the viewing browser’s local calendar and timezone. The newest successful Ownership process for any family profile refreshes the family Ownership age; any successful family CGT run refreshes the CGT age. Failed/cancelled runs do not make data fresh. Upgrading a V151 saved report without a completion timestamp requires one new CGT run to establish that timestamp.

On CGT Event History, CGT Report, Financial Year Totals, Wallets, Transaction Builder and Vaults, one read notice is generated on visiting the page. Ownership warnings replace CGT warnings. The same notice appears on Notifications with its X disabled. A new run updates/removes the notice for each viewer; obsolete statement-coverage notices are retired when the new freshness check runs.

## Access and packaging

Family profile selection is password-free because access is controlled by your SSH tunnels. The service binds loopback, supports IPv4/IPv6 and different client-side tunnel ports, and separates browser workspaces. Shared configuration writes are locked and atomic. Public descriptors cannot sign transactions, but reveal wallet addresses/history and are private financial information. Wallet descriptors and blockchain queries remain between the Dashboard, Agents and connected nodes. Never export with `true` or paste private keys, seeds or passphrases; private descriptors are rejected.

The fee estimator, history graphs, Address Book and offline HTML reports remain available. Zero-balance labelled historical wallets remain hidden from balance cards; historical addresses are excluded from Address Book and transaction construction. Live descriptor wallets remain visible at zero. Historical exchanges are excluded from Address Book. Saved tax HTML does not contain the public descriptor collection.

This ZIP contains runtime files and bundled-library notices only: no user configuration, wallet exports, test records, reports or caches.

The builder uses the familiar separation of draggable nodes and connection handles described in the [React Flow handle documentation](https://reactflow.dev/learn/customization/handles). Its implementation uses the dashboard’s bundled JavaScript and SVG; no external UI library or online script is required.

The builder’s **Next** button becomes available only with at least one positive payment, a positive Network Fee contribution and a successful confirmed-UTXO funding estimate. Missing destinations, insufficient funds and payment outputs below standard dust policy keep it disabled. Next now prepares the complete unsigned transaction, rechecking confirmed inputs and deriving change from each funding wallet’s public internal descriptor. It opens Stage 3 below the diagram; the prepared diagram is fixed until its creator selects Reopen for editing. For multiple sending wallets, every sender, including a fee-only sender, receives the same PSBT for signing. Stage 3 accepts signed PSBTs from each wallet owner; Stage 4 combines verified signatures for the transaction creator to download. A single sending wallet instead uses three stages, including the template selector, with signing and broadcasting entirely in Bitcoin Core. The dashboard does not broadcast.

## Maximum spend and signing notifications

Select **Send maximum** in a payment connection to disable manual Bitcoin entry. One connection per source wallet can use maximum; other outgoing amounts are deducted first, along with that source’s allocated fee. All confirmed spendable inputs from that wallet are selected, and no change is returned to it. The amount recalculates after changes, on template loading, and again using fresh UTXOs before PSBT construction. Unconfirmed Bitcoin is excluded. A wallet cannot end empty if this same transaction also pays into it; that conflicting plan is rejected.

Prepared PSBTs contain one complete transaction, verified previous transactions and public change metadata. Change addresses are derived within the current active internal range after refreshing Core cursors, and advanced beyond Dashboard reservations. Refresh the descriptor export if its range or change cursor is exhausted. Fee calculations use conservative signed sizes, so the final fee rate may be slightly higher after signing.

Stage 3 opens after preparation for the creator or sending owner. **Notifications** is also available through the bell beside the profile name. New signing requests mark the bell, Notifications and builder links with red dots for other sending owners. The transaction creator receives a read notification without a red dot. Opening Notifications marks requests as viewed without deleting them. Selecting a request in Notifications opens its prepared transaction at Stage 3. Sending participants can follow that request to sign even while their template-construction pages are locked. The X deletes only that user’s notification. Opening Transaction Builder resumes the latest accessible prepared transaction at that user’s permitted signing/combination stage, including after closing the browser or restarting the service. Unprepared diagram edits are discarded when leaving the builder; save a template to retain them.

The same binary `.psbt` file is served to every participant. Open it with Bitcoin Core’s **File → Load PSBT from file**, review its details, sign using the selected wallet and save the signed PSBT. For a multi-wallet transaction, repeat for each sending wallet you control. Upload the saved file using that wallet’s Stage 3 card. Only its owner can upload; other wallets remain visible but read-only. Cards turn green after all inputs for that wallet have valid signatures, and update live across connected browsers. Private keys remain in Bitcoin Core.

Prepared transactions, unsigned PSBTs, verified signature proofs, combined PSBTs, notification state and change reservations persist in **data/transactions/workflows.json**. Include the existing data directory in backups. Reopening a transaction supersedes its dashboard PSBT and releases its input reservation; files already downloaded are not cryptographically revoked and must not be used after replacement. Removing a profile cancels orphaned prepared plans. No notification or wallet/transaction identity is hardcoded.


## Verified signing and completion

The multi-wallet workflow has **Stage 1 — Transaction Templates**, **Stage 2 of 4**, **Stage 3 of 4** and **Stage 4 of 4**. Upload binary or Base64 PSBT files saved by Bitcoin Core. The dashboard verifies signatures against the original transaction and previous outputs, rejects changed transactions, and requires every selected input for that wallet to be signed. Bitcoin Core finalized inputs are accepted. Uploaded unrelated metadata is discarded; only verified signature proofs are retained.

Once every wallet card is green, the profile that started the transaction receives one completion notification linking to **Stage 3**. Repeated uploads do not duplicate it. Only that creator can click Next to combine signatures and download the combined PSBT from Stage 4. Other participants can see completion without advancing or receiving completion notifications. Reopening the prepared transaction invalidates its dashboard workflow. Previously downloaded files remain usable outside the dashboard and must be discarded after replacement.

Older prepared V143 transactions are checked against saved public descriptor history to establish input ownership. If that cannot be established unambiguously, reopen and prepare the transaction again.

V1 Build 10 uses the non-dismissible shared process-age notices described above. Fee Estimates and Administration do not show those banners.


## Cancelling construction

A red **Cancel** button appears from Stage 3 onward in both workflows, including Stage 4 of a multi-wallet transaction. The transaction creator can cancel; other participants see the control disabled. The confirmation offers Yes and No with: “Are you sure you want to cancel construction of the current transaction?” No preserves the current process. Yes closes the prepared workflow, releases dashboard reservations, marks its requests read and returns the creator to Dashboard. Transaction Builder remains available and opens at Stage 1 for a new template selection. Cancelling does not broadcast or revoke any file previously downloaded from Bitcoin Core or Wallaby.

## Combined pages and complete exchange disposals

Ownership contains all three stages, beginning with automatic node wallet collection. Saving complete ownership enables Transaction Builder immediately. Its template chooser is Stage 1, the diagram is Stage 2, signing is Stage 3, and multi-wallet combination is Stage 4. Existing signing-workflow file stage numbers remain compatible internally. The sidebar and Dashboard use the same unlock rules. Notifications is disabled only when its list is empty; read notifications still count.

Receipt validation checks all supplied historical wallet records, including receipts outside the target year's active dependency graph. It no longer reports those exhausted historical receipts as unmatched. Unresolved source/destination questions remain on Terminal Activity. A genuine reconciliation error blocks completion rather than producing a partial report with review flags.

A supplied crypto-to-BTC trade now records the sold asset's CGT as well as the BTC acquisition. AUD disposal proceeds come from the supplied BTC consideration and the existing timestamped BTC/AUD lookup. No additional crypto/crypto or USD conversion is needed. Network fees in the sold asset create separate disposal components; the existing BTC acquisition basis includes the fee's incidental acquisition cost once. Non-BTC amounts keep their own unit, without Bitcoin pipe notation.

The exchange's quantities establish consideration, not the disposed asset's acquisition cost or holding period. Earlier supplied purchases establish those automatically. If they are absent, Terminal Activity asks for acquisition parcels (date, quantity, AUD cost). For otherwise untraced BCH, V1 Build 10 assumes investment acquisition at the 1 August 2017 Bitcoin Cash fork with zero AUD cost, including BCH spent on network fees. It does not ask for BCH acquisition details. Known supplied purchases retain their actual cost and date. Other non-fiat assets still require acquisition facts when missing. USD, RUB and all other fiat conversions are unchanged. Different holding periods remain separate CGT components. These answers belong to the current calculation and its saved working paper; they are not wallet ownership settings or transaction templates. See [ATO crypto chain splits](https://www.ato.gov.au/individuals-and-families/investments-and-assets/crypto-asset-investments/transactions-acquiring-and-disposing-of-crypto-assets/crypto-chain-splits).

Prompt headings now use Exchange Tracer and Wallet Tracer. The wallaby logo retains its shape with a four-tile dashboard emblem.

## BCH fork acquisition default

The explicit BCH-only default avoids acquisition questions for BCH received from the Bitcoin fork and then sold directly. The amount sold and any BCH network fee still come entirely from the Exchanges ODS. Disposal proceeds still use the BTC received and transaction-time BTC/AUD quote; the BTC acquisition calculation is unchanged. The assumed acquisition date is 1 August 2017, so the usual holding-period test remains in place. This is a date-only acquisition fact, not a fabricated blockchain receipt time. The assumption is included in calculation notes and saved report facts. It does not depend on a profile, exchange or wallet name, and does not classify all non-Bitcoin assets as zero-cost.

## Signed exchange units

The two unit headings remain fixed within a tab. A negative amount means that unit was sold; a positive amount means it was received. Purchases and sales can share one exchange tab. Network Fee remains zero or negative and follows the actual outgoing crypto unit, or incoming BTC for fiat-funded buys. A supplied sale with a unique matching withdrawal/send uses that transfer’s parcels and does not create an additional disposal for the funding transfer.

## Build 8 Agent transactions and vaults

See [the Agent integration contract](AGENT_PROTOCOL.md). On Nodes, amber **Downloading Blockchain** means that Core is in initial download or has fewer blocks than known headers. A connected transport is not sufficient for signing eligibility. Nodes with unknown/error status are also excluded from signing and broadcast selection.

Vaults lists only detected vault wallets. Hover amber cards for missing funds, invalid/unverified policy or pending local sync authorisation. Green means funds are under a validated unexpired vault policy; red **Unlocked** means the height restriction has ended. Countdown estimates show the two largest units and are not time guarantees.

In a Builder diagram, select the source Agent to receive an unsigned vault intent. Its fixed red arrows cannot be changed; add other wallets' payments alongside it before Next. All wallet owners then review the same final PSBT on their selected local Agents. Signing cards are red until signatures have been checked; each becomes green and disabled when complete. Only the transaction creator can request **Broadcast combined PSBT** after every signature is verified. Broadcast still requires local Agent approval. Private keys and wallet passwords stay on the signing computer.

These features work with Agent V2 Development Build 8 advertising the documented capabilities. An empty node list explains that readiness, private-key wallet matching or Agent support is missing. No private wallet is transferred through the Dashboard. Wallet synchronisation uses Agent Build 8 and Dashboard Build 10 signed metadata previews. Compare the sender fingerprint and snapshot code directly between Agent screens. Nodes displays queued/active/outcome states. Failed attempts require closing both local requests and reconciling Core before choosing New transfer attempt. Private files never pass through Dashboard.

## Build 10 Wallets and synchronisation

Wallets replaces Wallet Balances. All Owners is the initial owner filter. Active Wallets starts selected and Extinct Wallets starts clear; changing filters never leaves both clear. Historical archive cards are grey and show the first/last confirmed activity years (one year when equal), without AUD balance or hypothetical profit/loss. Multiple addresses carrying the same base label form one old wallet. Separate labels in the same Core archive remain separate wallets and owners.

Make Extinct requests local Agent review for a wallet owned by the logged-in user. Its execution needs a later Agent advertising wallet_retirement; current Agent Build 8 will explain that an update is needed. The Agent must confirm an empty wallet and complete history, preserve historical labels, and must not delete the wallet or keys. Refresh Ownership after the approved conversion. Retirement does not mean lost keys or create a tax loss.

Use Agent V2 Development Build 8 or newer. Nodes shows all relevant missing-wallet requests and non-dismissible history-recovery warnings. Review the local Agent Synchronisation page to authorise work. Data received with incomplete history remains incomplete until recovery is locally approved and verified. Final receiver completion clears both paired request lists; it does not resend the wallet. The Dashboard cannot restart Core, disable pruning or authorise a transfer.
