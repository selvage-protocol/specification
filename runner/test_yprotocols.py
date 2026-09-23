#!/usr/bin/env python3
"""Checks the binary decoder without a server: the code the replay half leans on.

CI cannot build a `selvaged`, so `run_vectors.py --schema-only` exercises none of the
y-protocols decoder. These two checks do, straight from the vectors: the frames vector
009 applies rebuild the text it then asserts, and every `frame` description in vector
010 matches the bytes the peer sent.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import yprotocols  # noqa: E402

VECTOR_DIR = pathlib.Path(__file__).resolve().parent.parent / "vectors"


def load(vector_id: str) -> dict:
    for path in sorted(VECTOR_DIR.glob("*.json")):
        vector = json.loads(path.read_text())
        if vector["id"] == vector_id:
            return vector
    raise AssertionError(f"no vector {vector_id}")


def hex_bytes(text: str) -> bytes:
    return bytes(int(byte, 16) for byte in text.split())


class TestReplica(unittest.TestCase):
    def test_vector_009_rebuilds_the_text_it_asserts(self) -> None:
        vector = load("009")
        replica = yprotocols.Replica()
        for step in vector["steps"]:
            if (
                step["op"] in ("sendBinary", "expectBinary")
                and step.get("apply")
                and step["conn"] == "guest"
            ):
                replica.apply(hex_bytes(step["hex"]))

        self.assertEqual(replica.text_at("src/main.rs"), "!a😀bXc")
        self.assertEqual(replica.state(), ((1, 6), (2, 1)))

    def replica_for(self, conn: str) -> yprotocols.Replica:
        vector = load("009")
        replica = yprotocols.Replica()
        for step in vector["steps"]:
            if (
                step["op"] in ("sendBinary", "expectBinary")
                and step.get("apply")
                and step["conn"] == conn
            ):
                replica.apply(hex_bytes(step["hex"]))
        return replica

    def test_both_roles_rebuild_the_same_text(self) -> None:
        # The guest and the host apply the same three payloads from opposite sides of
        # each exchange; both replicas must converge on the asserted text and state.
        # `expectSameState` pins this on the wire, this pins it in the decoder's reader.
        host = self.replica_for("host")
        guest = self.replica_for("guest")
        self.assertEqual(host.text_at("src/main.rs"), "!a😀bXc")
        self.assertEqual(host.state(), ((1, 6), (2, 1)))
        self.assertEqual(host.text_at("src/main.rs"), guest.text_at("src/main.rs"))
        self.assertEqual(host.state(), guest.state())


class TestUpdateItems(unittest.TestCase):
    """Every apply-payload of 009 decodes to the items the Replica integrates.

    The text tests pin the outcome; these pin the reading — client, clock, length,
    kind and linkage — so a decoder that misplaces an origin or miscounts a UTF-16
    length still fails.
    """

    def decoded(self) -> list[tuple[str, int, list[tuple]]]:
        vector = load("009")
        out = []
        for step in vector["steps"]:
            if step.get("apply"):
                message = yprotocols.decode_message(hex_bytes(step["hex"]))
                self.assertIsInstance(message, yprotocols.SyncMessage)
                out.append(
                    (
                        step["conn"],
                        message.subtype,
                        [
                            (
                                item.client,
                                item.clock,
                                item.length,
                                item.kind,
                                item.text,
                                item.origin,
                                item.right,
                                item.parent,
                            )
                            for item in yprotocols.decode_update(message.payload)
                        ],
                    )
                )
        return out

    def test_both_roles_apply_the_same_item_streams(self) -> None:
        decoded = self.decoded()
        self.assertEqual(len(decoded), 6, "009 applies three payloads on each side")
        host = [entry for entry in decoded if entry[0] == "host"]
        guest = [entry for entry in decoded if entry[0] == "guest"]
        self.assertEqual([entry[1:] for entry in host], [entry[1:] for entry in guest])

    def test_the_payloads_decode_to_their_items(self) -> None:
        seen = {(subtype, tuple(items)) for _, subtype, items in self.decoded()}
        self.assertIn(
            (1, ((1, 0, 5, "String", "a😀bc", None, None, "src/main.rs"),)),
            seen,
        )
        self.assertIn(
            (2, ((1, 5, 1, "String", "X", (1, 3), (1, 4), None),)),
            seen,
        )
        self.assertIn(
            (2, ((2, 0, 1, "String", "!", None, (1, 0), None),)),
            seen,
        )

    def test_an_unknown_content_ref_is_a_decode_error(self) -> None:
        # One struct, client 1, clock 0, origin and right present so no parent is read,
        # then content ref 7, which this reader does not know.
        with self.assertRaises(yprotocols.DecodeError):
            yprotocols.decode_update(bytes([1, 1, 1, 0, 0xC7, 1, 0, 1, 0]))

    def test_gc_structs_are_skipped(self) -> None:
        # A GC run of length 3, then the string it leaves the clock after: only the
        # string is an item, starting where the run ended.
        items = yprotocols.decode_update(
            bytes([1, 2, 1, 0, 0x00, 0x03, 0x04, 0x01, 0x01]) + b"p" + bytes([0x02]) + b"hi"
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(
            (item.client, item.clock, item.length, item.kind, item.text, item.parent),
            (1, 3, 2, "String", "hi", "p"),
        )


class TestAwareness(unittest.TestCase):
    def test_vector_010_descriptions_match_the_sent_bytes(self) -> None:
        vector = load("010")
        import run_vectors

        checked = 0
        sent: str | None = None
        for step in vector["steps"]:
            if step["op"] == "sendBinary":
                sent = step["hex"]
            if step["op"] == "expectBinary" and "frame" in step:
                self.assertIsNotNone(sent, "a frame description before any send")
                run_vectors.check_frame_spec(step["frame"], hex_bytes(sent))
                checked += 1
        self.assertEqual(checked, 2, "vector 010 describes two frames")


class TestCarriesContent(unittest.TestCase):
    """The whole-stream content scan, over the message types `yrs` defines."""

    def test_a_leading_request_does_not_hide_the_update_behind_it(self) -> None:
        # SyncStep1 (type 0, sub-type 0, one-byte empty state vector), then an update.
        self.assertTrue(yprotocols.carries_content(bytes([0, 0, 1, 0, 0, 2, 0])))

    def test_a_lone_request_is_not_content(self) -> None:
        self.assertFalse(yprotocols.carries_content(bytes([0, 0, 1, 0])))

    def test_an_auth_message_does_not_hide_the_update_behind_it(self) -> None:
        # Auth is a status varint (granted, `02 01`), not a length-prefixed buffer: a walk
        # that reads it as one loses alignment and misses the update.
        self.assertTrue(yprotocols.carries_content(bytes([2, 1, 0, 2, 0])))

    def test_a_truncated_message_returns_what_it_saw(self) -> None:
        self.assertTrue(yprotocols.carries_content(bytes([0, 2, 0, 0, 0])))
        self.assertFalse(yprotocols.carries_content(bytes([0, 0])))

if __name__ == "__main__":
    unittest.main()
