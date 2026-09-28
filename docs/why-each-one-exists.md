# Why each one exists

- **The schema** lets a frame be validated without a server, and makes the shape of every method,
  event, result and error something a machine reads rather than a table a human transcribes. It
  covers `/meta`, the version member and the capability grammar too.
- **The canonicalisation rule** exists because "key order is not significant" is not enough to
  make two implementations produce the same bytes. A vector cannot be written without it, and
  neither can a test that compares bytes.
- **The vectors** exist because the first review of this project found a bug in the server that the
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

`DESIGN.md` §7 asks for "CC-BY prose plus JSON Schema", and §13.4 asks for the machine-readable
model to be built early because prose drifts. This repository is that, plus the two things prose
alone cannot carry: a byte-level rule and a suite.

