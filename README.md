# The Selvage Session Protocol specification

This repository specifies Selvage, a collaborative live-coding protocol: the *session* layer of
rooms, participants, roles, the open-document set the peers announce, presence, and join and leave,
with document sync and awareness carried through it. It is written to be implemented on its own,
without reading the reference implementation in
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
refused sealed frame is reported in, one for each layer of the corpus, one for the absence scan,
and `result OK`:

```
schema ok      10 schemas, 39 values checked against the control-character refusal
sealed         48 values checked against the sealed payloads of selvage/2
refusals       13 values checked against selvage/2's local report vocabulary
vectors        25 files, 33784 frame checks, 8394 assertion steps
peer vectors   27 files, 19 frame, 8 decision, 232 checks, 74 assertion steps
absence        49 session shapes, 50 vectors, 9 control violations, 50 sealed frames, 100 needle checks
result         OK
```

Those counts are pinned in `schema/validate.py`, so a deleted vector, frame check or assertion
fails the run. [Validating the schemas and the vectors](docs/validating.md) lists what the run
checks.

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
produces those bytes. To replay the corpus against a real server, see [Replaying the vectors against
a server](docs/replaying-the-wire-vectors.md).

`scripts/ci-local.sh` runs the same commands as `.github/workflows/validate.yml`, one flake check per
step, plus the link check over this file and the `docs` tree with `lychee`, and actionlint over the
workflow files. It needs `nix`:

```
scripts/ci-local.sh all      # lint + validate
scripts/ci-local.sh validate # the flake checks and the link check
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
| [`schema/`](schema/) | The machine-readable model: JSON Schema 2020-12, one file per concern, plus `limits.json` (the numeric bounds the spec owns) and `validate.py`. |
| [`runner/`](runner/) | The language-neutral replay. `run_vectors.py` starts a server and replays every wire transcript against it; `run_peer.py` replays the peer corpus's frame layer with no server and no client; `sealed.py` is `CANONICAL.md` §6.1 in Python; `subject.py` is the protocol a decision vector drives a client through. None of it needs a Rust toolchain. |
| [`vectors/`](vectors/) | Versioned transcripts of real bytes, replayed by the reference server's [`crates/harness/tests/vectors.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/vectors.rs) and by `runner/`. `vectors/peer/` is the peer corpus and `vectors/fixture/` is its keys. |
| [`NOTES.md`](NOTES.md) | **Informative.** What the implementations do where `PROTOCOL.md` does not bind them, the decisions this draft had to make, and what is still open. Nothing there is a requirement. |

## More

- [Validating the schemas and the vectors](docs/validating.md): every check the validator makes, and
  the counts it pins.
- [Replaying the vectors against a server](docs/replaying-the-wire-vectors.md): the packages, the
  `selvaged` build, and the summary line to expect.
- [Replaying the peer corpus](docs/replaying-the-peer-corpus.md): `run_peer.py`'s modes, the decision
  vectors, and the link refusal.
- [Comparison rules](docs/comparison-rules.md): the four rules the runner enforces.
- [What the vectors do not cover](docs/what-the-vectors-do-not-cover.md): the limits, stated up
  front.
- [`vectors/anchors/`](docs/anchors.md): the one artifact that is not a replayable transcript.
- [Versioning](docs/versioning.md): the three numbers that move together, and what a release
  bundles.
- [Adding a vector](docs/adding-a-vector.md): recording from the server, the file's members, the
  step table, and the counts to update.
- [Adding a peer vector](docs/adding-a-peer-vector.md): the layer, the mutation in `catches`, and
  the seating leg.
- [Why each one exists](docs/why-each-one-exists.md): what each piece is for.

## Licence

The prose, the canonicalisation rule, the schemas and the vectors are **CC-BY 4.0**
([`LICENSE`](LICENSE)), as `DESIGN.md` §7 asks; the tooling (`schema/validate.py` and
`runner/`) is **MIT OR Apache-2.0** ([`LICENSE-MIT`](LICENSE-MIT),
[`LICENSE-APACHE`](LICENSE-APACHE)), the same pair the
reference server carries for its code. Reuse the specification with attribution, and the tooling
under either licence.
