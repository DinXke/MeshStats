# Per-node web credentials

*[Nederlands](nl/per-node-credentials.md)*

MeshManager reaches a sensor node's own HTTP API over IP -- `/status.json`,
`/cfg.json`, `/acl.json`, `POST /cli` -- behind HTTP Basic auth. This page
describes how each node can have its **own** web login that the server knows and
uses, instead of one shared login for the whole fleet.

## The weakness this removes

Until this feature, every node authenticated with the same pair,
`MM_FW_NODE_USER`/`MM_FW_NODE_PASS` (the "fleet" credential). That is the exact
same credential the server uses to write firmware and settings to **every** node.
So one leaked node -- a sniffed HTTP session, a node that ends up in the wrong
hands, a backup that walks -- handed out the keys to the whole fleet.

A per-node login does not make any single node harder to sniff. What it does is
contain the blast radius: a leak of node A no longer opens nodes B and C.

## The model

Two nullable columns live on the node's row (`repeaters`): `web_user` (plain
text -- a username is not a secret, and the column must be searchable to find the
row for an address) and `web_pass_enc` (the password, obfuscated -- see below).

`NULL` in `web_user` means exactly "this node has no own login yet, use the fleet
credential". That is the fallback that keeps existing nodes working after the
update, until each one has been rotated once. When a node does have its own login,
the server uses it for every IP connection to that node's `sensor_host` or
`ota_host`; the choice happens in one place, `firmware._auth_header`, which every
outgoing request to a node passes through.

## How rotation works

Rotation lives behind the same boundary as setting a management address: only a
**server administrator** may do it (the same non-delegatable check as filling in
`sensor_host`), plus the ordinary `node.beheeradres` right on that node. The
button sits next to the credential status on the node page, behind CSRF.

The sequence is the whole safety of the operation:

1. Generate a strong, random `user` + `pass`.
2. Call the node at `POST /web/cred` with its **current** credential, with body
   `{"user": "<new>", "pass": "<new>"}`. The node answers `200 {"ok":1}` and uses
   the new credential from its next request; an empty password is rejected `400`.
3. Store the new login **only after** a `200`.

If the node call fails -- address refused, no answer, an HTTP error, or any answer
that is not `{"ok":1}` -- nothing about the stored credential changes, and the
failure is reported. That order is deliberate: storing first and trying second
would lock you out the moment the node does not follow, because the server would
then be knocking with a password the node never accepted. Every rotation, success
or failure, writes an audit line (never the password itself).

## Bootstrap

The very first rotation has no per-node login to authenticate with yet. The
"current" credential is therefore the per-node login if one exists, and otherwise
the fleet credential. This falls out naturally: `firmware._auth_header` looks at
what is stored right now, which is still the old value until step 3 above has run.
So the first rotation authenticates with the fleet credential and, on success,
replaces it with the node's own. If neither a per-node login nor a fleet
credential exists, there is nothing to authenticate the change with, and rotation
says so instead of failing at the node with a 401.

## What is stored, and how it is obfuscated

The password is turned into a blob with a key derived from the installation
secret (`config.SECRET`, the `secret.key` file next to the database), using
HMAC-SHA256 as a keystream with a fresh random nonce per value and a short
authenticity tag. This is stdlib only, the same line the project already uses for
sessions and CSRF tokens; it needs no extra dependency.

Be honest about what this is: it is **obfuscation, not encryption**. Anyone who
can read the database can usually also read `secret.key` -- they sit in the same
data directory -- and can then reverse the blob. What it buys is that the password
is not sitting in plain text in a backup you might email or paste. It never
appears in `/status.json`, never reaches the UI, and never enters the audit trail.
The fresh nonce means two nodes with the same password do not get the same blob,
so the database does not leak which nodes were set alike.

## The honest limit: Basic-auth over HTTP

A per-node credential still travels **readable over the LAN** on every request,
because Basic auth over plain HTTP sends `user:pass` base64-encoded, not
encrypted. So this feature limits the damage of one leak (one node instead of the
fleet); it does not replace transport security. Anyone who can passively watch the
wire between the server and a node can still read that node's login. The real
defences against that are TLS on the node's web server, or a separate management
VLAN that the traffic cannot be watched from -- both out of scope here, and both
still worth doing. This feature and those are complementary, not substitutes.

## Fallback and rollback safety

The migration is additive: two nullable columns, no data rewrite. That matters for
the deploy gate, which can put older code back on an already-migrated database --
old code that does not know these columns simply leaves them alone, and nodes fall
back to the fleet credential, exactly as before. Clearing a node's login
(`clear_node_web_cred`) puts that node back on the fleet credential too, for the
case where a node has been reset and its web login is back at the shared value: a
stored credential that no longer matches the node is worse than none, because the
server would keep knocking with a dead password.
