# The Selvage Session Protocol specification

This repository specifies Selvage, a collaborative live-coding protocol: the *session* layer of
rooms, participants, roles, the open-document set, presence, and join and leave, with document sync
and awareness carried through it. It is written to be implemented on its own, without reading the
reference implementation in
[`selvage-protocol/reference_server`](https://github.com/selvage-protocol/reference_server) or the
editor clients in
[`selvage-protocol/vscode_client`](https://github.com/selvage-protocol/vscode_client) and
[`selvage-protocol/nvim_client`](https://github.com/selvage-protocol/nvim_client). The design
record, `DESIGN.md`, is not published; it is cited below by section.

## Get it working

[`PROTOCOL.md`](PROTOCOL.md) is the specification and it is normative: §1.1 says which sentences
bind a reader and what a conforming implementation is. [`CANONICAL.md`](CANONICAL.md) is **SJ-C/1**,
the byte form of a session text frame, normative for the bytes. The [table
below](#what-lives-here) lists the rest of the repository.

Checking the corpus takes a couple of minutes and needs no server and no Rust:

```
pip install jsonschema referencing cryptography   # or: nix develop  (the same set, pinned)
python3 schema/validate.py
```

A green run prints one line for the schemas, one for the sealed payloads, one for the reasons a
refused sealed frame is reported in, one for `selvage/2`'s session layer, one for each layer of
the corpus, one for the absence scan, and `result OK`:

```
schema ok      11 schemas, 39 values checked against the control-character refusal
sealed         48 values checked against the sealed payloads of selvage/2
refusals       13 values checked against selvage/2's local report vocabulary
session v2     42 values checked against selvage/2's session layer
vectors        36 files, 35022 frame checks, 8676 assertion steps
peer vectors   25 files, 19 frame, 6 decision, 210 checks, 74 assertion steps
absence        8 selvage/2 shapes, 25 vectors of this version, 8636 selvage/1 frames (8742 carrying a deleted member, 197 a deleted event), 50 sealed frames, 100 needle checks
result         OK
```

Those counts are pinned in `schema/validate.py`, so a deleted vector, frame check or
assertion fails the run. The [section below](#validating-the-schemas-and-the-vectors) lists what
the run checks.

**The corpus has two layers.** The **wire** layer is a transcript and the server is the subject:
`vectors/*.json`, `runner/run_vectors.py`, and everything this file said before the peer layer
existed. The **peer** layer holds a client to the rules a server cannot enforce — verify before
apply, a counter mark, a signature, a lease, a role — and it is `vectors/peer/*.json` with two
runners: `runner/run_peer.py`, which replays its frame vectors with no client at all, and the same
runner driving its decision vectors against a **subject** — a client, named by a command.
`schema/validate.py` checks both layers and needs no key for either. The runner is the relay for
the decision layer: it seals each vector's recipes and hands the bytes over, so the only thing
that layer still needs is a subject, and without one it reports those vectors as **not attempted**
rather than passing them.

`cryptography` is there for the peer layer's frame code alone: AES-256-GCM, HKDF-SHA256 and
Ed25519. `python3 schema/validate.py` itself still needs only `jsonschema` and `referencing`, and
none of the sealed frames is derivable without a key.

`python3 schema/validate.py` checks the frames the vectors contain; it cannot check that a server
produces those bytes. To replay the corpus against a real server, see [Replaying the vectors
against a server](#replaying-the-vectors-against-a-server).

`scripts/ci-local.sh` runs the same commands as `.github/workflows/validate.yml`, one flake check
per step, plus actionlint over the workflow files, and needs `nix`:

```
scripts/ci-local.sh all      # lint + validate
scripts/ci-local.sh validate # the flake checks alone
```

Its `validate` half refuses to run when `schema/`, `vectors/`, `runner/` or the flake files differ
from `HEAD`, because CI checks out the committed ref. Commit before running it; staging is not
enough.

## What lives here

Six things live here, and they are meant to be read together. The first is the specification; the
other five are what an implementation checks itself against and reads where the specification is
silent, without reading the Rust.

| Path | What it is |
|---|---|
| [`PROTOCOL.md`](PROTOCOL.md) | **The specification.** What the members mean, and what a conforming peer must, should or may do. §1.1 says which sentences bind a reader and what conformance is. |
| [`CANONICAL.md`](CANONICAL.md) | **SJ-C/1**, the canonical byte form of a session text frame, and §6.1, the byte form of a `selvage/2` sealed frame. Normative for the bytes. |
| [`schema/`](schema/) | The machine-readable model: JSON Schema 2020-12, one file per concern, plus `validate.py`. |
| [`runner/`](runner/) | The language-neutral replay. `run_vectors.py` starts a server and replays every wire transcript against it; `run_peer.py` replays the peer corpus's frame layer with no server and no client; `sealed.py` is `CANONICAL.md` §6.1 in Python; `subject.py` is the protocol a decision vector drives a client through. None of it needs a Rust toolchain. |
| [`vectors/`](vectors/) | Versioned transcripts of real bytes, replayed by the reference server's [`crates/harness/tests/vectors.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/vectors.rs) and by `runner/`. `vectors/peer/` is the peer corpus and `vectors/fixture/` is its keys. |
| [`NOTES.md`](NOTES.md) | **Informative.** What the implementations do where `PROTOCOL.md` does not bind them, the decisions this draft had to make, and what is still open. Nothing there is a requirement. |

`DESIGN.md` §7 asks for "CC-BY prose plus JSON Schema", and §13.4 asks for the machine-readable
model to be built early because prose drifts. This repository is that, plus the two things prose
alone cannot carry: a byte-level rule and a suite.

## Validating the schemas and the vectors

`python3 schema/validate.py` checks the schemas as schemas, and checks every schema-eligible frame
in every vector against them. It prints a line for the schemas, a line for the corpus counts, and
`result OK`. It checks:

- every `*.json` in `schema/` is a valid JSON Schema 2020-12 document;
- every `send` text frame not marked `refused` and every `expect` text frame in every vector
  parses, validates against `schema/session.json`, and validates against the params schema of the
  method or event it names;
- that a refused `send` frame parses unless it is also marked `unparsable`, in which case it must
  not parse;
- every `expect` and `expectBody` frame is written in the canonical byte form of
  `CANONICAL.md` §2, since the bytes a vector claims are the bytes it has to be written in, and
  every `expect` carries the canonical spelling of the version (§2.5);
- every `expectBody` against `schema/meta.json`;
- every `expectClose` code against the close-code vocabulary in `schema/errors.json`;
- every binary `frame` description for the shape the runner can use it in, and the awareness
  state inside it against `schema/awareness.json`;
- that every vector asserts something, and that the corpus still holds the number of vectors,
  frame checks and assertion steps it is pinned to, so a deleted assertion is a red run
  rather than a smaller number in a line of output;
- that a vector is bound to `selvage/1` and `SJ-C/1`;
- that the schema itself refuses a control character in a `display_name`, a `path` and each
  member of a `paths` or `documents` list, which is `PROTOCOL.md` §5's Unicode `Cc` exclusion:
  nothing else in the corpus exercises it, because a frame sent on purpose to test a refusal is
  not schema-checked;
- that the error and close codes each vector asserts are the ones `EXPECTED_CODES` pins for it, so
  a substitution inside a closed vocabulary (`unknown_method` for `bad_params`, say) is a red run
  rather than a corpus that keeps every count and quietly asserts something else;
- that `schema/sealed.json` describes what `selvage/2` carries sealed — the room state, the
  closing, the holds and the session-key announcement — by running it against values that must
  validate and values that must not,
  and, beside them, the vocabulary a receiver reports a refused sealed frame in, which `PROTOCOL.md`
  §13.2 binds a client to produce. Step 8 is read as `CANONICAL.md` §6.1 reads it — an object of the
  members its `kind` fixes, each member of the type that kind gives it — so a value a rule elsewhere
  re-homes is one of the values that **must** validate: a listing whose path carries a control
  character and a holds message whose set does, because `PROTOCOL.md` §13.3 and §13.7 have a receiver
  drop such a path rather than refuse the frame or the state, and a model that refused it would put
  two conforming receivers at odds about one state. The peer corpus reaches both now: `vectors/peer/117`
  seals a state whose `listing` carries a control-carrying path and a holds message whose set does,
  so this check is the model's own half of a rule the corpus pins in bytes rather than the only half.
  A sealed frame is bytes rather than JSON, so a vector cannot hand these values to the schema check
  directly; what keeps it here is that the rule re-homed from the server to the peers is pinned in
  the model and in the corpus at once, and one of the two going slack is a red run in the other.
  The report vocabulary is a closed *value* rather than a member, which
  is the one thing a schema here can refuse: it is pinned to `CANONICAL.md` §6.1's table in both
  directions, so a reason removed from it, a reason added with no rule behind it, and the reason a
  reader expects and cannot have (`bad_tag`, because the signature covers the ciphertext) are each a
  red run.
- that `schema/session-v2.json` describes the shapes `selvage/2`'s session layer states and
  `selvage/1`'s do not — a `/meta` body of four members whose `wire_versions` name `selvage/2` or a
  later minor and whose `keepalive` carries `room_grace_ms`, a `PeerInfo` without `role`, the two
  replies to `session.hello` without `documents`, the `peer.joined` whose params that peer record
  is, and the fault vocabulary of a server that seats nobody as the host — the same way and for
  the same reason: `/meta` carries no `v`, the transcripts are `selvage/1`'s until the corpus is
  re-baselined, and nothing else in this suite reaches a frame of that version. It pins the
  tolerance as well as the shape: a body, a peer record, an event or a reply carrying a member the
  version does not define must still validate, because `PROTOCOL.md` §4.1 has a receiver ignore one,
  and a capability of a server's own is one of those. The fault code is the exception, and it is a
  closed value rather than a member: the two codes `PROTOCOL.md` §11 takes out of this version —
  `host_present` and the reserved `doc_not_open` — must validate under `selvage/1`'s vocabulary and
  be refused by this one, which is checked in the same run.
- every peer vector's steps, recipes and refusals: the version and layer it declares, a `kind` of
  `frame` or `decision` with the step vocabulary that kind has, each `seal`/`deliver` recipe's
  shape — a fixture key that exists, a count, a nonce of twelve bytes, exactly one of `plaintext`
  and `payload` — every `expectReject` reason against §6.1's closed vocabulary, and the mutation in
  `catches` against the layer's own table. It also pins the peer layer's own counts and two
  censuses: `EXPECTED_REFUSALS`, which vector asserts which reasons, and `EXPECTED_MUTATIONS`,
  which vector must go **red** under which removed guard. The second is the one pin in this corpus
  that is a statement about what the corpus catches rather than what it contains, and it is
  `runner/run_peer.py --mutation-census` that drives it.
- the absence rule, three ways. **The model**: every `selvage/2` session shape is walked for a
  member no server-authored frame of that version may carry — a `role` made a property or a
  required entry of `session-v2.json` is a red run, which is the structural half and the strongest
  of the three. **The frames**: every peer vector declares the strings its own plaintext names, and
  none of them may appear in the bytes of a frame it seals, which needs no key because the
  ciphertext is in the vector. **The control**: the same walk is run over the `selvage/1` corpus,
  where it must find something — 8,335 frames naming `role`, 397 naming `documents`, 197 naming one
  of the five events this version deletes — and those counts are pinned, because a scan that read
  nothing reports zero and an unpinned zero is a green line rather than a check.

Testing a refusal means sending a frame the server must reject. Such a frame carries
`"refused": true`, and its params are not schema-checked. When the frame is not JSON at all, it
carries `"unparsable": true` as well: the two markers are different claims, the first that the
frame is well formed and refused for what it says, the second that a parser refuses it. An
`unparsable` marker left on a frame that does parse is a red run: otherwise the run reports OK
with an assertion that never ran.

`SELVAGE_VECTORS=DIR` reads the transcripts from another directory; both halves honour it, and
`SELVAGE_PEER_VECTORS=DIR` moves the peer layer alone. Replaying a corrupt *copy* is how a failure
is shown to be caught. The counts are this repository's, so either half fails on a directory that
does not hold them rather than checking less of it quietly.

## Replaying the vectors against a server

`schema/validate.py` checks the shape of every schema-eligible frame; it cannot check that a server
produces the bytes. `runner/run_vectors.py` does, and needs no Rust toolchain: it starts a `selvaged` on an
ephemeral port, replays each transcript against it over a real WebSocket, and compares what comes
back, the text frames structurally and then byte for byte, the binary frames byte for byte, and the
document and awareness state once a frame is applied.

It needs Python 3 and four packages:

```
pip install websockets jsonschema referencing cryptography
# or: nix develop, in this repository, for the same four packages at the pins the workflow installs
```

`cryptography` is the peer layer's (`runner/sealed.py`); replaying the wire layer alone does not
need it.

and a `selvaged`, which is not part of this repository:

```
cd ../reference_server
nix develop . -c cargo build -p selvaged
export SELVAGE_SELVAGED=$PWD/target/debug/selvaged
```

Then, from this directory:

```
python3 runner/run_vectors.py
```

Against a `selvaged` that implements every frame the corpus covers, it ends with:

```
summary        36 files, 35022 frame checks, 36 vectors passed, 0 failed
```

The red line to expect is a vector that pins behaviour newer than the server you point it at; the
summary names the file and the frame it disagreed about.

It exits non-zero if any vector fails. A `selvaged` must accept `--room-grace-ms MS` and
`--serve-version-1-only`: the grace period is per-vector (`vectors/012` waits out 400 ms,
`vectors/011` four seconds), and a runner that spawns the server has no other way to set it, while
every vector in this corpus is bound to `selvage/1` and two of them — `001`'s `/meta` and `005`'s
refused `selvage/2` hello — are claims about a server that seats that version alone, so the runner
starts the server that way. `SELVAGE_VECTORS=DIR` reads the transcripts
from another directory, the same escape `schema/validate.py` honours, and the replay holds the
same pin on the file count before it starts. `--schema-only` is exactly
`python3 schema/validate.py` and starts no server.

`python3 runner/test_runner.py` checks the replay's comparison code (`matches`,
`expected_bytes`, `check_text` and `check_frame_spec`) with frames whose answer is known, in
both directions, so a comparison that stopped failing is itself a red run. A comparison that
only ever runs against a server never runs in CI. It checks the peer layer's code the same way
and for the same reason: the envelope's layout, the counter mark, the refusal vocabulary, the
subject protocol's framing and deadlines, and the equality of the two halves' step tables.
`python3 runner/test_yprotocols.py` checks
the binary decoder, the one part of the replay that is hand-written, from the vectors. CI runs
both, and the live replay runs in the reference server's own suite over its vendored copy of the
corpus, because this workflow has no Rust toolchain to build a `selvaged` with.

## Replaying the peer corpus

`runner/run_peer.py` needs no server, no client and no Rust toolchain. It reads
`vectors/peer/*.json` and `vectors/fixture/keys.json`, seals every recipe, signs it, and reads
what it produces in the order `CANONICAL.md` §6.1 fixes:

```
python3 runner/run_peer.py                      # every frame vector
python3 runner/run_peer.py --vector 102         # one of them
python3 runner/run_peer.py --list-mutations     # the guard each mutation removes
python3 runner/run_peer.py --mutation no-verify # with one guard taken out
python3 runner/run_peer.py --mutation-census    # what each vector must go red under
python3 runner/run_peer.py --subject "my-client --drive"   # and the decision vectors, with a client
```

**A vector carries a recipe, and the bytes are derived.** `seal` says what is sealed — the fixture
key that signs it, the `kind`, the counter, the nonce, and the plaintext as hex or as the JSON
object it is — and the vector also carries the `hex` the recipe produces. Both are claims:
`runner/test_recipe.py` re-derives every frame in the corpus from its recipe and asserts the two
agree, so a recipe that drifted from the bytes it explains is a red run rather than a quiet
difference. The one place a vector carries bytes that are not derivable is a deliberate
corruption, and the corruption is a step of its own — `{"op": "corrupt", "frame": "edit",
"as": "tampered", "at": 40, "xor": 1}` — so it is re-derived too.

**The decision layer runs a client, and the runner says which client.** A `"kind": "decision"`
vector is about what a real client did with a frame it received — what it applied, what it dropped
and why, what it published, whether it ended — so it needs a subject, which is a client named by a
command: `--subject "my-client --drive"`. The runner plays the relay: `start` seats the subject
with the invite, the session's clock, the roster and the session keypair the vector's `key` names,
`deliver` hands over one sealed frame, and `expectSubject` reads the report. With no `--subject`
every such vector is reported **not attempted**, the summary counts `not attempted` separately
from `passed`, and `runner/subject.py` holds the protocol a client implements. Run
`python3 runner/run_vectors.py --layer all` to see the same split from the wire layer's side.

```
python3 runner/run_peer.py --subject "./target/debug/my-client --drive"
python3 runner/run_peer.py --subject … --mutation-census   # what each client vector must go red under
```

**One member of the subject protocol is a test seam and not production surface.** A decision
vector's delivered state commits a *fixture* key's public half, and nothing in the protocol lets a
host hand a peer a chosen key, so `join` carries `session_key` — a 32-byte Ed25519 seed the runner
resolves out of the vector's `key`. `PROTOCOL.md` §13.1 mints that keypair in memory per connection;
a client has no member that fixes one, and the client this repository's own tests drive says so in
`selvage_client::peer::PeerOptions`.

The red line to expect is a vector that pins behaviour the runner does not have; the failure
names the file, the step and the reason the receiver gave instead.

## Comparison rules

Four are worth knowing before writing a vector, because the runner enforces them:

- **Member sets are exact, in both directions.** A frame with a member the vector does not
  mention fails. A version-locked vector is checking that nothing was silently added or renamed,
  so an implementation that adds a member to a `selvage/1` frame has broken this version.
- **`peers`, `capabilities`, `wire_versions` and `roles` are compared as sets.** The protocol
  promises no order for them (`CANONICAL.md` §2.7), so the comparison matches them as multisets
  and puts the vector's into the order the wire sent before it compares the bytes. `documents`
  and a grant's `paths` are not among them: the protocol promises an order for both (`PROTOCOL.md`
  §6.2 and §5), and the comparison holds each to the order the vector wrote.
- **The bytes are compared, not just the parsed JSON.** The vector's frame is written in the
  canonical form of `CANONICAL.md`, the reference server produces exactly those bytes, and a
  failure prints both.
- **Binary frames are compared byte for byte.** The server is payload-opaque, so its whole job on
  the document and awareness paths is to deliver what it was handed.

## What the vectors do not cover

Nothing here is a conformance suite for **two** clients. The wire corpus holds a server to its
transcripts, and every `expect` in it is the server speaking. The peer corpus holds **one**
client to the rules of `selvage/2`, and it does it twice over: its frame vectors hold any
*receiver* to `CANONICAL.md` §6.1 — the envelope, the key schedule, the counter mark, the ten
reasons — with no client at all, and its decision vectors hold a real client to what it does
with a frame it has received, which is what a subject is for. What no vector here
can show is that two clients **agree**: a vector can hold a client to a rule it states, and it
cannot show that two implementations reach the same state. That stays the interop test's job
(`vscode_client/test/interop.test.ts` against
`reference_server/crates/harness/examples/interop_peer.rs`), which is a convergence proof and
not a conformance one, and it is the reason neither is sufficient alone.

Three more limits, said rather than left to be discovered. A vector asserts the bytes of a
frame; a conforming implementation whose Ed25519 **randomises** its signatures — Safari's does —
produces a different 64-byte signature for the same frame, so what such an implementation is
held to is that its signature **verifies** over §6.1's input, which is what `NOTES.md` §B.31
records. The peer corpus's mutation census shows that a vector catches a named mutation of the
runner's own reader; it does not show that a differently-wrong client fails. And the runner's
frame layer is a **format** check and a **self-consistency** check rather than a second
implementation: it was written from the same prose as any client will be, so agreement between
them is weak evidence.

Client behaviour beyond that (renewal, expiry, reconnection, the adapter seam, the two clients'
agreement) is tested in
the reference server's [`crates/harness/tests/`](https://github.com/selvage-protocol/reference_server/tree/main/crates/harness/tests)
and in the clients' own suites. The cases these vectors do not reach are listed in
[`NOTES.md`](NOTES.md) §B, and a second implementation will find more.

## `vectors/anchors/`

The one artifact here that is not a replayable transcript. A transcript is one server's bytes, and
what `vector 010` cannot show is whether *two different libraries* agree about the shape of the
member inside those bytes, because a vector only ever decodes it with the library that wrote it.
So `vectors/anchors/` holds one document and one caret, written the way each of the two
ecosystems' libraries writes it (the yjs shape, which names the scope beside the element, and the
`yrs` shape, which names the element alone), together with the document both anchors are taken
from. [`crates/harness/tests/crossing/`](https://github.com/selvage-protocol/reference_server/tree/main/crates/harness/tests/crossing)
and [`test/crossing.test.ts`](https://github.com/selvage-protocol/vscode_client/blob/main/test/crossing.test.ts)
rebuild each half from the real library and then resolve the *other* half, so a shape that one
side stops accepting is a red test on both sides rather than a peer that silently shows no
cursor. The file's own `notes` member says the same thing next to the data.

## Versioning

Three numbers move together, and nothing here is allowed to move independently of the prose:

1. **The wire version**, `selvage/1`, in the `v` member of every text frame and in
   `GET /meta`'s `wire_versions`. It changes when a member's meaning or presence changes.
2. **The canonical form version**, `SJ-C/1`, in this file's sibling `CANONICAL.md`. It changes
   when the *bytes* of a frame change: a different member order, a different number form, even
   when the members' meaning does not.
3. **The spec revision**, the git history of this directory. Prose edits that change no bytes
   are commits, not version bumps.

Each release tags the commit it was cut from and attaches one zip holding `schema/`, `vectors/`,
`PROTOCOL.md`, `CANONICAL.md` and `LICENSE`. That zip is what an implementation pins against.

Every vector carries both versions in `selvage` and `canonical`, and the runner refuses to replay
a vector bound to anything else. That is the version binding: a vector set without
one rots, because nothing can say whether it is out of date or the implementation is wrong.

The compatibility rule itself is `PROTOCOL.md` §10: same major, and while at `0.x` also the same
minor. At major 1, `selvage/1`, `selvage/1.0` and `selvage/1.9` are all this version, but only
`selvage/1` is how a conforming producer writes it (`CANONICAL.md` §2.5).

## Adding a vector

1. **Start the server the vector needs** and record what it actually says. The reference bytes for
   the binary frames are produced by the reference server's
   [`crates/harness/tests/vectors/runner.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/vectors/runner.rs)
   and its siblings: build the document you want with a *fixed* client id (`yrs::Doc::with_options`
   with `client_id` set), and print the frames. A payload with a random client id in it is not a
   vector, it is a flaky test.
2. **Write the file** as `vectors/NNN-<slug>.json`, where `NNN` is the next free number. A new
   vector is numbered after the last one, which is `036` as this is written; a peer vector goes in
   `vectors/peer/` and is numbered from `101`, so that a corpus of two layers stays readable at a
   glance. The members are:

   ```json
   {
     "selvage": "selvage/1",
     "canonical": "SJ-C/1",
     "id": "013",
     "title": "one line, in the present tense, saying what holds",
     "spec": "PROTOCOL.md#the-section-it-pins",
     "notes": "why this transcript is worth keeping, for a human",
     "harness": { "room_grace_ms": 400 },
     "steps": [ ... ]
   }
   ```

   `harness` is optional; without it the server runs with the reference defaults. Set
   `room_grace_ms` when the vector depends on the grace period, because the vectors are the only
   place the number is written down.

3. **Write the steps**, one of:

   | `op` | what it does |
   |---|---|
   | `open` | opens a WebSocket at `target`, after substituting named bindings into it, and names it `conn` |
   | `send` | sends `text` as a text frame on `conn` |
   | `expect` | reads the next text frame on `conn` and compares it with `text` |
   | `sendBinary` | sends the bytes in `hex`; `apply: true` also applies them to that connection's replica |
   | `expectBinary` | reads a binary frame and compares it with `hex`, or with the `frame` description; `apply` as above |
   | `expectClose` | reads until the connection closes and asserts the close `code` |
   | `close` | closes the connection from the client's side |
   | `wait` | sleeps `ms`; only for waiting out a server timer the vector is testing |
   | `http` | issues `GET target` over plain HTTP and remembers the status and body |
   | `expectStatus`, `expectBody` | assert on the last HTTP reply |
   | `expectDoc` | asserts that `conn`'s replica holds `text` for `path` |
   | `expectSameState` | asserts that the named connections have equal CRDT state vectors |

4. **Where a value is not yours to know**, write a placeholder. `$room`, `$token`, `$host_peer` and
   so on bind the first time they are seen and must be equal every later time, which is what makes
   a later `room.joined` demonstrably the same room as an earlier `room.created`. `$_` matches
   anything and is never remembered: use it for the members the prose calls unstable, such as
   `error.message` and the `reason` of `room.gone`. Two connections that mint two different rooms
   need two different placeholder names.
5. **Run it** in a [`reference_server`](https://github.com/selvage-protocol/reference_server)
   checkout: `cargo test -p selvage-harness --test vectors` (its flake provides `cargo`). Without
   Rust, `python3 runner/run_vectors.py` does the same replay against a running server. Then run
   the schema validator, which checks the vector's shape as well as its frames and pins the number
   of vectors, frame checks and assertion steps, so a new vector or a deleted assertion is a red
   run until those constants in `schema/validate.py` are updated with it. A new vector also
   needs its own entry in that file's `EXPECTED_CODES` census, or the run fails on the vector that
   has no census. The counts and the census move in the same commit as the vector, not in one
   after it.

## Adding a peer vector

Same file, same conventions, two things different.

1. **`vectors/peer/NNN-<slug>.json`, numbered from `101`.** It declares its `layer` (`peer`), its
   `kind` (`frame` or `decision`), the `fixture` it reads, and the mutation in `catches` that it
   must go **red** under. That last member is not optional in effect: `run_peer.py
   --mutation-census` runs the vector twice, once as it stands and once with the one guard it says
   it catches removed, and a vector that stays green under its own mutation is a fragment. A vector
   whose `catches` is `null` is the **positive control** and must instead stay green under *every*
   mutation there is, because the other way to pass a corpus of refusals is to refuse everything.
2. **A vector carries a recipe when the frame is derivable and `hex` when it is not, and the two
   must agree.** `seal` carries the recipe — the fixture key, the `kind`, the counter, the nonce,
   the plaintext as hex or as the JSON object it is — and the step also carries the `hex` that
   recipe produces; `runner/test_recipe.py` re-derives every frame in the corpus and asserts the
   two are the same, for the vector as it is written and for the corruption a `corrupt` step
   applies. A frame that is *not* derivable is a corruption of one, and `corrupt` names the source,
   the byte it flips or the bytes it appends, and the result — so even the literal bytes in the
   corpus are re-derived. The one thing nothing re-derives is the signature itself, and the reason
   is in `NOTES.md` §B.31: Safari's Ed25519 randomises signatures, so a conforming implementation
   need not produce the bytes a vector carries. What the vector claims about it is that it
   verifies.
3. **Assert the consequence, not only the verdict.** A refusal vector that asserts only a reason
   is passed by a receiver that drops everything, so §13.11 has every rule say what the observable
   is: the replica the client holds (`expectDoc`), the listing it holds (`expectListing`), the
   holds it keeps (`expectHolds`), the frames it applied (`expectVerify` after a refusal), or the
   subject's own report (`expectSubject`). `schema/validate.py` pins the reasons each vector
   asserts in `EXPECTED_REFUSALS` and the mutation each declares in `EXPECTED_MUTATIONS`, so both
   move in the same commit as the vector.

## Why each one exists

- **The schema** lets a frame be validated without a server, and makes the shape of every method,
  event, result and error something a machine reads rather than a table a human transcribes. It
  covers `/meta`, the version grammar and the capability grammar too.
- **The canonicalisation rule** exists because "key order is not significant" is not enough to
  make two implementations produce the same bytes. A vector cannot be written without it, and
  neither can a test that compares bytes.
- **The vectors** exist because the first review of this project found a `doc.close` bug that the
  prose *described* correctly and the code did not implement: a faithful port of the prose would
  have reproduced the bug. A transcript that asserts the intended semantics catches that, where a
  transcript that asserts only what the current code does cannot. Every vector is a real exchange
  against the reference server, recorded byte for byte, and that server's own test replays it.
- **The runner** makes replaying the vectors possible without a Rust checkout.
  `runner/run_vectors.py` reads the same JSON, opens a WebSocket to a server it starts itself, and
  compares the bytes, so a second implementation in any language can be held to the transcripts
  without reading `reference_server/`. `runner/run_peer.py` is the same property for the layer
  whose subject is a client: it seals, signs and refuses `selvage/2`'s frames in one process, with
  no server, no client and no toolchain, so a stranger implementing the peer rules has something
  runnable to be held to and something to run their own implementations against.
- **The notes** exist because a specification has to be able to say what is settled and what is
  not, without either pretending an undecided question is a rule or leaving a reader to infer one
  from an implementation. They are kept apart from `PROTOCOL.md` so that nothing in the
  specification has to be read twice to find out whether it binds.

## Licence

The prose, the canonicalisation rule, the schemas and the vectors are **CC-BY 4.0**
([`LICENSE`](LICENSE)), as `DESIGN.md` §7 asks; the tooling (`schema/validate.py` and
`runner/`) is **MIT OR Apache-2.0** ([`LICENSE-MIT`](LICENSE-MIT),
[`LICENSE-APACHE`](LICENSE-APACHE)), the same pair the
reference server carries for its code. Reuse the specification with attribution, and the tooling
under either licence.
