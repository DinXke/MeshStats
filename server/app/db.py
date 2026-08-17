"""SQLite layer: schema, helpers and ingest logic.

Deliberately plain sqlite3 with a module-level connection and a mutex instead of
an ORM: the workload is a handful of small writes per minute plus page reads, so
an ORM would only add a dependency and a migration story we do not need. The
schema is applied with CREATE TABLE IF NOT EXISTS on every connect, which
doubles as the migration mechanism for additive changes.
"""
import logging
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from . import config, countries, packets, tsdb

log = logging.getLogger("meshmanager.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS repeaters(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  pubkey_prefix TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  is_public INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  last_seen TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS latest(
  repeater_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  metric TEXT NOT NULL,
  ts TEXT NOT NULL,
  value REAL,
  value_str TEXT,
  PRIMARY KEY(repeater_id, metric)
);
CREATE TABLE IF NOT EXISTS samples(
  repeater_id INTEGER NOT NULL,
  metric TEXT NOT NULL,
  ts TEXT NOT NULL,
  value REAL NOT NULL,
  PRIMARY KEY(repeater_id, metric, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS neighbors(
  repeater_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  prefix TEXT NOT NULL,
  name TEXT,
  snr REAL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY(repeater_id, prefix)
);
CREATE TABLE IF NOT EXISTS tokens(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  token_hash TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL,
  last_used TEXT,
  revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS admins(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL
);
-- Groepen gebruikers en groepen nodes. Twee tabellenparen met dezelfde vorm, en
-- met opzet niet één generieke ``groups``-tabel met een typekolom: een lidmaatschap
-- verwijst naar een echte rij, en een gedeelde tabel zou die refertes niet meer
-- door de databank kunnen laten bewaken. Zie rbac.py voor wat een groep betekent.
CREATE TABLE IF NOT EXISTS user_groups(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_group_members(
  group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
  PRIMARY KEY(group_id, user_id)
);
CREATE TABLE IF NOT EXISTS node_groups(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_group_members(
  group_id INTEGER NOT NULL REFERENCES node_groups(id) ON DELETE CASCADE,
  repeater_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  PRIMARY KEY(group_id, repeater_id)
);
-- Welke repeaters een node MOGEN beheren, in volgorde van voorkeur. Twee
-- tabellen met dezelfde vorm, één per doelsoort: één node, of een nodegroep --
-- dezelfde tweedeling als bij grants, en om dezelfde reden.
--
-- Dit is CONFIGURATIE en staat nadrukkelijk naast de WAARNEMING in
-- repeaters.source_prefix. Die kolom zegt wie de cijfers van een node feitelijk
-- doorstuurt; deze tabellen zeggen wie hem mag uitvragen. Ze kunnen verschillen,
-- en dat verschil is informatie en geen fout: een node die via X binnenkomt
-- terwijl Y is ingesteld, betekent dat X hem hoort en Y hem niet bereikt -- of
-- dat iemand de instelling net veranderd heeft en de volgende ronde het nog moet
-- laten zien. Ze in één kolom persen zou dat onzichtbaar maken.
--
-- ``position`` is de volgorde van proberen, vanaf 0. Redundantie loopt over
-- ronden en niet binnen één ronde: zie sweepsched voor waarom drie kandidaten
-- achter elkaar aflopen zendtijd is die niemand gevraagd heeft.
CREATE TABLE IF NOT EXISTS node_monitors(
  repeater_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  monitor_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY(repeater_id, position)
);
CREATE TABLE IF NOT EXISTS node_group_monitors(
  group_id INTEGER NOT NULL REFERENCES node_groups(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  monitor_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY(group_id, position)
);
-- Eén toekenning: wie (gebruiker of gebruikersgroep) mag wat (rol) op welke
-- nodes (één node, een nodegroep, of alle). ``effect`` is 'allow' of 'deny'.
-- De oplossingsregel staat in rbac.resolve() en nergens anders.
CREATE TABLE IF NOT EXISTS grants(
  id INTEGER PRIMARY KEY,
  subject_type TEXT NOT NULL,
  subject_id INTEGER NOT NULL,
  object_type TEXT NOT NULL,
  object_id INTEGER,
  role TEXT,
  effect TEXT NOT NULL DEFAULT 'allow',
  created_at TEXT NOT NULL,
  created_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_grants_subject ON grants(subject_type, subject_id);
-- Het audittrail. Eén rij per handeling die iets probeerde te doen -- geslaagd,
-- geweigerd of mislukt. Zie audit.py voor waarom een geweigerde poging er net zo
-- goed in hoort als een geslaagde.
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  object_type TEXT,
  object_id INTEGER,
  object_name TEXT,
  outcome TEXT NOT NULL,
  detail TEXT,
  ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_object ON audit(object_type, object_id, ts);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts(
  prefix TEXT PRIMARY KEY,
  prefix6 TEXT NOT NULL,
  name TEXT,
  lat REAL,
  lon REAL,
  node_type TEXT,
  updated TEXT NOT NULL,
  country TEXT
);
CREATE INDEX IF NOT EXISTS idx_contacts_p6 ON contacts(prefix6);
CREATE TABLE IF NOT EXISTS repeater_cli(
  repeater_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  param TEXT NOT NULL,
  value TEXT,
  updated TEXT NOT NULL,
  PRIMARY KEY(repeater_id, param)
);
CREATE TABLE IF NOT EXISTS repeater_filter(
  repeater_id INTEGER PRIMARY KEY REFERENCES repeaters(id) ON DELETE CASCADE,
  state TEXT NOT NULL,
  updated TEXT NOT NULL,
  source TEXT
);
CREATE TABLE IF NOT EXISTS packets(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  observer TEXT NOT NULL,
  snr REAL,
  rssi REAL,
  len INTEGER,
  route TEXT,
  payload_type INTEGER,
  payload_name TEXT,
  path_len INTEGER,
  sender TEXT,
  phash TEXT,
  path TEXT,
  raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_packets_ts ON packets(ts);
-- Duplicate lookup and retention sweeps both scan on (observer, hash, time).
CREATE INDEX IF NOT EXISTS idx_packets_dup ON packets(observer, phash, ts);
-- The node panel asks "everything this node sent" of a table that holds a week
-- of receptions, which without an index is a full scan per opened node. Cheap
-- to carry: the column is six hex characters and NULL on the majority of rows
-- (only an advert names its sender in full), so the index stays a fraction of
-- the table. The observer side of the same panel needs no index of its own --
-- idx_packets_dup already leads with that column, which is why
-- node_reception_summary asks a range on ``observer`` instead of wrapping it in
-- substr(), an expression no index can serve.
CREATE INDEX IF NOT EXISTS idx_packets_sender ON packets(sender);
"""

# Additive column migrations. CREATE TABLE IF NOT EXISTS covers new tables, but
# SQLite has no ADD COLUMN IF NOT EXISTS, so existing tables need the explicit
# check below. Dropping a live database is not an option here.
COLUMN_MIGRATIONS = [
    # Which node published this repeater's last statistics, and when. A node
    # relaying figures about a repeater it monitors is legitimate, so the two
    # identities differ on purpose -- see mqtt_ingest for the reasoning.
    ("repeaters", "source_prefix", "TEXT"),
    ("repeaters", "source_seen", "TEXT"),
    # Which firmware the last message came from: the MeshCore version, and the
    # our own module's version when the node runs it. Stored rather than
    # merely shown, because it decides whether the site may ask this node
    # anything at all -- accepting commands on the MQTT cmd topic starts at
    # nodefirmware 1.8.0, and a button that publishes into the void on anything
    # older is precisely the dishonesty these columns exist to prevent.
    ("repeaters", "fw", "TEXT"),
    ("repeaters", "fw_meshmanager", "TEXT"),
    # Op welk MQTT-topicvoorvoegsel deze node zich meldt. Zie
    # mqtt_ingest.command_prefix: tijdens de hernoeming luistert de site naar
    # twee voorvoegsels, maar een opdracht moet naar het ene waar deze node
    # werkelijk meeleest.
    ("repeaters", "topic_prefix", "TEXT"),
    # Where this node's management page lives, and which build the image on it
    # came from. Two things that cannot come from anywhere else:
    #
    # ``ota_host`` is typed in by an operator, because no message a node sends
    # contains its own IP -- and there is no reason to want one: whether the
    # server can reach a node over IP is a property of the network between them,
    # not of the node. Empty means "no IP path", which for a relayed node is a
    # permanent state and not a forgotten setting.
    #
    # ``pio_env`` is the PlatformIO build environment the node reports about
    # itself. Kept here so the page can show which image belongs to it without
    # touching the network first; never relied on when actually writing, where it
    # is read from the node again -- a board can have been swapped since.
    ("repeaters", "ota_host", "TEXT"),
    ("repeaters", "pio_env", "TEXT"),
    # De parametertabel zoals de node hem zelf meldt, en wanneer.
    #
    # Waarom dit bewaard wordt en niet elke keer opgehaald: de site bouwt zijn
    # schrijfformulier uit de lijst van de node -- nooit uit een tabel hier, zie
    # nodeconfig.py -- en die lijst kwam tot nu toe alleen van GET /api/cfg. Dat
    # werkt uitsluitend voor een node die de server over IP bereikt en waarvoor
    # hij een weblogin heeft. Een node die alleen over MQTT te bereiken is stuurt
    # hem mee met zijn instellingenronde, en die gebeurt hooguit dagelijks -- dus
    # moet wat er ooit binnenkwam blijven staan, ook na een herstart van de site.
    #
    # Nog steeds de lijst van de NODE en geen lijst van ons: hier staat alleen
    # wat hij zelf gemeld heeft. Ontbreekt hij, dan is er geen formulier, en dat
    # is het juiste antwoord -- niet een gok op een tabel die hier verzonnen is.
    ("repeaters", "cfg_spec", "TEXT"),
    ("repeaters", "cfg_spec_at", "TEXT"),
    # Whether this node counts as critical. A flag rather than something derived,
    # because "critical" is the operator's judgement about what breaks when this
    # node goes away, and that is nothing the traffic can be read for. With it
    # on, an upgrade first asks for the node's name -- upgrading the wrong node
    # is the most expensive mistake available on this page.
    ("repeaters", "is_critical", "INTEGER NOT NULL DEFAULT 0"),
    # Om de hoeveel uur deze repeater zijn CLI-instellingen opnieuw laat
    # uitvragen. NULL of 0 is uit, en dat is de standaard -- een node die erbij
    # komt hoort niet ongevraagd zendtijd te gaan kosten.
    #
    # Per node en niet één globaal getal, omdat de kosten per node verschillen:
    # een repeater aan de rand van je bereik betaalt elke ronde met stroom uit
    # een zonnepaneel en met pakketten die het misschien niet halen, terwijl een
    # node twee straten verderop bijna gratis is. Eén interval voor allemaal
    # betekent dat je de duurste node bepaalt of de goedkoopste verwaarloost.
    ("repeaters", "sweep_hours", "INTEGER"),
    # Het middenstuk van het topic waarop deze node zich meldt, LETTERLIJK zoals
    # het langskwam. Niet hetzelfde als pubkey_prefix: dat is genormaliseerd naar
    # kleine letters, en MQTT-topics zijn hoofdlettergevoelig.
    #
    # Dit bestaat omdat het niet zo was en dat twaalf uur onopgemerkt bleef.
    # MeshCore bouwt zijn topics met Utils::toHex(), en dat gebruikt
    # "0123456789ABCDEF" -- dus een node schrijft op en luistert naar
    # HOOFDLETTERS. De site normaliseerde bij binnenkomst naar kleine letters en
    # bouwde de opdracht daar weer mee op, waardoor elke opdracht op een topic
    # belandde waar niemand op geabonneerd was. publish() slaagde, de broker nam
    # de bytes aan, en er luisterde niemand -- exact hetzelfde patroon als de
    # verkeerde-topic-bug van firmware 1.3.0, en om exact dezelfde reden
    # onzichtbaar.
    #
    # Dus: onthouden wat we gezien hebben in plaats van uitrekenen wat het zou
    # moeten zijn. Dezelfde redenering als bij topic_prefix hierboven, en de
    # kolommen staan naast elkaar omdat ze samen één antwoord vormen: waar gaat
    # een opdracht voor deze node heen.
    ("repeaters", "topic_node", "TEXT"),
    # Of de cijfers van deze repeater bij hem OPGEHAALD zijn zonder inloggegevens,
    # in plaats van door hem zelf gepubliceerd of door een monitor met rechten
    # doorgestuurd. Een eigen kolom omdat het een eigen soort meting is:
    #
    #   er komt minder uit   een gast krijgt de status, de burenlijst en de
    #                        basistelemetrie; externe sensoren blijven achter
    #                        (perm_mask gaat op nul voor een gast)
    #   het komt minder vaak de monitor polt zijn hele lijst op zijn eigen
    #                        interval (standaard 900 s), niet op het ritme waarin
    #                        de node zelf zou publiceren
    #   het mag ontbreken    een gat in een grafiek betekent hier "niet gepolst"
    #                        en niet "de node lag stil", en dat verschil hoort
    #                        leesbaar te blijven
    #
    # Ditzelfde onderscheid maakt dit project elders overal -- gesteld tegenover
    # afgeleid bij een afzender, gemeten tegenover gemodelleerd bij een accu -- en
    # het weglaten zou een grafiek met gaten laten liegen over wie er stil was.
    ("repeaters", "is_guest_polled", "INTEGER NOT NULL DEFAULT 0"),
    # Wat een bezoeker van deze node te zien krijgt, fijnmaziger dan is_public.
    # Een positie is gevoeliger dan een batterijstand, en tot deze twee kolommen
    # er waren kon deze site dat verschil niet uitdrukken: publiek was alles of
    # niets.
    #
    # Allebei DEFAULT 1, en die standaard is het halve ontwerp. ALTER TABLE ADD
    # COLUMN vult bestaande rijen met de standaard, dus elke repeater die er al
    # stond blijft precies zo zichtbaar als hij gisteren was. Een privacykolom
    # die bij het toevoegen stilzwijgend iets van de kaart haalt, is een
    # slechtere fout dan de kolom die ontbrak -- wie morgenochtend zijn kaart
    # opent, hoort daar niets van te merken.
    #
    # De handhaving zit niet hier maar in ``visible_contacts`` en in de
    # naam-uitdrukkingen verderop; zie de toelichting bij VIEWS.
    ("repeaters", "show_position", "INTEGER NOT NULL DEFAULT 1"),
    ("repeaters", "show_name", "INTEGER NOT NULL DEFAULT 1"),
    # The hop hashes of the packet's path, comma-separated. Denormalised out of
    # ``raw`` on purpose: the packet detail view resolves every hop against the
    # contacts table, and re-decoding frames for that is work the ingest path has
    # already done once.
    ("packets", "path", "TEXT"),
    # The frame exactly as it came off the radio, hex. It is the only complete
    # record of a packet -- everything else in this table is a lossy summary --
    # and it is what lets a later reader re-parse a packet the decoder of the day
    # got wrong. It roughly doubles the size of a packet row, which is affordable
    # only because packets have their own short retention (PACKET_RETENTION_DAYS,
    # 7 by default) rather than the 180 days that metric samples get.
    ("packets", "raw", "TEXT"),
    # ISO 3166-1 alpha-2 for the contact's position, NULL when we cannot tell.
    # Written once, when a position becomes known -- see set_country.
    ("contacts", "country", "TEXT"),
    # Whether the sender restricted this packet to a region: 'unscoped', 'scoped'
    # or 'share'. See the Scoping section in packets.py for what each means and
    # why the region itself is not one of them. Stored rather than derived on
    # read because the packet list shows it per row, and re-decoding the frame
    # for a column is work the ingest path has already done once -- the same
    # reasoning as ``path`` above.
    ("packets", "scope", "TEXT"),
    # The two transport codes, comma-separated, exactly as they were on the wire.
    # NULL on an unscoped packet, where the wire has no room for them at all.
    ("packets", "scope_codes", "TEXT"),
    # The 1-byte source and destination hashes of REQ/RESPONSE/TXT_MSG/PATH
    # payloads (dest only for ANON_REQ), two hex characters each. One byte names
    # nobody by itself, but resolved against the contacts table it usually
    # answers "who sent this" on a mesh of realistic size -- the same resolution,
    # with the same honesty about ambiguity, that path hops already get.
    # Empty string means "decoded, and this packet type has none": without that
    # sentinel the backfill would re-decode every ACK and advert on every start,
    # looking for a hash that was never there.
    ("packets", "src_hash", "TEXT"),
    ("packets", "dest_hash", "TEXT"),
    # Wat het pakketfilter van de waarnemende node met dit pakket deed:
    # 'geweerd', 'doorgelaten', of NULL voor 'niet beoordeeld'. Die derde is een
    # eigen antwoord en niet een nette manier om 'doorgelaten' te zeggen: een
    # pakket dat aan de node zelf gericht was, dat direct gerouteerd werd, of
    # waarvan het frame de parser niet haalde, komt bij het filter niet eens
    # langs. NULL is hier dus de eerlijke waarde -- en het is ook wat elke rij van
    # vóór deze kolom krijgt. Dat valt niet achteraf te herstellen: het oordeel
    # staat niet in de bytes, dus _backfill_from_raw() kan hier niets, precies
    # zoals daar al staat over rijen van vóór de raw-kolom.
    #
    # Bewust GEEN index. De archiefquery zet twee LEFT JOINs en een GROUP BY
    # p.id neer en bouwt hoe dan ook een tijdelijke B-tree, dus een index hierop
    # zou die query niet bedienen en wel elke insert duurder maken.
    ("packets", "fwd", "TEXT"),
    # Waarop het geweerd werd: type, hops, rate, hash, kanaal of misvormd --
    # dezelfde zes sleutels die pktfilter.DROP_LABELS en de filter_drop_*-metrics
    # al gebruiken. Een zevende spelling van dezelfde zes redenen is precies wat
    # deze keuze voorkomt.
    ("packets", "fwd_reason", "TEXT"),
    # Of dit account alles mag, overal. Er staat DEFAULT 0 en niet DEFAULT 1, en
    # dat is een bewuste keuze met een prijs: ALTER TABLE ADD COLUMN vult
    # bestaande rijen met de standaard, dus zonder meer zou deze migratie élke
    # bestaande beheerder van al zijn rechten ontdoen. Dat wordt hieronder
    # rechtgezet door POST_MIGRATIONS, precies één keer, op het moment dat de
    # kolom aangemaakt wordt. Andersom -- DEFAULT 1 -- zou korter zijn en
    # verkeerd de andere kant op falen: een INSERT die de kolom vergeet, levert
    # dan stilzwijgend een serverbeheerder op.
    ("admins", "is_superuser", "INTEGER NOT NULL DEFAULT 0"),
    # Een account uitzetten zonder het te verwijderen. Verwijderen zou de naam
    # uit het audittrail niet weghalen (dat is de bedoeling) maar wel de
    # toekenningen, en dan is "waarom mocht die persoon dat" achteraf niet meer
    # te beantwoorden.
    ("admins", "disabled", "INTEGER NOT NULL DEFAULT 0"),
    ("admins", "created_at", "TEXT"),
    ("admins", "created_by", "TEXT"),
    # Wie dit token heeft aangemaakt. Zonder deze kolom is een token een sleutel
    # zonder eigenaar zodra er meer dan één beheerder is.
    ("tokens", "created_by", "TEXT"),
]


# Wat er precies één keer moet gebeuren: op het ogenblik dat een kolom hierboven
# voor het eerst aangemaakt wordt, en daarna nooit meer.
#
# Er staat er één in, en die is de belangrijkste regel van de hele
# rechtenmigratie. Vóór dit model was toegang alles-of-niets: wie kon inloggen,
# kon alles. Elk account dat er al stond had dus in de praktijk volledige
# rechten, en de migratie hoort dat te behouden in plaats van iedereen buiten te
# sluiten en een nieuwe eigenaar te moeten uitvinden.
#
# Gebonden aan het aanmaken van de kolom en niet aan "is er al een
# serverbeheerder": dat laatste zou bij elke start opnieuw kijken, en dan zet een
# beheerder die zichzelf bewust degradeert zichzelf bij de volgende herstart weer
# terug.
POST_MIGRATIONS = [
    ("admins", "is_superuser", "UPDATE admins SET is_superuser=1"),
]


# Kolommen die van naam veranderd zijn: (tabel, oud, nieuw).
#
# Hernoemen en niet naast elkaar laten bestaan, want twee kolommen die
# hetzelfde betekenen worden vroeg of laat allebei half gevuld. Het kan hier
# ook veilig: de enige kolom in deze lijst wordt bij ELK statistiekbericht
# opnieuw geschreven (record_firmware), dus zelfs als iemand na deze migratie
# terugrolt naar de vorige versie van de site, maakt die de oude kolom weer aan
# en staat ze bij het eerstvolgende bericht van elke node weer vol. Dat is de
# reden dat dit mag; voor een kolom met geschiedenis erin zou het niet mogen.
COLUMN_RENAMES = [
    # Heette fw_meshstats tot de hernoeming naar MeshManager.
    ("repeaters", "fw_meshstats", "fw_meshmanager"),
]


# Eén rij per node-sleutelprefix met de zichtbaarheidskeuzes van de gevolgde
# repeater die erachter zit. Als losse subquery en niet als LEFT JOIN op
# ``repeaters`` zelf: ``pubkey_prefix`` is uniek, maar twee sleutels kunnen in
# hun eerste zes hextekens samenvallen, en dan zou een rechtstreekse join elke
# contactrij verdubbelen -- in located_nodes() zou dezelfde node twee bolletjes
# krijgen. MIN() kiest bij zo'n botsing de striktste keuze, wat de enige
# richting is waarin fout gaan mag.
VISIBILITY_SQL = (
    "(SELECT substr(pubkey_prefix, 1, 6) AS p6, "
    " MIN(show_position) AS show_position, MIN(show_name) AS show_name "
    " FROM repeaters GROUP BY substr(pubkey_prefix, 1, 6))"
)

# De handhaving van "positie tonen" en "naam tonen", op één plek.
#
# Waarom een view en niet een filter per endpoint: de positie van een node komt
# langs zes endpoints naar buiten en de naam langs nog meer, allemaal doordat ze
# ``contacts`` aanjoinen. Een schakelaar die belooft een positie te verbergen
# terwijl één endpoint haar nog uitlevert, is erger dan geen schakelaar -- en
# zes losse filters zijn zes plaatsen waar de zevende endpoint vergeten wordt.
# Elke publieke leesweg leest daarom uit ``visible_contacts`` en de
# ingestwegen (upsert_advert, upsert_contacts, set_country) uit ``contacts``:
# wat de site weet verandert niet, alleen wat ze vertelt.
#
# Verborgen positie wordt NULL, en dat is met opzet dezelfde waarde als "van
# deze node nooit een advert met locatie gehoord". Die toestand is overal al
# afgehandeld -- de kaart telt hem als niet-geplaatst, een hop zonder positie
# breekt de keten van de heatmap, en een pakket valt terug op de waarnemer --
# dus er komt geen tweede mechanisme naast dat eerste. De endpoints tellen wél
# apart hoeveel nodes om die reden wegvallen, want stil weglaten is nog steeds
# liegen.
#
# Een verborgen naam wordt niet NULL maar de adreshash: '0x' plus de eerste
# sleutelbyte in hoofdletters, precies wat de pakkettenlijst al print voor een
# afzender die ze niet kan noemen. NULL zou de bestaande terugval op
# prefix.upper() in gang zetten, en dan stond er alsnog een identiteit.
#
# Het land hangt mee aan de positie: het is uit de coördinaten berekend
# (set_country) en dus niets anders dan een grove vorm ervan. Het achterlaten
# terwijl de positie weg is, zou de belofte half maken.
VIEWS = """
DROP VIEW IF EXISTS visible_contacts;
CREATE VIEW visible_contacts AS
SELECT c.prefix, c.prefix6, c.node_type, c.updated,
       CASE WHEN v.show_name = 0
            THEN '0x' || upper(substr(c.prefix6, 1, 2)) ELSE c.name END AS name,
       CASE WHEN v.show_position = 0 THEN NULL ELSE c.lat END AS lat,
       CASE WHEN v.show_position = 0 THEN NULL ELSE c.lon END AS lon,
       CASE WHEN v.show_position = 0 THEN NULL ELSE c.country END AS country
FROM contacts c
LEFT JOIN %s v ON v.p6 = c.prefix6;
""" % VISIBILITY_SQL

# De naam van een buur, met dezelfde handhaving erop.
#
# Buren hebben een eigen naamkolom: de repeater stuurt zijn burenlijst mét
# namen mee, en die wint normaal van wat wij uit adverts weten. Dat is precies
# waarom deze uitdrukking niet aan ``visible_contacts`` alleen genoeg heeft --
# een gevolgde repeater met verborgen naam die als buur van een ándere repeater
# in de lijst staat, zou anders langs ``neighbors.name`` alsnog naar buiten
# komen. En een gevolgde node hoeft niet in ``contacts`` te staan (hij kan
# gevolgd worden zonder ooit geadverteerd te hebben), dus de keuze moet uit
# ``repeaters`` komen en niet uit de view.
NEIGHBOR_NAME_SQL = (
    "CASE WHEN v.show_name = 0 THEN '0x' || upper(substr(n.prefix, 1, 2)) "
    "     WHEN n.name IS NULL OR lower(n.name) = n.prefix "
    "     THEN COALESCE(c.name, n.name) ELSE n.name END AS name"
)
NEIGHBOR_FROM = (
    "FROM neighbors n "
    "LEFT JOIN visible_contacts c ON c.prefix6 = n.prefix "
    f"LEFT JOIN {VISIBILITY_SQL} v ON v.p6 = n.prefix "
)


def _migrate(conn: sqlite3.Connection) -> None:
    # PRAGMA table_info op een tabel die niet bestaat geeft geen fout maar een
    # lege lijst, en dan zou de regel hieronder concluderen dat de kolom nog
    # ontbreekt en op een ALTER stuklopen. In productie kan dat niet gebeuren --
    # SCHEMA draait ervoor -- maar deze functie wordt ook los aangeroepen op een
    # oude database met een handvol tabellen erin, en dan hoort ze de rest over
    # te slaan in plaats van te vallen.
    def kolommen(table: str) -> set:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

    bestaat = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    # Eerst hernoemen, dan pas toevoegen: andersom maakt de regel hieronder
    # eerst een lege nieuwe kolom aan, en dan zou de hernoeming stuiten op een
    # naam die al bestaat en de oude waarden alsnog laten liggen.
    for table, old, new in COLUMN_RENAMES:
        if table not in bestaat:
            continue
        names = kolommen(table)
        if old in names and new not in names:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    fresh = set()
    for table, column, decl in COLUMN_MIGRATIONS:
        if table not in bestaat:
            continue
        if column not in kolommen(table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            fresh.add((table, column))
    # Alleen voor kolommen die zojuist ontstaan zijn. Zie POST_MIGRATIONS.
    for table, column, sql in POST_MIGRATIONS:
        if (table, column) in fresh:
            conn.execute(sql)
    # Ná de kolommen, want de view leest ze. En met een DROP ervoor in plaats
    # van CREATE IF NOT EXISTS: een view bewaart de tekst waarmee ze gemaakt is,
    # dus een database die de vorige versie al draaide zou anders voor altijd de
    # oude definitie houden. Opnieuw aanmaken kost niets -- er zitten geen
    # gegevens in.
    conn.executescript(VIEWS)


def _shorten_long_node_keys(conn: sqlite3.Connection) -> None:
    """Repareer repeaterrijen met een sleutel die langer is dan NODE_KEY_HEX.

    Ze zijn ontstaan doordat de uitvraagpagina de volle 32-byte sleutel uit een
    advert doorgaf aan een controle die op lengte alleen keurt. De weg is
    dichtgezet met ``node_key``; deze functie ruimt op wat er al staat, want die
    rijen blijven anders zitten -- zonder naamtreffer en zonder mogelijkheid om
    uitgevraagd te worden.

    Inkorten kan botsen met een rij die de korte sleutel al heeft, en dan is
    weggooien de juiste keus in plaats van samenvoegen. De korte rij is de
    oudere: hij ontstond uit een bericht van de node zelf of van zijn monitor,
    hij wordt elders aangewezen (``latest``, ``repeater_cli``, ``node_monitors``,
    toekenningen) en hij draagt wat een beheerder erover besloten heeft. De lange
    rij is hoogstens een paar uur oud en draagt niets dat de korte niet ook kan
    krijgen. Alleen ``is_guest_polled`` verhuist mee, want dat is een ware
    uitspraak over de herkomst van cijfers die nu bij de korte rij horen, en die
    zou anders verdwijnen.

    Eén ding gaat NIET mee: de naam. Die was op de lange rij de hex zelf -- dat
    is precies het gebrek dat dit oplost -- en hem overzetten zou de goede naam
    van de korte rij overschrijven met een sleutel.
    """
    try:
        rijen = conn.execute(
            "SELECT id, pubkey_prefix, is_guest_polled FROM repeaters "
            "WHERE length(pubkey_prefix) > ?", (NODE_KEY_HEX,)).fetchall()
    except sqlite3.OperationalError:
        return                      # kolom bestaat nog niet op een oude database
    for rij in rijen:
        kort = node_key(rij["pubkey_prefix"])
        if not kort:
            continue
        bestaand = conn.execute(
            "SELECT id FROM repeaters WHERE pubkey_prefix=?", (kort,)).fetchone()
        if bestaand is None:
            conn.execute("UPDATE repeaters SET pubkey_prefix=? WHERE id=?",
                         (kort, rij["id"]))
            # De naam meteen mee. Op deze rijen stond de hex als naam -- dat is het
            # gebrek dat de lange sleutel veroorzaakte -- en hij is nu wél op te
            # zoeken. Alleen als de naam de sleutel zelf was: een naam die iemand
            # zelf getypt heeft, blijft staan.
            oude_naam = conn.execute("SELECT name FROM repeaters WHERE id=?",
                                     (rij["id"],)).fetchone()["name"]
            if (oude_naam or "").lower() in (rij["pubkey_prefix"].lower(), kort):
                p6 = kort[:6]
                treffer = conn.execute(
                    "SELECT name FROM contacts WHERE prefix6=? "
                    "ORDER BY length(prefix) DESC, updated DESC", (p6,)).fetchone()
                if treffer and (treffer["name"] or "").strip():
                    conn.execute("UPDATE repeaters SET name=? WHERE id=?",
                                 (treffer["name"].strip()[:64], rij["id"]))
                    log.info("Repeaternaam voor %s uit contacts overgenomen: %s",
                             kort, treffer["name"])

            # En terug naar verborgen, maar alleen voor een node die wij bij
            # iemand anders zijn gaan ophalen. Dat is de standaard die voor zulke
            # rijen geldt, en met een sleutel van 64 tekens kon niemand zien wát
            # hij publiek zette. Dit staat luid in het logboek en in het rapport,
            # want het is niet aan deze migratie om een keuze van de beheerder
            # stil terug te draaien -- één klik op de pagina zet hem weer aan.
            if rij["is_guest_polled"]:
                was = conn.execute("SELECT is_public FROM repeaters WHERE id=?",
                                   (rij["id"],)).fetchone()["is_public"]
                if was:
                    conn.execute("UPDATE repeaters SET is_public=0 WHERE id=?",
                                 (rij["id"],))
                    log.warning(
                        "Repeater %s stond publiek terwijl hij zonder inloggegevens "
                        "bij iemand anders opgehaald wordt; op verborgen gezet. "
                        "Zet hem desgewenst met één klik weer zichtbaar.", kort)
            log.info("Repeatersleutel ingekort naar %s", kort)
            continue
        if rij["is_guest_polled"]:
            conn.execute("UPDATE repeaters SET is_guest_polled=1 WHERE id=?",
                         (bestaand["id"],))
        conn.execute("DELETE FROM repeaters WHERE id=?", (rij["id"],))
        log.info("Dubbele repeaterrij met lange sleutel verwijderd; %s bestond al",
                 kort)


def _backfill_from_raw(conn: sqlite3.Connection) -> None:
    """Fill decoder-derived columns on packets stored before those columns existed.

    The frame is kept in ``raw``, so this is a re-read of what was already there
    rather than an invention: the same bytes through the same decoder that new
    packets go through. Without it a new column would stay empty until the whole
    table had rolled over, and the list would show a week of dashes on rows whose
    answer is sitting right next to them.

    Self-limiting. Every row it touches gets a non-NULL src_hash (the empty
    string when the packet type carries none), so the second start finds nothing
    to do -- cheap enough at the once-per-process this runs. Rows older than the
    ``raw`` column keep NULLs forever, which is the honest answer for a packet
    whose bytes nobody kept.
    """
    rows = conn.execute(
        "SELECT id, raw FROM packets "
        "WHERE (scope IS NULL OR src_hash IS NULL) AND raw IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            pkt = packets.decode(bytes.fromhex(row["raw"]))
        except ValueError:
            continue        # stored hex that is not hex: nothing to re-read
        conn.execute(
            "UPDATE packets SET scope=COALESCE(?, scope), "
            "scope_codes=COALESCE(?, scope_codes), src_hash=?, dest_hash=? "
            "WHERE id=?",
            (pkt.get("scope"), _scope_codes(pkt),
             pkt.get("src_hash", ""), pkt.get("dest_hash", ""), row["id"]),
        )


def _scope_codes(pkt: dict) -> str | None:
    """The transport codes as they go into the ``scope_codes`` column."""
    codes = pkt.get("transport_codes")
    return ",".join(str(int(c)) for c in codes) if codes else None


# A flooded packet is repeated by every node in range, so the same observer
# hears the same payload several times within seconds. Collapsing those keeps
# the table and the live map readable without losing distinct traffic.
PACKET_DUP_WINDOW_S = 60


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _shorten_long_node_keys(_conn)
        _backfill_from_raw(_conn)
        _conn.commit()
    return _conn


def q(sql: str, params=()) -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(sql, params).fetchall()


def qone(sql: str, params=()) -> sqlite3.Row | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=()) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def execute_rowcount(sql: str, params=()) -> int:
    """Like execute(), but answers how many rows it touched.

    Its own function rather than a changed return value on execute(): every
    INSERT in this module relies on getting a lastrowid back. The retention
    sweep is the caller that needs the other number, because "we pruned" and
    "we pruned 40 000 packets" are different things to put on the admin page.
    """
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def get_setting(key: str, default: str | None = None) -> str | None:
    row = qone("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def setting_int(key: str, default: int) -> int:
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# --- de vertrouwensgrens: wat één ingest-bericht mag bevatten ----------------
#
# Alles hieronder gaat over één vraag: welke rijen in deze databank kan iemand
# anders laten ontstaan, en hoeveel.
#
# Het ``stats``-topic is geen invoer van de eigenaar. Iedereen met
# brokerreferenties publiceert eronder, en een node is een computertje op een
# dak dat ooit van iemand anders kan zijn. Drie tabellen groeien rechtstreeks
# uit wat zo'n bericht zegt -- één ``repeaters``-rij per verschillende sleutel,
# één ``latest``-rij per metrieknaam, één ``neighbors``-rij per burenprefix --
# en geen van drieën werd geteld of gecontroleerd.
#
# De opruiming redde dat niet. Het byteplafond in ``prune()`` verwijdert
# uitsluitend uit ``packets``, dus een opgeblazen ``latest`` zou het pakketten
# tot aan de FIFO-bodem laten wegsnoeien terwijl het bestand precies even groot
# bleef. Een schijfbewaking die op papier klopt en in de praktijk de verkeerde
# tabel leegt, is erger dan geen: ze stelt gerust.
#
# De regels hier zijn met opzet saai, en ze draaien vóór er iets geschreven
# wordt. Achteraf snoeien is opruimen; vooraf weigeren is de tabel niet laten
# groeien.
#
# Over de waarschijnlijkheid, want die hoort naast de code en niet alleen in het
# auditrapport: hiervoor is een node met geldige of gelekte brokerreferenties
# nodig, op een LAN achter een VPN, met een ACL per node en met Mosquitto's
# ``message_size_limit`` van 8192 byte die er een druppel van maakt in plaats
# van een stroom. Laag dus. De reden dat het toch dichtgaat, is dat dit precies
# het soort gat is waar een schijfbewaking op stukloopt zonder dat iemand het
# ziet.

_HEX_RE = re.compile(r"[0-9a-f]+")

# Een MeshCore-publieke sleutel is 32 byte. Bronnen zijn het oneens over hoeveel
# ervan ze meesturen -- Home Assistant vijf, de eigen firmware zes, een advert
# de hele sleutel -- dus 64 hextekens is de ruimste eerlijke sleutel, en twee
# het smalste dat überhaupt iets kan aanduiden.
MAX_KEY_HEX = 64
MIN_KEY_HEX = 2

# Plafonds op ÉÉN bericht. Een repeater meldt enkele tientallen cijfers plus één
# regel per buur die hij gehoord heeft, en de firmware begrenst zijn eigen
# contactentabel al (MAX_CONTACTS: standaard 100, ruimhartig 350). Beide getallen
# liggen ruim boven elke eerlijke node en ruim onder wat een tabel zou vullen.
MAX_METRICS_PER_MESSAGE = 128
MAX_NEIGHBORS_PER_MESSAGE = 512
MAX_METRIC_NAME = 64


# Hoe lang de sleutel van een NODE is. Zes bytes, twaalf hextekens, en dat is
# geen keuze van ons: de firmware bouwt zijn eigen identiteit met
# ``toHex(_node_hex, self_id.pub_key, 6)`` en publiceert onder die twaalf tekens.
# ``contacts`` volgde dat al met ``pk[:12]``, en dat getal stond daar los in de
# code.
#
# Deze constante en ``node_key`` hieronder bestaan omdat het NIET één plek was,
# en dat kostte drie kapotte rijen. De uitvraagpagina gaf de sleutel door die uit
# een advert komt -- de volle 32 bytes, vierenzestig tekens -- door ``key_prefix``,
# die op lengte alleen KEURT en niet inkort. Gevolg: rijen met een sleutel van 64
# waar de rest van het systeem er 12 verwacht, dus geen naamtreffer (de hex
# belandde in het naamveld) en geen uitvraging (de monitor adresseert op 12).
#
# Dat is dezelfde fout als de hoofdletters in de MQTT-topics, in een andere
# gedaante: één identiteit die op twee plaatsen een andere vorm heeft. Daar was
# het antwoord onthouden in plaats van berekenen; hier is het één functie in
# plaats van elke weg zijn eigen variant.
NODE_KEY_HEX = 12


def node_key(value) -> str:
    """De sleutel van een node in de enige vorm waarin hij bewaard mag worden.

    Kleine letters, hex, en afgekapt op ``NODE_KEY_HEX``. Geeft "" terug voor
    alles wat geen sleutel is, net als ``key_prefix``, zodat een beller met een
    zinnig alternatief dat kan gebruiken.

    Het verschil met ``key_prefix`` is precies de reden dat deze functie bestaat.
    ``key_prefix`` keurt een sleutelVOORVOEGSEL zoals het ergens langskomt -- in
    een topic, in de payload van een opdracht -- en daar zijn langere vormen
    legitiem; het kort dus niet in. ``node_key`` levert de IDENTITEIT van een rij,
    en die heeft één lengte. Wie een rij aanmaakt of opzoekt, hoort deze te
    gebruiken.
    """
    schoon = key_prefix(value)
    return schoon[:NODE_KEY_HEX] if schoon else ""


def key_prefix(value) -> str:
    """Een sleutelvoorvoegsel zoals het bewaard mag worden, of "" voor de rest.

    Kleine letters, hex, en een begrensde lengte. Meer niet. Dat "" wordt
    teruggegeven in plaats van een uitzondering is met opzet: sommige bellers
    hebben een zinnig alternatief voor "dat was geen sleutel", en alleen wie dat
    niet heeft hoort er een fout van te maken.

    Merk op dat dit niets zegt over het topicvoorvoegsel. Deze site luistert
    tijdens de hernoeming naar MeshManager op twee voorvoegsels tegelijk, en het
    voorvoegsel staat elders in het topic dan de sleutel; wat hier gecontroleerd
    wordt is uitsluitend het middenstuk ``<voorvoegsel>/<node_hex>/<soort>``.
    Beide voorvoegsels komen dus even ver.
    """
    p = str(value or "").lower().strip()
    return p if MIN_KEY_HEX <= len(p) <= MAX_KEY_HEX and _HEX_RE.fullmatch(p) else ""


def check_snapshot(pubkey_prefix, metrics, neighbors=None) -> str:
    """Keur één statistiekbericht. Geeft de schone sleutel terug, of werpt op.

    ValueError, met in de tekst welke regel geschonden is, want beide
    ingest-wegen maken daar al iets zichtbaars van: de MQTT-lus telt hem en zet
    hem met een fragment van de payload in het logboek, de HTTP-route antwoordt
    422. Een weigering die niemand kan lezen is maar een halve weigering.

    Aantallen worden op het niveau van het hele bericht geweigerd, losse regels
    niet. Tweehonderd metrieknamen in één bericht is geen repeater met een
    slechte dag maar iemand die aan het opsommen is; terwijl één misvormde
    burenregel tussen veertig goede een firmware-eigenaardigheid is, en de
    andere negenendertig weggooien om die te bestraffen kost meer dan het
    oplevert. Die vallen er één voor één uit in ``ingest()``, waar ze geteld
    worden -- niet hier.
    """
    key = key_prefix(pubkey_prefix)
    if not key:
        raise ValueError("repeater.pubkey_prefix is geen begrensde hexsleutel: "
                         f"{str(pubkey_prefix)[:32]!r}")
    if not isinstance(metrics, dict):
        raise ValueError("metrics moet een object zijn")
    if len(metrics) > MAX_METRICS_PER_MESSAGE:
        raise ValueError(f"{len(metrics)} metrieken in één bericht, "
                         f"hoogstens {MAX_METRICS_PER_MESSAGE} aanvaard")
    for name in metrics:
        if not isinstance(name, str) or not name or len(name) > MAX_METRIC_NAME:
            raise ValueError(f"onbruikbare metrieknaam: {str(name)[:40]!r}")
    if neighbors is not None:
        if not isinstance(neighbors, list):
            raise ValueError("neighbors moet een lijst zijn")
        if len(neighbors) > MAX_NEIGHBORS_PER_MESSAGE:
            raise ValueError(f"{len(neighbors)} buren in één bericht, "
                             f"hoogstens {MAX_NEIGHBORS_PER_MESSAGE} aanvaard")
    return key


def set_country(prefix6: str, lat, lon) -> None:
    """Work out which country a node sits in and store it on every row it owns.

    Keyed on prefix6, and applied with a single UPDATE across all rows sharing
    it, because one node can hold more than one contact row: Home Assistant sends
    five key bytes where a node's own firmware sends six, so the same node
    arrives under keys of different length. That is the trap _find_by_prefix
    exists for on the repeaters table. Matching on the literal key here would
    give one node two countries, or none.

    Called only when a position is written that differs from the stored one, so
    an ordinary advert -- which repeats a position we already have -- costs
    nothing. A node that never moves is classified exactly once.
    """
    if lat is None or lon is None or not countries.available():
        return
    execute("UPDATE contacts SET country=? WHERE prefix6=?",
            (countries.lookup(lat, lon), prefix6.lower()))


def _position_changed(prev, lat, lon) -> bool:
    """True when this position is new information about where a node is."""
    return prev is None or prev["lat"] != lat or prev["lon"] != lon


def upsert_contacts(contacts: list[dict]) -> int:
    """Refresh contact positions (advert data); returns how many were stored."""
    now = utcnow()
    n = 0
    moved = []
    with _lock:
        conn = get_conn()
        for c in contacts:
            prefix = str(c.get("prefix", "")).lower().strip()
            lat, lon = c.get("lat"), c.get("lon")
            if len(prefix) < 6 or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            lat, lon = float(lat), float(lon)
            prev = conn.execute(
                "SELECT lat, lon FROM contacts WHERE prefix6=? LIMIT 1", (prefix[:6],)
            ).fetchone()
            conn.execute(
                "INSERT INTO contacts(prefix, prefix6, name, lat, lon, node_type, updated) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(prefix) DO UPDATE SET "
                "name=COALESCE(excluded.name, name), lat=excluded.lat, lon=excluded.lon, "
                "node_type=COALESCE(excluded.node_type, node_type), updated=excluded.updated",
                (prefix, prefix[:6], c.get("name"), lat, lon, c.get("type"), now),
            )
            n += 1
            if _position_changed(prev, lat, lon):
                moved.append((prefix[:6], lat, lon))
        conn.commit()
    # Outside the lock: set_country takes it itself, and threading.Lock is not
    # reentrant.
    for prefix6, lat, lon in moved:
        set_country(prefix6, lat, lon)
    return n


def contact_location(prefix6: str):
    """Position of a contact, or None. Adverts may register a node by name
    before it ever reports coordinates, so rows without a position exist and
    must not be handed to callers that are about to plot them.

    Uit ``visible_contacts``, dus een gevolgde node met "positie tonen" uit
    komt hier als None terug -- dezelfde uitkomst als een node waarvan we nooit
    coördinaten hoorden, en daarmee dezelfde afhandeling bij elke aanroeper.
    """
    return qone(
        "SELECT * FROM visible_contacts "
        "WHERE prefix6=? AND lat IS NOT NULL AND lon IS NOT NULL",
        (prefix6.lower(),),
    )


def withheld_position_prefixes() -> set[str]:
    """De sleutelprefixen van gevolgde nodes die hun positie niet tonen.

    Niet voor de handhaving -- die zit in ``visible_contacts`` en werkt zonder
    dat een aanroeper eraan hoeft te denken -- maar om er eerlijk over te
    kunnen zijn. Een kaart die een node weglaat hoort te kunnen zeggen dat er
    iets weggelaten is, en het verschil tussen "nooit een positie gehoord" en
    "deze node toont zijn positie niet" is precies wat een bezoeker anders zelf
    zit te raden.
    """
    return {r["p6"] for r in q(
        "SELECT substr(pubkey_prefix, 1, 6) AS p6 FROM repeaters WHERE show_position = 0"
    )}


def address_hash_name(pubkey_prefix: str) -> str:
    """De adreshash zoals de site die al print voor een afzender zonder naam.

    Eén byte, in de vorm 0x92: zie static/app.js, waar een hop die op geen
    enkele bekende node past precies zo op het scherm komt. Een verborgen naam
    krijgt dezelfde weergave in plaats van een eigen vondst, want een bezoeker
    hoort niet te hoeven leren dat er twee soorten naamloos zijn.
    """
    return "0x" + (pubkey_prefix or "")[:2].upper()


def public_name(row) -> str:
    """De naam van een gevolgde repeater zoals een bezoeker die te zien krijgt.

    Voor elke plek waar ``repeaters.name`` rechtstreeks naar buiten gaat --
    ``visible_contacts`` kan daar niets aan doen, want die naam komt niet uit
    de contactentabel maar uit de repeaterrij zelf.
    """
    if _field(row, "show_name") == 0:
        return address_hash_name(_field(row, "pubkey_prefix") or "")
    return row["name"]


def upsert_advert(pubkey: str, name: str | None = None, lat: float | None = None,
                  lon: float | None = None, node_type: str | None = None) -> None:
    """Record the identity carried by an advert in the shared contacts table.

    Adverts arrive far more often than they change, and a node may advertise its
    name without a position (or the other way round), so every field is only
    overwritten when the advert actually carries it -- otherwise a nameless
    advert would erase a known name.
    """
    pk = (pubkey or "").lower().strip()
    if len(pk) < 6:
        return
    prefix6 = pk[:6]
    # Contacts pushed by Home Assistant use a shorter pubkey prefix than the
    # 32-byte key in an advert. Reuse the existing row's key so both sources
    # keep converging on one row per node instead of two that shadow each other.
    row = qone("SELECT prefix, lat, lon FROM contacts WHERE prefix6=?", (prefix6,))
    prefix = row["prefix"] if row else pk[:NODE_KEY_HEX]
    if lat is None or lon is None:
        lat = lon = None
    execute(
        "INSERT INTO contacts(prefix, prefix6, name, lat, lon, node_type, updated) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(prefix) DO UPDATE SET "
        "name=COALESCE(excluded.name, name), "
        "lat=COALESCE(excluded.lat, lat), lon=COALESCE(excluded.lon, lon), "
        "node_type=COALESCE(excluded.node_type, node_type), updated=excluded.updated",
        (prefix, prefix6, name, lat, lon, node_type, utcnow()),
    )
    # A positionless advert keeps whatever position we already had (COALESCE
    # above), so the effective position -- not the advert's own -- decides
    # whether anything needs classifying.
    now_lat = lat if lat is not None else (row["lat"] if row else None)
    now_lon = lon if lon is not None else (row["lon"] if row else None)
    if _position_changed(row, now_lat, now_lon):
        set_country(prefix6, now_lat, now_lon)


# A MeshCore frame is at most 255 bytes, so 510 hex characters plus slack is
# already generous; the cap only exists so a nonsense payload cannot store a
# megabyte per row.
MAX_RAW_HEX_STORED = 600


def insert_packet(observer: str, pkt: dict, snr=None, rssi=None,
                  length: int | None = None, ts: str | None = None,
                  raw: str | None = None, fwd: str | None = None,
                  fwd_reason: str | None = None) -> int | None:
    """Store one packet reception. Returns the row id, or None if skipped.

    ``pkt`` is the dict from packets.decode(); ``raw`` the hex frame it was
    decoded from. An advert also refreshes the contacts table, which is what
    later lets the live map place a packet.

    ``fwd`` is wat het pakketfilter van de waarnemende node met dit pakket deed
    ('geweerd', 'doorgelaten', of None voor 'niet beoordeeld'). Het komt uit het
    rx-bericht zelf en niet uit de bytes: de node zet zijn oordeel bij het pakket
    voordat het de deur uitgaat. Zie meshmanager_on_forward_verdict() in de
    firmware voor waarom het daar past en niet in een tweede bericht.
    """
    observer = str(observer or "").lower().strip()[:16]
    if not observer:
        return None
    ts = ts or utcnow()
    phash = pkt.get("hash")

    if phash:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=PACKET_DUP_WINDOW_S)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if qone("SELECT 1 FROM packets WHERE observer=? AND phash=? AND ts>=? LIMIT 1",
                (observer, phash, cutoff)):
            return None

    if pkt.get("pubkey"):
        upsert_advert(pkt["pubkey"], pkt.get("name"), pkt.get("lat"), pkt.get("lon"),
                      pkt.get("node_type"))

    raw_hex = str(raw or "").strip().lower()[:MAX_RAW_HEX_STORED] or None
    return execute(
        "INSERT INTO packets(ts, observer, snr, rssi, len, route, payload_type, "
        "payload_name, path_len, sender, phash, path, raw, scope, scope_codes, "
        "src_hash, dest_hash, fwd, fwd_reason) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ts, observer,
         float(snr) if isinstance(snr, (int, float)) else None,
         float(rssi) if isinstance(rssi, (int, float)) else None,
         int(length) if isinstance(length, int) else pkt.get("len"),
         pkt.get("route_name"), pkt.get("payload_type"), pkt.get("payload_name"),
         pkt.get("path_len"), pkt.get("sender"), phash,
         ",".join(pkt.get("path") or []) or None, raw_hex,
         pkt.get("scope"), _scope_codes(pkt),
         pkt.get("src_hash", ""), pkt.get("dest_hash", ""),
         fwd or None, fwd_reason or None),
    )


def recent_packets(since_id: int = 0, limit: int = 200) -> list[sqlite3.Row]:
    """Packets for the live feed, ascending by id, with the sender's name and
    position joined in so the caller can plot them without a second query.

    Two regimes behind one ascending contract. With a positive ``since_id``
    this is the incremental tail: everything newer than that id, oldest first,
    so the poller appends in arrival order. The opening call (since_id=0)
    instead returns the NEWEST ``limit`` packets -- also handed back oldest
    first, so the caller never sees a second ordering. It used to return the
    oldest stored packets there ("everything after id 0"), which made a
    refreshed page open on traffic from hours ago and crawl towards now one
    page per poll; a first look at a live feed should show what is happening,
    not what happened first.
    """
    # GROUP BY p.id keeps one row per packet: contacts is keyed on the full
    # pubkey prefix, and two sources (adverts, Home Assistant) can register the
    # same node under prefixes of different length, which would otherwise
    # multiply every packet by the number of matching contact rows.
    select = (
        "SELECT p.*, c.name AS sender_name, c.lat AS sender_lat, c.lon AS sender_lon, "
        "c.country AS sender_country, "
        "o.name AS observer_name, o.lat AS observer_lat, o.lon AS observer_lon, "
        "o.country AS observer_country "
        "FROM packets p "
        "LEFT JOIN visible_contacts c ON c.prefix6 = p.sender "
        "LEFT JOIN visible_contacts o ON o.prefix6 = substr(p.observer, 1, 6) "
    )
    if since_id > 0:
        return q(select + "WHERE p.id > ? GROUP BY p.id ORDER BY p.id LIMIT ?",
                 (since_id, limit))
    # Fetched descending so LIMIT keeps the fresh end, then flipped in Python
    # rather than through a nested SELECT ordered twice: SQL would express the
    # same thing in more machinery, and reversing at most ``limit`` rows that
    # are already in memory costs nothing.
    rows = q(select + "GROUP BY p.id ORDER BY p.id DESC LIMIT ?", (limit,))
    rows.reverse()
    return rows


def packets_with_paths(since: str, limit: int = 20000) -> list[sqlite3.Row]:
    """Packets since ``since``, reduced to what the heat map aggregation needs.

    A lean cousin of recent_packets: no raw frame, no radio figures, no
    countries -- the aggregation only places stops along each path. Newest
    first, so when the cap bites it is the oldest packets that fall off the
    heat map rather than the freshest. Same GROUP BY p.id as recent_packets,
    for the same reason: two sources can register one node under prefixes of
    different length.
    """
    return q(
        "SELECT p.sender, p.path, c.name AS sender_name, "
        "c.lat AS sender_lat, c.lon AS sender_lon, "
        "substr(p.observer, 1, 6) AS observer6, o.name AS observer_name, "
        "o.lat AS observer_lat, o.lon AS observer_lon "
        "FROM packets p "
        "LEFT JOIN visible_contacts c ON c.prefix6 = p.sender "
        "LEFT JOIN visible_contacts o ON o.prefix6 = substr(p.observer, 1, 6) "
        "WHERE p.ts >= ? GROUP BY p.id ORDER BY p.id DESC LIMIT ?",
        (since, limit),
    )


def packet_by_id(packet_id: int) -> sqlite3.Row | None:
    """One packet with its sender and observer contact rows joined in.

    Same GROUP BY as recent_packets, and for the same reason: two sources can
    register one node under prefixes of different length, and without it a single
    packet comes back once per matching contact row.
    """
    return qone(
        "SELECT p.*, c.name AS sender_name, c.lat AS sender_lat, c.lon AS sender_lon, "
        "c.country AS sender_country, "
        "o.name AS observer_name, o.lat AS observer_lat, o.lon AS observer_lon, "
        "o.country AS observer_country "
        "FROM packets p "
        "LEFT JOIN visible_contacts c ON c.prefix6 = p.sender "
        "LEFT JOIN visible_contacts o ON o.prefix6 = substr(p.observer, 1, 6) "
        "WHERE p.id = ? GROUP BY p.id",
        (packet_id,),
    )


def contacts_by_key_prefix(key_prefix: str) -> list[sqlite3.Row]:
    """Every known node whose public key starts with these hex characters.

    Returns a list, never a single row, because a path hop identifies a node by
    only its first one or two key bytes -- so several nodes can legitimately
    answer to the same hop. Callers must present that as the ambiguity it is.

    ``updated`` rides along because the candidate weighing (app/candidates.py)
    falls back to it for recency when this observer has never heard the node's
    advert itself -- a contact pushed by Home Assistant has a date but no
    reception.
    """
    h = (key_prefix or "").lower().strip()
    if not h or len(h) > 6 or not re.fullmatch(r"[0-9a-f]+", h):
        return []
    return q(
        "SELECT prefix6, name, lat, lon, node_type, MAX(updated) AS updated "
        "FROM visible_contacts "
        "WHERE substr(prefix6, 1, ?) = ? GROUP BY prefix6 ORDER BY prefix6",
        (len(h), h),
    )


def observer_receptions(observer: str) -> dict[str, dict]:
    """Which nodes this observer has really heard, and how close they came.

    Keyed on the node's 6-hex key prefix: ``{"hops": int, "seen": iso}``, where
    ``hops`` is the fewest hops any of its adverts had travelled when this
    observer picked it up, and ``seen`` the most recent one.

    Only adverts can answer this, and that is the point: an advert is the one
    payload that names its sender by full key prefix, so every row here is a
    measurement rather than a resolution of some ambiguous byte. Feeding
    ambiguous data into the thing that resolves ambiguity would be circular.

    Only floods count. On a FLOOD ``path_len`` is the route already travelled,
    which is the number wanted; on a DIRECT it is the route still to go, and
    mixing the two in one MIN() would report a node as a neighbour on the
    strength of a packet that was merely nearly finished (docs/protocol.md 1.4).

    A full scan of the packets table, which packet retention keeps to a week --
    a few thousand rows. Callers cache it; see routes_api.
    """
    return {
        r["prefix6"]: {"hops": r["hops"], "seen": r["seen"]}
        for r in q(
            "SELECT sender AS prefix6, MIN(path_len) AS hops, MAX(ts) AS seen "
            "FROM packets WHERE observer = ? AND sender IS NOT NULL "
            "AND route LIKE '%FLOOD' AND path_len IS NOT NULL "
            "GROUP BY sender",
            (observer,),
        )
    }


# The archive page asks three questions about one query -- the rows, the total,
# and the shape over time -- and a fourth per field it breaks down. They share
# this FROM clause, so the joins the search fields assume live in one place.
# search.FIELDS refers to these aliases by name; keep the two in step.
_SEARCH_FROM = (
    "FROM packets p "
    "LEFT JOIN visible_contacts c ON c.prefix6 = p.sender "
    "LEFT JOIN visible_contacts o ON o.prefix6 = substr(p.observer, 1, 6) "
)


def _search_where(query, since: str, until: str) -> tuple[str, list]:
    """The WHERE for a parsed query inside a time window."""
    sql = "WHERE p.ts >= ? AND p.ts <= ?"
    params: list = [since, until]
    if query.sql:
        sql += f" AND ({query.sql})"
        params.extend(query.params)
    return sql, params


def search_packets(query, since: str, until: str, limit: int = 100,
                   offset: int = 0, sort=None) -> list[sqlite3.Row]:
    """One page of matching packets, newest first unless asked otherwise.

    Newest first, unlike the live feed: the archive is read by someone looking
    for something that already happened, and the most recent match is the one
    they most often mean.

    ``sort`` is a search.Sort, whose ``sql`` is built from that module's own
    table of columns -- never from anything a visitor typed. It is interpolated
    rather than bound because a placeholder cannot stand in for a column name;
    see the Sort class for why the fixed table is the defence rather than an
    escaping routine. Left out, the ORDER BY is the literal below, which is the
    same thing search.parse_sort("") produces; the two are spelled out
    separately only so that this module keeps working without the other one.

    The ordering deliberately reaches no further than this query. The total, the
    histogram and the facets answer questions about the whole result set, and
    what a set contains does not change with the order it is listed in -- so they
    are neither re-run nor re-sorted when the reader clicks a heading.

    No index was added for the new orderings, and measurement is the reason. The
    two LEFT JOINs and the GROUP BY already force this query onto a temporary
    B-tree for its ORDER BY, even for the default order on the indexed ts column
    -- so an index on path_len or snr could not be used here at all. Measured on
    50 000 packets (about seven times a busy week) one page costs 43 to 70 ms
    whichever column it is sorted by, against 52 ms for the order that was
    already there. Indexes on four more columns would slow every insert on the
    ingest path down for a difference that does not exist.
    """
    where, params = _search_where(query, since, until)
    order = sort.sql if sort is not None else "p.ts DESC, p.id DESC"
    return q(
        "SELECT p.*, c.name AS sender_name, c.lat AS sender_lat, c.lon AS sender_lon, "
        "c.country AS sender_country, o.name AS observer_name, "
        "o.country AS observer_country "
        f"{_SEARCH_FROM}{where} GROUP BY p.id ORDER BY {order} "
        "LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )


def count_packets(query, since: str, until: str) -> int:
    """How many packets match, in total.

    Counted rather than inferred from the page: "1-100 of many" is the kind of
    half-answer that makes a search box untrustworthy, and over one week of
    packets on one SQLite file this is a cheap query.
    """
    where, params = _search_where(query, since, until)
    row = qone(f"SELECT COUNT(DISTINCT p.id) AS n {_SEARCH_FROM}{where}", tuple(params))
    return (row["n"] or 0) if row else 0


def packet_histogram(query, since: str, until: str, bucket_s: int) -> list[dict]:
    """Match counts per time bucket, for the bar chart above the results.

    Bucketed in SQL on the epoch second: pulling every matching timestamp into
    Python to group it there would mean transferring the whole result set to draw
    sixty bars.
    """
    where, params = _search_where(query, since, until)
    rows = q(
        f"SELECT CAST(strftime('%s', p.ts) AS INTEGER) / {int(bucket_s)} AS b, "
        f"COUNT(DISTINCT p.id) AS n {_SEARCH_FROM}{where} GROUP BY b ORDER BY b",
        tuple(params),
    )
    return [{"t": r["b"] * int(bucket_s), "n": r["n"]} for r in rows]


def packet_facets(query, since: str, until: str, column: str,
                  limit: int = 8) -> list[dict]:
    """The most common values of one field among the matches.

    ``column`` is a SQL expression from search.FIELDS, never anything a visitor
    typed -- the field name is looked up in that table first, so an unknown one
    never reaches here.
    """
    where, params = _search_where(query, since, until)
    rows = q(
        f"SELECT {column} AS v, COUNT(DISTINCT p.id) AS n {_SEARCH_FROM}{where} "
        f"AND {column} IS NOT NULL AND {column} != '' "
        "GROUP BY v ORDER BY n DESC, v LIMIT ?",
        (*params, limit),
    )
    return [{"value": str(r["v"]), "count": r["n"]} for r in rows]


def packet_span() -> dict:
    """Oldest and newest packet held, so the page can bound its time picker."""
    row = qone("SELECT MIN(ts) AS lo, MAX(ts) AS hi, COUNT(*) AS n FROM packets")
    if not row or not row["hi"]:
        return {"oldest": None, "newest": None, "total": 0}
    return {"oldest": row["lo"], "newest": row["hi"], "total": row["n"] or 0}


def last_packet_id() -> int:
    row = qone("SELECT MAX(id) AS id FROM packets")
    return (row["id"] or 0) if row else 0


def located_nodes() -> list[sqlite3.Row]:
    """Every node we know a position for; the base layer of the live map."""
    return q(
        "SELECT prefix6, name, lat, lon, node_type, country FROM visible_contacts "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL GROUP BY prefix6"
    )


# --- everything known about one node ----------------------------------------
# The dots on the live map come from located_nodes() above; clicking one of them
# asks the questions below. They are all aggregations over ``packets``, which
# holds a full retention window of receptions -- thousands of rows today, and
# the mesh is only going to get noisier -- so each of them is a GROUP BY in
# SQLite rather than a fetch followed by a loop in Python. Counting a week of
# receptions in the process would mean shipping the whole window over the
# sqlite3 boundary to produce a dozen numbers, once per opened node.
#
# Everything here is keyed on the six-hex ``prefix6``, because that is the key
# every other table already agrees on: contacts.prefix6, packets.sender, the
# first six characters of packets.observer, and neighbors.prefix. The longer
# ``contacts.prefix`` is deliberately not the key -- one node can hold several
# contact rows under keys of different length (Home Assistant sends five bytes
# where a node's own firmware sends six), and keying on the literal would split
# one node's history in two. See set_country for the same trap.


def node_contacts(prefix6: str) -> list[sqlite3.Row]:
    """Every contact row this node owns, longest key first.

    A list rather than a row for exactly the reason above: two sources can have
    registered the same node under keys of different length. The caller merges
    them; the order puts the least ambiguous key first.
    """
    return q(
        "SELECT prefix, prefix6, name, lat, lon, node_type, country, updated "
        "FROM visible_contacts WHERE prefix6=? ORDER BY length(prefix) DESC, updated DESC",
        (prefix6.lower(),),
    )


def contact_name_for(key: str) -> str:
    """De bekende naam van een node, of "" als er geen is.

    Uit ``contacts``, want daar staan de namen die uit adverts komen -- ook van
    nodes die hier geen repeaterrij hebben. De opzoeking loopt op ``prefix6``
    omdat dat de kolom is waarop de tabel geïndexeerd is en waarop dezelfde node
    onder verschillende sleutellengtes samenvalt; de langste sleutel eerst, want
    die is het minst dubbelzinnig.

    Bestaat omdat de uitvraagpagina de hex zelf als naam gebruikte toen er geen
    treffer was. De namen waren wél bekend -- ze stonden in deze tabel -- en het
    is niet aan een nieuwe weg om zijn eigen naamloosheid mee te brengen.
    """
    p6 = (key_prefix(key) or "")[:6]
    if len(p6) < 6:
        return ""
    rij = qone("SELECT name FROM contacts WHERE prefix6=? "
               "ORDER BY length(prefix) DESC, updated DESC", (p6,))
    return (rij["name"] or "") if rij else ""


def node_sent_by_observer(prefix6: str) -> list[sqlite3.Row]:
    """Per observer: how much of this node's own traffic it heard, and how well.

    ``sender`` is only ever filled from an advert -- the one payload that names
    its origin by a full key prefix -- so this counts the traffic that is
    provably this node's, and nothing else it may have sent. The endpoint says
    so rather than presenting the total as "all its packets".

    Hop counts are taken from FLOOD packets only. On a FLOOD ``path_len`` is the
    route already travelled, which is the distance from this node to that
    observer; on a DIRECT it is the route still to go, and averaging the two
    together would report a node as a near neighbour on the strength of a packet
    that was merely nearly finished (docs/protocol.md 1.4). observer_receptions
    draws the same line for the same reason.

    The observer's name comes from a correlated subquery, not a LEFT JOIN.
    Joining contacts would multiply every counted row by the number of contact
    rows that observer happens to own, and the counts in this very function are
    what would silently double -- the trap recent_packets pays a GROUP BY p.id
    to avoid.
    """
    return q(
        "SELECT p.observer, substr(p.observer, 1, 6) AS observer6, "
        "(SELECT c.name FROM visible_contacts c "
        " WHERE c.prefix6 = substr(p.observer, 1, 6) AND c.name IS NOT NULL "
        " LIMIT 1) AS observer_name, "
        "COUNT(*) AS n, MIN(p.ts) AS first_ts, MAX(p.ts) AS last_ts, "
        "AVG(p.snr) AS snr_avg, MAX(p.snr) AS snr_best, "
        "AVG(p.rssi) AS rssi_avg, MAX(p.rssi) AS rssi_best, "
        "MIN(CASE WHEN p.route LIKE '%FLOOD' THEN p.path_len END) AS hops_min, "
        "AVG(CASE WHEN p.route LIKE '%FLOOD' THEN p.path_len END) AS hops_avg "
        "FROM packets p WHERE p.sender = ? "
        "GROUP BY p.observer ORDER BY n DESC",
        (prefix6.lower(),),
    )


def node_sent_breakdown(prefix6: str) -> list[sqlite3.Row]:
    """This node's own traffic split by payload type and by scope, in one pass.

    One query for two breakdowns: the pair (type, scope) has a handful of
    distinct values on any real mesh, so the caller can total each axis over a
    result of ten-ish rows. Two separate GROUP BYs would read the same rows
    twice to answer half a question each.
    """
    return q(
        "SELECT payload_name, scope, COUNT(*) AS n FROM packets "
        "WHERE sender = ? GROUP BY payload_name, scope ORDER BY n DESC",
        (prefix6.lower(),),
    )


def node_reception_summary(prefix6: str) -> sqlite3.Row | None:
    """What this node heard, when it is itself an observer feeding this site.

    Written as a range on ``observer`` rather than substr(observer, 1, 6) = ?
    so idx_packets_dup can serve it: ``observer`` holds a key prefix of unknown
    length, and hex runs 0-9a-f, so 'g' is the first string that sorts after
    every key starting with these six characters.
    """
    p6 = prefix6.lower()
    return qone(
        "SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts, "
        "COUNT(DISTINCT sender) AS senders "
        "FROM packets WHERE observer >= ? AND observer < ?",
        (p6, p6 + "g"),
    )


def node_hop_appearances(prefix6: str) -> sqlite3.Row | None:
    """How often this node's key prefix turns up as a hop in someone else's path.

    Honest only as a ceiling, and the endpoint labels it as one. A path entry is
    1, 2 or 3 bytes of a public key -- the originating node chooses which, see
    path_hash_size -- so the three widths are all tried, and the shortest of
    them names one byte, which several hundred known nodes cannot help sharing.
    node_hash_siblings() below says how crowded that byte is, so the panel can
    print the ambiguity next to the number instead of behind it.

    A full scan, unavoidably: the hop list is one comma-separated column, and
    the match is on a member of it, which no index can answer. It is bounded by
    the packet retention (a week) and runs once per opened node, which is the
    other half of why this is acceptable where a per-packet version would not
    be. Splitting ``path`` into its own table was considered and rejected: it
    would carry an insert per hop on every reception -- the hot path -- to speed
    up a click.

    The commas around both sides make it a whole-entry match: without them the
    hop '2a' would also match the entry '2ae7'. Hex needs no LIKE escaping,
    since neither % nor _ can occur in it.
    """
    p6 = prefix6.lower()
    return qone(
        "SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
        "FROM packets WHERE path IS NOT NULL AND ("
        "  ',' || path || ',' LIKE '%,' || ? || ',%'"
        "  OR ',' || path || ',' LIKE '%,' || ? || ',%'"
        "  OR ',' || path || ',' LIKE '%,' || ? || ',%')",
        (p6[:2], p6[:4], p6),
    )


def node_hash_siblings(prefix6: str) -> int:
    """How many known nodes share this node's first key byte.

    The measure of how much a one-byte hop hash is worth. Counted over contacts
    rather than guessed from 256: what matters is how many nodes this site could
    actually confuse with each other, not how many the byte could theoretically
    address.

    Bewust over ``contacts`` en niet over ``visible_contacts``: dit getal is een
    maat voor onzekerheid, geen identiteit. Een node die zijn positie of naam
    verbergt bezet nog steeds zijn sleutelbyte, en hem hier niet meetellen zou
    de dubbelzinnigheid kleiner laten lijken dan ze is -- het enige antwoord dat
    hier gevaarlijk zou zijn. Er komt geen naam en geen coördinaat uit; alleen
    een aantal.
    """
    row = qone(
        "SELECT COUNT(DISTINCT prefix6) AS n FROM contacts "
        "WHERE substr(prefix6, 1, 2) = ?",
        (prefix6.lower()[:2],),
    )
    return (row["n"] or 0) if row else 0


def node_heard_by_repeaters(prefix6: str) -> list[sqlite3.Row]:
    """The tracked repeaters that list this node as a neighbour, best link first.

    The one relation in this whole panel that is a measurement by a node rather
    than an inference by this site: the repeater put the entry in its own
    neighbour table, key and SNR included.
    """
    return q(
        # show_name en pubkey_prefix rijden mee zodat public_name() de naam van
        # een repeater met verborgen naam kan vervangen; de rij gaat verder
        # ongewijzigd naar de aanroeper.
        "SELECT r.slug, r.name, r.show_name, r.pubkey_prefix, n.snr, n.last_seen "
        "FROM neighbors n "
        "JOIN repeaters r ON r.id = n.repeater_id "
        "WHERE n.prefix = ? AND r.is_public = 1 "
        "ORDER BY n.snr DESC",
        (prefix6.lower(),),
    )


def node_neighbors(repeater_id: int, limit: int) -> list[sqlite3.Row]:
    """One tracked repeater's own neighbour list, best link first.

    Capped by the caller: a busy repeater lists dozens, and a side panel is not
    the repeater's own page -- which shows the full table, with history per
    link, and is one click away.
    """
    return q(
        f"SELECT n.prefix, n.snr, n.last_seen, {NEIGHBOR_NAME_SQL} "
        f"{NEIGHBOR_FROM}"
        "WHERE n.repeater_id = ? GROUP BY n.prefix ORDER BY n.snr DESC LIMIT ?",
        (repeater_id, limit),
    )


def neighbor_rows(repeater_id: int) -> list[sqlite3.Row]:
    """De volledige burenlijst van één gevolgde repeater, sterkste link eerst.

    Eén functie voor de drie plaatsen die deze lijst opvroegen -- de publieke
    repeaterpagina, ``/repeaters/{slug}`` en ``/repeaters/{slug}/map`` -- want ze
    deelden ook dezelfde valkuil: de naam van een buur staat in twee tabellen
    (``neighbors.name`` wint van ``contacts.name``) en de zichtbaarheidskeuze in
    een derde. Drie kopieën van die uitdrukking is er één te veel om gelijk te
    houden, en gelijk houden is hier het hele punt.
    """
    return q(
        f"SELECT n.prefix, n.snr, n.last_seen, {NEIGHBOR_NAME_SQL} "
        f"{NEIGHBOR_FROM}"
        "WHERE n.repeater_id = ? GROUP BY n.prefix ORDER BY n.snr DESC",
        (repeater_id,),
    )


def public_repeater_by_prefix6(prefix6: str) -> sqlite3.Row | None:
    """The public repeater whose key starts with these six hex characters.

    Not find_repeater(): that one refuses to treat a short key as a shortening
    of a longer one below MIN_PREFIX_MATCH characters, and six is below it --
    rightly, because it is asked to decide whether two *identities* are the same
    node. Here the six characters come off the map's own node layer, which is
    keyed on prefix6 throughout, so the question is only "does a repeater sit at
    this dot". Public only, like every other route in the public API.
    """
    return qone(
        "SELECT * FROM repeaters WHERE substr(pubkey_prefix, 1, 6) = ? "
        "AND is_public = 1 ORDER BY length(pubkey_prefix) DESC LIMIT 1",
        (prefix6.lower(),),
    )


def oldest_packet_ts() -> str | None:
    """When the oldest retained packet was heard.

    What turns "over the last 7 days" into something a reader can check: on a
    server that started yesterday the retention window is a promise, not a
    period, and quoting the configured number alone would overstate what the
    figures cover.
    """
    row = qone("SELECT MIN(ts) AS ts FROM packets")
    return row["ts"] if row else None


def known_countries() -> list[str]:
    """Countries actually represented on the map, for the filter's choices.

    Only countries we have placed a node in: offering a visitor a filter that can
    only ever return nothing is worse than not offering it.
    """
    return [r["country"] for r in q(
        "SELECT country FROM visible_contacts WHERE country IS NOT NULL "
        "AND lat IS NOT NULL AND lon IS NOT NULL "
        "GROUP BY country ORDER BY country"
    )]


def classify_countries(force: bool = False) -> int:
    """Give every located contact a country. Returns how many rows changed.

    Existing databases were filled long before this column existed, and a node
    that never moves would otherwise never be classified. Run at startup, so the
    first request after a deploy already has countries; ``force`` recomputes
    everything, which is what to use after rebuilding borders.json.
    """
    if not countries.available():
        return 0
    rows = q("SELECT prefix6, lat, lon FROM contacts "
             "WHERE lat IS NOT NULL AND lon IS NOT NULL"
             + ("" if force else " AND country IS NULL") + " GROUP BY prefix6")
    for r in rows:
        set_country(r["prefix6"], r["lat"], r["lon"])
    return len(rows)


# 'cmd:' prefix = literal CLI command (not prefixed with 'get ')
# This list steers the polling path (Home Assistant) only; a node reading its
# own CLI works from its own table (SET_PARAMS in the firmware) and never sees
# this one. Keep the two in step, or a parameter will exist for one kind of node
# and be missing for the other.
DEFAULT_CLI_PARAMS = ("name,role,radio,freq,tx,af,repeat,advert.interval,"
                      "flood.advert.interval,flood.max,flood.max.unscoped,"
                      "allow.read.only,rxdelay,txdelay,lat,lon,cmd:region")


def request_settings(prefix: str, params: list[str]) -> None:
    """Queue a CLI settings request for a polling client (Home Assistant today).

    The second route, not the first. A node that publishes over MQTT is asked
    directly (see mqtt_ingest.publish_command); this queue is for repeaters that
    only something else can reach. Callers should only fill it when a poller has
    actually been seen, or it collects requests nobody will ever collect.
    """
    import json
    try:
        d = json.loads(get_setting("settings_requests", "{}"))
    except ValueError:
        d = {}
    d[prefix] = {"ts": utcnow(), "params": params}
    set_setting("settings_requests", json.dumps(d))


def pop_settings_requests() -> list[dict]:
    """Hand every queued look-up to the caller and clear the queue.

    Writes down per key that it was handed out, because that is the moment the
    only record of the request disappears. A poller that takes a request and
    then achieves nothing -- because it lost its own upstream, or never had a
    repeater password -- leaves the page in exactly the state it was in before
    the click, with no way to tell that from "nobody ever collected it". The
    handover timestamp plus the age of the stored values is what separates the
    two.
    """
    import json
    try:
        d = json.loads(get_setting("settings_requests", "{}"))
    except ValueError:
        d = {}
    if d:
        set_setting("settings_requests", "{}")
        try:
            handed = json.loads(get_setting("settings_delivered", "{}"))
        except ValueError:
            handed = {}
        now = utcnow()
        for prefix in d:
            handed[prefix] = now
        # Bounded, because this one is not clear-on-read: keep the newest keys.
        if len(handed) > 200:
            handed = dict(sorted(handed.items(), key=lambda kv: kv[1])[-200:])
        set_setting("settings_delivered", json.dumps(handed))
    return [{"prefix": p, "params": v.get("params", [])} for p, v in d.items()]


def settings_delivered_at(prefix: str) -> str | None:
    """When a look-up for this key was last handed to a poller."""
    import json
    try:
        d = json.loads(get_setting("settings_delivered", "{}"))
    except ValueError:
        return None
    value = d.get(prefix)
    return value if isinstance(value, str) else None


def pending_settings_request(prefix: str) -> str | None:
    """When a queued settings request for this key was placed, if any.

    The queue is clear-on-read, so this answers a question the admin page could
    otherwise not answer at all: a request that is still here means nothing has
    polled since the button was pressed, and one that is gone means the poller
    took it and the silence that follows is its own. Without the distinction
    both look identical -- a page that says "look-up started" and never changes.

    Paired with poller_last_seen(), which tells the third case apart: nothing
    was ever going to come and collect it.
    """
    import json
    try:
        d = json.loads(get_setting("settings_requests", "{}"))
    except ValueError:
        return None
    entry = d.get(prefix)
    return entry.get("ts") if isinstance(entry, dict) else None


# --- de filterstand van een repeater ------------------------------------------
#
# Een eigen tabel met een JSON-blok erin, en niet als rijen in ``repeater_cli``.
# Dat is de enige plek in dit bestand waar bewust van de sleutel/waarde-vorm
# afgeweken wordt, dus de reden hoort erbij.
#
# ``repeater_cli`` bewaart wat de CLI van een node antwoordt op ``get <naam>``:
# één regel tekst per sleutel, en de vergelijkingstabel zet die naast elkaar. Een
# filterstand is drie tabellen (hoplimiet, snelheidslimiet en aan/uit per
# pakkettype) plus een lijst geblokkeerde kanalen plus zes tellers. Dat in rijen
# persen levert sleutels op als ``filter.rate.05.limit`` -- zeventig rijen per
# node waar de site er nooit één los van bevraagt, en een kolomkiezer die
# onbruikbaar wordt omdat er zeventig kolommen bij komen die niemand wil.
#
# Waarom dan niet genormaliseerd, met een rij per type? Omdat er niets is dat
# ernaar vraagt. De site leest deze stand altijd in zijn geheel: om hem te tonen,
# om hem te vergelijken met wat de node zelf zegt, of om te weten of er
# überhaupt een filter aanstaat. Een schema dat query's ondersteunt die niemand
# stelt, is onderhoud zonder opbrengst -- en de dag dat de firmware er een regel
# bij krijgt, is dit de vorm die zonder migratie meebeweegt.
#
# Wat hier staat is een MOMENTOPNAME uit het laatste statistiekenbericht, niet de
# waarheid. De waarheid staat in de node. Vandaar ``updated`` en ``source``: wie
# het zei en wanneer, zodat de pagina "volgens het bericht van 14:03" kan tonen
# in plaats van te doen alsof ze het nu weet.


def upsert_filter_state(repeater_id: int, state: dict, source: str = "") -> None:
    """Bewaar de filterstand zoals hij in een bericht binnenkwam."""
    import json as _json
    execute(
        "INSERT INTO repeater_filter(repeater_id, state, updated, source) VALUES(?,?,?,?) "
        "ON CONFLICT(repeater_id) DO UPDATE SET "
        "state=excluded.state, updated=excluded.updated, source=excluded.source",
        (repeater_id, _json.dumps(state)[:20000], utcnow(), (source or "")[:32]),
    )


def filter_state_for(repeater_id: int) -> dict | None:
    """De laatst gemelde filterstand, of None als er nooit een bericht was.

    None en een lege stand zijn met opzet verschillend. 'Deze node heeft nooit
    iets over een filter gezegd' betekent meestal dat er firmware zonder filter
    op staat, en dat is iets anders dan 'deze node meldt dat er geen filter
    aanstaat'. De pagina zegt die twee ook anders.
    """
    import json as _json
    row = qone("SELECT * FROM repeater_filter WHERE repeater_id=?", (repeater_id,))
    if not row:
        return None
    try:
        state = _json.loads(row["state"])
    except (ValueError, TypeError):
        return None
    if not isinstance(state, dict):
        return None
    state["_updated"] = row["updated"]
    state["_source"] = row["source"]
    return state


def filter_states_all() -> dict:
    """Elke bekende filterstand, per repeater-id -- één query voor de tabel.

    Zelfde reden als ``cli_settings_all``: de vergelijkingstabel vraagt het van
    alle nodes tegelijk, en dat per node doen is twintig query's voor één scherm.
    """
    import json as _json
    uit = {}
    for row in q("SELECT * FROM repeater_filter"):
        try:
            state = _json.loads(row["state"])
        except (ValueError, TypeError):
            continue
        if isinstance(state, dict):
            state["_updated"] = row["updated"]
            state["_source"] = row["source"]
            uit[row["repeater_id"]] = state
    return uit


def upsert_cli_settings(repeater_id: int, values: dict, prune: bool = True) -> None:
    """Store a node's CLI parameters.

    ``prune`` drops rows this push did not mention and the configured list does
    not name, which is what a full re-read through Home Assistant wants: a
    parameter that no longer exists should disappear.

    Pass prune=False when the source omits what it could not read, as the node's
    own daily sweep does. There, an absent parameter means "no answer this
    time", not "gone" -- and the two are indistinguishable from here. A push
    that carries no information about deletion must not be allowed to delete.

    What survives a prune is the union of this push with the configured list, so
    a silent parameter is safe only as long as that list still names it. The
    node's own table (SET_PARAMS in the firmware) is maintained separately from
    the server setting and the two drift; a key only the firmware knows about
    has nothing holding it. Pruning on a partial sweep stakes the row on those
    two lists agreeing, which is a bet worth avoiding when the push had nothing
    to say about the row either way.
    """
    now = utcnow()
    # Prune against the configured parameter list rather than this push: a
    # partial re-read must not wipe rows it simply did not ask about.
    configured = {p.strip() for p in
                  (get_setting("cli_params", DEFAULT_CLI_PARAMS) or "").replace(";", ",").split(",")
                  if p.strip()}
    keep = [str(p)[:64] for p in ({str(k)[:64] for k in values} | configured)]
    with _lock:
        conn = get_conn()
        if prune:
            placeholders = ",".join("?" for _ in keep) or "''"
            conn.execute(
                f"DELETE FROM repeater_cli WHERE repeater_id=? AND param NOT IN ({placeholders})",
                [repeater_id, *keep],
            )
        for param, value in values.items():
            conn.execute(
                "INSERT INTO repeater_cli(repeater_id, param, value, updated) VALUES(?,?,?,?) "
                "ON CONFLICT(repeater_id, param) DO UPDATE SET "
                "value=excluded.value, updated=excluded.updated",
                (repeater_id, str(param)[:64],
                 None if value is None else str(value)[:4000], now),
            )
        conn.commit()


def cli_settings_for(repeater_id: int) -> list:
    return q("SELECT * FROM repeater_cli WHERE repeater_id=? ORDER BY param", (repeater_id,))


def cli_settings_all() -> list:
    """Elke gelezen CLI-waarde van elke repeater, in één query.

    Bestaat naast ``cli_settings_for`` omdat de vergelijkingstabel de waarden van
    alle nodes naast elkaar zet en de per-node-versie in een lus roepen bij
    twintig repeaters twintig queries is voor één scherm. De sortering ligt vast
    zodat de tabel bij elke verversing dezelfde volgorde heeft: een rij die
    verspringt tussen twee weergaven maakt vergelijken onmogelijk, wat nu net het
    doel is.
    """
    return q("SELECT repeater_id, param, value, updated FROM repeater_cli "
             "ORDER BY repeater_id, param")


def request_refresh(prefix: str) -> None:
    """Queue a manual status request for a polling client (Home Assistant today)."""
    import json
    d = {}
    try:
        d = json.loads(get_setting("refresh_requests", "{}"))
    except ValueError:
        pass
    d[prefix] = utcnow()
    set_setting("refresh_requests", json.dumps(d))


def pop_refresh_requests() -> list[str]:
    """Fetch pending requests and clear them (delivered to Home Assistant)."""
    import json
    try:
        d = json.loads(get_setting("refresh_requests", "{}"))
    except ValueError:
        d = {}
    if d:
        set_setting("refresh_requests", "{}")
    return list(d)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "repeater"


# Below this many hex characters two different keys could collide by chance, so
# we refuse to treat one as a shortening of the other.
MIN_PREFIX_MATCH = 8


def _find_by_prefix(pubkey_prefix: str) -> sqlite3.Row | None:
    """Find a repeater by public key, tolerating differing prefix lengths.

    Sources disagree on how much of the key they send: Home Assistant reports
    5 bytes, a node's own firmware 6. Matching on the string alone registered
    one node twice and split its history down the middle. For this to be the
    same node, the shorter key must be a prefix of the longer one.
    """
    row = qone("SELECT * FROM repeaters WHERE pubkey_prefix=?", (pubkey_prefix,))
    if row or len(pubkey_prefix) < MIN_PREFIX_MATCH:
        return row

    # Stored key is shorter: 'aabbccddee' matches an incoming 'aabbccddeeff'.
    row = qone(
        "SELECT * FROM repeaters WHERE ?1 LIKE pubkey_prefix || '%'"
        " AND length(pubkey_prefix) >= ?2"
        " ORDER BY length(pubkey_prefix) DESC LIMIT 1",
        (pubkey_prefix, MIN_PREFIX_MATCH),
    )
    if row:
        # Keep the longest key seen; it is the least ambiguous.
        if len(pubkey_prefix) > len(row["pubkey_prefix"]):
            execute("UPDATE repeaters SET pubkey_prefix=? WHERE id=?",
                    (pubkey_prefix, row["id"]))
            row = qone("SELECT * FROM repeaters WHERE id=?", (row["id"],))
        return row

    # Stored key is longer: an incoming 'aabbccddee' matches 'aabbccddeeff'.
    return qone(
        "SELECT * FROM repeaters WHERE pubkey_prefix LIKE ?1 || '%'"
        " ORDER BY length(pubkey_prefix) DESC LIMIT 1",
        (pubkey_prefix,),
    )


def find_repeater(pubkey_prefix: str) -> sqlite3.Row | None:
    """Look up a repeater by public key, without creating one.

    Public door to the prefix-tolerant match, for callers that need to ask "are
    these two keys the same node?" rather than "give me a row". Comparing the
    strings instead would answer no whenever the two sources disagree on key
    length -- Home Assistant sends five bytes where a node's own firmware sends
    six -- which is the trap _find_by_prefix exists for.
    """
    return _find_by_prefix(str(pubkey_prefix or "").lower().strip())


# Hoeveel repeaters er vanzelf mogen ontstaan.
#
# De enige tabel van de drie die niet gesnoeid kan worden. Een oude repeater
# weggooien betekent zijn hele historiek weggooien -- ``latest`` en
# ``repeater_cli`` hangen er met ON DELETE CASCADE aan -- en dat automatisch
# doen aan een node die een maand offline was is precies verkeerd. Dus in plaats
# van snoeien: een plafond dat WEIGERT. Weigeren verliest nooit iets, en het
# maakt de tabel even begrensd.
#
# Vijfhonderd is twee orden van grootte boven een echt mesh (deze site volgt er
# een handvol) en ver onder wat op schijf iets voorstelt: een repeaterrij is een
# paar honderd byte. Wordt hij toch geraakt, dan is dat geen ruis om weg te
# filteren maar een gebeurtenis -- vandaar de uitzondering, die in het logboek
# en op de beheerpagina belandt.
MAX_REPEATERS = 500


def repeater_count() -> int:
    row = qone("SELECT COUNT(*) AS n FROM repeaters")
    return (row["n"] or 0) if row else 0


def get_or_create_repeater(pubkey_prefix: str, name: str | None) -> sqlite3.Row:
    """Zoek de repeater bij deze sleutel, of maak hem aan.

    Twee dingen zijn hier veranderd omdat dit de plek is waar iemand anders een
    rij in deze databank kan laten ontstaan.

    *De sleutel wordt gekeurd.* Alles wat geen begrensde hexsleutel is, wordt
    geweigerd in plaats van als repeaternaam bewaard. Zonder dat maakte elke
    verzonnen ``pubkey_prefix`` een rij, en elke rij sleepte er ``latest``-rijen
    achteraan.

    *Een nieuwe repeater komt VERBORGEN binnen* (``is_public = 0``). Dit is een
    publieke site: een repeater zichtbaar maken is een besluit van de beheerder
    en hoort geen bijwerking te zijn van het feit dat er een MQTT-bericht
    binnenkwam. Wie op het topic mag publiceren kon tot nu toe rechtstreeks iets
    op de voorpagina zetten.

    Wat dit NIET raakt: bestaande repeaters. De INSERT hieronder draait alleen
    voor een sleutel die we nog nooit gezien hebben, dus alles wat vandaag
    zichtbaar is blijft zichtbaar -- de kolomstandaard in het schema staat nog
    steeds op 1 en wordt hier alleen voor nieuwe rijen overschreven. De
    beheerpagina zegt hoeveel er verborgen wachten, zodat een nieuwe repeater
    opvalt in plaats van te verdwijnen.
    """
    key = key_prefix(pubkey_prefix)
    if not key:
        raise ValueError("pubkey_prefix is geen begrensde hexsleutel: "
                         f"{str(pubkey_prefix)[:32]!r}")
    row = _find_by_prefix(key)
    if row:
        # Adopt the name whenever Home Assistant sends a new one
        if name and name != row["name"]:
            execute("UPDATE repeaters SET name=? WHERE id=?", (name, row["id"]))
            row = qone("SELECT * FROM repeaters WHERE id=?", (row["id"],))
        return row
    if repeater_count() >= MAX_REPEATERS:
        raise ValueError(f"al {MAX_REPEATERS} repeaters bekend; {key} is niet "
                         "aangemaakt -- ruim ongebruikte repeaters op in /admin")
    base = slugify(name or key)
    slug = base
    i = 2
    while qone("SELECT 1 FROM repeaters WHERE slug=?", (slug,)):
        slug = f"{base}-{i}"
        i += 1
    execute(
        "INSERT INTO repeaters(slug, pubkey_prefix, name, created_at, is_public) "
        "VALUES(?,?,?,?,0)",
        (slug, key, name or key, utcnow()),
    )
    log.info("Nieuwe repeater %s (%s) aangemaakt en verborgen; maak hem publiek "
             "op /admin als hij op de site hoort", name or key, key)
    return qone("SELECT * FROM repeaters WHERE pubkey_prefix=?", (key,))


def record_source(repeater_id: int, source: str) -> None:
    """Note who delivered this repeater's statistics.

    Kept because the deliverer and the subject need not be the same node: a node
    may report on repeaters it monitors. Recording the route makes that visible
    instead of invisible, so a repeater suddenly arriving via an unexpected node
    is something the admin page can show rather than something nobody notices.
    """
    execute("UPDATE repeaters SET source_prefix=?, source_seen=? WHERE id=?",
            (str(source or "")[:32] or None, utcnow(), repeater_id))


def mark_guest_polled(key: str, on: bool = True) -> None:
    """Leg vast dat de cijfers van deze node bij hem opgehaald worden.

    Op de sleutel en niet op een rij-id, want bij het uitvragen bestaat de rij
    vaak nog niet: die ontstaat pas als de monitor het eerste bericht over hem
    publiceert. Vandaar ook ``get_or_create_repeater`` hier -- en die maakt hem
    NIET-publiek aan, wat precies goed is: het gaat per definitie om de node van
    iemand anders.
    """
    sleutel = key_prefix(key)
    if not sleutel:
        return
    rij = get_or_create_repeater(sleutel, sleutel)
    execute("UPDATE repeaters SET is_guest_polled=? WHERE id=?",
            (1 if on else 0, rij["id"]))


def record_topic_node(node: str, raw: str) -> None:
    """Note het middenstuk van het topic zoals deze node het schrijft.

    Alleen als het na normalisatie dezelfde sleutel is; anders zou een topic met
    een tikfout erin de weg terug kunnen kapen. En alleen als het veranderd is,
    want dit komt bij elk statistiekbericht langs en een UPDATE per bericht is
    een schrijfactie per bericht.
    """
    schoon = str(raw or "").strip()
    if not schoon or key_prefix(schoon) != key_prefix(node):
        return
    rij = find_repeater(node)
    if rij is None:
        return
    try:
        huidig = rij["topic_node"]
    except (KeyError, IndexError):
        huidig = None
    if huidig == schoon:
        return
    execute("UPDATE repeaters SET topic_node=? WHERE id=?", (schoon[:32], rij["id"]))


def record_topic_prefix(node: str, prefix: str) -> None:
    """Note het MQTT-topicvoorvoegsel waarop deze node zich meldt.

    Nodig omdat de site tijdens de hernoeming naar MeshManager naar twee
    voorvoegsels tegelijk luistert, maar een opdracht op precies één moet
    vertrekken -- op dat van de node zelf. Een node die nog niet geflasht is,
    luistert op het oude; hem op het nieuwe aanspreken levert een knop op die
    zegt dat hij iets verstuurd heeft terwijl er niemand meeleest.

    In de databank en niet alleen in het geheugen, omdat het antwoord een
    herstart van de site moet overleven: anders is elke opdracht in de eerste
    minuten na een herstart een gok.

    Schrijft alleen als er iets verandert -- dit komt bij elk statistiekbericht
    langs en een UPDATE die niets wijzigt is nog steeds een schrijfactie op een
    databank die op een SD-kaartje kan staan.
    """
    prefix = str(prefix or "").strip()[:32]
    if not prefix:
        return
    row = find_repeater(node)
    if row is None or _field(row, "topic_prefix") == prefix:
        return
    execute("UPDATE repeaters SET topic_prefix=? WHERE id=?", (prefix, row["id"]))


def topic_prefix_counts() -> list:
    """Hoeveel nodes zich op welk topicvoorvoegsel melden.

    Bestaat voor één vraag, en het is de vraag die de hele migratie afsluit:
    mag het oude voorvoegsel weg? Zonder dit is het antwoord "ik denk het" en
    is de enige controle een sniffer op de broker. Nodes die nog nooit iets
    over MQTT gestuurd hebben (HTTP-ingest, Home Assistant) hebben geen
    voorvoegsel en tellen hier niet mee -- die luisteren ook nergens.
    """
    rows = q("SELECT topic_prefix AS prefix, COUNT(*) AS n FROM repeaters "
             "WHERE topic_prefix IS NOT NULL AND topic_prefix<>'' "
             "GROUP BY topic_prefix ORDER BY n DESC")
    return [{"prefix": r["prefix"], "n": r["n"]} for r in rows]


def topic_prefix_for(node: str) -> str | None:
    """Het opgeslagen voorvoegsel van deze node, of None."""
    row = find_repeater(node)
    return _field(row, "topic_prefix") if row is not None else None


def _field(row, name):
    """Kolom uit een rij die de kolom nog niet hoeft te hebben.

    Een oudere databank die net bijgewerkt wordt heeft de kolom pas na
    _migrate(), en een sqlite3.Row werpt op een naam die hij niet kent.
    """
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


def payload_module_version(rep):
    """De versie van onze eigen firmwaremodule uit een ``repeater``-blok.

    Twee namen, en dat blijft nog even zo. Firmware van 2.0.0 en hoger stuurt
    ``fw_meshmanager``; alles wat er nu op de daken hangt stuurt nog
    ``fw_meshstats``. Deze versie beslist of de site een node überhaupt iets
    mág vragen, dus de oude naam niet herkennen betekent knoppen die uitgrijzen
    op nodes die het prima aankunnen -- precies andersom als bedoeld.

    Hier en niet in de twee aanroepers (MQTT en de HTTP-ingest), zodat de dag
    dat de oude naam weg mag maar op één plaats hoeft te gebeuren. Weg te halen
    zodra geen enkele node nog firmware onder 2.0.0 draait; de beheerpagina
    toont per node welke versie binnenkomt.
    """
    if not isinstance(rep, dict):
        return None
    value = rep.get("fw_meshmanager")
    if value in (None, ""):
        value = rep.get("fw_meshstats")
    return value


def record_firmware(repeater_id: int, fw=None, fw_module=None) -> None:
    """Note the firmware the last message came from.

    Only overwrites what the message actually named. A source that knows one of
    the two -- Home Assistant reads a repeater's MeshCore version off the mesh
    and has no idea whether our own module is on it -- must not be able to
    erase the other by staying silent about it.
    """
    sets, args = [], []
    for column, value in (("fw", fw), ("fw_meshmanager", fw_module)):
        text = str(value).strip()[:32] if value not in (None, "") else ""
        if text:
            sets.append(f"{column}=?")
            args.append(text)
    if not sets:
        return
    execute(f"UPDATE repeaters SET {', '.join(sets)} WHERE id=?", (*args, repeater_id))


# Hoeveel tekst de parametertabel van één node mag beslaan. Achtentwintig
# parameters meten ongeveer 960 byte; het dubbele is ruim en het is tegelijk een
# grens op wat een publisher onder dit veld de databank in kan schrijven. Dit
# komt van het stats-topic, en dat is geen invoer van de eigenaar.
MAX_CFG_SPEC = 4000


def record_cfg_spec(repeater_id: int, spec: str) -> None:
    """De parametertabel die een node in zijn instellingenronde meestuurde.

    Alleen overschrijven als er werkelijk iets meekwam. Een ronde die de tabel
    niet bij zich had, mag de tabel van gisteren niet wissen -- dezelfde regel
    als bij ``record_firmware``, en om dezelfde reden: zwijgen is geen bewering.
    """
    text = str(spec or "").strip()
    if not text or len(text) > MAX_CFG_SPEC:
        return
    execute("UPDATE repeaters SET cfg_spec=?, cfg_spec_at=? WHERE id=?",
            (text, utcnow(), repeater_id))


def record_pio_env(repeater_id: int, env: str) -> None:
    """Note the build environment a node reported about itself.

    Written only when the node actually said one, for the same reason
    record_firmware() never erases on silence: a stale-but-true answer beats an
    empty one, and an empty one here means the page stops offering an upgrade.
    """
    text = str(env or "").strip()[:64]
    if text:
        execute("UPDATE repeaters SET pio_env=? WHERE id=?", (text, repeater_id))


def set_ota_host(repeater_id: int, host: str) -> None:
    """Where this node's management page is, or empty to say there is no IP path."""
    text = str(host or "").strip()[:120]
    execute("UPDATE repeaters SET ota_host=? WHERE id=?", (text or None, repeater_id))


def set_critical(repeater_id: int, critical: bool) -> None:
    execute("UPDATE repeaters SET is_critical=? WHERE id=?",
            (1 if critical else 0, repeater_id))


# When the Home Assistant poller last emptied the command queue. The queue is
# clear-on-read and holds no history, so without this the site cannot tell "the
# poller took the request and the repeater stayed silent" from "nothing has ever
# come to collect it" -- and the admin page has to promise something in both
# cases. See commanding.py, which turns this into what the page says.
POLLER_SEEN_KEY = "poller_seen"


def note_poller_seen() -> None:
    set_setting(POLLER_SEEN_KEY, utcnow())


def poller_last_seen() -> str | None:
    return get_setting(POLLER_SEEN_KEY, "") or None


def spill_samples(items) -> None:
    """Write measurements into ``samples`` that VictoriaMetrics could not take.

    Registered with the tsdb module, which calls it from its writer thread when
    a batch fails, when the queue is full, or when no time-series database is
    configured at all. Full resolution goes in here: this is a safety net, and
    thinning the very points that only exist because the primary store was
    unavailable would defeat it.
    """
    with _lock:
        conn = get_conn()
        for repeater_id, _slug, metric, value, _ts_ns, ts in items:
            conn.execute(
                "INSERT OR REPLACE INTO samples(repeater_id, metric, ts, value) "
                "VALUES(?,?,?,?)",
                (repeater_id, metric, ts, float(value)),
            )
        conn.commit()


tsdb.register_spill(spill_samples)


def ingest(repeater_id: int, ts: str, metrics: dict, neighbors: list | None,
           force: bool = False):
    """Store a snapshot.

    ``latest`` always gets the new value: it feeds the home page and has to be
    readable without touching the network.

    Where the *history* goes depends on whether a time-series database is
    configured. With one, every numeric value is handed to it at full
    resolution, which is the whole point of the move -- nodes are going to
    publish every ten seconds. Without one, the old rule applies: a value only
    enters ``samples`` when it changed, or when the last stored point is older
    than the heartbeat interval, because otherwise a stable metric would fill
    the table with identical rows while its chart still needs points to keep
    running. force=True (manual status update) always writes.
    """
    # Read the setting before taking the lock; get_setting takes it itself
    heartbeat = timedelta(minutes=setting_int("heartbeat_min", config.HEARTBEAT_MIN))
    to_tsdb: dict = {}
    slug = None
    dropped_neighbors = 0
    with _lock:
        conn = get_conn()
        row = conn.execute("SELECT slug FROM repeaters WHERE id=?",
                           (repeater_id,)).fetchone()
        slug = row["slug"] if row else None
        for name, raw in metrics.items():
            value = value_str = None
            if isinstance(raw, bool):
                value = 1.0 if raw else 0.0
            elif isinstance(raw, (int, float)):
                value = float(raw)
            else:
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    value_str = None if raw is None else str(raw)[:255]
            prev = conn.execute(
                "SELECT ts, value, value_str FROM latest WHERE repeater_id=? AND metric=?",
                (repeater_id, name),
            ).fetchone()
            conn.execute(
                "INSERT INTO latest(repeater_id, metric, ts, value, value_str) VALUES(?,?,?,?,?) "
                "ON CONFLICT(repeater_id, metric) DO UPDATE SET ts=excluded.ts, "
                "value=excluded.value, value_str=excluded.value_str",
                (repeater_id, name, ts, value, value_str),
            )
            if value is None:
                continue
            to_tsdb[name] = value
            if tsdb.enabled():
                continue    # VictoriaMetrics keeps this one, at full resolution
            store = True
            if not force and prev is not None and prev["value"] == value:
                # Unchanged: only a heartbeat point, and judged on the last
                # STORED sample rather than the last ingest.
                last_sample = conn.execute(
                    "SELECT MAX(ts) AS ts FROM samples WHERE repeater_id=? AND metric=?",
                    (repeater_id, name),
                ).fetchone()
                try:
                    prev_dt = datetime.strptime(last_sample["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    now_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    store = (now_dt - prev_dt) >= heartbeat
                except (TypeError, ValueError):
                    store = True
            if store:
                conn.execute(
                    "INSERT OR REPLACE INTO samples(repeater_id, metric, ts, value) VALUES(?,?,?,?)",
                    (repeater_id, name, ts, value),
                )
        if neighbors is not None:
            for nb in neighbors:
                # Een burenprefix is een sleutelvoorvoegsel en verder niets.
                # Het werd vroeger alleen op "niet leeg" gecontroleerd, en dat
                # is een gat met twee gaten erin: de tekst belandde als
                # ``neighbors``-rij én, via ``neighbor_<prefix>``, als
                # ``latest``-rij, twee tabellen waar niets hem ooit weer uit
                # haalde. Eén regel eruit gooien en doortellen, want de rest van
                # dit bericht kan best in orde zijn -- zie check_snapshot over
                # waarom aantallen wél het hele bericht afkeuren.
                prefix = key_prefix(nb.get("prefix")) if isinstance(nb, dict) else ""
                if not prefix:
                    dropped_neighbors += 1
                    continue
                # 'seen_min' = minutes since last heard -> absolute timestamp
                last = ts
                seen_min = nb.get("seen_min")
                if isinstance(seen_min, (int, float)):
                    try:
                        ts_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                        last = (ts_dt - timedelta(minutes=seen_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except ValueError:
                        pass
                snr = nb.get("snr")
                prev_nb = conn.execute(
                    "SELECT snr FROM neighbors WHERE repeater_id=? AND prefix=?",
                    (repeater_id, prefix),
                ).fetchone()
                conn.execute(
                    "INSERT INTO neighbors(repeater_id, prefix, name, snr, last_seen) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(repeater_id, prefix) DO UPDATE SET "
                    "name=COALESCE(excluded.name, name), snr=COALESCE(excluded.snr, snr), "
                    "last_seen=excluded.last_seen",
                    (repeater_id, prefix, nb.get("name"), snr, last),
                )
                # Per-link history: SNR trend of one individual neighbour link.
                # There are a lot of these -- one per heard node -- and they go
                # to the same place as everything else.
                if isinstance(snr, (int, float)):
                    to_tsdb[f"neighbor_{prefix}"] = float(snr)
                    if tsdb.enabled():
                        continue
                    store_link = force or prev_nb is None or prev_nb["snr"] != snr
                    if not store_link:
                        last_sample = conn.execute(
                            "SELECT MAX(ts) AS ts FROM samples WHERE repeater_id=? AND metric=?",
                            (repeater_id, f"neighbor_{prefix}"),
                        ).fetchone()
                        try:
                            prev_dt = datetime.strptime(last_sample["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                            now_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                            store_link = (now_dt - prev_dt) >= heartbeat
                        except (TypeError, ValueError):
                            store_link = True
                    if store_link:
                        conn.execute(
                            "INSERT OR REPLACE INTO samples(repeater_id, metric, ts, value) VALUES(?,?,?,?)",
                            (repeater_id, f"neighbor_{prefix}", ts, float(snr)),
                        )
        conn.execute("UPDATE repeaters SET last_seen=? WHERE id=?", (ts, repeater_id))
        conn.commit()

    if dropped_neighbors:
        # WARNING en niet DEBUG. Een echte node stuurt dit nooit, dus dit is
        # ofwel een firmware die iets anders is gaan sturen ofwel iemand die
        # probeert wat er in deze tabellen past -- en allebei die antwoorden wil
        # je kunnen vinden zonder een sniffer op de broker te zetten.
        log.warning("Repeater %s: %d burenregel(s) zonder bruikbare sleutel "
                    "overgeslagen", slug or repeater_id, dropped_neighbors)

    # Outside the lock, and non-blocking: record() only queues. Nothing in the
    # ingest path waits on a socket, and if the queue cannot take the points
    # they come straight back to spill_samples above.
    if slug and to_tsdb:
        tsdb.record(repeater_id, slug, ts, to_tsdb)


def history(repeater_id: int, metric: str, hours: int) -> list[tuple[str, float]]:
    """History straight from SQLite. The fallback path -- callers should go
    through metric_history(), which prefers VictoriaMetrics."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if hours <= 48:
        rows = q(
            "SELECT ts, value FROM samples WHERE repeater_id=? AND metric=? AND ts>=? ORDER BY ts",
            (repeater_id, metric, since),
        )
        return [(r["ts"], r["value"]) for r in rows]
    # Longer windows: average per hour to keep the response small
    rows = q(
        "SELECT substr(ts,1,13)||':00:00Z' AS bucket, AVG(value) AS value "
        "FROM samples WHERE repeater_id=? AND metric=? AND ts>=? GROUP BY bucket ORDER BY bucket",
        (repeater_id, metric, since),
    )
    return [(r["bucket"], round(r["value"], 3)) for r in rows]


def metric_history(repeater, metric: str, hours: int) -> list[tuple[str, float]]:
    """History for a chart, from wherever it actually lives.

    VictoriaMetrics when it answers, SQLite when it does not. The fallback is
    silent on purpose: a visitor looking at a chart cannot act on which database
    served it, and the admin page reports the health.
    """
    points = tsdb.history(repeater["slug"], metric, hours)
    if points is None:
        return history(repeater["id"], metric, hours)
    return points


def computed_utilization(repeater, total_metric: str, window_min: int = 90) -> float | None:
    """Utilisation (%) derived from the airtime totals: delta airtime / delta time.

    Computed here instead of read from the node because the meshcore-side figure
    resets on every Home Assistant restart.
    """
    # This reads the same measurements the charts do, so it has to follow them
    # to VictoriaMetrics -- otherwise moving the history would quietly empty
    # these two tiles, since `samples` stops being written once the move is on.
    series = tsdb.window_values(repeater["slug"], total_metric, window_min)
    if series is None:
        since = (datetime.now(timezone.utc)
                 - timedelta(minutes=window_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = q(
            "SELECT ts, value FROM samples WHERE repeater_id=? AND metric=? AND ts>=? "
            "ORDER BY ts",
            (repeater["id"], total_metric, since),
        )
        series = []
        for r in rows:
            try:
                dt = datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            series.append((dt.timestamp(), r["value"]))
    if len(series) < 2:
        return None
    dt_min = (series[-1][0] - series[0][0]) / 60
    dv_min = series[-1][1] - series[0][1]   # airtime is in minutes
    if dt_min < 10 or dv_min < 0:           # window too short, or counter reset
        return None
    return round(dv_min / dt_min * 100, 2)


def latest_for(repeater_id: int) -> dict[str, sqlite3.Row]:
    return {r["metric"]: r for r in q("SELECT * FROM latest WHERE repeater_id=?", (repeater_id,))}


# --- retention ---------------------------------------------------------------
#
# Three limits, and the order they are applied in is the whole design.
#
# 1. AGE. Everything older than its retention goes. This is the setting a reader
#    of this site would recognise: "we keep a month of packets".
# 2. ROWS. Above ``packet_max_rows`` the oldest packets go until the count fits,
#    whatever their age. First in, first out.
# 3. BYTES. Above ``db_max_mb`` on disk, more of the oldest packets go until the
#    estimated saving covers the excess.
#
# Age is the aim; rows and bytes are the promise. The difference matters on the
# admin page: whenever 2 or 3 does the cutting, the configured period was NOT
# achieved, and a reader who set 30 days is actually looking at 12. That is
# reported rather than absorbed -- a gap in a graph with no explanation is
# exactly the failure mode this project keeps trying to avoid.
#
# FIFO is on ``id``, not on ``ts``. The id is the insertion order, which is what
# "first in, first out" means, and it is the primary key -- so the sweep is an
# index seek plus a ranged delete instead of a scan. A timestamp would also be
# wrong in the one case where the two disagree: a node whose clock is off sends
# packets that are stored now but dated last year, and deleting on ts would
# throw those away first even though they are the freshest thing we have.

# Never trim the packets table below this, whatever the byte ceiling says. A
# ceiling that has to be met by emptying the table entirely is a misconfigured
# ceiling, and the honest answer is to say so on the admin page rather than to
# leave a site with no packets on it at all.
PACKET_FIFO_FLOOR = 1000

# What one packet row costs when we cannot measure it. Measured on the live
# server (7 477 rows, table plus its three indexes) at roughly 335 bytes;
# rounded up, because underestimating means a byte-ceiling pass that deletes too
# little and has to run again, while overestimating deletes packets nobody asked
# to lose.
PACKET_BYTES_FALLBACK = 400

# When a VACUUM is worth its cost. Both must be true: enough absolute waste to
# be worth a rewrite, and enough relative waste that the file is meaningfully
# bigger than its contents.
VACUUM_MIN_FREE_BYTES = 16 * 1024 * 1024
VACUUM_MIN_FREE_RATIO = 0.20
# VACUUM writes a complete second copy before swapping it in, so the disk needs
# room for both. The multiplier is over the database size and deliberately
# generous: refusing a cleanup is free, running out of disk halfway through one
# is not.
VACUUM_MIN_DISK_FACTOR = 3.0


def retention_settings() -> dict:
    """The limits actually in force, settings table first, .env as the default.

    Read on every pass rather than captured at import: the whole point of moving
    these into ``settings`` is that raising a retention takes effect without a
    container restart. Anything that caches these values reintroduces exactly
    the restart this replaces -- see routes_api's heat-map window.
    """
    return {
        "days": max(1, setting_int("packet_retention_days", config.PACKET_RETENTION_DAYS)),
        "sample_days": max(1, setting_int("retention_days", config.RETENTION_DAYS)),
        "max_rows": max(PACKET_FIFO_FLOOR,
                        setting_int("packet_max_rows", config.PACKET_MAX_ROWS)),
        "max_mb": max(1, setting_int("db_max_mb", config.DB_MAX_MB)),
    }


def db_bytes() -> int:
    """What this database occupies on disk, WAL and shared memory included.

    The WAL counts because it is real disk: in WAL mode a busy database carries
    megabytes there that the main file does not show yet, and a ceiling that
    ignores it is a ceiling that is quietly exceeded.
    """
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(str(config.DB_PATH) + suffix)
        except OSError:
            pass
    return total


def _packet_bytes_per_row(conn: sqlite3.Connection, rows: int) -> float:
    """How many bytes one packet row really costs, indexes included.

    Measured through ``dbstat`` when the SQLite build has it, because a guess
    that is wrong by a factor of two means a byte ceiling that either bites far
    too hard or never converges. dbstat is a compile-time option
    (SQLITE_ENABLE_DBSTAT_VTAB) and is genuinely absent on some builds, so the
    measured constant above stands in -- a slightly-off estimate that runs again
    in an hour beats a sweep that refuses to work at all.
    """
    if rows <= 0:
        return float(PACKET_BYTES_FALLBACK)
    try:
        row = conn.execute(
            "SELECT SUM(pgsize) AS b FROM dbstat WHERE name IN "
            "('packets', 'idx_packets_ts', 'idx_packets_dup', 'idx_packets_sender')"
        ).fetchone()
    except sqlite3.Error:
        return float(PACKET_BYTES_FALLBACK)
    if row and row["b"]:
        return max(1.0, float(row["b"]) / rows)
    return float(PACKET_BYTES_FALLBACK)


def packet_row_count() -> int:
    row = qone("SELECT COUNT(*) AS n FROM packets")
    return (row["n"] or 0) if row else 0


def _trim_oldest_packets(keep: int) -> int:
    """Cut the packets table back to ``keep`` rows, oldest first. Returns how many went.

    One OFFSET seek to find the id of the ``keep``-th newest packet, then a
    single ranged DELETE below it. Both ride the primary key, so the cost is
    independent of how far over the limit we were -- which matters, because the
    pass that has the most to delete is the pass that runs while the machine is
    already under pressure.
    """
    keep = max(PACKET_FIFO_FLOOR, int(keep))
    row = qone("SELECT id FROM packets ORDER BY id DESC LIMIT 1 OFFSET ?", (keep,))
    if row is None:
        return 0            # fewer rows than the cap: nothing to do
    return execute_rowcount("DELETE FROM packets WHERE id <= ?", (row["id"],))


def _span_days(oldest: str | None, newest: str | None) -> float | None:
    """How many days of packets the table actually holds, or None if we cannot tell."""
    if not oldest or not newest:
        return None
    try:
        lo = datetime.strptime(oldest, "%Y-%m-%dT%H:%M:%SZ")
        hi = datetime.strptime(newest, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return round(max(0.0, (hi - lo).total_seconds()) / 86400.0, 2)


# Hoeveel verschillende metrieken één repeater een actuele waarde mag hebben.
#
# Een repeater meldt enkele tientallen cijfers plus één ``neighbor_<prefix>``
# per node die hij gehoord heeft, en de firmware stopt zelf bij een paar
# honderd contacten. Duizend ligt daar ruim boven en stelt op schijf niets voor;
# het bestaat zodat een publisher die per bericht een nieuwe metrieknaam
# verzint de tabel niet eindeloos kan laten groeien.
MAX_LATEST_PER_REPEATER = 1000
# Idem voor CLI-parameters. De ingestelde lijst telt er vijftien; tweehonderd is
# ruimte voor een firmware die er flink bij krijgt, en nog steeds een plafond.
MAX_CLI_PER_REPEATER = 200


def _prune_latest(stale_before: str) -> int:
    """Snoei ``latest``: wezen, uitgestorven metrieken, en een plafond per repeater.

    Drie regels, en de tweede is de subtiele.

    *Wezen* horen er met ``ON DELETE CASCADE`` niet te zijn. Deze regel is er
    voor rijen van vóór die clausule en voor een databank die ooit met
    ``foreign_keys=OFF`` is aangeraakt; hij kost één indexscan en is de enige
    manier om zeker te weten dat het klopt.

    *Uitgestorven metrieken* zijn rijen die binnen de bewaartermijn van de
    metingen niet meer ververst zijn -- een naam die één keer langskwam en nooit
    meer. Alleen bij repeaters die zelf nog WEL rapporteren, en dat voorwaardje
    is de hele reden dat deze regel zo geschreven is: bij een repeater die al
    een half jaar stil ligt zijn ál zijn waarden oud, en die dan wissen zou zijn
    kaart op de startpagina leegmaken terwijl "dit was het laatste wat we van
    hem hoorden" precies het antwoord is dat iemand zoekt. Dood laten liggen, en
    alleen bij de levenden opruimen.

    *Het plafond* is wat er werkelijk toe doet bij misbruik. De regel hierboven
    ruimt pas na de bewaartermijn op, en binnen die termijn kan een publisher
    die per bericht een nieuwe naam verzint er heel wat kwijt. Het plafond
    houdt de nieuwste ``MAX_LATEST_PER_REPEATER`` en gooit de rest weg, oudste
    eerst -- dezelfde FIFO als bij de pakketten, en om dezelfde reden: wat het
    langst niet ververst is, is het minst waard.
    """
    n = execute_rowcount(
        "DELETE FROM latest WHERE repeater_id NOT IN (SELECT id FROM repeaters)")
    n += execute_rowcount(
        "DELETE FROM latest WHERE ts < ? AND repeater_id IN ("
        "  SELECT repeater_id FROM latest GROUP BY repeater_id "
        "  HAVING MAX(ts) >= ?)",
        (stale_before, stale_before))
    n += execute_rowcount(
        "DELETE FROM latest WHERE rowid IN (SELECT rowid FROM ("
        "  SELECT rowid, ROW_NUMBER() OVER ("
        "    PARTITION BY repeater_id ORDER BY ts DESC, metric) AS rn"
        "  FROM latest) WHERE rn > ?)",
        (MAX_LATEST_PER_REPEATER,))
    return n


def _prune_cli(stale_before: str) -> int:
    """Hetzelfde voor ``repeater_cli``, met ``updated`` in de rol van ``ts``.

    ``upsert_cli_settings(prune=True)`` ruimt hier al op, maar alleen wanneer er
    een volledige uitlezing binnenkomt en alleen tegen de ingestelde lijst. Een
    repeater die nooit meer uitgelezen wordt, houdt zijn rijen dus voorgoed; en
    een firmware die parameters bijverzint komt langs de andere kant binnen.
    Vandaar dezelfde drie regels als bij ``latest``.
    """
    n = execute_rowcount(
        "DELETE FROM repeater_cli WHERE repeater_id NOT IN (SELECT id FROM repeaters)")
    n += execute_rowcount(
        "DELETE FROM repeater_cli WHERE updated < ? AND repeater_id IN ("
        "  SELECT repeater_id FROM repeater_cli GROUP BY repeater_id "
        "  HAVING MAX(updated) >= ?)",
        (stale_before, stale_before))
    n += execute_rowcount(
        "DELETE FROM repeater_cli WHERE rowid IN (SELECT rowid FROM ("
        "  SELECT rowid, ROW_NUMBER() OVER ("
        "    PARTITION BY repeater_id ORDER BY updated DESC, param) AS rn"
        "  FROM repeater_cli) WHERE rn > ?)",
        (MAX_CLI_PER_REPEATER,))
    return n


def prune() -> dict:
    """One retention pass over the whole database. Returns what it did.

    A report rather than nothing, because the admin page has to be able to say
    when the last sweep ran and how much it threw away. A prune that happens
    silently is the reason a hole in a graph turns into an evening of
    debugging.

    Safe to call from anywhere and at any time: every delete goes through the
    module lock like every other write, and a pass that finds nothing to do
    costs three index lookups.
    """
    cfg = retention_settings()
    now = datetime.now(timezone.utc)

    def cutoff(days) -> str:
        return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    report = {
        "at": utcnow(), "samples": 0, "neighbors": 0, "packets_age": 0,
        "packets_rows": 0, "packets_bytes": 0, "limit_hit": "",
        "over_by_bytes": 0, "latest": 0, "cli": 0, **cfg,
    }

    # Measurements: they are pruned by their own, much longer retention, and
    # they mostly do not grow any more -- see the note in retention.py about
    # where the history actually lives now.
    report["samples"] = execute_rowcount("DELETE FROM samples WHERE ts<?",
                                         (cutoff(cfg["sample_days"]),))
    # Neighbours unheard for 7 days drop off the list.
    report["neighbors"] = execute_rowcount("DELETE FROM neighbors WHERE last_seen<?",
                                           (cutoff(7),))
    # De twee tabellen die hier tot voor kort helemaal niet in voorkwamen.
    report["latest"] = _prune_latest(cutoff(cfg["sample_days"]))
    report["cli"] = _prune_cli(cutoff(cfg["sample_days"]))
    # 1. Age. The retention as the reader understands it.
    report["packets_age"] = execute_rowcount("DELETE FROM packets WHERE ts<?",
                                             (cutoff(cfg["days"]),))

    # 2. Rows. The cheap ceiling, and the one that normally bites first.
    left = packet_row_count()
    if left > cfg["max_rows"]:
        report["packets_rows"] = _trim_oldest_packets(cfg["max_rows"])
        if report["packets_rows"]:
            report["limit_hit"] = "rows"
        left = packet_row_count()

    # 3. Bytes. The ceiling that cannot be argued away, checked against the file
    #    itself rather than against a model of it.
    total = db_bytes()
    ceiling = cfg["max_mb"] * 1024 * 1024
    if total > ceiling and left > PACKET_FIFO_FLOOR:
        excess = total - ceiling
        with _lock:
            per_row = _packet_bytes_per_row(get_conn(), left)
        # +1 so an excess smaller than one row still removes one; without it a
        # database a few bytes over the ceiling would loop forever doing nothing.
        keep = max(PACKET_FIFO_FLOOR, left - int(excess / per_row) - 1)
        report["packets_bytes"] = _trim_oldest_packets(keep)
        if report["packets_bytes"]:
            report["limit_hit"] = "bytes"
        left = packet_row_count()
        # Deletes free pages inside the file; they do not shrink it. Whether the
        # ceiling is now met is a question for after the VACUUM, so what is
        # recorded here is the excess as it stood -- see maybe_vacuum().
        report["over_by_bytes"] = max(0, db_bytes() - ceiling)

    span = qone("SELECT MIN(ts) AS lo, MAX(ts) AS hi FROM packets")
    report["packets_left"] = left
    report["oldest"] = span["lo"] if span else None
    report["newest"] = span["hi"] if span else None
    report["effective_days"] = _span_days(report["oldest"], report["newest"])
    report["db_bytes"] = db_bytes()
    return report


def free_pages() -> tuple[int, int, int]:
    """(free bytes, file bytes according to SQLite, page size).

    Read from the database's own bookkeeping rather than from the file size: a
    file that is 200 MB of which 150 MB is free list is a very different case
    from one that is 200 MB of packets, and only the first is worth a VACUUM.
    """
    with _lock:
        conn = get_conn()
        page = conn.execute("PRAGMA page_size").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return free * page, pages * page, page


def _checkpoint() -> None:
    """Write the WAL back into the main file and shrink it to nothing.

    Best effort: a checkpoint that cannot get exclusive access simply does less,
    which is a delay and never a loss -- the WAL keeps the data either way.
    """
    try:
        with _lock:
            conn = get_conn()
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


def maybe_vacuum(force: bool = False) -> dict:
    """Rewrite the database if enough of it has become empty space. Returns why or why not.

    Why a full VACUUM and not auto_vacuum
    -------------------------------------
    SQLite never shrinks a file on DELETE; the pages go on a free list and get
    reused. On its own that is fine -- a table that is pruned and refilled at a
    steady rate reaches an equilibrium and stops growing. It stops being fine
    the moment someone LOWERS a retention or the byte ceiling bites: then a big
    slice of the file is free list forever, and the user who set out to reclaim
    disk watches the file not move at all.

    ``PRAGMA auto_vacuum=INCREMENTAL`` was considered and rejected, twice over.
    Turning it on for an existing database requires a full VACUUM anyway -- the
    very operation it was supposed to avoid -- and once on, every page write
    carries pointer-map maintenance forever, on the ingest path, to save an
    operation that at this size takes seconds and runs a handful of times a
    year. A threshold is the cheaper trade.

    The costs are real and both are guarded. VACUUM takes a write lock for its
    duration: here that is the module lock every other query already goes
    through, so nothing sees a half-rewritten database, and on a file of tens of
    megabytes it is under a second. And it builds a full second copy before
    swapping, so the disk must have room for both -- hence the free-space check,
    which refuses rather than risks filling the very disk this feature exists to
    protect.
    """
    out = {"ran": False, "reason": "", "before": 0, "after": 0, "freed": 0,
           "at": None}
    # Fold the WAL back into the main file before measuring anything. In WAL
    # mode the write-ahead log is real disk that db_bytes() counts, and a VACUUM
    # leaves a big one behind -- so without this the honest answer "we gave 40 MB
    # back" comes out as "the database grew", which is the sort of number that
    # makes a user distrust the whole panel.
    _checkpoint()
    free, sized, _page = free_pages()
    out["before"] = db_bytes()
    if not force:
        if free < VACUUM_MIN_FREE_BYTES or not sized or free / sized < VACUUM_MIN_FREE_RATIO:
            out["reason"] = (f"niet nodig: {free // (1024 * 1024)} MB vrije ruimte "
                             f"in een bestand van {sized // (1024 * 1024)} MB")
            return out
    try:
        room = shutil.disk_usage(str(config.DB_PATH.parent)).free
    except OSError:
        room = 0
    if room and room < out["before"] * VACUUM_MIN_DISK_FACTOR:
        out["reason"] = ("geweigerd: te weinig vrije schijfruimte om veilig te "
                         "herschrijven")
        return out
    try:
        with _lock:
            conn = get_conn()
            conn.commit()           # VACUUM cannot run inside a transaction
            conn.execute("VACUUM")
            conn.commit()
    except sqlite3.Error as err:
        out["reason"] = f"mislukt: {err}"
        return out
    _checkpoint()
    out["ran"] = True
    out["at"] = utcnow()
    out["after"] = db_bytes()
    out["freed"] = max(0, out["before"] - out["after"])
    out["reason"] = f"{out['freed'] // (1024 * 1024)} MB teruggegeven aan de schijf"
    return out


def storage_overview() -> dict:
    """Everything the admin page needs to say what this database is doing.

    One query set rather than a dozen template calls, so the page cannot end up
    quoting a packet count and a time span that were measured a second apart.
    """
    cfg = retention_settings()
    span = qone("SELECT MIN(ts) AS lo, MAX(ts) AS hi, COUNT(*) AS n FROM packets")
    samples = qone("SELECT COUNT(*) AS n, MIN(ts) AS lo, MAX(ts) AS hi FROM samples")
    free, sized, _page = free_pages()
    oldest = span["lo"] if span else None
    newest = span["hi"] if span else None
    # De drie tabellen die iemand anders kan laten groeien. Ze staan hier omdat
    # een plafond dat nergens te zien is, een plafond is waarvan niemand weet
    # dat het geraakt wordt -- en bij ``repeaters`` is dat extra van belang: dat
    # plafond snoeit niet, het weigert, en een weigering die stil blijft is een
    # node die nooit verschijnt zonder dat iemand begrijpt waarom.
    reps = qone("SELECT COUNT(*) AS n, SUM(CASE WHEN is_public=0 THEN 1 ELSE 0 END) "
                "AS hidden FROM repeaters")
    latest_row = qone("SELECT COUNT(*) AS n FROM latest")
    cli_row = qone("SELECT COUNT(*) AS n FROM repeater_cli")
    return {
        **cfg,
        "db_bytes": db_bytes(),
        "sqlite_bytes": sized,
        "free_bytes": free,
        "ceiling_bytes": cfg["max_mb"] * 1024 * 1024,
        "packets": (span["n"] or 0) if span else 0,
        "oldest": oldest,
        "newest": newest,
        "effective_days": _span_days(oldest, newest),
        "samples": (samples["n"] or 0) if samples else 0,
        "samples_oldest": samples["lo"] if samples else None,
        "samples_newest": samples["hi"] if samples else None,
        "latest_rows": (latest_row["n"] or 0) if latest_row else 0,
        "cli_rows": (cli_row["n"] or 0) if cli_row else 0,
        "repeaters": (reps["n"] or 0) if reps else 0,
        "repeaters_hidden": (reps["hidden"] or 0) if reps else 0,
        "repeaters_max": MAX_REPEATERS,
        "latest_max": MAX_LATEST_PER_REPEATER,
    }
