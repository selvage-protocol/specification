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


if __name__ == "__main__":
    unittest.main()
