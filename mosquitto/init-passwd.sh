#!/bin/bash
# Maakt het MQTT-wachtwoordbestand en de ACL aan op basis van .env.
# Gebruik: ./mosquitto/init-passwd.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Maak eerst .env aan (cp .env.example .env)"; exit 1; }
# shellcheck disable=SC1091
source .env
: "${MCS_MQTT_USER:?ontbreekt in .env}" "${MCS_MQTT_PASS:?ontbreekt in .env}"

# Een vorige run laat de bestanden als alleen-lezen achter, en 'mosquitto_passwd -c'
# weigert dan te schrijven. Eerst opruimen dus, in een container: op de host zijn
# ze eigendom van uid 1883 en zonder root niet te verwijderen.
docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 \
  sh -c 'rm -f /m/passwd /m/acl'

docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 \
  mosquitto_passwd -c -b /m/passwd "$MCS_MQTT_USER" "$MCS_MQTT_PASS"

# De ACL wordt hier meegeschreven omdat mosquitto.conf ernaar verwijst: zonder
# het bestand start de broker niet. Het gedeelde account krijgt voorlopig ook
# schrijfrechten, want vandaag publiceren de nodes ermee — pas als elke node via
# add-node-user.sh een eigen account heeft, mag die regel weg. Zolang ze het
# account delen, kan elke node onder eender welk topic publiceren.
cat > mosquitto/acl <<EOF
# Gegenereerd door init-passwd.sh. Node-accounts worden hieronder toegevoegd
# door add-node-user.sh; zie acl.example voor uitleg.
#
# Geen topic-regels vóór het eerste 'user'-blok: die zouden voor alle clients
# gelden en de rest van dit bestand betekenisloos maken.

user $MCS_MQTT_USER
topic read meshcore/#
# Verwijder de volgende regel zodra elke node zijn eigen account heeft.
topic write meshcore/#
EOF

# mosquitto_passwd draait als root en laat een bestand achter dat alleen root
# mag lezen. De broker zelf draait als gebruiker 'mosquitto' (uid 1883) en
# weigert te starten als hij er niet bij kan.
docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 \
  sh -c 'chown 1883:1883 /m/passwd /m/acl && chmod 0400 /m/passwd /m/acl'

echo "mosquitto/passwd en mosquitto/acl aangemaakt voor gebruiker '$MCS_MQTT_USER'."
echo "Gebruik dezelfde gegevens op je node (beheerpagina van de node)."
echo
echo "LET OP: dit overschrijft beide bestanden, dus ook node-accounts die je"
echo "eerder met add-node-user.sh hebt aangemaakt."
