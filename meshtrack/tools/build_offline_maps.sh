#!/usr/bin/env bash
# Kaarten voor de offline-app (/offline) uitsnijden uit de weergavekaart.
# Draait op de server, in tiles_dir:  bash build_offline_maps.sh [/var/lib/meshtrack/tiles]
# De sleutels moeten overeenkomen met OFFLINE_MAPS in server/meshtrack/main.py.
set -euo pipefail
cd "${1:-/var/lib/meshtrack/tiles}"
mkdir -p offline
cut() { pmtiles extract basemap.pmtiles "offline/$1.pmtiles.tmp" --bbox="$2" --maxzoom="$3" && mv "offline/$1.pmtiles.tmp" "offline/$1.pmtiles"; }
cut limburg-z14   4.95,50.65,6.0,51.35  14
cut belgie-z13    2.5,49.45,6.45,51.55  13
cut benelux-z10   2.5,49.4,7.3,53.6     10
cut benelux-z12   2.5,49.4,7.3,53.6     12
cut frankrijk-z10 -5.3,41.3,9.7,51.2    10
cut frankrijk-z12 -5.3,41.3,9.7,51.2    12
cut duitsland-z10 5.8,47.2,15.1,55.1    10
cut duitsland-z12 5.8,47.2,15.1,55.1    12
chown -R meshtrack:meshtrack offline 2>/dev/null || true
ls -la offline
