#!/bin/bash
# Installatie/update van MC Repeater Stats op Debian (LXC).
# Gebruik: sudo bash deploy/install.sh   (vanuit de repo-root)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/opt/mc-repeater-stats
DATA_DIR=/var/lib/mc-repeater-stats

echo "== Pakketten =="
apt-get update -qq
apt-get install -y -qq python3 python3-venv rsync

echo "== Gebruiker en mappen =="
id -u mcstats &>/dev/null || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin mcstats
mkdir -p "$APP_DIR" "$DATA_DIR"

echo "== Code kopiëren =="
rsync -a --delete "$REPO_DIR/server/" "$APP_DIR/server/"

echo "== Python-omgeving =="
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/server/requirements.txt"

chown -R mcstats:mcstats "$DATA_DIR"
chown -R root:root "$APP_DIR"

echo "== Systemd =="
cp "$REPO_DIR/deploy/mc-repeater-stats.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable mc-repeater-stats
systemctl restart mc-repeater-stats

sleep 2
systemctl --no-pager --lines 5 status mc-repeater-stats || true
echo
echo "Klaar. Site draait op poort 8080."
echo "Eerste start? Admin-wachtwoord staat in: journalctl -u mc-repeater-stats | grep Wachtwoord"
