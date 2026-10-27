# V1 Build 3 — validation

The Python regressions include receive-only personal balance provenance, zero/SMSF exclusion, complete non-joint family balance verification without generating tax reports, context-bound persistent decisions, profile isolation and watch display naming. The actual Agent transport fixture confirms that a wallet with a normal name is recognised as watch-only from Core metadata, while names containing Watch are not classified as watch-only when private keys are enabled.

JavaScript regressions cover the Build 2 poll race and restored descriptor readiness in a fresh page session. Existing ownership/transport/service checks are retained. Package inspection and IPv4/IPv6 extracted-runtime smoke checks are performed before publication. No supplied personal diagnostic logs or wallet records are included in the source/package.

Balance Trace reuses the established historical lot/accounting logic; real family input completeness and online quote availability remain live-installation checks. Unknown owned sources fail rather than invent a cost base. CGT no longer invokes the removed portfolio current-basis replay.
