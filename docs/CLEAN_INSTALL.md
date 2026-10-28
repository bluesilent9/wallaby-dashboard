# Fresh server installation — V1 Build 7

These commands are for the **server**, inside your SSH session. They remove the older manually installed Dashboard and its saved configuration/data. They do not change Wallaby Agent or SSH tunnel services. The new package starts immediately and is enabled at boot.

Download the new installer, its checksum and the [legacy cleanup helper](remove_legacy_installation.sh):

```sh
mkdir -p ~/wallaby-dashboard-install
cd ~/wallaby-dashboard-install
curl -fL -o wallaby-dashboard_1.0.0~dev7_all.deb https://raw.githubusercontent.com/bluesilent9/wallaby-dashboard/Development/dist/wallaby-dashboard_1.0.0~dev7_all.deb
curl -fL -o SHA256SUMS.txt https://raw.githubusercontent.com/bluesilent9/wallaby-dashboard/Development/dist/SHA256SUMS.txt
sha256sum -c SHA256SUMS.txt
curl -fL -o remove_legacy_installation.sh https://raw.githubusercontent.com/bluesilent9/wallaby-dashboard/Development/docs/remove_legacy_installation.sh
```

Continue only if the checksum reports `OK`. Inspect the helper if desired. It finds the old system service by its application path rather than its name, stops/disables it, removes its locally installed unit and drop-ins, and deletes the application directory including configuration and saved data. It also clears that service user's default Dashboard cache and the new package's data/cache directories for a clean test. It stops without removing anything if the old service cannot be identified or a Dashboard DEB is already installed.

For the previous installation at `/storage/Programs/wallaby_bitcoin_dashboard`:

```sh
sudo bash ./remove_legacy_installation.sh /storage/Programs/wallaby_bitcoin_dashboard
```

If that command fails, stop before installation and inspect its message. Once it succeeds:

```sh
sudo apt install ./wallaby-dashboard_1.0.0~dev7_all.deb
systemctl is-enabled wallaby-dashboard.service
systemctl is-active wallaby-dashboard.service
systemctl status wallaby-dashboard.service --no-pager
```

The first two checks should print `enabled` and `active`. To inspect startup errors:

```sh
sudo journalctl -u wallaby-dashboard.service -n 80 --no-pager
```

Use your existing Agent/tunnel and open **http://[::1]:38951/** on your laptop. The web port is 38951 and the Agent port is 56443; both remain loopback-only on IPv4/IPv6. New code lives under `/usr/lib/wallaby-dashboard`; records go under `/var/lib/wallaby-dashboard`; temporary workspaces go under `/var/cache/wallaby-dashboard`. Create users and run setup again. The old directory and launcher are no longer used.
