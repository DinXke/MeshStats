"""Twee rijen die dezelfde fysieke node zijn, samenvoegen tot één.

Waarom dit bestaat
------------------
Een MeshCore-node is in deze databank zijn PUBLIEKE SLEUTEL. Dat is de juiste
keuze -- een naam is te veranderen en een IP-adres is van het netwerk -- maar
hij heeft één gat: raakt het bestandssysteem van een node beschadigd, dan maakt
MeshCore bij de volgende start een nieuw sleutelpaar aan. Hetzelfde apparaat,
op dezelfde plek, met dezelfde firmware en dezelfde instellingen, komt dan
binnen als iemand die we nooit eerder zagen, en ``get_or_create_repeater`` doet
wat hij hoort te doen: hij maakt een rij. Omdat de NAAM wel dezelfde is,
botst de slug en wordt het ``<naam>-2``.

Zo is op 24 oktober 2026 ``be-hss-dinx-home-2`` ontstaan, na de brownout van de
dakrepeater. Twee rijen, één antenne, en op elke grafiek met een reeks per node
twee lijnen van dezelfde accu.

Dit script is GEEN automatische ontdubbeling en hoort dat ook niet te worden.
Twee nodes met dezelfde naam zijn meestal gewoon twee nodes; alleen een mens
weet dat er in dit geval één kast op tafel stond. Het script controleert die
aanname wel (zie ``--doen`` hieronder) en weigert als ze niet klopt.

Wat er verhuist, en wat niet
----------------------------
``packets`` -- de kern. De waarnemingen staan onder de sleutel van de node die
ze HOORDE, niet onder een rij-id, dus ze blijven bij het verwijderen van de rij
gewoon staan als wees: 84 pakketten met een waarnemer die nergens meer bestaat.
Ze verhuizen naar de nieuwe sleutel, want het was fysiek dezelfde ontvanger.

``alerts`` -- verhuizen. Geen ``ON DELETE CASCADE``, dus ook deze zouden anders
achterblijven zonder node.

``latest``, ``samples``, ``neighbors`` -- alleen waar het doel NIETS heeft. Een
waarde van de oude rij over een nieuwere heen zetten zou de grafiek laten
terugspringen; dat is geen samenvoeging maar bederf. Wat niet mee kon, wordt
geteld en genoemd -- stil laten vallen is hier de enige echt verkeerde uitkomst.

``repeater_cli``, ``repeater_filter``, ``channel_names``, ``node_monitors`` --
blijven bij het doel zoals ze zijn en verdwijnen bij de bron met de CASCADE. De
bron heeft ze per definitie nauwelijks: hij bestond maar een half uur.

De TIJDREEKSEN in VictoriaMetrics verhuizen NIET. Een reeks draagt daar het
label ``repeater="<slug>"`` en dat label is niet te herschrijven zonder de
reeks opnieuw in te sturen. Dat is hier ook niet de moeite: het gaat om één
tijdstip. De reeksen blijven dus onder de oude slug staan tot de bewaartermijn
ze opruimt, en dit script zegt dat erbij in plaats van te doen alsof ze weg
zijn. Wie ze eerder kwijt wil, gebruikt het ``delete_series``-pad van
VictoriaMetrics -- apart, en met dezelfde terughoudendheid.

Draaien (in de container, want daar staat de databank):

    docker compose exec -T meshmanager python tools/samenvoegen_dubbele_node.py \
        --van be-hss-dinx-home-2 --naar be-hss-dinx-home
    docker compose exec -T meshmanager python tools/samenvoegen_dubbele_node.py \
        --van be-hss-dinx-home-2 --naar be-hss-dinx-home --doen

Zonder ``--doen`` verandert er niets en staat er alleen wat er zou gebeuren.
Dat is de standaard omdat de laatste stap -- de bronrij verwijderen -- met zijn
CASCADE niet terug te draaien is.

**Eerst op een kopie.** ``--db`` wijst dit script naar een ander bestand:

    docker compose exec -T meshmanager python -c "import sqlite3; \
        b=sqlite3.connect('/data/mcs.sqlite3'); k=sqlite3.connect('/tmp/kopie.sqlite3'); \
        b.backup(k)"
    docker compose exec -T meshmanager python tools/samenvoegen_dubbele_node.py \
        --db /tmp/kopie.sqlite3 --van ... --naar ... --doen

``--db`` en niet een omgevingsvariabele, en dat is met bloed geschreven: de
variabele die ``config.DATA_DIR`` vult heet ``MM_DATA_DIR`` en NIET ``DATA_DIR``
(zie ``config.env``, dat er ``MM_`` voor plakt). Wie het zonder voorvoegsel zet,
krijgt geen foutmelding maar een script dat rustig de ECHTE databank opent. Deze
vlag maakt dat onmogelijk en het script drukt bovendien bij elke start af welk
bestand het gaat aanraken -- lees die regel voor je ``--doen`` typt.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app import config, db   # noqa: E402  (na de sys.path-regel, met opzet)

# Tabellen waar een rij per (node, sleutel) staat: verhuizen mag alleen in een
# gat. De tweede waarde is de kolom die de rij binnen de node uniek maakt.
PER_SLEUTEL = (
    ("latest", "metric"),
    ("samples", "metric"),      # ook ts, maar zie _verhuis_gaten
    ("neighbors", "prefix"),
)


def _zoek(aanduiding: str):
    """Een rij bij een slug of bij een pubkey-voorvoegsel."""
    rij = db.qone("SELECT * FROM repeaters WHERE slug=?", (aanduiding,))
    if rij:
        return rij
    sleutel = db.key_prefix(aanduiding)
    return db.find_repeater(sleutel) if sleutel else None


def _overlapt(bron, doel) -> str:
    """"" als de twee elkaar niet overlappen, anders waarom dat erg is.

    De controle die de hele aanname draagt. Twee identiteiten van ÉÉN apparaat
    bestaan na elkaar: de oude houdt op, de nieuwe begint. Zijn er momenten
    waarop ze allebei iets hoorden, dan stonden er twee radio's aan en is dit
    geen dubbele maar een tweede node -- en dan mag er niets samengevoegd
    worden, hoe erg de namen ook op elkaar lijken.

    Gemeten aan ``packets``, want dat is de enige tabel met een tijdstempel per
    waarneming in plaats van per laatste stand.

    De toets is NIET of de twee min/max-vensters elkaar raken. Dat lijkt de
    voor de hand liggende vraag en hij geeft het verkeerde antwoord: het doel
    luistert hier al een maand en zijn venster OMVAT dus per definitie de
    twintig minuten van een identiteit die vanmiddag ontstond. De vraag is of
    het doel IN dat venster iets gehoord heeft -- één pakket is al genoeg, want
    dan stonden er twee ontvangers tegelijk aan.

    Hoort de bron GEEN pakketten (een node die alleen uitgevraagd wordt, bouwt
    die tabel niet), dan is er geen bewijs -- en dat is iets anders dan bewijs
    van onschuld. Dit geval geeft een weigering en geen stilte, want hier staat
    de gevaarlijkste vergissing: twee nodes die toevallig op elkaar lijken,
    zonder één meting die tegenspreekt dat ze dezelfde zijn.
    """
    vraag = ("SELECT min(ts) mn, max(ts) mx, count(*) n FROM packets "
             "WHERE observer=?")
    a = db.qone(vraag, (bron["pubkey_prefix"],))
    if not a or not a["n"]:
        return ("GEENBEWIJS: " + bron["pubkey_prefix"] + " heeft geen enkel "
                "pakket in deze databank, dus er valt niet te meten of de twee "
                "ooit tegelijk aan stonden. Dit script kan hier niets "
                "bevestigen")
    tegelijk = db.qone(
        "SELECT count(*) n FROM packets WHERE observer=? AND ts BETWEEN ? AND ?",
        (doel["pubkey_prefix"], a["mn"], a["mx"]))["n"]
    if tegelijk:
        return (f"{doel['pubkey_prefix']} heeft {tegelijk} pakket(ten) gehoord "
                f"binnen het venster van {bron['pubkey_prefix']} "
                f"({a['mn']}..{a['mx']}). Twee radio's die tegelijk hoorden "
                "zijn twee nodes, geen dubbele")
    return ""


def _verhuis_gaten(tabel: str, sleutelkolom: str, bron_id: int, doel_id: int,
                   doen: bool) -> tuple[int, int]:
    """(verhuisd, laten staan) voor een tabel met een rij per sleutel."""
    extra = ", ts" if tabel == "samples" else ""
    rijen = db.q(f"SELECT {sleutelkolom}{extra} FROM {tabel} WHERE repeater_id=?",
                 (bron_id,))
    kan = []
    for r in rijen:
        waar = f"repeater_id=? AND {sleutelkolom}=?"
        params = [doel_id, r[sleutelkolom]]
        if tabel == "samples":
            # samples heeft (node, metric, ts) als sleutel: een punt op een
            # tijdstip dat het doel nog niet kent is geen conflict maar een gat.
            waar += " AND ts=?"
            params.append(r["ts"])
        if db.qone(f"SELECT 1 FROM {tabel} WHERE {waar}", tuple(params)):
            continue
        kan.append(r)
    if doen and kan:
        for r in kan:
            zet = f"UPDATE {tabel} SET repeater_id=? WHERE repeater_id=? AND {sleutelkolom}=?"
            params = [doel_id, bron_id, r[sleutelkolom]]
            if tabel == "samples":
                zet += " AND ts=?"
                params.append(r["ts"])
            db.execute(zet, tuple(params))
    return len(kan), len(rijen) - len(kan)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--van", required=True,
                   help="slug of pubkey-voorvoegsel van de rij die verdwijnt")
    p.add_argument("--naar", required=True,
                   help="slug of pubkey-voorvoegsel van de rij die blijft")
    p.add_argument("--doen", action="store_true",
                   help="schrijf de wijzigingen weg (standaard: alleen tonen)")
    p.add_argument("--db", default="",
                   help="pad naar een ANDERE databank (een kopie); standaard "
                        "de databank van de draaiende site")
    p.add_argument("--zonder-bewijs", action="store_true",
                   dest="zonder_bewijs",
                   help="ga door als de bron geen pakketten heeft en het "
                        "script dus niets kan bevestigen")
    args = p.parse_args()

    # Vóór de eerste db-aanroep, want get_conn() leest config.DB_PATH één keer
    # en houdt die verbinding daarna vast.
    if args.db:
        config.DB_PATH = Path(args.db)
        db._conn = None
    print(f"databank: {config.DB_PATH}")

    bron, doel = _zoek(args.van), _zoek(args.naar)
    if bron is None:
        print(f"FOUT: geen node gevonden voor --van {args.van!r}")
        return 2
    if doel is None:
        print(f"FOUT: geen node gevonden voor --naar {args.naar!r}")
        return 2
    if bron["id"] == doel["id"]:
        print("FOUT: --van en --naar zijn dezelfde rij")
        return 2

    print(f"bron : id={bron['id']} slug={bron['slug']} key={bron['pubkey_prefix']} "
          f"naam={bron['name']} laatst={bron['last_seen']}")
    print(f"doel : id={doel['id']} slug={doel['slug']} key={doel['pubkey_prefix']} "
          f"naam={doel['name']} laatst={doel['last_seen']}")

    bezwaar = _overlapt(bron, doel)
    if bezwaar.startswith("GEENBEWIJS: ") and args.zonder_bewijs:
        # De beheerder zegt met zoveel woorden dat hij het langs een andere weg
        # weet. Dat mag -- maar het staat er dan wel bij, zodat een
        # schermuitdraai later niet suggereert dat het script het bevestigd
        # heeft.
        print(f"LET OP: {bezwaar[12:]}; voortgezet op gezag van --zonder-bewijs")
    elif bezwaar:
        print(f"GEWEIGERD: {bezwaar.removeprefix('GEENBEWIJS: ')}")
        if bezwaar.startswith("GEENBEWIJS: "):
            print("           Weet je het zeker langs een andere weg (zelfde "
                  "kast, zelfde instellingen, een herstart ertussen), draai dan "
                  "opnieuw met --zonder-bewijs.")
        return 3

    pakketten = db.qone("SELECT count(*) n FROM packets WHERE observer=?",
                        (bron["pubkey_prefix"],))["n"]
    alarmen = db.qone("SELECT count(*) n FROM alerts WHERE repeater_id=?",
                      (bron["id"],))["n"]
    print(f"pakketten met deze waarnemer : {pakketten}")
    print(f"alarmen                      : {alarmen}")

    if args.doen:
        db.execute("UPDATE packets SET observer=? WHERE observer=?",
                   (doel["pubkey_prefix"], bron["pubkey_prefix"]))
        db.execute("UPDATE alerts SET repeater_id=? WHERE repeater_id=?",
                   (doel["id"], bron["id"]))

    for tabel, kolom in PER_SLEUTEL:
        verhuisd, bleef = _verhuis_gaten(tabel, kolom, bron["id"], doel["id"],
                                         args.doen)
        regel = f"{tabel:<10}: {verhuisd} verhuisd"
        if bleef:
            # Expliciet noemen: dit zijn de enige getallen die bij deze operatie
            # ECHT verdwijnen, en ze verdwijnen omdat het doel al een jonger
            # antwoord had. Stil laten vallen zou dit script onbetrouwbaar maken.
            regel += (f", {bleef} overgeslagen (het doel heeft daar al een "
                      "eigen, nieuwere waarde)")
        print(regel)

    print("tijdreeksen: blijven onder de oude slug staan in VictoriaMetrics "
          f"(repeater=\"{bron['slug']}\"); zie de kop van dit bestand")

    if not args.doen:
        print(f"\nDROOG GEDRAAID -- er is niets gewijzigd. Maak eerst een kopie "
              f"van de databank en draai dan opnieuw met --doen om rij "
              f"{bron['id']} ({bron['slug']}) te verwijderen.")
        return 0

    db.execute("DELETE FROM repeaters WHERE id=?", (bron["id"],))
    print(f"\nrij {bron['id']} ({bron['slug']}) verwijderd; "
          f"{doel['slug']} is nu de enige rij voor deze node.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
