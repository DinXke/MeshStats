# Deploying MeshManager

*[Nederlands](nl/deploy.md)*

MeshManager runs on the host at `10.10.30.144` through Docker Compose (project
`meshstats`, container `meshmanager`, image `meshmanager:latest`). This page
describes how new versions reach that container: on a **git tag**, through a
**healthcheck gate**, with **automatic rollback** when the gate does not close.
It replaces the blind auto-update that rolled out every commit on `main`.

## The old auto-deploy (what it did)

The repository still carries the old machinery under `deploy/`: `autoupdate.sh`,
driven by a systemd timer (`meshmanager-autoupdate.timer` +
`meshmanager-autoupdate.service`, installed by `deploy/install-autoupdate.sh`).
Every five minutes it did, without anyone watching:

```sh
git fetch origin main
# if origin/main moved since the last deploy:
git merge --ff-only origin/main
docker compose build
docker compose up -d --remove-orphans
```

Two properties made this dangerous. It rolled out **every commit on `main`** the
moment it landed — there was no notion of "a version that is ready". And it
**never checked whether the site still lived** after the swap: a build that
succeeded but produced a broken app, or a migration that corrupted the database
at startup, went straight to production and stayed there until someone noticed.
That is how two production outages happened. The healthcheck gate below is the
answer to the second property; deploying on tags is the answer to the first.

## The new model: deploy on tags

A release is a git tag `v*` (semver, e.g. `v1.4.0`). Nothing reaches production
because it landed on `main`; it reaches production because someone tagged it and
ran the deploy. `.github/workflows/release.yml` runs the full server test suite
and a Docker build on every `v*` tag, so a tag whose tests are red never earns
the label "ready to deploy".

On the host you roll out a release with:

```sh
sudo sh scripts/deploy.sh            # newest v* tag
sudo sh scripts/deploy.sh v1.4.0     # a specific tag, commit or ref
```

`scripts/deploy.sh` checks out the ref, builds the image to its **own** tag
(`meshmanager:<gittag>`, not straight to `:latest`), saves the currently running
image as `meshmanager:previous`, points `:latest` at the new build, restarts the
container, and then runs the gate. Build first, swap second: if the build fails,
nothing is switched and the old container keeps running (exit code `2`).

## The healthcheck gate

After the swap the script does not declare victory — it waits for two locks to
close, within a deadline (`HEALTH_DEADLINE_S`, default 120 s):

1. **`State.Health` == `healthy`.** This is the Dockerfile's own `HEALTHCHECK`:
   HTTP 200 on `/`. A container that does not start, keeps restarting, or answers
   500 never becomes healthy.
2. **A functional query inside the container.** Independent of the home page, it
   opens the database read-only (the same way `scripts/backup.sh` does) and
   proves the core tables — `repeaters`, `latest`, `samples`, `packets`,
   `admins`, `settings`, `neighbors`, `contacts` — both **exist** and are
   **queryable**, then reports the packet count and the age of the newest packet.

If either lock does not close before the deadline, the script rolls back and
exits non-zero with the reason.

### How the gate catches migration damage

Migrations run at app startup (`server/app/db.py`, `_migrate` and
`POST_MIGRATIONS`), so a bad migration is one of the ways a deploy breaks a node.
The gate catches it two ways. A migration that **crashes** at startup takes the
HTTP server down with it, so lock 1 (healthy) never closes. A migration that does
**not** crash but leaves the schema wrong — a dropped table, a column renamed the
wrong way — passes a naive "HTTP 200 on `/`" check but fails lock 2, because the
functional query selects from every core table and a broken one raises there.

One honest limit. The newest-packet age is reported, but it is **not** a gate by
default. Just after a restart the database still holds packets from the *old*
version (it lives in a volume that survives the restart), so a fresh timestamp
proves nothing about the new one — and a genuinely dead ingest (the MQTT-credential
outage that cost thirteen minutes of data: the site answered 200 while nothing
came in) only shows once packets *should* have arrived after the restart, which on
a quiet mesh at night they sometimes do not. Set `MM_DEPLOY_MAX_PACKET_AGE_S` to a
number of seconds to make freshness a hard gate — but only on a server with
continuous intake, or a quiet night will roll back a perfectly good release.

## Rollback: what exactly gets restored

Before the swap, the script tags the **currently running image** — captured by
its image id, not by a tag, because tags move and an id does not — as
`meshmanager:previous`. A rollback is therefore literal: `:latest` is pointed
back at `meshmanager:previous` and `docker compose up -d` recreates the container
on that exact image. It restores the **image** (the application code as it ran a
moment ago). It does **not** touch the `/data` volume: the SQLite database and
the VictoriaMetrics history are shared state, and a rollback deliberately leaves
them where they are. This is why a migration must never be destructive to old
columns — a rollback returns the old code, and the old code has to keep reading a
database the new code has already migrated. Additive migrations (the repository's
rule) make that safe; a migration that drops or rewrites data does not, and no
rollback can undo it.

If there was no running container to capture (a first-ever deploy), there is no
`:previous`, the gate becomes a wall without a net, and the script says so loudly
in the log rather than pretending a rollback exists.

## Making a release

1. Make sure `main` is where you want it and green.
2. Tag it: `git tag v1.4.0 && git push origin v1.4.0`.
3. Watch `.github/workflows/release.yml`: it runs the tests and the image build
   on the tag. Red tests mean the tag is not a release — fix and tag again.
4. On the host, once the tag is green, roll it out: `sudo sh scripts/deploy.sh`.
   The gate decides whether it stays up or rolls back; the exit code and the
   dated log say which.

## The switchover checklist (do this on the server)

This is the exact sequence to move from the old five-minute auto-update to the
gated deploy. Do it once, on the host, after you have reviewed this change — the
same way the backup script was switched over.

### Turn off the old auto-update

The old mechanism in this repository is a systemd timer. Stop and disable it, and
remove the unit files so nothing re-enables it:

```sh
sudo systemctl disable --now meshmanager-autoupdate.timer
sudo systemctl status  meshmanager-autoupdate.timer   # confirm: inactive/dead
sudo rm -f /etc/systemd/system/meshmanager-autoupdate.timer \
           /etc/systemd/system/meshmanager-autoupdate.service
sudo systemctl daemon-reload
```

If this host was instead wired up with a plain crontab line (the task called it
"the cron"), find and remove that line too — check `sudo crontab -l` and
`/etc/cron.d/` for anything that calls `deploy/autoupdate.sh` or a bare
`docker compose up -d --build`, and comment it out or delete it.

### Install the new call

The gated deploy is meant to be run **per release**, deliberately, not on a
blind timer — that deliberateness is the whole point of replacing the poll. So
the "installation" is simply: from the deploy clone, run

```sh
sudo sh scripts/deploy.sh
```

each time you cut a release (step 4 of *Making a release*). The output goes to
the screen and to a dated log in `/var/log/meshmanager-deploy-*.log` (or, without
write access there, next to the script). If you want it automated, guard it on
"a new tag exists since the last deploy" the way the old script guarded on a
marker — do **not** put a bare `scripts/deploy.sh` on a five-minute timer, because
it re-deploys the newest tag every run and would rebuild the image each time.

## What breaks if the old cron keeps running

Leaving the old timer (or cron line) enabled next to the new deploy is the one
mistake that undoes everything on this page, quietly. Every five minutes the old
poll runs `git merge --ff-only origin/main` and `docker compose up -d --build`,
which means:

- It **rebuilds `:latest` from the tip of `main`** and swaps the container to it —
  with **no gate**. Within five minutes of a reviewed, gated release, the old poll
  replaces it with an ungated build of whatever is on `main`, reintroducing exactly
  the blind deploy this change removed. Your rollback target `:previous` gets
  overwritten on the next run too.
- It **fights the checkout.** `scripts/deploy.sh` leaves the clone on a detached
  tag; the old poll tries to fast-forward `main` in the same clone, and the two
  produce a confusing git state and two `docker compose up` runs racing each other.

There is no "run both for a while to be safe" here: the two models are mutually
exclusive on one host. Turn the old one off in the same sitting you turn the new
one on.

## Exit codes and logs

`scripts/deploy.sh` says what happened through its exit code, so a cron or a
timer can act on it:

| Exit code | Meaning |
|---|---|
| 0 | deployed and the gate went green |
| 1 | usage error: no `v*` tag found, not a git repo, docker missing |
| 2 | the build failed — nothing was switched, the old container runs on |
| 3 | the gate did not close — rolled back to `meshmanager:previous` |
| 4 | the gate did not close AND the rollback also failed — intervene by hand |

Everything the script does is written to both the screen and a dated log file.
`APP_CONTAINER`, `IMAGE`, `HEALTH_DEADLINE_S`, `POLL_S`, `LOG_DIR` and
`MM_DEPLOY_MAX_PACKET_AGE_S` can all be overridden through the environment.
