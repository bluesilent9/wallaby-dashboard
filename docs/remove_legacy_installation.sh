#!/bin/bash
# Retire the user's manually installed Dashboard before installing the DEB.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo 'Run this script with sudo.' >&2; exit 1; fi
legacy_root=${1:?Supply the old Dashboard installation directory}
legacy_root=${legacy_root%/}
if [[ "$legacy_root" != /* || "$legacy_root" = / ]]; then echo 'Supply an absolute application directory.' >&2; exit 1; fi
if dpkg-query -W -f='${Status}' wallaby-dashboard 2>/dev/null | grep -qx 'install ok installed'; then
    echo 'The Dashboard DEB is already installed. This helper only removes the older manual installation.' >&2; exit 1
fi
if [ ! -f "$legacy_root/launch_wallaby_bitcoin_dashboard.py" ]; then
    echo 'The supplied directory does not contain the old Dashboard launcher; nothing removed.' >&2; exit 1
fi
units=(); fragments=(); homes=()
while read -r unit rest; do
    text=$(systemctl cat "$unit" 2>/dev/null) || continue
    if ! grep -Fq -- "$legacy_root" <<< "$text"; then continue; fi
    fragment=$(systemctl show "$unit" --property=FragmentPath --value)
    case "$fragment" in
        /etc/systemd/system/"$unit"|/usr/local/lib/systemd/system/"$unit") ;;
        *) echo "Unexpected legacy unit location: $fragment. Nothing removed." >&2; exit 1 ;;
    esac
    units+=("$unit"); fragments+=("$fragment")
    account=$(systemctl show "$unit" --property=User --value)
    account=${account:-root}
    home=$(getent passwd "$account" | cut -d: -f6)
    if [[ "$home" = /* ]]; then homes+=("$home"); fi
    printf 'Found legacy service: %s\n' "$unit"
done < <(systemctl list-unit-files --type=service --no-legend --no-pager)
if [ "${#units[@]}" -eq 0 ]; then
    echo 'No system service referencing that installation was found. Nothing removed; inspect the old service before proceeding.' >&2; exit 1
fi
for i in "${!units[@]}"; do
    systemctl disable --now "${units[$i]}"
    rm -f -- "${fragments[$i]}"
    rm -rf -- "/etc/systemd/system/${units[$i]}.d"
done
systemctl daemon-reload
for unit in "${units[@]}"; do systemctl reset-failed "$unit" 2>/dev/null || true; done
rm -rf -- "$legacy_root"
for home in "${homes[@]}"; do rm -rf -- "$home/.cache/wallaby-bitcoin-dashboard"; done
rm -rf -- /var/lib/wallaby-dashboard /var/cache/wallaby-dashboard
echo 'Removed the legacy Dashboard services, application directory, configuration, saved data and default application caches.'
echo 'Wallaby Agent and SSH tunnel services have not been changed. Install the Dashboard DEB next.'
