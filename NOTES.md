# Notes on the implementations, decisions and open questions

**Informative.** Nothing in this document binds a conforming implementation. It records what the
implementations in this project do where [`PROTOCOL.md`](PROTOCOL.md) does not bind them, what this
draft had to decide, and what is still undecided. The specification is `PROTOCOL.md`; where this
document and that one disagree, this one is wrong.

Part **A** is implementation status. Part **B** is one item per decision or open question, numbered
`B.1`… so that `PROTOCOL.md` and the schemas can cite one (`PROTOCOL.md` §5.1 cites `B.17`, and
`schema/common.json` cites `B.8`).

The implementations this document describes:

- [`selvage-protocol/reference_server`](https://github.com/selvage-protocol/reference_server):
  `crates/selvaged` (the server), `crates/client` (the Rust client), `crates/protocol` (the wire
  types) and `crates/harness` (the tests `PROTOCOL.md`'s requirements were observed on).
- [`selvage-protocol/vscode_client`](https://github.com/selvage-protocol/vscode_client): the VS
  Code client, engine and adapter.
- [`selvage-protocol/nvim_client`](https://github.com/selvage-protocol/nvim_client): the Neovim
  client: a Lua front-end and a Node companion that drives its own vendored copy of the same
  engine. The two copies are byte-identical, so "the TypeScript client" below is both of them.

---

## A. Notes on the reference implementation

### A.1 The server's numbers and the corpus that pins them

`PROTOCOL.md` §2.1 carries the table of bounds. The rows a reader is most likely to be caught by:

- The **8 MiB frame and 8 MiB message** bounds are the server's own configured values, not the
  WebSocket library's defaults. Exceeding one is a stream error, which the connection loop treats as
  "the socket ended": no `session.error` and no session close code. Whether to pin
  them explicitly is **settled** for these two (`B.18`).
- The **10 s hello timeout** is not advertised anywhere: `/meta`'s `keepalive` object carries the
  ping and awareness clocks, and no client can learn how long it has to send `session.hello`. That
  is the one bound a second implementation most needs, and the only negotiation surface the
  protocol has does not carry it.
- The **awareness window** is 15 s / 30 s by default, and the conformance harness advertises 40 ms
  and 250 ms instead so that expiry is tested in under a second. The vectors' `harness.room_grace_ms`
  overrides the grace period per transcript (400 ms in `vectors/012`, four seconds in `011`).
- The **room's peer cap is the one number a vector depends on and cannot set.** `vectors/026` pins
  the refusal of a join to a full room by filling a room to the reference server's default cap of
  128 peers, because `harness` carries `room_grace_ms` alone: the server takes
  `--max-peers-per-room`, and neither replay passes it from a transcript. The transcript that fills
  the room is quadratic as well — every join is announced to every peer already seated, so the
  corpus grows by the square of the cap, and this one vector is most of the corpus and most of what
  `schema/validate.py` spends its time on. It is therefore both the corpus's cost centre and the one
  vector a second implementation cannot replay if its cap is not 128. A `harness` knob for the cap
  would let the same claim be pinned with three peers.
- The **envelope bound and the room-state caps** are the bounds the corpus cannot reach at all, and
  the ones a second implementation is most likely to get wrong because they are not all refusals of
  the same kind: a text envelope past `--max-envelope-bytes` (5 MiB) is refused `bad_message` with
  the connection left open, where a frame past the transport's 8 MiB bound is a drop with nothing on
  the wire; a mint past `--max-rooms` and a join past `--max-peers-per-room` are `x.server_full` and
  `x.room_full`, and a connection past `--inbound-bytes-per-sec` / `--inbound-burst-bytes`
  is told `x.rate_limited` and closed 1013. `PROTOCOL.md` §2.1's table carries the numbers and the
  codes; `crates/harness/tests/session.rs` and `bounds.rs` pin the shapes. The corpus cannot, because
  `harness` carries `room_grace_ms` alone (`B.29`). The caps a room's *contents* took — the paths in
  an open-document set and the paths in a grant — are gone with the server holding neither; a
  listing's three (4096 bytes to a path, 100 000 paths, 4 MiB of path bytes) are a receiver's bound
  now (`PROTOCOL.md` §13.3).

### A.2 The Rust client

- Bounded reconnection: 500 ms doubling to a 10 s ceiling, five attempts, and **no retry on the
  first connection**. `PROTOCOL.md` §9.1 asks only for the shape (a bounded retry, an observable
  give-up, no retry of a refusal), and these numbers are policy.
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
- The client's request surface is the handshake and `session.rename`;
  see `A.4`.

### A.3 The TypeScript client (VS Code, and the Neovim companion)

- Bounded reconnection: 500 ms doubling to a 10 s ceiling, with the **attempt budget sized from the
  server's advertised `room_grace_ms`** (`/meta`, §2) so the cumulative backoff spans the grace the
  room actually has: seven attempts, ≈35 s, for the 30 s default. `PROTOCOL.md` §9.1 asks for the
  shape (a bounded retry, an observable give-up, no retry of a refusal), and these numbers are
  policy. A server that advertises no grace (or an unreachable `/meta`) falls back to the previous
  five attempts, and a caller's own `maxAttempts` still wins; the budget is capped at about an hour
  of backoff rather than retrying forever. The Rust client predates the advertised grace and still
  uses the flat five attempts (§A.2), so the two engines currently differ here.
- Bounds a request at ten seconds and fails every pending request when the socket goes.
- **The outbound queue is unbounded** (a `QueuedFrame[]` drained when the socket opens).
- Rotates its awareness client id on every reconnect (`rotateIdentity` before `session.hello`),
  dropping the outgoing id's local state.
- **Removes a departed peer's awareness state on `peer.left`** (`removeAwarenessStates`). The Rust
  client does the same in `peer_left`, so both clients drop a departed peer's state rather than
  leave it to expire on the advertised clock (`PROTOCOL.md` §8.4).
- **An incoming awareness query is dropped, not answered** (`MESSAGE_QUERY_AWARENESS` in
  `src/engine/sync.ts`), and a frame of query messages draws no answer at all. `PROTOCOL.md` §8.3
  allows that and bounds the answering form, because answering one per message made a legal 256 KiB
  frame of one-byte query messages cost 256 000 replies carrying the whole awareness set. The Rust
  client still answers through `yrs`'s own protocol handler — one reply per query message in the
  frame, with no per-frame cap — so the two engines differ here and the Rust one carries the
  amplification.
- **Its outgoing frames are not in SJ-C member order.** The envelope is serialised as
  `{v, id, method, params}`, and `session.hello`'s params as
  `{display_name, role, awareness_client_id, capabilities, client}`, because the engine hands
  `JSON.stringify` a plain object. `CANONICAL.md` §2.1 makes ascending member order a producer rule
  for requests as for events; a receiver must tolerate any order (§4), and the corpus compares only
  a server's frames, so nothing in this repository catches it.
- **It permits more than one request in flight.** `pending` is a map keyed by request id and each
  request goes out as it is made, and `test/engine.test.ts` pins two in flight together, each
  answered by its own id. `PROTOCOL.md` §5 makes one request at a time a MUST, because a seated
  `session.error{bad_message}` carries no `id` and a client that pipelined cannot tell which request
  it sank; the engine emits a `sessionError` event and leaves whatever is outstanding to its
  ten-second timeout rather than failing it. The Rust client queues instead (`waiting` and
  `inflight`), so the two engines differ here as well.

### A.4 The `x.` method surface

`PROTOCOL.md` §10.1 says the wire is open: a server answers any method it does not implement with
`unknown_method`, so an extension method travels like any other. A *client* is the constraint:
both reference clients can send the handshake and `session.rename` and nothing else, so an editor
adapter behind them cannot ask for an `x.` method at all.
An implementation that defines one has to expose a way to send a method whose params it does not
interpret; neither reference client has made that decision. An `x.` **event** needs nothing of the
sort, because events reach a client that has already promised to ignore the ones it does not know.

### A.5 `GET /meta`

Hand-rolled on the WebSocket listener: no keep-alive and no routing. `GET /meta` answers the body;
`HEAD /meta` answers the same status line and headers, the body's `content-length` among them, with
nothing after them, which is what RFC 9110 §9.3.2 asks for; and any other method is
`405 Method Not Allowed` with `allow: GET, HEAD`. It is fine for negotiation, and it should be
replaced rather than extended if a real HTTP surface is ever needed (`B.14`). The body is written in
canonical member order (`CANONICAL.md` §2.1), which the server's `Meta` struct achieves by declaring
its members in ascending order. With `--serve-page` the same listener serves a static page from
`GET /` and every other plain path, on the same origin as `/session` and `/meta`, which is what
makes a page-link invite (§5.1) open in a browser; that page is a deployment's and not this
protocol's (`PROTOCOL.md` §2).

### A.6 Reconnection status, and what it costs

Both clients implement the bounded policy of `PROTOCOL.md` §9.1 and both rotate their awareness
client ids, so a reconnecting peer is never mistaken for the peer it replaces. Both reference
engines now emit a local `reconnecting` event while a retry is in flight, so an adapter does not
have to infer it from the silence; what remains a wire gap is the retry itself, because
`selvage/1`'s event vocabulary has no name for it (`PROTOCOL.md` §9.1, *Known gap*).

### A.7 The second client

`nvim_client` exists and is a second *editor* client, but it drives a byte-identical vendored copy
of the same TypeScript engine rather than an independent implementation. What `DESIGN.md` §14.2
defers (whether the conformance harness should carry a genuinely independent second client) is
still undecided, and this draft exists so that it can be one.

### A.8 Where a decision's provenance is recorded

The project keeps an unpublished design record (`DESIGN.md`) that some of these decisions were
taken in. Citations to it are kept here rather than in `PROTOCOL.md`, because a reader of the
specification cannot obtain it: `PROTOCOL.md` §1's profile reservation (`DESIGN.md` §4.1, the
`terminal/1` name and the "named optional profile" shape) and §10.1's `x.` namespace (§4.7). The
rest of the design's reasoning is inline in `PROTOCOL.md` or in `B` below.

### A.9 The one value a `v` may name

`PROTOCOL.md` §10 says `v` has one value, `selvage/2`, and that a frame naming anything else is an
envelope the receiver does not read: `bad_message`, refused before seating and an event on a seated
connection. The reference server reads it the same way and nothing wider:
`crates/protocol/src/lib.rs`'s `speaks` is `version == WIRE_VERSION`, so `selvage/2.0`, `selvage/2.9`,
`selvage/1`, `selvage/3`, `selvage`, `selvage/03` and `selvage/x` are every one of them a version it
does not read. Nothing is matched by prefix and no major is compared, so no second spelling of the
one value is open between the two. The corpus pins both shapes the refusal takes: `vectors/005`
refuses `selvage/3`, `selvage/1`, `selvage`, `selvage/03` and a frame carrying no `v` before
seating, and `vectors/027` the same on a seated connection, which stays open.

---

## B. Decisions and open questions

Each item was decided by the implementation if it says **decided**, and is a candidate for
ratification; **unresolved** items are marked as such and none of them is a requirement. Items are
numbered so that `PROTOCOL.md` and the schemas can cite one.

**B.1 Room and token in the URL vs. in the handshake.** The invitation is the WebSocket URL, with
room and token in the query string and the display name in `session.hello`. The cost is that a
token in a URL leaks into logs, history and referrers (`PROTOCOL.md` §12). The alternative, with
everything in `session.hello`, makes the invite link a non-URL, or makes the client compose it.
**Unresolved.**

**B.2 The host role is claimed, not proven.** Any holder of the token may send `role: "host"` and,
when the room is hostless, become the host: keeping the room alive, or ending it by leaving. Since
the same token already grants read access to the whole working copy, this grants nothing new
*today*, but it stops being harmless the moment the token is shared more widely than the host's
devices. **Unresolved. Superseded 2026-09-22 by owner decision**: reclaiming is dropped, so the
host role is not claimable at all — the host is whoever holds the private half of the room's host
keypair, whose public half travels in the invite URL's fragment, and a host that returns inside the
blip window proves it with a signature its peers verify rather than with a token the server checks.
What survives here is only the part about identity and the link, which is `B.1`'s subject.

**B.3 Read-only guests: the `viewer` role, endpoint-side.** The design has the host serving content
and guests reading it, and this slice has both roles editing, because the conformance gate requires
concurrent edits from both sides. What is settled is **where enforcement lives**: the server never
inspects a payload, and `PROTOCOL.md` §12 is being revised toward an encrypted shape in which the
server will not be able to read a payload at all, so the enforcement the design wants is not a thing
the server can perform. It is endpoint-side instead — a client denies a change that came from a
viewer — and the role it denies by is **`viewer`**, beside `host` and `guest`. The next revision
carries it the way it carries every other room fact, which is a change from what this item first
recorded: **the host assigns it and signs it**, in the room state the host publishes, so no server
seats it and `session.hello`'s `role` member, `PeerInfo.role` and `/meta`'s `roles` all go. The
invite still carries it as a parameter, so a conforming client knows what it is and does not try to
edit; that half is a convention between the host's client and the client that joins, and it is not
what enforces anything.

**What the wire is missing is attribution.** A relayed binary frame is byte for byte with no sender:
the server relays and forgets (`PROTOCOL.md` §7, whose message-type table has no origin), and the
reference transport hands its consumer the bytes and nothing else
(`vscode_client/src/engine/transport.ts`). A receiver therefore cannot tell whose change it is
holding, and "a client denies a viewer's changes" cannot be implemented until a frame says where it
came from. The smallest shape that supplies it is a **per-sender signature in the frame envelope,
over an untouched payload** — bytes this protocol writes, not bytes `y-protocols` writes, so a later
sync algorithm is attributed the same way and the payload stays opaque. A server-written stamp is the
cheaper shape and it is **not** this one: under the threat the encryption is being built for, a stamp
the server writes is a value the server can lie about, and a frame's sender has to be something a
peer verifies for itself.

**Where the two `MUST`s this item asked for are now.** They are normative: a frame carries a
signature verifiable against a key the room's host has committed (`PROTOCOL.md` §7.1,
[`CANONICAL.md`](CANONICAL.md) §6.1), and a client **MUST NOT** apply content from a sender it cannot
authenticate, nor document content from a sender the host's state commits as `viewer` (§13.2,
§13.4, §13.5), reporting the second `unauthorised_content`. Nothing implements either yet — no client
speaks `selvage/2` — so what they still lack is a vector, which is **step 4b** of
`docs/studies/e2ee-plan.md` §11.

**The residual, stated as it is.** Even with both, the honest sentence is "no conforming client
applies a viewer's content", not "a viewer cannot edit": a viewer holds the room key once there is
one, so it can produce a perfectly signed frame, and a client that does not implement the rule
applies it. Endpoint enforcement is a denial between conforming peers, not a boundary.

**The accepted cost of the next revision, in this item's own terms.** Verifying a sender costs every
client a verification state machine and a key exchange that is not a person's, which is the one place
the project's "a stranger can implement a client from the spec" property is genuinely strained
(`docs/studies/e2ee-plan.md` §8). It buys what this item wanted without asking anything of the server:
a role a peer cannot forge and a sender no relay can mislabel.

**Not implemented, and what triggers it.** The role is specified and unimplemented. `viewer` is a
value in what the host signs and assigns ([`CANONICAL.md`](CANONICAL.md) §6.1), and a rule a receiver
refuses document content by (`PROTOCOL.md` §13.5); what the invite says about it is a convention
between the host's client and the client that joins rather than a wire rule (§5.1 has a receiver
ignore an unknown query parameter, which is the whole of what such a parameter can be), and a client
learns its own role authoritatively from the state. It is implemented when the revision's
implementations land; until then `DESIGN.md` §4.2's inversion stands in full — every holder of the
invite token edits the session CRDT — and the page's claim gate keeps refusing "guests are read-only"
for the reason it gives (`site/README.md`).

**B.4 Selections are published with `assoc: 0`.** A selection endpoint is a yjs `RelativePosition`
object, no index reaches the wire, and offsets are local to a client's adapter seam. Both reference
clients publish `0` for both endpoints of a selection, and `PROTOCOL.md` §8.1 requires it. `0` is
yjs's own default and what `y-protocols` awareness carries, and `-1` would change what the
scope-only anchor means: at the end of a text a caret is a scope with no element, and `assoc: 0`
makes it follow every append forever, while `-1` binds it to the last character and would leave a
caret behind when a peer appends. The trade being accepted is that an insertion landing exactly on
the selection's **right** edge **extends** the selection (the endpoint is bound to the element
after it, so the inserted text falls inside), while an insertion on the **left** edge leaves the
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

**B.7 No per-document request/response.** A guest's `doc.open` is informational on the wire: no
frame asks the host for a path, and a document's content arrives because the single session
`Y.Doc` syncs as a whole. That mechanism is unchanged, and `B.23`'s grant does not touch it, since
a listing carries paths and never content. What the grant changes is *which* paths a client opens:
the room carries a listing of the host's working tree, a client mirrors its shape and fetches a
file's content only when something needs it (`DESIGN.md` §4.2), so a guest no longer inherits the
union of what its peers happen to hold open. The question this item left open (whether the
protocol wants a real "serve me this path" exchange) is **superseded rather than answered** for
`selvage/1`: a fetch stays a `doc.open` plus the ordinary sync exchange (§7) and no frame is
added, while content large enough to want one would reopen it in a later version. Both
reference clients now materialise the grant and fetch content lazily: the VS Code client holds
the listing its engine reports on `doc.granted` and opens a granted path on demand, while the
Neovim guest mirrors the listing into a directory and reads a file only when something opens it.

**B.8 Open-document paths are unvalidated.** `path` is an opaque string, and `PROTOCOL.md` §5 only
requires it to be non-blank and free of control characters. The first is `trim()` non-empty, which
the schema's `documentPath` describes and approximates with a `minLength` and a `\S` pattern; the
second is carried exactly, as a `not` around a pattern that matches a control character anywhere in
the string. Everything past those two is unvalidated. The
room's grant is no longer missing from the protocol: `PROTOCOL.md` §5 defines `doc.grant` and
`B.23` describes it, a listing of paths carrying that same non-blank, control-free rule and no
other, and both reference clients now speak it, with vectors 021 to 023 pinning the exchange. A
listing is not a confinement, and the three rules this item is about are implemented host-side,
where the design puts them, by `isGrantedPath` and the per-segment directory checks: a read is held
under the session's captured folders, the exclude globs (`.env`, `.git/**`) bind a peer-named path
as they bind the listing, the path is clamped to plain relative segments, and every directory on
the way to the file must itself be a plain directory of its folder. A peer may still open any path it
names, listed or not (the wire half is unchanged), but a host no longer reads unboundedly to
serve one: a guessed exclude or a path through a link is refused rather than read.
`PROTOCOL.md` §12 says what an implementation that does read it owes.

**B.9 Do capabilities ever gate behaviour?** Today they are pure advertisement: unknown ones are
ignored and a client cannot insist on one. If a profile ever becomes mandatory, the protocol needs
a failure mode other than "unknown is ignored". **Unresolved.**

**B.10 Lifecycle of the open-document set.** Settled for this draft in `PROTOCOL.md` §1.2 and §5:
the set belongs to the room and outlives any peer, a hold belongs to one connection, and a path
leaves the set when the last peer holding it open closes it. The second half of the old question,
whether a reconnecting host should re-`doc.open` its documents rather than inheriting the set, is
**settled by `PROTOCOL.md` §9.1**: it should, and both clients do. One smaller question remains
**unresolved**: whether a disconnect should drop the paths that only the departing peer held (today
it does not, so a path can outlive every peer that opened it).

**Revised 2026-09-24 by §B.45.** That remaining question is settled by the removal rather than
answered in prose, and it is no longer open: the set is the peers' own — the union of the seated
peers' live holds, an open path being a hold that a connection renews on a clock a receiver runs
(`PROTOCOL.md` §1.2, §13.7) — so a connection's holds are released when it ends and nothing a
departing peer held on its own outlives it. There is no server-held set left to ask the question
about.

**B.11 No `session.leave`.** A client leaves by closing the WebSocket. There is no graceful goodbye
message and no way to detach from a room while keeping the connection. **Unresolved**, and cheap to
add if a client ever needs it.

**B.12 Room id and token formats are unspecified.** The reference server mints `r-<12 hex>` and 32
hex characters, and treats both as opaque. A spec could pin a format (a client may want to
validate an invite link before connecting) or leave it opaque. **Unresolved.**

**B.13 Awareness expiry applies to remote states only.** A client that never renews is expired by
its peers and not by the server, which keeps no awareness state at all. The 15 s / 30 s pair is
advertised, not exercised: the tests compress the window (a 40 ms renewal and a 250 ms expiry) so
the check costs no CI time, once on the client's own clock and once on the clock the server
advertises. **Values are unresolved**; the mechanism and the clock are settled and tested at a
compressed scale.

**B.14 HTTP `/meta` is hand-rolled.** See `A.5`. Fine for negotiation; it should be replaced, not
extended, if a real HTTP surface is ever needed. **Unresolved by design.**

**B.15 `y-protocols` message types 2 (auth) and 3 (awareness query)** are never sent by either
implementation, an auth message is read and ignored, and a query may be ignored or answered at most
once for the frame it arrived in (`PROTOCOL.md` §8.3). Whether `selvage/1` should *forbid* them, or
keep them available for a future profile, is **unresolved**; so is whether a query should be
answered at all, now that the answering form is bounded — answering costs one frame per query frame
and gains a client nothing, since the three discovery steps of §8.3 are the whole story, while a
peer that speaks y-protocols without Selvage may ask one and get nothing.

**B.16 The second client implementation.** See `A.7`. **Unresolved.**

**B.17 A token with no `room` mints a room.** `PROTOCOL.md` §5.1 states the rule normatively: a URL
that carries `token` but no `room` mints a new room, seats the connection as its host and discards
the token, because `room` alone decides which case a URL is. **Decided**: the behaviour is kept,
and a second implementation matches it by doing the same (`claims_host = join.room.is_none()` is
the reference server's whole implementation of the rule). The alternative, a `token_invalid`
refusal whenever `token` appears without `room`, was rejected because a URL that names no room
cannot name the room the token is for, so refusing was defensible, but it changes the reference
server's wire behaviour and nothing in the project needs it. The accepted cost is the current
behaviour's: a truncated invite link (`room` lost to a chat client, a proxy or a copy-paste,
`token` surviving) silently hosts a new, empty room and neither side is told.

**B.18 The server pins its own WebSocket size caps.** See `A.1`. The frame and message bounds
documented in `PROTOCOL.md` §2.1 are the server's own configured values, not the WebSocket library's
defaults. **Decided.**

**B.19 Where the reference client's request surface ends.** See `A.4`: the client can express
`session.hello`, `doc.open`, `doc.close`, `session.rename` and `doc.grant` and nothing else, so no `x.` method
can be sent through it. **Unresolved**, and a client-API decision rather than a wire one.

**B.20 A client's outbound bound is a SHOULD, deliberately.** `PROTOCOL.md` §2.1 asks a client to
bound what it holds rather than queue without limit, and states it as a SHOULD. **Decided**: the
level stays a SHOULD. All three implementations queue without bound: a `VecDeque` drained only
when the socket is writable in the Rust client (`crates/client/src/engine.rs`), a `QueuedFrame[]`
drained when the socket opens in the TypeScript engine, and the Neovim companion's byte-identical
vendored copy of it. So a MUST here would be a rule all three violate, and a rule no
implementation honours teaches a reader to distrust the rest. Raising the level means bounding all
three clients *and* deciding what a client does when it reaches the bound, whether it fails the
session or stops committing CRDT transactions until there is room, which is a wire-visible
decision this draft does not take. It becomes a MUST when those two things exist:
every conforming client bounds what it holds, and the specification states the observable
consequence at the bound.

**B.21 A display name is bounded at 32 UTF-16 code units.** `display_name` is a peer-controlled
string that every client draws somewhere, and an unbounded one can cover the screen; both
reference clients were seen doing exactly that. `PROTOCOL.md` §5 states the bound normatively: a
server **MUST** refuse a `session.hello` whose `display_name` is longer than 32 UTF-16 code units
with `bad_params` and close 4000, the refusal a blank one already gets, and **MUST NOT** truncate
it: a displayed name is then not the name its owner chose. **Decided.** The unit matters: it is
counted the way a JavaScript string is measured, the same unit §8.1's offsets use, so an astral
character costs two. An implementation that counts `str::len()` (bytes) refuses names the protocol
allows; one that counts `chars().count()` (code points) admits names it forbids. The schema's
`maxLength` counts Unicode code points and so cannot express the unit exactly: it is a necessary
condition, and the server is where the bound holds. A client **SHOULD** validate before sending so
that a person is asked for a shorter name rather than refused after typing; that is the clients'
own obligation and not a second wire rule. The other free-form peer string, `client`, stays
unbounded: it is diagnostics that no client draws. The lack of a `path` bound is B.8.

**B.22 A mid-session display-name change is `session.rename` and `peer.renamed`.** A seated
connection could not change its name: a second `session.hello` is `already_seated`, and both
reference clients told a person that the session keeps the name it started with. `PROTOCOL.md`
§5 adds `session.rename`, answered with `{}` (the room's statement of the new name is the event,
not the response), and §6 adds `peer.renamed` carrying the minimal `{ "peer_id",
"display_name" }`: no `PeerInfo`, because a rename changes one field and a receiver already holds
`role` and `awareness_client_id` from the roster, and no awareness change at all, because §8.3's
rule that identity is not in awareness still stands. **Decided.** The name carries the
handshake's bound (non-blank, at most 32 UTF-16 code units, `B.21`); a refusal is a **non-fatal**
error response carrying `bad_params`, and the connection stays open, because §11's closing refusal
is the shape of a fault *before* seating and a malformed rename must not drop a working session;
the event is broadcast to **every** peer in the room, the mover included, as `doc.opened` and
`doc.closed` are; and a rename to the name already in force is **still announced**, so "the request
was applied" and "the room was told" stay the same observable thing and no receiver has to
decide whether a name changed. A rename is accepted while the room is hostless (§9's "the room
is fully usable" during the grace period), and touches neither the grace deadline nor the room's
peer-list order, the peer's `role` or its `awareness_client_id`. The wire version stays
`selvage/1`: the member set of no frame changes and two frames are added. `CANONICAL.md` is
**unchanged** and the canonical form stays `SJ-C/1`: a `session.rename` or `peer.renamed` frame
is already canonical under §2.1, §2.2 and §2.3, and §2.8 already covers its `display_name` bound.
No new error code and no new close code: `bad_params` and `unsupported_version` cover every
outcome.

**B.23 A room's working tree is `doc.grant` and `doc.granted`.** A room could not say what its
working copy is: `documents` is the union of the holds its connections declared, so a guest's view
of a project was the union of what every peer happened to have open, and a path nobody had opened
was invisible (`PROTOCOL.md` §1.2, §6.2). `PROTOCOL.md` §5 adds `doc.grant`, a host-only request
answered with `{}` (the room's statement of the listing is the event, as it is for a rename), and
§6 adds `doc.granted`, sent to every peer, the publishing host included, and to a joining
connection immediately after its `room.joined` when the room's grant is non-empty. **Decided**,
with six deliberate choices and three questions left open.

- **The listing is one snapshot, not a stream.** A host enumerates and the whole listing arrives in
  one frame: there is no per-directory walk, no request for a subdirectory and no delta. Paths are
  cheap beside content, so the room's *shape* arrives at once while a file's **content** still
  arrives the way it did before, through `doc.open` and the ordinary sync exchange, when someone
  opens the path (§7). A listing carries **files** and no directory entry: a client that presents a
  tree derives the directories by splitting the paths it was given, which is also what makes a
  project-wide search over one flat list possible.
- **A publication is announced, and only a join can be silent.** A server **MUST NOT** suppress
  `doc.granted` because the listing equals the one already in force, the same choice §5 makes for
  a rename to the current name: "the request was applied" and "the room was told" stay one
  observable thing, and no receiver has to diff two listings. The join-time event is the other
  way round, and deliberately so: it is sent only when the grant is non-empty, because a joiner
  of a room that grants nothing has nothing to be told and `room.joined` already says so.
- **The order of `paths` is a rule.** A publisher **MUST** write its listing in ascending order by
  UTF-16 code unit, and a server **MUST** carry the order it received and not sort, deduplicate or
  normalise it. Without a defined order, byte-exact vectors for `doc.granted` are impossible and
  the corpus could only assert set equality, which has a cost of its own. `CANONICAL.md` §2.7
  said exactly one array was ordered (`documents`) and now names two, and §2.8's "one such bound on
  a value" is qualified, because the server's own limit on what it will store is policy and not a
  bound this document fixes on a value. The wire version stays `selvage/1` and the canonical form
  stays `SJ-C/1`: two frames are added and no existing frame's bytes change, so the two edits are
  prose about new members rather than a new byte rule.
- **The bounds are the server's policy.** A server **MAY** refuse a listing it will not store whole
  (too many paths, or a path longer than its own limit) with `bad_params`, and a host **SHOULD**
  bound what it enumerates so that a pathological tree cannot wedge the session: a listing over the
  transport's frame bound ends the connection with nothing on the wire to say why (§2.1), which is
  the worse failure of the two.
- **There is deliberately no capability name.** Adding one would change the `capabilities` array in
  every `room.created` and `room.joined` and re-baseline all 20 pre-existing vectors, which is a far
  larger change than the two frames it would announce. A host learns support by sending
  `doc.grant` and handling `unknown_method`; a client that does not know `doc.granted` ignores it,
  as it ignores any unknown event, and keeps working from `documents` (§10). A `file-tree`
  capability can be added later in its own change, with the corpus re-baselined for it then.
- **The grant sits in the room and outlives its peers**, exactly as the open-document set does
  (§9). A host that disconnects leaves it, a host that reclaims the room inside the grace period
  learns it from the join-time `doc.granted` and may republish or not, and destroying the room
  destroys it. It is one more thing about a room that a reconnect inherits rather than rebuilds.

**What a non-host publisher gets** is the first open question. §11's vocabulary has no code for
"not permitted" and this change adds none, so §5 says a `doc.grant` from a connection the server
does not hold as the room's host is answered `bad_params`, and vector `023` pins that: the same
code a malformed request gets, which is a distinction a client cannot see. The spec-correct
alternative is an `x.`-namespaced code (§10.1) or a new bare one, and either is a new
client-visible failure; §5, §11 and `023` move together when one is wanted. Note what host-only can
and cannot mean: the server has no filesystem, so it can only refuse a publisher that is not the
host of record. It can never tell whether a listing is the host's disk.

**What a listed path promises** is now settled in `PROTOCOL.md` §12 and is worth repeating here,
because it is the sentence an adapter has to implement: a listed path is a name the host's working
copy held when it enumerated, the server neither resolves nor verifies it, and it may since have
been deleted, may be unreadable, may name a directory anyway, or may name something the host
declines to seed. A client **MUST** treat it as a candidate, not as a promise of a file or of
content. That is the same trust boundary the open-document set already had.

**Two questions are left open.** The first is the sort unit: §5 now says UTF-16 code unit, so a
path beginning with a supplementary character sorts among the surrogates, and a byte- or
code-point-ordered sort (a Rust `Vec<String>::sort()`, for one) orders such a listing the other
way round. Vector `022` is written to fail against a server that sorts at all, or a host that sorts
by code point; if the owner prefers a listing whose order is not a claim, both that rule and the
vector's last two paths move with it. The second is a mid-session tree change: the listing is a
snapshot, no filesystem watcher is specified, and a host republishes when it notices. That is
defensible rather than complete: a path absent from the listing can still be opened by a peer that
knows it, so staleness is a discoverability problem and not a protocol fault. Whether `selvage/1`
wants a file-created/file-removed event, or a republish policy it states, is unowned for now.

**B.24 `awareness_client_id` is not an identity.** `PROTOCOL.md` §8.4 now states normatively what
the id is not: a receiver **MUST NOT** treat it as identity, because a client chooses it, the
server carries it, and any holder of the room's token may claim an id a seated peer is already
using, to publish a cursor under that peer's name, or to publish nothing and suppress the peer's
own state behind the claim. The session layer's identity is `peer_id` together with the display
name attached to it. Whether the server should mint or validate the id (taking the choice out of a
client's hands, at the cost of the id no longer matching a client's local `Awareness` instance), or
refuse a second claim of an id already in force, is **unresolved**. The statement in §8.4 is what
stands until one of those is chosen.

**B.25 A repeated query parameter is refused.** `PROTOCOL.md` §5.1 now forbids a URL that repeats
`room` or `token`, because last-wins and first-wins parsers would name two different rooms from one
URL. §11 has no code for a malformed URL, so the refusal is `token_invalid`, the code a room whose
named token is not the room's already gets, a distinction a client cannot see, the same cost §B.23
records for `doc.grant`'s `bad_params`. **Decided** as the code to use; whether `selvage/1` wants a
URL-fault code of its own is open, and §5.1, §11 and any vector that pins one move together if it
is wanted.

**No vector pinned any of §5.1's URL rules**, and two of the three cannot be pinned by this
corpus at all. The **decoding** rule — `%XX` is the only escape, and a literal `+` is the
character `+` — has no vocabulary to state it in: a placeholder binds one whole value, so a step
cannot spell an encoding of a room id or a token the server minted. The **repeated parameter** is
refused by the reference server before `session.hello`, at the upgrade — a connection that sends
nothing at all is answered `session.error{token_invalid}` and closed 4002, while a wrong token, an
unknown room and a missing token all stay silent until a hello arrives — and §5.1 does not say
when the refusal goes out. A transcript of that refusal would therefore begin with its `expect`
and hold a second implementation to the timing as well as to the rule, where §1.1's silence rule
says a peer must not depend on what the prose does not state; a transcript that sent a hello first
would fail against the reference server, because the socket is already gone. So the rule is
**unpinned and unpinnable** in the corpus as it stands, and what settles it is prose: either §5.1
says when the refusal goes out, or the seating rule of §9.2 does. (The reference server does all
three — a join whose room id or token has its first character percent-encoded still reaches the
same room — so the gap is the corpus's and not the implementation's, and it is the corpus's gap
that matters: an implementation that read only the prose is where the decoding would diverge, and
§5.1's sentence about `+` exists because that divergence is silent — one peer's token is another's
`token_invalid`, and neither can see why.)

**B.26 A one-sided drop has no *required* liveness bound.** A socket can die at one end while the
other stays open (a roaming client, a hung relay, a half-open TCP connection), and every party is
individually conformant. `PROTOCOL.md` §2.1 and §9 bind a ping-based bound at **MAY**: a server
pings every `ping_interval_ms`, and one that leaves its pings unanswered for a stated number of
intervals may be closed as an ordinary drop. The reference server applies it — a peer that has not
answered two successive pings by the time the third is due, which is 60 s of complete silence at
the default interval, is ended — and the end is an ordinary drop: `peer.left`, `host.detached`
when the peer held the room, and then the grace period. So a drop the pings can see does not hang,
and two shapes are left. A peer whose WebSocket implementation still answers them while its session
is not being served is never ended for it, at the reference or anywhere else (`PROTOCOL.md` §2.1:
a connection that answers its pings is never closed for being quiet). A server that does not apply
the bound at all is conformant, and its room then stays hosted by the peer whose socket is gone:
the guests never receive `host.detached`, so no grace period starts, the real host reconnects and
is refused `host_present`, and the room stays hosted by a peer it cannot displace while its guests
keep editing into a room nobody can reclaim. Whether `selvage/1` should *require* a liveness bound,
and where it binds (a **MUST** on the ping bound, or a `host_present` claim becoming reclaimable by
the token holder) is **unresolved**, and it is wire-visible because it decides whether a session
ends, so it is a decision and not wording.

**B.27 A string that decodes to a lone surrogate is refused `bad_message`, and only `CANONICAL.md`
says so.** `CANONICAL.md` §2.3 requires the refusal: a `\u` escape that resolves to an unpaired
surrogate is not representable in UTF-8, which is the encoding a frame's bytes are, so the escape is
one a producer must not write and a receiver rejects. `PROTOCOL.md` §5's value rule for a `path` and
a `display_name` — non-blank, and free of the Unicode `Cc` characters — admits one, so a frame
carrying it satisfies §5 and fails `CANONICAL.md`, and §1.1's "a frame conforms when it satisfies
both" is what makes the refusal the answer. The reference server does refuse it: a `doc.open` whose
`path` is `"src/\ud800.rs"` and a `session.rename` to `"\ud800"` are each answered
`session.error{bad_message}`, while the same character written as a proper surrogate pair
(`"src/\ud83d\ude00.rs"`) is accepted, announced `doc.opened` and rendered. **Unresolved** is where
that refusal is stated, because the parsers do not agree by default: `serde_json` refuses the escape
in the whole frame, while JavaScript's `JSON.parse` accepts it (as does Python's `json.loads`, which
is what the check tooling reads vectors with), and a value that came in that way is one a naive
re-encoder writes as bytes that are not valid UTF-8 at all. `schema/common.json`'s `documentPath`
and `displayName` accept the value today, so an implementation that reads the schema and not this
would conclude it is legal. Stating the exclusion in a `pattern` is not portable: a pattern matching
`[\uD800-\uDFFF]` matches both code units of a conforming astral character wherever the dialect
reads the string without the `u` flag. Vector `036` pins the refusal from the sending side, which
is the only side that can carry it: a `send` step's text can hold the escape, and
`schema/common.json` accepts the frame it makes, while an `expect` step could not — `CANONICAL.md`
§2 makes a lone surrogate unrepresentable, so the tooling's canonical check refuses to let a vector
claim those bytes. Whether the model should refuse the value too, and where, is what is unresolved.

**B.28 Which invite form a host hands on.** `PROTOCOL.md` §5.1 now states both forms: the
connection URL a socket opens directly, and the page link whose origin is the server. The reference
clients hand on the **page link** for every room, including a room on a server started without
`--serve-page`, where the link opens a `404` in a browser and joins only when pasted into a client
that reads page links; the Rust reference client builds the connection URL and cannot read a page
link at all. Whether a host whose server serves no page should hand on the connection URL instead,
and whether §5.1's both-forms requirement is what settles that divergence (the Rust client would
then owe a page-link reader) or the page form should stay a client convention this document merely
describes, is **unresolved**. A link is a handshake in the sense that one peer's link is the
other's entry point, and the two reference client families currently disagree about which form a
human is given.

**B.29 The corpus cannot pin a server's policy bounds.** Every capacity row of `PROTOCOL.md` §2.1
is enforced by the reference server and covered by `crates/harness/tests/session.rs` and
`bounds.rs`, and none of them can be pinned by a transcript: `harness` carries `room_grace_ms`
alone (`A.1`), so `--max-envelope-bytes`, `--max-rooms`, `--max-peers-per-room`,
`--max-documents-per-room` and the inbound budget are all at their defaults for every vector, and
a transcript that needed a small one — the way `vectors/012` needs a 400 ms grace — has no way to
ask. The shapes worth a vector are the ones where the *kind* of refusal differs: an oversized text
envelope (`bad_message`, connection open) against an oversized frame (a drop, nothing on the wire),
and a capacity refusal (`x.room_full`, close 4000) against a malformed request (`bad_params`).
Whether `harness` should carry those knobs — which is a change in `reference_server` plus a corpus
re-baseline — or whether the harness tests are enough for a bound the opening sentence of §2.1
calls policy and not a peer's contract, is **unresolved**.

**B.30 Whether `selvage/1` should name a capacity fault.** `PROTOCOL.md` §2.1 and §11 leave every
capacity refusal to the implementation's own `x.` code — `x.server_full`, `x.room_full`,
`x.rate_limited` in the reference server — so a client cannot tell "the room is full" from "the
request was malformed" without reading a code that is not this version's, and §9.1's rule for `x.*`
is the only thing that makes either terminal. The same question is recorded for a non-host
publisher in `B.23`. Whether a later version defines `room_full`, `server_full` and a rate-limit
code as `selvage/1` codes with their own closes, or keeps them private and leaves a client to treat
every refusal the same, is **unresolved**.

**Revised 2026-09-24 by §B.45.** The question as it stands is settled by the removal: there is no
`selvage/1` for a later version to give a code to, and every capacity refusal is the implementation's
own `x.` code with the close that follows it, which is what the version has instead of a reserved
name. What the question was reaching for survives as a question about a **future** version — whether
`selvage/3` or later defines `room_full`, `server_full` and a rate-limit code of its own — and that
is decided when such a version is written rather than here.

**B.31 The sealed frame's bytes, and Ed25519 over ECDSA P-256.** `PROTOCOL.md` §7.1 and §5.1's
fragment paragraph, `CANONICAL.md` §6.1 and `schema/sealed.json` now fix `selvage/2`'s sealed
frame: the envelope's field list, the key schedule, the associated data, the signature input, the
key id, the counter and the replay rule, the two keys in the invite's fragment, and the two payloads
`kind = 1` and `kind = 2` carry. **Decided** (2026-09-22), first of the revision's pieces because
the corpus's vectors are byte-exact against these and cannot be written until they are frozen.
**Implemented** by `reference_server`'s `crates/client/src/sealed.rs`, which seals, opens and reads a
frame to this layout, with `crates/selvaged` relaying a sealed frame without opening it and each
client carrying the same module (`vscode_client`'s `src/engine/sealed.ts`, and its copies in
`web_client` and in `nvim_client`'s vendored engine). The wire version is `selvage/2` (§A.9), and
the peer corpus is written against these bytes. The measurement script lived in
`.tmp/envelope/envelope.mjs` in this pass's worktree and is not committed, as
`docs/studies/peer-corpus.md` §10's was not; it rebuilds that study's vector 101's ciphertext and
signature byte for byte (apart from the three length bytes this layout drops), and exercises every
refusal rule below.

**The signature is Ed25519**, measured against ECDSA P-256 rather than preferred. The two are the
same size on the wire once the encoding is pinned — 64 bytes either way — so "ECDSA P-256 has a
bigger signature" is false as a wire claim: it is bigger only as DER (70 to 72 bytes over 200
signings, and the length varies), which is what a runtime produces unless the implementation asks
for `dsaEncoding: "ieee-p1363"`, and a spec that left that to the runtime would give two
implementations two frame lengths for one message. Both need a Rust crate, so "ECDSA P-256 needs no
new dependency" is false too, for the Rust client: `ed25519-dalek` 2.2.0 (BSD-3-Clause, 24 crates
with default features, 3.2 s for a clean release build) against `p256` 0.13.2 (MIT OR Apache-2.0, 25
crates, 3.1 s), both sets permissive and neither crate in the tree today (`Cargo.lock`'s only
crypto is what `yrs` pulls in). What is not even: speed, at 13.7 µs a signature and 26.3 µs a
verification against 135 µs and 231 µs on this host, and malleability, because P-256's `s` and
`n − s` are both valid signatures for one message, so a verifier that normalises and one that does
not disagree about a frame. Ed25519 has no parameter, no encoding choice and no such pair. P-256's
one real advantage is its WebCrypto floor, which is why the other algorithm was priced at all:
**Ed25519 needs Chrome 137, Firefox 129, Safari 17, Deno 1.26 or Node 16.17**, where P-256 has been
in every browser since 2017. The floor is accepted rather than argued away: the page needs Chromium
to host (the File System Access API), joining is the path the other two browsers take, all three
were released more than a year before this date, and a client that finds no `crypto.subtle`
Ed25519 refuses locally in the same register as a missing fragment. In the browser the two are
within 1.5× of each other (Chrome 152: 26.0/46.3 µs a signature and a verification against
39.8/66.3 µs; Node 22, the clients' runtime: 43.6/103.1 against 41.1/73.3), so the Rust side is
what the speed decides.

**Where the bytes differ from the two studies.** The corpus study's §5.2 layout was adopted wherever
the plan did not force otherwise — the AAD and the signature input are its own, byte for byte,
including the length-prefixed `"selvage/2"` and room id, and `kind = 2` is its proposal for
`room.closing` — and this pass's own implementation reproduces its `key_id` derivation on all three
fixture keys (`SHA-256` of the 32-byte public key, first 8). Four things changed. The envelope
writes `key_id` (8 bytes), the nonce (12) and the signature (64) at their fixed widths instead of as
`varUint8Array`: 104 bytes of overhead a frame rather than the study's measured 107, and a 63-byte
signature is malformed rather than a variant of the layout. The room state carries each peer's
**public key** where the plan and the study both wrote the `key_id` only: an 8-byte id verifies
nothing, and the plan's own sentence has a receiver "verifying against the key `key_id` names"
without anywhere that key could have come from. There is **no replay window**, because the transport
is ordered and a window would only widen what a captured frame can do; the plan's "small window for
reordering" is a window a single WebSocket never needs. And a room state and a closing are ordered
by their own `issued` rather than by the envelope counter, because the host key is minted with the
room and outlives the connection that published any one state — a host that reloads would otherwise
be refused by the peers it had just returned to. It follows that the host **MUST** publish a closing
above every state it has published, or an old closing replayed after a resume would end a live room.
The two sealed payloads are JSON and inherit §2 and §3, with no `v` of their own: §2.5 is not
theirs, and `schema/validate.py` runs `sealed.json` against conforming and refused values, so the
member set cannot be relaxed with every other check still green.

**The fixture.** `docs/studies/peer-corpus.md` §5 rests on a fixture room key, fixture host keys and
fixed nonces, and no word in this file described one until now, because nothing here had a place
for it. A fixture is a set of **public test values** — a room id, a room key, three Ed25519 keypairs
and one nonce a frame — whose job is to make a sealed frame a constant two implementations can be
held to; it lives in a public repository and is never a room's. Its authority is reproducibility and
not secrecy, and `test_recipe.py` re-derives what is derivable and asserts the bytes a vector
carries. One thing a fixture cannot make derivable is the signature: Safari's Ed25519 **randomises**
signatures (its compat data says so, citing the noise draft; Node, Chrome, Firefox and Rust follow
RFC 8032), so a conforming implementation need not produce the bytes a vector carries. A sealed
vector therefore asserts that a signature verifies and carries its own as literal hex, which is
`docs/studies/peer-corpus.md` §5.1's "`hex` is a checked cache" rule applied to the one part of the
envelope that has no derivation at all.

**What is not here.** The holds and their lease (whose carrier is a `kind` this version leaves
unused) and `selvage/2`'s session layer, which is §B.32. The resume, the rule that refuses a
committed `viewer`'s content and the client-behaviour section that stands where the server no longer
enforces anything are §B.34; `dropped`, the mutation switches and whether a client persists the
host's private key are open where `docs/studies/peer-corpus.md` §9 and `docs/studies/e2ee-plan.md`
§14 left them.

**B.32 `selvage/2`'s session layer: what is settled, and what is not.** `PROTOCOL.md` §1.1 names
every passage of `selvage/2`, and this item's are the session layer's: what `/meta` advertises, the
handshake, the version gate, and the invite's forms. **Decided** (2026-09-22), after §B.31 froze
the frame's bytes and before the server's state leaves `selvage/1`. **Implemented**, except the
version gate §B.45 removed: the reference server and every client speak `selvage/2`
(`crates/protocol/src/lib.rs`'s `WIRE_VERSION`, `vscode_client/src/engine/envelope.ts` and its two
copies), `/meta` advertises the one version and no `roles`, and both invite forms carry the
fragment. The pre-socket `/meta` check, `unsupported_version` and close **4005** are gone with
§B.45, so a frame whose `v` the receiver does not read is `bad_message` (§A.9).

What the passages settle:

- **`/meta` keeps its shape and loses one member.** `wire_versions` names `selvage/2` and, from a
  server of that version, no lower version: a `selvage/2` server cannot seat a `selvage/1` peer,
  and advertising one would be a downgrade a compromised server could take a client down.
  `capabilities` is `["y-protocols/1", "awareness"]`; `keepalive` is unchanged in membership and
  in shape, and in this version `awareness_renew_ms` and `awareness_expire_ms` are the session's
  only clock. `ping_interval_ms` and `room_grace_ms` are the server's, and `room_grace_ms` now
  measures a room's survival of its **last** connection rather than of its host's. `roles` is the
  member that leaves, because the server seats nobody as anything.
- **The handshake is `selvage/1`'s minus one optional param.** `session.hello` has no `role` to
  claim, and minting a room still seats the connection without the server recording that it is the
  host — the host is whoever holds the private half of the host keypair whose public half the
  invite's fragment carries. `room.created` and `room.joined` lose `documents` and `PeerInfo`
  loses `role`; the listing, the roles and the host's identity arrive afterwards, in the host's
  sealed room state.
- **The version gate is `unsupported_version` and close 4005**, unchanged from `selvage/1`'s
  vocabulary and named in both directions. A reachable `/meta` that names no version a client can
  speak is refused locally, before a socket; nothing is refused before the hello, because the
  version is a member of the frame and not of the URL; and there is no downgrade path, which is
  one client MUST: a client that can speak `selvage/2` **MUST NOT** connect to a server whose
  `/meta` does not name it, and **MUST NOT** fall back when a hello is refused.
- **Both invite forms carry the fragment** (§5.1), and a client refuses locally when it is absent,
  has a value missing, or has one that is not 32 bytes. That refusal has **no wire form** — no
  `session.error`, no close code, no §11 code — and the sentence said is the clients' own: the
  shared rows live in `docs/studies/client-command-parity.md` §5 and are pinned by a test in each
  client. A **handover** (the two values said separately, beside a link that lost its fragment) is
  allowed and **NOT RECOMMENDED**; it is not a second flow.

**What is not here, and which step owns it.** The three `doc.*` methods with the open-document set
and the grant, the host machinery, the roles and the full-set echo are deleted from `selvage/1`
rather than restated in `selvage/2`'s terms. That step has two halves and §B.33 is the first of
them, the specification's; the second, deleting version 1's text, schema members and vectors and
re-baselining the corpus, is **step 8** of the same plan (`docs/studies/e2ee-plan.md` §11) and lands
after the release wave. The holds and the lease that replace the set are **step 4b**'s and not step
4's, and so is the clock the lease runs on (`docs/studies/e2ee-plan.md` §8, §11). What step 4 wrote —
the resume, the rule that refuses a committed `viewer`'s content and the client-behaviour section that
stands where the server no longer enforces anything — is §B.34. The lease's clock may want a number
`keepalive` does not carry today (`docs/studies/relay-only-spec.md` §8). The wire version itself does
not move: `v: "selvage/1"` is what every implementation writes until the revision's implementations
land together.

**Two things this pass found rather than settled.** The capability *mechanism* is left with two
names, both of which the version already fixes, so the array now tells a peer nothing it did not
already know; whether it stays at all is open (`docs/studies/relay-only-spec.md` §2.6). And
`docs/studies/relay-only-spec.md` §6's "the schema is the check" is right about the intent and
wrong as written: no schema in this directory forbids a member, because tolerance is the rule
(`CANONICAL.md` §3), so a server-authored frame carrying a `role` or a `documents` does **not**
fail `schema/session.json`. What holds a server to the member set of its version is the corpus's
exact member comparison in the replay; the model's part is that the version's own shape validates
at all, which is what `schema/session-v2.json` and `schema/validate.py`'s `check_session_v2` add
for the frames §2, §5 and §6.1 describe.

**B.33 `selvage/2`'s server, and the `selvage/1` passages that stay.** `PROTOCOL.md` §1.1, §1.2,
§2.1, §3, §6, §9, §9.1, §9.2 and §11 now state the server of `selvage/2` as what it is, and
`schema/session-v2.json` with `schema/validate.py`'s `check_session_v2` gained that version's
`peer.joined` and the fault vocabulary it answers with. **Decided** (2026-09-22), after §B.32 wrote
the version's session layer and before the corpus moves to it. **Implemented** by
`reference_server`'s `crates/selvaged` — `room.rs` for a room and its grace, `budget.rs` and `net/`
for the bounds, `main.rs` for seating — which seats `selvage/2` for every implementation here.
§B.45 removed what a server of this version does not have: `roles`, `host_present` and close
**4004**, `unsupported_version` and close **4005**, and every `doc.*` method.

What that server is, positively:

- **Rooms and membership.** A room is minted by a connection whose URL names no room (§5.1); its
  `id` and its `token` are the server's, the token is the permission to join, and each seated
  connection gets a `peer_id` that is opaque, per-room and does not survive the connection. Seating
  is a `session.hello` answered with `room.created` or `room.joined`, the peers already seated are
  told `peer.joined`, an ending connection is `peer.left`, and a rename is `peer.renamed`. No
  record the server sends carries a role.
- **The relay.** A binary frame is relayed to the room's other connections byte for byte and to
  nobody else; the server does not open, verify, transform or drop one, and it writes no member
  into one.
- **The room's life.** It lives while it has connections and for `room_grace_ms` after its **last**
  one ends. The timer arms when the last connection ends rather than when a host's does; a
  connection seated inside the window cancels it and the room goes on with the same id and the same
  token; at the deadline, with no connection seated, the room is destroyed and the id is gone for
  good. A room whose peers stay connected is not reaped by anything the server knows, whatever has
  happened to whoever hosted it.
- **The bounds that survive.** Every per-connection bound in §2.1 survives — the frame, message and
  envelope bounds, the outbound queue, the hello timeout, the ping bound, the head bound and the
  inbound budget — and so do the room and peer caps. The two rows that bound a room's stored state
  with its open-document set and its grant do not: this server holds neither. A room's stored state
  is its token, its membership and its connections.
- **The refusals that survive.** `unknown_method`, `bad_message`, `bad_params`, `hello_required`,
  `unsupported_version`, `room_unknown`, `token_invalid`, `room_gone` and `already_seated`, plus an
  implementation's own `x.` codes; the closes are 4000–4003 and 4005. `host_present` and close
  **4004** go, because a server that does not know who the host is has nothing to refuse on, and
  the reserved `doc_not_open` goes because this version has no `doc.*` method for it to be about.
- **The keepalive.** Unchanged: a WebSocket Ping every `ping_interval_ms`, a peer that stops
  answering closable as an ordinary drop, and no ping relayed to a room.

**What this pass found rather than settled.**

- **`room.gone` has no recipient in `selvage/2`.** The plan and the corpus study both keep the
  event, with its cause changed from "the host did not return" to "the last connection ended
  `grace_ms` ago" (`docs/studies/relay-only-spec.md` §2.4, `docs/studies/e2ee-plan.md` §6.1). Those
  two cannot both hold: a room is destroyed only when the grace after its last connection has run
  out, so at the deadline no connection is seated and there is nobody to tell. The event stays in
  the vocabulary — it names the ending, and the close 4003 that goes with it — and a `selvage/2`
  server produces none; a room's being gone is learned as `room_unknown` by the next connection that
  names it, which is also the observable a new harness test gets. The plan's own sentence "a room
  with nobody in it survives the window and then is destroyed (`room.gone`, close 4003)" is the one
  it contradicts.
- **§9.1 is not `selvage/1`'s whole.** Two of its rules are that version's — the reclaim by a
  connection claiming `role: "host"`, and inheriting a room's documents from `room.joined` — and
  the rest is the reconnect policy neither version owns alone. So it is scoped sentence by
  sentence rather than treated as a section one version has and the other does not, and the grace it
  names is read for each version: `host.detached`'s `grace_ms` is `selvage/1`'s number, and
  `room_grace_ms` in `/meta` is `selvage/2`'s.
- **What a schema can and cannot check here, measured.** §B.32's finding stands: no schema in this
  directory forbids a *member*, so the version's central negative duty — a server-authored frame
  carries no path, a role, a document name or a character of a room's text — is prose plus the
  corpus's exact member comparison and cannot be a schema. What a schema *can* close is a *value*,
  and a fault code is a closed vocabulary in both versions (§11), so
  `session-v2.json#/$defs/errorObject` refuses `host_present` and `doc_not_open` while
  `errors.json#/$defs/errorObject` still accepts both. Both directions are checked in
  `check_session_v2`, and each was shown red on its own: adding `host_present` back to the
  version's enum fails two checks, and making `peer.joined`'s `peer` optional fails one.
- **The schema's gain, and the counts that did not move.** `session-v2.json` gained
  `#/$defs/peerJoined` (the one of this version's seven server-authored events whose params differ
  from `selvage/1`'s), `#/$defs/errorCode` and `#/$defs/errorObject` (the nine codes, and the fault
  shape `session.error` and a response both carry here). `check_session_v2` now runs 42 values
  against the file, against 25 before, and the published run's line moves with it. Nothing in the
  corpus moved: `EXPECTED_VECTORS` is 36, `EXPECTED_FRAME_CHECKS` 35022, `EXPECTED_ASSERTIONS`
  8676, and `EXPECTED_CODES` is untouched, because no vector was added, deleted or re-pointed and
  the transcripts are still `selvage/1`'s.

**What is not here, and which step owns it.** The removal of `selvage/1` — its text, its schema
members, its vectors and `check_vector`'s version pin — is **step 8** of
`docs/studies/e2ee-plan.md` §11 and not this step: those vectors pin the live behaviour of the
reference server and of the released clients, and nothing may go red while the specification runs
ahead of the implementations, so version 1's passages stay where they are and are read as that
version's (§1.1). Step 8 lands after the release wave, when no published client speaks `selvage/1`
any more, and it is where the corpus is re-baselined onto version 2 and version 1's sections,
schema members, vectors and glossary entries are deleted rather than scoped. The peer-side rules
the version needs — the holds and their lease — are step 4b, and the clock they run on with them; the
resume, the refusal of a committed `viewer`'s frames and the client-behaviour section are step 4's and
are written (§B.34). §12's replaced wording is step 7, and step 4 scoped it as `selvage/1`'s and
stated the version's own facts where §3's negative duties are.

**B.34 `selvage/2`'s authority model: the room state, attribution, the resume and the viewer rule.**
`PROTOCOL.md` §3's closing account of the relay, §7.1's room-state passage, §9.1's return passage,
§12's scoping lead-in and the new §13 state the peer side of the version: who may say what, and how a
peer knows it. **Decided** (2026-09-22), after the frame's bytes (§B.31) and the server's state
(§B.33), and before the holds and the lease. **Implemented** by `reference_server`'s
`crates/client/src/peer.rs` and `host.rs` and by each client's `src/engine/peer.ts` and `host.ts`,
with §13's decision vectors behind them (`vectors/peer/104`, `106`, `111`, `118`, `151` and `152`).
One rule of the item is not: the second **SHOULD** §B.46 records as its §13.3's — a host committing
a key `viewer` because the invite it handed out was `viewer` alone — which no implementation does.
It would take a host engine that can place a key at the seat its invite went to, and none can today:
a host reads a role from the announcer's declaration alone, so `commit` writes
`declared.unwrap_or("guest")`. **Revised 2026-09-22 by §B.36**, which binds a role to a key: where
gives a role to a *peer*, read *key*.

What the passages settle:

- **The room state is the room's authority, and the host key is the only thing that can publish one.**
  §7.1 says when a host publishes (at mint, on every change, on every `peer.joined`), that a state
  replaces wholesale, that `issued` is strictly increasing, that its own entry carries its
  connection's *session* key — not the host key, which signs `kind = 1` and `kind = 2` and nothing
  else — that a state names exactly one `host` entry, and that a peer holding a state **SHOULD**
  re-send it unchanged when a peer is seated.
- **Attribution is cryptographic, and a client's rule is a lookup.** §13.4: the `key_id` is an index,
  a receiver resolves it by verifying, the frame belongs to the key that verified, the role is the one
  the applied state gives that peer, and a key no state commits is `uncommitted_key`. The two
  authorities are named: the server's roster decides *seated*, the host's state decides *keys and
  roles* (`docs/studies/relay-only-spec.md` §7's demand, answered for the half that does not need the
  lease).
- **The viewer rule is two obligations and a residual.** §13.5: a `viewer` **MUST NOT** send document
  content — a `kind = 0` frame carrying a SyncStep2 or an Update, while its SyncStep1 and its
  awareness are still sent, answered and applied — and a receiver **MUST NOT** apply such a frame from
  a committed `viewer`, refusing it `unauthorised_content`. The residual is §B.3's: the guarantee is
  that no *conforming* client applies it.
- **The resume dissolves.** §9.1: a returning host publishes a state signed by the host key with an
  `issued` above the room's, which is both the proof — only that key can sign one — and the
  anti-replay, because a state not above the mark is refused `stale_issued`. No fresh value, no
  `resume` frame and no new carrier. What replaces it is a persistence rule: the host key is minted at
  share time and **must be persisted** for as long as the host means to keep hosting, with its
  `issued` beside it, and a client that does not persist it ends its own hosting on reload. That
  retires the open question `docs/studies/e2ee-plan.md` §14 and
  `docs/studies/relay-revision-brief.md` §7 leave as "where the host's private key lives".
- **The tie is first-wins, and the divergence is stated.** §13.3: two connections of one host can
  publish different states at one `issued`, and nothing tells a receiver which is later, so the
  strictly-greater rule `CANONICAL.md` §6.1 had already fixed makes the first state a receiver accepts
  the one it holds and the second a `stale_issued` refusal. What that leaves is two receivers on
  different listings at one edition, repaired when a greater `issued` arrives; a client **SHOULD**
  report the equal-`issued` case as a conflict and **MUST NOT** clear its listing, drop content or end
  the session for it. Two producer rules — one host session per room at a time, and the `issued`
  persisted with the key — are what keep it from arising.
- **§12 is `selvage/1`'s**, its deployment duties hold in both versions, and the three wire properties
  it states that version 2 does not share are named at its head. `selvage/2`'s own security facts are
  where §3's negative duties are: what the relay cannot do, and what it still learns.
- **The client-behaviour section is §13.** It holds the order of operations at a join — which answers
  the question `docs/studies/relay-only-spec.md` §3 left open, because frames from a key no state has
  committed are **dropped** and the client **MUST** re-run §7's handshake after its first verified
  state, which is what makes the drop safe — verify-before-apply with a local report of every refusal,
  the room state as a receiver, attribution, the viewer rule, and what a client owes convergence.

What this pass found rather than settled:

- **Only the host can re-announce a state.** `docs/studies/e2ee-plan.md` §7.2 has "the host (and any
  peer holding a verified state)" re-announce the state "sealed and signed", and a guest cannot sign
  one: `CANONICAL.md` §6.1 refuses a `kind = 1` frame from any key but the host's. What a guest can do
  is re-send the host's own bytes, which verify because they are the host's; §7.1 says that, and makes
  the re-send a **SHOULD** rather than a second flow.
- **A refusal never reaches its publisher.** The relay sends a frame to the room's *other*
  connections and a refusal is the receiver's local report, so a host whose state no peer applies —
  the shape a lost `issued` counter makes — learns nothing from the wire. That is why the counter's
  continuity is a **MUST** on the host, and why the re-send above exists: without either, a host that
  lost its counter has no way back into a room that still holds one of its states.
- **The report vocabulary gained one reason.** `unauthorised_content` is the ninth in
  `CANONICAL.md` §6.1's table, and the only one that is not a property of the envelope's bytes.
  `schema/sealed.json` now carries the vocabulary as a closed *value* and `validate.py`'s
  `check_refusals` pins it against that table in both directions, so `bad_tag` — the reason a reader
  expects and no conforming receiver can produce (`docs/studies/peer-corpus.md` §5.4) — is a red run
  rather than a line in a vector. The counts did not move: `EXPECTED_VECTORS` is 36,
  `EXPECTED_FRAME_CHECKS` 35022, `EXPECTED_ASSERTIONS` 8676 and `EXPECTED_CODES` is untouched, because
  no vector was added, deleted or re-pointed.
- **The invite's naming of a `viewer` is not a wire rule.** A host's client telling a guest's client
  that it is read-only is a convention across the link — §5.1 has a receiver ignore a query parameter
  it does not know, which is the whole of what such a parameter can be — and a client learns its own
  role authoritatively from the state.
- **The holds have no carrier in the frozen bytes, and step 4b has to choose one.**
  `CANONICAL.md` §6.1 defines `kind` 0, 1 and 2 and leaves values above 2 to a later revision, which a
  receiver of this version refuses `unknown_kind`; and §7's message table is y-protocols', whose free
  values are not this protocol's to define. So a hold announcement is neither a `kind` this version
  has nor a message inside a `kind = 0` frame, and the two studies disagree about which it is:
  `docs/studies/e2ee-plan.md` §7.3 has it as "a sealed session message" and §B.31 as "a `kind` this
  version leaves unused". Either answer is a change to a frozen file — a fourth `kind` in
  `CANONICAL.md` §6.1, or a payload inside `kind = 0` — and the choice is step 4b's, because it is
  made by the holds' text rather than by this pass's. What this pass fixes is that nothing here
  depends on it: §13's state, attribution and viewer rules carry no hold.

What is not here, and which step owns it: the holds and their lease, the presence clock a client runs
on, the lifecycle rules that need it, and the viewer's own behaviour, which is where the convention
above belongs if it is specified at all. Each is a further subsection of §13's subject, all of them
**step 4b** of `docs/studies/e2ee-plan.md` §11. The sealed sub-corpus is 4b's too, and what this pass
constrains is its fixture: a committed `viewer` whose frame verifies, two states at one `issued` with
different listings, a state below and a state at the mark, a key no state commits, and a host entry
whose key is not the host key. `docs/studies/peer-corpus.md` §5.3 was written before the bytes were
frozen and its room states carry a `key_id` per peer where `CANONICAL.md` §6.1 fixes a 32-byte `key`;
that shape is what the frozen layout corrects.

**B.35 `selvage/2`'s holds, their lease, the presence clock and the client's lifecycle.**
`CANONICAL.md` §6.1 gained a fourth `kind` and a third sealed payload; `PROTOCOL.md` §1.2, §5.1, §7.1
and §13.7–§13.11 complete §13; `schema/sealed.json` gained the holds payload and
`schema/validate.py`'s `check_sealed_payloads` runs it. **Decided** (2026-09-22), after §B.34's
authority model and before the corpus. **Implemented** by `reference_server`'s
`crates/client/src/peer.rs` and `host.rs` and by each client's `src/engine/peer.ts` and
`presence.ts`: the holds and their lease, the presence clock and the two windows, with
`vectors/peer/114` and `154` behind them. **Revised 2026-09-22 by §B.36**, which adds a second
`kind` to the frozen layout.

**The carrier is a fourth `kind`, and it amends the frozen bytes deliberately.** §B.34 left the
choice between a fourth `kind` in `CANONICAL.md` §6.1 and a payload inside `kind = 0`, and this pass
decides it: **`kind = 3` carries the sender's holds**, a JSON object `{"holds":[…]}`, sealed under
the frame key and signed by the sender's **session key**. A fourth `kind` was taken over a payload in
`kind = 0` for three reasons. `kind = 0` is defined as the y-protocols stream of §7 *whole*, and §7's
message table is y-protocols' — its free values are not this protocol's to define — so a hold inside
it would be either a message this protocol cannot add or a container that breaks the "the plaintext
is that stream" equation, and it would muddy §13.5's rule, which reads a `kind = 0` plaintext as sync
messages. Carrying holds in the room state (`kind = 1`) was rejected too: a hold changes far more
often than a listing, is not the host's to author, and could not express a guest's hold in a value
only the host signs. **Phase 1's frozen bytes gained a `kind` and nothing else about the envelope
changed**: the field list, the widths, the AAD, the signature input, the key schedule and the two
existing payloads are as they were.

**The holds have no clock of their own; they reuse §8.2's.** A hold is announced on the awareness
cadence (`awareness_renew_ms`) and expires after `awareness_expire_ms`, checked on the same renewal
tick, so a set is forgotten within `awareness_expire_ms + awareness_renew_ms`. No member was added to
`keepalive`. The reuse is honest because a hold's life has the same shape as a cursor's and §8.2's
clock is the session's only one; the cost, recorded rather than hidden, is that a hold lapses exactly
as a cursor does, so a throttled tab loses both. Whether a hold should outlive a cursor is the
measurement §B.34 and `docs/studies/e2ee-plan.md` §14 still leave open.

**Renewal is one explicit message on the holder's own clock, not any verified frame.** The two rules
on record were "any verified frame from that peer renews, with an unconditional tick for a silent
peer" and "one explicit renewal message". This pass takes the second. A peer that is seated, idle and
holding a document open must not lose its holds, and only the holder's own unconditional timer
guarantees that; and renewal by any frame would let a held set outlive the announcement that carried
it, because a lease any frame renewed cannot expire a set whose holder has stopped announcing it.
The timer is the client's own monotone clock and nothing it receives, which is §8.2's awareness rule
applied to holds.

**The presence clock is local and monotone, and it is armed by an event.** A client's "the host is
away" clock is armed by the first moment it holds a verified state naming a `host` peer the roster
does not seat, and disarmed when an applied state names a seated host; its threshold is the
advertised `room_grace_ms`. It is **not** armed by silence, because a client cannot tell a host that
is away from its own broken socket. A client that has passed the grace **MUST** end its session. The
server's `room_grace_ms` is armed by a different event — the room's **last** connection ending — so the
two need not agree and the client's may pass with the server's unstarted. The debt is stated rather
than implied: a non-conforming client, or a person who leaves a tab open, keeps a hostless room
alive, and the rule is the only thing that ends one.

**The viewer is the state's, and the invite's parameter is presentation.** §13.9 states what a
`viewer` sends — a SyncStep1, its awareness and its holds, and never content — what it applies, and
that the invite's "you are a `viewer`" query parameter is a client convention this document does not
define: the role is the applied state's, per §B.34's finding.

**What this pass found rather than settled.**

- **A `kind = 1`, `2` or `3` plaintext that is not the object its kind defines has no reason in the
  report vocabulary.** The nine reasons are about authenticity and ordering, and an authentic but
  malformed payload is neither. This is not new — `kind = 1` and `2` had the same hole before this
  pass — and this pass does not close it, because either fix (a tenth reason, or widening
  `bad_envelope`) changes a frozen table outside its subject. What a receiver does with such a
  plaintext is therefore **unstated**; a client implementer should treat it as a sender's bug and drop
  the frame locally. `PROTOCOL.md` §13.11 says no rule this pass writes needs a tenth reason.
- **No lease rule produces a report.** Expiry is a set becoming empty, not a dropped frame, so the
  lease's only observable is the subject's view of another peer's holds, which is why §13.11 makes it
  checkable by behaviour and not by a byte vector.
- **The `holds` array is a set and its order carries nothing**, so §1.2's `set` entry now names it and
  `CANONICAL.md` §2.7 gains no ordering rule for it; the one array `selvage/2` orders is still the
  listing.

**The schema's gain, and the counts that did not move.** `sealed.json` gained `#/$defs/roomHolds`
(a one-member object whose `holds` is an array of §5's paths, a member-set check the payload did not
have before), and `check_sealed_payloads` now runs nine more values (four conforming, five refused),
so the published run's `sealed` line moves from 19 to 28 and `README.md`'s transcript with it.
Nothing in the corpus moved: `EXPECTED_VECTORS` is 36, `EXPECTED_FRAME_CHECKS` 35022,
`EXPECTED_ASSERTIONS` 8676 and `EXPECTED_CODES` is untouched, because no vector was added, deleted
or re-pointed and the transcripts are still `selvage/1`'s.

**What is not here, and which step owns it.** The corpus and the vectors are **step 4b**'s
(`docs/studies/e2ee-plan.md` §11): this pass fixes what a fixture must express and nothing more —
`PROTOCOL.md` §13.11 lists the shapes, and they add to §B.34's list a holds message (and its replay,
and one from an uncommitted key), a set that changes between announcements, holds from a `viewer`
and a `guest` under one state, a room whose `keepalive` compresses the awareness window, and a host
peer's `peer.left` with no state above it naming a seated host. `docs/studies/peer-corpus.md` §5.3
predates the frozen layout and its room states carry a `key_id` per peer where §6.1 fixes a 32-byte
`key`: a fixture for this layer follows §6.1. Every implementation is later still, and the wire
version does not move.

**B.36 `selvage/2`'s key binding: the session-key announcement, a state keyed by key, and nine
smaller fixes.** `PROTOCOL.md` §7.1, §8's awareness passage, §9.1 and the §13 subsections that
name a key or a mark, `CANONICAL.md` §6.1 and `schema/sealed.json` now carry the version's second
new sealed `kind` and the rules that depend on it. **Decided** (2026-09-22), in a fix pass over §B.34's text taken from
an independent review of that pass. **Implemented**: the session-key announcement is `kind = 4` in
`reference_server`'s `crates/client/src/sealed.rs`, `crates/client/src/host.rs` commits every
announcement it accepts, the state's `peers` is keyed by the peer's public key, and each client's
`src/engine/sealed.ts` and `host.ts` are the TypeScript half (`vectors/peer/105`, `108` and `153`).

**The hole, and why the state had to be re-keyed.** §B.34 has the host commit each peer's session
key, and §13.1 forbids a client to send any binary frame before a state commits its own — and
nothing carried a key to the host. No server frame can: `peer_id` is the server's word and it is
public, so **any carrier that binds a key to a `peer_id` fails**, letting a peer claim another's
`peer_id`, have its own key committed under it and silence the victim, and letting a `viewer` claim
a guest's `peer_id` and have its edits applied. The only identity a room can trust is a key, so the
state's `peers` is **keyed by the peer's public key** and the `peer_id` beside it is a **label** —
the host's claim about which seat holds a key — that decides no attribution and no role. A sender's
role is the one the applied state gives the key that verified (§13.4).

**The carrier is `kind = 4`, the session-key announcement.** `{"key": …}` and an optional
`"role"`, sealed under the frame key and **signed by the very key it names**: the first
self-certifying carrier here, and the reason its read order is the one kind that is not §6.1's
table — its signer is inside its plaintext, so a receiver opens the AEAD before it can verify
anything. It is **exempt from §13.1's step 4**, and that is the whole of what it is for: it is the
one binary frame a client may send before a state commits its key, and the frame that makes such a
commitment possible. A peer **MUST** announce again when it applies a verified state that does not
commit its key, because a relay may drop one; a host **MUST NOT** commit a key it cannot place in
its roster, because an entry beyond the server's seats is a role nothing answers for. **Revised
2026-09-22 by §B.37**, which has a host commit every announcement it accepts and label the entry
with the best seat it has, because a withheld commitment silences the peer for the session and the
label is read for two presence questions and nothing else.

**The host assigns the role, and its pairing can be wrong.** Nothing on the wire joins a key to a
seat with any authority, so a host can commit the wrong role for a key. A client **MAY** declare in
its announcement the role it believes it has been given — `guest` or `viewer`, never `host` — and a
host **SHOULD** honour the declaration: it is the one statement about a peer's role that comes from
the peer's own key, and it is no weaker a source than the roster, which is the server's word about a
name the server minted. Why honouring it is not a weakening: a role was never enforced by the
assignment, only by the clients that honour it, which is §13.5's residual, so a client that
declares a role it was not given is a non-conforming client exactly as one that ignores a `viewer`
role it was given is. The residual a wrong pairing leaves is stated where it is decided: a key
committed under the wrong role can make a guest read-only, or a `viewer` writable, for as long as
the state stands.

**The nine smaller findings, as they landed.** `issued` has a first value — `1`, because the mark
starts at `0` and a state at or below it is refused, and `CANONICAL.md` §6.1's worked example and
`sealed.json`'s `minimum` said so; **§B.37 removed that `minimum`**, which had made the same value a
step-8 refusal as well as step 9's `stale_issued`. The marks a receiver keeps — the `issued` it has accepted and the
counter mark for each key — are **retained for as long as it holds the room's keys**, and a
`kind = 2` closing is applied **only when the receiver already holds a verified state below it**:
a replayed closing delivered to a fresh joiner passed the old rule and drove it out of a live room,
and what stays unclosable is named with it — a replayed *state* is applied by a peer that holds
none, and converges on the next state above it. The client's host-away threshold is the **host-away
window**, one `awareness_expire_ms` on the session's own clock, and no longer the server's
`room_grace_ms`, which counts a different event. §8 is scoped into `selvage/2` by a version passage
that states what `path` means there and when a newcomer may publish, and §8.3's third step is
`selvage/1`'s. A listing's paths are bounded at the receiver — a path over **4096 bytes** is
dropped along with the excess of a size cap, never the state — which re-homes the bound the server
used to hold. The equal-`issued` conflict stays **`stale_issued`** with the distinct report as a
**local annotation beside** the named reason, because the vocabulary is closed. A `kind = 1`, `2`,
`3` or `4` plaintext that is not its kind's object is refused **`bad_payload`**, a tenth reason:
`bad_envelope` is step one's reason for the envelope's own bytes and widening it would put one
reason at two steps of an order this document says is normative. The relay's ability to **lie about
who is present** is stated in §3's account of what the relay cannot do, with the harm named and the
one thing it cannot do with a roster frame: produce an edit, because content under an invented
`peer_id` is refused `uncommitted_key`. Last, two wording fixes with observable consequences: the
counter mark is "the highest counter it has **not refused**" rather than "accepted", so a frame
refused at the `viewer` step does not move it and the same bytes are refused for the same reason
every time; and §13.1's step 5 says a state is applied if it verifies **and** its `issued` is above
the mark, because §13.3 orders states by `issued` and not by arrival.

**Phase 1's frozen bytes gained a second `kind`, and nothing else about the envelope changed.**
§6.1's field list, the widths, the AAD, the signature input and the key schedule are as §B.31 froze
them; `kind = 3` (§B.35) and `kind = 4` are the two additions, and both are carried by the same
envelope every other kind is. `kind = 4` is the one kind whose *reading* order differs from §6.1's
table, and §6.1 says so where the table is, with the reason: a frame signed by the key it carries
cannot be verified before it is opened.

**The schema's gain, and the counts.** `sealed.json` gained `#/$defs/sessionAnnouncement` and
`#/$defs/declaredRole`, `sealedIssued` gained the `minimum` of `1`, `sealedPeer` became the two
members a seat has (`peer_id`, `role`) with the key now the object's name, and `check_sealed_payloads`
runs eleven more values (four conforming, seven refused) while `check_refusals` gains the tenth
reason. The published run moves on two lines and `README.md`'s transcript with it: `sealed` is 39
where it was 28, and `refusals` is 13 where it was 12; **§B.37 moved `sealed` to 48**. Nothing in the corpus moved:
`EXPECTED_VECTORS` is 36, `EXPECTED_FRAME_CHECKS` 35022, `EXPECTED_ASSERTIONS` 8676 and
`EXPECTED_CODES` is untouched, because no vector was added, deleted or re-pointed and the
transcripts are still `selvage/1`'s. Six mutations were shown red against the new checks: dropping
`bad_payload` from the enum (both directions), `issued` back to a minimum of `0`, `peers` keyed by
`peer_id` again, a declaration of `host` allowed, an announcement with no `key`, and a peer entry
that requires a member this version no longer defines (which turns the *conforming* tolerance case
red). The listing's 4096-byte path bound is the one rule here with no schema behind it, deliberately:
`CANONICAL.md` §2.8 keeps a value over a bound canonical, so the model must not refuse it, and the
checkable half is the receiver's drop (`PROTOCOL.md` §13.3).

**What is not here, and which step owns it.** The corpus still is **step 4b**'s
(`docs/studies/e2ee-plan.md` §11), and §13.11's fixture list gains what this pass added: an
announcement signed by the key it names and one that is not, and a `kind = 1` plaintext that is not
the room state's object. The wire version does not move, and no implementation speaks it.

**B.37 `selvage/2`'s authority text, second pass: the host's seat pairing, step 8's read, and five
smaller fixes.** `PROTOCOL.md` §3, §5.1, §7.1, §13.1, §13.3, §13.10 and §13.11, `CANONICAL.md`
§6.1, `schema/sealed.json`, `schema/validate.py` and `README.md` now carry what a second, focused
review of §B.36 found. **Decided** (2026-09-22), from an independent re-review that confirmed
§B.36's binding decision holds and the room bootstraps, and found two blocking defects and five
smaller ones in its text. **Implemented**: the host's seat pairing is `crates/client/src/host.rs`'s
`commit`, `label` and `fallback_seat` (and `HostProducer.commit` with `label` in each client's
`src/engine/host.ts`), step 8's read of a payload's member set and each member's type is
`crates/client/src/sealed.rs`'s `read_payload`, and the peer corpus pins the results
(`vectors/peer/109`, `110` and `153`).

**The host commits every announcement it accepts, and the label costs nothing.** §B.36 had a host
commit each key it had accepted *that it can place in its roster* and **MUST NOT** commit one it
cannot, which left the step between a key arriving and the room working undefined: an announcement
names no seat, a `peers` entry requires a `peer_id`, and the strict reading silences the peer
entirely, because its frames are refused `uncommitted_key` and §13.3's rule that a refusal does not
reach its publisher means it is never told. The prose now has the host commit **every announcement
it accepts**, labelling the entry with the seat it believes holds the key or, with nothing to go on,
with the announcer's own seat or any seat the roster names: the label decides no attribution and no
role (§13.4), so a wrong one costs the two presence questions that read it and a withheld commitment
costs the peer its whole session. Three rules close the amplification the same passage left open — a
`viewer` may announce, so one read-only peer could make a room re-broadcast its listing per frame
and grow every peer's key set: **at most one key per seat is committed** (a seat is one connection,
so a new key replaces the old and the replaced key's frames are refused `uncommitted_key`), an
announcement whose key the state **already commits** publishes nothing new and is answered with a
state its sender can apply — the one the host holds, re-sent unchanged — at most once per
`awareness_renew_ms`, which is what recovers a *state* the relay dropped, and a receiver's held set is
what its state commits — a key only an announcement has named is held until the state that omits it,
with a **MAY** cap on how many such keys it keeps, §13.7's remedy applied to a key set. A fourth
belongs with the first two and is the one this pass added beyond the review's list: the host
**drops from every state it publishes a key whose entry labels a seat the roster no longer has**, so
the roster's size bounds `peers` as one key per seat bounds it within a seat — otherwise a peer that
reconnected N times leaves N keys behind, which is the same unbounded growth by another road. A
fifth bounds the *rate* rather than the size, and is the one a review comment on this pass raised: a
host **MUST NOT** be obliged to publish more than one state in any `awareness_renew_ms` for the
announcements it accepts, because one state carries every key it has committed by then — so a peer
that mints keys without bound obliges one state broadcast a window instead of one per frame, and a
host that answers each announcement at once is choosing to answer a frame rate it cannot attribute,
which is its own policy as §2.1's inbound bounds are a server's. A
declaration of a role in an announcement about a key the state **already** commits changes that
key's role not at all: the stickiness is new, and without it a committed `viewer` could ask to be
re-roled. The residual is stated with the rules rather than implied: the state carries one key per
seat and drops a seat the roster has lost, the publish obligation is discharged by one state a
window, and a receiver's cap on the keys it holds is a permission — so a conforming host may still
publish more often than a window, and a conforming receiver may still hold a mark for every key it
was ever told about. Neither is a divergence between conforming peers; both are the room's own
capacity policy.

**Step 8 reads a member set and each member's type, and a value a rule elsewhere re-homes is not
read there.** §6.1's step 8 said "the plaintext is the object its `kind` defines" and `sealed.json`
`$ref`'d `common.json`'s `documentPath` from a listing and a holds set, so a control-carrying path —
a legal filename on the platform a client runs on, and a legal string in canonical JSON — was a
**frame** refusal in the model and a **drop** in §13.3 and §13.7. Two conforming receivers could
reach opposite verdicts about one state, which is the one disagreement `issued` exists to prevent.
`documentPath` was `selvage/1`'s shape in any case: there a bad path is refused `bad_params` at a
method, which is a refusal about a request and not about a frame. Step 8 now reads the object's
member set and each member's type, and the two values are plain strings in `sealed.json` with the
drop moved wholly to §13.3 and §13.7. The fixtures follow: a listing whose path carries a control
character, a holds set that carries one and a blank hold are **conforming** values now — what a
receiver must accept and then not offer, and nothing in the check file can express a drop, which is
said at the fixtures — while a listing member or a hold that is not a string is refused instead. The
`refusalReason` enum and `check_refusals`'s census are reordered to the table, `bad_payload` at step
8 before `stale_issued` at step 9, and `sealedIssued`'s `minimum` of `1` is gone: a negative `issued`
is not a count (§2.4), so the bound it carries now is `0` and step 8 refuses a negative one, while
`0` is a count and a state or a closing at `0` is refused `stale_issued` at step 9 — the reason
§6.1's own justification names, and one no schema can express. A state at `issued` 0 is a
conforming fixture for that reason, as a closing at `0` is.

**The five smaller ones.** (a) §3's account of the relay names the second harm its roster power
carries: a forged `peer.left` for the seat the state's `host` entry labels arms §13.8's host-away
clock, and a client whose clock has passed the window **MUST** end its session, so one frame the
relay authors ends a live room's sessions for every guest at the window's delay — 30 s at the
defaults — with nothing in any frame to say a lie occurred; a withheld `peer.left` is the other
half, keeping a departed peer's holds and a hostless room looking live. §13.10 names the
closing-replay composition beside it, and §7.1's claim that a closing is the one value a replay
cannot use at all is corrected with it: a replayed *state* is applied by a receiver that holds none,
so a replayed genuine closing above it ends a joiner's session, and "a receiver that holds no state
cannot be told a room is over by a frame" is a bound on one frame and not on a relay. (b) A dropped
announcement is recovered by a bounded timer: §13.1's step 4 re-announces on the session's
`awareness_renew_ms` for as long as no applied state commits the key, because the old only trigger —
applying a state that omits the key — cannot start when the state itself is what went missing.
(c) A client seated with no state and no host has a clock now: **the no-state window**, one
`awareness_expire_ms` from being seated, after which it **MUST** end and say why, or name the room
again on a new socket and learn from the answer which room it was in. §13.8's clock needs a state to
arm and §13.3 forbade ending for the absence of one, so such a client waited for ever with its seat
keeping a dead room alive and nobody ever reaching `room_unknown`. (d) A key's encoding is
canonical: RFC 4648 §3.5 leaves the last of a 32-byte value's 43 base64url characters carrying two
zero bits, so a 43-character string whose final character is not one of `AEIMQUYcgkosw048` spells no
32-byte value at all. A strict decoder and a lenient one disagreed about one state's key —
`bad_payload` against an applied state, on the same bytes — so `sealedPeerKey`'s pattern fixes the
final character, and both the sealed payloads (step 8) and the invite's fragment (§5.1's local
refusal) say what a spelling that is not canonical gets. The fragment's `k` and `h` are the interop
half of the same ambiguity and are fixed with it. (e) §13.11's table gains a row for each rule
above, and its account of what a vector can pin and what the fixture must carry follows them.

**The schema's gain, and the counts.** `sealedIssued` lost its `minimum` of `1` and took a `minimum`
of `0` in its place: a negative `issued` is not a count and step 8 refuses it `bad_payload`, while
`0` is a count and stays step 9's `stale_issued`. `sealedPeerKey` gained the
pad-bit constraint on its final character and the `minLength`/`maxLength` of 43, because `$` in a
`pattern` matches before a trailing newline in Python's `re`, which is the engine the shipped
`jsonschema` runs. The room state's `listing` and the holds' `holds`
became arrays of strings rather than `$ref`s to `common.json`. `check_sealed_payloads` runs nine
more values: five moved from the refused side to the conforming one (a listing's control-carrying
path, a blank hold, a control-carrying hold, and `issued` 0 in a state and in a closing) and nine
new refused ones (a listing member that is not a string, a hold that is not a string, a key whose
final character carries non-zero pad bits in a state's `peers` and in an announcement's `key`, a key
with a trailing newline in each of those, and a negative `issued` in a state and in a closing). The published run moves on one line and `README.md`'s
transcript with it: `sealed` is **48** where it was 39. Nothing else moves: `schema ok` is 39,
`refusals` 13, `session v2` 42, and the corpus counts are untouched, because no vector was added,
deleted or re-pointed. Eleven mutations were shown red against the new checks: `minimum: 1` back on
`sealedIssued` and a `minimum: 2` beside the bound (against the conforming `issued` 0 fixtures),
`minimum: 0` dropped (against the negative `issued` fixtures), `bad_payload` back to the end of the
enum, the same census line in `validate.py` back to its old order, the listing back to `common.json`'s
`documentList` (against the conforming control-carrying listing), the holds' `items` back to
`documentPath` (against the conforming blank and control-carrying holds), that `items` type dropped
altogether (against a hold that is not a string), `sealedPeerKey`'s pattern back to
`[A-Za-z0-9_-]{43}` (against the pad-bit refusals), its length bounds dropped (against the
trailing-newline refusals), and a `sealedPeer` that no longer requires `peer_id` (against a peer with
no seat).

**What is not here, and which step owns it.** The corpus still is **step 4b**'s
(`docs/studies/e2ee-plan.md` §11). §13.11's fixture list grows with the pass's shapes. The wire
version does not move, and no implementation speaks it.

**B.38 The peer corpus: `vectors/peer/`, the runner that replays its frame layer, and the seeds
of the decision layer.** `CANONICAL.md` §6.1's bytes had been frozen since §B.31 and there was
nothing in the corpus that read them: `docs/studies/peer-corpus.md` designed the corpus, priced
the tooling and wrote six vectors into a study, and no vector, no runner and no check in this
repository touched a sealed frame. This pass is that corpus's first artifact, and it is the peer
layer's frame half — the evidence that makes a client implementation checkable, runnable where
the specification is read. **Decided** (2026-09-22), the implementation phases' step 4b, and
recorded as it stands rather than as it was planned. **Nothing implements `selvage/2` on a
wire**: no server speaks it, no client seals a frame, and the decision vectors in the corpus are
written, declared and **not run**.

*What the tooling does now.* `runner/sealed.py` is `CANONICAL.md` §6.1 in Python: the envelope's
layout and both its byte strings, the key schedule, the fragment's encoding, the four sealed
payloads as step 8 reads them, and the ten-step read with its verdicts. `runner/run_peer.py`
replays `vectors/peer/*.json` with no socket in it, `--mutation-census` runs every frame vector
twice, and `--list-mutations` prints what each removed guard is. `runner/subject.py` is the
subject protocol the decision layer will be driven through — one JSON object per line, the runner
doing the waiting, `dropped` as a list of `(frame, reason)` — tested against a scripted subject
and against no vector. `runner/test_recipe.py` re-derives every frame in the corpus from the
recipe the vector carries. `runner/run_vectors.py` gained a `layer` filter: it still replays the
wire layer exactly as it did, `--layer all` reports every peer vector as **not attempted** with
the reason, and `--layer peer` refuses rather than attempting nothing and exiting zero.

*The one dependency.* `cryptography`, for AES-256-GCM, HKDF-SHA256 and Ed25519, in the flake's
`python3.withPackages` and in the workflow's `pip install` at the same version (50.0.0). It is a
wheel for every supported interpreter on both, and nixpkgs' is in the binary cache, so neither
side builds a Rust extension: `nix build` substituted it in about a second. Hand-rolling either
primitive was rejected for the reason the study gives — it is security code in the repository
whose claim is that a stranger can trust its bytes. `schema/validate.py` remains key-free and
still needs only `jsonschema` and `referencing`.

*The corpus.* Twenty-three vectors. Seventeen are `frame` vectors (101–117) and six are
`decision` vectors (151–156). The frame ones need no client at all and each declares the single
guard it must go red under: 101 is the positive control (a sealed edit that applies); 102 a
flipped tag byte, refused `bad_signature` — **not** `bad_tag`, and `expectPlaintext` on the
following step is what shows the same counter arrives intact afterwards, so a refused frame
never moves a mark; 103 a counter replay; 104 a state at the mark and one below it; 105 an
announcement that is not a commitment; 106 a state that drops a key a previous one committed;
107 a host state signed by a committed session key; 108 a replayed announcement; 109 a key whose
final base64url character carries non-zero pad bits and one with a newline appended; 110 a
plaintext that is not its kind's object; 111 a committed `viewer`'s content that decrypts and
verifies and is refused `unauthorised_content`; 112 a closing above the mark and at it; 113 a
frame with a byte left over and one short inside a field; 114 a holds message and its replay;
115 an unknown kind; 116 an unknown epoch; 117 a path `PROTOCOL.md` §5 refuses, dropped from a
listing and a hold set and refusing neither. Each one asserts the consequence and not only the
verdict: the replica, the listing, the holds, or a later frame that must still apply.

The decision vectors are §13.11's fixtures and nothing runs them: a committed `viewer` whose
frame verifies, two states at one `issued` with different listings, an announcement the relay
withholds and the client recovers on its renewal clock, a lease that expires a silent peer, a
closing that must not end a stateless receiver, and the no-state window. `run_peer.py` reports
each as **not attempted**, naming both things that are missing — no subject and no relay that
speaks `selvage/2` — and the summary counts `not attempted` apart from `passed`.

*The negative class.* No server-authored frame carries a path, a role or a byte of content, and
`schema/validate.py`'s `check_absence` asserts it three ways: every `selvage/2` session shape is
walked for a member of the forbidden list (the structural half — a `role` made a property of
`session-v2.json` is a red run); every peer vector's sealed frames are searched for the strings
its own plaintext names (the ciphertext is in the vector, so no key is needed); and the same
walk is run over the `selvage/1` corpus as its **positive control**, where it finds 8,335 frames
naming `role`, 397 `documents`, 10 `paths`, 197 an event this version deletes, 58 naming
`src/main.rs` and 6 binary steps carrying its bytes — all pinned, because a scan that read
nothing reports zero and an unpinned zero is a green line. The corpus study's §6 table counts
`host` among the member names at 186; `host` is not a member name anywhere in this corpus, it
begins two *event* names, and the member census and the event census are two measurements.

*The counts.* `EXPECTED_VECTORS` is now `EXPECTED_WIRE_VECTORS`, and it is 36 — unmoved. Three
pins are new and one is a rename: `EXPECTED_PEER_VECTORS` 23, `EXPECTED_PEER_CHECKS` 194,
`EXPECTED_PEER_ASSERTIONS` 66. The wire layer's `EXPECTED_FRAME_CHECKS` 35022 and
`EXPECTED_ASSERTIONS` 8676 are **unmoved**, because the wire layer's own checks and vectors did
not change; the peer layer is counted apart so that one layer's loss cannot be paid by the
other's gain, which is the argument the study makes for splitting the vector count. Two censuses
are new: `EXPECTED_REFUSALS` (vector → the reasons it asserts) and `EXPECTED_MUTATIONS` (vector →
the mutation it must go red under, `None` for the positive control) — the second is the only pin
in this corpus that says what the corpus *catches* rather than what it contains.

*The two halves of the tooling, which had disagreed.* `apply` was in `validate.py`'s
pass-through op list while `run_vectors.py` raises on an op it does not know, so a step could
validate here and fail the replay there. Both halves now name their ops in a table —
`validate.WIRE_OPS`, `run_vectors.WIRE_OPS`, `validate.PEER_FRAME_OPS`, `run_peer.FRAME_OPS` —
and `runner/test_runner.py` compares each pair, so the two cannot disagree about a step name
again. `run_step`'s `else` clause is reached only by an op its dispatch has no branch for.

*Mutations, all shown red.* Twelve receiver guards, each of which a named vector fails under and
which 101 stays green under — the positive-control sweep is part of the census and is what
refuses "drop everything and complain": `lenient-layout`, `lenient-kind`, `lenient-epoch`,
`no-commit`, `kind-any`, `no-mark`, `no-verify`, `no-payload`, `lenient-key`, `no-issued`,
`no-roles`, `refuse-bad-path`, `merge-peers` (thirteen, with two vectors sharing `no-mark` and
two sharing `no-issued`). Six subject mutations are declared by the decision vectors and none can
be exercised yet: `ignore-roles`, `ignore-issued`, `announce-once`, `no-lease`, `any-closing`,
`wait-for-ever`. `test_runner.py` pins the two tables against what the corpus declares, so a
guard with no vector and a vector naming no guard are each a red run.

*What this pass had to choose, and which way.* The fixture is **generated** rather than adopted
from the study's §2.6: those keys are real Ed25519 pairs and their `key_id`s reproduce §6.1's
derivation, but `mallory-1` has no private half in the study and a vector that must sign with a
key cannot be written without one, so the corpus's fixture is five complete pairs with the
study's room id and room key kept. A **recipe** carries the plaintext as hex when the plaintext
is a `kind = 0` stream or is deliberately not its kind's object, and as the JSON object itself
otherwise, so a reader sees what the frame means; `test_recipe.py` ties whichever it is to the
`hex` the vector carries. A **peer vector is numbered from 101** and the wire corpus's own
numbering is untouched. A **corruption is a step** (`corrupt`, with `xor`/`truncate`/`append`,
and an `as` name for the frame it produces) rather than literal bytes with a comment, so the one
place a vector cannot carry a derivation still carries a re-derivable rule. `run_vectors.py`
**refuses** `--layer peer` rather than exiting zero having attempted nothing, and `--layer all`
reports every peer vector as not attempted — the study asked for the report and the refusal is
added because a zero-exit run that ran nothing is the failure mode the report exists to prevent.

*What is written and not runnable, said plainly.* **Revised 2026-09-22 by §B.40**, which runs all of
it against a client and moves two members of the report. The six decision vectors, and with them the
decision half of the corpus, the `expectSubject` op, the `within_ms`/`at_least`/`frozen`
members and the subject mutations. `runner/subject.py`'s framing, deadlines and report shape are
tested, but **no vector has ever been run against a client**, and the report's members are
therefore a proposal the first real subject will move. Nothing in the corpus demonstrates that
two implementations agree; a vector can hold a client to a rule it states and cannot show two
clients reaching the same state, which stays the interop test's job. And the byte layout's
reading rules the spec leaves unstated are recorded below rather than invented as checks.

**Revised 2026-09-23: `154-lease-expires-a-silent-peer` could not pass and now does.** Its state
committed `host-session` and `guest-1` while its holds message was signed by `guest-2`, which no
state committed, so §13.4 and `CANONICAL.md` §6.1's step 4 refused the frame `uncommitted_key` and
the vector asserted what no conforming client could do — the defect §B.40 found and declined to fix.
Its state now commits `guest-2` under a `peer_id` label and role `guest`, the key its holds message
is signed with, and the state's `hex` is re-derived from its recipe; the vector passes against the
Rust subject and goes red under `no-lease` and no other guard, so the lease §13.7 states is what it
tests. `EXPECTED_PEER_VECTORS` 23, `EXPECTED_PEER_CHECKS` 194 and `EXPECTED_PEER_ASSERTIONS` 66 are
unmoved: a `peers` member and a `hex` live inside a recipe, and neither is one of the corpus's
counts.

**What this pass could not settle.** (1) **A non-minimal varint.** §6.1 says the envelope's
`varUint` fields are LEB128 and does not forbid an overlong spelling, so `80 00` and `00` are two
spellings of the counter `0` whose signature inputs are identical — one frame to a verifier, two
to a byte comparison. The reader takes either and no vector pins one; if the version wants one
spelling it has to say so. (2) **A `kind = 0` plaintext that is not a y-protocols stream.**
Step 8's table row is written for the four JSON payloads and kind 0's plaintext is "that same
stream, whole"; a receiver has to report *something* for bytes that are not one, and the reader
reports `bad_payload` because it is the only reason that fits. No vector asserts it. (3) **A
receiver tolerates another spelling of a payload.** §2 is a producer's rule and a receiver
tolerates a differently-spelled text frame, so the reader parses a plaintext's JSON and reads
its member set and types rather than comparing its bytes; nothing in §6.1 says otherwise and no
vector pins it either way. (4) **Whether the subject protocol's report is the right shape** — the
study's own largest uncertainty, and unchanged by writing it down. (5) **The lease and the
no-state window are asserted by polls with deadlines in the decision vectors, and no poll has
ever run**: `within_ms` is a member a runner has to implement and nothing has implemented it.

**Revised 2026-09-23: the frame layer's two blind spots, each now a vector.** A review of the
vendored engine found two defects the corpus could not catch. Its first-message-only content
check — read the same way by this runner's `yprotocols.decode_message` and by
`reference_server`'s `crates/client/src/sealed.rs` — let a `viewer`'s `Update` behind a leading
`SyncStep1` pass `CANONICAL.md` §6.1's step 10 and have its content applied; the check now walks
the whole stream, message for message, with the decoder the applier uses. And a path over
§13.3's **4096-byte** bound was kept by the Rust client's `usable_path`, where the engine already
dropped it; the bound is applied at the receiver in `runner/sealed.py` and `sealed.rs` alike, so
a path at 4096 bytes is kept and one at 4097 is dropped. Two frame vectors pin them: **118** is
111's frame with a SyncStep1 in front of the Update, refused `unauthorised_content` and applied
to nothing, and it goes red under `no-roles`; **119** carries a 4096-byte path and a 4097-byte
one in a listing and a hold set, keeps the first and drops the second, and goes red under the new
`keep-long-path`. `EXPECTED_PEER_VECTORS` is 27 — 19 frame and 8 decision — `EXPECTED_PEER_CHECKS`
222, `EXPECTED_PEER_ASSERTIONS` 74, and the absence scan reads 27 vectors of this version, 50
sealed frames and 100 needle checks. No rule moves; the corpus grew.

**B.39 `/meta`'s version list, for a server that implements both versions.** `PROTOCOL.md` §2
said a `selvage/2` server **MUST NOT** name a version below `selvage/2` in `wire_versions`. That
is true of a server that implements only `selvage/2`, and it forbids the one shape the release
wave needs: a server that seats both versions while the published clients still speak
`selvage/1`. **Changed** (2026-09-22): the sentence now says that a server which implements both
advertises both, which is what it accepts, and keeps the prohibition for a server that
implements only `selvage/2`. The anti-downgrade rule is unmoved and is what makes the list safe
to widen: §10 forbids a client that can speak `selvage/2` to take an earlier version whatever
`/meta` offers, so the list can never be the thing that moves a client down. Nothing else in §2
moved, and no vector, fixture or count is touched: the change is a sentence about what a server
may advertise, and the corpus's transcripts pin one server's body.

**The room's version is pinned by its mint, which this version's prose does not say.** A room
cannot serve both versions at once — a `selvage/1` client expects the server to hold the
open-document set and the grant, and a `selvage/2` one requires that it hold neither — so an
implementation that seats both has to decide where a room's version comes from and what a
connection speaking the other version gets. The reference server pins the room to the version
its **minting connection** spoke and refuses the other version `unsupported_version` with close
**4005**, which is §10's code and §10's refusal shape at the handshake. That is an
implementation decision with a wire-visible consequence and no passage of this document states
it; it is recorded here so that the next implementation does not have to guess, and it is a
candidate for a sentence of its own if a second server has to agree with this one.

**Revised 2026-09-24 by §B.45.** Both halves are settled by the removal. There is no server that
implements both versions, so there is no list to widen: `wire_versions` is a list with the one entry
in it precisely because the member stays a list for a later version to say so in (§2, §10), and a
server of this protocol implements `selvage/2` alone. And no room's version is pinned by its mint,
because there is one version for a room to be: the open-document set is the peers' own (§1.2,
§13.7), so a connection has no version for a mint to pin, and `unsupported_version` with close
**4005** is gone with the pin it refused (§10, §11).

**B.40 `selvage/2`'s decision layer runs: the subject, its one seam, four §13 corrections and two
reader defects.** `PROTOCOL.md` §13.3, §13.6, §13.7, §13.8, §13.9 and §13.11, `runner/subject.py`,
`runner/run_peer.py`, `runner/sealed.py`, `runner/test_runner.py` and `README.md` hold what running
the decision half of the peer corpus found. **Decided** (2026-09-22), on the same day as the client
that made it runnable. No vector, fixture, schema or count moves: the corpus is what the layer is
about, and what it found is recorded here.

**The runner plays the relay.** A decision vector's frames are its own `deliver` steps, sealed by
`run_peer.py` and handed to a subject through the protocol `runner/subject.py` fixes. `start` seats
the subject with the invite, the session's `keepalive` clock, the roster and the session keypair;
`expectSubject` is the decision channel, with `within_ms` a poll's deadline and `frozen` a window a
member must not move over. `not attempted` now means one thing — no subject was named — and
`--subject` is the only thing a decision vector still needs.

**The one seam, and it is a test seam.** A decision vector's delivered state commits a *fixture*
key's public half, and nothing in the protocol lets a host hand a peer a chosen key, so a subject
whose session keypair it minted itself could not be the peer a vector is about. `join` therefore
carries `session_key`, a 32-byte Ed25519 seed the runner resolves from `vectors/fixture/keys.json`
out of the `key` the vector's `start` names. It is **not production surface**: §13.1 mints the
keypair in memory per connection and never persists it, and the client's member that accepts a fixed
one (`selvage_client::peer::PeerOptions::fixed_session_key`) says so where it is read. It is the same
kind of thing as `CompanionOptions.editor` in the Neovim companion, and it is recorded here for the
same reason.

**Two counts, not one, for what a client sends.** §13.1's step 6 obliges a client that applies a
state committing its own key to send a `SyncStep1`, and §13.6 obliges another after a content
refusal, so a single "published" count could not tell a republished request from a publication —
and vector 151 asserts `published: 1` where a conforming client has already sent its handshake.
§13.11 now states the two observables: a client's **publications** (its announcements, its content,
its holds, and the states and closings a host publishes) and its **§7 handshake frames**, counted
apart. The subject protocol reports both.

**Four corrections to §13, each found by implementing it.** (a) §13.7 said a holder re-announces on
its clock and requires a re-announcement when its set changes or a peer joins, and never said when
the *first* holds message goes out — which §13.1's step 4 forbids before a state commits the
sender's key, so the two rules collided for every holder at its join. It now says the first message
waits for that state, that a holder with a document already open announces at once then, and that
that message is what the renewal clock counts from. (b) §13.6 said a client re-syncs "after any
interval in which content was refused" and never defined the interval, which is the difference
between a client that re-syncs and one that answers a peer frame for frame. It now defines it as
the client's own renewal clock, with a floor (one re-sync after a refusal, on the next renewal tick
at the latest) and a ceiling (never more than one per `awareness_renew_ms`), and states what a
content refusal is once: a `kind = 0` frame refused at step 3 or later of §6.1's order, since a
frame refused at step 4, 5 or 6 has a plaintext nobody can read and steps 1 and 2 are refusals of
the bytes themselves. (c) §13.9 used the same phrase for a `viewer`, where it read as its own
content being refused by its peers — which §13.3 says never reaches its publisher. It now says what
a `viewer` re-syncs after is a content frame *it* refused. (d) §13.8's host-away clock and §13.3's
no-state window can run in sequence for one client, and neither section said so: a client seated
with no state waits the first window, and the state that ends it is what arms the second. §13.8 now
states the sequence and that both windows are owed, so an implementer who meets it knows it is the
design and not a double count.

**Two defects in this repository's own reader, fixed, because one specification with two readers
that disagree is worse than either being wrong.** (a) `runner/sealed.py`'s `_read_ordinary` took the
first key an 8-byte `key_id` named and verified against that one, where `CANONICAL.md` §6.1 says
every key the id names is tried and the frame belongs to the one that verified: on a collision, one
reader reported `bad_signature` for a frame the other applied. It now tries every candidate, keeps
step 5 where the table puts it (against the id, which is what a mark is kept under), and reports
`bad_signature` only when none verified. `runner/test_runner.py` builds the collision the derivation
cannot — about 2^64 candidates — by aliasing one key's id onto another and sealing a frame under the
shared id, and that test is red without the fix. (b) `read_varuint` widened a byte read at shift 63
into an integer past 64 bits where the Rust reader refuses it as a value no `varUint` spells, so the
same bytes were a value to one reader and a malformed frame to the other. It now refuses a byte at
shift 63 with any bit above bit 0, which is `NOTES.md` §B.38's other question left where it was: the
overlong *spelling* of a value inside 64 bits (`80 00` for `0`) is still read as written, in both
readers.

**What the corpus found, and what a later pass fixed.** Vector
`154-lease-expires-a-silent-peer` **could not pass**, and the vector was what was wrong. Its state
committed `host-session` and `guest-1`, and its holds message is signed by `guest-2`, which that
state did not commit: §13.4 resolves a `kind = 3` frame against the keys the applied state commits
and `CANONICAL.md` §6.1's step 4 refuses one from any other key `uncommitted_key`, which is also what
§13.11's own table says twice — a holds message applies "under a fixture state that commits it", and
"a holds message signed by a key the fixture state does not name" is the row for `uncommitted_key`.
The vector's own fixture line asks for "a peer that announces once and is then silent", which is a
*committed* peer, so its state was missing the entry its premise needed. **Revised 2026-09-23:**
`NOTES.md` §B.38 records the fix — the state commits `guest-2` and the `hex` is re-derived, so the
vector passes and the lease is what it tests — and `reference_server`'s
`crates/harness/tests/decisions.rs` replays 154 with the other five and no longer pins it. The other
five — 151, 152, 153, 155 and 156 — pass, and each goes red under the guard it declares it catches.

**A third disagreement between the two readers, found by review and left open.** `kind = 0`'s
plaintext is the y-protocols stream of `PROTOCOL.md` §7 and what a receiver does with bytes that
are not one is unstated (`NOTES.md` §B.38, item 2). The two readers answer it differently, in both
directions: `runner/sealed.py` decodes the framing and refuses `bad_payload` for anything it cannot
read — which includes a message type 2 or 3, where §7 has a receiver read an `auth` message and
ignore it and lets a client ignore an awareness query — while `sealed.rs`'s step 8 for `kind = 0`
carries the plaintext whole and refuses nothing, so a client built on it accepts a stream no
decoder reads and applies nothing. That is now an observable difference and not only a verdict one,
because §13.6 makes a content refusal what a client re-syncs on. It is **not** fixed here and it is
not one of the two defects: §B.38 records the question as open, no vector asserts it, and settling
it means deciding how much of a y-protocols stream a receiver validates, which is this version's to
say and not a pass's. What the next pass needs is one sentence in §7 or §6.1 — what a `kind = 0`
plaintext that is not a stream of §7's message table is — and then both readers move to it.

**Two more things reading the vectors as an implementer turned up, neither an error in the spec.**
`scenario.relay_withholds` (vector 153) names a kind the relay does not forward; the runner's frames
*are* the vector's `deliver` steps, so the withholding is already in which frames the vector hands
over, and the member is checked and otherwise has no effect. And a decision vector's report is keyed
by a *key*, which a client has only as the state's spelling of it or as an id, while the vectors
write the fixture's name for one: the runner resolves a fixture key's name, its canonical spelling
and its id in hex to the one name, and a key a report does not mention at all holds nothing, so
§13.7's expiry is `[]` either way.

**B.41 The two rules a link is decided about, each now a decision vector.** `PROTOCOL.md` §5.1,
§2, §10 and §13.11, `runner/subject.py`, `runner/run_peer.py`, `runner/test_runner.py`,
`schema/validate.py`, `vectors/peer/157` and `vectors/peer/158`. **Added** (2026-09-23).

Both rules are decided before a socket is opened, so neither is a frame and neither has a reason of
§6.1's or a code of §11's: a client answers the `join` itself, in its own words, and seats nothing
behind the answer. The step that asserts one is `expectRefusal`, whose `names` are the strings a
client's words must carry — §13.11 states them, and no sentence of the client's is pinned, because
§2 and §5.1 leave the sentence to the client. The two things a client reads before a socket reach it
as members of the `start` that hands it the link: `meta`, the body `GET /meta` answered, of which
`wire_versions` is the only member read, and `pin`, the version a client's own setting pins it to or
nothing.

**Where a guard is removed is part of the guard.** `runner/subject.py`'s `LINK_MUTATIONS` names
`accept-partial-fragment` and `fall-back-to-version-1`, and the runner removes those with a `mutate`
**before** the `join` and every other guard after it: a subject asked for a link guard once it is
seated could not have refused the link anyway. The six session guards are unmoved, and a subject
that cannot remove a guard still refuses the command, which is a failure of the harness rather than
a red run the census counts.

**What the two vectors pin.** `157` carries three legs: `k` without `h`, refused by name of `h`; `h`
without `k`, refused by name of `k`; and the whole fragment, which must join. `158` carries the
version gate: a link that would speak `selvage/2` against a `/meta` naming no version at major 2,
refused by name of the version it would need, and the `selvage/1` link a client pinned to `selvage/1`
joins. Each goes red under the one guard it declares (`accept-partial-fragment`,
`fall-back-to-version-1`) and stays green under the other's, and each is red at a leg that must join
against a subject that refuses every link. `runner/test_runner.py` drives both against a stub client,
which is the only subject in this repository.

**Counts.** `EXPECTED_PEER_VECTORS` 25 → 27 (19 frame, 8 decision), `EXPECTED_PEER_CHECKS` 210 →
222, `EXPECTED_PEER_ASSERTIONS` 74 unmoved — a decision vector's assertion steps are counted in its
own run's summary and not in the corpus-wide pin — the absence scan's vectors of this version 25 →
27, and the subject's mutation table 6 → 8. The wire layer, the schema layer and the fixture are
untouched.

**Which client holds which rule, as the two implementations stand.** `reference_server`'s
`selvage-subject` refuses a partial fragment by name of the missing key, so `157` passes against it
unchanged. It reads no `/meta` and has no pin, so `158` is red against it: the rule lives in that
repository's own client (`crates/client/src/relay.rs`'s `refuse_a_version_this_client_cannot_speak`,
called by both the join and the mint, with no pin anywhere in that client) and the offline subject,
which drives a session and not a connection, does not reach it. The editor clients refuse a partial
fragment as well — `fragmentKeyRefusal` in the adapter and `parseInvite` in the engine both name the
missing key — and decide the version against `/meta` with the pin included (`hostVersion`), on the
path that mints a room; a link whose fragment names no key is joined by them as a `selvage/1` join
rather than refused, which is B.42.

**Revised 2026-09-24 by §B.45.** One rule now, not two: the version gate §B.41's second half
describes is gone with version 1, and `fall-back-to-version-1` is no longer a mutation a subject
declares. `vectors/peer/158`, which pinned that gate, is deleted with the rule. What §B.41 says about
`157` — the guard it catches alone, and the two readers that refuse a partial fragment by name —
still holds as written, and `157` carries two more legs since §B.45: a `k` and an `h` that are not
32-byte values, which §5.1 refuses for the same reason and which no mutation removes a guard for
(the subject has no such mutation, so the census stays silent about them).

**B.42 A link whose fragment names neither of §5.1's two keys, for a client that can speak
`selvage/2`.** §5.1 has two readings of one link and no vector pins it. Its refusal passage binds the
client that would speak `selvage/2` on that join, and says in the same breath that a `selvage/1` link
carries no fragment and that the client which cannot speak `selvage/2` or is pinned to `selvage/1`
joins the room it names — so a link with no fragment is a version-1 link for *that* client, and the
passage does not say what an unpinned client that **can** speak the encrypted wire does with it,
while its first sentence ("an invite whose fragment is absent") reads as a refusal for any of them.
§2's "their absence is §5.1's local refusal rather than a version to join as" reads as a refusal
too, and the reference implementation's own client refuses it (`crates/client/src/peer.rs`'s
`PeerInvite::parse`: `the invite carries no fragment, so neither its room key nor its host key is
here`) — of every link its version-2 relay is handed, and not of this case alone. Both readings are
consistent with §10. Which one the version means decides whether a guest handed a link a chat client
truncated the `#` off is refused or seated in the clear, so it is a decision and not a wording.
`vectors/peer/157` is about a fragment that names one of the two keys and its third leg is the whole
fragment, so neither leg reaches this case. **Unresolved.**

**Revised 2026-09-24 by §B.45.** The question is settled by the removal rather than answered in
prose: there is one wire version and no pin, so a link whose fragment names neither of §5.1's two
keys has nothing to be joined as, and §5.1's first sentence — an invite whose fragment is absent is
refused — is the whole of the rule. The passage that made it a question was the one giving that link
a version to be joined as, and that passage is gone. Nothing pins it: `157`'s legs are a fragment
that names one key of the two, and `expectRefusal` holds a client to the **naming**, which for an
absent fragment is not a key's name but an ask for the whole link (`crates/client/src/peer.rs`'s
`MISSING_FRAGMENT` says so in those words). A vector for it would need a `names` entry a conforming
client can be held to, and neither the runner nor the harness accepts an empty one; the rule is
stated in §5.1 and its refusal is the subject's own sentence.

**B.43 A review pass over §7.1's host, §13's peer side and the sealed frame's key.** Seven changes,
none of them to a byte, a schema or a vector; the corpus counts are unchanged.

- **Which seat a new key is labelled with is fixed.** §7.1 let a host label a key it could not place
  with "any seat the roster names, its own included", and in the same paragraph had a new key replace
  the key its seat held — so a guest's announcement labelled with the host's own seat would have
  replaced the one `host` entry the list below it obliges. The four implementations had each settled
  it the same way, with a comment that §7.1 "does not say which seat gives way" (`HostProducer.label`
  in `vscode_client`, `web_client` and `nvim_client`'s vendored engine; `fn label` and
  `fallback_seat` in `reference_server`'s `crates/client/src/host.rs`): a free seat first, then the
  seat whose key was committed earliest, and the host's own seat last and without eviction. §7.1 now
  states that order, the reason the last case needs a reordering relay to arise, and the residual of
  the third: a peer that holds the room key can displace the earliest commitment one window at a time.
- **The peer re-send of a state is for a room whose host is away.** §7.1 had every peer holding a
  state re-send it on every `peer.joined`, beside the host's own fresh state for the same join. A state
  carries the whole listing — up to 4 MiB at §13.3's bounds — so a join cost one copy per seated peer
  on every connection, against an outbound queue of 32 frames and 32 MiB (§2.1) that disconnects a
  peer past either. The two cases the re-send exists for — a joiner while the host is away, and a
  returning host that lost its `issued` — both arrive while the applied state's `host` entry labels a
  seat the roster lacks, so the **SHOULD** is now conditioned on that and a **SHOULD NOT** covers the
  rest. **Divergence:** every client engine re-sends unconditionally today (`PeerSession.seatJoined`
  in the three TypeScript engines); no vector pins either behaviour.
- **§13.1's host paragraph said "on every session-key announcement it accepts"**, which read as a
  state per announcement against §7.1's window bound. It now says what §7.1 says, and names
  `peer.left` beside `peer.joined`.
- **A `viewer` is as read-only as its own client, and §7.1 says so.** Every host commits an undeclared
  key as `guest` (`declared ?? 'guest'`, `declared.unwrap_or("guest")`), and a host can place no key,
  so a peer handed a `viewer` invite that leaves the declaration out of its announcement is a writer.
  §13.5 already called the role a denial between conforming peers; it now names this path to it, and
  a deployment is told not to rely on the role for a reader who must not write.
- **The frame key's nonce budget.** Every sender seals under one frame key with random 96-bit nonces
  for the life of the room, and `epoch` is reserved, so nothing rekeys. `CANONICAL.md` §6.1 now states
  SP 800-38D's 2³² bound as the room's, and has every client count the room's frames and stop at
  2³¹: a deployment cannot count sealed frames and a relay cannot read them, so the peers, which see
  every frame, are the parties a rule can bind (§1.1).
- **§13.3's no-state rejoin is once.** The **MAY** that let a client with no state rejoin instead of
  ending had no bound, and a client that took it at every window's end would hold a hostless room
  alive indefinitely — the case the ending exists for. No client takes it today.
- **Two numbers.** §2.1's inbound-budget row now says each frame is charged at least 1 KiB, which
  `selvaged`'s `budget.rs` (`FRAME_COST_BYTES`) does and the table did not say; and §8.3's awareness
  query example said a 256 KiB frame of one-byte messages is 256 000 of them, which is 262 144.

**What B.43 asks of each implementation.** Checked against every repository's `main` on
2026-09-24; none of them had an open pull request. Two items need code, items 1 and 4, each the
same change in four places. Everything else needs nothing or is optional.

1. **The state re-send is conditional (§7.1). Needed in every client engine.** A non-host peer
   re-sends the state frame it holds on `peer.joined` only when the seat its applied state's `host`
   entry labels is not in the roster, or when the state has no `host` entry. It does not re-send
   while the host's seat is seated. The host's own path, which publishes a fresh state, is
   unchanged. The roster check comes *after* the joining seat is added, so a returning host's new
   seat still triggers the re-send, because the old `host` entry labels a seat that has gone.
   - `vscode_client` `src/engine/peer.ts`, `PeerSession.seatJoined` (the
     `if (this.heldStateFrame !== undefined) this.republish(...)` branch): guard it with
     `const seat = this.hostSeat(); if (seat === undefined || !this.roster.has(seat))`.
   - `web_client` `src/engine/peer.ts`, `seatJoined`: the same guard. This is the same engine; keep
     the files identical.
   - `nvim_client` `vendor/engine/peer.ts`, `seatJoined`: the same guard. This is the vendored
     engine, so re-sync it from `vscode_client` with `scripts/sync-engine.sh` rather than editing it
     by hand.
   - `reference_server` `crates/client/src/peer.rs`, `PeerSession::seat_joined` (the
     `if let Some(frame) = self.held_state_frame.clone()` branch): guard it with
     `self.host_seat().is_none_or(|s| !self.roster.contains(s))`. Leave `announce_holds` unguarded:
     §13.7's holds re-announcement on `peer.joined` is still a **MUST**.
   - Tests for each engine: (a) with the host's seat in the roster, a non-host `seatJoined`
     publishes no state frame (the `published` count does not move for it); (b) after `seatLeft`
     for the host's seat, a `seatJoined` for a new seat re-sends the held frame byte for byte;
     (c) with a held state that has no `host` entry, it re-sends. No vector in this repository pins
     either behaviour yet. A decision vector for (a) and (b) is a follow-up here.
2. **The seat label (§7.1). Nothing to change.** `HostProducer.label`/`commit` in the three
   TypeScript engines and `label`/`fallback_seat`/`commit` in `crates/client/src/host.rs` are the
   order §7.1 now states. The code comments that say §7.1 "does not say which seat gives way" and
   cite "*its own included*" can be re-pointed at §7.1's numbered list when the file is next
   touched.
3. **The viewer declaration (§7.1, §13.5). Nothing to change.** No host engine can place a key at a
   seat, so the new **SHOULD** does not apply to any of them. A client UI that presents a `viewer`
   invite as a read-only guarantee (`web_client`'s host invite, `vscode_client`'s share command)
   **SHOULD** word it as a request the viewer's client honours, not as enforcement.
4. **The frame budget (`CANONICAL.md` §6.1). Needed in every client engine.** A session counts
   every binary frame it is delivered and every frame it seals, but not a state it re-sends
   unchanged. Once the count reaches 2³¹ it seals nothing more. A host publishes one closing and
   every session ends with its own ending, `frame-budget`, and says why. Take the number from an
   option, so that a test can set the budget to a handful of frames.
   - `vscode_client` `src/engine/peer.ts`: add `'frame-budget'` to `Ending` and `endingReason`,
     add `frameBudget?: number` to `PeerOptions` (default `FRAME_BUDGET = 2 ** 31`), count in
     `deliverOne`, `publish` and `publishState` (fresh publications only), refuse to seal in
     `publish` once spent, and end in `tickOne`, publishing the host's closing first.
   - `web_client` `src/engine/peer.ts` and `nvim_client` `vendor/engine/peer.ts`: the same change.
   - `reference_server` `crates/client/src/peer.rs`: the same, plus an `Ending::FrameBudget`
     variant carried through `relay.rs`'s `RelayEnding`.
   - Tests: with a budget of a few frames, the session ends `frame-budget` at the count and seals
     nothing after it, and a host session publishes a closing first.
5. **The no-state rejoin (§13.3). Nothing to change.** Every engine ends at the no-state window
   (`Ending` `'no-state'` in `peer.ts`) and none rejoins, which the bounded **MAY** allows.
6. **`selvaged` (`reference_server/crates/selvaged`). Nothing to change.** Every §2.1 number matches
   `lib.rs`, `room.rs`, `budget.rs` and `net/`. The 1 KiB per-frame floor is now written down, and
   the code already applies it.

**B.44 The frame budget's count is the host's.** §B.43 had every client count the room's frames and
stop at 2³¹, and claimed a client's count was the room's total. It is not: a client that joined late
counts from its own seat, so every client can stay below the budget while the room as a whole passes
the bound (a review comment on specification#58 caught it after the merge). `CANONICAL.md` §6.1 now
makes the **host's** count the room's: the host is present from the room's first frame, keeps
its count across every reconnect, persists it beside `issued` at least once per
`awareness_renew_ms`, and continues it after a reload. Every other client's count stays as the
backstop that runs while the host is away. What the host's count misses — frames sealed while it is
away — is bounded by §13.8's host-away window and §13.3's no-state window, which end the peers'
sessions. §6.1 states that bound against the 2³¹ margin. Reading the peers' envelope counters on the
host's return would close even that gap, but it would let any holder of the room key inflate its
counter and close the room at will, so it was left out.

**Two review findings on specification#59, both taken.** The first version of this entry said one
absence of the host hides at most about two host-away windows of traffic and that the margin covers
some thirty-five thousand of them. That bounds one absence and not their number, and a host that
leaves and returns inside its window keeps the room going indefinitely. `CANONICAL.md` §6.1 now
charges the count a fixed 2²¹ frames on every return — a reconnect or a reload — which bounds the
number of uncounted absences at 1024 with no rate measured and no value read from a peer. A
follow-up comment pointed out that §13.8's "MAY wait longer" left one absence unbounded in time, and
that this version fixes no sending rate. §13.8 now caps the wait at twice the window, and §6.1
states the charge's one assumption, that a room seals fewer than 2²¹ frames during an absence,
rather than presenting the charge as a guarantee. Reading
the peers' envelope counters instead was rejected again for the reason above. The second finding:
a persisted host record written before the count existed loaded as `0`, which would continue a room
that may already have sealed frames. Such a record now reads as a spent budget, and the host closes
the room at once. No implementation persists host records yet, so that costs nothing today.

**What B.44 asks of each implementation.** One change, the same in four places:

- `vscode_client` `src/engine/host.ts`: `PersistedHost` gains an optional `frames: number`.
  `HostProducer` loads it under the seed check `issued` already has. A record without it loads as
  the frame budget itself, so the host closes the room at its first tick. A record with it loads as
  that count plus the absence charge, `ABSENCE_CHARGE = 2 ** 21`. The producer takes the session's
  current count, writes it in every `save` beside `issued`, saves it from the tick at least once per
  `awareness_renew_ms` when it has moved, and saves it at once when the session ends. `FRAME_BUDGET`
  moves here from `peer.ts`, which re-exports it. `src/engine/peer.ts`: a host session starts
  `roomFrames` from the producer, hands its count to the producer as it moves, flushes on the tick,
  counts and saves the closing on both closing paths, and adds `ABSENCE_CHARGE` on every re-seat.
- `web_client` `src/engine/` and `nvim_client` `vendor/engine/`: the same change, synced or applied
  as with §B.43.
- `reference_server` `crates/client/src/host.rs` and `peer.rs`: the same, with `PersistedHost`
  gaining `frames: Option<u64>`.
- Tests: a reload continues the saved count plus the charge; a re-seat adds the charge; a record
  without a count closes the room at the first tick; the moving count is saved at least once a
  renewal interval; and both closing paths save a count that includes the closing.

**B.45 One wire version: `selvage/1` removed, and the corpus re-baselined onto `selvage/2`.**
`PROTOCOL.md`, `CANONICAL.md`, `README.md`, `schema/`, `runner/` and `vectors/`. **Decided**
(2026-09-24) by the owner: *the protocol has one wire version, `selvage/2`; version 1 is removed.*
There are no users to keep it for, and nothing may speak a wire version half the endpoints do not.

**What went.** The version machinery, whole: §10's grammar, compatibility rule and refusal; the
version gate a client made before a socket, and the `/meta` reading it was decided from; the pin a
client's own setting could be (`?wire=`, `selvage.wireVersion`, `vim.g.selvage_wire_version`, and
the `meta`/`pin` members a decision vector's `start` carried for it); the room-pinning rule; the
`unsupported_version` code and close **4005**; the `host_present` code and close **4004** and the
reserved `doc_not_open`, none of which has a fault left to name; `roles` in `/meta` and in a
`PeerInfo`; the two capabilities version 1's server machinery needed (`open-document-set`,
`host-reclaim`); `schema/session-v2.json`, whose shapes are the session layer's now and are stated
by `common.json`, `events.json`, `methods.json`, `meta.json` and `errors.json`; the subject
mutation `fall-back-to-version-1` and the link guard it removed; and `vectors/peer/158`, which
pinned that guard.

**The corpus is `selvage/2`'s, and every vector in it still pins a rule.** Twelve of the
thirty-six wire vectors are deleted, each with the rule it pinned: `004` (same-major acceptance,
which one value no longer allows — §A.9 records where the implementation still reads it), `006` and
`033` (a refused `doc.*`), `011` and `018` (the open-document set), `014` (a second host claim),
`021`–`023` (the grant), `024` (the reclaim), `025` (a close for an unheld path) and `034` (a
repeated open). `005` and `027` are deleted *and restored*: what they pinned was never version 1
but the one rule that outlives it — how a frame whose `v` the receiver does not read is refused —
so they are re-baselined onto `selvage/2` with `bad_message` in place of `unsupported_version`, and
`027` carries the seated leg that the split in §11 makes the interesting one. The other twenty-two
are re-baselined: `v` moves to `selvage/2`, `role` and `documents` leave every frame,
`capabilities` is the version's two names, and the legs that exercised a `doc.*` method became the
version's own controls. `012` is the one split — the reclaim half is gone and what is left is the
room's grace, armed by its **last** connection. `028` no longer pins version-before-method, which
no longer exists; it pins envelope-before-method and method-before-params, which does.

**Counts, re-derived from the runs and not from the old numbers.** The wire layer: 36 files →
24, 35,022 frame checks → 33,760, 8,676 assertion steps → 8,387. The peer layer: 27 files → 26
and 222 checks → 221, with its 74 assertion steps unchanged because a decision vector's steps are
counted in its own run's summary. `schema/` is ten files rather than eleven. The absence scan no
longer has a `selvage/1` corpus to be its own positive control — a corpus of the version the rule
is about cannot be — so its control is a document built to break every rule it states, and the
nine violations it must find are pinned in `validate.py`. Each number comes from a run:
`python3 schema/validate.py` (`result OK`) and `runner/run_vectors.py`.

**Four vectors the re-baseline damaged, repaired on the same branch.** The pass that rewrote the
frame in each step's `text` parsed and re-serialised it, so it collapsed the repeated member that `031`
exists to send: the vector sent a frame carrying no duplicate and still expected the refusal,
which no conforming receiver can answer — `bad_message` for a repeated member is the rule §4
states and the server correctly applies. `008`'s `chatty` leg lost the `send` a `doc.*` leg
carried and kept its expectation, so it waited on the server's hello deadline, which is the
runner's frame deadline to the tick, and passed or failed by a race. `015` and `030` lost the
control request their own prose still claimed. Each is restored with a method this version has,
`session.rename`: `031` carries both depths the rule names — a member of the envelope, where
`session.rename` and `session.hello` are what a first-wins and a last-wins reader would each
resolve the frame to, and one inside `params` — and each control is answered and announced. The
counts above are the repaired corpus's, re-derived from the same two runs, with the replay green
against `chore/drop-version-1` in `reference_server`: `24 files, 33760 frame checks, 24 vectors
passed, 0 failed`.

**What the corpus is verified against.** The `selvaged` in this workspace is built from
`reference_server`'s `main` (`c8cee0d`), which seats the one version: `crates/protocol/src/lib.rs`'s
`WIRE_VERSION` is `selvage/2`, its `/meta` advertises the one entry and no `roles`, and a `v` it does
not read is `bad_message` rather than `unsupported_version`. Against it the replay is green —
`nix develop . -c python3 runner/run_vectors.py --layer wire --server
<reference_server>/target/debug/selvaged` ends `24 files, 33760 frame checks, 24 vectors passed,
0 failed`. The reference
server's own `crates/harness/tests/vectors.rs` is the other half of the same gate, and it replays
this corpus over its vendored copy.

**The prose is one document now.** §1.2's entries, §2 and §2.1, §3, §4.1, §5's method surface and
its handshake, §6's events and replies, §7.1, §8's awareness passage, §9, §9.1, §9.2, §10, §11's
vocabulary, §12 and §13 are written for the one version: no sentence of `PROTOCOL.md` describes a
second wire, and §10 is "The wire version, and capabilities" — the one value `v` has, the refusal a
frame naming another is answered with, and the capability table. §10, §10.1 and §13 keep their
numbers so that a citation in a dated record still lands.

**B.46 The revision's review: what the sealed frame's marks and §13.3's declaration leave open.**
`CANONICAL.md` §6.1 and §2.4, `PROTOCOL.md` §13.3 and §13.5, `reference_server`'s
`crates/client/src/sealed.rs` and `vscode_client`'s `src/engine/sealed.ts`. **Open** (2026-09-24), each
one a place where the prose and the code say different things rather than a decision anybody has
taken. What they sit under is sound: the room id, the kind, the epoch and the key id are inside
§6.1's associated data and the counter is inside the signed input, so a relay cannot move a frame
between rooms, between kinds or between senders, and truncating one breaks its signature. None of the
six below lets a relay change what a conforming receiver *does* with a frame. The review that found
them is `ai_notes/docs/review-one-wire-2026-09-24.md`.

- **A receiver that holds no mark yet accepts an old `kind = 0` or `3` frame.** §6.1's mark starts
  at `0` for a key, and `Reader::replayed` is
  `counter <= self.marks.get(&id).map_or(0, |mark| mark.counter)` — so a receiver with no entry for
  the sender is handed `0` to compare against: counter `0` is refused there, and every positive
  counter passes. A joiner has accepted nothing from
  anyone and is the case that matters: a frame the room published before it joined carried a
  positive counter, which is what the mark check does not reject. What
  the prose has to state is what a receiver holding no mark does with those two kinds, which is the
  rule §6.1 already states for a mark that exists.
- **A non-minimal `varUint` is read while the signed input is written minimally.**
  `crates/client/src/sealed.rs`'s `read_varuint` and `vscode_client/src/engine/sealed.ts`'s
  `readVaruint` both accept a value whose continuation bytes could have been written shorter —
  `0x80 0x00` for `0` — and `signing_input` re-encodes `varuint(counter)` minimally, so a relay can
  change a frame's bytes without breaking its signature. No value changes and no conforming receiver
  behaves differently; what is open is whether §6.1's bytes are canonical as evidence or only as a
  value.
- **The TypeScript reader refuses a counter above 2⁵³−1 where Rust reads on.** `readVaruint` returns
  `undefined` past `Number.MAX_SAFE_INTEGER` while the Rust reader holds the same bytes in a `u64`,
  so the two readers disagree about one frame's bytes — the disagreement §2.4's bound on every count
  exists to prevent.
- **Marks are kept per 8-byte id where §6.1 says "for each key it holds".** `Reader::marks` is
  a `HashMap<KeyId, Mark>` and `KeyId` is the first 8 bytes of the key's hash, so two keys sharing an
  id share a mark. A collision is not a misattribution — `read_ordinary` verifies every key the id
  names and the frame belongs to the one that verified — but it is one peer's counter refusing
  another's frame. Either the prose says `key_id` or the code keys the map by the key.
- **A `viewer` can declare itself a writer.** §13.3 lets a client announce the role it believes it has
  been given, `guest` or `viewer` and never `host`, and the first half of the rule it states is
  implemented: `crates/client/src/host.rs`'s `commit` writes `declared.unwrap_or("guest")`, so a
  declaration is honoured. The half that is not implemented is the **SHOULD** beside it — a host that
  has its own reason to hold a seat read-only, an invite it handed out as `viewer` alone, should
  commit that key as `viewer` whatever it declares. `commit` never consults the invite, so a `viewer`
  that announces `guest`, or announces nothing, is committed as a writer and writes. It is a missing
  implementation rather than a wording difference, and §13.5's residual states the cost in the same
  breath: a `viewer` is exactly as read-only as its own client.
- **The relay can make the host look like a guest in the roster.** Withholding a joiner's
  `peer.joined` leaves a seat the roster does not carry, and the roster is the server's word about
  names. The authority does not move — the state names the host and the state is verified against the
  host key the invite's fragment carries — and §13.3 already says that the one binding not signed is
  `peer_id`, the label a host writes into the state from the roster it was sent. What is open is
  whether the prose says what a client draws when the roster and the state disagree, which today it
  does not.
