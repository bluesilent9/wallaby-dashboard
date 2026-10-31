# V1 Build 17 — validation

- 181 Python regression tests pass, including independent interoperability using actual Agent Build 16 source.
- A real isolated HTTP endpoint regression exercises both profile clear and load: the closed fee service is replaced, its cached data is discarded, and all four rates can refresh again. The same test reproduces the failure against Build 16.
- Thirteen JavaScript regression programs and all asset syntax checks pass. New checks exercise all four policy selections, dynamic signer counts, hidden single-role names, retained Agent/storage controls, Extinct Wallet source text, removed Dashboard navigation, explicit empty/error mempool messages and prompt polling after deferral.
- Existing node connection gates, signed identity checks, transaction and vault workflows retain their regression coverage. No Agent changes or protocol changes are included.
- Package validation checks version, runtime/source parity, absence of user records and test data, source archive availability, service startup/enable scripts and isolated IPv4/IPv6 browser and Agent health endpoints. The DEB is not installed on the development laptop or production server by these tests.

The supplied production log contained successful earlier fee refreshes but no later refresh entries. The permanently closed service after profile replacement is independently reproducible; no live server repair or private wallet operation was performed. Physical M-Disc and installed protected-desktop checks remain deployment tests.
