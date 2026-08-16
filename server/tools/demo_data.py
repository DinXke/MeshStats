"""Een demo-instantie met VERZONNEN data, voor de schermafbeeldingen in ``docs/``.

Draai hem vanuit ``server/``::

    python tools/demo_data.py                 # vult en start op http://127.0.0.1:8099
    python tools/demo_data.py --port 8100
    python tools/demo_data.py --seed-only     # alleen vullen, niet starten

Waarom dit bestand bestaat
--------------------------
De documentatie toont schermafbeeldingen van de beheerpagina's, en die pagina's
gaan over apparaten van mensen. Een afbeelding van de échte installatie zou
precies uitlekken wat dit project elders zorgvuldig beschermt: nodenamen van
derden, hun posities, hun sleutelprefixen, het IP-adres van een draaiende node
en de beheerpagina van een server die iemand beheert. De repository is publiek.

Dus komen álle afbeeldingen in ``docs/images/`` van dit script. Het zet een
wegwerpdatabase op met verzonnen nodes en start de site ertegen. Er raakt geen
enkele echte waarde in beeld, omdat er geen enkele echte waarde in dit bestand
staat.

Hoe de verzonnen gegevens gekozen zijn
--------------------------------------
Niet "willekeurig genoeg", maar aantoonbaar onbestaand -- dat is een sterkere
eigenschap dan onherkenbaar, en het is na te kijken zonder de echte installatie
erbij te halen:

**Namen** beginnen allemaal met ``Voorbeeld-``. Een lezer die zich afvraagt of
een node echt is, hoeft alleen naar het eerste woord te kijken.

**Sleutelprefixen** zijn herhalende bytes (``aa00aa00aa00``, ``bb11bb11bb11``).
Een echte MeshCore-sleutel is de eerste helft van een Ed25519-publieke sleutel
en ziet er nooit zo uit. Ze zijn ook onderling niet te verwarren.

**IP-adressen** komen uit ``192.0.2.0/24``. Dat is TEST-NET-1 uit RFC 5737, door
de IETF gereserveerd voor documentatie: het is niet routeerbaar en er kan per
definitie geen apparaat achter zitten.

**Posities** staan er niet in. De beheerpagina's tonen er geen, en wat er niet
staat kan ook niet lekken -- dus is de coördinatenvraag hier helemaal geen
vraag. Wie ooit een kaartafbeelding nodig heeft, moet die keuze bewust maken en
hier verzonnen coördinaten toevoegen.

Wat er niet echt is en toch nagebootst wordt
--------------------------------------------
Twee dingen worden voorgewend, omdat ze anders elke knop op elke afbeelding uit
zouden zetten en de afbeeldingen dan niets meer laten zien:

- ``mqtt_ingest.can_publish()`` geeft True. Er is geen broker; de site zou
  anders bij elke node melden dat er nu geen weg is.
- ``firmware._cache`` wordt gevuld met een verzonnen releaselijst in plaats van
  bij GitHub opgehaald. Zo doet dit script geen enkel netwerkverzoek en ziet de
  firmwarepagina er hetzelfde uit met of zonder internet.

Beide zijn hier expliciet en staan nergens in de app zelf.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Moet vóór de eerste app-import: ``app.config`` maakt bij het importeren al de
# datamap aan en schrijft er een geheime sleutel in. Zelfde reden als in
# ``tests/conftest.py``, en met dezelfde variabele.
_DATA_DIR = os.environ.get("MM_DATA_DIR") or tempfile.mkdtemp(prefix="meshmanager-demo-")
os.environ["MM_DATA_DIR"] = _DATA_DIR

# De firmwarepagina zet elke upgradeknop uit zonder inloggegevens voor de nodes,
# en meldt bovenaan dat ze ontbreken. Verzonnen waarden, want er wordt niets mee
# ingelogd: dit script raakt geen netwerk aan.
os.environ.setdefault("MM_FW_NODE_USER", "voorbeeld")
os.environ.setdefault("MM_FW_NODE_PASS", "voorbeeld")
# Zonder repository meldt de pagina bovenaan dat ze niet weet waar de releases
# staan. De lijst zelf komt hieronder uit de cache en niet van GitHub.
os.environ.setdefault("MM_GITHUB_REPO", "voorbeeld/meshmanager-demo")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, firmware, mqtt_ingest  # noqa: E402  (na de omgevingsvariabelen)

# --- de verzonnen nodes -------------------------------------------------------
#
# Vijf nodes, gekozen zodat elk beheerniveau er staat én zodat de gevallen die
# de documentatie uitlegt allemaal op één afbeelding te zien zijn. De volgorde
# van ``sort_order`` bepaalt de volgorde binnen een groep op de pagina.
#
#   Voorbeeld-Thuisnode   full managed, IP-pad, kritiek -> upgrade mogelijk
#   Voorbeeld-Zendmast    full managed, IP-pad          -> upgrade niet teruggekomen
#   Voorbeeld-Dakrepeater semi-managed via de thuisnode -> schrijven over LoRa,
#                         want de thuisnode draait 1.10.0 en niet 2.4.0: blokkade
#                         'relay_old_fw', met de zin die uitlegt dat het de
#                         MONITOR is die nieuwe firmware nodig heeft
#   Voorbeeld-Buurnode    unmanaged, alleen in het verkeer -> blokkade 'no_host'
#   Voorbeeld-Veldpost    unmanaged, doorgestuurd door een onbekende node
#
# De dakrepeater is met opzet de node zonder eigen firmware: dat is het geval
# waar dit project omheen gebouwd is, en een documentatie die alleen de makkelijke
# node toont legt precies het verkeerde uit.

NU = db.utcnow()

NODES = [
    {
        "slug": "voorbeeld-thuisnode", "pubkey_prefix": "bb11bb11bb11",
        "name": "Voorbeeld-Thuisnode", "is_public": 1, "sort_order": 1,
        "source_prefix": "bb11bb11bb11", "fw": "v1.16.0",
        "fw_meshmanager": "1.10.0", "topic_prefix": "meshmanager",
        "ota_host": "http://192.0.2.11", "pio_env": "heltec_v3", "is_critical": 1,
    },
    {
        "slug": "voorbeeld-zendmast", "pubkey_prefix": "cc22cc22cc22",
        "name": "Voorbeeld-Zendmast", "is_public": 1, "sort_order": 2,
        "source_prefix": "cc22cc22cc22", "fw": "v1.16.0",
        # Wel een IP-pad, want deze node draagt hieronder de opdracht die niet
        # terugkwam -- en zo'n opdracht kan alleen bestaan op een node waar een
        # image naartoe kon.
        "fw_meshmanager": "1.10.0", "topic_prefix": "meshmanager",
        "ota_host": "http://192.0.2.12", "pio_env": "heltec_v3", "is_critical": 0,
    },
    {
        "slug": "voorbeeld-dakrepeater", "pubkey_prefix": "aa00aa00aa00",
        "name": "Voorbeeld-Dakrepeater", "is_public": 1, "sort_order": 3,
        # Publiceert zelf niet: zijn cijfers komen binnen via de thuisnode.
        "source_prefix": "bb11bb11bb11", "fw": "v1.16.0",
        "fw_meshmanager": None, "topic_prefix": "meshmanager",
        "ota_host": "", "pio_env": "", "is_critical": 0,
    },
    {
        "slug": "voorbeeld-buurnode", "pubkey_prefix": "dd33dd33dd33",
        "name": "Voorbeeld-Buurnode", "is_public": 1, "sort_order": 4,
        "source_prefix": "dd33dd33dd33", "fw": "v1.15.1",
        "fw_meshmanager": None, "topic_prefix": "meshmanager",
        "ota_host": "", "pio_env": "", "is_critical": 0,
    },
    {
        "slug": "voorbeeld-veldpost", "pubkey_prefix": "ee44ee44ee44",
        "name": "Voorbeeld-Veldpost", "is_public": 0, "sort_order": 5,
        # Doorgestuurd door een node die hier zelf geen repeater is: de site
        # kent zijn firmware niet en weigert daarom te gokken.
        "source_prefix": "ff99ff99ff99", "fw": None,
        "fw_meshmanager": None, "topic_prefix": "meshmanager",
        "ota_host": "", "pio_env": "", "is_critical": 0,
    },
]

# Wat een sweep over LoRa van de dakrepeater teruggaf. Waarden die bij een
# stock-MeshCore-repeater horen, met één parameter zonder antwoord: dat is wat
# een echte sweep oplevert en het is precies het geval dat de documentatie
# uitlegt ("(geen antwoord)" in plaats van een oude waarde die vers lijkt).
CLI_DAKREPEATER = {
    "name": "Voorbeeld-Dakrepeater",
    "role": "repeater",
    "radio": "869.525,250,11,5",
    "freq": "869.525",
    "tx": "22",
    "af": "1.0",
    "repeat": "on",
    "advert.interval": "240",
    "flood.advert.interval": "12",
    "flood.max": "3",
    "flood.max.unscoped": "2",
    "allow.read.only": "off",
    "rxdelay": "900",
    "txdelay": "0",
    "cmd:region": None,          # geen antwoord binnengekomen
}

# Een verzonnen releaselijst. Vervangt het GitHub-antwoord zodat dit script
# offline draait en de firmwarepagina er altijd hetzelfde uitziet.
RELEASES = [
    {
        "tag": "fw-v1.10.0", "version": "1.10.0", "name": "fw-v1.10.0",
        "published": "2026-07-14 09:12:03", "prerelease": False,
        "notes": "Voorbeeldrelease. 'time <epoch>' op het cmd-topic, en een\n"
                 "klokronde langs de gemonitorde repeaters.",
        "images": {"heltec_v3": "https://example.invalid/fw-v1.10.0/heltec_v3.bin"},
        "envs": ["heltec_v3"],
    },
    {
        "tag": "fw-v1.9.0", "version": "1.9.0", "name": "fw-v1.9.0",
        "published": "2026-05-02 17:40:55", "prerelease": False,
        "notes": "Voorbeeldrelease. Een monitor kan 'settings <sleutel>' aannemen\n"
                 "en die sweep over LoRa doorgeven.",
        "images": {"heltec_v3": "https://example.invalid/fw-v1.9.0/heltec_v3.bin"},
        "envs": ["heltec_v3"],
    },
]

# Een upgrade die geschreven is en waarna de node niet terugkwam. Dit is de
# toestand die de documentatie het uitvoerigst behandelt en die je op een
# gezonde installatie nooit te zien krijgt -- vandaar dat hij hier ingezet
# wordt in plaats van afgewacht.
JOB_NIET_TERUGGEKOMEN = {
    "name": "Voorbeeld-Zendmast", "tag": "fw-v1.10.0", "version": "1.10.0",
    "from": "1.9.0", "env": "heltec_v3", "state": "niet_teruggekomen",
    "msg": "Image geschreven en gecontroleerd; de node antwoordde na de herstart "
           "niet meer op zijn beheeradres.",
    "step": "herstart", "started": "2026-08-16T09:03:11Z",
    "ended": "2026-08-16T09:07:48Z", "bytes": 1338464, "downgrade": False,
}


def seed() -> None:
    """Vul de wegwerpdatabase. Idempotent: leegt eerst wat hij zelf zet."""
    db.get_conn()

    # Een vast wachtwoord, want de afbeeldingen worden achter de login gemaakt
    # en een wachtwoord dat elke start verandert maakt dat onnodig omslachtig.
    # Dit account bestaat alleen in een wegwerpmap die na afloop weg mag.
    from app import auth
    db.execute("DELETE FROM admins")
    db.execute("INSERT INTO admins(username, pw_hash) VALUES(?,?)",
               ("admin", auth.hash_password("demo-wachtwoord")))

    db.execute("DELETE FROM repeater_cli")
    db.execute("DELETE FROM repeaters")
    for node in NODES:
        db.execute(
            "INSERT INTO repeaters(slug, pubkey_prefix, name, is_public, sort_order,"
            " last_seen, created_at, source_prefix, source_seen, fw, fw_meshmanager,"
            " topic_prefix, ota_host, pio_env, is_critical)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (node["slug"], node["pubkey_prefix"], node["name"], node["is_public"],
             node["sort_order"], NU, NU, node["source_prefix"] or None, NU,
             node["fw"], node["fw_meshmanager"], node["topic_prefix"],
             node["ota_host"] or None, node["pio_env"] or None, node["is_critical"]),
        )

    dak = db.qone("SELECT id FROM repeaters WHERE pubkey_prefix='aa00aa00aa00'")
    for param, value in CLI_DAKREPEATER.items():
        db.execute("INSERT INTO repeater_cli(repeater_id, param, value, updated)"
                   " VALUES(?,?,?,?)", (dak["id"], param, value, NU))

    mast = db.qone("SELECT id FROM repeaters WHERE pubkey_prefix='cc22cc22cc22'")
    db.set_setting(firmware.JOBS_KEY,
                   json.dumps({str(mast["id"]): {**JOB_NIET_TERUGGEKOMEN,
                                                 "rep": mast["id"]}}))

    print(f"[demo] Databank gevuld: {len(NODES)} verzonnen nodes in {_DATA_DIR}")


def pretend() -> None:
    """Broker en releaselijst voorwenden. Zie de moduletekst voor waarom."""
    mqtt_ingest.can_publish = lambda: True
    firmware._cache.update(at=time.time(), items=RELEASES, error="",
                           slug=firmware.repo_slug())


def open_admin() -> None:
    """De login van de beheerpagina's overslaan. Alleen met ``--no-login``.

    Bestaat voor het maken van de schermafbeeldingen: een headless browser die
    eerst een formulier moet invullen is een stap die bij elke hermaak opnieuw
    stuk kan, en het wachtwoord van dit wegwerpaccount hoort nergens in een
    commandoregel of een script thuis. De patch zit hier en niet in de app, dus
    er is geen schakelaar in de site zelf die dit ooit per ongeluk kan doen.
    """
    from app import routes_admin
    routes_admin.require_login = lambda request: "admin"
    print("[demo] LET OP: /admin staat open zonder login (--no-login).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--no-login", action="store_true",
                    help="beheerpagina's zonder inloggen tonen (voor schermafbeeldingen)")
    args = ap.parse_args()

    seed()
    if args.seed_only:
        return

    pretend()
    if args.no_login:
        open_admin()
    # De app pas hier importeren: zijn startup-hook maakt anders een tweede
    # admin-account aan voordat seed() het zijne gezet heeft.
    import uvicorn
    from app.main import app

    print(f"[demo] Inloggen op http://{args.host}:{args.port}/admin/login "
          f"met admin / demo-wachtwoord")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
