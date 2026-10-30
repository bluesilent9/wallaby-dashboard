# Install or upgrade — V1 Build 11

Download the Development DEB and verify it against the accompanying SHA256 checksum before installation. Install it using Kubuntu's package installer or your usual Debian package-management workflow.

The package creates its dedicated service account, starts the Dashboard service immediately and enables startup at boot. An upgrade from an existing Dashboard DEB preserves saved profiles and records.

For an older manually installed Dashboard, retire its previous service locally before installing the DEB so that the two installations do not compete for the same listeners. Exact cleanup commands depend on that machine's installation and are intentionally omitted from this public guide. Keep any records you want before removing an old installation.

Access remains through your existing authorised SSH tunnels. Installing the Dashboard does not update Wallaby Agent or change tunnel services. The new Agent signing and transfer capabilities require their separately planned Agent update; see [the integration contract](AGENT_PROTOCOL.md).
