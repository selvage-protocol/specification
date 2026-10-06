# Replaying the vectors against a server

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

Then, from the repository root:

```
python3 runner/run_vectors.py
```

Against a `selvaged` that implements every frame the corpus covers, it ends with:

```
summary        25 files, 33784 frame checks, 25 vectors passed, 0 failed
```

**An older `selvaged` is the red line to expect.** A summary that is red rather than green names
the file and the frame the binary disagreed about, and the usual cause is a vector that pins
behaviour newer than the server you point it at: the corpus grows with the protocol, and a binary
built before the change a vector pins will not answer for it.

It exits non-zero if any vector fails. A `selvaged` must accept `--room-grace-ms MS`: the grace
period is per-vector (`vectors/012` waits out 400 ms), and a runner that spawns the server has no
other way to set it. There is no version to select — the protocol has one — so the runner starts the
server with no flag at all. `SELVAGE_VECTORS=DIR` reads the transcripts
from another directory, the same escape `schema/validate.py` honours, and the replay holds the
same pin on the file count before it starts. `--schema-only` is exactly
`python3 schema/validate.py` and starts no server.

`python3 runner/test_runner.py` checks the replay's comparison code (`matches`,
`expected_bytes`, `check_text` and `check_frame_spec`) with frames whose answer is known, in
both directions, so a comparison that stopped failing is itself a red run. A comparison that
only ever runs against a server never runs in CI. It checks the peer layer's code the same way
and for the same reason: the envelope's layout, the counter mark, the refusal vocabulary, the
subject protocol's framing and deadlines, and the equality of the two halves' step tables.
`python3 runner/test_yprotocols.py` checks the binary decoder, the one part of the replay that is
hand-written, from the vectors. CI runs both, and the live replay runs in the reference server's own
suite over its vendored copy of the corpus, because this workflow has no Rust toolchain to build a
`selvaged` with.
