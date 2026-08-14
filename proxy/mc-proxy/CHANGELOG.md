# Changelog

## 1.8.4

- **Commands are paced** (by default at least 0.25 s between two commands to the
  node). The Home Assistant integration opens several connections at once;
  directly against the node only one fit, but through the proxy they all get
  through and can overwhelm a small radio device — causing connections to drop.

## 1.8.3

- **Important fix**: the handshake watchdog from an older connection attempt
  could tear down a new, healthy node connection. On a slow link these watchdogs
  piled up, so the proxy kept killing its own working connections every few
  seconds — exactly the "node answering / connection lost" pattern in the logs.
  Each watchdog now guards only its own connection.

## 1.8.2

- **More patient with a weak node.** On a poor WiFi link the proxy made things
  worse by disconnecting and reconnecting quickly. The handshake now gets 30 s
  (with one retry halfway), the keepalive goes to 30 s, and three silent rounds
  are needed before the connection is renewed.
- The cached `self_info` survives a reconnect, so clients can still register
  during a hiccup.
- Clients are only disconnected once the node has been gone for more than 60 s,
  instead of on every short interruption.

## 1.8.1

- **The proxy answers `APP_START` itself.** The companion firmware answers
  `APP_START` only once per TCP connection: the proxy performs that handshake on
  connect, after which every client's `APP_START` was ignored by the node.
  Clients hung on "connecting" or "failed to fetch device info". The proxy now
  stores the node's `SELF_INFO` reply and uses it to answer every client's
  registration. This was the root cause of nearly all connection problems.

## 1.8.0

- **Simplified routing**: every node frame goes to all clients; clients match
  what belongs to their own command themselves. The earlier "reply only to the
  asker" logic could deliver replies to the wrong client, or lose them, when
  several clients were active. The lock now serves only to keep frames from two
  clients from being interleaved on the wire.
- This also removes any chance of a busy client blocking the line.

## 1.7.2

- **Fair turn-taking**: a client held the line for up to 8 s when the node did
  not answer, so other clients (and the meshcore integration's validation) timed
  out. A client now waits at most 2 s for its turn and its command goes through
  regardless afterwards; the response window is 3 s.
- Stale internal replies are cleared on every client command, so they can never
  filter out a client's reply.

## 1.7.1

- Increasing wait between failed connection attempts (1 s → max 15 s): a node
  with a weak or stalled network stack is no longer hammered every second.
- Idle client sessions are cleaned up periodically, so the slots do not silt up
  with orphaned connections.

## 1.7.0

- **Status page** on port 5001 (JSON): shows whether the node is connected,
  whether it is answering, how long ago data last arrived, and which clients are
  attached. Lets you diagnose a problem remotely without digging through logs.

## 1.6.0

- **Self-healing against a stalled node.** The companion firmware can reach a
  state where it still accepts TCP but answers nothing. The proxy now detects
  this: no answer to the handshake (10 s), or two keepalives in a row without an
  answer → close the connection and rebuild it. A fresh TCP session usually
  revives such a node.
- Clear log messages about node health ("node answering", "node not responding").

## 1.5.1

- Response window from 2 s to 8 s: a slow or freshly restarted node sometimes
  answers only after several seconds, so replies were not reaching the client.
- Internal handshake/keepalive replies are only swallowed within 5 s of being
  sent (prevents late client replies from disappearing).
- When the node connection is lost, all client sessions are closed; they
  reconnect by themselves once the node is back (prevents silting up).
- Default `max_clients` from 8 to 32 (meshcore-ha opens 4–8 by itself).

## 1.5.0

- **Own handshake + keepalive towards the node.** The node closes connections
  that do not register or stay quiet too long; the proxy now sends an
  `APP_START` immediately after connecting and a keepalive every 20 s
  afterwards. Without this the node connection kept dropping and client commands
  disappeared into a dead socket. (Tested: two simultaneous clients now both get
  a correct handshake and their own command replies.)
- Replies to the internal handshake/keepalive are swallowed, not sent to clients.
- Clear warning in the log when a command cannot be forwarded because the node is
  unreachable.

## 1.4.0

- **Real frame parsing**: the TCP transport does use framing after all
  (`0x3C`/`0x3E` + 16-bit length + payload). The proxy now parses complete frames
  in both directions; the packet type (offset 3) drives routing — responses to
  the asker, pushes to everyone. Earlier versions looked at the frame marker and
  therefore routed everything wrongly.

## 1.3.0

- **Exchange serialisation**: one command/response exchange at a time over the
  node; while a command is running, all response frames are guaranteed to go to
  the asker (silence detection for multi-part replies, max 2 s). Fixes handshake
  races when several clients — or several connections from the same integration —
  send commands at once.

## 1.2.0

- **Smart routing**: command responses from the node go only to the client that
  sent the command; push frames (adverts, incoming messages, first byte ≥ `0x80`)
  go to all clients. Previously every client saw everyone else's replies, which
  put some clients (the meshcore integration among them) into a reconnect storm.

## 1.1.3

- Eviction on full client slots now only affects sessions that have sent nothing
  for more than 60 s; active connections (the meshcore integration uses several
  at once) are left alone.
- Default `max_clients` raised from 4 to 8.

## 1.1.2

- On reaching `max_clients` the oldest connection is replaced instead of the new
  one being refused — stranded sessions (for example a client's aggressive
  reconnects) no longer clog the proxy.

## 1.1.1

- Connections from the Home Assistant host (localhost / docker gateway) are
  always allowed, even with an allow-list configured — the port mapping makes
  them arrive with the internal gateway address as source.

## 1.1.0

- Client allow-list (`allowed_ips`, IPs or CIDRs) — recommended to set.
- Maximum number of simultaneous clients (`max_clients`, default 4).
- Configurable log level (`log_level`).
- Runs without host networking: only port 5000/tcp is mapped (adjustable in the
  add-on's network section).
- `node_host` is required on first start, with a clear error message.

## 1.0.0

- First release: TCP fan-out proxy for MeshCore WiFi nodes; several companions
  share one node, with automatic reconnection.
