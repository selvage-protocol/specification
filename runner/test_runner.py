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
from dataclasses import dataclass
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
        self.assertEqual(set(run_peer.DECISION_OPS), set(validate.PEER_DECISION_OPS))
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


class TestTheTwoLinkRules(unittest.TestCase):
    """The two rules a link is decided about **before a socket**, driven against a stub client.

    `PROTOCOL.md` §13.11 says these two are the decision layer's own subject, and no client in
    this repository drives them, so what is checked here is the **vector**: each passes against a
    subject that holds the rule, goes red under the one mutation it declares it catches, stays
    green under the *other* link guard — a guard with a vector of its own and not this one's —
    and fails a subject that refuses every link. That is the property the census asserts of a
    declared mutation, and it is the one a runner with no client cannot show.
    """

    def link(self, vector_id: str, behaviour: str = "link-rules",
             mutation: str | None = None) -> "run_peer.Outcome":
        for vector in run_peer.load_vectors():
            if vector.get("id") == vector_id:
                break
        else:
            self.fail(f"no peer vector has the id {vector_id!r}")
        return run_peer.attempt(
            vector,
            {},
            frozenset(),
            run_peer.Driver(
                command=" ".join(STUB_SUBJECT + [behaviour]), mutation=mutation
            ),
        )

    def test_the_partial_fragment_vector_holds_and_catches_its_guard_alone(self) -> None:
        clean = self.link("157")
        self.assertIsNone(clean.failure)
        self.assertEqual(clean.assertions, 3, "two refusals and the leg that must join")
        red = self.link("157", mutation="accept-partial-fragment")
        self.assertIn("(`expectRefusal`)", red.failure or "", red.failure)
        other = self.link("157", mutation="fall-back-to-version-1")
        self.assertIsNone(other.failure, other.failure)

    def test_the_version_vector_holds_and_catches_its_guard_alone(self) -> None:
        clean = self.link("158")
        self.assertIsNone(clean.failure)
        self.assertEqual(clean.assertions, 2, "the refusal and the pinned control")
        red = self.link("158", mutation="fall-back-to-version-1")
        self.assertIn("(`expectRefusal`)", red.failure or "", red.failure)
        other = self.link("158", mutation="accept-partial-fragment")
        self.assertIsNone(other.failure, other.failure)

    def test_a_subject_that_refuses_every_link_fails_the_control_leg(self) -> None:
        # Both vectors carry the leg that must not be refused — 157's complete fragment and
        # 158's pin to `selvage/1` — and a subject whose refusals are worded well enough to
        # answer the refusal legs is caught by them and not by anything else.
        for vector_id in ("157", "158"):
            outcome = self.link(vector_id, behaviour="link-rules-refuse-all")
            self.assertIn("(`expectSubject`)", outcome.failure or "", outcome.failure)

    def test_the_mutation_each_vector_declares_is_the_one_the_census_removes(self) -> None:
        # The name in the vector is the name the stub removes, and the census sends the first as
        # the second: a vector whose `catches` named nothing the subject knows cannot be red, so
        # this is what makes the two cases above evidence rather than a coincidence.
        for vector_id, declared in (("157", "accept-partial-fragment"),
                                    ("158", "fall-back-to-version-1")):
            for vector in run_peer.load_vectors():
                if vector.get("id") == vector_id:
                    self.assertEqual(vector.get("catches"), declared)
                    self.assertIn(declared, subject.SUBJECT_MUTATIONS)
                    break
            else:
                self.fail(f"no peer vector has the id {vector_id!r}")


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


class TestUsablePath(unittest.TestCase):
    """`sealed.usable_path`'s two rules and the encoding failure a lone surrogate makes."""

    def test_a_path_over_the_bound_is_unusable(self) -> None:
        self.assertTrue(sealed.usable_path("b" * sealed.MAX_PATH_BYTES))
        self.assertFalse(sealed.usable_path("a" * (sealed.MAX_PATH_BYTES + 1)))

    def test_a_blank_or_control_carrying_path_is_unusable(self) -> None:
        self.assertFalse(sealed.usable_path(""))
        self.assertFalse(sealed.usable_path("a\tb"))

    def test_a_lone_surrogate_is_unusable_rather_than_raising(self) -> None:
        # JSON can carry a lone surrogate and UTF-8 cannot encode it: §13.3 drops the path.
        self.assertFalse(sealed.usable_path("\ud800"))


class TestMutationCensus(unittest.TestCase):
    """The census on a corpus built here, so its verdicts are known.

    `run_peer.py --mutation-census` is the meta-test the corpus is held to, and a meta-test that
    only ever runs green is not one. These cases drive it over two vectors: one that catches the
    mutation it declares and one that declares a mutation that does nothing to it.
    """

    def census(self, vectors: list[dict]) -> tuple[list[str], int]:
        return run_peer.mutation_census(vectors, run_peer.Driver())

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
    """Why a decision vector was not attempted, in English and naming what is missing.

    The reason is the whole of what separates `not attempted` from `passed`, so it is the one
    piece of the layer report a reader has to be able to trust. The runner plays the relay, so
    the one thing a decision vector still needs is a subject.
    """

    def test_with_no_subject_it_names_the_one_thing_missing(self) -> None:
        reason = run_peer.decision_reason(False)
        self.assertEqual(
            reason, 'a decision vector needs a subject (`--subject "my-client --drive"`)'
        )
        self.assertNotIn("server", reason, "the runner is the relay and no server is missing")

    def test_with_a_subject_there_is_no_reason_left(self) -> None:
        self.assertEqual(run_peer.decision_reason(True), "")


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


#: What the stub subject answers with. A fixed report, plus the two members a delivered frame
#: moves, so a case can assert on the parse and on what the runner made of it without a client.
STUB_REPORT = {
    "text": {"src/main.rs": "a\u1f600bc".replace("\u1f600", "\U0001f600")},
    "documents": ["src/main.rs"],
    "applied": [{"frame": 0, "kind": 1}],
    "dropped": [{"frame": 1, "reason": "replayed_counter"}],
    "published": 2,
    "handshake": 1,
    "ended": False,
    "peers": [{"peer_id": "p-1", "display_name": "Ada"}],
    "holds": {"mwSkxeRytFUs0XcaBujkAxuamFmfjb9gAG1V1aYUkEw": ["src/main.rs"]},
    "listing": ["README.md"],
    "frames": 2,
}


def run_stub_subject(behaviour: str) -> int:
    """A subject, in this file, for these cases to drive.

    Running the stub from `test_runner.py` rather than writing a script keeps the test free of
    any temporary file — there is nowhere on this host to put one that is not the RAM disk or a
    read-only store path — and it keeps the protocol in one place. `decisions` is the one
    behaviour that behaves like a client rather than like a fixture: it counts what it is handed
    and reads each frame's `kind` out of the envelope, which is enough to be a subject for a
    runner case without being a client.
    """
    applied: list[dict] = []
    handed = 0
    mutated: str | None = None
    link_state: dict = {"seated": False, "frames": 0, "mutation": None}
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
        if behaviour == "garbage":
            sys.stdout.write("this is not JSON\n")
            sys.stdout.flush()
            continue
        if name == "quit":
            reply: object = {"ok": True}
        elif behaviour in ("link-rules", "link-rules-refuse-all"):
            reply = link_reply(behaviour, command, link_state)
        elif behaviour == "no-mutate" and name == "mutate":
            # A subject that implements no mutations: it must be a census failure, not a red run.
            reply = {"ok": False, "error": "I have no guards to remove"}
        elif behaviour == "refuse":
            reply = {"ok": False, "error": "I cannot do that"}
        elif behaviour == "bad-report":
            reply = {"ok": True, "report": {"published": 1}}
        else:
            if name == "deliver":
                handed += 1
                applied.append({"frame": handed - 1, "kind": kind_of(command.get("frame"))})
            if name == "mutate":
                mutated = command.get("name")
            report = stub_report(behaviour, applied, handed, mutated, name)
            reply = {"ok": True, "report": report}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def link_reply(behaviour: str, command: dict, state: dict) -> dict:
    """What the link-rule stub answers one command with.

    It is a client for the two decisions a link carries **before a socket**, which is the layer
    the rest of the corpus has no subject for: `PROTOCOL.md` §5.1 refuses a fragment that names
    one of its two keys and not the other, naming the missing one, and §2/§10 refuse a server
    whose `/meta` names no version at major 2 rather than fall back to one that is seated.

    `state["mutation"]` is the guard this run removed, and it arrives before the `join`: a guard
    on the link has to be gone before the link is read. Each of the two names makes this the
    wrong implementation the census is about.
    `link-rules-refuse-all` is the other way of passing a corpus of refusals: it refuses every
    link with words that answer the first leg of a refusal vector, and the control leg beside it
    is what catches that.
    """
    name = command.get("cmd")
    if name == "quit":
        return {"ok": True}
    if name == "mutate":
        state["mutation"] = command.get("name")
        return {"ok": True, "report": link_report(state)}
    if name == "join":
        refusal = link_refusal(behaviour, command, state["mutation"])
        state["seated"] = refusal is None
        state["frames"] = 0
        if refusal is not None:
            return {"ok": False, "error": refusal}
        return {"ok": True, "report": link_report(state)}
    if name == "deliver":
        state["frames"] += 1
    return {"ok": True, "report": link_report(state)}


def link_refusal(behaviour: str, command: dict, mutation: str | None) -> str | None:
    """The words this stub refuses a link with, or `None` when it joins it."""
    invite = command.get("invite") if isinstance(command.get("invite"), str) else ""
    base = invite.split("?", 1)[0].split("#", 1)[0]
    fall_back = (
        f"{base} advertises selvage/1: this client needs selvage/2 and does not fall back "
        "to an earlier version"
    )
    if behaviour == "link-rules-refuse-all":
        # A superset of both refusal vectors' own words on purpose: this double passes every
        # refusal leg and can only be caught by the control leg beside it, which is the leg that
        # exists to say "refusing everything is not answering".
        return (
            "the invite carries no room key (`k`) and no host key (`h`): this client needs "
            "selvage/2 and refuses this link"
        )
    fragment = invite.split("#", 1)[1] if "#" in invite else ""
    names = {pair.split("=", 1)[0] for pair in fragment.split("&") if pair}
    if mutation != "accept-partial-fragment":
        if "k" in names and "h" not in names:
            return "the invite carries no host key (`h`)"
        if "h" in names and "k" not in names:
            return "the invite carries no room key (`k`)"
    meta = command.get("meta") if isinstance(command.get("meta"), dict) else {}
    versions = meta.get("wire_versions") if isinstance(meta.get("wire_versions"), list) else []
    offered = [one for one in versions if isinstance(one, str)]
    if (
        command.get("pin") is None
        and offered
        and not any(major_2(one) for one in offered)
        and mutation != "fall-back-to-version-1"
    ):
        return fall_back
    return None


def major_2(version: str) -> bool:
    """True for a version spelling at major 2, read the way `PROTOCOL.md` §10's grammar reads it.

    The minor binds only while the major is 0, so what a `/meta` body has to name for the
    encrypted wire is any spelling whose major is 2 (`selvage/2`, `selvage/2.1`).
    """
    parts = version.split("/", 1)
    return len(parts) == 2 and parts[1].split(".", 1)[0] == "2"


def link_report(state: dict) -> dict:
    """A seated subject's report, or the empty one a client with no session holds."""
    empty = {
        "text": {},
        "documents": [],
        "applied": [],
        "dropped": [],
        "published": 0,
        "handshake": 0,
        "ended": False,
        "holds": {},
        "listing": [],
        "frames": 0,
    }
    if not state["seated"]:
        return empty
    return {**empty, "frames": state["frames"], "mutation": state["mutation"]}


def stub_report(
    behaviour: str, applied: list[dict], handed: int, mutated: str | None, command: object
) -> dict:
    """The report a stub answers a command with.

    `STUB_REPORT` for the protocol's own cases, and a minimal one for `decisions`, `wrong-count`
    and `no-mutate`, which is what a decision vector's members are asserted against. The
    `decisions` double answers a mutation by moving `published`, which is what a removed guard
    does to a real client's decision and what the census has to notice; `wrong-count` is the
    shape a subject that counts differently from the runner has, and `no-mutate` one that refuses
    to remove a guard at all.
    """
    if behaviour not in ("decisions", "wrong-count", "no-mutate"):
        report = {**STUB_REPORT, "applied": list(applied), "frames": handed}
        return {**report, "mutation": mutated} if command == "mutate" else report
    return {
        "text": {},
        "documents": [],
        "applied": list(applied),
        "dropped": [],
        "published": 2 if mutated else 1,
        "handshake": 0,
        "listing": [],
        "holds": {},
        "frames": handed + 5 if behaviour == "wrong-count" else handed,
        "ended": False,
        "mutation": mutated,
    }


def kind_of(frame: object) -> int:
    """The `kind` a sealed frame carries, read out of the envelope's ninth byte.

    A `key_id` is eight bytes and a `kind` of 0 to 4 is one `varUint` byte, which is all a stub
    has to read to name what it was handed (`CANONICAL.md` §6.1).
    """
    if not isinstance(frame, str) or len(frame) < 18:
        return 0
    try:
        return int(frame[16:18], 16)
    except ValueError:
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
        self.assertEqual(report.handshake, 1, "§7's frames are counted apart")
        self.assertFalse(report.ended)
        self.assertEqual(report.text, {"src/main.rs": "a\U0001f600bc"})
        self.assertEqual([(d.frame, d.reason) for d in report.dropped],
                         [(1, "replayed_counter")])
        self.assertEqual(report.holds["mwSkxeRytFUs0XcaBujkAxuamFmfjb9gAG1V1aYUkEw"],
                         ["src/main.rs"])
        self.assertEqual(report.listing, ["README.md"])
        self.assertEqual(report.frames, 0, "nothing has been handed over yet")

    def test_the_commands_of_a_join_are_the_members_the_runner_resolves(self) -> None:
        # The one test seam of the layer, and the reason it is a member: a decision vector's
        # delivered state commits a fixture key, so the runner hands the keypair over.
        peer = self.subject("decisions")
        report = peer.join(
            "/session?room=R&token=t#k=k&h=h",
            offline=True,
            keepalive={"awareness_renew_ms": 300, "awareness_expire_ms": 900},
            seat="p-subject",
            roster=["p-host"],
            session_key="5f" * 32,
        )
        self.assertEqual(report.mutation, None)
        self.assertEqual(report.frames, 0)

    def test_a_delivered_frame_is_counted_and_decided_about(self) -> None:
        peer = self.subject("decisions")
        peer.join("/session?room=R&token=t#k=k&h=h", offline=True)
        # A `kind = 1` frame: the stub reads that byte, which is all a double has to do.
        report = peer.deliver(bytes.fromhex("00" * 8 + "01" + "00" * 40))
        self.assertEqual(report.frames, 1)
        self.assertEqual(report.applied, [{"frame": 0, "kind": 1}])
        report = peer.deliver(bytes.fromhex("00" * 8 + "03" + "00" * 40))
        self.assertEqual([entry["kind"] for entry in report.applied], [1, 3])

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


@dataclass(frozen=True)
class AliasedKey(sealed.Key):
    """A key that reports another key's id: a `key_id` collision, constructed.

    §6.1 derives the id from SHA-256, so two real keys that collide cost about 2^64
    candidates and no test can have one. What a collision *means* is a reader rule, though —
    every key the id names is tried — and a collision is the only shape that rule is read in,
    so the test builds the collision the derivation cannot: the same id, two keys, one of
    which signed the frame.
    """

    alias: str = ""

    @property
    def id(self) -> bytes:
        return bytes.fromhex(self.alias)

    @property
    def hex_id(self) -> str:
        return self.alias


def content_frame(signer: sealed.Key, shared_id: bytes) -> bytes:
    """A `kind = 0` frame carrying a SyncStep2, sealed under `shared_id` and signed by `signer`.

    `00 01 01 00` is message type 0 (sync), subtype 1 (SyncStep2) and a one-byte payload: enough
    for §13.5's rule, which is about the message's type and not about what it carries.
    """
    return sealed_frame(signer, shared_id, 0, bytes.fromhex("00010100"))


def shared_id_frame(signer: sealed.Key, shared_id: bytes) -> bytes:
    """One holds frame whose envelope carries `shared_id` and whose signature is `signer`'s.

    A collision cannot be produced by §6.1's derivation, so the frame is built the way that
    section builds one rather than by `seal`: the AEAD's associated data contains the id the
    envelope carries, so a frame attributed to a shared id is sealed *under* that id and then
    signed by one of the keys that id names.
    """
    plaintext = json.dumps({"holds": ["src/main.rs"]}, **sealed.CANONICAL).encode("utf-8")
    return sealed_frame(signer, shared_id, 3, plaintext)


def sealed_frame(signer: sealed.Key, shared_id: bytes, kind: int, plaintext: bytes) -> bytes:
    """One frame sealed under `shared_id` and signed by `signer`, which is a collision's shape."""
    epoch, counter = 0, 1
    nonce = bytes.fromhex("031e2f3a4b5c6d7e8f90a1b2")
    aad = sealed.associated_data(FIXTURE.room_id, kind, epoch, shared_id)
    ciphertext = sealed.AESGCM(FIXTURE.frame_key()).encrypt(nonce, plaintext, aad)
    envelope = sealed.Envelope(shared_id, kind, epoch, counter, nonce, ciphertext)
    envelope.signature = signer.sign(sealed.signing_input(aad, envelope))
    return envelope.bytes()


class TestKeyIdCollision(unittest.TestCase):
    """§6.1 resolves an 8-byte `key_id` by verifying, so a collision costs a verification.

    The state's `peers` is keyed by the public key and one key appears once, so a collision
    can only come from two keys whose first eight SHA-256 bytes agree. A reader that takes
    the first key the id names reports `bad_signature` for a frame the second key signed and
    a conforming reader applies: two readers, one state, opposite verdicts.
    """

    def colliding_reader(self) -> sealed.Reader:
        first = FIXTURE.key("guest-1")
        second = FIXTURE.key("guest-2")
        aliased = AliasedKey(
            name="guest-2", public=second.public, private=second.private, alias=first.hex_id
        )
        reader = sealed.Reader(FIXTURE)
        # The state's names are the public keys' spellings and `by_key_id` reads them in that
        # order, so `guest-1` is the first candidate and the frame is signed by the second.
        reader.committed = {
            sealed.b64url(first.public): sealed.Peer(first, "guest", "p-1"),
            sealed.b64url(second.public): sealed.Peer(aliased, "guest", "p-2"),
        }
        return reader

    def test_a_frame_signed_by_the_second_key_of_a_collision_verifies(self) -> None:
        reader = self.colliding_reader()
        verdict = reader.read(
            shared_id_frame(FIXTURE.key("guest-2"), FIXTURE.key("guest-1").id)
        )
        self.assertTrue(verdict.ok, f"refused {verdict.reason}")
        self.assertEqual(verdict.sender, FIXTURE.key("guest-1").hex_id)
        self.assertEqual(verdict.payload["holds"], ["src/main.rs"])
        # The set belongs to the key that verified, which is the one whose signature did.
        self.assertEqual(reader.holds[verdict.sender], ["src/main.rs"])

    def test_the_role_reads_from_the_key_that_verified_and_not_from_the_shared_id(self) -> None:
        # §13.4: the role is the state's, and it is a *key's*. An id cannot tell two colliding
        # keys apart, so a `viewer` whose id collides with a `guest`'s would have its content
        # applied under the guest's role if the reader asked the id.
        reader = sealed.Reader(FIXTURE)
        guest = FIXTURE.key("guest-1")
        viewer = FIXTURE.key("mallory-1")
        aliased = AliasedKey(
            name="mallory-1", public=viewer.public, private=None, alias=guest.hex_id
        )
        reader.committed = {
            sealed.b64url(guest.public): sealed.Peer(guest, "guest", "p-1"),
            sealed.b64url(viewer.public): sealed.Peer(aliased, "viewer", "p-2"),
        }
        # The id names both entries, and `role_of` reads whichever key comes first in the
        # state's own order — here the viewer's, because its spelling sorts before the guest's.
        # Which of the two an id-read returns is an accident of spelling; which key verified is
        # not.
        self.assertEqual(len(reader.by_key_id(guest.hex_id)), 2)
        self.assertEqual(reader.role_of_key(guest), "guest")
        self.assertEqual(reader.role_of_key(viewer), "viewer")

    def test_a_viewers_content_is_refused_under_a_colliding_id(self) -> None:
        reader = sealed.Reader(FIXTURE)
        guest = FIXTURE.key("guest-1")
        viewer = FIXTURE.key("mallory-1")
        aliased = AliasedKey(
            name="mallory-1", public=viewer.public, private=None, alias=guest.hex_id
        )
        reader.committed = {
            sealed.b64url(guest.public): sealed.Peer(guest, "guest", "p-1"),
            sealed.b64url(viewer.public): sealed.Peer(aliased, "viewer", "p-2"),
        }
        # A SyncStep2, the plaintext a `kind = 0` content frame carries: it needs no CRDT
        # because §13.5's rule is read from the message's own type.
        verdict = reader.read(content_frame(viewer, guest.id))
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "unauthorised_content")

    def test_a_frame_no_key_of_a_collision_signed_is_still_a_bad_signature(self) -> None:
        verdict = self.colliding_reader().read(
            shared_id_frame(FIXTURE.key("mallory-1"), FIXTURE.key("guest-1").id)
        )
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "bad_signature")


class TestVaruintBound(unittest.TestCase):
    """A `varUint` past 64 bits is not a `varUint` (`CANONICAL.md` §6.1, `PROTOCOL.md` §7).

    Python's integers widen, so a byte at shift 63 carrying a bit above bit 0 spells a value
    the frozen layout has no field for — and a reader that widens it holds a counter no other
    reader of the same bytes has. The overlong *spelling* of a value inside 64 bits (`80 00`
    for `0`) is a different question, still open (`NOTES.md` §B.38), and is read here as
    written.
    """

    def test_a_byte_at_shift_63_may_set_only_bit_0(self) -> None:
        with self.assertRaises(sealed.SealedError):
            sealed.read_varuint(b"\x80" * 9 + b"\x02", 0)
        with self.assertRaises(sealed.SealedError):
            sealed.read_varuint(b"\x80" * 9 + b"\x7f", 0)
        # The largest value a 64-bit `varUint` spells is ten bytes, and the tenth sets bit 63.
        self.assertEqual(sealed.read_varuint(b"\xff" * 9 + b"\x01", 0)[0], (1 << 64) - 1)

    def test_the_overlong_spelling_of_a_value_inside_64_bits_is_read(self) -> None:
        self.assertEqual(sealed.read_varuint(b"\x80\x00", 0), (0, 2))
        self.assertEqual(sealed.read_varuint(b"\x81\x00", 0), (1, 2))

    def test_a_frame_whose_counter_is_past_64_bits_is_a_bad_envelope(self) -> None:
        # The disagreement the fix removes, on one frame: Python widened the counter into
        # 2**64 and applied the frame, while the frozen layout's `varUint` cannot spell it and
        # the Rust reader refuses the layout at step 1.
        key = FIXTURE.key("guest-2")
        envelope = sealed.seal(
            FIXTURE,
            {
                "sign": "guest-2",
                "kind": 3,
                "counter": 1,
                "nonce": "031e2f3a4b5c6d7e8f90a1b2",
                "payload": {"holds": ["src/main.rs"]},
            },
        )
        envelope.counter = 1 << 64
        envelope.signature = key.sign(
            sealed.signing_input(
                sealed.associated_data(
                    FIXTURE.room_id, envelope.kind, envelope.epoch, envelope.key_id
                ),
                envelope,
            )
        )
        raw = envelope.bytes()
        # The frame is otherwise authentic: the counter is the one field past its bound, and
        # the signature and the AEAD cover it as written.
        self.assertEqual(raw[10:20], b"\x80" * 9 + b"\x02")
        second = FIXTURE.key("guest-2")
        reader = sealed.Reader(FIXTURE)
        reader.committed = {
            sealed.b64url(second.public): sealed.Peer(second, "guest", "p-2")
        }
        verdict = reader.read(raw)
        self.assertFalse(verdict.ok, "the widened counter was applied")
        self.assertEqual(verdict.reason, "bad_envelope")


class TestDecisionLayer(unittest.TestCase):
    """The decision layer's driver, against a scripted subject.

    `run_peer.py --subject` runs against a real client; these cases run against the stub in this
    file, so what is tested is the runner's own code — the invite it builds, the bytes it seals,
    the way it reads a report and polls a predicate to a deadline — and not a client's decisions.
    """

    def vector(self, steps: list[dict], catches: str | None = None) -> dict:
        return {
            "_file": "test-decision.json",
            "id": "999",
            "title": "a vector built here, whose subject is in this file",
            "layer": "peer",
            "kind": "decision",
            "selvage": "selvage/2",
            "canonical": "SJ-C/1",
            "fixture": "fixture/keys.json",
            "catches": catches,
            "scenario": {
                "keepalive": {
                    "awareness_expire_ms": 900,
                    "awareness_renew_ms": 300,
                    "ping_interval_ms": 30000,
                    "room_grace_ms": 30000,
                }
            },
            "steps": steps,
        }

    def start_step(self) -> dict:
        return {
            "op": "start",
            "conn": "peer",
            "key": "guest-1",
            "invite": "/session?room=$room&token=$token#k=$room_key&h=$host_key",
        }

    def state_step(self, seats: list[tuple[str, str]]) -> dict:
        """A delivered room state, sealed from a recipe the way a vector carries one.

        `seats` is `(key name, peer id)` pairs, so two peers is two keys and not one key
        labelled twice: the state's `peers` is keyed by the public key and a key appears once.
        """
        recipe = {
            "sign": "host-key",
            "kind": 1,
            "counter": 1,
            "nonce": "0a" * 12,
            "payload": {
                "issued": 1,
                "listing": ["README.md"],
                "peers": {
                    sealed.b64url(FIXTURE.key(name).public): {
                        "peer_id": seat,
                        "role": "guest",
                    }
                    for name, seat in seats
                },
            },
        }
        return self.delivered("state", recipe)

    def delivered(self, name: str, recipe: dict) -> dict:
        raw = sealed.seal(FIXTURE, recipe).bytes()
        return {
            "op": "deliver",
            "conn": "peer",
            "frame": name,
            "recipe": recipe,
            "hex": " ".join(f"{byte:02x}" for byte in raw),
        }

    def expect(self, **members: object) -> dict:
        return {"op": "expectSubject", "conn": "peer", **members}

    def command(self, behaviour: str = "decisions") -> str:
        return " ".join(STUB_SUBJECT + [behaviour])

    def run_vector(
        self,
        steps: list[dict],
        catches: str | None = None,
        mutation: str | None = None,
        behaviour: str = "decisions",
    ) -> int:
        run = run_peer.DecisionRun(
            vector=self.vector(steps, catches),
            fixture=FIXTURE,
            command=self.command(behaviour),
            timeout=5.0,
            mutation=mutation,
        )
        return run.drive()

    def test_a_vector_the_subject_satisfies_holds(self) -> None:
        steps = [
            self.start_step(),
            self.state_step([("guest-1", "p-self")]),
            self.expect(applied=[{"frame": 0, "kind": 1}], dropped=[], published=1,
                        ended=False),
        ]
        self.assertEqual(self.run_vector(steps), 1)

    def test_a_member_the_report_does_not_carry_is_named_with_both_values(self) -> None:
        steps = [
            self.start_step(),
            self.state_step([("guest-1", "p-self")]),
            self.expect(applied=[{"frame": 0, "kind": 3}]),
        ]
        with self.assertRaises(run_peer.PeerError) as caught:
            self.run_vector(steps)
        message = str(caught.exception)
        self.assertIn("step 2 (`expectSubject`)", message)
        self.assertIn("`applied`", message)
        self.assertIn('"kind": 3', message, "what the vector claims")
        self.assertIn('"kind": 1', message, "and what the subject reported")

    def test_a_poll_runs_to_its_deadline_and_fails_with_the_last_report(self) -> None:
        steps = [self.start_step(), self.expect(published=4, within_ms=150)]
        started = time.monotonic()
        with self.assertRaises(run_peer.PeerError) as caught:
            self.run_vector(steps)
        self.assertGreaterEqual(time.monotonic() - started, 0.15)
        self.assertIn("`published` is 4 in the vector and 1 in the report", str(caught.exception))

    def test_within_ms_is_optional_and_a_bare_expectation_is_read_once(self) -> None:
        steps = [self.start_step(), self.expect(published=4)]
        with self.assertRaises(run_peer.PeerError):
            self.run_vector(steps)

    def test_frozen_holds_the_members_still_over_the_window_it_is_given(self) -> None:
        steps = [
            self.start_step(),
            self.expect(frozen=["published"], within_ms=120),
        ]
        self.assertEqual(self.run_vector(steps), 1)

    def test_frozen_without_a_window_is_refused_rather_than_slept_over(self) -> None:
        steps = [self.start_step(), self.expect(frozen=["published"])]
        with self.assertRaises(run_peer.PeerError) as caught:
            self.run_vector(steps)
        self.assertIn("`frozen` needs", str(caught.exception))

    def test_the_delivered_bytes_are_the_recipe_sealed_and_a_drift_is_a_red_run(self) -> None:
        step = self.state_step([("guest-1", "p-self")])
        step["hex"] = " ".join(["00"] * len(bytes.fromhex(step["hex"].replace(" ", ""))))
        with self.assertRaises(run_peer.PeerError) as caught:
            self.run_vector([self.start_step(), step])
        self.assertIn("other bytes than the vector carries", str(caught.exception))

    def test_a_subject_that_counts_other_frames_than_it_was_handed_fails(self) -> None:
        steps = [self.start_step(), self.state_step([("guest-1", "p-self")])]
        with self.assertRaises(run_peer.PeerError) as caught:
            self.run_vector(steps, behaviour="wrong-count")
        self.assertIn("counts 6 frames received", str(caught.exception))

    def test_a_step_no_decision_vector_defines_is_refused(self) -> None:
        steps = [self.start_step(), {"op": "seal", "conn": "peer"}]
        with self.assertRaises(run_peer.PeerError) as caught:
            self.run_vector(steps)
        self.assertIn("not a step of a decision vector", str(caught.exception))

    def test_a_scenario_naming_something_nothing_reads_is_refused(self) -> None:
        vector = self.vector([self.start_step()])
        vector["scenario"]["relay_sleeps"] = True
        with self.assertRaises(run_peer.PeerError) as caught:
            run_peer.scenario_of(vector)
        self.assertIn("relay_sleeps", str(caught.exception))

    def test_the_invite_substitutes_the_longest_name_first(self) -> None:
        # `$room` is a prefix of `$room_key`: substituting the shorter one first leaves the room
        # id inside a key's value, and the fragment then spells no key at all.
        invite = run_peer.invite_of(FIXTURE, self.start_step())
        self.assertIn(f"room={FIXTURE.room_id}", invite)
        self.assertIn(sealed.b64url(FIXTURE.room_key), invite)
        self.assertIn(sealed.b64url(FIXTURE.host.public), invite)
        self.assertNotIn(f"{FIXTURE.room_id}_key", invite)

    def test_a_template_naming_a_value_nothing_substitutes_is_refused(self) -> None:
        step = self.start_step()
        step["invite"] = "/session?room=$room&token=$nobody"
        with self.assertRaises(run_peer.PeerError):
            run_peer.invite_of(FIXTURE, step)

    def test_the_roster_is_the_seats_the_vectors_own_states_label(self) -> None:
        vector = self.vector([self.state_step([("guest-1", "p-self"), ("mallory-1", "p-host")])])
        self.assertEqual(run_peer.roster_of(vector), ["p-self", "p-host"])

    def test_the_run_hands_the_subject_the_keypair_the_vector_names(self) -> None:
        seed = run_peer.session_key_of(FIXTURE, self.start_step(), "x")
        self.assertEqual(seed, FIXTURE.key("guest-1").private.hex())
        with self.assertRaises(run_peer.PeerError) as caught:
            run_peer.session_key_of(FIXTURE, {"key": "nobody"}, "x")
        self.assertIn("which the fixture does not have", str(caught.exception))

    def test_holds_are_read_under_the_names_a_vector_writes(self) -> None:
        guest = FIXTURE.key("guest-1")
        other = FIXTURE.key("guest-2")
        held = {
            guest.hex_id: ["src/main.rs"],           # by the key id a subject may report
            sealed.b64url(other.public): ["x"],      # and by the state's own spelling
            "nobody-at-all": [],
        }
        report = subject.Report.from_json({**STUB_REPORT, "holds": held})
        names = run_peer.holds_of(FIXTURE, report)
        self.assertEqual(names["guest-1"], ["src/main.rs"])
        self.assertEqual(names["guest-2"], ["x"])
        self.assertEqual(names["nobody-at-all"], [])

    def test_a_key_the_report_does_not_name_holds_nothing(self) -> None:
        # §13.7's expiry is a set becoming empty, and a client that drops the key entirely
        # reports nothing about it: the two are one assertion.
        step = self.expect(holds={"guest-2": []})
        report = subject.Report.from_json({**STUB_REPORT, "holds": {}})
        self.assertEqual(run_peer.unmet(step, report, FIXTURE), [])
        step = self.expect(holds={"guest-2": ["src/main.rs"]})
        self.assertIn("holds[guest-2]", " ".join(run_peer.unmet(step, report, FIXTURE)))

    def test_a_decision_vector_is_not_attempted_without_a_subject(self) -> None:
        outcome = run_peer.attempt(
            self.vector([self.start_step()]), {}, frozenset(), run_peer.Driver()
        )
        self.assertFalse(outcome.attempted)
        self.assertIn("--subject", outcome.not_attempted)

    def test_a_decision_vector_with_a_subject_is_attempted(self) -> None:
        steps = [self.start_step(), self.expect(published=1)]
        driver = run_peer.Driver(command=self.command(), timeout=5.0)
        outcome = run_peer.attempt(self.vector(steps), {}, frozenset(), driver)
        self.assertEqual(outcome.failure, None, outcome.failure)
        self.assertEqual(outcome.assertions, 1)

    def test_the_census_wants_a_decision_vector_red_under_the_guard_it_declares(self) -> None:
        steps = [self.start_step(), self.expect(published=1)]
        driver = run_peer.Driver(command=self.command(), timeout=5.0)
        report, failures = run_peer.mutation_census(
            [self.vector(steps, catches="announce-once")], driver
        )
        self.assertEqual(failures, 0, report)
        self.assertTrue(any("red under `announce-once`" in line for line in report), report)

    def test_a_decision_vector_declaring_a_receivers_mutation_fails_the_census(self) -> None:
        driver = run_peer.Driver(command=self.command(), timeout=5.0)
        report, failures = run_peer.mutation_census(
            [self.vector([self.start_step()], catches="no-mark")], driver
        )
        self.assertEqual(failures, 1)
        self.assertTrue(any("removes a receiver's rule" in line for line in report), report)

    def test_a_typo_in_a_frozen_member_is_a_named_failure_and_not_a_traceback(self) -> None:
        steps = [self.start_step(), self.expect(frozen=["publishd"], within_ms=50)]
        with self.assertRaises(run_peer.PeerError) as caught:
            self.run_vector(steps)
        self.assertIn("`publishd` is not a report member", str(caught.exception))

    def test_a_typo_in_an_at_least_member_is_a_named_failure(self) -> None:
        steps = [self.start_step(), self.expect(at_least={"publishd": 1})]
        with self.assertRaises(run_peer.PeerError) as caught:
            self.run_vector(steps)
        self.assertIn("`publishd` is not a report member", str(caught.exception))

    def test_the_clean_run_of_a_census_is_clean_whatever_the_caller_passed(self) -> None:
        # `--mutation-census --mutation X` must still run each vector's positive half without a
        # mutation: the census is about each vector's own declared guard.
        steps = [self.start_step(), self.expect(published=1)]
        driver = run_peer.Driver(command=self.command(), timeout=5.0, mutation="announce-once")
        report, failures = run_peer.mutation_census(
            [self.vector(steps, catches="announce-once")], driver
        )
        self.assertEqual(failures, 0, report)
        self.assertFalse(any("without a mutation" in line for line in report), report)
        self.assertTrue(any("red under `announce-once`" in line for line in report), report)

    def test_a_subject_that_will_not_remove_a_guard_is_not_a_red_run(self) -> None:
        # Any failure of the mutated run used to count as the vector catching its guard, so a
        # subject that cannot remove one passed the census without testing anything.
        steps = [self.start_step(), self.expect(published=1)]
        driver = run_peer.Driver(command=self.command("no-mutate"), timeout=5.0)
        report, failures = run_peer.mutation_census(
            [self.vector(steps, catches="announce-once")], driver
        )
        self.assertEqual(failures, 1)
        self.assertTrue(
            any("before any expectation" in line for line in report), report
        )

    def test_each_mutation_goes_only_to_the_layer_whose_table_names_it(self) -> None:
        # Each table names its own guards and neither names the other's: handing `no-lease` to a
        # receiver fails every frame vector, and handing `no-verify` to a subject fails every
        # decision vector.
        self.assertEqual(run_peer.main(["--mutation", "no-lease", "--vector", "101"]), 0)
        with mock.patch.object(run_peer, "DecisionRun") as run:
            run.return_value.drive.return_value = 0
            run_peer.main(["--subject", self.command(), "--mutation", "no-verify", "--vector", "151"])
            self.assertIsNone(run.call_args.kwargs["mutation"], "a receiver's guard")
            run_peer.main(
                ["--subject", self.command(), "--mutation", "announce-once", "--vector", "151"]
            )
            self.assertEqual(run.call_args.kwargs["mutation"], "announce-once")

    def test_a_subject_that_cannot_start_is_a_named_failure(self) -> None:
        vector = self.vector([self.start_step()])
        driver = run_peer.Driver(command="/nonexistent/selvage-subject", timeout=1.0)
        outcome = run_peer.attempt(vector, {}, frozenset(), driver)
        self.assertIn("could not be started", outcome.failure)


if __name__ == "__main__" and "--stub-subject" in sys.argv:
    raise SystemExit(run_stub_subject(sys.argv[sys.argv.index("--stub-subject") + 1]))


if __name__ == "__main__":
    unittest.main()
