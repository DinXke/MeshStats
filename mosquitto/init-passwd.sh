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
echo "mosquitto/passwd aangemaakt voor gebruiker '$MCS_MQTT_USER'."
echo "Gebruik dezelfde gegevens op je node (beheerpagina van de node)."
