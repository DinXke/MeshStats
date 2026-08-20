"""Wat mág deze gebruiker met déze node -- op één plek.

Waarom dit bestand bestaat
--------------------------
``commanding.route_for`` beantwoordt de ene helft van elke knop op de
beheerpagina: *kan* dit, gegeven de firmware van de node, de brokerverbinding en
wie er voor hem publiceert. Dit bestand is de tegenhanger: *mag* dit, gegeven wie
er ingelogd is. Een knop hoort pas te werken als allebei ja zeggen, en hij hoort
niet te verdwijnen als er één nee zegt -- uitgeschakeld met de reden erbij, want
dat is de lijn die deze site overal aanhoudt.

De reden dat het één module is en geen controle per route: een controle per route
is een controle die bij de volgende route vergeten wordt. Er is precies één
functie die ja of nee zegt (``decide``), er is één tabel die zegt wat een
handeling is (``ACTIONS``), en er is een test die elke schrijvende beheerroute
tegen die tabel houdt.

Het model in vier zinnen
------------------------
1. Een **handeling** is wat iemand doet, niet welke tabel eronder ligt. Elke
   handeling draagt een **risicoklasse**.
2. Een **rol** is niets anders dan een plafond op die klasse. Vier rollen, vier
   klassen, één op één.
3. Een **toekenning** bindt een onderwerp (gebruiker of gebruikersgroep) aan een
   voorwerp (één node, een nodegroep, of alle nodes) met een rol.
4. Een **serverbeheerder** staat buiten dat alles: hij mag alles, overal, en
   alleen hij mag de serverinstellingen, de tokens en de gebruikers.

De risicoklassen
----------------
De indeling is niet verzonnen voor dit bestand. De instellingenschrijver deelt
zijn parameters al in drie klassen in -- gewoon, schrijft merkbaar, kan de
bereikbaarheid afsnijden -- en dat is precies de grens waarop iemand rechten wil
knippen: wel de klok mogen zetten maar geen firmware flashen. Er is er één
vóórgezet, ``kijken``, omdat "mag hier rondkijken" een echte rol is die niets in
gang zet.

``kijken``      verandert niets en kost niets. Een pagina openen, de opgeslagen
                instellingen van een node lezen.
``gewoon``      een handeling met gevolgen die vanzelf overgaan. Uitvragen kost
                zendtijd op een gedeelde band; hernoemen raakt geen apparaat.
``merkbaar``    verandert iets blijvends. De klok van een node zetten schrijft op
                het apparaat; zichtbaarheid omklappen verandert wat de wereld te
                zien krijgt en is niet ongedaan te maken voor wie al keek.
``ingrijpend``  kan de node onbereikbaar maken of gegevens vernietigen. Firmware
                schrijven, een risicovolle instelling zetten, een node wissen.

Verworpen alternatief: losse rechten per handeling, aan te vinken. Dat is
flexibeler en in de praktijk onleesbaar -- een matrix van veertien vinkjes maal
elke node maal elke groep is een matrix waarin niemand nog ziet wie wat mag, en
"wie mocht deze node flashen" is nu juist de vraag die beantwoordbaar moet
blijven. Het plafond-model geeft precies de knip die gevraagd werd en houdt de
beheerpagina leesbaar.
"""
from collections import namedtuple

from . import db

# --- risicoklassen ------------------------------------------------------------
#
# Op volgorde van oplopend risico. De volgorde ís de betekenis: een rol met
# plafond ``merkbaar`` mag alles daaronder ook.
KLASSE_KIJKEN = "kijken"
KLASSE_GEWOON = "gewoon"
KLASSE_MERKBAAR = "merkbaar"
KLASSE_INGRIJPEND = "ingrijpend"

KLASSEN = (KLASSE_KIJKEN, KLASSE_GEWOON, KLASSE_MERKBAAR, KLASSE_INGRIJPEND)

KLASSE_UITLEG = {
    KLASSE_KIJKEN: "verandert niets",
    KLASSE_GEWOON: "gewoon; de gevolgen gaan vanzelf over",
    KLASSE_MERKBAAR: "schrijft merkbaar",
    KLASSE_INGRIJPEND: "kan de bereikbaarheid afsnijden",
}

# --- rollen -------------------------------------------------------------------
#
# Een rol is een plafond en niets anders. Dat maakt "mag deze rol dit?" een
# vergelijking van twee getallen in plaats van een lijst die per rol onderhouden
# moet worden -- en dus onmogelijk om per ongeluk half bij te werken wanneer er
# een handeling bij komt.
ROL_LEZER = "lezer"
ROL_BEDIENER = "bediener"
ROL_TECHNICUS = "technicus"
ROL_BEHEERDER = "beheerder"

ROLLEN = (ROL_LEZER, ROL_BEDIENER, ROL_TECHNICUS, ROL_BEHEERDER)

ROL_PLAFOND = {
    ROL_LEZER: KLASSE_KIJKEN,
    ROL_BEDIENER: KLASSE_GEWOON,
    ROL_TECHNICUS: KLASSE_MERKBAAR,
    ROL_BEHEERDER: KLASSE_INGRIJPEND,
}

ROL_UITLEG = {
    ROL_LEZER: "mag kijken en verder niets",
    ROL_BEDIENER: "mag kijken en uitvragen",
    ROL_TECHNICUS: "mag ook de klok zetten en zichtbaarheid bepalen",
    ROL_BEHEERDER: "mag alles op deze nodes, firmware en verwijderen inbegrepen",
}


def _rang(klasse: str) -> int:
    return KLASSEN.index(klasse) if klasse in KLASSEN else len(KLASSEN)


def rol_rang(rol: str) -> int:
    """Hoe ruim een rol is. -1 voor onbekend, zodat een vervuilde rij nooit wint."""
    return ROLLEN.index(rol) if rol in ROLLEN else -1


# --- handelingen --------------------------------------------------------------

Handeling = namedtuple("Handeling", "scope klasse tekst")

# ``scope`` is 'node' of 'server'. Een node-handeling gaat altijd over één
# repeater en heeft er dus een nodig; een server-handeling gaat over deze
# installatie en is voorbehouden aan een serverbeheerder (zie decide).
#
# ``tekst`` is Nederlands en is de werkwoordzin die in de weigering komt te
# staan: "<tekst> mag u niet". Dat leest op het scherm als een zin en niet als
# een sleutel -- deze redenen belanden in de tooltip van een uitgeschakelde knop,
# en daar is "node.firmware" geen antwoord.
ACTIONS = {
    # -- kijken
    "node.bekijken": Handeling("node", KLASSE_KIJKEN,
                               "deze node bekijken"),
    # -- gewoon
    "node.uitvragen": Handeling("node", KLASSE_GEWOON,
                                "deze node uitvragen"),
    "node.hernoemen": Handeling("node", KLASSE_GEWOON,
                                "deze node hernoemen"),
    "node.instelling.gewoon": Handeling("node", KLASSE_GEWOON,
                                        "een gewone instelling van deze node schrijven"),
    # Het pakketfilter, met opzet een eigen reeks handelingen naast
    # node.instelling.*. Ze lijken op elkaar en ze zijn het niet: een filter
    # bepaalt wat deze node van ANDERMANS verkeer doorlaat, en dat is een andere
    # bevoegdheid dan het zendvermogen van je eigen node bijstellen. Wie de
    # dagelijkse verstoppingen op het mesh opruimt, hoeft daarvoor niet aan de
    # radio te mogen komen.
    #
    # En de lichtste klasse is hier de WEG TERUG: uitzetten en terugzetten op de
    # standaard vallen onder 'gewoon', aanzetten niet. Een filter maakt een node
    # nutteloos zonder hem onbereikbaar te maken -- hij antwoordt nog, hij staat
    # groen op elke pagina, en hij stuurt niets meer door. Dan mag de handeling
    # die dat opheft niet zwaarder afgeschermd zijn dan de handeling die het
    # veroorzaakte.
    "node.filter.gewoon": Handeling("node", KLASSE_GEWOON,
                                    "het pakketfilter van deze node uitzetten of ruimer maken"),
    # -- merkbaar
    "node.klok": Handeling("node", KLASSE_MERKBAAR,
                           "de klok van deze node zetten"),
    "node.zichtbaarheid": Handeling("node", KLASSE_MERKBAAR,
                                    "de zichtbaarheid van deze node wijzigen"),
    "node.beheeradres": Handeling("node", KLASSE_MERKBAAR,
                                  "het beheeradres van deze node wijzigen"),
    "node.instelling.merkbaar": Handeling("node", KLASSE_MERKBAAR,
                                          "een merkbare instelling van deze node schrijven"),
    "node.filter.merkbaar": Handeling("node", KLASSE_MERKBAAR,
                                      "het pakketfilter van deze node aanzetten of strenger maken"),
    # Een schema is geen gewone instelling: het kost niet één keer zendtijd maar
    # elke dag opnieuw, op een band die van iedereen is. Wie het aanzet legt dus
    # een terugkerende last op andermans mesh, en dat hoort een klasse zwaarder
    # te wegen dan de knop die één ronde start ("node.uitvragen").
    "node.schema": Handeling("node", KLASSE_MERKBAAR,
                             "het uitvraagschema van deze node wijzigen"),
    # Herstarten is geen instelling, en daarom een eigen handeling in plaats van
    # meeliften op node.instelling.merkbaar -- die tekst zou in de weigering
    # "een merkbare instelling schrijven" komen te staan bij een knop die niets
    # schrijft.
    #
    # Merkbaar en niet ingrijpend: de node komt uit zichzelf terug, in ongeveer
    # twintig seconden, en er gaat niets blijvend verloren. Wat er wél verloren
    # gaat hoort op de pagina te staan: de gemeten toestanden beginnen weer op
    # onbekend, en een node zonder batterijgevoede klok staat daarna weer op de
    # datum uit zijn firmware.
    "node.herstart": Handeling("node", KLASSE_MERKBAAR,
                               "deze node herstarten"),
    # -- ingrijpend
    "node.firmware": Handeling("node", KLASSE_INGRIJPEND,
                               "de firmware van deze node schrijven"),
    "node.instelling.ingrijpend": Handeling(
        "node", KLASSE_INGRIJPEND,
        "een instelling schrijven die deze node onbereikbaar kan maken"),
    "node.filter.ingrijpend": Handeling(
        "node", KLASSE_INGRIJPEND,
        "een filterregel zetten die al het verkeer van een soort kan blokkeren"),
    "node.verwijderen": Handeling("node", KLASSE_INGRIJPEND,
                                  "deze node verwijderen"),
    # -- deze installatie
    "server.instellingen": Handeling("server", KLASSE_INGRIJPEND,
                                     "de serverinstellingen wijzigen"),
    "server.tokens": Handeling("server", KLASSE_INGRIJPEND,
                               "API-tokens beheren"),
    "server.gebruikers": Handeling("server", KLASSE_INGRIJPEND,
                                   "gebruikers en groepen beheren"),
    "server.audit": Handeling("server", KLASSE_KIJKEN,
                              "het volledige audittrail lezen"),
    "server.firmwarelijst": Handeling("server", KLASSE_GEWOON,
                                      "de releaselijst verversen"),
}


# --- de gebruiker -------------------------------------------------------------

class Gebruiker:
    """Alles wat er over een ingelogd account te weten valt, één keer opgehaald.

    Bestaat zodat een pagina die dertig knoppen tekent niet dertig keer dezelfde
    drie vragen aan de databank stelt. Hij wordt per verzoek gemaakt en niet
    bewaard: rechten die een halve minuut oud zijn, zijn rechten die iemand net
    ingetrokken heeft.
    """

    __slots__ = ("id", "username", "is_superuser", "disabled", "_groups",
                 "_grants", "_nodegroups")

    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.is_superuser = bool(row["is_superuser"])
        self.disabled = bool(row["disabled"])
        self._groups = None
        self._grants = None
        # Ledenlijsten van nodegroepen, per groep-id. De nodepagina vraagt dertien
        # besluiten over dezelfde node, en elk ervan zou anders dezelfde
        # lidmaatschapsvraag opnieuw aan de databank stellen.
        self._nodegroups = {}

    @property
    def group_ids(self) -> set:
        if self._groups is None:
            self._groups = {r["group_id"] for r in db.q(
                "SELECT group_id FROM user_group_members WHERE user_id=?", (self.id,))}
        return self._groups

    @property
    def grants(self) -> list:
        """Elke toekenning die op dit account slaat, rechtstreeks of via een groep."""
        if self._grants is None:
            rows = db.q("SELECT * FROM grants WHERE (subject_type='user' AND subject_id=?)"
                        " OR subject_type='group'", (self.id,))
            mine = self.group_ids
            self._grants = [r for r in rows
                            if r["subject_type"] == "user" or r["subject_id"] in mine]
        return self._grants

    def nodes_in(self, group_id: int) -> set:
        if group_id not in self._nodegroups:
            self._nodegroups[group_id] = _node_ids_in_group(group_id)
        return self._nodegroups[group_id]


def load(username: str | None):
    """De ingelogde gebruiker, of None als de naam niet (meer) bestaat."""
    if not username:
        return None
    row = db.qone("SELECT * FROM admins WHERE username=?", (username,))
    return Gebruiker(row) if row else None


# --- de beslissing ------------------------------------------------------------

class Besluit(namedtuple("Besluit", "allowed reason rol")):
    """Ja of nee, waarom, en met welke rol.

    ``reason`` is altijd gevuld en altijd Nederlands, ook bij ja: de beheerpagina
    toont bij een node waar iemand beperkte rechten heeft welke rol dat is, en
    "u bent bediener op deze node" is de zin die voorkomt dat een uitgeschakelde
    knop op een bug lijkt.

    ``rol`` staat er als eigen veld naast en wordt niet uit die zin teruggelezen.
    Dat lijkt overbodig zolang de zin de rol noemt, en het is precies het soort
    koppeling dat stukgaat zodra iemand de tekst mooier maakt.

    **Er staat met opzet geen ``__bool__`` op.** Die stond er wel, en hij deed
    precies wat je zou verwachten -- een weigering was onwaar -- en juist daarom
    was hij een val. Een sjabloon dat ``{% if besluit %}`` schrijft bedoelt "is er
    een besluit", want de reden dat het er geen is, is dat de bezoeker niet
    ingelogd is. Met ``__bool__`` viel dat samen met "het besluit is nee", en dan
    slaat de tak die de knop uitschakelt stilletjes over -- bij een weigering,
    precies wanneer het ertoe doet. Nu is elk besluit waar en vraag je expliciet
    naar ``.allowed``.
    """


def _node_ids_in_group(group_id: int) -> set:
    return {r["repeater_id"] for r in db.q(
        "SELECT repeater_id FROM node_group_members WHERE group_id=?", (group_id,))}


def _matches_object(user: Gebruiker, grant, rep) -> bool:
    kind = grant["object_type"]
    if kind == "all":
        return True
    if rep is None:
        return False
    rid = rep["id"] if not isinstance(rep, int) else rep
    if kind == "node":
        return grant["object_id"] == rid
    if kind == "nodegroup":
        # Via de gebruiker en niet rechtstreeks: die onthoudt het antwoord voor de
        # duur van dit verzoek. Zie Gebruiker._nodegroups.
        return rid in user.nodes_in(grant["object_id"])
    return False


def resolve(user: Gebruiker, rep) -> Besluit:
    """Welke rol heeft deze gebruiker op deze node -- of waarom geen.

    De conflictregel, en er is er precies één omdat twee regels op verschillende
    plekken vroeg of laat een andere uitkomst geven:

    **Weigeren wint van toestaan.** Altijd, en ongeacht hoe specifiek de
    toestemming was. Een weigering op "alle nodes" verslaat dus ook een
    toestemming die iemand rechtstreeks op één node gekregen heeft. Dat is de
    minst verrassende kant om fout te gaan: wie een uitzondering intrekt, wil dat
    die intrekking het laatste woord heeft, en niet dat er ergens nog een oudere,
    specifiekere rij ligt die hem overstemt.

    **Onder de toestemmingen wint de ruimste.** Een gebruiker die via zijn groep
    lezer is en rechtstreeks technicus, is technicus. Anders zou het toevoegen
    van iemand aan een groep zijn rechten kunnen verkleinen, en dat is precies
    het soort verrassing waar dit model vanaf moet.

    **Geen toekenning is geen toegang.** Er is geen impliciete rol voor nodes die
    in geen enkele groep zitten en waar niemand iets over gezegd heeft. Zo'n node
    is voor een gewone gebruiker onzichtbaar tot een serverbeheerder er iets over
    zegt. De beheerpagina telt ze daarom apart -- stil wegvallen is hier hetzelfde
    probleem als een repeater die ongemerkt verborgen binnenkomt.

    Een weigering draagt geen rol: ze weigert alles op dat voorwerp. Een
    weigering die zelf weer graduaties heeft ("mag hier hooguit lezer zijn") is
    niet meer te overzien op een pagina, en het geval waarvoor je een weigering
    nodig hebt -- deze ene node niet, hoe dan ook -- is een geval zonder
    graduaties.
    """
    passend = [g for g in user.grants if _matches_object(user, g, rep)]
    weigering = next((g for g in passend if g["effect"] == "deny"), None)
    if weigering is not None:
        return Besluit(False, "een uitdrukkelijke weigering "
                              f"({_voorwerp_tekst(weigering)}) gaat voor elke toestemming",
                       None)
    beste = None
    for g in passend:
        if g["effect"] != "allow":
            continue
        if beste is None or rol_rang(g["role"]) > rol_rang(beste["role"]):
            beste = g
    if beste is None or rol_rang(beste["role"]) < 0:
        return Besluit(False, "u hebt geen rechten op deze node", None)
    return Besluit(True, f"u bent {beste['role']} op deze node "
                         f"({_voorwerp_tekst(beste)})", beste["role"])


def _voorwerp_tekst(grant) -> str:
    kind = grant["object_type"]
    if kind == "all":
        return "via de toekenning op alle nodes"
    if kind == "nodegroup":
        row = db.qone("SELECT name FROM node_groups WHERE id=?", (grant["object_id"],))
        return f"via de nodegroep {row['name']}" if row else "via een nodegroep"
    return "rechtstreeks op deze node"


def decide(user, action: str, rep=None) -> Besluit:
    """Mag deze gebruiker deze handeling, op deze node? De enige plek waar dat besloten wordt.

    ``user`` is een ``Gebruiker``, een gebruikersnaam of None. ``rep`` is een
    repeaterrij (of haar id) en hoort erbij zodra de handeling er een noemt.
    """
    handeling = ACTIONS.get(action)
    if handeling is None:
        # Fail closed. Een tikfout in een routenaam hoort een dichte deur te zijn
        # en geen open.
        return Besluit(False, f"onbekende handeling ({action})", None)

    if isinstance(user, str) or user is None:
        user = load(user)
    if user is None:
        return Besluit(False, "u bent niet ingelogd", None)
    if user.disabled:
        return Besluit(False, "dit account staat uit", None)

    if user.is_superuser:
        return Besluit(True, "u bent serverbeheerder", ROL_BEHEERDER)

    if handeling.scope == "server":
        # Serverhandelingen zijn niet per groep toe te kennen, en dat is een
        # keuze en geen gat. Ze zijn met z'n vijven, en drie ervan (tokens,
        # gebruikers, instellingen) zijn genoeg om zichzelf al het andere te
        # geven. Ze opsplitsen zou een scheiding suggereren die er niet is.
        return Besluit(False, f"alleen een serverbeheerder mag {handeling.tekst}", None)

    if rep is None:
        return Besluit(False, f"{handeling.tekst} vraagt om een node", None)

    besluit = resolve(user, rep)
    if not besluit.allowed:
        return besluit
    plafond = ROL_PLAFOND[besluit.rol]
    if _rang(handeling.klasse) > _rang(plafond):
        # "<handeling> mag u niet" en niet "u mag <handeling> niet": de tekst van
        # een handeling is een werkwoordzin die op het werkwoord eindigt, en de
        # ontkenning hoort er in het Nederlands vóór.
        return Besluit(False, f"{handeling.tekst} mag u niet: dat is "
                              f"'{KLASSE_UITLEG[handeling.klasse]}', en uw rol "
                              f"{besluit.rol} gaat tot '{KLASSE_UITLEG[plafond]}'",
                       besluit.rol)
    return besluit


def rol_op_node(user, rep) -> str | None:
    """De rolnaam, of None. Voor de beheerpagina, die rollen wil tonen."""
    if isinstance(user, str) or user is None:
        user = load(user)
    if user is None or user.disabled:
        return None
    if user.is_superuser:
        return ROL_BEHEERDER
    return resolve(user, rep).rol


def zichtbare_nodes(user, repeaters) -> list:
    """De nodes waarop deze gebruiker minstens mag kijken.

    Filteren en niet grijs maken: een node waar iemand geen enkel recht op heeft,
    is voor hem geen uitgeschakelde knop maar een node die niet van hem is. De
    uitgeschakelde-knop-met-reden geldt binnen een node waar je wél iets mag --
    daar is "waarom kan ik dit niet" een zinnige vraag.
    """
    if isinstance(user, str) or user is None:
        user = load(user)
    if user is None or user.disabled:
        return []
    if user.is_superuser:
        return list(repeaters)
    return [r for r in repeaters if decide(user, "node.bekijken", r).allowed]


def rechten_op(user, rep) -> dict:
    """Elke node-handeling met haar besluit, voor één node.

    De beheerpagina krijgt dit als één woordenboek mee, zodat een sjabloon
    ``rechten['node.firmware']`` vraagt in plaats van zelf te redeneren. Een
    sjabloon dat zelf redeneert is de tweede plek waar het antwoord vandaan komt,
    en de eerste keer dat die twee het oneens zijn staat er een knop die iets
    belooft wat de route weigert.
    """
    if isinstance(user, str) or user is None:
        user = load(user)
    return {naam: decide(user, naam, rep)
            for naam, h in ACTIONS.items() if h.scope == "node"}


def serverrechten(user) -> dict:
    if isinstance(user, str) or user is None:
        user = load(user)
    return {naam: decide(user, naam)
            for naam, h in ACTIONS.items() if h.scope == "server"}


# De knop zelf staat niet hier. Wat een sjabloon nodig heeft is een besluit en
# de vraag of de weg openstaat, en die twee komen uit verschillende hoeken --
# ``commanding.route_for`` zegt wat er kán, ``decide`` wat er mág. Ze hier
# samenvoegen tot één woordenboek leverde een derde vorm op naast de twee die er
# al waren; de sjablonen doen het met de macro in admin/node.html en het filter
# ``mag_attr`` in templating.py, allebei één regel breed.


# --- beheer van het model -----------------------------------------------------
#
# Het aanmaken en weghalen van gebruikers, groepen en toekenningen staat hier en
# niet in de routes: de routes doen de rechtencontrole en het audittrail, en wat
# er daarna in de databank verandert hoort bij de module die weet wat die rijen
# betekenen. Zo blijft er ook maar één plek waar een toekenning gevalideerd wordt.

SUBJECT_TYPES = ("user", "group")
OBJECT_TYPES = ("node", "nodegroup", "all")
EFFECTEN = ("allow", "deny")


def gebruikers() -> list:
    return db.q("SELECT * FROM admins ORDER BY username")


def gebruiker_op_naam(username: str):
    return db.qone("SELECT * FROM admins WHERE username=?", ((username or "").strip(),))


def maak_gebruiker(username: str, pw_hash: str, *, is_superuser: bool = False,
                   door: str = "") -> int:
    """Een nieuw account. Het wachtwoord komt er al gehasht in.

    Met opzet: deze module ziet nooit een wachtwoord in leesbare vorm, en een
    beheerder die er een zet voor iemand anders hoort hem ook niet te kunnen
    teruglezen. Het hashen gebeurt in auth.hash_password, één laag hoger.
    """
    return db.execute(
        "INSERT INTO admins(username, pw_hash, is_superuser, disabled,"
        " created_at, created_by) VALUES(?,?,?,0,?,?)",
        (username.strip(), pw_hash, 1 if is_superuser else 0, db.utcnow(), door))


def aantal_serverbeheerders(behalve: int | None = None) -> int:
    """Hoeveel actieve serverbeheerders er zijn, eventueel op één na.

    Bestaat voor precies één controle, en die is de kern van "sluit Björn niet
    buiten": de laatste serverbeheerder mag zichzelf niet degraderen, uitzetten
    of verwijderen. Zonder die controle is één verkeerd vinkje een installatie
    waar niemand meer bij de gebruikers kan -- en de weg terug loopt dan langs de
    opdrachtregel op de server zelf.
    """
    sql = "SELECT COUNT(*) AS n FROM admins WHERE is_superuser=1 AND disabled=0"
    params = ()
    if behalve is not None:
        sql += " AND id<>?"
        params = (behalve,)
    return db.qone(sql, params)["n"]


def zet_serverbeheerder(user_id: int, waarde: bool) -> None:
    db.execute("UPDATE admins SET is_superuser=? WHERE id=?",
               (1 if waarde else 0, user_id))


def zet_uit(user_id: int, waarde: bool) -> None:
    db.execute("UPDATE admins SET disabled=? WHERE id=?",
               (1 if waarde else 0, user_id))


def verwijder_gebruiker(user_id: int) -> None:
    """Het account weg, en met hem zijn toekenningen en lidmaatschappen.

    Het audittrail blijft. Dat is het hele punt van een audittrail: wat er
    gebeurd is, blijft waar ook nadat de persoon weg is. De naam staat er als
    tekst in en niet als verwijzing, juist zodat dit kan.
    """
    db.execute("DELETE FROM grants WHERE subject_type='user' AND subject_id=?", (user_id,))
    db.execute("DELETE FROM user_group_members WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM admins WHERE id=?", (user_id,))


def gebruikersgroepen() -> list:
    return db.q("SELECT * FROM user_groups ORDER BY name")


def nodegroepen() -> list:
    return db.q("SELECT * FROM node_groups ORDER BY name")


def maak_groep(soort: str, name: str, note: str = "") -> int:
    tabel = "user_groups" if soort == "user" else "node_groups"
    return db.execute(f"INSERT INTO {tabel}(name, note, created_at) VALUES(?,?,?)",
                      (name.strip(), note.strip(), db.utcnow()))


def verwijder_groep(soort: str, group_id: int) -> None:
    tabel = "user_groups" if soort == "user" else "node_groups"
    subject_of_object = "subject_type='group' AND subject_id=?" if soort == "user" \
        else "object_type='nodegroup' AND object_id=?"
    db.execute(f"DELETE FROM grants WHERE {subject_of_object}", (group_id,))
    db.execute(f"DELETE FROM {tabel} WHERE id=?", (group_id,))


def zet_lidmaatschap(soort: str, group_id: int, member_id: int, lid: bool) -> None:
    if soort == "user":
        tabel, kolom = "user_group_members", "user_id"
    else:
        tabel, kolom = "node_group_members", "repeater_id"
    if lid:
        db.execute(f"INSERT OR IGNORE INTO {tabel}(group_id, {kolom}) VALUES(?,?)",
                   (group_id, member_id))
    else:
        db.execute(f"DELETE FROM {tabel} WHERE group_id=? AND {kolom}=?",
                   (group_id, member_id))


def leden(soort: str, group_id: int) -> set:
    if soort == "user":
        return {r["user_id"] for r in db.q(
            "SELECT user_id FROM user_group_members WHERE group_id=?", (group_id,))}
    return _node_ids_in_group(group_id)


def nodes_zonder_groep(repeaters) -> list:
    """Nodes die in geen enkele nodegroep zitten.

    Ze zijn niet stuk en niet verboden -- ze zijn alleen alleen bereikbaar via
    een rechtstreekse toekenning of een toekenning op 'alle nodes'. Dat is de
    valkuil van dit soort modellen: een nieuwe node verschijnt vanzelf in de
    databank (zie db.get_or_create_repeater) en zit dan nergens in, waarna hij
    voor iedereen behalve de serverbeheerder onzichtbaar is. Stil onzichtbaar
    zijn is hier hetzelfde probleem als stil verborgen binnenkomen, dus de
    beheerpagina telt ze.
    """
    in_een_groep = {r["repeater_id"] for r in db.q(
        "SELECT DISTINCT repeater_id FROM node_group_members")}
    return [r for r in repeaters if r["id"] not in in_een_groep]


def toekenningen() -> list:
    """Elke toekenning, met de namen erbij die een pagina wil tonen."""
    return db.q("""
        SELECT g.*,
               CASE g.subject_type WHEN 'user' THEN a.username ELSE ug.name END AS subject_name,
               CASE g.object_type WHEN 'node' THEN r.name
                                  WHEN 'nodegroup' THEN ng.name
                                  ELSE 'alle nodes' END AS object_name
        FROM grants g
        LEFT JOIN admins a ON g.subject_type='user' AND a.id=g.subject_id
        LEFT JOIN user_groups ug ON g.subject_type='group' AND ug.id=g.subject_id
        LEFT JOIN repeaters r ON g.object_type='node' AND r.id=g.object_id
        LEFT JOIN node_groups ng ON g.object_type='nodegroup' AND ng.id=g.object_id
        ORDER BY g.effect DESC, subject_name, object_name
    """)


def maak_toekenning(subject_type: str, subject_id: int, object_type: str,
                    object_id: int | None, role: str | None, effect: str,
                    door: str = "") -> int:
    """Eén toekenning erbij. Weigert alles wat niet in het model past.

    De validatie staat hier en niet in de route, zodat een tweede aanroeper --
    een migratie, een test, een opdrachtregel -- er niet omheen kan. Een
    weigering krijgt geen rol: zie ``resolve`` voor waarom een weigering geen
    graduaties heeft.
    """
    if subject_type not in SUBJECT_TYPES or object_type not in OBJECT_TYPES:
        raise ValueError("onbekend onderwerp of voorwerp")
    if effect not in EFFECTEN:
        raise ValueError("effect moet 'allow' of 'deny' zijn")
    if effect == "allow" and role not in ROLLEN:
        raise ValueError("onbekende rol")
    if object_type == "all":
        object_id = None
    elif not object_id:
        raise ValueError("dit voorwerp vraagt om een node of een nodegroep")
    return db.execute(
        "INSERT INTO grants(subject_type, subject_id, object_type, object_id,"
        " role, effect, created_at, created_by) VALUES(?,?,?,?,?,?,?,?)",
        (subject_type, subject_id, object_type, object_id,
         role if effect == "allow" else None, effect, db.utcnow(), door))


def verwijder_toekenning(grant_id: int) -> None:
    db.execute("DELETE FROM grants WHERE id=?", (grant_id,))
