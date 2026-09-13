# The Selvage Session Protocol specification

This repository is the specification for **Selvage**, a collaborative live-coding protocol, and
is written to be implemented on its own. The reference implementation lives in
[`selvage-protocol/reference_server`](https://github.com/selvage-protocol/reference_server), and
the editor client in
[`selvage-protocol/vscode_client`](https://github.com/selvage-protocol/vscode_client). The design
record, `DESIGN.md`, is not published; it is cited below by section.

Four things live here, and they are meant to be read together:

| Path | What it is |
|---|---|
| [`PROTOCOL.md`](PROTOCOL.md) | The prose specification. What the members mean, and why. |
| [`CANONICAL.md`](CANONICAL.md) | **SJ-C 1** — the canonical byte form of a session text frame. |
| [`schema/`](schema/) | The machine-readable model: JSON Schema 2020-12, one file per concern, plus `validate.py`. |
| [`vectors/`](vectors/) | Versioned transcripts of real bytes, replayed by the reference server's [`crates/harness/tests/vectors.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/vectors.rs). |

The prose is the specification; the other three are what an independent implementation can
*check itself against* without reading the Rust.
`DESIGN.md` §7 asks for
"CC-BY prose plus JSON Schema" and §13.4 for the machine-readable model to be built early because
prose drifts. This repository is that, plus the two things prose alone cannot carry: a byte-level
rule and a suite.

## Why each one exists

- **The schema** exists so a frame can be validated without a server, and so the shape of every
  method, event, result and error is a thing a machine can read rather than a table a human has
  to transcribe. It covers `/meta`, the version grammar and the capability grammar too.
- **The canonicalisation rule** exists because "key order is not significant" is not enough to
  make two implementations produce the same bytes. A vector cannot be written without it, and
  neither can a test that compares bytes.
- **The vectors** exist because the first review of this project found a `doc.close` bug that the
  prose *described* correctly and the code did not implement — a faithful port of the prose would
  have reproduced the bug. A transcript that asserts the intended semantics catches that; a
  transcript that asserts only what the current code does cannot. Every vector is a real exchange
  against the reference server, recorded byte for byte, and that server's own test replays it.

## Versioning

Three numbers move together, and nothing here is allowed to move independently of the prose:

1. **The wire version**, `selvage/1`, in the `v` member of every text frame and in
   `GET /meta`'s `wire_versions`. It changes when a member's meaning or presence changes.
2. **The canonical form version**, `SJ-C/1`, in this file's sibling `CANONICAL.md`. It changes
   when the *bytes* of a frame change — a different member order, a different number form —
   even when the members' meaning does not.
3. **The spec revision**, the git history of this directory. Prose edits that change no bytes
   are commits, not version bumps.

Every vector carries both versions in `selvage` and `canonical`, and the runner refuses to replay
a vector bound to anything else. That is the whole version-binding mechanism: a vector set without
one rots, because nothing can say whether it is out of date or the implementation is wrong.

The compatibility rule itself is `PROTOCOL.md` §10: same major, and while at `0.x` also the same
minor. At major 1, `selvage/1`, `selvage/1.0` and `selvage/1.9` are all this version — but only
`selvage/1` is how a conforming producer writes it (`CANONICAL.md` §2.5).

## Validating the schemas and the vectors

The schemas are checked as schemas, and every frame in every vector is checked against them:

```
pip install jsonschema referencing     # or: nix-shell -p python3Packages.jsonschema
python3 schema/validate.py
```

It prints one line per schema, a count of the frames it checked, and `result OK`. It checks:

- every `*.json` in `schema/` is a valid JSON Schema 2020-12 document;
- every `send` and `expect` text frame in every vector parses, validates against
  `schema/session.json`, and validates against the params schema of the method or event it
  names;
- every `expectBody` against `schema/meta.json`;
- every `expectClose` code against the close-code vocabulary in `schema/errors.json`;
- a vector that asserts nothing about the version it is bound to.

A frame a vector sends *on purpose* knowing it is malformed — that is how a refusal is tested —
carries `"refused": true`, and its params are not schema-checked.

## Adding a vector

1. **Start the server the vector needs** and record what it actually says. The reference bytes
   for the binary frames are produced by the reference server's
   [`crates/harness/tests/vectors/runner.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/vectors/runner.rs)
   and its siblings: build the document you want with a *fixed* client id (`yrs::Doc::with_options` with
   `client_id` set), and print the frames. A payload with a random client id in it is not a
   vector, it is a flaky test.
2. **Write the file** as `vectors/NNN-<slug>.json`, where `NNN` is the next free number.
   The members are:

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

4. **Where a value is not yours to know**, write a placeholder. `$room`, `$token`, `$host_peer`
   and so on bind the first time they are seen and must be equal every later time — which is what
   makes a later `room.joined` demonstrably the same room as an earlier `room.created`. `$_`
   matches anything and is never remembered: use it for the members the prose calls unstable,
   such as `error.message` and the `reason` of `room.gone`. Two connections that mint two
   different rooms need two different placeholder names.
5. **Run it** in a [`reference_server`](https://github.com/selvage-protocol/reference_server)
   checkout: `cargo test -p selvage-harness --test vectors` (its flake provides `cargo`).
   Then run the schema validator, which checks the vector's shape as well as its frames.

Four comparison rules are worth knowing before writing one, because they are what the runner
enforces:

- **Member sets are exact, in both directions.** A frame with a member the vector does not
  mention fails. A version-locked vector is checking that nothing was silently added or renamed,
  so an implementation that adds a member to a `selvage/1` frame has broken this version.
- **`peers` is compared as a set.** The protocol promises no order for it.
- **The bytes are compared, not just the parsed JSON.** The vector's frame is written in the
  canonical form of `CANONICAL.md`, the reference server produces exactly those bytes, and a
  failure prints both.
- **Binary frames are compared byte for byte.** The server is payload-opaque, so its whole job on
  the document and awareness paths is to deliver what it was handed.

## What the vectors do not cover

They are not a conformance suite for a *client*. Every `expect` is the server speaking, and the
only client-side claims they make are about the bytes a peer receives and what those bytes mean
once decoded. Client behaviour — renewal, expiry, reconnection, the adapter seam — is tested in
the reference server's [`crates/harness/tests/`](https://github.com/selvage-protocol/reference_server/tree/main/crates/harness/tests).
The cases they do not reach are listed at the end of `PROTOCOL.md` §12, and a second
implementation will find more.

## `vectors/anchors/`

The one artifact here that is not a replayable transcript. A transcript is one server's bytes;
what `vector 010` cannot show is whether *two different libraries* agree about the shape of the
member inside those bytes, because a vector only ever decodes it with the library that wrote it.
So `vectors/anchors/` holds one document and one caret, written the way each of the two
ecosystems' libraries writes it — the yjs shape, which names the scope beside the element, and
the `yrs` shape, which names the element alone — together with the document both anchors are
taken from. [`crates/harness/tests/crossing/`](https://github.com/selvage-protocol/reference_server/tree/main/crates/harness/tests/crossing)
and [`test/crossing.test.ts`](https://github.com/selvage-protocol/vscode_client/blob/main/test/crossing.test.ts)
rebuild each half from the real library and then resolve the *other* half, so a shape that one
side stops accepting is a red test on both sides rather than a peer that silently shows no
cursor. The file's own `notes` member says the same thing next to the data.

## Licence

The prose, the canonicalisation rule, the schemas and the vectors are **CC-BY 4.0**
([`LICENSE`](LICENSE)), as `DESIGN.md` §7 asks; the tooling — `schema/validate.py` — is **MIT OR
Apache-2.0** ([`LICENSE-MIT`](LICENSE-MIT), [`LICENSE-APACHE`](LICENSE-APACHE)), the same pair the
reference server carries for its code. Reuse the specification with attribution, and the tooling
under either licence.
