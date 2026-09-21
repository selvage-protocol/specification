# Selvage Session Protocol: wire specification

**Status: DRAFT.** Wire version `selvage/1`. Every requirement below was observed on the wire of
the running implementation before it was written down, in the sessions in
[`crates/harness/tests/`](https://github.com/selvage-protocol/reference_server/tree/main/crates/harness/tests),
and nothing here is ratified.

This document is normative, and §1.1 says which sentences bind a reader and what a conforming
implementation is. Three sibling artifacts fix the other halves of the same thing, and all four
are meant to be read together:

| artifact | what it fixes |
|---|---|
| [`CANONICAL.md`](CANONICAL.md) | **SJ-C/1**, the byte form of a session text frame. This document says what the members mean; that one says how they are written, and a frame conforms to both |
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
payloads but does not define them: those are y-protocols (§13).

This draft covers, and only covers:

- handshake, version and capability negotiation;
- room mint, join, roles, and the room lifecycle;
- the open-document set, and the room's grant listing;
- relay of document-sync and awareness payloads (opaque to the server).

It does **not** cover, in this slice: persistence, accounts, authentication beyond a room token,
file access (the room's grant is a list of *names* the server carries and never resolves, §5),
terminals, rich text, E2EE, or any HTTP API other than `GET /meta`.

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
- the items of [§13 References](#13-references) that are marked informative;
- [`NOTES.md`](NOTES.md), which is not part of this specification.

`CANONICAL.md` is normative for the bytes of a frame: member order, whitespace, string escapes,
number form and array order. This document is normative for what those members mean. A frame
conforms when it satisfies both.

**What conforms.** A conforming **server** implements §2–§11 on the server side; a conforming
**client** implements §4–§10 on the client side. Both implement `CANONICAL.md`, both are bound by
§12 and §13, and both are bound by the [frames of the version they
speak](#10-version-and-capability-negotiation): conformance is per wire version, and this document
is `selvage/1`.

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
  only while the room has no host.
- **token**: the room's permission, minted with the room, carried in the invite URL, never echoed
  after `room.created` (§5.1). It is the whole authentication in this slice (§12).
- **refusal**: a fault that ends a connection before it is seated, answered with `session.error`
  and then a close carrying the matching code (§11).
- **grace period**: the interval after a host's connection ends during which the room still
  exists and can be reclaimed. `host.detached` announces it, with its length in `grace_ms` (§9).
- **hold**: one connection's claim on one path, made by `doc.open` and released by `doc.close` or
  by the connection ending. A hold belongs to a connection.
- **the room's open-document set**: the paths the room has open, `documents`, in first-opened
  order. It belongs to the room and **outlives every peer that opened a path**; a path leaves it
  only when the last hold on it is released (§5).
- **the room's grant**: the files the room's host has published as its working tree, `paths`,
  written ascending by its publisher in UTF-16 code units (§5). It is the same kind of value and
  the same kind of claim as a path in the open-document set: a list of names, no content,
  unvalidated by the server (§5, §12). It belongs to the room as the open-document set does, and
  is replaced wholesale by every `doc.grant`.
- **the session document**: the single `Y.Doc` a room's peers converge on, one `Y.Text` per open
  path keyed by the path (§7). The server never holds it.
- **text frame**, **envelope**, **binary frame**, **message**: a *text frame* is one JSON
  **envelope** (§4). A *binary frame* carries a stream of one or more y-protocols **messages**
  (§7), which the server relays without decoding.
- **advertised**: carried by the server in `capabilities`, `keepalive` and `wire_versions`:
  in `GET /meta` and again in the handshake reply (§10).
- **set**: an array whose order the protocol does not promise: `peers`, `capabilities`,
  `wire_versions`, `roles` (§2, §6.2, §10, `CANONICAL.md` §2.7). `documents` and a grant's
  `paths` are the only arrays this protocol orders (§5, §6.3).
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
  set and its grant: a request that would exceed a bound the server sets is refused with
  `bad_params` or an `x.` capacity code (§10.1, §11), leaving the room as it was.

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
| room state: rooms | 1024 minted at once | a mint past it is refused `x.server_full`; the room is not created |
| room state: peers per room | 128 | a further join is refused `x.room_full`; a connection reclaiming a hostless room as its host seats anyway, because the room's owner must be able to come back |
| room state: the room's open-document set | 1024 paths | a `doc.open` for a path the set does not already hold is refused `x.room_full`, and the connection stays open |
| room state: the room's grant | 100 000 paths, 4096 bytes to a path, 4 MiB of path bytes in total | a `doc.grant` past any of the three is refused `bad_params`, and the connection stays open |
| inbound budget, per connection | 2 MiB a second, refilled continuously, with a 64 MiB burst to spend | the peer is told `x.rate_limited` and closed **1013** (try again later); its reconnect starts with a fresh budget |

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

Five methods exist. A method is cited by its name; this table is the index.

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
ignored, so a client **MAY** attach its own parameters without a protocol change. `room` and
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
  alternative was rejected.

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
  "for ever". (The two reference clients differ here; [`NOTES.md`](NOTES.md) §A.2.)
- **A socket that drops fails every request in flight.** When the connection ends, whether the
  client asked for it or not, each outstanding request **MUST** be failed locally: no answer can
  arrive on a socket that is gone, and a caller left holding a request that never completes cannot
  tell that from a slow server. Whether the server applied a request it never answered is not
  knowable, and a guess about it is an answer the wire does not carry; for the methods in
  `selvage/1` it does not matter: a hold is a set, so a second `doc.open` for a path already held
  changes nothing, a grant is a snapshot, so a second `doc.grant` that repeats a listing changes
  nothing, and an applied rename reaches the mover as the `peer.renamed` every peer receives
  (§6).
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
| `room.joined` | `SessionParams` without `token` | reply to a `session.hello` that joined one | adopts `keepalive` and reads `documents` as the room's membership, not as its content (§6.1) |
| `peer.joined` | `{ "peer": PeerInfo }` | to the peers already in the room when a connection is seated | adds the peer to the roster, and reads `peer.role` rather than assuming a guest arrived |
| `peer.left` | `{ "peer_id": string }` | to the remaining peers when a connection ends | removes the peer, and **SHOULD** drop its awareness state (§8.4). A host leaving is *not* the end of the room (§9) |
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
  to nobody else.
- **Convergence.** Two replicas are converged when their texts are identical **and** their state
  vectors agree. A client that has just joined is brought up to date by this handshake; the server
  has nothing to replay.
- **Ordering.** The server offers no cross-peer ordering guarantee (each peer has its own queue).
  yjs convergence does not require one, but an editor adapter **MUST NOT** assume ordering between
  documents or between peers.

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

There is no awareness handshake in this slice:

1. On seating a connection the server sends `peer.joined` to the peers already present.
2. Each of those peers republishes its own current awareness state.
3. The newcomer publishes its own state as soon as it is seated.

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
editor adapter joins the two: awareness client id → peer → display name and role.

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
  the grace arms when the peer was the host, and no session close code is sent), because a host
  that has silently stopped answering would otherwise hold a room open forever: the reference
  waits two intervals, and its number is policy (§2.1). The same `keepalive` object carries the
  awareness window that clients run on (§8.2): the server's numbers are the session's, and a
  client that overrides them is choosing to disagree, not negotiating.

### 9.1 Reconnecting

A dropped connection takes everything that belonged to it: the `peer_id`, the claimed role, this
connection's document holds and its awareness state. Nothing about a client survives a socket, so
a reconnecting client is a new peer that has to say who it is again. What it must know and do:

- **Reconnect is `session.hello` again**, on a new socket, with the room and token the invite URL
  carries. There is no resume, no session id and no server-side state to hand back.
- **The host reclaims; a guest rejoins.** Within `grace_ms` of the host's disconnect, a connection
  claiming `role: "host"` **with the token** is seated, and the others are told `host.attached`
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
  is seated: it **SHOULD** re-`doc.open` the documents it still holds open (which is what puts them
  back in the room's set when nobody else had them), and republish awareness.
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
  `host_present` and `unsupported_version` refuse for a reason a retry cannot change (a room that
  is gone is gone for good, and the host did not come back inside the grace period), so a client
  **MUST** stop and say why rather than reconnect into the same refusal. A fault in the reserved
  `x.` namespace (capacity, in this slice (§2.1, §10.1, §11)) is a stop as well: a handshake
  refused with an `x.*` code **MUST NOT** be re-helloed automatically, and a seated request
  refused with one **MUST NOT** be re-issued automatically; the attempt ends, or the request
  fails, and anything further needs its user's action. A room destroyed under a
  *seated* connection is not a refusal a client could have avoided: it learns of it as the
  `room.gone` event and close 4003 (§6, §11), which is an ending and not a retry. Every other loss
  of the socket is **recoverable**, and a client **SHOULD** re-hello with a bounded backoff rather
  than in a tight loop.
- **What `selvage/1` asks for is the shape of the policy, not its numbers.** A retry **MUST** be
  bounded, giving up **MUST** be a decision a caller can observe, and a refusal **MUST NOT** be
  retried. A client that has never had a session has nothing to recover, and a failure before the
  first connection is reported to whoever asked for one rather than retried behind its back. (The
  reference client's backoff parameters are [`NOTES.md`](NOTES.md) §A.2.) A client that knows the
  room's grace (`room_grace_ms` from `/meta` (§2), or the `grace_ms` a `host.detached` carried
  (§9)) **SHOULD** keep retrying at least until that window has passed, because the room survives
  its host's absence for exactly that long and a retry that gives up inside it abandons a room
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
  token (§9).

### 9.2 The state machines

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
| unseated | no frame within the server's hello timeout | closed | `session.error{hello_required}`, close 4000 |
| seated | `session.hello` | seated | the error response `already_seated` |
| seated | `doc.open` / `doc.close` with a non-blank `path` | seated | the result, then `doc.opened`/`doc.closed` to the room |
| seated | `doc.open` / `doc.close` with a blank, over-long or control-carrying `path`, or params that do not parse | seated | the error response `bad_params` |
| seated | `doc.grant` with a `paths` array of non-blank, control-free strings | seated | the result, then `doc.granted` to the room |
| seated | `doc.grant` from a connection the server does not hold as the room's host, or with malformed `paths` | seated | the error response `bad_params` |
| seated | `session.rename` with a non-blank `display_name` within the bound | seated | `{ "result": {} }` to the caller, then `peer.renamed` to the room, the caller included |
| seated | `session.rename` with params that do not parse, or a blank, over-long or control-carrying `display_name` | seated | the error response `bad_params` |
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
| `host_present` | a host is already connected | refusal: `session.error`, then close **4004** | — |
| `already_seated` | `session.hello` sent twice | — | error response; the connection stays open |
| `doc_not_open` | reserved; not produced by this slice | — | — |

The **close** vocabulary is separate, and `selvage/1`'s part of it lives in the private-use
range: 4000 `protocol_error`, 4001 `room_unknown`, 4002 `token_invalid`, 4003 `room_gone`, 4004
`host_present`, 4005 `unsupported_version`. A code with a matching close ends the connection; a
code without one does not. That vocabulary is not the whole of what a peer can receive: a close
outside it carries no session meaning and a client **MUST NOT** read one into it, because a
capacity fault that is not about the session protocol is IANA's to name — the reference server
closes **1013** (try again later) at its connection cap and when a connection has spent its
inbound budget (§2.1, §12) — and 1013 is not in the private-use range at all.

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

## 13. References

**Normative.**

- [RFC 2119] Bradner, *Key words for use in RFCs to Indicate Requirement Levels*, BCP 14.
- [RFC 8174] Leiba, *Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words*, BCP 14.
- [RFC 6455] Fette, Melnikov, *The WebSocket Protocol*: the framing, the Ping/Pong keepalive, the
  close codes and the 125-byte control-frame payload this document relies on.
- [RFC 9110] Fielding, Nottingham, Reschke, *HTTP Semantics*: for `GET /meta`, which deviates
  from it in one respect (§2).
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
