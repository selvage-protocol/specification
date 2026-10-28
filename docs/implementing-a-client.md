# Implementing a client

This guide is informative. `PROTOCOL.md` and `CANONICAL.md` are the specification and nothing here
changes them; where this page and the specification disagree, the specification wins. It follows an
order a client can be built and tested in, without reading the reference implementation, and each
stage ends where the next can start.

The wire has one version, `selvage/2`. A frame names it in `v`, `GET /meta` advertises it, and a
frame naming anything else is refused `bad_message` (`PROTOCOL.md` §10, `CANONICAL.md` §2.5). There
is no version to negotiate and none to fall back to. [Versioning](versioning.md) lists the three
numbers that move together, and the release bundle an implementation pins against.

## Stage 1: transport, framing and canonical bytes

A session is one WebSocket at `/session` and one plain-HTTP metadata endpoint, `GET /meta`, on the
same listener (`PROTOCOL.md` §2). Text frames are JSON envelopes in one of three shapes: a request,
a response, or an event. Binary frames are sealed frames and are not JSON (§4, §7.1).

Every text frame is the UTF-8 encoding of one JSON object in the canonical form of `CANONICAL.md`
§2: members in ascending order of member name; no whitespace between tokens; the three escapes of
§2.3; counts in plain decimal and omitted when absent; nesting no deeper than 127. A receiver
tolerates any member order and whitespace, ignores unknown members at any depth, and rejects a
frame that is not one object, has no `v`, repeats a member name, or gives a member the wrong type
(`CANONICAL.md` §3, §4, §5; `PROTOCOL.md` §4). An unknown method is different: it is answered
`unknown_method` and never dropped, because silence turns version skew into a timeout
(`PROTOCOL.md` §4.2, §5). When one frame carries more than one fault, the envelope, its `id` and
its `v` are judged first, then the method, then the params (`PROTOCOL.md` §9.2, §11).

`GET /meta` is a convenience and not a decision point. Read it when it answers, adopt its
`keepalive` if you like, and connect anyway when it does not: the handshake decides (`PROTOCOL.md`
§2, `CANONICAL.md` §7). The schema file `schema/negotiation.json` carries the version constant, the
capability grammar and the advertised `keepalive` block; its filename predates the one-value rule
and does not mean the protocol negotiates a version.

Build the canonical encoder and decoder, the envelope types and the handshake first. The machine
readable shapes are `schema/envelope.json`, `schema/common.json`, `schema/methods.json` and
`schema/meta.json`, with the numeric bounds in `schema/limits.json`; Stage 6 checks the set.

## Stage 2: the invite, the sealed frame and roles

An invite has two forms that name the same address: the connection URL and the page link
(`PROTOCOL.md` §5.1). Both carry `room` and `token` in the query and the room's two keys in the
fragment, `k` for the room key and `h` for the host's public key, each 32 bytes in base64url
without padding. The fragment is the part a user agent never sends, which is why the keys travel
there. A client strips the fragment before opening the socket and keeps invite URLs out of logs.
The server never echoes the token after `room.created`; clients retain it and send it in the
connection URL on every join or reconnect, including a returning host (§5.1, §9.1).

Several refusals happen locally, before a socket is opened, with no wire form: a fragment that is
absent, that repeats `k` or `h`, or that does not decode to a 32-byte value; and a page link that
repeats `room` or `token`, because deriving the connection URL would have to choose a value
(`PROTOCOL.md` §5.1). Name the parameter the refusal is about in your own words. A client must
accept either invite form and join the room it names.

A sealed binary frame is `CANONICAL.md` §6.1's bytes, and a client should treat that section as the
codec specification rather than hand-write a second interpretation of it. §6.1 fixes the envelope's
field order, the two fragment values, the base64url encoding and its canonical spelling, the frame
key derived once per room, the AES-256-GCM associated data and signature input, the `key_id`, the
counter and its mark, and the order a receiver reads a frame in with the ten reasons it reports a
refusal by. What a client must supply itself, each with its requirement fixed there:

- keys and nonces drawn from the platform's CSPRNG, with a 96-bit nonce fresh for every frame and
  never derived from a counter or another shared value;
- a counter that starts at 1 and strictly increases under each key, and a per-key mark that starts
  at 0 and never moves for a frame that was refused;
- a per-connection Ed25519 session keypair, minted in memory and never persisted. Announce it in
  a `kind = 4` frame before sending other binary frames. The host bootstrap exception permits a
  host-signed state that commits its own session key without a preceding announcement
  (`PROTOCOL.md` §13.1 step 4, §7.1);
- the host key, which signs `kind = 1` (the room state) and `kind = 2` (the closing), while a
  session key signs `kind = 0` (document content), `kind = 3` (holds) and `kind = 4`.

Roles are the host's, not the server's: no server frame carries one, and a receiver reads a
sender's role from the key that verified against the applied room state (`PROTOCOL.md` §7.1,
§13.4). `schema/sealed.json` models the four sealed payloads and the closed refusal vocabulary.

## Stage 3: the listing, holds and content on disk

Document sync follows y-protocols (`PROTOCOL.md` §7). One session-wide `Y.Doc` holds a `Y.Text`
root named by each open path (§8.1). Yjs updates identify those roots through their encoding;
the sealed envelope adds no per-document address. The public [anchors fixture](anchors.md)
includes an initial update encoding the root name `src/main.rs`. Two peer facts shape a client:

- The **listing** is the host's sealed room state (`kind = 1`): the files its working copy held
  when it enumerated them, replaced wholesale by every state, ascending by UTF-16 code unit
  (`PROTOCOL.md` §7.1, §13.3).
- The **holds** are each connection's sealed claim about the paths it keeps open (`kind = 3`); the
  room's open-document set is the union of the seated peers' live holds, and no server frame carries
  it. A hold is renewed on the `awareness_renew_ms` clock from the last accepted holds message and
  expires after `awareness_expire_ms` (`PROTOCOL.md` §13.7).

A path is a name and never a promise that a file exists. The wire does not validate one, so a
receiver applies `PROTOCOL.md` §5's rule where it meets a path: a blank path, one carrying a
control character, or one over the byte bound is dropped while the rest of the listing or hold set
is applied, and neither the frame nor the state is refused (§13.3, §13.7). `..`, an absolute path
and a name that escapes the folder can arrive from a peer even though they are unsafe to resolve
against a local filesystem.

Apply content updates to the session document; their Yjs root and parent references determine
which text they affect. An unchanged update cannot be redirected to another path by adding a
wrapper. The sync exchange covers text already introduced into the session document, with no
per-path wire fetch method (`PROTOCOL.md` §7, informative). Desktop clients can mirror the listing
into a real directory and materialize text on demand; browser guests can edit without a local
folder. Three clients' public docs describe their storage choices:
[mirror and fetch](https://github.com/selvage-protocol/vscode_client/blob/main/docs/mirror-and-fetch.md),
[grant](https://github.com/selvage-protocol/nvim_client/blob/main/docs/grant.md) and
[hosting from the page](https://github.com/selvage-protocol/web_client/blob/main/docs/hosting-from-the-page.md).

Reading the host's disk is the host's own job, and the protocol supplies no confinement for it
(`PROTOCOL.md` §12): an implementation that reads the filesystem confines the path itself, and the
folder is the bound. In a desktop mirror, saving writes the guest's local copy. The hosting
client separately saves settled room edits to the host's working folder.

## Stage 4: awareness and anchors

Awareness is a y-protocols `message_type = 1` frame whose state this protocol shapes as `path` plus
`selection` (`PROTOCOL.md` §8.1, `schema/awareness.json`). The two endpoints of a selection are
yjs `RelativePosition` anchors and never offsets. No index crosses the wire, so each receiver
recomputes the offset against its own replica and a cursor survives a concurrent edit.

Two shapes name one position and a receiver must resolve both: `tname` beside `item`, which a yjs
client writes for a position in a root type, and `item` alone, which another library writes. A
`tname` with no `item` denotes an end of the text, chosen by `assoc`. If either endpoint fails to
resolve, the state carries no selection, and a receiver must not fall back to an offset or clamp to
a guess (`PROTOCOL.md` §8.1, §8.1.1). The [`vectors/anchors/`](anchors.md) fixture holds the two
libraries' shapes for one document so a crossing test can hold them to it.

The offset unit is fixed at the adapter seam and must be the unit the editor uses. Where that is
the UTF-16 code unit, the unit a JavaScript string is indexed in, a client on a library that
defaults to another unit must configure it for UTF-16, or a non-ASCII document yields a wrong
anchor before concurrency even enters (`PROTOCOL.md` §8.1.1; `NOTES.md` §A.11 is informative).

Renew and expire presence on the advertised `keepalive`, not numbers of your own, and stop any
library timer that would run a second clock (`PROTOCOL.md` §8.2). That clock is also the holds
lease Stage 3 uses.

## Stage 5: reconnect, history and the room's life

A reconnect is a new `session.hello` on a new socket and the connection is a new peer: nothing
about the old connection survives, not its `peer_id`, its holds or its awareness state. Two things
do, because the room told the client: the highest `issued` accepted and the counter mark for each
key (`PROTOCOL.md` §9.1, §13.10; `CANONICAL.md` §6.1). A reconnecting client announces the paths it
still holds again, mints a fresh session key and awareness client id, and gets the room's content
through the finite SyncStep1/SyncStep2 exchange, because the server replays nothing (`PROTOCOL.md`
§7, §13.6).

A client that refused a content frame re-syncs, and a writer's first request activates one
reciprocal request. Both coalesce into at most one locally generated SyncStep1 per
`awareness_renew_ms`. A `viewer` sends only its SyncStep1, its awareness and its holds, and a
receiver must not apply document content from a key the state gives `viewer` (`PROTOCOL.md` §13.5,
§13.6, §13.9). A viewer's local edits are never room history, even after a later promotion or key.

The room lives while it has connections and for `room_grace_ms` after its last one ends, so a
retry should keep trying at least until that window has passed (`PROTOCOL.md` §9, §9.1). Two
client-side clocks come from the same advertised `keepalive`: the no-state window a client seated
with no verified state waits, and the host-away window, `awareness_expire_ms`, after which a client
holding a state whose `host` entry labels an absent seat must end (`PROTOCOL.md` §13.3, §13.8). A
host that means to keep hosting must persist its private host key with its `issued` and the room's
frame count beside it (`PROTOCOL.md` §7.1, §9.1; `CANONICAL.md` §6.1).

`room_unknown`, `token_invalid` and any `x.` capacity code are terminal, so stop and say why rather
than reconnect into a retry that cannot change the answer (`PROTOCOL.md` §9.1, §11). Keep the
invite token, since nothing echoes it after `room.created`, and keep the invite URL out of logs and
history.

The [reconnect contract recipes](reconnect-contract-recipes.md) are deterministic test recipes for
the loss, fresh-key, role-transition and bounded-count cases the corpus cannot yet express. They
are recipes, not vectors, and they do not prove a particular client passes.

## Stage 6: check it against the corpus

The corpus has two layers (the repository README states the split). The **wire** layer's subject is
a server: `vectors/*.json` replayed by `runner/run_vectors.py`. The **peer** layer's subject is one
client: `vectors/peer/*.json`, the frame layer replayed with no client by `runner/run_peer.py`, and
its decision vectors driven against a client you name. The runner is a format and self-consistency
check written from the same prose, not an independent implementation, so agreement with it is weak
evidence ([What the vectors do not cover](what-the-vectors-do-not-cover.md)).

### Commands

Run the Python commands from this specification checkout. Schema validation needs `jsonschema`
and `referencing`; wire replay also needs `websockets`, and peer replay needs `cryptography`.
Each entry point's `--help` describes its flags once those dependencies are available.

```
# this checkout, with jsonschema and referencing
python3 schema/validate.py

# this checkout, with a selvaged built from reference_server
SELVAGE_SELVAGED=/path/to/selvaged python3 runner/run_vectors.py
SELVAGE_SELVAGED=/path/to/selvaged python3 runner/run_vectors.py --layer all

# this checkout, the peer layer
python3 runner/run_peer.py
python3 runner/run_peer.py --vector 102
python3 runner/run_peer.py --list-mutations
python3 runner/run_peer.py --mutation no-verify
python3 runner/run_peer.py --mutation-census
python3 runner/run_peer.py --subject "./my-client --drive"

# from the reference_server checkout, whose flake supplies the toolchain
nix develop . -c cargo test -p selvage-harness --test vectors
nix develop . -c cargo test -p selvage-harness --test peer_vectors
nix develop . -c cargo test -p selvage-harness --test decisions
```

`run_vectors.py` needs `SELVAGE_SELVAGED` or `--server` and replays the wire layer; `--layer all`
reports the peer layer it cannot run rather than passing it. `run_peer.py --subject` expects a
JSON-lines subject adapter implementing the contract in [`runner/subject.py`](../runner/subject.py),
not an ordinary editor executable. It drives decision vectors against that adapter; without a
subject, those vectors are reported as not attempted and do not count as passes.

`--mutation-census` checks each applicable vector against the guard mutations it declares.
Positive controls must remain green under every mutation. Decision-vector census entries are
not attempted without a subject, so a subjectless census does not cover client decisions.

### What the corpus proves, and what it does not

A green `run_vectors.py` summary says a server produced the bytes the transcripts claim. It does
not say that two clients agree, that a differently wrong client fails the mutation census, or that
the work is production ready. The interop test
[`vscode_client/test/interop-v2.test.ts`](https://github.com/selvage-protocol/vscode_client/blob/main/test/interop-v2.test.ts)
drives the shared TypeScript engine against
[`reference_server/crates/harness/examples/interop_peer.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/examples/interop_peer.rs):
that is a convergence proof between two implementations and not a conformance one. The anchors
fixture is exercised on the client side by
[`vscode_client/test/crossing.test.ts`](https://github.com/selvage-protocol/vscode_client/blob/main/test/crossing.test.ts).

The vector corpus also does not reach physical disk or an editor's I/O. A client should add its own
tests for the seams the corpus cannot see: a file deleted or renamed on the host's disk while the
room holds it; an insertion or deletion boundary between the halves of a surrogate pair; a `viewer`
role applied to the client's own key; a reconnect that keeps history; a close and reopen of the
same document; and a save the host's filesystem refuses. Each is a real path through the client,
and a corpus pass says nothing about any of them.
