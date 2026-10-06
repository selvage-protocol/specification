# Replaying the peer corpus

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
command: `--subject "my-client --drive"`. The runner plays the relay: `start` hands the subject the
link it starts from — the invite, the session's clock, the roster, the session keypair the vector's
`key` names, and what `/meta` answered — `deliver` hands over one sealed frame, and `expectSubject`
reads the report. With no `--subject` every such vector is reported **not attempted**, the summary
counts `not attempted` separately from `passed`, and `runner/subject.py` holds the protocol a client
implements. Run `python3 runner/run_vectors.py --layer all` to see the same split from the wire
layer's side.

**A rule decided about the link, before a socket, is answered by refusing the `join`.** §5.1's
fragment, and a link that names any of its four keys more than once, are not frames, so there is
nothing to deliver and no reason of §6.1's to report: the client answers the `join` itself, in its
own words, and seats nothing, leaving the subject free for the vector's next `start`. A vector
asserts that with `expectRefusal`, whose `names` are the strings the client's words must carry —
the missing or malformed key, or the parameter a link repeats (§5.1) — because the section leaves
the sentence to the client. A guard on the link is removed **before** the `join`
(`runner/subject.py`'s `LINK_MUTATIONS`), which is where a client reads it; every other guard is
removed once the subject is seated.

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
