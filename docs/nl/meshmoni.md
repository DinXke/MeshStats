# MeshMoni: monitoring op de telefoon

*[English](../meshmoni.md)*

De subsite achter `/meshmoni` is een PWA — een webpagina die zich als app op
het beginscherm laat zetten — voor één vraag: doen mijn diensten het, en is er
iets gebeurd? Ze toont de sensornodes met hun kanalen (met de namen uit
`channel_names`, nooit kale metricnamen), de historiek als grafiek met
min/gemiddelde/max en een histogram, een knop om een node nú uit te vragen, en
de alertenlijst. Daarachter zit webpush: een melding op de telefoon zodra er
een alert binnenkomt, als tweede weg naast de meshberichten van de
companion-app.

## Toegang

De subsite staat achter dezelfde login als `/admin`: dezelfde sessiekoek,
hetzelfde inlogscherm. Er is geen aparte PWA-login en dus ook geen tweede
plek om die te vergeten. Pagina's leiden zonder sessie om naar het
inlogscherm; de data-endpoints (`/meshmoni/api/...`) geven dan een 401, zodat
het script zelf de weg naar het inlogscherm wijst in plaats van HTML als data
te lezen.

Het uitvragen van een node loopt langs exact de weg van de opvraagknop in de
beheer-UI, met hetzelfde recht (`node.uitvragen`) en dezelfde regel in het
audittrail. Wie het recht niet heeft ziet de knop uitgeschakeld staan, met de
reden erop.

## Vers of niets: hoe de subsite met cache omgaat

De service worker bewaart alléén de app-schil (stylesheet, script, iconen).
Metingen en alerts komen altijd van de server en dragen
`Cache-Control: no-store`: een meting uit een verouderde cache is een leugen
met een stellig gezicht. Valt het netwerk weg, dan blijft het laatste beeld
staan en zegt de stempel onderaan hoe oud het is — dat is de hele afspraak.

## Pushmeldingen aanzetten

Webpush staat uit tot de server VAPID-sleutels heeft. Dat is bewust: de
sleutels identificeren deze server bij de pushdiensten van de
browserleveranciers, ze zijn geheim, en een sleutel die stil bij de eerste
start gegenereerd wordt is een geheim waarvan niemand weet dat het een back-up
verdient. Zolang ze leeg zijn zegt de subsite dat pushmeldingen uitstaan, met
deze reden — dezelfde afspraak als `MM_FW_NODE_USER`.

### Sleutels aanmaken

Eenmalig, op om het even welke machine waar de serverafhankelijkheden staan
(`py_vapid` komt mee met `pywebpush`):

```sh
python -c "from py_vapid import Vapid02; from cryptography.hazmat.primitives import serialization; import base64; v = Vapid02(); v.generate_keys(); print('MM_VAPID_PRIVATE=' + base64.urlsafe_b64encode(v.private_key.private_numbers().private_value.to_bytes(32, 'big')).rstrip(b'=').decode()); print('MM_VAPID_PUBLIC=' + base64.urlsafe_b64encode(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b'=').decode())"
```

Zet de twee regels in de `.env`, herstart de container, en zet desgewenst
`MM_VAPID_SUBJECT` op een adres waarop een pushdienst je kan bereiken
(`mailto:...`). De private sleutel hoort nergens anders dan in die `.env`:
niet in de repo, niet in een chat, niet in een issue.

### Abonneren en versturen

"Meldingen aanzetten" op de overzichtspagina vraagt de browser om toestemming
en meldt het abonnement bij de server aan; dat komt in de tabel
`push_subscriptions`. Een achtergrondlus kijkt elke vijftien seconden in de
`alerts`-tabel en stuurt elke nieuwe rij versleuteld naar elk abonnement — de
pushdienst kan de inhoud niet lezen. Een endpoint dat 404/410 antwoordt (app
verwijderd, toestemming ingetrokken) wordt meteen opgeruimd; een endpoint dat
acht keer op rij faalt ook. De melding zelf draagt de nodenaam en de
kanaalnaam, en een tik erop opent de alertenlijst.

### De beperkingen, eerlijk

* Webpush werkt **alleen over HTTPS** — de service worker eist dat. Op
  `http://localhost` werkt het voor ontwikkeling, elders niet.
* Op **iOS** bestaat webpush pas nadat de site via *Zet op beginscherm* als
  app geïnstalleerd is én vanaf dat icoon geopend wordt; Safari toont de
  toestemmingsvraag nergens anders. De subsite zegt dat ook op het scherm.
* De bezorging loopt via de pushdienst van de browserleverancier (Google,
  Apple, Mozilla). Die kan vertragen of bundelen; de alertenlijst op de
  subsite is de bron, de melding is de bel.

## De alerts

De alertenlijst leest de tabel `alerts` en toont onbevestigde alerts eerst;
bevestigen (`acked=1`) haalt ze uit de standaardweergave maar verwijdert
niets — een gebeurtenis wegpoetsen omdat ze gezien is zou de vraag "wat is
hier vorige week gebeurd?" onbeantwoordbaar maken. Wie de tabel vult maakt
voor deze subsite niet uit: de tabel is het koppelvlak, en zowel de vuller als
deze lezer maken haar aan met `CREATE TABLE IF NOT EXISTS` en hetzelfde
schema, zodat de volgorde niet uitmaakt.

**Twee bronnen, en de pagina zegt welke — want ze verschillen in het ene dat
telt: de ouderdom.** Een alert met het label *via het mesh* is door een repeater
doorgezet, seconden na het feit. Een alert met het label *IP-poll* is
**afgeleid**: de server leest elke `MM_SENSOR_POLL_S` (standaard 300 s) de eigen
API van de sensornode uit en maakt van een overgang — een dienst die neergaat,
een melder die stilvalt, netvoeding die wegvalt — een alertrij. Zo'n alert is
dus tot een heel pollinterval laat, en het label zegt dat, met het echte
interval erin. De afleiding bestaat omdat de mesh-schakel node→repeater op dit
moment een bevestigd hardwaredefect is; zodra die weer werkt, zou dezelfde
gebeurtenis twee keer binnenkomen — en daarom dragen beide rijen een `kind` en
worden ze ontdubbeld op (node, soort, dienst) binnen een venster van vijftien
minuten. Eén gebeurtenis, één melding, welke weg ook wint.
