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
"""

from __future__ import annotations

import json
import contextlib
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import run_vectors  # noqa: E402
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
        pin = mock.Mock(EXPECTED_VECTORS=files if pinned is None else pinned)
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


if __name__ == "__main__":
    unittest.main()
