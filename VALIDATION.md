# V1 Build 45 validation

504 Python tests and 40 JavaScript interface suites pass. New archive regressions cover Checking, synchronising, downloading and error chain-health states while a separate wallet ceremony remains pending. They confirm exact preservation of workspace/setup files, no duplicate queue entries, and continued refusal of watch-only, disc-loaded, scanning, disconnected or changed live wallets.

Existing archive Agent selection, authorisation, queue identity, live read checks, setup confirmation, saved profiles, multisig/vault, PSBT, tracing and cancellation checks remain active. Installer checks verify source parity, exclusion of runtime/user data, automatic service enable/start and isolated IPv4/IPv6 browser and Agent health. Results are recorded in outputs/build45_review.

No production node, wallet, Agent code, running workspace or installed service was changed. No new wire protocol is added. The Agent chat separately handles its busy gates and applying that local fix without discarding the user's unfinished setup. Physical media and the user's live ceremony were not exercised by these isolated tests.
