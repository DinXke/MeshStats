# Back-ups

*[English](../backup.md)*

Eén script, `scripts/backup.sh`, maakt een back-up van beide plaatsen waar dit
project zijn toestand bewaart: de SQLite-databank (repeaters, actuele waarden,
contacten, pakketten, alarmen, accounts en instellingen) en VictoriaMetrics
(de meethistoriek). Het draait op de host naast Docker, heeft buiten `docker`
zelf niets nodig, en is bedoeld om door cron aangeroepen te worden.

## Wat het script doet

1. **Een consistente SQLite-kopie.** Nooit een kale `cp` van het levende
   bestand — die kopieert midden in een transactie en laat de WAL achter. Het
   script draait in plaats daarvan de backup-API van `sqlite3` binnen de
   app-container, die pagina voor pagina kopieert onder het slot van de
   databank zelf: de uitkomst is een geldige databank, ook terwijl de site
   schrijft. Het pad komt uit `app.config`, dus een oude `mcs.sqlite3` wordt
   ook gevonden.
2. **Een VictoriaMetrics-snapshot.** `GET /snapshot/create` wordt vanuit de
   app-container aangevraagd (het VM-image heeft zelf geen shell, en de
   VM-poort wordt met opzet niet naar de host gepubliceerd). De snapshotmap
   wordt daarna met `docker cp` naar buiten gehaald, ingepakt als `.tar.gz`,
   en het snapshot wordt server-side weer verwijderd — een snapshot dat blijft
   staan houdt via zijn hardlinks oude datablokken vast.
3. **Gedateerde bestanden in `/opt/meshstats/backups/`** —
   `meshmanager-JJJJMMDD-UUMMSS.sqlite3.gz` en
   `victoria-JJJJMMDD-UUMMSS.tar.gz`.
4. **Hoogstens 7 van elke soort** blijven staan; de oudste gaan eerst weg. Per
   soort, met opzet: een week met een liggende VictoriaMetrics mag niet
   stilletjes de laatste goede VM-back-ups wegdrukken met alleen-SQLite-dagen.

## Half-falen is een uitkomst, geen crash

Draait VictoriaMetrics niet, dan gaat het SQLite-deel gewoon door en zegt de
exitcode wat er miste:

| Exitcode | Betekenis |
|---|---|
| 0 | beide delen gelukt |
| 1 | het SQLite-deel is mislukt (het VM-deel wordt dan niet meer geprobeerd) |
| 2 | SQLite gelukt, het VictoriaMetrics-deel niet |

Cron mailt (of logt) de uitvoer hoe dan ook; een `2` is de regel om naar te
gaan kijken voordat de historiek die je niet veiligstelde de historiek wordt
die je nodig had.

## De cronregel

Het script installeert zichzelf **niet**; hem in cron zetten is de stap van de
beheerder, eenmalig, op de server:

```cron
# Elke nacht om 03:17, met de uitvoer in het logboek/de mail
17 3 * * * /opt/meshmanager/scripts/backup.sh >> /var/log/meshmanager-backup.log 2>&1
```

Pas het pad aan naar waar de repository staat (`deploy/install.sh` gebruikt
`/opt/meshmanager`). Alles wat het script aanneemt is via de omgeving te
overschrijven: `BACKUP_DIR` (standaard `/opt/meshstats/backups`),
`APP_CONTAINER` (`meshmanager`), `VM_CONTAINER` (`meshmanager-tsdb`),
`VM_URL` (`http://victoria:8428`, gezien vanuit de app-container) en
`KEEP` (7).

## Terugzetten

* **SQLite**: stop de site, `gunzip` de kopie, zet hem terug als
  `/data/meshmanager.sqlite3` in het volume `meshstats-data` (haal eventueel
  achtergebleven `-wal`/`-shm`-bestanden ernaast weg), start de site.
* **VictoriaMetrics**: pak de tarball uit in
  `/victoria-metrics-data/snapshots/` in het volume `victoria-data` en zet hem
  terug met `vmrestore`, of — eenvoudiger bij een volledige terugzetting —
  stop VM, maak zijn datamap leeg en kopieer de inhoud van de snapshot over de
  mappen `data/` en `indexdb/` die erin zitten. Zie de
  VictoriaMetrics-documentatie bij `vmrestore`.

## De eerlijke noot over offsite

Dit script schrijft naar een map **op dezelfde machine**. Een back-up die
naast het origineel woont overleeft een misgelopen deploy en een verkeerde
delete, maar geen kapotte schijf en geen dode host. Het kopiëren van
`/opt/meshstats/backups/` naar ergens anders — een andere machine,
objectopslag, een USB-schijf in een lade — is de stap van de beheerder, en
geen regel in deze repository kan die verantwoordelijkheid overnemen.
