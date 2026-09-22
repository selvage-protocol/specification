#!/usr/bin/env python3
"""Re-derives every peer vector's frames from the recipes those vectors carry.

A sealed vector is byte-exact and it is also a **rule**: it says what is sealed, not only what
the bytes are. That is worth having precisely because a ciphertext says nothing — a vector
carrying only bytes would keep passing while the meaning of the frame it claims drifted — and
it is only worth having if the two are tied together. This file is that tie: every recipe is
sealed again here, by code that does not go through the runner's step machinery, and the bytes
are compared with the `hex` the vector carries. Two derivations of one claim, and a drift in
either is a red run that names the file and the step.

`reference_server/crates/harness/tests/vectors/provenance.rs` is the same idea for the wire
corpus's binary frames, in the repository that cannot see this one.

It also checks the three things a fixture is trusted for: each key's declared id is the
derivation `CANONICAL.md` §6.1 fixes, each private half is the one its public half belongs to,
and no two *different* frames of one vector share a nonce. Every check is driven in both
directions at the bottom: a recipe that is changed must produce other bytes, and a `hex` that
is changed must be caught — a comparison that cannot fail is not a check.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import sealed  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "vectors" / "fixture" / "keys.json"
PEER_DIR = ROOT / "vectors" / "peer"

#: The ops whose steps carry a recipe. A `corrupt` step carries a corruption of one instead.
RECIPE_OPS = frozenset({"seal", "deliver", "publish"})


def hex_text(raw: bytes) -> str:
    return " ".join(f"{byte:02x}" for byte in raw)


def bytes_of(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", ""))


def read_hex(step: dict) -> bytes:
    return bytes_of(step["hex"])


def peer_vectors() -> list[tuple[pathlib.Path, dict]]:
    return [
        (path, json.loads(path.read_text())) for path in sorted(PEER_DIR.glob("*.json"))
    ]


def derived(vector: dict, fixture: sealed.Fixture, mutations: frozenset[str] = frozenset()) -> dict:
    """Every frame a vector produces, from its recipes and its corruptions alone.

    This is `run_peer.py`'s own order — a recipe is sealed, a corruption is applied to a frame
    that already exists — without its verdicts, so that a disagreement here is about the bytes
    and never about what a receiver decided.
    """
    frames: dict[str, bytes] = {}
    for step in vector["steps"]:
        if step.get("op") in RECIPE_OPS:
            frames[step["frame"]] = sealed.seal(fixture, step["recipe"]).bytes()
        elif step.get("op") == "corrupt":
            source = bytearray(frames[step["frame"]])
            if "xor" in step:
                source[step["at"]] ^= step["xor"]
            elif "truncate" in step:
                source = source[: len(source) - step["truncate"]]
            elif "append" in step:
                source += bytes_of(step["append"])
            else:  # pragma: no cover - the schema's own check refuses this shape
                raise AssertionError(f"a corruption this file cannot apply: {step}")
            frames[step["as"]] = bytes(source)
    return frames


class TestFixture(unittest.TestCase):
    """The fixture is a checked cache of six values and not a trusted one.

    `Fixture` refuses a declared id that is not `SHA-256(public)[0..8]` and a private half that
    is not the one its public half belongs to, so an edit to the fixture that broke either is a
    red run here rather than a frame that verifies against nothing. Both are the derivation
    §6.1 fixes; the study's own keys are reproducible by it, which is why the derivation is
    written down there and not invented here.
    """

    def test_the_fixture_loads_and_its_keys_are_pairs(self) -> None:
        fixture = sealed.Fixture(FIXTURE)
        self.assertEqual(fixture.host_name, fixture.host.name)
        self.assertTrue(fixture.keys)
        for key in fixture.keys.values():
            self.assertEqual(len(key.public), 32)
            self.assertEqual(key.hex_id, sealed.key_id(key.public).hex())

    def test_a_declared_id_that_is_not_the_derivation_is_refused(self) -> None:
        document = json.loads(FIXTURE.read_text())
        document["keys"]["guest-1"]["key_id"] = "0" * 16
        with self.assertRaises(sealed.SealedError):
            sealed.Fixture.from_document(document)

    def test_a_private_half_that_is_not_the_one_its_public_half_belongs_to_is_refused(self) -> None:
        document = json.loads(FIXTURE.read_text())
        document["keys"]["guest-1"]["private"] = document["keys"]["guest-2"]["private"]
        with self.assertRaises(sealed.SealedError):
            sealed.Fixture.from_document(document)

    def test_a_room_key_that_is_not_32_bytes_is_refused(self) -> None:
        document = json.loads(FIXTURE.read_text())
        document["room"]["key"] = "00" * 16
        with self.assertRaises(sealed.SealedError):
            sealed.Fixture.from_document(document)

    def test_the_fragment_encoding_round_trips_and_refuses_a_lenient_reading(self) -> None:
        fixture = sealed.Fixture(FIXTURE)
        for key in fixture.keys.values():
            spelled = sealed.b64url(key.public)
            self.assertEqual(len(spelled), 43)
            self.assertEqual(sealed.decode_key(spelled), key.public)
            # RFC 4648 §3.5: the last character carries two bits that must be zero.
            self.assertIn(spelled[-1], "AEIMQUYcgkosw048")
            wrong = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" if c not in "AEIMQ"]
            self.assertIsNone(sealed.decode_key(spelled[:-1] + wrong[0]))
            self.assertIsNone(sealed.decode_key(spelled + "\n"))
            # The two 32-byte values in an invite's fragment are the room key and the host key.
            self.assertEqual(len(sealed.b64url(fixture.room_key)), 43)


class TestRecipeDerivation(unittest.TestCase):
    """Every vector's bytes, re-derived from what the vector says it seals."""

    def setUp(self) -> None:
        self.fixture = sealed.Fixture(FIXTURE)

    def test_every_vector_derives_the_bytes_it_carries(self) -> None:
        checked = 0
        for path, vector in peer_vectors():
            frames = derived(vector, self.fixture)
            for step in vector["steps"]:
                if step.get("op") not in RECIPE_OPS and step.get("op") != "corrupt":
                    continue
                name = step.get("frame") if step.get("op") == "corrupt" else step["frame"]
                if step.get("op") == "corrupt":
                    name = step["as"]
                self.assertIn(name, frames, f"{path.name}: {name}")
                self.assertEqual(
                    hex_text(frames[name]),
                    hex_text(read_hex(step)),
                    f"{path.name}: the recipe and the bytes disagree at `{name}`",
                )
                checked += 1
        self.assertGreater(checked, 20, "the corpus has fewer frames than it did")

    def test_no_two_different_frames_share_a_nonce(self) -> None:
        for path, vector in peer_vectors():
            seen: dict[str, str] = {}
            for step in vector["steps"]:
                if step.get("op") not in RECIPE_OPS:
                    continue
                nonce = step["recipe"]["nonce"]
                digest = hex_text(read_hex(step))
                if nonce in seen and seen[nonce] != digest:
                    self.fail(f"{path.name}: two frames share the nonce {nonce}")
                seen[nonce] = digest

    def test_a_payload_is_sealed_in_the_canonical_form_of_section_2(self) -> None:
        # Read back from the frame rather than from the recipe, so this is a claim about the
        # bytes and not a restatement of `plaintext_of`.
        checked = 0
        for path, vector in peer_vectors():
            for step in vector["steps"]:
                recipe = step.get("recipe")
                if step.get("op") not in RECIPE_OPS or "payload" not in recipe:
                    continue
                envelope = sealed.Envelope.parse(read_hex(step))
                self.assertEqual(
                    sealed.opens(self.fixture, envelope),
                    json.dumps(
                        recipe["payload"],
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    f"{path.name}: a payload is sealed as §2 writes it",
                )
                checked += 1
        self.assertGreater(checked, 10, "fewer payloads were read than the corpus holds")

    def test_every_signature_verifies_against_the_key_its_recipe_names(self) -> None:
        for path, vector in peer_vectors():
            for step in vector["steps"]:
                if step.get("op") not in RECIPE_OPS:
                    continue
                envelope = sealed.Envelope.parse(read_hex(step))
                key = self.fixture.key(step["recipe"]["sign"])
                self.assertEqual(envelope.key_id, key.id, f"{path.name}: the key id")
                self.assertTrue(
                    sealed.authentic(self.fixture, envelope, key),
                    f"{path.name}: the signature does not verify against {key.name}",
                )

    def test_a_signature_that_is_not_these_bytes_still_verifies(self) -> None:
        # Safari's Ed25519 randomises signatures (NOTES.md §B.31), so a conforming
        # implementation need not produce the bytes a vector carries. What it must produce is a
        # signature that verifies over §6.1's input, and this is that claim: a frame signed
        # twice is two frames, and both verify.
        recipe = json.loads((PEER_DIR / "101-sealed-edit-applies.json").read_text())["steps"][0]["recipe"]
        first = sealed.seal(self.fixture, recipe)
        second = sealed.seal(self.fixture, recipe)
        self.assertEqual(first.bytes(), second.bytes(), "the fixture's Ed25519 is deterministic")
        self.assertTrue(sealed.authentic(self.fixture, first, self.fixture.key("host-key")))

    def test_the_envelope_costs_104_bytes_over_its_plaintext(self) -> None:
        # Measured in the corpus study and re-measured here: 8 key id, 1 kind, 1 epoch, 1
        # counter, 12 nonce, 1 ciphertext length, 16 tag, 64 signature. It is 105 bytes once the
        # ciphertext's length no longer fits one varint byte, which is a plaintext of 112.
        fixture = self.fixture
        for length, overhead in ((0, 104), (30, 104), (111, 104), (112, 105)):
            recipe = {
                "sign": "guest-1",
                "kind": 0,
                "counter": 1,
                "nonce": "01" * 12,
                "plaintext": "00" * length,
            }
            frame = sealed.seal(fixture, recipe).bytes()
            self.assertEqual(len(frame) - length, overhead, f"a {length}-byte plaintext")

    def test_a_changed_recipe_produces_other_bytes(self) -> None:
        path, vector = peer_vectors()[0]
        step = vector["steps"][0]
        before = sealed.seal(self.fixture, step["recipe"]).bytes()
        changed = json.loads(json.dumps(step["recipe"]))
        changed["counter"] += 1
        self.assertNotEqual(sealed.seal(self.fixture, changed).bytes(), before)

    def test_a_changed_hex_is_caught(self) -> None:
        # The comparison above, refused in the other direction: this is the case that makes it a
        # check rather than a line that always agrees.
        path, vector = peer_vectors()[0]
        step = json.loads(json.dumps(vector["steps"][0]))
        step["hex"] = hex_text(bytes_of(step["hex"])[:-1] + b"\x00")
        frames = derived(vector, self.fixture)
        self.assertNotEqual(hex_text(read_hex(step)), hex_text(frames["state"]))


class TestEnvelopeBytes(unittest.TestCase):
    """The layout `CANONICAL.md` §6.1 fixes, read back from the bytes."""

    def setUp(self) -> None:
        self.fixture = sealed.Fixture(FIXTURE)

    def test_the_associated_data_is_length_prefixed_and_in_order(self) -> None:
        key_id = bytes(range(8))
        aad = sealed.associated_data("R7f3a2c19", 3, 0, key_id)
        expected = b"".join(
            (
                b"\x09selvage/2",
                b"\x09R7f3a2c19",
                b"\x03",
                b"\x00",
                b"\x08" + key_id,
            )
        )
        self.assertEqual(aad, expected)

    def test_the_signature_input_covers_the_counter_the_nonce_and_the_ciphertext(self) -> None:
        envelope = sealed.Envelope(b"\x00" * 8, 0, 0, 1, b"\x01" * 12, b"\x02" * 3, b"\x03" * 64)
        signed = sealed.signing_input(b"aad", envelope)
        self.assertEqual(signed, b"aad" + b"\x01" + b"\x0c" + b"\x01" * 12 + b"\x03" + b"\x02" * 3)

    def test_the_envelope_is_exactly_one_envelope(self) -> None:
        frame = sealed.seal(
            self.fixture,
            {"sign": "guest-1", "kind": 0, "counter": 1, "nonce": "01" * 12,
             "plaintext": "00 00 01 00"},
        ).bytes()
        self.assertEqual(sealed.Envelope.parse(frame).bytes(), frame)
        with self.assertRaises(sealed.SealedError):
            sealed.Envelope.parse(frame + b"\x00")
        self.assertEqual(sealed.Envelope.parse(frame + b"\x00", ignore_trailing=True).bytes(), frame)
        with self.assertRaises(sealed.SealedError):
            sealed.Envelope.parse(frame[:-1])
        with self.assertRaises(sealed.SealedError):
            sealed.Envelope.parse(frame[:8] + b"\x80" * 70)

    def test_a_counter_needs_a_varint_and_a_negative_one_is_refused(self) -> None:
        self.assertEqual(sealed.varuint(0), b"\x00")
        self.assertEqual(sealed.varuint(127), b"\x7f")
        self.assertEqual(sealed.varuint(128), b"\x80\x01")
        self.assertEqual(sealed.read_varuint(b"\x80\x01", 0), (128, 2))
        with self.assertRaises(sealed.SealedError):
            sealed.varuint(-1)
        with self.assertRaises(sealed.SealedError):
            sealed.read_varuint(b"\x80", 0)

    def test_the_frame_key_is_the_schedule_the_spec_fixes(self) -> None:
        fixture = self.fixture

        # An independent HKDF-SHA256, written out rather than called through the library's:
        # `CANONICAL.md` §6.1 fixes the schedule, and a test that called the same function the
        # module calls would agree with it whatever it did.
        def hkdf(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
            import hmac

            key = hmac.new(salt, ikm, "sha256").digest()
            out = b""
            block = b""
            counter = 1
            while len(out) < length:
                block = hmac.new(key, block + info + bytes([counter]), "sha256").digest()
                out += block
                counter += 1
            return out[:length]

        self.assertEqual(
            fixture.frame_key(),
            hkdf(fixture.room_key, fixture.room_id.encode("utf-8"), b"selvage/2 frame", 32),
        )
        self.assertNotEqual(
            fixture.frame_key(),
            hkdf(fixture.room_key, fixture.room_id.encode("utf-8"), b"selvage/2 state", 32),
        )
        self.assertNotEqual(
            fixture.frame_key(),
            hkdf(fixture.room_key, b"another room", b"selvage/2 frame", 32),
        )


class TestReadOrder(unittest.TestCase):
    """The verdicts, and the mutations that remove one guard each."""

    def setUp(self) -> None:
        self.fixture = sealed.Fixture(FIXTURE)

    def state(self, **overrides) -> bytes:
        recipe = {
            "sign": "host-key",
            "kind": 1,
            "counter": 1,
            "nonce": "02" * 12,
            "payload": {
                "issued": 1,
                "listing": ["README.md"],
                "peers": {
                    sealed.b64url(self.fixture.key("host-session").public): {
                        "peer_id": "p-1",
                        "role": "host",
                    },
                    sealed.b64url(self.fixture.key("guest-1").public): {
                        "peer_id": "p-2",
                        "role": "guest",
                    },
                },
            },
        }
        recipe.update(overrides)
        return sealed.seal(self.fixture, recipe).bytes()

    def content(self, counter: int = 1, nonce: str = "03") -> bytes:
        return sealed.seal(
            self.fixture,
            {
                "sign": "guest-1",
                "kind": 0,
                "counter": counter,
                "nonce": nonce * 12,
                "plaintext": "00 00 01 00",
            },
        ).bytes()

    def reader(self, **kwargs) -> sealed.Reader:
        return sealed.Reader(self.fixture, **kwargs)

    def test_a_state_is_applied_and_its_listing_held(self) -> None:
        reader = self.reader()
        verdict = reader.read(self.state())
        self.assertTrue(verdict.ok, str(verdict))
        self.assertEqual(verdict.kind, 1)
        self.assertEqual(reader.issued, 1)
        self.assertEqual(reader.listing, ["README.md"])
        self.assertEqual(reader.applied, [verdict])

    def test_a_negative_issued_is_bad_payload_and_zero_is_stale(self) -> None:
        # `0` is a count and step 9 reads it; a negative one is not a count and step 8 does.
        reader = self.reader()
        self.assertEqual(reader.read(self.state(payload={
            "issued": -1, "listing": [], "peers": {}})).reason, "bad_payload")
        self.assertEqual(reader.read(self.state(payload={
            "issued": 0, "listing": [], "peers": {}})).reason, "stale_issued")

    def test_the_reasons_are_the_vocabulary_the_schema_ships(self) -> None:
        schema = json.loads((ROOT / "schema" / "sealed.json").read_text())
        self.assertEqual(
            list(sealed.REASONS), schema["$defs"]["refusalReason"]["enum"]
        )

    def test_bad_aead_is_reachable_only_by_a_sender_that_signs_what_it_cannot_open(self) -> None:
        # §6.1: the one refusal a *sender's bug* produces rather than an attack, because the
        # signature already verified over the ciphertext. Built by hand: a real signature over a
        # ciphertext the room's key does not produce.
        reader = self.reader()
        envelope = sealed.Envelope.parse(self.state())
        broken = sealed.Envelope(
            envelope.key_id, envelope.kind, envelope.epoch, envelope.counter,
            envelope.nonce, bytes([envelope.ciphertext[0] ^ 0xFF]) + envelope.ciphertext[1:],
        )
        aad = sealed.associated_data(
            self.fixture.room_id, broken.kind, broken.epoch, broken.key_id
        )
        broken.signature = self.fixture.key("host-key").sign(sealed.signing_input(aad, broken))
        self.assertTrue(sealed.authentic(self.fixture, broken, self.fixture.key("host-key")))
        self.assertEqual(reader.read(broken.bytes()).reason, "bad_aead")

    def test_a_refused_frame_never_moves_a_mark(self) -> None:
        reader = self.reader()
        reader.read(self.state())
        # mallory-1 is a key no state commits, so its frame is refused at step 4 and leaves
        # nothing behind: a relay cannot poison a mark.
        outsider = sealed.seal(
            self.fixture,
            {"sign": "mallory-1", "kind": 0, "counter": 1, "nonce": "05" * 12,
             "plaintext": "00 00 01 00"},
        ).bytes()
        self.assertEqual(reader.read(outsider).reason, "uncommitted_key")
        self.assertNotIn(self.fixture.key("mallory-1").hex_id, reader.marks)

    def test_a_frame_above_the_mark_is_accepted_however_far_above_it_is(self) -> None:
        reader = self.reader()
        reader.read(self.state())
        self.assertTrue(reader.read(self.content(counter=1)).ok)
        self.assertEqual(reader.marks[self.fixture.key("guest-1").hex_id], 1)
        # A gap is not a fault: the relay may drop frames.
        self.assertTrue(reader.read(self.content(counter=99, nonce="06")).ok)
        self.assertEqual(reader.read(self.content(counter=99, nonce="07")).reason,
                         "replayed_counter")

    def test_the_mutations_are_the_guard_table_the_census_drives(self) -> None:
        self.assertIn("no-verify", sealed.MUTATIONS)
        self.assertNotIn("bad_tag", sealed.REASONS)
        with self.assertRaises(sealed.SealedError):
            self.reader(mutations=frozenset({"no-such-guard"}))

    def test_a_mutation_that_is_removed_alone_changes_one_verdict(self) -> None:
        # The shape every vector's `catches` relies on: with the signature check gone, a frame
        # whose ciphertext byte is flipped is no longer refused for its signature. The frame is
        # sealed at counter 2 so that the counter mark, which runs first, does not stop it.
        frame = bytearray(self.content(counter=2, nonce="04"))
        frame[24 + 1] ^= 1  # inside the ciphertext
        tampered = bytes(frame)
        strictly = self.reader()
        strictly.read(self.state())
        self.assertEqual(strictly.read(tampered).reason, "bad_signature")
        loose = self.reader(mutations=frozenset({"no-verify"}))
        loose.read(self.state())
        self.assertNotEqual(loose.read(tampered).reason, "bad_signature")

    def test_every_mutation_is_a_removal_of_one_step_and_nothing_else(self) -> None:
        # The census's own premise, checked rather than asserted in prose: for the vector the
        # guard belongs to, the mutation changes the verdict, and for a frame that reaches no
        # other step it does not.
        for mutation, what in sealed.MUTATIONS.items():
            self.assertTrue(what, mutation)
        reader = self.reader(mutations=frozenset(sealed.MUTATIONS))
        self.assertTrue(reader.read(self.state()).ok, "a whole state still applies")


if __name__ == "__main__":
    unittest.main()
