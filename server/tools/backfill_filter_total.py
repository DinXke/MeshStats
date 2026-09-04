"""``filter_total`` terugrekenen over de historie die er al ligt.

Waarom dit bestaat
------------------
``filter_total`` (doorgelaten + weggegooid + vrijgesteld via de ACL) is de
noemer waartegen "hoeveel is er geweigerd" een verhouding wordt. Hij is
toegevoegd in site-versie 2.14.0 en wordt vanaf dat moment bij elke ingest
weggeschreven -- maar de grafiek van gisteren blijft leeg, terwijl de drie
componenten er wél staan. Dit script vult dat gat.

Het verzint niets. Voor elk tijdstip waarop ALLE DRIE de componenten een punt
hebben, wordt hun som weggeschreven onder dezelfde tijdstempel. Waar er één van
de drie ontbreekt, komt er geen punt: een som van twee van de drie zou een lager
totaal opleveren en dus een hoger weigeringspercentage suggereren dan er was.
Dat is precies de fout die ``_filter_metrics`` vermijdt door het totaal alleen te
berekenen waar ``passed`` gemeld is.

Draaien (in de container, want daar staan de instellingen en de tijdreeksen):

    docker compose exec -T meshmanager python tools/backfill_filter_total.py --uren 168
    docker compose exec -T meshmanager python tools/backfill_filter_total.py --uren 168 --doen

Zonder ``--doen`` rekent hij en schrijft hij niets. Dat is de standaard omdat
schrijven naar een tijdreeksdatabank niet ongedaan te maken is.

Herhaald draaien is veilig: dezelfde tijdstempel met dezelfde waarde overschrijft
zichzelf. Het is dus geen migratie die precies één keer mag lopen.
"""
import argparse
import sys
import time

sys.path.insert(0, "/app")

from app import db, tsdb   # noqa: E402  (na de sys.path-regel, met opzet)

COMPONENTEN = ("filter_passed", "filter_dropped", "filter_exempt")
DOEL = "filter_total"


def punten(slug, metric, uren):
    """{tijdstempel: waarde} voor één reeks, of None als de bron niets zegt."""
    reeks = tsdb.history(slug, metric, uren)
    if reeks is None:
        return None
    return {p[0]: p[1] for p in reeks if p and p[1] is not None}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uren", type=int, default=168,
                    help="hoe ver terug (standaard 168 = zeven dagen)")
    ap.add_argument("--doen", action="store_true",
                    help="werkelijk wegschrijven; zonder dit alleen rekenen")
    args = ap.parse_args()

    if not tsdb.enabled():
        print("Geen tijdreeksdatabank ingesteld (MM_TSDB_URL leeg). Dit script "
              "werkt alleen daarvoor: zonder tijdreeksen staat de historie in "
              "de samples-tabel en die vult zichzelf vanaf 2.14.0.")
        return 1

    totaal_geschreven = 0
    for rep in db.q("SELECT id, slug, name FROM repeaters ORDER BY id"):
        slug = rep["slug"]
        reeksen = {m: punten(slug, m, args.uren) for m in COMPONENTEN}
        if any(r is None for r in reeksen.values()):
            print("%-26s tijdreeksen niet te lezen -- overgeslagen" % rep["name"])
            continue
        if not reeksen["filter_passed"]:
            # Geen doorlaatteller: deze node (de stock-variant) heeft geen
            # noemer, en die verzinnen we niet. Zie de kop van dit bestand.
            print("%-26s geen doorlaatteller -- geen totaal mogelijk" % rep["name"])
            continue

        al = punten(slug, DOEL, args.uren) or {}
        nieuw = {}
        onvolledig = 0
        for ts, passed in reeksen["filter_passed"].items():
            dropped = reeksen["filter_dropped"].get(ts)
            exempt = reeksen["filter_exempt"].get(ts)
            if dropped is None or exempt is None:
                onvolledig += 1
                continue
            if ts in al:
                continue        # staat er al; niet nog eens
            nieuw[ts] = float(passed) + float(dropped) + float(exempt)

        print("%-26s %4d punten te vullen (%d stonden er al, %d onvolledig)"
              % (rep["name"], len(nieuw), len(al), onvolledig))
        if not args.doen or not nieuw:
            continue
        for ts, waarde in sorted(nieuw.items()):
            tsdb.record(rep["id"], slug, ts, {DOEL: waarde})
            totaal_geschreven += 1
        # De schrijfweg is een wachtrij met een eigen draad; even wachten zodat
        # hij leeg is voordat het proces eindigt.
        time.sleep(2)

    if args.doen:
        print("weggeschreven: %d punten" % totaal_geschreven)
    else:
        print("niets geschreven (geef --doen om het echt te doen)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
