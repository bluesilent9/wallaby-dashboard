#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
exec python3 launch_wallaby_bitcoin_dashboard.py --no-browser "$@"
