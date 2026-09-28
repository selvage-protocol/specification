# Adding a vector

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
     "selvage": "selvage/2",
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
