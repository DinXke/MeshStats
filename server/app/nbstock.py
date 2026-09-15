"""De burenlijst van een stock repeater, uit zijn CLI.

WAAROM DIT BESTAAT. De dakrepeater vroeg de buren van de nodes die hij bewaakte
op met het binaire verzoek ``REQ_TYPE_GET_NEIGHBOURS`` (0x06) en publiceerde ze
mee in zijn statistiekenbericht. Toen die node wegviel bleef ``neighbor_count``
op de kaarten staan zoals hij dagen eerder was, en de burenlijst van die nodes
met hem -- niet omdat de buren weg waren, maar omdat de koerier weg was.

De weg terug hoeft geen tweede protocolimplementatie te zijn: **stock firmware
heeft een `neighbors`-commando in de gewone CLI**, en over die weg praat
MeshUptime toch al met deze repeaters (login + CLI over LoRa). Het antwoord is
tekst:

    2AE7AFA9:182:-11 208C4190:188:-46 624F70C1:195:41

per buur ``<4 byte sleutel hex>:<seconden geleden>:<snr maal 4>``, nieuwste
eerst, en ``-none-`` als er geen enkele buur is. De SNR staat er als SNR x 4 in
(``neighbour->snr = (int8_t)(snr * 4)`` in upstream), dus delen door vier.

DRIE BYTE SLEUTEL, NIET VIER. De CLI geeft acht hextekens; overal in deze
databank is een buurprefix er zes (``contacts.prefix6``, ``packets.sender``,
``neighbors.prefix``), en de naamkoppeling van een buur zoekt letterlijk op
``n.prefix = <prefix6>``. Acht bewaren zou elke buur een tweede rij geven naast
zijn eigen historie en hem tegelijk naamloos maken. Dus afkappen op zes -- exact
de breedte die de dakrepeater ook publiceerde (``MONITOR_NBR_PREFIX 3``).

WAT NIET GEBEURT. Een antwoord dat er niet uitziet als een burenlijst (een
repeater die het commando niet kent antwoordt "Unknown command") levert None op
en wordt niet als een lege lijst opgeslagen: "ik kon het niet lezen" en "hij
heeft er geen" zijn twee verschillende dingen, en alleen het tweede is een
meting.
"""
import re

# Eén buur: 8 hex, seconden geleden, snr x 4. Hoofdletters komen voor (toHex
# schrijft ze zo), dus niet hoofdlettergevoelig matchen.
_BUUR_RE = re.compile(r"^([0-9a-fA-F]{8}):(\d+):(-?\d+)$")

# Zoveel hextekens van de sleutel bewaren we. Zie de kop.
PREFIX_HEX = 6


def parse_neighbors(tekst: str) -> list | None:
    """De CLI-tekst -> een lijst zoals ``db.ingest`` hem verwacht, of None.

    None betekent "dit was geen burenantwoord". Een lege lijst betekent "deze
    repeater heeft geen buren", en dat is wel een uitkomst.
    """
    if not isinstance(tekst, str):
        return None
    t = tekst.strip()
    if not t:
        return None
    # RepeaterCli maakt van regeleindes spaties, de node zelf scheidt met \n:
    # beide vormen komen hier langs.
    if t.lower().startswith("-none-") or t.lower() == "-none-":
        return []

    buren = []
    for stuk in t.split():
        m = _BUUR_RE.match(stuk)
        if not m:
            # Eén onleesbaar stuk maakt de rest niet verdacht (het antwoord kan
            # afgekapt zijn: upstream stopt bij ~134 tekens), maar een antwoord
            # ZONDER ook maar één leesbare buur is geen burenlijst -- zie onder.
            continue
        sleutel, secs, snr4 = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        buren.append({
            "prefix": sleutel[:PREFIX_HEX],
            "snr": snr4 / 4.0,
            "seen_min": secs // 60,
        })
    if not buren:
        return None
    return buren


def apply_cli_neighbors(repeater_id: int, values: dict, source: str = "") -> int | None:
    """Een ``cmd:neighbors``-antwoord uit een CLI-ronde opslaan als burenlijst.

    Dezelfde bestemming als de MQTT-weg gebruikte: de ``neighbors``-tabel plus de
    meting ``neighbor_count``. De rest van de site hoort daarna niet te kunnen
    zien langs welke van de twee wegen het binnenkwam.

    Geeft het aantal buren terug, of None als er geen leesbaar burenantwoord in
    deze push zat.
    """
    from . import db

    ruw = None
    for sleutel, waarde in (values or {}).items():
        if str(sleutel).strip().lower() == "cmd:neighbors":
            ruw = waarde
            break
    if ruw is None:
        return None

    buren = parse_neighbors(ruw)
    if buren is None:
        return None

    # force=True: dit is een gevraagde uitlezing, net als een handmatige
    # verversing. Zonder force zou een ongewijzigd aantal buiten de
    # hartslagvenster-regel vallen en de grafiek gaten tonen waar niets
    # veranderde.
    db.ingest(repeater_id, db.utcnow(), {"neighbor_count": len(buren)}, buren, force=True)
    return len(buren)
