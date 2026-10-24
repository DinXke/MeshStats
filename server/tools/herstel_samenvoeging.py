"""Een samenvoeging terugdraaien met een momentopname van ervoor.

Waarom dit bestaat
------------------
``samenvoegen_dubbele_node.py`` verwijdert een rij, en die verwijdering neemt
met ``ON DELETE CASCADE`` alles mee wat eraan hing. Dat is onomkeerbaar binnen
de databank zelf. Het is wél omkeerbaar met een KOPIE van vlak ervoor, en dit
script is die weg terug -- zodat "eerst een kopie maken" geen vrome raad is
maar een knop.

Het verzint niets en het gokt nergens. De kopie is de waarheid over welke rijen
bij die node hoorden; dit script leest ze daar op hun primaire sleutel uit en
zet ze terug waar ze in de huidige databank ontbreken of verplaatst zijn:

* ``repeaters`` -- de rij zelf, met alle kolommen en hetzelfde ``id``, want
  daar hangt al het andere aan.
* ``latest``, ``neighbors``, ``samples``, ``repeater_cli``, ``channel_names``,
  ``repeater_filter`` -- alles wat de CASCADE heeft meegenomen.
* ``alerts`` -- staan nog in de databank maar wijzen nu naar de andere node;
  die gaan op hun ``id`` terug.
* ``packets`` -- hun ``observer`` is omgeschreven naar de sleutel van het doel;
  die gaan op hun ``id`` terug naar de sleutel die de kopie noemt.

Wat het NIET doet: nieuwere gegevens overschrijven. Een rij die inmiddels in de
huidige databank bestaat, blijft zoals hij is, en het script telt hoeveel het
daarom heeft overgeslagen. De tijdreeksen in VictoriaMetrics zijn nooit
aangeraakt door de samenvoeging en hoeven hier dus ook niets.

Draaien (droog, dan echt):

    docker compose exec -T meshmanager python tools/herstel_samenvoeging.py \
        --uit /tmp/kopie.sqlite3 --id 10
    docker compose exec -T meshmanager python tools/herstel_samenvoeging.py \
        --uit /tmp/kopie.sqlite3 --id 10 --doen

``--db`` wijst de DOELdatabank aan als dat niet de databank van de draaiende
site moet zijn -- test het herstel daar eerst op, met dezelfde reden als
waarom de samenvoeging die vlag heeft.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app import config, db   # noqa: E402  (na de sys.path-regel, met opzet)

# De tabellen die met de CASCADE verdwenen zijn, met de kolommen die een rij
# binnen de node uniek maken. ``samples`` staat erbij hoewel hij geen
# foreign key heeft: hij overleeft de CASCADE wél, en dan is terugzetten een
# lege operatie die netjes 0 meldt.
HANGEND = (
    ("latest", ("metric",)),
    ("samples", ("metric", "ts")),
    ("neighbors", ("prefix",)),
    ("repeater_cli", ("param",)),
    ("channel_names", ("channel",)),
    ("repeater_filter", ()),
)


def _rijen(conn: sqlite3.Connection, sql: str, params=()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, params).fetchall()


def _zet_terug(kopie, tabel: str, sleutels, rid: int, doen: bool) -> tuple[int, int]:
    """(teruggezet, overgeslagen) voor één tabel die aan de node hing."""
    bron = _rijen(kopie, f"SELECT * FROM {tabel} WHERE repeater_id=?", (rid,))
    terug = overgeslagen = 0
    for r in bron:
        waar = " AND ".join(["repeater_id=?"] + [f"{k}=?" for k in sleutels])
        params = [rid] + [r[k] for k in sleutels]
        if db.qone(f"SELECT 1 FROM {tabel} WHERE {waar}", tuple(params)):
            overgeslagen += 1
            continue
        kolommen = list(r.keys())
        if doen:
            db.execute(
                f"INSERT INTO {tabel}({','.join(kolommen)}) "
                f"VALUES({','.join('?' * len(kolommen))})",
                tuple(r[k] for k in kolommen))
        terug += 1
    return terug, overgeslagen


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--uit", required=True,
                   help="pad naar de momentopname van vóór de samenvoeging")
    p.add_argument("--id", required=True, type=int,
                   help="het id van de rij die teruggezet moet worden")
    p.add_argument("--doen", action="store_true",
                   help="schrijf de wijzigingen weg (standaard: alleen tonen)")
    p.add_argument("--db", default="",
                   help="pad naar de DOELdatabank; standaard die van de site")
    args = p.parse_args()

    if args.db:
        config.DB_PATH = Path(args.db)
        db._conn = None
    print(f"databank  : {config.DB_PATH}")
    print(f"kopie     : {args.uit}")

    kopie = sqlite3.connect(f"file:{args.uit}?mode=ro", uri=True)
    rij = _rijen(kopie, "SELECT * FROM repeaters WHERE id=?", (args.id,))
    if not rij:
        print(f"FOUT: de kopie kent geen repeater met id={args.id}")
        return 2
    rij = rij[0]
    print(f"terug te zetten: id={rij['id']} slug={rij['slug']} "
          f"key={rij['pubkey_prefix']} naam={rij['name']}")

    bestaat = db.qone("SELECT slug FROM repeaters WHERE id=?", (args.id,))
    if bestaat:
        print(f"repeaters : rij {args.id} bestaat al ({bestaat['slug']}) -- "
              "niet aangeraakt")
    else:
        kolommen = list(rij.keys())
        if args.doen:
            db.execute(f"INSERT INTO repeaters({','.join(kolommen)}) "
                       f"VALUES({','.join('?' * len(kolommen))})",
                       tuple(rij[k] for k in kolommen))
        print(f"repeaters : 1 rij teruggezet (id={args.id})")

    for tabel, sleutels in HANGEND:
        terug, over = _zet_terug(kopie, tabel, sleutels, args.id, args.doen)
        regel = f"{tabel:<16}: {terug} teruggezet"
        if over:
            regel += f", {over} overgeslagen (staat er al)"
        print(regel)

    # Alarmen en pakketten zijn niet verdwenen maar VERPLAATST. Ze gaan op hun
    # eigen id terug, en alleen als ze nu nog staan waar de samenvoeging ze
    # neergezet heeft -- is er daarna iets mee gebeurd, dan blijft dat staan.
    alarmen = _rijen(kopie, "SELECT id FROM alerts WHERE repeater_id=?", (args.id,))
    verzet = 0
    for a in alarmen:
        if args.doen:
            verzet += db.execute_rowcount(
                "UPDATE alerts SET repeater_id=? WHERE id=? AND repeater_id<>?",
                (args.id, a["id"], args.id))
        else:
            verzet += 1 if db.qone(
                "SELECT 1 FROM alerts WHERE id=? AND repeater_id<>?",
                (a["id"], args.id)) else 0
    print(f"alerts          : {verzet} van {len(alarmen)} teruggezet")

    sleutel = rij["pubkey_prefix"]
    pakketten = _rijen(kopie, "SELECT id FROM packets WHERE observer=?", (sleutel,))
    hernoemd = 0
    for pk in pakketten:
        if args.doen:
            hernoemd += db.execute_rowcount(
                "UPDATE packets SET observer=? WHERE id=? AND observer<>?",
                (sleutel, pk["id"], sleutel))
        else:
            hernoemd += 1 if db.qone(
                "SELECT 1 FROM packets WHERE id=? AND observer<>?",
                (pk["id"], sleutel)) else 0
    print(f"packets         : {hernoemd} van {len(pakketten)} terug op "
          f"waarnemer {sleutel}")

    kopie.close()
    if not args.doen:
        print("\nDROOG GEDRAAID -- er is niets gewijzigd. Draai opnieuw met "
              "--doen om dit echt terug te zetten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
