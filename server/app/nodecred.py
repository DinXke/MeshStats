"""De eigen weblogin per node: opslag, obfuscatie, en het genereren ervan.

Waarom dit bestaat
------------------
Tot nu toe deelde elke node één weblogin met de hele vloot:
``MM_FW_NODE_USER``/``MM_FW_NODE_PASS`` (firmware.NODE_USER/NODE_PASS). Dat is
dezelfde credential waarmee de server firmware en instellingen naar ELKE node
schrijft. Eén node die uitlekt -- een afgeluisterde HTTP-sessie, een node die in
verkeerde handen valt -- geeft daarmee de sleutel van álle nodes weg.

De reparatie: elke sensornode krijgt zijn eigen weblogin, die de server kent en
per node bewaart. Een lek beperkt zich dan tot die ene node. De vlootsleutel
blijft de TERUGVAL voor nodes die nog niet geroteerd zijn (``web_user`` NULL),
zodat een bestaande installatie na de update gewoon blijft werken tot elke node
een keer geroteerd is.

Waarom obfuscatie en geen echte versleuteling
---------------------------------------------
Het wachtwoord wordt met een sleutel uit de installatie (``config.SECRET``, het
bestand ``secret.key`` naast de databank) omgezet in een blob die een terloopse
blik in de databank of een back-up niet leesbaar oplevert. Dat is met opzet
GEEN echte versleuteling en dit bestand doet niet alsof: wie de databank kan
lezen, kan meestal ook ``secret.key`` lezen (ze staan in dezelfde datamap), en
dan is de blob terug te draaien. Het haalbare hier is "niet in platte tekst in
een back-up die je doormailt", en dat is precies wat dit levert -- niet meer.

De echte grens ligt elders en staat eerlijk in de docs: Basic-auth over HTTP
stuurt ook een PER-NODE-wachtwoord nog leesbaar over het LAN. Dit ontwerp
beperkt de SCHADE van één lek (één node in plaats van de vloot); het vervangt
geen TLS of een apart VLAN. Zie docs/per-node-credentials.md.

Afhankelijkheden bewust smal
----------------------------
Dit bestand leunt alleen op ``config`` en ``db`` -- niet op ``firmware``. Dat is
geen toeval: ``firmware`` leunt op DIT bestand (in ``_auth_header``), en een
tegenimport zou een kringetje maken. De netwerkkant van een rotatie
(``POST /web/cred`` naar de node) staat daarom in ``sensornode.rotate_cred``,
waar de andere IP-handelingen naar een node ook staan, en niet hier.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from . import config, db

# Versiebyte vooraan de blob. Eén byte, zodat een toekomstig ander formaat naast
# dit kan bestaan zonder de oude blobs onleesbaar te maken: de-obfuscatie kijkt
# eerst hiernaar en weet dan hoe de rest eruitziet.
_VERSION = 1
_NONCE_LEN = 16
_TAG_LEN = 8


def _keystream(nonce: bytes, n: int) -> bytes:
    """Een sleutelstroom van ``n`` bytes uit de installatiesleutel en de nonce.

    HMAC-SHA256 in tellermodus: blok i is HMAC(SECRET, nonce || i). Geen kale
    hash maar HMAC, zodat de sleutel er echt in zit en niet alleen als voorvoegsel
    van de data. Stdlib en geen extern pakket, dezelfde lijn als auth.py: de
    sessies en CSRF-tokens van dit project hangen al aan ``config.SECRET`` via
    hmac/hashlib, en één cryptolaag is genoeg.
    """
    out = bytearray()
    counter = 0
    while len(out) < n:
        out.extend(hmac.new(config.SECRET,
                            nonce + counter.to_bytes(4, "big"),
                            hashlib.sha256).digest())
        counter += 1
    return bytes(out[:n])


def _tag(nonce: bytes, cipher: bytes) -> bytes:
    """Een korte echtheidsmarkering, zodat een verkeerde sleutel opvalt.

    Niet om een aanvaller tegen te houden -- dat kan obfuscatie niet -- maar zodat
    ``deobfuscate`` een blob die met een ANDERE ``secret.key`` gemaakt is (of een
    corrupte rij) herkent en None teruggeeft, in plaats van stilzwijgend een
    onzinwachtwoord op te leveren en daarmee bij de node te gaan kloppen.
    """
    return hmac.new(config.SECRET, b"nodecred-tag|" + nonce + cipher,
                    hashlib.sha256).digest()[:_TAG_LEN]


def obfuscate(plain: str) -> str:
    """Een wachtwoord omzetten in de blob die in ``web_pass_enc`` gaat.

    Elke keer een nieuwe nonce, zodat twee nodes met hetzelfde wachtwoord niet
    dezelfde blob krijgen -- anders zou de databank verraden welke nodes gelijk
    ingesteld zijn.
    """
    raw = (plain or "").encode("utf-8")
    nonce = secrets.token_bytes(_NONCE_LEN)
    cipher = bytes(b ^ k for b, k in zip(raw, _keystream(nonce, len(raw))))
    blob = bytes([_VERSION]) + nonce + _tag(nonce, cipher) + cipher
    return base64.b64encode(blob).decode("ascii")


def deobfuscate(blob: str) -> str | None:
    """De blob terug naar het wachtwoord, of None als hij niet klopt.

    None bij een lege waarde, een onleesbare blob, een onbekende versie, of een
    markering die niet klopt (verkeerde ``secret.key`` of corrupte rij). Elke
    aanroeper behandelt None als "geen bruikbare per-node-login" en valt terug op
    de vlootsleutel -- dezelfde terugval als een node die nog nooit geroteerd is.
    """
    if not blob:
        return None
    try:
        raw = base64.b64decode(str(blob).encode("ascii"), validate=True)
    except (ValueError, TypeError):
        return None
    if len(raw) < 1 + _NONCE_LEN + _TAG_LEN or raw[0] != _VERSION:
        return None
    nonce = raw[1:1 + _NONCE_LEN]
    tag = raw[1 + _NONCE_LEN:1 + _NONCE_LEN + _TAG_LEN]
    cipher = raw[1 + _NONCE_LEN + _TAG_LEN:]
    if not hmac.compare_digest(tag, _tag(nonce, cipher)):
        return None
    plain = bytes(b ^ k for b, k in zip(cipher, _keystream(nonce, len(cipher))))
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return None


def for_host(host: str) -> tuple[str, str] | None:
    """De per-node ``(user, pass)`` bij een beheeradres, of None.

    Dit is de functie die firmware._auth_header aanroept om te beslissen WELKE
    credential aan een uitgaand verzoek hangt. None betekent "gebruik de
    vlootsleutel" -- geen opgeslagen login, of een blob die niet te openen is.

    Bewust GEEN uitzondering bij een databankfout: het bepalen van de credential
    mag een verbinding naar een node niet laten stranden. Kan de opzoeking niet,
    dan is de vlootsleutel het veilige antwoord (de node blijft bereikbaar), en de
    doelcontrole in firmware.check_target beslist toch al of dit adres überhaupt
    een verbinding en een wachtwoord verdient.
    """
    try:
        row = db.node_web_cred_for_host(host)
    except Exception:  # noqa: BLE001 - zie docstring: nooit de verbinding breken
        return None
    if not row:
        return None
    user = str(row["web_user"] or "").strip()
    plain = deobfuscate(row["web_pass_enc"])
    if not user or plain is None:
        return None
    return user, plain


def has_own(host: str) -> bool:
    """Of dit adres een eigen, bruikbare weblogin heeft (True) of nog op de
    gedeelde vlootsleutel zit (False). Voor de zichtbaarheid op de nodepagina."""
    return for_host(host) is not None


# Uit welke tekens een gegenereerd wachtwoord bestaat. Geen leestekens: de
# credential reist door een JSON-body naar de node en door een Basic-auth-header
# terug, en een alfanumeriek wachtwoord van ruime lengte is even sterk zonder de
# kans op een teken dat ergens onderweg ontsnapt moet worden.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
_USER_LEN = 12
_PASS_LEN = 24


def generate() -> tuple[str, str]:
    """Een verse, sterke ``(user, pass)`` voor een rotatie.

    Ook de gebruikersnaam is willekeurig: een vaste naam met alleen een wisselend
    wachtwoord geeft de helft van de credential weg aan wie de vorige lek zag. Het
    voorvoegsel ``mm-`` maakt op de node zichtbaar dat deze login door de server
    beheerd wordt en niet met de hand gezet is.
    """
    user = "mm-" + "".join(secrets.choice(_ALPHABET) for _ in range(_USER_LEN))
    pw = "".join(secrets.choice(_ALPHABET) for _ in range(_PASS_LEN))
    return user, pw


def store(repeater_id: int, user: str, plain: str) -> None:
    """De nieuwe login opslaan, geobfusceerd. Alleen NA een geslaagde rotatie.

    Zie db.set_node_web_cred voor waarom de volgorde ertoe doet: eerst de node
    laten aanvaarden, dan pas bewaren.
    """
    db.set_node_web_cred(repeater_id, user, obfuscate(plain))
