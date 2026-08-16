# Uitrollen

*[English](../deployment.md)*

Twee ondersteunde manieren om de server te draaien: Docker Compose (aanbevolen,
brengt zijn eigen broker mee) en een systemd-service op Debian/Ubuntu.

## Docker Compose

```bash
git clone https://github.com/DinXke/MeshStats.git
cd MeshStats
cp .env.example .env          # wachtwoorden aanpassen
./mosquitto/init-passwd.sh    # maakt de MQTT-gebruiker aan
docker compose up -d
```

De site draait op poort **8080**. Bij de eerste start wordt een admin-account
aangemaakt en het wachtwoord één keer afgedrukt:

```bash
docker compose logs meshstats | grep -i wachtwoord
```

Log in op `/admin`, wijzig het wachtwoord, en maak een API-token aan als je ook
over HTTP wilt pushen.

### Services

| Service | Image | Poort | Volumes |
|---|---|---|---|
| `meshstats` | gebouwd uit `./server` | `${MESHSTATS_PORT:-8080}` → 8080 | `meshstats-data:/data` |
| `mosquitto` | `eclipse-mosquitto:2` | `${MQTT_PORT:-1883}` → 1883 | config (ro), `mosquitto-data`, `mosquitto-log` |
| `victoria` | `victoriametrics/victoria-metrics` | geen (alleen intern) | `victoria-data:/victoria-metrics-data` |

`meshstats` verklaart `depends_on: [mosquitto, victoria]`. Dat regelt alleen de
startvolgorde en geen gereedheid — beide clients proberen het uit zichzelf
opnieuw, dus een afhankelijkheid die nog niet draait is geen probleem.

`victoria` publiceert **geen hostpoort**, met opzet: alleen de toepassing praat
ermee, over het compose-netwerk. Hij heeft geen eigen authenticatie, dus
publiceren zou iedereen op het netwerk het schrijf-endpoint in handen geven.

De applicatiecontainer heeft een healthcheck die elke 30 s `/` ophaalt. Er is
**geen apart `/health`-endpoint**; de controle gebruikt de publieke startpagina.

De container draait als **root** (geen `USER`-instructie in de Dockerfile). Als
dat in jouw omgeving telt, voeg dan `user:` toe aan de compose-service en zorg
dat het volume `/data` beschrijfbaar is voor die uid.

### Persistentie

Alles wat de server nodig heeft staat in `/data`:

| Pad | Wat |
|---|---|
| `/data/mcs.sqlite3` | De databank (plus WAL-bestanden) |
| `/data/secret.key` | 32 willekeurige bytes, aangemaakt bij de eerste start, `chmod 0600` |

Maak van beide een back-up. **`secret.key` kwijtraken maakt elke sessiecookie en
elk CSRF-token ongeldig** — iedereen wordt uitgelogd. Het maakt geen API-tokens
ongeldig; die worden met kale SHA-256 gehasht.

De metingen staan er **niet** in. Die leven in het volume `victoria-data`, dat
apart geback-upt moet worden; zie [Tijdreeksdatabank](#tijdreeksdatabank).

## Zonder Docker

```bash
sudo bash deploy/install.sh
```

Op Debian/Ubuntu doet dit:

1. `python3`, `python3-venv` en `rsync` installeren
2. een systeemgebruiker `mcstats` aanmaken
3. `server/` naar `/opt/mc-repeater-stats/server` rsyncen
4. een venv bouwen en `requirements.txt` installeren
5. `mc-repeater-stats.service` installeren en starten

De gegevens staan in `/var/lib/mc-repeater-stats`. De unit draait met
`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp` en
`ReadWritePaths` beperkt tot de datamap.

Het wachtwoord van de eerste start gaat naar het journal:

```bash
journalctl -u mc-repeater-stats | grep -i wachtwoord
```

> **De systemd-unit zet geen `MCS_MQTT_*`-variabelen, dus MQTT-ingest staat uit
> in deze installatie.** Voeg ze toe met een drop-in en breng je eigen broker
> mee:
>
> ```bash
> sudo systemctl edit mc-repeater-stats
> ```
> ```ini
> [Service]
> Environment=MCS_MQTT_HOST=127.0.0.1
> Environment=MCS_MQTT_USER=meshstats
> Environment=MCS_MQTT_PASS=...
> ```
>
> Gebruik liever een `EnvironmentFile=` met modus `0600` dan `Environment=`-regels,
> die via `systemctl show` leesbaar zijn.

`install.sh` opnieuw draaien werkt ter plaatse bij. Het gebruikt
`rsync --delete`, dus alles wat je onder `/opt/mc-repeater-stats/server`
toevoegde, wordt verwijderd.

## Omgevingsvariabelen

### Toepassing

| Variabele | Standaard | Betekenis |
|---|---|---|
| `MCS_DATA_DIR` | `server/data` | Waar de databank en de geheime sleutel staan. Docker zet `/data`; systemd zet `/var/lib/mc-repeater-stats`. |
| `MCS_SITE_NAME` | `MeshCore Repeater Stats` | Titel in de kop |
| `MCS_RETENTION_DAYS` | `180` | Bewaartermijn voor metingen. Wordt overruled door de instelling in de databank als je hem in `/admin` wijzigt. |
| `MCS_HEARTBEAT_MIN` | `5` | Minuten; forceert een grafiekpunt ook als de waarde niet veranderde. Ook aanpasbaar in `/admin`. |
| `MCS_PACKET_RETENTION_DAYS` | `7` | Bewaartermijn voor ruwe pakketten; die komen veel sneller binnen dan metingen. Aanpasbaar in `/admin`, en het is meteen het venster van de heatmap. |
| `MCS_PACKET_MAX_ROWS` | `200000` | FIFO-bovengrens op de pakkettentabel: erboven gaan de oudste pakketten, wat de bewaartermijn ook zegt. Aanpasbaar in `/admin`. |
| `MCS_DB_MAX_MB` | `512` | FIFO-bovengrens op het databankbestand, WAL inbegrepen. Erboven gaan er nog meer van de oudste pakketten. Aanpasbaar in `/admin`. |
| `MCS_PRUNE_MINUTES` | `60` | Minuten tussen twee opruimrondes. Er wordt ook bij het opstarten gesnoeid, maar een server die maanden draait moet er tussenin snoeien. |
| `MCS_MAX_BODY_BYTES` | `2000000` | Grootste request-body die aanvaard wordt, op elke route en methode. Afgedwongen tijdens het lezen, dus een chunked request kan er niet omheen. |
| `MCS_TRUSTED_PROXY_HOPS` | `1` | Hoeveel proxy's er vóór de app staan. De inlogbegrenzing telt zoveel `X-Forwarded-For`-vermeldingen van rechts naar binnen om het clientadres te vinden. Alleen verhogen als je er echt een hop bij zet — zie [`security.md`](../security.md#which-address-gets-counted). |

### MQTT

| Variabele | Standaard (code) | Standaard (compose) |
|---|---|---|
| `MCS_MQTT_HOST` | *(leeg — MQTT uit)* | `mosquitto` |
| `MCS_MQTT_PORT` | `1883` | `1883` |
| `MCS_MQTT_USER` | *(leeg)* | `meshstats` |
| `MCS_MQTT_PASS` | *(leeg)* | uit `.env` |
| `MCS_MQTT_TOPIC` | `meshcore/+/stats` | idem |
| `MCS_MQTT_RX_TOPIC` | `meshcore/+/rx` | idem |
| `MCS_MQTT_CMD_TOPIC` | `meshcore/{node}/cmd` | idem |

`MCS_MQTT_CMD_TOPIC` is het enige topic waarop de site zelf publiceert. Het
draagt precies drie woorden — `settings`, `status` en `time <epoch>` — die een
node vragen nu zijn CLI-instellingen te lezen, nu een statusbericht te sturen, of
zijn klok te zetten. Het vraagt een broker-ACL die elke node zijn eigen
`cmd`-topic laat lezen — zonder dat wordt het abonnement van de node geweigerd en
meldt niets dat ergens. Zie
[`mqtt.md`](../mqtt.md#asking-a-node-for-something) en
[`commanding.md`](commanding.md).

De standaardwaarden in de code en die in compose verschillen. Draai je de
container buiten compose, zet dan `MCS_MQTT_HOST` expliciet, anders blijft de
ingest uit.

Details in [`mqtt.md`](../mqtt.md).

### Kloksynchronisatie

De site publiceert periodiek `time <epoch>` naar elke node die rechtstreeks
publiceert, want een MeshCore-node zet zijn eigen klok nooit goed. Alle vier de
variabelen, en de controles die de server op zijn eigen klok uitvoert voordat er
iets vertrekt, staan in [`clocksync.md`](clocksync.md#configuratie).

| Variabele | Standaard | Betekenis |
|---|---|---|
| `MCS_CLOCKSYNC_ENABLED` | `1` | `0`, `false`, `no`, `nee`, `off` of leeg zet het uit |
| `MCS_CLOCKSYNC_HOURS` | `24` | Uren tussen twee rondes, minimaal 1 |
| `MCS_CLOCKSYNC_MAX_ERROR_S` | `10` | Hoeveel onzekerheid de kernel over zijn eigen klok mag hebben en toch geloofd worden |
| `MCS_CLOCKSYNC_MAX_JUMP_S` | `30` | Hoever de wandklok tegenover de monotone klok mag verschuiven voor het een sprong heet |

Vereist MeshStats-firmware 1.10.0 op de node. In een LXC leest de klokcontrole
de discipline van de **host**-kernel, dus de juistheid van elke klok in het mesh
hangt uiteindelijk aan de NTP-instelling van die host.

### Tijdreeksdatabank

| Variabele | Standaard (code) | Standaard (compose) | Betekenis |
|---|---|---|---|
| `MCS_TSDB_URL` | *(leeg — alles blijft in SQLite)* | `http://victoria:8428` | Basis-URL van VictoriaMetrics |
| `MCS_TSDB_RETENTION` | — | `180d` | Alleen compose; gaat als `-retentionPeriod` naar de container |

Dezelfde valkuil als bij MQTT: **leeg is een ondersteunde configuratie**, geen
kapotte. Draai de container buiten compose zonder `MCS_TSDB_URL` en de site houdt
elke meting in SQLite precies zoals vroeger — uitgedund door de hartslagregel,
maar werkend.

Zie [Tijdreeksdatabank](#tijdreeksdatabank-1) onder Beheer voor wat je moet
nakijken als hij zich misdraagt.

### Alleen compose

| Variabele | Standaard | Betekenis |
|---|---|---|
| `MESHSTATS_PORT` | `8080` | Hostpoort voor de site |
| `MQTT_PORT` | `1883` | Hostpoort voor de broker |

De meeste applicatie-instellingen zijn ook in `/admin` te wijzigen, waar ze in de
databank bewaard worden en voorrang krijgen op de omgeving.

## Achter cloudflared of een reverse proxy

De app draait met `--proxy-headers --forwarded-allow-ips "*"`, dus hij
respecteert `X-Forwarded-Proto`. De `Secure`-vlag van de sessiecookie wordt uit
die header gezet, en dat is wat het inloggen achter een tunnel juist laat werken.

Wijs de tunnel naar `http://localhost:8080`.

> `--forwarded-allow-ips "*"` vertrouwt `X-Forwarded-*` van **elke** bron. Dat is
> prima zolang alleen jouw proxy poort 8080 kan bereiken. Is de poort
> rechtstreeks bereikbaar, dan kan een client die headers vervalsen. Bind de
> container aan loopback (`127.0.0.1:8080:8080`) of zet er een firewall voor.

### cloudflared

```yaml
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json
ingress:
  - hostname: stats.example.com
    service: http://localhost:8080
  - service: http_status:404
```

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name stats.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Caddy

```
stats.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy zet de forwarded-headers zelf.

### Wat je moet beschermen

De server zet `Content-Security-Policy`, `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy` en `Permissions-Policy`
zelf. Hij zet **geen** `Strict-Transport-Security`; voeg die toe op de proxy als
je daar TLS afsluit.

`POST /admin/login` wordt in de toepassing begrensd, per clientadres en per
gebruikersnaam, met een oplopende blokkade — zie
[`security.md`](../security.md#rate-limiting). Die toestand leeft in het
uvicorn-proces en wordt bij een herstart vergeten, dus is de site publiek, dan is
een tweede slot op `/admin*` alsnog de moeite: Cloudflare Access, een IP-witte
lijst, of een snelheidsbegrenzing op de proxy. De rest van de site en
`/api/v1/*` mogen open blijven.

De begrenzing moet weten welk adres van de client is. Zet
`MCS_TRUSTED_PROXY_HOPS` op het aantal proxy's dat je werkelijk vóór de app zet
(standaard `1`); de redenering staat in
[`security.md`](../security.md#which-address-gets-counted).

De MQTT-poort hoeft helemaal niet naar het internet open te staan. Staat elke
node op je eigen netwerk, laat dan de `ports:`-toewijzing van de
`mosquitto`-service weg zodat hij alleen op het compose-netwerk bereikbaar is.

## Bijwerken

```bash
git pull
docker compose build
docker compose up -d
```

Het schema wordt bij het opstarten met `CREATE TABLE IF NOT EXISTS` toegepast; er
is geen migratieframework. Maak een back-up van `/data` voordat je over een
schemawijziging heen bijwerkt.

### Automatisch bijwerken

Voor een compose-installatie die `main` uit zichzelf moet volgen:

```bash
sudo bash deploy/install-autoupdate.sh
```

Dat installeert `meshstats-autoupdate.timer`, die elke vijf minuten
`deploy/autoupdate.sh` draait vanuit de kloon waarin je het installatiescript
uitvoerde. Het script haalt `main` op, stopt stil als er niets nieuws is, en doet
anders precies de handmatige reeks hierboven: `git pull --ff-only`,
`docker compose build`, `docker compose up -d`.

Het **pollt** in plaats van naar een webhook te luisteren, met opzet: de server
kan achter LAN of VPN staan zonder enige inkomende poort, en een vertraging van
hooguit vijf minuten is het niet waard om er een tunnel voor open te houden.

Gedrag bij fouten:

- Een mislukte build raakt de draaiende site nooit. `up -d` wordt pas bereikt
  nadat `build` geslaagd is, dus de containers blijven op de vorige image draaien
  en de fout belandt in het journal.
- De laatst *geslaagd uitgerolde* commit staat in `.git/autoupdate-deployed`,
  zodat een ronde die na de pull strandde bij de volgende tik opnieuw geprobeerd
  wordt in plaats van voor klaar aangezien te worden.
- De pull is `--ff-only`. Een deploy-kloon hoort geen lokale commits te hebben;
  zijn ze er toch, dan is luid stoppen beter dan ongezien een merge op de server
  fabriceren.
- Overlappen kan niet: de service is `Type=oneshot`, en systemd start de unit van
  een timer niet opnieuw zolang de vorige activering nog loopt. Er is geen
  lockbestand omdat er geen nodig is.

De timer is stil van opzet — alleen rondes die werk vonden, of op een fout
stuitten, schrijven iets:

```bash
journalctl -u meshstats-autoupdate -f
systemctl list-timers meshstats-autoupdate.timer
```

De unit draait als root (docker heeft dat nodig); kloon de repository ook als
root, anders weigert git eraan te komen ("dubious ownership").

Dit geldt alleen voor de compose-installatie. De systemd/venv-installatie uit
`install.sh` kopieert de code weg uit de repository en heeft geen compose-stack
om te herbouwen, dus daar is de timer niet van toepassing.

## Beheer

### Back-up

```bash
docker compose exec meshstats \
  sqlite3 /data/mcs.sqlite3 ".backup '/data/backup.sqlite3'"
docker compose cp meshstats:/data/backup.sqlite3 ./backup.sqlite3
docker compose cp meshstats:/data/secret.key ./secret.key
```

Gebruik `.backup` in plaats van het bestand te kopiëren — de databank draait in
WAL-modus en een kale kopie kan inconsistent zijn.

### Het adminwachtwoord opnieuw zetten

```bash
docker compose exec meshstats python -m app.main set-password admin
```

Leest het nieuwe wachtwoord van stdin; minstens 8 tekens.

> Een wachtwoord wijzigen maakt **wél** elke sessie ongeldig die onder het oude
> geslagen is. Elke cookie draagt een HMAC-vingerafdruk van de wachtwoordhash van
> het account, waardoor de `admins`-rij de intrekkingslijst is en er geen
> sessietabel nodig is. De ene uitzondering is de browser die de wijziging via
> `/admin` uitvoerde: die krijgt een verse cookie, zodat wie net het wachtwoord
> wijzigde niet uit zijn eigen beheerpagina gegooid wordt. `/data/secret.key`
> verwijderen en herstarten blijft het botte middel: dat maakt elke sessie **en**
> elk CSRF-token ongeldig. Details in [`admin.md`](admin.md#sessies).

### Tijdreeksdatabank

De metingen wonen in VictoriaMetrics; SQLite doet al de rest, `latest`
inbegrepen. Achtergrond en redenering staan in
[`server.md`](server.md#waar-de-metingen-wonen).

**Bekijk de toestand** in `/admin` → *Metingen (tijdreeksen)*: bereikbaar ja/nee,
geschreven punten, wachtrijdiepte, hoeveel er naar SQLite moesten uitwijken, en
de laatste fout. Dezelfde informatie in het logboek onder `meshstats.tsdb`.

**Met de hand**, vanuit de applicatiecontainer (de databank heeft geen
hostpoort):

```bash
# staat hij aan?
docker compose exec meshstats python -c \
  "import urllib.request;print(urllib.request.urlopen('http://victoria:8428/health').read())"

# welke reeksen bestaan er -- let op de expliciete start/end: zonder die kijkt de
# label-API maar een paar uur terug en denk je dat er gegevens ontbreken
docker compose exec meshstats python -c \
  "import json,urllib.request,time;e=int(time.time());print(len(json.load(urllib.request.urlopen(
   f'http://victoria:8428/api/v1/label/__name__/values?start={e-400*86400}&end={e}'))['data']))"
```

**Als hij onbereikbaar is**, gaat er niets stuk en gaat er niets verloren:
metingen worden in plaats daarvan naar de SQLite-tabel `samples` geschreven en de
grafieken lezen weer daaruit. Je verliest voor die duur resolutie, geen gegevens.
De beheerpagina telt die punten onder *Uitgeweken naar SQLite*.

**Vers geschreven punten zijn niet meteen bevraagbaar.** VictoriaMetrics
indexeert ze in de volgende seconden — gemeten tot ongeveer 8 s voor een burst op
een koude instantie. Op een grafiek over uren zie je dat niet, maar bij het
handmatig testen brengt het je in de war.

**Back-ups** zijn een aparte klus van die van SQLite:

```bash
docker compose stop victoria
docker run --rm -v meshstats_victoria-data:/from -v "$PWD":/to alpine \
  tar czf /to/victoria-backup.tgz -C /from .
docker compose start victoria
```

Voor een live back-up zonder te stoppen gebruik je het eigen
`/snapshot/create`-endpoint van VictoriaMetrics en kopieer je de snapshotmap.

**Terugvallen op alleen SQLite** kost één variabele: zet `MCS_TSDB_URL=` (leeg)
en herstart. De site leest en schrijft weer `samples`. Historiek die ondertussen
naar VictoriaMetrics geschreven is, wordt niet teruggevoegd, dus grafieken tonen
voor die periode een gat tot het weer aangezet wordt.

### Schijfgebruik

In SQLite is `samples` in rijen de grootste — maar alleen als erfenis. Met een
tijdreeksdatabank ingesteld komt er niets meer in behalve tijdens een storing,
dus hij slinkt naarmate zijn bewaartermijn van 180 dagen verstrijkt, en wordt
`packets` de tabel die werkelijk groeit.

Drie grenzen houden de pakkettentabel klein, in deze volgorde toegepast: de
bewaartermijn, een rijmaximum (`MCS_PACKET_MAX_ROWS`), en een maximum op het hele
databankbestand inclusief zijn WAL (`MCS_DB_MAX_MB`). Leeftijd is wat we willen,
de twee bovengrenzen zijn wat we beloven, en botsen ze, dan gaan de oudste
pakketten het eerst. Er draait elk uur een opruimronde (`MCS_PRUNE_MINUTES`),
plus bij het opstarten, bij het opslaan van de instellingen, bij ongeveer elke
500e HTTP-ingest en per 2000 ontvangen MQTT-pakketten. De volledige redenering,
en wanneer het bestand met `VACUUM` herschreven wordt om de ruimte werkelijk terug
te geven, staat in [`retention.md`](retention.md).

Zodra een van de twee bovengrenzen snijdt, is de ingestelde termijn niet gehaald
— en `/admin` zegt dat in plaats van het op te vangen, want een bewaartermijn die
stilzwijgend niet nagekomen wordt, ontdek je pas als iemand zich afvraagt waar
een week grafiek gebleven is.

VictoriaMetrics houdt zijn eigen bewaartermijn (`MCS_TSDB_RETENTION`, 180 d) en
comprimeert tot ruwweg een byte per punt. Een node die om de 10 s met 100
metrieken publiceert is ongeveer 315 miljoen punten per jaar, in de orde van een
paar honderd MB — en dat is waarom volledige resolutie daar betaalbaar is en in
SQLite niet.

`latest`, `contacts` en `repeater_cli` worden nooit gesnoeid. Ze worden begrensd
door het aantal repeaters en contacten, dus dat is meestal geen probleem.

### Logboeken

```bash
docker compose logs -f meshstats
docker compose logs -f mosquitto
journalctl -u mc-repeater-stats -f     # systemd
```

Loggernamen, zodat een filter er een uit kan pikken: `meshstats.mqtt` (ingest en
het ene publicatietopic), `meshstats.tsdb` (de tijdreeksschrijver),
`meshstats.clocksync` (de klokrondes en hun weigeringen), `meshstats.retention`
(snoeien en VACUUM) en `meshstats.countries` (het grenzenbestand bij het
opstarten). Verbindingstoestand, tellers en laatste fout van elk staan ook in
`/admin`.

Twee dingen worden met opzet luid gelogd, want het zijn de toestanden waarin een
functie stopt met werken: een klokronde die op de klokcontrole strandt, en een
opruimronde waarin een bovengrens en niet de bewaartermijn sneed. Allebei op
WARNING.

### De tests draaien

```bash
cd server
pip install -r requirements-dev.txt
python -m pytest
```

`pytest.ini` zet de testmap en het importpad; er is verder niets te
configureren. De tests raken geen netwerk, geen MQTT en geen echte databank:
alles loopt tegen tijdelijke SQLite-bestanden, en `tests/conftest.py` wijst de
datamap van de app naar een wegwerpmap zodat een testrun nooit `server/data/` in
je werkkopie aanmaakt — wat hij anders wél zou doen, compleet met een
`secret.key`, bij de eerste import van `app.config`.

Alle testvectoren zijn gebouwd uit de protocolkennis in
[`protocol.md`](../protocol.md); er staat geen enkel echt, opgevangen pakket in
de testmap.

## Home Assistant-onderdelen

Noch de HA-integratie noch de TCP-proxy hoort bij de compose-stack.

**`mc_repeater_stats`** — kopieer
`homeassistant/custom_components/mc_repeater_stats/` naar je
HA-`config/custom_components/`, herstart, en voeg de integratie toe. Ze vraagt om
de URL van de site en een API-token dat je in `/admin` aanmaakte. Ze vereist de
`meshcore`-integratie, want ze leest diens entiteiten en roept
`meshcore.execute_command` aan.

**`mc-proxy`** — een Home Assistant-add-on, geen Docker-service. Voeg
`https://github.com/DinXke/MeshCore-Proxy` toe als add-onrepository, of wijs HA
naar de map `proxy/`. Verplichte optie: `node_host`. Hij luistert op 5000 en
biedt een health-endpoint op 5001. Gebruik hem alleen als je geen aangepaste
firmware kunt flashen; zie
[`firmware.md`](../firmware.md#if-you-cannot-flash).
