# Notes on the implementations, decisions and open questions

**Informative.** Nothing in this document binds a conforming implementation. It records what the
implementations in this project do where [`PROTOCOL.md`](PROTOCOL.md) does not bind them, what this
draft had to decide, and what is still undecided. The specification is `PROTOCOL.md`; where this
document and that one disagree, this one is wrong.

Part **A** is implementation status. Part **B** is one item per decision or open question, numbered
`B.1`… so that `PROTOCOL.md` and the schemas can cite one (`PROTOCOL.md` §5.1 cites `B.17`, and
`schema/common.json` cites `B.8`).

The implementations this document describes:

- [`selvage-protocol/reference_server`](https://github.com/selvage-protocol/reference_server) —
  `crates/selvaged` (the server), `crates/client` (the Rust client), `crates/protocol` (the wire
  types) and `crates/harness` (the tests `PROTOCOL.md`'s requirements were observed on).
- [`selvage-protocol/vscode_client`](https://github.com/selvage-protocol/vscode_client) — the VS
  Code client, engine and adapter.
- [`selvage-protocol/nvim_client`](https://github.com/selvage-protocol/nvim_client) — the Neovim
  client: a Lua front-end and a Node companion that drives its own vendored copy of the same
  engine. The two copies are byte-identical, so "the TypeScript client" below is both of them.

---

## A. Notes on the reference implementation

### A.1 The server's numbers and the corpus that pins them

`PROTOCOL.md` §2.1 carries the table of bounds. The rows a reader is most likely to be caught by:

- The **16 MiB frame and 64 MiB message** bounds are not configured by the server at all: it takes
  `tokio-tungstenite`'s default `WebSocketConfig`, so what it enforces is the library's default and
  a dependency bump moves it. Exceeding one is a stream error, which the connection loop treats as
  "the socket ended" — no `session.error` and no session close code. Whether the server should pin
  the config explicitly rather than inherit it is **open** (`B.18`).
- The **10 s hello timeout** is not advertised anywhere: `/meta`'s `keepalive` object carries the
  ping and awareness clocks, and no client can learn how long it has to send `session.hello`. That
  is the one bound a second implementation most needs, and the only negotiation surface the
  protocol has does not carry it.
- The **awareness window** is 15 s / 30 s by default, and the conformance harness advertises 40 ms
  and 250 ms instead so that expiry is tested in under a second. The vectors' `harness.room_grace_ms`
  overrides the grace period per transcript (400 ms in `vectors/012`, four seconds in `011`).

### A.2 The Rust client

- Bounded reconnection: 500 ms doubling to a 10 s ceiling, five attempts, and **no retry on the
  first connection**. `PROTOCOL.md` §9.1 asks only for the shape — a bounded retry, an observable
  give-up, no retry of a refusal — and these numbers are policy.
- **No per-request timeout.** The client bounds the handshake (`HANDSHAKE_TIMEOUT`) and nothing
  else: a server that holds the socket open and never answers leaves a caller waiting. The
  TypeScript client bounds the wait at ten seconds, so the two clients differ where `PROTOCOL.md`
  §5 only says SHOULD.
- **The outbound queue is unbounded.** `engine.rs` queues frames in a `VecDeque` and drains them
  when the socket is writable; a peer that reads slowly grows it without limit. `PROTOCOL.md` §2.1
  therefore states the client-side bound as a SHOULD, and that level is a settled decision rather
  than an oversight (`B.20`).
- A reconnecting client seeds a **new `Y.Doc`** carrying the outgoing replica's state on every
  handshake, so it never reuses an awareness client id.
- The client's request surface is the handshake, `doc.open` and `doc.close`; see `A.4`.

### A.3 The TypeScript client (VS Code, and the Neovim companion)

- Bounded reconnection matches the Rust policy: 500 ms doubling to a 10 s ceiling, five attempts,
  no retry on the first connection.
- Bounds a request at ten seconds and fails every pending request when the socket goes.
- **The outbound queue is unbounded** (a `QueuedFrame[]` drained when the socket opens).
- Rotates its awareness client id on every reconnect (`rotateIdentity` before `session.hello`),
  dropping the outgoing id's local state.
- **Removes a departed peer's awareness state on `peer.left`** (`removeAwarenessStates`). The Rust
  client does not: it leaves the state to expire on the advertised clock, which is why
  `PROTOCOL.md` §8.4 states this as a SHOULD.

### A.4 The `x.` method surface

`PROTOCOL.md` §10.1 says the wire is open: a server answers any method it does not implement with
`unknown_method`, so an extension method travels like any other. A *client* is the constraint —
both reference clients can send the handshake, `doc.open` and `doc.close` and nothing else, so an
editor adapter behind them cannot ask for an `x.` method at all. An implementation that defines one
has to expose a way to send a method whose params it does not interpret; neither reference client
has made that decision. An `x.` **event** needs nothing of the sort, because events reach a client
that has already promised to ignore the ones it does not know.

### A.5 `GET /meta`

Hand-rolled on the WebSocket listener: no keep-alive, no routing, and the request method is not
inspected, so `GET`, `POST` and `HEAD /meta` all answer `200` with the same body. Answering `HEAD`
with a body deviates from RFC 9110. It is fine for negotiation, and it should be replaced rather
than extended if a real HTTP surface is ever needed (`B.14`). The body is written in canonical
member order (`CANONICAL.md` §2.1), which the server's `Meta` struct achieves by declaring its
members in ascending order.

### A.6 Reconnection status, and what it costs

Both clients implement the bounded policy of `PROTOCOL.md` §9.1 and both rotate their awareness
client ids, so a reconnecting peer is never mistaken for the peer it replaces. What no client can
express is the retry itself: nothing is emitted while a reconnect is being attempted, so an adapter
that wants to show "reconnecting…" has to infer it from the silence (`PROTOCOL.md` §9.1, *Known
gap*).

### A.7 The second client

`nvim_client` exists and is a second *editor* client, but it drives a byte-identical vendored copy
of the same TypeScript engine rather than an independent implementation. What `DESIGN.md` §14.2
defers — whether the conformance harness should carry a genuinely independent second client — is
still undecided, and this draft exists so that it can be one.

### A.8 Where a decision's provenance is recorded

The project keeps an unpublished design record (`DESIGN.md`) that some of these decisions were
taken in. Citations to it are kept here rather than in `PROTOCOL.md`, because a reader of the
specification cannot obtain it: `PROTOCOL.md` §1's profile reservation (`DESIGN.md` §4.1 — the
`terminal/1` name and the "named optional profile" shape), §10's compatibility rule (§4.6), and
§10.1's `x.` namespace (§4.7). The rest of the design's reasoning is inline in `PROTOCOL.md` or in
`B` below.

---

## B. Decisions and open questions

Each item was decided by the implementation if it says **decided**, and is a candidate for
ratification; **unresolved** items are marked as such and none of them is a requirement. Items are
numbered so that `PROTOCOL.md` and the schemas can cite one.

**B.1 Room and token in the URL vs. in the handshake.** The invitation is the WebSocket URL, with
room and token in the query string and the display name in `session.hello`. The cost is that a
token in a URL leaks into logs, history and referrers (`PROTOCOL.md` §12). The alternative —
everything in `session.hello` — makes the invite link a non-URL, or makes the client compose it.
**Unresolved.**

**B.2 The host role is claimed, not proven.** Any holder of the token may send `role: "host"` and,
when the room is hostless, become the host: keeping the room alive, or ending it by leaving. Since
the same token already grants read access to the whole working copy, this grants nothing new
*today* — but it stops being harmless the moment the token is shared more widely than the host's
devices. **Unresolved.**

**B.3 Read-only guests are not implemented.** The design has the host serving content and guests
reading it. This slice has both roles editing, because the conformance gate requires concurrent
edits from both sides. Enforcing read-only at the server would mean parsing CRDT operations, which
contradicts payload opacity (`PROTOCOL.md` §3); it has to be host-side or not at all.
**Unresolved, and deliberately deferred.**

**B.4 Selections are published with `assoc: 0`.** A selection endpoint is a yjs `RelativePosition`
object, no index reaches the wire, and offsets are local to a client's adapter seam. Both reference
clients publish `0` for both endpoints of a selection, and `PROTOCOL.md` §8.1 requires it. `0` is
yjs's own default and what `y-protocols` awareness carries, and `-1` would change what the
scope-only anchor means: at the end of a text a caret is a scope with no element, and `assoc: 0`
makes it follow every append forever, while `-1` binds it to the last character and would leave a
caret behind when a peer appends. The trade being accepted is that an insertion landing exactly on
the selection's **right** edge **extends** the selection — the endpoint is bound to the element
after it, so the inserted text falls inside — while an insertion on the **left** edge leaves the
selection outside it, since the same rule binds that endpoint forward too. A publisher that wants
the other policy on one edge publishes `-1` for it itself; nothing about the shape prevents that,
and `CANONICAL.md` §2.4 says so. Undo interop (yjs's `followUndoneDeletions`, which it recommends
leaving `false` for shared positions) is out of scope for `selvage/1`.

**B.5 Where does an awareness client id belong?** The session layer carries `awareness_client_id`
so that a cursor can be attributed to a display name without putting identity into awareness
(`PROTOCOL.md` §8.4). This is one reading of "identity travels in the peer/session layer"; an
alternative is for the awareness state to name a `peer_id`. **Unresolved.**

**B.6 No directed messages.** Binary frames are broadcast to the rest of the room. There is no way
to address one peer, which the host-seeded document flow ("the host serves this path to that
requester") would want. Adding it means a target field in the frame. **Unresolved.**

**B.7 No per-document request/response.** A guest's `doc.open` is informational: content arrives
because the single session `Y.Doc` syncs as a whole, not because the host was asked for that path.
Whether the protocol wants a real "serve me this path" exchange — needed once content can be large,
or lazily fetched, or access-controlled — is **unresolved.**

**B.8 Open-document paths are unvalidated.** `path` is an opaque string, and `PROTOCOL.md` §5 only
requires it to be non-blank (`trim()` non-empty, and the schema's `documentPath` says the same in
its description; the JSON Schema itself is a `minLength` and cannot express "non-blank"). The
design's folder grant, exclude globs (`.env`, `.git/**`) and path clamping are not implemented, and
are harmless only while nothing reads the host's filesystem. `PROTOCOL.md` §12 says what an
implementation that does read it owes. **Must be settled before file access exists.**

**B.9 Do capabilities ever gate behaviour?** Today they are pure advertisement: unknown ones are
ignored and a client cannot insist on one. If a profile ever becomes mandatory, the protocol needs
a failure mode other than "unknown is ignored". **Unresolved.**

**B.10 Lifecycle of the open-document set.** Settled for this draft in `PROTOCOL.md` §1.2 and §5:
the set belongs to the room and outlives any peer, a hold belongs to one connection, and a path
leaves the set when the last peer holding it open closes it. The second half of the old question —
whether a reconnecting host should re-`doc.open` its documents rather than inheriting the set — is
**settled by `PROTOCOL.md` §9.1**: it should, and both clients do. One smaller question remains
**unresolved**: whether a disconnect should drop the paths that only the departing peer held (today
it does not, so a path can outlive every peer that opened it).

**B.11 No `session.leave`.** A client leaves by closing the WebSocket. There is no graceful goodbye
message and no way to detach from a room while keeping the connection. **Unresolved**, and cheap to
add if a client ever needs it.

**B.12 Room id and token formats are unspecified.** The reference server mints `r-<12 hex>` and 32
hex characters, and treats both as opaque. A spec could pin a format — a client may want to
validate an invite link before connecting — or leave it opaque. **Unresolved.**

**B.13 Awareness expiry applies to remote states only.** A client that never renews is expired by
its peers and not by the server, which keeps no awareness state at all. The 15 s / 30 s pair is
advertised, not exercised: the tests compress the window (a 40 ms renewal and a 250 ms expiry) so
the check costs no CI time, once on the client's own clock and once on the clock the server
advertises. **Values are unresolved**; the mechanism and the clock are settled and tested at a
compressed scale.

**B.14 HTTP `/meta` is hand-rolled.** See `A.5`. Fine for negotiation; it should be replaced, not
extended, if a real HTTP surface is ever needed. **Unresolved by design.**

**B.15 `y-protocols` message types 2 (auth) and 3 (awareness query)** are never sent by either
implementation — a query a client receives is answered, and an auth message is ignored
(`PROTOCOL.md` §8.3). Whether `selvage/1` should *forbid* them, or keep them available for a future
profile, is **unresolved**.

**B.16 The second client implementation.** See `A.7`. **Unresolved.**

**B.17 A token with no `room` mints a room.** `PROTOCOL.md` §5.1 states the behaviour: a URL that
carries `token` but no `room` mints a new room and discards the token, because `room` alone decides
which case a URL is. The consequence is that a truncated invite link — `room` lost to a chat
client, a proxy or a copy-paste, `token` surviving — silently hosts an empty room instead of
refusing, and neither side is told. The alternative is a `token_invalid` refusal whenever `token`
appears without `room`, which is a behaviour change in the server and a decision, not a wording
fix. **Unresolved; the current behaviour is documented so that a second implementation can match
it.**

**B.18 The server inherits the WebSocket library's size caps.** See `A.1`. The bound exists, is
documented in `PROTOCOL.md` §2.1, and is not the server's own: it will move with a dependency bump
unless the server configures it explicitly. **Unresolved.**

**B.19 Where the reference client's request surface ends.** See `A.4`: the client can express
`session.hello`, `doc.open` and `doc.close` and nothing else, so no `x.` method can be sent through
it. **Unresolved**, and a client-API decision rather than a wire one.

**B.20 A client's outbound bound is a SHOULD, deliberately.** `PROTOCOL.md` §2.1 asks a client to
bound what it holds rather than queue without limit, and states it as a SHOULD. **Decided**: the
level stays a SHOULD. All three implementations queue without bound — a `VecDeque` drained only
when the socket is writable in the Rust client (`crates/client/src/engine.rs`), a `QueuedFrame[]`
drained when the socket opens in the TypeScript engine, and the Neovim companion's byte-identical
vendored copy of it — so a MUST here would be a rule all three violate, and a rule no
implementation honours teaches a reader to distrust the rest. Raising the level is not a wording
change: it means bounding all three clients *and* deciding what a client does when it reaches the
bound — fail the session, or stop committing CRDT transactions until there is room — which is a
wire-visible decision this draft does not take. It becomes a MUST when those two things exist:
every conforming client bounds what it holds, and the specification states the observable
consequence at the bound.
