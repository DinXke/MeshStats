"""Wie mag welke node uitvragen: de ingestelde monitorlijst, per node en per groep.

Tot nu toe was de monitorrelatie een **waarneming**: wie de cijfers van een node
doorstuurt, is zijn monitor (``repeaters.source_prefix``). Dat werkt zolang er
één kandidaat is en het is meteen stuk zodra er twee zijn -- dan bepaalt het
toeval wie er als eerste iets doorstuurde wie er voortaan uitvraagt.

Hier komt daar **configuratie** naast: een geordende lijst repeaters die een node
mogen uitvragen. Naast en niet in plaats van. Beide worden getoond, want ze
kunnen verschillen en dat verschil is informatie:

    "stuurt zijn cijfers door via X"     waargenomen -- wie hem feitelijk hoort
    "mag uitgevraagd worden via X, dan Y" ingesteld  -- wie het van ons mag

Wijken ze af, dan zegt dat iets. X hoort hem en Y bereikt hem niet; of iemand
heeft de instelling net veranderd en de volgende ronde moet het nog laten zien.
Ze in één veld persen zou dat onzichtbaar maken -- en dat is precies de fout die
dit project op andere plekken al een paar keer heeft opgeruimd.

Twee niveaus, met dezelfde tweedeling als bij de rechten: per node, of per
nodegroep. De node wint van de groep, en dat is de enige regel -- ze worden niet
samengevoegd. Een lijst die half uit een groep en half uit een node komt, is een
lijst waarvan niemand de volgorde nog kan navertellen.
"""

from __future__ import annotations

from . import commanding, db, firmware, nodeconfig

# Hoeveel kandidaten een lijst mag hebben. Niet omdat vier onmogelijk is, maar
# omdat elke extra kandidaat een extra mislukte sweep betekent voordat de lijst
# op is -- en dat is zendtijd op een gedeelde band. Drie is genoeg voor
# "de gebruikelijke, de buurman, en de node op zolder".
MAX_CANDIDATES = 3


def _rows(target_id: int) -> list:
    return db.q("SELECT nm.position, r.* FROM node_monitors nm "
                "JOIN repeaters r ON r.id = nm.monitor_id "
                "WHERE nm.repeater_id=? ORDER BY nm.position", (target_id,))


def _group_rows(group_id: int) -> list:
    return db.q("SELECT gm.position, r.* FROM node_group_monitors gm "
                "JOIN repeaters r ON r.id = gm.monitor_id "
                "WHERE gm.group_id=? ORDER BY gm.position", (group_id,))


def groups_of(target_id: int) -> list:
    return db.q("SELECT g.* FROM node_groups g "
                "JOIN node_group_members m ON m.group_id = g.id "
                "WHERE m.repeater_id=? ORDER BY g.name", (target_id,))


def candidates(rep) -> dict:
    """De geordende kandidaatlijst voor deze node, en waar hij vandaan komt.

    De node wint van de groep. Niet samenvoegen: een lijst die half uit een groep
    en half uit een node komt is een lijst waarvan de volgorde niet meer na te
    vertellen is, en de volgorde is hier het hele punt.

    Staat er niets ingesteld, dan valt dit terug op de waarneming -- wie de
    cijfers feitelijk doorstuurt. Dat is wat er vóór deze tabellen gebeurde en het
    blijft het juiste antwoord voor de meeste installaties: één monitor, niets in
    te stellen. De herkomst gaat mee terug zodat de pagina het verschil kan
    benoemen in plaats van drie lijsten door elkaar te tonen.
    """
    target_id = int(firmware._field(rep, "id") or 0)
    eigen = _rows(target_id)
    if eigen:
        return {"source": "node", "group": None, "monitors": eigen}

    for groep in groups_of(target_id):
        uit_groep = _group_rows(groep["id"])
        if uit_groep:
            return {"source": "group", "group": groep, "monitors": uit_groep}

    # Niets ingesteld: terug naar de waarneming, en die mag de node zelf zijn.
    # Een node die zijn eigen cijfers publiceert leest ook zijn eigen CLI uit --
    # dat is de gewone gang van zaken voor een full managed node, en 'monitor'
    # is daar een groot woord voor 'zichzelf'. Hem hier uitsluiten zou de meest
    # voorkomende node zonder afzender laten zitten.
    waargenomen = db.find_repeater(firmware._field(rep, "source_prefix") or "")
    if waargenomen is not None:
        return {"source": "observed", "group": None, "monitors": [waargenomen]}
    return {"source": "none", "group": None, "monitors": []}


# --- toetsen bij het toewijzen ------------------------------------------------

def check(rep, monitor) -> str:
    """Kan deze repeater deze node werkelijk uitvragen? "" als het klopt.

    Getoetst bij het TOEWIJZEN en niet bij de eerste poging, want een lijst met
    een monitor die het nooit kan is een lijst die liegt -- en de kosten van die
    leugen zijn een sweep-timeout op een gedeelde band, elke ronde opnieuw.

    Wat hier wél en niet vast te stellen valt, is de moeite van het onderscheid
    waard. Firmware en identiteit weten we zeker. Of de monitor RECHTEN heeft op
    het doelwit weten we alleen als we zijn monitorlijst kunnen lezen, en dat
    vraagt een beheeradres; zonder dat adres is het antwoord "niet te
    controleren" en niet "goed".
    """
    if monitor is None:
        return "die repeater bestaat hier niet"
    if int(monitor["id"]) == int(rep["id"]):
        return "een node kan zichzelf niet uitvragen"

    versie = commanding.parse_version(monitor["fw_meshmanager"])
    if versie is None:
        return ("die repeater meldt geen versie van onze firmware; alleen een node "
                "met onze firmware kan monitor zijn")
    if versie < commanding.MIN_MON_CMD_VERSION:
        nodig = ".".join(str(n) for n in commanding.MIN_MON_CMD_VERSION)
        return (f"die repeater draait {monitor['fw_meshmanager']}; een monitor "
                f"uitvragen over LoRa bestaat pas vanaf {nodig}")
    return ""


def rights_note(rep, monitor) -> dict:
    """Wat de monitor zelf zegt over zijn rechten op deze node.

    Apart van ``check`` omdat dit het netwerk op gaat: ``check`` is een toets die
    altijd kan, dit is een blik die alleen kan als de monitor een beheeradres
    heeft. De uitkomst is nadrukkelijk driedelig -- goed, mis, of onbekend -- want
    "niet te controleren" als "goed" tonen is precies hoe een lijst gaat liegen.
    """
    host = str((monitor["ota_host"] if monitor is not None else "") or "").strip()
    if not host:
        return {"known": False, "reason": "de monitor heeft geen beheeradres, "
                                          "dus zijn rechten zijn hier niet te lezen"}
    info = nodeconfig.rights_for(host, rep["pubkey_prefix"])
    if not info["ok"]:
        return {"known": False, "reason": info["error"]}
    return {"known": True, "info": info}


# --- schrijven ----------------------------------------------------------------

def set_for_node(target_id: int, monitor_ids: list) -> None:
    """De hele lijst in één keer, want de volgorde is de instelling.

    Vervangen en niet bijwerken: een lijst die je regel voor regel bijwerkt heeft
    tussenstanden waarin dezelfde monitor twee posities heeft, en de primaire
    sleutel zou daar terecht over vallen. Eén transactie, oude eruit, nieuwe erin.
    """
    db.execute("DELETE FROM node_monitors WHERE repeater_id=?", (target_id,))
    for positie, mid in enumerate(_schoon(monitor_ids)):
        db.execute("INSERT INTO node_monitors(repeater_id, position, monitor_id, "
                   "created_at) VALUES(?,?,?,?)",
                   (target_id, positie, mid, db.utcnow()))


def set_for_group(group_id: int, monitor_ids: list) -> None:
    db.execute("DELETE FROM node_group_monitors WHERE group_id=?", (group_id,))
    for positie, mid in enumerate(_schoon(monitor_ids)):
        db.execute("INSERT INTO node_group_monitors(group_id, position, monitor_id, "
                   "created_at) VALUES(?,?,?,?)",
                   (group_id, positie, mid, db.utcnow()))


def _schoon(monitor_ids: list) -> list:
    """Lege plekken eruit, dubbelen eruit, en afkappen op MAX_CANDIDATES.

    Dubbelen eruit omdat dezelfde monitor twee keer proberen precies twee keer
    dezelfde uitkomst oplevert, tegen twee keer de zendtijd.
    """
    uit = []
    for mid in monitor_ids:
        try:
            n = int(mid)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in uit:
            uit.append(n)
    return uit[:MAX_CANDIDATES]
