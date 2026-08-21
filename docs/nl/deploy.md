# MeshManager uitrollen

*[English](../deploy.md)*

MeshManager draait op de host `10.10.30.144` via Docker Compose (project
`meshstats`, container `meshmanager`, image `meshmanager:latest`). Deze pagina
beschrijft hoe nieuwe versies bij die container komen: op een **git-tag**, door
een **healthcheck-poort**, met **automatische rollback** als de poort niet
dichtgaat. Het vervangt de blinde autoupdate die elke commit op `main` uitrolde.

## De oude auto-deploy (wat hij deed)

De repo draagt de oude machinerie nog onder `deploy/`: `autoupdate.sh`,
aangedreven door een systemd-timer (`meshmanager-autoupdate.timer` +
`meshmanager-autoupdate.service`, geïnstalleerd door
`deploy/install-autoupdate.sh`). Elke vijf minuten deed die, zonder dat iemand
keek:

```sh
git fetch origin main
# als origin/main sinds de vorige deploy verschoven is:
git merge --ff-only origin/main
docker compose build
docker compose up -d --remove-orphans
```

Twee eigenschappen maakten dit gevaarlijk. Hij rolde **elke commit op `main`**
uit zodra die binnenkwam — er was geen begrip van "een versie die klaar is". En
hij **keek nooit of de site na de wissel nog leefde**: een build die slaagde maar
een kapotte app opleverde, of een migratie die de databank bij het opstarten
sloopte, ging rechtstreeks naar productie en bleef daar tot iemand het merkte. Zo
zijn er twee productiestoringen ontstaan. De healthcheck-poort hieronder is het
antwoord op het tweede; uitrollen op tags is het antwoord op het eerste.

## Het nieuwe model: uitrollen op tags

Een release is een git-tag `v*` (semver, bijv. `v1.4.0`). Niets komt in productie
omdat het op `main` belandde; het komt in productie omdat iemand het getagd heeft
en de deploy heeft gedraaid. `.github/workflows/release.yml` draait op elke
`v*`-tag de volledige testsuite en een Docker-build, zodat een tag met rode tests
nooit de status "klaar om te deployen" haalt.

Op de host rol je een release uit met:

```sh
sudo sh scripts/deploy.sh            # nieuwste tag v*
sudo sh scripts/deploy.sh v1.4.0     # een bepaalde tag, commit of ref
```

`scripts/deploy.sh` checkt de ref uit, bouwt het image naar een **eigen** tag
(`meshmanager:<gittag>`, niet meteen naar `:latest`), bewaart de nu draaiende
image als `meshmanager:previous`, laat `:latest` naar de nieuwe build wijzen,
herstart de container en draait dan de poort. Eerst bouwen, dan pas wisselen:
faalt de build, dan is er niets gewisseld en draait de oude container door
(exitcode `2`).

## De healthcheck-poort

Na de wissel roept het script geen overwinning uit — het wacht tot twee sloten
dichtgaan, binnen een deadline (`HEALTH_DEADLINE_S`, standaard 120 s):

1. **`State.Health` == `healthy`.** Dat is de eigen `HEALTHCHECK` van de
   Dockerfile: HTTP 200 op `/`. Een container die niet opstart, blijft
   herstarten of 500 geeft, wordt nooit healthy.
2. **Een functionele query ín de container.** Los van de startpagina opent hij de
   databank read-only (net zoals `scripts/backup.sh` doet) en bewijst dat de
   kern-tabellen — `repeaters`, `latest`, `samples`, `packets`, `admins`,
   `settings`, `neighbors`, `contacts` — zowel **bestaan** als **te bevragen**
   zijn, en meldt daarna het aantal pakketten en de leeftijd van het nieuwste.

Gaat een van beide sloten niet dicht voor de deadline, dan rolt het script terug
en stopt met een niet-nul exitcode en de reden erbij.

### Hoe de poort migratieschade vangt

Migraties draaien bij het opstarten van de app (`server/app/db.py`, `_migrate` en
`POST_MIGRATIONS`), dus een slechte migratie is een van de manieren waarop een
deploy een node breekt. De poort vangt dat op twee manieren. Een migratie die bij
het opstarten **crasht**, sleurt de HTTP-server mee, dus slot 1 (healthy) gaat
nooit dicht. Een migratie die niet crasht maar het schema scheeftrekt — een
weggevallen tabel, een verkeerd hernoemde kolom — komt langs een naïeve "HTTP 200
op `/`"-controle, maar zakt door slot 2, want de functionele query bevraagt elke
kern-tabel en een kapotte struikelt daar.

Eén eerlijke grens. De leeftijd van het nieuwste pakket wordt gemeld, maar is
standaard **geen** poort. Vlak na een herstart bevat de databank nog pakketten
van de *oude* versie (hij leeft in een volume dat de herstart overleeft), dus een
verse tijdstempel bewijst niets over de nieuwe — en een echt dode ingest (de
MQTT-storing die dertien minuten datastroom kostte: de site gaf 200 terwijl er
niets binnenkwam) zie je pas als er ná de herstart pakketten hadden moeten komen,
en op een rustig mesh 's nachts komen die er soms even niet. Zet
`MM_DEPLOY_MAX_PACKET_AGE_S` op een aantal seconden om versheid een harde poort te
maken — maar alleen op een server met continue instroom, anders rolt een rustige
nacht een prima release terug.

## Rollback: wat er precies wordt teruggezet

Vóór de wissel tagt het script de **nu draaiende image** — vastgelegd op zijn
image-id, niet op een tag, want tags schuiven en een id niet — als
`meshmanager:previous`. Een rollback is dus letterlijk: `:latest` wijst weer naar
`meshmanager:previous` en `docker compose up -d` herbouwt de container op precies
die image. Hij zet het **image** terug (de applicatiecode zoals die een moment
eerder draaide). Hij raakt het `/data`-volume **niet**: de SQLite-databank en de
VictoriaMetrics-historiek zijn gedeelde toestand, en een rollback laat die bewust
staan. Daarom mag een migratie nooit destructief zijn voor oude kolommen — een
rollback geeft de oude code terug, en die oude code moet een databank blijven
lezen die de nieuwe al gemigreerd heeft. Additieve migraties (de regel van deze
repo) maken dat veilig; een migratie die gegevens weggooit of herschrijft niet,
en geen rollback maakt dat ongedaan.

Was er geen draaiende container om vast te leggen (een allereerste deploy), dan is
er geen `:previous`, wordt de poort een muur zonder vangnet, en zegt het script
dat luid in het log in plaats van te doen alsof er een rollback is.

## Een release maken

1. Zorg dat `main` staat waar je hem wilt en groen is.
2. Tag hem: `git tag v1.4.0 && git push origin v1.4.0`.
3. Kijk naar `.github/workflows/release.yml`: die draait de tests en de
   image-build op de tag. Rode tests betekenen dat de tag geen release is —
   repareer en tag opnieuw.
4. Op de host, zodra de tag groen is, rol hem uit: `sudo sh scripts/deploy.sh`.
   De poort beslist of hij blijft staan of terugrolt; de exitcode en het
   gedateerde log zeggen welke van de twee.

## De omschakel-checklist (dit doe jij op de server)

Dit is de exacte volgorde om van de oude vijf-minuten-autoupdate naar de gepoorte
deploy over te stappen. Doe het één keer, op de host, nadat je deze wijziging
nagelezen hebt — net zoals het back-upscript werd omgeschakeld.

### De oude autoupdate uitzetten

De oude machinerie in deze repo is een systemd-timer. Stop en schakel hem uit, en
haal de unit-bestanden weg zodat niets hem weer aanzet:

```sh
sudo systemctl disable --now meshmanager-autoupdate.timer
sudo systemctl status  meshmanager-autoupdate.timer   # bevestig: inactive/dead
sudo rm -f /etc/systemd/system/meshmanager-autoupdate.timer \
           /etc/systemd/system/meshmanager-autoupdate.service
sudo systemctl daemon-reload
```

Is deze host in plaats daarvan met een gewone crontab-regel bedraad (de opdracht
noemde het "de cron"), haal dan ook die regel weg — kijk in `sudo crontab -l` en
`/etc/cron.d/` naar alles wat `deploy/autoupdate.sh` of een kaal
`docker compose up -d --build` aanroept, en zet het uit of gooi het weg.

### De nieuwe aanroep installeren

De gepoorte deploy is bedoeld om **per release** te draaien, bewust, en niet op
een blinde timer — die bewustheid is de hele reden dat de poll vervangen wordt.
De "installatie" is dus simpelweg: draai vanuit de deploy-kloon

```sh
sudo sh scripts/deploy.sh
```

telkens als je een release maakt (stap 4 van *Een release maken*). De uitvoer
gaat naar het scherm en naar een gedateerd log in
`/var/log/meshmanager-deploy-*.log` (of, zonder schrijfrecht daar, naast het
script). Wil je het automatiseren, bewaak het dan op "er is een nieuwe tag sinds
de vorige deploy", zoals het oude script op een marker bewaakte — zet **geen**
kale `scripts/deploy.sh` op een vijf-minuten-timer, want die rolt elke ronde de
nieuwste tag opnieuw uit en zou het image telkens herbouwen.

## Wat er misgaat als de oude cron blijft staan

De oude timer (of cron-regel) aan laten staan naast de nieuwe deploy is de ene
fout die alles op deze pagina ongemerkt ongedaan maakt. Elke vijf minuten draait
de oude poll `git merge --ff-only origin/main` en `docker compose up -d --build`,
en dat betekent:

- Hij **herbouwt `:latest` vanaf de kop van `main`** en wisselt de container
  daarheen — **zonder poort**. Binnen vijf minuten na een nagekeken, gepoorte
  release vervangt de oude poll hem door een ongepoorte build van wat er op `main`
  staat, precies de blinde deploy terug die deze wijziging weghaalde. Je
  rollback-doel `:previous` wordt de ronde erna ook overschreven.
- Hij **vecht met de checkout.** `scripts/deploy.sh` laat de kloon op een detached
  tag staan; de oude poll wil in diezelfde kloon `main` fast-forwarden, en die
  twee leveren een verwarrende git-toestand op en twee `docker compose up`-runs
  die om elkaar heen racen.

Er is hier geen "laat ze allebei een tijdje draaien voor de zekerheid": de twee
modellen sluiten elkaar op één host uit. Zet de oude uit in dezelfde zit waarin je
de nieuwe aanzet.

## Exitcodes en logs

`scripts/deploy.sh` zegt via zijn exitcode wat er gebeurd is, zodat een cron of
een timer erop kan reageren:

| Exitcode | Betekenis |
|---|---|
| 0 | uitgerold en de poort ging groen |
| 1 | gebruiksfout: geen tag `v*` gevonden, geen git-repo, docker ontbreekt |
| 2 | de build mislukte — er is niets gewisseld, de oude container draait door |
| 3 | de poort ging niet dicht — teruggerold naar `meshmanager:previous` |
| 4 | de poort ging niet dicht ÉN de rollback mislukte ook — met de hand erbij |

Alles wat het script doet, wordt naar het scherm én naar een gedateerd logbestand
geschreven. `APP_CONTAINER`, `IMAGE`, `HEALTH_DEADLINE_S`, `POLL_S`, `LOG_DIR` en
`MM_DEPLOY_MAX_PACKET_AGE_S` zijn allemaal via de omgeving te overrulen.
