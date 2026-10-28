# V1 Build 7 — validation

Scope: replace historical-wallet ODS uploads with Core-labelled fixed-address watch groups; add no-compensation CGT event C1 accounting for declared irreversible loss of access.

- 61 Python tests passed, including the Agent V2 Build 4 → HTTP broker → simulated pruned Core integration. No tests skipped in this run.
- Historical fixtures cover addr() parsing, grouping multiple labelled addresses, distinct groups in one parent wallet, original parent RPC identity, replica deduplication, disagreement errors, missing labels, key-disabled enforcement and fixed-name inventory.
- A decoded node-ledger fixture reconstructs a transfer, retained change and loss without any public explorer request. The creating output remains in chain evidence; the economic loss removes its retained quantity once.
- C1 fixtures cover zero compensation/proceeds, traced proportional reduced cost, pre/post reform cases without indexation or discount, losses before annual discounts, carry-forward, missing quantity/basis errors, repeated processing, graph identity and same-instant event ordering. Balance Trace and CGT agree on removed and retained costs. Other owners are not charged.
- The declared loss record is linked to original outpoints in the shared graph; it creates no fake blockchain transaction. Later conflicting receipts/spends, future dates and labels without retained outputs fail explicitly.
- Existing JavaScript regressions cover immediate Terminal Activity navigation, return to Ownership Stage 3, suggestions, icon ordering, balance tooltips, fees and block marker helpers. The obsolete ODS upload assertion is replaced with its removal assertion.
- Descriptor-scan fingerprints and balance algorithm versions invalidate older scans/cost snapshots; users must load node wallets and verify balances again. Exchange ODS parsing and its signed trading directions are unchanged.

The real family node is resynchronising. These are deterministic fixture and package checks, not a claim that a fresh family-wide production report has already been run.

Tax references: ATO loss/theft of crypto guidance and ITAA 1997 s104-20. The LOST suffix is a user declaration of irrecoverability/discovery time, not proof supplied by Bitcoin's blockchain. This path assumes no compensation and uses the application's existing parcel-cost convention.
