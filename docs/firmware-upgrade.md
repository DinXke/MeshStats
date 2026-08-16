# Firmware upgrades

*[Nederlands](nl/firmware-upgrade.md)*

How a repeater running `MeshStatsNet` gets a new image: where the image comes
from, which node it is allowed to go to, what is checked before anything is made
permanent, and how to get back if it was a mistake.

The node-side firmware itself is described in [`firmware.md`](firmware.md); this
page is only about replacing it.

---

## The short version

1. A git tag builds the firmware in GitHub Actions and publishes one `.bin` per
   board, each with a `.sha256`, plus the changelog as release notes.
2. The site lists those releases on its admin page, per node, with the installed
   version beside them.
3. Pressing upgrade makes the **server** download the image, check its digest,
   and push it to the node over HTTP.
4. The **node** checks the same digest again, and only then switches the boot
   partition and restarts. A failure leaves it running exactly what it was
   running.
5. If the new image misbehaves, the previous one is still in flash:
   `POST /api/fw/rollback`, or `wifi fw rollback` over the mesh.

---

## Why there are two upgrade paths, and why the old one stays

The node has had an upgrade page at `/update` (AsyncElegantOTA) for a long time.
It still does, and it is not deprecated. But it cannot be the path the site
drives, for a reason that was measured rather than guessed.

On real hardware, an upload of **1,284,538 bytes** sent as
`-F "update=@firmware.bin"` — the field name a person naturally reaches for, and
without an `MD5` field — was accepted in full, thrown away, and followed by a
restart onto the **old** firmware. The caller saw `HTTP 000`. The same binary
sent as `-F "MD5=<md5>" -F "file=@firmware.bin;filename=firmware.bin"` worked.

Three properties combine into that:

| Property | Consequence |
|---|---|
| The image is only recognised under the multipart field name `file`, with an `MD5` field beside it | The wrong field name is a silent discard, not an error |
| The handler restarts the node whether `Update.end()` succeeded or not | "It rebooted" is emitted identically by success and by failure |
| The restart happens before the HTTP response is flushed | There is nothing to read; `curl` reports `000` |

So the only observable signal carries no information. On a repeater on a roof
that is not a rough edge — it is an upgrade path that lies.

**And it stays anyway.** It is the fallback for when the new path is the thing
that is broken, and a recovery route may never depend on the thing you are
recovering from. That is the same rule that gave the mesh command `start ota` its
stock soft-AP behaviour back after an earlier release had replaced it with a
message pointing at `/update`.

The full ladder of fallbacks, most convenient first:

| When | Route | Still works if |
|---|---|---|
| Normal | `POST /api/fw` from the site | — |
| New path broken | `POST /update` with `MD5=` and `file=`, by hand | the module still runs |
| New image will not join WiFi | `wifi fw rollback` over the mesh CLI | LoRa works |
| Module disabled itself (6 restarts) | `start ota` over the mesh CLI, then the soft-AP | MeshCore boots |
| Nothing works | USB | you can reach the node |

---

## The node endpoints

All three sit behind the same HTTP login as the rest of the admin page
(`_cfg.user` / console password). Whoever can write firmware here can also
download the private key through `/api/backup`, so there is no lighter tier.

### `POST /api/fw?sha256=<hex>&size=<bytes>&ver=<label>`

The image is the **raw request body**. Not multipart — the bug above was a
multipart field name, and a format without field names cannot have it.

| Parameter | Required | Meaning |
|---|---|---|
| `sha256` | yes | 64 hex characters, the digest of the whole image |
| `size` | no | expected byte count; refused before the partition is erased if it disagrees with `Content-Length` |
| `ver` | no | a label for logging, e.g. `1.12.0`. Never trusted for anything |

The order of operations is the guarantee:

1. Authenticate. A refusal answers `401` and reads no image.
2. Validate the parameters. A bad digest string fails here, before any flash is
   touched.
3. `Update.begin(size)` — which also rejects an image larger than the partition
   and checks the ESP32 image magic byte, so an HTML error page saved as `.bin`
   fails immediately with a legible reason.
4. Stream to the **inactive** application partition while hashing. This changes
   nothing about what boots.
5. Compare the digest. On a mismatch: `Update.abort()`, and the node carries on
   running the firmware it booted with.
6. Only now `Update.end()`, which writes `otadata`. **This is the only
   definitive act, and it is reached only after the digest matched.**
7. Answer, then restart 1.5 s later — long enough for the answer to leave.

**Only success restarts.** A failed write leaves a healthy system running, and
restarting it is the one action that turns a failed upgrade into an outage.

The answer is always readable JSON:

```json
{"ok":0,"step":"sha","msg":"checksum klopt niet na 1284538 van 1284538 bytes",
 "bytes":1284538,"total":1284538,"want":"ab12…","have":"cd34…",
 "from":"1.11.0","to":"1.12.0","env":"heltec_v4_repeater_meshstats","reboot":0}
```

`step` is one of `auth`, `param`, `bezig`, `begin`, `write`, `sha`, `kort`,
`end`, `leeg`, or empty on success.

### `GET /api/fw`

What is installed, what can be gone back to, and how the last attempt ended.

```json
{"ver":"1.12.0","fw":"v1.17.0","env":"heltec_v4_repeater_meshstats",
 "board":"Heltec V4.3 OLED","busy":0,"got":0,"total":0,
 "run":"app0","other":{"slot":"app1","valid":1,"ver":"1.11.0"},
 "last":{"any":1,"ok":1,"step":"","msg":"","bytes":1284538,"total":1284538}}
```

Behind the login on purpose: `env` plus `ver` is a shopping list for anyone who
would like to write the wrong image here.

### `POST /api/fw/rollback`

Boot the other partition again. See [below](#going-back).

### `wifi fw` and `wifi fw rollback`

The same two, over any CLI — serial, telnet console, **or the mesh**. The mesh
form is the one that matters: an upgrade whose only fault is that it cannot join
the WiFi takes every IP route into the node with it at once, and LoRa comes up
from the radio driver before any of them.

---

## Which image belongs to which node

This is the part that can destroy hardware. An image built for a different board
written to a node on a roof is not recoverable over the air.

**The node reports the PlatformIO environment it was built under**, in
`MESHSTATS_ENV`, set from `$PIOENV` in `platformio.ci.ini`. A release carries one
asset per environment, named `meshstats-<env>-<version>.bin`. The site matches
those two **exactly**, and refuses when it cannot.

The rejected alternative was matching on the board name the node already
reports — `"board":"Heltec V4.3 OLED"` in `/api/status`. That is free-form prose
maintained upstream: it can differ between two boards that take the same binary,
agree between two that do not, and be reworded by a MeshCore release without
anyone here noticing. The env name is the exact key the image was built under,
so image and node either match or they do not, with no judgement in between.

**A node that reports an empty `env` gets no upgrade button.** That is an image
built before 1.12.0, or one built without the flag. The honest outcome is "cannot
determine which image belongs here", not a best guess.

### Adding a board

1. Add an `[env:...]` section to `firmware/platformio.ci.ini`. Point `extends` at
   the right variant from MeshCore's `variants/` tree, and keep
   `-D MESHSTATS_ENV='"$PIOENV"'`.
2. That is all. The release workflow derives its build matrix from the section
   names in that file.

Two boards that take the same binary still get their own entry when their env
names differ, because a node reports the name it was built under and nothing
else.

### Boards that cannot use this at all

`MeshStatsNet` is a WiFi module. On a variant without WiFi it is not compiled in,
there is no HTTP anything, and this page does not apply — those nodes are
upgraded over USB.

A node with the module but **no IP path from the server** cannot be upgraded
either, and that is a permanent state rather than a temporary one for at least
one node in this project. See the next section.

---

## Which nodes can be upgraded

Firmware upgrade is deliberately **not** part of a node's management level
(`unmanaged` / `semi_managed` / `full_managed`). A `full_managed` node can accept
commands over MQTT and still be unable to accept an image, because the two travel
over different things. The site keeps it in a separate key.

| Node | Upgradeable | Why |
|---|---|---|
| Own firmware, reachable over IP from the server, reports an env | yes | there is a path for 1.3 MB and the image is identified |
| Own firmware, no IP path from the server | **no** | see below |
| Own firmware, IP path, but no env reported | **no** | pre-1.12.0 image; cannot tell which asset fits |
| Stock MeshCore, managed through a monitor over LoRa | **no** | not our firmware, and no path anyway |

**Why no IP path means no, permanently.** A firmware image is ~1.28 MB. The only
other route to such a node is LoRa through a monitoring repeater, and at the
radio settings these nodes use (BW 62.5 kHz, SF 8) plus the European duty-cycle
limit, that is on the order of **days** of transmit time — out of all proportion,
quite apart from what it would do to the mesh. There is no clever encoding that
changes the order of magnitude.

So a node reached only through a monitor gets no upgrade button, and an
explanation instead. That is the situation for the roof repeater this project was
built around, and it is expected to stay that way for months.

---

## Going back

An ESP32 with a `default_16MB` partition table has **two application
partitions**, and an OTA never erases the one it is not writing. So the firmware
the node ran before the last upgrade is still in flash, byte for byte, and going
back is one `otadata` write and a restart. No download, no radio, no network
beyond the request itself.

```
POST /api/fw/rollback          # over HTTP
wifi fw rollback               # over serial, console, or the mesh
```

The node refuses when the other slot holds no valid application image — which is
the case on a node that has only ever been flashed over USB, because nothing has
written the second slot yet. **The first upgrade through this path is therefore
also what makes the first rollback possible.**

### It is not automatic, on purpose

The tempting version is "three failed boots, roll back", and this firmware
already has a boot counter for exactly that shape of problem. It is refused
because a solar repeater restarts for reasons that have nothing to do with
firmware: a flat cell in November browns the board out three times in a night,
and an automatic rollback would quietly undo a good upgrade and then keep undoing
it.

The reachability guarantee does not need it either. Three restarts already drop
the node into **safe mode** — its own access point and the admin page, regardless
of what the new image broke. Safe mode is what keeps the node reachable; rollback
is what repairs it, and a repair is a decision.

### What rollback does *not* undo

Nothing outside the application partition. Configuration, keys, the ACL, the
advert cache and the monitor list all live on the data partition and are
untouched by both the upgrade and the rollback — which is the same reason an OTA
does not lose your keys.

That cuts both ways for a **downgrade** (installing an older release than the one
running). The stored files are forgiving by construction: `loadConfig()` fills in
every default first and then lets the file overwrite what it recognises, so an
unknown key is ignored and a missing key keeps its default. An older image
therefore reads a newer file without complaint — it simply ignores settings it
does not know, and those settings stop having an effect until you upgrade again.

The one thing to be aware of is that a setting introduced after the version you
are going back to will appear to have been forgotten, and will be written out of
the file the next time that older image saves its configuration. The site warns
on a downward step for that reason rather than silently allowing it.

---

## Where the images come from

There is no image to install unless something builds one, so releases are part of
this feature rather than a separate concern.

### Tagging a release

```bash
# MESHSTATS_VERSION in MeshStatsNet.h must already say 1.12.0
git tag fw-v1.12.0
git push origin fw-v1.12.0
```

`.github/workflows/firmware-release.yml` then:

1. **refuses** if the tag and `MESHSTATS_VERSION` disagree — a release whose
   assets report a different version than the release does would send the site
   looking for an upgrade that installs something else;
2. reads the build matrix from the `[env:...]` sections of
   `firmware/platformio.ci.ini`;
3. checks out MeshCore at the pinned `MESHCORE_REF`, copies `firmware/src` and
   `firmware/examples` over it, and builds each environment;
4. publishes `meshstats-<env>-<version>.bin` and `.sha256` per environment;
5. uses the changelog block for that version, taken straight out of
   `MeshStatsNet.cpp`, as the release notes. Written once, published twice.

### The build needs no secrets

`platformio.local.ini` is gitignored because it holds WiFi credentials and an
admin password, so CI cannot use it. It does not need to, and that is a property
of the firmware rather than a workaround:

- `WIFI_SSID` / `WIFI_PWD` are read by `loadConfig()` **as defaults**, before
  `/msnet.json` on the data partition is allowed to overwrite them.
- `ADMIN_PASSWORD` is written into MeshCore's prefs only in the defaults block,
  for a node that has no prefs yet.
- An OTA writes an application partition and leaves the data partition alone.

So a node that has been configured once keeps its network and its password across
every image built from placeholders. `firmware/platformio.ci.ini` holds those
placeholders and is committed, precisely because there is nothing in it.

`ADMIN_PASSWORD` is a placeholder rather than empty because it is not only a
value: `#if defined(ADMIN_PASSWORD)` in MeshCore's `ESP32Board.cpp` is what
compiles the stock soft-AP updater into the image at all, and that updater is the
bottom rung of the fallback ladder.

> **These are upgrade images.** Flashing one over USB onto a virgin board gives a
> node with no network and a placeholder password — on a roof, a node you need a
> ladder for. First installs are built from `platformio.local.ini` with real
> values.

---

## Safety nets, and how this path stays out of their way

`MeshStatsNet` already had four, described in [`firmware.md`](firmware.md). The
upgrade path was built to lean on them rather than around them:

| Net | Interaction |
|---|---|
| Boot counter → safe mode after 3 restarts | The upgrade endpoints are registered **unconditionally**, so they work in safe mode — which is exactly the state in which somebody needs to put a working image back |
| Boot counter → module disabled after 6 restarts | The endpoints and `wifi fw` are gone too. What remains is stock MeshCore and `start ota`. This is the floor, and it is deliberate: a command that survives its own module's failure would have to live outside it |
| Task watchdog | Already steps aside while `Update` is running, so it cannot abort a flash write. Our path uses the same `Update` object, so it inherits that |
| Network side starts even on radio failure | Unchanged — a node that cannot talk LoRa can still be reflashed |

Two writers on one `Update` object would interleave their bytes into one
partition, so a second upload while one is running is refused rather than joined.

---

## Threat model

The most important sentence on this page: **a checksum proves integrity, not
authenticity.** The digest that the server checks and the node checks again
proves that the bytes arrived exactly as they left. It proves nothing at all
about who made them. Confusing those two is the classic way to feel safe about
an update channel that is not, so this section says plainly where the line is.

### What is actually checked

| Check | Where | Proves |
|---|---|---|
| SHA-256 against the published `.sha256` | server, before sending | the download is not truncated or corrupted |
| SHA-256 again, over the bytes written | node, before switching partitions | nothing was lost or mangled between server and node |
| ESP32 image header and size | node, inside `Update.begin()`/`Update.end()` | this is *an* application image, of a size that fits |
| HTTP login | node | the uploader has the node's admin credentials |

There is **no code signing and no secure boot**. `Update` will happily accept any
well-formed ESP32 application image. So:

> **Anyone who can reach the node's port 80 and has its credentials can flash
> arbitrary firmware.** That is the same set of people who can already download
> the private key through `/api/backup`. The management network is the boundary,
> and it is the only boundary.

### What the trust chain actually rests on

1. **GitHub**, for storing the release and its assets.
2. **The server's HTTPS connection to GitHub**, for delivering both the image and
   the `.sha256` unmodified. Note that the digest and the image travel the same
   way from the same place: someone who can substitute one can substitute the
   other, so this check catches accidents, not adversaries.
3. **The workflow that built the image**, which is why it builds from a tag in
   this repository and not from somebody's laptop — it makes "which source is my
   roof node running" a question with an answer.
4. **Whoever holds the node's admin credentials**, and whoever can reach the
   network the node is on.

Everything downstream of a compromise at any of those four is unprotected.

### Would a signature help, and can we have one?

Yes to the first, and it looks feasible — but it is **not built today**, and
saying so is more useful than implying otherwise.

The shape it would take: CI signs the image digest with a private key held as a
repository secret; the public key is compiled into the firmware; the node
verifies the signature *at the point where it already compares the digest*, just
before `Update.end()`. Ed25519 is the natural choice because MeshCore already
carries an Ed25519 implementation for node identities, so it costs no new
dependency and very little flash.

That would close the gap at step 1 and 2 above: a substituted asset would no
longer be accepted, because whoever substituted it cannot produce the signature.
It would **not** close step 4 — a signing key in a GitHub secret is still trusted
to GitHub, and someone at the node's USB port or with `start ota` can still flash
anything, because without secure boot the bootloader does not care either.

Two reasons it is not in this release, both worth knowing before someone adds it:

- **A signing scheme is a way to lose a node.** If the key is lost, or the
  verification has a bug, the OTA path stops accepting *any* image — on a
  repeater on a roof. So a signed path must always keep an unsigned fallback
  (`/update`, `start ota`, USB), which means it raises the bar without ever being
  the only door. Worth doing, but not worth rushing.
- **Key handling is a decision, not a detail.** Where the private key lives, who
  can trigger a signing build, and what happens when it is rotated on nodes that
  already carry the old public key are questions with real answers, and picking
  them silently is worse than not signing yet.

Until then: **treat the node's admin password and the network it sits on as the
things that protect it**, because they are.

### Other assumptions

- **The `cmd` MQTT topic is not part of this path.** No upgrade verb was added to
  it, and none should be: that topic is reachable by anyone holding broker
  credentials, and it accepts a short fixed list of words for exactly that
  reason. The only MQTT message this feature sends is `status`, after a
  successful upgrade, to make the node publish its new version instead of leaving
  the site showing the old one until the next scheduled message.
- **The node's `/api/fw` is behind the login too**, including the read-only GET.
  `env` plus `ver` is a shopping list for anyone who would like to write the
  wrong image.
- **The management address is typed by an operator**, and is validated to be
  `http://` or `https://` before it is used — a host field that accepted
  `file:///etc/...` would be a way to make the server read its own disk.
