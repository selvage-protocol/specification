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

Read [`PROTOCOL.md`](PROTOCOL.md) for what the members mean. It is the specification and it is
normative; §1.1 says which sentences bind a reader and what a conforming implementation is.
[`CANONICAL.md`](CANONICAL.md) is **SJ-C/1**, the byte form of a session text frame, normative for
the bytes. [`schema/`](schema/) is the machine-readable model, one JSON Schema 2020-12 file per
concern. [`vectors/`](vectors/) is the conformance corpus: transcripts of real bytes that a server
replays. [`NOTES.md`](NOTES.md) is informative, and its Part B lists the decisions this draft had
to make and what is still open.

Checking the corpus takes a couple of minutes and needs no server and no Rust:

```
pip install jsonschema referencing     # or: nix develop  (the same package, pinned)
python3 schema/validate.py
```

A green run prints one line for the schemas, one for the corpus counts, and `result OK`:

```
schema ok      9 schemas, 39 values checked against the control-character refusal
vectors        31 files, 34858 frame checks, 8642 assertion steps
result         OK
```

Those three numbers (31 vectors, 34858 frame checks, 8642 assertion steps) are pinned in
`schema/validate.py`, so a deleted vector, frame check or assertion is a red run rather than a
smaller number in a line of output. Green means every schema is a valid JSON Schema 2020-12
document, every frame in every vector parses and validates against the schema for the concern it
belongs to, every frame a vector claims the server produces is written in the canonical byte form
of `CANONICAL.md` §2, and the corpus still holds the pinned counts and the pinned per-vector census
of asserted error and close codes. The [section below](#validating-the-schemas-and-the-vectors)
lists the checks in full.

To check that a real server produces those bytes, point the replay at a `selvaged`, which is not
part of this repository:

```
cd ../reference_server
nix develop . -c cargo build -p selvaged
export SELVAGE_SELVAGED=$PWD/target/debug/selvaged
cd ../specification
python3 runner/run_vectors.py
```

It starts the server on an ephemeral port, replays every transcript against it over a real
WebSocket, and ends with a summary. Against a `selvaged` that implements every frame the corpus
covers:

```
summary        31 files, 34858 frame checks, 31 vectors passed, 0 failed
```

A vector that pins behaviour newer than the server you point it at is where a red line comes from,
and the summary names the file and the frame it disagreed about.

`scripts/ci-local.sh` runs the same commands as `.github/workflows/validate.yml`, one flake check
per step, plus actionlint over the workflow files, and needs `nix`:

```
scripts/ci-local.sh all      # lint + validate
scripts/ci-local.sh validate # the flake checks alone
```

Its `validate` half refuses to run when `schema/`, `vectors/`, `runner/` or the flake files differ
from `HEAD`, because CI checks out the committed ref. Commit before running it, not just stage.

## What lives here

Six things live here, and they are meant to be read together. The first is the specification; the
rest are what an independent implementation can *check itself against*, and read where the first is
silent, without reading the Rust.

| Path | What it is |
|---|---|
| [`PROTOCOL.md`](PROTOCOL.md) | **The specification.** What the members mean, and what a conforming peer must, should or may do. §1.1 says which sentences bind a reader and what conformance is. |
| [`CANONICAL.md`](CANONICAL.md) | **SJ-C/1**, the canonical byte form of a session text frame. Normative for the bytes. |
| [`schema/`](schema/) | The machine-readable model: JSON Schema 2020-12, one file per concern, plus `validate.py`. |
| [`runner/`](runner/) | The language-neutral replay: `run_vectors.py` starts a server and replays every transcript against it, with no Rust toolchain. |
| [`vectors/`](vectors/) | Versioned transcripts of real bytes, replayed by the reference server's [`crates/harness/tests/vectors.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/vectors.rs) and by `runner/`. |
| [`NOTES.md`](NOTES.md) | **Informative.** What the implementations do where `PROTOCOL.md` does not bind them, the decisions this draft had to make, and what is still open. Nothing there is a requirement. |

`DESIGN.md` §7 asks for "CC-BY prose plus JSON Schema", and §13.4 asks for the machine-readable
model to be built early because prose drifts. This repository is that, plus the two things prose
alone cannot carry: a byte-level rule and a suite.

## Adding a vector

1. **Start the server the vector needs** and record what it actually says. The reference bytes for
   the binary frames are produced by the reference server's
   [`crates/harness/tests/vectors/runner.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/vectors/runner.rs)
   and its siblings: build the document you want with a *fixed* client id (`yrs::Doc::with_options`
   with `client_id` set), and print the frames. A payload with a random client id in it is not a
   vector, it is a flaky test.
2. **Write the file** as `vectors/NNN-<slug>.json`, where `NNN` is the next free number. A new
   vector is numbered after the last one, `031` as this is written. The members are:

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
   run until those three constants in `schema/validate.py` are updated with it. A new vector also
   needs its own entry in that file's `EXPECTED_CODES` census, or the run fails on the vector that
   has no census. The counts and the census move in the same commit as the vector, not in one
   after it.

## Validating the schemas and the vectors

The schemas are checked as schemas, and every frame in every vector is checked against them:

```
pip install jsonschema referencing     # or: nix develop  (the same package, pinned)
python3 schema/validate.py
```

It prints a line for the schemas, a line for the corpus counts, and `result OK`. It checks:

- every `*.json` in `schema/` is a valid JSON Schema 2020-12 document;
- every `send` and `expect` text frame in every vector parses, validates against
  `schema/session.json`, and validates against the params schema of the method or event it
  names;
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
  rather than a corpus that keeps every count and quietly asserts something else.

A frame a vector sends *on purpose* knowing it is malformed, which is how a refusal is tested,
carries `"refused": true`, and its params are not schema-checked. One whose malformation is that
it is not JSON at all carries `"unparsable": true` as well, because the two are different
claims: the first says the frame is well formed and refused for what it says, the second says a
parser refuses it, and a marker left on a frame that does parse is a red run rather than an
assertion that never runs.

`SELVAGE_VECTORS=DIR` reads the transcripts from another directory; both halves honour it.
Replaying a corrupt *copy* is how a failure is shown to be caught. The counts are this
repository's, so either half fails on a directory that does not hold them rather than checking
less of it quietly.

## Replaying the vectors against a server

`schema/validate.py` checks the shape of every frame; it cannot check that a server produces the
bytes. `runner/run_vectors.py` does, and needs no Rust toolchain: it starts a `selvaged` on an
ephemeral port, replays each transcript against it over a real WebSocket, and compares what comes
back, the text frames structurally and then byte for byte, the binary frames byte for byte, and the
document and awareness state once a frame is applied.

It needs Python 3 and three packages:

```
pip install websockets jsonschema referencing
# or: nix develop, in this repository, for the same three packages at the pins the workflow installs
```

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

It exits non-zero if any vector fails. A `selvaged` must accept `--room-grace-ms MS`: the grace
period is per-vector (`vectors/012` waits out 400 ms, `vectors/011` four seconds), and a runner
that spawns the server has no other way to set it. `SELVAGE_VECTORS=DIR` reads the transcripts
from another directory, the same escape `schema/validate.py` honours, and the replay holds the
same pin on the file count before it starts. `--schema-only` is exactly
`python3 schema/validate.py` and starts no server.

`python3 runner/test_runner.py` checks the replay's comparison code (`matches`,
`expected_bytes`, `check_text` and `check_frame_spec`) with frames whose answer is known, in
both directions, so a comparison that stopped failing is itself a red run. A comparison that
only ever runs against a server never runs in CI. `python3 runner/test_yprotocols.py` checks
the binary decoder, the one part of the replay that is hand-written, from the vectors. CI runs
both, and the live replay runs in the reference server's own suite over its vendored copy of the
corpus, because this workflow has no Rust toolchain to build a `selvaged` with.

## Comparison rules

Four of them are worth knowing before writing a vector, because they are what the runner
enforces:

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

They are not a conformance suite for a *client*. Every `expect` is the server speaking, and the
only client-side claims they make are about the bytes a peer receives and what those bytes mean
once decoded. Client behaviour (renewal, expiry, reconnection, the adapter seam) is tested in
the reference server's [`crates/harness/tests/`](https://github.com/selvage-protocol/reference_server/tree/main/crates/harness/tests).
The cases they do not reach are listed in [`NOTES.md`](NOTES.md) §B, and a second
implementation will find more.

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

Every vector carries both versions in `selvage` and `canonical`, and the runner refuses to replay
a vector bound to anything else. That is the whole version-binding mechanism: a vector set without
one rots, because nothing can say whether it is out of date or the implementation is wrong.

The compatibility rule itself is `PROTOCOL.md` §10: same major, and while at `0.x` also the same
minor. At major 1, `selvage/1`, `selvage/1.0` and `selvage/1.9` are all this version, but only
`selvage/1` is how a conforming producer writes it (`CANONICAL.md` §2.5).

## Why each one exists

- **The schema** exists so a frame can be validated without a server, and so the shape of every
  method, event, result and error is a thing a machine can read rather than a table a human has
  to transcribe. It covers `/meta`, the version grammar and the capability grammar too.
- **The canonicalisation rule** exists because "key order is not significant" is not enough to
  make two implementations produce the same bytes. A vector cannot be written without it, and
  neither can a test that compares bytes.
- **The vectors** exist because the first review of this project found a `doc.close` bug that the
  prose *described* correctly and the code did not implement: a faithful port of the prose would
  have reproduced the bug. A transcript that asserts the intended semantics catches that, where a
  transcript that asserts only what the current code does cannot. Every vector is a real exchange
  against the reference server, recorded byte for byte, and that server's own test replays it.
- **The runner** exists so replaying the vectors is not something only a Rust checkout can do.
  `runner/run_vectors.py` reads the same JSON, opens a WebSocket to a server it starts itself, and
  compares the bytes, so a second implementation in any language can be held to the transcripts
  without reading `reference_server/`.
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
