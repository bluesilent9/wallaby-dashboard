# Agent follow-up after Dashboard V1 Build 13

Dashboard Build 13 requires no new Agent protocol. Its changes are presentation, freshness handling, fee review and removal of obsolete current-UI manual-signing screens. The user explicitly keeps the existing signature collection and broadcast workflow.

For a later Agent update:

1. **FEE-01:** label the fee in the local transaction diagram “Total network fee”. Do not label the full joint fee as paid from the currently signing wallet. The total comes from verified inputs minus outputs; the blockchain does not assign each wallet a fee share.
2. Native signing passphrase prompts should name the exact locally verified Core wallet and action. Clearly distinguish Core wallet passphrases, disc-backup passphrases and peer computer login passwords. Do not take the authoritative wallet name from a Dashboard display label.
3. Compare one independently checked fingerprint per distinct producing Agent within a wallet ceremony, then bind all its roles to that verified identity. Keep full fingerprints and exact policy/address checks; do not accept a remote “already verified” claim.
4. Add an independent local high-fee review/acknowledgement based on the final Core-verified transaction. Dashboard warns at a fee of at least 10% of planned payments or a rate of at least 100 sats/vB. Those browser thresholds and acknowledgement do not authorise the Agent. The Agent must determine its own meaningful denominator for payments/change; do not infer it blindly from Dashboard labels. Keep the ability to deliberately approve a high fee.
5. Keep unsent, pending, attempted/outcome-uncertain and completed statuses clear. Never automatically repeat irreversible operations after lost responses, failed burns or restarts.
6. After checking callers, remove retired full-wallet Vault Builder entry points/helpers and obsolete tests. Preserve current Wallet Builder creation, policy checks, archive restoration and PSBT signing capabilities.

Do not implement withholding the last signature or a new broadcast security barrier: the user declined that change. No Agent files or repository were changed by Dashboard Build 13.

Physical optical media and the installed protected Qt/systemd/AppArmor/Snap lifecycle still require deployment testing. No new guarantee about hardware or operating-system behaviour is implied.
