# Backups

*[Nederlands](nl/backup.md)*

One script, `scripts/backup.sh`, backs up both places this project keeps
state: the SQLite database (repeaters, current values, contacts, packets,
alerts, accounts and settings) and VictoriaMetrics (the measurement history).
It runs on the host next to Docker, needs no extra packages beyond `docker`
itself, and is meant to be run by cron.

## What the script does

1. **A consistent SQLite copy.** Never a plain `cp` of the live file — that
   copies mid-transaction and leaves the WAL behind. Instead the script runs
   the `sqlite3` backup API inside the app container, which copies page by
   page under the database's own lock: the result is a valid database even
   while the site is writing. The database path comes from `app.config`, so a
   legacy `mcs.sqlite3` is found too.
2. **A VictoriaMetrics snapshot.** `GET /snapshot/create` is requested from
   inside the app container (the VM image has no shell of its own, and the VM
   port is deliberately not published to the host). The snapshot directory is
   then copied out with `docker cp`, packed as a `.tar.gz`, and the snapshot
   is deleted server-side again — a snapshot left behind pins old data blocks
   through its hardlinks.
3. **Dated files in `/opt/meshstats/backups/`** —
   `meshmanager-YYYYMMDD-HHMMSS.sqlite3.gz` and
   `victoria-YYYYMMDD-HHMMSS.tar.gz`.
4. **At most 7 of each kind** are kept; the oldest go first. Per kind, on
   purpose: a week with VictoriaMetrics down must not quietly push the last
   good VM backups out with SQLite-only days.

## Half-failure is an outcome, not a crash

If VictoriaMetrics is not running, the SQLite part still completes and the
exit code says what was missed:

| Exit code | Meaning |
|---|---|
| 0 | both parts succeeded |
| 1 | the SQLite part failed (the VM part is then not attempted) |
| 2 | SQLite succeeded, the VictoriaMetrics part did not |

Cron mails (or logs) the output either way; a `2` is the line to go look at
before the history you did not back up becomes history you needed.

## The cron line

The script does **not** install itself; putting it in cron is the operator's
step, once, on the server:

```cron
# Every night at 03:17, with the output in the journal/mail
17 3 * * * /opt/meshmanager/scripts/backup.sh >> /var/log/meshmanager-backup.log 2>&1
```

Adjust the path to where the repository is checked out
(`deploy/install.sh` uses `/opt/meshmanager`). Everything the script assumes
can be overridden through the environment: `BACKUP_DIR`
(default `/opt/meshstats/backups`), `APP_CONTAINER` (`meshmanager`),
`VM_CONTAINER` (`meshmanager-tsdb`), `VM_URL` (`http://victoria:8428`, as seen
from the app container) and `KEEP` (7).

## Restoring

* **SQLite**: stop the site, `gunzip` the copy, put it back as
  `/data/meshmanager.sqlite3` in the `meshstats-data` volume (remove any
  stale `-wal`/`-shm` files next to it), start the site.
* **VictoriaMetrics**: unpack the tarball into
  `/victoria-metrics-data/snapshots/` in the `victoria-data` volume and
  restore it with `vmrestore`, or — simpler for a full restore — stop VM,
  empty its data directory, and copy the snapshot's contents over the
  `data/` and `indexdb/` directories it contains. See the VictoriaMetrics
  documentation for `vmrestore`.

## The honest note about offsite

This script writes to a directory **on the same machine**. A backup that
lives next to what it backs up survives a bad deploy and a fat-fingered
delete, but not a dead disk and not a dead host. Copying
`/opt/meshstats/backups/` to somewhere else — another machine, object
storage, a USB disk in a drawer — is the operator's step, and no line in this
repository can take that responsibility over.
