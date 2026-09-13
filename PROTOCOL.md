# Selvage Session Protocol — wire draft

**Status: DRAFT, implementation-led.** This document describes exactly what
`impl/` in this repository does today, at wire version `selvage/1`. It is written from
running code, not from intent: every JSON shape below was observed on the wire of the
integration tests in `impl/crates/harness/tests/`, and the binary framing is described as
`yrs` implements `y-protocols`. Nothing here is ratified, and the section
[Open questions](#12-open-questions) lists what had to be decided in the absence of a
settled design.

If you are writing a second, independent client, this document plus
[`y-protocols/PROTOCOL.md`](https://github.com/yjs/y-protocols/blob/master/PROTOCOL.md)
should be sufficient; the Rust in `impl/` is not required reading, and where this document
and that code disagree, the code is wrong.

`DESIGN.md` at the repository root is the agreed design record. This document is the
wire-level refinement of §4 of that record. It does not modify it.

---

## 1. Scope

The Selvage Session Protocol is the *session* layer above document sync: rooms,
participants, roles, which documents are open, presence, join and leave. It carries
document sync and awareness payloads but does not define them — those are y-protocols.

This draft covers, and only covers:

- handshake, version and capability negotiation;
- room mint, join, roles, and the room lifecycle;
- the open-document set;
- relay of document-sync and awareness payloads (opaque to the server).

It does **not** cover, in this slice: persistence, accounts, authentication beyond a room
token, file access, terminals, rich text, E2EE, or any HTTP API other than `GET /meta`.

## 2. Transport

- **One endpoint**: `ws://<host>:<port>/session`, a WebSocket. No TLS in this slice;
  the server is expected to run on localhost or behind a terminator.
- **One negotiation endpoint**: `GET /meta` over plain HTTP on the same listener.
  Response body:

  ```json
  {
    "server": "selvaged/0.1.0",
    "wire_versions": ["selvage/1"],
    "capabilities": ["y-protocols/1", "awareness", "open-document-set", "host-reclaim"],
    "keepalive": {
      "ping_interval_ms": 30000,
      "awareness_renew_ms": 15000,
      "awareness_expire_ms": 30000
    },
    "roles": ["host", "guest"]
  }
  ```

  A client **should** read `/meta` before connecting when it can, to fail fast on an
  incompatible server. Other paths return `404`; the negotiation endpoint does not
  support keep-alive, `HEAD`, or any method other than `GET` in this slice.

- **Frame types.** Text frames carry the JSON session envelope (§4–§6). Binary frames
  carry y-protocols payloads (§7, §8). The server routes binary frames by room membership
  and never decodes them.

## 3. Layering and opacity

The server is **session-aware, payload-opaque** (`DESIGN.md` §4.5). It parses text frames,
owns rooms, membership and the open-document set, and forwards binary frames to the rest of
the room byte for byte. A conforming client must not expect the server to interpret,
validate, transform, or take an interest in any y-protocols payload. In particular the
server does not hold a CRDT, and document convergence is achieved peer to peer through the
relay, not against the server.

## 4. Session envelope

Every text frame is one JSON object. Three shapes exist, distinguished by which keys are
present.

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

**Unknown fields are ignored** — clients and servers must tolerate them, so that adding a
field is never a protocol break. Unknown *capabilities* are likewise ignored (§10).

A request without `id` produces a `session.error` event (§6) with code `bad_message`.

### 4.2 Response (server → client)

```json
{ "v": "selvage/1", "id": 2, "result": {} }
{ "v": "selvage/1", "id": 3, "error": { "code": "unknown_method", "message": "no such method: cursor.teleport" } }
```

Exactly one of `result` or `error` is present. `result` is an object (`{}` when a method has
nothing to return). `error.code` is a machine-readable code from §11; `error.message` is
human-readable and not stable.

**An unknown method is always an error response.** It is never silently dropped: silence
turns version skew into an unexplained timeout.

### 4.3 Event (server → client)

```json
{ "v": "selvage/1", "event": "peer.left", "params": { "peer_id": "p-3d33…" } }
```

Events carry no `id` and are not responses to anything. Key order within a JSON object is
not significant and is not stable.

## 5. Methods

### `session.hello` — the handshake

Must be the **first** text frame on a connection. Sending anything else first, or failing
to complete the handshake within the server's hello timeout (10 s in the reference
implementation), closes the connection.

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
| `display_name`         | string | yes      | non-empty; the only identity in this slice |
| `role`                 | string | no       | `"host"` or `"guest"`; defaults to `"host"` when the URL carries no room, `"guest"` otherwise |
| `awareness_client_id`  | number | no       | the y-protocols awareness client id this connection will speak with; see §8.4 |
| `capabilities`         | array  | no       | capabilities the client believes it has; the server ignores any it does not know |
| `client`               | string | no       | free-form client identification for diagnostics |

The room to join, and its token, are carried in the connection URL, not here (§5.1).

The reply is a single `room.created` or `room.joined` event (§6.1, §6.2). Refusals are a
`session.error` event followed by a WebSocket close with the matching code (§11).
`session.hello` sent a second time on the same connection is an error, code
`already_seated`.

### 5.1 Room and token in the connection URL

```
ws://host:port/session                          → mint a room; the connection is its host
ws://host:port/session?room=<room_id>&token=<tok> → join an existing room
```

`room` and `token` are percent-encoded. A `room` without a `token` is a `token_invalid`
refusal. Unknown query parameters are ignored, so a client may attach its own parameters
without a protocol change.

This is what makes the shared link literal: the host's invite URL **is** the WebSocket URL.

### `doc.open`

```json
{ "v": "selvage/1", "id": 2, "method": "doc.open", "params": { "path": "src/main.rs" } }
```

`path` is a workspace-relative path; the string must be non-empty, and is otherwise
unvalidated in this slice (§12, question 8). The connection declares that it holds `path`
open, and the room's open-document set gains the path if it was not already in it. The
reply is `{ "result": { "documents": [ … ] } }` — the room's set after the change — so a
caller is told what its request did instead of assuming it. Every peer in the room,
including the one that sent the request, then receives a `doc.opened` event (§6.5)
carrying the same set. A peer must tolerate the same path being opened by several peers,
and by the same peer twice: holds belong to a connection, and opening a path twice from
one connection is one hold.

### `doc.close`

Same shape with `method: "doc.close"`. Releases **this connection's** hold on the path.
The path leaves the room's open-document set only when no other peer still holds it open;
if another peer has the same document open, the set does not change. The reply is
`{ "result": { "documents": [ … ] } }`, the room's set after the change, and every peer
receives a `doc.closed` event carrying it. Closing does not delete content: the `Y.Text`
remains in the session document, and a later `doc.open` by any peer sees it again.

A connection that disconnects releases its holds without announcing anything, but the
paths it held stay in the room's set: the set belongs to the room and outlives the peers
that opened a path, so a host that reconnects during the grace period is told what was in
play.

### Unknown methods

Any other method name is answered with an error response, code `unknown_method`. The
connection stays open.

## 6. Events

All event `params` are flat objects.

| event | params | when |
|---|---|---|
| `room.created` | `SessionParams` with `token` | reply to `session.hello` that minted a room |
| `room.joined` | `SessionParams` without `token` | reply to `session.hello` that joined one |
| `peer.joined` | `{ "peer": PeerInfo }` | to existing peers when a connection is seated |
| `peer.left` | `{ "peer_id": string }` | to remaining peers when a connection ends |
| `doc.opened` | `{ "peer_id": string, "path": string, "documents": [string] }` | to every peer when a peer opens a document |
| `doc.closed` | `{ "peer_id": string, "path": string, "documents": [string] }` | to every peer when a peer closes one |
| `host.detached` | `{ "grace_ms": number }` | to remaining peers when the host's connection ends |
| `host.attached` | `{ "peer": PeerInfo }` | to remaining peers when a host reclaims the room |
| `room.gone` | `{ "room_id": string, "reason": string }` | to remaining peers when the room is destroyed |
| `session.error` | `{ "code": string, "message": string }` | for faults that cannot be attached to a request id |

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
- `token` is present **only** in `room.created`, and only for the connection that minted
  the room. It is never echoed in `room.joined`, not even to the host after a reconnect.
- `self` is the joining connection's own peer record.
- `peers` lists the peers already in the room, excluding `self`.
- `documents` is the room's open-document set, in first-opened order.
- The response is guaranteed to be the first frame on the connection after the handshake,
  before any relayed payload or other event.

## 7. Document sync

Follows `y-protocols/PROTOCOL.md`. `yrs` is byte-compatible; this implementation uses
`yrs::sync::protocol::DefaultProtocol` rather than a hand-written codec.

- **One `Y.Doc` per session, one `Y.Text` per document**, keyed by workspace-relative path
  (`Y.Doc::get_or_insert_text(path)`). Document identity is the path, and it travels in the
  `doc.open` method; it is not encoded inside the CRDT.
- A binary frame is `varUint(message_type)`, then:

  | message_type | meaning | body |
  |---|---|---|
  | 0 | sync | `varUint(sync_type)` then `varUint8Array(payload)`; `sync_type` is 0 = SyncStep1 (state vector), 1 = SyncStep2 (update), 2 = Update |
  | 1 | awareness | `varUint8Array(awareness update)` |
  | 2 | auth | unused in this slice (there is no per-join approval) |
  | 3 | awareness query | valid but unused; see §8.3 |

  `varUint` is LEB128; `varUint8Array` is a `varUint` byte length followed by the bytes.
  The sync payloads are yjs v1 encodings.

- **Who sends what.** Each client, immediately after `room.joined`/`room.created`, sends a
  SyncStep1 with its state vector. Every peer that receives a SyncStep1 replies with a
  SyncStep2 containing what the sender is missing. Local edits are broadcast as Update
  messages carrying only the delta. The server relays each binary frame to every other
  participant in the room, and to nobody else.
- **Convergence.** Two replicas are converged when their texts are identical **and** their
  state vectors agree. A client that has just joined is brought up to date by this
  handshake; the server has nothing to replay.
- **Ordering.** The server offers no cross-peer ordering guarantee (each peer has its own
  queue). yjs convergence does not require one, but an editor adapter must not assume
  ordering between documents or between peers.

## 8. Awareness

Awareness uses the y-protocols awareness format, inside `message_type = 1` frames.
Convergence of cursors is peer to peer; the server relays and forgets.

### 8.1 The state payload

y-protocols leaves the awareness state opaque. This implementation's state is:

```json
{ "path": "src/main.rs", "selection": { "anchor": 11, "head": 13 } }
```

Both fields are optional. Identity is **not** here: a display name travels in the session
layer (§6.1). A client that understands neither field must still relay nothing and simply
ignore a state it cannot parse.

### 8.2 Renewal and expiry

- A client renews its own state every `keepalive.awareness_renew_ms` (15 s) by republishing
  it with a newer awareness clock.
- A client drops a *remote* state it has not seen for `keepalive.awareness_expire_ms` (30 s).
- The server does not track awareness and does not expire it. A client alone in a room
  therefore never churns: it never receives an echo of its own awareness, and never expires
  itself.

### 8.3 Discovery

There is no awareness handshake in this slice:

1. On seating a connection the server sends `peer.joined` to the peers already present.
2. Each of those peers republishes its own current awareness state.
3. The newcomer publishes its own state as soon as it is seated.

`message_type = 3` (awareness query) and the `handle_awareness_query` reply are part of
y-protocols and a client should tolerate them, but this implementation does not use them.

### 8.4 Attributing a cursor to a person

An awareness state is keyed by a y-protocols client id, which carries no identity. Session
`PeerInfo` therefore carries `awareness_client_id`, supplied by the client in
`session.hello`. An editor adapter joins the two: awareness client id → peer → display name
and role. When a peer leaves, its awareness state is dropped locally.

## 9. Rooms and the lifecycle

- A room is minted by a connection that arrives without `room` in its URL; that connection
  is the **host**. Guests join with the room id and token.
- The **token is the permission** (`DESIGN.md` §5). Any holder may join; there is no
  per-join approval. The token is secret: it is in the invite URL and nothing else.
- **Exactly one host connection at a time.** A joining connection that claims
  `role: "host"` while a host is present is refused with `host_present`, and the room is
  untouched.
- When the host's connection ends the room enters a grace period of `grace_ms`
  (`room_grace`, 30 s in the reference server). Remaining peers receive `host.detached`.
  During grace the room is fully usable: guests keep syncing with each other, the
  open-document set survives, and a connection claiming `role: "host"` with the right token
  reclaims the room, announced to the others as `host.attached`.
- If the grace period expires with no host, the room is destroyed: remaining peers receive
  `room.gone` and are then closed with code 4003. The room id is gone for good — a later
  join attempt is `room_unknown`, not a fresh room.
- A guest disconnecting produces `peer.left` and nothing else.
- Keepalive: the server sends a WebSocket Ping every `ping_interval_ms` (30 s). Clients must
  answer with a Pong (WebSocket libraries do this for you). Protocol-level pings are not
  session messages and are never relayed.

## 10. Version and capability negotiation

- The wire version is `selvage/1` and appears in every text frame as `v`. It must not be
  omitted, and an incompatible value is refused at the handshake with
  `unsupported_version` (close code 4005). Version is also checked on every later request;
  an incompatible one is answered with an error and the connection is closed.
- **Compatibility rule** (`DESIGN.md` §4.6): same major, and while at `0.x` also the same
  minor. `selvage/1` therefore accepts only `selvage/1`.
- Capabilities are advertised additively by the server in `room.created`/`room.joined` and
  in `/meta`, and optionally by the client in `session.hello`. **Unknown capabilities and
  unknown fields are ignored by both sides.** There is no failure mode for an unknown
  capability, and no way for a client to require one — see §12.

## 11. Errors

Machine-readable codes in `error.code` and `session.error.params.code`:

| code | meaning | closes the connection? |
|---|---|---|
| `unknown_method` | no such method | no |
| `bad_message` | not a session envelope / no `id` / undecodable | no |
| `bad_params` | method params missing or malformed | no |
| `hello_required` | first frame is not `session.hello`, or none arrived in time | yes, 4000 |
| `unsupported_version` | version refused | yes, 4005 |
| `room_unknown` | no such room (never minted, or destroyed) | yes, 4001 |
| `token_invalid` | room present, token absent or wrong | yes, 4002 |
| `room_gone` | the room was destroyed under a seated connection | yes, 4003 |
| `host_present` | a host is already connected | yes, 4004 |
| `already_seated` | `session.hello` sent twice | no |
| `doc_not_open` | reserved; not produced by this slice | — |

Close codes live in the private-use range: 4000 `protocol_error`, 4001 `room_unknown`,
4002 `token_invalid`, 4003 `room_gone`, 4004 `host_present`, 4005 `unsupported_version`.
A refusal sends `session.error` **and then** a close with the matching code, so a client
that does not read close frames still learns why.

## 12. Open questions

Decisions this implementation had to make that `DESIGN.md` does not settle. Each one is a
candidate for the ratified spec, and each is currently pinned by code rather than by
agreement.

1. **Room and token in the URL vs. in the handshake.** `DESIGN.md` §5 says "room id plus an
   invite token in the URL plus a display name typed by the user"; this draft takes that
   literally: room and token in the query string, display name in `session.hello`. The cost
   is that a token in a URL leaks into logs, history and referrers. The alternative —
   everything in `session.hello` — makes the invite link a non-URL, or makes the client
   compose it. **Unresolved.**
2. **The host role is claimed, not proven.** Any holder of the token may send
   `role: "host"` and, when the room is hostless, become the host: keeping the room alive,
   or ending it by leaving. Since the same token already grants read access to the whole
   working copy, this grants nothing new *today* — but it stops being harmless the moment
   the token is shared more widely than the host's devices. **Unresolved.**
3. **Read-only guests are not implemented.** `DESIGN.md` §4.2 has the host serving content
   and guests reading it. This slice has both roles editing, because the conformance gate
   requires concurrent edits from both sides. Enforcing read-only at the server would mean
   parsing CRDT operations, which contradicts payload opacity (§3); it has to be host-side
   or not at all. **Unresolved, and deliberately deferred.**
4. **Selections are character offsets, not CRDT-relative positions.** `DESIGN.md` §4.3 asks
   for selections anchored to relative positions. `{ "anchor": 11, "head": 13 }` is what is
   implemented, which drifts when edits land earlier in the document. The fix is a sticky
   index (`yrs::StickyIndex` / yjs relative positions) encoded as bytes in the state.
   **Known gap.**
5. **Where does an awareness client id belong?** The session layer carries
   `awareness_client_id` so that a cursor can be attributed to a display name without
   putting identity into awareness (§8.4). This is one reading of "identity travels in the
   peer/session layer"; an alternative is for the awareness state to name a `peer_id`.
   **Unresolved.**
6. **No directed messages.** Binary frames are broadcast to the rest of the room. There is
   no way to address one peer, which the host-seeded document flow ("the host serves this
   path to that requester") would want. Adding it means a target field in the frame.
   **Unresolved.**
7. **No per-document request/response.** A guest's `doc.open` is informational: content
   arrives because the single session `Y.Doc` syncs as a whole, not because the host was
   asked for that path. Whether the spec wants a real "serve me this path" exchange —
   needed once content can be large, or lazily fetched, or access-controlled — is
   **unresolved.**
8. **Open-document paths are unvalidated.** `path` is an opaque string. `DESIGN.md`'s folder
   grant, exclude globs (`.env`, `.git/**`), and path clamping are not implemented, and are
   harmless only while nothing reads the host's filesystem. **Must be settled before
   file access exists.**
9. **Do capabilities ever gate behaviour?** Today they are pure advertisement: unknown ones
   are ignored and a client cannot insist on one. If a profile ever becomes mandatory, the
   spec needs a failure mode other than "unknown is ignored". **Unresolved.**
10. **Lifecycle of the open-document set.** Settled for this draft in §5: the set belongs
    to the room and outlives any peer, a hold belongs to one connection, and a path leaves
    the set when the last peer holding it open closes it. Two smaller questions remain
    **unresolved**: whether a disconnect should drop the paths that only the departing peer
    held (today it does not, so a path can outlive every peer that opened it), and whether
    a reconnecting host should re-open its documents explicitly rather than inheriting the
    set.
11. **No `session.leave`.** A client leaves by closing the WebSocket. There is no graceful
    goodbye message and no way to detach from a room while keeping the connection.
    **Unresolved**, and cheap to add if a client ever needs it.
12. **Room id and token formats are unspecified.** The reference server mints
    `r-<12 hex>` and 32 hex characters, and treats both as opaque. A spec could pin a
    format (a client may want to validate an invite link before connecting) or leave it
    opaque. **Unresolved.**
13. **Awareness expiry applies to remote states only**, and no test runs it at the
    y-protocols defaults: `crates/harness/tests/awareness.rs` compresses the window to a
    40 ms renewal and a 250 ms expiry so the check costs no CI time. The 15 s / 30 s pair is
    advertised, not exercised. A client that never renews is expired by its peers and not by
    the server — the server keeps no awareness state at all. **Values are unresolved; the
    mechanism is tested at a compressed scale.**
14. **HTTP `/meta` is hand-rolled** on the WebSocket listener: no keep-alive, no `HEAD`, no
    routing. Fine for negotiation; it should be replaced, not extended, if a real HTTP
    surface is ever needed. **Unresolved by design.**
15. **`y-protocols` message types 2 (auth) and 3 (awareness query)** are accepted and
    unused. Whether `selvage/1` should *forbid* them, or keep them available for a future
    profile, is **unresolved.**
16. **The second client implementation is not written.** `DESIGN.md` §14.2 defers the
    decision whether the conformance harness's second client should be an independent
    implementation. This draft exists so that it can be: everything in `impl/crates/protocol`
    and `impl/crates/client` is meant to be replaceable without touching the server.
