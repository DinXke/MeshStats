#!/bin/bash
# Installatie/update van MeshManager op Debian (LXC), zonder Docker.
# Gebruik: sudo bash deploy/install.sh   (vanuit de repo-root)
#
# Dit script moet ook draaien op een machine waar de vorige versie onder de
# oude naam staat (MC Repeater Stats). Twee dingen mogen daarbij niet gebeuren:
# de databank achterlaten in een map waar niemand meer kijkt, en twee units
# tegelijk laten vechten om poort 8080. Zie de twee blokken hieronder.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/opt/meshmanager
SERVICE=meshmanager
USER_NAME=meshmanager

# De oude namen, van voor de hernoeming. Weg te halen als er geen installatie
# van voor die wissel meer bestaat -- en dat is iets wat alleen de beheerder
# van die machine kan weten.
OLD_APP_DIR=/opt/mc-repeater-stats
OLD_DATA_DIR=/var/lib/mc-repeater-stats
OLD_SERVICE=mc-repeater-stats

# De datamap VOLGT de databank, niet andersom. Staat er al een oude, dan blijft
# die in gebruik -- precies zoals de site zelf een bestaand mcs.sqlite3 gebruikt
# waar het staat. Verplaatsen is eenrichtingsverkeer: wie na deze update
# terugrolt naar de vorige versie hoort zijn gegevens nog te vinden, en een
# lege site is een schrik die je niemand wilt aandoen om een mapnaam. Verhuizen
# kan altijd nog met de hand, als de weg terug niet meer nodig is.
DATA_DIR=/var/lib/meshmanager
if [ ! -d "$DATA_DIR" ] && [ -d "$OLD_DATA_DIR" ]; then
  DATA_DIR="$OLD_DATA_DIR"
  echo "== Bestaande datamap gevonden: $DATA_DIR (blijft in gebruik) =="
fi

echo "== Pakketten =="
apt-get update -qq
apt-get install -y -qq python3 python3-venv rsync

echo "== Gebruiker en mappen =="
id -u "$USER_NAME" &>/dev/null || \
  useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$APP_DIR" "$DATA_DIR"

echo "== Code kopiëren =="
rsync -a --delete "$REPO_DIR/server/" "$APP_DIR/server/"

echo "== Python-omgeving =="
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/server/requirements.txt"

chown -R "$USER_NAME:$USER_NAME" "$DATA_DIR"
chown -R root:root "$APP_DIR"

# De oude unit eerst weg. Zonder dit draaien er na de update twee: de oude
# houdt poort 8080 vast, de nieuwe start niet, en het enige spoor daarvan is
# een regel in het journal die niemand leest omdat de site het "nog doet".
if systemctl list-unit-files | grep -q "^${OLD_SERVICE}\.service"; then
  echo "== Oude unit ${OLD_SERVICE} stoppen en uitschakelen =="
  systemctl disable --now "$OLD_SERVICE" || true
  rm -f "/etc/systemd/system/${OLD_SERVICE}.service"
fi

echo "== Systemd =="
# De datamap en de gebruiker worden in de unit ingevuld: de map kan hierboven
# twee waarden hebben en systemd kent geen voorwaarden.
sed "s|__DATA_DIR__|$DATA_DIR|g; s|__USER__|$USER_NAME|g" \
  "$REPO_DIR/deploy/meshmanager.service" > "/etc/systemd/system/${SERVICE}.service"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

sleep 2
systemctl --no-pager --lines 5 status "$SERVICE" || true
echo
echo "Klaar. Site draait op poort 8080, met de gegevens uit $DATA_DIR."
if [ -d "$OLD_APP_DIR" ]; then
  echo "De oude codemap $OLD_APP_DIR staat er nog; die mag weg zodra je zeker bent."
fi
echo "Eerste start? Admin-wachtwoord staat in: journalctl -u $SERVICE | grep Wachtwoord"
