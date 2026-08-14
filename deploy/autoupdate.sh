#!/bin/bash
# Werkt de site bij zodra er nieuwe commits op main staan: git pull --ff-only,
# docker compose build, docker compose up -d. Bedoeld om elke vijf minuten te
# draaien via meshstats-autoupdate.timer; met de hand aanroepen kan ook.
# Gebruik: sudo bash deploy/autoupdate.sh   (vanuit de deploy-kloon)
#
# Polling en geen webhook, bewust: de server staat achter LAN/VPN zonder
# inkomende poort, dus GitHub kan hem niet bereiken. Naar buiten fetchen kan
# altijd, en een vertraging van hooguit vijf minuten is niet waard om er een
# tunnel of port-forward voor open te houden.
#
# Stil als er niets te doen is. Dit draait 288 keer per dag, en een journal
# waarin elke ronde "niets nieuws" meldt, verbergt precies de ene ronde die
# faalde. Alleen wie werk vindt (of op een fout stuit) schrijft output.
#
# Geen eigen lock tegen overlappende runs: de service is Type=oneshot, en
# systemd start een timer-unit niet opnieuw zolang de vorige activering nog
# loopt. Een flock hierbovenop zou hetzelfde nog eens doen, maar dan met een
# bestand dat kan blijven hangen.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Er wordt vergeleken met de laatst geslaagde deploy, niet met HEAD. Vergelijk
# je met HEAD, dan is een run die na de pull strandt (build-fout, register even
# onbereikbaar) bij de volgende ronde onzichtbaar -- HEAD staat dan immers al
# gelijk met origin/main -- en blijft de site op de oude image staan tot er
# toevallig een nieuwe commit komt. De marker staat in .git/ omdat die map van
# ons is en buiten het zicht van de werkboom blijft; geen .gitignore nodig.
# Keerzijde: een build die blijvend stuk is wordt elke ronde opnieuw
# geprobeerd. Dat is de bedoeling -- de fout staat dan elke vijf minuten in het
# journal in plaats van eenmalig weg te glijden.
MARK="$REPO_DIR/.git/autoupdate-deployed"

git fetch --quiet origin main
REMOTE="$(git rev-parse origin/main)"
[ "$REMOTE" = "$(cat "$MARK" 2>/dev/null || true)" ] && exit 0

echo "Bijwerken naar $(git rev-parse --short origin/main)"

# --ff-only: dit is een deploy-kloon, geen werkkopie. Lokale commits of een
# uiteengelopen main horen hier niet te bestaan, en als ze er toch zijn is
# luid stoppen beter dan ongezien een merge-commit op de server fabriceren.
git merge --ff-only --quiet origin/main

# Eerst bouwen, dan pas vervangen. Faalt de build, dan wordt 'up -d' nooit
# bereikt (set -e) en blijven de draaiende containers op de oude image staan;
# de fout komt in het journal en de marker blijft achter, dus de volgende
# ronde probeert het opnieuw.
docker compose build
docker compose up -d

echo "$REMOTE" > "$MARK"
echo "Klaar: site draait op $(git rev-parse --short HEAD)."
