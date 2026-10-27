# V1 Build 1 — pre-build validation

Prepared 27 October 2026. No DEB has been built at this checkpoint.

Four focused Dashboard regressions pass: immediate loading failures remain on Ownership after Terminal Activity locks; the broker dispatches at most two commands per Agent; package scripts enable/start only the new service and do not remove a legacy service; actual Agent Build 4 loads twelve wallets through the real Dashboard hub and strict simulated Core HTTP transport. Names include the empty default wallet and Unicode/punctuation. Public blockchain scanning is isolated in this transport regression.

All 44 matching Agent tests pass. The Dashboard's CGT arithmetic is unchanged by this update. Existing historical validation is not a substitute for testing a live Core node.

Pending after the requested reboot: DEB generation/inspection, checksum generation, installation/startup and real node loading. The DEB is prepared to enable and immediately start wallaby-dashboard.service on installation, using ports 38951/56443 on IPv4/IPv6 loopback. No server or laptop service has been modified during preparation.

Browser regression: clicking Load Node Wallets with no connected Agent switched to Terminal Activity, then returned to Ownership with the connection error visibly retained and Terminal Activity disabled. The user can retry from Ownership.
