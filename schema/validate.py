#!/usr/bin/env python3
"""Validates the Selvage schemas, and the vectors against them.

Every schema in this directory is checked as a JSON Schema 2020-12 schema. Then every frame
in `spec/vectors/*.json` is checked against it: a text frame against `session.json` and
against the params schema of the method or event it names, an `expectBody` against
`meta.json`, and every expected WebSocket close against the error vocabulary.

Run it with a JSON Schema implementation available, e.g.:

    nix-shell -p python3Packages.jsonschema --run 'python3 spec/schema/validate.py'

Vector placeholders (`$room`, `$message`, ...) are strings by construction — a vector never
uses one where the schema requires a number — so they validate as ordinary strings.
"""

from __future__ import annotations

import json
import pathlib
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent
VECTOR_DIR = SCHEMA_DIR.parent / "vectors"
BASE = "https://selvageprotocol.com/schema/1/"

METHOD_PARAMS = {
    "session.hello": f"{BASE}methods.json#/$defs/sessionHelloParams",
    "doc.open": f"{BASE}methods.json#/$defs/docOpenParams",
    "doc.close": f"{BASE}methods.json#/$defs/docCloseParams",
}

EVENT_PARAMS = {
    "room.created": f"{BASE}events.json#/$defs/roomCreated",
    "room.joined": f"{BASE}events.json#/$defs/roomJoined",
    "peer.joined": f"{BASE}events.json#/$defs/peerJoined",
    "peer.left": f"{BASE}events.json#/$defs/peerLeft",
    "doc.opened": f"{BASE}events.json#/$defs/docOpened",
    "doc.closed": f"{BASE}events.json#/$defs/docClosed",
    "host.detached": f"{BASE}events.json#/$defs/hostDetached",
    "host.attached": f"{BASE}events.json#/$defs/hostAttached",
    "room.gone": f"{BASE}events.json#/$defs/roomGone",
    "session.error": f"{BASE}events.json#/$defs/sessionError",
}

CLOSE_CODES = {
    "unsupported_version": 4005,
    "room_unknown": 4001,
    "token_invalid": 4002,
    "room_gone": 4003,
    "host_present": 4004,
    "bad_message": 4000,
    "hello_required": 4000,
}

FAILURES: list[str] = []
CHECKS = 0


def fail(where: str, problem: str) -> None:
    FAILURES.append(f"{where}: {problem}")


def registry() -> Registry:
    loaded = Registry()
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        loaded = loaded.with_resource(
            schema["$id"], Resource(contents=schema, specification=DRAFT202012)
        )
    return loaded


def check(reg: Registry, ref: str, instance: object, where: str, label: str) -> None:
    global CHECKS
    CHECKS += 1
    # The ref is resolved through the registry, never by extracting a subschema: a
    # relative `#/$defs/...` inside it must keep the base URI of the file it lives in.
    validator = Draft202012Validator({"$ref": ref}, registry=reg)
    error = next(validator.iter_errors(instance), None)
    if error is None:
        return
    at = "/".join(str(part) for part in error.absolute_path) or "<root>"
    fail(where, f"{label}: {at}: {error.message}")


def check_frame(reg: Registry, text: str, where: str) -> None:
    try:
        frame = json.loads(text)
    except json.JSONDecodeError as error:
        fail(where, f"frame is not JSON: {error}")
        return

    check(reg, f"{BASE}session.json", frame, where, "session.json")

    if not isinstance(frame, dict):
        return
    if "method" in frame:
        ref = METHOD_PARAMS.get(frame["method"])
        if ref is None:
            check(reg, f"{BASE}methods.json#/$defs/anyParams", frame.get("params", {}), where,
                  f"params for unknown method {frame['method']!r}")
        else:
            check(reg, ref, frame.get("params", {}), where, f"params for {frame['method']}")
    elif "event" in frame:
        ref = EVENT_PARAMS.get(frame["event"])
        if ref is None:
            fail(where, f"event {frame['event']!r} has no schema")
        else:
            check(reg, ref, frame.get("params"), where, f"params for {frame['event']}")
    elif "result" in frame:
        check(reg, f"{BASE}methods.json#/$defs/anyResult", frame["result"], where, "result")
    elif "error" in frame:
        check(reg, f"{BASE}errors.json#/$defs/errorObject", frame["error"], where, "error")


def check_vector(reg: Registry, document: dict, name: str) -> None:
    where = f"{name} [{document.get('id')}]"

    for member in ("id", "title", "spec", "selvage", "canonical", "steps"):
        if member not in document:
            fail(where, f"missing {member!r}")
    if document.get("selvage") != "selvage/1":
        fail(where, "selvage must be `selvage/1`")
    if document.get("canonical") != "SJ-C/1":
        fail(where, "canonical must be `SJ-C/1`")

    for index, step in enumerate(document.get("steps", [])):
        at = f"{where} step {index} ({step.get('op')})"
        op = step.get("op")
        if op in {"send", "expect"}:
            if "text" not in step:
                fail(at, "no text")
            elif op == "send" and step.get("refused"):
                # A vector sends a malformed frame on purpose when it is testing the
                # refusal: the frame is *meant* not to conform.
                try:
                    json.loads(step["text"])
                except json.JSONDecodeError as error:
                    fail(at, f"refused frame is not JSON: {error}")
            else:
                check_frame(reg, step["text"], at)
        elif op == "expectBody":
            check(reg, f"{BASE}meta.json", json.loads(step["text"]), at, "meta.json")
        elif op == "expectClose":
            candidate = step.get("code")
            if candidate not in set(CLOSE_CODES.values()):
                fail(at, f"close code {candidate!r} is not one of {sorted(set(CLOSE_CODES.values()))}")
        elif op in {"expectBinary", "sendBinary"}:
            if "hex" not in step and "frame" not in step:
                fail(at, "needs hex or frame")
            if "hex" in step and not step["hex"].split():
                fail(at, "empty hex")
            if "frame" in step:
                frame = step["frame"]
                if not isinstance(frame, dict) or "message_type" not in frame:
                    fail(at, "frame needs message_type")
                elif frame["message_type"] not in (0, 1):
                    fail(at, "message_type must be 0 (sync) or 1 (awareness)")
        elif op in {"open", "close", "wait", "http", "apply", "expectDoc",
                    "expectStatus", "expectSameState"}:
            pass
        else:
            fail(at, f"unknown op {op!r}")


def main() -> int:
    reg = registry()
    print(f"schema ok      {len(list(SCHEMA_DIR.glob('*.json')))} schemas")

    vectors = sorted(VECTOR_DIR.glob("*.json"))
    for path in vectors:
        check_vector(reg, json.loads(path.read_text()), path.name)

    for problem in FAILURES:
        print(f"FAIL           {problem}")
    print(f"vectors        {len(vectors)} files, {CHECKS} frame checks")
    print(f"result         {'FAIL' if FAILURES else 'OK'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
