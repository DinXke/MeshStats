#!/bin/sh
# Back-up van MeshManager: de SQLite-databank en een VictoriaMetrics-snapshot.
#
# Wat dit script doet, in volgorde:
#   1. een CONSISTENTE kopie van de SQLite-databank, via de backup-API van
#      sqlite3 in de app-container -- nooit een cp van het levende bestand,
#      want dat kopieert midden in een transactie en de WAL blijft achter;
#   2. een snapshot van VictoriaMetrics (/snapshot/create), waarna de
#      snapshotmap uit de container gekopieerd en ingepakt wordt en het
#      snapshot server-side weer opgeruimd;
#   3. beide naar $BACKUP_DIR met een datumstempel in de naam;
#   4. per soort blijven er hoogstens $KEEP staan, oudste eerst weg.
#
# Half-falen is een uitkomst en geen crash: draait VictoriaMetrics even niet,
# dan gaat het SQLite-deel gewoon door en zegt de exitcode wat er miste.
#
# Exitcodes:
#   0  beide delen gelukt
#   1  het SQLite-deel is mislukt (het VM-deel wordt dan niet meer geprobeerd:
#      zonder de databank is de back-up van de historiek een halve belofte)
#   2  SQLite gelukt, het VictoriaMetrics-deel niet (VM niet bereikbaar, of
#      het kopiëren/inpakken van de snapshot mislukte)
#
# Cron installeert dit script NIET zelf; de cronregel staat in
# docs/nl/backup.md (en docs/backup.md), samen met de eerlijke noot dat
# offsite kopiëren de stap van de beheerder blijft.

set -u

BACKUP_DIR="${BACKUP_DIR:-/opt/meshstats/backups}"
APP_CONTAINER="${APP_CONTAINER:-meshmanager}"
VM_CONTAINER="${VM_CONTAINER:-meshmanager-tsdb}"
# Vanuit de app-container gezien, dus de compose-servicenaam en niet localhost:
# VictoriaMetrics publiceert met opzet geen poort naar de host.
VM_URL="${VM_URL:-http://victoria:8428}"
KEEP="${KEEP:-7}"

STAMP="$(date +%Y%m%d-%H%M%S)"
# Tijdelijke naam ín het datavolume, zodat docker cp hem kan pakken. Met een
# punt ervoor: mocht het script halverwege sneuvelen, dan ligt er geen bestand
# dat op een echte databank lijkt.
TMP_IN_CONTAINER="/data/.backup-onderweg.sqlite3"

mkdir -p "$BACKUP_DIR" || { echo "FOUT: kan $BACKUP_DIR niet aanmaken" >&2; exit 1; }

echo "[backup] $STAMP -> $BACKUP_DIR"

# --- 1. SQLite, consistent via de backup-API -----------------------------------
#
# In de app-container, met de Python die daar toch al staat: het pad van de
# databank komt uit app.config (die kent ook de oude naam mcs.sqlite3), en
# sqlite3.Connection.backup kopieert pagina voor pagina onder het slot van de
# databank zelf -- de kopie is een geldig bestand, ook terwijl de site schrijft.
sqlite_backup() {
    docker exec -i "$APP_CONTAINER" python - "$TMP_IN_CONTAINER" <<'PY'
import sqlite3
import sys

from app import config

doel = sys.argv[1]
bron = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
kopie = sqlite3.connect(doel)
with kopie:
    bron.backup(kopie)
kopie.close()
bron.close()
print(f"kopie van {config.DB_PATH}")
PY
}

if ! sqlite_backup; then
    echo "FOUT: de SQLite-back-up is mislukt (draait de container '$APP_CONTAINER'?)" >&2
    exit 1
fi
SQLITE_UIT="$BACKUP_DIR/meshmanager-$STAMP.sqlite3"
if ! docker cp "$APP_CONTAINER:$TMP_IN_CONTAINER" "$SQLITE_UIT"; then
    echo "FOUT: kon de databankkopie niet uit de container halen" >&2
    docker exec "$APP_CONTAINER" rm -f "$TMP_IN_CONTAINER" >/dev/null 2>&1
    exit 1
fi
docker exec "$APP_CONTAINER" rm -f "$TMP_IN_CONTAINER" >/dev/null 2>&1
gzip -f "$SQLITE_UIT" || { echo "FOUT: gzip mislukte" >&2; exit 1; }
echo "[backup] sqlite: $(basename "$SQLITE_UIT").gz"

# --- 2. VictoriaMetrics-snapshot -------------------------------------------------
#
# Het snapshot wordt vanuit de APP-container aangevraagd (die zit op hetzelfde
# compose-netwerk; het VM-image is scratch en heeft zelf geen shell), en de
# snapshotmap wordt daarna met docker cp uit de VM-container gehaald -- dat
# werkt ook op een container zonder shell. Een snapshot is bij VictoriaMetrics
# een map met hardlinks: goedkoop te maken, en pas een echte kopie zodra tar
# hem inpakt.
VM_MIST=0

vm_snapshot_create() {
    docker exec -i "$APP_CONTAINER" python - "$VM_URL" <<'PY'
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1] + "/snapshot/create", timeout=30) as resp:
        data = json.load(resp)
except Exception as fout:  # noqa: BLE001 - de exitcode is hier de boodschap
    print(f"FOUT: {fout}", file=sys.stderr)
    sys.exit(1)
if data.get("status") != "ok" or not data.get("snapshot"):
    print(f"FOUT: onverwacht antwoord: {data}", file=sys.stderr)
    sys.exit(1)
print(data["snapshot"])
PY
}

vm_snapshot_delete() {
    docker exec -i "$APP_CONTAINER" python - "$VM_URL" "$1" <<'PY'
import sys
import urllib.parse
import urllib.request

url = (sys.argv[1] + "/snapshot/delete?snapshot="
       + urllib.parse.quote(sys.argv[2]))
try:
    urllib.request.urlopen(url, timeout=30).read()
except Exception as fout:  # noqa: BLE001
    print(f"waarschuwing: snapshot niet opgeruimd: {fout}", file=sys.stderr)
    sys.exit(1)
PY
}

SNAP="$(vm_snapshot_create)" || SNAP=""
if [ -z "$SNAP" ]; then
    echo "waarschuwing: geen VictoriaMetrics-snapshot (draait VM?); het SQLite-deel staat er wel" >&2
    VM_MIST=1
else
    echo "[backup] vm-snapshot: $SNAP"
    WERK="$(mktemp -d)" || WERK=""
    if [ -z "$WERK" ]; then
        VM_MIST=1
    elif ! docker cp "$VM_CONTAINER:/victoria-metrics-data/snapshots/$SNAP" "$WERK/"; then
        echo "waarschuwing: kon de snapshotmap niet uit '$VM_CONTAINER' kopiëren" >&2
        VM_MIST=1
    elif ! tar -czf "$BACKUP_DIR/victoria-$STAMP.tar.gz" -C "$WERK" "$SNAP"; then
        echo "waarschuwing: inpakken van de snapshot mislukte" >&2
        rm -f "$BACKUP_DIR/victoria-$STAMP.tar.gz"
        VM_MIST=1
    else
        echo "[backup] vm: victoria-$STAMP.tar.gz"
    fi
    [ -n "$WERK" ] && rm -rf "$WERK"
    # Server-side opruimen, ook als het kopiëren mislukte: een snapshot dat
    # blijft staan houdt via zijn hardlinks oude datablokken vast en laat de
    # schijf van VM stil vollopen.
    vm_snapshot_delete "$SNAP" || true
fi

# --- 3. hoogstens $KEEP per soort -------------------------------------------------
#
# Per soort en niet over alles samen, zodat een week zonder draaiende VM niet
# stilletjes de laatste goede VM-back-ups wegdrukt met alleen-sqlite-dagen.
rotate() {
    ls -1t "$BACKUP_DIR"/$1 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r OUD; do
        rm -f -- "$OUD"
        echo "[backup] opgeruimd: $(basename "$OUD")"
    done
}
rotate 'meshmanager-*.sqlite3.gz'
rotate 'victoria-*.tar.gz'

if [ "$VM_MIST" -ne 0 ]; then
    echo "[backup] klaar, MAAR zonder VictoriaMetrics-deel (exit 2)"
    exit 2
fi
echo "[backup] klaar"
exit 0
