"""Webpush: een alert op de telefoon, ook als er geen browser openstaat.

Dit is de tweede weg naast de meshberichten van de companion-app. De eerste weg
loopt over LoRa en werkt zonder internet; deze loopt over het pushkanaal van de
browserleverancier en werkt overal waar de telefoon internet heeft. Ze vullen
elkaar aan en vervangen elkaar niet: wie buiten bereik van het mesh is heeft
niets aan een meshbericht, en wie zonder internet zit heeft niets aan webpush.

Hoe het werkt
-------------
De browser abonneert zich bij zijn eigen pushdienst en geeft ons een endpoint
plus twee sleutels; die rij bewaren we in ``push_subscriptions``. Zodra er een
nieuwe rij in ``alerts`` verschijnt -- wie die tabel vult maakt hier niet uit,
dat is het hele punt van een tabel als koppelvlak -- stuurt de verzendlus het
bericht versleuteld naar elk bewaard endpoint. De pushdienst van de leverancier
bezorgt het verder; de inhoud kan hij niet lezen, want de payload is met de
sleutels van de abonnee versleuteld (dat doet pywebpush).

De verzendlus is een eigen achtergronddraad, net als retention en clocksync en
om dezelfde reden: een module die de opslag bezit hoort geen planner te zijn,
en andersom. Hij POLLT de tabel in plaats van dat de schrijver hem wakker
maakt, met opzet: de schrijver van een alert (een andere route, een
achtergrondtaak, een script naast de app) hoeft deze module dan niet te kennen,
en een alert die geschreven wordt terwijl de site herstart gaat niet verloren
-- het watermerk staat in ``settings`` en de volgende ronde ziet de rij alsnog.

De sleutels
-----------
VAPID-sleutels identificeren deze server bij de pushdiensten. Ze staan NIET in
de repo en worden ook niet bij de eerste start gegenereerd: een gegenereerde
sleutel die stil in een datamap verschijnt is een geheim waarvan niemand weet
dat het bestaat, laat staan dat het een back-up verdient. Ze komen uit
``MM_VAPID_PUBLIC`` / ``MM_VAPID_PRIVATE`` (met ``MCS_``-terugval, zoals elke
variabele hier). Leeg betekent: webpush staat uit, met die reden zichtbaar in
de UI -- dezelfde filosofie als ``MM_FW_NODE_USER``. Hoe je ze aanmaakt staat
in docs/nl/meshmoni.md.

Eerlijk over de beperkingen: push werkt alleen over HTTPS (de service worker
eist dat), en iOS toont pas een toestemmingsvraag nadat de site via "Zet op
beginscherm" als app geïnstalleerd is. Dat staat ook in de documentatie en in
de UI, want een knop die op een iPhone stil niets doet is een kapotte knop.
"""
import json
import logging
import threading
import time

from . import config, db

log = logging.getLogger("meshmanager.webpush")

# pywebpush is een echte afhankelijkheid (zie requirements.txt), maar het
# ontbreken ervan mag de site niet plat leggen: een installatie die zonder
# webpush wil draaien, of een testomgeving zonder het pakket, krijgt dan
# "webpush staat uit" met de reden erbij in plaats van een ImportError bij het
# opstarten. Zelfde patroon als een lege VAPID-sleutel, en met opzet: alles wat
# deze feature nodig heeft en er niet is, is één zichtbare reden en geen crash.
try:
    from pywebpush import WebPushException, webpush as _pywebpush
except ImportError:  # pragma: no cover - hangt af van de omgeving
    _pywebpush = None

    class WebPushException(Exception):
        """Vervanger met dezelfde vorm als het origineel (bericht + response),
        zodat de except-tak hieronder en de tests hem net zo kunnen gebruiken."""

        def __init__(self, message, response=None):
            super().__init__(message)
            self.response = response


VAPID_PUBLIC = config.env("VAPID_PUBLIC", "").strip()
VAPID_PRIVATE = config.env("VAPID_PRIVATE", "").strip()
# Het contactadres dat in elk ondertekend pushverzoek meereist. Pushdiensten
# willen iemand kunnen bereiken als een server zich misdraagt; de standaard
# wijst naar de site zelf en is te overschrijven voor forks.
VAPID_SUBJECT = config.env("VAPID_SUBJECT", "mailto:beheer@meshmanager.net").strip()

# Seconden tussen twee kijkjes in de alerts-tabel. Kort genoeg dat een melding
# als "nu" voelt, lang genoeg dat de query (één indexzoekactie op de primaire
# sleutel) nooit iemand in de weg zit.
POLL_SECS = 15

# Na zoveel mislukkingen op rij gooien we een abonnement weg, ook zonder nette
# 404/410 van de pushdienst. Een endpoint dat een week lang time-outs geeft is
# een telefoon die er niet meer is, en elke ronde opnieuw proberen is werk dat
# nooit meer iets oplevert.
MAX_FAILURES = 8

WATERMERK = "push_last_alert_id"

# De alerts-tabel wordt óók elders aangemaakt (het beheer van de sensornodes
# vult hem); beide plekken gebruiken CREATE TABLE IF NOT EXISTS met exact
# hetzelfde schema, zodat de volgorde van aanmaken niet uitmaakt. Wijzig dit
# schema dus nooit eenzijdig.
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, repeater_id INTEGER, channel INTEGER,
       text TEXT NOT NULL, severity TEXT, ts TEXT NOT NULL,
       source TEXT NOT NULL, acked INTEGER DEFAULT 0, kind TEXT,
       ack_pushed INTEGER NOT NULL DEFAULT 0)""",
    # Eén rij per browser die meldingen wil. ``endpoint`` is de identiteit die
    # de pushdienst uitdeelt en dus de natuurlijke unieke sleutel; ``username``
    # zegt wie het abonnement nam, zodat een beheerder kan zien welke rijen van
    # een vertrokken account zijn. De twee sleutelvelden zijn van de browser en
    # voor ons betekenisloos: ze reizen alleen mee naar pywebpush.
    """CREATE TABLE IF NOT EXISTS push_subscriptions(
       id INTEGER PRIMARY KEY,
       endpoint TEXT UNIQUE NOT NULL,
       p256dh TEXT NOT NULL,
       auth TEXT NOT NULL,
       username TEXT,
       created_at TEXT NOT NULL,
       last_ok TEXT,
       failures INTEGER NOT NULL DEFAULT 0)""",
)


def ensure_schema() -> None:
    """Maak de twee tabellen aan als ze er nog niet zijn. Idempotent en goedkoop,
    dus aangeroepen door alles wat ze aanraakt in plaats van één keer bij het
    opstarten: ook een testdatabase of een vers aangemaakte datamap doet het dan."""
    for stmt in _SCHEMA:
        db.execute(stmt)


def status() -> dict:
    """Staat webpush aan, en zo nee: waarom niet? Dit is wat de UI toont.

    De reden is een zin voor op het scherm en geen foutcode: de lezer staat met
    zijn telefoon in de hand en moet weten wat hem te doen staat.
    """
    ensure_schema()
    row = db.qone("SELECT COUNT(*) AS n FROM push_subscriptions")
    if _pywebpush is None:
        reason = ("pywebpush is niet geïnstalleerd — "
                  "pip install -r requirements.txt op de server")
    elif not VAPID_PUBLIC or not VAPID_PRIVATE:
        reason = ("VAPID-sleutels niet gezet (MM_VAPID_PUBLIC / MM_VAPID_PRIVATE); "
                  "zie docs/nl/meshmoni.md voor hoe je ze aanmaakt")
    else:
        reason = None
    return {
        "enabled": reason is None,
        "reason": reason,
        "public_key": VAPID_PUBLIC or None,
        "subscriptions": row["n"] if row else 0,
    }


def enabled() -> bool:
    return status()["enabled"]


def subscribe(sub: dict, username: str) -> bool:
    """Bewaar (of ververs) één browserabonnement. False bij een onbruikbare rij.

    De browser stuurt zijn abonnement als ``{endpoint, keys: {p256dh, auth}}``.
    Meer dan vorm-controle is er niet te doen: of het endpoint echt bestaat
    blijkt pas bij het eerste versturen, en dan ruimt de verzendlus het op.
    ``https`` is wel hard: pushdiensten wonen nergens anders, en een http-URL
    hier is per definitie iets anders dan een pushabonnement.
    """
    endpoint = str(sub.get("endpoint", "")).strip()
    keys = sub.get("keys") or {}
    p256dh = str(keys.get("p256dh", "")).strip()
    auth_key = str(keys.get("auth", "")).strip()
    if not endpoint.startswith("https://") or not p256dh or not auth_key:
        return False
    ensure_schema()
    # Upsert op het endpoint: een browser die zich opnieuw aanmeldt (nieuwe
    # sleutels na een herinstallatie) vervangt zijn oude rij in plaats van er
    # een tweede naast te zetten -- anders krijgt die telefoon alles dubbel.
    db.execute(
        "INSERT INTO push_subscriptions(endpoint, p256dh, auth, username, created_at) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, "
        "auth=excluded.auth, username=excluded.username, failures=0",
        (endpoint, p256dh, auth_key, username, db.utcnow()),
    )
    return True


def unsubscribe(endpoint: str) -> bool:
    """Gooi één abonnement weg. True als er echt iets weg is."""
    ensure_schema()
    return db.execute_rowcount(
        "DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint or "",)) > 0


def _payload(alert) -> dict:
    """Wat er op het meldingsscherm komt, met namen en nooit kale nummers.

    De naam van de node en de kanaalnaam uit ``channel_names`` staan erbij als
    ze er zijn: "Uptimenode — google" zegt op een vergrendeld scherm waar het
    over gaat, "alert 17" niet. Een kanaal zonder naam heet "kanaal N", om
    dezelfde reden als overal op de site: een meting zonder naam is nog steeds
    een meting.
    """
    kop = []
    if alert["repeater_id"] is not None:
        rep = db.qone("SELECT name FROM repeaters WHERE id=?", (alert["repeater_id"],))
        if rep:
            kop.append(rep["name"])
    if alert["channel"] is not None:
        named = db.qone(
            "SELECT name FROM channel_names WHERE repeater_id=? AND channel=?",
            (alert["repeater_id"], alert["channel"]))
        kop.append(named["name"] if named else f"kanaal {alert['channel']}")
    return {
        "title": " — ".join(kop) or "MeshManager",
        "body": alert["text"],
        "severity": alert["severity"] or "info",
        "ts": alert["ts"],
        # Waar de tik op de melding heen moet: de alertenlijst van de subsite.
        "url": "/meshmoni#alerts",
    }


def _verstuur(sub, payload: dict) -> bool:
    """Eén melding naar één abonnement. True als de pushdienst hem aannam.

    Opruimen gebeurt hier en niet in een aparte veegronde: het antwoord van de
    pushdienst ís het signaal. 404/410 betekent "dit abonnement bestaat niet
    meer" (app verwijderd, toestemming ingetrokken) en de rij gaat meteen weg;
    al het andere telt als tijdelijke storing tot MAX_FAILURES bereikt is.
    """
    if _pywebpush is None or not VAPID_PRIVATE:
        return False
    try:
        _pywebpush(
            subscription_info={"endpoint": sub["endpoint"],
                               "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE,
            vapid_claims={"sub": VAPID_SUBJECT},
            # Een alert die na een uur nog niet bezorgd kon worden is oud
            # nieuws: het scherm van de subsite is dan allang de betere bron.
            ttl=3600,
        )
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code in (404, 410):
            db.execute("DELETE FROM push_subscriptions WHERE id=?", (sub["id"],))
            log.info("pushabonnement %s opgeruimd (HTTP %s)", sub["id"], code)
        else:
            _faal(sub)
        return False
    except Exception:  # noqa: BLE001 - netwerkfouten zijn hier verwacht verkeer
        _faal(sub)
        return False
    db.execute("UPDATE push_subscriptions SET last_ok=?, failures=0 WHERE id=?",
               (db.utcnow(), sub["id"]))
    return True


def _faal(sub) -> None:
    failures = (sub["failures"] or 0) + 1
    if failures >= MAX_FAILURES:
        db.execute("DELETE FROM push_subscriptions WHERE id=?", (sub["id"],))
        log.info("pushabonnement %s opgeruimd na %d mislukkingen", sub["id"], failures)
    else:
        db.execute("UPDATE push_subscriptions SET failures=? WHERE id=?",
                   (failures, sub["id"]))


def ronde() -> int:
    """Verstuur alles wat sinds de vorige ronde in ``alerts`` bijkwam.

    Geeft terug hoeveel meldingen de pushdiensten aannamen. Het watermerk
    schuift óók op als elk versturen mislukte: een alert wordt één keer
    aangekondigd en niet elke vijftien seconden opnieuw tot in de eeuwigheid --
    wie hem miste vindt hem in de alertenlijst, die blijft staan tot hij
    bevestigd wordt.
    """
    ensure_schema()
    try:
        grens = int(db.get_setting(WATERMERK, "0") or 0)
    except ValueError:
        grens = 0
    rows = db.q("SELECT * FROM alerts WHERE id>? ORDER BY id", (grens,))
    if not rows:
        return 0
    verstuurd = 0
    for alert in rows:
        payload = _payload(alert)
        # Per alert opnieuw gelezen: _verstuur kan rijen weggooien, en een
        # verwijderd abonnement nog eens proberen is precies het werk dat het
        # opruimen moest schelen.
        for sub in db.q("SELECT * FROM push_subscriptions"):
            if _verstuur(sub, payload):
                verstuurd += 1
    db.set_setting(WATERMERK, str(rows[-1]["id"]))
    return verstuurd


def _run() -> None:
    while True:
        time.sleep(POLL_SECS)
        try:
            ronde()
        except Exception:  # noqa: BLE001 - de lus mag nooit sterven
            log.exception("webpush-ronde mislukt")


_thread = None


def start() -> None:
    """Start de verzendlus, of leg uit waarom niet. Zelfde vorm als retention.start."""
    global _thread
    ensure_schema()
    st = status()
    if not st["enabled"]:
        # In de opstartlog, net als het eerste-start-wachtwoord: dit is waar een
        # beheerder kijkt als "waarom krijg ik geen meldingen?" de vraag is.
        print(f"[meshmanager] Webpush staat uit: {st['reason']}", flush=True)
        return
    # Eerste keer aanzetten: begin bij het heden. Zonder dit watermerk zou een
    # verse installatie met een gevulde alerts-tabel elke historische alert
    # alsnog naar elke telefoon duwen.
    if db.get_setting(WATERMERK) is None:
        row = db.qone("SELECT MAX(id) AS m FROM alerts")
        db.set_setting(WATERMERK, str((row["m"] if row else None) or 0))
    if _thread is not None:
        return
    _thread = threading.Thread(target=_run, name="webpush", daemon=True)
    _thread.start()
