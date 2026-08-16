/* Changelog of this module (see MESHMANAGER_VERSION in MeshManagerNet.h).
 *
 * Deze module heette MeshStats tot en met 1.12.0. Elke regel hieronder met
 * een versienummer onder 2.0.0 gaat dus over een release die onder die naam
 * verschenen is. Met opzet niet herschreven: een release die nooit bestaan
 * heeft, hoort niet in een changelog te staan.
 *
 * 2.6.0  'radio' kan niet meer van afstand gezet worden, en het filter houdt bij
 *        WAT het weggooit in plaats van alleen HOEVEEL.
 *        DE RADIO EERST, want dat is de regel en niet de functie. Van afstand mag
 *        het zendvermogen gewijzigd worden en verder niets aan de radio: geen
 *        frequentie, geen spreidingsfactor, geen coderingssnelheid, geen
 *        bandbreedte. 'radio' is daarom uit CFG_PARAMS verdwenen; 'tx' blijft.
 *        De asymmetrie die dat draagt: een verkeerde 'tx' maakt een node zwakker
 *        maar bereikbaar, een verkeerde frequentie of modulatie haalt hem van de
 *        lucht -- hij hoort niemand meer en niemand hoort hem, en er is geen
 *        commando dat dat terugdraait omdat er geen weg meer naar binnen is. Dat
 *        koop je niet af met een zwaardere bevestiging.
 *        Het staat in de tabel en niet in de schermen, want die ene lijst is
 *        tegelijk wat de beheerpagina tekent, wat de server mag aanbieden en wat
 *        er over LoRa naar een gemonitorde repeater geschreven kan worden. Eén
 *        regel weghalen sluit alle drie tegelijk; drie schermen die er zelf iets
 *        van vinden, kunnen uit elkaar lopen.
 *        Wat het kost, en dat hoort erbij gezegd: de beheerpagina van de node
 *        blijft over wifi bereikbaar als de radio verkeerd staat -- wifi en LoRa
 *        zijn onafhankelijk -- dus dit was de enige weg die een verkeerde
 *        bandbreedte nog zonder ladder kon rechtzetten. Die weg is nu de seriële
 *        kabel of de mesh-CLI. De controle eromheen (cfgRadioOk en de
 *        grenzentabel) blijft staan, zodat terugzetten één regel is.
 *        EN HET FILTER. Het houdt bij WAT het weggooit en niet alleen HOEVEEL, en
 *        dat reist over MQTT mee naar de site.
 *        Wat erbij komt: de kruising pakkettype x reden (welk type sneuvelt op
 *        welke regel), de druk op de snelheidslimiet per type (in hoeveel
 *        vensters met verkeer beet hij, en wat was de piek), de vrijstellingen
 *        via de ACL per type, en de treffers per geblokkeerd kanaal.
 *        Waarom dit meer zegt dan de zes totalen die er al waren. 'Er is 412 keer
 *        iets weggegooid' vertelt niet welke regel te streng staat. ADVERT dat op
 *        de hoplimiet sneuvelt is een andere diagnose dan GRP_TXT dat op de
 *        snelheidslimiet sneuvelt, en uit één totaal zijn die twee niet te
 *        onderscheiden. Bij de snelheidslimiet geldt dat dubbel: twaalf keer
 *        gebeten in vierduizend vensters is een limiet die ruim staat, twaalf in
 *        veertien er een die structureel verkeer wegsnijdt, en het aantal
 *        weggegooide pakketten kan in beide gevallen hetzelfde zijn. Zonder
 *        noemer is een weggooiteller geen meting maar een getal.
 *        De vrijstelling via de ACL is het cijfer waarmee je merkt dat een filter
 *        strenger staat dan je dacht: staat dat hoog naast een lage
 *        'doorgelaten', dan werkt het filter vooral voor de mensen die er toch al
 *        buiten vielen.
 *        WELKE WEG WELKE GEGEVENS NEMEN, en dat is hier de hoofdregel. Alles
 *        hierboven gaat uitsluitend over MQTT -- wifi of LAN, waar bandbreedte
 *        niets kost. De sweep en de mesh-CLI blijven exact zo zuinig als ze
 *        waren, want daar is elke byte zendtijd op een gedeelde band. De korte
 *        vorm van ~160 byte die in elk statistiekenbericht meereist is
 *        ONVERANDERD; de uitsplitsing komt erachteraan en kost in de praktijk
 *        ~130 byte extra, in het volst denkbare geval 2,6 kB.
 *        Wat er tegelijk gerepareerd is. De filterstand ging via een tijdelijke
 *        buffer van 320 byte, en paste die niet in het bericht, dan viel het HELE
 *        filterobject weg -- waarna de site 'deze node meldt geen filter' toont
 *        over een node waarvan het bericht toevallig een paar buren langer was.
 *        Dat is de faalmodus die dit project het minst wil: een publicatie die
 *        slaagt en stilletjes iets kwijt is. Nu schrijft pf_summary_json()
 *        rechtstreeks in de berichtbuffer met de ruimte die er werkelijk is: de
 *        korte vorm eerst, daarna zoveel uitsplitsing als erin past, en
 *        "trunc":1 als er iets niet meepaste. Wat er staat klopt dus altijd, en
 *        alleen de volledigheid is voorwaardelijk -- met dat voorbehoud erbij.
 *        Elke bijschrijving loopt via pfAppend(), dat 'vol' als toestand kent.
 *        Zonder dat hing de veiligheid aan de vraag of de gereserveerde staart
 *        groot genoeg was voor alle sluittekens die er nog aan kwamen, en één
 *        keer verkeerd betekent dat p voorbij max staat, (max - p) als size_t een
 *        enorm getal is, en de volgende snprintf buiten de buffer schrijft.
 *        Kosten in RAM: 528 byte aan tellers (12x7 kruising, 12x3 vensterstanden,
 *        12 vrijstellingen, plus een treffer per kanaal). Ze worden niet bewaard
 *        over een herstart, net als de bestaande: /filter_prefs beschrijft wat er
 *        gehandhaafd wordt, en een teller die een herstart overleeft zou zeggen
 *        dat er iets gebeurd is sinds een moment dat niemand kan aanwijzen.
 *        'filter reset' wist ze, zoals voorheen.
 *        Aan de kant van de site: de uitsplitsing wordt GEEN metric. Twaalf types
 *        maal zes redenen zijn tweeënzeventig namen, tegen een dak van 128
 *        metrics per bericht en een FIFO van 1000 rijen per repeater -- en het is
 *        een momentopname van een verdeling, geen reeks. Ze gaat in de bestaande
 *        JSON-blob van repeater_filter, één rij per repeater die per definitie
 *        niet groeit. Alleen de druk op de snelheidslimiet wordt een reeks, als
 *        twee metrics (filter_rate_windows en filter_rate_capped), want daar is
 *        het verloop juist de vraag.
 * 2.5.0  De beheerpagina van de node zelf kan nu alles wat de app en het filter
 *        kennen: elke CLI-instelling en het volledige pakketfilter. Tot nu toe
 *        toonde ze status, wifi, MQTT, voeding, monitorlijst en back-up -- en
 *        precies de twee dingen niet die je op een slechte dag nodig hebt.
 *        Waarom dit meer is dan gemak. Deze pagina is de weg die overblijft als
 *        de server, het internet of de broker weg is, en dit project bestaat mede
 *        voor noodcommunicatie. De site kan dit sinds 2.1.0 en 2.3.0 allemaal,
 *        maar de site staat tussen jou en het apparaat in; de eigen pagina van de
 *        node staat dat niet. Een repeater op een dak met een filter dat te streng
 *        staat en een broker die eruit ligt, was tot nu toe alleen met een seriële
 *        kabel of over de mesh-CLI recht te zetten.
 *        Eén lijst, geen tweede. Beide schermen worden volledig getekend uit wat
 *        de firmware zelf publiceert: GET /api/cfg voor de parameters met hun
 *        type, grenzen, keuzelijst en risicoklasse, GET/POST /api/filter voor het
 *        filter. Er staat dus geen tabel in de pagina die naast die van de
 *        firmware kan gaan lopen -- dezelfde afspraak die de server al hanteert.
 *        Wat /api/cfg erbij kreeg: ?values=1 zet achter elke parameter wat er NU
 *        in de node staat, zodat de velden voorgevuld zijn en 'zet dit op wat het
 *        al is' een proefrit is over de hele schrijfweg. Zonder die vlag is het
 *        antwoord byte voor byte wat het was, want de server cachet die lijst en
 *        een gecachte huidige waarde is een verkeerde huidige waarde. Ook nieuw
 *        in dat antwoord: de vier deelgrenzen van 'radio' reizen mee in
 *        'choices', uit dezelfde tabel als cfgRadioOk(), zodat de pagina vier
 *        velden met elk hun eigen minimum en maximum kan tekenen in plaats van
 *        één tekstvak waarin "869.525 250 11 5" overgetypt moet worden.
 *        Ongeldige waarden zijn niet in te typen: opsommingen worden
 *        keuzelijsten met de werkelijke woorden, getallen krijgen hun eigen
 *        grenzen, booleans worden een keuze uit on en off. En na afloop staat er
 *        wat de node ANTWOORDT en niet wat er gevraagd is -- met dezelfde
 *        vergelijking (cfgSameValue) die /api/cfg al gebruikte, dus
 *        'advert.interval 61' meldt eerlijk dat er 60 staat.
 *        De risicoklassen zijn zichtbaar en niet alleen aanwezig. De parameters
 *        staan gegroepeerd onder de drie klassen mét de uitleg erboven, dus vóór
 *        de keuze; de zwaarste klasse vraagt de naam van de node over te typen,
 *        met het gevolg erbij. Dat is een drempel in de browser en niet in de
 *        node, en dat is een bewuste grens: POST /api/cfg kent geen
 *        bevestigingsveld omdat de server langs dezelfde weg schrijft. Wat de
 *        node wél doet -- toetsen en teruglezen -- blijft de controle die telt.
 *        Bij het filter hangt de zwaarte aan de RICHTING en aan waar de handeling
 *        bovenop komt: 'filter off' en 'filter reset' vragen niets, want herstel
 *        mag nooit strakker afgeschermd zijn dan de fout die het terugdraait, en
 *        'filter aan' vraagt de naam zodra er al een regel klaarstaat die een
 *        hele categorie dichtzet. Er is met opzet geen tekstvak voor een
 *        commandoregel: dat zou een CLI op een webpagina zijn, en dan hangt de
 *        risicoweging af van hoe iemand toevallig spelt.
 *        De pagina is ingedeeld in inklapbare secties, en dat zijn <details> en
 *        geen tabbladen. Omklappen, het toetsenbord, de schermlezer en 'zoeken op
 *        deze pagina' doet de browser dan zelf; tabbladen zijn dezelfde functie
 *        voor een omschakelaar in JS, een toestand in CSS en aria-attributen
 *        erbij. Alleen het onthouden kost JavaScript, en dat is vier regels met
 *        dezelfde localStorage-sleutel als de publieke site. Toestand staat open,
 *        de rest dicht.
 *        Wat het kost: de pagina gaat van 25.839 naar 46.086 byte in het
 *        flashgeheugen (+20.247). Daarvan is 980 byte de indeling zelf (714
 *        markup en CSS, 266 het onthouden), 10.263 byte de woordenlijsten voor
 *        twee talen, en 9.004 byte de twee formulieren. Er is op deze pagina geen
 *        gzip-budget zoals bij de companion -- die zit klem tegen de 5760 byte van
 *        CONFIG_LWIP_TCP_SND_BUF_DEFAULT -- want deze wordt ongecomprimeerd met
 *        send_P rechtstreeks uit flash gestuurd en door AsyncWebServer in stukken
 *        over de lijn gezet. De grens is dus de app-partitie en niet de
 *        socketbuffer. De statische buffer van handleCfgList() gaat van 3000 naar
 *        5600 byte, op het slechtste geval berekend in plaats van op het gewone:
 *        de oude maat paste met 122 byte over, en één parameter erbij had de lus
 *        stilletjes laten stoppen met een geldig en onvolledig antwoord.
 *        Wat er NIET in zit. De weg terug blijft de mesh-CLI: 'filter off' en
 *        'filter reset' hebben geen wifi, geen beheerpagina en geen server nodig,
 *        en daar is niets aan veranderd. Er zijn geen nieuwe afhankelijkheden en
 *        geen CDN's -- deze pagina moet het doen zonder internet, want dat is de
 *        hele reden dat ze bestaat. 'prv.key', 'bridge.secret' en 'set freq'
 *        blijven ontbreken om de redenen die bij 2.1.0 staan; die lijst is niet
 *        ruimer geworden, alleen beter bedienbaar.
 * 2.4.0  De schrijfweg over LoRa. Deze node kan één CLI-instelling zetten op een
 *        repeater die hij MONITORT, en leest die parameter daarna terug:
 *        POST /api/moncfg om het te vragen, GET /api/moncfg voor de uitslag, en
 *        'wifi mon set <hex> <param> <waarde>' voor hetzelfde vanaf een kabel of
 *        over de mesh.
 *        Waarvoor dit bestaat. Lezen over LoRa werkt sinds 1.9.0 en schrijven
 *        kon alleen over IP, naar een node van onszelf. De repeater waar dit
 *        project omheen gebouwd is heeft geen IP-pad en krijgt er ook nooit een:
 *        stock MeshCore op een dak, alleen over de radio te bereiken. Die kon je
 *        dus wel uitlezen en niet bijstellen, en de enige manier om er een
 *        zendvermogen op te veranderen was een ladder.
 *        Het aardige is welke node hiervoor nieuwe firmware nodig heeft: DEZE,
 *        de monitor. Het doel hoeft niets te leren, niets te krijgen en niets te
 *        weten -- het krijgt gewoon twee CLI-commando's binnen zoals altijd. Dat
 *        is precies waarom deze weg de juiste is voor een node die maandenlang
 *        geen nieuwe firmware krijgt.
 *        Eén schrijfweg, twee vervoermiddelen, en dat is met opzet geen tweede
 *        schrijfweg. Dezelfde tabel CFG_PARAMS, dezelfde grenzen, dezelfde
 *        risicoklassen, dezelfde waardecontrole -- die laatste is hiervoor uit
 *        handleCfgPost() gelicht tot cfgCheckValue(), zodat er niet twee zeven
 *        zijn die uit elkaar kunnen lopen. Was dat wel gebeurd, dan zou de
 *        soepelste van de twee uitgerekend de weg zijn zonder tweede ingang.
 *        Waarom er teruggelezen wordt en waarom dat hier zwaarder weegt dan over
 *        IP. MeshCore antwoordt "OK" op dingen die het niet overgenomen heeft:
 *        'set lat abc' is een kale atof() die er 0.0 van maakt, en
 *        'advert.interval 61' legt 30 vast (minuten/2 in één byte) en leest terug
 *        als 60. Over IP zie je dat meteen en zet je het terug. Hier is het doel
 *        een node zonder tweede weg naar binnen, en duurt een ronde lang genoeg
 *        dat niemand het uit zichzelf natrekt. Dus is de uitslag niet wat de
 *        'set' antwoordde maar wat de 'get' teruggeeft, met de vraag ernaast.
 *        De vergelijking gebeurt niet in de toestandsmachine maar in
 *        handleMonCfgGet(), met cfgSameValue() -- dezelfde functie die /api/cfg
 *        gebruikt. Anders zouden er twee opvattingen bestaan over of
 *        "869.525 250 11 5" en "869.525,250,11,5" dezelfde waarde zijn, en zou
 *        er een waarschuwing staan bij een radio die goed staat. Een melding die
 *        te vaak afgaat is even onbruikbaar als een die nooit afgaat.
 *        Een nieuwe soort stilte, en die is eerlijk gemeld. Blijft het antwoord
 *        op de 'set' uit, dan weten we NIET of het commando is aangekomen -- het
 *        is de lucht in gegaan en het antwoord kan onderweg zijn gebleven. Dat
 *        heet 'geen_antwoord' en nadrukkelijk niet 'mislukt', want dat laatste
 *        zou iemand laten denken dat er niets gebeurd is. Een login die
 *        onbeantwoord bleef is wél zeker: dan is er niets verstuurd.
 *        Wat het kost, en waarom de minimumpauze hier LAGER ligt dan bij de
 *        sweep. Twee commando's en twee antwoorden, ruwweg een tiende van een
 *        uitleesronde, met de gemeten wachttijden van die ronde (20 s voor het
 *        eerste commando na een login, 12 s daarna, 2 s ertussen, 90 s hard
 *        plafond). Tussen twee schrijfacties zit een minuut waar de sweep er
 *        tien heeft, en dat is de belangrijkste afweging van dit blok: de
 *        handeling die je na een misser meteen wilt doen is de omgekeerde. Wie
 *        'tx 5' zette waar 'tx 20' hoorde moet dat binnen een minuut kunnen
 *        rechtzetten. Herstel mag nooit zwaarder afgeremd worden dan de fout die
 *        het terugdraait -- dezelfde regel die 'filter off' lichter maakt dan
 *        'filter on'.
 *        En anders dan de sweep vraagt deze weg geen werkende broker. De sweep
 *        publiceert zijn uitslag op MQTT en heeft zonder broker niets om voor te
 *        doen; deze antwoordt over HTTP aan wie hem vroeg. monitorLoop() laat
 *        een schrijfactie daarom door waar hij een pollronde tegenhoudt: een
 *        installatie zonder internet hoort een radio-instelling nog te kunnen
 *        rechtzetten.
 * 2.3.0  Een pakketfilter op de repeater, en beheer ervan vanaf de site.
 *        Wat het doet: in MyMesh::allowPacketForward() -- de enige plek waar
 *        deze node beslist over ANDERMANS pakket -- worden floodpakketten
 *        getoetst aan zes soorten regel: hoplimiet per type, snelheidslimiet
 *        per type, minimale padhashgrootte, geblokkeerde kanalen, een
 *        structurele controle op groepstekst, en een type dat helemaal niet
 *        meer doorgaat. Standaard staat alles uit. Zie PacketFilter.h.
 *        Wat het NIET raakt, en dat is het ontwerp: pakketten aan deze node
 *        zelf (die komen nooit langs allowPacketForward), direct gerouteerde
 *        pakketten (die horen bij een gevestigde route), en pakketten waarvan
 *        de bestemmings- of afzenderhash een client uit de ACL is. Wie deze
 *        node mag beheren blijft dus werken terwijl er een filter aan staat, en
 *        je kunt jezelf er niet mee buitensluiten.
 *        Waarom dit gevaarlijker is dan het lijkt, en wat daartegen staat. Een
 *        filter is de zeldzame instelling die een node nutteloos maakt zonder
 *        hem onbereikbaar te maken: hij antwoordt, hij adverteert, hij staat op
 *        elke pagina groen, en hij stuurt niets meer door. Je merkt het pas als
 *        iemand klaagt. Daarom drie dingen. 'filter off' en 'filter reset'
 *        werken over de mesh-CLI, de weg die als laatste wegvalt. De
 *        filterstand reist mee met ELK statistiekenbericht (de korte vorm, ~160
 *        byte) in plaats van met de dagelijkse instellingenronde, zodat de site
 *        het dezelfde minuut ziet. En in veilige modus worden de regels wel
 *        gelezen maar niet gehandhaafd -- een node die drie keer opnieuw
 *        opstartte is een node waarvan je de configuratie niet vertrouwt, en
 *        dit is de enige instelling daarop die hem gezond laat lijken terwijl
 *        hij niets doet. Het bestand wordt daarbij niet herschreven, dus een
 *        schone start handhaaft weer wat de beheerder koos.
 *        Waarom /api/filter een commandoregel aanneemt en geen sleutel met een
 *        waarde, zoals /api/cfg wel doet: een filter is drie tabellen en een
 *        lijst, geen sleutel/waardepaar. Dat door een sleutel/waarde-endpoint
 *        persen levert sleutels op als 'rate.05.limit' en verspreidt de
 *        grammatica van het filter over de server, dit bestand en de parser --
 *        drie plaatsen die op de dag dat het misgaat niet meer hetzelfde
 *        denken. Nu is er een parser, en wat je op de beheerpagina aanklikt is
 *        te lezen als het commando dat je over de mesh had kunnen typen. Dat is
 *        precies wat je nodig hebt als je het vanaf een seriele kabel moet
 *        terugdraaien.
 *        Waar dit afwijkt van de beschrijving die het volgt (Dutch-MeshCore,
 *        docs/packet_filter_reference.md; hun code is NIET overgenomen, zie
 *        docs/contributing.md): een kanaal blokkeren gaat op sleutel of hash en
 *        niet op naam, want een repeater ziet alleen sha256(sleutel)[0] -- een
 *        byte, die ongeveer een kanaal op 256 deelt met een ander. En
 *        'misvormd' toetst alleen wat zonder sleutel te toetsen valt: lengte en
 *        blokuitlijning. De tijdstempel- en UTF-8-controles uit de beschrijving
 *        vragen de leesbare tekst, en die vraagt een kanaalsleutel die een
 *        repeater niet heeft.
 * 2.2.0  De sweep vraagt ook 'ver', zodat de site van elke gemonitorde node de
 *        MeshCore-versie kent en niet alleen van de nodes die zelf publiceren.
 *        Het commando is 'ver' en niet 'show version': CommonCLI.cpp regel 271,
 *        `memcmp(command, "ver", 3)`, antwoordt met "%s (Build: %s)" uit
 *        getFirmwareVer() en getBuildDate(). Op een node met deze module ervoor
 *        komt er meer terug, want mmnet_handle_command() vangt 'ver' af en zet
 *        er de moduleversie voor: "<naam> v<module> - MeshCore <fw> (Build:
 *        <datum>)". Eén vraag, twee kolommen -- en dus geen tweede ronde
 *        zendtijd voor de moduleversie, wat op een dak het verschil is tussen
 *        een regel erbij en een besluit.
 *        Waarom dit meer is dan een kolom: met de upgradeweg erbij bepaalt de
 *        MeshCore-versie wat een node aankan. flood.max.unscoped bestaat niet op
 *        oudere firmware, en zonder deze regel merk je dat pas achteraf aan een
 *        '??'-antwoord waar geen versie bij staat om het aan op te hangen. Het
 *        is de tegenhanger van de bouwomgeving bij het schrijven van een image:
 *        de site weigert daar al te gokken, en dit is dezelfde vraag voor de CLI.
 *        Eerst in de tabel, met opzet, want het is de vraag die betekenis geeft
 *        aan de rest ervan. De sweep gaat daarmee van negentien naar twintig
 *        parameters: nominaal 286 s tegen een cap van 360 s, dus de marge blijft
 *        ruim. Bij vierentwintig is hij op, en dat staat er nu bij.
 * 2.1.0  De site kan een instelling schrijven in plaats van alleen lezen: POST
 *        /api/cfg met een sleutel en een waarde, GET /api/cfg voor welke
 *        sleutels dit image toelaat, van welk type, tussen welke grenzen en in
 *        welke risicoklasse.
 *        Het is het hele oppervlak van handleSetCmd() in CommonCLI.cpp --
 *        achtentwintig parameters -- en met opzet geen met de hand gekozen
 *        veilig hoekje. Die eerste opzet liet alleen toe wat de bereikbaarheid
 *        niet kon afsnijden, en dat was veilig en naast de kwestie: juist de
 *        instellingen die je op afstand nodig hebt -- zendvermogen,
 *        radioparameters -- zijn de gevaarlijke, en ze weglaten haalt het risico
 *        er niet uit maar zorgt dat iemand een ladder haalt en hetzelfde doet met
 *        minder zorg en zonder dat het ergens vastligt. Het risico verhuist dus
 *        van weglaten naar afvangen: elke parameter draagt een risicoklasse, en
 *        de bedieningskant hangt daar de zwaarte van de bevestiging aan op.
 *        Waarom een tabel in de firmware en niet gewoon de CLI doorgeven. De
 *        telnetconsole doet dat laatste, en daar mag het: er zit een mens achter
 *        die een wachtwoord intypte. Een knop op een webpagina wordt aangeklikt,
 *        soms op de verkeerde regel. De tabel staat hier en niet in de server,
 *        want de server is te bewerken door wie de site draait en dit is wat er
 *        werkelijk tussen een klik en de radio staat. De server houdt dan ook
 *        geen tweede lijst maar haalt deze op: één waarheid, en een parameter
 *        die deze firmware niet kent kan niet worden aangeboden.
 *        Drie dingen ontbreken, en niet per ongeluk. 'prv.key' vervangt de
 *        identiteit van de node -- geen instelling maar een andere node, waarna
 *        elke contactlijst, ACL en monitorregel elders in het mesh naar iemand
 *        wijst die niet meer bestaat. 'bridge.secret' komt bij het teruglezen
 *        gewoon weer tevoorschijn, en een wachtwoord dat in een logregel geweest
 *        is, is weg. En 'set freq' laat MeshCore alleen toe vanaf de seriële
 *        kabel (sender_timestamp == 0), wat deze weg met opzet niet is;
 *        frequentie hoort bij de andere drie radiowaarden en gaat via 'radio',
 *        dat wél gecontroleerd wordt.
 *        Er wordt teruggelezen in plaats van "OK" te geloven, en dat is niet
 *        theoretisch. MeshCore's 'set lat' is een kale atof(), dus atof("noord")
 *        is 0.0 -- een node die OK antwoordt en daarna beweert in de Golf van
 *        Guinee te staan. En 'set advert.interval' bewaart minuten/2 in één byte
 *        terwijl 'get' weer verdubbelt, dus 61 komt terug als 60, ook met "OK".
 *        Het antwoord draagt daarom 'asked' en 'applied' apart, plus een vlag of
 *        ze gelijk zijn.
 *        Vergelijken gebeurt op waarde en niet op tekst, wat pietluttig klinkt
 *        en het verschil is tussen een waarschuwing die iets betekent en een die
 *        niemand leest: 'set radio' neemt spaties en 'get radio' geeft komma's
 *        terug, en 'set dutycycle 50' leest terug als "50.0%". Een strcmp() zou
 *        beide als "niet toegepast" melden.
 *        Eén parameter is gemarkeerd als geheim: 'guest.password'. Die wordt
 *        teruggelezen en vergeleken zoals al het andere -- dat is de hele reden
 *        dat dit endpoint bestaat -- maar de gelezen waarde gaat niet mee terug.
 *        Anders staat het wachtwoord dat je net zette in de HTML van de
 *        beheerpagina, in de browsergeschiedenis en in elke schermafdruk ervan.
 *        De CLI-aanroep geeft een tijdstempel ongelijk aan nul mee. Nul betekent
 *        in MeshCore "van de seriële kabel" en ontgrendelt 'erase', 'get prv.key'
 *        en 'set freq'. Deze weg heeft die niet nodig, dus blijkt de tabel ooit
 *        een gat te hebben, dan is dat gat kleiner.
 *
 * 2.0.1  De eenmalige verhuizing van het topicvoorvoegsel ging nooit af.
 *        Waargenomen op een node die 2.0.0 draaide en netjes zijn cijfers
 *        stuurde -- op meshcore/, waar de server ze ook opvangt, dus er ging
 *        niets verloren en er stond nergens een fout. Alleen was het
 *        terugvalpad daarmee stilletjes permanent geworden.
 *        De oorzaak: cfg_ver kreeg zijn standaard in het DEFAULTS-blok van
 *        loadConfig(), en dat blok draait bij elke start en niet alleen bij een
 *        verse node. Een bestand zonder cfg_ver hield dus de standaard, en de
 *        test 'ben ik ouder dan de huidige versie' was nooit waar. De
 *        standaard is nu 0 en een verse node wordt bijgezet op de plek waar
 *        vaststaat dat er geen configuratiebestand ligt.
 *        MMNET_CFG_VERSION gaat naar 2, want nodes die 2.0.0 al draaien
 *        hebben inmiddels 'cfg_ver:1' opgeslagen, en met alleen de reparatie
 *        zou de verhuizing daar alsnog nooit gebeuren.
 *        Wat dit NIET was: dataverlies. De server luistert naar allebei de
 *        voorvoegsels, en dat vangnet heeft precies gedaan waarvoor het er is.
 * 2.0.0  Alles heet MeshManager: de module, de bestanden, de defines, het
 *        MQTT-topicvoorvoegsel, de sleutel waaronder de versie gepubliceerd
 *        wordt, de kopregel van een backup, de client-id op de broker, het
 *        eigen AP en de naam van de release-images.
 *        Een hoofdversie en geen 1.13.0, omdat dit de enige release is die
 *        eist dat de andere kant eerst om is. Deze firmware publiceert op
 *        meshmanager/; een server van voor die wissel luistert daar niet, en
 *        dan valt de datastroom stil zonder dat er ergens een fout staat.
 *        Server eerst, dan pas flashen -- docs/migration.md zegt het in die
 *        volgorde, en de site zelf toont per node op welk voorvoegsel hij
 *        binnenkomt, zodat het na te kijken is in plaats van te geloven.
 *
 *        WAT ER NIET VAN NAAM VERANDERT, en dat is de belangrijkste
 *        beslissing van deze release: de bestandsnamen op de datapartitie.
 *        /msnet.json, /mspwr.json, /msmon.json, /msfw.json en /adverts.dat
 *        heten nog precies zo. Een OTA schrijft de applicatiepartitie en
 *        laat de datapartitie met rust; dat is juist waarom een node zijn
 *        netwerk en wachtwoord over een upgrade heen houdt. Zouden die
 *        namen meeverhuizen, dan komt de node terug zonder WiFi-gegevens,
 *        zonder brokerinstellingen en zonder monitorlijst -- dus als eigen
 *        accesspoint, op een dak, met een ladder als enige weg terug. Een
 *        naam op een flashbestand is cosmetica; die configuratie is dat
 *        niet. De C-namen ernaartoe (MMNET_CFG_FILE en de rest) zijn wel
 *        hernoemd, want die staan in de code en niet op de flash.
 *
 *        Het topicvoorvoegsel verhuist daarom eenmalig vanzelf, van
 *        'meshcore' naar 'meshmanager', maar alleen als er letterlijk de
 *        oude STANDAARD stond: wie bewust iets anders koos, wordt niet
 *        aangeraakt. Anders zou flashen maar de helft van het werk zijn en
 *        moest er per node ook nog een CLI-regel getypt worden, op nodes
 *        die soms alleen over de mesh bereikbaar zijn. Er is nu een cfg_ver
 *        in /msnet.json zodat het maar EEN keer gebeurt: wie hierna met
 *        opzet terugzet naar 'meshcore', blijft staan waar hij zet.
 *
 *        Een backup die onder de oude naam gemaakt is, wordt nog steeds
 *        teruggezet: de kopregel wordt in beide spellingen aanvaard. Zo'n
 *        bestand bevat het sleutelpaar van een node, en firmware die haar
 *        eigen vorige backups weigert, ontdek je op de slechtst denkbare
 *        dag.
 *
 *        De bouwomgeving heet nu heltec_v4_repeater_meshmanager, en de
 *        images heten meshmanager-<env>-<versie>.bin. Een node die nog
 *        1.12.0 draait meldt de OUDE envnaam, dus de site vertaalt die naar
 *        de nieuwe voor ze een image kiest -- zonder dat zou deze release
 *        alleen met een USB-kabel te installeren zijn, wat op een dak geen
 *        upgradeweg is. Zie ENV_ALIAS in server/app/firmware.py.
 *
 *        Nieuwe nodes krijgen 'meshmanager' als standaardwachtwoord voor
 *        het eigen netwerk en de console. Dat raakt geen enkele bestaande
 *        node: die leest zijn wachtwoorden uit /msnet.json, dat blijft
 *        staan.
 *
 * 1.12.0 An upgrade path that tells the truth: POST /api/fw with the image as
 *        the raw body and its SHA-256 as a query parameter, GET /api/fw for
 *        what is installed and what can be gone back to, POST /api/fw/rollback
 *        and 'wifi fw rollback' to actually go back. The image also says which
 *        PlatformIO env it was built for, in /api/status and in the answer of
 *        both /api/fw calls.
 *        Why, with the measurement: an upload to the existing /update of
 *        1.284.538 bytes, sent as 'update=@firmware.bin' without an MD5 field,
 *        was accepted, discarded, and followed by a restart onto the OLD
 *        firmware. The caller saw HTTP 000, because that handler restarts the
 *        node before the response leaves -- and restarts it whether Update.end()
 *        succeeded or not. So the only observable signal, "it rebooted", is
 *        emitted identically by a successful upgrade and by one that wrote
 *        nothing. On a repeater on a roof that is not a rough edge, it is an
 *        upgrade path that lies.
 *        What the new one does differently, in one line: the digest is checked
 *        before the boot partition is switched, only success reboots, and the
 *        answer says which step failed, what was expected, what was found and
 *        how many bytes arrived. The full reasoning, including the two designs
 *        that were rejected (staging the image in SPIFFS first, and rebooting on
 *        failure "to be safe"), sits above fwBody().
 *        Why the old /update stays anyway: it is the fallback for when this new
 *        path is broken, and a recovery route may never depend on the thing you
 *        are recovering from. Same rule that gave 'start ota' its stock
 *        behaviour back in an earlier release.
 *        Why the env name is published and the board name is not enough: the
 *        server picks a release asset per build environment, and matching that
 *        against getManufacturerName() ("Heltec V4.3 OLED") means matching on
 *        upstream prose that differs between boards taking the same binary and
 *        agrees between boards that do not. MESHMANAGER_ENV comes from $PIOENV, so
 *        it is exactly the key the image was built under. It is empty on an
 *        image built without the flag, and that stays empty rather than being
 *        guessed at: the cost of a wrong guess is a bricked node on a roof.
 *        Rollback is possible because the partition table has two application
 *        slots and an OTA never erases the one it is not writing -- so the
 *        firmware from before the last upgrade is still in flash and going back
 *        is one otadata write. Not automatic, deliberately: this node reboots
 *        for reasons that have nothing to do with firmware (a solar cell browns
 *        the board out on a November night), and an automatic rollback would
 *        quietly undo a good upgrade and keep undoing it. Reachability is
 *        already guaranteed by safe mode, which comes up regardless of what the
 *        new image broke; rollback is the repair, and a repair is a decision.
 *        The one thing it cannot survive is DISABLE_BOOTS: at six restarts this
 *        module does not start, so neither does the command. What remains there
 *        is stock MeshCore and 'start ota'.
 * 1.11.0 The sweep collects the region tree again, under the key the site has
 *        always stored it as: 'cmd:region'.
 *        Why it was missing: 1.7.1 took 'region' out of the table because its
 *        answer is a tree and not a value, and publishing a fragment of a table
 *        in a settings column is worse than publishing nothing. That was right.
 *        What was wrong was dropping the tree with it -- it is the only place a
 *        reader can see which regions this node knows, which one is home and
 *        where flooding is denied. So after a LoRa sweep eighteen rows said "32
 *        minutes ago" and that one still said "7 days ago", from the last Home
 *        Assistant read, with nothing on the page to explain the difference.
 *        Why 'cmd:region' and not 'region': 'cmd:<x>' is the site's notation for
 *        "run <x> literally instead of 'get <x>'", and the row in repeater_cli
 *        is named after the configured parameter. Publishing this as "region"
 *        would have created a second row beside the first and left the original
 *        ageing forever -- the same near-miss as the parameter tables drifting
 *        apart in 1.7.2.
 *        How a multi-line answer actually arrives, because assuming would have
 *        cost a mechanism nobody needs: as ONE text message. MeshCore caps the
 *        tree itself at 160 bytes (handleRegionCmd calls exportTo(reply, 160))
 *        and onPeerDataRecv sends the whole reply in a single datagram, which
 *        fits inside MAX_PACKET_PAYLOAD with room to spare. So there is no
 *        collecting-until-quiet here and no widened timeout: one command, one
 *        reply, exactly like every other entry. (The Home Assistant path does
 *        gather several events, because there the CLI output reaches it a line
 *        at a time. Different transport, different problem.) A tree too big for
 *        160 bytes is cut on the far side, not here.
 *        Two things had to give for it. SET_VALUE_MAX went from 32 to 176,
 *        because 160 is the ceiling MeshCore itself imposes; and jsonEsc() now
 *        writes \n, \r and \t instead of dropping them. That second one was the
 *        subtle half: control characters were dropped on purpose, and for a
 *        node name that is right, but here the line breaks and the indentation
 *        ARE the value -- nesting is expressed as leading spaces. Dropping them
 *        would have turned fourteen meaningful lines into one run of region
 *        names, published successfully, and wrong. The two-character escapes
 *        keep the "twice the source is always enough" sizing that every caller
 *        relies on, which is why \u00XX stays rejected for the rest.
 *        Also: MeshCore refuses two ways, "Error..." and "Err - ...", and only
 *        the first was recognised. So 'Err - unknown region' was stored as
 *        though it were a setting, with a fresh timestamp beside it -- an answer
 *        that looks more authoritative than "(geen antwoord)" while meaning
 *        strictly less. Both spellings are now refused, spelled out rather than
 *        matched as "Err", so a node called 'Erratic' survives.
 *        And MON_SET_TOTAL_MS went from 300 s to 360 s. At nineteen parameters
 *        a sweep nominally takes 272 s, so a 300 s cap no longer bounds a
 *        runaway -- it truncates a normal run, reporting 'no answer' for
 *        commands that were never sent. The tree is last in the table for the
 *        same reason: if a budget does run out, that is the entry worth losing.
 * 1.10.0 The site can set this node's clock, and this node then checks the
 *        clocks of the repeaters it monitors: 'time <epoch>' on the cmd topic,
 *        'wifi clock' to read back what happened.
 *        Why the mesh needs this at all: a MeshCore node timestamps the
 *        messages it sends and the adverts it emits from its own clock, and an
 *        ESP32 without a battery-backed RTC starts at whatever it was compiled
 *        or 'clkreboot'-ed with. A repeater on a roof reboots on its own -- flat
 *        battery, watchdog, a lightning-season power cut -- and comes back with
 *        a clock reading May 2024. Everything it says afterwards is stamped
 *        wrong, and nothing on the mesh corrects it, because nothing on the mesh
 *        knows better either. The site does: it runs on a machine whose clock is
 *        disciplined against real time.
 *        The exact command, because guessing it would have been the whole bug:
 *        MeshCore's CLI takes 'time <secs>' with UNIX epoch seconds in UTC
 *        (CommonCLI.cpp, the 'time ' branch -- _atoi of the rest, then
 *        setCurrentTime), answers 'clock' with "HH:MM - D/M/YYYY UTC" (the
 *        'clock' branch), and has a third form, 'clock sync', which sets the
 *        clock from the TIMESTAMP OF THE REQUEST PACKET rather than from text.
 *        All three refuse to go backwards.
 *        Why forward only, and why that refusal is not an upstream quirk to
 *        work around: an advert carries the emitter's clock, and a node that
 *        already knows us drops an advert whose timestamp is not higher than the
 *        one it has (onAdvertRecv in MyMesh.cpp). Move a clock back an hour and
 *        that node is invisible to everyone who knows it for an hour. On a roof
 *        repeater that is worse than any wrong timestamp, so a node that runs
 *        FAST is reported and left alone rather than corrected.
 *        Why the far side gets 'clock sync' and not 'time <epoch>': it is ten
 *        characters against fifteen, and with five bytes of header that is one
 *        16-byte cipher block against two -- a third of the airtime of the
 *        packet, for a value we would have taken from our own clock anyway.
 *        There is also no number to format wrongly.
 *        Why the clock is READ first (one round trip) instead of just synced
 *        (also one round trip): the two cost the same when a node is fine, and
 *        reading is what makes 'this node was four minutes behind' a fact the
 *        site can show instead of a correction nobody can see. It also means
 *        this node never transmits a command that changes somebody's clock on a
 *        guess. The second round trip is spent only when the first one proved it
 *        was needed.
 *        Why a threshold of two minutes: 'clock' answers to the minute, so
 *        anything finer is not measurable over this interface, and the reply
 *        arrives seconds after the far side read it. Two minutes is the smallest
 *        drift this can honestly claim to have seen.
 *        Why this may run on a schedule when the settings sweep may not: one
 *        command and one reply per monitored node per day, against eighteen and
 *        eighteen. That is roughly the cost of a single poll round, which this
 *        node already pays every fifteen minutes.
 * 1.9.1  The name of a monitored repeater is escaped before it goes into a
 *        published message, and so are the six pieces of typed text in
 *        /api/status. Both places printed them between two quotes as they came.
 *        Why this is not cosmetic: a name holding a quote, a backslash or a
 *        control character does not arrive looking odd, it does not arrive at
 *        all. The message stops being JSON, mqtt_ingest.py drops it whole, and
 *        publish() on this side still reports success -- because it was a
 *        success, the broker took the bytes. The repeater then fades out of the
 *        statistics with no error anywhere to connect it to a name somebody
 *        changed weeks earlier. Exactly the shape of the 1.3.0 wrong-topic bug,
 *        and the reason the fix is one helper used everywhere rather than a
 *        quote-stripper at each site.
 *        Why a patch release: no command, field or message shape changed. A
 *        name without special characters produces byte-identical output, so the
 *        server needs to learn nothing and an older server reads a 1.9.1 node
 *        exactly as it read a 1.9.0 one. Only messages that were invalid before
 *        become valid.
 *        jsonEsc() gained two rules while it was being spread around, both for
 *        failures with the same signature. Control characters are dropped
 *        rather than written as \u00XX, because the six-byte form would break
 *        the "twice the source is always enough" sizing every caller relies on.
 *        And a UTF-8 sequence is now copied whole or not at all: names are cut
 *        to a fixed length with strncpy() long before they get here, and half a
 *        character makes a payload that is not valid UTF-8 -- which json.loads()
 *        refuses just as firmly as a stray quote, for a reason nobody would
 *        think to look for. Truncation now always lands on a character
 *        boundary.
 *        Not touched, and worth knowing about: msnet.json and monitors.json are
 *        written with the same printf-style JSON and read back by a parser that
 *        searches for '":"'. A quote in an SSID, a password or a monitor name
 *        corrupts that file rather than a message. Fixing it means escaping on
 *        write AND unescaping on read, and a half-applied change there loses a
 *        stored configuration on a node hanging on a roof -- so it wants its own
 *        release, not a line in this one.
 * 1.9.0  A monitoring node can now read the CLI settings of a repeater it
 *        monitors, over LoRa, and publish them the same way it already
 *        publishes that repeater's statistics: 'settings <sleutel>' on the cmd
 *        topic, or 'wifi mon settings <sleutel>' from any CLI.
 *        Why: 1.8.0 gave the site a way to ask a node for its settings, and
 *        that way only reaches a node that publishes to MQTT itself. The
 *        repeater on the hospital roof does not -- it is read out by the node
 *        in my house and forwarded -- so for exactly the repeater this project
 *        was built around, the button stayed grey and said "doorgestuurd,
 *        alleen de node zelf kan zijn eigen CLI uitlezen". Which was true, and
 *        was also the whole problem: a monitor that can log in and poll can
 *        just as well ask, and it already accepted the answers (TXT_MSG has
 *        been forwarded to this module since 1.4.0) -- it simply never asked.
 *        Why the same parameter table and the same "settings" object: the
 *        server then needs to learn nothing. The one thing it did have to give
 *        is that settings may now come from the node that relays this
 *        repeater's statistics and not only from the repeater itself; see
 *        _handle_settings in mqtt_ingest.py for what that costs.
 *        Why nothing runs on a schedule: our own daily sweep is free
 *        (handleCommand() is a function call), this one is not. Eighteen
 *        requests and eighteen replies on a shared band, half of them paid for
 *        by a solar repeater on a roof. So: on request only, at most one every
 *        ten minutes, two seconds between commands, twelve per answer, and a
 *        sweep that stops after three silences instead of transmitting fifteen
 *        more times into a hole. The full reasoning, including which of Home
 *        Assistant's numbers were copied and which were deliberately not, sits
 *        above MON_SET_FIRST_MS.
 *        Why a failed sweep still publishes: a parameter that was asked and
 *        stayed silent goes out as null and shows up as "(geen antwoord)". The
 *        common cause is worth recognising -- the far side only runs CLI
 *        commands for a client with admin rights, so a read-only monitor logs
 *        in perfectly and is then ignored eighteen times. A login that never
 *        answered publishes nothing at all, because that says nothing about
 *        any parameter and would only throw away what an earlier sweep knew.
 * 1.8.0  The node listens on '<prefix>/<node>/cmd' and accepts exactly two
 *        words there: 'settings' forces a CLI sweep and publishes the result
 *        the moment it is done, 'status' publishes a statistics message now.
 *        Everything else is refused and counted.
 *        Why at all: the site's "fetch settings" button dropped a request in a
 *        queue that only the Home Assistant integration ever emptied. Take
 *        Home Assistant out of the chain -- which is the whole point of a node
 *        publishing straight to MQTT -- and the button did nothing at all,
 *        while the values on the page kept ageing until the daily sweep came
 *        round. The site can now ask the node itself, over the connection that
 *        was already open.
 *        Why not a remote CLI: the temptation was to hand the payload to
 *        handleCommand() and be done, since the telnet console already does
 *        exactly that. But the console asks for a password over a link the
 *        operator controls, while the cmd topic is reachable by anyone holding
 *        broker credentials -- and one 'reboot' in a loop is enough to lose a
 *        repeater on a roof. Two words that only make the node say what it
 *        would have said by itself cannot do that, and they cover the entire
 *        reason this exists.
 * 1.7.2  The sweep asks for flood.max.unscoped as well. The parameter list on
 *        the site only steers the Home Assistant path; this sweep has its own
 *        table, so a parameter added there never showed up for MQTT nodes.
 * 1.7.1  The automatic monitor round never started. passed() reads 0 as 'not
 *        scheduled' and _mon_next_round begins at 0, so MST_IDLE waited on a
 *        deadline that never arrived; only 'wifi mon poll' set one, after which
 *        it looked healthy until the next reboot. Present since 1.2.0, hidden
 *        because every test began with a manual poll. The scheduler now arms
 *        itself, and /api/mon and 'wifi mon' report when the next round is due
 *        so a stalled one is visible without waiting for it.
 *        Also: 'region' no longer publishes the whole region tree as one
 *        multi-line value; region.home and region.default are asked for
 *        separately, and any multi-line answer is refused as a list rather
 *        than a setting.
 * 1.7.0  The CLI settings sweep runs once a day instead of every six hours,
 *        and is now observable: how many parameters answered and how many did
 *        not, when the last sweep ran and when the next is due, the collected
 *        values themselves, and a way to force one -- on the page, in
 *        /api/status and over the CLI. Before this the values only appeared in
 *        the single message following a sweep, one in 1440, which made it
 *        impossible to tell a failed sweep from one that never ran.
 * 1.6.0  Battery-to-interval is now a table the user edits (add/remove rules,
 *        hysteresis that works for any number of them) instead of five fixed
 *        levels, with a floor per mode: 10 s in 'always reachable', 60 s in
 *        power-save where the interval also decides how often the radio wakes.
 *        The page shows what a setting costs -- messages per day and LoRa
 *        packets per hour -- rather than two numbers to combine by hand.
 *        Also: the node reads its own CLI parameters and ships them as a
 *        "settings" object inside a stats message, one parameter per loop pass.
 * 1.5.0  Adverts are cached on the file system (key, name, type, last heard,
 *        coordinates), so node names survive a restart instead of showing bare
 *        hex until the next advert hours later. Written lazily, like the ACL.
 *        Also: a metric that is not actually available is now left out of the
 *        payload rather than published as 0 -- JessaZH was reporting
 *        noise_floor 0, which drew a line diving to zero on a graph where a
 *        gap belonged.
 * 1.4.0  A monitored repeater is now read with three requests instead of one:
 *        status, telemetry (CayenneLPP -> ch<N>_temperature / ch<N>_voltage)
 *        and neighbours, published as one message with a "neighbors" array.
 *        Any of the three may fail without losing the others. Per-type
 *        counters and trace lines say which part came back.
 * 1.3.1  A poll that stalled after a successful login looked exactly like one
 *        whose request was never sent -- both leave polls=1, oks=0, lr=1. Added
 *        a trace of the poll sequence (admin page, 'wifi mon trace', serial),
 *        and one flood retry per step: the status request is the first packet
 *        that depends on the path learned from the login, and nothing had ever
 *        tested that path.
 * 1.3.0  Fixed monitored repeaters never arriving anywhere: their statistics
 *        went to a '<prefix>/<node>/mon' topic that nothing subscribes to, so
 *        publish() succeeded and the data was dropped at the broker. Now on the
 *        ordinary stats topic, with the subject in the payload. Same mistake
 *        fixed for the neighbour list, which belongs inside the stats payload
 *        as a "neighbors" array. Separate polls/reads/published counters so a
 *        gap like that is visible without a sniffer. Chip temperature renamed
 *        mcu_temperature; ch1_temperature stays reserved for a real sensor.
 * 1.2.0  Monitor other repeaters: pick them from the heard list or paste a
 *        public key, log in with a password or via their access list, poll
 *        GET_STATUS over the mesh and publish the result to <prefix>/<node>/mon.
 * 1.1.0  Task watchdog: a hung loop() now becomes a reboot, so the existing
 *        boot-counter safety nets can actually fire. See WDT_TIMEOUT_S below.
 * 1.0.0  MQTT publishing (own stats + every raw packet), battery- and
 *        clock-aware publish interval with hysteresis, power-save WiFi mode
 *        with a forced-on escape hatch, admin page restyled after the public
 *        MeshManager site with light/dark themes and NL/EN translation, own
 *        version reported by 'ver', on the page and in the stats payload.
 */

#include "MeshManagerNet.h"
#include "MyMesh.h"
#include "PacketFilter.h"

#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <AsyncElegantOTA.h>
#include <PubSubClient.h>
#include <target.h>
#include <Update.h>
#include <esp_task_wdt.h>
#include <esp_idf_version.h>
#include <esp_ota_ops.h>
#include <mbedtls/sha256.h>
#include <mbedtls/version.h>
#include <helpers/sensors/LPPDataHelpers.h>   // decodes the telemetry replies

/* De bestandsnamen op de DATAPARTITIE blijven wat ze waren, ook nu de
 * module MeshManager heet. Een OTA raakt die partitie niet aan, en dat is
 * precies waarom een node zijn netwerk en zijn wachtwoord over een upgrade
 * heen houdt. Zouden deze namen meeverhuizen, dan komt de node terug zonder
 * configuratie -- als eigen accesspoint, op een dak. De C-namen zijn wel
 * hernoemd; die staan in de code, niet op de flash. */
#define MMNET_CFG_FILE    "/msnet.json"
#define MMNET_BOOT_FILE   "/msboot"

/* Het MQTT-topicvoorvoegsel. 'meshcore' was de naam van het protocol en van
 * een ander project, niet van dit project; op een gedeelde broker is dat een
 * botsing die staat te wachten, en een ACL kan er niet mee zeggen "dit is
 * van ons". De server luistert tijdens de overgang naar allebei, dus een
 * node die nog niet om is blijft gewoon binnenkomen. */
#define MQTT_PREFIX_DEFAULT  "meshmanager"
#define MQTT_PREFIX_LEGACY   "meshcore"

/* Versie van het CONFIGURATIEBESTAND, niet van de firmware. Bestaat voor
 * precies een ding: een eenmalige verhuizing mag maar een keer gebeuren.
 * Zie loadConfig(). Zonder dit getal zou een beheerder die met opzet
 * terugzet naar het oude voorvoegsel dat bij elke herstart teruggedraaid
 * zien worden, zonder te kunnen zien waarom.
 *   0 = van voor de hernoeming naar MeshManager
 *   1 = topicvoorvoegsel verhuisd (2.0.0 -- deze stap heeft nooit gewerkt)
 *   2 = topicvoorvoegsel verhuisd, nu werkelijk (2.0.1)
 *
 * Waarom er een 2 nodig is en 1 niet volstond. In 2.0.0 stond de standaard voor
 * dit veld in het DEFAULTS-blok van loadConfig(), en dat blok draait bij elke
 * start -- niet alleen bij een verse node. Een oud bestand zonder 'cfg_ver'
 * hield daardoor de standaard 1, en de test 'cfg_ver < 1' was nooit waar: de
 * verhuizing was dode code op precies de nodes waarvoor ze geschreven was.
 * Erger nog, zodra zo'n node daarna iets opsloeg, kwam er 'cfg_ver:1' in het
 * bestand te staan -- een leugen die met de reparatie alleen niet te herstellen
 * is. Vandaar een nieuw nummer: 1 < 2 is waar voor allebei de gevallen, voor
 * een bestand dat nog niets zegt en voor een bestand dat 1 beweert. */
#define MMNET_CFG_VERSION  2

#define SSID_MAX      33
#define PASS_MAX      65
#define USER_MAX      17

/* Mirrors node_name[] in MeshCore's NodePrefs, and is used only to size the
 * escaped copy that goes into /api/status. Deliberately not shared with that
 * struct: this module compiles against several MeshCore versions, and a
 * mismatch here is harmless anyway -- jsonEsc() truncates on a character
 * boundary, so too small a figure costs a few letters on the page and never a
 * broken answer. */
#define NODE_NAME_MAX 32

#define MQTT_HOST_MAX     64
#define MQTT_USER_MAX     32
#define MQTT_PREFIX_MAX   32

/* Received packets wait here until mmnet_loop() can ship them. Eight slots of
 * 255 bytes is ~2 kB of RAM, which buys roughly one burst of traffic; beyond
 * that we would rather lose packets than memory. */
#define MQTT_RX_QUEUE      8
#define MQTT_RX_MAX_LEN  255      // MAX_TRANS_UNIT
#define MQTT_RETRY_MS  15000UL    // do not hammer a broker that will not answer
#define MQTT_DRAIN_MAX     4      // packets per loop pass, so the mesh keeps its turn
/* Largest message we will ever hand to PubSubClient. The stats payload is the
 * big one because it carries the neighbour array: MAX_NEIGHBOURS entries of
 * ~70 characters each plus the envelope. Anything longer is truncated at the
 * source (fewer neighbours) rather than being refused here, because publish()
 * silently drops whatever exceeds its buffer. */
#define MQTT_PUB_MAX    5120

/* Inbound commands. Longer than the longest command we accept, because a
 * payload that does not fit has to be recognisable as 'too long' rather than
 * silently truncated into something that happens to match. The longest we
 * accept is 'settings ' plus a full 64-character public key, so 96 leaves room
 * without ever getting close.
 *
 * The gap is a power budget, not a security measure: every accepted command
 * ends in a publish, and a node on a panel cannot afford one per second because
 * somebody left a script running. Commands arriving inside the gap are dropped,
 * not queued -- 'do it now' loses its meaning if it waits. */
#define MQTT_CMD_MAX          96
#define MQTT_CMD_MIN_GAP_MS  30000UL

#define STA_TIMEOUT_MS      30000UL    // try this long before broadcasting our own SSID
#define STA_RETRY_MS       300000UL    // while in AP mode, retry the network every 5 min
/* Two safety nets, because a fault can also live in starting the web server --
 * safe mode itself would then hang in it.
 *   3 restarts: safe mode (own network + admin page, nothing else)
 *   6 restarts: this module does not start at all. What remains is a plain
 *               MeshCore repeater with its mesh CLI and 'start ota'.
 * Five minutes of continuous uptime counts as a successful start and resets
 * the counter. */
#define SAFE_MODE_BOOTS          3
#define DISABLE_BOOTS            6
#define STABLE_UPTIME_MS   300000UL

/* Cell voltage window used to derive a percentage. Same curve as the rest of
 * this project. It is crude at both ends of a LiPo discharge curve, but it
 * never has to be accurate: it only has to pick one of five buckets. */
#define BATT_EMPTY_MV     3000
#define BATT_FULL_MV      4200

/* Task watchdog. DO NOT REMOVE THIS BECAUSE IT LOOKS REDUNDANT -- it closes a
 * hole the other three safety nets cannot reach.
 *
 * The three nets above (safe mode after 3 restarts, module off after 6, no
 * halt() on radio failure) all key off *restarts*. We watched a sibling node
 * fail in a way that produces none: after a flash it answered on no TCP port
 * at all while ping kept working on and off. That is the signature of a
 * blocked loop(). WiFi and lwip live in their own FreeRTOS tasks and keep
 * answering pings, while the application task stands still. No crash, no
 * backtrace, no restart -- so the boot counter never advances and safe mode
 * never arrives. On a roof that is a dead node.
 *
 * This turns such a hang back into a restart, which is the one event the rest
 * of the machinery does know how to handle: the counter climbs, and a node
 * that keeps hanging lands in safe mode by itself. panic=true is deliberate:
 * the panic handler prints a backtrace before rebooting (the framework is
 * built with PANIC_PRINT_REBOOT), so the next person gets the diagnosis the
 * sibling node never produced.
 *
 * Why 30 s and not the framework default of 5 s: several things legitimately
 * block this task far longer than five seconds, and a spurious reboot loop on
 * a roof is worse than the illness. The long pole is an MQTT connect to a
 * broker given as a hostname -- lwip's DNS wait plus the socket timeout is
 * roughly 15-20 s of blocked loop() with nothing wrong. SPIFFS writes and a
 * flash erase add a few more. Thirty seconds clears that worst realistic case
 * with room to spare, and still brings a hung node back inside half a minute.
 *
 * Note this also relaxes the idle-task watchdog the core installs at 5 s, to
 * the same 30 s. That is intended: the operations above starve the idle task
 * for exactly the same reasons.
 */
#define WDT_TIMEOUT_S       30

#define FORCE_DEFAULT_MIN   30    // 'wifi on' without an argument

// Compiled-in defaults; overridable from the admin page or the CLI.
#ifndef WIFI_SSID
  #define WIFI_SSID ""
#endif
#ifndef WIFI_PWD
  #define WIFI_PWD ""
#endif

struct Config {
  char ssid[SSID_MAX];
  char pass[PASS_MAX];
  char ap_pass[PASS_MAX];
  char user[USER_MAX];              // console login
  char console_pass[PASS_MAX];

  // MQTT
  char mqtt_host[MQTT_HOST_MAX];
  char mqtt_user[MQTT_USER_MAX];
  char mqtt_pass[PASS_MAX];
  char mqtt_prefix[MQTT_PREFIX_MAX];
  uint16_t mqtt_port;
  uint16_t mqtt_enabled;
  uint16_t mqtt_rx;                 // also forward every received packet
  uint16_t cfg_ver;                 // zie MMNET_CFG_VERSION

  /* Power management. All of it is tunable rather than compiled in: the right
   * numbers depend on the panel, the cell and the season, and they have to be
   * changeable over the mesh without a reflash. */
  uint16_t pwr_mode;                // 0 = always reachable, 1 = power save
  uint16_t pwr_window;              // seconds reachable after waking
  uint16_t wifi_sleep;              // modem-sleep while associated
  uint16_t tx_power;                // dBm, 0 = leave the driver default alone
  uint16_t bat_full, bat_high, bat_norm, bat_crit;   // level boundaries in %
  uint16_t bat_hyst;                // % past a boundary before the level moves
  uint16_t bat_live;                // % above which raw packets go out at once
  uint16_t bat_mon;                 // % below which polling other repeaters stops
  /* Minutes, not seconds: a day is 86400, which does not fit in a uint16_t and
   * would silently wrap to under six hours. */
  uint16_t set_iv_min;              // minutes between CLI settings sweeps
  uint16_t full_hold;               // minutes above bat_full before 'full' counts
  uint16_t iv_full, iv_high, iv_norm, iv_low, iv_crit;   // publish interval, secs
  uint16_t night_from, night_to;    // night window, hours UTC
  uint16_t night_factor;            // interval multiplier during that window
};

enum WifiState { WIFI_TRYING, WIFI_OK, WIFI_FALLBACK_AP };
enum PwrMode { PWR_ALWAYS = 0, PWR_SAVE = 1 };


static const char HEXCHARS[] = "0123456789abcdef";

static FS *_fs = nullptr;
static MyMesh *_mesh = nullptr;
static Config _cfg;
static AsyncWebServer _server(80);
static WiFiServer _console(23);
static WiFiClient _client;

static WifiState _state = WIFI_TRYING;
static unsigned long _state_since = 0;
static unsigned long _last_retry = 0;
static bool _safe_mode = false;
static bool _disabled = false;
static bool _boot_cleared = false;
static bool _started = false;
static char _ap_ssid[SSID_MAX];
static char _node_hex[13];

// Power management state.
static bool _asleep = false;
static unsigned long _wake_at = 0;      // when to bring WiFi back up
static unsigned long _awake_until = 0;  // end of the reachability window
static unsigned long _force_until = 0;  // 'wifi on <min>' overrides everything
static uint8_t _level = 0;      // index into the power rules table
static uint8_t _batt_pct = 0;
static uint16_t _batt_mv = 0;
static bool _batt_known = false;
static unsigned long _full_since = 0;   // first moment the cell read as full
static unsigned long _batt_read_at = 0;
static bool _published_this_wake = false;

/* The main loop runs thousands of times a second and the battery does not move
 * that fast. On some boards reading it also switches a divider on, so polling
 * it every pass would itself cost energy. */
#define BATT_POLL_MS   10000UL

// MQTT state.
static WiFiClient _mqtt_net;
static PubSubClient _mqtt(_mqtt_net);
static unsigned long _mqtt_last_try = 0;
static unsigned long _mqtt_last_push = 0;
static uint32_t _stats_count = 0;
static uint32_t _rx_count = 0;
static uint32_t _drop_count = 0;
static uint32_t _fail_count = 0;
/* Error as a code, not a sentence: the admin page speaks two languages and
 * translates it itself. "" | "conn" | "stats" | "pkt" */
static const char *_mqtt_err = "";
static int _mqtt_err_rc = 0;

/* Inbound commands. The callback runs inside _mqtt.loop() -- so on this task,
 * not on another one -- but it is still the wrong place to start a sweep or a
 * publish: PubSubClient is in the middle of reading its socket there, and
 * publishing from inside its own read is how you get a reply interleaved with
 * an incoming message. So the callback copies the word and nothing else, and
 * mqttLoop() acts on it a few instructions later. Same discipline as the raw
 * packet queue and the web server flags. */
static volatile bool _cmd_have = false;      // a word is waiting in _cmd_word
static char _cmd_word[MQTT_CMD_MAX];
static unsigned long _cmd_last_ms = 0;       // when we last accepted one
static uint32_t _cmd_count = 0;              // accepted since boot
static uint32_t _cmd_refused = 0;            // unknown word, too long, too soon
static bool _cmd_push = false;               // publish statistics at the first chance
static bool _cmd_after_sweep = false;        // ...but wait for the running sweep first

/* What 'time <epoch>' did to our own clock, kept so 'wifi clock' can answer the
 * only question anybody has about this feature: is anything actually happening?
 * A node that is never told the time and a node that is told a time it refuses
 * look identical from the outside, and the second one is a configuration
 * mistake somebody could fix. */
static uint32_t _clk_sets = 0;               // times we moved our own clock
static uint32_t _clk_noops = 0;              // told a time we already had
static uint32_t _clk_back = 0;               // told a time BEHIND ours; refused
static uint32_t _clk_bad = 0;                // told a time outside the window
static unsigned long _clk_last_ms = 0;       // when the last 'time' arrived
static uint32_t _clk_last_epoch = 0;         // what we were told then
static long _clk_last_delta = 0;             // how far it moved us, in seconds

struct RxItem {
  uint32_t ms;
  int16_t snr4;      // SNR times 4, the way the radio reports it
  int16_t rssi;
  uint8_t len;
  uint8_t data[MQTT_RX_MAX_LEN];
};
static RxItem _rx_queue[MQTT_RX_QUEUE];
static volatile uint8_t _rx_head = 0, _rx_tail = 0;

/* The web server runs in its own task. We never write settings from there, but
 * in loop(); these flags hand the work over. */
static volatile bool _apply_wifi = false;
static volatile bool _apply_mqtt = false;
static volatile bool _apply_power = false;
static volatile bool _apply_rules = false;

// Console state
enum ConsoleState { CON_USER, CON_PASS, CON_READY };
static ConsoleState _con_state = CON_USER;
static char _con_line[160];
static size_t _con_len = 0;
static uint8_t _con_tries = 0;
static unsigned long _con_active = 0;

/* A session that is gone still reports as 'connected' on the ESP32 for a
 * while. Without these two timers one aborted connection would close the
 * console for good -- exactly the channel you need when something is wrong.
 *   CON_IDLE_MS     we close a silent session ourselves after this
 *   CON_TAKEOVER_MS if the existing session is quiet longer than this, a new
 *                   connection may take it over instead of being refused
 */
#define CON_IDLE_MS       300000UL
#define CON_TAKEOVER_MS    60000UL

/* millis() wraps after ~49 days. The signed difference keeps every deadline
 * comparison in this file correct across that wrap. A deadline of 0 means
 * 'not scheduled'. */
static bool passed(unsigned long deadline) {
  return deadline != 0 && (long)(millis() - deadline) >= 0;
}

static uint32_t secsLeft(unsigned long deadline) {
  if (deadline == 0 || passed(deadline)) return 0;
  return (uint32_t)((deadline - millis()) / 1000UL);
}

// --------------------------------------------------------------------- clocks

/* The window a time has to fall in before this node will believe it, whether it
 * arrives from the site or is read back off another repeater.
 *
 * The floor is not decoration. MeshCore's own 'clkreboot' sets the clock to
 * 1715770351 -- 15 May 2024 -- and a board that has never been told the time at
 * all starts somewhere near its build date. Both are in the past by more than a
 * year, so one comparison separates 'this clock was never set' from 'this clock
 * has drifted', and those two deserve different words on a page.
 *
 * The ceiling only has to catch nonsense: a millisecond value truncated into 32
 * bits, a parse that read a field it should not have, a typo with an extra
 * digit. Anything past 2100 is not a time anybody meant. */
#define CLOCK_MIN_EPOCH  1735689600UL   // 2025-01-01 00:00:00 UTC
#define CLOCK_MAX_EPOCH  4102444800UL   // 2100-01-01 00:00:00 UTC

/* Smallest difference that makes us touch our own clock. The site publishes
 * daily and the message takes a moment to arrive, so a second or two is the
 * measurement rather than the drift; stepping for that would turn a healthy
 * clock into a stream of pointless corrections in the log. */
#define CLOCK_OWN_MIN_STEP_S  5

static bool clockPlausible(uint32_t epoch) {
  return epoch >= CLOCK_MIN_EPOCH && epoch < CLOCK_MAX_EPOCH;
}

/* Civil date -> UNIX epoch seconds, UTC. Howard Hinnant's days_from_civil, in
 * the shape everybody ends up writing: March-based years, so the leap day is
 * the last day of the year and needs no special case.
 *
 * Written out here rather than pulled from a time library because this file
 * needs exactly one direction of exactly one conversion -- turning the
 * "HH:MM - D/M/YYYY UTC" that a monitored repeater answers back into a number we
 * can subtract. mktime() would have meant dragging in the local-time machinery
 * and its timezone assumptions for that. */
static uint32_t civilToEpoch(int y, int m, int d, int hh, int mm, int ss) {
  y -= (m <= 2) ? 1 : 0;
  int era = (y >= 0 ? y : y - 399) / 400;
  unsigned yoe = (unsigned)(y - era * 400);                       // [0, 399]
  unsigned doy = (unsigned)((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1);
  unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;           // [0, 146096]
  long days = (long)era * 146097 + (long)doe - 719468;            // since 1970-01-01
  return (uint32_t)(days * 86400L + hh * 3600L + mm * 60L + ss);
}

// ------------------------------------------------------------------ watchdog

static bool _wdt_watching = false;
static unsigned long _wdt_ota_deadline = 0;

// A real upload over local WiFi is a matter of seconds; this is the point at
// which we stop believing one is still in progress.
#define WDT_OTA_MAX_MS   300000UL

/* Two different APIs, picked at compile time so a framework upgrade does not
 * quietly break the one safety net that catches hangs. Arduino core 2.x (IDF
 * 4.x, what this build uses) takes seconds plus a panic flag; core 3.x (IDF
 * 5.x) takes a config struct and refuses a second init, hence reconfigure. */
static void wdtBegin() {
#if ESP_IDF_VERSION_MAJOR >= 5
  esp_task_wdt_config_t cfg = {};
  cfg.timeout_ms = WDT_TIMEOUT_S * 1000;
  cfg.idle_core_mask = 0;          // leave idle-task subscriptions as the core set them
  cfg.trigger_panic = true;
  if (esp_task_wdt_init(&cfg) == ESP_ERR_INVALID_STATE) esp_task_wdt_reconfigure(&cfg);
#else
  esp_task_wdt_init(WDT_TIMEOUT_S, true);   // already running at 5 s; this retunes it
#endif
  // Called from setup(), so NULL is the task that also runs loop().
  if (esp_task_wdt_add(NULL) == ESP_OK) _wdt_watching = true;
  Serial.printf("MeshManagerNet: watchdog %s (%d s)\n",
                _wdt_watching ? "actief" : "NIET actief", WDT_TIMEOUT_S);
}

/* An OTA upload writes and erases flash from the async task. Those stretches
 * stop the world for longer than any normal operation, and a watchdog reboot
 * halfway through a firmware write is the one reboot we must not cause. So we
 * step out for the duration instead of trying to guess a timeout that covers
 * it. */
static void wdtFeed() {
  if (Update.isRunning()) {
    /* An abandoned upload (browser closed halfway) never reaches Update.end(),
     * so isRunning() would stay true forever and quietly leave this node
     * without a watchdog -- the exact silent failure this whole thing exists
     * to prevent. Hence a deadline on how long we are willing to believe it. */
    if (_wdt_ota_deadline == 0) _wdt_ota_deadline = millis() + WDT_OTA_MAX_MS;

    if (!passed(_wdt_ota_deadline)) {
      if (_wdt_watching && esp_task_wdt_delete(NULL) == ESP_OK) _wdt_watching = false;
      return;
    }
  } else {
    _wdt_ota_deadline = 0;
  }

  if (!_wdt_watching) {
    if (esp_task_wdt_add(NULL) != ESP_OK) return;
    _wdt_watching = true;
  }
  esp_task_wdt_reset();
}

/* Copies src into dest as the contents of a JSON string. Everything that came
 * from somebody else -- node names off the air, names typed on the admin page,
 * CLI answers from another repeater, an SSID -- goes through here before it is
 * printed between two quotes.
 *
 * Why this matters more than it looks. A name containing a quote does not
 * produce an odd-looking label at the far end: it produces a message that does
 * not parse, and mqtt_ingest.py drops one of those in full. The node then stops
 * appearing in the statistics while every counter on this side keeps saying
 * that publishing succeeded, because it did -- the broker took the bytes. That
 * is the same class of failure as the 1.3.0 wrong-topic bug: no error anywhere,
 * a node that simply disappears. So the rule is that this function must be
 * impossible to get half-right, which is why it also handles the two cases
 * below that the first version did not.
 *
 * Newline, carriage return and tab are written as \n, \r and \t. Every other
 * control character (< 0x20) is dropped rather than escaped as \u00XX.
 *
 * The split looks arbitrary and is not. The \u form turns one input byte into
 * six output bytes, so every caller's "twice the source is always enough"
 * buffer sizing would silently become wrong -- while these three have a
 * two-character form that keeps it exactly right. And there is one value in the
 * sweep whose line breaks and indentation are not decoration but the value
 * itself: cmd:region publishes MeshCore's region tree, where nesting is
 * expressed as leading spaces. Dropping those characters, which is what this
 * function did until 1.11.0, collapsed fourteen meaningful lines into one run
 * of names. A control character in a NODE NAME still carries nothing a reader
 * would miss, so those keep being dropped.
 *
 * A multi-byte UTF-8 character is copied whole or not at all, and anything that
 * is not a valid sequence ends the copy. This is the part that makes truncation
 * safe. Names live in fixed buffers and are copied in with strncpy(), so a name
 * whose last byte lands in the middle of a two-byte character is already half a
 * character before it reaches us -- see meshmanager_on_advert(), which cuts at
 * ADV_NAME_MAX. Passing that half byte-for-byte yields a JSON string that is
 * not valid UTF-8, and json.loads() refuses it exactly as firmly as it refuses
 * a stray quote: the same disappearance, from a cause nobody would think to
 * look for. Stopping on a character boundary costs at most one visible glyph.
 *
 * No allocation and no String, deliberately: this sits in the publish path of a
 * node on a solar panel, next to static buffers, and a helper that can fail on
 * a fragmented heap is not one you want between a reading and the broker. dest
 * is never overrun -- each branch checks that the whole character plus the
 * terminator still fits, and stops rather than writing part of it. */
static void jsonEsc(char *dest, size_t max, const char *src) {
  if (max == 0) return;
  size_t o = 0;

  for (const unsigned char *p = (const unsigned char *)src; *p; ) {
    unsigned char c = *p;

    if (c == '"' || c == '\\') {
      if (o + 2 >= max) break;
      dest[o++] = '\\';
      dest[o++] = (char)c;
      p++;
    } else if (c == '\n' || c == '\r' || c == '\t') {
      /* Written out rather than dropped, since 1.11.0. One value in the sweep
       * is a tree whose line breaks and indentation ARE the value -- see
       * cmd:region -- and dropping them turned fourteen lines into one
       * unreadable run of region names. The two-character form keeps the
       * "twice the source is always enough" sizing every caller here relies on,
       * which is exactly why the other control characters are still dropped
       * instead of written as \u00XX: six bytes per character would break it. */
      if (o + 2 >= max) break;
      dest[o++] = '\\';
      dest[o++] = (c == '\n') ? 'n' : (c == '\r') ? 'r' : 't';
      p++;
    } else if (c < 0x20) {
      p++;                                   // dropped; see above
    } else if (c < 0x80) {
      if (o + 1 >= max) break;
      dest[o++] = (char)c;
      p++;
    } else {
      /* The lead byte says how many bytes belong to this character. The gaps in
       * the ranges are not tidiness: 0xC0/0xC1 and 0xF5..0xFF cannot start a
       * legal sequence at all, and a byte in 0x80..0xBF is a continuation that
       * arrived without its lead. All three mean the text is already damaged,
       * and there is nothing to repair it with -- so we stop here and publish
       * the part that is still a valid string. */
      size_t len = 0;
      if (c >= 0xC2 && c <= 0xDF)      len = 2;
      else if (c >= 0xE0 && c <= 0xEF) len = 3;
      else if (c >= 0xF0 && c <= 0xF4) len = 4;
      if (len == 0) break;

      size_t i = 1;
      while (i < len && (p[i] & 0xC0) == 0x80) i++;
      if (i < len) break;                    // cut short: stop on the boundary
      if (o + len >= max) break;
      for (i = 0; i < len; i++) dest[o++] = (char)p[i];
      p += len;
    }
  }
  dest[o] = 0;
}

// ------------------------------------------------------------------ settings

/* Gezet door loadConfig() als er iets gemigreerd is. Wegschrijven gebeurt in
 * mmnet_begin(): saveConfig() staat verderop in dit bestand, en een migratie
 * die zichzelf niet vastlegt is er een die elke herstart opnieuw gebeurt. */
static bool _cfg_dirty = false;

/* Moet het topicvoorvoegsel van deze node eenmalig mee verhuizen?
 *
 * Zuiver met opzet: geen globals, geen bestandssysteem, alleen de twee dingen
 * waar het antwoord van afhangt. Dat is niet netheid maar de les van 2.0.0 --
 * daar zat deze beslissing verweven in een inleesfunctie van honderd regels,
 * en dat een van haar invoerwaarden nooit de waarde kon hebben die de test
 * verwachtte, was op geen enkele manier te zien. Zo staat de hele voorwaarde
 * op een regel en is ze na te lezen zonder de rest erbij te halen.
 *
 * Twee eisen, en allebei doen ze werk:
 *
 *  - het bestand moet OUDER zijn dan de huidige configuratieversie. Wie na de
 *    verhuizing met opzet terugzet naar het oude voorvoegsel, moet dat kunnen;
 *    zonder deze eis zou elke herstart die keuze stil overschrijven.
 *  - er moet letterlijk de oude STANDAARD staan. Wie zelf een voorvoegsel koos
 *    -- een eigen tak op een gedeelde broker -- wordt niet aangeraakt.
 *
 * Gaat dit onverhoopt toch mis, dan valt er nog steeds geen bericht op de
 * grond: de server luistert in deze periode naar allebei de voorvoegsels. */
static bool mmnetNeedsPrefixMigration(uint16_t cfg_ver, const char *prefix) {
  return cfg_ver < MMNET_CFG_VERSION &&
         strcmp(prefix, MQTT_PREFIX_LEGACY) == 0;
}

static void loadConfig() {
  memset(&_cfg, 0, sizeof(_cfg));
  strncpy(_cfg.ssid, WIFI_SSID, SSID_MAX - 1);
  strncpy(_cfg.pass, WIFI_PWD, PASS_MAX - 1);
  strcpy(_cfg.ap_pass, "meshmanager");
  strcpy(_cfg.user, "admin");
  strcpy(_cfg.console_pass, "meshmanager");

  strcpy(_cfg.mqtt_prefix, MQTT_PREFIX_DEFAULT);
  /* 0 = "we weten nog niet wat voor bestand hier ligt". Dit blok is de basis
   * voor ELKE start en niet het pad van een verse node -- dat onderscheid is
   * precies waar 2.0.0 op stukliep. Een verse node wordt hieronder bijgezet,
   * op de plek waar vaststaat dat er geen configuratiebestand is. */
  _cfg.cfg_ver = 0;
  _cfg.mqtt_port = 1883;
  _cfg.mqtt_enabled = 0;
  _cfg.mqtt_rx = 1;

  _cfg.pwr_mode = PWR_ALWAYS;   // a new install stays reachable until told otherwise
  _cfg.pwr_window = 180;
  _cfg.wifi_sleep = 1;
  _cfg.tx_power = 0;
  _cfg.bat_full = 95;
  _cfg.bat_high = 90;
  _cfg.bat_norm = 70;
  _cfg.bat_crit = 40;
  _cfg.bat_hyst = 3;
  _cfg.bat_live = 85;
  _cfg.bat_mon = 40;
  _cfg.set_iv_min = 1440;           // a day: sixteen CLI calls for values
                                    // that change once in a blue moon
  _cfg.full_hold = 30;
  _cfg.iv_full = 60;
  _cfg.iv_high = 120;
  _cfg.iv_norm = 300;
  _cfg.iv_low = 900;
  _cfg.iv_crit = 3600;
  _cfg.night_from = 22;
  _cfg.night_to = 5;
  _cfg.night_factor = 4;

  /* Geen bestandssysteem of geen configuratiebestand: een verse node. Die
   * heeft niets te verhuizen, dus hij is per definitie bij -- en dat moet hier
   * gezegd worden, want het migratieblok onderaan wordt door deze returns
   * overgeslagen. */
  if (!_fs) { _cfg.cfg_ver = MMNET_CFG_VERSION; return; }
  File f = _fs->open(MMNET_CFG_FILE, "r");
  if (!f) { _cfg.cfg_ver = MMNET_CFG_VERSION; return; }
  String s = f.readString();
  f.close();

  /* Very small parser: we write this file ourselves, so the format is fixed.
   * Anything missing keeps the default above, which is what makes adding a new
   * setting to an already-deployed node harmless. */
  auto grab = [&](const char *key, char *out, size_t max) {
    String pat = String("\"") + key + "\":\"";
    int i = s.indexOf(pat);
    if (i < 0) return;
    i += pat.length();
    int j = s.indexOf('"', i);
    if (j < 0) return;
    String v = s.substring(i, j);
    strncpy(out, v.c_str(), max - 1);
    out[max - 1] = 0;
  };
  auto num = [&](const char *key, uint16_t &out) {
    String pat = String("\"") + key + "\":";
    int i = s.indexOf(pat);
    if (i < 0) return;
    i += pat.length();
    uint32_t v = 0;
    bool any = false;
    while (i < (int)s.length() && s[i] >= '0' && s[i] <= '9') {
      v = v * 10 + (s[i++] - '0');
      any = true;
    }
    if (any) out = (v > 65535) ? 65535 : (uint16_t)v;
  };

  grab("ssid", _cfg.ssid, SSID_MAX);
  grab("pass", _cfg.pass, PASS_MAX);
  grab("ap_pass", _cfg.ap_pass, PASS_MAX);
  grab("user", _cfg.user, USER_MAX);
  grab("console_pass", _cfg.console_pass, PASS_MAX);

  grab("mqtt_host", _cfg.mqtt_host, MQTT_HOST_MAX);
  grab("mqtt_user", _cfg.mqtt_user, MQTT_USER_MAX);
  grab("mqtt_pass", _cfg.mqtt_pass, PASS_MAX);
  grab("mqtt_prefix", _cfg.mqtt_prefix, MQTT_PREFIX_MAX);
  if (_cfg.mqtt_prefix[0] == 0) strcpy(_cfg.mqtt_prefix, MQTT_PREFIX_DEFAULT);
  num("mqtt_port", _cfg.mqtt_port);
  if (_cfg.mqtt_port == 0) _cfg.mqtt_port = 1883;
  num("mqtt_enabled", _cfg.mqtt_enabled);
  num("mqtt_rx", _cfg.mqtt_rx);
  /* Ontbreekt in elk bestand van voor de hernoeming, en dat is precies het
   * signaal: geen cfg_ver betekent 0 betekent "nog niet verhuisd". */
  num("cfg_ver", _cfg.cfg_ver);

  num("pwr_mode", _cfg.pwr_mode);
  num("pwr_window", _cfg.pwr_window);
  num("wifi_sleep", _cfg.wifi_sleep);
  num("tx_power", _cfg.tx_power);
  num("bat_full", _cfg.bat_full);
  num("bat_high", _cfg.bat_high);
  num("bat_norm", _cfg.bat_norm);
  num("bat_crit", _cfg.bat_crit);
  num("bat_hyst", _cfg.bat_hyst);
  num("bat_live", _cfg.bat_live);
  num("bat_mon", _cfg.bat_mon);
  num("set_iv_min", _cfg.set_iv_min);
  if (_cfg.set_iv_min < 5) _cfg.set_iv_min = 5;
  num("full_hold", _cfg.full_hold);
  num("iv_full", _cfg.iv_full);
  num("iv_high", _cfg.iv_high);
  num("iv_norm", _cfg.iv_norm);
  num("iv_low", _cfg.iv_low);
  num("iv_crit", _cfg.iv_crit);
  num("night_from", _cfg.night_from);
  num("night_to", _cfg.night_to);
  num("night_factor", _cfg.night_factor);

  if (_cfg.pwr_window < 30) _cfg.pwr_window = 30;   // shorter is not worth waking for
  if (_cfg.night_factor == 0) _cfg.night_factor = 1;

  /* Eenmalige verhuizing van het topicvoorvoegsel, bij de hernoeming naar
   * MeshManager.
   *
   * Waarom automatisch: anders is flashen maar de helft van het werk en moet
   * er per node ook nog een CLI-regel getypt worden -- op nodes die op daken
   * hangen en waarvan sommige alleen over de mesh bereikbaar zijn. Dan
   * blijft de oude wereld jarenlang half bestaan.
   *
   * Waarom veilig: dit raakt alleen een node die letterlijk de oude
   * STANDAARD had staan. Wie bewust iets anders koos, wordt niet aangeraakt.
   * En de server luistert in deze periode naar allebei, dus zelfs als deze
   * regel het mis heeft, valt er geen bericht op de grond.
   *
   * Waarom maar een keer: wie hierna met opzet terugzet naar 'meshcore',
   * moet dat kunnen. Zonder cfg_ver zou elke herstart die keuze stil
   * overschrijven, en zou niemand begrijpen waarom de instelling niet
   * blijft staan. */
  if (mmnetNeedsPrefixMigration(_cfg.cfg_ver, _cfg.mqtt_prefix)) {
    strcpy(_cfg.mqtt_prefix, MQTT_PREFIX_DEFAULT);
    Serial.printf("MeshManagerNet: topicvoorvoegsel verhuisd van %s naar %s\n",
                  MQTT_PREFIX_LEGACY, MQTT_PREFIX_DEFAULT);
  }
  if (_cfg.cfg_ver < MMNET_CFG_VERSION) {
    _cfg.cfg_ver = MMNET_CFG_VERSION;
    _cfg_dirty = true;      // mmnet_begin() schrijft het weg
  }
}

static void saveConfig() {
  if (!_fs) return;
  File f = _fs->open(MMNET_CFG_FILE, "w");
  if (!f) return;
  f.printf("{\"ssid\":\"%s\",\"pass\":\"%s\",\"ap_pass\":\"%s\","
           "\"user\":\"%s\",\"console_pass\":\"%s\","
           "\"mqtt_host\":\"%s\",\"mqtt_port\":%u,\"mqtt_user\":\"%s\","
           "\"mqtt_pass\":\"%s\",\"mqtt_prefix\":\"%s\","
           "\"mqtt_enabled\":%u,\"mqtt_rx\":%u,\"cfg_ver\":%u,",
           _cfg.ssid, _cfg.pass, _cfg.ap_pass, _cfg.user, _cfg.console_pass,
           _cfg.mqtt_host, _cfg.mqtt_port, _cfg.mqtt_user, _cfg.mqtt_pass,
           _cfg.mqtt_prefix, _cfg.mqtt_enabled, _cfg.mqtt_rx, _cfg.cfg_ver);
  f.printf("\"pwr_mode\":%u,\"pwr_window\":%u,\"wifi_sleep\":%u,\"tx_power\":%u,"
           "\"bat_full\":%u,\"bat_high\":%u,\"bat_norm\":%u,\"bat_crit\":%u,"
           "\"bat_hyst\":%u,\"bat_live\":%u,\"bat_mon\":%u,\"set_iv_min\":%u,\"full_hold\":%u,"
           "\"iv_full\":%u,\"iv_high\":%u,\"iv_norm\":%u,\"iv_low\":%u,\"iv_crit\":%u,"
           "\"night_from\":%u,\"night_to\":%u,\"night_factor\":%u}",
           _cfg.pwr_mode, _cfg.pwr_window, _cfg.wifi_sleep, _cfg.tx_power,
           _cfg.bat_full, _cfg.bat_high, _cfg.bat_norm, _cfg.bat_crit,
           _cfg.bat_hyst, _cfg.bat_live, _cfg.bat_mon, _cfg.set_iv_min, _cfg.full_hold,
           _cfg.iv_full, _cfg.iv_high, _cfg.iv_norm, _cfg.iv_low, _cfg.iv_crit,
           _cfg.night_from, _cfg.night_to, _cfg.night_factor);
  f.close();
}

/* Boot counter: every start increments it, and only STABLE_UPTIME_MS of
 * running resets it. Three starts without ever becoming stable means something
 * is structurally wrong -- then we leave out everything that is not needed. */
static void checkSafeMode() {
  if (!_fs) return;
  uint8_t count = 0;
  File f = _fs->open(MMNET_BOOT_FILE, "r");
  if (f) { count = f.read(); f.close(); }
  if (count > 200) count = 0;          // invalid, start over

  _safe_mode = (count >= SAFE_MODE_BOOTS);
  _disabled = (count >= DISABLE_BOOTS);

  f = _fs->open(MMNET_BOOT_FILE, "w");
  if (f) { f.write((uint8_t)(count + 1)); f.close(); }
}

static void clearBootCount() {
  if (!_fs || _boot_cleared) return;
  File f = _fs->open(MMNET_BOOT_FILE, "w");
  if (f) { f.write((uint8_t)0); f.close(); }
  _boot_cleared = true;
}

// ---------------------------------------------------------------------- wifi

static void startAP() {
  WiFi.mode(WIFI_AP_STA);              // keep the AP up while we retry the STA
  WiFi.softAP(_ap_ssid, _cfg.ap_pass);
  _state = WIFI_FALLBACK_AP;
  _state_since = millis();
  _last_retry = millis();
  _asleep = false;
  Serial.printf("MeshManagerNet: eigen netwerk '%s' actief op %s\n",
                _ap_ssid, WiFi.softAPIP().toString().c_str());
}

static void startSTA() {
  if (_cfg.ssid[0] == 0) { startAP(); return; }
  if (_state != WIFI_FALLBACK_AP) WiFi.mode(WIFI_STA);
  WiFi.begin(_cfg.ssid, _cfg.pass);
  _asleep = false;
  if (_state != WIFI_FALLBACK_AP) {
    _state = WIFI_TRYING;
    _state_since = millis();
  }
  Serial.printf("MeshManagerNet: verbinden met '%s'...\n", _cfg.ssid);
}

/* Applied every time we associate, because a reconnect resets both settings.
 * Modem-sleep is the cheap win here: it lets the radio idle between beacons
 * without giving up reachability. Lowering TX power helps too, but only when
 * the AP is close -- hence off by default. */
static void applyRadioTuning() {
  WiFi.setSleep(_cfg.wifi_sleep != 0);
  if (_cfg.tx_power >= 2 && _cfg.tx_power <= 20) {
    WiFi.setTxPower((wifi_power_t)(_cfg.tx_power * 4));   // API takes quarter dBm
  }
}

// Machine-readable for the admin page; the page owns the wording.
static const char *wifiStateCode() {
  if (_asleep) return "off";
  if (_state == WIFI_OK) return "ok";
  if (_state == WIFI_FALLBACK_AP) return "ap";
  return "try";
}

// Dutch, for the CLI.
static const char *stateNameNl() {
  if (_asleep) return "uit (zuinig)";
  if (_state == WIFI_OK) return "verbonden";
  if (_state == WIFI_FALLBACK_AP) return "eigen netwerk (WiFi onbereikbaar)";
  return "verbinden...";
}

// --------------------------------------------------------- power management

/* Battery percentage over BATT_EMPTY_MV..BATT_FULL_MV. A board that reports no
 * voltage at all is treated as 'unknown', and unknown is treated as mains
 * power further on: a node that cannot measure its cell should not be
 * throttled by a guess. */
int meshmanager_batt_percent(uint16_t mv) {
  if (mv < 2000) return -1;                      // no usable reading
  if (mv <= BATT_EMPTY_MV) return 0;
  if (mv >= BATT_FULL_MV) return 100;
  return (int)(((uint32_t)(mv - BATT_EMPTY_MV) * 100) / (BATT_FULL_MV - BATT_EMPTY_MV));
}

static uint8_t battPercent(bool *known) {
  _batt_mv = board.getBattMilliVolts();
  int pct = meshmanager_batt_percent(_batt_mv);
  *known = (pct >= 0);
  return (pct < 0) ? 0 : (uint8_t)pct;
}

/* Battery percentage -> publish interval, as a table the user owns instead of
 * five levels compiled in. Rules are kept sorted by percentage, highest first,
 * and the last one always sits at 0 so every reading matches something.
 *
 * The floor under an interval depends on the mode, and that is not a detail.
 * In 'always reachable' the interval only decides how often we talk to the
 * broker, so 10 s is a fair floor. In power-save it also decides how often the
 * radio wakes, and a 10 s wake cycle would flatten the very battery this table
 * exists to protect. Both are applied where the interval is used, not where it
 * is stored, so switching modes never silently rewrites what somebody typed. */
#define PWR_RULES_MAX     8
#define PWR_MIN_ALWAYS   10
#define PWR_MIN_SAVE     60
#define MMPWR_FILE       "/mspwr.json"

struct PwrRule {
  uint8_t  pct;      // applies from this battery percentage upwards
  uint16_t secs;     // publish interval
};
static PwrRule _pwr[PWR_RULES_MAX];
static int _pwr_n = 0;

static uint16_t pwrMinInterval() {
  return (_cfg.pwr_mode == PWR_SAVE) ? PWR_MIN_SAVE : PWR_MIN_ALWAYS;
}

// First rule at or below this percentage; the table guarantees a match.
static uint8_t rawRule(uint8_t pct) {
  for (int i = 0; i < _pwr_n; i++) {
    if (pct >= _pwr[i].pct) return (uint8_t)i;
  }
  return (uint8_t)(_pwr_n > 0 ? _pwr_n - 1 : 0);
}

// Highest first, and always a catch-all at 0 so rawRule() cannot fall through.
static void pwrNormalise() {
  for (int i = 1; i < _pwr_n; i++) {          // insertion sort; at most 8 rules
    PwrRule key = _pwr[i];
    int j = i - 1;
    while (j >= 0 && _pwr[j].pct < key.pct) { _pwr[j + 1] = _pwr[j]; j--; }
    _pwr[j + 1] = key;
  }
  if (_pwr_n == 0) {
    _pwr[0].pct = 0; _pwr[0].secs = 3600; _pwr_n = 1;
  } else if (_pwr[_pwr_n - 1].pct != 0) {
    if (_pwr_n < PWR_RULES_MAX) {
      _pwr[_pwr_n].pct = 0;
      _pwr[_pwr_n].secs = _pwr[_pwr_n - 1].secs;
      _pwr_n++;
    } else {
      _pwr[_pwr_n - 1].pct = 0;               // table full: repurpose the last
    }
  }
  if (_level >= _pwr_n) _level = (uint8_t)(_pwr_n - 1);
}

/* Seeds the table from the fixed fields this module used before 1.6.0, so a
 * node that already had those tuned keeps behaving the same across the upgrade
 * instead of quietly reverting to defaults. */
static void pwrDefaults() {
  const uint16_t pcts[] = { _cfg.bat_full, _cfg.bat_high, _cfg.bat_norm, _cfg.bat_crit, 0 };
  const uint16_t ivs[]  = { _cfg.iv_full, _cfg.iv_high, _cfg.iv_norm, _cfg.iv_low, _cfg.iv_crit };
  _pwr_n = 0;
  for (int i = 0; i < 5; i++) {
    _pwr[_pwr_n].pct = (uint8_t)(pcts[i] > 100 ? 100 : pcts[i]);
    _pwr[_pwr_n].secs = ivs[i] < 1 ? 1 : ivs[i];
    _pwr_n++;
  }
  pwrNormalise();
}

static void pwrSave() {
  if (!_fs) return;
  File f = _fs->open(MMPWR_FILE, "w");
  if (!f) return;
  f.print("{\"rules\":[");
  for (int i = 0; i < _pwr_n; i++) {
    f.printf("%s{\"p\":%u,\"s\":%u}", i ? "," : "", _pwr[i].pct, _pwr[i].secs);
  }
  f.print("]}");
  f.close();
}

static void pwrLoad() {
  pwrDefaults();
  if (!_fs) return;
  File f = _fs->open(MMPWR_FILE, "r");
  if (!f) return;                 // no file yet: keep the migrated defaults
  String s = f.readString();
  f.close();

  int n = 0, pos = 0;
  PwrRule tmp[PWR_RULES_MAX];
  while (n < PWR_RULES_MAX) {
    int b = s.indexOf("{\"p\":", pos);
    if (b < 0) break;
    int e = s.indexOf('}', b);
    if (e < 0) break;
    String item = s.substring(b, e + 1);
    pos = e + 1;

    int pi = item.indexOf("\"p\":"), si = item.indexOf("\"s\":");
    if (pi < 0 || si < 0) continue;
    long p = item.substring(pi + 4).toInt();
    long sec = item.substring(si + 4).toInt();
    if (p < 0 || p > 100 || sec < 1 || sec > 65535) continue;
    tmp[n].pct = (uint8_t)p;
    tmp[n].secs = (uint16_t)sec;
    n++;
  }
  if (n > 0) {
    memcpy(_pwr, tmp, sizeof(PwrRule) * n);
    _pwr_n = n;
    pwrNormalise();
  }
}

/* Rules change with hysteresis: one is only left once the percentage is
 * bat_hyst past the boundary. A cell hovering exactly on a threshold is
 * precisely the situation where the panel is marginal and stable behaviour
 * matters most -- flapping between two intervals there would cost energy for
 * nothing. The logic is index-based, so it keeps working whatever the user
 * makes the table: rules are sorted, so a lower index is always the better
 * battery.
 *
 * The top rule has an extra condition: the cell must have read that high for
 * full_hold minutes. 'Full' should mean surplus, not the first sunbeam of the
 * morning. */
static void updatePowerLevel() {
  if (_batt_read_at != 0 && millis() - _batt_read_at < BATT_POLL_MS) return;
  _batt_read_at = millis();

  bool known;
  uint8_t pct = battPercent(&known);
  _batt_pct = pct;
  _batt_known = known;

  if (!known) { _level = 0; return; }   // no cell reading: treat as mains

  uint8_t want = rawRule(pct);
  if (want == 0 && _pwr_n > 1) {
    if (_full_since == 0) _full_since = millis();
    if (millis() - _full_since < (unsigned long)_cfg.full_hold * 60000UL) want = 1;
  } else {
    _full_since = 0;
  }

  if (want < _level) {              // a better battery than the current rule
    if (pct < _pwr[want].pct + _cfg.bat_hyst) want = _level;
  } else if (want > _level) {       // worse
    if (pct + _cfg.bat_hyst >= _pwr[_level].pct) want = _level;
  }
  _level = want;
}

/* Night is a bonus, never a dependency: an unset or implausible clock simply
 * means 'not night', so a wrong RTC can never make this node quieter than the
 * battery rules on their own would. */
static bool isNight() {
  uint8_t h;
  if (!_mesh || !_mesh->getClockHour(&h)) return false;
  if (_cfg.night_from == _cfg.night_to) return false;
  if (_cfg.night_from < _cfg.night_to) return h >= _cfg.night_from && h < _cfg.night_to;
  return h >= _cfg.night_from || h < _cfg.night_to;    // window wraps past midnight
}

static uint32_t currentIntervalSecs() {
  uint32_t iv = (_pwr_n > 0) ? _pwr[_level].secs : 300;
  if (isNight()) iv *= _cfg.night_factor;
  uint16_t lo = pwrMinInterval();
  if (iv < lo) iv = lo;
  return iv;
}

static bool isForced() {
  if (_force_until == 0) return false;
  if (passed(_force_until)) { _force_until = 0; return false; }
  return true;
}

static void wifiSleep() {
  if (_mqtt.connected()) _mqtt.disconnect();
  if (_client) _client.stop();          // no point holding a console we cannot reach
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  _asleep = true;
  _state = WIFI_TRYING;                 // waking starts the state machine over
  _wake_at = millis() + currentIntervalSecs() * 1000UL;
  Serial.printf("MeshManagerNet: wifi uit, volgende ronde over %u s\n",
                (unsigned)currentIntervalSecs());
}

static void wifiWake() {
  _awake_until = millis() + (unsigned long)_cfg.pwr_window * 1000UL;
  _wake_at = 0;
  _published_this_wake = false;
  startSTA();
}

/* Radio silence saves far more than a slower publish interval does, so in
 * power-save mode WiFi is off by default and only comes up long enough to ship
 * what is queued and to stay reachable for pwr_window seconds.
 *
 * Two rules keep this from locking anyone out:
 *   - safe mode never sleeps; that mode exists to be reachable
 *   - a forced window ('wifi on <min>') outranks everything, at any battery
 *     level, and also re-enables the AP fallback so a broken WiFi config still
 *     ends with a network you can reach
 */
static void powerLoop() {
  updatePowerLevel();

  if (_safe_mode || _cfg.pwr_mode == PWR_ALWAYS || isForced()) {
    if (_asleep) wifiWake();
    return;
  }

  if (_asleep) {
    if (passed(_wake_at)) wifiWake();
    return;
  }

  if (!passed(_awake_until)) return;

  /* Waking up and then sleeping again without having said anything would waste
   * the whole round, so a connected broker gets a little extra time to take
   * the backlog and this round's stats. Never more than a minute, though: an
   * unreachable broker must not keep the radio on all day. */
  bool unfinished = (_rx_head != _rx_tail) ||
                    (_cfg.mqtt_enabled && _cfg.mqtt_host[0] && !_published_this_wake);
  if (unfinished && _mqtt.connected() && !passed(_awake_until + 60000UL)) return;

  wifiSleep();
}

/* Whether received packets go out the moment they arrive, rather than a few
 * per pass. Above bat_live there is charge to spare and the point of this node
 * is watching packets move across the map live, so nothing should sit in a
 * queue waiting its turn.
 *
 * Returns a code rather than a bool because the honest answer has three parts,
 * and the page must not promise a live view it cannot deliver:
 *   "on"   forwarding immediately
 *   "batt" battery below the threshold, saving comes first
 *   "save" power-save mode -- WiFi is off between rounds, so 'immediate' is
 *          impossible by construction, whatever the battery says
 */
static const char *liveCode() {
  if (_cfg.pwr_mode != PWR_ALWAYS) return "save";
  if (_batt_known && _batt_pct < _cfg.bat_live) return "batt";
  return "on";
}

static bool liveForwarding() {
  return strcmp(liveCode(), "on") == 0;
}

static const char *powerStateCode() {
  if (isForced()) return "forced";
  if (_cfg.pwr_mode == PWR_ALWAYS || _safe_mode) return "always";
  return _asleep ? "asleep" : "awake";
}

static uint32_t powerSecsLeft() {
  if (isForced()) return secsLeft(_force_until);
  if (_cfg.pwr_mode == PWR_ALWAYS || _safe_mode) return 0;
  return _asleep ? secsLeft(_wake_at) : secsLeft(_awake_until);
}

// Dutch one-liner for the CLI; the admin page builds its own from the codes.
static void powerSummaryNl(char *out, size_t max) {
  uint32_t iv = currentIntervalSecs();
  const char *night = isNight() ? ", nacht" : "";
  const char *st = powerStateCode();

  if (strcmp(st, "forced") == 0) {
    snprintf(out, max, "opgevorderd, nog %u min (elke %u s%s)",
             (unsigned)(powerSecsLeft() / 60 + 1), (unsigned)iv, night);
  } else if (strcmp(st, "always") == 0) {
    snprintf(out, max, "altijd bereikbaar, elke %u s%s", (unsigned)iv, night);
  } else if (strcmp(st, "asleep") == 0) {
    snprintf(out, max, "zuinig, wifi terug over %u s", (unsigned)powerSecsLeft());
  } else {
    snprintf(out, max, "zuinig, nog %u s bereikbaar (elke %u s%s)",
             (unsigned)powerSecsLeft(), (unsigned)iv, night);
  }
}

// ------------------------------------------------------------- CLI settings

/* The node's own configuration, read back from its own CLI and shipped with
 * the statistics. These change once in a blue moon, so they go out far less
 * often than the metrics.
 *
 * NOT on a topic of their own. The receiving side subscribes to exactly two
 * patterns, '<prefix>/+/stats' and '<prefix>/+/rx' -- verified in its
 * mqtt_ingest.py -- so a third leaf would be accepted by the broker and
 * dropped there unread. That is exactly the bug that cost us the monitored
 * repeaters in 1.3.0. These therefore ride along inside an ordinary stats
 * message as a "settings" object: unknown top-level keys are already ignored
 * at the far end, so it is inert until the site learns to read it.
 *
 * One parameter per pass of mmnet_loop(): handleCommand() is cheap but not
 * free, and there is no reason to do sixteen in one go on a node whose first
 * duty is relaying other people's packets. */
/* Room for one value.
 *
 * Every setting on this node answers in a dozen characters or so, and 32 was
 * plenty until cmd:region joined the table in 1.11.0. That one is a tree, and
 * MeshCore caps it at 160 bytes itself -- handleRegionCmd() calls
 * exportTo(reply, 160) -- so 160 plus room for a terminator and a little slack
 * is the honest ceiling rather than a guess.
 *
 * Rejected: a second, larger buffer for the one long parameter, leaving this at
 * 32. It saves about five kilobytes of a two-megabyte part -- a quarter of a
 * percent of the RAM on a node using five percent of it -- and pays for that
 * with two storage paths, two sizing rules and a special case in every loop
 * that walks the table. The day somebody adds a second long parameter, the
 * cheap version is the one that breaks. */
#define SET_VALUE_MAX    176
#define SET_BUF          600

/* Each parameter carries the command that answers it, because they are not all
 * 'get <name>'.
 *
 * 'region' on its own is the odd one: it reports no single setting but dumps
 * the entire region tree via RegionMap::exportTo(), one line per region,
 * indented by depth:
 *
 *     *
 *      eu F
 *       bx F
 *        be^ F
 *         be-vbr F
 *
 * Reading printChildRegions() settles what the markers mean: '*' is simply the
 * name of the wildcard root region, NOT a marker for the active one; '^' marks
 * the home region (here 'be'); ' F' means flooding is allowed and its absence
 * means DENY_FLOOD; indentation is parent/child nesting.
 *
 * Until 1.7.1 that tree was collected under the name "region" and stored as if
 * it were a value, which is what got it refused: a setting is one line, and
 * publishing a fragment of a table in that column is worse than publishing
 * nothing. The two things in there that genuinely ARE settings were given their
 * own one-line commands instead, and that part was right and stays.
 *
 * What was wrong was throwing the tree away with them. It is the only place a
 * reader can see which regions this node knows, which one is home and where
 * flooding is denied -- and the site has had a row for it, under the key
 * 'cmd:region', since the Home Assistant path first fetched it. Dropping it
 * from this table meant that after a LoRa sweep eighteen rows said "32 minutes
 * ago" and that one still said "7 days ago", with nothing on the page to
 * explain why. So since 1.11.0 it is collected again, as a list rather than as
 * a value -- see 'list' below.
 *
 * 'name' is the key exactly as the site stores it, which is why this one entry
 * carries a prefix the others do not. 'cmd:<x>' is the site's own notation for
 * "run <x> literally instead of 'get <x>'" (see _get_param in the Home
 * Assistant integration, which strips those four characters), and the row in
 * repeater_cli is named after the configured parameter. Publishing it as
 * "region" would not update that row -- it would create a second one next to
 * it, and leave the original ageing forever.
 *
 * 'after' is the separator whose tail we keep: " home is be" -> "be". Explicit
 * per parameter rather than a blanket rule, because a node name could itself
 * contain " is ".
 *
 * 'list' says the answer may span lines and that this is not a fault. Only one
 * parameter sets it, and that is the point: every other entry keeps the rule
 * that a multi-line answer is a table which has no business in a settings
 * column, and keeps being refused for it. */
struct SetParam {
  const char *name;      // key in the published object
  const char *cmd;       // CLI command that answers it
  const char *after;     // keep what follows the last occurrence, or NULL
  bool list;             // a multi-line answer is expected, not a fault
};

static const SetParam SET_PARAMS[] = {
  /* Eerst, en dat is een keuze. 'ver' is het antwoord op "wat kan deze node
   * eigenlijk", en dat is de vraag die je stelt vóórdat de rest van de tabel
   * betekenis heeft: flood.max.unscoped bestaat niet op oudere MeshCore, en
   * zonder deze regel merk je dat pas achteraf aan een '??'-antwoord waar geen
   * versie bij staat om het aan op te hangen.
   *
   * Het commando is 'ver' en niet 'show version' -- CommonCLI.cpp regel 271,
   * `memcmp(command, "ver", 3)`, dat antwoordt met "%s (Build: %s)" uit
   * getFirmwareVer() en getBuildDate(). Op een node met deze module ervoor komt
   * er meer terug: mmnet_handle_command() vangt 'ver' af en antwoordt met
   * "<naam> v<module> - MeshCore <fw> (Build: <datum>)". Eén vraag, twee
   * kolommen, en dus geen tweede ronde zendtijd voor de moduleversie.
   *
   * 'cmd:' ervoor omdat het letterlijk uitgevoerd wordt in plaats van als
   * 'get ver' -- dezelfde notatie als cmd:region, en de sleutel waaronder de
   * site het opslaat. */
  { "cmd:ver",               "ver",                       NULL,  false },
  { "name",                  "get name",                  NULL,  false },
  { "role",                  "get role",                  NULL,  false },
  { "radio",                 "get radio",                 NULL,  false },
  { "freq",                  "get freq",                  NULL,  false },
  { "tx",                    "get tx",                    NULL,  false },
  { "af",                    "get af",                    NULL,  false },
  { "repeat",                "get repeat",                NULL,  false },
  { "advert.interval",       "get advert.interval",       NULL,  false },
  { "flood.advert.interval", "get flood.advert.interval", NULL,  false },
  { "flood.max",             "get flood.max",             NULL,  false },
  /* Newer firmwares split the flood budget in two; on one that has not, the
   * "??" reply is refused below and the parameter is simply a miss. */
  { "flood.max.unscoped",    "get flood.max.unscoped",    NULL,  false },
  { "allow.read.only",       "get allow.read.only",       NULL,  false },
  { "rxdelay",               "get rxdelay",               NULL,  false },
  { "txdelay",               "get txdelay",               NULL,  false },
  { "lat",                   "get lat",                   NULL,  false },
  { "lon",                   "get lon",                   NULL,  false },
  { "region.home",           "region home",               " is ", false },
  { "region.default",        "region default",            " is ", false },
  /* Last on purpose. It is the longest answer and the least urgent thing here:
   * region topology changes about as often as somebody reflashes the node,
   * while everything above it is what you look at when something is wrong. If a
   * sweep ever runs out of its time budget, this is the entry that should be
   * missing from it. */
  { "cmd:region",            "region",                    NULL,  true  },
};
#define SET_PARAM_COUNT ((int)(sizeof(SET_PARAMS) / sizeof(SET_PARAMS[0])))

/* Kept as name/value pairs rather than a pre-built JSON string. The values have
 * to be answerable on demand -- from the page, from the CLI, at any moment --
 * and not only in the one message that follows a sweep. With a sweep a day that
 * message is one in 1440, which is no way to find out whether the thing ever
 * ran. */
struct SetVal {
  const char *name;               // points into SET_PARAMS, never freed
  char value[SET_VALUE_MAX];
};
static SetVal _set_vals[SET_PARAM_COUNT];
static int _set_n = 0;            // parameters answered in the last full sweep
static int _set_miss = 0;         // parameters that gave nothing usable
static int _set_build_n = 0, _set_build_miss = 0;
static int _set_next = -1;        // parameter being collected, -1 = idle
static bool _set_ready = false;   // a complete set is waiting to be published
static bool _set_force = false;   // 'wifi settings now' / the page button
static unsigned long _set_due = 0;
static unsigned long _set_done_at = 0;   // millis of the last completed sweep

/* Turns one raw CLI answer into the value we would publish, or NULL when the
 * answer says nothing usable. Rewrites the buffer in place and returns a
 * pointer into it.
 *
 * Its own function since 1.9.0, when a second caller appeared: the same
 * parameter table is now also asked of a MONITORED repeater, over the air,
 * where the answer arrives as a text message instead of coming back from
 * handleCommand(). The two paths differ in how the bytes get here and in
 * nothing else, and they land in the same column of the same table on the
 * site. A rule that held for one and not the other is exactly how a row like
 * 'cmd:temp = Unknown command' gets into a database, so there is one rule and
 * one place where it lives. */
static char *settingsValue(char *reply, const SetParam &sp) {
  /* Replies come back as "> value", sometimes just " value". Anything else --
   * an error, an empty answer, the "??" the CLI gives for a command it does not
   * know -- is simply not recorded. Publishing a parameter this firmware could
   * not actually read would be the same mistake as publishing noise_floor 0. */
  char *val = reply;
  if (*val == '>') val++;
  /* Leading whitespace is stripped off a VALUE and left alone on a list. On the
   * region tree the indentation is the parent/child nesting -- eating it would
   * turn a tree into a flat list of names that all look like siblings. The
   * first line happens to start at column zero today, so this is a rule about
   * what the data means rather than a bug that has been seen. */
  if (!sp.list) {
    while (*val == ' ' || *val == '\t') val++;
  }

  size_t vlen = strlen(val);
  while (vlen && (val[vlen - 1] == '\n' || val[vlen - 1] == '\r' ||
                  val[vlen - 1] == ' ' || val[vlen - 1] == '\t')) {
    val[--vlen] = 0;
  }

  // Keep only the tail after a known separator, for the region commands.
  if (sp.after) {
    const char *last = NULL, *p = val;
    while ((p = strstr(p, sp.after)) != NULL) { last = p; p += strlen(sp.after); }
    if (last) {
      val = (char *)(last + strlen(sp.after));
      vlen = strlen(val);
    }
  }

  /* A value containing a line break is a list, not a setting. Truncating at the
   * first newline would quietly publish a fragment of a table as though it were
   * a value, so for everything that is not declared a list such an answer is
   * dropped and counted as a miss instead. */
  if (!sp.list && (strchr(val, '\n') != NULL || strchr(val, '\r') != NULL)) {
    Serial.printf("MeshManagerNet: %s gaf meerdere regels, overgeslagen\n", sp.name);
    return NULL;
  }
  /* A list is normalised to plain newlines. The CLI writes '\n' and nothing
   * else, but a stray '\r' would reach the site as an escaped \r inside the
   * value and show up as a blank line in a column where every line means
   * something. Done in place; a lone '\r' becomes a line break rather than
   * disappearing, because it was one. */
  if (sp.list) {
    char *r = val, *w = val;
    while (*r) {
      if (*r == '\r') { *w++ = '\n'; r++; if (*r == '\n') r++; }
      else            { *w++ = *r++; }
    }
    *w = 0;
    vlen = strlen(val);
  }

  if (vlen == 0) return NULL;
  /* MeshCore writes its refusals two ways and this only caught one of them.
   * "Err - unknown region" and "Err - save failed" sailed straight through and
   * were stored as if they were settings, so a page could show 'region.home =
   * Err - unknown region' with a fresh timestamp next to it -- an answer that
   * looks more authoritative than "(geen antwoord)" while meaning strictly
   * less. Both forms are spelled out rather than testing for "Err", because a
   * node name like 'Erratic' is a legal value and must not be swallowed. */
  if (strncmp(val, "Error", 5) == 0 || strncmp(val, "Err - ", 6) == 0 ||
      strncmp(val, "??", 2) == 0) {
    return NULL;
  }
  return val;
}

// Collects one parameter. Runs once per loop pass while a sweep is going.
static void settingsStep() {
  if (_set_next < 0 || _set_next >= SET_PARAM_COUNT || !_mesh) return;

  const SetParam &sp = SET_PARAMS[_set_next];
  char cmd[48], reply[160];
  snprintf(cmd, sizeof(cmd), "%s", sp.cmd);
  reply[0] = 0;
  _mesh->handleCommand(0, cmd, reply);

  char *val = settingsValue(reply, sp);
  if (val) {
    if (_set_build_n < SET_PARAM_COUNT) {
      _set_vals[_set_build_n].name = sp.name;
      strncpy(_set_vals[_set_build_n].value, val, SET_VALUE_MAX - 1);
      _set_vals[_set_build_n].value[SET_VALUE_MAX - 1] = 0;
      _set_build_n++;
    }
  } else {
    _set_build_miss++;
  }

  if (++_set_next >= SET_PARAM_COUNT) {
    _set_next = -1;
    _set_n = _set_build_n;
    _set_miss = _set_build_miss;
    _set_ready = (_set_n > 0);
    _set_done_at = millis();
    _set_due = millis() + (unsigned long)_cfg.set_iv_min * 60000UL;
    Serial.printf("MeshManagerNet: instellingen gelezen, %d gelukt, %d geen antwoord\n",
                  _set_n, _set_miss);
    /* Somebody asked for this sweep over MQTT and is watching a page. Waiting
     * for the ordinary publish interval would mean up to five minutes of a
     * button that looks like it did nothing -- and far longer in power-save
     * mode. Arm the publish here rather than in the command handler, because
     * only here do we know the sweep actually finished. */
    if (_cmd_after_sweep) {
      _cmd_after_sweep = false;
      _cmd_push = true;
    }
  }
}

static void settingsLoop() {
  if (_safe_mode || !_mesh) return;
  if (_set_next >= 0) { settingsStep(); return; }

  // First sweep a minute after boot, so it is not competing with startup.
  if (_set_due == 0) _set_due = millis() + 60000UL;

  /* Deliberately not gated on the previous set having been published: with the
   * broker down, a daily sweep should still refresh what the page and the CLI
   * show, rather than freezing on the last set that got out. */
  if (_set_force || passed(_set_due)) {
    _set_force = false;
    _set_build_n = 0;
    _set_build_miss = 0;
    _set_next = 0;
  }
}

// Seconds until the next sweep, or 0 when one is due or running.
static uint32_t settingsNextIn() {
  if (_set_next >= 0) return 0;
  return secsLeft(_set_due);
}

// ---------------------------------------------------------------------- mqtt

static void mqttTopic(const char *leaf, char *out, size_t max) {
  snprintf(out, max, "%s/%s/%s", _cfg.mqtt_prefix,
           _node_hex[0] ? _node_hex : "node", leaf);
}

static bool mqttEnsure() {
  if (_mqtt.connected()) return true;
  if (!_cfg.mqtt_enabled || _cfg.mqtt_host[0] == 0) return false;
  if (WiFi.status() != WL_CONNECTED) return false;

  // Backing off matters here: a broker that does not answer costs a full
  // socket timeout per attempt, and that time comes straight out of the mesh.
  if (_mqtt_last_try != 0 && millis() - _mqtt_last_try < MQTT_RETRY_MS) return false;
  _mqtt_last_try = millis();

  char client_id[32];
  snprintf(client_id, sizeof(client_id), "meshmanager-%s",
           _node_hex[0] ? _node_hex : "node");

  bool ok = _cfg.mqtt_user[0]
    ? _mqtt.connect(client_id, _cfg.mqtt_user, _cfg.mqtt_pass)
    : _mqtt.connect(client_id);

  if (ok) {
    _mqtt_err = "";
    /* Subscribing belongs here and nowhere else: a subscription lives inside
     * one session, and this client connects with a clean session, so every
     * reconnect starts with none. Doing it once at startup would work until
     * the first WiFi hiccup and then silently stop working -- which is the
     * kind of fault that only shows up as 'the button used to do something'. */
    char topic[96];
    mqttTopic("cmd", topic, sizeof(topic));
    if (!_mqtt.subscribe(topic, 0)) {
      Serial.printf("MeshManagerNet: kon niet inschrijven op %s\n", topic);
    }
  } else {
    _fail_count++;
    _mqtt_err = "conn";
    _mqtt_err_rc = _mqtt.state();
  }
  return ok;
}

/* Arrives from inside _mqtt.loop(). Copies one word and returns; see the note
 * at _cmd_have for why nothing else happens here.
 *
 * A retained message would be redelivered on every reconnect, so the node would
 * sweep on every boot and after every WiFi drop, forever. The publisher is the
 * one that has to get that right (retain=false), but a length cap and the
 * interval below are what keep the damage bounded when it does not. */
static void mqttOnMessage(char *topic, uint8_t *payload, unsigned int len) {
  (void)topic;                    // we subscribe to exactly one, no need to sort
  if (_cmd_have) return;          // one is already waiting; drop this
  if (len == 0 || len >= MQTT_CMD_MAX) { _cmd_refused++; return; }

  for (unsigned int i = 0; i < len; i++) _cmd_word[i] = (char)payload[i];
  _cmd_word[len] = 0;
  _cmd_have = true;
}

/* Stages an on-demand CLI settings sweep of a repeater we MONITOR. Defined
 * with the monitor code far below, because that is where the table, the login
 * and the state machine live; declared here because the MQTT command handler is
 * the way in and sits above it. Returns false when nothing was staged, with
 * *why pointing at a sentence saying which of the half-dozen reasons it was --
 * 'geweigerd' on its own is the kind of answer that costs an afternoon. */
static bool monSettingsRequest(const char *key_hex, const char **why);

/* Stages a check of the clocks of the repeaters we monitor. Same arrangement
 * and same reason as the line above. Never fails the command: a node that
 * cannot do the LoRa half has still had its own clock set, which is the half
 * that matters and the half that costs nothing. *why then says why the rest did
 * not happen, for the log. */
static bool monClockRequest(const char **why);

/* Sets our own clock from a time the site gave us. Returns what happened, in
 * words, and never anything that is not one of the four counters above.
 *
 * Three things are refused here and it is worth being explicit about which,
 * because each one protects against a different accident:
 *
 *  - a time outside CLOCK_MIN_EPOCH..CLOCK_MAX_EPOCH. Somebody published
 *    milliseconds, or an empty string that parsed as zero. A node with a clock
 *    reading 1970 is worse off than one with a clock reading last week: the
 *    second is merely stale, the first makes every timestamp it emits absurd
 *    and, being far in the past, cannot be corrected forward past a mesh that
 *    has already seen higher advert timestamps from us.
 *  - a time BEHIND our own. See the header: our adverts carry this clock, and
 *    every node that knows us drops an advert that does not step forward. A
 *    backwards correction of an hour is an hour of invisibility for a repeater
 *    on a roof. So the node that runs fast keeps running fast and gets
 *    reported; it is the lesser of the two faults and the only reversible one.
 *  - a difference too small to be worth a step. Not a safety rule, a noise
 *    rule: the site publishes this daily and the trip from there to here takes
 *    a moment, so a second or two of difference is the measurement, not drift.
 */
static const char *clockApplyOwn(uint32_t epoch, long *delta_out) {
  uint32_t now = _mesh ? _mesh->getRTCClock()->getCurrentTime() : 0;
  *delta_out = 0;

  if (!clockPlausible(epoch)) { _clk_bad++; return "buiten het toegestane venster"; }
  if (!_mesh)                 { _clk_bad++; return "mesh nog niet gestart"; }

  long delta = (long)(epoch - now);          // both uint32; the cast is the diff
  *delta_out = delta;
  _clk_last_ms = millis();
  _clk_last_epoch = epoch;
  _clk_last_delta = delta;

  /* An unset clock is the case this exists for, so it is not held to the noise
   * rule below: a jump of a year and a half is exactly what should happen. */
  if (!clockPlausible(now)) {
    _mesh->getRTCClock()->setCurrentTime(epoch);
    _clk_sets++;
    return "klok stond niet, nu gezet";
  }
  if (delta < 0) { _clk_back++; return "onze klok loopt voor; niet teruggezet"; }
  if (delta < CLOCK_OWN_MIN_STEP_S) { _clk_noops++; return "stond al gelijk"; }

  _mesh->getRTCClock()->setCurrentTime(epoch);
  _clk_sets++;
  return "klok bijgezet";
}

/* Runs what the callback left behind, from the ordinary loop.
 *
 * The allowlist is the whole security model: three exact words, 'settings',
 * 'status' and 'time', no passthrough to handleCommand(). See the 1.8.0
 * changelog entry for why the console's route is not good enough here.
 *
 * Since 1.9.0 'settings' may carry one argument: the public key of a repeater
 * we monitor, which asks for a sweep of THEIR CLI instead of our own. That
 * argument does not weaken the rule above, and it is worth being precise about
 * why. It never becomes text that reaches a CLI, here or on the far side: it
 * only has to select exactly one entry from the monitor list, and the commands
 * that then go out are the compiled-in SET_PARAMS table and nothing else. The
 * monitor list is writable from the admin page and the mesh CLI, both of which
 * ask for a password over a link the operator controls -- so a broker account
 * can aim this at a node the operator already chose to monitor, and at nothing
 * else. A key that matches no entry, or more than one, is refused and counted.
 *
 * Since 1.10.0 there is a third word, 'time <epoch>', and this one really is a
 * word of its own rather than an argument on an existing one -- because unlike
 * the other two it does not ask the node to say something it would have said
 * anyway, it changes state. Naming it separately is what lets the site, the
 * broker ACL and this list all talk about the same thing.
 *
 * Its argument is a number and is treated as one: parsed here, bounded by a
 * window of years, and applied by clockApplyOwn() which will only ever move the
 * clock forward. So the worst an attacker on the broker can do with it is set
 * this node's clock to a time between 2025 and 2100, once every thirty seconds,
 * and only ever later than it already was. That is a real capability and worth
 * naming: a clock pushed far into the future cannot be walked back (see
 * clockApplyOwn for why nothing may step a clock backwards), so recovering from
 * it needs 'clkreboot' and a resync. It is still a far smaller capability than
 * the 'reboot' a CLI passthrough would have handed over, and the feature is
 * pointless without it.
 *
 * Rejected alternative: a third word, 'monsettings <key>'. It buys nothing over
 * an argument -- the same parse, the same list -- and it would have meant a
 * second name to keep in step with the site's COMMANDS tuple. */
static void mqttRunCommand() {
  if (!_cmd_have) return;

  char word[MQTT_CMD_MAX];
  strncpy(word, _cmd_word, sizeof(word) - 1);
  word[sizeof(word) - 1] = 0;
  _cmd_have = false;              // free the slot before doing any work

  // Trim, so a publisher that adds a newline is not punished for it.
  char *w = word;
  while (*w == ' ' || *w == '\r' || *w == '\n' || *w == '\t') w++;
  size_t wl = strlen(w);
  while (wl && (w[wl - 1] == ' ' || w[wl - 1] == '\r' ||
                w[wl - 1] == '\n' || w[wl - 1] == '\t')) w[--wl] = 0;

  // Split off the optional argument, so the word itself stays an exact match.
  char *arg = strpbrk(w, " \t");
  if (arg) {
    *arg++ = 0;
    while (*arg == ' ' || *arg == '\t') arg++;
  }
  if (arg && *arg == 0) arg = NULL;

  /* Which of the three words takes an argument is checked here and not further
   * down, so 'status <anything>' is refused rather than quietly run as 'status'.
   * A publisher sending an argument to a command that has none has misunderstood
   * something, and running the command anyway hides that from both ends. */
  bool wants_arg = (strcmp(w, "time") == 0);          // must have one
  bool takes_arg = wants_arg || (strcmp(w, "settings") == 0);
  bool known = takes_arg || (strcmp(w, "status") == 0);
  if (!known || (arg && !takes_arg) || (!arg && wants_arg)) {
    _cmd_refused++;
    Serial.printf("MeshManagerNet: opdracht '%.16s' geweigerd, alleen "
                  "settings [sleutel]|status|time <epoch>\n", w);
    return;
  }
  if (_cmd_last_ms != 0 && millis() - _cmd_last_ms < MQTT_CMD_MIN_GAP_MS) {
    _cmd_refused++;
    Serial.printf("MeshManagerNet: opdracht '%s' te snel na de vorige, genegeerd\n", w);
    return;
  }
  _cmd_last_ms = millis();
  _cmd_count++;

  if (strcmp(w, "time") == 0) {
    /* strtoul and not atol: the epoch passes 2^31 in 2038 and this node may
     * well still be on that roof. A trailing character that is not a digit
     * means the argument was not a bare number, and a number with something
     * stuck to it is a mistake somewhere upstream, not a time. */
    char *end = NULL;
    unsigned long secs = strtoul(arg, &end, 10);
    if (end == arg || (end && *end != 0)) {
      _clk_bad++;
      _cmd_refused++;
      Serial.printf("MeshManagerNet: 'time %.20s' is geen getal, genegeerd\n", arg);
      return;
    }
    long delta = 0;
    const char *res = clockApplyOwn((uint32_t)secs, &delta);
    Serial.printf("MeshManagerNet: tijd %lu ontvangen (%+ld s): %s\n", secs, delta, res);

    /* The LoRa half is a separate decision and a separate budget. Our own clock
     * has just been set whatever happens below -- that part is free, it is the
     * reason the message was sent, and a node whose own clock is right is
     * already the larger share of the value. */
    const char *why = "";
    if (!monClockRequest(&why)) {
      Serial.printf("MeshManagerNet: klokronde langs gemonitorde nodes niet gestart: %s\n", why);
    }
  } else if (arg) {
    /* Nothing is armed for publication here. This sweep talks to another node
     * over the radio and takes minutes, not one pass of the loop, so it ships
     * its own message when it is done -- see monSettingsFinish(). */
    const char *why = "";
    if (!monSettingsRequest(arg, &why)) {
      _cmd_refused++;
      Serial.printf("MeshManagerNet: sweep voor %.16s geweigerd: %s\n", arg, why);
      return;
    }
    Serial.printf("MeshManagerNet: instellingen-sweep gevraagd voor gemonitorde node %.16s\n", arg);
  } else if (strcmp(w, "settings") == 0) {
    _set_force = true;            // settingsLoop() picks this up this same pass
    _cmd_after_sweep = true;      // publish once it has something to publish
    Serial.println("MeshManagerNet: instellingen-sweep gevraagd via MQTT");
  } else {
    _cmd_push = true;
    Serial.println("MeshManagerNet: statusbericht gevraagd via MQTT");
  }
}

static bool mqttPublishStats() {
  if (!_mesh || !mqttEnsure()) return false;

  static char body[MQTT_PUB_MAX];
  size_t n = _mesh->fillStatsJson(body, sizeof(body));
  if (n == 0) return false;

  /* De filterstand reist mee met elk statistiekenbericht, niet met de sweep.
   *
   * Dat verschil is het halve punt van deze feature. Een filter is de instelling
   * die een node stilletjes nutteloos maakt: hij blijft antwoorden, hij blijft
   * adverteren, en hij stuurt niets meer door. Wie dat pas ziet in de dagelijkse
   * instellingenronde, ziet het een dag te laat. De korte vorm kost ongeveer 160
   * byte -- op een bericht van een paar kilobyte is dat de prijs niet waard om
   * over na te denken, en de rekening staat er dan bij elke meting naast.
   *
   * De regeltabellen zitten er NIET in. Twaalf hoplimieten met hun
   * snelheidsvensters zijn twee kilobyte die eens per maand veranderen; die
   * horen achter een verzoek van iemand die ze gaat wijzigen (GET /api/filter)
   * en niet in een bericht dat elke paar minuten vertrekt. */
  /* Sinds 2.6.0 wordt er rechtstreeks in 'body' geschreven in plaats van via een
   * tijdelijke buffer van 320 byte. Dat is niet alleen zuiniger: met een aparte
   * buffer moest het hele filterobject passen of het viel in zijn geheel weg, en
   * "de node meldt geen filter" is precies de verkeerde boodschap over een node
   * waarvan het bericht toevallig een paar buren langer was. Nu krijgt
   * pf_summary_json() de ruimte die er werkelijk is, vult die met de korte vorm
   * eerst en daarna met zoveel uitsplitsing als erin past, en zet "trunc":1 als
   * er iets niet meepaste. Wat er staat klopt dus altijd; alleen de volledigheid
   * is voorwaardelijk, en dat staat erbij. */
  {
    size_t start = n - 1;                   // over de sluitaccolade heen terug
    size_t room = (sizeof(body) > start + 16) ? sizeof(body) - start - 16 : 0;
    int hdr = room ? snprintf(body + start, room, ",\"filter\":") : 0;
    size_t fn = (hdr > 0) ? pf_summary_json(body + start + hdr, room - hdr, true) : 0;
    if (fn > 0) {
      n = start + hdr + fn;
      n += snprintf(body + n, sizeof(body) - n, "}");
    } else {
      body[n - 1] = '}';                    // paste niet eens de korte vorm: laat
      body[n] = 0;                          // het bericht heel
    }
  }

  /* A finished settings sweep rides along with this message. Appended here
   * rather than built into fillStatsJson because it is this module's data, not
   * the mesh's, and because it goes out once a day against the metrics' every
   * couple of minutes. */
  if (_set_ready && _set_n > 0) {
    size_t start = n - 1;                   // step back over the closing brace
    int w = snprintf(body + start, sizeof(body) - start, ",\"settings\":{");
    bool fits = (w > 0);
    size_t q = start + (fits ? w : 0);

    for (int i = 0; i < _set_n && fits; i++) {
      char esc[SET_VALUE_MAX * 2 + 4];
      jsonEsc(esc, sizeof(esc), _set_vals[i].value);
      int e = snprintf(body + q, sizeof(body) - q, "%s\"%s\":\"%s\"",
                       i ? "," : "", _set_vals[i].name, esc);
      if (e <= 0 || q + (size_t)e + 4 >= sizeof(body)) { fits = false; break; }
      q += e;
    }
    if (fits) {
      int e = snprintf(body + q, sizeof(body) - q, "}}");
      if (e > 0 && q + (size_t)e < sizeof(body)) {
        n = q + e;
        _set_ready = false;
      }
    }
    if (_set_ready) body[n - 1] = '}';       // did not fit: restore the payload
  }

  char topic[96];
  mqttTopic("stats", topic, sizeof(topic));

  if (!_mqtt.publish(topic, (const uint8_t *)body, n, false)) {
    _fail_count++;
    _mqtt_err = "stats";
    return false;
  }
  _stats_count++;
  _published_this_wake = true;
  _mqtt_err = "";
  return true;
}

static void mqttDrainRx() {
  if (_rx_head == _rx_tail) return;

  /* While asleep the queue keeps filling on purpose -- that is the point of
   * buffering -- but once we are up and the broker still will not take them,
   * old packets have no value left. Dropping beats sending a pile of stale
   * traffic minutes later. */
  if (!mqttEnsure()) {
    if (_asleep || WiFi.status() != WL_CONNECTED) return;
    while (_rx_tail != _rx_head) {
      _rx_tail = (uint8_t)((_rx_tail + 1) % MQTT_RX_QUEUE);
      _drop_count++;
    }
    return;
  }

  char topic[96];
  mqttTopic("rx", topic, sizeof(topic));

  /* With charge to spare, empty the whole buffer in one pass so nothing waits
   * a round. The cap is a bound on one pass, not a brake: at these radio
   * settings a full-size packet occupies the air for roughly half a second, so
   * even the lower figure drains far faster than LoRa can deliver. */
  int cap = liveForwarding() ? MQTT_RX_QUEUE : MQTT_DRAIN_MAX;

  for (int guard = 0; guard < cap && _rx_tail != _rx_head; guard++) {
    RxItem &it = _rx_queue[_rx_tail];

    static char body[MQTT_RX_MAX_LEN * 2 + 96];
    int n = snprintf(body, sizeof(body),
      "{\"t\":%u,\"snr\":%.2f,\"rssi\":%d,\"len\":%u,\"raw\":\"",
      (unsigned)it.ms, it.snr4 / 4.0f, (int)it.rssi, (unsigned)it.len);

    for (uint8_t i = 0; i < it.len; i++) {
      body[n++] = HEXCHARS[it.data[i] >> 4];
      body[n++] = HEXCHARS[it.data[i] & 0x0F];
    }
    body[n++] = '"';
    body[n++] = '}';

    if (!_mqtt.publish(topic, (const uint8_t *)body, n, false)) {
      _fail_count++;
      _mqtt_err = "pkt";
      return;              // leave it queued; try again next pass
    }
    _rx_count++;
    _rx_tail = (uint8_t)((_rx_tail + 1) % MQTT_RX_QUEUE);
  }
}

static void mqttLoop() {
  if (!_cfg.mqtt_enabled || _cfg.mqtt_host[0] == 0) return;
  if (_asleep) return;

  if (_mqtt.connected()) _mqtt.loop();
  mqttRunCommand();
  mqttDrainRx();

  /* An asked-for publish jumps the interval, and resets it: whoever gets this
   * message has just been given everything the next scheduled one would have
   * carried, so sending that one seconds later is pure airtime. */
  if (_cmd_push) {
    _cmd_push = false;
    _mqtt_last_push = millis();
    mqttPublishStats();
    return;
  }

  if (millis() - _mqtt_last_push < currentIntervalSecs() * 1000UL) return;
  _mqtt_last_push = millis();
  mqttPublishStats();
}

void meshmanager_on_raw_packet(float snr, float rssi, const uint8_t raw[], int len) {
  if (!_started || _disabled || _safe_mode) return;
  if (!_cfg.mqtt_enabled || !_cfg.mqtt_rx) return;
  if (len <= 0 || len > MQTT_RX_MAX_LEN) return;

  uint8_t next = (uint8_t)((_rx_head + 1) % MQTT_RX_QUEUE);
  if (next == _rx_tail) {     // full: rather lose a packet than hold up reception
    _drop_count++;
    return;
  }

  RxItem &it = _rx_queue[_rx_head];
  it.ms = millis();
  it.snr4 = (int16_t)(snr * 4);
  it.rssi = (int16_t)rssi;
  it.len = (uint8_t)len;
  memcpy(it.data, raw, len);
  _rx_head = next;
}

// -------------------------------------------------------------- advert cache

/* Who is out there, kept across restarts. neighbours[] in MyMesh lives in RAM
 * and starts empty after every flash, so without this the monitor list and the
 * heard list show bare hex keys until each node happens to advertise again --
 * which at an advert interval of hours is most of a day.
 *
 * Written lazily, following the same reasoning as the repeater's own ACL
 * (dirty_contacts_expiry in MyMesh): a busy mesh delivers adverts in bursts,
 * and writing each one would grind through SPIFFS erase cycles for data that
 * changes by a name every few hours. So changes accumulate in RAM and go to
 * disk at most once every ADV_WRITE_DELAY, and only when something actually
 * differs from what is already stored. */
#define MMADV_FILE        "/adverts.dat"
#define MMADV_MAGIC       0x41565331UL    // "AVS1"
#define ADV_CACHE_MAX     48
#define ADV_NAME_MAX      24
#define ADV_WRITE_DELAY   120000UL        // 2 minutes of quiet before writing

struct AdvEntry {
  uint8_t  key[PUB_KEY_SIZE];
  char     name[ADV_NAME_MAX];
  uint8_t  type;
  uint32_t heard;                 // epoch seconds, 0 when the clock was unset
  int32_t  lat, lon;              // 1e-6 degrees; 0,0 means 'not advertised'
};

static AdvEntry _adv[ADV_CACHE_MAX];
static int _adv_count = 0;
static unsigned long _adv_dirty_at = 0;   // 0 = nothing to write

static int advFind(const uint8_t *key, int prefix_len) {
  if (prefix_len <= 0 || prefix_len > PUB_KEY_SIZE) return -1;
  for (int i = 0; i < _adv_count; i++) {
    if (memcmp(_adv[i].key, key, prefix_len) == 0) return i;
  }
  return -1;
}

const char *meshmanager_advert_name(const uint8_t *pub_key, int prefix_len) {
  int i = advFind(pub_key, prefix_len);
  return (i >= 0 && _adv[i].name[0]) ? _adv[i].name : NULL;
}

static void advLoad() {
  _adv_count = 0;
  if (!_fs) return;
  File f = _fs->open(MMADV_FILE, "r");
  if (!f) return;

  uint32_t magic = 0;
  uint16_t entry_size = 0, count = 0;
  /* The entry size is part of the header because these are raw structs: add a
   * field one day and an old file would read back as garbage names and
   * nonsense timestamps. A mismatch just starts the cache empty. */
  if (f.read((uint8_t *)&magic, 4) == 4 && magic == MMADV_MAGIC &&
      f.read((uint8_t *)&entry_size, 2) == 2 && entry_size == (uint16_t)sizeof(AdvEntry) &&
      f.read((uint8_t *)&count, 2) == 2) {
    if (count > ADV_CACHE_MAX) count = ADV_CACHE_MAX;
    for (uint16_t i = 0; i < count; i++) {
      if (f.read((uint8_t *)&_adv[_adv_count], sizeof(AdvEntry)) != (int)sizeof(AdvEntry)) break;
      _adv[_adv_count].name[ADV_NAME_MAX - 1] = 0;   // never trust a stored string
      _adv_count++;
    }
  }
  f.close();
  Serial.printf("MeshManagerNet: %d adverts geladen\n", _adv_count);
}

static void advSave() {
  if (!_fs) return;
  File f = _fs->open(MMADV_FILE, "w");
  if (!f) return;
  uint32_t magic = MMADV_MAGIC;
  uint16_t entry_size = (uint16_t)sizeof(AdvEntry);
  uint16_t count = (uint16_t)_adv_count;
  f.write((const uint8_t *)&magic, 4);
  f.write((const uint8_t *)&entry_size, 2);
  f.write((const uint8_t *)&count, 2);
  for (int i = 0; i < _adv_count; i++) {
    f.write((const uint8_t *)&_adv[i], sizeof(AdvEntry));
  }
  f.close();
  _adv_dirty_at = 0;
}

void meshmanager_on_advert(const uint8_t *pub_key, const char *name, uint8_t type,
                         bool has_latlon, int32_t lat, int32_t lon) {
  if (!_started || _disabled) return;

  uint32_t now = 0;
  if (_mesh) {
    uint8_t h;
    // Only trust the clock if it looks set; a bogus 'heard' would evict wrongly.
    if (_mesh->getClockHour(&h)) now = _mesh->getRTCClock()->getCurrentTime();
  }

  int i = advFind(pub_key, PUB_KEY_SIZE);
  if (i < 0) {
    if (_adv_count < ADV_CACHE_MAX) {
      i = _adv_count++;
    } else {
      /* Full: drop whoever we heard longest ago. This runs on a node with a
       * few hundred kB of flash, not a server. */
      i = 0;
      for (int j = 1; j < _adv_count; j++) {
        if (_adv[j].heard < _adv[i].heard) i = j;
      }
    }
    memset(&_adv[i], 0, sizeof(AdvEntry));
    memcpy(_adv[i].key, pub_key, PUB_KEY_SIZE);
    _adv_dirty_at = millis() + ADV_WRITE_DELAY;
  }

  AdvEntry &e = _adv[i];
  e.heard = now;
  if (e.type != type) { e.type = type; _adv_dirty_at = millis() + ADV_WRITE_DELAY; }

  // A name straight off the air always wins over a stored one.
  if (name && *name && strncmp(e.name, name, ADV_NAME_MAX - 1) != 0) {
    strncpy(e.name, name, ADV_NAME_MAX - 1);
    e.name[ADV_NAME_MAX - 1] = 0;
    _adv_dirty_at = millis() + ADV_WRITE_DELAY;
  }
  if (has_latlon && (e.lat != lat || e.lon != lon)) {
    e.lat = lat;
    e.lon = lon;
    _adv_dirty_at = millis() + ADV_WRITE_DELAY;
  }
  /* Deliberately no write here, and 'heard' alone never schedules one: the
   * timestamp changes on every advert, and chasing it would defeat the whole
   * point of writing lazily. It rides along with the next real change. */
}

// ------------------------------------------------------ monitored repeaters

/* Polling other repeaters over the mesh. Per peer the sequence is the one a
 * chat client performs: an ANON_REQ carrying the password (or an empty one,
 * which makes the far side skip the password check and consult its access list
 * instead), then a REQ of type GET_STATUS once that is accepted, then a
 * RESPONSE carrying RepeaterStats.
 *
 * It is a state machine driven from mmnet_loop(), one peer at a time. Not
 * because that is simpler, but because this node is a repeater: a burst of
 * logins from the very node meant to relay other people's traffic is
 * antisocial, and every flooded login costs the whole mesh airtime.
 *
 * A refused login cannot be told from an unreachable one -- the far side
 * answers a rejected login with silence. Hence LOGIN_NOANSWER rather than a
 * pretence of knowing which of the two happened. */

#define MMMON_CFG_FILE   "/msmon.json"
#define MON_KEY_HEX_MAX  65      // 64 hex chars + NUL
#define MON_NAME_MAX     24
#define MON_PASS_MAX     16      // the protocol truncates at 15 characters
/* Minimum pasted key: 6 bytes. That is what this firmware itself uses to name
 * a node (MQTT topics, the id on the page), and short enough to copy off a
 * screenshot. Below it, collisions stop being theoretical once a few dozen
 * nodes are on the air, and monitoring the wrong repeater is worse than being
 * asked for two more characters. */
#define MON_MIN_HEX      12
/* A first login is flooded and its answer comes back over an unknown number of
 * hops; 20 s turned out to be tight for that. */
#define MON_STEP_MS      30000UL // how long to wait for one login/status answer
#define MON_GAP_MS        3000UL // breathing space between peers
// First automatic round after boot, late enough not to fight with startup.
#define MON_FIRST_MS     60000UL
#define MON_HEARD_MAX      40    // heard rows handed to the page

enum MonLogin { LOGIN_NONE = 0, LOGIN_OK = 1, LOGIN_NOANSWER = 2 };

struct MonEntry {
  char key[MON_KEY_HEX_MAX];   // longest key seen for this node, lowercase hex
  char name[MON_NAME_MAX];
  char pass[MON_PASS_MAX];     // empty is a valid choice: try their access list
  bool enabled;
  // runtime only, never persisted
  int8_t   mesh_idx;           // index in MyMesh's table, -1 = not resolvable yet
  uint8_t  login_res;
  bool     logged_in;
  /* Three counters, not two. 'oks' used to be incremented only on a successful
   * publish, which meant a reading that was fetched but never delivered looked
   * exactly like one that was never fetched -- and finding out took a sniffer
   * on the broker. Now: polls = attempts, oks = answers received and parsed,
   * pubs = actually published. Any gap between them is visible on the page. */
  uint32_t polls, oks, pubs;
  // Per request type, so a round that half worked says which half.
  uint32_t ok_st, ok_tl, ok_nb;
  uint8_t  fails;              // consecutive rounds that produced nothing
  unsigned long last_ok;
};

/* Three requests each waiting out a 30 s timeout is 90 s spent on a peer that
 * is simply not there. With a handful of dead entries a round would run
 * practically without pause, and this node's day job is relaying other
 * people's traffic. So after this many barren rounds an entry is only retried
 * every fourth one. Any answer at all clears it. */
#define MON_BACKOFF_AFTER   3
#define MON_BACKOFF_EVERY   4

static MonEntry _mon[MAX_MONITORS];
static int _mon_count = 0;
static uint16_t _mon_interval = 900;

/* One state machine, not two. MST_CLI_WAIT belongs to the settings sweep of a
 * monitored node and the rest to the ordinary poll, and they deliberately share
 * this variable: there is exactly one slot for an incoming reply
 * (_mon_got_reply), one login session per peer, and one radio. Two machines
 * would have had to agree about all three anyway, and the version where they
 * disagree is a poll answer parsed as a CLI answer. */
/* MST_CLK_WAIT is appended rather than slotted in next to MST_CLI_WAIT where it
 * belongs by meaning: this enum is printed as a number by /api/mon and by 'wifi
 * mon', so renumbering the existing states would silently change what those two
 * report on a node somebody is already watching. MST_WSET_WAIT -- writing one
 * setting to a monitored node -- is appended for the same reason. */
enum MonState { MST_IDLE, MST_LOGIN_WAIT, MST_REQ_WAIT, MST_TELEM_WAIT, MST_NBR_WAIT,
                MST_CLI_WAIT, MST_GAP, MST_CLK_WAIT, MST_WSET_WAIT };
static MonState _mon_state = MST_IDLE;
static int _mon_cur = -1;
static uint8_t _mon_retry = 0;      // one flood retry per step, then give up

/* Results of the round in progress, gathered before anything is published.
 * Each of the three requests can fail on its own, and a partial reading beats
 * none -- so we collect what comes back and publish once, at the end, with
 * whatever we have. */
static RepeaterStats _mon_st;
static bool _mon_have_st = false;
static int  _mon_st_len = 0;        // bytes of RepeaterStats that actually arrived
static uint8_t _mon_nbr[176];       // raw neighbours reply, decoded when publishing
static int  _mon_nbr_len = 0;
static bool _mon_have_nbr = false;

/* Telemetry arrives as CayenneLPP, so which channels a node reports on is not
 * known in advance. Values are kept under the channel the source used rather
 * than renamed to something we assume they mean. */
#define MON_TELEM_MAX 6
struct MonTelem { uint8_t channel; char kind; float value; };   // kind: 't' or 'v'
static MonTelem _mon_tl[MON_TELEM_MAX];
static int _mon_tl_n = 0;
static unsigned long _mon_deadline = 0;
static unsigned long _mon_next_round = 0;
static uint32_t _mon_round = 0;

/* A short trace of the poll sequence, because the counters alone cannot tell
 * two very different failures apart: a status request that was never sent
 * (packet pool empty) and one that was sent but never answered both leave
 * polls=1, oks=0, lr=1. Readable from the admin page and over the mesh CLI, so
 * diagnosing a node on a roof does not need a serial cable. */
#define MON_TRACE_LINES  12
#define MON_TRACE_LEN    64
static char _mon_trace[MON_TRACE_LINES][MON_TRACE_LEN];
static uint32_t _mon_trace_n = 0;

static void monTrace(const char *fmt, ...) {
  char *dest = _mon_trace[_mon_trace_n % MON_TRACE_LINES];
  int p = snprintf(dest, MON_TRACE_LEN, "%lus ", (unsigned long)(millis() / 1000));
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(dest + p, MON_TRACE_LEN - p, fmt, ap);
  va_end(ap);
  _mon_trace_n++;
  Serial.print("MON: ");
  Serial.println(dest);
}

// One staging slot is enough: only ever one poll is outstanding.
static volatile bool _mon_got_reply = false;
static uint8_t _mon_reply[MAX_PACKET_PAYLOAD];
static int _mon_reply_len = 0;
static int _mon_reply_idx = -1;
static uint8_t _mon_reply_type = 0;   // RESPONSE, or TXT_MSG for a CLI answer

/* Lowercases and drops the separators people paste along, but rejects anything
 * that is not hex. Strict on purpose: a silently mangled key becomes a monitor
 * entry that can never work, with nothing on screen to say why. */
static bool normaliseKey(char *key) {
  char out[MON_KEY_HEX_MAX];
  int n = 0;
  for (const char *p = key; *p; p++) {
    char c = *p;
    if (c == ' ' || c == ':' || c == '-' || c == '\t') continue;
    if (c >= 'A' && c <= 'F') c = (char)(c - 'A' + 'a');
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    if (n >= MON_KEY_HEX_MAX - 1) return false;
    out[n++] = c;
  }
  out[n] = 0;
  if (n < MON_MIN_HEX || n > 64 || (n & 1)) return false;
  strcpy(key, out);
  return true;
}

/* Two keys mean the same node when the shorter is a prefix of the longer.
 * Different sources hand out different lengths for one repeater -- Home
 * Assistant 5 bytes, this firmware 6 -- and treating those as two nodes is
 * exactly how one repeater ends up listed twice with its history split. */
static bool sameNode(const char *a, const char *b) {
  size_t la = strlen(a), lb = strlen(b);
  size_t n = (la < lb) ? la : lb;
  return n > 0 && memcmp(a, b, n) == 0;
}

static int findMonitor(const char *key) {
  for (int i = 0; i < _mon_count; i++) {
    if (sameNode(_mon[i].key, key)) return i;
  }
  return -1;
}

static void saveMonitors() {
  if (!_fs) return;
  File f = _fs->open(MMMON_CFG_FILE, "w");
  if (!f) return;
  f.printf("{\"iv\":%u,\"m\":[", _mon_interval);
  for (int i = 0; i < _mon_count; i++) {
    f.printf("%s{\"k\":\"%s\",\"n\":\"%s\",\"p\":\"%s\",\"e\":%d}",
             i ? "," : "", _mon[i].key, _mon[i].name, _mon[i].pass,
             _mon[i].enabled ? 1 : 0);
  }
  f.print("]}");
  f.close();
}

static void loadMonitors() {
  memset(_mon, 0, sizeof(_mon));
  _mon_count = 0;
  _mon_interval = 900;
  if (!_fs) return;

  File f = _fs->open(MMMON_CFG_FILE, "r");
  if (!f) return;
  String s = f.readString();
  f.close();

  auto strOf = [](const String &src, const char *key, char *out, size_t max) {
    String pat = String("\"") + key + "\":\"";
    int i = src.indexOf(pat);
    if (i < 0) { out[0] = 0; return; }
    i += pat.length();
    int j = src.indexOf('"', i);
    if (j < 0) { out[0] = 0; return; }
    String v = src.substring(i, j);
    strncpy(out, v.c_str(), max - 1);
    out[max - 1] = 0;
  };
  auto intOf = [](const String &src, const char *key, long fallback) -> long {
    String pat = String("\"") + key + "\":";
    int i = src.indexOf(pat);
    if (i < 0) return fallback;
    i += pat.length();
    long v = 0;
    bool any = false;
    while (i < (int)src.length() && src[i] >= '0' && src[i] <= '9') {
      v = v * 10 + (src[i++] - '0');
      any = true;
    }
    return any ? v : fallback;
  };

  long iv = intOf(s, "iv", 900);
  _mon_interval = (iv < 60) ? 60 : (iv > 65535 ? 65535 : (uint16_t)iv);

  int pos = 0;
  while (_mon_count < MAX_MONITORS) {
    int b = s.indexOf("{\"k\":\"", pos);
    if (b < 0) break;
    int e = s.indexOf('}', b);
    if (e < 0) break;
    String item = s.substring(b, e + 1);
    pos = e + 1;

    MonEntry &m = _mon[_mon_count];
    memset(&m, 0, sizeof(m));
    m.mesh_idx = -1;
    strOf(item, "k", m.key, sizeof(m.key));
    strOf(item, "n", m.name, sizeof(m.name));
    strOf(item, "p", m.pass, sizeof(m.pass));
    m.enabled = intOf(item, "e", 1) != 0;
    if (normaliseKey(m.key) && findMonitor(m.key) < 0) _mon_count++;
  }
}

/* Rebuilds MyMesh's radio-level table. Only entries whose full 32-byte key we
 * know can go in: the shared secret is ECDH over the whole key, so a pasted
 * prefix stays inert until we hear that repeater advertise itself. */
static void syncMonitorsToMesh() {
  if (!_mesh) return;
  _mesh->clearMonitors();
  for (int i = 0; i < _mon_count; i++) {
    _mon[i].mesh_idx = -1;
    _mon[i].logged_in = false;
    if (strlen(_mon[i].key) < 64) continue;

    uint8_t key[PUB_KEY_SIZE];
    if (!mesh::Utils::fromHex(key, PUB_KEY_SIZE, _mon[i].key)) continue;
    int idx = _mesh->addMonitor(key);
    if (idx >= 0) _mon[i].mesh_idx = (int8_t)idx;
  }
}

/* Matches entries against the heard list: upgrades a prefix to the full key,
 * and lets the advert name win over anything typed by hand. Cheap enough (a
 * handful of memcmp) to run once per round. */
static bool resolveMonitors() {
  if (!_mesh) return false;
  bool changed = false;

  for (int i = 0; i < _mon_count; i++) {
    size_t hex_len = strlen(_mon[i].key);
    int plen = (int)(hex_len / 2);
    if (plen > PUB_KEY_SIZE) plen = PUB_KEY_SIZE;

    uint8_t prefix[PUB_KEY_SIZE];
    if (!mesh::Utils::fromHex(prefix, plen, _mon[i].key)) continue;

    uint8_t full[PUB_KEY_SIZE];
    char nm[MON_NAME_MAX];
    nm[0] = 0;

    if (_mesh->findNeighbourByPrefix(prefix, plen, full, nm, sizeof(nm))) {
      if (hex_len < 64) {
        mesh::Utils::toHex(_mon[i].key, full, PUB_KEY_SIZE);
        hex_len = 64;
        changed = true;
      }
    }
    /* Fall back to the stored adverts, which is what makes a name reappear
     * after a restart instead of waiting hours for the next advert. */
    if (nm[0] == 0) {
      const char *cached = meshmanager_advert_name(prefix, plen);
      if (cached) {
        strncpy(nm, cached, sizeof(nm) - 1);
        nm[sizeof(nm) - 1] = 0;
      }
    }
    if (nm[0] && strcmp(_mon[i].name, nm) != 0) {
      strncpy(_mon[i].name, nm, sizeof(_mon[i].name) - 1);
      _mon[i].name[sizeof(_mon[i].name) - 1] = 0;
      changed = true;
    }
  }
  return changed;
}

void meshmanager_on_monitor_response(int mon_idx, uint8_t type, const uint8_t *data, int len) {
  if (!_started || _disabled || _safe_mode) return;
  if (len <= 0 || len > (int)sizeof(_mon_reply)) return;
  if (_mon_got_reply) return;            // previous one not consumed yet

  memcpy(_mon_reply, data, len);
  _mon_reply_len = len;
  _mon_reply_idx = mon_idx;
  _mon_reply_type = type;
  _mon_got_reply = true;
}

/* Publishes one polled repeater on OUR OWN stats topic, with the subject's
 * prefix in the payload.
 *
 * This used to go to a '<prefix>/<node>/mon' topic of my own invention, and
 * that was the bug: the receiving side subscribes to exactly two patterns,
 * '<prefix>/+/stats' and '<prefix>/+/rx'. Everything else reaches the broker
 * and is discarded there unread, which is why two successful readings produced
 * a publish that returned success and a site that never heard of them.
 *
 * The relay case is deliberately supported at the far end: the topic names who
 * published, repeater.pubkey_prefix names who it is about, and the difference
 * is recorded as source_prefix. So the right topic is the ordinary one. */
// Clears the collected results; called when a peer's turn begins.
static void monResetResults() {
  memset(&_mon_st, 0, sizeof(_mon_st));
  _mon_have_st = false;
  _mon_st_len = 0;
  _mon_have_nbr = false;
  _mon_nbr_len = 0;
  _mon_tl_n = 0;
}

/* Decodes a CayenneLPP telemetry reply. Only temperature and voltage are kept:
 * those are the two the site has fields for, and skipData() lets us step over
 * everything else without having to understand it. */
static void monDecodeTelemetry(const uint8_t *data, int len) {
  _mon_tl_n = 0;
  if (len <= 4) return;

  LPPReader reader(&data[4], (uint8_t)(len - 4));
  uint8_t channel, type;
  while (_mon_tl_n < MON_TELEM_MAX && reader.readHeader(channel, type)) {
    float v;
    if (type == LPP_TEMPERATURE && reader.readTemperature(v)) {
      _mon_tl[_mon_tl_n].channel = channel;
      _mon_tl[_mon_tl_n].kind = 't';
      _mon_tl[_mon_tl_n].value = v;
      _mon_tl_n++;
    } else if (type == LPP_VOLTAGE && reader.readVoltage(v)) {
      _mon_tl[_mon_tl_n].channel = channel;
      _mon_tl[_mon_tl_n].kind = 'v';
      _mon_tl[_mon_tl_n].value = v;
      _mon_tl_n++;
    } else {
      reader.skipData(type);
    }
  }
}

/* Publishes one polled repeater on OUR OWN stats topic, with the subject's
 * prefix in the payload.
 *
 * This used to go to a '<prefix>/<node>/mon' topic of my own invention, and
 * that was the bug: the receiving side subscribes to exactly two patterns,
 * '<prefix>/+/stats' and '<prefix>/+/rx'. Everything else reaches the broker
 * and is discarded there unread, which is why two successful readings produced
 * a publish that returned success and a site that never heard of them.
 *
 * The relay case is deliberately supported at the far end: the topic names who
 * published, repeater.pubkey_prefix names who it is about, and the difference
 * is recorded as source_prefix. So the right topic is the ordinary one. */
static bool publishMonitorRound(MonEntry &m) {
  if (!_mon_have_st && _mon_tl_n == 0 && !_mon_have_nbr) return false;
  if (!mqttEnsure()) return false;

  char prefix_hex[13];
  memcpy(prefix_hex, m.key, 12);
  prefix_hex[12] = 0;

  /* The key needs no escaping -- normaliseKey() refuses anything that is not
   * hex before an entry ever reaches the list -- but the name does: it is
   * either typed on the admin page or taken straight from that repeater's
   * advert, and neither is under our control. Twice MON_NAME_MAX is the worst
   * case, a name made entirely of quotes. */
  char name_esc[MON_NAME_MAX * 2];
  jsonEsc(name_esc, sizeof(name_esc), m.name);

  static char body[MQTT_PUB_MAX];
  int p = snprintf(body, sizeof(body),
    "{\"repeater\":{\"pubkey_prefix\":\"%s\",\"name\":\"%s\"},\"metrics\":{\"online\":true",
    prefix_hex, name_esc);
  if (p <= 0 || p >= (int)sizeof(body)) return false;

  if (_mon_have_st) {
    const RepeaterStats &st = _mon_st;

    /* Two separate reasons a field may be missing, and both must leave it out
     * rather than send a zero:
     *   ST_HAS  -- an older firmware sent a shorter struct, so those bytes
     *              never arrived and our zeroed copy reads 0
     *   physics -- the bytes arrived but the far side never filled them in. A
     *              noise floor or an RSSI in dBm cannot be >= 0; JessaZH
     *              reports noise_floor 0, which means 'my driver does not
     *              measure this', not 'the band is silent'.
     * Counters stay unfiltered: zero packets sent is a fact, not a gap. */
    #define ST_HAS(f) ((int)(offsetof(RepeaterStats, f) + sizeof(st.f)) <= _mon_st_len)

    p += snprintf(body + p, sizeof(body) - p,
      ",\"uptime\":%.5f,\"airtime\":%.1f,\"rx_airtime\":%.1f,"
      "\"nb_recv\":%u,\"nb_sent\":%u,\"sent_flood\":%u,\"sent_direct\":%u,"
      "\"recv_flood\":%u,\"recv_direct\":%u,\"tx_queue_len\":%u",
      st.total_up_time_secs / 86400.0,
      st.total_air_time_secs / 60.0f, st.total_rx_air_time_secs / 60.0f,
      (unsigned)st.n_packets_recv, (unsigned)st.n_packets_sent,
      (unsigned)st.n_sent_flood, (unsigned)st.n_sent_direct,
      (unsigned)st.n_recv_flood, (unsigned)st.n_recv_direct,
      (unsigned)st.curr_tx_queue_len);

    if (ST_HAS(noise_floor) && st.noise_floor < 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"noise_floor\":%d", (int)st.noise_floor);
    }
    if (ST_HAS(last_rssi) && st.last_rssi < 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"last_rssi\":%d", (int)st.last_rssi);
    }
    // SNR of 0.0 dB is a real reading; only a node that heard nothing has none.
    if (ST_HAS(last_snr) && st.n_packets_recv > 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"last_snr\":%.2f", st.last_snr / 4.0f);
    }
    if (ST_HAS(err_events)) {
      p += snprintf(body + p, sizeof(body) - p, ",\"err_events\":%u", (unsigned)st.err_events);
    }
    if (ST_HAS(n_flood_dups)) {
      p += snprintf(body + p, sizeof(body) - p, ",\"direct_dups\":%u,\"flood_dups\":%u",
                    (unsigned)st.n_direct_dups, (unsigned)st.n_flood_dups);
    }
    if (ST_HAS(n_recv_errors)) {
      p += snprintf(body + p, sizeof(body) - p, ",\"recv_errors\":%u",
                    (unsigned)st.n_recv_errors);
    }
    /* battery_percentage is derived rather than asked for: the same cell
     * voltage through the same shared curve, so it costs no extra packet. A
     * board reporting no usable voltage gets neither field. */
    int pct = meshmanager_batt_percent(st.batt_milli_volts);
    if (pct >= 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"bat\":%.3f,\"battery_percentage\":%d",
                    st.batt_milli_volts / 1000.0f, pct);
    }
    #undef ST_HAS
  }

  /* Under the channel the source itself used. On a MeshCore repeater channel 1
   * is its own board, so ch1_temperature there is the MCU die rather than the
   * outside air -- but that is the far side's naming to make, not ours to
   * reinterpret. */
  for (int i = 0; i < _mon_tl_n && p < (int)sizeof(body) - 64; i++) {
    p += snprintf(body + p, sizeof(body) - p, ",\"ch%u_%s\":%.2f",
                  (unsigned)_mon_tl[i].channel,
                  _mon_tl[i].kind == 't' ? "temperature" : "voltage",
                  _mon_tl[i].value);
  }

  int nbr_written = 0;
  char nbr_json[MONITOR_NBR_MAX * 56 + 32];
  nbr_json[0] = 0;

  if (_mon_have_nbr && _mon_nbr_len >= 8) {
    int16_t total, returned;
    memcpy(&total, &_mon_nbr[4], 2);
    memcpy(&returned, &_mon_nbr[6], 2);
    if (total >= 0) {
      p += snprintf(body + p, sizeof(body) - p, ",\"neighbor_count\":%d", (int)total);
    }

    int q = snprintf(nbr_json, sizeof(nbr_json), ",\"neighbors\":[");
    const int entry = MONITOR_NBR_PREFIX + 5;      // key + 4 age + 1 snr
    for (int i = 0; i < returned; i++) {
      int off = 8 + i * entry;
      if (off + entry > _mon_nbr_len) break;

      char hex[MONITOR_NBR_PREFIX * 2 + 1];
      mesh::Utils::toHex(hex, &_mon_nbr[off], MONITOR_NBR_PREFIX);
      uint32_t secs_ago;
      memcpy(&secs_ago, &_mon_nbr[off + MONITOR_NBR_PREFIX], 4);
      int8_t snr4 = (int8_t)_mon_nbr[off + MONITOR_NBR_PREFIX + 4];

      /* No name field: their reply carries only key, age and SNR, and the far
       * end keeps any name it already had when we leave it out. */
      int w = snprintf(nbr_json + q, sizeof(nbr_json) - q,
                       "%s{\"prefix\":\"%s\",\"snr\":%.2f,\"seen_min\":%u}",
                       nbr_written ? "," : "", hex, snr4 / 4.0f,
                       (unsigned)(secs_ago / 60));
      if (w <= 0 || (size_t)(q + w) >= sizeof(nbr_json) - 2) break;
      q += w;
      nbr_written++;
    }
    snprintf(nbr_json + q, sizeof(nbr_json) - q, "]");
  }

  p += snprintf(body + p, sizeof(body) - p, "}%s,\"via\":\"%s\"}",
                nbr_written ? nbr_json : "", _node_hex);
  if (p <= 0 || p >= (int)sizeof(body)) return false;

  char topic[96];
  mqttTopic("stats", topic, sizeof(topic));
  if (!_mqtt.publish(topic, (const uint8_t *)body, p, false)) {
    _fail_count++;
    _mqtt_err = "stats";
    return false;
  }
  m.pubs++;
  m.fails = 0;                 // something came back: no reason to rest
  m.last_ok = millis();
  monTrace("pub st=%d tl=%d nb=%d %db", _mon_have_st ? 1 : 0, _mon_tl_n, nbr_written, p);
  return true;
}

// A round that yielded nothing at all counts towards the backoff.
static void monRoundFailed(MonEntry &m) {
  if (m.fails < 255) m.fails++;
}

// ------------------------------------- CLI settings of a MONITORED repeater

/* Asking another repeater what its settings are, over the air.
 *
 * Why this exists. A repeater that does not publish to MQTT itself -- ours
 * hangs on a hospital roof and is read out by the node in my house -- had no
 * command path at all. The site could show its statistics, because those are
 * relayed, and could show nothing about its configuration, because the only
 * way to a CLI ran through Home Assistant, and taking Home Assistant out of
 * the chain was the whole point of 1.8.0. The monitor already ACCEPTED text
 * answers from a monitored node (handleMonitorData forwards TXT_MSG, and has
 * since 1.4.0); what was missing was anything that ever ASKED.
 *
 * So: the same SET_PARAMS table the node reads from its own CLI once a day,
 * asked of somebody else's CLI one command at a time, and published on the
 * same stats topic in the same "settings" object -- with the monitored node in
 * repeater.pubkey_prefix, which is how a relayed reading already names its
 * subject. The receiving side needs no new message type and no new topic; see
 * publishMonitorSettings() for the one rule on the server that did have to
 * give, and why.
 *
 * The table is reusable over the air because of a fix that had nothing to do
 * with this: 1.7.1 replaced the bare 'region' command, whose answer is a
 * multi-line tree, with 'region home' and 'region default', whose answers are
 * one line each. Every entry now answers in a single packet, which is what
 * makes 'one command, one reply, next' a correct description rather than an
 * optimistic one -- there is exactly one staging slot for an incoming reply.
 *
 * ---- What this costs, and the limits that bound it -----------------------
 *
 * This is the expensive thing in this file. The daily sweep of our OWN
 * settings costs no airtime whatsoever: handleCommand() is a function call.
 * This one puts eighteen requests and up to eighteen replies on a shared band,
 * and the node paying half of that is a solar repeater on a roof that may
 * never become unreachable. Every number below exists to bound that.
 *
 *  - ON REQUEST ONLY. There is no daily version of this and there must not be:
 *    a schedule would spend that airtime forever, for values that change once
 *    a year. Somebody presses a button, or nothing happens.
 *  - MON_SET_MIN_GAP_MS between two sweeps, whichever node they are for. A
 *    page that gets reloaded, or a browser tab left on a refresh, must not be
 *    able to keep the band busy. Ten minutes is far longer than the couple of
 *    minutes a sweep needs, so a legitimate second attempt is never blocked by
 *    it, and it caps this feature at roughly 1% of the hour whatever anyone
 *    does upstream.
 *  - MON_SET_GAP_MS between two commands, so eighteen round trips are spread
 *    over minutes instead of fired as fast as the far side answers. LoRa is a
 *    shared medium and this node is a repeater: its day job is relaying other
 *    people's packets, and a burst from the relay itself is the least excusable
 *    kind of congestion. Two seconds is what the Home Assistant implementation
 *    settled on for the same sequence, on the same band, and it works.
 *  - MON_SET_STEP_MS per parameter, and no per-parameter retry. Home Assistant
 *    waits 12 s for the first answer and then runs one extra round for the
 *    parameters that stayed silent; the wait is right (it is measured, over
 *    the same hops) and the extra round is not. Home Assistant runs on mains
 *    power through a USB-attached node; here a second round doubles the cost
 *    of the whole sweep to chase the parameters least likely to answer. A
 *    silent parameter is published as 'no answer' instead, which is worth more
 *    than a value fetched at twice the price.
 *  - MON_SET_FIRST_MS for the first command only. It is the first packet that
 *    depends on the path learned from the login, and the poll sequence found
 *    out the hard way (1.3.1) that this is where a route goes wrong rather
 *    than where a session does.
 *  - MON_SET_SILENT_MAX consecutive silences ends the sweep. Somebody who is
 *    not answering the third parameter is not going to answer the eighteenth,
 *    and continuing means transmitting fifteen more times into a hole. This is
 *    the common failure and it has an ordinary cause: the far side only runs a
 *    CLI command for a client with ADMIN rights (see onPeerDataRecv in
 *    MyMesh.cpp), so a monitor that logs in read-only -- which is enough for
 *    everything else in this file, and is what the header recommends -- gets a
 *    login that succeeds and eighteen commands that are silently ignored.
 *  - MON_SET_TOTAL_MS caps the whole thing regardless. While a sweep runs the
 *    ordinary poll rounds wait, because they share this state machine; the cap
 *    is what keeps 'wait' from meaning 'until the next reboot'.
 *
 * ---- When it fails ------------------------------------------------------
 *
 * Two failures that look alike from a distance are kept apart on purpose:
 *
 *   the login never answered   nothing was asked, so nothing is published. The
 *                              site keeps showing the values it had, with
 *                              their old timestamps, which is what 'we learned
 *                              nothing' honestly looks like. Publishing
 *                              eighteen nulls here would throw away values an
 *                              earlier sweep did get, for a fault that says
 *                              nothing about any individual parameter.
 *   we were logged in and
 *   parameters stayed silent   published, with null for each one that did not
 *                              answer. The site renders that as "(geen
 *                              antwoord)", the same phrase the Home Assistant
 *                              path has always produced for the same fact. It
 *                              overwrites what we knew, and that is intended:
 *                              here we did ask, and 'they would not tell us'
 *                              is a fresher fact than a value from March.
 */

/* First command after a login: the packet that proves the path, so it gets the
 * room the poll sequence learned to give the equivalent step. */
#define MON_SET_FIRST_MS     20000UL
// Per parameter after that. Home Assistant's measured 12 s, same band, same hops.
#define MON_SET_STEP_MS      12000UL
#define MON_SET_GAP_MS        2000UL   // breathing space between two commands
#define MON_SET_SILENT_MAX        3    // consecutive silences that end a sweep
/* Hard cap on one sweep, whatever happens.
 *
 * Raised from 300 s in 1.11.0, when the table went from eighteen parameters to
 * nineteen. The nominal cost of a sweep is MON_SET_FIRST_MS plus (n-1) times
 * MON_SET_STEP_MS + MON_SET_GAP_MS: 258 s at eighteen, 272 s at nineteen, and
 * 286 s at twenty since 'cmd:ver' joined the table. A cap only 28 s above the
 * nominal run does not bound a runaway, it truncates a normal one -- the last
 * parameters would be dropped by the budget every time anything went slightly
 * slowly, and 'geen antwoord' would be reported for commands that were never
 * sent. 360 s restores roughly the margin the 300 s cap had at eighteen, and
 * still leaves 74 s of it at twenty. What a longer cap actually costs is a poll
 * round that starts later, since the two share this state machine, and that is
 * the cheaper of the two failures by a wide margin.
 *
 * The next parameter added here is worth a moment's arithmetic rather than a
 * shrug: at twenty-four the nominal run is 342 s and the margin is gone. */
#define MON_SET_TOTAL_MS    360000UL
#define MON_SET_MIN_GAP_MS  600000UL   // between two sweeps, for any node

/* The pending request is held as a key rather than as an index into _mon[],
 * because the list can be edited between the request and the moment the state
 * machine is free to act on it -- monDelete() shifts everything after the gap
 * down by one. An index would then quietly address the wrong repeater, which
 * is the one failure this feature must not have: it logs in and runs commands
 * somewhere. Empty means nothing is waiting. */
static char _mset_req_key[MON_KEY_HEX_MAX] = {0};
static int  _mset_cur = -1;      // entry the running sweep is for, -1 = not running
static int  _mset_next = 0;      // parameter being collected
static int  _mset_asked = 0;     // commands that actually went on the air
static int  _mset_ok = 0;        // parameters with a usable answer
static int  _mset_miss = 0;      // parameters asked (or not sent) without one
static uint8_t _mset_silent = 0; // consecutive silences, reset by any answer
/* Indexed by parameter, not packed like _set_vals, because a missing parameter
 * has to stay identifiable: this sweep publishes what it did NOT get as well. */
static char _mset_vals[SET_PARAM_COUNT][SET_VALUE_MAX];
static bool _mset_got[SET_PARAM_COUNT];
static unsigned long _mset_send_at = 0;   // next command is due; 0 = none waiting
static unsigned long _mset_until = 0;     // whole-sweep budget
/* A request waits for the state machine to reach MST_IDLE, and everything that
 * can hold it there is temporary -- a poll round, a flat battery, monitoring
 * switched off. Almost. Without an expiry, one request that never got its turn
 * would answer every later one with 'er loopt er al een' until the next reboot,
 * and the reason would be invisible. Same budget as a sweep itself: if it could
 * not even start inside the time a whole sweep is allowed to take, it is stale
 * and whoever asked has long since reloaded the page. */
static unsigned long _mset_req_until = 0;
static unsigned long _mset_done_at = 0;   // last finished sweep, for the min gap
static int _mset_last_idx = -1;           // which entry that was, for the CLI
static int _mset_last_ok = 0, _mset_last_miss = 0;

/* Lowercases a pasted key and drops the separators, like normaliseKey(), but
 * with a lower floor: eight hex characters instead of twelve.
 *
 * The difference is deliberate. normaliseKey() ADDS a node to the monitor list,
 * where too short a key means monitoring the wrong repeater. This one only
 * SELECTS from a list that already exists, and has to be refused outright when
 * it matches more than one entry -- so a collision cannot silently pick wrong.
 * Eight is also what the site treats as the shortest key it dares call the same
 * node (MIN_PREFIX_MATCH in commanding.py) and what a Home Assistant five-byte
 * key still satisfies, so refusing those would have meant refusing the exact
 * repeaters this feature exists for. Odd lengths are allowed for the same
 * reason: nothing here is decoded, it is compared as text. */
static bool monKeyArg(char *key) {
  char out[MON_KEY_HEX_MAX];
  int n = 0;
  for (const char *p = key; *p; p++) {
    char c = *p;
    if (c == ' ' || c == ':' || c == '-' || c == '\t') continue;
    if (c >= 'A' && c <= 'F') c = (char)(c - 'A' + 'a');
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    if (n >= MON_KEY_HEX_MAX - 1) return false;
    out[n++] = c;
  }
  out[n] = 0;
  if (n < 8 || n > 64) return false;
  strcpy(key, out);
  return true;
}

// -1 = no entry matches this key, -2 = more than one does.
static int monFindByPrefix(const char *key) {
  int found = -1;
  for (int i = 0; i < _mon_count; i++) {
    if (!sameNode(_mon[i].key, key)) continue;
    if (found >= 0) return -2;
    found = i;
  }
  return found;
}

static bool monSettingsRequest(const char *key_hex, const char **why) {
  char key[MON_KEY_HEX_MAX];
  strncpy(key, key_hex ? key_hex : "", sizeof(key) - 1);
  key[sizeof(key) - 1] = 0;

  if (!monKeyArg(key))          { *why = "sleutel is geen hex of korter dan 8 tekens"; return false; }
  if (_mon_count == 0)          { *why = "deze node monitort niemand"; return false; }
  /* Refused here rather than started and stranded. Both of these stop
   * monitorLoop() before it ever reaches the state machine, so a sweep asked
   * for now would sit in the queue instead of running -- and the queue holds
   * one, so it would also block the next attempt. The person asking deserves
   * the reason, and publishing is the entire point of the exercise anyway. */
  if (!_cfg.mqtt_enabled || _cfg.mqtt_host[0] == 0) {
    *why = "MQTT staat uit, er is nergens om het te publiceren"; return false;
  }
  if (_batt_known && _batt_pct < _cfg.bat_mon) {
    *why = "batterij te laag om andere repeaters uit te vragen"; return false;
  }
  int i = monFindByPrefix(key);
  if (i == -2)                  { *why = "sleutel past op meer dan een gemonitorde node"; return false; }
  if (i < 0)                    { *why = "staat niet in de monitorlijst"; return false; }
  if (!_mon[i].enabled)         { *why = "monitoren staat uit voor die node"; return false; }
  /* Without the full 32-byte key there is no shared secret, so there is nothing
   * to encrypt a command with. syncMonitorsToMesh() fills this in as soon as we
   * hear that repeater advertise itself; until then the honest answer is that
   * we cannot reach them yet, not a sweep that quietly sends nothing. */
  if (_mon[i].mesh_idx < 0)     { *why = "volledige sleutel nog niet gehoord"; return false; }
  if (_mset_req_key[0] && passed(_mset_req_until)) _mset_req_key[0] = 0;   // stale, see above
  if (_mset_cur >= 0 || _mset_req_key[0]) { *why = "er loopt er al een"; return false; }
  if (_mset_done_at != 0 && millis() - _mset_done_at < MON_SET_MIN_GAP_MS) {
    *why = "te kort na de vorige sweep"; return false;
  }
  strcpy(_mset_req_key, key);
  _mset_req_until = millis() + MON_SET_TOTAL_MS;
  return true;
}

/* Publishes the result on OUR OWN stats topic, with the monitored node named in
 * the payload -- the same shape publishMonitorRound() uses, for the same reason
 * (see the note there: any other topic is accepted by the broker and dropped
 * unread). metrics is deliberately an empty object: this message carries no
 * measurement, and inventing one so the envelope looks familiar is how a graph
 * ends up with a point that never happened. The far end walks an empty metrics
 * dict without writing anything, which is exactly right.
 *
 * One rule on the server did have to give for this, and it is worth naming
 * here because it is a security decision made elsewhere: mqtt_ingest.py used to
 * refuse settings from any node reporting about somebody else, on the grounds
 * that the firmware only ever sent its own. That is no longer true, so the rule
 * is now 'from the node that already relays this repeater's statistics'. See
 * _handle_settings there for what that does and does not buy. */
static bool publishMonitorSettings(MonEntry &m) {
  if (!mqttEnsure()) return false;

  char prefix_hex[13];
  memcpy(prefix_hex, m.key, 12);
  prefix_hex[12] = 0;

  // Same reason as in publishMonitorRound(): the name is theirs, not ours.
  char name_esc[MON_NAME_MAX * 2];
  jsonEsc(name_esc, sizeof(name_esc), m.name);

  static char body[MQTT_PUB_MAX];
  int p = snprintf(body, sizeof(body),
    "{\"repeater\":{\"pubkey_prefix\":\"%s\",\"name\":\"%s\"},"
    "\"metrics\":{},\"settings\":{", prefix_hex, name_esc);
  if (p <= 0 || p >= (int)sizeof(body)) return false;

  /* Every parameter appears, answered or not. Leaving the silent ones out would
   * publish a half-read sweep that looks exactly like a complete one, and the
   * page would keep showing months-old values next to fresh ones with nothing
   * to tell them apart. null says "asked, no answer" and renders as "(geen
   * antwoord)" -- the same phrase, from the same column, that the Home
   * Assistant path has always produced for the same fact. */
  /* Headroom for the longest single entry: an escaped value (two bytes per
   * source byte, worst case), its name, the quotes, the comma and the closing
   * braces. Was a flat 128, which was true while no value could exceed 32
   * characters and became silently false when cmd:region raised that to 176 --
   * snprintf would then truncate mid-value, the length check at the end would
   * catch it, and the whole message would be dropped rather than one field. */
  const int room = SET_VALUE_MAX * 2 + 96;
  for (int i = 0; i < SET_PARAM_COUNT && p < (int)sizeof(body) - room; i++) {
    if (_mset_got[i]) {
      char esc[SET_VALUE_MAX * 2 + 4];
      jsonEsc(esc, sizeof(esc), _mset_vals[i]);
      p += snprintf(body + p, sizeof(body) - p, "%s\"%s\":\"%s\"",
                    i ? "," : "", SET_PARAMS[i].name, esc);
    } else {
      p += snprintf(body + p, sizeof(body) - p, "%s\"%s\":null",
                    i ? "," : "", SET_PARAMS[i].name);
    }
  }

  p += snprintf(body + p, sizeof(body) - p, "},\"via\":\"%s\"}", _node_hex);
  if (p <= 0 || p >= (int)sizeof(body)) return false;

  char topic[96];
  mqttTopic("stats", topic, sizeof(topic));
  if (!_mqtt.publish(topic, (const uint8_t *)body, p, false)) {
    _fail_count++;
    _mqtt_err = "stats";
    return false;
  }
  /* Deliberately not counted in m.pubs. Those three counters exist so a gap
   * between them means something -- polls attempted, answers parsed, readings
   * published -- and a settings message is none of the three. Raising pubs here
   * would make a healthy node look like it published more readings than it
   * polled for. The trace and 'wifi mon settings' report this instead. */
  return true;
}

/* Ends the running sweep and hands the machine back to the poll scheduler.
 *
 * Straight to MST_IDLE rather than through MST_GAP, because MST_GAP means
 * 'continue the round with the next peer' and there is no round: _mon_next_round
 * was never touched, so the ordinary polling resumes on the schedule it already
 * had, as though this had not happened. */
static void monSettingsFinish(const char *why) {
  if (_mset_cur >= 0) {
    MonEntry &m = _mon[_mset_cur];
    monTrace("set klaar %d/%d ok, %s", _mset_ok, SET_PARAM_COUNT, why);
    Serial.printf("MeshManagerNet: sweep %.12s klaar: %d gelezen, %d geen antwoord (%s)\n",
                  m.key, _mset_ok, _mset_miss, why);
    // Nothing asked means nothing learned; see the block comment above.
    if (_mset_asked > 0 && !publishMonitorSettings(m)) monTrace("set publish mislukt");
    _mset_last_idx = _mset_cur;
    _mset_last_ok = _mset_ok;
    _mset_last_miss = _mset_miss;
  }
  _mset_done_at = millis();
  _mset_cur = -1;
  _mset_next = 0;
  _mset_send_at = 0;
  _mon_cur = -1;
  _mon_retry = 0;
  _mon_state = MST_IDLE;
}

// Schedules the next command, or ends the sweep when there is no next.
static void monSettingsAdvance() {
  _mset_next++;
  if (_mset_next >= SET_PARAM_COUNT) { monSettingsFinish("alle parameters gehad"); return; }
  if (passed(_mset_until))           { monSettingsFinish("tijdsbudget op"); return; }
  _mset_send_at = millis() + MON_SET_GAP_MS;
  _mon_state = MST_CLI_WAIT;
  _mon_deadline = 0;              // nothing is outstanding; the gap decides
}

// Puts one 'get <param>' on the air and waits for the text message back.
static void monSettingsSend() {
  if (_mset_cur < 0 || _mset_next >= SET_PARAM_COUNT) { monSettingsFinish("niets te vragen"); return; }
  MonEntry &m = _mon[_mset_cur];

  if (!_mesh->sendMonitorCliCmd(m.mesh_idx, SET_PARAMS[_mset_next].cmd)) {
    /* Packet pool empty, which is normal under load. Not a silence from the far
     * side, so it does not count towards MON_SET_SILENT_MAX -- but it is still
     * a parameter we hold no value for, and the site has to see that. */
    monTrace("set %s NIET VERSTUURD (pool vol)", SET_PARAMS[_mset_next].name);
    _mset_miss++;
    monSettingsAdvance();
    return;
  }
  bool first = (_mset_asked == 0);
  _mset_asked++;
  _mon_state = MST_CLI_WAIT;
  _mon_deadline = millis() + (first ? MON_SET_FIRST_MS : MON_SET_STEP_MS);
}

/* One text message back from the monitored node. Layout is the one this same
 * firmware builds when it answers a CLI command (onPeerDataRecv in MyMesh.cpp):
 * four bytes of timestamp, one of flags, then the text -- zero-padded out to
 * the cipher block, which is why the text is copied and terminated here rather
 * than read where it lies. */
static void monSettingsReply(const uint8_t *data, int len) {
  if (_mset_cur < 0 || _mset_next < 0 || _mset_next >= SET_PARAM_COUNT) return;
  const SetParam &sp = SET_PARAMS[_mset_next];

  char text[200];
  int n = len - 5;
  if (n > (int)sizeof(text) - 1) n = (int)sizeof(text) - 1;
  if (n < 0) n = 0;
  memcpy(text, data + 5, n);
  text[n] = 0;

  char *val = settingsValue(text, sp);
  if (val) {
    strncpy(_mset_vals[_mset_next], val, SET_VALUE_MAX - 1);
    _mset_vals[_mset_next][SET_VALUE_MAX - 1] = 0;
    _mset_got[_mset_next] = true;
    _mset_ok++;
  } else {
    _mset_miss++;
  }
  /* They answered, whatever the answer said. A refused value is a fact about
   * that one parameter; silence is a fact about the link, and only the second
   * one is a reason to stop asking. */
  _mset_silent = 0;
  monTrace("set %s = %.16s", sp.name, val ? val : "(geweigerd)");
  monSettingsAdvance();
}

/* Starts a staged sweep. Called from MST_IDLE only, so no poll round is in
 * progress and _mon_cur is free to point at our subject -- the reply matching
 * further down keys off it and needs no second mechanism. */
static void monSettingsBegin() {
  char key[MON_KEY_HEX_MAX];
  strcpy(key, _mset_req_key);
  _mset_req_key[0] = 0;                    // consumed, whatever happens next

  int i = monFindByPrefix(key);
  if (!_mesh || i < 0 || !_mon[i].enabled || _mon[i].mesh_idx < 0) {
    // The list changed under us between the request and this moment.
    monTrace("set %.6s vervallen (lijst gewijzigd)", key);
    return;
  }

  _mset_cur = i;
  _mset_next = 0;
  _mset_asked = 0;
  _mset_ok = 0;
  _mset_miss = 0;
  _mset_silent = 0;
  _mset_send_at = 0;
  _mset_until = millis() + MON_SET_TOTAL_MS;
  memset(_mset_got, 0, sizeof(_mset_got));
  memset(_mset_vals, 0, sizeof(_mset_vals));

  MonEntry &m = _mon[_mset_cur];
  _mon_cur = _mset_cur;
  _mon_retry = 0;
  int hops = _mesh->getMonitorPathLen(m.mesh_idx);

  /* A session from an earlier poll is reused when we have one: a login is a
   * flooded packet, and spending one to learn what we already know is the most
   * expensive way to be tidy. If the far side has since forgotten us the
   * commands go unanswered, and MON_SET_SILENT_MAX ends the sweep -- at which
   * point logged_in is cleared and the next attempt starts with a login. */
  if (m.logged_in) {
    monTrace("set start %.6s %s hops=%d", m.key, hops < 0 ? "FLOOD" : "direct", hops);
    monSettingsSend();
    return;
  }
  if (_mesh->sendMonitorLogin(m.mesh_idx, m.pass)) {
    monTrace("set login %.6s %s hops=%d", m.key, hops < 0 ? "FLOOD" : "direct", hops);
    _mon_state = MST_LOGIN_WAIT;
    _mon_deadline = millis() + MON_STEP_MS;
    return;
  }
  monTrace("set login NIET VERSTUURD (pool vol) %.6s", m.key);
  monSettingsFinish("login niet verstuurd");
}

/* ---- EEN instelling schrijven naar een gemonitorde node -------------------
 *
 * De tegenhanger van de sweep hierboven, en de reden dat deze module bestaat
 * voor een dakrepeater die geen IP-pad heeft en er ook nooit een krijgt. Lezen
 * over LoRa werkt al lang; dit is dezelfde weg de andere kant op.
 *
 * Wat er over de lucht gaat is precies twee commando's, en die tweede is niet
 * optioneel:
 *
 *     set <param> <waarde>
 *     get <param>
 *
 * ---- waarom er teruggelezen wordt, en waarom dat HIER zwaarder weegt ------
 *
 * MeshCore antwoordt "OK" op dingen die het niet heeft overgenomen. 'set lat'
 * is een kale atof(), en atof("noord") is 0.0; 'advert.interval' wordt bewaard
 * als minuten/2 in een byte, dus 61 legt 30 vast en leest terug als 60. Over IP
 * is dat hinderlijk -- je ziet het meteen, en je zet het terug. Hier is het
 * erger, om twee redenen die elkaar versterken: het doel is een node waar geen
 * tweede weg naartoe loopt, en een ronde duurt lang genoeg dat niemand het uit
 * zichzelf natrekt. Een "OK" die niemand controleert is op zo'n node precies de
 * halve waarheid waar dit project omheen gebouwd is.
 *
 * Dus wordt het antwoord van 'set' NIET als uitslag gerapporteerd. De uitslag
 * is wat 'get' teruggeeft, met de vraag ernaast, en het verschil tussen die
 * twee is een eigen toestand -- niet een vorm van geslaagd.
 *
 * ---- de vergelijking gebeurt bewust niet hier -----------------------------
 *
 * Deze machine bewaart de twee ANTWOORDEN als tekst en verder niets. Wat er
 * uiteindelijk 'toegepast' en 'precies' heet, rekent handleMonCfgGet() uit met
 * cfgSameValue() -- dezelfde functie die /api/cfg gebruikt. Een tweede
 * vergelijking hier zou een tweede plek zijn waar "869.525 250 11 5" en
 * "869.525,250,11,5" wel of niet dezelfde waarde heten, en de dag dat die twee
 * het oneens worden is de dag dat er een waarschuwing staat bij een radio die
 * gewoon goed staat. Een melding die te vaak afgaat is net zo onbruikbaar als
 * een die nooit afgaat.
 *
 * ---- wat dit kost, en de grenzen eromheen ---------------------------------
 *
 * Twee commando's en twee antwoorden, dus ruwweg een tiende van een sweep. De
 * getallen zijn desondanks die van de sweep, want ze zijn gemeten op dezelfde
 * band over dezelfde hops en er is geen reden waarom een 'set' sneller zou
 * terugkomen dan een 'get'.
 *
 *  - OP VERZOEK ALLEEN. Er is geen schema en er komt er geen. Een schrijfactie
 *    hoort een besluit te zijn.
 *  - Een tegelijk. De toestandsmachine is er een, en een tweede 'set' terwijl
 *    de eerste nog niet teruggelezen is zou de uitslag van de eerste wissen.
 *  - MON_WSET_MIN_GAP_MS tussen twee schrijfacties, en die staat met opzet
 *    LAGER dan die van de sweep (een minuut tegen tien). Dat is geen slordigheid
 *    maar de belangrijkste afweging in dit blok: de handeling die je na een
 *    misser meteen wilt doen, is de omgekeerde. Wie 'tx 5' zette waar 'tx 20'
 *    hoorde, moet dat binnen een minuut kunnen rechtzetten en niet binnen tien.
 *    Herstel mag nooit zwaarder afgeremd worden dan de fout die het terugdraait.
 *    Wat de grens wel tegenhoudt is een script dat de band vol zet, en daar is
 *    een minuut ruim genoeg voor: hij ligt boven de duur van een hele ronde.
 *  - MON_WSET_TOTAL_MS begrenst er een, wat er ook gebeurt. Terwijl dit loopt
 *    wachten de gewone pollrondes, want ze delen deze machine.
 *
 * ---- de stilte die hier nieuw is ------------------------------------------
 *
 * Bij lezen betekent geen antwoord: we weten niets. Bij schrijven betekent geen
 * antwoord op de 'set' iets anders en veel onaangenamers: het commando is de
 * lucht in gegaan en we weten NIET of het is aangekomen. De node kan het
 * uitgevoerd hebben en het antwoord kan onderweg verdwenen zijn.
 *
 * Dat wordt daarom als eigen einde gerapporteerd ("geen antwoord op set") en
 * nadrukkelijk niet als mislukt. Wie het ziet, weet dat de enige manier om
 * erachter te komen een nieuwe uitleesronde is -- en dat is precies wat de site
 * erbij zet. Een mislukking melden waar onzekerheid hoort, zou iemand laten
 * denken dat er niets gebeurd is. */

/* Ruimte voor een sleutel en een waarde uit CFG_PARAMS. Die tabel staat verderop
 * in dit bestand (ze hoort bij de webkant), dus haar maten kunnen hier nog niet
 * gebruikt worden -- onderaan bij die tabel staat een static_assert die
 * controleert dat deze twee niet krapper zijn geworden. */
#define MON_WSET_PARAM_MAX   28
#define MON_WSET_VALUE_MAX   40
/* Een CLI-antwoord is 160 byte. Dat past hier niet en hoeft ook niet: wat we
 * bewaren is een waarde of een foutregel, en een node die meer terugstuurt dan
 * dit heeft geen waarde teruggestuurd. Afkappen is dan het juiste antwoord --
 * de vergelijking mislukt en de pagina meldt 'niet precies' in plaats van een
 * halve regel als waarde te presenteren. */
#define MON_WSET_REPLY_MAX   96

#define MON_WSET_FIRST_MS    20000UL   // eerste commando na een login
#define MON_WSET_STEP_MS     12000UL   // per commando daarna
#define MON_WSET_GAP_MS       2000UL   // adempauze tussen 'set' en 'get'
#define MON_WSET_TOTAL_MS    90000UL   // harde begrenzing op een schrijfactie
#define MON_WSET_MIN_GAP_MS  60000UL   // tussen twee schrijfacties; zie hierboven

/* Het verzoek, als sleutel en niet als index -- om dezelfde reden als bij de
 * sweep: monDelete() schuift de lijst op, en een index zou daarna stilletjes een
 * andere repeater aanwijzen. Bij schrijven is dat geen ongemak maar de ene fout
 * die deze weg niet mag hebben. */
static char _mwr_req_key[MON_KEY_HEX_MAX] = {0};
static char _mwr_req_param[MON_WSET_PARAM_MAX] = {0};
static char _mwr_req_value[MON_WSET_VALUE_MAX] = {0};
static unsigned long _mwr_req_until = 0;

static int _mwr_cur = -1;            // regel waarvoor er nu geschreven wordt
static uint8_t _mwr_step = 0;        // 0 = 'set' uitstaand, 1 = 'get' uitstaand
static unsigned long _mwr_send_at = 0;   // pauze voor de 'get'; 0 = niets wacht
static unsigned long _mwr_until = 0;     // budget voor deze schrijfactie
static unsigned long _mwr_done_at = 0;   // laatste afronding, voor de minimumgap

/* De opdracht zoals ze gerapporteerd wordt. Blijft na afloop staan: de site
 * vraagt hem op over HTTP en de gebruiker mag de pagina intussen herladen. Een
 * uitslag die verdwijnt zodra je wegklikt, is bij een handeling van een halve
 * minuut hetzelfde als geen uitslag. */
static uint32_t _mwr_seq = 0;        // 0 = er is er sinds de start nooit een geweest
static char _mwr_key[MON_KEY_HEX_MAX] = {0};
static char _mwr_param[MON_WSET_PARAM_MAX] = {0};
static char _mwr_asked[MON_WSET_VALUE_MAX] = {0};
static char _mwr_set_reply[MON_WSET_REPLY_MAX] = {0};   // leeg = geen antwoord
static char _mwr_get_reply[MON_WSET_REPLY_MAX] = {0};   // leeg = geen antwoord
/* Is de 'set' werkelijk de lucht in gegaan? Het verschil tussen deze vlag en een
 * leeg _mwr_set_reply is het verschil tussen twee uitkomsten die je niet mag
 * verwarren. Niet verstuurd (een login die onbeantwoord bleef, een volle
 * pakketpool) betekent dat er met ZEKERHEID niets veranderd is. Wel verstuurd en
 * geen antwoord betekent dat we het niet weten, en dat is de onaangename van de
 * twee. Wie ze op één hoop gooit, laat iemand geruststellen door het verkeerde
 * geval. */
static bool _mwr_sent = false;
static char _mwr_end[48] = {0};      // waarom het eindigde; leeg zolang het loopt
static unsigned long _mwr_started = 0;

/* Stelt een schrijfactie in de wachtrij. De waarde is hier al gecontroleerd:
 * beide aanroepers (POST /api/moncfg en 'wifi mon set') doen dat tegen
 * CFG_PARAMS, met dezelfde functies die /api/cfg gebruikt. Hier staat alleen wat
 * over de MONITOR en het DOEL gaat -- of we die node kennen, of we hem mogen
 * aanspreken, en of de band niet net bezet is. */
static bool monWriteRequest(const char *key_hex, const char *param,
                            const char *value, const char **why) {
  char key[MON_KEY_HEX_MAX];
  strncpy(key, key_hex ? key_hex : "", sizeof(key) - 1);
  key[sizeof(key) - 1] = 0;

  if (!monKeyArg(key))    { *why = "sleutel is geen hex of korter dan 8 tekens"; return false; }
  if (!param || !*param)  { *why = "geen parameter opgegeven"; return false; }
  if (_safe_mode)         { *why = "veilige modus: deze node spreekt niemand aan"; return false; }
  if (!_mesh)             { *why = "mesh nog niet gestart"; return false; }
  if (_mon_count == 0)    { *why = "deze node monitort niemand"; return false; }
  /* Anders dan bij de sweep staat hier GEEN eis dat MQTT aanstaat. De sweep
   * publiceert zijn uitslag op het stats-topic en heeft zonder broker dus
   * niets om voor te doen; deze weg antwoordt aan wie hem vroeg, over HTTP, op
   * hetzelfde netwerk. monitorLoop() laat een schrijfactie daarom door waar hij
   * een pollronde tegenhoudt. Dat is meteen het geval waar het om gaat: een
   * installatie zonder internet, of met een broker die er even niet is, hoort
   * een radio-instelling nog te kunnen rechtzetten. */
  if (_batt_known && _batt_pct < _cfg.bat_mon) {
    *why = "batterij te laag om andere repeaters aan te spreken"; return false;
  }
  int i = monFindByPrefix(key);
  if (i == -2)            { *why = "sleutel past op meer dan een gemonitorde node"; return false; }
  if (i < 0)              { *why = "staat niet in de monitorlijst"; return false; }
  if (!_mon[i].enabled)   { *why = "monitoren staat uit voor die node"; return false; }
  /* Zonder de volledige 32-byte sleutel is er geen gedeeld geheim en dus niets
   * om een commando mee te versleutelen. syncMonitorsToMesh() vult dit aan zodra
   * we die repeater horen adverteren. */
  if (_mon[i].mesh_idx < 0) { *why = "volledige sleutel nog niet gehoord"; return false; }
  if (_mwr_req_key[0] && passed(_mwr_req_until)) _mwr_req_key[0] = 0;   // verlopen
  if (_mwr_cur >= 0 || _mwr_req_key[0]) { *why = "er loopt er al een"; return false; }
  if (_mwr_done_at != 0 && millis() - _mwr_done_at < MON_WSET_MIN_GAP_MS) {
    *why = "te kort na de vorige schrijfactie"; return false;
  }
  /* Een sweep die loopt houdt de machine minutenlang vast. De schrijfactie zou
   * daarna alsnog beginnen, maar wie op een pagina wacht heeft meer aan een
   * weigering met een reden dan aan vijf minuten stilte. */
  if (_mset_cur >= 0)     { *why = "er loopt een uitleesronde"; return false; }

  strcpy(_mwr_req_key, key);
  strncpy(_mwr_req_param, param, sizeof(_mwr_req_param) - 1);
  _mwr_req_param[sizeof(_mwr_req_param) - 1] = 0;
  strncpy(_mwr_req_value, value ? value : "", sizeof(_mwr_req_value) - 1);
  _mwr_req_value[sizeof(_mwr_req_value) - 1] = 0;
  _mwr_req_until = millis() + MON_WSET_TOTAL_MS;
  return true;
}

/* Rondt af en geeft de machine terug aan de pollplanner. Rechtstreeks naar
 * MST_IDLE en zonder _mon_next_round aan te raken, net als monSettingsFinish():
 * de gewone rondes gaan door op het schema dat ze al hadden. */
static void monWriteFinish(const char *why) {
  if (_mwr_cur >= 0) {
    monTrace("wset %.6s klaar: %s", _mon[_mwr_cur].key, why);
    Serial.printf("MeshManagerNet: schrijven %.12s %s = %s klaar (%s); set:'%s' get:'%s'\n",
                  _mon[_mwr_cur].key, _mwr_param, _mwr_asked, why,
                  _mwr_set_reply, _mwr_get_reply);
  }
  strncpy(_mwr_end, why, sizeof(_mwr_end) - 1);
  _mwr_end[sizeof(_mwr_end) - 1] = 0;
  _mwr_done_at = millis();
  _mwr_cur = -1;
  _mwr_step = 0;
  _mwr_send_at = 0;
  _mon_cur = -1;
  _mon_retry = 0;
  _mon_state = MST_IDLE;
}

// Zet 'set <param> <waarde>' of 'get <param>' op de lucht, naar _mwr_step.
static void monWriteSend(bool after_login) {
  if (_mwr_cur < 0) { monWriteFinish("niets te doen"); return; }
  MonEntry &m = _mon[_mwr_cur];

  char cmd[MON_WSET_PARAM_MAX + MON_WSET_VALUE_MAX + 8];
  /* De parameternaam komt uit CFG_PARAMS en nooit uit het verzoek, en de waarde
   * is altijd het laatste woord -- er is dus geen scheider waarmee een tweede
   * commando kan beginnen. Dezelfde afspraak als bij cfgCli() hiernaast, en om
   * dezelfde reden: dit is de enige plek in dit bestand waar tekst van buiten
   * een CLI van iemand anders bereikt. */
  if (_mwr_step == 0) snprintf(cmd, sizeof(cmd), "set %s %s", _mwr_param, _mwr_asked);
  else                snprintf(cmd, sizeof(cmd), "get %s", _mwr_param);

  if (!_mesh->sendMonitorCliCmd(m.mesh_idx, cmd)) {
    /* Pakketpool vol, wat onder belasting gewoon voorkomt. Bij stap 0 is er
     * dan niets vertrokken en is dat het hele verhaal; bij stap 1 staat er wel
     * een 'set' geschreven waarvan we de uitwerking niet meer nakijken, en dat
     * hoort er anders te staan. */
    monTrace("wset %s NIET VERSTUURD (pool vol)", cmd);
    monWriteFinish(_mwr_step == 0 ? "niets verstuurd (pool vol)"
                                  : "teruglezen niet verstuurd (pool vol)");
    return;
  }
  if (_mwr_step == 0) _mwr_sent = true;   // vanaf hier weten we het niet meer zeker
  _mon_state = MST_WSET_WAIT;
  _mon_deadline = millis() + (after_login ? MON_WSET_FIRST_MS : MON_WSET_STEP_MS);
}

/* Een tekstantwoord van de gemonitorde node. Zelfde indeling als bij
 * monSettingsReply(): vier byte tijdstempel, een byte vlaggen, dan de tekst,
 * uitgevuld tot het cipherblok -- vandaar de kopie. */
static void monWriteReply(const uint8_t *data, int len) {
  if (_mwr_cur < 0) return;

  char text[MON_WSET_REPLY_MAX];
  int n = len - 5;
  if (n > (int)sizeof(text) - 1) n = (int)sizeof(text) - 1;
  if (n < 0) n = 0;
  memcpy(text, data + 5, n);
  text[n] = 0;
  // Een regeleinde in een waarde maakt van het JSON-antwoord verderop rommel.
  for (char *p = text; *p; p++) {
    if (*p == '\r' || *p == '\n' || *p == '\t') *p = ' ';
  }

  if (_mwr_step == 0) {
    strcpy(_mwr_set_reply, text);
    monTrace("wset %s -> %.24s", _mwr_param, text);
    /* En nu teruglezen, na een adempauze. Ook als de 'set' met een foutregel
     * antwoordde: juist dan wil je weten wat er wel staat. Een weigering die
     * de oude waarde intact laat, en een weigering die halverwege iets veranderd
     * heeft, zien er van hieraf identiek uit -- en alleen de tweede is een
     * probleem. */
    _mwr_step = 1;
    _mwr_send_at = millis() + MON_WSET_GAP_MS;
    _mon_state = MST_WSET_WAIT;
    _mon_deadline = 0;              // niets uitstaand; de pauze beslist
    return;
  }

  strcpy(_mwr_get_reply, text);
  monTrace("wset %s = %.24s", _mwr_param, text);
  monWriteFinish("klaar");
}

/* Begint een schrijfactie. Alleen vanuit MST_IDLE, dus er loopt geen pollronde
 * en _mon_cur staat vrij -- dezelfde afspraak waar monSettingsBegin() op leunt. */
static void monWriteBegin() {
  char key[MON_KEY_HEX_MAX];
  strcpy(key, _mwr_req_key);
  _mwr_req_key[0] = 0;                     // opgenomen, wat er hierna ook gebeurt

  int i = monFindByPrefix(key);
  if (!_mesh || i < 0 || !_mon[i].enabled || _mon[i].mesh_idx < 0) {
    // De lijst is tussen het verzoek en dit moment gewijzigd.
    monTrace("wset %.6s vervallen (lijst gewijzigd)", key);
    return;
  }

  _mwr_cur = i;
  _mwr_step = 0;
  _mwr_sent = false;
  _mwr_send_at = 0;
  _mwr_until = millis() + MON_WSET_TOTAL_MS;
  _mwr_started = millis();
  _mwr_seq++;
  _mwr_end[0] = 0;
  _mwr_set_reply[0] = 0;
  _mwr_get_reply[0] = 0;
  strcpy(_mwr_key, _mon[i].key);
  strcpy(_mwr_param, _mwr_req_param);
  strcpy(_mwr_asked, _mwr_req_value);

  MonEntry &m = _mon[_mwr_cur];
  _mon_cur = _mwr_cur;
  _mon_retry = 0;
  int hops = _mesh->getMonitorPathLen(m.mesh_idx);

  /* Een sessie van een eerdere ronde wordt hergebruikt als we er een hebben: een
   * login is een geflood pakket, en er een uitgeven om te leren wat we al weten
   * is de duurste vorm van netheid. Is de overkant ons vergeten, dan blijft het
   * commando onbeantwoord en eindigt dit met 'geen antwoord op set' -- waarna
   * logged_in gewist is en de volgende poging wel met een login begint. */
  if (m.logged_in) {
    monTrace("wset start %.6s %s hops=%d", m.key, hops < 0 ? "FLOOD" : "direct", hops);
    monWriteSend(false);
    return;
  }
  if (_mesh->sendMonitorLogin(m.mesh_idx, m.pass)) {
    monTrace("wset login %.6s %s hops=%d", m.key, hops < 0 ? "FLOOD" : "direct", hops);
    _mon_state = MST_LOGIN_WAIT;
    _mon_deadline = millis() + MON_STEP_MS;
    return;
  }
  monTrace("wset login NIET VERSTUURD (pool vol) %.6s", m.key);
  monWriteFinish("login niet verstuurd");
}

/* ---- the clocks of the repeaters we monitor -----------------------------
 *
 * The other half of 'time <epoch>'. Our own clock was set the moment the
 * message arrived, for free; this is what it costs to pass that on to the
 * repeaters that have no other way to hear it -- the ones which do not publish
 * to MQTT themselves, which is exactly the set this project exists for.
 *
 * Two commands per node, and usually only the first:
 *
 *   clock         -> "HH:MM - D/M/YYYY UTC"          (CommonCLI.cpp)
 *   clock sync    -> "OK - clock set: HH:MM - D/M/YYYY UTC", or an ERR line
 *
 * 'clock sync' takes no argument: the far side sets its clock from the
 * timestamp of the packet that carried the command, which sendMonitorCliCmd()
 * fills from OUR clock -- the one the site just corrected. So the value that
 * propagates is the site's, and there is no number to format wrongly. It is
 * also ten characters against the fifteen of 'time <epoch>', which with five
 * bytes of message header is one 16-byte cipher block instead of two.
 *
 * ---- what this costs ----------------------------------------------------
 *
 * Far less than the settings sweep above, which is why this one is allowed to
 * run on a schedule and that one is not. One command and one reply per
 * monitored node, once a day. A node needing a correction pays a second pair.
 * For comparison: an ordinary poll round is three requests and three replies
 * per node, and runs every fifteen minutes. So a daily clock check is worth
 * about a fifth of one poll round -- somewhere under half a percent of what
 * this node already spends on monitoring, and a rounding error against what it
 * spends relaying other people's packets.
 *
 * The limits that keep it there:
 *
 *  - MON_CLK_MIN_GAP_MS between two runs. The site asks once a day; this caps
 *    what a broker account can do with that to once an hour, which is still 24
 *    times the intended rate and still cheaper than one poll round.
 *  - MON_CLK_SKEW_S before anything is corrected. Below it, one round trip and
 *    we are done.
 *  - No retries anywhere. A node that does not answer 'clock' is skipped until
 *    tomorrow; a clock is not urgent enough to flood for, and tomorrow is one
 *    day of drift away.
 *  - MON_CLK_SILENT_MAX silent nodes in a row ends the run, on the same
 *    reasoning as the settings sweep: after three, the thing that is wrong is
 *    not the fourth node.
 *  - MON_CLK_TOTAL_MS caps the run whatever happens. While it runs the poll
 *    rounds wait, because they share this state machine.
 *
 * ---- why the clock is read before it is set -----------------------------
 *
 * Sending 'clock sync' blind would cost exactly the same as reading 'clock':
 * one command, one reply, every node, every day. So thrift is NOT the argument
 * here, and pretending otherwise would be the kind of reasoning that survives
 * in a comment long after it stopped being true. The argument is that a read
 * produces a measurement -- 'this repeater was four minutes behind' -- where a
 * blind sync produces nothing anybody can see, and that a node which is fine is
 * then never sent a command that changes its clock at all. The second round
 * trip is spent only where the first one proved it was needed.
 *
 * ---- why nothing here ever moves a clock backwards ----------------------
 *
 * The far side refuses it ('clock cannot go backwards'), so a correction to a
 * node running fast would be pure wasted airtime -- but that is the small
 * reason. The large one is in clockApplyOwn(): a node's adverts carry its
 * clock, and a node that already knows the sender drops an advert whose
 * timestamp did not increase. Moving a repeater's clock back by an hour hides
 * it from everyone who knows it for an hour. So a node found running fast is
 * counted and reported, and left exactly as it is.
 */

/* Room for the first command after a login: the packet that depends on a path
 * we have just learned, same allowance the settings sweep gives the equivalent
 * step and for the same measured reason. */
#define MON_CLK_FIRST_MS    20000UL
#define MON_CLK_STEP_MS     12000UL   // per command after that
#define MON_CLK_GAP_MS       3000UL   // between two commands, and between nodes
#define MON_CLK_SILENT_MAX       3    // silent nodes in a row that end a run
#define MON_CLK_TOTAL_MS   300000UL   // hard cap on one run
#define MON_CLK_MIN_GAP_MS 3600000UL  // between two runs

/* Smallest deviation we will act on.
 *
 * Not a taste: it is the resolution of the interface. 'clock' answers to the
 * minute, so a reading of "09:05" means the far side is somewhere in a sixty
 * second window, and the reply reaches us seconds after it read. Everything
 * below is therefore computed as a RANGE -- the drift is known to lie between
 * two bounds -- and a correction goes out only when the whole range sits beyond
 * this threshold. Two minutes is the smallest number for which that can be true
 * at all; anything smaller would mean claiming a precision this interface does
 * not have, and would have this node transmitting daily to chase measurement
 * noise on somebody else's roof. */
#define MON_CLK_SKEW_S          120

static bool _mclk_run = false;        // a run is walking the monitor list
static int  _mclk_next = 0;           // entry to visit next
static int  _mclk_cur = -1;           // entry we are talking to, -1 = none
static uint8_t _mclk_step = 0;        // 0 = asked 'clock', 1 = asked 'clock sync'
static uint8_t _mclk_silent = 0;      // consecutive nodes that said nothing
static uint32_t _mclk_ref = 0;        // our clock when the command went out
static unsigned long _mclk_until = 0; // whole-run budget
static unsigned long _mclk_gap_at = 0;// next node may start
static unsigned long _mclk_done_at = 0;   // last finished run, for the min gap

// This run, and then kept as the last run's summary for 'wifi clock'.
static uint8_t _mclk_asked = 0, _mclk_answered = 0, _mclk_synced = 0, _mclk_ahead = 0;
static long _mclk_worst = 0;          // largest deviation seen, signed seconds
static unsigned long _mclk_last_at = 0;   // when the last run ended, 0 = never

/* Arms a run. Never refuses because of the state machine: a run that cannot
 * start now would be pointless to queue -- the site asks again tomorrow, and a
 * queued clock is a stale clock by definition. Hence a plain refusal with a
 * reason rather than the request slot the settings sweep keeps. */
static bool monClockRequest(const char **why) {
  if (!_mesh)                     { *why = "mesh nog niet gestart"; return false; }
  if (_mon_count == 0)            { *why = "deze node monitort niemand"; return false; }
  if (!clockPlausible(_mesh->getRTCClock()->getCurrentTime())) {
    /* The one refusal that matters most. Passing on a clock we do not believe
     * ourselves would put OUR fault on somebody else's roof, forward only and
     * therefore unrecoverable without a visit. Nothing beats a wrong time here
     * except no time. */
    *why = "onze eigen klok is niet betrouwbaar; niets doorgegeven"; return false;
  }
  if (_batt_known && _batt_pct < _cfg.bat_mon) {
    *why = "batterij te laag om andere repeaters aan te spreken"; return false;
  }
  /* A run whose budget has expired is dead whatever the flag says. monitorLoop()
   * has several early returns -- safe mode, MQTT switched off, an empty monitor
   * list, a flat battery -- and a run abandoned behind one of them never reaches
   * MST_IDLE to end itself. Without this line _mclk_run would stay true and
   * refuse every later request with 'er loopt er al een' until a reboot, which
   * is exactly the kind of fault this node cannot afford: invisible, permanent,
   * and only fixable by climbing onto a roof. */
  if (_mclk_run && passed(_mclk_until)) {
    monTrace("klok: vorige ronde bleef hangen, opgeruimd");
    _mclk_run = false;
    _mclk_cur = -1;
    _mclk_step = 0;
  }
  if (_mclk_run)                  { *why = "er loopt er al een"; return false; }
  if (_mclk_done_at != 0 && millis() - _mclk_done_at < MON_CLK_MIN_GAP_MS) {
    *why = "te kort na de vorige klokronde"; return false;
  }
  _mclk_run = true;
  _mclk_next = 0;
  _mclk_cur = -1;
  _mclk_silent = 0;
  _mclk_asked = _mclk_answered = _mclk_synced = _mclk_ahead = 0;
  _mclk_worst = 0;
  _mclk_until = millis() + MON_CLK_TOTAL_MS;
  _mclk_gap_at = 0;                    // the first node may start immediately
  return true;
}

/* Ends the run and hands the machine back. Straight to MST_IDLE and without
 * touching _mon_next_round, exactly like monSettingsFinish(): the poll rounds
 * resume on the schedule they already had, as though this had not happened. */
static void monClockFinish(const char *why) {
  monTrace("klok klaar: %u gevraagd, %u geantwoord, %u gezet, %u loopt voor (%s)",
           (unsigned)_mclk_asked, (unsigned)_mclk_answered,
           (unsigned)_mclk_synced, (unsigned)_mclk_ahead, why);
  Serial.printf("MeshManagerNet: klokronde klaar: %u gevraagd, %u geantwoord, "
                "%u bijgezet, %u loopt voor (%s)\n",
                (unsigned)_mclk_asked, (unsigned)_mclk_answered,
                (unsigned)_mclk_synced, (unsigned)_mclk_ahead, why);
  _mclk_run = false;
  _mclk_cur = -1;
  _mclk_step = 0;
  _mclk_done_at = millis();
  _mclk_last_at = millis();
  _mon_cur = -1;
  _mon_retry = 0;
  _mon_state = MST_IDLE;
}

// Done with this node, whatever the outcome. The next one waits out a gap.
static void monClockNodeDone() {
  _mclk_cur = -1;
  _mclk_step = 0;
  _mon_cur = -1;
  _mon_retry = 0;
  _mclk_gap_at = millis() + MON_CLK_GAP_MS;
  _mon_state = MST_IDLE;
}

// Puts 'clock' or 'clock sync' on the air, depending on _mclk_step.
static void monClockSend(bool after_login) {
  if (_mclk_cur < 0) { monClockNodeDone(); return; }
  MonEntry &m = _mon[_mclk_cur];
  const char *cmd = (_mclk_step == 0) ? "clock" : "clock sync";

  /* Read our own clock at the moment the command LEAVES, not when the answer
   * arrives. It is one end of the interval the far side's reading has to be
   * compared against; the other end is read when the reply comes in. */
  _mclk_ref = _mesh->getRTCClock()->getCurrentTime();

  if (!_mesh->sendMonitorCliCmd(m.mesh_idx, cmd)) {
    // Packet pool empty, which is normal under load. Not a silence from them.
    monTrace("klok %s NIET VERSTUURD (pool vol) %.6s", cmd, m.key);
    monClockNodeDone();
    return;
  }
  if (_mclk_step == 0) _mclk_asked++;
  _mon_state = MST_CLK_WAIT;
  _mon_deadline = millis() + (after_login ? MON_CLK_FIRST_MS : MON_CLK_STEP_MS);
}

/* One text answer from a monitored node.
 *
 * Same layout as monSettingsReply(): four bytes of timestamp, one of flags,
 * then the text, zero-padded out to the cipher block -- hence the copy. */
static void monClockReply(const uint8_t *data, int len) {
  if (_mclk_cur < 0) return;
  MonEntry &m = _mon[_mclk_cur];

  char text[96];
  int n = len - 5;
  if (n > (int)sizeof(text) - 1) n = (int)sizeof(text) - 1;
  if (n < 0) n = 0;
  memcpy(text, data + 5, n);
  text[n] = 0;

  if (_mclk_step == 1) {
    /* The answer to 'clock sync'. Both outcomes are useful and neither is a
     * surprise: OK means we corrected it, ERR means it decided it was ahead of
     * us after all -- which can happen honestly, because between our reading
     * and this command a minute of rounding stands. */
    bool ok = (strstr(text, "OK") != NULL);
    if (ok) _mclk_synced++; else _mclk_ahead++;
    monTrace("klok %.6s zetten: %.24s", m.key, text);
    Serial.printf("MeshManagerNet: klok %.12s %s (%.40s)\n",
                  m.key, ok ? "bijgezet" : "NIET gezet", text);
    monClockNodeDone();
    return;
  }

  _mclk_answered++;
  _mclk_silent = 0;

  /* "HH:MM - D/M/YYYY UTC". Day and month are printed with %d and not %02d on
   * the far side, so they may be one digit or two -- which is why this is a
   * scan and not a fixed-offset parse. */
  int hh = -1, mm = -1, dd = 0, mo = 0, yy = 0;
  if (sscanf(text, "%d:%d - %d/%d/%d", &hh, &mm, &dd, &mo, &yy) != 5 ||
      hh < 0 || hh > 23 || mm < 0 || mm > 59 ||
      dd < 1 || dd > 31 || mo < 1 || mo > 12 || yy < 1970 || yy > 2100) {
    monTrace("klok %.6s onleesbaar: %.24s", m.key, text);
    monClockNodeDone();
    return;
  }

  uint32_t theirs = civilToEpoch(yy, mo, dd, hh, mm, 0);
  uint32_t now = _mesh->getRTCClock()->getCurrentTime();

  /* Interval arithmetic, because that is what the data supports. Their clock
   * read somewhere in [theirs, theirs+59] -- the seconds were never sent -- at
   * some instant in [_mclk_ref, now], the window between our command leaving
   * and their answer arriving. So their deviation from us is bounded by:
   *
   *   lo = theirs      - now          (most flattering to us)
   *   hi = theirs + 59 - _mclk_ref    (most flattering to them)
   *
   * Negative is behind. A correction goes out only when the whole interval is
   * past the threshold, so a node that MIGHT be fine is always left alone. */
  long lo = (long)theirs - (long)now;
  long hi = (long)theirs + 59 - (long)_mclk_ref;
  long est = (lo + hi) / 2;
  // Largest deviation of the run, sign kept: 'four minutes behind' and 'four
  // minutes ahead' are the same size and completely different problems.
  long mag = est < 0 ? -est : est;
  long worst = _mclk_worst < 0 ? -_mclk_worst : _mclk_worst;
  if (mag > worst) _mclk_worst = est;
  monTrace("klok %.6s %+lds (%ld..%ld)", m.key, est, lo, hi);

  if (hi <= -(long)MON_CLK_SKEW_S) {
    Serial.printf("MeshManagerNet: klok %.12s loopt %ld s achter, bijzetten\n", m.key, -est);
    _mclk_step = 1;
    monClockSend(false);
    return;
  }
  if (lo >= (long)MON_CLK_SKEW_S) {
    /* Nothing to send: 'clock sync' would be refused, and even if it were not,
     * see the block comment above for why a clock is never walked back. This is
     * reported and left alone -- an operator with a serial cable can decide
     * whether a reboot is worth it, and this node cannot. */
    _mclk_ahead++;
    Serial.printf("MeshManagerNet: klok %.12s loopt %ld s VOOR; niet corrigeerbaar "
                  "over de lucht, alleen 'clkreboot' op die node helpt\n", m.key, est);
    monClockNodeDone();
    return;
  }
  monClockNodeDone();                 // within the threshold; one round trip, done
}

/* Picks the next monitored node worth asking, or ends the run. Called from
 * MST_IDLE only, so no poll round is in progress and _mon_cur is free -- the
 * same contract monSettingsBegin() relies on. */
static void monClockAdvance() {
  if (passed(_mclk_until)) { monClockFinish("tijdsbudget op"); return; }
  if (_mclk_silent >= MON_CLK_SILENT_MAX) { monClockFinish("te veel stilte op rij"); return; }

  while (_mclk_next < _mon_count) {
    int i = _mclk_next++;
    if (!_mon[i].enabled || _mon[i].mesh_idx < 0) continue;

    _mclk_cur = i;
    _mclk_step = 0;
    _mon_cur = i;
    _mon_retry = 0;
    MonEntry &m = _mon[i];

    /* Reuse a session from an earlier poll when we have one, for the reason
     * monSettingsBegin() gives: a login is a flooded packet, and spending one
     * to learn what we already know is the most expensive kind of tidiness. */
    if (m.logged_in) { monClockSend(false); return; }
    if (_mesh->sendMonitorLogin(m.mesh_idx, m.pass)) {
      monTrace("klok login %.6s", m.key);
      _mon_state = MST_LOGIN_WAIT;
      _mon_deadline = millis() + MON_STEP_MS;
      return;
    }
    monTrace("klok login NIET VERSTUURD (pool vol) %.6s", m.key);
    monClockNodeDone();
    return;
  }
  monClockFinish("alle nodes gehad");
}

/* Config changes arrive on the web server's task; the list is only ever
 * mutated in loop(). Same hand-over the wifi/mqtt/power forms already use. */
enum MonAction { MA_NONE = 0, MA_ADD, MA_DEL, MA_PASS, MA_ENABLE, MA_INTERVAL, MA_POLL };
static volatile uint8_t _mon_action = MA_NONE;
static char _ma_key[MON_KEY_HEX_MAX];
static char _ma_name[MON_NAME_MAX];
static char _ma_pass[MON_PASS_MAX];
static uint16_t _ma_num = 0;

static bool monAdd(const char *key_in, const char *name) {
  char key[MON_KEY_HEX_MAX];
  strncpy(key, key_in, sizeof(key) - 1);
  key[sizeof(key) - 1] = 0;
  if (!normaliseKey(key)) return false;

  int i = findMonitor(key);
  if (i >= 0) {
    // Already known under a shorter or longer form: merge, never duplicate.
    if (strlen(key) > strlen(_mon[i].key)) strcpy(_mon[i].key, key);
    if (name && *name && _mon[i].name[0] == 0) {
      strncpy(_mon[i].name, name, sizeof(_mon[i].name) - 1);
      _mon[i].name[sizeof(_mon[i].name) - 1] = 0;
    }
    return true;
  }
  if (_mon_count >= MAX_MONITORS) return false;

  MonEntry &m = _mon[_mon_count];
  memset(&m, 0, sizeof(m));
  m.mesh_idx = -1;
  m.enabled = true;
  strcpy(m.key, key);
  if (name) {
    strncpy(m.name, name, sizeof(m.name) - 1);
    m.name[sizeof(m.name) - 1] = 0;
  }
  _mon_count++;
  return true;
}

static bool monDelete(const char *key) {
  int i = findMonitor(key);
  if (i < 0) return false;
  for (int j = i; j < _mon_count - 1; j++) _mon[j] = _mon[j + 1];
  _mon_count--;
  memset(&_mon[_mon_count], 0, sizeof(MonEntry));
  return true;
}

// Runs in loop(): applies whatever the page or CLI staged, then persists.
static void applyMonAction() {
  uint8_t act = _mon_action;
  _mon_action = MA_NONE;
  if (act == MA_NONE) return;

  bool touched_list = false;
  switch (act) {
    case MA_ADD:
      touched_list = monAdd(_ma_key, _ma_name);
      break;
    case MA_DEL:
      touched_list = monDelete(_ma_key);
      break;
    case MA_PASS: {
      int i = findMonitor(_ma_key);
      if (i >= 0) {
        strncpy(_mon[i].pass, _ma_pass, sizeof(_mon[i].pass) - 1);
        _mon[i].pass[sizeof(_mon[i].pass) - 1] = 0;
        _mon[i].logged_in = false;        // credentials changed: log in again
        _mon[i].login_res = LOGIN_NONE;
      }
      break;
    }
    case MA_ENABLE: {
      int i = findMonitor(_ma_key);
      if (i >= 0) _mon[i].enabled = (_ma_num != 0);
      break;
    }
    case MA_INTERVAL:
      _mon_interval = _ma_num;
      break;
    case MA_POLL:
      _mon_next_round = millis();         // start a round on the next pass
      return;                             // nothing to persist
  }

  saveMonitors();
  if (touched_list) syncMonitorsToMesh();
}

/* Fires the request belonging to a step and waits for it. A send that fails
 * (packet pool empty) skips that step rather than stalling the sequence -- the
 * round still ends in a publish. */
static void monStep(MonState next) {
  if (_mon_cur < 0) return;
  MonEntry &m = _mon[_mon_cur];
  _mon_retry = 0;

  while (true) {
    bool sent = false;
    if (next == MST_REQ_WAIT)        sent = _mesh->sendMonitorStatusReq(m.mesh_idx);
    else if (next == MST_TELEM_WAIT) sent = _mesh->sendMonitorTelemetryReq(m.mesh_idx);
    else if (next == MST_NBR_WAIT)   sent = _mesh->sendMonitorNeighboursReq(m.mesh_idx);

    if (sent) {
      _mon_state = next;
      _mon_deadline = millis() + MON_STEP_MS;
      return;
    }
    monTrace("step %d NOT SENT (pool full)", (int)next);

    if (next == MST_REQ_WAIT)        next = MST_TELEM_WAIT;
    else if (next == MST_TELEM_WAIT) next = MST_NBR_WAIT;
    else break;
  }

  if (!publishMonitorRound(m)) monRoundFailed(m);
  _mon_state = MST_GAP;
  _mon_deadline = millis() + MON_GAP_MS;
}

// Moves to the next entry worth polling, or ends the round.
static void monitorAdvance() {
  for (int i = _mon_cur + 1; i < _mon_count; i++) {
    if (!_mon[i].enabled || _mon[i].mesh_idx < 0) continue;
    if (_mon[i].fails >= MON_BACKOFF_AFTER && (_mon_round % MON_BACKOFF_EVERY) != 0) {
      continue;                        // resting; see MON_BACKOFF_AFTER
    }

    _mon_cur = i;
    _mon_retry = 0;
    _mon[i].polls++;
    monResetResults();
    int hops = _mesh->getMonitorPathLen(_mon[i].mesh_idx);

    if (_mon[i].logged_in) {
      monTrace("req %s hops=%d %.6s", hops < 0 ? "FLOOD" : "direct", hops, _mon[i].key);
      monStep(MST_REQ_WAIT);
      if (_mon_state != MST_GAP) return;
    } else if (_mesh->sendMonitorLogin(_mon[i].mesh_idx, _mon[i].pass)) {
      monTrace("login sent %s hops=%d %.6s", hops < 0 ? "FLOOD" : "direct", hops, _mon[i].key);
      _mon_state = MST_LOGIN_WAIT;
      _mon_deadline = millis() + MON_STEP_MS;
      return;
    } else {
      monTrace("login NOT SENT (pool full) %.6s", _mon[i].key);
    }
    // packet pool empty: leave this one for the next round rather than spin
  }

  _mon_cur = -1;
  _mon_state = MST_IDLE;
  _mon_round++;
  _mon_next_round = millis() + (unsigned long)_mon_interval * 1000UL;
}

static void monitorLoop() {
  if (_safe_mode || _mon_count == 0 || !_mesh) return;
  /* No broker means nowhere to put the answers, and every poll costs the whole
   * mesh airtime. Same for a tired battery: monitoring is a luxury, staying
   * reachable is not. */
  /* Een schrijfactie is de uitzondering, en dat is geen gemak maar de kern van
   * waar ze voor bestaat. Zij rapporteert over HTTP aan wie haar vroeg en heeft
   * de broker dus nergens voor nodig, terwijl een pollronde zonder broker
   * zendtijd uitgeeft aan cijfers die nergens heen kunnen. Een installatie
   * zonder internet, of met een broker die er even niet is, hoort een
   * radio-instelling nog te kunnen rechtzetten -- dat is precies het soort
   * moment waarop iemand het nodig heeft. */
  if (!_cfg.mqtt_enabled || _cfg.mqtt_host[0] == 0) {
    if (!_mwr_req_key[0] && _mwr_cur < 0) return;
  }
  if (_batt_known && _batt_pct < _cfg.bat_mon) {
    /* A clock check that was running when the battery gave out has to be let
     * go here rather than left standing. These three early returns are the only
     * way out of this function, so a run abandoned mid-list would never reach
     * MST_IDLE again -- and _mclk_run being stuck true refuses every later
     * request with 'er loopt er al een', for good, on a node nobody can reach
     * to reboot. Sunrise brings the next one; the site asks daily anyway. */
    if (_mclk_run) monClockFinish("batterij te laag");
    // Dezelfde reden, en hier weegt hij zwaarder: een schrijfactie die blijft
    // staan, weigert elke volgende met 'er loopt er al een' tot een herstart.
    if (_mwr_cur >= 0) monWriteFinish("batterij te laag");
    return;
  }

  if (_mon_got_reply) {
    int idx = _mon_reply_idx;
    uint8_t rtype = _mon_reply_type;
    _mon_got_reply = false;

    /* A text message is an answer to a CLI command, and there is exactly one
     * moment at which we have asked for one. Checked before everything else so
     * the poll states below can go on assuming a RESPONSE, which is what they
     * were written against.
     *
     * _mset_send_at == 0 is part of the condition, not a detail: while it is
     * armed we are waiting out the pause BEFORE the next command, so nothing is
     * outstanding, and _mset_next already names the parameter we have not asked
     * about yet. A late duplicate of the previous answer arriving in that window
     * would otherwise be filed under the next parameter's name -- a wrong value
     * on a settings page, which is worse than a missing one. Nothing in the
     * protocol lets us match an answer to the command that caused it, so this
     * window is what we can close, and it is the one that matters. */
    if (rtype == PAYLOAD_TYPE_TXT_MSG && _mon_state == MST_CLI_WAIT &&
        _mset_cur >= 0 && _mon[_mset_cur].mesh_idx == idx) {
      if (_mset_send_at != 0) {
        monTrace("set laat antwoord in de pauze, genegeerd");
        return;
      }
      monSettingsReply(_mon_reply, _mon_reply_len);
      return;
    }
    /* Het antwoord op een schrijfactie, met dezelfde bewaking als hierboven en
     * om een reden die hier nog een maat erger is. Tussen de 'set' en de 'get'
     * ligt een pauze, en een laat duplicaat van het set-antwoord dat daarin
     * binnenvalt zou als teruggelezen WAARDE geboekt worden. Dan zou er "OK" in
     * de kolom staan waar de instelling hoort, en zou de hele terugleescontrole
     * -- de reden dat dit blok bestaat -- precies het tegenovergestelde doen van
     * wat ze belooft. Zolang _mwr_send_at staat, staat er niets uit. */
    if (rtype == PAYLOAD_TYPE_TXT_MSG && _mon_state == MST_WSET_WAIT &&
        _mwr_cur >= 0 && _mon[_mwr_cur].mesh_idx == idx) {
      if (_mwr_send_at != 0) {
        monTrace("wset laat antwoord in de pauze, genegeerd");
        return;
      }
      monWriteReply(_mon_reply, _mon_reply_len);
      return;
    }
    /* The clock check's own answer. It needs no equivalent of the
     * _mset_send_at guard above, because there is no pause between two
     * commands here that a late duplicate could be misfiled into: 'clock sync'
     * goes out from inside the handler for the 'clock' answer, so the only
     * moment this node is in MST_CLK_WAIT with nothing outstanding does not
     * exist. And the two commands have answers that cannot be confused -- one
     * is a bare time, the other starts with OK or ERR. */
    if (rtype == PAYLOAD_TYPE_TXT_MSG && _mon_state == MST_CLK_WAIT &&
        _mclk_cur >= 0 && _mon[_mclk_cur].mesh_idx == idx) {
      monClockReply(_mon_reply, _mon_reply_len);
      return;
    }
    // Every state below expects a RESPONSE; a stray CLI answer is not one.
    if (rtype != PAYLOAD_TYPE_RESPONSE) {
      monTrace("reply type %u genegeerd", (unsigned)rtype);
    } else if (_mon_cur >= 0 && _mon[_mon_cur].mesh_idx == idx) {
      MonEntry &m = _mon[_mon_cur];
      if (_mon_state == MST_LOGIN_WAIT) {
        m.login_res = LOGIN_OK;
        m.logged_in = true;
        int hops = _mesh->getMonitorPathLen(m.mesh_idx);
        monTrace("login OK len=%d, req %s hops=%d", _mon_reply_len,
                 hops < 0 ? "FLOOD" : "direct", hops);
        // Een login die door een schrijfactie gezet is, gaat daarin verder.
        // Vóór de andere twee omdat er hoogstens één van de drie gewapend is en
        // deze de kortste is: iemand staat ervoor te wachten.
        if (_mwr_cur >= 0) { monWriteSend(true); return; }
        // A login staged by a settings sweep continues into that, not a poll.
        if (_mset_cur >= 0) { monSettingsSend(); return; }
        // ... and one staged by the clock check into that. Only ever one of the
        // two is armed: both are started from MST_IDLE, which the other holds.
        if (_mclk_cur >= 0) { monClockSend(true); return; }
        monStep(MST_REQ_WAIT);
        return;
      }
      /* Each answer moves to the next request type. A failure at any step only
       * costs that step: the round still ends in a publish with whatever came
       * back, because half a reading is worth more than none. */
      if (_mon_state == MST_REQ_WAIT) {
        m.oks++;                      // status read succeeded
        m.ok_st++;
        int n = _mon_reply_len - 4;
        if (n >= 20) {
          if (n > (int)sizeof(_mon_st)) n = sizeof(_mon_st);
          memcpy(&_mon_st, &_mon_reply[4], n);   // older firmware sends fewer fields
          _mon_st_len = n;                       // ... which is why we remember how many
          _mon_have_st = true;
        }
        monTrace("status len=%d ok", _mon_reply_len);
        monStep(MST_TELEM_WAIT);
        return;
      }
      if (_mon_state == MST_TELEM_WAIT) {
        monDecodeTelemetry(_mon_reply, _mon_reply_len);
        if (_mon_tl_n > 0) m.ok_tl++;
        monTrace("telem len=%d, %d values", _mon_reply_len, _mon_tl_n);
        monStep(MST_NBR_WAIT);
        return;
      }
      if (_mon_state == MST_NBR_WAIT) {
        _mon_nbr_len = (_mon_reply_len > (int)sizeof(_mon_nbr))
                       ? (int)sizeof(_mon_nbr) : _mon_reply_len;
        memcpy(_mon_nbr, _mon_reply, _mon_nbr_len);
        _mon_have_nbr = (_mon_nbr_len >= 8);
        if (_mon_have_nbr) m.ok_nb++;
        monTrace("nbrs len=%d", _mon_reply_len);
        publishMonitorRound(m);
        _mon_state = MST_GAP;
        _mon_deadline = millis() + MON_GAP_MS;
        return;
      }
      monTrace("reply len=%d but state=%d, dropped", _mon_reply_len, (int)_mon_state);
    } else {
      monTrace("reply for idx=%d, current=%d, dropped", idx,
               _mon_cur >= 0 ? _mon[_mon_cur].mesh_idx : -1);
    }
  }

  switch (_mon_state) {
    case MST_IDLE:
      /* Een schrijfactie gaat voor alles. Ze is de kortste van de drie (twee
       * commando's tegen twintig), ze is uitdrukkelijk gevraagd door iemand die
       * ervoor een bevestiging heeft moeten typen, en ze is de enige waarbij
       * wachten iets verandert aan wat er op het apparaat staat. */
      if (_mwr_req_key[0]) {
        if (passed(_mwr_req_until)) { _mwr_req_key[0] = 0; break; }
        monWriteBegin();
        break;
      }

      /* A requested settings sweep goes before the scheduled poll round, and
       * that is the point of it: somebody is sitting in front of a page that
       * says "reload this in a minute". A round that has already started is
       * left to finish -- it is bounded, and interrupting a peer halfway would
       * cost the airtime already spent on it for nothing. */
      if (_mset_req_key[0]) { monSettingsBegin(); break; }

      /* A running clock check comes next, and holds the machine here between
       * two nodes rather than taking a state of its own -- which is exactly
       * what MST_GAP does for a poll round, for the same three seconds. It goes
       * after the settings sweep because a sweep is somebody waiting in front
       * of a page, and this is a schedule that can wait five minutes. */
      if (_mclk_run) {
        if (!passed(_mclk_gap_at) && _mclk_gap_at != 0) return;
        _mclk_gap_at = 0;
        monClockAdvance();
        break;
      }

      /* passed() reads 0 as 'not scheduled', and this starts at 0 -- so until
       * something set it, the first automatic round never came. Only 'wifi mon
       * poll' did, and because the end of a round then sets a real deadline,
       * everything looked healthy from that moment on. Which is exactly why it
       * survived: every test began with a manual poll, and a reboot silently
       * disarmed it again. Same arming pattern as the settings sweep. */
      if (_mon_next_round == 0) {
        _mon_next_round = millis() + MON_FIRST_MS;
        monTrace("eerste ronde over %us", (unsigned)(MON_FIRST_MS / 1000));
      }
      if (!passed(_mon_next_round)) return;
      if (resolveMonitors()) {           // a prefix may have become a full key
        saveMonitors();
        syncMonitorsToMesh();
      }
      _mon_cur = -1;
      monTrace("round start, %d entries", _mon_count);
      monitorAdvance();
      break;

    case MST_LOGIN_WAIT:
      if (!passed(_mon_deadline)) return;
      if (_mon_cur < 0) { _mon_state = MST_GAP; _mon_deadline = millis() + MON_GAP_MS; break; }

      /* Silence. Either they refused us or they are out of reach, and the
       * protocol gives no way to tell those apart. If the attempt went direct,
       * one stale hop would explain it, so retry once by flood before drawing
       * any conclusion -- the trace then says which of the two worked. */
      if (_mon_retry == 0 && _mesh->getMonitorPathLen(_mon[_mon_cur].mesh_idx) >= 0) {
        _mesh->resetMonitorPath(_mon[_mon_cur].mesh_idx);
        if (_mesh->sendMonitorLogin(_mon[_mon_cur].mesh_idx, _mon[_mon_cur].pass)) {
          monTrace("login timeout, retry FLOOD");
          _mon_retry = 1;
          _mon_deadline = millis() + MON_STEP_MS;
          return;
        }
      }
      monTrace("login timeout, giving up (retry=%u)", _mon_retry);
      _mon[_mon_cur].login_res = LOGIN_NOANSWER;
      /* Een schrijfactie die niet eens binnenkwam is de gunstigste manier
       * waarop deze weg kan falen: er is niets verstuurd, dus er staat met
       * zekerheid niets veranderd. Dat is een heel ander bericht dan een 'set'
       * die wel vertrok en waarvan het antwoord uitbleef, en de twee horen dus
       * uit elkaar gehouden te worden. */
      if (_mwr_cur >= 0) { monWriteFinish("login onbeantwoord"); break; }
      /* A settings sweep that never got in has nothing to say and does not
       * touch the backoff either: the backoff exists so dead entries stop
       * costing every poll round, and this was not a poll round. */
      if (_mset_cur >= 0) { monSettingsFinish("login onbeantwoord"); break; }
      /* Same for the clock check, and for the same reason: this was not a poll
       * round, so it does not feed the backoff either. One silent node, on to
       * the next; three in a row and the run ends in monClockAdvance(). */
      if (_mclk_cur >= 0) { _mclk_silent++; monClockNodeDone(); break; }
      monRoundFailed(_mon[_mon_cur]);
      _mon_state = MST_GAP;
      _mon_deadline = millis() + MON_GAP_MS;
      break;

    case MST_REQ_WAIT:
      if (!passed(_mon_deadline)) return;
      if (_mon_cur < 0) { _mon_state = MST_GAP; _mon_deadline = millis() + MON_GAP_MS; break; }

      /* The login just worked, so they are reachable and they know us. A status
       * request that then goes unanswered points at the route rather than the
       * session: the login was flooded and answered over a path we learned from
       * it, and that path is the one thing the status request relies on and the
       * login did not. So drop it and ask once more by flood, in this same
       * round, before writing the round off. */
      if (_mon_retry == 0) {
        _mesh->resetMonitorPath(_mon[_mon_cur].mesh_idx);
        if (_mesh->sendMonitorStatusReq(_mon[_mon_cur].mesh_idx)) {
          monTrace("req timeout, retry FLOOD");
          _mon_retry = 1;
          _mon_deadline = millis() + MON_STEP_MS;
          return;
        }
        monTrace("req timeout, retry NOT SENT (pool full)");
      } else {
        monTrace("req timeout after flood retry, giving up");
      }
      // Log in again next round; the session may be what went stale.
      _mon[_mon_cur].logged_in = false;
      monRoundFailed(_mon[_mon_cur]);
      _mon_state = MST_GAP;
      _mon_deadline = millis() + MON_GAP_MS;
      break;

    /* Telemetry and neighbours get no flood retry. They are extras on top of a
     * status reading we may already hold, and spending another round trip (and
     * everyone else's airtime) on a nice-to-have is the wrong trade for a node
     * whose day job is relaying other people's traffic. */
    case MST_TELEM_WAIT:
      if (!passed(_mon_deadline)) return;
      if (_mon_cur < 0) { _mon_state = MST_GAP; _mon_deadline = millis() + MON_GAP_MS; break; }
      monTrace("telem timeout, skipping");
      monStep(MST_NBR_WAIT);
      break;

    case MST_NBR_WAIT:
      if (!passed(_mon_deadline)) return;
      if (_mon_cur < 0) { _mon_state = MST_GAP; _mon_deadline = millis() + MON_GAP_MS; break; }
      monTrace("nbrs timeout, publishing what we have");
      if (!publishMonitorRound(_mon[_mon_cur])) monRoundFailed(_mon[_mon_cur]);
      _mon_state = MST_GAP;
      _mon_deadline = millis() + MON_GAP_MS;
      break;

    /* Two waits in one state, because they are the same wait seen from both
     * ends: _mset_send_at is the pause before the next command goes out, and
     * _mon_deadline is how long we listen after it did. Exactly one of the two
     * is armed at any moment. A third state would have added a transition and
     * nothing else. */
    case MST_CLI_WAIT:
      if (_mset_cur < 0) { _mon_state = MST_IDLE; break; }   // cannot happen; costs nothing

      /* The backstop. Every path below arms exactly one of the two deadlines, so
       * reaching MON_SET_TOTAL_MS this way should be impossible -- which is
       * exactly why the check is here rather than only in monSettingsAdvance().
       * This state machine holds up the poll rounds while it runs, on a node
       * nobody can walk over to and reboot, and 'cannot happen' is not a
       * guarantee worth a roof. */
      if (passed(_mset_until)) { monSettingsFinish("tijdsbudget op"); break; }

      if (_mset_send_at != 0) {
        if (!passed(_mset_send_at)) return;
        _mset_send_at = 0;
        monSettingsSend();
        return;
      }
      if (!passed(_mon_deadline)) return;

      _mset_miss++;
      _mset_silent++;
      monTrace("set %s stil (%u op rij)", SET_PARAMS[_mset_next].name, (unsigned)_mset_silent);
      if (_mset_silent >= MON_SET_SILENT_MAX) {
        /* Give up on the session as well as on the sweep. Either they forgot us
         * or they never let us run commands in the first place; both are worth
         * one fresh login next time rather than eighteen more silences now. */
        _mon[_mset_cur].logged_in = false;
        monSettingsFinish("te veel stilte op rij");
        break;
      }
      monSettingsAdvance();
      break;

    /* One command outstanding, always. Unlike MST_CLI_WAIT there is no second
     * timer here: the pause between two nodes is waited out in MST_IDLE, and
     * the second command of a node goes out from inside the first one's reply. */
    case MST_CLK_WAIT:
      if (_mclk_cur < 0) { _mon_state = MST_IDLE; break; }   // cannot happen
      /* The backstop, for the same reason the settings sweep has one: this
       * machine holds up the poll rounds while it runs, on a node nobody can
       * walk over to and reboot. */
      if (passed(_mclk_until)) { monClockFinish("tijdsbudget op"); break; }
      if (!passed(_mon_deadline)) return;

      /* Silence. No retry and no flood: yesterday's clock is not worth a second
       * transmission today, and the site will ask again tomorrow. Only the
       * unanswered READ counts as a silent node -- an unanswered 'clock sync'
       * means they did answer the read, so the link is fine and the run should
       * keep going. */
      if (_mclk_step == 0) {
        _mclk_silent++;
        monTrace("klok %.6s stil (%u op rij)", _mon[_mclk_cur].key, (unsigned)_mclk_silent);
        /* Same conclusion the settings sweep draws from repeated silence: either
         * they forgot us or they never let us run commands at all. Both are
         * worth one fresh login next time rather than more silence now. */
        if (_mclk_silent >= MON_CLK_SILENT_MAX) _mon[_mclk_cur].logged_in = false;
      } else {
        monTrace("klok %.6s antwoordde niet op zetten", _mon[_mclk_cur].key);
      }
      monClockNodeDone();
      break;

    /* Twee wachttijden in één toestand, net als MST_CLI_WAIT en om dezelfde
     * reden: _mwr_send_at is de pauze vóór het teruglezen, _mon_deadline is hoe
     * lang we luisteren nadat er iets vertrok. Precies één van de twee staat
     * gewapend. */
    case MST_WSET_WAIT:
      if (_mwr_cur < 0) { _mon_state = MST_IDLE; break; }   // kan niet; kost niets

      /* De achtervang. Elke weg hierboven wapent precies één van de twee
       * deadlines, dus hier komen zou onmogelijk moeten zijn -- en juist daarom
       * staat hij er. Deze machine houdt de pollrondes tegen zolang ze loopt, op
       * een node waar niemand naartoe kan lopen om hem te herstarten. */
      if (passed(_mwr_until)) { monWriteFinish("tijdsbudget op"); break; }

      if (_mwr_send_at != 0) {
        if (!passed(_mwr_send_at)) return;
        _mwr_send_at = 0;
        monWriteSend(false);
        return;
      }
      if (!passed(_mon_deadline)) return;

      /* Stilte, en welke van de twee het is doet er hier meer toe dan waar ook
       * in dit bestand.
       *
       * Op stap 0 zwijgt het antwoord op de 'set'. Dat is GEEN mislukking: het
       * commando is de lucht in gegaan en of het is aangekomen weten we niet.
       * Het staat er dus zo, en niet anders. Op stap 1 weten we wat de 'set'
       * antwoordde maar niet wat er nu werkelijk staat -- en dat laatste is de
       * hele reden dat er teruggelezen wordt, dus ook dat is een eigen einde.
       *
       * Geen herhaling en geen flood. Een tweede 'set' zou het commando voor de
       * tweede keer uitvoeren op een node die het misschien al aannam, en dat is
       * bij 'advert.interval' onschuldig en bij niets anders. */
      _mon[_mwr_cur].logged_in = false;
      monWriteFinish(_mwr_step == 0 ? "geen antwoord op set"
                                    : "geen antwoord op teruglezen");
      break;

    case MST_GAP:
      if (!passed(_mon_deadline)) return;
      monitorAdvance();
      break;
  }
}

// ---------------------------------------------------------------- admin page

/* One static PROGMEM string, sent in a single write. The earlier version built
 * the HTML in pieces with live values baked in; every piece is a separate
 * blocking write, and with the latency spikes of ESP32 wifi (modem-sleep) the
 * main loop stalled inside them -- taking the mesh down with it. So: one send,
 * and the page fetches its data as JSON afterwards.
 *
 * Styling follows the public MeshManager site (same tokens, cards, green section
 * heads) so the two stay one visual family. Theme and language live entirely
 * in the browser: colours are CSS variables swapped by data-theme, and every
 * label carries a data-i18n key that JavaScript fills from one of two small
 * dictionaries. Both choices are remembered in localStorage.
 *
 * That is also why /api/status returns codes instead of finished sentences:
 * the firmware should not have an opinion about the reader's language. */
static const char PAGE[] PROGMEM =
  "<!doctype html><html><head><meta charset=utf-8>"
  "<meta name=viewport content='width=device-width,initial-scale=1'>"
  "<title>MeshCore repeater</title><style>"
  ":root{--bg:#0b0f14;--grid:#10161e;--card:#121a23;--edge:#1e2b3a;--text:#d7e2ea;"
  "--muted:#7d8fa0;--accent:#35e08c;--dim:#1d7a4f;--cyan:#4cc9f0;--amber:#ffb454;"
  "--bar:rgba(11,15,20,.82);--line:rgba(255,255,255,.014);"
  "--mono:ui-monospace,'Cascadia Code',Consolas,monospace;"
  "--sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}"
  ":root[data-theme=light]{--bg:#eef3f1;--grid:#e2eae6;--card:#fff;--edge:#d2ddd7;"
  "--text:#16241d;--muted:#5b6b63;--accent:#0e9c60;--dim:#0b7a4b;--cyan:#0b7fa8;"
  "--amber:#b8741a;--bar:rgba(255,255,255,.86);--line:rgba(10,40,25,.04)}"
  "*{box-sizing:border-box}html{background:var(--bg)}"
  "body{margin:0;color:var(--text);font:15px/1.5 var(--sans);min-height:100vh;"
  "background:radial-gradient(ellipse 80% 50% at 50% -10%,rgba(53,224,140,.07),transparent),"
  "repeating-linear-gradient(0deg,transparent 0 23px,var(--line) 23px 24px),"
  "repeating-linear-gradient(90deg,transparent 0 23px,var(--line) 23px 24px),var(--bg);"
  "background-attachment:fixed}"
  ".topbar{display:flex;justify-content:space-between;align-items:center;gap:.6rem;"
  "padding:.7rem 1.2rem;background:var(--bar);backdrop-filter:blur(8px);"
  "border-bottom:1px solid var(--edge);position:sticky;top:0;z-index:10}"
  ".brand{font-family:var(--mono);font-weight:700;color:var(--accent);letter-spacing:.03em;"
  "text-shadow:0 0 12px rgba(53,224,140,.35)}"
  "main{max-width:680px;margin:0 auto;padding:.5rem 1.2rem 3rem}"
  "h1{font-size:1.5rem;margin:1.2rem 0 .2rem;letter-spacing:-.01em}"
  /* De secties zijn <details>, en dat is een keuze tegen JavaScript in. Open en
   * dicht klappen, het toetsenbord, de schermlezer en 'zoeken op deze pagina'
   * doet de browser zelf; tabbladen zouden dezelfde functie zijn voor een
   * omschakelaar in JS, een toestand in CSS en aria-attributen erbij. Op een
   * pagina die in het flashgeheugen van de node staat is dat de duurste vorm van
   * hetzelfde. Alleen het ONTHOUDEN kost hier JavaScript, en dat is vier regels. */
  "details.sec>summary{font-family:var(--mono);font-size:.82rem;margin:2rem 0 .7rem;"
  "text-transform:uppercase;letter-spacing:.2em;color:var(--accent);cursor:pointer;list-style:none}"
  "details.sec>summary::-webkit-details-marker{display:none}"
  "details.sec>summary::before{content:'\\25B8  ';color:var(--dim)}"
  "details.sec[open]>summary::before{content:'\\25BE  ';color:var(--dim)}"
  "h3{font-family:var(--mono);font-size:.72rem;text-transform:uppercase;letter-spacing:.12em;"
  "color:var(--muted);margin:1.3rem 0 .4rem}"
  "a{color:var(--cyan);text-decoration:none}"
  "small{color:var(--muted);font-size:.78em}"
  ".ok{color:var(--accent)}.bad{color:var(--amber)}"
  ".mini{padding:.15rem .5rem;font-size:.75rem;border-radius:99px}"
  "td.act{text-align:right;white-space:nowrap}"
  ".trace{font-family:var(--mono);font-size:.75rem;color:var(--muted);margin:0;"
  "white-space:pre-wrap;word-break:break-word;max-height:12rem;overflow-y:auto}"
  ".muted{color:var(--muted);font-size:.85rem}"
  ".card{background:linear-gradient(180deg,rgba(255,255,255,.025),transparent 55%),var(--card);"
  "border:1px solid var(--edge);border-radius:10px;padding:1rem}"
  ".warn{border-color:var(--amber);margin:1rem 0}"
  "table{width:100%;border-collapse:collapse}"
  "td{padding:.4rem .5rem;border-bottom:1px solid var(--edge);font-family:var(--mono);font-size:.85rem}"
  "td:first-child{color:var(--muted);white-space:nowrap}tr:last-child td{border-bottom:none}"
  "input,select,button{font:inherit;padding:.45rem .7rem;border-radius:7px;"
  "border:1px solid var(--edge);background:var(--grid);color:var(--text)}"
  "input:focus,select:focus{outline:1px solid var(--dim);border-color:var(--dim)}"
  "button{background:var(--dim);color:#eafff4;cursor:pointer;font-family:var(--mono);font-size:.85rem}"
  "button:hover{background:var(--accent);color:#06130c;box-shadow:0 0 12px rgba(53,224,140,.35)}"
  ".pill{background:transparent;border:1px solid var(--edge);color:var(--muted);"
  "border-radius:99px;padding:.2rem .6rem;font-size:.78rem}"
  ".pill:hover{background:transparent;color:var(--text);border-color:var(--muted);box-shadow:none}"
  "label{display:block;margin-bottom:.8rem}"
  "label input,label select{width:100%;margin-top:.25rem}"
  ".row{display:flex;gap:.6rem}.row label{flex:1}"
  "label input.ck{width:auto;margin:0 .45rem 0 0}"
  "code{font-family:var(--mono);font-size:.85em;color:var(--cyan)}"
  /* Een knop die een categorie verkeer stilzet of de radio omgooit, hoort er
   * anders uit te zien dan een knop die een naam verandert. De risicoklasse zelf
   * staat in de kop waaronder de parameter valt, met de uitleg erbij -- dus vóór
   * de keuze, en niet pas in het venster dat om een bevestiging vraagt. */
  "button.dgr{background:transparent;color:var(--amber);border-color:var(--amber)}"
  "button.dgr:hover{background:var(--amber);color:#2a1600;box-shadow:0 0 12px rgba(255,180,84,.4)}"
  "table.cfg td{padding:.3rem .35rem;vertical-align:middle}"
  "table.cfg input,table.cfg select{width:100%}"
  "table.cfg td.k{white-space:normal;word-break:break-all;color:var(--text)}"
  ".nw{white-space:nowrap}"
  ".rw{display:flex;gap:.3rem;align-items:center}.rw>input{min-width:0}"
  "</style></head><body>"
  "<div class=topbar><span class=brand>MeshManager</span>"
  "<span><button class=pill id=lg></button> <button class=pill id=th></button></span></div>"
  "<main>"
  "<h1 id=nm>MeshCore</h1><p class=muted id=sub></p>"
  "<div id=safe></div>"
  /* Welke sectie open begint is een keuze en geen standaardwaarde. De toestand
   * staat open omdat dat de vraag is waarmee iemand deze pagina opent; de rest
   * gaat dicht, want dat zijn de plekken waar je bewust naartoe gaat. Een pagina
   * die alles openklapt is net zo onleesbaar als een pagina zonder secties -- en
   * met de instellingen en het filter erbij is ze twee keer zo lang. */
  "<details class=sec data-ck=state open><summary data-i18n=t_state></summary>"
  "<div class=card><table id=st></table></div></details>"
  "<details class=sec data-ck=wifi><summary data-i18n=t_wifi></summary><div class=card><form id=f>"
  "<label><span data-i18n=l_ssid></span><input name=ssid></label>"
  "<label><span data-i18n=l_pass></span><input name=pass type=password data-i18n-ph=ph_unch></label>"
  "<label><span data-i18n=l_appass></span><input name=ap_pass type=password data-i18n-ph=ph_unch></label>"
  "<button type=submit data-i18n=b_saveconn></button></form>"
  "<p class=muted data-i18n=h_wifi></p></div></details>"
  "<details class=sec data-ck=power><summary data-i18n=t_power></summary><div class=card><form id=p>"
  "<div class=row><label><span data-i18n=l_mode></span><select name=mode>"
  "<option value=0 data-i18n=o_always></option><option value=1 data-i18n=o_save></option>"
  "</select></label>"
  "<label style='max-width:9rem'><span data-i18n=l_window></span>"
  "<input name=window type=number min=30 max=3600></label></div>"
  "<label><input class=ck type=checkbox name=sleep><span data-i18n=l_sleep></span></label>"
  "<h3 data-i18n=t_rules></h3><table id=rt></table>"
  "<p><button type=button class=pill id=radd data-i18n=b_addrule></button> "
  "<button type=submit data-i18n=b_save></button></p></form>"
  "<p class=muted id=est></p>"
  "<p class=muted data-i18n=h_power></p></div></details>"
  "<details class=sec data-ck=mqtt><summary data-i18n=t_mqtt></summary><div class=card><form id=m>"
  "<div class=row><label><span data-i18n=l_broker></span><input name=host placeholder='10.0.0.5'></label>"
  "<label style='max-width:7rem'><span data-i18n=l_port></span>"
  "<input name=port type=number min=1 max=65535></label></div>"
  "<div class=row><label><span data-i18n=l_user></span><input name=user></label>"
  "<label><span data-i18n=l_pass></span><input name=pass type=password data-i18n-ph=ph_unch></label></div>"
  "<label><span data-i18n=l_prefix></span><input name=prefix></label>"
  "<label><input class=ck type=checkbox name=enabled><span data-i18n=l_enabled></span></label>"
  "<label><input class=ck type=checkbox name=rx><span data-i18n=l_rx></span></label>"
  "<button type=submit data-i18n=b_save></button></form><table id=mt></table>"
  "<p class=muted><span data-i18n=h_topics></span> <code id=tp></code></p></div></details>"
  "<details class=sec data-ck=mon><summary data-i18n=t_mon></summary><div class=card>"
  "<p class=muted data-i18n=h_mon></p>"
  "<label><span data-i18n=l_filter></span><input id=fl></label>"
  "<h3 data-i18n=t_heard></h3><table id=hl></table>"
  "<h3 data-i18n=t_manual></h3>"
  "<div class=row><label><span data-i18n=l_key></span><input id=mk></label>"
  "<label style='max-width:9rem'><span data-i18n=l_name></span><input id=mn></label></div>"
  "<button id=mab data-i18n=b_add></button>"
  "<h3 data-i18n=t_monlist></h3><table id=ml></table>"
  "<h3 data-i18n=t_trace></h3><pre id=tr class=trace></pre>"
  "<p class=muted data-i18n=h_acl></p>"
  "<div class=row style='margin-top:.6rem'>"
  "<label style='max-width:10rem'><span data-i18n=l_moniv></span>"
  "<input id=miv type=number min=60 max=65535></label></div>"
  "<button id=mivb data-i18n=b_save></button> "
  "<button id=mpb class=pill data-i18n=b_pollnow></button></div></details>"
  "<details class=sec data-ck=sweep><summary data-i18n=t_settings></summary><div class=card>"
  "<p class=muted data-i18n=h_settings></p>"
  "<table id=sv></table>"
  "<div class=row style='margin-top:.6rem'>"
  "<label style='max-width:12rem'><span data-i18n=l_setiv></span>"
  "<input id=siv type=number min=5 max=65535></label></div>"
  "<button id=sivb data-i18n=b_save></button> "
  "<button id=snow class=pill data-i18n=b_sweep></button></div></details>"
  /* De twee secties hieronder zijn de reden dat deze pagina bestaat zoals ze
   * bestaat. Alles wat de MeshCore-app over het mesh kan instellen, en alles wat
   * het pakketfilter kent, hoort ook te kunnen als de server, het internet of de
   * broker weg is -- dit is de weg die dan overblijft. Beide worden volledig door
   * JavaScript getekend uit /api/cfg en /api/filter, zodat de lijst met
   * parameters en de grammatica van het filter op één plek staan: in de firmware
   * eronder. Wat hier in het HTML staat is niet meer dan waar het komt. */
  "<details class=sec data-ck=cfg><summary data-i18n=t_cfg></summary><div class=card>"
  "<p class=muted data-i18n=h_cfg></p><div id=cfgn></div><div id=cfgb></div></div></details>"
  "<details class=sec data-ck=flt><summary data-i18n=t_flt></summary><div class=card>"
  "<p class=muted data-i18n=h_flt></p><div id=fltn></div><div id=fltb></div></div></details>"
  "<details class=sec data-ck=fw><summary data-i18n=t_fw></summary><div class=card>"
  "<p class=muted data-i18n=h_fw></p><p><a href=/update data-i18n=a_fw></a></p></div></details>"
  "<details class=sec data-ck=backup><summary data-i18n=t_backup></summary><div class=card>"
  "<p class=muted data-i18n=h_backup></p>"
  "<p><a href=/api/backup data-i18n=a_backup></a></p>"
  "<form id=r style='margin-top:.8rem'>"
  "<label><span data-i18n=l_restore></span><input type=file name=f accept='.mcb'></label>"
  "<button type=submit data-i18n=b_restore></button></form></div></details>"
  "</main><script>"
  "var T={nl:{"
  "t_state:'Toestand',t_wifi:'WiFi',t_power:'Energie',t_mqtt:'MQTT',t_fw:'Firmware',"
  "t_backup:'Back-up',l_ssid:'Netwerk (SSID)',l_pass:'Wachtwoord',"
  "l_appass:'Wachtwoord van het eigen netwerk',b_saveconn:'Opslaan en verbinden',b_save:'Opslaan',"
  "ph_unch:'ongewijzigd',"
  "h_wifi:'Lukt verbinden niet, dan zendt de repeater zijn eigen netwerk uit en blijft hij het "
  "jouwe proberen. Via de mesh-CLI werkt wifi altijd.',"
  "l_mode:'Modus',o_always:'Altijd bereikbaar',o_save:'Zuinig (WiFi meestal uit)',"
  "l_window:'Venster (s)',l_sleep:'Modem-sleep terwijl WiFi aan staat',"
  "t_rules:'Accu naar interval',b_addrule:'+ regel',"
  "t_settings:'Instellingen van deze node',b_sweep:'Nu ophalen',l_setiv:'Interval (minuten)',"
  "s_sweep:'Laatste ronde',s_swnext:'Volgende ronde',sw_busy:'bezig...',"
  "sw_never:'nog niet gelopen',sw_done:'%1 gelezen, %2 geen antwoord, %3',"
  "h_settings:'Deze node leest zijn eigen CLI uit en stuurt de waarden mee met een "
  "statistiekenbericht. Ze veranderen zelden, dus dat gebeurt hooguit een keer per dag \u2014 "
  "gebruik Nu ophalen om het meteen te doen.',"
  "e_now:'Nu: elke %1 s, ongeveer %2 berichten per dag.',"
  "e_fast:'Snelste regel: elke %1 s, ongeveer %2 per dag.',"
  "e_floor:'(opgetrokken tot de ondergrens van % s van deze modus)',"
  "e_mon:'Monitoren: %1 repeater(s), %2 LoRa-pakketten per ronde, ongeveer %3 per uur.',"
  "h_power:'Hoe voller de accu, hoe vaker de repeater publiceert; \\u2019s nachts trager. In de "
  "zuinige modus staat WiFi normaal uit en komt hij elke ronde even boven water; ontvangen "
  "pakketten wachten intussen in een buffer. Kwijt geraakt? wifi on 30 via de mesh-CLI zet WiFi "
  "meteen 30 minuten aan, ook bij een lege accu. Drempels en intervallen wijzig je met "
  "wifi power set <naam> <waarde>.',"
  "l_broker:'Broker',l_port:'Poort',l_user:'Gebruiker',l_prefix:'Topicprefix',"
  "l_enabled:'Doorsturen ingeschakeld',l_rx:'Ook elk ontvangen pakket doorsturen',"
  "h_topics:'Topics:',"
  "h_fw:'Upgraden kan hier, over je gewone WiFi. Een afgebroken upload laat de huidige firmware "
  "staan.',a_fw:'Firmware uploaden \\u2192',"
  "h_backup:'De back-up bevat alles uit het bestandssysteem: je sleutelpaar, de "
  "repeater-instellingen, de ACL en de netwerkinstellingen. Wie dit bestand heeft, heeft de "
  "identiteit van je node \\u2014 bewaar het veilig.',a_backup:'Back-up downloaden \\u2192',"
  "l_restore:'Back-up terugzetten',b_restore:'Terugzetten en herstarten',"
  "safe:'Veilige modus: de repeater is meermaals herstart, dus alle extra\\u2019s staan uit. "
  "Herstart hem opnieuw zodra je de oorzaak weg hebt.',"
  "s_wifi:'WiFi',s_ip:'IP',s_net:'Netwerk',s_signal:'Signaal',s_uptime:'Uptime',"
  "s_heap:'Vrij geheugen',s_batt:'Batterij',s_power:'Energie',s_wdt:'Watchdog',"
  "s_mcu:'Chiptemperatuur',h_mcu:'van de chip zelf, niet de buitenlucht',"
  "m_reads:'gelezen',m_pubs:'verstuurd',m_kinds:'status/telemetrie/buren',"
  "u_min:'min',d_on:'actief (% s)',d_off:'uit (upload bezig)',s_live:'Live doorsturen',"
  "lv_on:'aan \\u2014 pakketten gaan meteen door',"
  "lv_batt:'uit \\u2014 accu onder %%',"
  "lv_save:'niet mogelijk in zuinige modus (WiFi gaat tussendoor uit)',"
  "w_ok:'verbonden',w_try:'verbinden\\u2026',w_ap:'eigen netwerk (WiFi onbereikbaar)',"
  "w_off:'uit (zuinig)',"
  "b_unknown:'onbekend (aangenomen: netstroom)',"
  "p_always:'altijd bereikbaar',p_forced:'opgevorderd, nog % min',"
  "p_awake:'zuinig, nog % s bereikbaar',p_asleep:'zuinig, wifi terug over % s',"
  "p_every:'publiceert elke % s',p_night:'nacht',"
  "m_broker:'Broker',m_stats:'Statistieken',m_pkts:'Pakketten',m_queue:'Wachtrij',"
  "m_errors:'Fouten',m_sent:'verstuurd',m_fwd:'doorgestuurd',m_drop:'laten vallen',"
  "mq_off:'uit',mq_conn:'verbonden',mq_disc:'niet verbonden',mq_unset:'niet ingesteld',"
  "e_conn:'verbinding faalde (rc %)',e_stats:'stats versturen faalde',"
  "e_pkt:'pakket versturen faalde',"
  "t_mon:'Monitoren',t_heard:'Gehoorde repeaters',t_manual:'Handmatig toevoegen',"
  "t_monlist:'Wordt gemonitord',t_trace:'Verloop laatste ronde',l_filter:'Filteren',l_key:'Publieke sleutel (hex)',"
  "l_name:'Naam',b_add:'Toevoegen',l_moniv:'Interval (s)',b_pollnow:'Nu ophalen',"
  "h_mon:'Deze repeater kan op andere repeaters inloggen, hun status ophalen en die "
  "doorsturen naar de site. Kies er een uit de gehoorde lijst, of plak een publieke "
  "sleutel van een node die je nog niet gehoord hebt.',"
  "h_acl:'Laat het wachtwoord leeg om via de access list van de andere node binnen te "
  "raken: die operator voegt jouw sleutel dan \\u00e9\\u00e9nmalig toe met "
  "setperm <jouw-sleutel> 1 (1=alleen lezen, 2=lezen/schrijven, 3=beheerder). Netter dan "
  "wachtwoorden rondsturen. Een geweigerde login is niet te onderscheiden van een "
  "onbereikbare node: beide zwijgen.',"
  "m_none:'nog niets',m_heardnone:'nog geen repeaters gehoord',h_stored:'uit bewaarde adverts',"
  "st_unres:'wacht op advert',st_never:'nog niet geprobeerd',st_ok:'login gelukt',"
  "st_noans:'geen antwoord (geweigerd of onbereikbaar)',"
  "ph_pw:'leeg = via access list',u_ago:'geleden',u_never:'nooit',"
  "a_badkey:'Dat is geen geldige sleutel. Alleen hex, minstens % tekens.',"
  "a_saved:'Opgeslagen. De repeater verbindt opnieuw; ververs deze pagina zo dadelijk.',"
  "a_pick:'Kies eerst een back-upbestand.',"
  "a_conf:'Alle instellingen en sleutels worden overschreven. Doorgaan?',"
  // ---- CLI-instellingen -------------------------------------------------
  "t_cfg:'Instellingen wijzigen',"
  "h_cfg:'Alles wat je met de MeshCore-app of over de CLI kunt zetten, staat hier ook. "
  "De lijst, de grenzen en de risicoklassen komen uit deze firmware zelf, dus wat je hier "
  "ziet is wat er werkelijk tussen een klik en de radio staat. Na het zetten wordt de "
  "waarde teruggelezen: er staat wat de node ANTWOORDT, niet wat je vroeg.',"
  "c_g1:'Gewoon',c_g2:'Schrijft merkbaar',c_g3:'Kan de bereikbaarheid afsnijden',"
  "c_b1:'Waarden die je zo weer terugzet. Opslaan volstaat.',"
  "c_b2:'Deze veranderen merkbaar hoe de node zich gedraagt op het mesh. Ze vragen een "
  "bevestiging, maar ze kunnen de node niet buiten bereik brengen.',"
  "c_b3:'Deze raken de radio of wie er mag inloggen. Een fout hier kan betekenen dat je "
  "deze node alleen nog met een kabel in je hand terugkrijgt. Typ daarom de naam over.',"
  "c_par:'Parameter',c_now:'Nu',c_new:'Nieuwe waarde',b_set:'Zet',"
  "c_none:'\\u2014',c_secret:'\\u2022\\u2022\\u2022',"
  "c_load:'De lijst met parameters is niet op te halen bij deze node.',"
  "c_rb:'Bewaard, maar nog niet actief: dit gaat pas om bij een herstart. Tot dan draait "
  "de radio op de oude waarden, en pas bij die herstart blijkt of de nieuwe kloppen.',"
  "c_ok:'Gezet. %1 staat nu op %2.',"
  "c_near:'Gezet, maar niet precies. Gevraagd %1, in de node staat nu %2 \\u2014 en dat "
  "laatste is wat er werkelijk staat.',"
  "c_bad:'Niet gezet. %1',"
  "a_c2:'%1 op deze node zetten op: %2\\n\\nDit verandert merkbaar hoe de node zich "
  "gedraagt op het mesh.\\n\\nDoorgaan?',"
  "a_c3:'%1 op deze node zetten op: %2\\n\\nDIT KAN DE NODE ONBEREIKBAAR MAKEN. Gaat het "
  "mis, dan is er geen tweede weg naar binnen behalve een kabel.\\n\\nTyp de naam van de "
  "node over om door te gaan:',"
  "a_cno:'De naam klopt niet. Er is niets veranderd.',"
  // ---- pakketfilter -----------------------------------------------------
  "t_flt:'Pakketfilter',"
  "h_flt:'Bepaalt welk verkeer van ANDEREN deze repeater nog doorstuurt. Pakketten aan "
  "deze node zelf gaan er nooit langs, en wie in de access list staat blijft werken: je "
  "kunt jezelf hier niet mee buitensluiten. Je kunt de node er w\\u00e9l stilletjes "
  "nutteloos mee maken \\u2014 hij blijft antwoorden en adverteren, en stuurt niets meer "
  "door.',"
  "f_back:'De weg terug werkt hoe dan ook over de mesh-CLI: filter off en filter reset "
  "hebben geen WiFi, geen beheerpagina en geen server nodig.',"
  "f_load:'De filterstand is niet op te halen bij deze node.',"
  "f_onoff:'Aan of uit',"
  "f_swblurb:'Uitzetten laat de regels staan; terugzetten wist ze \\u00e9n zet het filter "
  "uit. Allebei lichter dan aanzetten, met opzet: herstel mag nooit strakker afgeschermd "
  "zijn dan de fout die het terugdraait.',"
  "f_bon:'Filter aan',f_boff:'Filter uit',f_brst:'Alles terug naar standaard',"
  "f_now:'Nu volgens de node zelf: %1, minimale padhash %2, structuurcontrole op "
  "groepstekst %3.',"
  "f_on:'aan',f_off:'uit',f_yes:'ja',f_no:'nee',"
  "f_disarm:'De node heeft het filter zelf uit gelaten na herhaalde herstarts (veilige "
  "modus). De regels staan er nog en komen terug zodra hij weer normaal opstart.',"
  "f_rules:'Losse regels',f_rule:'Regel',"
  "f_hash:'Minimale padhash',"
  "f_h1:'1 byte \\u2014 alles door',f_h2:'2 byte \\u2014 oudere nodes vallen af',"
  "f_h3:'3 byte \\u2014 vrijwel alles valt af',"
  "f_mal:'Structuurcontrole op groepstekst',"
  "f_malb:'Misvormd betekent hier: structureel onmogelijk (lengte en blokuitlijning). De "
  "inhoud kan een repeater niet lezen \\u2014 daarvoor zou hij de kanaalsleutel nodig hebben.',"
  "f_types:'Per pakkettype',"
  "f_tb:'Een hoplimiet van 0 of een type op niet-doorsturen zet een hele categorie verkeer "
  "stil; daar vraagt deze pagina de naam van de node voor. Drie losse knoppen per regel, "
  "want het zijn drie handelingen met drie verschillende gewichten.',"
  "f_ty:'Type',f_thr:'Door',f_hops:'Max hops',f_rate:'Snelheid',f_drop:'Weg',"
  "f_act:'Wijzigen',f_close:'dichtzetten',f_open:'openzetten',"
  "f_chan:'Geblokkeerde kanalen',"
  "f_cb:'Een repeater ziet geen kanaalnaam, alleen \\u00e9\\u00e9n byte: sha256(sleutel)[0]. "
  "Blokkeren gaat dus op sleutel of op hash, en ongeveer 1 op de 256 kanalen deelt die byte "
  "met een ander.',"
  "f_clab:'Label',f_chash:'Hash',f_cadd:'blokkeer',f_crem:'weer doorlaten',"
  "f_cnone:'geen enkel kanaal geblokkeerd',f_cpsk:'kanaalsleutel (base64) of #hh',"
  "f_cnt:'Tellers',f_pass:'Doorgelaten',f_ex:'Vrijgesteld via de ACL',"
  "f_dropped:'Weggegooid omdat',f_num:'aantal',f_nodrop:'nog niets weggegooid',"
  "r_type:'type helemaal dicht',r_hops:'te veel hops',r_rate:'over de snelheidslimiet',"
  "r_hash:'padhash te klein',r_kanaal:'geblokkeerd kanaal',r_misvormd:'misvormde groepstekst',"
  "a_f2:'%1\\n\\nDoorgaan?',"
  "a_f3:'%1\\n\\nDIT ZET EEN HELE CATEGORIE VERKEER STIL. De node blijft antwoorden en "
  "adverteren alsof er niets aan de hand is.\\n\\nTyp de naam van de node over om door te "
  "gaan:',"
  /* q_ en niet d_: d_on en d_off zijn hierboven al de twee toestanden van de
   * watchdog. Twee betekenissen achter dezelfde sleutel is een fout die pas
   * opvalt als er ergens 'uit (upload bezig)' staat waar een filter bedoeld was. */
  "q_on:'het pakketfilter AANZETTEN',q_off:'het pakketfilter uitzetten (de regels blijven "
  "staan)',q_rst:'alle filterregels terugzetten op de standaard en het filter uitzetten',"
  "q_hop0:'%1 helemaal niet meer doorsturen (0 hops)',q_hop:'%1 beperken tot %2 hops',"
  "q_rt0:'%1 geen snelheidslimiet meer geven',q_rt:'%1 beperken tot %2 pakketten per %3 s',"
  "q_tyof:'%1 helemaal niet meer doorsturen',q_tyon:'%1 weer doorsturen',"
  "q_hsh:'de minimale padhash op %1 byte zetten',"
  "q_mlon:'de structuurcontrole op groepstekst aanzetten',"
  "q_mlof:'de structuurcontrole op groepstekst uitzetten',"
  "q_cadd:'kanaal %1 blokkeren',q_crem:'kanaal %1 weer doorlaten',"
  "f_okm:'Gedaan: %1.',f_badm:'Niet gedaan. %1'},"
  "en:{"
  "t_state:'Status',t_wifi:'WiFi',t_power:'Power',t_mqtt:'MQTT',t_fw:'Firmware',"
  "t_backup:'Backup',l_ssid:'Network (SSID)',l_pass:'Password',"
  "l_appass:'Password of its own network',b_saveconn:'Save and connect',b_save:'Save',"
  "ph_unch:'unchanged',"
  "h_wifi:'If it cannot connect, the repeater broadcasts its own network and keeps retrying "
  "yours. Over the mesh CLI, wifi always works.',"
  "l_mode:'Mode',o_always:'Always reachable',o_save:'Power save (WiFi mostly off)',"
  "l_window:'Window (s)',l_sleep:'Modem sleep while WiFi is up',"
  "t_rules:'Battery to interval',b_addrule:'+ rule',"
  "t_settings:'This node\u2019s settings',b_sweep:'Sweep now',l_setiv:'Interval (minutes)',"
  "s_sweep:'Last sweep',s_swnext:'Next sweep',sw_busy:'running...',"
  "sw_never:'not run yet',sw_done:'%1 read, %2 no answer, %3',"
  "h_settings:'This node reads back its own CLI and ships the values with a statistics "
  "message. They rarely change, so that happens at most once a day \u2014 use Sweep now to "
  "do it immediately.',"
  "e_now:'Now: every %1 s, roughly %2 messages per day.',"
  "e_fast:'Fastest rule: every %1 s, roughly %2 per day.',"
  "e_floor:'(raised to this mode\u2019s floor of %s)',"
  "e_mon:'Monitoring: %1 repeater(s), %2 LoRa packets per round, roughly %3 per hour.',"
  "h_power:'The fuller the battery, the more often the repeater publishes; slower at night. In "
  "power-save mode WiFi is normally off and only surfaces once per round; received packets wait "
  "in a buffer meanwhile. Locked out? wifi on 30 over the mesh CLI brings WiFi up for 30 minutes "
  "right away, even on a flat battery. Thresholds and intervals are changed with "
  "wifi power set <name> <value>.',"
  "l_broker:'Broker',l_port:'Port',l_user:'User',l_prefix:'Topic prefix',"
  "l_enabled:'Forwarding enabled',l_rx:'Also forward every received packet',"
  "h_topics:'Topics:',"
  "h_fw:'Upgrade here, over your normal WiFi. An aborted upload leaves the current firmware in "
  "place.',a_fw:'Upload firmware \\u2192',"
  "h_backup:'The backup holds everything in the file system: your key pair, the repeater "
  "settings, the ACL and the network settings. Whoever has this file has your node\\u2019s "
  "identity \\u2014 keep it safe.',a_backup:'Download backup \\u2192',"
  "l_restore:'Restore a backup',b_restore:'Restore and restart',"
  "safe:'Safe mode: the repeater restarted repeatedly, so all extras are off. Restart it once "
  "you have removed the cause.',"
  "s_wifi:'WiFi',s_ip:'IP',s_net:'Network',s_signal:'Signal',s_uptime:'Uptime',"
  "s_heap:'Free memory',s_batt:'Battery',s_power:'Power',s_wdt:'Watchdog',"
  "s_mcu:'Chip temperature',h_mcu:'of the chip itself, not the outside air',"
  "m_reads:'read',m_pubs:'published',m_kinds:'status/telemetry/neighbours',"
  "u_min:'min',d_on:'armed (% s)',d_off:'off (upload in progress)',s_live:'Live forwarding',"
  "lv_on:'on \\u2014 packets go out immediately',"
  "lv_batt:'off \\u2014 battery below %%',"
  "lv_save:'not possible in power-save mode (WiFi goes off between rounds)',"
  "w_ok:'connected',w_try:'connecting\\u2026',w_ap:'own network (WiFi unreachable)',"
  "w_off:'off (power save)',"
  "b_unknown:'unknown (assuming mains)',"
  "p_always:'always reachable',p_forced:'forced up, % min left',"
  "p_awake:'power save, reachable for % s',p_asleep:'power save, wifi back in % s',"
  "p_every:'publishes every % s',p_night:'night',"
  "m_broker:'Broker',m_stats:'Statistics',m_pkts:'Packets',m_queue:'Queue',"
  "m_errors:'Errors',m_sent:'sent',m_fwd:'forwarded',m_drop:'dropped',"
  "mq_off:'off',mq_conn:'connected',mq_disc:'not connected',mq_unset:'not configured',"
  "e_conn:'connection failed (rc %)',e_stats:'publishing stats failed',"
  "e_pkt:'publishing packet failed',"
  "t_mon:'Monitoring',t_heard:'Repeaters heard',t_manual:'Add by hand',"
  "t_monlist:'Being monitored',t_trace:'Last round in detail',l_filter:'Filter',l_key:'Public key (hex)',"
  "l_name:'Name',b_add:'Add',l_moniv:'Interval (s)',b_pollnow:'Poll now',"
  "h_mon:'This repeater can log in to other repeaters, fetch their status and forward it "
  "to the site. Pick one from the heard list, or paste the public key of a node you have "
  "not heard yet.',"
  "h_acl:'Leave the password empty to get in via the other node\\u2019s access list: its "
  "operator adds your key once with setperm <your-key> 1 (1=read-only, 2=read/write, "
  "3=admin). Tidier than passing passwords around. A refused login cannot be told from an "
  "unreachable node: both stay silent.',"
  "m_none:'nothing yet',m_heardnone:'no repeaters heard yet',h_stored:'from stored adverts',"
  "st_unres:'waiting for advert',st_never:'not tried yet',st_ok:'login succeeded',"
  "st_noans:'no answer (refused or unreachable)',"
  "ph_pw:'empty = via access list',u_ago:'ago',u_never:'never',"
  "a_badkey:'That is not a valid key. Hex only, at least % characters.',"
  "a_saved:'Saved. The repeater is reconnecting; refresh this page in a moment.',"
  "a_pick:'Pick a backup file first.',"
  "a_conf:'All settings and keys will be overwritten. Continue?',"
  // ---- CLI settings -----------------------------------------------------
  "t_cfg:'Change settings',"
  "h_cfg:'Everything you can set from the MeshCore app or over the CLI is here too. The "
  "list, the bounds and the risk classes come from this firmware itself, so what you see "
  "is what actually sits between a click and the radio. After setting, the value is read "
  "back: what you see is what the node ANSWERS, not what you asked for.',"
  "c_g1:'Ordinary',c_g2:'Noticeably changes behaviour',c_g3:'Can cut off reachability',"
  "c_b1:'Values you can put back just as easily. Saving is enough.',"
  "c_b2:'These noticeably change how the node behaves on the mesh. They ask for a "
  "confirmation, but they cannot put the node out of reach.',"
  "c_b3:'These touch the radio or who may log in. A mistake here can mean you only get "
  "this node back with a cable in your hand. So retype its name.',"
  "c_par:'Parameter',c_now:'Now',c_new:'New value',b_set:'Set',"
  "c_none:'\\u2014',c_secret:'\\u2022\\u2022\\u2022',"
  "c_load:'The parameter list cannot be fetched from this node.',"
  "c_rb:'Stored, but not active yet: this only takes effect on a restart. Until then the "
  "radio runs on the old values, and only that restart will show whether the new ones are "
  "right.',"
  "c_ok:'Set. %1 is now %2.',"
  "c_near:'Set, but not exactly. Asked for %1, the node now holds %2 \\u2014 and that last "
  "one is what is really there.',"
  "c_bad:'Not set. %1',"
  "a_c2:'Set %1 on this node to: %2\\n\\nThis noticeably changes how the node behaves on "
  "the mesh.\\n\\nContinue?',"
  "a_c3:'Set %1 on this node to: %2\\n\\nTHIS CAN MAKE THE NODE UNREACHABLE. If it goes "
  "wrong there is no second way in but a cable.\\n\\nRetype the node\\u2019s name to "
  "continue:',"
  "a_cno:'That name does not match. Nothing was changed.',"
  // ---- packet filter ----------------------------------------------------
  "t_flt:'Packet filter',"
  "h_flt:'Decides which of OTHER people\\u2019s traffic this repeater still forwards. "
  "Packets addressed to this node never pass through it, and whoever is in the access list "
  "keeps working: you cannot lock yourself out with this. You can quietly make the node "
  "useless \\u2014 it keeps answering and advertising, and forwards nothing.',"
  "f_back:'The way back works regardless over the mesh CLI: filter off and filter reset "
  "need no WiFi, no admin page and no server.',"
  "f_load:'The filter state cannot be fetched from this node.',"
  "f_onoff:'On or off',"
  "f_swblurb:'Switching off keeps the rules; resetting wipes them and switches the filter "
  "off. Both are lighter than switching on, deliberately: recovery must never be gated more "
  "tightly than the mistake it undoes.',"
  "f_bon:'Filter on',f_boff:'Filter off',f_brst:'Everything back to defaults',"
  "f_now:'Now, according to the node itself: %1, minimum path hash %2, structural check on "
  "group text %3.',"
  "f_on:'on',f_off:'off',f_yes:'yes',f_no:'no',"
  "f_disarm:'The node has left the filter off itself after repeated restarts (safe mode). "
  "The rules are still there and come back once it starts normally again.',"
  "f_rules:'Single rules',f_rule:'Rule',"
  "f_hash:'Minimum path hash',"
  "f_h1:'1 byte \\u2014 everything through',f_h2:'2 bytes \\u2014 older nodes drop out',"
  "f_h3:'3 bytes \\u2014 nearly everything drops out',"
  "f_mal:'Structural check on group text',"
  "f_malb:'Malformed here means: structurally impossible (length and block alignment). A "
  "repeater cannot read the content \\u2014 that would need the channel key.',"
  "f_types:'Per packet type',"
  "f_tb:'A hop limit of 0, or a type set to not-forwarded, silences a whole category of "
  "traffic; this page asks for the node\\u2019s name for those. Three separate buttons per "
  "row, because they are three actions with three different weights.',"
  "f_ty:'Type',f_thr:'Through',f_hops:'Max hops',f_rate:'Rate',f_drop:'Dropped',"
  "f_act:'Change',f_close:'close off',f_open:'open up',"
  "f_chan:'Blocked channels',"
  "f_cb:'A repeater sees no channel name, only one byte: sha256(key)[0]. So blocking takes "
  "a key or a hash, and roughly 1 channel in 256 shares that byte with another.',"
  "f_clab:'Label',f_chash:'Hash',f_cadd:'block',f_crem:'allow again',"
  "f_cnone:'no channel blocked',f_cpsk:'channel key (base64) or #hh',"
  "f_cnt:'Counters',f_pass:'Passed',f_ex:'Exempt via the ACL',"
  "f_dropped:'Dropped because',f_num:'count',f_nodrop:'nothing dropped yet',"
  "r_type:'type closed off entirely',r_hops:'too many hops',r_rate:'over the rate limit',"
  "r_hash:'path hash too small',r_kanaal:'blocked channel',r_misvormd:'malformed group text',"
  "a_f2:'%1\\n\\nContinue?',"
  "a_f3:'%1\\n\\nTHIS SILENCES A WHOLE CATEGORY OF TRAFFIC. The node keeps answering and "
  "advertising as if nothing were wrong.\\n\\nRetype the node\\u2019s name to continue:',"
  "q_on:'switch the packet filter ON',q_off:'switch the packet filter off (the rules stay)',"
  "q_rst:'put all filter rules back to their defaults and switch the filter off',"
  "q_hop0:'stop forwarding %1 entirely (0 hops)',q_hop:'limit %1 to %2 hops',"
  "q_rt0:'give %1 no rate limit',q_rt:'limit %1 to %2 packets per %3 s',"
  "q_tyof:'stop forwarding %1 entirely',q_tyon:'forward %1 again',"
  "q_hsh:'set the minimum path hash to %1 byte(s)',"
  "q_mlon:'switch the structural check on group text on',"
  "q_mlof:'switch the structural check on group text off',"
  "q_cadd:'block channel %1',q_crem:'allow channel %1 again',"
  "f_okm:'Done: %1.',f_badm:'Not done. %1'}};"
  "var $=function(s){return document.querySelector(s)},last=null;"
  "var L=localStorage.getItem('mslang')||((navigator.language||'').indexOf('nl')==0?'nl':'en');"
  "var TH=localStorage.getItem('mstheme')||"
  "(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark');"
  "function theme(){document.documentElement.setAttribute('data-theme',TH);"
  "$('#th').textContent=TH=='light'?'\\u263e':'\\u2600'}"
  "function lang(){var d=T[L];document.documentElement.lang=L;renderMon();"
  "$('#lg').textContent=L=='nl'?'EN':'NL';"
  "document.querySelectorAll('[data-i18n]').forEach(function(e){"
  "var v=d[e.getAttribute('data-i18n')];if(v)e.textContent=v});"
  "document.querySelectorAll('[data-i18n-ph]').forEach(function(e){"
  "var v=d[e.getAttribute('data-i18n-ph')];if(v)e.placeholder=v});"
  /* Ook de twee tabellen die volledig door JavaScript getekend worden. Die
   * dragen geen data-i18n, want hun teksten zitten in de rijen zelf -- de lus
   * hierboven komt er dus niet aan, en zonder deze twee regels blijft de halve
   * pagina in de vorige taal staan. Beide kunnen tegen 'nog niets geladen'. */
  "render();renderCfg();renderFlt()}"
  "function rows(o){var h='';for(var k in o){h+='<tr><td>'+k+'</td><td>'+o[k]+'</td></tr>'}return h}"
  "function fill(f,c){for(var k in c){var e=f[k];if(!e)continue;"
  "if(e.type=='checkbox')e.checked=!!c[k];else e.value=c[k]}}"
  "function nfmt(n){return n>=1000?(Math.round(n/100)/10)+'k':Math.round(n)}"
  // The rules table, plus what the whole configuration costs on the air. Two
  // separate numbers the user cannot combine in his head is not an interface.
  "function rules(d){var t=T[L],h='',i,fast=99999;"
  "for(i=0;i<d.rules.length;i++){var r=d.rules[i];if(r.s<fast)fast=r.s;"
  "h+='<tr'+(i==d.pwr.rule?' class=ok':'')+'>'"
  "+'<td>&ge; <input type=number min=0 max=100 class=rp value='+r.p+' style=\"width:4.5rem\"> %</td>'"
  "+'<td><input type=number min=1 max=65535 class=rs value='+r.s+' style=\"width:6rem\"> s</td>'"
  "+'<td class=act><button type=button class=\"mini rd\" data-i='+i+'>\\u2715</button></td></tr>'}"
  "$('#rt').innerHTML=h;"
  "var lo=d.pwr.min,eff=Math.max(fast,lo),now=d.pwr.iv;"
  "var nmon=0;if(mon&&mon.mon)for(i=0;i<mon.mon.length;i++)if(mon.mon[i].e&&mon.mon[i].res)nmon++;"
  "var s=t.e_now.replace('%1',now).replace('%2',nfmt(86400/now));"
  "s+=' '+t.e_fast.replace('%1',eff).replace('%2',nfmt(86400/eff));"
  "if(eff>fast)s+=' '+t.e_floor.replace('%',lo);"
  "if(nmon&&mon.iv)s+=' '+t.e_mon.replace('%1',nmon).replace('%2',nmon*6)"
  ".replace('%3',nfmt(nmon*6*3600/mon.iv));"
  "$('#est').textContent=s}"
  // The sweep, answerable at any moment rather than only in the message that
  // happens to follow one.
  "function sett(d){var t=T[L],e=d.set,o={},k;"
  "o[t.s_sweep]=e.busy?t.sw_busy:(e.age<0?t.sw_never"
  ":t.sw_done.replace('%1',e.ok).replace('%2',e.miss).replace('%3',ago(e.age,t)));"
  "o[t.s_swnext]=e.busy?t.sw_busy:(e.next<60?e.next+' s':Math.round(e.next/60)+' min');"
  "for(k in e.v)o[k]=e.v[k];"
  "$('#sv').innerHTML=rows(o);$('#siv').value=e.iv}"
  "function pwrtext(d){var t=T[L],p=d.pwr,s=t['p_'+p.st]||p.st;"
  "s=s.replace('%',p.st=='forced'?Math.ceil(p.secs/60):p.secs);"
  "return s+' \\u00b7 '+t.p_every.replace('%',p.iv)+(p.night?' ('+t.p_night+')':'')}"
  "function render(){var d=last,t=T[L];if(!d)return;"
  "$('#nm').textContent=d.name;"
  "$('#sub').textContent=d.ms+' \\u00b7 MeshCore '+d.fw+' \\u00b7 '+d.board"
  "+' \\u00b7 id '+d.node;"
  "var s={};s[t.s_wifi]=t['w_'+d.wifi.st];s[t.s_ip]=d.wifi.ip;s[t.s_net]=d.wifi.net;"
  "s[t.s_signal]=d.wifi.rssi+' dBm';s[t.s_uptime]=d.wifi.up+' '+t.u_min;"
  "s[t.s_heap]=d.wifi.heap+' bytes';"
  "s[t.s_batt]=d.bat.known?((d.bat.mv/1000).toFixed(2)+' V \\u00b7 '+d.bat.pct+'%'"
  "+(d.rules&&d.rules[d.pwr.rule]?' \\u00b7 \\u2265'+d.rules[d.pwr.rule].p+'%':'')):t.b_unknown;"
  "s[t.s_power]=pwrtext(d);"
  "if(d.mcu_t>-100)s[t.s_mcu]=d.mcu_t.toFixed(1)+' \\u00b0C <small>'+t.h_mcu+'</small>';"
  "s[t.s_live]=(t['lv_'+d.live]||d.live).replace('%',d.livepct);"
  "s[t.s_wdt]=d.wdt?t.d_on.replace('%',d.wdt_s):t.d_off;$('#st').innerHTML=rows(s);"
  "rules(d);sett(d);"
  "var q=d.mqtt,m={};m[t.m_broker]=t['mq_'+q.st];"
  "m[t.m_stats]=q.stats+' '+t.m_sent;"
  "m[t.m_pkts]=q.pkt+' '+t.m_fwd+', '+q.drop+' '+t.m_drop;"
  "m[t.m_queue]=q.queue;"
  "m[t.m_errors]=q.fail+(q.err?' \\u2014 '+(t['e_'+q.err]||q.err).replace('%',q.rc):'');"
  "$('#mt').innerHTML=rows(m);"
  "$('#f').ssid.value=d.ssid;fill($('#p'),d.pwr);fill($('#m'),d.mqtt);"
  "$('#tp').textContent=q.prefix+'/'+d.node+'/stats + /rx';"
  "$('#safe').innerHTML=d.safe?'<div class=\"card warn\">'+t.safe+'</div>':''}"
  "function load(){fetch('/api/status').then(function(r){return r.json()})"
  ".then(function(d){last=d;render()})}"
  // ---- monitoring -------------------------------------------------------
  "var mon=null;"
  "function esc(s){return String(s).replace(/[&<>\"]/g,function(c){"
  "return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]})}"
  "function hit(o,q){if(!q)return true;q=q.toLowerCase();"
  "return (o.n||'').toLowerCase().indexOf(q)>=0||(o.k||'').toLowerCase().indexOf(q)>=0}"
  "function ago(s,t){return s<0?t.u_never:(s<120?s+' s':Math.round(s/60)+' min')+' '+t.u_ago}"
  "function monst(m,t){if(!m.res)return'<span class=bad>'+t.st_unres+'</span>';"
  "if(m.lr==1)return'<span class=ok>'+t.st_ok+'</span>';"
  "if(m.lr==2)return'<span class=bad>'+t.st_noans+'</span>';return t.st_never}"
  "function renderMon(){var d=mon,t=T[L];if(!d)return;"
  "var q=$('#fl').value,h='',i;"
  "for(i=0;i<d.heard.length;i++){var x=d.heard[i];if(!hit(x,q))continue;"
  "h+='<tr><td>'+esc(x.n||'-')+'<br><small>'+x.k.substr(0,12)+'</small></td>'"
  "+'<td>'+(x.snr==null?'<span class=muted>'+t.h_stored+'</span>':x.snr.toFixed(1)+' dB')"
  "+'<br><small>'+ago(x.age,t)+'</small></td>'"
  "+'<td class=act><button class=\"mini ha\" data-k=\"'+x.k+'\" data-n=\"'+esc(x.n||'')+'\">+</button></td></tr>'}"
  "$('#hl').innerHTML=h||'<tr><td>'+t.m_heardnone+'</td></tr>';"
  "h='';"
  "for(i=0;i<d.mon.length;i++){var m=d.mon[i];if(!hit(m,q))continue;"
  "h+='<tr><td>'+esc(m.n||'-')+'<br><small>'+m.k.substr(0,12)+'</small></td>'"
  "+'<td>'+monst(m,t)+'<br><small>'+m.oks+' '+t.m_reads+', '+m.pubs+' '+t.m_pubs"
  "+' / '+m.polls+' \\u00b7 '+ago(m.age,t)+'<br>'+t.m_kinds+': '+m.st+'/'+m.tl+'/'+m.nb"
  "+'</small></td>'"
  "+'<td><input type=password class=mp data-k=\"'+m.k+'\" placeholder=\"'"
  "+(m.pw?t.ph_unch:t.ph_pw)+'\"></td>'"
  "+'<td class=act><input type=checkbox class=\"ck me\" data-k=\"'+m.k+'\"'"
  "+(m.e?' checked':'')+'> <button class=\"mini md\" data-k=\"'+m.k+'\">\\u2715</button></td></tr>'}"
  "$('#ml').innerHTML=h||'<tr><td>'+t.m_none+'</td></tr>';"
  "$('#tr').textContent=(d.trace&&d.trace.length)?d.trace.join('\\n'):t.m_none;"
  "$('#miv').value=d.iv}"
  "function loadMon(){fetch('/api/mon').then(function(r){return r.json()})"
  ".then(function(d){mon=d;renderMon()})}"
  "function monPost(b,cb){fetch('/api/mon',{method:'POST',body:new URLSearchParams(b)})"
  ".then(function(r){return r.json()}).then(function(j){"
  "if(j.err=='key')alert(T[L].a_badkey.replace('%',mon?mon.minhex:12));"
  "if(cb)cb();loadMon()})}"
  "$('#fl').oninput=renderMon;"
  "document.addEventListener('click',function(e){var b=e.target;"
  "if(b.classList.contains('ha'))monPost({act:'add',key:b.dataset.k,name:b.dataset.n});"
  "else if(b.classList.contains('md'))monPost({act:'del',key:b.dataset.k})});"
  "document.addEventListener('change',function(e){var b=e.target;"
  "if(b.classList.contains('me'))monPost({act:'en',key:b.dataset.k,on:b.checked?1:''});"
  "else if(b.classList.contains('mp'))monPost({act:'pass',key:b.dataset.k,pass:b.value})});"
  "$('#mab').onclick=function(){monPost({act:'add',key:$('#mk').value,name:$('#mn').value},"
  "function(){$('#mk').value='';$('#mn').value=''})};"
  "$('#mivb').onclick=function(){monPost({act:'iv',secs:$('#miv').value})};"
  "$('#mpb').onclick=function(){monPost({act:'poll'})};"
  "function setPost(b){fetch('/api/settings',{method:'POST',body:new URLSearchParams(b)})"
  ".then(function(){setTimeout(load,500)})}"
  "$('#sivb').onclick=function(){setPost({iv:$('#siv').value})};"
  "$('#snow').onclick=function(){setPost({now:1})};"
  "function post(u,f,cb){fetch(u,{method:'POST',body:new URLSearchParams(new FormData(f))})"
  ".then(cb)}"
  "$('#th').onclick=function(){TH=TH=='light'?'dark':'light';"
  "localStorage.setItem('mstheme',TH);theme()};"
  "$('#lg').onclick=function(){L=L=='nl'?'en':'nl';localStorage.setItem('mslang',L);lang()};"
  "$('#f').onsubmit=function(e){e.preventDefault();post('/api/wifi',$('#f'),function(){"
  "$('#f').pass.value='';$('#f').ap_pass.value='';alert(T[L].a_saved)})};"
  "function rulespec(){var p=document.querySelectorAll('#rt .rp'),"
  "s=document.querySelectorAll('#rt .rs'),a=[],i;"
  "for(i=0;i<p.length;i++)a.push(p[i].value+':'+s[i].value);return a.join(',')}"
  "$('#p').onsubmit=function(e){e.preventDefault();"
  "var b=new URLSearchParams(new FormData($('#p')));b.set('rules',rulespec());"
  "fetch('/api/power',{method:'POST',body:b}).then(load)};"
  "$('#radd').onclick=function(){if(!last)return;"
  "last.rules.push({p:50,s:600});rules(last)};"
  "document.addEventListener('click',function(e){"
  "if(!e.target.classList.contains('rd')||!last)return;"
  "last.rules.splice(+e.target.dataset.i,1);rules(last)});"
  "$('#m').onsubmit=function(e){e.preventDefault();post('/api/mqtt',$('#m'),function(){"
  "$('#m').pass.value='';load()})};"
  "$('#r').onsubmit=function(e){e.preventDefault();var f=$('#r').f.files[0];"
  "if(!f){alert(T[L].a_pick);return}if(!confirm(T[L].a_conf))return;"
  "var b=new FormData();b.append('f',f);"
  "fetch('/api/restore',{method:'POST',body:b}).then(function(r){return r.json()})"
  ".then(function(j){alert(j.msg)})};"
  // ---- inklapbare secties -------------------------------------------------
  /* Het openklappen doet de browser; dit onthoudt alleen de stand. Dezelfde
   * sleutel als de publieke site (mcs-collapse:<naam>), zodat de twee pagina's
   * er hetzelfde over denken. Het verschil: de site slaat alleen 'dicht' op en
   * beschouwt afwezig als open, want daar staat alles standaard open. Hier
   * verschilt de standaard per sectie -- toestand open, de rest dicht -- dus
   * wordt de stand voluit bewaard en betekent 'niets bewaard' juist "gebruik
   * wat de firmware koos". */
  "document.querySelectorAll('details.sec').forEach(function(d){"
  "var k='mcs-collapse:'+d.dataset.ck,v=null;"
  "try{v=localStorage.getItem(k)}catch(e){}"
  "if(v!==null)d.open=v=='0';"
  "d.addEventListener('toggle',function(){"
  "try{localStorage.setItem(k,d.open?'0':'1')}catch(e){}})});"
  /* Vervangt %n door een waarde zonder dat de vervanging zelf nog betekenis
   * heeft. String.replace() met tekst als tweede argument kent '$&' en '$1', dus
   * een nodenaam of een kanaallabel met een dollarteken erin zou anders in de
   * melding iets anders worden dan het is. */
  "function rp(s,n,v){return s.replace('%'+n,function(){return v})}"
  "function opt(v,s,tx){return '<option value=\"'+v+'\"'+(v==s?' selected':'')+'>'"
  "+(tx||v)+'</option>'}"
  "function pad(n){return (n<10?'0':'')+n}"
  // ---- CLI-instellingen ---------------------------------------------------
  /* De lijst, de grenzen, de keuzelijsten en de risicoklassen komen alle uit
   * /api/cfg. Er staat hier met opzet geen tweede tabel: een parameter die deze
   * firmware niet kent kan niet aangeboden worden, en een grens die hier
   * afweek zou de losse van de twee zijn -- precies waar iemand op een knop
   * drukt. */
  "var cfg=null;"
  "function cfgIn(p){var v=p.val||'',a='data-in=\"'+p.key+'\"',i;"
  "if(p.kind=='bool')return '<select '+a+'>'+opt('on',v)+opt('off',v)+'</select>';"
  "if(p.kind=='enum'){var h='<select '+a+'>',c=(p.choices||'').split('|');"
  "for(i=0;i<c.length;i++)if(c[i])h+=opt(c[i],v);return h+'</select>'}"
  /* Vier velden en niet één tekstvak. 'get radio' antwoordt met komma's en 'set
   * radio' wil spaties, en één vak waarin je "869.525 250 11 5" moet overtypen
   * is het vak waarin een tikfout een node kwijtmaakt. De grenzen per veld komen
   * uit choices ("freq:150-2500|bw:7-500|..."), dus uit dezelfde tabel als de
   * controle in de firmware. */
  "if(p.kind=='radio'){var f=(p.choices||'').split('|'),"
  "g=v.replace(/,/g,' ').split(/\\s+/),h='<div class=rw>';"
  "for(i=0;i<f.length;i++){var nm=f[i].split(':')[0],b=(f[i].split(':')[1]||'0-0').split('-');"
  "h+='<input type=number '+a+' data-f='+i+' min='+b[0]+' max='+b[1]"
  "+(i>1?' step=1':' step=any')+' value=\"'+(g.length==4?esc(g[i]):'')+'\" title=\"'+nm"
  "+'\" placeholder=\"'+nm+'\">'}return h+'</div>'}"
  "if(p.kind=='int')return '<input type=number step=1 min='+p.lo+' max='+p.hi+' '+a"
  "+' value=\"'+esc(v)+'\">';"
  "if(p.kind=='float')return '<input type=number step=any min='+p.lo+' max='+p.hi+' '+a"
  "+' value=\"'+esc(v)+'\">';"
  /* Een geheim wordt nooit voorgevuld. De node stuurt het ook niet mee; wat er
   * staat blijft staan tot iemand hier iets nieuws intypt. */
  "if(p.secret)return '<input type=password maxlength=39 autocomplete=new-password '+a+'>';"
  "return '<input maxlength=39 autocomplete=off '+a+' value=\"'+esc(v)+'\">'}"
  /* Gegroepeerd op risicoklasse, want dat is wat de lezer moet zien vóórdat hij
   * kiest -- niet pas in het venster dat om een bevestiging vraagt. De kopjes en
   * de uitleg erboven zijn dezelfde drie als op de site. */
  "function renderCfg(){var d=cfg,t=T[L],h='',r,i;if(!d)return;"
  "if(!d.params){$('#cfgb').innerHTML='<p class=muted>'+t.c_load+'</p>';return}"
  "for(r=1;r<=3;r++){var g=d.params.filter(function(p){return (p.risk||1)==r});"
  "if(!g.length)continue;"
  "h+='<h3>'+t['c_g'+r]+'</h3><p class=muted>'+t['c_b'+r]+'</p><table class=cfg>'"
  "+'<tr><td>'+t.c_par+'</td><td>'+t.c_now+'</td><td>'+t.c_new+'</td><td></td></tr>';"
  "for(i=0;i<g.length;i++){var p=g[i];"
  "h+='<tr><td class=k>'+p.key+'</td><td class=muted>'"
  "+(p.secret?t.c_secret:(p.val?esc(p.val):t.c_none))+'</td><td>'+cfgIn(p)+'</td>'"
  "+'<td class=act><button class=\"mini cs'+(r==3?' dgr':'')+'\" data-k=\"'+p.key+'\">'"
  "+t.b_set+'</button></td></tr>'}h+='</table>'}"
  "$('#cfgb').innerHTML=h}"
  /* ?values=1 haalt er de huidige waarden bij. Bewust niet in een setInterval:
   * dit scherm heeft invoervelden, en een lijst die zichzelf ververst gooit weg
   * wat iemand half heeft ingetypt. Hij wordt geladen bij het openen en opnieuw
   * na elke schrijfactie -- dat zijn precies de twee momenten waarop hij kan
   * veranderd zijn. */
  "function loadCfg(){fetch('/api/cfg?values=1').then(function(r){return r.json()})"
  ".then(function(d){cfg=d;renderCfg()}).catch(function(){cfg={};renderCfg()})}"
  "function cfgVal(k,p){if(p.kind!='radio')return $('[data-in=\"'+k+'\"]').value;"
  "var e=document.querySelectorAll('[data-in=\"'+k+'\"]'),a=[],i;"
  "for(i=0;i<e.length;i++)a.push(e[i].value);return a.join(' ')}"
  /* De drempel hangt aan de risicoklasse uit de firmware, en de zwaarste vraagt
   * de naam van de node -- niet omdat het overtypen op zich iets bewijst, maar
   * omdat het de enige handeling is die niet per ongeluk lukt. Dit is een
   * drempel in de browser en niet in de node: POST /api/cfg kent geen
   * bevestigingsveld, want de server schrijft langs dezelfde weg en zou erover
   * struikelen. Wat de node wél doet is de waarde toetsen en teruglezen, en dat
   * is de controle die telt. */
  "function cfgSet(k){var t=T[L],p=null,i;if(!cfg||!cfg.params)return;"
  "for(i=0;i<cfg.params.length;i++)if(cfg.params[i].key==k)p=cfg.params[i];"
  "if(!p)return;var v=cfgVal(k,p),risk=p.risk||1,nm=last?last.name:'';"
  "if(risk==2){if(!confirm(rp(rp(t.a_c2,1,k),2,v)))return}"
  "else if(risk==3){var a=prompt(rp(rp(t.a_c3,1,k),2,v),'');"
  "if(a===null)return;if(a.trim()!==nm){alert(t.a_cno);return}}"
  "fetch('/api/cfg',{method:'POST',body:new URLSearchParams({key:k,value:v})})"
  ".then(function(r){return r.json()}).then(function(j){var m;"
  /* Wat er ná afloop in de node staat, en niet wat er gevraagd is. 'Niet
   * precies' is geen mislukking en krijgt daarom geen foutmelding maar de twee
   * waarden naast elkaar: MeshCore bewaart advert.interval als minuten/2, dus
   * 61 wordt 60, en dat is het gewone geval en geen storing. */
  "if(!j.ok)m='<span class=bad>'+rp(t.c_bad,1,esc(j.msg||j.reply||''))+'</span>';"
  "else if(j.exact)m='<span class=ok>'+rp(rp(t.c_ok,1,esc(j.key)),2,esc(j.applied))+'</span>';"
  "else m='<span class=bad>'+rp(rp(t.c_near,1,esc(j.asked)),2,esc(j.applied))+'</span>';"
  "if(j.ok&&p.reboot)m+='<br><span class=bad>'+t.c_rb+'</span>';"
  "$('#cfgn').innerHTML='<div class=\"card warn\">'+m+'</div>';loadCfg()})}"
  "document.addEventListener('click',function(e){"
  "if(e.target.classList.contains('cs'))cfgSet(e.target.dataset.k)});"
  // ---- pakketfilter -------------------------------------------------------
  /* Elke knop bewaart een functie in FA die pas bij het aanklikken zijn commando
   * samenstelt. Zo staat er nergens een tekstvak waarin een hele commandoregel
   * te typen valt -- dat zou een CLI op een webpagina zijn, en dan hangt de
   * risicoweging af van hoe iemand toevallig spelt -- en lezen de getalvelden
   * hun waarde op het moment dat het ertoe doet. */
  "var flt=null,FA=[];"
  "function fb(get,label,dgr){FA.push(get);"
  "return '<button class=\"mini fx'+(dgr?' dgr':'')+'\" data-i='+(FA.length-1)+'>'"
  "+label+'</button>'}"
  /* Staat er al een regel die een hele categorie dichtzet, dan is 'filter aan'
   * geen gewone handeling meer maar het scherpstellen ervan. De zwaarte van een
   * handeling hangt af van waar hij bovenop komt. */
  "function fltBlank(){var f=flt,i;if(!f||!f.types)return false;"
  "if(f.hash>=3)return true;"
  "for(i=0;i<f.types.length;i++)if(!f.types[i].on||f.types[i].hops==0)return true;"
  "return false}"
  "function fltRow(y,t){var id=pad(y.id),nm=y.name,w=nm+' ('+id+')';"
  "return '<tr><td class=k>'+id+' '+nm+'</td><td class=nw>'"
  "+(y.on?t.f_yes:'<b class=bad>'+t.f_no+'</b>')+' '"
  "+fb(function(){return {c:'type '+id+(y.on?' off':' on'),"
  "w:rp(y.on?t.q_tyof:t.q_tyon,1,w),r:y.on?3:1}},y.on?t.f_close:t.f_open,y.on)+'</td>'"
  "+'<td><div class=rw><input type=number min=0 max=63 value='+y.hops+' data-h=\"'+id+'\">'"
  "+fb(function(){var v=$('[data-h=\"'+id+'\"]').value;"
  "return {c:'hops '+id+' '+v,w:v=='0'?rp(t.q_hop0,1,w):rp(rp(t.q_hop,1,w),2,v),"
  "r:v=='0'?3:2}},t.f_hops,false)+'</div></td>'"
  "+'<td><div class=rw><input type=number min=0 max=65535 value='+y.limit+' data-rl=\"'+id+'\">'"
  "+'<input type=number min=1 max=3600 value='+y.window+' data-rw=\"'+id+'\">'"
  "+fb(function(){var l=$('[data-rl=\"'+id+'\"]').value,q=$('[data-rw=\"'+id+'\"]').value;"
  "return {c:'rate '+id+' '+l+' '+q,"
  "w:l=='0'?rp(t.q_rt0,1,w):rp(rp(rp(t.q_rt,1,w),2,l),3,q),r:l=='0'?1:2}},"
  "t.f_rate,false)+'</div></td>'"
  "+'<td>'+y.drop+'</td></tr>'}"
  "function renderFlt(){var f=flt,t=T[L],h='',i;if(!f)return;"
  "if(!f.types){$('#fltb').innerHTML='<p class=muted>'+t.f_load+'</p>';return}"
  "FA=[];"
  /* Uit en terugzetten vragen niets, aanzetten wel. Dat is geen slordigheid in
   * de indeling maar het punt ervan: herstel mag nooit strakker afgeschermd zijn
   * dan de fout die het terugdraait. */
  "h+='<h3>'+t.f_onoff+'</h3><p class=muted>'+t.f_swblurb+'</p><p>'"
  "+fb(function(){return {c:'off',w:t.q_off,r:1}},t.f_boff,false)+' '"
  "+fb(function(){return {c:'reset',w:t.q_rst,r:1}},t.f_brst,false)+' '"
  "+fb(function(){return {c:'on',w:t.q_on,r:fltBlank()?3:2}},t.f_bon,true)+'</p>'"
  "+'<p class=muted>'+rp(rp(rp(t.f_now,1,'<b>'+(f.on?t.f_on:t.f_off)+'</b>'),2,f.hash),"
  "3,f.malformed?t.f_on:t.f_off)+'</p>'"
  "+(f.disarmed?'<p class=bad>'+t.f_disarm+'</p>':'')"
  "+'<p class=muted>'+t.f_back+'</p>';"
  "h+='<h3>'+t.f_rules+'</h3><table class=cfg>'"
  "+'<tr><td class=k>'+t.f_hash+'</td><td><select data-x=hash>'"
  "+opt(1,f.hash,t.f_h1)+opt(2,f.hash,t.f_h2)+opt(3,f.hash,t.f_h3)+'</select></td>'"
  "+'<td class=act>'+fb(function(){var v=$('[data-x=hash]').value;"
  "return {c:'hash '+v,w:rp(t.q_hsh,1,v),r:v=='1'?1:(v=='3'?3:2)}},t.b_set,false)+'</td></tr>'"
  "+'<tr><td class=k>'+t.f_mal+'</td><td><select data-x=mal>'"
  "+opt('on',f.malformed?'on':'off',t.f_on)+opt('off',f.malformed?'on':'off',t.f_off)"
  "+'</select></td><td class=act>'+fb(function(){var v=$('[data-x=mal]').value;"
  "return {c:'malformed '+v,w:v=='on'?t.q_mlon:t.q_mlof,r:v=='on'?2:1}},t.b_set,false)"
  "+'</td></tr></table><p class=muted>'+t.f_malb+'</p>';"
  "h+='<h3>'+t.f_types+'</h3><p class=muted>'+t.f_tb+'</p><table class=cfg>'"
  "+'<tr><td>'+t.f_ty+'</td><td>'+t.f_thr+'</td><td>'+t.f_hops+'</td><td>'+t.f_rate"
  "+'</td><td>'+t.f_drop+'</td></tr>';"
  "for(i=0;i<f.types.length;i++)h+=fltRow(f.types[i],t);"
  "h+='</table>';"
  "h+='<h3>'+t.f_chan+'</h3><p class=muted>'+t.f_cb+'</p><table class=cfg>';"
  "if(!f.channels.length)h+='<tr><td class=muted>'+t.f_cnone+'</td></tr>';"
  "for(i=0;i<f.channels.length;i++)h+=(function(c){"
  "return '<tr><td class=k>'+esc(c.label)+'</td><td class=muted>#'+c.hash+'</td>'"
  "+'<td class=act>'+fb(function(){return {c:'channel remove '+c.label,"
  "w:rp(t.q_crem,1,c.label),r:1}},t.f_crem,false)+'</td></tr>'})(f.channels[i]);"
  "h+='</table><div class=rw style=margin-top:.6rem>"
  "<input data-x=clab maxlength=23 placeholder=\"'+t.f_clab+'\">"
  "<input data-x=cpsk maxlength=64 placeholder=\"'+t.f_cpsk+'\">'"
  "+fb(function(){var l=$('[data-x=clab]').value,k=$('[data-x=cpsk]').value;"
  "if(!l||!k)return null;return {c:'channel add '+l+' '+k,w:rp(t.q_cadd,1,l),r:2}},"
  "t.f_cadd,false)+'</div>';"
  "h+='<h3>'+t.f_cnt+'</h3><table class=cfg><tr><td>'+t.f_pass+'</td><td>'+f.passed"
  "+'</td></tr><tr><td>'+t.f_ex+'</td><td>'+f.exempt+'</td></tr></table>';"
  "var any=false,dh='',kk;"
  "for(kk in f.drop){if(!f.drop[kk])continue;any=true;"
  "dh+='<tr><td>'+(t['r_'+kk]||kk)+'</td><td>'+f.drop[kk]+'</td></tr>'}"
  "h+='<h3>'+t.f_dropped+'</h3>'+(any?'<table class=cfg>'+dh+'</table>'"
  ":'<p class=muted>'+t.f_nodrop+'</p>');"
  "$('#fltb').innerHTML=h}"
  "function loadFlt(){fetch('/api/filter').then(function(r){return r.json()})"
  ".then(function(d){flt=d;renderFlt()}).catch(function(){flt={};renderFlt()})}"
  /* Het antwoord draagt de stand ná afloop mee, dus die wordt gebruikt in plaats
   * van er nog eens om te vragen: 'filter hops 05 0' wordt keurig aangenomen en
   * betekent "stuur geen groepstekst meer door", en dat hoort op het scherm te
   * staan in de vorm waarin het gehandhaafd wordt. */
  "function fltDo(cmd,what,risk){var t=T[L],nm=last?last.name:'';"
  "if(risk==2){if(!confirm(rp(t.a_f2,1,what)))return}"
  "else if(risk==3){var a=prompt(rp(t.a_f3,1,what),'');"
  "if(a===null)return;if(a.trim()!==nm){alert(t.a_cno);return}}"
  "fetch('/api/filter',{method:'POST',body:new URLSearchParams({cmd:cmd})})"
  ".then(function(r){return r.json()}).then(function(j){"
  "$('#fltn').innerHTML='<div class=\"card warn\">'"
  "+(j.ok?rp(t.f_okm,1,what)+'<br><small>'+esc(j.msg||'')+'</small>'"
  ":'<span class=bad>'+rp(t.f_badm,1,esc(j.msg||''))+'</span>')+'</div>';"
  "if(j.state){flt=j.state;renderFlt()}else loadFlt()})}"
  "document.addEventListener('click',function(e){var b=e.target;"
  "if(!b.classList.contains('fx'))return;var g=FA[+b.dataset.i];if(!g)return;"
  "var a=g();if(a)fltDo(a.c,a.w,a.r)});"
  "theme();lang();load();loadMon();loadCfg();loadFlt();"
  "setInterval(load,5000);setInterval(loadMon,20000);"
  "</script></body></html>";

/* The admin page hands out your keys (backup) and can flash firmware. That may
 * not sit open on your network without a login. */
static bool requireAuth(AsyncWebServerRequest *req) {
  if (req->authenticate(_cfg.user, _cfg.console_pass)) return true;
  req->requestAuthentication();
  return false;
}

/* Values and codes only -- no finished sentences. The page renders them in the
 * reader's language, which is also why the battery arrives as millivolts,
 * percentage and level rather than as a formatted string. */
static void handleStatus(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  /* Grew when the rules table and the settings joined, and again for escaping:
   * six fields below can now double in length, and the clamp further down turns
   * an overflow into a truncated answer rather than a wrong one -- which is
   * still a page that shows nothing. 300 bytes of static RAM is the cheaper
   * side of that trade. */
  static char body[2900];
  IPAddress ip = (_state == WIFI_FALLBACK_AP) ? WiFi.softAPIP() : WiFi.localIP();

  // "%.1f" of a NAN prints 'nan', which is not JSON and would blank the page.
  float mcu_t = board.getMCUTemperature();
  if (isnan(mcu_t)) mcu_t = -999.0f;

  /* Six fields here are text somebody chose: the node name, the network name
   * we are on or offering, and four broker settings. All six are typed by a
   * person -- and an SSID may legally contain a quote or a backslash, which is
   * the case that turns this page blank rather than ugly: the JSON never
   * parses, the fetch fails, and the page you would use to correct the setting
   * is the page the setting broke. Passwords are absent from this answer on
   * purpose and therefore need nothing.
   *
   * Static rather than on the stack, for the same reason body[] above is:
   * AsyncWebServer runs this on its own task, one request at a time, and that
   * task's stack is among the tightest on the node. Twice the source length is
   * the worst case for jsonEsc(), a value made entirely of quotes. */
  static char e_name[NODE_NAME_MAX * 2], e_ssid[SSID_MAX * 2], e_net[SSID_MAX * 2],
              e_host[MQTT_HOST_MAX * 2], e_user[MQTT_USER_MAX * 2],
              e_prefix[MQTT_PREFIX_MAX * 2];

  jsonEsc(e_name, sizeof(e_name), _mesh ? _mesh->getNodeName() : "repeater");
  jsonEsc(e_ssid, sizeof(e_ssid), _cfg.ssid);
  jsonEsc(e_net, sizeof(e_net), _state == WIFI_FALLBACK_AP ? _ap_ssid : _cfg.ssid);
  jsonEsc(e_host, sizeof(e_host), _cfg.mqtt_host);
  jsonEsc(e_user, sizeof(e_user), _cfg.mqtt_user);
  jsonEsc(e_prefix, sizeof(e_prefix), _cfg.mqtt_prefix);

  int n = snprintf(body, sizeof(body),
    "{\"name\":\"%s\",\"node\":\"%s\",\"board\":\"%s\",\"fw\":\"%s\","
    "\"ms\":\"%s v%s\",\"env\":\"%s\",\"ssid\":\"%s\",\"safe\":%d,"
    "\"wifi\":{\"st\":\"%s\",\"ip\":\"%s\",\"net\":\"%s\",\"rssi\":%d,"
    "\"up\":%lu,\"heap\":%u},"
    "\"bat\":{\"known\":%d,\"mv\":%u,\"pct\":%u,\"lv\":%u},\"wdt\":%d,\"wdt_s\":%d,"
    "\"mcu_t\":%.1f,"
    "\"pwr\":{\"st\":\"%s\",\"secs\":%u,\"iv\":%u,\"night\":%d,"
    "\"mode\":%u,\"window\":%u,\"sleep\":%u,\"min\":%u,\"rule\":%u},"
    "\"live\":\"%s\",\"livepct\":%u,",
    e_name, _node_hex,
    board.getManufacturerName(), FIRMWARE_VERSION,
    MESHMANAGER_NAME, MESHMANAGER_VERSION, MESHMANAGER_ENV,
    e_ssid, _safe_mode ? 1 : 0,
    wifiStateCode(), ip.toString().c_str(),
    e_net,
    (int)WiFi.RSSI(), (unsigned long)(millis() / 60000UL), (unsigned)ESP.getFreeHeap(),
    _batt_known ? 1 : 0, (unsigned)_batt_mv, (unsigned)_batt_pct, (unsigned)_level,
    _wdt_watching ? 1 : 0, WDT_TIMEOUT_S, mcu_t,
    powerStateCode(), (unsigned)powerSecsLeft(), (unsigned)currentIntervalSecs(),
    isNight() ? 1 : 0, _cfg.pwr_mode, _cfg.pwr_window, _cfg.wifi_sleep,
    (unsigned)pwrMinInterval(), (unsigned)_level,
    liveCode(), _cfg.bat_live);

  // Truncation here would ship broken JSON, so clamp before appending.
  if (n < 0 || (size_t)n >= sizeof(body)) n = sizeof(body) - 1;

  snprintf(body + n, sizeof(body) - n,
    "\"mqtt\":{\"host\":\"%s\",\"port\":%u,\"user\":\"%s\",\"prefix\":\"%s\","
    "\"enabled\":%u,\"rx\":%u,\"st\":\"%s\",\"stats\":%u,\"pkt\":%u,\"drop\":%u,"
    "\"queue\":%u,\"fail\":%u,\"err\":\"%s\",\"rc\":%d}}",
    e_host, _cfg.mqtt_port, e_user, e_prefix,
    _cfg.mqtt_enabled, _cfg.mqtt_rx,
    !_cfg.mqtt_enabled ? "off" : (_mqtt.connected() ? "conn"
      : (_cfg.mqtt_host[0] ? "disc" : "unset")),
    (unsigned)_stats_count, (unsigned)_rx_count, (unsigned)_drop_count,
    (unsigned)((_rx_head - _rx_tail + MQTT_RX_QUEUE) % MQTT_RX_QUEUE),
    (unsigned)_fail_count, _mqtt_err, _mqtt_err_rc);

  int q = (int)strlen(body);
  q -= 1;                                   // step back over the closing brace
  q += snprintf(body + q, sizeof(body) - q, ",\"rules\":[");
  for (int i = 0; i < _pwr_n && q < (int)sizeof(body) - 40; i++) {
    q += snprintf(body + q, sizeof(body) - q, "%s{\"p\":%u,\"s\":%u}",
                  i ? "," : "", _pwr[i].pct, _pwr[i].secs);
  }

  /* The CLI sweep, readable at any moment. 'age' is seconds since the last one
   * finished (-1 = never run), 'next' seconds until the following one, and the
   * values themselves so nobody has to catch the one message in 1440 that
   * carries them. */
  q += snprintf(body + q, sizeof(body) - q,
                "],\"set\":{\"ok\":%d,\"miss\":%d,\"age\":%ld,\"next\":%u,"
                "\"iv\":%u,\"busy\":%d,\"v\":{",
                _set_n, _set_miss,
                _set_done_at ? (long)((millis() - _set_done_at) / 1000UL) : -1L,
                (unsigned)settingsNextIn(), (unsigned)_cfg.set_iv_min,
                _set_next >= 0 ? 1 : 0);

  for (int i = 0; i < _set_n && q < (int)sizeof(body) - 90; i++) {
    char esc[SET_VALUE_MAX * 2 + 4];
    jsonEsc(esc, sizeof(esc), _set_vals[i].value);
    q += snprintf(body + q, sizeof(body) - q, "%s\"%s\":\"%s\"",
                  i ? "," : "", _set_vals[i].name, esc);
  }
  snprintf(body + q, sizeof(body) - q, "}}}");

  req->send(200, "application/json", body);
}

// Empty password fields mean 'leave it alone'; otherwise visiting the page
// would wipe a password you cannot see.
static void copyParam(AsyncWebServerRequest *req, const char *name, char *out, size_t max) {
  if (!req->hasParam(name, true)) return;
  strncpy(out, req->getParam(name, true)->value().c_str(), max - 1);
  out[max - 1] = 0;
}

static uint16_t paramNum(AsyncWebServerRequest *req, const char *name, uint16_t fallback,
                         uint16_t lo, uint16_t hi) {
  if (!req->hasParam(name, true)) return fallback;
  long v = req->getParam(name, true)->value().toInt();
  if (v < lo || v > hi) return fallback;
  return (uint16_t)v;
}

static void handleWifiPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  copyParam(req, "ssid", _cfg.ssid, SSID_MAX);
  if (req->hasParam("pass", true) && req->getParam("pass", true)->value().length() > 0) {
    copyParam(req, "pass", _cfg.pass, PASS_MAX);
  }
  if (req->hasParam("ap_pass", true) && req->getParam("ap_pass", true)->value().length() >= 8) {
    copyParam(req, "ap_pass", _cfg.ap_pass, PASS_MAX);
  }
  _apply_wifi = true;      // saving and reconnecting happens in loop()
  req->send(200, "application/json", "{\"ok\":1}");
}

static void handlePowerPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  _cfg.pwr_mode = paramNum(req, "mode", _cfg.pwr_mode, 0, 1);
  _cfg.pwr_window = paramNum(req, "window", _cfg.pwr_window, 30, 3600);
  _cfg.wifi_sleep = req->hasParam("sleep", true) ? 1 : 0;

  /* The whole table arrives as one "pct:secs,..." string. Replacing it in one
   * go keeps the page and the CLI on the same footing, and avoids a half
   * applied table if the browser gives up between two row updates. */
  if (req->hasParam("rules", true)) {
    String spec = req->getParam("rules", true)->value();
    PwrRule tmp[PWR_RULES_MAX];
    int n = 0, i = 0;
    while (i < (int)spec.length() && n < PWR_RULES_MAX) {
      while (i < (int)spec.length() && (spec[i] == ',' || spec[i] == ' ')) i++;
      int colon = spec.indexOf(':', i);
      if (colon < 0) break;
      long pct = spec.substring(i, colon).toInt();
      long secs = spec.substring(colon + 1).toInt();
      if (pct >= 0 && pct <= 100 && secs >= 1 && secs <= 65535) {
        tmp[n].pct = (uint8_t)pct;
        tmp[n].secs = (uint16_t)secs;
        n++;
      }
      int comma = spec.indexOf(',', i);
      if (comma < 0) break;
      i = comma + 1;
    }
    if (n > 0) {
      memcpy(_pwr, tmp, sizeof(PwrRule) * n);
      _pwr_n = n;
      pwrNormalise();
      _apply_rules = true;
    }
  }
  _apply_power = true;
  req->send(200, "application/json", "{\"ok\":1}");
}

/* Forces a sweep, and sets the interval. Both deferred to loop() like every
 * other change: the sweep calls into the mesh CLI, which is not the web
 * server's task to be doing. */
static void handleSettingsPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  if (req->hasParam("iv", true)) {
    long v = req->getParam("iv", true)->value().toInt();   // minutes
    if (v < 5 || v > 65535) {
      req->send(200, "application/json", "{\"ok\":0,\"err\":\"range\"}");
      return;
    }
    _cfg.set_iv_min = (uint16_t)v;
    _apply_power = true;                    // reuses the deferred saveConfig()
  }
  if (req->hasParam("now", true)) _set_force = true;
  req->send(200, "application/json", "{\"ok\":1}");
}

static void handleMqttPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  copyParam(req, "host", _cfg.mqtt_host, MQTT_HOST_MAX);
  copyParam(req, "user", _cfg.mqtt_user, MQTT_USER_MAX);
  copyParam(req, "prefix", _cfg.mqtt_prefix, MQTT_PREFIX_MAX);
  if (_cfg.mqtt_prefix[0] == 0) strcpy(_cfg.mqtt_prefix, MQTT_PREFIX_DEFAULT);
  if (req->hasParam("pass", true) && req->getParam("pass", true)->value().length() > 0) {
    copyParam(req, "pass", _cfg.mqtt_pass, PASS_MAX);
  }
  _cfg.mqtt_port = paramNum(req, "port", _cfg.mqtt_port, 1, 65535);
  _cfg.mqtt_enabled = req->hasParam("enabled", true) ? 1 : 0;
  _cfg.mqtt_rx = req->hasParam("rx", true) ? 1 : 0;
  _apply_mqtt = true;
  req->send(200, "application/json", "{\"ok\":1}");
}

/* Monitor list plus the heard repeaters to pick from, in one fetch. Passwords
 * never leave the node: only whether one is set, because "no password" is a
 * meaningful setting here and the page has to be able to show which of the two
 * you chose. */
static void handleMonJson(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  static char body[6500];
  char esc[MON_TRACE_LEN * 2 + 4];   // also used for trace lines, the longest
  int p = 0;

  /* 'next' and 'state' exist because the scheduler stalling was invisible from
   * outside: every counter read zero, which looks identical to 'never
   * configured'. Now a round that is not coming can be seen without waiting
   * for one. */
  p += snprintf(body + p, sizeof(body) - p,
                "{\"iv\":%u,\"max\":%d,\"minhex\":%d,\"next\":%ld,\"state\":%d,\"mon\":[",
                _mon_interval, MAX_MONITORS, MON_MIN_HEX,
                _mon_state == MST_IDLE ? (long)secsLeft(_mon_next_round) : -1L,
                (int)_mon_state);

  for (int i = 0; i < _mon_count && p < (int)sizeof(body) - 400; i++) {
    jsonEsc(esc, sizeof(esc), _mon[i].name);
    long age = _mon[i].last_ok ? (long)((millis() - _mon[i].last_ok) / 1000UL) : -1;
    p += snprintf(body + p, sizeof(body) - p,
      "%s{\"k\":\"%s\",\"n\":\"%s\",\"pw\":%d,\"e\":%d,\"res\":%d,\"lr\":%u,"
      "\"polls\":%u,\"oks\":%u,\"pubs\":%u,\"st\":%u,\"tl\":%u,\"nb\":%u,\"age\":%ld}",
      i ? "," : "", _mon[i].key, esc, _mon[i].pass[0] ? 1 : 0,
      _mon[i].enabled ? 1 : 0, _mon[i].mesh_idx >= 0 ? 1 : 0,
      (unsigned)_mon[i].login_res, (unsigned)_mon[i].polls, (unsigned)_mon[i].oks,
      (unsigned)_mon[i].pubs, (unsigned)_mon[i].ok_st, (unsigned)_mon[i].ok_tl,
      (unsigned)_mon[i].ok_nb, age);
  }

  p += snprintf(body + p, sizeof(body) - p, "],\"heard\":[");

  int n_heard = _mesh ? _mesh->getNumNeighbours() : 0;
  if (n_heard > MON_HEARD_MAX) n_heard = MON_HEARD_MAX;
  int written = 0;
  for (int i = 0; i < n_heard && p < (int)sizeof(body) - 200; i++) {
    char hex[PUB_KEY_SIZE * 2 + 1], name[MON_NAME_MAX];
    uint32_t secs_ago;
    int8_t snr4;
    if (!_mesh->getNeighbourAt(i, hex, name, sizeof(name), &secs_ago, &snr4)) continue;

    // Names in neighbours[] are lost on restart; the advert cache is not.
    if (name[0] == 0) {
      uint8_t key[PUB_KEY_SIZE];
      if (mesh::Utils::fromHex(key, PUB_KEY_SIZE, hex)) {
        const char *cached = meshmanager_advert_name(key, PUB_KEY_SIZE);
        if (cached) {
          strncpy(name, cached, sizeof(name) - 1);
          name[sizeof(name) - 1] = 0;
        }
      }
    }
    jsonEsc(esc, sizeof(esc), name);
    p += snprintf(body + p, sizeof(body) - p,
                  "%s{\"k\":\"%s\",\"n\":\"%s\",\"snr\":%.2f,\"age\":%u}",
                  written ? "," : "", hex, esc, snr4 / 4.0f, (unsigned)secs_ago);
    written++;
  }

  /* Then the repeaters we only know from stored adverts. After a restart
   * neighbours[] is empty, so without these the list a user picks from would
   * be blank until every node happens to advertise again. They carry no SNR --
   * we have not heard them this run, and inventing one would be a lie about
   * whether they are reachable right now. */
  uint32_t now = 0;
  {
    uint8_t h;
    if (_mesh && _mesh->getClockHour(&h)) now = _mesh->getRTCClock()->getCurrentTime();
  }
  for (int i = 0; i < _adv_count && p < (int)sizeof(body) - 200; i++) {
    if (_adv[i].type != ADV_TYPE_REPEATER) continue;

    uint8_t full[PUB_KEY_SIZE];
    char nm[MON_NAME_MAX];
    if (_mesh && _mesh->findNeighbourByPrefix(_adv[i].key, PUB_KEY_SIZE, full, nm, sizeof(nm))) {
      continue;                      // already listed above, with a live SNR
    }
    char hex[PUB_KEY_SIZE * 2 + 1];
    mesh::Utils::toHex(hex, _adv[i].key, PUB_KEY_SIZE);
    jsonEsc(esc, sizeof(esc), _adv[i].name);

    long age = (now && _adv[i].heard && now > _adv[i].heard)
               ? (long)(now - _adv[i].heard) : -1;
    p += snprintf(body + p, sizeof(body) - p,
                  "%s{\"k\":\"%s\",\"n\":\"%s\",\"age\":%ld,\"cached\":1}",
                  written ? "," : "", hex, esc, age);
    written++;
  }

  // Oldest first, so the page reads top-to-bottom like a log.
  p += snprintf(body + p, sizeof(body) - p, "],\"trace\":[");
  uint32_t first = (_mon_trace_n > MON_TRACE_LINES) ? _mon_trace_n - MON_TRACE_LINES : 0;
  written = 0;
  for (uint32_t i = first; i < _mon_trace_n && p < (int)sizeof(body) - 120; i++) {
    jsonEsc(esc, sizeof(esc), _mon_trace[i % MON_TRACE_LINES]);
    p += snprintf(body + p, sizeof(body) - p, "%s\"%s\"", written ? "," : "", esc);
    written++;
  }
  snprintf(body + p, sizeof(body) - p, "]}");

  req->send(200, "application/json", body);
}

static void handleMonPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  if (_mon_action != MA_NONE) {          // previous change not applied yet
    req->send(200, "application/json", "{\"ok\":0,\"err\":\"busy\"}");
    return;
  }

  String act = req->hasParam("act", true) ? req->getParam("act", true)->value() : "";
  _ma_key[0] = _ma_name[0] = _ma_pass[0] = 0;
  _ma_num = 0;

  if (act == "iv") {
    long v = req->hasParam("secs", true) ? req->getParam("secs", true)->value().toInt() : 0;
    if (v < 60 || v > 65535) {
      req->send(200, "application/json", "{\"ok\":0,\"err\":\"range\"}");
      return;
    }
    _ma_num = (uint16_t)v;
    _mon_action = MA_INTERVAL;
    req->send(200, "application/json", "{\"ok\":1}");
    return;
  }
  if (act == "poll") {
    _mon_action = MA_POLL;
    req->send(200, "application/json", "{\"ok\":1}");
    return;
  }

  copyParam(req, "key", _ma_key, sizeof(_ma_key));
  /* Validated here rather than in loop(), because a mistyped key is the one
   * error worth telling the user about immediately. */
  if (!normaliseKey(_ma_key)) {
    req->send(200, "application/json", "{\"ok\":0,\"err\":\"key\"}");
    return;
  }

  if (act == "add") {
    copyParam(req, "name", _ma_name, sizeof(_ma_name));
    _mon_action = MA_ADD;
  } else if (act == "del") {
    _mon_action = MA_DEL;
  } else if (act == "pass") {
    // An empty password is a choice, not a missing field: it means 'try their
    // access list'. So no length check here.
    copyParam(req, "pass", _ma_pass, sizeof(_ma_pass));
    _mon_action = MA_PASS;
  } else if (act == "en") {
    _ma_num = req->hasParam("on", true) ? 1 : 0;
    _mon_action = MA_ENABLE;
  } else {
    req->send(200, "application/json", "{\"ok\":0,\"err\":\"act\"}");
    return;
  }
  req->send(200, "application/json", "{\"ok\":1}");
}

// ------------------------------------------------------------ backup/restore

/* Format, deliberately line-based so we never have to hold a whole file in
 * memory:
 *
 *   MESHMANAGER-BACKUP 1
 *   FILE /identity 64
 *   <hex, 64 bytes per line>
 *   ...
 *   END
 *
 * This contains everything in the file system: your key pair, the repeater
 * prefs, the ACL and the network settings. Keep such a backup safe -- whoever
 * has it, has your node's identity.
 */
/* De kopregel van een backupbestand. Die van voor de hernoeming wordt bij
 * het terugzetten nog steeds aanvaard -- zie restoreBackupFile(). */
#define BACKUP_MAGIC         "MESHMANAGER"
#define LEGACY_BACKUP_MAGIC  "MESHSTATS"

#define BACKUP_FILE   "/backup.mcb"
#define RESTORE_FILE  "/restore.mcb"
#define HEX_PER_LINE  64

static bool skipInBackup(const char *name) {
  return strcmp(name, BACKUP_FILE) == 0 || strcmp(name, RESTORE_FILE) == 0 ||
         strcmp(name, MMNET_BOOT_FILE) == 0;
}

static bool writeBackupFile() {
  if (!_fs) return false;
  File out = _fs->open(BACKUP_FILE, "w");
  if (!out) return false;

  out.print(BACKUP_MAGIC "-BACKUP 1\n");

  File dir = _fs->open("/");
  File f = dir.openNextFile();
  uint8_t buf[HEX_PER_LINE];
  char hex[HEX_PER_LINE * 2 + 2];

  while (f) {
    // f.name() returns with or without a leading slash depending on core version
    String path = f.name();
    if (!path.startsWith("/")) path = "/" + path;

    if (!f.isDirectory() && !skipInBackup(path.c_str())) {
      out.printf("FILE %s %u\n", path.c_str(), (unsigned)f.size());
      int n;
      while ((n = f.read(buf, sizeof(buf))) > 0) {
        int p = 0;
        for (int i = 0; i < n; i++) {
          hex[p++] = HEXCHARS[buf[i] >> 4];
          hex[p++] = HEXCHARS[buf[i] & 0x0F];
        }
        hex[p++] = '\n';
        hex[p] = 0;
        out.print(hex);
      }
    }
    f = dir.openNextFile();
  }
  out.print("END\n");
  out.close();
  return true;
}

static void handleBackup(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  if (!writeBackupFile()) {
    req->send(500, "text/plain", "backup mislukt");
    return;
  }
  char fname[64];
  snprintf(fname, sizeof(fname), "meshmanager-%s.mcb", _node_hex);

  AsyncWebServerResponse *res = req->beginResponse(*_fs, BACKUP_FILE, "application/octet-stream");
  res->addHeader("Content-Disposition", String("attachment; filename=\"") + fname + "\"");
  req->send(res);
}

static uint8_t hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return 0xFF;
}

/* Reads the uploaded file back line by line and writes the files out. Only
 * once everything succeeded do we restart; if anything goes wrong the node is
 * still exactly as it was. */
static bool applyRestore(char *err, size_t err_max) {
  File in = _fs->open(RESTORE_FILE, "r");
  if (!in) { snprintf(err, err_max, "geen bestand ontvangen"); return false; }

  char line[HEX_PER_LINE * 2 + 8];
  size_t len = in.readBytesUntil('\n', line, sizeof(line) - 1);
  line[len] = 0;
  /* Allebei de kopregels aanvaarden, en dat is geen beleefdheid: een backup
   * die iemand vorige maand maakte draagt de oude naam, en zo'n bestand
   * bevat het sleutelpaar van een node op een dak. Firmware die haar eigen
   * vorige backups weigert, ontdek je op de slechtst denkbare dag.
   *
   * Weg te halen zodra de oude naam nergens meer op een schijf staat -- wat
   * je niet kunt weten. Praktisch: laten staan. Zestien bytes vergelijken
   * kost niets; het alternatief is onherstelbaar. */
  if (strncmp(line, BACKUP_MAGIC "-BACKUP",
              sizeof(BACKUP_MAGIC "-BACKUP") - 1) != 0 &&
      strncmp(line, LEGACY_BACKUP_MAGIC "-BACKUP",
              sizeof(LEGACY_BACKUP_MAGIC "-BACKUP") - 1) != 0) {
    in.close();
    snprintf(err, err_max, "dit is geen backupbestand");
    return false;
  }

  File out;
  uint32_t remaining = 0;
  int files = 0;
  uint8_t buf[HEX_PER_LINE];

  while (in.available()) {
    len = in.readBytesUntil('\n', line, sizeof(line) - 1);
    line[len] = 0;
    if (len && line[len - 1] == '\r') line[--len] = 0;
    if (len == 0) continue;

    if (strncmp(line, "FILE ", 5) == 0) {
      if (out) out.close();
      char *sp = strrchr(line + 5, ' ');
      if (!sp) { in.close(); snprintf(err, err_max, "onleesbare regel"); return false; }
      *sp = 0;
      remaining = (uint32_t)atol(sp + 1);
      out = _fs->open(line + 5, "w");
      if (!out) { in.close(); snprintf(err, err_max, "kan %s niet schrijven", line + 5); return false; }
      files++;
    } else if (strncmp(line, "END", 3) == 0) {
      break;
    } else if (out) {
      size_t n = len / 2;
      if (n > sizeof(buf)) n = sizeof(buf);
      for (size_t i = 0; i < n; i++) {
        uint8_t hi = hexVal(line[i * 2]), lo = hexVal(line[i * 2 + 1]);
        if (hi == 0xFF || lo == 0xFF) { in.close(); out.close();
          snprintf(err, err_max, "beschadigde inhoud"); return false; }
        buf[i] = (hi << 4) | lo;
      }
      if (n > remaining) n = remaining;
      out.write(buf, n);
      remaining -= n;
    }
  }
  if (out) out.close();
  in.close();
  _fs->remove(RESTORE_FILE);

  if (files == 0) { snprintf(err, err_max, "backup bevatte geen bestanden"); return false; }
  snprintf(err, err_max, "%d bestanden teruggezet", files);
  return true;
}

static volatile bool _reboot_pending = false;
static unsigned long _reboot_at = 0;

// -------------------------------------------------------- firmware upgrade

/* A second way to write firmware, beside the /update page that AsyncElegantOTA
 * puts on this same server. Both stay, and the reason both stay is at the
 * bottom of this comment.
 *
 * Why a second one exists at all. The elegant path has three properties which
 * together make a failed upgrade indistinguishable from a successful one:
 *
 *  - It parses a multipart form and recognises the image only under the field
 *    name 'file', with an MD5 field beside it. Send the same bytes as
 *    'update=@firmware.bin' and 1.284.538 of them travel, are accepted, and are
 *    thrown away. Measured on this hardware, not deduced from the source.
 *  - Its handler restarts the node whether Update.end() succeeded or not. So
 *    "the node rebooted" -- the one signal a caller gets -- carries no
 *    information at all. It reboots just as promptly after writing nothing.
 *  - That restart happens before the HTTP response is flushed, so curl reports
 *    000 and there is nothing to read. The node then comes back on the OLD
 *    firmware, looking exactly like a node that came back on the new one.
 *
 * An upgrade path that lies by omission, on a repeater that hangs on a roof.
 * Hence this endpoint, built on the opposite rule: nothing becomes definitive
 * before it has been checked, and the caller always gets a sentence naming the
 * step that failed.
 *
 * No multipart. The image is the raw request body; everything else is a query
 * parameter. The bug above was a multipart field name, and a format without
 * field names cannot have it. It also keeps AsyncWebServer streaming the bytes
 * to us in chunks rather than assembling 1,3 MB in a heap this node does not
 * have.
 *
 * SHA-256 rather than MD5, computed here over the bytes actually written. MD5
 * is what the old path takes and would have worked; SHA-256 is what a release
 * pipeline publishes next to a binary, and having the node check the same digest
 * the server checked means the two cannot disagree about what "this image" is.
 *
 * The verification happens BEFORE anything is made definitive, and that ordering
 * is the whole point rather than an implementation detail. An ESP32 has two
 * application partitions; Update writes into the one we are not running from,
 * which changes nothing about what boots. The single definitive act is the
 * otadata switch inside Update.end(), and that is reached only after the digest
 * matched. A mismatch calls Update.abort(), and this node carries on running the
 * firmware it booted with, from a partition nothing has touched.
 *
 * Rejected: buffering the whole image into SPIFFS first and copying it to flash
 * only after the digest matched, which is what "check before you write" sounds
 * like it ought to mean. There is room (the data partition is 3,4 MB), but it
 * doubles both the write time and the flash wear to buy a guarantee we already
 * have -- writing into the passive partition commits to nothing.
 *
 * Rejected: rebooting on failure "to be safe". A failed write leaves a perfectly
 * healthy system running, and restarting it is the one action that can turn a
 * failed upgrade into an outage. Only success reboots, and only after the
 * answer has had 1,5 s to leave -- the same delay the restore handler uses.
 *
 * And the thing this may never do: replace the old path. That path is what you
 * fall back to when this one is broken, and a recovery route may not depend on
 * the thing you are recovering from. Same reasoning that gave 'start ota' its
 * stock behaviour back; see mmnet_handle_command().
 */

/* Records which version sits in the partition we are NOT running from. The
 * partition table knows there is an image there and esp_ota_get_partition_
 * description() will even hand over its esp_app_desc_t, but that struct carries
 * the ESP-IDF project version -- a constant Arduino sets to something like "1"
 * -- and never MESHMANAGER_VERSION. So the only way to answer "what do I fall
 * back to" with a number a human recognises is to write it down ourselves, at
 * the moment we know it: just before rebooting into a freshly written image, the
 * version we are still running is the version that stays behind. */
#define FW_NOTE_FILE   "/msfw.json"
#define FW_SHA_HEX     64
#define FW_VER_MAX     24
#define FW_SLOT_MAX    16

/* Build environment this image was compiled for -- the PlatformIO env name,
 * e.g. heltec_v4_repeater_meshmanager. Set it from platformio.ini with
 *
 *     -D MESHMANAGER_ENV='"$PIOENV"'
 *
 * so it can never drift from the env that actually built the binary.
 *
 * Why the env name and not the board name. getManufacturerName() already
 * answers "Heltec V4.3 OLED", and matching a release asset against that was the
 * obvious route. It is also the route that eventually flashes an ESP32-S3 image
 * onto an nRF52: that string is free-form prose maintained upstream, it differs
 * between boards that take the same binary and matches between boards that do
 * not, and a MeshCore release may reword it without anyone noticing here. The
 * env name is the exact key the image was built under, so image and node either
 * match or they do not, with no judgement in between.
 *
 * Empty when the flag was not set, and that is deliberately not papered over
 * with a guess: a node that cannot say what it was built from gets no automatic
 * upgrade, because the failure mode is a bricked repeater on a roof. */
#ifndef MESHMANAGER_ENV
  #define MESHMANAGER_ENV ""
#endif

static struct {
  bool     active;              // a transfer is running (index 0 seen, not finished)
  bool     answered;            // we already sent a response from the body handler
  bool     any;                 // there has been a transfer since boot
  bool     ok;                  // ...and it succeeded
  uint32_t got;                 // bytes received
  uint32_t total;               // Content-Length of the transfer
  const char *step;             // "" while healthy, else where it died
  char     err[100];
  char     want[FW_SHA_HEX + 1];
  char     have[FW_SHA_HEX + 1];
  char     ver[FW_VER_MAX];     // version label the caller claims to be sending
} _fw;

static mbedtls_sha256_context _fw_sha;

/* mbedtls renamed these in 3.0: the _ret suffix only ever existed while return
 * values were being added, and went away once every function had one. Arduino
 * core 2.x ships mbedtls 2.x and core 3.x ships 3.x, and this file compiles
 * against both -- the same reason wdtBegin() picks its API at compile time. */
#if defined(MBEDTLS_VERSION_MAJOR) && MBEDTLS_VERSION_MAJOR >= 3
  #define FW_SHA_STARTS(c)        mbedtls_sha256_starts((c), 0)
  #define FW_SHA_UPDATE(c, d, n)  mbedtls_sha256_update((c), (d), (n))
  #define FW_SHA_FINISH(c, o)     mbedtls_sha256_finish((c), (o))
#else
  #define FW_SHA_STARTS(c)        mbedtls_sha256_starts_ret((c), 0)
  #define FW_SHA_UPDATE(c, d, n)  mbedtls_sha256_update_ret((c), (d), (n))
  #define FW_SHA_FINISH(c, o)     mbedtls_sha256_finish_ret((c), (o))
#endif

static void fwHex(char *out, const uint8_t *b, int n) {
  for (int i = 0; i < n; i++) {
    out[i * 2]     = HEXCHARS[b[i] >> 4];
    out[i * 2 + 1] = HEXCHARS[b[i] & 0x0f];
  }
  out[n * 2] = 0;
}

// One place to fail, so every failure leaves the same three facts behind: which
// step, why, and a Update that is no longer half-open.
static void fwFail(const char *step, const char *fmt, ...) {
  _fw.step = step;
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(_fw.err, sizeof(_fw.err), fmt, ap);
  va_end(ap);
  if (Update.isRunning()) Update.abort();
  Serial.printf("MeshManagerNet: firmware-upload mislukt (%s): %s\n", step, _fw.err);
}

// The image now in the partition we are not running from, as far as we know.
static void fwNoteOther(const char *ver, const char *slot) {
  if (!_fs) return;
  File f = _fs->open(FW_NOTE_FILE, "w");
  if (!f) return;
  f.printf("{\"ver\":\"%.*s\",\"slot\":\"%.*s\"}",
           FW_VER_MAX - 1, ver, FW_SLOT_MAX - 1, slot);
  f.close();
}

/* Reads that note back, but only believes it when the slot it names is still
 * the slot we would fall back to. A USB reflash writes app0 without telling
 * anyone, and a note claiming "1.11.0 lives in app1" would then be a confident
 * lie about a partition somebody else overwrote. Same small hand-written parser
 * as loadConfig(), for the same reason: we write this file ourselves. */
static bool fwReadOther(const char *slot, char *ver, size_t ver_max) {
  ver[0] = 0;
  if (!_fs || !slot) return false;
  File f = _fs->open(FW_NOTE_FILE, "r");
  if (!f) return false;
  String s = f.readString();
  f.close();

  int i = s.indexOf("\"slot\":\"");
  if (i < 0) return false;
  i += 8;
  int j = s.indexOf('"', i);
  if (j < 0 || s.substring(i, j) != slot) return false;

  i = s.indexOf("\"ver\":\"");
  if (i < 0) return false;
  i += 7;
  j = s.indexOf('"', i);
  if (j < 0) return false;
  strncpy(ver, s.substring(i, j).c_str(), ver_max - 1);
  ver[ver_max - 1] = 0;
  return true;
}

/* The image bytes, streamed. Runs on the AsyncWebServer task, once per chunk,
 * and is expected to keep being called after a failure -- refusing to read the
 * rest of a body does not make the sender stop sending it, it only means the
 * connection dies before the answer explaining what went wrong can be
 * delivered. So every failure sets _fw.step and the remaining chunks are read
 * and dropped. */
static void fwBody(AsyncWebServerRequest *req, uint8_t *data, size_t len,
                   size_t index, size_t total) {
  if (index == 0) {
    memset(&_fw, 0, sizeof(_fw));
    _fw.step   = "";
    _fw.any    = true;
    _fw.active = true;
    _fw.total  = (uint32_t)total;

    /* requestAuthentication() has already put a 401 on this request, so the
     * completion handler must keep its hands off it. Whoever can write firmware
     * here can also download the private key through /api/backup. */
    if (!requireAuth(req)) {
      _fw.answered = true;
      fwFail("auth", "aanmelden mislukt");
      return;
    }

    if (!req->hasParam("sha256")) {
      fwFail("param", "parameter sha256 ontbreekt");
      return;
    }
    String want = req->getParam("sha256")->value();
    want.toLowerCase();
    if (want.length() != FW_SHA_HEX) {
      fwFail("param", "sha256 moet %d hextekens zijn, kreeg %u",
             FW_SHA_HEX, (unsigned)want.length());
      return;
    }
    for (int i = 0; i < FW_SHA_HEX; i++) {
      if (!isxdigit((unsigned char)want[i])) {
        fwFail("param", "sha256 bevat een teken dat geen hex is");
        return;
      }
    }
    strcpy(_fw.want, want.c_str());

    /* An optional second opinion on the length. It cannot catch anything the
     * digest does not also catch, but it catches it before the partition is
     * erased instead of two minutes later, and the difference between "refused"
     * and "erased, then refused" matters on a node you cannot walk up to. */
    if (req->hasParam("size")) {
      uint32_t claimed = (uint32_t)req->getParam("size")->value().toInt();
      if (claimed != (uint32_t)total) {
        fwFail("param", "size=%lu maar de body is %lu bytes",
               (unsigned long)claimed, (unsigned long)total);
        return;
      }
    }
    if (req->hasParam("ver")) {
      strncpy(_fw.ver, req->getParam("ver")->value().c_str(), FW_VER_MAX - 1);
      _fw.ver[FW_VER_MAX - 1] = 0;
    }

    /* Refuse rather than join in. Two writers on one Update object interleave
     * their bytes into one partition and the digest of neither will match --
     * true, but by then both partitions are useless. */
    if (Update.isRunning()) {
      fwFail("bezig", "er loopt al een firmware-upload");
      return;
    }

    /* The real length, not UPDATE_SIZE_UNKNOWN: that erases the whole partition
     * up front, which on a 6,25 MB slot is seconds of stopped world for an image
     * of 1,3 MB. Update also rejects a body that is larger than the slot here,
     * before a byte is written, and checks the ESP32 image magic on the first
     * chunk -- so an HTML error page saved as .bin fails at 'begin' with a
     * legible reason rather than at 'sha' after a full upload. */
    if (!Update.begin((size_t)total, U_FLASH)) {
      fwFail("begin", "%s", Update.errorString());
      return;
    }

    mbedtls_sha256_init(&_fw_sha);
    FW_SHA_STARTS(&_fw_sha);
    Serial.printf("MeshManagerNet: firmware-upload gestart, %lu bytes\n",
                  (unsigned long)total);
  }

  if (_fw.step[0]) return;                 // already dead; swallow the remainder

  FW_SHA_UPDATE(&_fw_sha, data, len);
  if (Update.write(data, len) != len) {
    fwFail("write", "%s na %lu bytes", Update.errorString(),
           (unsigned long)_fw.got);
    return;
  }
  _fw.got = (uint32_t)(index + len);
}

/* Runs once, after the last chunk. This is the only place that may make
 * anything definitive, and the order below is the guarantee: digest first,
 * length second, otadata switch last. */
static void fwDone(AsyncWebServerRequest *req) {
  if (_fw.answered) {           // the 401 from the body handler stands
    _fw.answered = false;
    _fw.active   = false;
    return;
  }
  if (!_fw.active) {
    req->send(400, "application/json",
              "{\"ok\":0,\"step\":\"leeg\",\"msg\":\"geen inhoud; het beeld hoort "
              "de body van de POST te zijn\"}");
    return;
  }

  if (!_fw.step[0]) {
    uint8_t digest[32];
    FW_SHA_FINISH(&_fw_sha, digest);
    mbedtls_sha256_free(&_fw_sha);
    fwHex(_fw.have, digest, 32);
    if (strcmp(_fw.have, _fw.want) != 0) {
      fwFail("sha", "checksum klopt niet na %lu van %lu bytes",
             (unsigned long)_fw.got, (unsigned long)_fw.total);
    } else if (_fw.got != _fw.total) {
      /* Belt and braces: a digest over fewer bytes than announced cannot match
       * the digest of the whole image, so this is unreachable in practice. It
       * stays because the alternative to an impossible branch here is
       * Update.end() being asked to accept a short image. */
      fwFail("kort", "%lu van %lu bytes ontvangen",
             (unsigned long)_fw.got, (unsigned long)_fw.total);
    } else if (!Update.end(true)) {
      fwFail("end", "%s", Update.errorString());
    }
  }

  _fw.active = false;
  _fw.ok     = (_fw.step[0] == 0);

  const esp_partition_t *run = esp_ota_get_running_partition();

  static char body[420];
  snprintf(body, sizeof(body),
           "{\"ok\":%d,\"step\":\"%s\",\"msg\":\"%s\",\"bytes\":%lu,\"total\":%lu,"
           "\"want\":\"%s\",\"have\":\"%s\",\"from\":\"%s\",\"to\":\"%s\","
           "\"env\":\"%s\",\"reboot\":%d}",
           _fw.ok ? 1 : 0, _fw.step, _fw.ok ? "geschreven en geverifieerd" : _fw.err,
           (unsigned long)_fw.got, (unsigned long)_fw.total,
           _fw.want, _fw.have, MESHMANAGER_VERSION, _fw.ver, MESHMANAGER_ENV,
           _fw.ok ? 1 : 0);
  req->send(_fw.ok ? 200 : 400, "application/json", body);

  if (_fw.ok) {
    // We are still the old image; after the reboot we are the one behind.
    fwNoteOther(MESHMANAGER_VERSION, run ? run->label : "?");
    Serial.printf("MeshManagerNet: firmware geschreven, herstart over 1,5 s\n");
    _reboot_pending = true;
    _reboot_at = millis() + 1500;
  }
}

/* Everything a caller needs to decide whether to send an image, and everything
 * it needs afterwards to explain what happened. Readable without a login is not
 * an option: 'env' plus 'ver' is a shopping list for whoever wants to write the
 * wrong image here. */
static void fwState(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;

  const esp_partition_t *run   = esp_ota_get_running_partition();
  const esp_partition_t *other = esp_ota_get_next_update_partition(NULL);

  /* ESP_OK here means the slot holds something with a valid ESP32 application
   * header -- which is what makes a fallback possible at all. On a node that has
   * only ever been flashed over USB the second slot is erased and this fails,
   * and saying so is the honest answer to "can I go back": no. */
  esp_app_desc_t desc;
  bool other_valid = other && esp_ota_get_partition_description(other, &desc) == ESP_OK;

  char other_ver[FW_VER_MAX];
  bool other_known = other_valid && fwReadOther(other->label, other_ver, sizeof(other_ver));

  static char body[560];
  snprintf(body, sizeof(body),
           "{\"ver\":\"%s\",\"fw\":\"%s\",\"env\":\"%s\",\"board\":\"%s\","
           "\"busy\":%d,\"got\":%lu,\"total\":%lu,"
           "\"run\":\"%s\",\"other\":{\"slot\":\"%s\",\"valid\":%d,\"ver\":\"%s\"},"
           "\"last\":{\"any\":%d,\"ok\":%d,\"step\":\"%s\",\"msg\":\"%s\","
           "\"bytes\":%lu,\"total\":%lu}}",
           MESHMANAGER_VERSION, FIRMWARE_VERSION, MESHMANAGER_ENV,
           board.getManufacturerName(),
           _fw.active ? 1 : 0, (unsigned long)_fw.got, (unsigned long)_fw.total,
           run ? run->label : "?",
           other ? other->label : "?", other_valid ? 1 : 0,
           other_known ? other_ver : "",
           _fw.any ? 1 : 0, _fw.ok ? 1 : 0,
           _fw.any ? _fw.step : "", _fw.any && !_fw.ok ? _fw.err : "",
           (unsigned long)_fw.got, (unsigned long)_fw.total);
  req->send(200, "application/json", body);
}

/* Boot from the other partition again. Cheap, because the image is already
 * there: an OTA never erases the slot it is not writing, so the firmware this
 * node ran before the last upgrade is still sitting in flash, untouched, and
 * going back is one otadata write and a restart. No download, no radio, no
 * network beyond the request itself.
 *
 * Deliberately NOT automatic. The tempting version is "if the boot counter
 * reaches three, roll back" -- and this node has a boot counter for exactly
 * that reason. It is refused because a solar repeater reboots for reasons that
 * have nothing to do with firmware: a flat cell in November browns the board out
 * three times in a night, and an automatic rollback would quietly undo a good
 * upgrade and then keep undoing it. The reachability guarantee is already
 * elsewhere and does not need this: three restarts drop the node into safe mode,
 * where its own AP and this page come up regardless of what the new firmware
 * broke. Safe mode is what keeps the node reachable; this is what repairs it,
 * and repairs are a decision. */
static bool fwRollback(char *msg, size_t msg_max) {
  const esp_partition_t *other = esp_ota_get_next_update_partition(NULL);
  esp_app_desc_t desc;
  if (!other || esp_ota_get_partition_description(other, &desc) != ESP_OK) {
    snprintf(msg, msg_max, "geen geldig beeld in de andere sleuf");
    return false;
  }
  esp_err_t e = esp_ota_set_boot_partition(other);
  if (e != ESP_OK) {
    snprintf(msg, msg_max, "otadata schrijven mislukt: %s", esp_err_to_name(e));
    return false;
  }

  char ver[FW_VER_MAX];
  bool known = fwReadOther(other->label, ver, sizeof(ver));

  // The roles swap: what we are running now is what stays behind in our slot.
  const esp_partition_t *run = esp_ota_get_running_partition();
  fwNoteOther(MESHMANAGER_VERSION, run ? run->label : "?");

  snprintf(msg, msg_max, "terug naar %s in %s",
           known ? ver : "de vorige firmware", other->label);
  return true;
}

/* The same fallback from the mesh CLI, and this is the version that matters
 * most. Every other way into this node runs over IP, so an upgrade whose only
 * fault is that it cannot join the WiFi takes all of them away at once: no admin
 * page, no console, no /api/fw to undo it with. The mesh does not care -- LoRa
 * comes up from the radio driver, before any of that -- so 'wifi fw rollback'
 * over the mesh CLI reaches a node that has become invisible on the network and
 * puts the previous image back.
 *
 * Still gone once the boot counter passes DISABLE_BOOTS: at six restarts this
 * whole module stays down and a plain MeshCore repeater remains, whose way back
 * is 'start ota' and a soft-AP. That is the floor, and it is deliberate -- a
 * command that survives its own module's failure would have to live outside it.
 */
static void handleFwCommand(const char *arg, char *reply) {
  const esp_partition_t *run   = esp_ota_get_running_partition();
  const esp_partition_t *other = esp_ota_get_next_update_partition(NULL);

  if (memcmp(arg, "rollback", 8) == 0) {
    char msg[96];
    if (!fwRollback(msg, sizeof(msg))) {
      snprintf(reply, 155, "Err - %s", msg);
      return;
    }
    /* Three seconds rather than the 1,5 the HTTP path uses: a reply over the
     * mesh has to be encrypted, queued behind whatever else is in the transmit
     * queue and actually radiated before this node may disappear, and a
     * rollback nobody heard confirmed is a rollback somebody repeats. */
    _reboot_pending = true;
    _reboot_at = millis() + 3000;
    snprintf(reply, 155, "OK - %s, herstart over 3 s", msg);
    return;
  }

  esp_app_desc_t desc;
  bool other_valid = other && esp_ota_get_partition_description(other, &desc) == ESP_OK;
  char ver[FW_VER_MAX];
  bool known = other_valid && fwReadOther(other->label, ver, sizeof(ver));

  snprintf(reply, 155, "v%s in %s, env=%s; terug kan naar %s; laatste upload: %s",
           MESHMANAGER_VERSION, run ? run->label : "?",
           MESHMANAGER_ENV[0] ? MESHMANAGER_ENV : "onbekend",
           !other_valid ? "niets (andere sleuf leeg)" : (known ? ver : "onbekende versie"),
           !_fw.any ? "geen" : (_fw.ok ? "gelukt" : _fw.step));
}

static void fwRollbackPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  char msg[96];
  bool ok = fwRollback(msg, sizeof(msg));
  char body[160];
  snprintf(body, sizeof(body), "{\"ok\":%d,\"msg\":\"%s\"}", ok ? 1 : 0, msg);
  req->send(ok ? 200 : 409, "application/json", body);
  if (ok) {
    _reboot_pending = true;
    _reboot_at = millis() + 1500;
  }
}

// --------------------------------------------------- instellingen schrijven

/* Eén CLI-parameter van deze node zetten vanaf de site, en meteen teruglezen.
 *
 * Waarom hier een tabel staat en niet gewoon de CLI wordt doorgegeven. De
 * telnetconsole hieronder doet precies dat -- elke regel gaat door naar
 * MyMesh::handleCommand -- en daar mag dat, want daar zit een mens achter die
 * een wachtwoord heeft ingetypt en die weet wat hij doet. Een knop op een
 * webpagina is iets anders: die wordt aangeklikt, soms op de verkeerde regel,
 * en de gevolgen van een verkeerde parameter zijn niet symmetrisch. 'set name'
 * fout is een lelijke naam; 'set radio' fout is een node die op een andere
 * frequentie gaat luisteren en die je nooit meer terugziet.
 *
 * Vandaar een lijst die in de firmware ingebakken zit. Niet in de server, want
 * die is te bewerken door wie de site draait; deze lijst is wat er werkelijk
 * tussen een klik en de radio staat. De server heeft zijn eigen kopie om een
 * tikfout al op de pagina te kunnen weigeren, maar dát is de beleefdheid en dit
 * is de beveiliging.
 *
 * Wat er in mag: alles wat de CLI van een repeater kan, op drie uitzonderingen
 * na die hieronder bij de tabel staan uitgelegd. Dat is met opzet ruimer dan de
 * eerste opzet, die alleen parameters toeliet welke de bereikbaarheid niet
 * konden afsnijden. Die insteek was veilig en te krap: de instellingen die je
 * op afstand het hardst nodig hebt -- zendvermogen, radioparameters -- zijn nu
 * juist de gevaarlijke, en ze weglaten betekent alleen dat iemand een ladder
 * pakt of een seriële kabel zoekt, niet dat het risico verdwijnt.
 *
 * Het risico is dus verplaatst van weglaten naar afvangen, en dat is wat het
 * veld 'risk' doet. Het reist mee in /api/cfg, en de bedieningskant hangt er de
 * zwaarte van de bevestiging aan op: gewoon opslaan, een bevestiging die node en
 * sleutel noemt, of de naam van de node overtypen. Wat er hier in de firmware
 * blijft staan is de grens van wat een waarde mág zijn; wat er aan de overkant
 * bij komt is hoeveel moeite het kost om hem te zetten.
 *
 * Wat NIET verplaatst is: de controle op de waarde zelf. Die staat hier, en
 * juist bij de gevaarlijke klasse is dat de laatste zeef -- een frequentie
 * buiten de band is niet riskant maar gewoon fout, en zo'n waarde hoort de
 * radio nooit te bereiken, hoeveel bevestigingen iemand ook wegklikt.
 *
 * En waarom er teruggelezen wordt in plaats van 'OK' te geloven. MeshCore
 * controleert lang niet alles wat het aanneemt: 'set lat' is een kale atof(),
 * en atof("noord") is 0.0 -- een node die na een tikfout beweert dat hij in de
 * Golf van Guinee staat, met "OK" als antwoord. Erger nog is advert.interval:
 * dat wordt bewaard als minuten/2 in één byte, dus 'set advert.interval 61'
 * legt 30 vast en leest terug als 60. Beide keren luidt het antwoord "OK" en
 * beide keren staat er iets anders in de node dan er gevraagd is. Daarom geeft
 * dit endpoint niet terug wat er gevraagd was maar wat er ná afloop in de node
 * staat, met de vraag ernaast, en laat het aan de pagina om te melden dat die
 * twee verschillen. */

#define CFG_VALUE_MAX   40
#define CFG_KEY_MAX     28

enum CfgKind {
  CFG_TEXT = 0,   // vrije tekst
  CFG_INT = 1,    // geheel getal tussen lo en hi
  CFG_FLOAT = 2,  // kommagetal tussen lo en hi
  CFG_BOOL = 3,   // on/off -- MeshCore leest letterlijk "on" of "off"
  CFG_ENUM = 4,   // één woord uit 'choices', gescheiden door |
  CFG_RADIO = 5,  // vier getallen: freq bw sf cr
};

/* Risicoklasse, en die stuurt aan de andere kant de bevestiging. De grens tussen
 * 2 en 3 is de enige die er echt toe doet en luidt: kan het misgaan-geval deze
 * node onbereikbaar maken langs de weg waarlangs je hem bestuurt? Voor een node
 * op een dak zonder IP-pad is dat het einde -- er is dan geen tweede weg om de
 * fout mee terug te draaien. */
enum CfgRisk {
  RISK_PLAIN  = 1,   // zo weer terug te zetten
  RISK_WRITES = 2,   // verandert merkbaar hoe de node zich gedraagt
  RISK_CUTOFF = 3,   // kan de bereikbaarheid afsnijden
};

struct CfgParam {
  const char *key;      // wat de aanroeper vraagt; ook wat 'get <key>' teruggeeft
  uint8_t     kind;
  float       lo, hi;   // eigen grenzen, inclusief -- niet per se die van MeshCore
  const char *choices;  // alleen bij CFG_ENUM: de toegestane woorden, met |
  uint8_t     risk;
  uint8_t     reboot;   // 1 = wordt nu bewaard, pas actief na een herstart
  /* 1 = de waarde is een geheim. Er wordt nog steeds teruggelezen en vergeleken
   * -- de controle blijft dus overeind -- maar de gelezen waarde gaat niet mee
   * in het antwoord. Anders staat het wachtwoord dat je net zette in het
   * HTML van de beheerpagina, in de browsergeschiedenis en in elke
   * schermafdruk ervan, en een wachtwoord dat daar geweest is, is weg. Dat is
   * dezelfde reden waarom bridge.secret helemaal niet aangeboden wordt; het
   * verschil is dat guest.password een instelling is die je werkelijk van
   * afstand wilt kunnen zetten. */
  uint8_t     secret;
};

/* Het volledige oppervlak van handleSetCmd() in CommonCLI.cpp, op drie soorten
 * na. Die drie ontbreken niet per ongeluk:
 *
 *   prv.key       vervangt de identiteit van de node. Dat is geen instelling en
 *                 geen risico maar een andere node: elke contactlijst, elke ACL
 *                 en elke monitorregel elders in het mesh wijst daarna naar
 *                 iemand die niet meer bestaat. Er is geen bevestiging die dat
 *                 een goed idee maakt vanaf een webpagina.
 *   bridge.secret gedeeld geheim. Schrijven zou kunnen, maar de waarde komt bij
 *                 het teruglezen gewoon weer tevoorschijn, en een wachtwoord dat
 *                 in een logregel of een schermafdruk beland is, is weg.
 *   freq          MeshCore laat 'set freq' alleen toe vanaf de seriële kabel
 *                 (sender_timestamp == 0). Deze weg geeft met opzet een andere
 *                 tijdstempel mee, dus die tak is hier onbereikbaar -- en dat is
 *                 maar goed ook. De frequentie hoort bij de andere drie
 *                 radiowaarden en gaat via 'radio', dat wél gecontroleerd wordt.
 *
 * De grenzen zijn de onze. Soms strenger dan MeshCore (advert.interval 0 is daar
 * geldig en betekent "stop met adverteren": dat maakt een node niet onbereikbaar
 * maar laat hem wel uit ieders lijst wegzakken, wat op een dak hetzelfde voelt),
 * en soms de enige die er is -- lat, lon, af, tx, int.thresh, multi.acks en
 * adc.multiplier worden aan de overkant met een kale atof()/atoi() ingelezen
 * zonder één controle. */
static const CfgParam CFG_PARAMS[] = {
  // --- zo weer terug te zetten ---------------------------------------------
  { "name",                  CFG_TEXT,     0,      0, NULL,                          RISK_PLAIN,  0, 0 },
  { "lat",                   CFG_FLOAT,  -90,     90, NULL,                          RISK_PLAIN,  0, 0 },
  { "lon",                   CFG_FLOAT, -180,    180, NULL,                          RISK_PLAIN,  0, 0 },
  { "owner.info",            CFG_TEXT,     0,      0, NULL,                          RISK_PLAIN,  0, 0 },
  { "advert.interval",       CFG_INT,     60,    240, NULL,                          RISK_PLAIN,  0, 0 },  // minuten, stappen van 2
  { "flood.advert.interval", CFG_INT,      3,    168, NULL,                          RISK_PLAIN,  0, 0 },  // uren
  { "rxdelay",               CFG_FLOAT,    0,     20, NULL,                          RISK_PLAIN,  0, 0 },
  { "txdelay",               CFG_FLOAT,    0,      2, NULL,                          RISK_PLAIN,  0, 0 },
  { "direct.txdelay",        CFG_FLOAT,    0,      2, NULL,                          RISK_PLAIN,  0, 0 },

  // --- verandert merkbaar hoe de node zich gedraagt -------------------------
  { "dutycycle",             CFG_FLOAT,    1,    100, NULL,                          RISK_WRITES, 0, 0 },
  { "af",                    CFG_FLOAT,    0,    100, NULL,                          RISK_WRITES, 0, 0 },
  { "flood.max",             CFG_INT,      0,     64, NULL,                          RISK_WRITES, 0, 0 },
  { "flood.max.unscoped",    CFG_INT,      0,     64, NULL,                          RISK_WRITES, 0, 0 },
  { "flood.max.advert",      CFG_INT,      0,     64, NULL,                          RISK_WRITES, 0, 0 },
  { "int.thresh",            CFG_INT,      0,    255, NULL,                          RISK_WRITES, 0, 0 },
  { "agc.reset.interval",    CFG_INT,      0,   1020, NULL,                          RISK_WRITES, 0, 0 },  // bewaard als /4
  { "multi.acks",            CFG_INT,      0,      3, NULL,                          RISK_WRITES, 0, 0 },
  { "path.hash.mode",        CFG_INT,      0,      2, NULL,                          RISK_WRITES, 0, 0 },
  { "loop.detect",           CFG_ENUM,     0,      0, "off|minimal|moderate|strict", RISK_WRITES, 0, 0 },
  { "cad",                   CFG_BOOL,     0,      0, NULL,                          RISK_WRITES, 0, 0 },
  { "adc.multiplier",        CFG_FLOAT,    0,     10, NULL,                          RISK_WRITES, 0, 0 },

  // --- kan de bereikbaarheid afsnijden --------------------------------------
  /* Alle vijf hieronder raken de radio of wie er mag inloggen. Op een node met
   * twee onafhankelijke wegen naar binnen (de onze: mesh én IP) is een fout hier
   * hinderlijk; op een stock repeater die alleen over LoRa te bereiken is, is
   * hij blijvend. Vandaar de zwaarste drempel aan de bedieningskant. */
  { "tx",                    CFG_INT,      0,     30, NULL,                          RISK_CUTOFF, 0, 0 },
  { "repeat",                CFG_BOOL,     0,      0, NULL,                          RISK_CUTOFF, 0, 0 },
  { "allow.read.only",       CFG_BOOL,     0,      0, NULL,                          RISK_CUTOFF, 0, 0 },
  { "radio.rxgain",          CFG_BOOL,     0,      0, NULL,                          RISK_CUTOFF, 0, 0 },
  { "radio.fem.rxgain",      CFG_BOOL,     0,      0, NULL,                          RISK_CUTOFF, 0, 0 },
  { "guest.password",        CFG_TEXT,     0,      0, NULL,                          RISK_CUTOFF, 0, 1 },
  /* 'radio' stond hier tot 2.6.0 en is er met opzet uit. De regel van de
   * eigenaar luidt: van afstand mag het zendvermogen gewijzigd worden en verder
   * niets aan de radio -- geen frequentie, geen spreidingsfactor, geen
   * coderingssnelheid, geen bandbreedte.
   *
   * De asymmetrie die die regel draagt: een verkeerde 'tx' maakt een node
   * zwakker maar bereikbaar, terwijl een verkeerde frequentie of modulatie hem
   * van de lucht haalt -- hij hoort niemand meer en niemand hoort hem, en er is
   * geen commando dat dat nog kan terugdraaien omdat er geen weg meer naar
   * binnen is. Dat is geen risico dat je met een zwaardere bevestiging afkoopt;
   * het is een risico dat je niet neemt.
   *
   * Waarom hier, in de tabel, en niet in de drie schermen. Deze lijst is wat
   * /api/cfg publiceert, en daarmee tegelijk wat de beheerpagina van de node
   * tekent, wat de server mag aanbieden, en wat er over LoRa naar een
   * gemonitorde repeater geschreven kan worden (/api/moncfg). Eén regel weghalen
   * sluit alle drie tegelijk, en dat kan niet uit elkaar lopen. Drie schermen die
   * er elk zelf iets van vinden, kunnen dat wel.
   *
   * Wat dit kost, en dat hoort erbij: de beheerpagina van de node blijft
   * bereikbaar over wifi als de radio verkeerd staat -- wifi en LoRa zijn
   * onafhankelijk -- dus dit was de enige weg die een verkeerde bandbreedte nog
   * zonder ladder kon rechtzetten. Die weg is nu de seriële kabel of 'set radio'
   * over de mesh-CLI, zolang die nog draagt.
   *
   * De machinerie eromheen (CFG_RADIO, cfgRadioOk(), CFG_RADIO_LO/HI) blijft
   * staan. Dat is geen vergeten code: het is de controle die er moet zijn op het
   * moment dat iemand deze regel terugzet, en die wil je niet opnieuw hoeven
   * schrijven onder tijdsdruk. Terugzetten is deze ene regel:
   *   { "radio", CFG_RADIO, 0, 0, NULL, RISK_CUTOFF, 1, 0 },
   */
};
#define CFG_PARAM_COUNT ((int)(sizeof(CFG_PARAMS) / sizeof(CFG_PARAMS[0])))

static const char *cfgKindName(uint8_t kind) {
  switch (kind) {
    case CFG_INT:   return "int";
    case CFG_FLOAT: return "float";
    case CFG_BOOL:  return "bool";
    case CFG_ENUM:  return "enum";
    case CFG_RADIO: return "radio";
    default:        return "text";
  }
}

static const CfgParam *cfgFind(const char *key) {
  for (int i = 0; i < CFG_PARAM_COUNT; i++) {
    if (strcmp(CFG_PARAMS[i].key, key) == 0) return &CFG_PARAMS[i];
  }
  return NULL;
}

/* Tekens die MeshCore's isValidName() weigert, plus alles onder 0x20. Die
 * laatste staan er niet omdat MeshCore ze verbiedt -- dat doet het niet -- maar
 * omdat een naam met een regeleinde erin een JSON-bericht oplevert dat de
 * server weggooit, waarna de node uit de statistieken verdwijnt zonder dat er
 * ergens een fout te zien is. Dezelfde les als bij jsonEsc(). */
static bool cfgNameOk(const char *v) {
  if (*v == 0) return false;
  for (const char *p = v; *p; p++) {
    if ((unsigned char)*p < 0x20) return false;
    if (strchr("[]\\:,?*", *p)) return false;
  }
  return true;
}

/* Een getal en niets anders. strtof() alleen is niet genoeg: die leest "12abc"
 * als 12 en meldt dat het goed ging, en juist dát is de fout die we van
 * MeshCore proberen op te vangen in plaats van na te doen. */
static bool cfgNumber(const char *v, float &out) {
  if (*v == 0) return false;
  char *end = NULL;
  out = strtof(v, &end);
  if (end == v) return false;
  while (*end == ' ') end++;
  return *end == 0 && !isnan(out) && !isinf(out);
}

// Eén woord uit 'choices' ("off|minimal|moderate|strict"), hoofdlettergevoelig
// omdat MeshCore de zijne ook zo vergelijkt.
static bool cfgEnumOk(const char *choices, const char *v) {
  size_t n = strlen(v);
  if (n == 0) return false;
  for (const char *p = choices; *p; ) {
    const char *bar = strchr(p, '|');
    size_t len = bar ? (size_t)(bar - p) : strlen(p);
    if (len == n && strncmp(p, v, n) == 0) return true;
    if (!bar) break;
    p = bar + 1;
  }
  return false;
}

/* Vier getallen: freq bw sf cr. Dezelfde grenzen die MeshCore zelf aanhoudt
 * (CommonCLI.cpp, de 'radio ' tak), hier herhaald omdat dit de enige parameter
 * is waarbij een geweigerde waarde en een aanvaarde waarde allebei "OK" kunnen
 * opleveren aan de kant van de aanroeper: de radio gaat pas bij de herstart om,
 * dus wie hier iets doorlaat wat de node niet aankan, merkt dat pas als de node
 * wegblijft.
 *
 * De grenzen staan in een tabel en niet in de vergelijking, omdat ze sinds 2.5.0
 * twee lezers hebben: deze controle, en /api/cfg dat ze doorgeeft zodat de
 * beheerpagina vier invoervelden kan tekenen die elk hun eigen minimum en
 * maximum kennen. Zou de pagina ze zelf opschrijven, dan zijn er twee lijsten
 * met radiogrenzen -- en de losse van de twee zou uitgerekend die zijn waar
 * iemand op een knop drukt. Eén tabel, twee lezers. */
static const char *CFG_RADIO_FIELD[4] = { "freq", "bw", "sf", "cr" };
static const float CFG_RADIO_LO[4] = { 150.0f, 7.0f,  5.0f, 5.0f };
static const float CFG_RADIO_HI[4] = { 2500.0f, 500.0f, 12.0f, 8.0f };

static bool cfgRadioOk(const char *v) {
  float f[4] = { 0, 0, 0, 0 };
  char extra = 0;
  if (sscanf(v, "%f %f %f %f %c", &f[0], &f[1], &f[2], &f[3], &extra) != 4) return false;
  for (int i = 0; i < 4; i++) {
    if (f[i] < CFG_RADIO_LO[i] || f[i] > CFG_RADIO_HI[i]) return false;
  }
  // sf en cr zijn gehele getallen; 'sf 11.5' hoort niet door deze zeef te komen.
  for (int i = 2; i < 4; i++) {
    if (f[i] != (float)(long)f[i]) return false;
  }
  return true;
}

/* Is wat er nu in de node staat dezelfde waarde als wat er gevraagd is?
 *
 * Nadrukkelijk 'dezelfde waarde' en niet 'dezelfde tekst', want MeshCore schrijft
 * en leest sommige parameters in verschillende vormen op. 'set radio' neemt vier
 * getallen gescheiden door spaties, 'get radio' geeft ze terug gescheiden door
 * komma's. 'set dutycycle 50' antwoordt bij het lezen met "50.0%". Een kale
 * strcmp() zou die twee als 'niet toegepast' melden, en dan staat er bij elke
 * radio-instelling een waarschuwing die niets betekent -- waarna niemand meer
 * kijkt op het moment dat er wél iets afwijkt. Een melding die te vaak afgaat is
 * net zo onbruikbaar als een melding die nooit afgaat. */
static bool cfgSameValue(const CfgParam *p, const char *asked, const char *applied) {
  if (p->kind == CFG_RADIO) {
    float fa[4] = {0, 0, 0, 0}, fb[4] = {0, 0, 0, 0};
    // Beide vormen toestaan, want de ene kant schrijft spaties en de andere komma's.
    if (sscanf(asked,   "%f %f %f %f", &fa[0], &fa[1], &fa[2], &fa[3]) != 4 &&
        sscanf(asked,   "%f,%f,%f,%f", &fa[0], &fa[1], &fa[2], &fa[3]) != 4) return false;
    if (sscanf(applied, "%f,%f,%f,%f", &fb[0], &fb[1], &fb[2], &fb[3]) != 4 &&
        sscanf(applied, "%f %f %f %f", &fb[0], &fb[1], &fb[2], &fb[3]) != 4) return false;
    for (int i = 0; i < 4; i++) {
      if (fabsf(fa[i] - fb[i]) > 0.0005f) return false;
    }
    return true;
  }
  if (p->kind == CFG_INT || p->kind == CFG_FLOAT) {
    float a = 0, b = 0;
    if (!cfgNumber(asked, a)) return false;
    // Het percentteken van 'get dutycycle' hoort bij de opmaak, niet bij de waarde.
    char trimmed[CFG_VALUE_MAX];
    strncpy(trimmed, applied, sizeof(trimmed) - 1);
    trimmed[sizeof(trimmed) - 1] = 0;
    size_t n = strlen(trimmed);
    if (n && trimmed[n - 1] == '%') trimmed[n - 1] = 0;
    if (!cfgNumber(trimmed, b)) return false;
    return fabsf(a - b) <= 0.0005f;
  }
  return strcmp(asked, applied) == 0;
}

// "> 60" -> "60"; een foutmelding van de overkant blijft staan zoals hij is.
static const char *cfgStripMarker(const char *reply) {
  if (reply[0] == '>' && reply[1] == ' ') return reply + 2;
  return reply;
}

// Beide spellingen waarmee MeshCore weigert. Voluit, zodat een node die
// 'Erratic' heet zijn eigen naam nog kan terugkrijgen.
static bool cfgIsError(const char *reply) {
  return strncmp(reply, "Error", 5) == 0 || strncmp(reply, "Err - ", 6) == 0;
}

/* De CLI van deze node aanroepen. Het commando wordt opgebouwd uit een sleutel
 * die uit CFG_PARAMS komt en nooit uit het verzoek, dus er valt niets in te
 * smokkelen: de waarde is altijd het laatste woord en er is geen scheider
 * waarmee een tweede commando begint.
 *
 * De tijdstempel is met opzet niet 0. De console gebruikt 0, en dat betekent in
 * MeshCore "dit komt van de seriële kabel", wat een handvol commando's
 * ontgrendelt die alleen daar horen ('erase', 'get prv.key'). Deze weg heeft er
 * geen van nodig, dus krijgt hij ze ook niet -- blijkt de tabel hierboven ooit
 * een gat te hebben, dan is dat gat in elk geval kleiner. */
static void cfgCli(char *reply, size_t reply_max, const char *fmt, ...) {
  char cmd[80];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(cmd, sizeof(cmd), fmt, ap);
  va_end(ap);

  reply[0] = 0;
  if (!_mesh) {
    strncpy(reply, "Error: geen mesh", reply_max - 1);
    reply[reply_max - 1] = 0;
    return;
  }
  _mesh->handleCommand(1, cmd, reply);
}

/* De waardecontrole, eenmaal. Geeft NULL terug als de waarde mag, en anders de
 * reden in het Nederlands.
 *
 * Een eigen functie sinds er twee schrijfwegen zijn: /api/cfg zet een instelling
 * op DEZE node, /api/moncfg laat de monitor er een zetten op een node die alleen
 * over LoRa te bereiken is. Dat zijn twee vervoermiddelen en niet twee
 * schrijfwegen, en dus hoort de zeef er één te zijn. Een tweede kopie zou vroeg
 * of laat net iets soepeler blijken -- en dan zou juist de weg zonder tweede
 * ingang de losse zijn, wat de verkeerde kant op is. */
static const char *cfgCheckValue(const CfgParam *p, const char *value) {
  float num = 0;
  switch (p->kind) {
    case CFG_TEXT:
      if (!cfgNameOk(value)) {
        return "leeg of met een teken dat niet mag ([ ] \\\\ : , ? * of een stuurteken)";
      }
      return NULL;
    case CFG_BOOL:
      /* MeshCore vergelijkt hier met memcmp(&config[n], \"on\", 2), dus alles wat
       * met 'on' begint telt als aan en de rest als uit -- \"onzin\" zet het aan.
       * Hier alleen precies de twee woorden, want dit is de enige plek waar een
       * tikfout nog geweigerd kan worden. */
      if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) return "moet on of off zijn";
      return NULL;
    case CFG_ENUM:
      if (!cfgEnumOk(p->choices, value)) return "staat niet in de lijst met toegestane waarden";
      return NULL;
    case CFG_RADIO:
      if (!cfgRadioOk(value)) {
        /* Uit dezelfde tabel als de controle zelf. Een foutmelding die andere
         * grenzen noemt dan de zeef hanteert, stuurt iemand net zo hard het
         * verkeerde in als een zeef die te ruim staat. */
        static char why[112];
        snprintf(why, sizeof(why),
                 "moet 'freq bw sf cr' zijn: %g-%g MHz, %g-%g kHz, sf %g-%g, cr %g-%g",
                 CFG_RADIO_LO[0], CFG_RADIO_HI[0], CFG_RADIO_LO[1], CFG_RADIO_HI[1],
                 CFG_RADIO_LO[2], CFG_RADIO_HI[2], CFG_RADIO_LO[3], CFG_RADIO_HI[3]);
        return why;
      }
      return NULL;
    default: {                            // CFG_INT en CFG_FLOAT
      bool ok = cfgNumber(value, num);
      if (ok && (num < p->lo || num > p->hi)) ok = false;
      if (ok && p->kind == CFG_INT && num != (float)(long)num) ok = false;
      if (!ok) {
        static char range[80];
        snprintf(range, sizeof(range), "moet een %s tussen %g en %g zijn",
                 p->kind == CFG_INT ? "geheel getal" : "getal", p->lo, p->hi);
        return range;
      }
      return NULL;
    }
  }
  return NULL;    // onbereikbaar; de compiler ziet dat niet aan een switch
}

static void handleCfgPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;

  char key[CFG_KEY_MAX] = "", value[CFG_VALUE_MAX] = "";
  copyParam(req, "key", key, sizeof(key));
  copyParam(req, "value", value, sizeof(value));

  static char body[480];
  const CfgParam *p = cfgFind(key);
  if (!p) {
    /* Bewust dezelfde tekst voor "bestaat niet" en "mag niet": welke parameters
     * er zijn is geen geheim -- ze staan in de documentatie en in /api/cfg --
     * maar een antwoord dat die twee uit elkaar houdt nodigt uit tot aftasten,
     * en daar valt niets mee te winnen. */
    snprintf(body, sizeof(body),
             "{\"ok\":0,\"step\":\"sleutel\",\"msg\":\"deze parameter staat niet op de "
             "lijst van wat er van afstand gezet mag worden\"}");
    req->send(400, "application/json", body);
    return;
  }

  const char *bad = cfgCheckValue(p, value);
  if (bad) {
    snprintf(body, sizeof(body),
             "{\"ok\":0,\"step\":\"waarde\",\"msg\":\"%s %s\"}", p->key, bad);
    req->send(400, "application/json", body);
    return;
  }

  char set_reply[160];
  cfgCli(set_reply, sizeof(set_reply), "set %s %s", p->key, value);

  char get_reply[160];
  cfgCli(get_reply, sizeof(get_reply), "get %s", p->key);
  const char *applied = cfgStripMarker(get_reply);

  bool refused = cfgIsError(set_reply);
  /* 'Toegepast' is niet hetzelfde als 'gelijk aan wat er gevraagd is', en dat
   * verschil hoort de aanroeper te zien in plaats van te moeten raden. Bij
   * advert.interval is het zelfs het gewone geval: 61 wordt 60. */
  bool exact = !refused && cfgSameValue(p, value, applied);
  /* Wel vergeleken, niet verklapt. Het teruglezen is de hele reden dat dit
   * endpoint bestaat, dus dat blijft; alleen de waarde zelf gaat niet mee terug
   * de wereld in. */
  if (p->secret) applied = "(verborgen)";

  static char e_set[320], e_applied[320], e_asked[CFG_VALUE_MAX * 2];
  jsonEsc(e_set, sizeof(e_set), set_reply);
  jsonEsc(e_applied, sizeof(e_applied), applied);
  jsonEsc(e_asked, sizeof(e_asked), value);

  snprintf(body, sizeof(body),
           "{\"ok\":%d,\"step\":\"%s\",\"key\":\"%s\",\"asked\":\"%s\","
           "\"applied\":\"%s\",\"exact\":%d,\"reply\":\"%s\"}",
           refused ? 0 : 1, refused ? "node" : "",
           p->key, e_asked, e_applied, exact ? 1 : 0, e_set);
  req->send(refused ? 400 : 200, "application/json", body);
}

/* Welke parameters deze node van afstand laat zetten, met hun grenzen. Zodat de
 * pagina de lijst niet hoeft te kennen om hem te tonen, en -- belangrijker --
 * zodat een server die een parameter aanbiedt die deze firmware niet kent dat
 * merkt vóórdat iemand erop drukt in plaats van erna. */
/* GET /api/filter -- de volledige stand, regels en tellers.
 *
 * Eén endpoint voor lezen en één voor schrijven, en het schrijvende neemt een
 * commandoregel in plaats van een veld met een waarde. Dat is met opzet anders
 * dan /api/cfg, en het verschil zit in wat er beheerd wordt: een CLI-instelling
 * is een sleutel met een waarde, een filter is drie tabellen en een lijst.
 * Diezelfde vorm door een sleutel/waarde-endpoint persen levert sleutels op als
 * 'rate.05.limit', en dan staat de grammatica van het filter verspreid over de
 * server, dit bestand en de parser -- drie plaatsen die op de dag dat het
 * misgaat niet meer hetzelfde denken.
 *
 * Nu is er één parser (pf_command), en de CLI, de telnetconsole en de site
 * voeren letterlijk dezelfde regels erdoorheen. Wat je op de beheerpagina
 * aanklikt is te lezen als het commando dat je over de mesh had kunnen typen,
 * en dat is precies wat je nodig hebt als je het achteraf moet terugdraaien
 * vanaf een seriële kabel. */
static void handleFilterGet(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;
  static char body[2200];
  size_t n = pf_json(body, sizeof(body));
  if (n == 0) {
    req->send(500, "application/json",
              "{\"ok\":0,\"msg\":\"filterstand past niet in de buffer\"}");
    return;
  }
  req->send(200, "application/json", body);
}

/* POST /api/filter met cmd=<alles na het woord 'filter'>.
 *
 * Het antwoord draagt de stand ná afloop mee, en dat is dezelfde afweging als
 * bij /api/cfg: "OK" is geen bewijs dat er staat wat je vroeg. Hier is dat geen
 * theoretisch punt -- 'filter hops 05 0' wordt keurig aangenomen en betekent
 * "stuur geen groepstekst meer door", en dat wil je op het scherm zien in de
 * vorm waarin het gehandhaafd wordt, niet in de vorm waarin je het intypte. */
static void handleFilterPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;

  char cmd[160] = "";
  copyParam(req, "cmd", cmd, sizeof(cmd));

  char reply[192] = "";
  bool known = pf_command(cmd, reply, sizeof(reply));
  bool ok = known && strncmp(reply, "Err", 3) != 0;

  static char body[2600];
  char esc[sizeof(reply) * 2];
  jsonEsc(esc, sizeof(esc), known ? reply : "onbekend filtercommando");
  int n = snprintf(body, sizeof(body), "{\"ok\":%d,\"msg\":\"%s\",\"state\":", ok ? 1 : 0, esc);
  size_t m = (n > 0 && n + 4 < (int)sizeof(body)) ? pf_json(body + n, sizeof(body) - n - 2) : 0;
  if (m == 0) {
    req->send(500, "application/json",
              "{\"ok\":0,\"msg\":\"filterstand past niet in de buffer\"}");
    return;
  }
  n += (int)m;
  snprintf(body + n, sizeof(body) - n, "}");
  req->send(ok ? 200 : 400, "application/json", body);
}

/* GET /api/cfg -- welke parameters deze node van afstand laat zetten.
 *
 * Met ?values=1 komt er per parameter bij wat er NU in de node staat. Dat is een
 * vraagteken en geen tweede endpoint, en dat is de hele bedoeling: er blijft één
 * lijst met parameters, één stel grenzen en één stel risicoklassen. Een tweede
 * endpoint zou een tweede tabel worden zodra iemand er haast heeft.
 *
 * Waarom het niet standaard meekomt. De server haalt deze lijst op en bewaart
 * hem PARAMS_TTL_S lang (nodeconfig.py), want 'wat mag er gezet worden' verandert
 * alleen bij een nieuwe firmware. 'Wat staat er nu' verandert elke keer dat
 * iemand iets zet, en een gecachte huidige waarde is een verkeerde huidige
 * waarde. Zonder de vlag is het antwoord byte voor byte wat het was, dus de
 * server merkt van deze uitbreiding niets.
 *
 * Wat het kost aan de kant van de node: achtentwintig keer 'get <sleutel>' op de
 * eigen CLI. Dat is een lokale aanroep en geen zendtijd -- de radio komt er niet
 * aan te pas -- maar het is wel achtentwintig keer handleCommand() binnen één
 * verzoek, en daarom hangt het aan een vlag die alleen de beheerpagina zet, en
 * niet aan de lijst die de server elke paar minuten ophaalt. */
static void handleCfgList(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;

  // Zoals elders in dit bestand: geen pointer bewaren, want het type ervan is
  // tussen versies van ESPAsyncWebServer van const-heid veranderd.
  bool want_values = req->hasParam("values") && req->getParam("values")->value() != "0";

  /* Achtentwintig parameters met hun grenzen, keuzelijst en risicoklasse zijn
   * 2715 byte. Met ?values=1 komt daar per parameter een waarde bij die na het
   * ontsnappen 80 byte kan worden (CFG_VALUE_MAX * 2), dus het slechtste geval is
   * 2715 + 28 * 89 = 5207. Statisch, want de taak van de webserver heeft de
   * krapste stack op deze node.
   *
   * De maat is op dat slechtste geval gekozen en niet op het gewone, en dat is
   * geen overdaad maar de les van de vorige maat. Die was 3000 met 200 speling,
   * en de lijst paste er met 122 byte over in: één parameter erbij en de lus was
   * stilletjes gestopt met een antwoord dat geldig JSON is en onvolledig. Aan de
   * overkant is dat niet te onderscheiden van 'die parameter kent deze firmware
   * niet' -- de stilste soort fout die dit endpoint kan maken. De bewaking staat
   * er nog steeds, maar hij hoort nu nooit meer aan te slaan. */
  static char body[5600];
  int n = snprintf(body, sizeof(body), "{\"params\":[");
  for (int i = 0; i < CFG_PARAM_COUNT && n < (int)sizeof(body) - 300; i++) {
    const CfgParam &p = CFG_PARAMS[i];

    /* De vier deelgrenzen van 'radio', uit dezelfde tabel als cfgRadioOk(). Ze
     * reizen in 'choices' mee omdat dat het veld is dat al bestaat voor "welke
     * waarden mag dit aannemen"; een eigen veld erbij zou hetzelfde zeggen op een
     * tweede plek. Vorm: "freq:150-2500|bw:7-500|sf:5-12|cr:5-8". */
    char rspec[64];
    rspec[0] = 0;
    if (p.kind == CFG_RADIO) {
      int r = 0;
      for (int f = 0; f < 4; f++) {
        r += snprintf(rspec + r, sizeof(rspec) - r, "%s%s:%g-%g", f ? "|" : "",
                      CFG_RADIO_FIELD[f], CFG_RADIO_LO[f], CFG_RADIO_HI[f]);
      }
    }
    const char *choices = (p.kind == CFG_RADIO) ? rspec : (p.choices ? p.choices : "");

    n += snprintf(body + n, sizeof(body) - n,
                  "%s{\"key\":\"%s\",\"kind\":\"%s\",\"lo\":%g,\"hi\":%g,"
                  "\"choices\":\"%s\",\"risk\":%u,\"reboot\":%u,\"secret\":%u",
                  i ? "," : "", p.key, cfgKindName(p.kind),
                  p.lo, p.hi, choices,
                  (unsigned)p.risk, (unsigned)p.reboot, (unsigned)p.secret);

    if (want_values) {
      /* Een geheim wordt gelezen noch getoond. Bij het schrijven wordt er wél
       * teruggelezen en vergeleken -- de controle blijft dus overeind -- maar een
       * wachtwoord dat in het HTML van een beheerpagina heeft gestaan, in de
       * browsergeschiedenis of in een schermafdruk, is weg. */
      char val[CFG_VALUE_MAX * 2] = "";
      if (!p.secret) {
        char reply[160];
        cfgCli(reply, sizeof(reply), "get %s", p.key);
        const char *v = cfgStripMarker(reply);
        /* Een node die deze parameter niet kent antwoordt met een foutregel of
         * met "??". Dat is geen waarde, en het als waarde tonen zou een leeg veld
         * opleveren dat er ingevuld uitziet. Leeg laten en de pagina laat zien
         * dat het onbekend is. */
        if (!cfgIsError(v) && strcmp(v, "??") != 0) jsonEsc(val, sizeof(val), v);
      }
      n += snprintf(body + n, sizeof(body) - n, ",\"val\":\"%s\"", val);
    }
    n += snprintf(body + n, sizeof(body) - n, "}");
  }
  snprintf(body + n, sizeof(body) - n, "]}");
  req->send(200, "application/json", body);
}

// --------------------------------- instellingen schrijven over LoRa, voor een
//                                    node die deze node MONITORT

/* De buffers van de toestandsmachine hierboven staan lang voor deze tabel, dus
 * ze kennen haar maten niet. Dit is wat er gebeurt als er iemand aan draait. */
static_assert(CFG_KEY_MAX <= MON_WSET_PARAM_MAX,
              "MON_WSET_PARAM_MAX moet minstens CFG_KEY_MAX zijn");
static_assert(CFG_VALUE_MAX <= MON_WSET_VALUE_MAX,
              "MON_WSET_VALUE_MAX moet minstens CFG_VALUE_MAX zijn");

/* POST /api/moncfg met key=<hex van de doelnode>, param=<sleutel>, value=<waarde>.
 *
 * Dezelfde tabel, dezelfde grenzen, dezelfde risicoklassen als /api/cfg -- en dat
 * is de hele opzet. Er is één schrijfweg met twee vervoermiddelen: over IP naar
 * een node die de server bereikt, en over LoRa via deze node naar een repeater
 * die zij monitort. De tweede bestaat omdat er een dakrepeater is die stock
 * MeshCore draait, geen IP-pad heeft en er ook nooit een krijgt. Het aardige
 * eraan is welke node er nieuwe firmware voor nodig heeft: deze, de monitor. Het
 * DOEL hoeft niets te leren en niets te krijgen.
 *
 * Antwoordt 202 en niet 200, en dat is geen kosmetiek: er is nog niets gebeurd.
 * Over IP is een schrijfactie een aanroep van tienden van seconden; hier zijn het
 * twee pakketten over een gedeelde band met een adempauze ertussen, en dat duurt
 * tientallen seconden. De uitslag haal je op met GET /api/moncfg, en die blijft
 * na afloop staan zodat een pagina die intussen herladen is hem alsnog vindt. */
static void handleMonCfgPost(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;

  char key[MON_KEY_HEX_MAX] = "", param[CFG_KEY_MAX] = "", value[CFG_VALUE_MAX] = "";
  copyParam(req, "key", key, sizeof(key));
  copyParam(req, "param", param, sizeof(param));
  copyParam(req, "value", value, sizeof(value));

  static char body[400];
  const CfgParam *p = cfgFind(param);
  if (!p) {
    // Dezelfde tekst voor "bestaat niet" en "mag niet", om dezelfde reden als
    // bij /api/cfg: aftasten levert niets op en de lijst is geen geheim.
    snprintf(body, sizeof(body),
             "{\"ok\":0,\"step\":\"sleutel\",\"msg\":\"deze parameter staat niet op de "
             "lijst van wat er van afstand gezet mag worden\"}");
    req->send(400, "application/json", body);
    return;
  }
  const char *bad = cfgCheckValue(p, value);
  if (bad) {
    static char e_bad[240];
    jsonEsc(e_bad, sizeof(e_bad), bad);
    snprintf(body, sizeof(body),
             "{\"ok\":0,\"step\":\"waarde\",\"msg\":\"%s %s\"}", p->key, e_bad);
    req->send(400, "application/json", body);
    return;
  }

  /* De sleutel uit de tabel en niet die uit het verzoek. Dezelfde afspraak als
   * bij cfgCli(): er staat dan geen tekst van buiten in het commando behalve de
   * waarde, en die is altijd het laatste woord. */
  const char *why = "";
  if (!monWriteRequest(key, p->key, value, &why)) {
    static char e_why[160];
    jsonEsc(e_why, sizeof(e_why), why);
    snprintf(body, sizeof(body), "{\"ok\":0,\"step\":\"monitor\",\"msg\":\"%s\"}", e_why);
    req->send(409, "application/json", body);
    return;
  }
  snprintf(body, sizeof(body),
           "{\"ok\":1,\"step\":\"\",\"busy\":1,\"msg\":\"gevraagd; twee commando's over LoRa, "
           "haal de uitslag op met GET /api/moncfg\"}");
  req->send(202, "application/json", body);
}

/* GET /api/moncfg -- de lopende of laatst afgeronde schrijfactie.
 *
 * Hier, en niet in de toestandsmachine, wordt uitgerekend wat er 'toegepast' en
 * 'precies' heet. Die machine bewaart twee antwoorden als tekst; de betekenis
 * eraan geven gebeurt met cfgSameValue() en cfgStripMarker(), dezelfde functies
 * die /api/cfg gebruikt. Zo bestaat er één opvatting over wanneer
 * "869.525 250 11 5" en "869.525,250,11,5" dezelfde waarde zijn.
 *
 * De velden lopen bewust gelijk met die van POST /api/cfg -- asked, applied,
 * exact, step -- zodat de server er één soort antwoord van maakt en de pagina
 * niet hoeft te weten langs welke weg het gegaan is. Wat er bij komt is 'busy',
 * 'end' en 'seq': dit duurt lang genoeg om ernaar te moeten kijken, en 'seq'
 * zegt of dit nog dezelfde opdracht is als die je zag. */
static void handleMonCfgGet(AsyncWebServerRequest *req) {
  if (!requireAuth(req)) return;

  /* Ruim bemeten met opzet. Elk van de zes tekstvelden kan bij het ontsnappen
   * verdubbelen, en een JSON-antwoord dat halverwege afgekapt wordt is geen
   * onvolledig antwoord maar een onleesbaar antwoord -- de server gooit het weg
   * en de pagina meldt 'niet bereikbaar' over een node die keurig antwoordde. */
  static char body[1200];
  if (_mwr_seq == 0) {
    snprintf(body, sizeof(body),
             "{\"seq\":0,\"busy\":0,\"ok\":0,\"step\":\"\",\"msg\":\"sinds de start van "
             "deze node is er niets geschreven\",\"key\":\"\",\"param\":\"\","
             "\"asked\":\"\",\"applied\":\"\",\"exact\":0,\"reboot\":0,\"end\":\"\",\"age\":0}");
    req->send(200, "application/json", body);
    return;
  }

  bool busy = (_mwr_cur >= 0);
  const CfgParam *p = cfgFind(_mwr_param);

  /* Wat er ná afloop in de node staat, of leeg als er niet teruggelezen kon
   * worden. Dat laatste is een eigen uitslag en geen waarde: een leeg veld met
   * 'end' ernaast zegt "we weten het niet", en dat is hier de eerlijke tekst. */
  const char *applied = _mwr_get_reply[0] ? cfgStripMarker(_mwr_get_reply) : "";
  bool got = applied[0] != 0 && !cfgIsError(applied);
  bool refused = _mwr_set_reply[0] && cfgIsError(_mwr_set_reply);
  bool exact = got && p && cfgSameValue(p, _mwr_asked, applied);
  /* Wel vergeleken, niet verklapt -- net als bij /api/cfg. Het teruglezen is de
   * reden dat dit bestaat, dus dat blijft; alleen gaat de waarde niet mee terug
   * de wereld in. Over LoRa des te meer: dit antwoord reist over het lokale net
   * naar een server die het in een pagina zet. */
  if (p && p->secret && got) applied = "(verborgen)";

  /* 'ok' betekent hier precies één ding: er is teruggelezen en er staat iets.
   * Nadrukkelijk niet "de node zei OK" -- dat is juist de bewering die deze hele
   * weg niet vertrouwt. Een 'set' die met een foutregel antwoordde is niet ok;
   * een 'set' die "OK" zei maar waarvan het teruglezen zweeg ook niet, want dan
   * weten we niet wat er staat. */
  bool ok = !busy && got && !refused;
  const char *step = "";
  if (busy) step = "bezig";
  /* De volgorde is de volgorde van toenemende onzekerheid, en die is hier de
   * hele boodschap. Niet verstuurd = met zekerheid niets veranderd. Verstuurd en
   * stil = we weten het niet. Gezet maar niet gelezen = er staat misschien iets
   * anders dan gevraagd en dat is niet vastgesteld. */
  else if (!_mwr_sent) step = "niet_verstuurd";          // er is niets vertrokken
  else if (!_mwr_set_reply[0]) step = "geen_antwoord";   // de 'set' bleef stil
  else if (refused) step = "node";                       // de node weigerde
  else if (!got) step = "geen_teruglezing";              // wel gezet, niet gelezen

  static char e_key[MON_KEY_HEX_MAX], e_param[CFG_KEY_MAX * 2];
  static char e_asked[CFG_VALUE_MAX * 2], e_applied[MON_WSET_REPLY_MAX * 2];
  static char e_set[MON_WSET_REPLY_MAX * 2], e_end[128];
  jsonEsc(e_key, sizeof(e_key), _mwr_key);
  jsonEsc(e_param, sizeof(e_param), _mwr_param);
  jsonEsc(e_asked, sizeof(e_asked), _mwr_asked);
  jsonEsc(e_applied, sizeof(e_applied), applied);
  jsonEsc(e_set, sizeof(e_set), _mwr_set_reply);
  jsonEsc(e_end, sizeof(e_end), _mwr_end);

  unsigned long since = busy ? (millis() - _mwr_started) : (millis() - _mwr_done_at);
  snprintf(body, sizeof(body),
           "{\"seq\":%lu,\"busy\":%d,\"ok\":%d,\"step\":\"%s\",\"msg\":\"\","
           "\"key\":\"%s\",\"param\":\"%s\",\"asked\":\"%s\",\"applied\":\"%s\","
           "\"exact\":%d,\"reboot\":%d,\"reply\":\"%s\",\"end\":\"%s\",\"age\":%lu}",
           (unsigned long)_mwr_seq, busy ? 1 : 0, ok ? 1 : 0, step,
           e_key, e_param, e_asked, e_applied, exact ? 1 : 0,
           (p && p->reboot) ? 1 : 0, e_set, e_end, (unsigned long)(since / 1000UL));
  req->send(200, "application/json", body);
}

// ------------------------------------------------------------------- console

static void consolePrompt() {
  if (_con_state == CON_USER) _client.print("gebruiker: ");
  else if (_con_state == CON_PASS) _client.print("wachtwoord: ");
  else _client.print("> ");
}

static void consoleWelcome() {
  _client.printf("\r\nMeshCore repeater %s (%s)\r\n",
                 _mesh ? _mesh->getNodeName() : "", board.getManufacturerName());
  _client.print("Log in om de CLI te gebruiken.\r\n");
  _con_state = CON_USER;
  _con_len = 0;
  _con_tries = 0;
  _con_active = millis();
  consolePrompt();
}

static void consoleHandleLine() {
  _con_line[_con_len] = 0;

  if (_con_state == CON_USER) {
    _con_state = (strcmp(_con_line, _cfg.user) == 0) ? CON_PASS : CON_USER;
    if (_con_state == CON_USER) _client.print("onbekende gebruiker\r\n");
  } else if (_con_state == CON_PASS) {
    if (strcmp(_con_line, _cfg.console_pass) == 0) {
      _con_state = CON_READY;
      _client.print("\r\nWelkom. 'help' voor de MeshCore-commando's, "
                    "'wifi' voor de netwerkinstellingen, 'quit' om af te sluiten.\r\n");
    } else {
      _con_state = CON_USER;
      if (++_con_tries >= 3) {
        _client.print("te veel pogingen\r\n");
        _client.stop();
        _con_len = 0;
        return;
      }
      _client.print("onjuist wachtwoord\r\n");
    }
  } else if (_con_line[0]) {
    if (strcmp(_con_line, "quit") == 0 || strcmp(_con_line, "exit") == 0) {
      _client.print("tot ziens\r\n");
      _client.stop();
      _con_len = 0;
      return;
    }
    char reply[160];
    reply[0] = 0;
    if (!mmnet_handle_command(_con_line, reply) && _mesh) {
      _mesh->handleCommand(0, _con_line, reply);
    }
    if (reply[0]) { _client.print(reply); _client.print("\r\n"); }
  }

  _con_len = 0;
  consolePrompt();
}

static void consoleLoop() {
  // Clean up a silent session ourselves, even if the far end never closed it.
  if (_client && _client.connected() && millis() - _con_active > CON_IDLE_MS) {
    _client.stop();
  }

  if (_console.hasClient()) {
    WiFiClient fresh = _console.available();
    bool busy = _client && _client.connected() &&
                (millis() - _con_active < CON_TAKEOVER_MS);
    if (busy) {
      fresh.print("Er is al een sessie actief.\r\n");
      fresh.stop();
    } else {
      if (_client) _client.stop();      // let go of any stale session
      _client = fresh;
      consoleWelcome();
    }
  }
  if (!_client || !_client.connected()) return;

  while (_client.available()) {
    char c = _client.read();
    _con_active = millis();
    if (c == '\n' || c == '\r') {
      if (_con_len > 0 || c == '\r') consoleHandleLine();
    } else if (c >= 32 && _con_len < sizeof(_con_line) - 1) {
      _con_line[_con_len++] = c;
    }
  }
}

// -------------------------------------------------------------- CLI commands

/* Returns the value after 'key' (possibly empty), or NULL when arg does not
 * start with that keyword. Empty is meaningful: 'wifi mqtt host' clears the
 * broker. */
static const char *subArg(const char *arg, const char *key) {
  size_t n = strlen(key);
  if (strncmp(arg, key, n) != 0) return NULL;
  const char *p = arg + n;
  if (*p != 0 && *p != ' ') return NULL;
  while (*p == ' ') p++;
  return p;
}

static bool isOn(const char *v) {
  return strcmp(v, "on") == 0 || strcmp(v, "aan") == 0 || strcmp(v, "1") == 0;
}

/* Every power setting in one table: adding a knob here makes it settable over
 * the mesh without another command branch, which matters for a node you can
 * only reach over the air. */
struct Tunable { const char *name; uint16_t *value; uint16_t lo, hi; };
static const Tunable TUNABLES[] = {
  { "mode",         &_cfg.pwr_mode,     0, 1 },
  { "window",       &_cfg.pwr_window,  30, 3600 },
  { "sleep",        &_cfg.wifi_sleep,   0, 1 },
  { "txpower",      &_cfg.tx_power,     0, 20 },
  { "hyst",         &_cfg.bat_hyst,     0, 20 },
  { "live",         &_cfg.bat_live,     0, 100 },
  { "mon",          &_cfg.bat_mon,      0, 100 },
  { "hold",         &_cfg.full_hold,    0, 1440 },
  { "night_from",   &_cfg.night_from,   0, 24 },
  { "night_to",     &_cfg.night_to,     0, 24 },
  { "night_factor", &_cfg.night_factor, 1, 64 },
};

static void handlePowerCommand(const char *arg, char *reply) {
  const char *v;

  if (*arg == 0) {
    char pwr[96];
    powerSummaryNl(pwr, sizeof(pwr));
    if (_batt_known) {
      snprintf(reply, 155, "%s; accu %u%% (regel >=%u%%), venster %us", pwr,
               (unsigned)_batt_pct, (unsigned)_pwr[_level].pct, (unsigned)_cfg.pwr_window);
    } else {
      snprintf(reply, 155, "%s; accu onbekend, venster %us", pwr, (unsigned)_cfg.pwr_window);
    }
    return;
  }
  if (strcmp(arg, "altijd") == 0 || strcmp(arg, "always") == 0) {
    _cfg.pwr_mode = PWR_ALWAYS;
    saveConfig();
    if (_asleep) wifiWake();
    strcpy(reply, "OK - altijd bereikbaar");
    return;
  }
  if (strcmp(arg, "zuinig") == 0 || strcmp(arg, "save") == 0) {
    _cfg.pwr_mode = PWR_SAVE;
    _awake_until = millis() + (unsigned long)_cfg.pwr_window * 1000UL;
    saveConfig();
    snprintf(reply, 155, "OK - zuinig, nog %us bereikbaar", (unsigned)_cfg.pwr_window);
    return;
  }
  /* The whole table in one string: "95:60,90:120,70:300,0:3600". One command,
   * one atomic replacement, and it fits in a reply -- which matters when the
   * only way in is 160 bytes over the mesh. */
  if ((v = subArg(arg, "rules")) != NULL) {
    if (*v == 0) {
      int q = snprintf(reply, 155, "min %us,", (unsigned)pwrMinInterval());
      for (int i = 0; i < _pwr_n && q < 140; i++) {
        q += snprintf(reply + q, 155 - q, " %u:%u", _pwr[i].pct, _pwr[i].secs);
      }
      return;
    }
    PwrRule tmp[PWR_RULES_MAX];
    int n = 0;
    const char *c = v;
    while (*c && n < PWR_RULES_MAX) {
      while (*c == ' ' || *c == ',') c++;
      if (!*c) break;
      const char *colon = strchr(c, ':');
      if (!colon) { strcpy(reply, "Err - gebruik pct:secs, bv. 95:60,90:120,0:3600"); return; }
      long pct = atol(c), secs = atol(colon + 1);
      if (pct < 0 || pct > 100 || secs < 1 || secs > 65535) {
        strcpy(reply, "Err - pct 0..100, interval 1..65535 s");
        return;
      }
      tmp[n].pct = (uint8_t)pct;
      tmp[n].secs = (uint16_t)secs;
      n++;
      while (*c && *c != ',') c++;
    }
    if (n == 0) { strcpy(reply, "Err - geen regels herkend"); return; }
    memcpy(_pwr, tmp, sizeof(PwrRule) * n);
    _pwr_n = n;
    pwrNormalise();
    pwrSave();
    snprintf(reply, 155, "OK - %d regels, nu elke %us (ondergrens %us)", _pwr_n,
             (unsigned)currentIntervalSecs(), (unsigned)pwrMinInterval());
    return;
  }
  if ((v = subArg(arg, "set")) != NULL) {
    char name[16];
    unsigned val;
    if (sscanf(v, "%15s %u", name, &val) == 2) {
      for (unsigned i = 0; i < sizeof(TUNABLES) / sizeof(TUNABLES[0]); i++) {
        if (strcmp(name, TUNABLES[i].name) != 0) continue;
        if (val < TUNABLES[i].lo || val > TUNABLES[i].hi) {
          snprintf(reply, 155, "Err - %s moet %u..%u zijn", name,
                   (unsigned)TUNABLES[i].lo, (unsigned)TUNABLES[i].hi);
          return;
        }
        *TUNABLES[i].value = (uint16_t)val;
        saveConfig();
        snprintf(reply, 155, "OK - %s=%u, nu elke %us", name, val,
                 (unsigned)currentIntervalSecs());
        return;
      }
    }
    strcpy(reply, "Err - namen: mode window sleep txpower hyst live mon hold "
                  "night_from night_to night_factor. Intervallen: wifi power rules");
    return;
  }
  strcpy(reply, "Err - wifi power [altijd|zuinig|rules [spec]|set <naam> <waarde>]");
}

/* 'wifi clock'. Read-only, and that is the design rather than an omission: the
 * whole point of the feature is that the time comes from a machine which has a
 * reason to know it. A hand typing one at a serial cable does not, and a wrong
 * time typed here would be forwarded to every monitored repeater and could not
 * be walked back afterwards -- see the block comment above MON_CLK_FIRST_MS.
 *
 * What it answers is the question this feature is hard to see from the outside:
 * a node nobody ever sets the time on, and a node that is being told a time it
 * refuses, look identical from the mesh. The counters tell them apart. */
static void handleClockCommand(char *reply) {
  uint32_t now = _mesh ? _mesh->getRTCClock()->getCurrentTime() : 0;
  char stamp[32] = "onbekend";
  if (_mesh && clockPlausible(now)) {
    DateTime dt = DateTime(now);
    snprintf(stamp, sizeof(stamp), "%02d:%02d - %d/%d/%d UTC",
             dt.hour(), dt.minute(), dt.day(), dt.month(), dt.year());
  } else if (_mesh) {
    strcpy(stamp, "NIET GEZET");
  }

  if (_clk_last_ms == 0) {
    snprintf(reply, 155, "klok %s; nooit gezet door de site (%ug/%uw/%uf); "
             "gemonitord: nog geen ronde",
             stamp, (unsigned)_clk_sets, (unsigned)_clk_back, (unsigned)_clk_bad);
    return;
  }
  if (_mclk_last_at == 0) {
    snprintf(reply, 155, "klok %s; site %us geleden (%+lds, %ug/%un/%uw/%uf); "
             "gemonitord: nog geen ronde",
             stamp, (unsigned)((millis() - _clk_last_ms) / 1000UL), _clk_last_delta,
             (unsigned)_clk_sets, (unsigned)_clk_noops, (unsigned)_clk_back,
             (unsigned)_clk_bad);
    return;
  }
  snprintf(reply, 155, "klok %s; site %us geleden (%+lds, %ug/%un/%uw/%uf); "
           "ronde %us geleden: %u gevraagd, %u geantwoord, %u gezet, %u voor, "
           "grootste %+lds",
           stamp, (unsigned)((millis() - _clk_last_ms) / 1000UL), _clk_last_delta,
           (unsigned)_clk_sets, (unsigned)_clk_noops, (unsigned)_clk_back,
           (unsigned)_clk_bad,
           (unsigned)((millis() - _mclk_last_at) / 1000UL),
           (unsigned)_mclk_asked, (unsigned)_mclk_answered,
           (unsigned)_mclk_synced, (unsigned)_mclk_ahead, _mclk_worst);
}

static const char *LOGIN_NL[] = { "nooit geprobeerd", "gelukt", "geen antwoord" };

static void handleMonCommand(const char *arg, char *reply) {
  const char *v;

  if (*arg == 0) {
    int resolved = 0, ok = 0;
    for (int i = 0; i < _mon_count; i++) {
      if (_mon[i].mesh_idx >= 0) resolved++;
      if (_mon[i].login_res == LOGIN_OK) ok++;
    }
    if (_mon_state == MST_IDLE) {
      snprintf(reply, 155, "%d gemonitord (%d bruikbaar, %d ingelogd), elke %us, "
               "volgende ronde over %us",
               _mon_count, resolved, ok, (unsigned)_mon_interval,
               (unsigned)secsLeft(_mon_next_round));
    } else {
      snprintf(reply, 155, "%d gemonitord (%d bruikbaar, %d ingelogd), elke %us, "
               "ronde bezig (stap %d)",
               _mon_count, resolved, ok, (unsigned)_mon_interval, (int)_mon_state);
    }
    return;
  }
  if (_mon_action != MA_NONE) { strcpy(reply, "Err - vorige wijziging nog bezig"); return; }

  if ((v = subArg(arg, "list")) != NULL) {
    int n = (*v) ? atoi(v) : 0;           // optional index; default the first
    if (n < 0 || n >= _mon_count) { strcpy(reply, "Err - geen regel met dat nummer"); return; }
    MonEntry &m = _mon[n];
    snprintf(reply, 155, "%d: %.12s %.14s %s, %s, login %s, %up/%ug/%uv, st%u tl%u nb%u",
             n, m.key, m.name[0] ? m.name : "-",
             m.enabled ? "aan" : "uit",
             m.mesh_idx >= 0 ? "bruikbaar" : "wacht op advert",
             LOGIN_NL[m.login_res < 3 ? m.login_res : 0],
             (unsigned)m.polls, (unsigned)m.oks, (unsigned)m.pubs,
             (unsigned)m.ok_st, (unsigned)m.ok_tl, (unsigned)m.ok_nb);
    return;
  }
  if ((v = subArg(arg, "add")) != NULL) {
    char key[MON_KEY_HEX_MAX], name[MON_NAME_MAX];
    name[0] = 0;
    if (sscanf(v, "%64s %23s", key, name) < 1) { strcpy(reply, "Err - gebruik: wifi mon add <hex> [naam]"); return; }
    strncpy(_ma_key, key, sizeof(_ma_key) - 1); _ma_key[sizeof(_ma_key) - 1] = 0;
    if (!normaliseKey(_ma_key)) {
      snprintf(reply, 155, "Err - sleutel moet hex zijn, minstens %d tekens", MON_MIN_HEX);
      return;
    }
    strncpy(_ma_name, name, sizeof(_ma_name) - 1); _ma_name[sizeof(_ma_name) - 1] = 0;
    _mon_action = MA_ADD;
    snprintf(reply, 155, "OK - %.16s toegevoegd", _ma_key);
    return;
  }
  if ((v = subArg(arg, "del")) != NULL) {
    strncpy(_ma_key, v, sizeof(_ma_key) - 1); _ma_key[sizeof(_ma_key) - 1] = 0;
    if (!normaliseKey(_ma_key)) { strcpy(reply, "Err - ongeldige sleutel"); return; }
    _mon_action = MA_DEL;
    strcpy(reply, "OK - verwijderd");
    return;
  }
  if ((v = subArg(arg, "pass")) != NULL) {
    char key[MON_KEY_HEX_MAX], pw[MON_PASS_MAX];
    pw[0] = 0;
    int got = sscanf(v, "%64s %15s", key, pw);
    if (got < 1) { strcpy(reply, "Err - gebruik: wifi mon pass <hex> [woord]"); return; }
    strncpy(_ma_key, key, sizeof(_ma_key) - 1); _ma_key[sizeof(_ma_key) - 1] = 0;
    if (!normaliseKey(_ma_key)) { strcpy(reply, "Err - ongeldige sleutel"); return; }
    strncpy(_ma_pass, pw, sizeof(_ma_pass) - 1); _ma_pass[sizeof(_ma_pass) - 1] = 0;
    _mon_action = MA_PASS;
    // No password is a real choice: the far side then checks its access list.
    strcpy(reply, pw[0] ? "OK - wachtwoord ingesteld" : "OK - leeg, via hun access list");
    return;
  }
  if ((v = subArg(arg, "on")) != NULL || (v = subArg(arg, "off")) != NULL) {
    bool on = (arg[1] == 'n');
    strncpy(_ma_key, v, sizeof(_ma_key) - 1); _ma_key[sizeof(_ma_key) - 1] = 0;
    if (!normaliseKey(_ma_key)) { strcpy(reply, "Err - ongeldige sleutel"); return; }
    _ma_num = on ? 1 : 0;
    _mon_action = MA_ENABLE;
    strcpy(reply, on ? "OK - monitoren aan" : "OK - monitoren uit");
    return;
  }
  if ((v = subArg(arg, "iv")) != NULL) {
    long secs = atol(v);
    if (secs < 60 || secs > 65535) { strcpy(reply, "Err - interval 60..65535 s"); return; }
    _ma_num = (uint16_t)secs;
    _mon_action = MA_INTERVAL;
    snprintf(reply, 155, "OK - elke %ld s", secs);
    return;
  }
  if (strcmp(arg, "poll") == 0) {
    _mon_action = MA_POLL;
    strcpy(reply, "OK - ronde gestart");
    return;
  }
  /* The same sweep the site asks for over MQTT, reachable from a serial cable,
   * the telnet console and the mesh CLI. Not a convenience: when this fails it
   * fails silently by nature -- a login that works and commands nobody runs --
   * and being able to start one and read 'wifi mon trace' from wherever you are
   * is the difference between diagnosing that and guessing at it. */
  if ((v = subArg(arg, "settings")) != NULL) {
    if (*v == 0) {
      if (_mset_cur >= 0) {
        snprintf(reply, 155, "bezig voor %.12s: parameter %d van %d, %d gelezen",
                 _mon[_mset_cur].key, _mset_next + 1, SET_PARAM_COUNT, _mset_ok);
      } else if (_mset_last_idx < 0 || _mset_last_idx >= _mon_count) {
        // Also when the entry has since been deleted: the index would then name
        // somebody else, and a wrong name is worse than none.
        snprintf(reply, 155, "nog geen sweep gedaan; gebruik: wifi mon settings <hex>");
      } else {
        snprintf(reply, 155, "laatste: %.12s, %d gelezen, %d geen antwoord, %lu min geleden",
                 _mon[_mset_last_idx].key, _mset_last_ok, _mset_last_miss,
                 (unsigned long)((millis() - _mset_done_at) / 60000UL));
      }
      return;
    }
    const char *why = "";
    if (!monSettingsRequest(v, &why)) { snprintf(reply, 155, "Err - %s", why); return; }
    snprintf(reply, 155, "OK - sweep gevraagd, %d parameters over LoRa", SET_PARAM_COUNT);
    return;
  }
  /* De schrijfweg, vanaf een seriële kabel, de telnetconsole of de mesh-CLI.
   *
   * Niet alleen om te kunnen diagnosticeren, al is dat de reden dat 'settings'
   * hierboven bestaat. Deze staat er ook omdat de mesh-CLI de weg is die als
   * laatste wegvalt: WiFi kan weg zijn, de site kan plat liggen, de broker kan
   * weg zijn -- en dan kan een instelling op de dakrepeater nog steeds gezet
   * worden vanaf een telefoon met de companion-app. Dat is precies het geval
   * waarvoor een node op een dak deze module draagt.
   *
   * 'wifi mon set' zonder meer meldt hoe de laatste afliep, en wat er staat is
   * wat er is TERUGGELEZEN -- nooit wat de node op de 'set' antwoordde. */
  if ((v = subArg(arg, "set")) != NULL) {
    if (*v == 0) {
      if (_mwr_cur >= 0) {
        snprintf(reply, 155, "bezig voor %.12s: %s = %s, stap %s",
                 _mon[_mwr_cur].key, _mwr_param, _mwr_asked,
                 _mwr_step == 0 ? "zetten" : "teruglezen");
      } else if (_mwr_seq == 0) {
        snprintf(reply, 155, "nog niets geschreven; gebruik: wifi mon set <hex> <param> <waarde>");
      } else {
        snprintf(reply, 155, "laatste: %.12s %s gevraagd %s, staat nu %s (%s, %lu min geleden)",
                 _mwr_key, _mwr_param, _mwr_asked,
                 _mwr_get_reply[0] ? cfgStripMarker(_mwr_get_reply) : "(niet gelezen)",
                 _mwr_end, (unsigned long)((millis() - _mwr_done_at) / 60000UL));
      }
      return;
    }
    /* <hex> <param> <waarde>. De waarde is alles na de parameter, spaties en al,
     * want 'radio' bestaat uit vier getallen. */
    char key[MON_KEY_HEX_MAX] = "", param[CFG_KEY_MAX] = "";
    const char *p1 = strchr(v, ' ');
    if (!p1) { strcpy(reply, "Err - wifi mon set <hex> <param> <waarde>"); return; }
    size_t klen = (size_t)(p1 - v);
    if (klen >= sizeof(key)) { strcpy(reply, "Err - sleutel te lang"); return; }
    memcpy(key, v, klen); key[klen] = 0;
    while (*p1 == ' ') p1++;
    const char *p2 = strchr(p1, ' ');
    if (!p2) { strcpy(reply, "Err - geen waarde opgegeven"); return; }
    size_t plen = (size_t)(p2 - p1);
    if (plen >= sizeof(param)) { strcpy(reply, "Err - onbekende parameter"); return; }
    memcpy(param, p1, plen); param[plen] = 0;
    while (*p2 == ' ') p2++;

    const CfgParam *cp = cfgFind(param);
    if (!cp) { strcpy(reply, "Err - die parameter staat niet op de lijst"); return; }
    if (strlen(p2) >= CFG_VALUE_MAX) { strcpy(reply, "Err - waarde te lang"); return; }
    const char *bad = cfgCheckValue(cp, p2);
    if (bad) { snprintf(reply, 155, "Err - %s %s", cp->key, bad); return; }

    const char *why = "";
    if (!monWriteRequest(key, cp->key, p2, &why)) { snprintf(reply, 155, "Err - %s", why); return; }
    /* Geen bevestiging hier, en dat is geen vergeetachtigheid. De risicoklassen
     * bewaken een KNOP: die wordt aangeklikt, soms op de verkeerde regel, door
     * iemand die de gevolgen niet overziet. Hier zit een mens die een
     * wachtwoord heeft ingetypt en die de hele regel zelf uitschrijft, precies
     * zoals bij 'set' op de node zelf. Dezelfde afweging als bij de
     * telnetconsole hieronder. */
    snprintf(reply, 155, "OK - %s %s gaat over LoRa naar %.12s; 'wifi mon set' voor de uitslag",
             cp->key, p2, key);
    return;
  }
  if ((v = subArg(arg, "trace")) != NULL) {
    /* One line per call: a CLI reply is 160 bytes, and this has to be readable
     * over the mesh from wherever you happen to be. 0 = newest. */
    if (_mon_trace_n == 0) { strcpy(reply, "geen trace"); return; }
    uint32_t back = (*v) ? (uint32_t)atol(v) : 0;
    uint32_t avail = (_mon_trace_n < MON_TRACE_LINES) ? _mon_trace_n : MON_TRACE_LINES;
    if (back >= avail) { snprintf(reply, 155, "Err - 0..%u", (unsigned)(avail - 1)); return; }
    snprintf(reply, 155, "[%u] %s", (unsigned)back,
             _mon_trace[(_mon_trace_n - 1 - back) % MON_TRACE_LINES]);
    return;
  }
  strcpy(reply, "Err - wifi mon [list <n>|add <hex> [naam]|del <hex>|pass <hex> [woord]|"
                "on <hex>|off <hex>|iv <s>|poll|settings [hex]|set [<hex> <param> <waarde>]|"
                "trace <n>]");
}

static void handleSettingsCommand(const char *arg, char *reply) {
  const char *v;

  if (*arg == 0) {
    if (_set_next >= 0) {
      snprintf(reply, 155, "bezig: %d van %d", _set_next, SET_PARAM_COUNT);
    } else if (_set_done_at == 0) {
      snprintf(reply, 155, "nog niet gelopen, eerste over %us, elke %u min",
               (unsigned)settingsNextIn(), (unsigned)_cfg.set_iv_min);
    } else {
      snprintf(reply, 155, "%d gelezen, %d geen antwoord, %lu min geleden, "
               "volgende over %lu min, elke %u min",
               _set_n, _set_miss, (unsigned long)((millis() - _set_done_at) / 60000UL),
               (unsigned long)(settingsNextIn() / 60), (unsigned)_cfg.set_iv_min);
    }
    return;
  }
  if (strcmp(arg, "now") == 0) {
    _set_force = true;
    snprintf(reply, 155, "OK - sweep gestart, %d parameters", SET_PARAM_COUNT);
    return;
  }
  if ((v = subArg(arg, "list")) != NULL) {
    // One per call: a CLI reply is 160 bytes and this must work over the mesh.
    if (_set_n == 0) { strcpy(reply, "nog niets gelezen"); return; }
    int n = (*v) ? atoi(v) : 0;
    if (n < 0 || n >= _set_n) { snprintf(reply, 155, "Err - 0..%d", _set_n - 1); return; }
    snprintf(reply, 155, "[%d/%d] %s = %s", n, _set_n - 1,
             _set_vals[n].name, _set_vals[n].value);
    return;
  }
  if ((v = subArg(arg, "iv")) != NULL) {
    long mins = atol(v);
    if (mins < 5 || mins > 65535) { strcpy(reply, "Err - interval 5..65535 minuten"); return; }
    _cfg.set_iv_min = (uint16_t)mins;
    saveConfig();
    snprintf(reply, 155, "OK - elke %ld min (%ld u)", mins, mins / 60);
    return;
  }
  strcpy(reply, "Err - wifi settings [now|list <n>|iv <minuten>]");
}

static void handleMqttCommand(const char *arg, char *reply) {
  const char *v;

  if (*arg == 0) {
    snprintf(reply, 155, "%s, broker=%.32s:%u, prefix=%.16s, rx=%s, "
             "stats=%u pkt=%u drop=%u cmd=%u/%u",
             _cfg.mqtt_enabled ? (_mqtt.connected() ? "verbonden" : "niet verbonden") : "uit",
             _cfg.mqtt_host[0] ? _cfg.mqtt_host : "-", (unsigned)_cfg.mqtt_port,
             _cfg.mqtt_prefix, _cfg.mqtt_rx ? "aan" : "uit",
             (unsigned)_stats_count, (unsigned)_rx_count, (unsigned)_drop_count,
             (unsigned)_cmd_count, (unsigned)_cmd_refused);
    return;
  }
  if ((v = subArg(arg, "host")) != NULL) {
    strncpy(_cfg.mqtt_host, v, MQTT_HOST_MAX - 1);
    _cfg.mqtt_host[MQTT_HOST_MAX - 1] = 0;
    _apply_mqtt = true;
    snprintf(reply, 155, "OK - broker=%.60s", _cfg.mqtt_host);
  } else if ((v = subArg(arg, "port")) != NULL) {
    long p = atol(v);
    if (p < 1 || p > 65535) { strcpy(reply, "Err - poort 1..65535"); return; }
    _cfg.mqtt_port = (uint16_t)p;
    _apply_mqtt = true;
    snprintf(reply, 155, "OK - poort=%u", (unsigned)_cfg.mqtt_port);
  } else if ((v = subArg(arg, "user")) != NULL) {
    strncpy(_cfg.mqtt_user, v, MQTT_USER_MAX - 1);
    _cfg.mqtt_user[MQTT_USER_MAX - 1] = 0;
    _apply_mqtt = true;
    strcpy(reply, "OK - gebruiker opgeslagen");
  } else if ((v = subArg(arg, "pass")) != NULL) {
    strncpy(_cfg.mqtt_pass, v, PASS_MAX - 1);
    _cfg.mqtt_pass[PASS_MAX - 1] = 0;
    _apply_mqtt = true;
    strcpy(reply, "OK - wachtwoord opgeslagen");
  } else if ((v = subArg(arg, "prefix")) != NULL) {
    strncpy(_cfg.mqtt_prefix, v, MQTT_PREFIX_MAX - 1);
    _cfg.mqtt_prefix[MQTT_PREFIX_MAX - 1] = 0;
    if (_cfg.mqtt_prefix[0] == 0) strcpy(_cfg.mqtt_prefix, MQTT_PREFIX_DEFAULT);
    _apply_mqtt = true;
    snprintf(reply, 155, "OK - topics %.20s/%s/stats en /rx", _cfg.mqtt_prefix, _node_hex);
  } else if ((v = subArg(arg, "rx")) != NULL) {
    _cfg.mqtt_rx = isOn(v) ? 1 : 0;
    _apply_mqtt = true;
    snprintf(reply, 155, "OK - ruwe pakketten %s", _cfg.mqtt_rx ? "aan" : "uit");
  } else if (strcmp(arg, "on") == 0 || strcmp(arg, "aan") == 0) {
    _cfg.mqtt_enabled = 1;
    _apply_mqtt = true;
    strcpy(reply, "OK - doorsturen aan");
  } else if (strcmp(arg, "off") == 0 || strcmp(arg, "uit") == 0) {
    _cfg.mqtt_enabled = 0;
    _apply_mqtt = true;
    strcpy(reply, "OK - doorsturen uit");
  } else if (strcmp(arg, "test") == 0) {
    strcpy(reply, mqttPublishStats() ? "OK - verstuurd" : "Err - versturen faalde");
  } else {
    strcpy(reply, "Err - wifi mqtt [host|port|user|pass|prefix|rx on|off|on|off|test]");
  }
}

// -------------------------------------------------------------------- public

bool mmnet_is_safe_mode() { return _safe_mode; }

bool mmnet_handle_command(const char *command, char *reply) {
  if (_disabled) return false;   // leave everything to the stock firmware

  if (memcmp(command, "wifi", 4) != 0) {
    /* Both versions in one line: this module and the MeshCore release it is
     * built on. If this module ever disables itself, the answer falls through
     * to stock MeshCore -- so a missing MeshManager name is itself the
     * diagnosis. */
    if (strcmp(command, "ver") == 0) {
      snprintf(reply, 155, "%s v%s - MeshCore %s (Build: %s)",
               MESHMANAGER_NAME, MESHMANAGER_VERSION, FIRMWARE_VERSION, FIRMWARE_BUILD_DATE);
      return true;
    }
    /* 'start ota' hands over to the stock soft-AP updater instead of merely
     * printing the URL of our own /update page.
     *
     * It used to do the latter, on the assumption that an upload over the normal
     * network always works. It does not: uploads to /update have failed
     * repeatedly on real hardware. And because that reply replaced the stock
     * behaviour, the one fallback that did work had been taken away with it. A
     * recovery path must never depend on the thing you are recovering from.
     *
     * Both servers want port 80, so ours has to go first. After this the node
     * only serves the update page until it reboots -- which is precisely what
     * you want from a command whose whole purpose is reflashing. */
    /* 'filter ...' -- het pakketfilter. Hier en niet achter 'wifi', want dit
     * heeft niets met het netwerk te maken: het gaat over wat deze node van
     * andermans verkeer doorstuurt, en het moet werken op een node zonder WiFi.
     *
     * Dat het over de mesh-CLI loopt is de kern van het ontwerp en geen
     * bijvangst. Een filter is de zeldzame instelling die een node nutteloos
     * maakt zonder hem onbereikbaar te maken -- hij antwoordt nog, hij
     * adverteert nog, en hij stuurt niets meer door. 'filter off' en 'filter
     * reset' moeten daarom bereikbaar zijn langs de weg die als laatste
     * wegvalt, en dat is LoRa: die staat al voordat WiFi, de beheerpagina of de
     * server er zijn. Zelfde regel als 'wifi fw rollback'. */
    if (memcmp(command, "filter", 6) == 0 && (command[6] == 0 || command[6] == ' ')) {
      return pf_command(command + 6, reply, 155);
    }
    if (memcmp(command, "start ota", 9) == 0) {
      if (_asleep) {
        strcpy(reply, "WiFi staat uit (zuinig). Eerst 'wifi on 30'.");
        return true;
      }
      _server.end();
      _console.end();
      _started = false;          // stop serving from our own loop
      if (!board.startOTAUpdate(_mesh ? _mesh->getNodeName() : "repeater", reply)) {
        strcpy(reply, "Err - OTA niet beschikbaar in deze build");
      }
      return true;
    }
    return false;
  }

  const char *arg = command + 4;
  while (*arg == ' ') arg++;
  const char *v;

  if (*arg == 0) {
    IPAddress ip = (_state == WIFI_FALLBACK_AP) ? WiFi.softAPIP() : WiFi.localIP();
    char batt[24];
    if (_batt_known) snprintf(batt, sizeof(batt), "%u%%", (unsigned)_batt_pct);
    else strcpy(batt, "onbekend");
    snprintf(reply, 155, "%s, ssid=%.20s, ip=%s, rssi=%d, accu=%s, elke %us",
             stateNameNl(), _state == WIFI_FALLBACK_AP ? _ap_ssid : _cfg.ssid,
             ip.toString().c_str(), (int)WiFi.RSSI(), batt,
             (unsigned)currentIntervalSecs());
  } else if ((v = subArg(arg, "ssid")) != NULL) {
    strncpy(_cfg.ssid, v, SSID_MAX - 1);
    _cfg.ssid[SSID_MAX - 1] = 0;
    saveConfig();
    snprintf(reply, 155, "OK - ssid=%s ('wifi connect' om te verbinden)", _cfg.ssid);
  } else if ((v = subArg(arg, "pass")) != NULL) {
    strncpy(_cfg.pass, v, PASS_MAX - 1);
    _cfg.pass[PASS_MAX - 1] = 0;
    saveConfig();
    strcpy(reply, "OK - wachtwoord opgeslagen ('wifi connect' om te verbinden)");
  } else if (memcmp(arg, "connect", 7) == 0) {
    _state = WIFI_TRYING;
    startSTA();
    strcpy(reply, "OK - verbinden...");
  } else if (memcmp(arg, "ap", 2) == 0) {
    startAP();
    sprintf(reply, "OK - eigen netwerk '%s' actief", _ap_ssid);
  } else if ((v = subArg(arg, "on")) != NULL) {
    /* The way back in when the node is asleep: force WiFi up regardless of
     * mode or battery, and hold it there long enough to actually do something.
     * Deliberately not limited by the battery rules -- being locked out of a
     * node on a roof costs more than the charge does. */
    unsigned mins = (*v) ? (unsigned)atol(v) : FORCE_DEFAULT_MIN;
    if (mins == 0 || mins > 720) mins = FORCE_DEFAULT_MIN;
    _force_until = millis() + (unsigned long)mins * 60000UL;
    if (_asleep) wifiWake();
    snprintf(reply, 155, "OK - wifi %u min geforceerd aan", mins);
  } else if (memcmp(arg, "off", 3) == 0) {
    _force_until = 0;
    if (_cfg.pwr_mode == PWR_SAVE && !_safe_mode) {
      _awake_until = millis();      // powerLoop puts it to sleep this pass
      strcpy(reply, "OK - terug naar zuinig beheer");
    } else {
      strcpy(reply, "OK - terug naar automatisch beheer (modus: altijd bereikbaar)");
    }
  } else if ((v = subArg(arg, "mqtt")) != NULL) {
    handleMqttCommand(v, reply);
  } else if ((v = subArg(arg, "mon")) != NULL) {
    handleMonCommand(v, reply);
  } else if ((v = subArg(arg, "settings")) != NULL) {
    handleSettingsCommand(v, reply);
  } else if ((v = subArg(arg, "power")) != NULL) {
    handlePowerCommand(v, reply);
  } else if ((v = subArg(arg, "fw")) != NULL) {
    handleFwCommand(v, reply);
  } else if (memcmp(arg, "clock", 5) == 0) {
    handleClockCommand(reply);
  } else if ((v = subArg(arg, "console")) != NULL) {
    char u[USER_MAX], p[PASS_MAX];
    if (sscanf(v, "%16s %64s", u, p) == 2) {
      strcpy(_cfg.user, u);
      strcpy(_cfg.console_pass, p);
      saveConfig();
      strcpy(reply, "OK - console-login gewijzigd");
    } else {
      strcpy(reply, "Err - gebruik: wifi console <gebruiker> <wachtwoord>");
    }
  } else if (memcmp(arg, "wdt", 3) == 0) {
    /* Bewijst de hele keten hang -> watchdog -> herstart -> bootteller, zonder
     * het risico ervan. Een oneindige lus zou de node onherroepelijk ophangen
     * als de watchdog niet blijkt te werken, en die hangt op een dak. Dit
     * blokkeert begrensd: slaat de watchdog toe, dan herstart de node halverwege
     * (bewezen); slaat hij niet toe, dan komt de node gewoon terug en weten we
     * dat het net niet gespannen staat -- zonder schade. */
    unsigned long einde = millis() + (WDT_TIMEOUT_S + 10) * 1000UL;
    while (!passed(einde)) { }      // bewust niets aankloppen
    strcpy(reply, "Watchdog sloeg NIET toe - het vangnet werkt niet");
  } else {
    strcpy(reply, "Err - wifi [ssid|pass|connect|ap|on|off|console|mqtt|power|mon|settings|clock|wdt]");
  }
  return true;
}

void mmnet_begin(FS &fs, MyMesh *mesh) {
  _fs = &fs;
  _mesh = mesh;

  checkSafeMode();

  /* Before the _disabled return on purpose: a node that has switched this
   * module off is exactly the one that must still be able to reboot itself out
   * of a hang. */
  wdtBegin();

  if (_disabled) {
    // Even safe mode did not hold. Everything of ours stays off; what remains
    // is a plain MeshCore repeater, with mesh CLI and 'start ota'.
    Serial.println("MeshManagerNet: uitgeschakeld na herhaalde herstarts");
    _started = true;      // only so the boot counter can still be cleared
    return;
  }

  loadConfig();
  if (_cfg_dirty) { saveConfig(); _cfg_dirty = false; }
  pwrLoad();          // after loadConfig: it migrates from those fields
  /* Not armed in safe mode. A node that restarted three times in a row is a
   * node whose configuration is suspect, and of everything on it the packet
   * filter is the one setting that can leave it looking perfectly healthy while
   * forwarding nothing. The rules stay readable so you can see what it would
   * have done; the file is not rewritten, so a clean boot enforces them again.
   * Same reasoning as safe mode itself, applied to the one thing safe mode did
   * not yet cover. */
  pf_begin(fs, !_safe_mode);
  advLoad();          // before the monitors: they borrow names from it
  loadMonitors();
  syncMonitorsToMesh();

  if (_mesh) mesh::Utils::toHex(_node_hex, _mesh->self_id.pub_key, 6);
  snprintf(_ap_ssid, sizeof(_ap_ssid), "MeshManager-%s", _node_hex);

  /* A raw packet becomes over 500 characters in hex, and the neighbour payload
   * grows with the number of neighbours; the default 256-byte buffer is far too
   * small and publish() would silently refuse. */
  _mqtt.setBufferSize(MQTT_PUB_MAX);
  _mqtt.setSocketTimeout(4);
  _mqtt.setKeepAlive(60);
  _mqtt.setCallback(mqttOnMessage);
  _mqtt.setServer(_cfg.mqtt_host, _cfg.mqtt_port);

  updatePowerLevel();

  if (_safe_mode) {
    // Something made this node restart repeatedly. Only its own network and
    // the admin page, so you can get in and put it right.
    Serial.println("MeshManagerNet: VEILIGE MODUS na herhaalde herstarts");
    startAP();
  } else {
    startSTA();
    /* A fresh start in power-save mode still gets a full window: after a power
     * cut you want a chance to reach the node before it goes quiet. */
    _awake_until = millis() + (unsigned long)_cfg.pwr_window * 1000UL;
  }

  _server.on("/", HTTP_GET, [](AsyncWebServerRequest *req) {
    /* send_P streams straight from flash; send() would first copy all 45 kB into
     * a heap String, on a node that also has to keep a mesh running. Anders dan
     * bij de companion is er hier geen gzip-budget: die pagina wordt in één keer
     * in de socketbuffer gelegd en zit daarom klem tegen CONFIG_LWIP_TCP_SND_BUF_
     * DEFAULT, terwijl AsyncWebServer deze in stukken verstuurt naarmate het
     * venster het toelaat. De grens is dus de app-partitie, niet de buffer. */
    req->send_P(200, "text/html; charset=utf-8", PAGE);
  });
  _server.on("/api/status", HTTP_GET, handleStatus);
  _server.on("/api/wifi", HTTP_POST, handleWifiPost);
  _server.on("/api/power", HTTP_POST, handlePowerPost);
  _server.on("/api/mqtt", HTTP_POST, handleMqttPost);
  _server.on("/api/settings", HTTP_POST, handleSettingsPost);
  _server.on("/api/mon", HTTP_GET, handleMonJson);
  _server.on("/api/mon", HTTP_POST, handleMonPost);
  _server.on("/api/backup", HTTP_GET, handleBackup);

  _server.on("/api/restore", HTTP_POST,
    [](AsyncWebServerRequest *req) {                       // upload is in
      char msg[80];
      bool ok = applyRestore(msg, sizeof(msg));
      char body[128];
      snprintf(body, sizeof(body), "{\"ok\":%d,\"msg\":\"%s\"}", ok ? 1 : 0, msg);
      req->send(ok ? 200 : 400, "application/json", body);
      if (ok) {                                            // restart with the restored data
        _reboot_pending = true;
        _reboot_at = millis() + 1500;
      }
    },
    [](AsyncWebServerRequest *req, const String &filename, size_t index,
       uint8_t *data, size_t len, bool final) {            // write it out piece by piece
      static File up;
      if (index == 0) {
        if (!requireAuth(req)) return;
        up = _fs->open(RESTORE_FILE, "w");
      }
      if (up) up.write(data, len);
      if (final && up) up.close();
    });

  /* Our own upgrade path. Registered before AsyncElegantOTA so that a future
   * version of that library claiming /api/* cannot shadow it, and registered
   * unconditionally -- including in safe mode, which is exactly the state in
   * which somebody needs to put a working image back on this node. */
  _server.on("/api/filter", HTTP_GET, handleFilterGet);
  _server.on("/api/filter", HTTP_POST, handleFilterPost);
  _server.on("/api/cfg", HTTP_GET, handleCfgList);
  _server.on("/api/cfg", HTTP_POST, handleCfgPost);
  /* Dezelfde schrijfweg, ander vervoermiddel: /api/cfg zet iets op DEZE node,
   * /api/moncfg laat deze node iets zetten op een repeater die hij monitort,
   * over LoRa. Bewust een tweede pad en niet een vlag op het eerste: het
   * antwoord is van een andere soort (er is nog niets gebeurd) en het duurt
   * tientallen seconden in plaats van tienden. */
  _server.on("/api/moncfg", HTTP_GET, handleMonCfgGet);
  _server.on("/api/moncfg", HTTP_POST, handleMonCfgPost);
  _server.on("/api/fw", HTTP_GET, fwState);
  _server.on("/api/fw", HTTP_POST, fwDone, NULL, fwBody);
  _server.on("/api/fw/rollback", HTTP_POST, fwRollbackPost);

  // The firmware upload behind the same login too: whoever gets in here can
  // write firmware and download your keys.
  AsyncElegantOTA.begin(&_server, _cfg.user, _cfg.console_pass);
  _server.begin();
  _console.begin();
  _console.setNoDelay(true);

  _started = true;
  _mqtt_last_push = millis();
}

void mmnet_loop() {
  if (!_started) return;

  /* First thing every pass, and before any early return below: reaching this
   * line is the proof that loop() is still turning. */
  wdtFeed();

  // Up long enough: this firmware works, so the boot counter may go back to
  // zero. Also while disabled, so the next start tries everything again.
  if (!_boot_cleared && millis() > STABLE_UPTIME_MS) clearBootCount();
  if (_disabled) return;

  // After a restore, wait a moment so the response still reaches the browser.
  if (_reboot_pending && millis() > _reboot_at) ESP.restart();

  // Advert cache: one write once the burst has settled, not one per advert.
  if (_adv_dirty_at != 0 && passed(_adv_dirty_at)) advSave();

  // Same lazy write for the filter rules, and for the same reason: SPIFFS wears.
  pf_loop();

  if (_apply_wifi) {
    _apply_wifi = false;
    saveConfig();
    _state = WIFI_TRYING;
    startSTA();
  }
  if (_apply_mqtt) {
    _apply_mqtt = false;
    saveConfig();
    _mqtt.disconnect();          // reconnect with the new settings
    _mqtt_last_try = 0;
    _mqtt.setServer(_cfg.mqtt_host, _cfg.mqtt_port);
  }
  if (_apply_rules) {
    _apply_rules = false;
    pwrSave();
  }
  if (_apply_power) {
    _apply_power = false;
    saveConfig();
    if (!_asleep) applyRadioTuning();
    if (_cfg.pwr_mode == PWR_SAVE) {
      // Give whoever just pressed Save the full window to keep working.
      _awake_until = millis() + (unsigned long)_cfg.pwr_window * 1000UL;
    }
  }

  powerLoop();
  if (_asleep) return;      // radio off: nothing below has anything to do

  bool up = (WiFi.status() == WL_CONNECTED);

  switch (_state) {
    case WIFI_TRYING:
      if (up) {
        _state = WIFI_OK;
        _state_since = millis();
        applyRadioTuning();
        Serial.printf("MeshManagerNet: verbonden, http://%s/\n",
                      WiFi.localIP().toString().c_str());
      } else if (millis() - _state_since > STA_TIMEOUT_MS) {
        /* In power-save mode, raising an AP nobody is waiting for is the most
         * expensive thing we could do, so we go back to sleep and try again
         * next round. Unless the window was forced -- then someone is standing
         * next to it looking for a network. */
        if (_cfg.pwr_mode == PWR_SAVE && !_safe_mode && !isForced()) {
          wifiSleep();
          return;
        }
        startAP();
      }
      break;

    case WIFI_OK:
      if (!up) {                       // lost the connection: try again
        _state = WIFI_TRYING;
        _state_since = millis();
        startSTA();
      }
      break;

    case WIFI_FALLBACK_AP:
      if (up) {                        // the network is back
        _state = WIFI_OK;
        _state_since = millis();
        WiFi.softAPdisconnect(true);
        WiFi.mode(WIFI_STA);
        applyRadioTuning();
        Serial.printf("MeshManagerNet: netwerk terug, http://%s/\n",
                      WiFi.localIP().toString().c_str());
      } else if (millis() - _last_retry > STA_RETRY_MS) {
        _last_retry = millis();
        startSTA();                    // the AP stays up meanwhile
      }
      break;
  }

  if (!_safe_mode) {
    consoleLoop();
    mqttLoop();
    /* Deliberately below the _asleep return above: polling happens over the
     * mesh, but the answers go out over MQTT. Collecting stats we cannot
     * publish would spend other people's airtime for nothing. */
    applyMonAction();
    monitorLoop();
    settingsLoop();
  }
}
