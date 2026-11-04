<p align="center"><img src="assets/logo.svg" width="112" alt="Wallaby Dashboard"></p>
<h1 align="center">Wallaby Bitcoin Dashboard</h1>
<p align="center">Bitcoin tools, wallet insights and family workflows.</p>

**V1 Build 56 · Development DEB available for Ubuntu/Kubuntu 26.04 LTS.** [Download the DEB](dist/wallaby-dashboard_1.0.0~dev56_all.deb) and verify [SHA256SUMS.txt](dist/SHA256SUMS.txt). This resets the public version numbering following the earlier V156/V157 work; it does not reset saved data formats.

[Program page](https://bluesilent9.github.io/wallaby-suite/dashboard/) · [Wallaby Suite](https://bluesilent9.github.io/wallaby-suite/) · [Issues](https://github.com/bluesilent9/wallaby-dashboard/issues) · [User guide](docs/USER_GUIDE.md)

## Build 56

Wallet Builder progress cards distinguish participation, policy installation and verified physical backups. Signers start red, become amber after participation, and turn green only with the relevant authenticated completion evidence. The combined wallet remains amber until every signer and final Core confirmation is complete. Existing setup evidence and wallet files are unchanged.

## Build 55

An unrelated unloaded Core wallet directory no longer discards the inventory of loaded wallets. Archive choices and live wallet traits can use a successfully collected loaded-wallet inventory. Missing-wallet synchronisation still requires the full inventory: unloaded directories are unknown, never proof of a missing wallet. Background discovery collects loaded wallets without dropping previously saved accounts for unloaded folders. Strict full scans still report unknown unloaded folders. An installed ceremony waiting for disc backups can refresh its loaded wallet information during foreground activity. Explicit inventory refreshes run as foreground work. No setup, approval or existing wallet is deleted or regenerated.

## Build 54

Login and routine presentation use read-only profile snapshots and lightweight node presence instead of repeatedly rebuilding synchronisation history. Saved-input restoration performs file verification/copying outside the profile write lock. Explicit Wallet Builder identity checks pause routine discovery and inventory work, while Agent exchanges continue serving replies and urgent control requests. Nodes contains coloured cards only; detailed issue/activity lists are removed and operational diagnostics remain downloadable.

## Build 53

Combined signer-and-policy wallets retain one logical Dashboard name from Wallet Builder, including vaults. Signer visibility now binds the local private-key-enabled account to its signed setup role, rather than mistaking the public keys of the other cosigners for locally held private keys. Distinct combined accounts on one Agent have distinct signing-request identities. Missing nodes receive public-only policy sync offers, never private-role clones. The wallet hover lists the signed role names and visibility alongside the quorum. Disconnected and watch-only copies do not count as visible signers. No private descriptors are requested.

## Build 52

Nodes now shows distinct coloured cards and current issues or outstanding reviews only. Completed/superseded communication history stays in the downloadable diagnostic log.

Wallet creation now attaches the exact new proposal hash returned by creation, rather than treating the last alphabetically stored hash as the newest setup. Workspace reconstruction includes only sessions bound to that workspace; old setup evidence and cancellation records are retained. This fixes duplicate approval stages caused by an older setup being linked into a later creation.

The Dashboard accepts the Agent’s combined encrypted key-and-policy wallet layout: the monitoring and signing wallet names may be identical. Private keys remain local. Archive eligibility continues to require a connected Agent’s persistent private wallet and fresh public verification; queued disc work is not a Dashboard exclusion.

## Build 51

Archive this Wallet always opens an Agent chooser before sending a request, including when only one compatible private-wallet holder is connected. Nothing is preselected. Cancel and Escape send no request. Connected Agents that cannot supply the backup remain listed with a reason, including watch-only copies, an incomplete inventory or missing archive capability. Only explicitly selected eligible sources are queued, and the server still rechecks the wallet and connection before accepting them.

Archive creation/cancellation remains independent of Wallet Builder: active proposals, signer keys, approvals and disc proofs are unchanged. Cancelling an archive request preserves the source wallet and any unrelated disc members; a started burn still finishes verification and any required physical cleanup stays locally authorised.

This fixes direct archive destination selection. Receiving an encrypted wallet from another Agent into protected RAM solely for burning is a separate Agent-to-Agent handoff. It is not implemented by offering a watch-only copy as a full wallet or by ordinary wallet synchronisation. The Dashboard will need the Agent chat's exact new protocol before enabling that path.

## Build 50

A temporary Agent reconnect no longer stops policy delivery after its signing contributions are known. An interrupted setup can recover after a Dashboard restart when its active creator workspace is still bound, every original signed role contribution is available, the proposal is unexpired, and retained policy/approvals independently validate. Recovery reconstructs the same exact public policy and resumes policy approval only; it never sends a replacement participation/key-generation request.

Existing approvals, producer routes, policy hashes, local Core confirmations and required disc-backup receipts remain intact. Denied, cancelled, expired, altered or unbound setups stay stopped. Partial restart recovery waits for the remaining already-generated signed contributions; it does not assume missing private keys can be recreated. Existing signed outcomes and cancellation authority are retained.

Keep the Agents' protected workspaces open while installing this Dashboard update. The Agent's matching participation session must still exist in protected RAM for local policy approval to continue. No Agent protocol change is introduced. This does not prove that any particular live Agent still holds its keys; the read-only status check in the Agent chat establishes that before resuming the user's test.

## Build 49

The editable vault-height field now has dark text on an explicit white background. Its scoped styling takes precedence over the general Wallet Builder input rules, including focused and narrow-screen layouts. Calculation, exact-height edits, local/UTC estimates, copy and existing setup data are unchanged.

## Build 48

Ownership now distinguishes a logical wallet from its internal signing accounts. Empty BIP84 signer accounts are identified from authenticated public creation contributions, even before a monitor appears. Empty unfinished public policies wait until the producing nodes confirm completion. Completed policies appear once across nodes with the proposed readable wallet name; existing user-selected Dashboard names and actual Core filenames stay intact.

Background discovery removes previously cached, proven-empty internal entries and their obsolete Ownership rows. Missing history, receipts, sends or vault-funding evidence prevent that cleanup. A newly observed real transaction preserves the account and its existing ownership. The shared public policy, role routes, keys, creation approvals, disc receipts, cancellation requests and real sync outcomes are never rewritten by this presentation cleanup.

Build 46's signer-sync withdrawal remains included. The Agent chat provides the separate local guard against signer transfers. To abandon an unfinished test, use Wallet Builder cancellation and approve the correctly bound cleanup locally on each Agent. Dashboard cache cleanup does not delete Core wallets or erase the cancellation evidence needed by those Agents.

## Build 47

Calculate vault height still starts from 7:00 AM on the selected local date. The resulting block-height field is editable. Leaving that field recalculates the most-likely and 99% crossing estimates in the selected timezone and UTC for the exact entered height. Copying uses plain digits.

Only whole future block heights are accepted. While a change is awaiting its estimate, creation/refresh submission remains disabled; outdated responses cannot validate a newer edit. The chosen height and estimates stay in the saved Wallet Builder draft. This applies to single-signature and multisignature vault creation and vault refreshes; existing approved creation ceremonies are unchanged. No Agent update is required.

## Build 46

Separate signer accounts are never offered as ordinary missing-wallet replicas. Detection compares the actual BIP84 public branches with authenticated creation contributions or a known shared public policy. Core's additional default legacy, nested-SegWit and Taproot descriptors no longer prevent that match. Wallet names do not establish signer identity.

Previously queued signer invitations are withdrawn from both Agents' authoritative sync lists, including after reconnect. Their original request bindings, signed previews and actual operation outcomes remain recorded; delayed Agent results are still accepted. This changes no creation proposal, role assignment, approval, backup receipt, keys or workspace. Ordinary missing-wallet synchronisation and shared public-policy monitoring remain available.

Install this as an upgrade while keeping the existing Dashboard profile. No cancellation or reset of an in-progress creation is required. No new Agent protocol or Agent change is required for this correction. Withdrawal does not undo a transfer already authorised and completed locally.

## Build 45

A connected Agent with a complete matching persistent private-key wallet inventory can receive an archive request while its chain status is Checking, busy with wallet synchronisation, downloading, or unavailable. Blockchain readiness is not required to request a local wallet backup. Disconnected/conflicting nodes, watch-only wallets, disc-loaded accounts, extinct wallets and scanning wallets remain excluded.

Before queueing, Dashboard still obtains fresh public wallet information and descriptors from the selected Agent, verifies the actual private-key capability and exact reviewed wallet identity, and requires local Agent authorisation. A refused live query never creates an archive request. Existing wallet-creation drafts, proposals, role keys, receipts and cancellation state are not modified by archive selection or queueing.

The Agent chat is separately fixing its local busy gates during idle creation/approval waits. That change may still be needed for a running Agent to answer the fresh read-only checks. This Dashboard build adds no new Agent protocol and requires no setup cancellation or configuration reset.

## Build 44

Wallet Builder cards show the wallet icon first and disc icon last. Choosing encrypted physical-disc storage updates the icon immediately, including on ordinary new-wallet proposals. Participation and policy/backup approval cards show the same wallet/vault/multisig/disc indicators.

An active Wallet Builder remains open when an Agent is busy, its node health is being checked, or it reconnects. Read-only ceremony progress remains available without ready nodes. New setup/discovery actions still require ready nodes, and completion stays disabled until every producing Core node confirms its monitor. Drafting immediately after starting a workspace is also covered, before the general family poll sees that workspace.

The Agent chat handles its separate Authorise layout, passphrase retype prompts and adding unrelated archive requests to Disc Operations. This Dashboard build changes no Agent code or approval protocol.

## Build 43

Returning users with saved Ownership data now land on Wallets at login; new users and fresh Dashboard profiles start at Ownership. Saved Wallets, Address Book, reports, Vaults and Salary views remain available while node discovery refreshes data, including when no node is connected. Current-state checks still protect new transactions and calculations.

Opening Ownership no longer restarts a balance run solely because its latest fingerprint is pending. Ownership, descriptor and balance jobs leave the chosen page in place. Terminal Activity is available for progress or questions and opens only when selected. An explicit CGT run retains its existing report workflow.

A separate last-verified balance snapshot survives incomplete replacement traces and is included in profile backups. Old data is labelled as saved. A current BTC/AUD quote can refresh Wallets values and hypothetical gains without changing Bitcoin quantities, cost bases, ownership or saved tax reports, and without a node connection.

## Build 42

Node health checks now wait at least 15 seconds between attempts when an Agent is busy with protected local work. Busy replies do not count as a fresh verified node result or keep a stale node marked ready. This prevents repeated browser/status polls from flooding the same busy Agent.

Wallet Builder and Transaction Builder workspace polling now clear their own temporary error message after a successful retry. Unrelated approval, validation and concurrent-edit warnings remain visible. The Agent's separate local creation failure is being investigated in its own build; this Dashboard fix does not authorise or replay local wallet operations.

## Build 41

Background discovery now recognises unchanged wallet policies and transaction history separately from live node routes and watch-only status. A wallet rename, hostname change or changed connected replica updates the existing page without invalidating verified ownership, balances, reports or Address Book access. Discovery does not redirect the user when no ownership action is needed. New wallets and changed transaction/policy facts still require the normal checks.

An active rename keeps its exact original source names, destination names, request IDs, receipts and history. Discovering a partly renamed replica does not restart or reverse that operation. A separate presentation revision refreshes current wallet details without changing the calculation fingerprint. No Agent update is required for this fix.

## Build 40

Wallets has one **Pending Wallet Requests** table below its cards, combining extinction, rename, archive, old-disc cleanup and builder-cancellation delivery. It shows Wallet / Process, Node, Request, Status and Action; completed operations remain in saved history rather than keeping the table visible. The previous separate lists and blocking cancellation-progress overlay are removed.

Rename cancellation stops outstanding requests at any unfinished stage and retains completed/uncertain outcomes without renaming anything back. Make Extinct cancellation requires a connected matching private-key wallet; multisig requires **every signing role**, not just the spending threshold. The Dashboard rechecks public descriptors and private-key-enabled status through read-only RPC when submitted. Zero-balance eligibility to start extinction is unchanged. Once cancellation responses finish, ordinary synchronisation is no longer suppressed by the cancelled extinction.

Archive cancellation uses the new `wallet_archive_cancel_v1` capability. Dashboard retains a signed-result-tracked cleanup task. An unused request can be removed; any started burn and verification must finish, unrelated wallets on a mixed disc must be preserved, and all created discs require local destruction confirmation. Until the Agent is updated, the task remains visibly pending; Dashboard does not claim physical cleanup. **Agent changes are required for archive cancellation only.** See the [Agent handoff](docs/BUILD40_CANCELLATION_PROTOCOL.md).

Profiles retain all operation and cancellation records. Earlier independent names, rename lineage, portable-state filters and archive/extinction safeguards remain included.

## Build 32

- A fifteen-minute-old public preview no longer erases outstanding wallet-sync invitations or forces an unchanged review to restart. The original signature, paired request, challenge, peer addresses and wallet policy remain fixed.
- Use **Agent V2 Build 28**. It verifies the retained public snapshot against the exact active request and compares live Core facts before any locally approved transfer. Changed wallet facts, altered signatures or bindings and future timestamps remain rejected. Changed snapshots/endpoints still need deliberate reconciliation via Nodes.
- Three isolated companion Agent stores retain eleven missing wallet requests per receiver and twenty-two on the sender after an older empty withdrawal. A genuine older signed snapshot also passes through the Dashboard to the actual Agent 28 verifier without changing its hash or authorising an operation.

See [sync-list visibility](docs/BUILD32_SYNC_VISIBILITY.md). Production wallet files are not touched by these tests; direct transfer execution is tested separately in the Agent chat.

## Build 31

- Nodes explains stalled wallet synchronisation instead of directing users to an Agent list that has no actionable request. Reasons distinguish expired/changed previews, changed peer addresses, missing peers, permission checks and history recovery.
- **Start fresh sync reviews** refreshes up to 128 stalled paired reviews together after explicit local reconciliation. It creates new identities/challenges, never reuses an old approval or automatically imports/deletes wallets. Stale inventories and partially received wallets are reported separately; failures are shown with their specific explanations.
- Retry supersession no longer invents failed operation outcomes. Old attempts and late actual results remain recorded. Logs expose changed blocker counts even when an outbound review list remains empty.

Use **Agent V2 Build 27** for its separate native-close contention and guarded workspace cleanup fixes. The Nodes recovery action does not require a new Agent protocol. See [sync recovery](docs/BUILD31_SYNC_RECOVERY.md).

## Build 30

- Authenticated changed, expired or late wallet-preview notices no longer reject an entire Agent exchange. The original frozen snapshot is retained; notices never authorise a transfer, overwrite its outcome or silently start a new attempt. Unknown requests, invalid signatures and changed wallet/peer/challenge bindings remain errors.
- Preview delivery rotates by the number actually included in the bounded response, so mandatory control messages cannot repeatedly exclude the final wallets. Complete per-Agent request lists remain independent for every receiving node.
- Bounded diagnostics record changed queue revisions, send/receive counts and ignored preview notices without wallet payloads or credentials.

Use **Agent V2 Build 26** for its companion disabled-request race fix. Re-enabled policies remain controlled by each Agent's signed permission list. Actual overlapping node identities still trigger conflict protection; reconnecting does not silently replace another session.

See [sync lifecycle fixes](docs/BUILD30_SYNC_LIFECYCLE.md) and [validation](VALIDATION.md).

## Build 29

- Wallet hover controls include a pencil **Rename** action. Requests follow verified descriptor identities across persistent private wallets, watch copies and distinct multisig signing accounts.
- Every Agent approves locally. Cancellation is available until a verified rename or uncertain attempted mutation; partial completion remains visible and cannot be silently undone.
- Known archive members enter the rename queue too. Read their original disc on any capable Agent, then select that Agent for local review. A verified replacement disc is required unless the Agent independently verifies an exact persistent duplicate signer and the user explicitly chooses the planned-extinction waiver.
- Initial Wallet Builder archives and later multi-wallet archive inventories are retained as public evidence. Older discs without package evidence must be loaded once. Private wallet files never pass through Dashboard.
- Watch-only names use `_watch`. Existing unrelated targets are never overwritten. Finish active builds and archival work before renaming, then complete the rename before starting new builds or extinction.

Use matching **Agent V2 Build 25**. Physical disc writing/readback and filesystem renaming happen on the Agent; Dashboard only coordinates signed requests and outcomes. See [rename protocol](docs/BUILD29_RENAME_PROTOCOL.md).

## Build 28

- Ownership opens as soon as validated public wallet metadata is discovered. Complete history reconstruction runs separately; missing history never becomes a balance or tax result. Loading records can complete the history check at foreground priority.
- Known wallet TXIDs use their actual Core wallets first and rotate across replicas. Initial inventory does not fan out through hundreds of unrelated exchange inputs; unresolved external ancestry remains available to the tracers when needed. Owned spends still require complete input evidence.
- Historical addresses are synchronised as one **Extinct** watch-only container, with retired-wallet children displayed underneath. Ownership and C1 loss labels remain separate per child. Matching public addresses determine completeness; a partial destination archive can be extended.
- Superseded child preview notices are cryptographically checked and acknowledged so they cannot block the replacement queue. Operation outcomes/history recovery remain recorded. Deliberate retries retain the full group and children.
- Capability rejection details enter the bounded server log; the stable rejection category is preserved for Agent diagnostics.

Use matching **Agent V2 Build 24** for grouped archive reception, signed child-label display, local obsolete-denial cleanup and corrected exchange retry diagnostics. The Agent alone authorises changes. The Dashboard does not clear its denial list or confirm physical actions.

See [Build 28 discovery and synchronisation](docs/BUILD28_DISCOVERY_AND_SYNC.md) and [validation](VALIDATION.md).

## Build 27

- Parallel-key multisig badges, disc-only source badges, and cautious owner suggestions from original node wallet names.
- Wallet hover controls request locally approved archival of persistent private-key wallets. Dashboard maintains the archive/destruction queue; only the Agent confirms physical destruction. An authoritative empty queue after reconnect withdraws stale tasks.
- Duplicate transaction cards are receive-only fresh addresses; balances and UTXOs are never duplicated. Payment lines accept a live-quoted AUD amount with bounded, whole-satoshi rounding.
- Same-wallet vault renewal opens a linked Wallet Builder, reuses the existing keys/quorum, and requests locally approved additive public policies. Prior vault generations remain available. The funded PSBT is created only after the diagram advances; its vault outputs are bound to descriptor, index, address, amount and independently approved policy hash.
- Drawing a payment checks current vault spendability before creating its arrow. Locked-only sources are blocked; mixed wallets can spend only available inputs. Maximum/manual-full-available reminders are timestamped, immediately read and dismissible.
- UTXO reads combine every connected policy monitor and deduplicate replicas. Missing policy coverage is an explicit error, not a zero balance.
- Address Book arrows browse known addresses from oldest to latest, defaulting to the latest at the far right. Historical addresses are red and warned; browsing does not change builder destinations.

Use the matching **Agent V2 Build 23** for archive queues and public-policy refreshes. Existing disc accounts are refreshed locally in Agent Disc Operations; Dashboard never receives their private wallet files. A disc key cannot be moved into persistent node storage through Dashboard: perform that local operation on the Agent first. Physical drive/burn verification remains a hardware test.

See [Build 27 integration](docs/BUILD27_AGENT_CONTRACT.md) and [validation](VALIDATION.md).

## What it does

- Australian CGT working papers, financial year totals and visual transaction histories for supplied family records.
- Shared Ownership, Address Book and wallet balances.
- Node-driven Mempool fee estimates, pending/mined blocks and node-only transaction/address/output search, with Bitcoin Core unit conversions.
- Shared transaction templates, unsigned PSBT preparation and verified partial-signature collection. Private keys remain in Bitcoin Core; private signing remains local to the Agent and broadcast requires local Agent authorisation.
- Node discovery through Wallaby Agent, querying every connected node and deduplicating wallet replicas by public descriptors.

Vault monitoring uses public vault records and connected Core nodes. Reports depend on complete source records, ownership and pricing evidence; review the working papers before relying on them for tax reporting.

## This development update

Build 27 starts Balance Trace automatically when all ownership rows have owners. It opens Terminal Activity immediately, then returns to Wallets on completion. Reopening completed Ownership does not repeat the calculation; changing an assignment triggers verification again.

Salary Builder puts the collapsed tax/SMSF assumptions inside Stage 1, and the pay schedule at the start of Stage 2. Confirm the schedule before configuring salary cards; changing it hides those cards until reconfirmed. Monthly means 12 payments, modelled on each month’s first day. Distributor choices include every active wallet, regardless of owner. Scenario BTC/AUD and target age remain at the top; the saved-quote control is removed. Each person’s card has its own pay-rise multiplier. For an independent target it multiplies salary; for a recipient it multiplies their incoming share, with combined effective shares limited to 100%.

Wallets Sources now show balance/existence information: wallet-owned addresses and node names, or the latest supplied Bitaroo statement filenames. Cost provenance remains internal to the calculations and is not displayed as wallet identity.

Mempool has darker green blocks and a stronger dark/light red pulse for pending blocks. BTC/vB rounds half up to a whole satoshi, with copied values matching the display. Search sits above the blocks and accepts a TXID, address or TXID:output_index. A compact input/output explorer below the blocks adapts Blockstream Esplora’s MIT-licensed presentation. Clicking an address or previous output searches in the same page. Only a pending transaction or one in the three latest blocks receives a block marker.

Search uses connected Core nodes only, rotating requests and trying other nodes when a transaction is unavailable. Address searches check all connected nodes’ loaded wallets, including newly connected wallets not yet in the saved catalog. Replicated UTXOs are counted once. Core has no general address-history index: transaction lists reflect loaded wallet snapshots; a recognised wallet can provide current unspent outputs. Missing history, incomplete node checks and unknown fees are stated explicitly. Pruned or unindexed history is not treated as a zero balance, and no public explorer is contacted.

## Earlier Build 20

Build 20 adds saved, single-creator Wallet Builder and Transaction Builder workspaces. Wallet Builder begins with **Start Building a Wallet**. Each builder restores its public draft and stage after closing the browser; another user sees who is using it and cannot modify it. Transaction templates remain shared. The creator coordinates signatures for every funding wallet, with independent local approval on each Agent; other family members no longer need Dashboard signing notifications or logins.

Cancelling a build stops Dashboard coordination and sends exact-scoped cleanup requests only to Agents that received its work. The progress dialog lists each hostname with online/sending (amber), offline (red), or received/Complete (green). Offline delivery is saved and retried. Dashboard releases the workspace after every Agent acknowledges durable receipt. This never authorises deletion: the Agent must offer local **Delete and Cancel** or **Backup and Cancel** review. Cancellation cannot undo broadcast. Drafts with no Agent work cancel immediately. See the [cancellation contract](docs/BUILDER_CANCELLATION.md).

The Agent must implement `builder_cancellation_v1`, `builder_cancel` and `builder_cancel_receipt`. This update changes Dashboard only; an Agent without the capability remains pending rather than being falsely marked complete. Existing wallet-setup expiry and restart reconciliation checks remain; unfinished key ceremonies are never silently restarted. Automatic replication of a newly created monitor waits until its builder workspace completes.

## Earlier Build 19

Build 19 restored the calendar/timezone vault estimator, improved sidebar reveal and moved Make Extinct into wallet hover details. Those features remain. Pending-block rendering is enabled; the previously supplied generic local RPC failure remains an Agent-side investigation, with bounded Dashboard diagnostics and no invented projection or public fallback.

## Earlier Build 18

Build 18 changes Make Extinct into a zero-funds archival-and-deletion request. Dashboard checks fresh Core balances on connected replicas, including known related vault monitors and multisig signing accounts. Pending, immature, watch-only or locked funds block retirement; unavailable balances and scans also block it. The Agent must independently repeat the checks and obtain approval on its local Authorise page before changing anything.

Dashboard requires the new `wallet_retirement_v2` Agent capability. Agent Build 18 implements the matching local operation. Earlier archive-only Agents cannot execute this request; upgrade each holder before it can confirm retirement. Dashboard never deletes wallet files, unlocks wallets or invokes mutating Core RPCs.

Agent completion must explicitly confirm verified historical archival, complete history, zero funds and deletion of every listed local wallet account. Cancelled, denied and failed requests remain flagged. After every known holder confirms archival and deletion, the initiating user confirms that archival discs were destroyed or that none existed; this is a recorded user statement, not a physical-media verification. Requests cover every known private and watch-only holder. Each Agent needs separate local approval. Offline or unsupported holders remain pending, with durable per-holder completion tracking; one node's success cannot complete the group. Retirement records survive profile backup/restore, and normal sync is suppressed for policies being retired or already retired.

## Earlier Build 17 fixes

Build 17 repairs Fee Estimates after clearing or loading a profile: the old background service is closed and replaced with a fresh service, so node-backed estimates can refresh without restarting Dashboard. Deferred updates are polled promptly; an empty mempool and an unavailable projection have explicit messages instead of an indefinite waiting label.

There is no separate Dashboard page. Use the sidebar to open tools; login, transaction completion/cancellation and finished activity return to Ownership. Historical ownership rows display **Extinct Wallet** as their source. The SMSF icon depicts a person leaning on a cane.

Wallet Builder shows Role name only for multisignature policies. Single Signature and plain Vault still select a producing Agent and storage; their single signing role is assigned automatically. No Agent code or protocol change is needed.

## Earlier Build 16 fixes

Build 16 fixes the Fee Estimates block view: projected fee-rate labels are compact and stay inside each cube, with a separate sats/vB label. The full node estimate remains in hover details and source data. This is display formatting only; fee conversions and transaction calculations retain their original precision.

Ownership no longer contains the retired requirement for a Wallets ODS upload. Load Records and Review Ownership remains disabled until node-wallet loading (or the explicit skip choice), Exchanges ODS and every applicable user's Bitaroo PDFs have been supplied. Non Bitaroo user declarations still apply. The obsolete “Select Wallets ODS and Exchanges ODS first” error is removed.

Wallet Builder discovery now displays progress beside the button, including requests in progress, identity counts, waiting replies and no eligible Agents. Old GET responses cannot overwrite a newer discovery. Repeated clicks reuse an outstanding challenge. Previously queued, validly signed stale replies are acknowledged and discarded so they cannot block a new reply; they never mark an Agent verified. Signature checks and manual fingerprint comparison remain required. No Agent protocol change or fallback identity source is introduced.

Build 15's live-node builder gating and bounded diagnostic logs remain. This build changes no Agent code, private-key handling, transaction/tax algorithms or broadcast behavior.

## Agent compatibility

Use **[Agent V2 Development Build 20](https://github.com/bluesilent9/wallaby-agent/tree/Development)** for paged fee projections. Builder cancellation also requires the documented receipt capability on each involved Agent. Both sides now implement unsigned vault composition, separately authorised partial signatures and broadcast, and the signed initial wallet-preview relay for direct Agent-to-Agent synchronisation. The Dashboard verifies the sender's signature and frozen request bindings before relay. Receiving users must still compare the sender identity fingerprint directly between Agent screens; a fingerprint supplied solely through Dashboard is not trusted.

Agent Build 10 or later is required for the current synchronization-permission and disc workflow; Wallet Builder requires Build 9 or later; the existing exchange requires Build 8 or later; older synchronization protocols have been removed. Each Agent receives its complete alphabetically ordered missing-wallet list, including labelled historical address groups. The signed version-2 preview preserves public vault policies, address labels and LOST timestamps. Persistent revisions withdraw outdated lists when peers disconnect. `recovery_required` means received but incomplete; only final receiver completion clears both Agents’ lists. History recovery is locally authorised on the Agent and retains its pruning configuration. No remote restart/reindex command is sent.

**Wallets** now filters by owner and by **Active Wallets / Extinct Wallets** (at least one selected). Extinct cards are grey and show confirmed UTC activity years rather than balances/gains. Addresses sharing a historical label are one logical wallet; different labels remain separate ownership assignments even inside one Core archive. **Make Extinct** now uses the Build 18 zero-funds, locally approved archival-and-deletion protocol described above. Retirement itself does not declare lost keys or create a tax loss. See [the protocol](docs/AGENT_PROTOCOL.md) and [validation](VALIDATION.md).

## Run from source

Python 3.11 or later. Ubuntu/Kubuntu dependencies:

```sh
sudo apt install python3 python3-pypdf python3-odf tzdata 7zip openssh-client
python3 launch_wallaby_bitcoin_dashboard.py --no-browser
```

The shell launcher also defaults to no browser. The browser service uses **38951** and the Agent HTTP/JSON interface uses **56443**. Both bind to IPv4/IPv6 loopback. Reach them through authorised SSH tunnels; client-side forwarded ports may differ. Open `http://[::1]:38951/` on the tunnel client. No Bitcoin Core installation is needed on the server.

Create family users, connect Agents with the desired Core wallets loaded, then allow automatic wallet discovery to complete. This also reads fixed `addr()` policies from watch-only wallets and groups their addresses using Core labels. Upload each applicable profile's Bitaroo PDFs and the shared Exchanges ODS, then confirm ownership and select Verify Balances in Stage 2. The Extinct Wallets ODS is no longer uploaded. Wallets then becomes available. Generate the separate family CGT report for tax totals/history, which show the logged-in profile only.

This is a trusted-family service behind SSH, not a public password-authenticated website. Node identity hints are not substitutes for SSH authentication. Core RPC cookies stay on the node computers.

## DEB installation

The package is `wallaby-dashboard`, version `1.0.0~dev31`, architecture `all`. Its installation creates the dedicated `wallaby-dashboard` service user, starts `wallaby-dashboard.service` immediately and enables it at boot. Application files go under `/usr/lib/wallaby-dashboard`; private durable records go under `/var/lib/wallaby-dashboard`; temporary workspace files use `/var/cache/wallaby-dashboard`.

The installer does **not** remove an older manually installed service. For the first DEB installation, retire the exact legacy manual service before installing to avoid port conflicts. For an existing Dashboard DEB, install the new DEB with `sudo apt install ./wallaby-dashboard_1.0.0~dev31_all.deb`; it preserves records and restarts the service. [Clean-install instructions](docs/CLEAN_INSTALL.md) apply only to the older manual installation.

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

Extinct wallets now come from labelled fixed-address `addr()` descriptors in a Core wallet with private keys disabled. Labels group addresses into logical wallets, independently of the parent Core wallet's name. Automatic discovery queries every ready connected node and merges matching replicas. The obsolete historical-wallet ODS upload is removed; exchange ODS input remains unchanged. Historical group labels are read-only in Ownership and cannot be used as receiving/payment destinations.

A label `Earlier Wallet | LOST:DD/MM/YYYY HH:MM:SS UTC` declares irreversible loss of access at that time, without compensation. Build 7 records the affected retained outputs as a separate CGT event C1, consumes their quantity once, and calculates a loss from the traced reduced cost base using the existing proportional parcel convention. It does not use loss-date market value, indexation or a 50% loss discount. Existing annual loss application and carry-forward apply. The original blockchain outputs remain in the evidence graph. A label is a user declaration, not blockchain proof that a key was lost. Later receipts/spends conflicting with the declaration, missing history and unresolved acquisition cost require correction rather than an invented deduction.

After upgrading, wait for automatic node wallet discovery, load the exchange statements and **Verify Balances**, then regenerate CGT reports. Older descriptor scans and balance costs do not unlock the revised workflow. Your node must have completed its relevant watch-wallet rescan.

## Licence

This combined distribution is provided under GNU AGPL version 3. [Full licence](LICENSE.txt). Mempool component copyright and original pinned sources are retained in [vendor/mempool](vendor/mempool/UPSTREAM.md); other bundled libraries retain their notices. The running service provides corresponding source through its Source code link (`/source.zip`).

## Build 8

Nodes now distinguish connection from blockchain readiness. Vault cards use verified public policies and live node balances, with green/amber/red status and short unlock estimates. The Builder accepts fixed unsigned vault proposals, composes other wallets' payments before signing, and uses locally authorised Agent signature requests followed by combined-PSBT broadcast. Single and multiple senders use the same PSBT protocol.

That Build 8 integration boundary is completed in Build 9 with Agent Build 7.

## Build 9

The Dashboard verifies and relays a frozen signed public wallet snapshot bound to both peers, addresses, wallet policy and a fresh challenge. Only one pair is dispatched at a time, alphabetically. Temporary synchronisation busy responses preserve node status and delay inventory refresh. Duplicate delivery is idempotent; expired snapshots are not renewed, and failed/denied operations require an explicit new attempt. Requests, previews and outcomes survive restart/profile backup. Private wallet files travel directly between Agents after local approval, never through the Dashboard. Existing tax and Balance Trace calculations are unchanged.

The combined pending-request table includes remembered offline nodes. Obsolete nodes can be forgotten after disconnection, retaining applied history and leaving portable disc work available for reassignment to a matching loaded Agent. Same-wallet transfers were checked: new-address cards start at zero, sending cards are red, receiving and network-fee cards green, and a maximum transfer accounts for its fee without adding the received amount back to the source card.
