# V1 Build 1 — build validation

Built 27 October 2026. Package: `wallaby-dashboard_1.0.0~dev1_all.deb`.

Four focused Dashboard regressions pass: immediate loading failures remain on Ownership after Terminal Activity locks; the broker dispatches at most two commands per Agent; package scripts enable/start only the new service and do not remove a legacy service; actual Agent Build 4 loads twelve wallets through the real Dashboard hub and strict simulated Core HTTP transport. Names include the empty default wallet and Unicode/punctuation. Public blockchain scanning is isolated in this transport regression.

All 44 matching Agent tests pass. The Dashboard's CGT arithmetic is unchanged by this update. Existing historical validation is not a substitute for testing a live Core node.

Package inspection verified the Debian metadata, exact packaged runtime/source parity, absence of personal records/caches and the dedicated system service. A process launched directly from the extracted DEB served the browser page and Agent health endpoint over both IPv4 and IPv6 loopback. The maintainer script enables and immediately starts wallaby-dashboard.service on installation, using ports 38951/56443 by default. The package and checksum are uploaded on Development. Live systemd installation/startup and real node loading remain pending. No server or laptop service has been modified during this build.

Browser regression: clicking Load Node Wallets with no connected Agent switched to Terminal Activity, then returned to Ownership with the connection error visibly retained and Terminal Activity disabled. The user can retry from Ownership.
