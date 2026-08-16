#!/bin/bash
# Zet de autoupdate aan voor een Docker Compose-deploy: elke vijf minuten
# kijken of main nieuwe commits heeft en dan herbouwen (zie autoupdate.sh).
# Gebruik: sudo bash deploy/install-autoupdate.sh   (vanuit de deploy-kloon)
#
# Los van install.sh, bewust: dat script bedient de deploy zonder Docker
# (venv + eigen systemd-service) en kopieert de code weg uit de repo. Deze
# autoupdate hoort juist bij de compose-deploy, die vanuit de kloon zelf
# draait -- de twee combineren zou install.sh een docker-afhankelijkheid
# geven die de helft van zijn gebruikers niet heeft.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "== Systemd =="
# Het pad van deze kloon wordt in de service-unit ingevuld; systemd kent geen
# relatieve paden en de docs schrijven niet voor waar je kloont.
sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/deploy/meshmanager-autoupdate.service" \
  > /etc/systemd/system/meshmanager-autoupdate.service
cp "$REPO_DIR/deploy/meshmanager-autoupdate.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now meshmanager-autoupdate.timer

systemctl --no-pager list-timers meshmanager-autoupdate.timer || true
echo
echo "Klaar. De timer draait als root; zorg dat deze kloon ook van root is,"
echo "anders weigert git ('dubious ownership')."
echo "Volgen: journalctl -u meshmanager-autoupdate -f   (stil als er niets is)"
