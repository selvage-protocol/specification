# Selvage Session Protocol: wire specification

**Status: DRAFT.** Wire version `selvage/1`. Every requirement below was observed on the wire of
the running implementation before it was written down, in the sessions in
[`crates/harness/tests/`](https://github.com/selvage-protocol/reference_server/tree/main/crates/harness/tests),
and nothing here is ratified. The one passage that is not `selvage/1`'s is §7.1, which fixes
`selvage/2`'s sealed frame and the two keys that go with it ([`CANONICAL.md`](CANONICAL.md) §6.1):
that section is written from the design rather than from a session, and it says so itself.

This document is normative, and §1.1 says which sentences bind a reader and what a conforming
implementation is. Three sibling artifacts fix the other halves of the same thing, and all four
are meant to be read together:

| artifact | what it fixes |
|---|---|
| [`CANONICAL.md`](CANONICAL.md) | **SJ-C/1**, the byte form of a session text frame, and (§6.1) of a `selvage/2` sealed frame. This document says what the members mean; that one says how they are written, and a frame conforms to both |
| [`schema/`](schema/) | the machine-readable model: JSON Schema 2020-12, one file per concern, with every frame of every vector checked against it |
| [`vectors/`](vectors/) | transcripts of real bytes. They are examples, not the rule; a second implementation is held to them by [`runner/run_vectors.py`](runner/) |

**The implementations' status is not here.** What the reference server and the two clients do
where this document does not bind them, the decisions this draft had to make, and what is still
open are in [`NOTES.md`](NOTES.md), which is informative: no sentence in it binds a reader, and
nothing in this document is softened by anything there.

---

## 1. Scope

The Selvage Session Protocol is the *session* layer above document sync: rooms, participants,
roles, which documents are open, presence, join and leave. It carries document sync and awareness
payloads but does not define them: those are y-protocols (§14).

This draft covers, and only covers:

- handshake, version and capability negotiation;
- room mint, join, roles, and the room lifecycle;
- the open-document set, and the room's grant listing;
- relay of document-sync and awareness payloads (opaque to the server).

It does **not** cover, in this slice: persistence, accounts, authentication beyond a room token,
file access (the room's grant is a list of *names* the server carries and never resolves, §5),
terminals, rich text, E2EE in `selvage/1` (§7.1), or any HTTP API other than `GET /meta`.

Everything outside the core layer is a **named optional profile**. One profile name is reserved
here so that a later draft can define it without competing for the name: **`terminal/1`, shared
terminal and process execution.** It is a placeholder and nothing else: no methods, no payloads,
no behaviour. No implementation advertises it in this slice, and a client that sees it advertised
is talking to something that is not this draft.

### 1.1 Conformance and key words

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**,
**SHOULD NOT**, **RECOMMENDED**, **NOT RECOMMENDED**, **MAY** and **OPTIONAL** in this document
are to be interpreted as described in BCP 14 [RFC 2119] [RFC 8174] when, and only when, they
appear in capitals as shown here.

**What is normative.** Every sentence in this document binds, with three exceptions:

- a passage marked *(informative)*, which records what an implementation does and does not
  constrain a reader;
- the items of [§14 References](#14-references) that are marked informative;
- [`NOTES.md`](NOTES.md), which is not part of this specification.

`CANONICAL.md` is normative for the bytes of a frame: member order, whitespace, string escapes,
number form and array order. This document is normative for what those members mean. A frame
conforms when it satisfies both.

**What conforms.** A conforming **server** implements §2–§11 on the server side; a conforming
**client** implements §4–§10 on the client side. Both implement `CANONICAL.md`, both are bound by
§12–§14, and both are bound by the [frames of the version they
speak](#10-version-and-capability-negotiation): conformance is per wire version, and the unmarked
text of this document is `selvage/1`'s. `selvage/2`'s is every passage that names the version as
its own, and all of them are listed here so that nothing of that version has to be discovered:
[`CANONICAL.md`](CANONICAL.md) §6.1; §5.1's fragment, local-refusal, handover and
token-without-`room` paragraphs; §7.1; §8's awareness passage; §13;
and the passages of this document whose lead-in carries the version — §1.2's `host`, `guest`,
`grace period` and `set` entries, §2's `/meta` passage, §2.1's bounded-state passage, §3's server
passage and its closing account of what the relay cannot do, §4.1's fixed-member-set clause, §5's
method-surface, handshake and minting passages, §6's vocabulary
passage, §6.1's join passage, §9's room passage, §9.1's and §9.2's lead-ins and §9.1's return
passage, §10's version-gate passage, §11's vocabulary passage, and §12's scoping lead-in.

A peer that speaks `selvage/1` is not held to any of them, and the converse holds by the same rule:
a statement this document makes that no passage of `selvage/2` carries is `selvage/1`'s and does not
bind a `selvage/2` peer. The two versions' lifecycles are where that matters most, because
`selvage/2`'s server does not know who a room's host is: §9's host claim, its one-host-connection
rule and its `host.detached`/`host.attached` pair, §9.1's reclaim, §9.2's two state machines, §11's
`host_present` code and close **4004**, the peer-cap carve-out and the two room-state rows of §2.1,
and §1.2's `hold`, open-document-set and grant entries are all rules about state that version's
server holds and this one does not. Those passages stay where they are and are read as that
version's, which is what lets both versions be stated in one document.

Two rules follow, and they are what makes the keywords worth reading:

- **Silence is not a requirement.** A behaviour this document does not state is unspecified. A
  peer **MUST NOT** depend on one, and a requirement that is not here cannot be inferred from an
  implementation's behaviour.
- **A requirement is what a reader can meet.** A sentence states an obligation only if a
  conforming peer can observe whether it was met, or if it is about what an implementation must
  do to be interoperable at all. Where a rule is about a private seam, the observable consequence
  is stated with it.

A frame is cited in this document by its **wire name** (`session.hello`, `doc.open`,
`peer.joined`), which is the one name it has on the wire and the one name a peer will cite back.
[§5's method table](#5-methods) and [§6's event table](#6-events) are the index of them.

### 1.2 Terminology

These words carry obligations, and the protocol uses them precisely.

- **connection**: one WebSocket, from its upgrade to its close.
- **client**: an implementation speaking the protocol. It owns connections; it is not one.
- **peer**: a connection's identity inside a room. `peer_id` is assigned by the server, is opaque,
  does not survive the connection (§9.1), and is what every event about a participant names.
  **In `selvage/2`** a peer is still a connection and `peer_id` is still what every event about one
  names, but it is not what a frame is attributed to: a frame belongs to the key that verified, and
  a `peer_id` a room state carries beside a key is the host's label for a seat rather than an
  identity the key is bound to (§13.4).
- **participant**: a peer, seen from the room's side. The wire says `peer`; the two are the same
  thing.
- **session**: the connection as this layer sees it, once it is seated and until it ends. The
  *session layer* is what this document specifies.
- **seated**: a connection is **seated** once its `session.hello` has been answered with
  `room.created` or `room.joined`. Before that it is **unseated**. The distinction is not
  cosmetic: every fault closes an unseated connection, and some faults do not close a seated one
  (§11).
- **mint**: to create a room. A connection whose URL carries no `room` mints one (§5.1).
- **host**, **guest**: the two roles. `role` in `session.hello` is a **claim**, not a command
  (§9): minting is hosting whatever the claim says, and a claim of `host` on a join is honoured
  only while the room has no host. **In `selvage/2` there is no claim**, and the server seats
  nobody as anything: the host is whoever holds the private half of the room's host keypair, and
  the roles a room has are the host's to assign and sign ([`CANONICAL.md`](CANONICAL.md) §6.1,
  §7.1).
- **token**: the room's permission, minted with the room, carried in the invite URL, never echoed
  after `room.created` (§5.1). It is the whole authentication in this slice (§12).
- **refusal**: a fault that ends a connection before it is seated, answered with `session.error`
  and then a close carrying the matching code (§11).
- **grace period**: the interval after a host's connection ends during which the room still
  exists and can be reclaimed. `host.detached` announces it, with its length in `grace_ms` (§9).
  **In `selvage/2` it is the interval after the room's *last* connection ends**, and its length is
  the `room_grace_ms` a client reads from `/meta` (§2) rather than a number carried per room. §9's
  room passage is where that version's timer, and what cancels it, are stated.
- **hold**: one connection's claim on one path, made by `doc.open` and released by `doc.close` or
  by the connection ending. A hold belongs to a connection. **In `selvage/2`** a hold is a peer's
  own statement inside its sealed holds message ([`CANONICAL.md`](CANONICAL.md) §6.1) rather than a
  value the server keeps: the server relays it, reads nothing in it, holds none and releases none,
  and a hold's life — the lease that renews it and expires it — is §13.7's peer-side rule.
- **the room's open-document set**: the paths the room has open, `documents`, in first-opened
  order. It belongs to the room and **outlives every peer that opened a path**; a path leaves it
  only when the last hold on it is released (§5). **In `selvage/2`** the set is the peers' own: each
  peer announces the paths it holds (§13.7), the room's set is the union of the seated peers' live
  holds, no server frame carries one, and the server has nothing to say which documents are open.
- **the room's grant**: the files the room's host has published as its working tree, `paths`,
  written ascending by its publisher in UTF-16 code units (§5). It is the same kind of value and
  the same kind of claim as a path in the open-document set: a list of names, no content,
  unvalidated by the server (§5, §12). It belongs to the room as the open-document set does, and
  is replaced wholesale by every `doc.grant`. **In `selvage/2`** it is the `listing` of the host's
  sealed room state — the same kind of value, replaced wholesale by every state — and it reaches a
  peer inside a frame the server cannot read (§7.1).
- **the session document**: the single `Y.Doc` a room's peers converge on, one `Y.Text` per open
  path keyed by the path (§7). The server never holds it.
- **text frame**, **envelope**, **binary frame**, **message**: a *text frame* is one JSON
  **envelope** (§4). A *binary frame* carries a stream of one or more y-protocols **messages**
  (§7), which the server relays without decoding.
- **advertised**: carried by the server in `capabilities`, `keepalive` and `wire_versions`:
  in `GET /meta` and again in the handshake reply (§10).
- **set**: an array whose order the protocol does not promise: `peers`, `capabilities`,
  `wire_versions`, `roles` (§2, §6.2, §10, `CANONICAL.md` §2.7). `documents` and a grant's
  `paths` are the only arrays this protocol orders (§5, §6.3). **In `selvage/2`** the sets are
  `peers`, `capabilities`, `wire_versions` and the sealed holds, and the one array the version orders
  is the `listing` inside the sealed room state ([`CANONICAL.md`](CANONICAL.md) §6.1).
- **blank**: empty after removing leading and trailing Unicode whitespace. A `doc.open` or
  `doc.close` path and a `display_name`, wherever either appears, are held to this one rule;
  the schema patterns built on `\S` are a necessary-only approximation of it, as `maxLength`
  is of the UTF-16 bound (§5).

## 2. Transport

- **One endpoint**: `ws://<host>:<port>/session`, a WebSocket. No TLS in this slice (§12); the
  server is expected to run on localhost or behind a terminator.
- **One negotiation endpoint**: `GET /meta` over plain HTTP on the same listener.

  ```json
  {
    "server": "selvaged/0.1.0",
    "wire_versions": ["selvage/1"],
    "capabilities": ["y-protocols/1", "awareness", "open-document-set", "host-reclaim"],
    "keepalive": {
      "ping_interval_ms": 30000,
      "awareness_renew_ms": 15000,
      "awareness_expire_ms": 30000,
      "room_grace_ms": 30000
    },
    "roles": ["host", "guest"]
  }
  ```

  | member | type | meaning |
  |---|---|---|
  | `server` | string | free-form identification, e.g. `selvaged/0.1.0`. For diagnostics only, and not stable; a peer **MUST NOT** depend on it |
  | `wire_versions` | array of string | the wire versions this server accepts (§10). A client that can speak none of them **MUST NOT** open the socket |
  | `capabilities` | array of string | what the server believes it has; the same list the handshake reply advertises (§10) |
  | `keepalive` | object | the session's clocks (`ping_interval_ms`, `awareness_renew_ms`, `awareness_expire_ms`), which are the ones the handshake reply carries too (§8.2), and, in `/meta` alone, `room_grace_ms`, the server's default grace period (§9) |
  | `roles` | array of string | the roles this server seats (§9) |

  Unknown members are ignored, like an unknown member anywhere else. What a client needs is
  `wire_versions`; the rest it reads for a better default before the handshake answers. The three
  arrays are sets: like `peers` (§6.2), their order is not significant and a client **MUST NOT**
  depend on it (`CANONICAL.md` §2.7).

  A client **SHOULD** read `/meta` before connecting when it can, to fail fast on an incompatible
  server. Reading it has three outcomes, and they are not the same outcome:

  - **Reachable and compatible**: `wire_versions` holds a version the client can speak: connect.
    The advertised `keepalive` **MAY** be adopted at once, without waiting for the handshake,
    since the three clocks are the ones the handshake reply carries too (§8.2, §10); `/meta`
    carries `room_grace_ms` in addition, so that a client can size its reconnect retry to the
    grace before it has a session to be detached from (§9.1).
  - **Reachable and incompatible**: no version in the list: **do not connect**. The client
    refuses locally, with the reason the server would have given (`unsupported_version`, §11),
    before a socket is opened at all.
  - **Unreachable**: connection refused, a timeout, a body that is not the object above:
    **connect anyway**. `/meta` is a convenience, and the handshake decides; a client that treats
    an unreachable `/meta` as a refusal cannot reach a server behind a proxy that does not forward
    it, and the protocol already treats unknown members and unknown capabilities as ignorable,
    not fatal.

  `GET /meta` is the only request this document defines, and the negotiation endpoint does not
  support keep-alive. A client **MUST NOT** send it another method. *(informative)* The reference
  listener answers `HEAD /meta` with the `GET` headers, the body's `content-length` among them, and
  no body, which is what RFC 9110 §9.3.2 asks for, and answers any other method
  `405 Method Not Allowed` with `allow: GET, HEAD`: a `POST` answered `200` while creating nothing
  would be a lie.

  Other paths return `404`. An implementation **MAY** serve a static page there instead — the
  reference server does, from `--serve-page`, on the same origin as `/session` and `/meta` — which
  is what lets an invite link, whose origin is the server (§5.1), open in a browser. That page is
  outside `selvage/1`: §1 covers no HTTP surface but `GET /meta`, and the protocol gives a room
  exactly one wire endpoint.

- **Frame types.** Text frames carry the JSON session envelope (§4–§6). Binary frames carry
  y-protocols payloads (§7, §8). The server routes binary frames by room membership and never
  decodes them (§3).

**`/meta` in `selvage/2`.** The body is not version-tagged and is read the same way in both
versions, so what moves is its content and not its shape. No member leaves it but `roles`, which
`selvage/1` uses to say which roles its server seats and which a `selvage/2` server has nothing to
put in, because it seats nobody as anything (§1.2). A `selvage/2` server advertises:

- **`wire_versions`, naming every version the server accepts.** A server that implements only
  `selvage/2` **MUST NOT** name a version below it: that server cannot seat a `selvage/1` peer
  (§10), so a list offering one would be a downgrade a compromised server could take a client
  down, which is the one failure a confidentiality feature cannot have. A server that implements
  both versions advertises both, which is what it accepts and not that downgrade: an unpinned
  client that can speak `selvage/2` **MUST** mint `selvage/2`, and a reachable `wire_versions`
  that holds no version at major 2 it can speak is a refusal (§10) — local, before a socket is
  opened, and never a fall back to `selvage/1` — the client's to word, naming the server and the
  version it would need. A `/meta` that could not be read is not that answer: the client attempts
  `selvage/2` and the handshake decides, and a server that seats only `selvage/1` answers
  `unsupported_version` — loudly, and not as a downgrade. `selvage/1` is what a client mints when
  it cannot speak `selvage/2`, or when it is pinned there (a setting, a link, a deployment): a
  deliberate choice to host a room the server can read, which is what the pin is for. A **join**
  is not this choice: it speaks the version its invite names — the fragment's two keys, or their
  absence (§5.1) — whatever the list says. A pin to a version the server does not seat is a
  refusal (§10) and never a fall back to a version it does. Acceptance is unchanged — the same
  grammar and the [compatibility rule](#10-version-and-capability-negotiation) — so a server that
  accepts `selvage/2` accepts any `selvage/2.x`.
- **`capabilities`, which carries the two names this version defines for a server, `y-protocols/1`
  and `awareness`.** An implementation **MAY** advertise names of its own beyond them, as §10
  allows, and the two names `selvage/1` adds are absent here because they are that version's server
  machinery: `open-document-set` is a set this server does not keep, and `host-reclaim` is a
  reclaim it does not have. Nothing is gated by a capability in either version — a peer **MUST
  NOT** infer a failure from one it does not recognise — so the list says
  which frame shapes a peer may expect and not what it may send (§10).
- **`keepalive`, unchanged in shape and in membership.** `ping_interval_ms` is the server's: how
often it pings, and those pings are not session messages. `awareness_renew_ms` and
`awareness_expire_ms` are the session's: the server advertises them so that every peer renews and
expires on one clock, a client **MUST NOT** substitute its own, and in `selvage/2` that clock is
the version's only one (§8.2). `room_grace_ms`, in `/meta` alone, is the server's too, and it is
where a `selvage/2` client reads the room's grace: in that version the number is how long a room
survives its last connection ending (§1.2).

**What a `selvage/1`-only client sees.** It sees a server it cannot talk to, and it sees it without
a socket: `wire_versions` names no version it can speak, so it refuses locally with
`unsupported_version`, which is the rule above and not a new one. Nothing else in the body is a
signal to it, because the members it reads — `wire_versions`, and `capabilities` and `keepalive`
for a better default — are the ones that stay: `roles` leaving and `capabilities` shrinking are
both invisible to a client that reads `wire_versions` and connects. §10 says the same from the
server's side.

### 2.1 Limits

Nothing here is negotiated, and the protocol fixes none of the numbers: what a server bounds is
its policy, not a peer's contract. Six consequences do bind a peer.

- **A server bounds the wait for `session.hello`** and closes a connection that stays silent past
  it (§5). A client **MUST NOT** expect an unseated connection to live indefinitely.
- **A server does not close a connection for silence alone, and it MAY end one that stops
  answering.** The keepalive (§9) is how liveness is established: a server pings, and it **MAY**
  close a connection whose pings have gone unanswered for a stated number of intervals (the
  reference waits two), so a slow link is not mistaken for a dead one. Closing for silence is an
  ordinary drop: the room is told `peer.left`, the grace arms when the peer was the host, and no
  session close code is sent.
- **A server MAY bound the size of an inbound WebSocket frame or message.** A frame or message
  over that bound is a transport failure and not a session fault: it ends the connection the way a
  dropped socket ends, with no `session.error` and no session close code (§11), and the room learns
  of it as `peer.left` (§6, §9). There is no way in `selvage/1` to move a payload larger than a
  peer's transport bound. That bound is structural rather than policy: past it the transport cannot
  resync mid-message, so there is no session left to refuse on.
- **A server MAY bound the text envelope it will parse, below its transport bound, and that bound
  is a session fault.** The frame arrived whole, so there is a session to refuse on: the server
  judges the frame's own length, in wire bytes and before any JSON parser is handed it, and answers
  `bad_message`, naming the bound and the fact that the frame was not parsed. A seated connection
  gets the `session.error` event and stays open, exactly as §9.2 says of any other unreadable text
  frame, because a peer that sent one oversized envelope can send a smaller one next; a frame that
  arrives before `session.hello` gets the refusal every unseated fault gets, `session.error` and
  close **4000** (§11). An implementation that sets the envelope bound below its frame bound is
  telling a peer something it can act on — "send this again, smaller" — where the transport bound
  can only end the session; one that sets no envelope bound parses every frame its own frame bound
  admits.
- **A client's own buffers are the mirror image, and they are the client's.** A client that writes
  faster than its peer reads is buffering in its own memory: the socket's backpressure does not
  remove the queue, it moves it. A y-protocols delta cannot be regenerated once it has been
  produced, so a client **SHOULD** bound what it holds, by failing the session or by not
  committing a CRDT transaction until there is room, rather than queue without limit. The level
  is a settled decision rather than an oversight ([`NOTES.md`](NOTES.md) §B.20).
- **A server is bounded at both ends of its state.** A server **MUST** enforce a finite
  configured bound on the bytes one connection may send and on the state one room may store,
  and **MUST** refuse deterministically past each rather than grow without limit. The numbers
  are policy, as the opening sentence says; that a bound exists is what a peer is held to,
  because a second implementation cannot be held to a protocol whose other end allocates without
  limit. The per-connection bounds are the frame, message and envelope bounds below — past the
  first two the connection ends the way a dropped socket ends, and past the envelope bound it is
  refused on the frame's own vocabulary — and a room's stored state is its peers, its open-document
  set and its grant in `selvage/1`, and its membership and its connections in `selvage/2` (below): a
  request that would exceed a bound the server sets is refused with
  `bad_params` or an `x.` capacity code (§10.1, §11), leaving the room as it was. Which of the two
  shapes a capacity refusal takes follows §11 rather than the code: before seating every fault is a
  refusal, `session.error` and then the matching close — close 4000 for a code with no close of its
  own, which is every `x.` code — while on a seated connection the fault is an error response for
  the request that caused it and the connection stays open.

**The bounds in `selvage/2`.** A server of that version bounds the same things and one fewer. Every
per-connection bound above is unchanged — the frame, message, envelope, queue, hello-timeout and
inbound-budget rows, and the head bound that runs before admission — and so is what they bound: a
connection's bytes in, a connection's frames out, and a room's membership. What this version loses
is a bound on something it does not hold. Two of the rows below bound a room's stored state with the
open-document set and the grant, and this server keeps neither, so there is no `doc.open` to refuse
past a cap and no `doc.grant` to bound; the peer-cap row's carve-out for a connection reclaiming a
hostless room goes with the reclaim. A `selvage/2` server **MUST** still refuse deterministically
past a finite configured bound on the bytes one connection may send and on the room's membership,
and what is left of the last consequence above is that **a room's stored state in this version is
its token, its membership and its connections** — the room and peer caps are what bound it in the
reference server. A document's sync traffic carries no cap of its own here, because no document
crosses the session layer: the largest payload a peer can move is one sealed frame, and the frame
and message bounds are what it meets. A capacity fault is announced by §11's split and not by the
code: a request on a seated connection is answered with an error response and the connection stays
open, while a fault before seating is the refusal `session.error` and then close **4000**, which is
the generic close every `x.` code uses.

The reference server's numbers, for a reader who needs to know what to expect in practice
*(informative)*:

| bound | reference value | what happens when it is reached |
|---|---|---|
| HTTP request head | 16 KiB, and `head_timeout` (5 s) to arrive | an over-size head is answered `431 Request Header Fields Too Large`; a head that does not finish inside `head_timeout` is closed without a response. Neither is admitted |
| `session.hello` after the upgrade | 10 s | `hello_required`, then close 4000 |
| WebSocket ping | every 30 s, and two unanswered intervals end the connection | the server pings; a peer that stops answering is closed as an ordinary drop, and the room is told `peer.left` |
| outbound queue, per connection | 32 frames and 32 MiB | a peer past either cap is disconnected and the room is told `peer.left` |
| connections | 1024, counted past the head | past the cap a plain HTTP request is answered `503`, and a WebSocket upgrade is answered and then closed with **1013**; a half-sent head is bounded by `head_timeout` and not counted. Still no idle reaper (a connection that answers its pings is never closed for being quiet) and no per-source rate limit |
| inbound WebSocket frame | 8 MiB | the connection ends the way a dropped socket ends: no `session.error`, no session close code |
| inbound WebSocket message | 8 MiB | the same |
| inbound text envelope | 5 MiB, judged on the frame before the JSON parse | refused `bad_message`, naming the bound and that the frame was not parsed: the event on a seated connection, which stays open, and the refusal plus close 4000 before seating |
| room state: rooms | 1024 minted at once | a mint past it is refused `x.server_full`: a refusal, so `session.error` and then close **4000**, the generic close a code with no matching one uses (§11); the room is not created |
| room state: peers per room | 128 | a further join is refused `x.room_full`, the same refusal shape (`session.error`, close 4000); a connection reclaiming a hostless room as its host seats anyway, because the room's owner must be able to come back |
| room state: the room's open-document set | 1024 paths | a `doc.open` for a path the set does not already hold is answered with the error response `x.room_full`, correlated by the request's `id`, and the connection stays open — a seated fault is announced in the frame's own vocabulary, not as a refusal (§11) |
| room state: the room's grant | 100 000 paths, 4096 bytes to a path, 4 MiB of path bytes in total | a `doc.grant` past any of the three is refused `bad_params`, and the connection stays open |
| inbound budget, per connection | 2 MiB a second, refilled continuously, with a 64 MiB burst to spend | a seated connection is told `x.rate_limited` and closed **1013** (try again later); the same budget spent before seating is the ordinary refusal, `session.error` and close 4000; a reconnect starts with a fresh budget |

The head bound applies before admission, and it has two halves: `head_timeout` bounds how long
the whole request head may take, not merely the gap between reads, and the 16 KiB bounds its
size. A connection is counted against the connection cap only after its head has been read, so a
half-sent head occupies a descriptor but neither a slot nor the 16 KiB bound, and it is
`head_timeout` that reclaims it.

The frame, message, envelope, queue and connection bounds are the server's own configured
values rather than the WebSocket library's defaults, and the queue, connection, room-state and
budget rows are enforced caps rather than the v1 posture they once stated; the background is in
[`NOTES.md`](NOTES.md) §A.1. A capacity code is the implementation's own: `x.server_full`,
`x.room_full` and `x.rate_limited` are the reference server's, they live in the reserved `x.`
namespace of §10.1, and a peer reads no `selvage/1` meaning into one — what a peer reads is the
close code that follows it, and §9.1's rule that an `x.*` fault is not retried automatically.

## 3. Layering and opacity

The server is **session-aware, payload-opaque**: it parses text frames, owns rooms, membership and
the open-document set, and relays binary frames to the rest of the room byte for byte. A client
**MUST NOT** expect the server to interpret, validate, transform, or take an interest in any
y-protocols payload. In particular the server holds no CRDT, and document convergence is achieved
peer to peer through the relay, not against the server.

**In `selvage/2` a binary frame is a sealed frame** (§7.1): an envelope sealed under a key that
travels in the invite URL's fragment and signed with a key the fragment carries or the room's state
commits. The paragraph above is unchanged by it and gains no carve-out — the server relays the
envelope byte for byte, does not open it, does not verify it, and writes no member into it.

**What the server of that version is, in full.** A room's `id` and its `token`, the `keepalive` it
advertises, and the connections in the room with their `peer_id`, `display_name` and
`awareness_client_id` — membership and the relay, and nothing a room's contents could be read from.
Everything else a room has — its listing and its roles, which documents are open, what is in them,
where anyone's cursor is — is the peers' state, sealed under a key the server does not hold. The
negative half of that is as much a part of the version as the positive one:

- **A relayed frame is the bytes the sender handed the server.** The relay **MUST** deliver them
  unchanged to the room's other connections, and the server has no reason to drop one: it cannot
  read a frame, so it cannot refuse one for what it says (§7.1).
- **A frame the server authors carries membership, a room's existence, or a fault, and nothing
  else.** It **MUST NOT** carry a path, a role, a document name, or any character of a room's text,
  and no member of the session layer names one.
- **The server holds no CRDT, no document and no room state.** There is nothing for it to publish,
  echo or replay, which is why a joiner is brought up to date by its peers rather than by the
  server (§7, §9).
- **The server decides nothing about roles.** It **MUST NOT** seat a connection as anything, refuse
  one for a role, or carry a role in a frame it authors; who a room's host is, and who may edit,
  are facts the peers verify rather than values the server asserts (§1.2, [`CANONICAL.md`](CANONICAL.md)
  §6.1).

What that leaves the server deciding is membership and the relay, and they are §9's room passage and
§2.1's bounds. A rule that needs an authority on anything else — which paths are open, which
documents exist, who may write to one, whether a host is still present — is a rule for peers, and
this version's peer-side rules are stated in §13.

**What the relay cannot do, and what it still learns.** The four duties above are the whole of what
this version takes from the server, and what they buy is worth stating here rather than left to be
inferred from §12, whose claims are `selvage/1`'s:

- **It cannot read a room.** A frame is sealed under a key that travels in the invite URL's fragment
  (§5.1), and the listing, the roles, the text and the cursors are all inside frames: no character of
  a room's text, no cursor, no file name and no role reaches the server in a form it can read.
- **It cannot forge a frame or mis-attribute one.** Every frame is signed over its own bytes, and the
  room state commits the key each peer signs its frames with
  ([`CANONICAL.md`](CANONICAL.md) §6.1, §7.1). A receiver attributes a frame to the key whose
  verification succeeded, so attribution is cryptographic rather than a value the server writes: the
  state commits keys and their roles, a `peer_id` beside a key in it is a label the host wrote about
  a name the server minted and no authority rests on it, and a peer's own key reaches the room in a
  frame only its holder can sign (§13.1, §13.4).
- **It cannot replay one.** A frame's counter under one key is strictly increasing and a receiver
  refuses one at or below the mark it holds; a room state and a closing are ordered by their own
  `issued` and a receiver refuses one that is not above the mark
  ([`CANONICAL.md`](CANONICAL.md) §6.1, §13.3).
- **It cannot decide a role, and it cannot be the host.** No frame it authors carries a role, and the
  host is whoever holds the private half of the key the invite's fragment names — a value the server
  never sees.
- **It can lie about who is present, and only about that.** `room.created`, `room.joined`,
  `peer.joined`, `peer.left` and `peer.renamed` are frames the server authors (§6), so a compromised
  relay can invent a seat, withhold a leave, rename a peer, or answer a join with `room.created` for
  a room nobody minted. What it cannot do with one is produce a forged edit: a frame is attributed
  to the key that verified and a role comes from the host's state, so content sent under an invented
  `peer_id` is refused `uncommitted_key`, and no frame the server authors carries a role or a key
  (§13.4). The harm is a participant list that names someone who is not there or hides someone who
  is, and whatever a client shows from it: presence is the relay's word in this version and it is
  worth exactly the relay's honesty, which is why a client renders a name only where the roster
  accounts for it (§13.4). Two harms follow from the same power and neither is a forgery a peer can
  detect, so both are named where the rules that produce them are rather than left to be inferred. A
  forged `peer.left` for the seat the applied state's `host` entry labels **arms §13.8's host-away
  clock** for every peer holding that state, and a client whose clock has passed the host-away window
  **MUST** end its session (§13.8): one frame the relay authors ends a live room's sessions for
  every guest, at the window's delay — 30 s at the defaults — and with nothing in any frame to say a
  lie occurred. A *withheld* `peer.left` cuts the other way: the seat stays in the roster, so §13.7
  keeps a departed peer's holds and §13.8 sees no host absent, and a room nobody is hosting keeps
  looking live for as long as the relay withholds it, because presence is read from the roster alone.
- **It is still a relay, and a deployment is still a deployment.** It learns who is in a room, their
  display names, which frames arrived, when, and how large they were, and roughly how many documents
  and files a room has; it can drop a frame, delay one and destroy any room; and it may serve the
  client code a guest runs, which is why §2 lets an implementation serve the page and a guest decides
  what it trusts. Transport security stays the deployer's job exactly as in `selvage/1`: the protocol
  cannot detect a downgrade and no peer can tell whether its own transport is protected (§12).

## 4. Session envelope

Every text frame is one JSON object. Three shapes exist, distinguished by which keys are present.

A JSON object in a text frame is also a *set* of member names: the same name **MUST NOT** appear
twice in one object, at any depth. A receiver **MUST** refuse a frame that repeats one as
`bad_message`, which before seating closes the connection (§11), rather than let a parser's
last-wins or first-wins decide what the frame says. RFC 8259 admits the duplicate syntax and only
*recommends* unique names; this document makes uniqueness a rule, because two receivers that
resolve a duplicate differently re-derive two different frames from one set of bytes, which is
exactly the disagreement this document exists to close.

### 4.1 Request (client → server)

```json
{ "v": "selvage/1", "id": 2, "method": "doc.open", "params": { "path": "src/main.rs" } }
```

| field    | type   | required | meaning                                              |
|----------|--------|----------|------------------------------------------------------|
| `v`      | string | yes      | wire version, `selvage/1`                             |
| `id`     | number | yes      | client-assigned request id, unique per connection     |
| `method` | string | yes      | see §5                                               |
| `params` | object | no       | method-specific; absent is equivalent to `{}`         |

**Unknown members are ignored**: a receiver **MUST** ignore a member it does not know, at any
depth, in either direction, so that a *later* wire version can add one without breaking it.
Within `selvage/1` the member set of every frame is fixed (a producer **MUST NOT** add a member
to a frame of this version), and that is what lets a vector assert an exact one (`CANONICAL.md`
§3). Unknown *capabilities* are likewise ignored (§10).

**`selvage/2` fixes its member sets the same way.** What those sets are is what that version's
passages say, and the rule for a name they do not carry with them — a `role`, a `documents`, a
`roles` — reads as a producer's obligation: a member this document gives `selvage/1` and that no
passage of `selvage/2` carries is **not** a member of that version, so a producer of it **MUST
NOT** write one and a receiver **MUST** ignore one it is handed, exactly as it ignores any other
name it does not know.

A request without `id` produces a `session.error` event (§6) with code `bad_message`; before
seating, that fault closes the connection (§11).

### 4.2 Response (server → client)

```json
{ "v": "selvage/1", "id": 2, "result": {} }
{ "v": "selvage/1", "id": 3, "error": { "code": "unknown_method", "message": "no such method: cursor.teleport" } }
```

Exactly one of `result` or `error` is present. `result` is an object (`{}` when a method has
nothing to return). `error.code` is a machine-readable code from §11; `error.message` is
human-readable and not stable.

**An unknown method is always an error response.** It is never silently dropped: silence turns
version skew into an unexplained timeout.

### 4.3 Event (server → client)

```json
{ "v": "selvage/1", "event": "peer.left", "params": { "peer_id": "p-3d33…" } }
```

Events carry no `id` and are not responses to anything. Key order within a JSON object is not
significant and is not stable (`CANONICAL.md` §2.1).

## 5. Methods

**Two methods exist in `selvage/2`, and the two are subsections below.** `session.hello` and
`session.rename` are the version's whole method surface, and they are the first and last of the
method subsections here; the three `doc.*` rows of the table below are `selvage/1`'s, because a
`selvage/2` server holds no document set and no grant for them to change (§1.1). What surrounds a
request — one answer per request, no pipelining, the bounded wait, failing the in-flight request
on a drop — holds in both versions.

Five methods exist in `selvage/1`. A method is cited by its name; this table is the index.

| method | `params` | answered with | effect on the room's open-document set | events |
|---|---|---|---|---|
| `session.hello` | the table below | a `room.created` or `room.joined` **event**, not a response | unchanged; a room is minted here and starts empty | `peer.joined` to the peers already in the room |
| `session.rename` | `{ "display_name": string }` | `{ "result": {} }` | unchanged | `peer.renamed` to every peer |
| `doc.open` | `{ "path": string }` | `{ "result": { "documents": [string] } }` | gains `path` unless it was already there | `doc.opened` to every peer |
| `doc.close` | `{ "path": string }` | `{ "result": { "documents": [string] } }` | loses `path` when no other peer still holds it | `doc.closed` to every peer |
| `doc.grant` | `{ "paths": [string] }` | `{ "result": {} }` | unchanged: a grant is not a hold, and the room's grant is replaced wholesale | `doc.granted` to every peer |

### `session.hello` — the handshake

**MUST** be the **first** text frame on a connection. Sending anything else first, or failing to
complete the handshake within the server's hello timeout (§2.1), closes the connection.

```json
{
  "v": "selvage/1",
  "id": 1,
  "method": "session.hello",
  "params": {
    "display_name": "Ada",
    "role": "host",
    "awareness_client_id": 5466766094545993,
    "capabilities": ["y-protocols/1"],
    "client": "selvage-vscode/0.1.0"
  }
}
```

| param                  | type   | required | meaning |
|------------------------|--------|----------|---------|
| `display_name`         | string | yes      | non-blank and at most 32 UTF-16 code units (below); the only identity in this slice |
| `role`                 | string | no       | `"host"` or `"guest"`; a claim, not a command: see §9 |
| `awareness_client_id`  | number | no       | the y-protocols awareness client id this connection will speak with; see §8.4 |
| `capabilities`         | array  | no       | capabilities the client believes it has; the server ignores any it does not know |
| `client`               | string | no       | free-form client identification for diagnostics |

The room to join, and its token, are carried in the connection URL, not here (§5.1).

`display_name` is the only identity in this slice and the peer's to choose: a client sends what
its person typed. Every client draws that string somewhere, and an unbounded one covers the
screen, so the protocol bounds it. A `display_name` **MUST NOT** exceed **32 UTF-16 code units**,
counted in UTF-16 code units, the length of a JavaScript string, so an astral character (a
surrogate pair) costs two, not in bytes and not in code points. A server **MUST** refuse a
`session.hello` whose `display_name` is longer than that with `bad_params` and then close
**4000**, the refusal a blank one already gets (§11); it **MUST NOT** accept the name, and it
**MUST NOT** truncate it, because a displayed name is then not the name its owner chose. The same
string is the field a client sends, the `PeerInfo` a server stores and echoes, and the `self` and
`peers` of `room.created` and `room.joined` (§6.1, §6.2): one bound, wherever it appears. A client
**SHOULD** check the bound before sending, so that its person is asked for a shorter name rather
than refused after typing it.

A `display_name` **MUST NOT** contain a control character, the Unicode `Cc` category: C0
(U+0000–U+001F), DELETE (U+007F) and C1 (U+0080–U+009F), and a server **MUST** refuse one that
does with `bad_params`, before seating, the refusal a blank name already gets: `session.error`,
then close **4000**. The bound above exists because every client draws the name somewhere, and a
control character is exactly what a client cannot draw: a terminal escape or a carriage return
hides inside a string whose owner did not write what a peer sees, and two clients that render one
differently disagree about who is present. The same rule holds a `path` (in `doc.open` and
`doc.close` below, and each member of a grant's `paths`) for that reason and one more: a path
becomes a file name in a client that mirrors the working tree, and a control character in one is
read differently by the filesystem, the terminal and the next tool than by the receiver that
rendered it. The rule is about the characters and not the shape: `..`, an absolute path and a name
that escapes the working copy remain names the server carries unvalidated (§12,
[`NOTES.md`](NOTES.md) §B.8), and confinement stays where §12 puts it.

The reply is a single `room.created` or `room.joined` event (§6.1, §6.2), and it is guaranteed to
be the first frame on the connection after the handshake, before any relayed payload or other
event. Refusals are a `session.error` event followed by a WebSocket close with the matching code
(§11). A `session.hello` whose params object cannot be read is refused as `bad_message`
(including one with no `display_name`, which is a parse failure for a shape whose only required
member it is), while a well-formed hello whose `display_name` is blank or over-long is
`bad_params`. The distinction is parse failure against semantic failure, and both are reachable
here, before the handshake completes, where every fault closes the connection (§11).

`session.hello` sent a second time on the same connection is an error response, code
`already_seated`, and the connection stays open.

**`session.hello` in `selvage/2`.** The frame is the one above with one optional param gone:
`display_name` is still required, still non-blank, still free of control characters and still at
most 32 UTF-16 code units, and still the only identity in the version. `awareness_client_id`,
`capabilities` and `client` are unchanged. **`role` is gone**: there is no claim to make, because
the server seats nobody as anything (§1.2), and a `selvage/2` hello that carries one is carrying a
member the version does not have, which a receiver ignores like any other unknown name (§4.1).
**A key does not travel in this frame either**, and it is not an omission: a text frame is parsed
by the server and this slice has no transport security (§12), so neither key may be written where
the server can read it. The host key arrives in the invite's fragment, and a peer's session key is
announced in a sealed frame (§5.1, §7.1).

Everything around the frame holds as it stands, and this is why the version's handshake is a
subtraction rather than a second handshake: it is the first text frame on the connection, the
hello timeout and the unseated refusals are §11's, the reply is one `room.created` or
`room.joined` and is the connection's first frame after the handshake, and a second hello is
`already_seated` with the connection kept open.

**Minting in `selvage/2`.** A connection whose URL names no room still mints one and is still
seated by it, and it is still the room's host — but nothing on the server says so. The host is
whoever holds the private half of the host keypair whose public half the invite's fragment carries
(`h`), that connection is the one that minted the pair (§5.1), and the server records a peer like
any other. So there is no claim for a minting connection to make or to have ignored, no host state
for a later connection to conflict with, and no event for a reclaim: `selvage/1`'s rule that
minting is hosting whatever the claim says, and its `host_present` refusal, are that version's and
this one has neither.

### 5.1 Room and token in the connection URL

```
ws://host:port/session                          → mint a room; the connection is its host
ws://host:port/session?room=<room_id>&token=<tok> → join an existing room
```

`room` and `token` are percent-encoded, and the decoding is **RFC 3986**: `%XX` is the only
escape, and a literal `+` is the character `+`, never a space. (`+` means space only under the
`application/x-www-form-urlencoded` rule, which this query does not use. A producer that encodes
by RFC 3986's unreserved set writes a `+` in a value as `%2B`; one that leaves it literal writes
`+`. A receiver **MUST** read a literal `+` as `+` either way rather than refuse it, because
otherwise one implementation's token is another's `token_invalid`.) Unknown query parameters are
ignored, so a client **MAY** attach its own parameters without a protocol change; a convention that
uses one to tell a guest it will be a `viewer` is such a parameter, and this document defines none of
it — a client's role is the applied state's alone (§13.4, §13.9). `room` and
`token` each appear **at most once**: a URL that repeats either is malformed, and the server
refuses it rather than let one of two values win. There is no code for a malformed URL in §11's
vocabulary, so that refusal is the join refusal `token_invalid`, the code a room whose named token
is not the room's already gets. Which of the two cases a URL is depends on `room` alone:

- **A `room` without a `token`**, or with a token that is not the room's, is a `token_invalid`
  refusal.
- **A `token` with no `room`** is a connection claiming to host, and the server **MUST** mint a
  room for it, seat the connection as that room's host, and discard the token: `room` alone
  decides which case a URL is, so the token has nothing to be checked against and nothing to join.
  The handshake reply is `room.created` carrying the new room's token (§6.1). A truncated
  invite link (`room` lost to a chat client, a proxy, or a copy-paste, `token` surviving)
  therefore opens a *new, empty room* whose host is whoever sent it, and nothing on the wire says
  so. That is the accepted cost of the rule; [`NOTES.md`](NOTES.md) §B.17 records why the
  alternative was rejected. **In `selvage/2`** the same URL has the same effect — a room is minted
  and the connection is seated in it — and the one part of the sentence that reads differently is
  what it is seated *as*: that version's server seats nobody as a host and records a peer like any
  other, and the connection that minted the room is its host by holding the private half of the host
  keypair whose public half the fragment the sender built carries (§5's minting passage).

**A URL carrying a room and its token is an invite, and it has two forms.** The first is the
connection URL above, exactly as it stands: `ws://host:port/session?room=<room_id>&token=<tok>`.
A socket can be opened on it directly, and it is the form an implementation that serves no page
names. The second is the **page link**, which is what the reference clients hand a guest, because
a link a browser can open is one that works for every guest: the page the room's server serves —
an implementation **MAY** serve one (§2) — over the scheme a browser speaks, with the same host,
port and path prefix and nothing else in it:

```
https://host/page/?room=<room_id>&token=<tok>
http://host:port/?room=<room_id>&token=<tok>
```

Its origin **is** the server, so no member of the link names a second address and a link cannot
point at a page that dials another. A receiver derives the connection URL from it by reading the
scheme back — `http://` as `ws://`, `https://` as `wss://` — and appending `/session`, and the
rules above apply to the page link's query unchanged: `room` and `token` are percent-decoded by
RFC 3986, each appears at most once, and any other parameter, including a `server` written by an
implementation that predates this form, is ignored. **A receiver MUST accept either form and join
the room it names**, and a host **MAY** hand on either; a link that truncates either form is the
truncated-invite case the `token`-without-`room` rule above describes.

The two forms carry the same secret and differ only in which scheme names the same address, so
§12's rule about an invite URL covers both.

**An invite also carries the room's two keys, in its fragment.** The fragment is the one part of a
URL a user agent dereferences itself and never puts in a request (RFC 3986 §3.5, RFC 9110 §17.11),
which is what makes it the one place a key can travel from a host to a guest without the server, its
logs, or anybody on the path reading it. Both values are **32 bytes** written in **base64url**
(RFC 4648 §5) without padding, under the names `k` for the **room key** and `h` for the **host's
public key**, in that order:

```
https://host/page/?room=<room_id>&token=<tok>#k=<room key>&h=<host public key>
ws://host:port/session?room=<room_id>&token=<tok>#k=<room key>&h=<host public key>
```

The two are minted together, when the room is minted, silently and from the platform's CSPRNG, the
way the room id and the token already are; the host's private half never leaves the host's machine
and is never in a link. `k` is the key every frame of the room is sealed under and `h` is the
room's root of trust, and [`CANONICAL.md`](CANONICAL.md) §6.1 fixes both. A receiver reads the
fragment with the query's own decoding rule above — RFC 3986 `%XX` escapes, and a literal `+` is a
`+` — and a value that does not decode to exactly 32 bytes is not a key, and neither is a
43-character value whose final character carries non-zero padding bits: a 32-byte value has one
canonical spelling and [`CANONICAL.md`](CANONICAL.md) §6.1 fixes it, so a spelling that is not that
one is a value nothing encodes rather than a second way to write the same key. Each name appears **at
most once**, as `room` and `token` do, and a parameter the receiver does not know is ignored, as an
unknown query parameter is.

The fragment is **never sent**, by construction: it is not part of a request line, and nothing this
protocol defines puts it in a frame. A `selvage/2` client **MUST** strip it before it builds the
socket URL, **MUST NOT** log it, and **MUST NOT** send it to the server in any form. It **MUST**
refuse an invite whose fragment is absent, whose `k` or `h` is missing, or whose `k` or `h` is not a
32-byte value — **locally, and before it opens a socket**. Without both values it can neither read
a frame nor verify one, so there is no fallback and no plaintext mode: the honest refusal says the
key is missing and asks for the whole link, `#` and all. What this document fixes is that the
refusal happens; the sentence is the client's.

In `selvage/1` the fragment carries nothing and no `selvage/1` frame is sealed: a link that carries
`k` and `h` joins the same room in the clear, and neither value is read.

**That refusal is local, and has no wire form.** It happens before a socket is opened, so there is
no frame to refuse on and no code to carry it: it is not a `session.error`, it is paired with no
close code, and §11's vocabulary is not involved, because the fault never reaches the server. What
the version fixes is that the refusal happens and what it is about; the words are the clients',
and the clients keep one set of them between them rather than one per editor.

**The handover, in `selvage/2`.** A client **MAY** also accept the two values with no fragment at
all: an invite a guest already holds, beside the room key and the host's public key said
separately, in the fragment's own names and encoding. A client that accepts them joins exactly as
one handed the link does, and owes the same local refusal when either value is absent, missing, or
not 32 bytes; nothing about the handover reaches the server, as nothing about a fragment does. The
two values are short and cost nothing to produce, so the handover is an escape hatch for a link a
chat client or a shortener truncated — and it is **NOT RECOMMENDED** as the flow a host hands on,
because it is a second channel whose two values have to be correlated by hand where the link is
one action.

The token is **never echoed after the mint.** `room.created` is the only frame that carries it
(§6.1) and nothing re-sends it, so a client that might have to reconnect has to keep the token it
was minted with. A host that did not keep it is in the position of any stranger holding a room id:
it can be told the room exists, and it cannot be seated in it.

### `doc.open`

```json
{ "v": "selvage/1", "id": 2, "method": "doc.open", "params": { "path": "src/main.rs" } }
```

`path` is a workspace-relative path. It **MUST** be non-blank and free of control characters
(§5): a blank path is `bad_params`, and so is one carrying a control character, and,
beside length and that, a path is otherwise unvalidated in this slice (§12, [`NOTES.md`](NOTES.md)
§B.8). A server **MAY** bound the length of a path as its own policy, as it may bound a grant
listing, and a path over that bound is `bad_params`. The connection declares that it holds `path` open, and the room's open-document set gains
the path if it was not already in it.

The reply is `{ "result": { "documents": [ … ] } }`, the room's set after the change, so a
caller is told what its request did instead of assuming it. Every peer in the room, including the
one that sent the request, then receives a `doc.opened` event (§6) carrying the same set. A peer
**MUST** tolerate the same path being opened by several peers, and by the same peer twice: holds
belong to a connection, and opening a path twice from one connection is one hold.

### `doc.close`

Same shape with `method: "doc.close"`. Releases **this connection's** hold on the path, and
`path` is validated by the same non-blank rule as `doc.open`. The path leaves the room's
open-document set only when no other peer still holds it open; if another peer has the same
document open, the set does not change.

The reply is `{ "result": { "documents": [ … ] } }`, the room's set after the change, and every
peer receives a `doc.closed` event carrying it, even when the set is unchanged: every validated
close is announced, including a close for a path the connection never held, so a peer counting
`doc.closed` never diverges. Closing does not delete content: the `Y.Text`
remains in the session document, and a later `doc.open` by any peer sees it again.

A connection that disconnects releases its holds without announcing anything, but the paths it
held stay in the room's set: a hold belongs to a connection and the set belongs to the room (§1.2,
§9).

### `doc.grant`

```json
{ "v": "selvage/1", "id": 5, "method": "doc.grant", "params": { "paths": ["README.md", "src/main.rs"] } }
```

A `doc.grant` publishes the room's **grant**: the host's listing of the files in its working tree,
as an array of workspace-relative paths. Each is the same kind of value as `doc.open`'s `path` and
is held to the same rule: it **MUST** be non-blank and free of control characters (§5), and it
is otherwise unvalidated in this slice
(§12, [`NOTES.md`](NOTES.md) §B.8). A `paths` that is not an array, a member of it that is not a
string, or a blank one is `bad_params`; so is a request with no `paths` at all,
because the member is required.

The listing **replaces** the room's grant wholesale: it is a snapshot, not a delta, so a host that
grants fewer paths writes the shorter array and never has to say what was removed. An empty
`paths` is a valid listing and not `bad_params` ("this room grants nothing" is a statement a host
may make), and it is announced like any other change (§6.3).

A listing carries **files**, not directories. A path in `paths` names a file the host's working
copy held when it enumerated them; no frame carries a directory entry, and a receiver **MUST NOT**
expect one. A receiver that presents a tree derives it by splitting the paths it was given
(`src/main.rs` implies a `src`), and that implied directory is a rendering decision of the
receiver's, not something the wire said. One flat listing is what a client needs in order to offer
a search across the whole project, which a walk it had to drive directory by directory could not
answer.

Only the room's host **MAY** publish a grant. A `doc.grant` from a seated connection the server
does not hold as the room's host is answered with an error response carrying `bad_params`: the
vocabulary of §11 has no code for "not permitted" and this document adds none, so the code is the
one a malformed request gets, and a client **MUST NOT** read it as an accepted publication. The
server cannot tell whether a listing is the host's working tree and does not try: it has no
filesystem (§3), and §12 concedes that the host role is claimed rather than proven.

The reply is `{ "result": {} }`, because what the room has to say about the listing is the
`doc.granted` event and not the response, the same reason an accepted `session.rename` answers
with `{}`. The response **MUST** precede the event on the publishing connection, as a `doc.open`
result precedes its `doc.opened`, and **every** peer in the room, the publishing host included,
then receives `doc.granted` carrying the same listing (§6.3).

**The order of `paths` is defined, and it is part of what the frame says.** A client that
publishes **MUST** write its listing in ascending order of path, compared as a sequence of
**UTF-16 code units**, the unit this document counts in elsewhere, so a supplementary character,
which is a surrogate pair, sorts among the surrogates rather than where its code point would put
it. A server **MUST** carry the listing in the order it received and **MUST NOT** sort,
deduplicate, resolve or otherwise normalise it, so the bytes a room holds are the bytes its host
wrote. Beside `documents`, `paths` is the second array whose order is a claim
([`CANONICAL.md`](CANONICAL.md) §2.7), and the one array whose order the *client* fixes rather
than the server.

A host enumerates its working copy and the listing arrives whole: a `doc.grant` is one snapshot
and not a stream, there is no per-directory walk and no request for a subdirectory. That is
deliberate: paths are cheap beside content, so the room's *shape* arrives in one frame, while a
file's **content** is still fetched only when someone opens it (§7). A server **MAY** bound what
it will carry: a listing it will not store whole (too many paths, or a path longer than its own
limit) is answered `bad_params`, and the connection stays open. The numbers are the server's
policy and not a peer's contract, as §2.1's frame bound is. A host **SHOULD** bound its own
enumeration to match, leaving out what a working copy should not share and capping how much it
lists, so that a pathological tree (`node_modules`, a build output, a flat directory of ten
thousand files) cannot wedge the session: a listing over the transport's bound never arrives at
all, and ends the connection the way a dropped socket does (§2.1), while one over the server's
bound is a refusal the host has to shrink or give up on.

A malformed `doc.grant` on a seated connection (params that do not parse, no `paths`, a `paths`
that is not an array, a member that is not a string, a blank one, or a listing over the server's
bound) is answered with an **error response** carrying `bad_params`, correlated by the request's
`id`, and the connection **stays open**. It is not a refusal in the sense of §11: no
`session.error` event precedes it and no close follows. A malformed listing changes nothing: the
room keeps the grant it had, and a client **MUST NOT** read the room's grant as cleared because a
publication was refused.

There is deliberately **no capability name** for the grant. A host learns whether a server
implements this method by sending one and reading the answer: a server that does not know it
answers `unknown_method` and keeps the connection open (§5, §11), which a host **MUST** treat as
"this server has no grant", with no listing to publish to and no reason to end the session. Adding a
name of its own to `capabilities` would change that array in every `room.created` and
`room.joined`, a far larger change than the two frames it would announce; a name can be added in
its own change, with the corpus re-baselined for it ([`NOTES.md`](NOTES.md) §B.23).

### `session.rename` — the live rename

Any **seated** connection **MAY** rename itself at any time with a `session.rename` request. This
is the counterpart to `session.hello`: that one sets the name, this one changes it. The display
name is a peer's own and the only identity in this slice (§9), so no role or privilege is
involved. A connection **MUST NOT** rename another peer: the method names no peer, and a server
renames only the connection that sent it. A `session.rename` before seating is not a rename: the
first frame on a connection **MUST** be `session.hello`, and anything else is refused
`hello_required` (§11).

```json
{ "v": "selvage/1", "id": 4, "method": "session.rename", "params": { "display_name": "Ada Lovelace" } }
```

`display_name` in `session.rename` **MUST** satisfy the same rule as in `session.hello`:
non-blank, and at most **32 UTF-16 code units**, counted in UTF-16 code units, so an astral
character costs two. A server **MUST** refuse a rename whose `display_name` is blank or longer
than that and **MUST NOT** truncate it, exactly as for `session.hello`; the same bound is the
frame's field, the `PeerInfo` a server stores and echoes, and the `display_name` of
`peer.renamed`: one bound, wherever it appears.

A `session.rename` on a seated connection that is malformed (params that do not parse, no
`display_name`, a `display_name` that is not a string, blank, or over-long) is answered with an
**error response** carrying `bad_params`, correlated by the request's `id`, and the connection
**stays open**. It is **not** a refusal in the sense of §11: no `session.error` event precedes it
and no close follows, because §11 gives a seated `bad_params` an error response and the closing
refusal is the shape of a fault *before* seating. A client **MUST NOT** treat an accepted rename
as a reason to re-hello.

A server **MUST** announce an accepted rename to **every** peer in the room (the one that
renamed included) as a `peer.renamed` event (§6). It **MUST NOT** suppress the event when the new
name is the one already in force: an accepted rename is announced, so a receiver never has to
decide whether a name changed, and a mover's confirmation is the same frame every other peer
receives. A receiver that holds the peer **MUST** replace its `display_name` and **MUST NOT**
change any other field; one with no record for the `peer_id` **SHOULD** ignore the event, since
`peer.renamed` carries no `role` to insert.

`peer.renamed` is addressed like `doc.opened`/`doc.closed` (to everyone, the mover included), not
like `peer.joined`/`host.attached` (to the others). On the renaming connection the response
**MUST** precede the `peer.renamed` event, as a `doc.open` result precedes its `doc.opened`. The
event **MUST** be ordered after the renaming peer's `peer.joined` (or the `room.joined` that named
it) and before its `peer.left`; a peer is seated before it can rename and stops handling frames
before its `peer.left`, so no receiver can be told of a rename for a peer it was not told about. A
rename **MUST NOT** change the room's peer-list order, the peer's `role`, or its
`awareness_client_id`; it changes `display_name` and nothing else. A rename **MUST NOT** affect the
room's grace deadline: the room is as usable while hostless as it is for `doc.open` (§9), and a
rename neither reclaims nor keeps the room.

Every `session.rename` is answered with exactly one of a `result` or an `error` (§4.2); the result
of an accepted rename is `{}`, because the room's statement of the new name is the `peer.renamed`
event. A client **MUST** bound its wait for that answer (below).

A rename belongs to the connection that made it and dies with it, like its `peer_id`, role, holds
and awareness (§9.1): a reconnecting client is a new peer and its name is whatever its new
`session.hello` carries, so a client that renamed **MUST** re-hello with the current name.

### What a client owes a request

Four obligations on the request side, none of which changes the wire:

- **Every request is answered, and the wait has to be bounded.** `doc.open`, `doc.close`,
  `doc.grant` and `session.rename` are answered with a result or an error, and nothing obliges a
  server to answer promptly. A client **SHOULD** bound the wait, and the bound cannot be a
  protocol number: it has to be at least a round trip on the connection in use, and less than
  "for ever". (The two reference clients differ here; [`NOTES.md`](NOTES.md) §A.2.) In `selvage/2`
  `session.rename` is the only request answered this way: that version's `session.hello` is
  answered with `room.created` or `room.joined`, an event and not a response (§5, §6.1).
- **A socket that drops fails every request in flight.** When the connection ends, whether the
  client asked for it or not, each outstanding request **MUST** be failed locally: no answer can
  arrive on a socket that is gone, and a caller left holding a request that never completes cannot
  tell that from a slow server. Whether the server applied a request it never answered is not
  knowable, and a guess about it is an answer the wire does not carry; for the methods in
  `selvage/1` it does not matter: a hold is a set, so a second `doc.open` for a path already held
  changes nothing, a grant is a snapshot, so a second `doc.grant` that repeats a listing changes
  nothing, and an applied rename reaches the mover as the `peer.renamed` every peer receives
  (§6). In `selvage/2` the only request that can be outstanding is a rename — the handshake is
  answered with an event (§5) — and it is the same: its effect reaches the mover as the
  `peer.renamed` every peer receives.
- **A request id is not reused on a connection.** The id is the only correlation the wire has, and
  a client that reuses one cannot tell a late answer from a current one. A new connection may
  count from the beginning again, because it is a new connection: nothing survives it (§9.1).
- **At most one request is in flight on a connection.** A seated `session.error{bad_message}`
  carries no `id` (§11), so a client that pipelined could not tell which request it sank; a client
  **MUST NOT** pipeline, and it fails the outstanding request, if any, when such an event arrives.

### Unknown methods

Any other method name is answered with an error response, code `unknown_method`. The connection
stays open.

## 6. Events

All event `params` are flat objects.

| event | params | when | and therefore the receiver |
|---|---|---|---|
| `room.created` | `SessionParams` with `token` | reply to a `session.hello` that minted a room | is the room's host, and **MUST** keep `token`: nothing re-sends it (§5.1) |
| `room.joined` | `SessionParams` without `token` | reply to a `session.hello` that joined one | adopts `keepalive` and reads `documents` as the room's membership, not as its content (§6.1; `documents` is `selvage/1`'s and this version's reply does not carry it — §6.1's version passage) |
| `peer.joined` | `{ "peer": PeerInfo }` | to the peers already in the room when a connection is seated | adds the peer to the roster, and reads `peer.role` rather than assuming a guest arrived (`peer.role` is `selvage/1`'s: this version's peer record has no role, §6.1) |
| `peer.left` | `{ "peer_id": string }` | to the remaining peers when a connection ends | removes the peer, and **SHOULD** drop its awareness state (§8.4). A host leaving is *not* the end of the room (`selvage/1`: in `selvage/2` a room outlives any one connection and ends `room_grace_ms` after its last, §9) |
| `peer.renamed` | `{ "peer_id": string, "display_name": string }` | to every peer when a peer renames itself | replaces the peer's name and keeps the peer |
| `doc.opened` | `{ "peer_id": string, "path": string, "documents": [string] }` | to every peer when a peer opens a document | replaces its view of the room's set with `documents` |
| `doc.closed` | `{ "peer_id": string, "path": string, "documents": [string] }` | to every peer when a peer closes one, whether or not the closer held the path | the same |
| `doc.granted` | `{ "paths": [string] }` | to every peer when a grant is published, and to a joining connection right after its `room.joined` | replaces its view of the room's grant with `paths`, which is a list of names and not content |
| `host.detached` | `{ "grace_ms": number }` | to the remaining peers when the host's connection ends | starts the grace period: the room is still usable and still joinable |
| `host.attached` | `{ "peer": PeerInfo }` | to the remaining peers when a connection claiming `role: "host"` is seated into a room whose host is absent | reads `peer.role`: this is a reclaim, and a `peer.joined` for the same peer follows (§9.1) |
| `room.gone` | `{ "room_id": string, "reason": string }` | to the remaining peers when the room is destroyed | **MUST NOT** retry the URL: the room id is gone for good (§9.1) |
| `session.error` | `{ "code": string, "message": string }` | for faults that cannot be attached to a request id | reads `code` against §11; a code a retry cannot change means stop |

`reason` in `room.gone` and `message` in `session.error` are human-readable and not stable; a
peer **MUST NOT** depend on either.

**The events in `selvage/2`.** A server of that version authors seven frames and no more, which is
what a server of membership and a relay needs: `room.created` and `room.joined` (§6.1),
`peer.joined`, `peer.left`, `peer.renamed`, `room.gone` and `session.error`. `peer.left`, `peer.renamed`,
`room.gone` and `session.error` are the shapes above unchanged — a peer id, a peer id and a name, a
room's id and a reason, a code and a message — and two of the seven are read differently:

- **`peer.joined` carries the version's `PeerInfo`**: `peer_id` and `display_name`, with
  `awareness_client_id` optional and no `role` (§6.1). It has one meaning here — a connection was
  seated — so the `selvage/1` reading in which `peer.joined` may mean "the host is back" does not
  apply: no reclaim exists in this version.
- **`room.gone` has no recipient in this version.** A room is destroyed only when the grace after
  its last connection has run out (§9's room passage), and at that moment no connection is seated to
  be told, so the destruction is silent and a room's id being gone is learned as `room_unknown` by
  the next connection that names it. The event stays in the vocabulary and a `selvage/2` server
  produces none.

None of the seven carries a path, a role, a document name or any character of a room's text (§3),
and the five events the version does not have — `doc.opened`, `doc.closed`, `doc.granted`,
`host.detached`, `host.attached` — are `selvage/1`'s, each carrying state or an announcement this
version's server does not hold (§1.1).

### 6.1 / 6.2 `room.created` and `room.joined`

```json
{
  "v": "selvage/1",
  "event": "room.joined",
  "params": {
    "room_id": "r-a0bca377bb4e",
    "self": { "peer_id": "p-3d33…", "display_name": "Bob", "role": "guest", "awareness_client_id": 42 },
    "peers": [ { "peer_id": "p-852b…", "display_name": "Ada", "role": "host" } ],
    "documents": ["src/main.rs"],
    "capabilities": ["y-protocols/1", "awareness", "open-document-set", "host-reclaim"],
    "keepalive": { "ping_interval_ms": 30000, "awareness_renew_ms": 15000, "awareness_expire_ms": 30000 }
  }
}
```

- `PeerInfo` is `{ "peer_id": string, "display_name": string, "role": "host"|"guest", "awareness_client_id"?: number }`.
  `peer_id` is server-assigned, opaque, and unique per room.
- `token` is present **only** in `room.created`, and only for the connection that minted the room.
  It is never echoed in `room.joined`, not even to the host after a reconnect.
- `self` is the joining connection's own peer record.
- `peers` lists the peers already in the room, excluding `self`. No order is promised for it, and
  a receiver **MUST NOT** depend on one (`CANONICAL.md` §2.7).
- `documents` is the room's open-document set, in first-opened order. **Membership carries no
  claim that any peer holds content for a path**: it is a list of names, and the text, if any peer
  has it, arrives through the ordinary sync exchange (§7).
- `capabilities` is a set, like `peers`: no order is promised for it, and a receiver **MUST NOT**
  depend on one. `documents` is the only array in this frame whose order means anything. The same
  four capabilities and the same keepalive triple appear in `GET /meta` (§2): a server advertises
  one list, in two places, and a client reads either.

**`room.created` and `room.joined` in `selvage/2`.** Both replies are the shapes above with the
members that carried the server's state gone and nothing put in their place:

```json
{
  "v": "selvage/2",
  "event": "room.joined",
  "params": {
    "room_id": "r-a0bca377bb4e",
    "self": { "peer_id": "p-3d33…", "display_name": "Bob", "awareness_client_id": 42 },
    "peers": [ { "peer_id": "p-852b…", "display_name": "Ada" } ],
    "capabilities": ["y-protocols/1", "awareness"],
    "keepalive": { "ping_interval_ms": 30000, "awareness_renew_ms": 15000, "awareness_expire_ms": 30000 }
  }
}
```

- **`documents` is not a member of either reply.** This version has no server-held open-document
  set, so no frame says which documents are open, and a client reads nothing into a reply about
  them.
- **`PeerInfo` is `{ "peer_id": string, "display_name": string, "awareness_client_id"?: number }`**
  and nothing more: no `role`, because the server seats nobody as anything (§1.2). `self` and every
  member of `peers` are that shape, and `token` stays in `room.created` alone and is never echoed.
- `capabilities` and `keepalive` are the same two members and are read the same way; §2 says what
  they hold in this version, and `peers` and `capabilities` are still sets whose order means
  nothing.

**What a `selvage/2` peer learns at join.** `room.joined` is the whole of it: the room's id, the
joiner's own peer record, the roster of peers already seated, and the session's advertisement. It
does **not** carry the room's listing, the roles the host assigns, which documents are open, or who
the host is, and no later server frame does either: every one of those is a peer's own statement,
sealed under the frame key and signed. The host publishes the room state when a peer is seated, so
the listing and the roles reach a joiner one relay hop late rather than inside its reply, and a
joiner that arrives while the host is away gets no state until one arrives or the room is
destroyed. Until a state verifies, the joiner knows nobody's key and **MUST NOT** apply content:
§7.1's envelope is what makes a frame authentic, and there is no second rule beside it. It **MUST
NOT** publish content either, until a state commits the session key it is signing with — its
session-key announcement is what it sends in the meantime (§7.1) —: before that it does not know
its own role, and its frames are frames no peer can attribute. §13.1 is the order a client works
through.

### 6.3 `doc.granted`

```json
{
  "v": "selvage/1",
  "event": "doc.granted",
  "params": { "paths": ["README.md", "src/main.rs"] }
}
```

- **Sent to every peer in the room, the publishing host included, whenever the grant is
  published**: the `doc.opened`/`doc.closed`/`peer.renamed` addressing rule, not the
  `peer.joined` one. A change is announced even when the new listing is equal to the one already
  in force, exactly as an accepted rename to the current name is: the event says what the room's
  grant now is, so "the request was applied" and "the room was told" stay the same observable
  thing and no receiver has to decide whether anything changed.
- **Sent to a joining connection immediately after its `room.joined`, and to a minting connection
  after `room.created`, if and only if the room's grant is non-empty.** A server **MUST** order it
  after that reply, which §5 already promises is the connection's first frame, and **MUST NOT**
  send one for an empty grant: a joiner of a room that grants nothing receives `room.joined` alone
  and has nothing to miss. A fresh room's grant is always empty, so a mint never produces this
  event. This is how a joiner learns the room's listing without a round trip and without a fifth
  member in `room.joined`. The listing sent is the snapshot at seating, taken under the seating
  lock: publications are serialised with seatings, and every publication after that snapshot
  follows it on the joining connection.
- **It names no peer.** A grant is the host's and a room has one host, so the event carries the
  room's listing and not its author; a receiver that wants to know who published it reads the
  roster.
- **A receiver MUST replace its view of the grant with `paths`**, whatever it held before, and
  **MUST NOT** merge the two: a shorter listing is a smaller grant, not a partial one.
- **`paths` carries no content and no promise.** Membership does not claim that any peer holds a
  `Y.Text` for a path, that a file exists, or that the path is readable: it is a list of names to
  offer, and the text of one arrives, if a peer has it, through the ordinary sync exchange (§7). A
  client **MUST NOT** read the grant as evidence that content has arrived, exactly as it must not
  read `documents` that way (§6.2), and it **MUST** treat a listed path as a candidate rather than
  a promise: the host may since have deleted it, may be unable to read it, or may decline to seed
  it, and the server verifies none of that (§12). A receiver that presents a tree derives its
  directories by splitting the paths (§5); no frame carries one.
- **The listing belongs to the room and outlives every peer**, as the open-document set does
  (§1.2, §9). A host that disconnects releases its holds and leaves the grant in place; a host that
  returns inside the grace period inherits it and learns it from the join-time `doc.granted`;
  republishing an unchanged listing is allowed and not required; destroying the room destroys it.
- **A receiver that does not know the event ignores it**, as it ignores any event name it does not
  know, and falls back to the open-document set (§10, §11).

### What a client owes a join

One obligation on the client side, and it changes no bytes:

- **A client that is seated SHOULD present the room's documents**, at least the first of them
  that it can resolve, rather than waiting for its user to go and open a file by hand. The
  `documents` list above is the room's open-document set, so a client that holds it and shows
  nothing has been handed the room and not shown it.
  - **It is a SHOULD, not a MUST.** A client with no editor in front of it, or one that can
    resolve none of the named paths, has nothing to present and owes nothing.
  - **The first it can resolve, not all of them.** A room can name several documents, and
    opening every one of them for every newcomer is a hostile thing to do to an editor.
  - **What it is deciding is what to *show*, not what to fetch.** A path in the set is a name and
    carries no content: the text, if any peer has it, arrives through the ordinary sync exchange
    (§7). A client that presents a document before its text has arrived shows an empty one and
    fills it in, and the protocol neither requires nor forbids that. A client **MUST NOT** read
    the list as evidence that content has arrived.
  - **Presenting is not `doc.open`.** The set already names the path, so a client shows it
    without asking for it. `doc.open` is what declares a *hold*, and a client that wants the
    path to stay in the room's set after its other holders close it sends that itself (§5).

## 7. Document sync

Follows [`y-protocols/PROTOCOL.md`](https://github.com/yjs/y-protocols/blob/master/PROTOCOL.md).

- **One `Y.Doc` per session, one `Y.Text` per document**, keyed by workspace-relative path.
  Document identity is the path, and it travels in the `doc.open` method; it is not encoded inside
  the CRDT.
- A binary frame is `varUint(message_type)`, then:

  | message_type | meaning | body |
  |---|---|---|
  | 0 | sync | `varUint(sync_type)` then `varUint8Array(payload)`; `sync_type` is 0 = SyncStep1 (state vector), 1 = SyncStep2 (update), 2 = Update |
  | 1 | awareness | `varUint8Array(awareness update)` |
  | 2 | auth | not sent in this slice: there is no per-join approval. A receiver that gets one reads it and ignores it (§8.3) |
  | 3 | awareness query | not sent in this slice. A client **MAY** ignore one it receives, and a client that answers **MUST NOT** answer more than one such message for one frame (§8.3) |

  `varUint` is LEB128; `varUint8Array` is a `varUint` byte length followed by the bytes. The sync
  payloads are yjs v1 encodings.
- **A frame MAY hold several messages.** The body above is one message, and a binary frame is a
  *stream* of them, one after another, with no count and no terminator: a receiver reads messages
  until the frame ends. A frame carrying an update followed by an awareness state is valid and
  **MUST** be handled in full.
- **Who sends what.** Each client, immediately after `room.joined`/`room.created`, sends a
  SyncStep1 with its state vector. Every peer that receives a SyncStep1 replies with a SyncStep2
  containing what the sender is missing. Local edits are broadcast as Update messages carrying
  only the delta. The server relays each binary frame to every other participant in the room, and
  to nobody else. **In `selvage/2` that first send waits on a verified state**: a client sends its
  SyncStep1 once a state commits its own session key, because one sent before that is a frame every
  conforming peer refuses `uncommitted_key` (§13.1's step 6).
- **Convergence.** Two replicas are converged when their texts are identical **and** their state
  vectors agree. A client that has just joined is brought up to date by this handshake; the server
  has nothing to replay.
- **Ordering.** The server offers no cross-peer ordering guarantee (each peer has its own queue).
  yjs convergence does not require one, but an editor adapter **MUST NOT** assume ordering between
  documents or between peers.

### 7.1 The sealed frame (`selvage/2`)

A `selvage/2` binary frame is one **sealed frame**. Its bytes are [`CANONICAL.md`](CANONICAL.md)
§6.1's — the AEAD, the key schedule, the key id, the counter, the signature, and the order a
receiver checks them in — and this section says what the frame carries. The message table above is
unchanged by it: it is the plaintext of a `kind = 0` frame.

**Where the keys are.** The room key and the host's public key travel in the invite URL's fragment
(§5.1), which a user agent never sends to the server. A peer derives the frame key from the room
key and mints a **session keypair** for the connection it holds, which it announces in a
**session-key announcement** (`kind = 4`) and whose public key the host commits in the room state.
The server holds none of the three: it relays a sealed frame byte for byte (§3) and can read
neither what the frame carries nor who wrote it.

**The room state** (`kind = 1`) is what the host publishes, sealed under the frame key and signed
by the host key: the room's listing, the roles the host assigns, and the state's own edition.

- `listing` is the room's working tree as the host enumerated it — names, no content, and the same
  kind of value and the same kind of claim as a path in `selvage/1`'s grant (§5) — replaced
  wholesale by every state, so a shorter listing is a smaller working tree and not a partial
  update.
- `peers` is the roster the host seats, **keyed by public key**, the host's own entry included: the
  role the host gives each key — `host`, `guest` or `viewer` — and the `peer_id` the host believes
  that key's holder is seated under. The key is what a frame is verified against and what the role
  belongs to, and it is not a value the server can supply: the server does not know it and could
  lie about it, so a receiver uses the one it verified. The `peer_id` beside it is a **label and
  not a binding**: `peer_id` is the server's word (§9), so the server is neither the source of a
  key nor any authority on which key holds a name, and a role bound to a name would be a role any
  peer could claim by claiming the name (§13.4). A client reads the label only for the presence
  questions that are about a key (§13.7, §13.8). The entry whose role is `host` names its own
  connection's **session** key and not the host key: the host key signs `kind = 1` and `kind = 2`
  and nothing else, and the host's ordinary frames are verified against the key its `host` entry
  names like any other peer's ([`CANONICAL.md`](CANONICAL.md) §6.1).
- `issued` is the state's edition, and a receiver applies a state only if its `issued` is
  **strictly** above the one it holds: the two are compared, never the envelope's counter, because
  the host key is minted with the room and outlives the connection any one state was published on
  ([`CANONICAL.md`](CANONICAL.md) §6.1). What a receiver does with a state that is not above, and
  with two publications that carry one edition, is §13.3's.

Nothing else carries the listing or the roles in `selvage/2`, and a joiner therefore holds no
committed key until a state arrives: until then it refuses every content frame, because the
envelope's own key resolution — not a second rule beside it — is what makes content authentic. §13.1
orders what a client does around that: what it may publish, where §7's sync handshake comes, and
what it shows a person meanwhile.

**How a key reaches the host, and who assigns a role.** No frame the server authors carries a key,
and a peer cannot learn another's from the server, so every peer states its own. A client **MUST**
announce its session key before it sends any other binary frame (§13.1) — unless it is the host,
whose first state commits its own connection's key, so it has nothing to announce (§13.1) —: a
`kind = 4` frame sealed under the frame key, signed by the very key it names, and naming no peer,
so that a receiver learns from it that the key exists and that whoever holds it speaks, which is
the whole of what it claims ([`CANONICAL.md`](CANONICAL.md) §6.1). That frame is the one binary
frame a client may send before
a state commits its key, and the one thing that makes such a commitment possible at all. A client
**MUST** announce again when it applies a verified state that does not commit its key, because a
relay may drop a frame (§13.2) and the host cannot commit a key it never received.

**What a host commits, and what the label beside it is worth.** A host **MUST** commit in its state
every announcement it accepts, and **MUST NOT** withhold a commitment for want of a label. The
entry's `peer_id` is the seat the host believes holds the key — the announcer's own seat where it
can tell which seat that is, and otherwise any seat the roster names, its own included — because
nothing on the wire ties a key to a seat: an announcement names no peer, and the relay says nothing
about which connection sent one (§13.4). The label decides no attribution and no role (§13.4) and is
read only for the two presence questions §13.7 and §13.8 ask, so a wrong one costs those questions
alone for that one key and a host **MUST NOT** seat, unseat or identify anybody with one; a withheld
commitment costs the peer its whole session, because §13.1's step 4 leaves a peer whose key no state
commits unable to publish anything at all. **At most one key per seat is committed**: a seat is one
connection (§9), so a state that commits a new key for a seat replaces the key it held there and the
replaced key's frames are refused `uncommitted_key` from that state on (§13.3). **An announcement
whose key the state already commits publishes nothing new**: the room knows the key, the announcement
changes no role (below), and what it does say is that its sender has not applied the state that
commits its key — the only reading a re-announcement (§13.1) has. So a host **MUST** answer one with a
state that sender can apply, either the fresh one it would publish anyway or the one it already holds
re-sent unchanged (below), and **MAY** treat one answer as answering every announcement of that kind
it accepts inside the next `awareness_renew_ms`. That window is what keeps the obligation from being a
flood: a peer that announces without bound obliges at most one state frame a window, while a peer
whose state went missing gets it back inside that window rather than waiting for the room's next
change.

**A host discharges its publish obligation once a window.** A host **MUST NOT** be obliged to publish
more than one state in any `awareness_renew_ms` on account of the announcements it accepts: the first
state it publishes in a window carries every key it has committed by then, so that one state answers
every announcement it had accepted before it. A peer that mints keys without bound therefore obliges
at most one state broadcast a window rather than one per frame, which is what keeps the commitment
affordable — an announcement is one frame and a state is the room's listing, and a host cannot tell
which connection sent one. A host **MAY** publish more often, answering a later announcement at once
instead of folding it into the next window; that is a host's own rate policy, as §2.1's inbound bounds
are a server's, and a host that takes it is choosing to answer a frame rate it cannot attribute.

**The host assigns the role, and its pairing can be wrong.** What a host has to pair a key with is
the roster it was sent — `peer_id`s and display names — and whatever it handed out, and nothing on
the wire ties a key to a seat with any authority, which is exactly why the role binds to the key
and not to the name (§13.4). A client **MAY** declare in its announcement the role it believes it
has been given, `guest` or `viewer` and never `host`, and a host **SHOULD** honour a declaration
when it first commits that key: it is the one statement about a peer's role that comes from that
peer's own key, and it is no weaker a source than the roster, which is the server's word about a
name the server minted. **A declaration changes nothing for a key the state already commits.** The
role in the state is the room's and the declaration is the peer's own word about itself, so a host
that has committed a key as `viewer` does not re-role it because a later announcement asks: the
stickiness is what makes a `viewer` a `viewer` for as long as the state stands, and a peer that
declares a role it was not given is a non-conforming client exactly as one that ignores a `viewer`
role it was given is (§13.5's residual). What holds a room together under a wrong pairing is not the
pairing but the clients that conform to the state: a role is enforced by peers that honour it. The
residual a wrong pairing leaves is stated with it: a key committed under the wrong role can make a
guest read-only, or a `viewer` writable, for as long as the state stands.

**Who publishes one, and when.** Only the peer holding the private half of the host key can publish
a state: that is the whole of what being the host is in this version, and there is no claim, no seat
and no reclaim for a peer to make (§5's minting passage, §9.1's return). The host's obligations are
these, and they are what makes the state the room's authority:

- **MUST** publish a state when it mints a room, which is also what brings the room's listing into
existence;
- **MUST** publish one on every change to its `listing` or to `peers`, on every `peer.joined` and
  every `peer.left` it receives, which is how a joiner learns the listing and the roles without
  asking and how a departure's key leaves the room's statement with it (§6.1), and on every
  session-key announcement it accepts that commits a key its `peers` does not already carry, which is
  how a key it has just learned reaches every other peer. An announcement whose key the state already
  commits changes no member of it: it brings no state of its own, and what the host owes it is the
  state its sender has not applied, re-sent as the paragraph above says;
- **MUST** write an `issued` above every state it has published and, once it has verified a state at
  or above that edition, above that one instead. Its first state carries `issued` `1`, because the
  mark a receiver compares against starts at `0` and a state that is not above it is refused
  ([`CANONICAL.md`](CANONICAL.md) §6.1). A host that persists its host key **MUST** persist
  its `issued` with it and continue the series rather than restart it: a state at or below the
  room's edition is refused by every peer, and nothing in the protocol carries that refusal back to
  its publisher (§13.3);
- **MUST** give exactly one key the role `host`, its own connection's, because that entry is what
  tells a receiver which seated peer holds the host key;
- **MUST** keep its `peers` a statement about the seats the roster has: a key whose entry labels a
  seat the roster no longer has is dropped from every state it publishes. One key per seat within a
  seat, and the roster's size across seats, are then together the bound on `peers`. The label is what
  it is read for here — the roster is the authority on which connections are seated (§13.4), so the
  host's statement follows it — and its own entry survives by that rule while it is seated, because
  the seat it labels is one the roster has. A host that has left publishes nothing, and the state it
  last published is what tells its peers the host is away (§13.8); the state it publishes on its
  return carries its own new key and replaces that entry (§9.1);
- **MUST NOT** hold two host sessions for one room at a time. Two connections that share a host key
  and a counter series publish one edition twice, and §13.3 states what a receiver does with that.

**A peer that holds a verified state SHOULD re-send it, unchanged, when it sees a `peer.joined`.**
The bytes it re-sends are the ones it received: only the host key signs a state, so a peer that
re-sealed or re-signed one would hand the room a frame every other peer refuses `uncommitted_key`.
The re-send is what lets a joiner's state arrive while the host is away, and what lets a returning
host that has lost its `issued` learn the edition the room holds before it publishes above it
(§9.1). A peer that re-sends a state should expect every peer already holding that edition to refuse
it `stale_issued`, which changes nothing; the host's own obligation above supersedes this for the
host, whose fresh state is what a joiner needs from it.

**The closing** (`kind = 2`) is the host's statement that the room is over. It is signed by the
host key and ordered by `issued`, and a receiver applies one only when it already holds a verified
state below it: `issued` is an order between two states, and a receiver that holds none has nothing
to compare a closing against, so it ignores one that arrives first (§13.10). A relay that replays an
old closing to a peer holding the room's edition therefore changes nothing, which is what the
closing's `issued` above every state is for. What a replay can do with a closing is a composition of
two frames rather than one: a replayed *state* is applied by a receiver that holds none (§13.3), and
a replayed genuine closing above that state's `issued` is then applied as well — §13.10 names that
pair and the rule that a receiver holding no state ignores a closing is a bound on one frame and not
on a relay.

**A peer's holds** (`kind = 3`) are the paths one connection keeps open, published sealed under the
frame key and signed by that connection's session key: a set of paths, replaced wholesale by the next
holds message under the same key, and no member of it names the peer it is about. §13.7 states who
publishes one, what renews it and what expires it.

*(informative)* This section and [`CANONICAL.md`](CANONICAL.md) §6.1 are written from the design
rather than observed on a wire, and exist so that a corpus, a client and a server can be written
against frozen bytes. The session layer that version's passages state is written — §1.2's entries,
§2's `/meta`, §2.1's bounds, §3's server and its account of what the relay cannot do, §5's handshake
and the invite's fragment, §6's events, §6.1's replies, §7.1's four sealed values, §8's awareness
passage, §9's room, §9.1's return, §10's gate, §11's vocabulary, §12's scoping and §13, whose holds,
lease, presence clock and lifecycle rules complete the peer side — and what remains unstated belongs
to the corpus and the implementations ([`NOTES.md`](NOTES.md) §B.35). §1.1 is where the two versions'
passages are told apart.

### Document content: line endings and the trailing newline

The protocol never carries a document's text as text: it carries CRDT operations, and whatever an
adapter wrote is what the `Y.Text` holds, byte for byte. So two clients that disagree about how a
document is *written* do not disagree about the protocol: they edit each other's buffer for ever,
and neither converges. What a client's content must be, and all that is required:

- **LF is what the CRDT holds.** A client writes `\n` into the `Y.Text` and restores the
  document's own convention when it *renders*, never writing the restored text back. A CRLF
  adapter and an LF adapter that both enforce their convention rewrite each other's text on every
  pass; the CRDT ends up with whichever wrote last, and the other client's offsets then address
  the wrong character.
- **There is no trailing-newline invariant.** No part of `selvage/1` adds, removes, or requires a
  final newline, and two adapters that each "ensure" one (the shape of a format-on-change
  feature) edit each other's document indefinitely. An editor that wants the invariant owns it
  in exactly one place, and **MUST NOT** treat its own application of it as a local edit.
- **The line convention changes what an offset means.** A rendered CRLF document is one byte
  longer per line than the CRDT's text, so an adapter that publishes absolute offsets has to
  convert them, and an adapter that publishes relative positions (§8.1) does not. This is the
  second reason to prefer relative positions, after concurrent editing itself.

None of this is a new field or a method: it is a statement about what a client writes into its
replica, and it is normative because a client that ignores it converges on a document its peer is
not looking at.

## 8. Awareness

Awareness uses the y-protocols awareness format, inside `message_type = 1` frames. Convergence of
cursors is peer to peer; the server relays and forgets.

**Awareness in `selvage/2`.** The frame is the same one — a `message_type = 1` awareness update,
opaque to the server — and three things read differently in that version.

- **`path` names a path the room has a reason to hold.** That version's server keeps no
  open-document set (§1.2), so §8.1's meaning rules are `selvage/1`'s and their reading in
  `selvage/2` is this: a `path` normally names a path the sender announces in its holds (§13.7) or
  one the host's listing names (§7.1). A state naming any other path is still relayed and may still
  be displayed; it is not an error, exactly as in `selvage/1`. Nothing about the shape of an anchor
  or the way a receiver resolves one changes, and §8.1.1 is the whole of it in both versions.
- **Timing.** A peer publishes awareness only once a state commits its session key: until then it
  has not announced its key and no conforming peer can attribute what it sends (§13.1's step 4,
  §13.6). The first binary frame it sends is its session-key announcement and not an awareness
  state, and its first awareness state follows its first verified room state. §8.3's steps 1 and 2
  are unchanged; step 3 is `selvage/1`'s.
- **Attribution.** A cursor belongs to the key that signed the frame it arrived in, and a display
  name to the roster entry that answers for the awareness client id (§8.4). An
  `awareness_client_id` is a number a connection claims in both versions and is an identity in
  neither.

### 8.1 The state payload

y-protocols leaves the awareness state opaque. This protocol's state is:

```json
{ "path": "src/main.rs",
  "selection": {
    "anchor": { "tname": "src/main.rs",
                "item": { "client": 5466766094545993, "clock": 11 }, "assoc": 0 },
    "head":   { "tname": "src/main.rs",
                "item": { "client": 5466766094545993, "clock": 13 }, "assoc": 0 } } }
```

The shape a yjs client produces is `tname` **and** `item` together; a `yrs` client emits the same
position as the `item` alone. Both are conforming, and see the scope rule below, which is the
single detail an implementation is most likely to get wrong.

Both fields are optional, and identity is **not** here: a display name travels in the session
layer (§6.1), and a mid-session change to one is announced there as `peer.renamed` (§6). A client
that understands neither field ignores a state it cannot parse, and still relays the frame:
awareness is opaque to everything but its readers.

The shape is this protocol's, but a *meaning* is not: two clients that disagree about what a
selection means show each other no cursor, or the wrong one, and nothing about the frame reveals
it. So, in `selvage/1`:

- **`path` normally names a document in the room's open-document set** (§5). A state that names
  another path is still relayed and may still be displayed; it is not an error.
- **`selection.anchor` and `selection.head` are CRDT anchors, never offsets.** Each is an object
  in the format of a yjs `RelativePosition`, carrying a scope, an optional element, and an
  association:
  - **At least one** of `item`/`tname`/`type` **MUST** be present, and **at most one *scope***:
    `tname`, a root type name, which for Selvage is the document path, or `type`, a nested type
    (never produced by this version), never both at once. A scope is not required when `item` is
    there.
  - **`item`** (`{ "client": number, "clock": number }`) names the element the position sits
    against. When it is present it is **authoritative** for the position, and a scope beside it is
    a check on that element rather than a second way of naming the position. When it is absent the
    anchor denotes an end of the scope, chosen by `assoc`.
  - **`assoc`**: `0` for the element *after* the position, `-1` for the one *before*. It may be
    omitted, and then defaults to `0`. A publisher publishes both endpoints of a selection with
    `assoc: 0`, unless it deliberately wants the other policy on one edge (an endpoint that stays
    outside an insertion at that edge), in which case it publishes `-1` there. A receiver **MUST**
    accept `-1`; §8.1.1 and `CANONICAL.md` §2.4 say what the choice means and what it costs.
  - **The two shapes that name one element denote the same position.** A receiver **MUST** resolve
    `tname` beside `item` and `item` alone alike: which of the two a peer writes depends on its
    library, not on what the position means.

  Three anchors, each conforming, and what each denotes:

  ```
  // yjs, a caret inside a root type: the scope names the type and `item` the element.
  { "tname": "src/main.rs", "item": { "client": 5466766094545993, "clock": 11 }, "assoc": 0 }

  // yrs, the same position: the element alone.
  { "item": { "client": 5466766094545993, "clock": 11 }, "assoc": 0 }

  // either library, a position with no element to name: here the end of the text with
  // `assoc >= 0`, the start with `assoc < 0`, and anywhere in an empty text.
  { "tname": "src/main.rs", "assoc": 0 }
  ```

  The first form is not a theoretical allowance: yjs's `createRelativePositionFromTypeIndex` on a
  root type emits it (verified as
  `{"tname":"src/main.rs","item":{"client":…,"clock":2},"assoc":0}`), while `yrs` holds one or the
  other in its `IndexScope` and emits the second for a position inside a root type and the third
  for an end of one. A receiver that insisted on a single member, or on a scope, would show no
  cursor whatever for a peer on the other library, which is precisely the silent
  cross-implementation failure this section exists to prevent.

  **No index is ever carried on the wire.** An anchor stays valid for as long as the CRDT
  remembers the element it names, and the offset it denotes is recomputed by each receiver against
  its own replica. That is what makes a cursor survive a concurrent edit: an absolute offset drifts
  by the length of every edit landing before it, so a 157-character paste above a peer's caret
  moves that caret 157 characters and leaves it somewhere plausible-looking and wrong.
- **A sender MUST omit `item` for a position that has no element to name**: the end of the text
  with `assoc >= 0`, the start with `assoc < 0`, and anywhere in an empty text. The anchor is then
  its scope and `assoc` alone. This is not a degenerate case to be routed around: it is the only
  encoding that exists for those positions, and it is the one that behaves correctly, because
  `tname` with `assoc 0` follows appends forever and `tname` with `assoc -1` ignores prepends
  forever.
- **A sender MUST NOT publish a selection it cannot anchor.** If the `Y.Text` named by `path` is
  not in the sender's replica, or an endpoint is past that text's end, the state carries `path`
  and no `selection` at all. Falling back to the scope-only form instead would be inventing an end
  of text: the resulting state is byte-identical to a genuine caret at the end, so every peer
  resolves a position the sender never meant and nothing on the wire can say so. This is reachable
  without trying: a client that publishes presence when it joins, or when a document it has open
  is not in its replica yet, has nothing to anchor against until the sync frame arrives.

#### 8.1.1 What a receiver does with an anchor

A receiver resolves each endpoint against the `Y.Text` named by `path`, and **MUST** verify that
the resolved branch is that text. The cases, in full:

| the anchor | it resolves to | when it fails |
|---|---|---|
| `item`, with or without `tname` | the element it names, if the receiver's state vector is past it: the surviving boundary if that element was since **deleted** (a success, not a failure) | the element is not known to the receiver, or it does not resolve into the `Y.Text` for `path`, or an accompanying `tname` is not equal to `path` |
| `tname` alone, `assoc >= 0` | the **end** of the text | `tname` is not equal to `path` |
| `tname` alone, `assoc < 0` | the **start** of the text | `tname` is not equal to `path` |
| `type` | the nested type it names | always: `selvage/1` has no nested types, so a scope resolving anywhere other than the `Y.Text` for `path` fails |
| none of `item`/`tname`/`type` | — | always: at least one is required |

- **If either endpoint fails to resolve, the state carries no selection.** A receiver **MUST NOT**
  fall back to an offset, clamp to a guess, or otherwise manufacture a position. Resolution is
  *deferred*, not part of applying the awareness update: awareness frames and sync frames travel
  on independent queues, so a receiver that does not yet hold the document keeps the state and
  retries on the next change or renewal. A receiver **MAY** keep showing the last position that
  did resolve for at most one renewal interval, so a transient gap does not blink every cursor
  away, and **MUST** stop there, because longer is exactly the drift this shape exists to
  prevent.
- **A client MUST republish the same anchors on renewal** (§8.2), rather than recomputing them
  from an offset that may have shifted underneath it.
- **`head` resolving before `anchor` means the selection was made backwards**, just as it did when
  these were integers: direction stays implicit in which endpoint lands further left. A caret is
  an `anchor` and a `head` that resolve to the same index.
- **A receiver MUST ignore unknown keys**, in the state object and in an anchor object alike, so
  that a later version can add a member without breaking a receiver (§4.1). An `assoc` that is
  neither `0` nor `-1` is normalised to "after" (`>= 0`) or "before" (`< 0`), and any number is an
  `assoc` whatever its precision. A member a receiver cannot read *at all* costs **only the
  selection**: the state still names the document it is about, and a receiver that threw the whole
  state away would lose the one thing in it that it could read.

Because no offset reaches the wire, the protocol fixes no offset unit. An implementation that
speaks offsets across an editor-adapter seam fixes the unit **at that boundary**, and it **MUST**
be the unit its editor uses, since that is the unit the anchor was computed from. VS Code and
`yjs` both count UTF-16 code units, so a client built on `yrs` **MUST** build its document with
`OffsetKind::Utf16`: `yrs` defaults to `OffsetKind::Bytes`, under which an anchor taken from a
non-ASCII document is already wrong before any concurrency is involved. The CRDT clock inside an
`item` anchor is unaffected by the choice.

### 8.2 Renewal and expiry

- **The server advertises the session's awareness clock, and a client runs on it.** The
  `keepalive` object in `room.created`/`room.joined` (and in `/meta`, §2) is the session's value:
  it is what a client renews and expires its peers' cursors by. A client that substitutes its own
  numbers chooses to disagree with its peers about when a cursor is stale, a local choice and not a
  protocol one, and the one way two conformant clients can show each other different cursors with
  nothing on the wire to say why. A client **MUST** renew its own state every
  `keepalive.awareness_renew_ms` by republishing it with a newer awareness clock.
- **Those numbers are the only clock, and they are not necessarily 15 s and 30 s.** They are one
  implementation's defaults, not the protocol's values; a server **MAY** advertise anything
  positive. An implementation **MUST NOT** hardcode them, and a client built on a library that runs an
  awareness clock of its own (`y-protocols`' `Awareness` installs a 15 s/30 s `setInterval` when
  it is constructed) **MUST** stop that tick and drive renewal and expiry from the advertised
  values instead. A client that leaves both running has two clocks that disagree: a cursor expires
  at the wrong moment, or a test waits fifteen seconds for a state the server said went stale in a
  quarter of one. (The conformance suite compresses the window precisely so that this is
  testable; [`NOTES.md`](NOTES.md) §A.1.)
- **A client drops a *remote* state it has not seen for `keepalive.awareness_expire_ms`.** Expiry
  is checked on the renewal tick, so a state is forgotten at the first tick after
  `last_updated + awareness_expire_ms`, which is **within `awareness_expire_ms +
  awareness_renew_ms`** (45 s at the defaults above) and not exactly at the expiry value. A
  client **MUST** forget a state that was not renewed inside `awareness_expire_ms`; the renewal
  tick only decides *when* it notices.
- **The server does not track awareness and does not expire it.** A client alone in a room
  therefore never churns: it never receives an echo of its own awareness, and never expires
  itself.
- **This expiry is deliberately loose**, because the alternative, a timer per remote state, is
  what the y-protocols renewal tick exists to avoid. A client that needs the tighter bound is free
  to check more often.

### 8.3 Discovery

There is no awareness handshake in `selvage/1`:

1. On seating a connection the server sends `peer.joined` to the peers already present.
2. Each of those peers republishes its own current awareness state.
3. The newcomer publishes its own state as soon as it is seated.

Step 3 is `selvage/1`'s alone: in `selvage/2` a newcomer publishes nothing until a state commits
its session key, and its first binary frame is its session-key announcement rather than an
awareness state (§8's version passage, §13.1). Steps 1 and 2 hold in both versions.

`message_type = 3` (awareness query) and its reply are part of y-protocols. Nothing in `selvage/1`
sends one — the three steps above are the whole discovery story — and a client **MAY** ignore one
it receives. Answering is a hazard rather than a courtesy: a binary frame is a stream of messages
with no count (§7), so a receiver that answered each message could be made to answer a whole
frame's worth of them, and a legal 256 KiB frame of one-byte query messages draws 256 000 replies
from an implementation built on y-protocols' own protocol handler. A client that does answer
**MUST NOT** answer more than one query message per frame, so that one frame costs one reply
whatever it holds. A `message_type = 2` (auth) message is read and ignored: this slice has no
per-join approval for a denial to be about, and a denial inside one neither ends the connection
nor costs the frame's other messages.

### 8.4 Attributing a cursor to a person

An awareness state is keyed by a y-protocols client id, which carries no identity. Session
`PeerInfo` therefore carries `awareness_client_id`, supplied by the client in `session.hello`. An
editor adapter joins the two: awareness client id → peer → display name and, in `selvage/1`, role.

Nothing requires an `awareness_client_id` to be unique within a room, and the mapping is
last-claimant-wins: a client that reuses an id after reconnecting makes the id name two peers, and
a stale state carrying the old clock can still be in flight. A client **SHOULD** use a fresh
awareness client id for every connection, which is what keeps the mapping one-to-one (§9.1).

An `awareness_client_id` is **not an identity**, and a receiver **MUST NOT** treat it as one. It
is a number a client chooses and the server carries: nothing on the wire binds it to a `peer_id`,
and because the token is the whole permission (§12), any holder may claim an id a seated peer is
already speaking with, to publish a cursor under that peer's name or to publish nothing and so
hide the peer's own state behind the claim. The session layer's identity is `peer_id` and the
display name attached to it, and a name is only as trustworthy as the token that allowed the
claim; an adapter that keys presence to the awareness id alone is reading a number two peers can
hold. Whether the server should mint the id or refuse a claim already in force is open
([`NOTES.md`](NOTES.md) §B.24).

When a peer leaves, its awareness state **SHOULD** be dropped locally rather than left to expire
by the clock (the room's roster is the authority on who is present, and a state whose peer has
gone has no cursor to show), but only the state for the awareness id it last claimed, and only
while no other seated peer still claims that id.

## 9. Rooms and the lifecycle

**Two lifecycles are stated in this section.** The one below is `selvage/1`'s, and the passage at
the end of the section is `selvage/2`'s. What belongs to the first version is everything that needs
the server to know who the host is — the minting connection *being* the host, one host connection at
a time, `host_present`, the `host.detached`/`host.attached` pair, the reclaim, and a room destroyed
when its grace runs out with no host. The token, the `peer.left` a connection's end produces and the
keepalive hold in both versions as written, with the one sentence of them that reads differently
carrying both readings at it.

- A room is minted by a connection that arrives without `room` in its URL; that connection is the
  **host**. Guests join with the room id and token. A connection that mints a room is its host
  whatever it claims in `session.hello`: minting *is* hosting, so a claimed `"guest"` role on a
  minting connection is ignored: the alternative, refusal with `bad_params`, would leave a token
  holder unable to host the room it just created, and the alternative of honouring the claim would
  produce a room with no host whose real host is then refused it with `host_present`.
- The **token is the permission** (§12). Any holder may join; there is no per-join approval. The
  token is secret: it is in the invite URL and nothing else.
- **Exactly one host connection at a time.** A joining connection that claims `role: "host"` while
  a host is present is refused with `host_present`, and the room is untouched. Seating is atomic
  under the room lock: of concurrent host claimants for a hostless room exactly one is seated,
  and every loser is refused `host_present` with close 4004.
- **A host leaving is `peer.left` and then `host.detached`**, in that order, to the remaining
  peers. The room then enters a grace period of `grace_ms` (the value `host.detached` carries;
  30 s by default in the reference server) during which it is fully usable: guests keep syncing
  with each other, the open-document set survives, and a connection claiming `role: "host"` with
  the right token **reclaims** the room, announced to the others as `host.attached`.
- **A guest that joins during the grace period is a guest.** It is announced as `peer.joined` and
  nothing else: `host.attached` means a *host* reclaiming, so a connection admitted into a
  hostless room without claiming `host` produces no `host.attached` at all, and the room stays
  hostless. A guest join leaves the grace deadline unchanged, while every host departure arms
  a fresh grace period that supersedes the earlier timer. A client that reads `host.attached` as "the host is
  back" is right, and a client that reads `peer.joined` as "the host is back" is wrong (§9.1).
- **If the grace period expires with no host, the room is destroyed**: remaining peers receive
  `room.gone` and are then closed with code 4003. The room id is gone for good: a later join
  attempt is `room_unknown`, not a fresh room. Seating and reaping are serialised under the room
  lock: a reclaim hello seated before the deadline supersedes the armed timer, and a hello that
  arrives at or after the destruction is refused `room_unknown` with close 4001, never seated
  into a room that is gone.
- **A guest disconnecting produces `peer.left` and nothing else.**
- **Keepalive.** The server sends a WebSocket Ping every `ping_interval_ms`. A client answers it
  with a Pong (every mainstream WebSocket library does this for you). Protocol-level pings are not
  session messages and are never relayed. A connection that leaves its pings unanswered for a
  stated number of intervals **MAY** be closed as an ordinary drop (the room is told `peer.left`,
  the grace arms when the peer was the host in `selvage/1` and when it was the room's last connection
  in `selvage/2`, and no session close code is sent), because a host
  that has silently stopped answering would otherwise hold a room open forever: the reference
  waits two intervals, and its number is policy (§2.1). The same `keepalive` object carries the
  awareness window that clients run on (§8.2): the server's numbers are the session's, and a
  client that overrides them is choosing to disagree, not negotiating.

**The room in `selvage/2`.** This version's server knows a room's `id`, its `token`, the `keepalive`
it advertises, and which connections are in it with their `peer_id`, `display_name` and
`awareness_client_id` (§3), and it knows nothing else about it. The lifecycle that falls out of that
is short.

- **A room is minted by a connection whose URL names no room** (§5.1). It is seated by the mint like
  any other connection, and the token minted with the room is the permission to join it. The server
  records a peer and nothing about a host: the connection that minted the room holds the private
  half of the host keypair whose public half the invite's fragment carries, and that is the whole of
  what makes it the host (§5's minting passage).
- **Membership is the whole of what the server keeps.** Each seated connection is assigned a
  `peer_id`, which is opaque, unique in the room, and does not survive its connection, and no member
  of the roster carries a role (§6.1). A join is a `session.hello` answered with `room.created` or
  `room.joined`, and the peers already seated are told `peer.joined`; a connection that ends is
  announced as `peer.left`; a rename is `peer.renamed` (§6).
- **The server relays and authors nothing about a room but that** — §3's server passage is the whole
  of it, and §2.1's is what bounds it.
- **A room lives while it has connections and for `room_grace_ms` after its last one ends.** The
  timer arms when the room's **last** connection ends rather than when a host's does; a connection
  seated inside the window cancels it and the room goes on with the same id and the same token,
  which is what makes a blip survivable; and when the window passes with no connection seated the
  room is destroyed. The deadline can only be reached with no connection seated, so the destruction
  has no recipient and no frame announces it: `room.gone` is not sent, the id is gone for good, and
  a room's being gone is learned as `room_unknown` by the next connection that names it (§6, §11).
- **Nothing of a room survives it but that window.** A connection that rejoins inside the grace
  inherits the room's id and a token that still matches and nothing else: this server keeps no set,
  no listing, no hold and no state of any kind to hand back, and what a rejoining peer holds it
  announces again to its peers (§9.1).
- **The server reaps nothing else, and cannot.** A room whose peers stay connected is not reaped by
  anything the server knows, however long ago whoever hosted it left: the server cannot tell a room
  its host has abandoned from a room in use, which is the price of not knowing who the host is (§12).
  A client that ends its session once its own **host-away window** has passed is following §13.8's
  rule rather than the server's word, and it is a different clock from the grace above: the grace
  counts from the room's last connection ending and the host-away window from the host's own
  absence (§13.8).
- **What the server refuses at the door is §11's passage for this version**, and `host_present` is
  not in it: a server that does not know who the host is has nothing to refuse on, and close **4004**
  goes with the code.
- **The keepalive is unchanged**: a WebSocket Ping every `ping_interval_ms`, a peer that stops
  answering closable as an ordinary drop, and no ping relayed to a room (§2.1).

The room machine this gives a reader is four states and one table, and it is shorter than
§9.2's because the server holds nothing a row could carry:

| in state | the frame, or the clock | to | what goes out |
|---|---|---|---|
| absent | a connection whose URL names no room | live | `room.created` to the minting connection, carrying `token` |
| live | a connection joins with the right token | live | `room.joined` to the joiner, `peer.joined` to the peers already there |
| live | a connection ends and others remain | live | `peer.left` to the room |
| live | the room's **last** connection ends | grace | `peer.left` to the room it has just emptied; the grace timer arms |
| grace | a connection is seated with the right token | live | `room.joined` to the joiner, `peer.joined` to the room; the timer is cancelled |
| grace | the deadline passes with no connection seated | destroyed | — (no connection is seated to be told) |
| destroyed | any later join naming the room | destroyed | `session.error{room_unknown}`, close 4001 |

Seating and reaping are serialised under the room lock as in `selvage/1`, so a hello that arrives at
or after the destruction is refused `room_unknown` and never seated into a room that is gone. The
listing, the roles and the open paths are absent from every row because this server never held them:
a room's contents are the peers' state and no server state machine's business.

### 9.1 Reconnecting

**Two rules of this subsection are `selvage/1`'s, and each says so where it occurs**: the reclaim
by a connection claiming `role: "host"`, and inheriting a room's documents at join — the open-document
set and the reply's `documents` list are that version's (§1.1). The rest holds in both versions: a
reconnect is a `session.hello` on a new socket, it is a new peer, and nothing about a client
survives the socket. In `selvage/2` a rejoin is even plainer, because there is no state to hand
back: the server keeps the room's id and its token, and what a rejoining peer holds it announces
again to its peers (§9's room passage).

**The host's return, in `selvage/2`.** There is no resume frame, and none is needed: the host key is
the proof and a room state is the carrier. A host that returns re-hellos like any peer (§9), and the
connection that holds the private half of the host key resumes by publishing a state — sealed, signed
by that key, carrying an `issued` above the room's edition (§7.1). Every peer verifies it against the
key the invite's fragment names, so a connection that cannot sign one is not the host; and a relay
that replays an older one is refused `stale_issued`, which is the same `issued` rule that orders two
states ([`CANONICAL.md`](CANONICAL.md) §6.1). Nothing in this version asks a returning host for a
fresh value, a counter of its own or a second frame shape, and a client **MUST NOT** invent one: a
state above the room's edition is the whole of the resume. That state also carries the returning
connection's new **session** key in its own `host` entry, so a host has nothing to announce for
itself: the connection that signed the state is the one the entry commits (§7.1, §13.4).

What that costs is the host key's persistence, and the cost is the host's own: the keypair is minted
with the room and travels nowhere but the host's machine, so **a host that means to keep hosting
after a reload MUST persist its private half**, with its `issued` beside it (§7.1). A client that
does not persist it ends its own hosting when it reloads — it can be seated in the room, and it can
never publish a state a peer accepts again. A host on another device is in the same position: the key
belongs to a room and a machine, and there is no portable host identity that is not also an
identifier every guest can see.

A dropped connection takes everything that belonged to it: the `peer_id`, the claimed role
(`selvage/1`), this connection's document holds and its awareness state. Nothing about a client
survives a socket, so a reconnecting client is a new peer that has to say who it is again — with
one exception, which is the marks a `selvage/2` receiver keeps: the `issued` it has accepted and
the counter mark for each key it holds stay for as long as it holds the room's keys
([`CANONICAL.md`](CANONICAL.md) §6.1, §13.10), because they are what refuses a replayed state and
a replayed closing, and a client that reset them on a reconnect would apply a state it had already
replaced and obey a closing it had already passed. What a client must know and do:

- **Reconnect is `session.hello` again**, on a new socket, with the room and token the invite URL
  carries. There is no resume, no session id and no server-side state to hand back.
- **The host reclaims; a guest rejoins (`selvage/1`).** Within `grace_ms` of the host's
  disconnect, a connection claiming `role: "host"` **with the token** is seated, and the others are told `host.attached`
  and then `peer.joined`: a reclaiming host is a new peer as well as the new host, so a peer that
  treats every `peer.joined` as "a guest arrived" is wrong. Reclaiming is the same path an
  ordinary join takes, and it is only the same path if the reconnecting host kept the token: only
  `room.created` ever carried it (§5.1). A guest rejoins as a guest, and its join announces
  `peer.joined` alone (§9). Either way the reply is `room.joined` (only a mint produces
  `room.created`), whose `documents` list is the room's open-document set: a client does **not**
  have to re-open documents to inherit the room's set, and it **SHOULD** treat that list as the
  room's membership rather than as evidence that its content has arrived (§6.1).
- **What is lost is local.** The client's own `open_documents` set, its selection and its
  awareness state are gone with the socket and belong to the *new* connection from the moment it
  is seated: it **SHOULD** re-`doc.open` the documents it still holds open, which is what puts them
  back in the room's set when nobody else had them, and republish awareness. Both are peer rules in
  `selvage/2` rather than methods: there is no set for a hold to be added to, and a peer's holds are
  its own announcements.
- **The server replays nothing.** It holds no CRDT (§3), so it has no document to hand back and no
  history to replay: a reconnecting client gets the room's content from its peers through the
  ordinary SyncStep1/SyncStep2 exchange, exactly as a first-time joiner does (§7). Whether the
  client kept its own `Y.Doc` across the drop or starts from an empty one is a local decision and
  both work: a replica that kept its history asks for the delta it missed, and an empty one is
  brought up to date from scratch. A client **MUST NOT** conclude that the room is empty because
  it is: the server will not correct that, and its peers only send what a SyncStep1 asks for.
- **A reconnecting client SHOULD use a fresh awareness client id.** A library may remember a
  client id whose state was removed and drop the next publish from it (`yrs` 0.27.4 keeps that
  tombstone), and a client whose first republish is dropped looks, to every peer, like a
  participant with no cursor at all (§8.4).
- **A refusal means stop; a drop means try again.** `room_unknown`, `token_invalid`,
  `host_present` and `unsupported_version` refuse for a reason a retry cannot change — a room that
  is gone is gone for good, and in `selvage/1` the host did not come back inside the grace period;
  `host_present` is that version's code and a `selvage/2` server never sends it (§11) — so a client
  **MUST** stop and say why rather than reconnect into the same refusal. A fault in the reserved
  `x.` namespace (capacity, in this slice (§2.1, §10.1, §11)) is a stop as well: a handshake
  refused with an `x.*` code **MUST NOT** be re-helloed automatically, and a seated request
  refused with one **MUST NOT** be re-issued automatically; the attempt ends, or the request
  fails, and anything further needs its user's action. A room destroyed under a
  *seated* connection is not a refusal a client could have avoided: it learns of it as the
  `room.gone` event and close 4003 (§6, §11), which is an ending and not a retry. That case is
  `selvage/1`'s — a `selvage/2` destruction has no connection seated to be told (§9) — and it is the
  one place the two versions differ in what a client can be refused with. Every other loss
  of the socket is **recoverable**, and a client **SHOULD** re-hello with a bounded backoff rather
  than in a tight loop.
- **What `selvage/1` asks for is the shape of the policy, not its numbers.** A retry **MUST** be
  bounded, giving up **MUST** be a decision a caller can observe, and a refusal **MUST NOT** be
  retried. A client that has never had a session has nothing to recover, and a failure before the
  first connection is reported to whoever asked for one rather than retried behind its back. (The
  reference client's backoff parameters are [`NOTES.md`](NOTES.md) §A.2.) A client that knows the
  room's grace (`room_grace_ms` from `/meta` (§2), or in `selvage/1` the `grace_ms` a
  `host.detached` carried (§9)) **SHOULD** keep retrying at least until that window has passed,
  because the room survives its host's absence for exactly that long in `selvage/1` and its last
  connection's end for exactly that long in `selvage/2`, and a retry that gives up inside the window
  abandons a room
  that was still joinable. The grace is policy, like the retry's own numbers; what the wire fixes
  is that both exist and that a client uses the one it was told.
- **A client has to be able to tell its user which of the two happened, and the wire vocabulary
  still cannot express it.** A refusal reaches an adapter as `session.error` (§6) and then, if the
  server closed the connection, as a disconnection. A recoverable drop the client is retrying is
  now visible to an adapter as a local `reconnecting` event in both reference engines, so it no
  longer has to be inferred from the silence; what the *wire* cannot express is the retry itself,
  and one that gave up produces the same disconnection an orderly close produces. **Known gap** on
  the wire, and the fix there is an event, not a change to any existing frame.
- **Nothing survives the room.** After `room.gone` there is no room to rejoin, on any URL, with any
  token (§9), and in `selvage/2`, where no frame announces it, the same fact is the `room_unknown` a
  join naming it is refused with.

### 9.2 The state machines

**This subsection is `selvage/1`'s**, and it is the two machines of a server that holds a room's
documents and knows its host. The rows that need that state are that version's: the `doc.open`,
`doc.close` and `doc.grant` rows of the connection machine, its "a host claim while a host is
seated" and "the room is destroyed under it" rows, and — in the room machine — the host-departure,
reclaim and hostless-join rows together with its last one. Every other row of the connection machine
holds in both versions, because each of them is about a text frame's shape, the version gate, the
relay, the keepalive or a socket ending; the one clause among them that reads for one version only
is the "seated | the socket ends" row's `host.detached`, which is `selvage/1`'s — in `selvage/2` the
same transition arms the grace when the connection that ended was the room's last. `selvage/2`'s room
machine is the table in §9's room passage, and the peer-side machine that version needs is not a
server machine and is §13's (§1.1).

The two machines a reader has to build, derived from the transitions above and checked against the
corpus. Each row reads: in this state, this frame or this timer moves the machine here, and this is
what goes out. Where one frame carries more than one fault, the order §11 fixes decides which row
it reads as: the envelope and its `id` first, then `v`, then the method and its params, so a frame
is never judged by a fault later in that order than one it also carries.

**The connection.**

| in state | frame, or the clock | to | what goes out |
|---|---|---|---|
| (accepted) | a WebSocket upgrade on `/session` | unseated | — |
| (accepted) | any other request line | closed | the plain-HTTP answer: `/meta`'s body or its headers for `HEAD`, the served page when one is configured, `405` for another method, or `404` |
| (accepted) | no complete request head within the server's bound (§2.1) | closed | — |
| unseated | `session.hello`, compatible, room and token valid or no room named | seated | `room.created` (mint) or `room.joined` (join), to the sender; `peer.joined` to the room, unless it minted |
| unseated | a first text frame that is not `session.hello` | closed | `session.error{hello_required}`, close 4000 |
| unseated | a first frame that is binary, unparsable, over the server's envelope bound (§2.1), or an envelope with no `id` | closed | `session.error{bad_message}`, close 4000 |
| unseated | `session.hello` whose params do not parse, including no `display_name` | closed | `session.error{bad_message}`, close 4000 |
| unseated | `session.hello` whose `display_name` is blank or carries a control character (§5) | closed | `session.error{bad_params}`, close 4000 |
| unseated | `session.hello` whose `display_name` is over 32 UTF-16 code units | closed | `session.error{bad_params}`, close 4000 |
| unseated | `session.hello` with an incompatible `v` | closed | `session.error{unsupported_version}`, close 4005 |
| unseated | a join naming a room that does not exist | closed | `session.error{room_unknown}`, close 4001 |
| unseated | a join whose token is absent or wrong | closed | `session.error{token_invalid}`, close 4002 |
| unseated | a host claim while a host is seated | closed | `session.error{host_present}`, close 4004 |
| unseated | a mint or a join past a bound the server sets (§2.1) | closed | the refusal carrying that implementation's own `x.` code: `session.error`, then close 4000 |
| unseated | no frame within the server's hello timeout | closed | `session.error{hello_required}`, close 4000 |
| seated | `session.hello` | seated | the error response `already_seated` |
| seated | `doc.open` / `doc.close` with a non-blank `path` | seated | the result, then `doc.opened`/`doc.closed` to the room |
| seated | `doc.open` / `doc.close` with a blank, over-long or control-carrying `path`, or params that do not parse | seated | the error response `bad_params` |
| seated | `doc.grant` with a `paths` array of non-blank, control-free strings | seated | the result, then `doc.granted` to the room |
| seated | `doc.grant` from a connection the server does not hold as the room's host, or with malformed `paths` | seated | the error response `bad_params` |
| seated | `session.rename` with a non-blank `display_name` within the bound | seated | `{ "result": {} }` to the caller, then `peer.renamed` to the room, the caller included |
| seated | `session.rename` with params that do not parse, or a blank, over-long or control-carrying `display_name` | seated | the error response `bad_params` |
| seated | `doc.open` past a bound the server sets on the room's state (§2.1) | seated | the error response carrying that implementation's own `x.` code; the connection stays open |
| seated | any other method | seated | the error response `unknown_method` |
| seated | a text frame that is not an envelope, or has no `id` | seated | `session.error{bad_message}` |
| seated | a text frame longer than the server's envelope bound (§2.1) | seated | `session.error{bad_message}`, naming the bound; the connection stays open |
| seated | a request whose `v` is incompatible | closed | the error response `unsupported_version` for that request, then close 4005 |
| seated | a binary frame | seated | relayed to the rest of the room, byte for byte |
| seated | the room is destroyed under it (§9) | closed | `room.gone`, then close 4003 |
| seated | the socket ends, either side | closed | `peer.left` to the room, and `host.detached` after it if this was the host |
| seated | the ping interval | seated | a WebSocket Ping |

**The room.**

| in state | frame, or the clock | to | what goes out |
|---|---|---|---|
| absent | a connection whose URL names no room | hosted | `room.created` to the minting connection, carrying `token` |
| hosted | a connection joins with the right token | hosted | `room.joined` to the joiner, `peer.joined` to the peers already there |
| hosted | the host's connection ends | hostless | `peer.left`, then `host.detached` with `grace_ms`, to the remaining peers |
| hosted | a guest's connection ends | hosted | `peer.left` |
| hostless | a connection claims `role: "host"` with the right token | hosted | `host.attached`, then `peer.joined`, to the remaining peers |
| hostless | a guest joins with the right token | hostless | `peer.joined`; the grace deadline is unchanged |
| hostless | the grace deadline passes with no host seated | destroyed | `room.gone` to the remaining peers, then close 4003 |
| destroyed | any later join naming the room | destroyed | `session.error{room_unknown}`, close 4001 |

Across every one of those transitions the room's open-document set survives: it is lost only with
the room, at `destroyed`. A hold is released by the connection holding it, by `doc.close` or by
the connection ending, and the path outlives both (§1.2, §5). A room is still joinable throughout
its grace period whether or not any peer is left in it: only the deadline removes it, and
`vectors/011` closes the host and joins a guest before that deadline.

## 10. Version and capability negotiation

- The wire version is `selvage/1` and appears in every text frame as `v`. It **MUST NOT** be
  omitted: a frame with no `v` is not a session envelope at all, and is answered with
  `bad_message` like any other frame the receiver cannot read. An incompatible value is refused at
  the handshake with `unsupported_version` (close code 4005). Version is also checked on every
  later request; an incompatible one is answered with an error and the connection is closed
  (§11).
- **Compatibility rule**: same major, and while at `0.x` also the same minor. This document
  defines `selvage/1`, so the rule in force is *same major* alone: every `selvage/1.x` is
  accepted, including `selvage/1.9` and the bare `selvage/1`, whose minor defaults to `0`. Same-major
  acceptance is not a promise that binary frames interoperate across minors: a minor **MUST NOT**
  change the binary encoding without a handling rule both sides share, since binary frames carry
  no version to check. The
  grammar is the one [`schema/negotiation.json`](schema/) encodes as `wireVersion`: `selvage/`
  then a major, then optionally a minor, each a plain decimal with no leading zero and nothing
  after it, so `selvage/2` and `selvage/x` are refused for their major, and `selvage/01`,
  `selvage/1.09`, `selvage/1.9.3` and `selvage/1.` are refused because they are not that grammar
  at all. A receiver **MUST** refuse a version outside the grammar, and outside the grammar is
  still `unsupported_version`, never `bad_message`: at the handshake a refusal closed with 4005,
  on a seated connection the error response to the offending request followed by close 4005
  (§9.2, §11). The minor becomes decisive
  only when the major reaches `0`. A conforming producer writes `selvage/1`, never `selvage/1.0`
  (`CANONICAL.md` §2.5).
- Capabilities are advertised additively by the server in `room.created`/`room.joined` and in
  `/meta`, and optionally by the client in `session.hello`. **Unknown capabilities and unknown
  fields are ignored by both sides**: a receiver **MUST** ignore a capability name it does not
  know. There is no failure mode for an unknown capability, and no way for a client to require one
  ([`NOTES.md`](NOTES.md) §B.9). A `capabilities` array is a set: no order is promised for it, in
  any of the three places it appears.

**The capability names this document defines.** A server advertises all four; a client advertises
the ones it speaks. Name, what it says about the peer that advertises it, and what a peer may
infer:

| name | meaning | what a peer may infer |
|---|---|---|
| `y-protocols/1` | the peer carries y-protocols document sync (§7) | it can decode the binary frames of §7 |
| `awareness` | the peer carries y-protocols awareness (§8) | it publishes presence; an awareness query it receives it **MAY** ignore, or answer once for the frame (§8.3) |
| `open-document-set` | the server keeps the room's open-document set and announces every change to it (§5) | `doc.open` and `doc.close` are available, and `doc.opened`/`doc.closed` will arrive |
| `host-reclaim` | the server lets a host that returns inside the grace period reclaim the room (§9) | a reclaim is possible, and `host.attached` will announce one |

An implementation **MAY** advertise names beyond these. Nothing is gated by any of them: no
capability changes what a peer may send, and a peer **MUST NOT** infer a failure from a capability
it does not recognise. A capability is a statement of intent and not a proof: nothing on the wire
makes a peer honour it (§12).

**The version gate in `selvage/2`.** The wire version moves to `selvage/2`, on the same grammar and
the same compatibility rule, and the gate carries all of the weight this time because **there is no
downgrade path**: a `selvage/2` peer and a `selvage/1` peer cannot share a room, since a
`selvage/1` peer can neither read a sealed frame nor produce one, and a capability would be a
silent downgrade a compromised server could take.

- **A `selvage/2` server against a `selvage/1` client.** Two stops, and the first is the client's.
  A client that reads a reachable `/meta` finds no version it can speak and refuses locally with
  `unsupported_version` before it opens a socket (§2). One that connects anyway — because it could
  not read `/meta`, or because it was written before `/meta` existed — sends `v: "selvage/1"`, and
  the server refuses it on that member: `session.error{unsupported_version}`, then close **4005**,
  on a connection that is never seated. The `role` such a client sends is not why it is refused:
  `v` is judged before the method and its params (§11), and `role` is not this version's member.
- **A `selvage/1` server against a `selvage/2` client.** The same two stops in the other
direction. `/meta` names `selvage/1`, the client refuses locally, and a client that connects anyway
is refused `unsupported_version` with close **4005** by the rule §10 already states: `selvage/2` is
inside the grammar and outside the compatibility rule, so it is refused for its major, before
seating. **Nothing is added to `selvage/1` for this direction** — the gate it already has is the
one that does this — and the sentence a client says at the local stop is §2's.
- **Nothing is refused before the hello.** The version is a member of the frame and not of the URL,
so a server learns a client's version when it sends its first text frame and not before, and
`/meta` is the only check that precedes a socket: it is the client's, and it is a refusal with no
wire on it at all. There is no pre-hello negotiation to look for and no version in the connection
URL (§5.1).
- **A client whose version is `selvage/2` MUST NOT fall back.** It **MUST NOT** connect to a server
whose reachable `/meta` names no version at major 2 it can speak, whatever else the body says,
and a `session.hello` answered with `unsupported_version` **MUST NOT** be re-helloed as an earlier
version. That is the whole of the no-downgrade rule — that version is the one §2 gives a minting
client, the one an invite names, or the one the client pinned — and it is a client rule because
the server is the party it defends against; §9.1's rule that a refusal is terminal already names
`unsupported_version`, and this is the version that needs it.

Once a connection is seated the version is checked on every later request exactly as above,
`unsupported_version` answered as an error response and the connection closed **4005** (§11). The
close vocabulary is `selvage/1`'s without 4004: a `selvage/2` server does not produce
`host_present`, because it does not know who the host is.

### 10.1 Reserved names

A namespace is reserved for implementation-private and hosted-only messages, so that adding one
never has to mean splitting the protocol into a free one and a real one.

- **Method names, event names, capability names and error codes beginning with `x.`** are
  reserved for exactly that. None is defined by this document: no `x.` method, no `x.` event, no
  `x.` capability and no `x.` error code is part of `selvage/1`.
- A peer that does not know an `x.` method answers it like any other unknown method, with
  `unknown_method`; an `x.` event is ignored, like an unknown field; an `x.` capability is
  advertised and ignored like any other unknown capability; an `x.` error code is an
  implementation's own fault, carried where any code is (§11) and never given a `selvage/1`
  meaning. Nothing is negotiated by presence alone.
- An implementation that defines one documents it for its own users. A client **MUST NOT** assume
  any `x.` name exists, and **MUST** keep working when one is refused.
- **Allocation.** `x.` is flat, so two implementations that both define `x.editor-state` collide
  silently: each treats the other's as noise, and neither can tell. An implementation that defines
  an `x.` name **SHOULD** namespace it further with a vendor or project segment
  (`x.<vendor>.<name>`, `x.editor-state.vscode`), so that two extensions cannot mean different
  things by one name. This
  document's own names never begin with `x.`.
- **An `x.` name can only travel if the implementation lets it.** The wire is open: the server
  answers any method it does not implement with `unknown_method` (§5), so an extension method is a
  method like any other, and an implementation that defines one has to expose a way to *send* a
  method whose params it does not interpret. An `x.` *event* needs no such thing, because events
  travel to a client that has already promised to ignore the ones it does not know.
  ([`NOTES.md`](NOTES.md) §A.4 records what the reference clients can express.)

## 11. Errors

Codes carried in `error.code` on a response and in `session.error.params.code` on an event. The
same vocabulary is used in both, and the state a connection is in decides what a code does:

| code | meaning | unseated | seated |
|---|---|---|---|
| `unknown_method` | no such method | — (a non-hello first method is `hello_required`) | error response; the connection stays open |
| `bad_message` | not a session envelope / no `id` / duplicate member name / undecodable | refusal: `session.error`, then close **4000** | `session.error`; the connection stays open |
| `bad_params` | method params missing or malformed | refusal for a blank, over-long or control-carrying `display_name`: `session.error`, then close **4000** | error response; the connection stays open |
| `hello_required` | the first text frame was not `session.hello`, or none arrived in time | refusal: `session.error`, then close **4000** | — |
| `unsupported_version` | version refused | refusal: `session.error`, then close **4005** | the error response to the offending request, then close **4005** |
| `room_unknown` | no such room (never minted, or destroyed) | refusal: `session.error`, then close **4001** | — |
| `token_invalid` | room present, token absent or wrong | refusal: `session.error`, then close **4002** | — |
| `room_gone` | the room was destroyed under a seated connection. The frame that announces it is the `room.gone` **event** (§6), and a later join is `room_unknown` | — | — |
| `host_present` | a host is already connected (`selvage/1`: a `selvage/2` server does not know who the host is, and never sends this) | refusal: `session.error`, then close **4004** | — |
| `already_seated` | `session.hello` sent twice | — | error response; the connection stays open |
| `doc_not_open` | reserved; not produced by this slice, and there is no `doc.*` method in `selvage/2` for it to be about | — | — |

The **close** vocabulary is separate, and `selvage/1`'s part of it lives in the private-use
range: 4000 `protocol_error`, 4001 `room_unknown`, 4002 `token_invalid`, 4003 `room_gone`, 4004
`host_present`, 4005 `unsupported_version`. A code with a matching close ends the connection; a
code without one does not. That vocabulary is not the whole of what a peer can receive: a close
outside it carries no session meaning and a client **MUST NOT** read one into it, because a
capacity fault that is not about the session protocol is IANA's to name — the reference server
closes **1013** (try again later) at its connection cap and when a connection has spent its
inbound budget (§2.1, §12) — and 1013 is not in the private-use range at all.

**The vocabulary in `selvage/2`.** A server of that version carries nine of the eleven codes above,
and the two it does not are the ones whose fault its shape cannot produce. `host_present` is a
refusal a server makes when a host is already connected, and this one does not know who the host is;
`doc_not_open` is reserved for a `doc.*` method this version does not have, which is why no version
produces it. What is left is `unknown_method`, `bad_message`, `bad_params`, `hello_required`,
`unsupported_version`, `room_unknown`, `token_invalid`, `room_gone` and `already_seated`, plus any
`x.` code an implementation defines for itself (§10.1), and the closes are 4000, 4001, 4002, 4003
and 4005.

Everything else this section states holds in both versions exactly as written — the fault order, the
refusal/error-response split, the truncation of a close reason, and that a code with no matching
close does not end the connection. The split is what `room_unknown` and `token_invalid` are: a
`selvage/2` server refuses a join naming a room that is gone or a token that does not match, before
seating, and it has no reason to refuse anything a peer sends afterwards except a malformed request
— a frame it cannot read is a binary frame it relays rather than a fault (§3), and the bounds of
§2.1 are the other thing it can refuse past. What the version needs a reader to carry away is that
`room_unknown` is the ending: a room is gone for good, and the id is the whole of what a later
connection can be told about it (§9).

**A fault before seating is announced as a refusal**: a `session.error` event and then a close
with the matching code, so a client that does not read close frames still learns why. **A fault on
a seated connection is announced in the frame's own vocabulary**: an error response for a request,
and the `room.gone` event for the one fault that is not about a request. The version fault is the
case where both vocabularies meet: see its row above.

A close reason is WebSocket control-frame payload, so it is truncated to 123 bytes (RFC 6455
allows 125, two of which the code takes) when the message it would carry is longer: the reason is
a convenience, while the `session.error` before it carries the whole message and has no length
limit.

Before the handshake completes, **every fault closes the connection**: the server is not seated
with a peer yet and has nothing to keep open. A first frame that is not a text frame (a binary
frame, say) is reported as `bad_message` and the connection closes with 4000, not
`hello_required`: what was wrong is the *shape* of the frame, and the client is told the same code
it would get for an unparsable envelope.

**When one frame carries more than one fault, one order decides which is answered.** A receiver
**MUST** judge a frame in the order it is read: the envelope must parse and carry an `id`
(`bad_message`), then its `v` must be compatible (`unsupported_version`), then the method must
resolve (`hello_required` for a first frame that is not `session.hello`, `unknown_method`,
`already_seated`) and only then are its `params` read, with the code each method's params rule
gives (§5). The first fault in that order is the only one answered, and before seating it is the
one whose close code is used. So
`{"v":"selvage/2","id":5,"method":"cursor.teleport"}` on a seated connection is
`unsupported_version` and close **4005**, never `unknown_method` and a live connection; a first
frame with neither `id` nor a compatible `v` is `bad_message` and close **4000**, never
`hello_required` or `unsupported_version`; and a frame that repeats a member name is `bad_message`
before any of them. Two implementations that dispatch in a different order answer differently (on
this wire one answer closes the connection where the other keeps it), so the order above is the one
`selvage/1` fixes (`vectors/028` pins a frame of each pairing).

The codes above are `selvage/1`'s. An implementation **MUST NOT** reuse one with a different
meaning, and an implementation that needs a code of its own **SHOULD** name it in the reserved
`x.` namespace (§10.1) rather than invent a bare name that a later version may want. Close codes
are the same story: 4000–4005 are fixed here, and the rest of the private-use range (4000–4999) is
unregistered: two implementations that both pick 4006 have to be assumed to mean different
things.

## 12. Security considerations

Every deployment in `selvage/1` inherits these properties. They are not aspirations about a future
version: they are what the wire in §2–§11 does.

**This section is `selvage/1`'s, and it holds two kinds of sentence.** A duty on a *deployment* that
no wire shape supplies — terminate TLS in front of the server, do not log request URLs, bound
connections, peers and rooms — holds in both versions, and a `selvage/2` deployment is held to it. A
*property of the wire* below is `selvage/1`'s, and three of them read differently in `selvage/2`:

- **the token is not the whole permission.** It is the permission to **join**: no frame the server
  can read carries a document, a cursor, a file name or a role (§3, §7.1);
- **a leaked URL is a leaked room and its keys.** The fragment is what removes the server operator
  and the network path from the set that can read a room (§5.1), and it does not remove the link's
  holder: the fragment is in the link, and in the address bar, the history and whatever carried it;
- **the host role is proven, not claimed.** The host is whoever holds the private half of the key
  the invite's fragment names, a role is the host's signed statement, and no connection is seated as
  anything (§1.2, §7.1).

What the server of that version can and cannot do is stated where its negative duties are (§3), and
the peer-side rules that stand where the server enforces nothing are §13.

**The token is the whole permission.** A room's token is minted with the room, carried in the
invite URL, and never echoed after `room.created`. Any peer that presents it is seated, whatever
its display name, and there is no per-join approval (`message_type = 2`, auth, is unused, §8.3).
A holder may read every document in the room, write to any of them, and, while the room is
between hosts, claim the host role and end the room by leaving (§9). Holding the token is
therefore equivalent to holding the working copy the room is editing; a deployment **MUST** treat
the invite URL as a secret with the same sensitivity as the code it opens.

**The invite URL leaks.** Because the token travels in the query string, it appears in browser
history, referrers, proxy access logs, terminal scrollback and any chat client that unfurls the
link. Nothing authenticates a connection beyond the token, so a leaked URL is a leaked room. A
deployment **MUST NOT** log request URLs, and a client **SHOULD NOT** put an invite URL anywhere
it would not put the code. (Whether the token should move out of the URL is open; see
[`NOTES.md`](NOTES.md) §B.1.)

**There is no transport security in this slice.** One `ws://` endpoint and one plain-HTTP `/meta`
(§2). The server is expected to run on loopback or behind a terminator, and a deployment reachable
by anyone else **MUST** terminate TLS in front of it: the protocol cannot detect a downgrade, and
no member of a session can tell whether its peer's transport is protected.

**The host role is claimed, not proven.** Any holder of the token may send `role: "host"` and,
when the room is hostless, become the host: keeping the room alive, or ending it by leaving. Since
the same token already grants read access to the whole working copy, this grants nothing new
*today*: it stops being harmless the moment the token is shared more widely than the host's
devices. ([`NOTES.md`](NOTES.md) §B.2.)

**The denial-of-service posture is a v1 posture.** §2.1's capacity rows are the reference
server's own policy: a 1024-connection cap counted past the request head, a 1024-room cap, 128
peers to a room, 1024 paths in a room's open-document set, no idle reaper (only the ping bound
above, which closes a connection that has stopped answering), no per-source rate limit, a
per-connection outbound queue of 32 frames and 32 MiB past which the slow peer is disconnected,
and a per-connection inbound budget of 2 MiB a second with a 64 MiB burst, past which the peer is
told `x.rate_limited` and closed 1013. A frame over the transport's bound ends a connection with
nothing on the wire to say why; a text envelope over the server's own envelope bound is refused on
the frame's own vocabulary instead, because a whole frame is something a session can answer
(§2.1). What a peer *can* rely on is §2.1's bound: a conforming server refuses deterministically
past a finite configured limit on a connection's inbound bytes and on a room's stored state. A
room's peers can still be flooded at whatever rate one connection's budget allows, so a deployment
on the public internet **MUST** put a terminator or a proxy in front that supplies a connection
cap, an idle deadline and a rate limit.

**Paths are not validated.** `doc.open` and `doc.close` carry an opaque, workspace-relative path
that the server does not resolve, normalise or check against anything (§5), and a grant's `paths`
are exactly as unvalidated: the server carries the listing, relays it and never resolves one. A
listed path is a name the host's working copy held when it enumerated, and that is all a receiver
may read into it: the server neither resolves the name nor verifies that it names a file, that it
exists, or that it lies inside the host's working copy. It may since have been deleted, may be
unreadable, may name a directory even though a listing carries files, or may name something the
host declines to seed, so a client **MUST** treat a listed path as a candidate and not as a
promise of a readable file or of content. `..`, an absolute path and a name that escapes the
working copy are all names the server will hold and announce if a host sends them. This is harmless only while nothing reads the host's
filesystem: the moment an adapter turns a path from the wire into a file read, the protocol
supplies no confinement: a folder grant, exclude globs and path clamping are all outside this
slice. The grant is a list of names *the host chose*, which is a statement about the host and not
a check by the server; a peer's request for a name, and any read the host performs to serve it, is
where confinement has to happen. An implementation that reads the host's filesystem **MUST**
confine the path itself, and the path rules have to be settled before file access exists
([`NOTES.md`](NOTES.md) §B.8).

**Capabilities are not security.** `capabilities` is advertisement with no failure mode (§10): a
peer ignores what it does not know, and nothing on the wire proves that a peer can do what it
advertises. A capability name **MUST NOT** be used to decide whether a peer is safe to talk to.

## 13. Client behaviour

**This section is `selvage/2`'s**, and it is that version's peer side: what a client does with the
frames it receives, where the server no longer decides anything. A conforming `selvage/2` client
implements it, and it is where the version's security actually sits — the server holds nothing a
room's facts could be read from (§3), so what makes a frame mean what it says is a rule both ends of
a connection keep. It is normative, and it fixes no bytes: every rule below is about what a client
does with bytes §5.1, §7.1 and [`CANONICAL.md`](CANONICAL.md) §6.1 already fix, and the reasons
it reports a refused frame with are §6.1's own vocabulary.

### 13.1 The order of operations, at a join

A client that has read an invite (§5.1) and settled on a version (§2, §10) works through this order,
and the order is part of what follows:

1. **Read the room, the token and the two keys, and strip the fragment.** Both values are required,
   and their absence is a local refusal before a socket is opened (§5.1). Neither ever reaches the
   server in any form.
2. **Mint a session keypair in memory** for this connection and derive the frame key from the room
   key and the room id ([`CANONICAL.md`](CANONICAL.md) §6.1). The session keypair is not persisted:
   it belongs to the connection it was minted for, and a reconnect mints a new one.
3. **Open the socket, send `session.hello`, and read `room.created` or `room.joined`.** Its
   `self.peer_id` is the seat this connection is shown under. It is not what commits the session
   key and nothing is attributed by it: a frame is attributed to the key that verified, and the
   `peer_id` is a name the room shows and that the host may label a key with (§7.1, §13.4).
4. **Announce the session key, and publish nothing else** until a state that commits it has
   verified. The first binary frame a client sends is its **session-key announcement** — a
   `kind = 4` frame signed by the very key it names (§7.1, [`CANONICAL.md`](CANONICAL.md) §6.1) — and
   it is the one binary frame this step permits, because it is the frame that makes every other one
   possible: the host cannot commit a key it has not been told. Until a state commits the key, a
   client **MUST NOT** send any other binary frame — not content, not awareness and no holds
   message — because it does not yet know its own role and no peer can attribute what it sends
   (§6.1). The one other frame it may send before then is a room state signed by the host key, which
   only the host can produce (§7.1); a host's own state commits its own connection's key, so a host
   has nothing it must announce. A client **MUST** announce again when it applies a verified state
   that does not commit its key, and **MUST** re-announce on the session's own renewal clock
   `awareness_renew_ms` (§8.2) for as long as no state it has applied commits it, so that an
   announcement the relay dropped (§13.2) is recovered without the peer's whole session depending on
   one frame. The clock is the recovery's bound and not decoration: a client cannot tell a dropped
   announcement from a slow host, and a rule whose only trigger is applying a state cannot start when
   the host never answers. Each re-announcement is the same frame at the next counter under the same
   key ([`CANONICAL.md`](CANONICAL.md) §6.1), and a client whose key a state commits stops
   re-announcing there. §7.1 states what a host owes an announcement whose key its state already
   commits — the state its sender has not applied, re-sent — so the same timer recovers that drop
   too, within a window: a client cannot tell a dropped announcement from a dropped state, and it
   does not have to.
5. **Verify each room state against the host key the fragment names, and apply it if it verifies
   and its `issued` is above the mark this receiver holds** ([`CANONICAL.md`](CANONICAL.md) §6.1).
   Arrival is not the order: §13.3 orders states by `issued`, so a state that verifies and arrives
   after one at or above its edition is refused `stale_issued` and the receiver keeps what it holds.
   Until a state is applied, §13.3's waiting rules hold.
6. **Re-run the sync handshake, once.** Everything the relay handed this client before that point
   was refused (§13.2), so a client **MUST** send a SyncStep1 (§7) once it applies a verified state
   that commits its own session key, and apply the replies. Before that, a SyncStep1 of its own is a
   frame every conforming peer refuses `uncommitted_key`, so it is a frame nobody answers. A client
   that skips this step keeps what it dropped, which is divergence with nothing to observe it.
7. **Then for the rest of the session**: verify before applying (§13.2), attribute every frame
   (§13.4), apply only what the sender's role permits (§13.5), and re-send the state the room holds
   when a peer is seated (§7.1).

A host's order is the same with one difference: it **MUST** publish a state at mint, on every
`peer.joined` it receives and on every session-key announcement it accepts (§7.1), so its own state
may precede any state it verifies. It is held to the rest of the list as any peer is.

### 13.2 Verify before apply

- **A binary frame is one sealed frame, and nothing in it is used before it verifies.**
  [`CANONICAL.md`](CANONICAL.md) §6.1 fixes the checks and their order, and a client **MUST** read
  the first one that refuses the frame as the reason it reports.
- **A frame that fails is dropped.** Nothing in it is applied, no state changes, the session goes
  on, and nothing is sent in answer. This is the one place this document permits a receiver to drop
  a frame it received, and it is what convergence means in this version: **convergence over the
  frames a client applied** (§7's handshake, unchanged). §4.2's rule that a request is never
  silently dropped is about requests; a dropped relayed frame is not a session fault and no §11 code
  is involved.
- **Every refusal is reported locally, with the reason §6.1 names.** A client **MUST** be able to
  say which frame it dropped and why. The relay may drop a frame too
  ([`CANONICAL.md`](CANONICAL.md) §6.1), so without the report a client that refuses everything is
  indistinguishable from a client on a lossy link. The report is local — a status, a log line, a
  count — and **MUST NOT** be sent, because §11's vocabulary is for faults of the session and this
  is not one.
- **A client MUST NOT end the session for a refused frame.** A refusal is a statement about one
  frame; ending on it hands any relay the power to end a session by corrupting one byte.

### 13.3 The room state, as a receiver

The state replaces what a receiver held, and it is ordered by its own `issued`.

- **Apply it wholesale.** The listing is the room's working tree, and a shorter listing is a smaller
  one and not a partial update: a client **MUST NOT** merge two states' listings. Applying a state
  does not retract content — a path that leaves the listing leaves what a client *offers*, and the
  `Y.Text` for it stays in the session document exactly as a `selvage/1` `doc.close` never deleted
  content (§7) — so a client that still holds a document the listing no longer names is not in
  error; it may not offer the path to anyone.
- **A state is applied only if its `issued` is strictly above the mark the receiver holds.** One at
  or below it is refused `stale_issued` and no part of it is applied (§7.1,
  [`CANONICAL.md`](CANONICAL.md) §6.1).
- **`peers` replaces the receiver's keys and roles for the whole room.** A key the state names has
  that role from the moment the state is applied; a key it does not name is uncommitted and has no
  role, so its frames are refused `uncommitted_key` (§13.4) and nothing is attributed to it. A state
  that drops a key revokes what an earlier one granted, and one that adds a key grants. What a state
  does not do is seat anybody or unseat anybody: the server's roster is the authority on which
  connections are seated (§6.1, §9), so a state entry is a key and a role and not a participant, and
  the `peer_id` it carries is the host's belief about a seat rather than a fact about one (§13.4). A
  client **MUST NOT** show an entry as a person the roster does not account for.
- **The state is the only source of the keys a receiver keeps.** An announcement is read for the
  host's sake (§7.1): a key no applied state commits has no role, none of its frames is applied, and
  the mark it leaves behind ([`CANONICAL.md`](CANONICAL.md) §6.1) guards the announcement alone. A
  state the receiver applies is what decides which keys it holds, and because a host commits at most
  one key per seat (§7.1) the set it hands over is at most one key per seat. **A receiver MAY cap how
  many keys it holds and how many marks**, keeping what its state commits and the most recently
  announced of the rest, for §13.7's reason applied to a key set rather than a hold set: a peer that
  holds the room key can announce keys without bound, and a receiver that keeps every one of them
  carries that state for the life of the room.
- **The listing's paths are held to §5's rule.** A host **MUST NOT** write a blank path or one
  carrying a control character into a listing, and a receiver **MUST** refuse that path — not the
  frame and not the state, whose remaining members are as authentic as they would have been. To
  refuse a path is to drop it from what the client offers: it is not shown, not offered to a peer
  and never written out as a name, while the rest of the listing and the state's roles are applied.
  The server enforced that rule in `selvage/1` and does not here, so it is re-homed at both ends, and
  the receiver is the end that renders a name.
- **A listing's paths are bounded at the receiver too, and the bound the server used to hold is
  re-homed with it.** In `selvage/1` a path over 4096 bytes was refused `bad_params` as the
  server's own policy, and the same three bounds — 4096 bytes to a path, 100 000 paths, 4 MiB of
  path bytes (§2.1's reference numbers) — covered a listing too large for it to store; this
  version's server holds no listing to refuse. A host **MUST NOT** write a path longer than **4096
  bytes** into a listing and **SHOULD** bound its own enumeration, because a listing is one sealed
  frame: one that does not fit the room's frames never arrives at all, and one that does arrive is
  held whole by every peer (§2.1, §7.1). A receiver **MUST** refuse a path over that bound by
  dropping the path — not the frame and not the state, exactly as the rule above drops a blank or
  control-carrying one — and **MAY** cap how many paths it will hold and how many bytes of paths,
  keeping the first the listing names in the order [`CANONICAL.md`](CANONICAL.md) §2.7 fixes and
  dropping the rest, so that two receivers with the same cap read one state the same way. A receiver
  that drops a path for either reason applies the rest of the listing and the state's roles.
- **Two publications at one edition.** Two connections of one host can publish states with the same
  `issued` and different contents, and nothing a receiver holds says which is the later: both
  verify, both name one edition, and `issued` is the only order this version has. What a receiver
  does is fixed by the rule above rather than chosen between the two — the first state it accepts at
  an edition is the one it holds, and a second at that edition is refused `stale_issued`. It keeps
  that state, keeps applying content under it, and reports the refusal like any other. What that
  leaves, said rather than implied: **two receivers can hold different listings at one edition**, and
  neither the wire nor a conforming peer can tell which of the two is the host's latest. The room
  recovers when a state above the edition arrives, which §7.1 has the host publish on its next
  change of `listing` or `peers`, on the next `peer.joined`, and on the next key it commits that its
  `peers` did not already carry, and a
  client **SHOULD** report an equal-`issued` refusal distinctly, as a conflict rather than as an
  ordinary stale state, so that the divergence is visible where it exists. The reason stays
  `stale_issued`, because that is the step that refuses it: the distinct report is a local
  **annotation beside** the named reason — a note, a count, a second line — and not a reason of its
  own, since the vocabulary is closed and a state at the mark and a state below it are one refusal
  (§13.2, [`CANONICAL.md`](CANONICAL.md) §6.1). A client **MUST NOT** clear its listing, drop
  content or end the session for it. §7.1's producer rules — one host session at a time, and the
  `issued` persisted beside the
  host key — are what keep the case from arising, and the residual is that a host which breaks them
  cannot be corrected by anything on the wire, because both of its publications are authentic.
- **A refusal does not reach its publisher.** The relay sends a frame to the room's *other*
  connections (§7.1) and a refusal is the receiver's local report (§13.2), so a host whose state no
  peer applies is not told; it learns nothing from the wire at all. That is the reason §7.1's
  persistence rules are obligations rather than conveniences.

**Before any state has verified.** A client that is seated and holds no verified state:

- **MUST NOT** apply content or awareness, and **MUST NOT** publish either — the session-key
  announcement is the one frame it sends (§13.1's step 4);
- **MUST NOT** end the session for the absence of a state before **the no-state window** has
  passed. A room can be entered while the host is away, in which case no state arrives until one
  does (§6.1), and `awareness_expire_ms` is the length of that wait: the window runs from the moment
  the client is seated, on the session's own clock, and is §13.8's host-away window read from
  `room.created`/`room.joined` (or `/meta`) rather than a number of the client's own. A client seated
  a whole window with no state applied **MUST** end its session and say why, because there is nothing
  else for it to wait on: §13.8's host-away clock is armed by a state whose `host` entry labels an
  absent seat and it holds no state to arm it with, its own key is committed by no state so it can
  publish nothing, and its seat is the only thing keeping the room alive (§9). Ending is what makes
  the room's death and the `room_unknown` path §13.10 names reachable at all: while this client
  stays seated the room is never destroyed, and whoever names the id next is seated in a room nobody
  is in. A client **MAY** name the room again instead of ending — a fresh `session.hello` on a new
  socket, §9.1 — and the answer tells it which room it was in: `room_unknown` if the room is gone,
  and a seat in the room it left otherwise;
- **MUST NOT** present the room as having no files. It does not hold the room's listing, and an
  empty tree is a claim about the room it cannot make. It presents the room as **waiting for the
  host's state** — the roster from `room.joined` is known and may be shown, and the listing and the
  roles are not — and it **SHOULD** say so where it shows the room, so that a person does not read an
  empty workspace as an empty room.

### 13.4 Attribution, and what the server's roster is worth

**A frame's sender is the key that verified, and a role is the host's statement about that key.**

- **The `key_id` is an index, not an identity** ([`CANONICAL.md`](CANONICAL.md) §6.1). A receiver
  resolves it by verifying the frame against the keys it holds: for `kind = 0` and `kind = 3` the
  keys the applied room state commits, for `kind = 1` and `kind = 2` the host key the fragment
  names, and for `kind = 4` the key the frame itself announces, whose id the envelope's `key_id`
  must be. A frame whose signature verifies belongs to the key that verified; one that verifies
  against none of them is refused `uncommitted_key`, whatever else is right about it.
- **The role is the state's, and it is a key's.** A sender's role is the `role` the applied state
  gives the key that verified, and the two values a client acts on are `viewer` (§13.5) and `host`.
  A `peer_id` in the state is a label the host wrote beside that key and not a binding, so no role is
  read from one, in the state or in a server frame. Where one `peer_id` is named under two keys, or
  two keys are given the role `host`, the state is read as §6.1 says, so that two conforming clients
  read one state the same way.
- **The host's own frames are the case worth spelling out.** A `kind = 1` or `kind = 2` frame
  verifies against the host key, and the host's ordinary frames verify against the session key its
  own `host` entry names. So it is the state's one `host` entry that tells a client **which
  seated peer is the host's connection**; the host key is nobody's peer (§7.1). A state that names
  no `host` entry leaves a client with no identified host connection, and it **MUST NOT** guess one
  — not itself, not the peer whose state it is, and not the key it has seen longest — while
  everything else the state says is applied as it stands.
- **The server's roster and the state answer different questions.** The roster decides which
  connections are **seated**: `room.joined`, `peer.joined` and `peer.left` are what a client shows
  as participants (§6, §9). The state decides **keys and roles**: a role comes from no server frame
  in this version, and a key comes from no server frame at all. A `peer_id` inside the state is the
  host's belief about which seated connection holds a key, and a client reads it for the two
  questions that are about presence rather than authority — whether the state's own `host` entry is
  seated (§13.8), and whether a key's holder has left (§13.7). It is a claim and not a fact, and a
  client **MUST NOT** attribute a frame by it, **MUST NOT** read a role into it, **MUST NOT** read a
  role into `peer.joined`, and **MUST NOT** show a name the state carries as a participant the
  roster does not have. The roster is itself a set of frames the relay authored, so it is as honest
  as the relay is and no more (§3).
- **Attribution is not a value and not an order.** Nothing in a relayed frame says who sent it, and
  the order two frames arrived in says nothing about their senders: the relay holds one queue per
  connection and two peers' frames interleave at the server's pleasure (§2.1). A client that
  attributes a frame by anything but the verification is the client this section is for.

### 13.5 A `viewer`'s content

A peer whose committed key the host's state gives role `viewer` may read the room's documents, and
its edits are not part of the room's:

- **It MUST NOT send document content.** Document content is a `kind = 0` frame whose plaintext
  carries a sync message of `sync_type` 1 or 2 — a SyncStep2 or an Update (§7's message table). The
  three things a viewer may still send are a SyncStep1, which is a state vector and a request rather
  than content and is how a viewer is sent anything at all, awareness, which is presence (§8), and
  its holds, which are a claim about the paths it keeps open rather than content (§13.7).
- **A receiver MUST NOT apply document content from a key the state gives role `viewer`.** It
  refuses that frame, applies none of it, keeps the session, and reports it with the reason
  `unauthorised_content`
  ([`CANONICAL.md`](CANONICAL.md) §6.1). It answers a `viewer`'s SyncStep1 and applies a `viewer`'s
  awareness by §8 and its holds by §13.7, because none of the three is content.
- **The residual, stated rather than implied.** A `viewer` holds the room key, so it can produce a
  perfectly signed and perfectly readable frame, and nothing in the protocol stops it sending one:
  the guarantee is that **no conforming client applies it**, not that a viewer cannot send it. What
  refuses a viewer's edit is another client's rule, and a client that does not implement the rule
  applies the edit. Endpoint enforcement is a denial between conforming peers and not a boundary a
  deployment can point at. What the rule asks of the client on the other side is the same kind of
  conformance: a client that declares a role it was not given in its announcement, or that
  publishes content the state denies it, is a client outside the protocol rather than a client with
  a hole to exploit, and the host's assignment of a role (§7.1) is the other half of the same
  cooperative rule.

### 13.6 What a client owes the room's convergence

Two obligations follow from §13.2's drops, and both are about what a client sends:

- **A client that dropped frames re-syncs.** The rule is §13.1's step 6, applied again after an
  interval in which content was refused: a replica that has been refusing frames and one that has
  been applying them look the same from inside, and only the handshake tells the room which it is.
  **The interval is the client's own renewal clock.** What it runs from is the last `SyncStep1` the
  client sent — the one §13.1's step 6 sends, or a re-sync of its own — and it is closed by the next
  one: a client that has refused a `kind = 0` frame carrying content since that moment **MUST** send
  another `SyncStep1`, on the next renewal tick at the latest, and **MUST NOT** send more than one
  per `awareness_renew_ms` however many such frames it refused. Two bounds are stated rather than
  left to a client's judgement. The lower one is what keeps a peer that floods a client with refused
  content from making it answer frame for frame, which would be an amplification a hostile peer
  chooses; the upper one is what keeps a refusal from going un-repaired for longer than the
  session's own clock. A client that has refused nothing since its last `SyncStep1` has nothing to
  re-sync, and a re-sync is a frame a peer answers only because the client's key is committed, so
  one sent before that is still refused `uncommitted_key` (§13.1's step 4). **What counts as a
  content refusal is the envelope's `kind` and the step that refused the frame, and nothing else.**
  A `kind = 0` frame refused at step 3 or later of [`CANONICAL.md`](CANONICAL.md) §6.1's order is a
  content refusal — `unknown_epoch`, `uncommitted_key`, `replayed_counter`, `bad_signature`,
  `bad_aead`, `bad_payload` and `unauthorised_content` are all of them, and each is an example
  rather than the list — because the frame carried content and none of it was applied. A frame
  refused at step 1 or 2 is not: a malformed envelope and an unknown `kind` are refusals of the
  bytes themselves, `kind = 0` is not readable from them, and no client can say what such a frame
  carried. Nothing about the reason's *kind* is read here, so a client that re-syncs on a refusal
  the vocabulary gained later is conforming without a rule being added for it.
- **A client publishes what it is allowed to publish, and nothing else**: not until a state commits
  its session key (§13.1), its session-key announcement alone before that, no document content at
  all on a connection whose key the state gives role `viewer` (§13.5), and nothing beyond what §7's
  messages and §7.1's sealed values carry. A listing, a path or a role a client invents is not a
  peer's frame, and every conforming receiver refuses it.

### 13.7 The holds, and their lease

**A hold is a peer's statement about itself.** The room's open-document set, which the server kept in
`selvage/1` (§1.2), is in this version the union of the seated peers' live holds: a hold is the claim
that a connection keeps a path open, announced by the holder on a clock it runs and expired by each
receiver on one it runs.

- **Who publishes one, and what it carries.** A peer that has a document open **MUST** announce its
  held paths in a **holds** message — a `kind = 3` frame, sealed under the frame key and signed by
  the session key the applied state commits for it ([`CANONICAL.md`](CANONICAL.md) §6.1, §7.1). The
  message carries the peer's **whole** held set and no delta, and a receiver **MUST** replace that
  peer's set with it rather than merge the two. The peer the set belongs to is the key that signed
  the frame, so a receiver attributes it by the verification it already made (§13.4) and not by a
  value in the plaintext.
- **When the first one goes out.** A peer's first holds message waits for the state that commits
  its own key (§13.1's step 4): before that, a holds message is a frame no peer can attribute and
  every conforming peer refuses it `uncommitted_key`, so it would be a frame nobody applies. A
  holder that has a document open at the moment that state is applied announces at once, and one
  that opens a document later announces when its set changes. That first message is also what arms
  the renewal clock below, which is why the clock is stated against the last message rather than
  against the connection: a `MUST` to re-announce every `awareness_renew_ms` needs a moment to
  count from, and a peer whose key no state has committed yet has not had one.
- **What a hold is not.** It is not a cursor and it is not document content. It is one connection's
  claim on one path, the same kind of claim a `selvage/1` `doc.open` made (§1.2), and it says
  nothing about whether any peer holds a `Y.Text` for the path: a path in a hold set is a candidate
  for presentation, exactly as a path in the listing is (§6.2, §13.3). A `viewer` holds documents
  open like any peer, and its hold message is applied as one, because a hold is not content (§13.9).
- **Renewal is unconditional, timer-driven, and the holder's own.** A holder **MUST** re-announce
  its whole set every `awareness_renew_ms`, from a timer armed on the client's own monotone clock
  and driven by nothing it receives or does. It is not renewed by a keystroke, a caret movement, a
  received frame or any local activity, because the peer whose holds must not lapse is precisely the
  one that is seated, idle, with a document open and typing nothing. A holder **SHOULD** also
  re-announce at once when its set changes, and **MUST** re-announce when it sees a `peer.joined`, so
  that a joiner learns the holds without asking, as §8.3 republishes awareness and §7.1 re-sends a
  state.
- **Expiry.** A receiver **MUST** forget a peer's whole held set `awareness_expire_ms` after the
  last holds message it accepted from that peer. Expiry is checked on the renewal tick, so a set is
  forgotten at the first tick after `last_accepted + awareness_expire_ms`, which is **within
  `awareness_expire_ms + awareness_renew_ms`** (45 s at the defaults) and not exactly at the expiry
  value. **Only an accepted holds message renews a lease.** An ordinary frame from the same peer
  does not: "a frame arrived" does not say "this peer still holds these paths", and a lease any
  frame renewed could not expire a set whose holder had stopped announcing it.
- **Release, and a peer that leaves.** A holder **SHOULD** announce the empty set when it releases
  its last path, so the room learns in one hop rather than waiting out a lease. A receiver
  **SHOULD** drop a key's holds the moment the roster says the seat that key's entry labels is gone
  (its `peer.left`), as it drops that peer's awareness (§8.4): the roster is the authority on who is
  present (§13.4) and the state's `peer_id` is what joins a key to one of its entries, which is the
  only thing that label is read for. A lease is only how a *seated* peer's silence is read, so a
  key whose entry carries no seat the roster knows is left to its lease.
- **What expires is a lease, and not a peer.** A peer whose holds have lapsed is still seated:
  nothing here ends its session, removes it from the roster or refuses its content, and a client
  **MUST NOT** show it as gone. A peer with no holds is a peer with no cursor and no documents
  open; it is not a peer that has left.
- **The numbers, and why they are §8.2's.** There is no hold clock in `keepalive`: the renewal
  interval is `awareness_renew_ms`, the expiry is `awareness_expire_ms`, both read from
  `room.created`/`room.joined` (or `/meta`), and the margin is §8.2's own — the renewal tick's
  latency, up to one `awareness_renew_ms` past the expiry value. The reuse is deliberate and it is
  honest: a hold's life has the same shape as a cursor's (announced on a timer, forgotten after
  silence, checked on the same tick), and §8.2's clock is the session's only one, so a client
  **MUST NOT** substitute its own numbers here any more than it may there. The cost is inherited
  with the rule and stated rather than hidden: a hold lapses exactly as a cursor does, so a client
  whose own timer is throttled — a backgrounded browser tab is the reachable case — loses its holds
  with its cursor, and whether a hold should outlive a cursor is a measurement nobody has taken
  ([`NOTES.md`](NOTES.md) §B.35).
- **Paths in a hold.** A holder **MUST NOT** announce a blank path or one carrying a control
  character (§5), and a receiver **MUST** refuse that path — drop it from the set it holds for that
  peer — rather than the message or the session, exactly as §13.3 refuses such a path in a listing.
  A receiver **MUST NOT** fail on a holds message it cannot use, and **MAY** cap how much of another
  peer's state it will hold, because a hold set is one a hostile peer can grow.

### 13.8 The presence clock, and the host that is away

**No synchronised clock is needed and none travels.** Every timer a client reasons about is its own
monotone elapsed time, armed by an event it observed, and none is a value it reads from a frame or
sends.

- **What a client measures.** Every clock in this section is **local monotone elapsed time**, on the
  client's own machine and from an event it observed, read with the platform's monotone timer
  (`performance.now()`, a Rust `Instant`, `CLOCK_MONOTONIC`) and never a wall clock: a suspend, a clock
  adjustment or a timezone is not the room's business. Nothing about a measurement is on the wire —
  no timestamp, no sequence, no agreement — so two clients' clocks need not agree and this document
  does not ask them to. The holds' lease (§13.7) is one such measurement — elapsed since the last
  accepted holds message from a peer — the host-away clock below is another, and §13.3's **no-state
  window**, elapsed since the client was seated with no state applied, is the third.
- **The host-away clock.** A client's **host-away clock** is armed the first moment it holds a
  verified state whose `host` entry the server's roster does not seat, read through the `peer_id`
  that entry labels (§13.4), and it restarts at a new such state. That pair is the whole of the
  evidence a client has: the roster decides which connections are *seated* and the state decides
  which key *holds the host key*, and the label is the one thing that joins the two. "The host is
  away" is therefore the roster and the state disagreeing — a state whose `host` entry labels a seat
  the roster does not have. The clock is disarmed the moment an applied state's `host` entry labels a
  seat the roster does have, which is the shape a host's return takes: a new connection, a new peer
  id, a new session key in the state, and a state above the room's edition (§9.1).
- **Its threshold is the session's own clock, and not the server's grace.** The host-away clock's
  threshold is the **host-away window**: `awareness_expire_ms`, the session's clock, read from
  `room.created`/`room.joined` (or `/meta`, §2) exactly as renewal and expiry are (§8.2). It is not
  `room_grace_ms`: that number is the server's room-survival window, armed by a different event —
  the room's **last** connection ending (§9) — and reading it as this clock's threshold would make
  two windows that measure different things into one, with a client-side measurement standing on a
  server-side number. A client **MUST NOT** substitute a number of its own any more than it may for
  renewal and expiry, and for §8.2's reason: clients that disagreed would leave a room their peers
  were still holding, or stay in one they had left.
- **It is not armed by silence.** A client **MUST NOT** arm the host-away clock from "I have not
  received a frame from the host for the host-away window", and **MUST NOT** end a session on its own
  clock alone. Silence has two causes and a client cannot tell them apart — a host that is away and
  a client whose own socket has broken — and a client whose socket has broken receives no roster
  event at all: it is reconnecting (§9.1), not watching a room. The roster event is the evidence,
  and only it arms the clock.
- **The window is a floor, not a target.** A client **MUST NOT** leave before its host-away clock
  has passed the host-away window: `awareness_expire_ms` is the duration the session's clock gives a
  host that has dropped its socket to come back in, and a client that leaves inside it abandons a
  room whose host may still return. A client **MAY** wait longer, and the margin beyond the window
  is the observation delay: it errs in the safe direction, because the clock starts no earlier than
  the host actually left.
- **The two windows run in sequence, and both are owed.** A client seated with no state waits the
  **no-state window** of §13.3, and a state whose `host` entry labels an absent seat is what ends
  that wait — so the same client then waits the host-away window from the moment it applied that
  state. A client may therefore hold its seat for up to two windows before ending, and that is
  deliberate rather than double-counting: each window is the time the session's clock gives for a
  different piece of evidence, the first for a state to arrive at all and the second for the host
  that state names to return, and cutting either short abandons a room whose host may still come
  back. The second window is not shortened by the first having run: a client that applied its first
  state at the last moment of the no-state window is owed the whole of the host-away window from
  there.
- **The cooperative rule.** A client whose host-away clock **has** passed the host-away window
  **MUST** end its session and say why. This is a duty between conforming peers rather than a
  boundary a server enforces: in this version the room's death is armed by its **last** connection
  ending `room_grace_ms` later (§9), so a client that leaves once the host has been away that long
  is one of the connections whose ending lets the room die. Without the rule the room has no death at
  all.
- **The debt, stated rather than implied.** A client that does not implement the rule, or a person
  who leaves a tab open, can keep a hostless room alive as long as it stays: no server reaps it,
  because no server knows who the host is (§9). The rule above is the only thing that ends such a
  room, and it is cooperative and therefore not a guarantee.
- **The server's timer is a different one, armed by a different event.** The server's
  `room_grace_ms` counts from the room's **last** connection ending and the client's counts from the
  **host's** departure, and the two need not coincide: a host that leaves while guests remain arms
  the client's clock and leaves the server's unstarted, so the client's may pass while the server's
  has not begun. Each is what it is for — the server's says "the room has held nobody for
  `room_grace_ms`", and the client's says "the host has been away longer than I wait" — and a client
  runs the second and cannot run the first. They also run on different clocks: the server's window is
  `room_grace_ms`, and the client's is the session's `awareness_expire_ms`, so nothing reads the
  server's number for a client-side measurement. Where `room_grace_ms` is read on the client is the
  reconnect window §9.1 sizes from it, and it is that number's own subject.

### 13.9 A viewer's own behaviour

**A `viewer` is a peer whose own session key the applied state gives role `viewer` (§13.4), and
that is the only way a client learns it is one: the invite's parameter is presentation (§13.9's
last rule), and the key it announced is the thing the state names.**

- **What it does not send.** Document content: no `kind = 0` frame whose plaintext carries a
  SyncStep2 or an Update (§13.5). A `viewer` holds the room key and can produce such a frame, and
  what makes it a `viewer` is that it does not and that a conforming receiver will not apply one
  (§13.5's residual).
- **What it still sends.** Its own **SyncStep1**, once it applies a verified state that commits its
  session key and again after any interval in which it refused a content frame — the interval §13.6
  defines, with the same floor and ceiling (§13.1's steps 4 and 6). A SyncStep1 is a state vector and
  a request rather than content, and it is how a `viewer` is sent anything at all: peers answer it
  with a SyncStep2 and the `viewer` receives the room. What a `viewer` re-syncs after is a content
  frame *it* refused to apply; its own content being refused by its peers is the refusal §13.3 says
  never reaches its publisher, so it is not an event this client can observe and not one it answers.
  Its **awareness**, renewed on the session's clock (§8.2), so the room shows its cursor. Its
  **holds**, renewed on §13.7's lease, because a hold is not content. Nothing else: a `viewer`
  publishes no other frame, and in particular no content, no listing and no role.
- **What it applies.** Everything a `guest` applies: document content from peers that may send it,
  awareness, holds and the room state. A `viewer`'s *own* content is what is refused; what it
  receives is not.
- **What its editor shows.** The room as one it can read and not write, where it shows it. A client
  **SHOULD** make the read-only state visible so that a person does not type into a buffer whose
  edits the room does not take, and **MUST NOT** present the room as one it may write to. The
  protocol fixes that an edit is not sent as content and that a receiver refuses one; the words and
  the way the read-only state looks are the adapter's (`DESIGN.md` §4.3).
- **A local edit is the client's, and not the room's.** Whether a client refuses a `viewer`'s
  keystroke, keeps it in its own replica, or discards it is the client's to decide; what it **MUST
  NOT** do is send it as content or treat it as applied to the room. A client that lets a `viewer`
  edit its local replica and then publishes the delta has broken the rule however it looks locally.
- **The invite does not say it.** A host's client telling a guest's client, across the link, that
  it will be a `viewer` is **presentation and not a wire rule**. §5.1 has a receiver ignore a query
  parameter it does not know, which is the whole of what such a parameter can be, and a client
  learns its role authoritatively from the state (§13.4, §13.5). A client **MAY** read a parameter
  of that kind to set its own UI before a state arrives, and **MUST NOT** treat it as authoritative:
  a state that gives the peer's own key `guest` or `host` is the one that binds. Where the convention is
  written down at all is the clients' own shared wording, not this document.

### 13.10 The room's life, as a client sees it

**A client's copy of a room is its own, and a room can end for it in four ways: a verified closing,
the destruction that reaches it as `room_unknown`, its own host-away clock, and §13.3's no-state
window when it never held a state to arm one.** §9's version-2 room
is what the room *is* and §9.1 is the rejoin; this subsection is only what a client holds and
shows.

- **What a client keeps.** The last room state it verified — its listing and its roles — and the
  roster the server gave it. Across a blip it **MUST** keep what a rejoin needs: the room id and
  the token (§5.1's "never echoed after the mint"), and, if it is the host, the private half of the
  host key with its `issued` beside it (§9.1). What it keeps of the *document* is a local decision
  and both answers work: keeping its `Y.Doc` and asking for the delta it missed, or starting empty
  and being brought up to date; and it **MUST NOT** conclude the room is empty because its replica
  is (§9.1). It carries nothing across the socket that the protocol says belongs to a connection: a
  hold, an awareness state or a role belongs to the connection that ended, and a reconnecting
  client re-announces what it still holds rather than trusting what it remembered.
- **A verified `room.closing`.** A client that applies a `kind = 2` closing whose `issued` is above
  the mark it holds, **and that already holds a verified state below it**, treats the session as
  ended (§7.1): it shows the room as over and does not rejoin the id. A closing that does not
  verify, or is not above the mark, is refused like any other frame (`bad_signature`,
  `stale_issued`; [`CANONICAL.md`](CANONICAL.md) §6.1) and ends nothing — §13.2's "MUST NOT end the
  session for a refused frame" is what keeps a closing from being any frame's power. **A closing
  that arrives while the receiver holds no verified state is ignored**, and that is the rule rather
  than an accident: `issued` is an order between two states, so a receiver with nothing to compare
  against cannot tell a fresh closing from a replayed one, and obeying it would let a relay drive a
  joiner out of a live room with one frame. What that leaves, said rather than implied: a receiver
  that holds no state cannot be told a room is over by a frame, and learns it by naming the room
  again — `room_unknown` (§9) — or from its own host-away clock (§13.8). **That bound is on one
  frame and not on the relay.** A replayed room *state* is applied by a receiver that holds none
  (§13.3), and a replayed genuine closing above that state's `issued` then satisfies the rule at the
  top of this bullet: two frames a relay chose to send end a joiner's session in a room that is
  live. What keeps one frame from doing it is the rule above, and what the composition leaves is
  named here rather than implied, because nothing in either frame is unauthentic — the state and the
  closing are both the host's own bytes.
- **The room is destroyed.** In this version no frame announces it: the destruction has no
  recipient (§6, §9), and a client learns the room is gone as `room_unknown` when it next names the
  id, or as close **4001**. That is an ending and not a retryable refusal (§9.1): the id is gone for
  good, and a client **MUST NOT** keep retrying the URL or present the room as rejoinable. A room
  that is merely hostless is not this case and is not gone (§13.8).
- **The host has been away too long.** A client whose host-away clock has passed the host-away
  window ends its session (§13.8), and it **SHOULD** show the room as over rather than as still
  editable or as empty. This is the ending a client causes rather than one it is told about, and it
  is the cooperative half of §9's room simply having no server-side reaper.
- **A blip, and the rejoin.** §9.1 is the whole of it and this subsection does not restate it: a
  reconnect is a `session.hello` on a new socket, a new peer, the room and token from the invite, a
  fresh awareness client id, a re-run of §7's handshake, and, for a host, a state published above
  the room's edition. What §13.7 adds is that a reconnecting peer announces the paths it still
  holds again, and that a peer which has lost its session stops renewing, so its holds lapse with
  its lease instead of lingering. What a client **MUST NOT** do is carry anything about the old
  connection into the new one: the old peer id, its holds and its awareness state are gone with the
  socket (§9.1).
- **A room joined while the host is away.** A client can be seated in a room whose host is not
  there and receive no state until one arrives (§6.1). It **MUST NOT** present the room as having no
  files, and it **MUST NOT** guess a host (§13.3, §13.4); it shows the room as waiting. Once a state
  arrives whose `host` entry labels a seat the roster does not have, §13.8's clock runs and it leaves
  once the host-away window has passed; until a state arrives it **MUST NOT** end for the absence of
  one before the no-state window, and it **MUST** end once that window has passed with nothing
  applied (§13.3). That second ending is the one that reaches `room_unknown`: a client that stays
  seated holds a room nobody is in alive, and only its leaving lets the room's own grace run out and
  the next connection that names the id be told.

### 13.11 Checkability

**What a conformance test observes, and what a vector cannot pin.** The peer layer's evidence is a
subject a test drives through the corpus's decision layer ([`NOTES.md`](NOTES.md) §B.35), and every
rule above is stated in terms of the observables that layer already has: what a client **applied**
to its replica or its view, a frame it **dropped** and the reason [`CANONICAL.md`](CANONICAL.md)
§6.1's vocabulary gives, what it **published**, and whether it **ended** its session. No rule here
needs a reason the vocabulary does not already name: each is a refusal one of the ten names, an
expiry (a set becomes empty, and no frame is dropped), an obligation about what a client sends, or an
ending (`ended`).

**What a client sends is two counts and not one.** A client's **publications** are the frames §13's
rules are about: its session-key announcements, its document content, its holds messages, and the
room states and closings a host publishes. Its **sync-handshake frames** are §7's — a `SyncStep1` or
a `SyncStep2` — and are counted apart, because §13.1's step 6 obliges a client that applies a state
committing its own key to send one and §13.6 obliges it to send another after a content refusal, so a
publication count that folded the two together could not tell a republished request from a
publication. Both are frames and both are visible to a test; which of the two a rule moved is what an
assertion about them says.

| rule | the observable | the fixture, or the mutation, that must turn a wrong implementation red |
|---|---|---|
| A holds message applies (§13.7) | the subject holds the paths; `applied`; `published` counts the message | a holds message sealed with a committed fixture session key, under a fixture state that commits it; the positive control |
| A replayed holds message is refused | `dropped` with `replayed_counter`; the held set is unchanged; `published` did not move | the same holds bytes sent twice; the mutation that removes the counter mark must fail this leg |
| A key no state commits is refused | `dropped` with `uncommitted_key` | a holds message signed by a key the fixture state does not name |
| A `viewer`'s holds apply (§13.9) | `applied`; no content is sent | a state committing the sender as `viewer`, then its holds message; the mutation that removes the role rule must leave this leg green |
| A lease expires a silent peer (§13.7) | after a bounded wait, the subject's view of that peer's holds is empty; no frame dropped | a fixture room whose `keepalive` compresses the window, a peer that announces once and is then silent, and a poll on the predicate with a deadline; the mutation that removes the lease must fail it |
| Renewal is unconditional (§13.7) | the subject, idle and receiving nothing, keeps `published`-counting holds messages across a window | the mutation that renews only on a local edit: the idle subject publishes no renewal, and the peer's lease expires it |
| The host-away clock leaves at its window (§13.8) | `ended` within the host-away window of the state naming an absent host, and not before | a transcript with `peer.left` for the seat the state's `host` entry labels and no state above it whose `host` entry labels a seated seat, and a compressed `awareness_expire_ms`; a client that ends on silence, or before the window, or never, must fail |
| A session key is announced and committed (§7.1, §13.1) | `published` counts the announcement; the state the subject publishes next carries that key | a fixture announcement signed by the key it names, under a fixture state that does not commit it; the mutation that announces a key other than the signer's must be refused `bad_signature` |
| A plaintext that is not an object of its kind's members and types is refused (§13.2) | `dropped` with `bad_payload`; nothing applied | a `kind = 1` frame whose plaintext is a bare string and one whose `issued` is a string, sealed and signed with fixture keys; the mutation that removes the step must not drop the frame |
| A path §5 refuses is dropped and not refused (§13.3, §13.7) | the path is absent from what the subject offers and from the holds it keeps for that peer; the state or the message was applied and nothing was dropped | a fixture state whose `listing` carries a control-carrying path and a holds message whose set does, and a path over the 4096-byte bound; the mutation that refuses the state instead must fail this leg |
| Every accepted announcement is committed (§7.1) | the state the subject publishes next carries the announced key; `published` counts it, and counts no more than one announcement-driven state per `awareness_renew_ms` when several are accepted inside one | a fixture announcement whose key no seat in the roster can be placed, and a second and third accepted inside the same compressed window; a host that withholds the commitment for want of a label must fail, and so must one that publishes a state per announcement |
| A seat holds one key, a departure drops it, and a re-announcement changes nothing (§7.1) | the state published after a second announcement placed at one seat carries the new key and drops the old, and a frame from the replaced key is refused `uncommitted_key`; the state published after that seat's `peer.left` carries neither key; `published` does not move for an announcement whose key the state already commits | two announcements placed at one seat, then the replaced key's holds message, then a `peer.left` for the seat; a third announcement from the committed key on the renewal clock. The mutation that keeps both keys, or that leaves a departed seat's key in the state, or that publishes a state per announcement, must fail this leg |
| A client with no state ends at its window (§13.3) | `ended` within the no-state window of being seated, and not before | a fixture room whose host never publishes a state, with a compressed `awareness_expire_ms`; a client that waits for ever, or that ends inside the window, must fail |
| A dropped announcement is recovered (§13.1) | `published` counts announcements across a window with no committing state, the key is committed by the host's state once it arrives, and a state the host already holds is re-sent for an announcement whose key it already commits; the subject applies it and stops announcing | a fixture relay that drops the subject's first announcement, with a compressed `awareness_renew_ms`, and a second leg whose relay drops the *state* that commits the key; a client that announces once and waits, or a host that answers neither within a window, must fail |
| A key that is not the canonical encoding is refused (§13.2) | `dropped` with `bad_payload`, in a state's `peers` name and in an announcement's `key` | a state naming a key whose final character carries non-zero pad bits and one whose key carries a trailing newline, and the plaintext's own spelling of each; a lenient decoder that resolves either to a 32-byte key must fail |
| A client that refused content re-syncs (§13.6) | a `SyncStep1` sent after the refused frame, at most one per `awareness_renew_ms` however many were refused; nothing published in answer | a content frame from a key no state commits, delivered twice inside one compressed window; a client that never re-syncs, and one that answers frame for frame, must each fail |
| A verified closing ends; an unverified one does not (§13.10) | `ended` true for the first, false for the second and for a closing delivered to a subject holding no state | two `kind = 2` frames, one above the mark and one at it, and one delivered before any state; the mutation that drops the `issued` ordering must fail the first leg |
| The room is gone, not retryable (§13.10) | the subject sends no second `session.hello` to the id; `published` shows the one hello | an absence scan over the transcript after `room_unknown`, as §6's scans are |
| The invite's `viewer` parameter is not authoritative (§13.9) | a subject handed the parameter, then a state committing it as `guest`, behaves as a `guest` | a fixture state that contradicts the parameter; a client that trusts the URL must fail |

**What a vector can pin.** The bytes: that a holds message is sealed and signed as §6.1 fixes, that
a wrong key, a replay and a bad signature are refused with the reason the vocabulary names, that a
`viewer`'s content is refused `unauthorised_content` and its holds are not, and that a closing above
the mark ends while one at or below it does not. These are envelope facts, and a byte-exact vector
against the fixture keys pins them, and the corpus's design already fixes six such vectors for the
frame layer; a seventh and an eighth for the holds carrier, and two more for the session-key
announcement and for a plaintext that is not its kind's object, belong to the layer that is not
written.

**What no vector can pin, and it is the lease's own limit.** A vector is byte-exact evidence and a
clock is not. No vector can tell a client that renews on its own timer from one that renews on
activity, or one that expires at the advertised value from one that hard-codes 15 s, because the
difference is *when* frames are sent and not what they contain. The lease, the host-away clock and
every rule that names `awareness_renew_ms`, `awareness_expire_ms` or `room_grace_ms` are therefore
checkable only by **behaviour**: a subject that reports `published`, `applied` and `dropped`, and a
test that polls a real predicate with a deadline on a window the fixture compresses. That is also
why the fixture must carry a compressed `keepalive` for these vectors rather than the defaults:
with the defaults a test either sleeps 45 seconds or asserts a duration instead of an event, and
neither is evidence.

**The fixture shapes this subsection constrains.** In addition to the shapes
[`NOTES.md`](NOTES.md) §B.34 records, this layer needs: a holds message from a key a fixture state
commits, and the same bytes replayed; a holds message from a key no state commits; a holds message
whose set changes between two announcements; a `viewer` and a `guest` each announcing holds under
one state; a room whose `keepalive` compresses the awareness window, for the lease and the
host-away clock; a `peer.left` for the seat the state's `host` entry labels, with no state above it
whose `host` entry labels a seated seat; a session-key announcement signed by the key it names and
one that is not; a `kind = 1` plaintext that is not the room state's object and one whose `issued` is
not a count; a state whose `peers` names a key whose final character carries non-zero pad bits, and
an announcement whose `key` does; a state whose `listing` and a holds message whose set each carry a
path §5 refuses, and a path over §13.3's 4096-byte bound; a room whose host never publishes a state;
and a `kind = 2` closing above the mark, at the mark and before any state. The corpus's own fixture design predates the
frozen bytes and carries a `key_id` per peer where §6.1 fixes a 32-byte `key`: a fixture for this
layer follows §6.1 and not that shape.

## 14. References

**Normative.**

- [RFC 2119] Bradner, *Key words for use in RFCs to Indicate Requirement Levels*, BCP 14.
- [RFC 8174] Leiba, *Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words*, BCP 14.
- [RFC 6455] Fette, Melnikov, *The WebSocket Protocol*: the framing, the Ping/Pong keepalive, the
  close codes and the 125-byte control-frame payload this document relies on.
- [RFC 9110] Fielding, Nottingham, Reschke, *HTTP Semantics*: for `GET /meta`, which deviates
  from it in one respect (§2), and for what a fragment is not: a URL's fragment is dereferenced by
  the user agent and never sent, which is why the invite's two keys travel there (§5.1).
- [RFC 4648] Josefsson, *The Base16, Base32, and Base64 Data Encodings*: §5's URL- and
  filename-safe alphabet, which is how the invite's room key and host key are written into a
  fragment (§5.1, `CANONICAL.md` §6.1).
- [RFC 5869] Krawczyk, Eronen, *HMAC-based Extract-and-Expand Key Derivation Function (HKDF)*: the
  frame key a sealed frame is sealed under (`CANONICAL.md` §6.1).
- [RFC 8032] Josefsson, Liusvaara, *Edwards-Curve Digital Signature Algorithm (EdDSA)*: Ed25519,
  the signature algorithm of a sealed frame, of the room state and of a closing (§7.1,
  `CANONICAL.md` §6.1).
- [SP 800-38D] Dworkin, *Recommendation for Block Cipher Modes of Operation: GCM and GMAC*:
  AES-256-GCM, the AEAD of a sealed frame (`CANONICAL.md` §6.1).
- [y-protocols] [`y-protocols/PROTOCOL.md`](https://github.com/yjs/y-protocols/blob/master/PROTOCOL.md):
  the document-sync and awareness payloads this layer carries and does not define (§7, §8).
- [`CANONICAL.md`](CANONICAL.md): SJ-C/1, the byte form of a session text frame.
- [`schema/`](schema/): the machine-readable model of every frame this document describes.

**Informative.**

- [`NOTES.md`](NOTES.md): the implementations' status, the decisions this draft had to make, and
  what is still open. It binds nothing.
- [`vectors/`](vectors/) and [`runner/`](runner/): transcripts of real bytes, and the replay that
  holds a running server to them.
- [`selvage-protocol/reference_server`](https://github.com/selvage-protocol/reference_server):
  the implementation these requirements were observed on, and its conformance harness.
- The project's unpublished design record, cited in [`NOTES.md`](NOTES.md) §A.8 where a decision's
  provenance is worth keeping.
