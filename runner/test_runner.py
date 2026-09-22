#!/usr/bin/env python3
"""Checks the replay's comparison code without a server: the layer that decides whether a
transcript held.

CI cannot build a `selvaged`, so `python3 schema/validate.py` and
`run_vectors.py --schema-only` never reach `matches`, `matches_unordered`, `expected_bytes`,
`check_text` or `check_frame_spec` — a byte comparison that does not run cannot fail, and
a byte comparison that cannot fail is not a check. These cases drive all five, in both
directions: every rule is given a pair that must match and a pair that must not.

The same reason applies to the audit that reports a connection one frame ahead: it decides
what a failure means, so it is driven here against a connection whose queue is known.

They need no server and no `websockets`: `run_vectors` imports what it can, and its comparison
and reporting functions are pure, while the connections are scripted sockets.

**The peer layer is checked here too**, for the same reason and with the same rule. `sealed.py`
is the code that writes and reads a `selvage/2` frame, and `subject.py` is the protocol the
decision layer will be driven through; neither can be reached by `schema/validate.py` (which is
key-free) or by `run_peer.py` alone (which only exercises what the vectors happen to cover). So
the envelope's layout, the counter mark, the refusal vocabulary and the subject's framing and
deadlines are driven here in both directions, and the mutation tables are compared with what the
corpus declares. The subject is a **stub that runs from this file** — `python3 test_runner.py
--stub-subject BEHAVIOUR` — so no temporary script is written anywhere to test it.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import run_peer  # noqa: E402
import run_vectors  # noqa: E402
import sealed  # noqa: E402
import subject  # noqa: E402
from run_vectors import (  # noqa: E402
    Bindings,
    Incoming,
    Mismatch,
    Peer,
    ReplayError,
    Session,
    check_corpus_size,
    check_frame_spec,
    check_text,
    describe_failure,
    describe_unread,
    expected_bytes,
    failure_message,
    matches,
    matches_unordered,
    pending_reads,
    unread_frames,
)

VECTOR_DIR = pathlib.Path(__file__).resolve().parent.parent / "vectors"
PEER_DIR = VECTOR_DIR / "peer"
SCHEMA_DIR = pathlib.Path(__file__).resolve().parent.parent / "schema"


def validate_module():
    """`schema/validate.py`, for the pins and the vocabulary it owns. It needs `jsonschema`,
    which the flake's runner environment has; a machine without it skips these cases rather than
    failing them, and says so."""
    try:
        return run_vectors.load_validate_module()
    except SystemExit as error:  # the module exits 2 when its own imports are missing
        raise unittest.SkipTest(f"schema/validate.py needs jsonschema: {error}")


def peer_vectors() -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(PEER_DIR.glob("*.json"))]


def canonical(value: object) -> str:
    """A frame in the byte form of `CANONICAL.md`, which is what a vector carries."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hex_bytes(text: str) -> bytes:
    return bytes(int(byte, 16) for byte in text.split())


def vector(vector_id: str) -> dict:
    for path in sorted(VECTOR_DIR.glob("*.json")):
        document = json.loads(path.read_text())
        if document["id"] == vector_id:
            return document
    raise AssertionError(f"no vector {vector_id}")


class TestMatches(unittest.TestCase):
    def test_a_placeholder_binds_and_must_hold(self) -> None:
        bindings = Bindings()
        matches({"room_id": "$room"}, {"room_id": "r-1"}, bindings)
        matches({"room_id": "$room"}, {"room_id": "r-1"}, bindings)
        with self.assertRaises(Mismatch):
            matches({"room_id": "$room"}, {"room_id": "r-2"}, bindings)

    def test_any_placeholder_matches_without_binding(self) -> None:
        bindings = Bindings()
        matches({"message": "$_"}, {"message": "one"}, bindings)
        matches({"message": "$_"}, {"message": "another"}, bindings)

    def test_a_placeholder_against_a_number_is_not_a_binding(self) -> None:
        with self.assertRaises(Mismatch):
            matches({"id": "$id"}, {"id": 1}, Bindings())

    def test_the_member_set_is_exact_in_both_directions(self) -> None:
        with self.assertRaises(Mismatch):
            matches({"a": 1}, {"a": 1, "b": 2}, Bindings())
        with self.assertRaises(Mismatch):
            matches({"a": 1, "b": 2}, {"a": 1}, Bindings())

    def test_a_value_of_the_wrong_type_does_not_match(self) -> None:
        with self.assertRaises(Mismatch):
            matches({"a": 1}, {"a": "1"}, Bindings())
        with self.assertRaises(Mismatch):
            matches({"a": True}, {"a": 1}, Bindings())


class TestPeers(unittest.TestCase):
    def test_peers_are_compared_as_a_set(self) -> None:
        first = {"display_name": "Ada", "peer_id": "$host_peer", "role": "host"}
        second = {"display_name": "Bob", "peer_id": "$guest_peer", "role": "guest"}
        wire = [
            {"display_name": "Bob", "peer_id": "p-2", "role": "guest"},
            {"display_name": "Ada", "peer_id": "p-1", "role": "host"},
        ]
        bindings = Bindings()
        matches_unordered([first, second], wire, bindings)
        self.assertEqual(bindings.named["$host_peer"], "p-1")
        self.assertEqual(bindings.named["$guest_peer"], "p-2")

    def test_a_missing_or_extra_peer_does_not_match(self) -> None:
        one = [{"display_name": "Ada", "peer_id": "$p", "role": "host"}]
        with self.assertRaises(Mismatch):
            matches_unordered(one, [], Bindings())
        with self.assertRaises(Mismatch):
            matches_unordered(
                one,
                [
                    {"display_name": "Ada", "peer_id": "p-1", "role": "host"},
                    {"display_name": "Bob", "peer_id": "p-2", "role": "guest"},
                ],
                Bindings(),
            )

    def test_a_peer_with_the_wrong_members_does_not_match(self) -> None:
        want = [{"display_name": "Ada", "peer_id": "$p", "role": "host"}]
        have = [{"display_name": "Ada", "role": "host"}]
        with self.assertRaises(Mismatch):
            matches_unordered(want, have, Bindings())

    def test_a_string_array_in_another_order_is_the_same_array(self) -> None:
        # The prose promises no order for `capabilities` either, and it is built from a
        # constant here and from whatever an implementation holds elsewhere.
        want = ["y-protocols/1", "awareness", "host-reclaim"]
        matches_unordered(
            list(want), ["host-reclaim", "y-protocols/1", "awareness"], Bindings()
        )
        with self.assertRaises(Mismatch):
            matches_unordered(list(want), ["y-protocols/1", "awareness"], Bindings())

    def test_a_placeholder_bound_before_a_reordered_array_survives(self) -> None:
        # The re-order rebuilds this array's placeholders in the wire's order, and the
        # byte comparison writes the whole frame from all of them: the ones bound before
        # the array must not be dropped on the way.
        expected = canonical(
            {
                "event": "room.joined",
                "params": {
                    "a_peer": "$first",
                    "peers": [
                        {"display_name": "Ada", "peer_id": "$host_peer", "role": "host"},
                        {"display_name": "Bob", "peer_id": "$guest_peer", "role": "guest"},
                    ],
                },
                "v": "selvage/1",
            }
        )
        actual = canonical(
            {
                "event": "room.joined",
                "params": {
                    "a_peer": "p-9",
                    "peers": [
                        {"display_name": "Bob", "peer_id": "p-2", "role": "guest"},
                        {"display_name": "Ada", "peer_id": "p-1", "role": "host"},
                    ],
                },
                "v": "selvage/1",
            }
        )
        check_text(actual, expected, Bindings())

    def test_a_documents_array_is_compared_in_order(self) -> None:
        # `PROTOCOL.md` §6.2 promises `documents` first-opened order, so the comparison must
        # hold the wire to it rather than treat it as a set.
        bindings = Bindings()
        matches(
            {"documents": ["a.rs", "b.rs"]},
            {"documents": ["a.rs", "b.rs"]},
            bindings,
        )
        with self.assertRaises(Mismatch):
            matches(
                {"documents": ["a.rs", "b.rs"]},
                {"documents": ["b.rs", "a.rs"]},
                Bindings(),
            )

    def test_a_peers_array_in_another_order_is_the_same_frame(self) -> None:
        # The server's order is not something the vector can name. The byte comparison has
        # to see the frame the wire sent, not the order the vector chose (`CANONICAL.md`
        # §2.7).
        ada = {"display_name": "Ada", "peer_id": "$host_peer", "role": "host"}
        bob = {"display_name": "Bob", "peer_id": "$guest_peer", "role": "guest"}
        expected = canonical(
            {"event": "room.joined", "params": {"peers": [ada, bob], "room_id": "$room"}, "v": "selvage/1"}
        )
        actual = canonical(
            {
                "event": "room.joined",
                "params": {
                    "peers": [
                        {"display_name": "Bob", "peer_id": "p-2", "role": "guest"},
                        {"display_name": "Ada", "peer_id": "p-1", "role": "host"},
                    ],
                    "room_id": "r-1",
                },
                "v": "selvage/1",
            }
        )
        bindings = Bindings()
        check_text(actual, expected, bindings)
        self.assertEqual(bindings.named["$host_peer"], "p-1")
        self.assertEqual(bindings.named["$guest_peer"], "p-2")

    def test_a_reordered_peers_array_is_still_checked_member_by_member(self) -> None:
        ada = {"display_name": "Ada", "peer_id": "$host_peer", "role": "host"}
        bob = {"display_name": "Bob", "peer_id": "$guest_peer", "role": "guest"}
        expected = canonical(
            {"event": "room.joined", "params": {"peers": [ada, bob], "room_id": "$room"}, "v": "selvage/1"}
        )
        other = canonical(
            {
                "event": "room.joined",
                "params": {
                    "peers": [
                        {"display_name": "Mallory", "peer_id": "p-2", "role": "guest"},
                        {"display_name": "Ada", "peer_id": "p-1", "role": "host"},
                    ],
                    "room_id": "r-1",
                },
                "v": "selvage/1",
            }
        )
        with self.assertRaises(Mismatch):
            check_text(other, expected, Bindings())


    def test_a_reordered_capabilities_array_is_the_same_frame(self) -> None:
        expected = canonical(
            {
                "event": "room.joined",
                "params": {
                    "capabilities": ["y-protocols/1", "awareness"],
                    "room_id": "$room",
                },
                "v": "selvage/1",
            }
        )
        actual = canonical(
            {
                "event": "room.joined",
                "params": {
                    "capabilities": ["awareness", "y-protocols/1"],
                    "room_id": "r-1",
                },
                "v": "selvage/1",
            }
        )
        check_text(actual, expected, Bindings())

    def test_a_reordered_documents_array_is_not_the_same_frame(self) -> None:
        # `documents` is the one array whose order the prose promises.
        expected = canonical(
            {
                "event": "doc.opened",
                "params": {"documents": ["a.rs", "b.rs"], "path": "a.rs"},
                "v": "selvage/1",
            }
        )
        actual = canonical(
            {
                "event": "doc.opened",
                "params": {"documents": ["b.rs", "a.rs"], "path": "a.rs"},
                "v": "selvage/1",
            }
        )
        with self.assertRaises(Mismatch):
            check_text(actual, expected, Bindings())


class TestExpectedBytes(unittest.TestCase):
    def test_placeholders_are_replaced_in_order(self) -> None:
        text = '{"a":"$x","b":["$y"]}'
        self.assertEqual(
            expected_bytes(text, ["1", "2"]),
            '{"a":"1","b":["2"]}',
        )

    def test_a_matched_value_is_written_as_a_json_string(self) -> None:
        self.assertEqual(expected_bytes('"$x"', ['a"b']), '"a\\"b"')

    def test_an_unclosed_placeholder_is_an_error(self) -> None:
        with self.assertRaises(Mismatch):
            expected_bytes('{"a":"$x}', ["1"])

    def test_a_placeholder_with_no_matched_value_is_an_error(self) -> None:
        with self.assertRaises(Mismatch):
            expected_bytes('{"a":"$x","b":"$y"}', ["1"])


class TestCheckText(unittest.TestCase):
    def test_a_canonical_frame_matches_and_binds_for_the_next_one(self) -> None:
        bindings = Bindings()
        first = canonical(
            {"event": "room.created", "params": {"room_id": "$room", "token": "$token"}, "v": "selvage/1"}
        )
        check_text(
            canonical(
                {"event": "room.created", "params": {"room_id": "r-1", "token": "t-1"}, "v": "selvage/1"}
            ),
            first,
            bindings,
        )
        second = canonical({"event": "room.joined", "params": {"room_id": "$room"}, "v": "selvage/1"})
        check_text(
            canonical({"event": "room.joined", "params": {"room_id": "r-1"}, "v": "selvage/1"}),
            second,
            bindings,
        )
        with self.assertRaises(Mismatch):
            check_text(
                canonical({"event": "room.joined", "params": {"room_id": "r-2"}, "v": "selvage/1"}),
                second,
                bindings,
            )

    def test_a_frame_that_is_not_the_claimed_bytes_fails(self) -> None:
        expected = canonical({"event": "room.joined", "params": {"room_id": "r-1"}, "v": "selvage/1"})
        # The same members, in the order the tables list them rather than sorted.
        reordered = '{"v":"selvage/1","event":"room.joined","params":{"room_id":"r-1"}}'
        with self.assertRaises(Mismatch) as caught:
            check_text(reordered, expected, Bindings())
        self.assertIn("canonical bytes", str(caught.exception))

    def test_insignificant_whitespace_is_still_a_byte_difference(self) -> None:
        expected = canonical({"event": "room.joined", "params": {"room_id": "r-1"}, "v": "selvage/1"})
        spaced = expected.replace(":", ": ")
        with self.assertRaises(Mismatch):
            check_text(spaced, expected, Bindings())

    def test_a_number_that_is_only_equal_by_value_fails_the_bytes(self) -> None:
        # `1.0` == `1` in every JSON reader, and `CANONICAL.md` §2.4 forbids it on the
        # wire: the structural comparison cannot see this, the byte comparison can.
        with self.assertRaises(Mismatch):
            check_text('{"id":1.0,"result":{},"v":"selvage/1"}', canonical({"id": 1, "result": {}, "v": "selvage/1"}), Bindings())

    def test_a_missing_member_names_it(self) -> None:
        with self.assertRaises(Mismatch) as caught:
            check_text(
                canonical({"event": "room.gone", "params": {"room_id": "r-1"}, "v": "selvage/1"}),
                canonical({"event": "room.gone", "params": {"reason": "x", "room_id": "$r"}, "v": "selvage/1"}),
                Bindings(),
            )
        self.assertIn("reason", str(caught.exception))


class TestCheckFrameSpec(unittest.TestCase):
    def test_vector_010_descriptions_match_the_sent_bytes(self) -> None:
        sent: str | None = None
        checked = 0
        for step in vector("010")["steps"]:
            if step["op"] == "sendBinary":
                sent = step["hex"]
            if step["op"] == "expectBinary" and "frame" in step:
                self.assertIsNotNone(sent, "a frame description before any send")
                check_frame_spec(step["frame"], hex_bytes(sent))
                checked += 1
        self.assertEqual(checked, 2, "vector 010 describes two frames")

    def test_the_wrong_sync_type_is_a_mismatch(self) -> None:
        # Vector 009's first frame is a SyncStep1 with an empty state vector.
        frame = next(
            step["hex"]
            for step in vector("009")["steps"]
            if step["op"] == "sendBinary" and step["hex"] == "00 00 01 00"
        )
        check_frame_spec({"message_type": 0, "sync_type": 0}, hex_bytes(frame))
        with self.assertRaises(Mismatch):
            check_frame_spec({"message_type": 0, "sync_type": 1}, hex_bytes(frame))

    def test_an_awareness_frame_without_a_spec_is_a_mismatch(self) -> None:
        step = next(
            step
            for step in vector("010")["steps"]
            if step["op"] == "sendBinary" and "hex" in step
        )
        with self.assertRaises(Mismatch) as caught:
            check_frame_spec({"message_type": 1}, hex_bytes(step["hex"]))
        self.assertIn("awareness", str(caught.exception))


class TestEveryFrameDescription(unittest.TestCase):
    """Every `frame` description in the corpus has to be usable against the bytes."""

    def test_each_description_decodes_the_last_sent_frame(self) -> None:
        checked = 0
        for path in sorted(VECTOR_DIR.glob("*.json")):
            sent: str | None = None
            for step in json.loads(path.read_text())["steps"]:
                if step["op"] == "sendBinary" and "hex" in step:
                    sent = step["hex"]
                if step["op"] in ("expectBinary", "sendBinary") and "frame" in step:
                    self.assertIsNotNone(
                        sent,
                        f"{path.name}: a frame description with no sent bytes to check",
                    )
                    check_frame_spec(step["frame"], hex_bytes(sent))
                    checked += 1
        self.assertGreater(checked, 0, "no vector describes a binary frame")


class TestSendBinaryShapes(unittest.TestCase):
    """Every `sendBinary` carries the bytes it sends.

    A send is bytes the runner must transmit, and a `frame` description is not
    transmittable, so the schema half requires `hex` on every send while an
    `expectBinary` may still assert by description. This pins that contract on the
    corpus side: a frame-only send is a check that never runs.
    """

    def test_every_sendBinary_carries_hex(self) -> None:
        for path in sorted(VECTOR_DIR.glob("*.json")):
            for step in json.loads(path.read_text())["steps"]:
                if step.get("op") == "sendBinary":
                    self.assertIn(
                        "hex",
                        step,
                        f"{path.name}: a send is bytes, and a frame description is not",
                    )


class TestCorpusSize(unittest.TestCase):
    """The replay holds the same file-count pin the schema half pins.

    Without it a shrunk directory replays green on less: the count is a check. The
    number itself lives in `schema/validate.py` alone and arrives here as an argument.
    """

    def test_the_pinned_corpus_passes(self) -> None:
        check_corpus_size([{}] * 23, 23)

    def test_a_shrunk_directory_fails(self) -> None:
        with self.assertRaises(ReplayError):
            check_corpus_size([{}] * 22, 23)

    def test_an_empty_directory_fails(self) -> None:
        with self.assertRaises(ReplayError):
            check_corpus_size([], 23)


class TestReplayVerdict(unittest.TestCase):
    """A frame no step reads fails the vector: the drain audit is a verdict.

    The server is scripted out — `replay` is replaced with the unread it would have
    returned, and the corpus pin with a stub — so these cases decide the verdict alone.
    """

    def replay_all_with(
        self, unread: dict, files: int = 1, pinned: int | None = None
    ) -> tuple[int, int]:
        vector = {"_file": "000-probe.json", "id": "000"}
        pin = mock.Mock(EXPECTED_WIRE_VECTORS=files if pinned is None else pinned)
        with (
            mock.patch.object(
                run_vectors, "load_vectors", return_value=[vector] * files
            ),
            mock.patch.object(run_vectors, "load_validate_module", return_value=pin),
            mock.patch.object(
                run_vectors, "replay", new=mock.AsyncMock(return_value=unread)
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return run_vectors.replay_all("dummy")

    def test_a_transcript_that_reads_everything_passes(self) -> None:
        self.assertEqual(self.replay_all_with({}), (1, 0))

    def test_a_frame_no_step_reads_fails_the_run(self) -> None:
        unread = {"guest": [Incoming("text", text='{"event":"peer.joined"}')]}
        self.assertEqual(self.replay_all_with(unread), (1, 1))

    def test_a_corrupt_corpus_attempts_no_vectors(self) -> None:
        # The corpus failure is not a vector failure: nothing was attempted, so
        # the run must not report passes, let alone negative ones.
        self.assertEqual(self.replay_all_with({}, files=22, pinned=23), (0, 1))
        self.assertEqual(self.replay_all_with({}, files=0, pinned=23), (0, 1))


class TestMethodsSchemaUnavailable(unittest.TestCase):
    """An unreadable methods.json fails the run instead of crashing it.

    The method names are read at import, so a missing file, invalid JSON or a
    missing enum would raise before any check can report it. The loader reports
    through fail() and the method-dependent checks skip; these cases drive both
    halves against a fresh validate module.
    """

    def fresh_validate(self):
        module = run_vectors.load_validate_module()
        module.FAILURES.clear()
        return module

    def test_a_missing_methods_json_is_a_failure(self) -> None:
        module = self.fresh_validate()
        with mock.patch.object(
            module, "SCHEMA_DIR", pathlib.Path("/nonexistent-dir")
        ):
            self.assertIsNone(module.load_methods_schema())
        self.assertEqual(len(module.FAILURES), 1)

    def test_a_methods_json_without_the_enum_is_a_failure(self) -> None:
        module = self.fresh_validate()
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "methods.json").write_text('{"$defs":{}}')
            with mock.patch.object(module, "SCHEMA_DIR", pathlib.Path(tmp)):
                self.assertIsNone(module.load_methods_schema())
        self.assertEqual(len(module.FAILURES), 1)

    def test_the_method_dependent_checks_skip(self) -> None:
        module = self.fresh_validate()
        module.METHODS_SCHEMA = None
        module.KNOWN_METHODS = []
        reg = module.registry()
        module.check_method_map()
        hello = (
            '{"id":1,"method":"session.hello","params":{'
            '"awareness_client_id":77,"capabilities":["y-protocols/1"],'
            '"client":"probe/1","display_name":"Ada","role":"host"},'
            '"v":"selvage/1"}'
        )
        module.check_frame(reg, hello, "probe")
        self.assertEqual(module.FAILURES, [])

    def test_a_non_string_method_reports_without_raising(self) -> None:
        # `anyMethod` requires a string, so the schema error is already recorded;
        # the params lookup must then stand aside instead of raising TypeError on
        # the unhashable method and hiding the remaining frame errors.
        module = self.fresh_validate()
        reg = module.registry()
        module.check_frame(
            reg, '{"id":1,"method":[],"params":{},"v":"selvage/1"}', "probe"
        )
        self.assertTrue(
            any("session.json" in problem for problem in module.FAILURES),
            module.FAILURES,
        )


class ScriptedSocket:
    """A connection whose frames are already there, and then nothing.

    The audit waits for a frame that a healthy connection never sends, so the script raises
    the timeout it catches rather than spending the wall clock: a case that slept to fail
    would be a case that passes for the wrong reason.
    """

    def __init__(self, frames: list[object]) -> None:
        self.frames = list(frames)

    async def recv(self) -> object:
        if self.frames:
            return self.frames.pop(0)
        raise TimeoutError("no more frames")


class TestDesynchronisedQueue(unittest.IsolatedAsyncioTestCase):
    """What a failure has to be read against: how far the connection has got.

    An `expect` reads the next frame, so a transcript that omits an expectation leaves its
    connection one frame ahead and fails later on a frame from an earlier moment. These cases
    pin the two reports that make that visible — a completed transcript that names the frame
    no step reads, and a failure that names the connection and its place in the stream.
    """

    def session_with(self, **held: list[object]) -> Session:
        return Session(
            peers={
                name: Peer(name, ScriptedSocket(frames)) for name, frames in held.items()
            }
        )

    async def test_a_frame_no_step_reads_at_the_end_is_reported(self) -> None:
        session = self.session_with(guest=['{"event":"peer.joined"}'])
        held = await session.drain()
        report = describe_unread(unread_frames(held, {}))
        self.assertEqual(len(report), 1)
        self.assertIn("`guest`", report[0])
        self.assertIn("does not read", report[0])
        self.assertIn("peer.joined", report[0])

    async def test_a_connection_with_nothing_left_is_not_reported(self) -> None:
        session = self.session_with(host=[])
        held = await session.drain()
        self.assertEqual(held, {})
        self.assertEqual(describe_unread(held), [])

    async def test_a_frame_a_later_step_would_read_is_not_an_omission(self) -> None:
        # The transcript stopped short of a step that reads this frame, so the frame belongs
        # to that read and not to a missing expectation behind it.
        session = self.session_with(late=['{"event":"doc.opened"}'])
        held = await session.drain()
        self.assertEqual(unread_frames(held, {"late": 1}), {})
        self.assertEqual(len(unread_frames(held, {})), 1)

    def test_only_reading_steps_hold_a_connection_back(self) -> None:
        steps = [
            {"op": "expect", "conn": "guest"},
            {"op": "send", "conn": "guest"},
            {"op": "expectBinary", "conn": "guest"},
            {"op": "expectDoc", "conn": "guest"},
            {"op": "expect", "conn": "host"},
        ]
        self.assertEqual(pending_reads(steps, 0), {"guest": 2, "host": 1})
        self.assertEqual(pending_reads(steps, 3), {"host": 1})

    async def test_a_genuine_mismatch_still_reports_the_mismatch(self) -> None:
        peer = Peer("guest", ScriptedSocket([]))
        peer.frames = 5
        session = Session(peers={"guest": peer})
        expected = canonical(
            {
                "event": "doc.opened",
                "params": {"documents": ["late.txt"], "path": "late.txt"},
                "v": "selvage/1",
            }
        )
        actual = canonical(
            {
                "event": "peer.joined",
                "params": {"peer": {"display_name": "Cyd", "role": "guest"}},
                "v": "selvage/1",
            }
        )
        with self.assertRaises(Mismatch) as caught:
            check_text(actual, expected, Bindings())
        report = describe_failure(
            session, 28, 26, {"op": "expect", "conn": "guest"}, 4, caught.exception
        )
        self.assertIn("step 26 of 28 `expect` on `guest`", report)
        self.assertIn("reading frame 5 (4 read before it)", report)
        self.assertIn("frame does not match", report)
        self.assertIn("peer.joined", report)

    async def test_a_mismatch_with_nothing_left_is_not_padded(self) -> None:
        session = self.session_with(guest=[])
        held = await session.drain()
        report = describe_failure(
            session, 3, 1, {"op": "expect", "conn": "guest"}, 0, Mismatch("x")
        )
        self.assertEqual(failure_message(report, unread_frames(held, {})), report)

    async def test_a_silent_server_fails_the_step_within_the_bound(self) -> None:
        # The script raises the timeout at once rather than spending the wall clock:
        # what this pins is the verdict a hung server gets — a named failure, not a
        # hang — and the bound it names, which the reference replay must match.
        peer = Peer("guest", ScriptedSocket([]))
        with self.assertRaises(Mismatch) as caught:
            await peer.text('{"event":"room.created"}')
        self.assertIn("no frame within", str(caught.exception))

    def test_the_frame_bound_is_ten_seconds(self) -> None:
        self.assertEqual(run_vectors.FRAME_TIMEOUT, 10.0)
        self.assertEqual(Peer("guest", ScriptedSocket([])).timeout, 10.0)


# --- the peer layer ---------------------------------------------------------------


class TestStepVocabulary(unittest.TestCase):
    """The two halves of the tooling agree about a step name, and the op tables are pinned.

    `apply` is the case that made this necessary: it was in the validator's pass-through list
    while `run_step` raises on an op it does not know, so a step could validate here and fail
    the replay there with nothing saying which half was wrong. The peer layer multiplies that
    risk — `run_peer.py` and `validate.py` both hold a step vocabulary — so both pairs are
    compared rather than inspected.
    """

    def test_the_validator_and_the_runner_know_the_same_wire_ops(self) -> None:
        validate = validate_module()
        self.assertEqual(set(validate.WIRE_OPS), set(run_vectors.WIRE_OPS))

    def test_apply_is_a_member_and_not_a_step(self) -> None:
        validate = validate_module()
        self.assertNotIn("apply", run_vectors.WIRE_OPS)
        self.assertNotIn("apply", validate.WIRE_OPS)
        self.assertIn("apply", VECTOR_STEP_MEMBERS)

    def test_the_runner_and_the_validator_know_the_same_peer_ops(self) -> None:
        validate = validate_module()
        self.assertEqual(set(run_peer.FRAME_OPS), set(validate.PEER_FRAME_OPS))
        self.assertEqual(set(run_peer.KINDS), set(validate.PEER_OPS))

    def test_every_declared_mutation_is_implemented_and_every_guard_is_declared(self) -> None:
        # A vector is added with the mutation it must go red under (§13.11), and a mutation is
        # added with the vector it catches: this is the pin that makes both directions true, and
        # it is why a mutation with no vector behind it is a red run rather than an aspiration.
        frames = {v.get("catches") for v in peer_vectors() if v.get("kind") == "frame"}
        decisions = {v.get("catches") for v in peer_vectors() if v.get("kind") == "decision"}
        # The positive control declares none, which is the other half of the pin.
        self.assertEqual(frames - {None}, set(sealed.MUTATIONS))
        self.assertEqual(decisions, set(subject.SUBJECT_MUTATIONS))


#: The members `sendBinary` and `expectBinary` accept beside their bytes. `apply` is here and
#: not in the step table, which is the distinction that was once lost.
VECTOR_STEP_MEMBERS = frozenset({"apply"})


class TestAbsenceRule(unittest.TestCase):
    """The negative class, driven in both directions.

    A rule that only ever runs against a corpus that satisfies it is not a check: the scan has
    to be shown to catch the frame it is about. The control the corpus itself runs is the
    `selvage/1` transcripts, which carry every one of these members; these cases are the same
    claim at the smallest size, and they are what makes the rule's own function trustworthy.
    """

    def violations(self, document: dict) -> list[str]:
        validate = validate_module()
        return validate.absence_violations(document)

    def test_a_conforming_selvage_2_transcript_is_not_caught(self) -> None:
        document = {
            "selvage": "selvage/2",
            "steps": [
                {
                    "op": "expect",
                    "text": canonical(
                        {
                            "v": "selvage/2",
                            "event": "peer.joined",
                            "params": {"peer": {"display_name": "Bob", "peer_id": "p-1"}},
                        }
                    ),
                }
            ],
        }
        self.assertEqual(self.violations(document), [])

    def test_a_role_in_a_server_frame_is_caught(self) -> None:
        document = {
            "selvage": "selvage/2",
            "steps": [
                {
                    "op": "expect",
                    "text": canonical(
                        {
                            "v": "selvage/2",
                            "event": "peer.joined",
                            "params": {
                                "peer": {
                                    "display_name": "Bob",
                                    "peer_id": "p-1",
                                    "role": "guest",
                                }
                            },
                        }
                    ),
                }
            ],
        }
        self.assertTrue(self.violations(document))

    def test_a_deleted_event_is_caught(self) -> None:
        document = {
            "selvage": "selvage/2",
            "steps": [
                {
                    "op": "expect",
                    "text": canonical(
                        {
                            "v": "selvage/2",
                            "event": "doc.opened",
                            "params": {"path": "src/main.rs", "peer_id": "p-1"},
                        }
                    ),
                }
            ],
        }
        report = self.violations(document)
        self.assertTrue(any("doc.opened" in problem for problem in report), report)

    def test_a_path_in_a_binary_frame_is_caught(self) -> None:
        document = {
            "selvage": "selvage/2",
            "secret": ["src/main.rs"],
            "steps": [{"op": "expectBinary", "hex": "61 62 73 72 63 2f 6d 61 69 6e 2e 72 73"}],
        }
        self.assertTrue(self.violations(document))

    def test_the_rule_does_not_read_a_selvage_1_transcript(self) -> None:
        # The selector is the version member: the transcripts that exist today carry every one
        # of these members and are replayed by the wire layer, so the rule must not fire on
        # them. The corpus-wide control is a separate scan and is what shows the walker reads.
        document = {
            "selvage": "selvage/1",
            "steps": [{"op": "expect", "text": '{"v":"selvage/1","event":"doc.opened"}'}],
        }
        self.assertEqual(self.violations(document), [])

    def test_the_scan_finds_what_the_wire_corpus_carries(self) -> None:
        # The positive control, smaller: a walker that stopped walking finds nothing, so this
        # asserts the corpus-wide counts are not zero. `schema/validate.py` pins them exactly.
        validate = validate_module()
        self.assertGreater(sum(validate.EXPECTED_V1_MEMBERS.values()), 0)
        self.assertGreater(sum(validate.EXPECTED_V1_EVENTS.values()), 0)
        self.assertGreater(sum(validate.EXPECTED_V1_NEEDLES.values()), 0)


class TestMutationCensus(unittest.TestCase):
    """The census on a corpus built here, so its verdicts are known.

    `run_peer.py --mutation-census` is the meta-test the corpus is held to, and a meta-test that
    only ever runs green is not one. These cases drive it over two vectors: one that catches the
    mutation it declares and one that declares a mutation that does nothing to it.
    """

    def census(self, vectors: list[dict]) -> tuple[list[str], int]:
        return run_peer.mutation_census(vectors, has_subject=False)

    def test_a_vector_that_catches_its_mutation_passes_the_census(self) -> None:
        vector = self.vector("test", "no-mark")
        report, failures = self.census([vector])
        self.assertEqual(failures, 0, report)
        self.assertTrue(any("red under `no-mark`" in line for line in report), report)

    def test_a_vector_that_stays_green_under_its_mutation_fails(self) -> None:
        # `no-issued` removes an ordering this vector never reaches, so the vector must go red
        # under a mutation that does nothing to it: it is not testing the rule it names.
        vector = self.vector("test", "no-issued")
        report, failures = self.census([vector])
        self.assertEqual(failures, 1)
        self.assertTrue(any("stays green" in line for line in report), report)

    def test_a_vector_declaring_a_mutation_no_table_names_fails(self) -> None:
        vector = self.vector("test", "no-such-guard")
        report, failures = self.census([vector])
        self.assertEqual(failures, 1)
        self.assertTrue(any("no mutation table names" in line for line in report), report)

    def test_a_frame_vector_cannot_declare_a_subjects_mutation(self) -> None:
        vector = self.vector("test", "no-lease")
        report, failures = self.census([vector])
        self.assertEqual(failures, 1)
        self.assertTrue(any("removes a client's rule" in line for line in report), report)

    def test_a_vector_that_fails_without_a_mutation_fails_the_census(self) -> None:
        vector = self.vector("test", "no-mark")
        vector["steps"][-1]["reason"] = "bad_signature"
        report, failures = self.census([vector])
        self.assertEqual(failures, 1)
        self.assertTrue(any("without a mutation" in line for line in report), report)

    def vector(self, name: str, catches: str | None) -> dict:
        """A frame vector whose one refusal is a replayed counter."""
        recipe_state = {
            "sign": "host-key",
            "kind": 1,
            "counter": 1,
            "nonce": "0a" * 12,
            "payload": {
                "issued": 1,
                "listing": [],
                "peers": {
                    sealed.b64url(FIXTURE.key("guest-1").public): {
                        "peer_id": "p-1",
                        "role": "guest",
                    }
                },
            },
        }
        recipe_content = {
            "sign": "guest-1",
            "kind": 0,
            "counter": 1,
            "nonce": "0b" * 12,
            "plaintext": "00 00 01 00",
        }
        state = sealed.seal(FIXTURE, recipe_state).bytes()
        content = sealed.seal(FIXTURE, recipe_content).bytes()

        def text(raw: bytes) -> str:
            return " ".join(f"{byte:02x}" for byte in raw)

        return {
            "_file": f"{name}.json",
            "id": "999",
            "layer": "peer",
            "kind": "frame",
            "selvage": "selvage/2",
            "canonical": "SJ-C/1",
            "fixture": "fixture/keys.json",
            "catches": catches,
            "steps": [
                {"op": "seal", "frame": "state", "recipe": recipe_state, "hex": text(state)},
                {"op": "expectVerify", "frame": "state"},
                {"op": "seal", "frame": "content", "recipe": recipe_content, "hex": text(content)},
                {"op": "expectVerify", "frame": "content"},
                {"op": "expectReject", "frame": "content", "reason": "replayed_counter"},
            ],
        }


FIXTURE = sealed.Fixture(VECTOR_DIR / "fixture" / "keys.json")


class TestDecisionReason(unittest.TestCase):
    """Why a decision vector was not attempted, in both cases and in English.

    The reason is the whole of what separates `not attempted` from `passed`, so it is the one
    piece of the layer report a reader has to be able to trust: it names what is missing, and
    it has to read as a sentence when the subject is the thing that is missing and when the
    server is.
    """

    def reason(self, has_subject: bool) -> str:
        return run_peer.decision_reason({}, has_subject)

    def test_with_no_subject_it_names_both_things(self) -> None:
        self.assertEqual(
            self.reason(False),
            'a decision vector needs a subject (`--subject "my-client --drive"`) and '
            "a server that speaks `selvage/2` to seat a peer in a room",
        )

    def test_with_a_subject_it_names_the_one_thing_left(self) -> None:
        reason = self.reason(True)
        self.assertNotIn("--subject", reason)
        self.assertEqual(
            reason,
            "a decision vector needs a server that speaks `selvage/2` to seat a peer in a room",
        )


class TestLayerReport(unittest.TestCase):
    """A tool that can run one layer says what it could not run."""

    def test_the_peer_vectors_are_found_and_are_not_wire_vectors(self) -> None:
        peers = run_vectors.load_peer_vectors()
        self.assertEqual(len(peers), validate_module().EXPECTED_PEER_VECTORS)
        for peer in peers:
            self.assertIn(peer["kind"], ("frame", "decision"))

    def test_asking_for_the_peer_layer_refuses_rather_than_passing_nothing(self) -> None:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = run_vectors.main(["--layer", "peer"])
        self.assertEqual(code, 2)
        self.assertIn("run_peer.py", errors.getvalue())


# --- the subject protocol ---------------------------------------------------------


#: What the stub subject answers with. A fixed report, so a case can assert on the parse and on
#: what the runner made of it without a client.
STUB_REPORT = {
    "text": {"src/main.rs": "a\u1f600bc".replace("\u1f600", "\U0001f600")},
    "documents": ["src/main.rs"],
    "applied": [{"frame": 0, "kind": 1}],
    "dropped": [{"frame": 1, "reason": "replayed_counter"}],
    "published": 2,
    "ended": False,
    "peers": [{"peer_id": "p-1", "display_name": "Ada"}],
    "holds": {"mwSkxeRytFUs0XcaBujkAxuamFmfjb9gAG1V1aYUkEw": ["src/main.rs"]},
    "frames": 2,
}


def run_stub_subject(behaviour: str) -> int:
    """A subject, in this file, for these cases to drive.

    Running the stub from `test_runner.py` rather than writing a script keeps the test free of
    any temporary file — there is nowhere on this host to put one that is not the RAM disk or a
    read-only store path — and it keeps the protocol in one place.
    """
    for line in sys.stdin.buffer:
        try:
            command = json.loads(line)
        except json.JSONDecodeError:
            return 1
        name = command.get("cmd")
        if behaviour == "silent":
            time.sleep(60)
        if behaviour == "close":
            return 0
        if name == "quit":
            reply: object = {"ok": True}
        elif behaviour == "garbage":
            sys.stdout.write("this is not JSON\n")
            sys.stdout.flush()
            continue
        elif behaviour == "refuse":
            reply = {"ok": False, "error": "I cannot do that"}
        elif behaviour == "bad-report":
            reply = {"ok": True, "report": {"published": 1}}
        elif name == "mutate":
            reply = {"ok": True, "report": {**STUB_REPORT, "mutation": command.get("name")}}
        else:
            reply = {"ok": True, "report": STUB_REPORT}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


STUB_SUBJECT = [sys.executable, str(pathlib.Path(__file__).resolve()), "--stub-subject"]


class TestSubjectProtocol(unittest.TestCase):
    """The framing, the report's shape, and every way a subject can fail a vector."""

    def subject(self, behaviour: str = "answer", timeout: float = 5.0) -> subject.Subject:
        peer = subject.Subject(STUB_SUBJECT + [behaviour], timeout=timeout)
        peer.start()
        self.addCleanup(peer.stop)
        return peer

    def test_a_report_is_the_decision_channel_and_is_read_member_by_member(self) -> None:
        report = self.subject().report()
        self.assertEqual(report.published, 2)
        self.assertFalse(report.ended)
        self.assertEqual(report.text, {"src/main.rs": "a\U0001f600bc"})
        self.assertEqual([(d.frame, d.reason) for d in report.dropped],
                         [(1, "replayed_counter")])
        self.assertEqual(report.holds["mwSkxeRytFUs0XcaBujkAxuamFmfjb9gAG1V1aYUkEw"],
                         ["src/main.rs"])
        self.assertEqual(report.frames, 2)

    def test_the_commands_of_the_protocol_are_the_ones_the_stub_answers(self) -> None:
        peer = self.subject()
        self.assertEqual(peer.join("/session#k=x").published, 2)
        self.assertEqual(peer.insert("src/main.rs", 0, "x").published, 2)
        self.assertEqual(peer.announce("src/main.rs").published, 2)
        self.assertEqual(peer.mutate("no-lease").mutation, "no-lease")
        peer.quit()

    def test_a_mutation_no_table_names_is_refused_before_it_is_sent(self) -> None:
        peer = self.subject()
        with self.assertRaises(subject.SubjectError):
            peer.mutate("no-such-guard")
        self.assertEqual(peer.asked, 0)

    def test_a_report_missing_a_member_is_refused(self) -> None:
        with self.assertRaises(subject.SubjectError) as caught:
            self.subject("bad-report").report()
        self.assertIn("has no", str(caught.exception))

    def test_a_subject_that_never_answers_fails_with_its_deadline(self) -> None:
        # The runner does the waiting: a subject that stalls must fail the vector rather than
        # hang the run, and the failure names the bound it broke.
        with self.assertRaises(subject.SubjectError) as caught:
            self.subject("silent", timeout=0.5).report()
        self.assertIn("did not answer within", str(caught.exception))

    def test_a_subject_that_closes_its_stdout_fails_by_name(self) -> None:
        with self.assertRaises(subject.SubjectError) as caught:
            self.subject("close").report()
        self.assertIn("closed its stdout", str(caught.exception))

    def test_a_subject_that_refuses_a_command_fails_by_name(self) -> None:
        with self.assertRaises(subject.SubjectError) as caught:
            self.subject("refuse").report()
        self.assertIn("I cannot do that", str(caught.exception))

    def test_a_subject_that_answers_something_else_fails_by_name(self) -> None:
        with self.assertRaises(subject.SubjectError) as caught:
            self.subject("garbage").report()
        self.assertIn("not JSON", str(caught.exception))

    def test_a_subject_is_named_by_a_command_line_and_not_by_a_language(self) -> None:
        peer = subject.Subject.from_command('python3 my_client.py --drive')
        self.assertEqual(peer.command, ["python3", "my_client.py", "--drive"])

    def test_an_unstartable_subject_is_a_named_failure(self) -> None:
        peer = subject.Subject(["/nonexistent/selvage-subject"])
        with self.assertRaises(subject.SubjectError):
            peer.start()
        peer.stop()


if __name__ == "__main__" and "--stub-subject" in sys.argv:
    raise SystemExit(run_stub_subject(sys.argv[sys.argv.index("--stub-subject") + 1]))


if __name__ == "__main__":
    unittest.main()
