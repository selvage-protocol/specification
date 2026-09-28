# Validating the schemas and the vectors

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
- that a vector is bound to `selvage/2` and `SJ-C/1`;
- that the schema itself refuses a control character in a `display_name`, a `path` and each
  member of a `paths` or `documents` list, which is `PROTOCOL.md` §5's Unicode `Cc` exclusion:
  nothing else in the corpus exercises it, because a frame sent on purpose to test a refusal is
  not schema-checked;
- that the error and close codes each vector asserts are the ones `EXPECTED_CODES` pins for it, so
  a substitution inside a closed vocabulary (`unknown_method` for `bad_params`, say) is a red run
  rather than a corpus that keeps every count and quietly asserts something else;
- that `schema/sealed.json` describes what `selvage/2` carries sealed — the room state, the
  closing, the holds and the session-key announcement — by running it against values that must
  validate and values that must not, and, beside them, the vocabulary a receiver reports a refused
  sealed frame in, which `PROTOCOL.md` §13.2 binds a client to produce. Step 8 is read as
  `CANONICAL.md` §6.1 reads it — an object of the members its `kind` fixes, each member of the type
  that kind gives it — so a value a rule elsewhere re-homes is one of the values that **must**
  validate: a listing whose path carries a control character and a holds message whose set does,
  because `PROTOCOL.md` §13.3 and §13.7 have a receiver drop such a path rather than refuse the frame
  or the state, and a model that refused it would put two conforming receivers at odds about one
  state. The peer corpus reaches both now: `vectors/peer/117` seals a state whose `listing` carries a
  control-carrying path and a holds message whose set does, so this check is the model's own half of
  a rule the corpus pins in bytes rather than the only half. A sealed frame is bytes rather than JSON,
  so a vector cannot hand these values to the schema check directly; what keeps it here is that the
  rule re-homed from the server to the peers is pinned in the model and in the corpus at once, and
  one of the two going slack is a red run in the other. The report vocabulary is a closed *value*
  rather than a member, which is the one thing a schema here can refuse: it is pinned to
  `CANONICAL.md` §6.1's table in both directions, so a reason removed from it, a reason added with no
  rule behind it, and the reason a reader expects and cannot have (`bad_tag`, because the signature
  covers the ciphertext) are each a red run.
- that the session layer's own shapes hold: `/meta`'s four members, a `PeerInfo` without `role`,
  the two replies to `session.hello` without `documents`, the `peer.joined` whose params that peer
  record is, and the fault vocabulary of a server that seats nobody as the host, are validated by
  every wire vector's frames, because the transcripts are the one version's.
- every peer vector's steps, recipes and refusals: the version and layer it declares, a `kind` of
  `frame` or `decision` with the step vocabulary that kind has, each `seal`/`deliver` recipe's
  shape — a fixture key that exists, a count, a nonce of twelve bytes, exactly one of `plaintext`
  and `payload` — every `expectReject` reason against §6.1's closed vocabulary, an `expectRefusal`'s
  `names`, and the mutation in `catches` against the layer's own table. It also pins the peer layer's
  own counts and two censuses: `EXPECTED_REFUSALS`, which vector asserts which reasons, and
  `EXPECTED_MUTATIONS`, which vector must go **red** under which removed guard.
  The second is the one pin in this corpus that is a statement about what the corpus catches rather
  than what it contains, and it is `runner/run_peer.py --mutation-census` that drives it.
- the absence rule, three ways. **The model**: every session-layer shape is walked for a member no
  server-authored frame may carry — a `role` made a property or a required entry of a session schema
  is a red run, which is the structural half and the strongest of the three. **The frames**: every
  peer vector declares the strings its own plaintext names, and none of them may appear in the bytes
  of a frame it seals, which needs no key because the ciphertext is in the vector. **The control**:
  the same walk is run over a document built to break every rule the scan states — a `role` on a
  peer record, a `documents` list, a path, a `paths` grant and the five events this protocol does
  not have — and the nine violations it must find are pinned, because a scan that read nothing
  reports nothing and an unpinned nothing is a green line rather than a check.

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
