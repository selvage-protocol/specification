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
- The client's request surface is the handshake, `doc.open`, `doc.close`, `session.rename` and `doc.grant`;
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

### A.4 The `x.` method surface

`PROTOCOL.md` §10.1 says the wire is open: a server answers any method it does not implement with
`unknown_method`, so an extension method travels like any other. A *client* is the constraint:
both reference clients can send the handshake, `doc.open`, `doc.close`, `session.rename` and
`doc.grant` and nothing else, so an editor adapter behind them cannot ask for an `x.` method at all.
An implementation that defines one has to expose a way to send a method whose params it does not
interpret; neither reference client has made that decision. An `x.` **event** needs nothing of the
sort, because events reach a client that has already promised to ignore the ones it does not know.

### A.5 `GET /meta`

Hand-rolled on the WebSocket listener: no keep-alive, no routing, and the request method is not
inspected, so `GET`, `POST` and `HEAD /meta` all answer `200` with the same body. Answering `HEAD`
with a body deviates from RFC 9110. It is fine for negotiation, and it should be replaced rather
than extended if a real HTTP surface is ever needed (`B.14`). The body is written in canonical
member order (`CANONICAL.md` §2.1), which the server's `Meta` struct achieves by declaring its
members in ascending order.

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
token in a URL leaks into logs, history and referrers (`PROTOCOL.md` §12). The alternative, with
everything in `session.hello`, makes the invite link a non-URL, or makes the client compose it.
**Unresolved.**

**B.2 The host role is claimed, not proven.** Any holder of the token may send `role: "host"` and,
when the room is hostless, become the host: keeping the room alive, or ending it by leaving. Since
the same token already grants read access to the whole working copy, this grants nothing new
*today*, but it stops being harmless the moment the token is shared more widely than the host's
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
implementation: a query a client receives is answered, and an auth message is ignored
(`PROTOCOL.md` §8.3). Whether `selvage/1` should *forbid* them, or keep them available for a future
profile, is **unresolved**.

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

**B.26 A one-sided drop has no liveness bound.** A socket can die at one end while the other stays
open (a roaming client, a hung relay, a half-open TCP connection), and every party is individually
conformant. The server never ends a session for silence (`PROTOCOL.md` §2.1, §9), so it goes on
holding the connection as the room's host; the remaining guests never receive `host.detached`, so
no grace period starts; the real host reconnects and is refused `host_present`; and the room stays
hosted by a peer it cannot displace while its guests keep editing into a room nobody can reclaim.
`PROTOCOL.md` says nothing about detecting liveness, so the composition hangs where each half is
right: the server for keeping the session, the client for stopping at a refusal. Whether
`selvage/1` should require a liveness bound, and where it belongs (the server treating a missed
WebSocket Pong as a fault after some window, or a `host_present` claim becoming reclaimable by the
token holder) is **unresolved**, and it is wire-visible because it decides whether a session ends,
so it is a decision and not wording. The reference server is adding a liveness bound; the spec does
not yet bind one.
