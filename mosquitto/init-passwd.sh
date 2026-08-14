#!/bin/bash
# Maakt het MQTT-wachtwoordbestand aan op basis van .env.
# Gebruik: ./mosquitto/init-passwd.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Maak eerst .env aan (cp .env.example .env)"; exit 1; }
# shellcheck disable=SC1091
source .env
: "${MCS_MQTT_USER:?ontbreekt in .env}" "${MCS_MQTT_PASS:?ontbreekt in .env}"

docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 \
  mosquitto_passwd -c -b /m/passwd "$MCS_MQTT_USER" "$MCS_MQTT_PASS"

# mosquitto_passwd draait als root en laat een bestand achter dat alleen root
# mag lezen. De broker zelf draait als gebruiker 'mosquitto' (uid 1883) en
# weigert te starten als hij er niet bij kan.
docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 \
  sh -c 'chown 1883:1883 /m/passwd && chmod 0400 /m/passwd'

echo "mosquitto/passwd aangemaakt voor gebruiker '$MCS_MQTT_USER'."
echo "Gebruik dezelfde gegevens op je node (beheerpagina van de node)."
