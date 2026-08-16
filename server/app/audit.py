"""Het audittrail: wie deed wat, met welke node, wanneer, en hoe liep het af.

Waarom dit er is
----------------
Zolang er één beheerder was, was "wie heeft deze node geflasht" geen vraag. Met
meerdere gebruikers is het er een die op een avond gesteld wordt waarop iemand op
een dak moet klimmen, en dan is het antwoord "dat weten we niet" duur.

Het past ook bij de lijn die de rest van dit project aanhoudt: een knop die
belooft wat hij niet waarmaakt is oneerlijk, en een handeling op afstand die geen
spoor achterlaat is dezelfde oneerlijkheid één stap later. De site kan tegenwoordig
zendtijd kosten, klokken zetten en firmware schrijven. Alle drie horen ze
navertelbaar te zijn.

Wat er in gaat
--------------
Elke handeling die iets *probeerde*. Dus ook de geweigerde: een poging die
afketste op de rechten is precies de rij die je wil zien als je je afvraagt of
iemand iets probeerde wat niet mocht. Ze staan er met ``outcome='geweigerd'``
naast de geslaagde, en niet in een apart logboek -- twee logboeken zijn twee
plaatsen om te kijken, en de tweede wordt vergeten.

Uitkomsten: ``ok`` (gelukt), ``geweigerd`` (de rechten zeiden nee), ``mislukt``
(mocht wel, ging mis) en ``deels`` (voor de opdrachten die langs twee wegen
tegelijk vertrekken en er één halen -- zie routes_admin._dispatch).

Wat er niet in gaat
-------------------
Wachtwoorden, tokens en de inhoud van instellingen die een geheim kunnen zijn.
``detail`` is voor de leesbare samenvatting van wat er gebeurde ("naar 1.10.0",
"via de monitor"), niet voor de nuttige lading. Dat is dezelfde regel die overal
in dit project geldt: een geheim hoort niet in een log en niet in een URL.

Waarom de schrijver nooit een handeling mag laten stranden
----------------------------------------------------------
``log()`` slikt zijn eigen fouten. Een volle schijf of een gelockte databank mag
een firmware-upgrade die al onderweg is niet halverwege doen ontploffen. Er staat
dan wel een regel in het gewone logboek, zodat een audittrail dat stiekem niets
meer bijhoudt niet stil blijft.
"""
import logging

from . import db

log_ = logging.getLogger("meshmanager.audit")

OK = "ok"
GEWEIGERD = "geweigerd"
MISLUKT = "mislukt"
DEELS = "deels"

# Hoe lang de regels blijven staan, in dagen, als de instelling niet gezet is.
# Ruim langer dan de pakketten (7 dagen) en dan de metingen (180): dit is de
# enige tabel waarvan de waarde juist in de ouderdom zit. "Wie heeft dit vorig
# jaar aangezet" is een echte vraag.
DEFAULT_AUDIT_DAYS = 730


def log(actor: str | None, action: str, *, rep=None, outcome: str = OK,
        detail: str = "", ip: str | None = None) -> None:
    """Eén regel. Faalt nooit hoorbaar -- zie de module-uitleg."""
    object_type = object_id = object_name = None
    if rep is not None:
        object_type = "node"
        if isinstance(rep, int):
            object_id = rep
        else:
            object_id = rep["id"]
            try:
                object_name = rep["name"]
            except (KeyError, IndexError):
                object_name = None
    try:
        db.execute(
            "INSERT INTO audit(ts, actor, action, object_type, object_id,"
            " object_name, outcome, detail, ip) VALUES(?,?,?,?,?,?,?,?,?)",
            (db.utcnow(), actor or "onbekend", action, object_type, object_id,
             object_name, outcome, detail or "", ip or ""),
        )
    except Exception as err:  # noqa: BLE001 - een handeling mag hier niet op stranden
        log_.warning("audittrail niet geschreven (%s op %s): %s", action, object_id, err)


def recent(limit: int = 100, *, rep_id: int | None = None,
           actor: str | None = None) -> list:
    """De laatste regels, nieuwste eerst.

    ``rep_id`` beperkt tot één node: dat is de vorm waarin de nodepagina hem
    toont, zodat "wat is er met déze node gebeurd" te lezen is zonder door het
    hele trail te scrollen.
    """
    where, params = [], []
    if rep_id is not None:
        where.append("object_type='node' AND object_id=?")
        params.append(rep_id)
    if actor:
        where.append("actor=?")
        params.append(actor)
    sql = "SELECT * FROM audit"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    params.append(max(1, min(1000, limit)))
    return db.q(sql, params)


def prune(days: int | None = None) -> int:
    """Regels ouder dan de bewaartermijn weg. Geeft terug hoeveel er weggingen."""
    days = days if days is not None else db.setting_int("audit_retention_days",
                                                        DEFAULT_AUDIT_DAYS)
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return db.execute_rowcount("DELETE FROM audit WHERE ts<?", (cutoff,))
