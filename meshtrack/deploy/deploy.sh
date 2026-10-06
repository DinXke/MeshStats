#!/usr/bin/env bash
# Code naar de server zetten en herstarten. Gebruik: deploy/deploy.sh [root@host]
# Eerste keer: maakt gebruiker, mappen, venv en de systemd-unit aan. De config
# (/etc/meshtrack/config.yaml) en de data (/var/lib/meshtrack) worden nooit overschreven.
set -euo pipefail
HOST="${1:-root@10.10.10.178}"
cd "$(dirname "$0")/.."
tar --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.egg-info' -czf - server deploy | ssh "$HOST" '
set -euo pipefail
id meshtrack >/dev/null 2>&1 || useradd --system --home /var/lib/meshtrack --shell /usr/sbin/nologin meshtrack
mkdir -p /opt/meshtrack /etc/meshtrack /var/lib/meshtrack/tiles
rm -rf /opt/meshtrack/new && mkdir -p /opt/meshtrack/new
tar -xzf - -C /opt/meshtrack/new
rm -rf /opt/meshtrack/server && mv /opt/meshtrack/new/server /opt/meshtrack/server
cp /opt/meshtrack/new/deploy/meshtrack.service /etc/systemd/system/meshtrack.service
rm -rf /opt/meshtrack/new
[ -x /opt/meshtrack/venv/bin/python ] || python3 -m venv /opt/meshtrack/venv
/opt/meshtrack/venv/bin/pip install -q --upgrade pip
/opt/meshtrack/venv/bin/pip install -q -r /opt/meshtrack/server/requirements.txt
[ -f /etc/meshtrack/config.yaml ] || cp /opt/meshtrack/server/config.example.yaml /etc/meshtrack/config.yaml
chown -R meshtrack:meshtrack /var/lib/meshtrack
chown root:meshtrack /etc/meshtrack/config.yaml && chmod 640 /etc/meshtrack/config.yaml
systemctl daemon-reload
systemctl enable meshtrack >/dev/null 2>&1
systemctl restart meshtrack
sleep 4
systemctl --no-pager --lines=10 status meshtrack
'
