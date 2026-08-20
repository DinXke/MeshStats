# MeshMoni: monitoring on the phone

*[Nederlands](nl/meshmoni.md)*

The subsite behind `/meshmoni` is a PWA — a web page that installs itself on
the home screen like an app — built for one question: are my services up, and
did anything happen? It shows the sensor nodes with their channels (using the
names from `channel_names`, never bare metric names), history as a chart with
min/average/max and a histogram, a button to poll a node right now, and the
alert list. Behind it sits web push: a notification on the phone the moment an
alert arrives, as a second path next to the mesh messages of the companion
app.

## Access

The subsite sits behind the same login as `/admin`: the same session cookie,
the same login screen. There is no separate PWA login, and therefore no second
place to forget one. Pages redirect to the login screen when there is no
session; the data endpoints (`/meshmoni/api/...`) return a 401 instead, so the
script can point at the login screen itself rather than parse HTML as data.

Polling a node runs down exactly the path of the refresh button in the admin
UI, with the same permission (`node.uitvragen`) and the same line in the audit
trail. Whoever lacks the permission sees the button disabled, with the reason
on it.

## Fresh or nothing: how the subsite treats caching

The service worker stores only the app shell (stylesheet, script, icons).
Measurements and alerts always come from the server and carry
`Cache-Control: no-store`: a measurement from a stale cache is a lie told with
a straight face. When the network drops, the last picture stays on screen and
the stamp at the bottom says how old it is — that is the whole contract.

## Enabling push notifications

Web push stays off until the server has VAPID keys. That is deliberate: the
keys identify this server to the browser vendors' push services, they are
secret, and a key silently generated on first start is a secret nobody knows
deserves a backup. As long as they are empty, the subsite says push is off,
with this reason — the same contract as `MM_FW_NODE_USER`.

### Generating the keys

Once, on any machine that has the server dependencies (`py_vapid` ships with
`pywebpush`):

```sh
python -c "from py_vapid import Vapid02; from cryptography.hazmat.primitives import serialization; import base64; v = Vapid02(); v.generate_keys(); print('MM_VAPID_PRIVATE=' + base64.urlsafe_b64encode(v.private_key.private_numbers().private_value.to_bytes(32, 'big')).rstrip(b'=').decode()); print('MM_VAPID_PUBLIC=' + base64.urlsafe_b64encode(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b'=').decode())"
```

Put the two lines in the `.env`, restart the container, and optionally set
`MM_VAPID_SUBJECT` to an address where a push service can reach you
(`mailto:...`). The private key belongs nowhere but that `.env`: not in the
repo, not in a chat, not in an issue.

### Subscribing and sending

"Meldingen aanzetten" on the overview page asks the browser for permission and
registers the subscription with the server; it lands in the
`push_subscriptions` table. A background loop checks the `alerts` table every
fifteen seconds and sends every new row, encrypted, to every subscription —
the push service cannot read the contents. An endpoint answering 404/410 (app
removed, permission revoked) is cleaned up immediately; an endpoint failing
eight times in a row as well. The notification itself carries the node name
and the channel name, and tapping it opens the alert list.

### The limitations, honestly

* Web push works **over HTTPS only** — the service worker requires it. It
  works on `http://localhost` for development, nowhere else.
* On **iOS**, web push only exists after the site is installed via *Add to
  Home Screen* and opened from that icon; Safari shows the permission prompt
  nowhere else. The subsite says so on screen too.
* Delivery runs through the browser vendor's push service (Google, Apple,
  Mozilla). It may delay or batch; the alert list on the subsite is the
  source, the notification is the bell.

## The alerts

The alert list reads the `alerts` table and shows unacknowledged alerts first;
acknowledging (`acked=1`) removes them from the default view but deletes
nothing — wiping an event because it was seen would make the question "what
happened here last week?" unanswerable. Who fills the table does not matter to
this subsite: the table is the interface, and both the writer and this reader
create it with `CREATE TABLE IF NOT EXISTS` and the same schema, so ordering
never matters.

**Two sources, and the page says which — because they differ in one thing that
matters: age.** An alert labelled *via het mesh* was relayed by a repeater
seconds after the fact. An alert labelled *IP-poll* was **derived**: the server
polls the sensor node's own API every `MM_SENSOR_POLL_S` (300 s by default) and
turns a state transition — a service going down, a reporter falling silent,
mains dropping away — into an alert row. That alert is therefore up to one poll
interval late, and the label says so, with the actual interval filled in. The
derivation exists because the mesh leg node→repeater is a confirmed hardware
fault right now; the moment it works again, the same event would arrive twice —
which is why both rows carry a `kind` and are de-duplicated on
(node, kind, service) within a 15-minute window. One event, one notification,
whichever road wins.
