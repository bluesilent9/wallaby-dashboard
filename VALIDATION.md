# V1 Build 2 — validation

Prepared 27 October 2026. Package: `wallaby-dashboard_1.0.0~dev2_all.deb`.

The JavaScript regression executes the actual startJob and poll functions with a deliberately delayed prior descriptor-job response. It reproduces the incorrect state on Build 1 and passes on Build 2. It verifies immediate navigation to Terminal Activity before uploads finish, exclusion of the old poll, return to Ownership, Stage 3 scroll target and refreshed inventory. It also checks that the extinct upload selects the shared fossil icon and that the fossil uses a black fill without an outline stroke.

The four existing Python transport/error/service regressions pass, including the actual Agent Build 4 client through the Dashboard hub. Python/JavaScript/shell syntax and extracted-package runtime/source parity are checked. Packaged browser and Agent endpoints are smoke-tested on both IPv4 and IPv6 loopback. The installer starts/enables the service and restarts it on DEB upgrades. No live installation or service changes are performed by the build checks.

Public history tracing, ownership identities and CGT arithmetic are unchanged. Personal records, node identities, wallet descriptors and the supplied diagnostic log are excluded from the repository/package.
