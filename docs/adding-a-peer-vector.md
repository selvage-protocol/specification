# Adding a peer vector

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
4. **A `decision` vector about a link is refused or seated, and it carries the leg that must not
   be refused.** §5.1's link rules — a fragment's missing or malformed key, and a `room`, `token`,
   `k` or `h` a link repeats — are answered by the client's refusal of
   the `join`: `expectRefusal` names what the client's own words must carry, and a vector asserting
   one carries a `start` that must join, because a subject that refuses every link passes a
   refusal leg and is caught by the seating beside it. A guard that sits on the link is removed
   before the `join` (`runner/subject.py`'s `LINK_MUTATIONS`). One vector carries **one** leg
   that seats the subject: a subject already in a session refuses the next `join`, so a second
   seating leg is a failure of the harness rather than a decision.
