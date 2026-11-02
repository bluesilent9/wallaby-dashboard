# Install or upgrade — V1 Build 32

Download the Development DEB and verify it against the accompanying SHA256 checksum before installation. Install it using Kubuntu's package installer or your usual Debian package-management workflow.

The package creates its dedicated service account, starts the Dashboard service immediately and enables startup at boot. An upgrade from an existing Dashboard DEB preserves saved profiles and records.

For an older manually installed Dashboard, retire its previous service locally before installing the DEB so that the two installations do not compete for the same listeners. Exact cleanup commands depend on that machine's installation and are intentionally omitted from this public guide. Keep any records you want before removing an old installation.

Access remains through your existing authorised SSH tunnels. Installing the Dashboard does not update Wallaby Agent or change tunnel services. Build 24 Salary Builder is Dashboard-only and requires no new Agent protocol. Existing signing and transfer capabilities depend on the matching Agent; see [the integration contract](AGENT_PROTOCOL.md).

After this upgrade, run **Verify Balances** once before using complete Salary Builder tax estimates; the new balance format retains acquisition parcels.
