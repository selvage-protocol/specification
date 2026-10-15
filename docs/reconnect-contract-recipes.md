# Checking finite reconnect sync and publication history

These are deterministic implementation-test recipes for `PROTOCOL.md` §7,
§9.1, §13.6 and §13.9. They are not new corpus vectors or evidence that a
particular client passes. The request-accounting reference in
`runner/sync_requests.py`, exercised by `runner/test_runner.py`, covers finite
request decisions using verified fixture keys. It does not encode CRDT history,
implement a subject, or prove loss recovery or viewer-history exclusion.

## Two replicas: lose an update, retain history, replace the key

Use two actual peer/CRDT implementations and a controlled relay queue, with
explicit monotone time and bounded draining. Do not use sleeps, a text snapshot
as a retained replica, or a hash in place of a CRDT state vector.

1. Seed A's path with `abc`. Exchange initial traffic until both texts and
   actual encoded state vectors agree. Drain all outbound traffic.
2. B inserts `off ` at index 0. Drain its generated Update and record it as
   deliberately lost: it must no longer be in B's outbound or withheld queue.
3. B inserts `on ` at index 0. Deliver that dependent Update to A. Assert
   acceptance, no content refusal, B=`on off abc`, A=`abc`. A's applied-frame
   count increases once even though the update remains pending.
4. Detach B, retain the actual CRDT (struct identities, pending history and
   deletion sets), and reseat it under a genuinely different session key.
   Announce and commit that key through the real host producer. Keep the
   receiver's replay/edition marks and room frame count.
5. Without a new user edit, drive the initial and reciprocal requests through
   the first due renewal boundary, then boundedly drain the relay. Assert both
   texts are `on off abc` and their actual state vectors agree. A later B edit
   must apply normally. Inspect outbound messages: no replay of the old sealed
   bytes under B's old or new key.
6. Remove reciprocal activation only. This test must fail at step 5, while
   the acceptance assertion in step 3 still holds. Restore it and pass.

Repeat with a path absent from the current listing and holds. Sync is room
history, not a per-path fetch. The test fixture's fresh-key operation must not
replace or regenerate that history from visible text.

## Two replicas: lose published history, recommit the same local writer key

Use the actual two-replica harness above, not the Python accounting reference.
A is the host/writer; B is an already-handshaken writer. Keep the socket and
both replicas throughout: this is not a fresh-key reconnect or viewer promotion.

1. Seed `abc`, converge texts and actual encoded state vectors, drain bootstrap
   and reciprocal work, and verify no pending request obligations on either peer.
   Record the last successfully queued Step1 time on each local monotone clock.
2. B inserts `off ` at index 0 while its applied state permits writing. Observe
   Update U actually sealed and queued for transport, then deliberately lose U
   in the relay. Require it absent from all outbound/withheld queues: an unsent
   local edit or a retained retry frame is not this loss case.
3. B inserts `on ` at index 0. Deliver dependent Update V to A. Require acceptance
   with no content refusal, A=`abc`, B=`on off abc`, and V pending its missing
   dependency at A. `applied` increases once for V, not when U later integrates.
4. Use the real host-signed state producer to issue a newer state omitting B's
   full session key, and then a newer state recommitting that **same key** as a
   writer. Both replicas must apply both states in order. Retain B's permitted
   history, A's pending V, replay/edition marks and the room frame count. There
   is no intermediate viewer state, new key, new edit or content refusal. Do
   not inject a Step1 manually: B's new local eligibility must cause readiness;
   state application alone must not activate A's remote reciprocal obligation.
5. Recommit before B's next due boundary. Observe no premature readiness Step1.
   At the first due renewal tick, deliver B's single coalesced readiness request
   and the resulting bounded reciprocal exchange. Boundedly drain to convergence:
   both texts must be `on off abc` and actual state vectors must agree. Confirm
   published Step2 history supplies the missing dependency and integrates V;
   do not replay U's old sealed bytes. No new edit/refusal/fresh key may be used
   to make this assertion pass.
6. Advance several renewal windows while applying unchanged writer states,
   listing-only editions and `host`/`guest` changes preserving writer eligibility.
   Require no additional Step1. Removing only local same-key readiness must fail
   the text convergence assertion in step 5 while V is still accepted in step 3.
   Restore readiness and pass; removing reciprocal activation is a separate red
   control, not a substitute for this local-readiness control.

Repeat with the local non-writer interval represented by `viewer`, and with a
path absent from the listing and holds. In a gate leg, withhold attachment or
budget through the due tick (also exercise asynchronous seal/queue failure),
then restore it: the still-applicable readiness survives, and only successful
queueing advances the common request clock. End local writer eligibility again
before queueing: its readiness retires, but pending unrelated remote/refusal
work still publishes when commitment/gates permit. A later writer interval
rearms once. Coalesce several such transitions, remote requests and refusals
before one queue; one Step1 serves the pending work, not one per transition.
The first-ever local commitment remains the immediate initial request only.

For the **same-key lost-deletion leg**, converge `abc` and drain work again. B
deletes only the seeded `b` while writer; observe its deletion Update sealed and
queued, then lose it completely in the relay. Assert A=`abc`, B=`ac` **and equal
actual state vectors before recovery**: no new struct clock was allocated.
Apply the omission/regrant states at both replicas as in step 4, with no other
edit, refusal or key change, then perform the readiness/reciprocal exchange.
Require A=B=`ac` and equal state vectors after it. Inspect the recovered Step2
deletion set or apply it to an independently seeded probe. Removing local
readiness must leave A=`abc`, B=`ac` despite equal vectors. Visible text is the
necessary red/green assertion here; vector equality alone cannot prove recovery.

## Lost deletion

Seed and converge `abc`; B deletes `b`. Drain and lose that exact Update. A
still holds `abc`, B holds `ac`; their state vectors may already agree because
a deletion need not allocate a new struct clock. Reseat B with its actual
history and a fresh key, and perform step 5 above without another edit. Both
must hold `ac` and have equal state vectors. Inspect the recovered Step2's
deletion set or apply it to an independent seeded probe. Removing reciprocal
activation must leave A=`abc`. A state-vector comparison by itself is not the
red assertion for this case.

## Three peers: count requests and expensive replies

Count SyncStep1 and SyncStep2 separately by decoding actual outbound messages,
not an aggregate handshake counter. With three empty writer peers, one simple
schedule is: commit all keys, broadcast each initial request at time 0, drain;
advance to renew−1 (no repair requests), renew (one coalesced reciprocal request
per peer), and drain again. This schedule has **two requests and four Step2
replies per peer**, six requests and twelve replies in total. Advance several
more windows and require those counts to stay fixed. This is an illustrative
schedule, not a required room-wide minimum: different delivery/interleaving can
coalesce work differently. The contract fixes local maxima and eventual work,
not zero room-wide duplication.

Further legs:

- Several new writer requests plus content refusals within one window consume
  one local request at the due boundary, not one per key/frame.
- Pack 4096 Step1 messages followed by a legitimate Update/awareness message.
  Instrument the real diff encoder as well as the wire. At most one expensive
  Step2 is computed/published for the inbound frame, and the tail is applied.
- Repeat valid requests from the same key in later windows: answer with the
  bounded Step2, never reactivate reciprocal work. Step2 alone never requests
  another Step1.
- Repeated/equal/older states, listing-only editions, seat notifications,
  announcements, uncommitted requests, bad signatures and replayed counters
  are not reciprocal readiness. Content refusals can independently cause the
  existing coalesced refusal request.
- Revoke/demote a remote key before pending publication: retire its work.
  Recommit or promote it, then deliver its valid writer request: rearm once.
  Each new already-handshaken local writer interval, after either absence or
  viewer, produces one coalesced readiness request; ending that interval retires
  only its local readiness, not unrelated work. Unchanged eligibility does
  nothing. Fresh local key accounting starts anew; no cryptographic marks or
  frame budget reset.
- Gate attachment, local commitment, ending and budget at actual queueing,
  including a detach or role change while asynchronous sealing is in flight.
  Inject a transient sealing failure while repair is pending; restore sealing
  and require the request at the next due opportunity. Neither a failed attempt
  nor a gated attempt advances the successful-publication clock or consumes
  the obligation. Test the first possible due publication after a temporary
  gate, and the retirement of revoked work.
- Validate inner Step1 state vectors even when a local viewer suppresses
  answers or the expensive-reply allowance has been spent. A malformed inner
  vector must neither activate reciprocity nor consume first-request eligibility.
  Deliver a genuinely valid request later to prove eligibility remains. Test a
  valid prefix followed by malformed/undefined/truncated input: permitted earlier
  request and Update effects remain, but input past the parsing stop is not
  readiness. No second expensive diff may be used just to validate a vector.
- A viewer's Step1 is allowed and answered by writers. A viewer sends no Step2
  or Update, and its own Step1 cannot solicit prohibited viewer history through
  the reciprocal trigger. A local viewer may request committed writer history.

## Viewer history: policy A and display-only alternatives

For the chosen refuse/discard implementation policy A, test known-viewer
insertions and deletions are refused/discarded locally. Also test speculative
precommit mutations subsequently assigned viewer are discarded without losing
accepted remote history. This local presentation behavior is not mandatory for
all conforming clients: a client may retain a display-only fork. The common
conformance assertion is on published history, never mandatory local discard.

For each policy, test viewer insertion, deletion, insertion followed by deletion,
precommit speculation assigned viewer, same-key promotion, fresh-key reseating
as writer, and demotion/re-promotion. After promotion/reseating, solicit Step2
and apply every published content frame to an independent recipient. Verify
that the recipient gains none of the disallowed structs, deletion sets or
writer dependencies on those structs. Compare against the safe baseline using
actual CRDT histories, not just visible text. A viewer deletion can contaminate
history even though it makes the text shorter; an insertion followed by deletion
can contaminate history even though it leaves no visible text.

Include positive controls: remote updates received while viewer/speculative
survive; permitted offline writer insertions and deletions survive demotion and
re-promotion; legitimate later writer edits remain publishable. Removing history
exclusion alone must fail these tests separately from the reciprocal red control.

Queue an authenticated role state before a synchronous local insertion/deletion,
without awaiting its verification, and exercise both unknown-to-viewer assignment
and writer-to-viewer demotion. Classify the mutation at mutation time; the later
publication gate still forbids viewer Update/Step2. Promote again and prove a
legitimate later writer edit integrates at a real recipient. This pins the
asynchronous role/edit boundary, not just edits made after awaiting a state.

For policy A, observe rollback through the adapter as well as the peer: cache an
open speculative path, include a speculative-only root that disappears, accept
remote content during speculation, then apply the viewer state. Require editor
and mirror text to reconcile to the clean history, including removed roots and
local selections. A peer.text() assertion alone cannot detect a stale adapter
cache. Display-only policies instead test their chosen presentation while proving
that no display-only dependency reaches the publication history.

## Corpus gate and current limits

The existing decision vocabulary (`start`, `deliver`, `wait`, `expectSubject`,
`stop`) can pin a single-subject bounded request count, but it has no retained
reseat, local deletion, outbound-frame relay, or history/state-vector assertion
operation. `runner/yprotocols.py` reads a fixture subset: it has no general
pending-dependency integration or deletion-set replay/encoding. Its `Replica`
therefore cannot stand in for the actual implementations in the two-replica
recipes. Extending it into a new full CRDT is outside these recipes.

The accounting reference consumes already decoded message envelopes. It validates
inner Step1 vectors for readiness and keeps an earlier valid request when a later
inner vector/subtype is invalid; it does not change `sealed.Reader`'s existing
whole-frame refusal for malformed outer framing. Actual stream-application and
valid-prefix Update effects must be checked in the implementation recipes above.
Its `published` call records a caller-reported successful queue, not an exercised
asynchronous seal/transport boundary. None of these Python results establishes
that the separate client publication, edit-classification or adapter corrections
are complete.

Before adding a corpus vector, obtain known-correct emitted fixtures and subject
reports from the settled implementation semantics. Confirm the request clock,
partial-stream parsing and Step2 encoding with the adopting ports; then add only
the minimum subject operations needed for assertions, their mutation controls,
and the schema census. Do not invent expected sealed bytes or make a fixture
claim to retain history by copying text. No new mutation-census obligation is
claimed by these recipes.

Vectors 118 and 151 currently pin **incoming** viewer-content refusal; neither
requires a local edit to remain visible. Leave them intact. Any future local
viewer-edit vector must accept display-only retention when it cannot contaminate
publications. Single-subject vectors complement, rather than replace, the actual
multi-replica loss and history proofs above.
