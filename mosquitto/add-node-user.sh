#!/bin/bash
# Maakt een MQTT-account voor één node en beperkt dat account via de ACL tot de
# eigen topic-prefix. Zolang alle nodes hetzelfde account delen, kan elk van hen
# onder eender welk topic publiceren; pas met een eigen account per node dwingt
# de broker af dat het topic klopt.
#
# Gebruik: ./mosquitto/add-node-user.sh <node_pubkey_prefix> [wachtwoord]
# Zonder wachtwoord wordt er een willekeurig wachtwoord gegenereerd en getoond.
set -euo pipefail
cd "$(dirname "$0")/.."

NODE="$(printf '%s' "${1:-}" | tr 'A-Z' 'a-z')"
if ! printf '%s' "$NODE" | grep -Eq '^[0-9a-f]{6,32}$'; then
  echo "Gebruik: $0 <node_pubkey_prefix> [wachtwoord]" >&2
  echo "De prefix is hexadecimaal, 6 tot 32 tekens — precies zoals in het topic." >&2
  exit 1
fi

[ -f mosquitto/passwd ] || { echo "Draai eerst ./mosquitto/init-passwd.sh"; exit 1; }
[ -f mosquitto/acl ] || { echo "Draai eerst ./mosquitto/init-passwd.sh"; exit 1; }

USER="node-$NODE"
if grep -q "^${USER}:" mosquitto/passwd 2>/dev/null; then
  echo "Account '$USER' bestaat al. Verwijder het eerst uit mosquitto/passwd" >&2
  echo "als je een nieuw wachtwoord wil zetten." >&2
  exit 1
fi

PASS="${2:-}"
if [ -z "$PASS" ]; then
  # tr haalt de tekens weg die in een URL of configveld moeten worden ontsnapt.
  PASS="$(head -c 24 /dev/urandom | base64 | tr -d '\n/+=' | cut -c1-24)"
  GENERATED=1
fi

# Zonder -c, anders wordt het bestaande passwd-bestand overschreven.
docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 \
  mosquitto_passwd -b /m/passwd "$USER" "$PASS"

cat >> mosquitto/acl <<EOF

user $USER
topic write meshcore/$NODE/stats
topic write meshcore/$NODE/rx
EOF

# mosquitto_passwd draait als root en zet de rechten terug; de broker draait als
# uid 1883 en weigert te starten als hij zijn eigen bestanden niet kan lezen.
docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 \
  sh -c 'chown 1883:1883 /m/passwd /m/acl && chmod 0400 /m/passwd /m/acl'

echo
echo "Account aangemaakt:"
echo "  gebruiker : $USER"
echo "  wachtwoord: $PASS"
if [ -n "${GENERATED:-}" ]; then
  echo "  (willekeurig gegenereerd — noteer het nu, het wordt niet bewaard)"
fi
echo
echo "Zet deze gegevens op de beheerpagina van de node en herstart de broker:"
echo "  docker compose restart mosquitto"
