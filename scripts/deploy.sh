#!/bin/sh
# Uitrol van MeshManager OP EEN TAG, met een healthcheck-poort en automatische
# rollback. Vervangt de blinde autoupdate (deploy/autoupdate.sh) die elke vijf
# minuten main uitrolde -- die rolde ELKE commit uit en keek daarna niet of de
# site nog leefde, en heeft zo al twee keer productie omgelegd.
#
# Gebruik:
#   sudo sh scripts/deploy.sh              # nieuwste git-tag v* uitrollen
#   sudo sh scripts/deploy.sh v1.4.0       # een bepaalde tag (of commit/ref)
#
# Wat dit script doet, in volgorde:
#   1. de nieuwste tag v* opzoeken (of de meegegeven ref nemen) en die uitchecken;
#   2. het image bouwen naar een EIGEN tag (meshmanager:<gittag>), niet meteen
#      naar :latest -- faalt de build, dan is er niets gewisseld en draait de
#      oude container ongestoord door;
#   3. de nu draaiende image bewaren als meshmanager:previous (de weg terug);
#   4. :latest naar de nieuwe image wijzen en de container herstarten;
#   5. de POORT: wachten tot de container 'healthy' is EN een functionele query
#      bewijst dat de databank de migraties overleefd heeft en de kern-tabellen
#      leven. Zakt de poort binnen de deadline niet dicht, dan automatisch
#      terugrollen naar meshmanager:previous en met een niet-nul exitcode stoppen.
#
# Dit is de dual-slot-gedachte van de dak-repeater, maar voor de server: een
# nieuwe versie bewijst eerst dat ze leeft voor de oude wordt losgelaten, en er
# is altijd een vorige image om op terug te vallen.
#
# NIETS interactiefs, want dit hoort ook vanuit een cron/een timer te kunnen
# draaien. Alle uitvoer gaat naar het scherm EN naar een logbestand met datum
# (zie LOG_DIR onder), zodat de ene ronde die faalde terug te vinden is.
#
# Exitcodes (de exitcode is de boodschap, net als bij scripts/backup.sh):
#   0  uitgerold en de poort ging groen
#   1  gebruiksfout: geen tag gevonden, geen git-repo, docker ontbreekt
#   2  de build mislukte -- er is NIETS gewisseld, de oude container draait door
#   3  de poort ging niet dicht -> teruggerold naar meshmanager:previous
#   4  de poort ging niet dicht EN de rollback mislukte ook -> met de hand erbij
#
# Bewust GEEN 'set -e': de rollback moet juist kunnen draaien nadat een commando
# faalde, en met -e zou het script dan al gestopt zijn voordat het terugrolt.
# Alle fouten worden hieronder met de hand afgevangen en krijgen hun eigen code.
set -u

# --- instellingen (alles te overrulen via de omgeving, zoals bij backup.sh) ----
APP_CONTAINER="${APP_CONTAINER:-meshmanager}"
IMAGE="${IMAGE:-meshmanager}"
# Waar 'docker build' zijn context vindt: de map met de Dockerfile van de app.
BUILD_CONTEXT_REL="${BUILD_CONTEXT_REL:-server}"
# Hoe lang de poort maximaal mag proberen dicht te gaan, en hoe vaak we kijken.
HEALTH_DEADLINE_S="${HEALTH_DEADLINE_S:-120}"
POLL_S="${POLL_S:-3}"
# Optioneel STRENGER maken: eis dat het nieuwste pakket jonger is dan zoveel
# seconden. LEEG (de standaard) = alleen informatief, geen reden om te rollen --
# zie de eerlijke noot bij de functionele query verderop over waarom dit niet
# aanstaat op een rustig mesh.
MAX_PACKET_AGE_S="${MM_DEPLOY_MAX_PACKET_AGE_S:-}"
LOG_DIR="${LOG_DIR:-/var/log}"

# --- plek bepalen en loggen ----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR" || { echo "FOUT: kan niet naar $REPO_DIR" >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
if mkdir -p "$LOG_DIR" 2>/dev/null && [ -w "$LOG_DIR" ]; then
    LOGFILE="$LOG_DIR/meshmanager-deploy-$STAMP.log"
else
    # Geen schrijfrecht op LOG_DIR (bijv. als gewone gebruiker getest): val terug
    # op de scripts-map, zodat er altijd een logbestand met datum overblijft.
    LOGFILE="$SCRIPT_DIR/deploy-$STAMP.log"
fi

log() {
    # Naar scherm EN logbestand, met een tijdstempel per regel.
    printf '%s %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOGFILE"
}
fail() {
    # $1 = exitcode, rest = reden. De reden staat er DUIDELIJK bij, want een
    # rollback zonder reden laat je de volgende keer weer in het duister.
    code="$1"; shift
    log "FOUT ($code): $*"
    exit "$code"
}

command -v docker >/dev/null 2>&1 || fail 1 "docker staat niet in PATH"
docker compose version >/dev/null 2>&1 || fail 1 "'docker compose' ontbreekt (v2 nodig)"

log "== MeshManager deploy $STAMP =="
log "repo=$REPO_DIR log=$LOGFILE"

# --- 1. de ref bepalen en uitchecken -------------------------------------------
#
# Zonder argument: de NIEUWSTE tag v*. --sort=-v:refname sorteert op versie en
# niet alfabetisch, zodat v1.10.0 na v1.9.0 komt en niet ervoor. Met een
# argument: neem precies dat (een tag, maar ook een commit of branch mag, zodat
# je met de hand een hotfix kunt uitrollen).
git rev-parse --git-dir >/dev/null 2>&1 || fail 1 "dit is geen git-repo"
# Tags binnenhalen; een gefaalde fetch (offline) is geen ramp -- dan werken we
# met wat er lokaal al staat, en dat staat in het log.
if ! git fetch --tags --quiet origin 2>>"$LOGFILE"; then
    log "waarschuwing: 'git fetch --tags' mislukte; ik werk met de lokale tags"
fi

if [ "$#" -ge 1 ] && [ -n "$1" ]; then
    REF="$1"
else
    REF="$(git tag -l 'v*' --sort=-v:refname | head -n 1)"
    [ -n "$REF" ] || fail 1 "geen enkele tag v* gevonden; maak er een (git tag v1.0.0) of geef een ref mee"
fi

git rev-parse --verify --quiet "$REF^{commit}" >/dev/null \
    || fail 1 "ref '$REF' bestaat niet"
TARGET_SHA="$(git rev-parse --short "$REF^{commit}")"
log "uitrollen: $REF ($TARGET_SHA)"

# --ff is hier niet aan de orde: we checken een tag uit, geen branch. Detached
# HEAD is precies goed -- de werkboom staat exact op de tag en 'docker build'
# bouwt die code. Faalt de checkout (lokale wijzigingen in de deploy-kloon), dan
# stoppen we luid: ongezien iets overschrijven is erger.
git checkout -q --detach "$REF" 2>>"$LOGFILE" \
    || fail 1 "kon $REF niet uitchecken (lokale wijzigingen in de kloon?)"

# Een docker-tag mag geen '/' of '~' bevatten; een branch- of ref-naam wel.
# Maak er een veilige imagetag van, met de korte sha erin zodat hij uniek blijft.
IMG_TAG="$(printf '%s' "$REF" | tr '/~:^ ' '-----')-$TARGET_SHA"

# --- 2. bouwen naar een EIGEN tag (nog niet wisselen) --------------------------
#
# Naar meshmanager:<gittag> en NIET meteen naar :latest. Zo raakt een mislukte
# build niets: 'docker build' verplaatst een tag pas als de build slaagt, en de
# draaiende container houdt zijn eigen image via de id vast, los van welke tag
# waarheen wijst. Faalt dit, dan is er niets gewisseld (exit 2).
log "bouwen: $IMAGE:$IMG_TAG (context ./$BUILD_CONTEXT_REL)"
if ! docker build -t "$IMAGE:$IMG_TAG" "$REPO_DIR/$BUILD_CONTEXT_REL" >>"$LOGFILE" 2>&1; then
    fail 2 "de build mislukte; de oude container draait ongestoord door (zie $LOGFILE)"
fi
log "build klaar"

# --- 3. de nu draaiende image bewaren als :previous (de weg terug) -------------
#
# Ná een geslaagde build en vlak vóór de wissel, zodat :previous exact de image
# is die op DIT moment draaide. De id (niet de tag) wordt vastgelegd: tags
# schuiven, een id niet, en dat is precies wat een rollback nodig heeft.
PREV_IMG="$(docker inspect -f '{{.Image}}' "$APP_CONTAINER" 2>/dev/null || true)"
HAVE_PREV=0
if [ -n "$PREV_IMG" ]; then
    if docker tag "$PREV_IMG" "$IMAGE:previous" 2>>"$LOGFILE"; then
        HAVE_PREV=1
        log "vorige image bewaard als $IMAGE:previous ($(printf '%s' "$PREV_IMG" | cut -c1-19))"
    else
        log "waarschuwing: kon $IMAGE:previous niet zetten; rollback wordt onmogelijk"
    fi
else
    # Eerste uitrol, of de container draaide niet: er is geen vorige om op terug
    # te vallen. Dat mag, maar de gate wordt dan een muur zonder vangnet -- staat
    # duidelijk in het log zodat niemand zich later op een rollback verlaat die
    # er nooit was.
    log "let op: geen draaiende container '$APP_CONTAINER' gevonden; geen :previous, dus geen rollback bij een gefaalde poort"
fi

# --- 4. :latest laten wijzen en de container herstarten ------------------------
if ! docker tag "$IMAGE:$IMG_TAG" "$IMAGE:latest" 2>>"$LOGFILE"; then
    fail 2 "kon $IMAGE:latest niet naar de nieuwe build zetten; niets gewisseld"
fi
log "wisselen naar de nieuwe image"
# --remove-orphans om dezelfde reden als in de oude autoupdate: na de hernoeming
# kan er nog een container onder een oude naam poort 8080 vasthouden. Zonder
# --build, want we hebben net zelf gebouwd en :latest wijst er al heen; 'up'
# gebruikt dan het bestaande image en bouwt niet opnieuw.
if ! docker compose up -d --remove-orphans >>"$LOGFILE" 2>&1; then
    log "waarschuwing: 'docker compose up' gaf een fout terug; ik ga toch de poort proberen"
fi

# --- 5. de poort: healthy EN een functionele query ----------------------------
#
# Twee sloten. Eerst wacht dit op State.Health == healthy (de HEALTHCHECK in de
# Dockerfile: HTTP 200 op /). Dat vangt een container die niet opstart of 500
# geeft -- en dus ook een migratie die bij het opstarten van de app crasht,
# want dan komt de HTTP-server nooit omhoog. Daarna een EIGEN query die niet van
# de startpagina afhangt: hij bewijst dat de kern-tabellen bestaan en te
# bevragen zijn. Een migratie die niet crasht maar de databank scheeftrekt
# (kolom kwijt, tabel verkeerd hernoemd) valt daar door.

wait_healthy() {
    # 0 zodra healthy; 1 als de deadline verstrijkt of de container omvalt.
    deadline=$(( $(date +%s) + HEALTH_DEADLINE_S ))
    last=""
    while [ "$(date +%s)" -lt "$deadline" ]; do
        running="$(docker inspect -f '{{.State.Running}}' "$APP_CONTAINER" 2>/dev/null || echo false)"
        status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$APP_CONTAINER" 2>/dev/null || echo missing)"
        [ "$status" != "$last" ] && { log "  container: running=$running health=$status"; last="$status"; }
        case "$status" in
            healthy) return 0 ;;
            none)
                # Geen healthcheck in het image (zou niet moeten, de Dockerfile
                # heeft er een): val terug op 'draait hij nog'. De functionele
                # query hieronder is dan het echte oordeel.
                [ "$running" = "true" ] && return 0 ;;
        esac
        # Een container die stopte of blijft herstarten gaat niet meer healthy
        # worden; niet de volle deadline afwachten.
        if [ "$running" != "true" ]; then
            sleep "$POLL_S"
            running="$(docker inspect -f '{{.State.Running}}' "$APP_CONTAINER" 2>/dev/null || echo false)"
            [ "$running" != "true" ] && { log "  container draait niet (meer)"; return 1; }
        fi
        sleep "$POLL_S"
    done
    return 1
}

functional_check() {
    # Draait IN de app-container, met de Python die daar al staat, precies zoals
    # backup.sh de databank read-only opent. Faalt dit, dan is de exitcode van de
    # python de reden. Read-only mode=ro is veilig naast de schrijvende app (WAL).
    docker exec -i \
        -e MM_DEPLOY_MAX_PACKET_AGE_S="${MAX_PACKET_AGE_S:-}" \
        "$APP_CONTAINER" python - <<'PY'
import os
import sqlite3
import sys
from datetime import datetime, timezone

from app import config

# De tabellen zonder welke de site geen site is. Een migratie die er een sloopt
# of scheeftrekt, valt hier door -- ook als de startpagina toevallig nog 200 geeft.
CORE = ["repeaters", "latest", "samples", "packets",
        "admins", "settings", "neighbors", "contacts"]

try:
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
except sqlite3.OperationalError as fout:
    print(f"FUNC-FAIL kan databank niet openen: {fout}", file=sys.stderr)
    sys.exit(1)

have = {r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
missing = [t for t in CORE if t not in have]
if missing:
    print("FUNC-FAIL kern-tabellen ontbreken: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)

# Niet alleen 'de tabel bestaat' maar 'de tabel is te bevragen': een kapotte
# migratie kan een tabel achterlaten waar een SELECT op struikelt.
for t in CORE:
    try:
        con.execute(f"SELECT count(*) FROM {t}").fetchone()
    except sqlite3.OperationalError as fout:
        print(f"FUNC-FAIL tabel '{t}' is niet te bevragen: {fout}", file=sys.stderr)
        sys.exit(1)

# De datastroom: hoeveel pakketten, en hoe oud is het nieuwste. Dit BEWIJST dat
# de pakkettenweg leeft en is het cijfer waaraan je een dode ingest zou zien.
n = con.execute("SELECT count(*) FROM packets").fetchone()[0]
row = con.execute("SELECT ts FROM packets ORDER BY ts DESC LIMIT 1").fetchone()
newest = row[0] if row else None
age = None
if newest:
    try:
        dt = datetime.strptime(newest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = int((datetime.now(timezone.utc) - dt).total_seconds())
    except ValueError:
        pass
print(f"FUNC-OK kern-tabellen={len(CORE)} pakketten={n} "
      f"nieuwste={newest or 'geen'} leeftijd_s={age if age is not None else 'n.v.t.'}")

# Alleen als de operator er STRENG in wil zijn (MM_DEPLOY_MAX_PACKET_AGE_S gezet):
# een te oud nieuwste pakket laat de poort dicht blijven.
#
# EERLIJKE NOOT waarom dit standaard UIT staat. Vlak na een herstart bewijst
# "nieuwste pakket is jong" niets over de nieuwe versie: de databank staat in een
# volume dat de herstart overleeft, dus die pakketten komen nog van de OUDE
# versie van vlak ervoor. Een echt dode ingest (denk aan de MQTT-storing die
# dertien minuten datastroom kostte: de site gaf 200 maar er kwam niets binnen)
# zou je pas zien als er NA de herstart nieuwe pakketten hadden moeten komen --
# en op een rustig mesh 's nachts komen die er soms even niet. Deze poort vangt
# daarom betrouwbaar migratie- en opstartschade; een stille ingest vangt hij
# alleen als je hem streng zet EN je mesh druk genoeg is. Zet 'm dus enkel op een
# server met continue instroom.
max_age = os.environ.get("MM_DEPLOY_MAX_PACKET_AGE_S", "").strip()
if max_age:
    try:
        drempel = int(max_age)
    except ValueError:
        print(f"waarschuwing: MM_DEPLOY_MAX_PACKET_AGE_S='{max_age}' is geen getal; overgeslagen",
              file=sys.stderr)
        drempel = 0
    if drempel > 0 and (age is None or age > drempel):
        print(f"FUNC-FAIL nieuwste pakket is {age}s oud (grens {drempel}s); ingest lijkt dood",
              file=sys.stderr)
        sys.exit(1)
PY
}

rollback() {
    # De weg terug: :latest weer naar :previous en opnieuw omhoog. Best-effort de
    # poort nog even bekijken, maar de rollback zelf is wat telt.
    if [ "$HAVE_PREV" -ne 1 ]; then
        fail 4 "de poort ging niet dicht EN er is geen $IMAGE:previous om op terug te rollen -- grijp met de hand in (zie $LOGFILE)"
    fi
    log "ROLLBACK: terug naar $IMAGE:previous"
    if ! docker tag "$IMAGE:previous" "$IMAGE:latest" 2>>"$LOGFILE"; then
        fail 4 "rollback mislukte: kon $IMAGE:latest niet terugzetten (zie $LOGFILE)"
    fi
    if ! docker compose up -d --remove-orphans >>"$LOGFILE" 2>&1; then
        fail 4 "rollback mislukte: 'docker compose up' op de oude image faalde (zie $LOGFILE)"
    fi
    if wait_healthy; then
        log "rollback klaar: de oude versie draait weer en is healthy"
    else
        log "waarschuwing: teruggerold, maar de oude versie werd niet healthy binnen de deadline -- kijk na"
    fi
    fail 3 "de poort ging niet dicht; teruggerold naar de vorige versie"
}

log "poort: wachten tot '$APP_CONTAINER' healthy is (max ${HEALTH_DEADLINE_S}s)"
if ! wait_healthy; then
    log "poort DICHT: container werd niet healthy binnen ${HEALTH_DEADLINE_S}s"
    rollback
fi
log "container is healthy; functionele query draaien"
if ! functional_check 2>>"$LOGFILE" | tee -a "$LOGFILE" | grep -q '^FUNC-OK'; then
    log "poort DICHT: de functionele query faalde (kern-tabellen of datastroom)"
    rollback
fi

log "== poort GROEN: $REF ($TARGET_SHA) draait =="
exit 0
