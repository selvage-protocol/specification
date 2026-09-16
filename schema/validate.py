#!/usr/bin/env python3
"""Validates the Selvage schemas, and the vectors against them.

Every schema in this directory is checked as a JSON Schema 2020-12 schema. Then every frame
in `vectors/*.json` is checked against it: a text frame against `session.json` and
against the params schema of the method or event it names, an `expectBody` against
`meta.json`, an awareness `frame` description against `awareness.json`, and every expected
WebSocket close against the error vocabulary.

An `expect` or `expectBody` frame is additionally checked for the canonical byte form of
`CANONICAL.md` §2: a vector claims the reference server produces exactly those bytes, so
its own bytes have to be the ones it claims. That is the half of the byte agreement the
replay enforces against a running server, and this checks it without one.

The count of vectors, of frame checks and of assertion steps is pinned, because a corpus
that shrinks silently reads exactly like a corpus that passes: a deleted assertion changes
the numbers, and the numbers are a check.

The asserted error and close codes are pinned per vector for the same reason, one level
deeper: the counts catch deletion, not substitution. Swapping one vocabulary code for another
(`unknown_method` for `bad_params`) parses, validates and keeps every count, so without this
census a silently redefined assertion is a green run. Only the closed vocabularies are pinned —
response `error.code`, `session.error` `params.code`, and `expectClose` codes — because only a
substitution inside a closed vocabulary is both schema-green and meaning-red. Free-form params,
member order and prose are not pinned here; they would freeze legitimate evolution, and the live
replay in the reference server's suite is what checks them against a running server.

Run it with a JSON Schema implementation available, from the repository root:

    pip install jsonschema referencing
    python3 schema/validate.py

or, without pip: `nix-shell -p python3Packages.jsonschema --run 'python3 schema/validate.py'`.

`SELVAGE_VECTORS` replays another directory of transcripts, the way
`runner/run_vectors.py` does; the counts are this repository's, so a directory that does
not hold them fails rather than quietly checking less.

Vector placeholders (`$room`, `$message`, ...) are strings by construction — a vector never
uses one where the schema requires a number — so they validate as ordinary strings.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent
VECTOR_DIR = pathlib.Path(
    os.environ.get("SELVAGE_VECTORS", SCHEMA_DIR.parent / "vectors")
)
BASE = "https://selvageprotocol.com/schema/1/"

# What the corpus is expected to hold. Adding a vector, or an assertion inside one, is a
# deliberate edit, and these three numbers are what makes the opposite edit — a silent
# deletion — a red run instead of a smaller number in a line of output. Update them in the
# same commit that changes the corpus.
EXPECTED_VECTORS = 23
EXPECTED_FRAME_CHECKS = 806
EXPECTED_ASSERTIONS = 192

# The error and close codes each vector asserts, in sorted order. A substitution inside a
# closed vocabulary is schema-valid and count-identical, so this census is what makes one a
# red run instead of a quiet redefinition. `error:` is a response `error.code`,
# `session.error:` a `session.error` event's `params.code`, `close:` an `expectClose` code.
# Like the three counts above, this is updated in the same commit that changes what the
# corpus asserts: it cannot false-positive on a legal change, because the pin and the
# corpus move together.
EXPECTED_CODES = {
    "001": [],
    "002": [],
    "003": [],
    "004": [],
    "005": ["close:4005", "close:4005", "close:4005", "error:unsupported_version",
            "session.error:unsupported_version", "session.error:unsupported_version"],
    "006": ["error:bad_params", "error:bad_params"],
    "007": ["error:unknown_method", "error:unknown_method"],
    "008": ["close:4000", "close:4000", "session.error:bad_message",
            "session.error:hello_required"],
    "009": [],
    "010": [],
    "011": [],
    "012": ["close:4001", "close:4003", "session.error:room_unknown"],
    "013": ["close:4002", "close:4002", "session.error:token_invalid",
            "session.error:token_invalid"],
    "014": ["close:4004", "session.error:host_present"],
    "015": ["error:already_seated"],
    "016": ["close:4000", "session.error:bad_message", "session.error:bad_message"],
    "017": ["close:4000", "close:4000", "session.error:bad_message",
            "session.error:bad_params"],
    "018": [],
    "019": ["close:4000", "session.error:bad_params"],
    "020": ["error:bad_params"],
    "021": [],
    "022": [],
    "023": ["error:bad_params", "error:bad_params", "error:bad_params",
            "error:bad_params", "error:bad_params"],
}

# A step that reads or asserts something. Every other step only produces input for one, so
# a vector made of them alone can pass while claiming nothing.
ASSERTION_OPS = {
    "expect",
    "expectBinary",
    "expectClose",
    "expectStatus",
    "expectBody",
    "expectDoc",
    "expectSameState",
}

METHOD_PARAMS = {
    "session.hello": f"{BASE}methods.json#/$defs/sessionHelloParams",
    "session.rename": f"{BASE}methods.json#/$defs/renameParams",
    "doc.open": f"{BASE}methods.json#/$defs/docOpenParams",
    "doc.close": f"{BASE}methods.json#/$defs/docCloseParams",
    "doc.grant": f"{BASE}methods.json#/$defs/grantParams",
}

EVENT_PARAMS = {
    "room.created": f"{BASE}events.json#/$defs/roomCreated",
    "room.joined": f"{BASE}events.json#/$defs/roomJoined",
    "peer.joined": f"{BASE}events.json#/$defs/peerJoined",
    "peer.left": f"{BASE}events.json#/$defs/peerLeft",
    "peer.renamed": f"{BASE}events.json#/$defs/peerRenamed",
    "doc.opened": f"{BASE}events.json#/$defs/docOpened",
    "doc.closed": f"{BASE}events.json#/$defs/docClosed",
    "doc.granted": f"{BASE}events.json#/$defs/docGranted",
    "host.detached": f"{BASE}events.json#/$defs/hostDetached",
    "host.attached": f"{BASE}events.json#/$defs/hostAttached",
    "room.gone": f"{BASE}events.json#/$defs/roomGone",
    "session.error": f"{BASE}events.json#/$defs/sessionError",
}

# The number forms `CANONICAL.md` §2.4 admits, checked from the digits the parser saw
# rather than from the value it produced: `1.0` and `-0` are the values `1` and `0`, so only
# the source form can tell them apart.
PLAIN_INTEGER = re.compile(r"0|[1-9][0-9]*")
TWO_HEX_DIGITS = re.compile(r"[0-9a-fA-F]{2}")

FAILURES: list[str] = []
CHECKS = 0


def fail(where: str, problem: str) -> None:
    FAILURES.append(f"{where}: {problem}")


def close_codes() -> list[int]:
    """The close codes `selvage/1` uses, read from the vocabulary that defines them."""
    schema = json.loads((SCHEMA_DIR / "errors.json").read_text())
    return schema["$defs"]["closeCode"]["enum"]


CLOSE_CODES = close_codes()


def registry() -> Registry:
    loaded = Registry()
    identifiers: dict[str, str] = {}
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        try:
            schema = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            fail(path.name, f"not JSON: {error}")
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as error:  # jsonschema's SchemaError and its subclasses
            fail(path.name, f"not a JSON Schema 2020-12 document: {error}")
            continue
        identifier = schema.get("$id")
        if not isinstance(identifier, str):
            fail(path.name, "no $id")
            continue
        if identifier in identifiers:
            fail(path.name, f"shares its $id {identifier} with {identifiers[identifier]}")
            continue
        identifiers[identifier] = path.name
        loaded = loaded.with_resource(
            identifier, Resource(contents=schema, specification=DRAFT202012)
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


def plain_integer(digits: str) -> int:
    if not PLAIN_INTEGER.fullmatch(digits):
        raise ValueError(
            f"`{digits}` is not a plain non-negative decimal (CANONICAL.md §2.4)"
        )
    return int(digits)


def no_fraction(digits: str) -> int:
    raise ValueError(f"`{digits}` has a fraction or an exponent (CANONICAL.md §2.4)")


def check_canonical(text: str, where: str, label: str) -> None:
    """Checks that a frame is in the canonical byte form of `CANONICAL.md` §2.

    The parsed frame is written again the way SJ-C writes one — ascending member names, no
    whitespace, controls escaped as §2.3 says — and compared with the text the vector
    carries. A vector's own bytes are a claim about the server's bytes, so they have to be
    the bytes it claims.
    """
    global CHECKS
    CHECKS += 1
    try:
        frame = json.loads(text, parse_int=plain_integer, parse_float=no_fraction)
    except (json.JSONDecodeError, ValueError) as error:
        fail(where, f"{label}: not canonical: {error}")
        return
    canonical = json.dumps(
        frame, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    if canonical != text:
        fail(
            where,
            f"{label}: not the canonical bytes of CANONICAL.md §2:\n"
            f"  vector: {text}\n  form:   {canonical}",
        )


def parse_or_none(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


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


def check_frame_description(reg: Registry, frame: object, where: str) -> None:
    """Checks a binary `frame` description — the runner's own vocabulary, not the wire's.

    A description is what a vector asserts about a binary frame it cannot name in bytes:
    a sync frame by its `sync_type`, or an awareness frame by the states it carries. A
    description the runner could not use is a check that never runs.
    """
    if not isinstance(frame, dict):
        fail(where, "frame must be an object")
        return
    message_type = frame.get("message_type")
    if message_type not in (0, 1):
        fail(where, "frame needs message_type 0 (sync) or 1 (awareness)")
        return
    if message_type == 0:
        # The runner checks `sync_type` only when it is present; a description without one
        # still says the frame is a sync frame, and nothing about which kind.
        if "sync_type" in frame and frame["sync_type"] not in (0, 1, 2):
            fail(where, "sync_type must be 0 (SyncStep1), 1 (SyncStep2) or 2 (Update)")
        return
    awareness = frame.get("awareness")
    if not isinstance(awareness, dict):
        fail(where, "an awareness frame needs an awareness description")
        return
    clients = awareness.get("clients")
    if not isinstance(clients, list) or not clients:
        fail(where, "an awareness description needs a non-empty clients list")
    elif any(
        not isinstance(client, int) or isinstance(client, bool) or client < 0
        for client in clients
    ):
        fail(where, "clients holds awareness client ids")
    clock = awareness.get("clock")
    if not isinstance(clock, int) or isinstance(clock, bool) or clock < 0:
        fail(where, "an awareness description needs a clock")
    if "state" not in awareness:
        fail(where, "an awareness description needs the state it publishes")
    else:
        check(
            reg,
            f"{BASE}awareness.json#/$defs/state",
            awareness["state"],
            where,
            "awareness state",
        )


def asserted_codes(step: dict) -> list[str]:
    """The closed-vocabulary codes one step asserts.

    A response `error.code`, a `session.error` event's `params.code`, or an `expectClose`
    code. These are collected, not checked: `check_codes` compares them against the pinned
    census, which is what makes a substitution inside a closed vocabulary a red run.
    """
    op = step.get("op")
    if op == "expectClose" and isinstance(step.get("code"), int):
        return [f"close:{step['code']}"]
    if op != "expect" or not isinstance(step.get("text"), str):
        return []
    frame = parse_or_none(step["text"])
    if not isinstance(frame, dict):
        return []
    codes = []
    error = frame.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        codes.append(f"error:{error['code']}")
    if frame.get("event") == "session.error":
        params = frame.get("params")
        if isinstance(params, dict) and isinstance(params.get("code"), str):
            codes.append(f"session.error:{params['code']}")
    return codes


def check_vector(reg: Registry, document: object, name: str) -> tuple[int, list[str]]:
    """Checks one vector, returning its assertion steps and its asserted codes."""
    if not isinstance(document, dict):
        fail(name, "a vector is a JSON object")
        return 0, []
    where = f"{name} [{document.get('id')}]"

    for member in ("id", "title", "spec", "selvage", "canonical", "steps"):
        if member not in document:
            fail(where, f"missing {member!r}")
    if document.get("selvage") != "selvage/1":
        fail(where, "selvage must be `selvage/1`")
    if document.get("canonical") != "SJ-C/1":
        fail(where, "canonical must be `SJ-C/1`")

    steps = document.get("steps")
    if not isinstance(steps, list):
        fail(where, "steps must be a list")
        return 0, []

    assertions = 0
    codes: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            fail(f"{where} step {index}", "a step is a JSON object")
            continue
        at = f"{where} step {index} ({step.get('op')})"
        op = step.get("op")
        if op in ASSERTION_OPS:
            assertions += 1
        codes.extend(asserted_codes(step))
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
                if op == "expect":
                    check_canonical(step["text"], at, "expect frame")
                    frame = parse_or_none(step["text"])
                    if isinstance(frame, dict):
                        # A server writes its own version, never a tolerated spelling of
                        # it (CANONICAL.md §2.5), so the vector has to claim that one.
                        check(
                            reg,
                            f"{BASE}negotiation.json#/$defs/canonicalWireVersion",
                            frame.get("v"),
                            at,
                            "v",
                        )
        elif op == "expectBody":
            try:
                body = json.loads(step["text"])
            except json.JSONDecodeError as error:
                fail(at, f"body is not JSON: {error}")
                continue
            check(reg, f"{BASE}meta.json", body, at, "meta.json")
            check_canonical(step["text"], at, "expectBody")
        elif op == "expectClose":
            candidate = step.get("code")
            if candidate not in CLOSE_CODES:
                fail(at, f"close code {candidate!r} is not one of {CLOSE_CODES}")
        elif op in {"expectBinary", "sendBinary"}:
            if "hex" not in step and "frame" not in step:
                fail(at, "needs hex or frame")
            if "hex" in step:
                text = step["hex"]
                if not isinstance(text, str) or not text.split():
                    fail(at, "empty hex")
                else:
                    wrong = [
                        byte for byte in text.split() if not TWO_HEX_DIGITS.fullmatch(byte)
                    ]
                    if wrong:
                        fail(at, f"hex is two hex digits per byte: {wrong}")
            if "frame" in step:
                check_frame_description(reg, step["frame"], at)
        elif op in {"open", "close", "wait", "http", "apply", "expectDoc",
                    "expectStatus", "expectSameState"}:
            pass
        else:
            fail(at, f"unknown op {op!r}")

    if not assertions:
        fail(where, "asserts nothing: no step reads or asserts a frame")
    return assertions, sorted(codes)


def check_counts(vectors: int, assertions: int) -> None:
    """Fails when the corpus is not the one the pinned counts describe."""
    global CHECKS
    if vectors != EXPECTED_VECTORS:
        fail(
            "vectors",
            f"{vectors} files, and this suite pins {EXPECTED_VECTORS}: adding or "
            "removing a transcript is a deliberate edit",
        )
    if CHECKS != EXPECTED_FRAME_CHECKS:
        fail(
            "vectors",
            f"{CHECKS} frame checks, and this suite pins {EXPECTED_FRAME_CHECKS}: an "
            "assertion was added or removed",
        )
    if assertions != EXPECTED_ASSERTIONS:
        fail(
            "vectors",
            f"{assertions} assertion steps, and this suite pins {EXPECTED_ASSERTIONS}: "
            "an assertion was added or removed",
        )


def check_codes(collected: dict[str, list[str]]) -> None:
    """Fails when a vector asserts codes the pinned census does not describe."""
    for vid in sorted(set(collected) | set(EXPECTED_CODES)):
        want = EXPECTED_CODES.get(vid)
        have = collected.get(vid)
        if want is None:
            fail(
                "vectors",
                f"vector {vid!r} asserts {have} and this suite pins no census for it: "
                "a new transcript is a deliberate edit, and so is its entry here",
            )
        elif have is None:
            fail(
                "vectors",
                f"vector {vid!r} is in the pinned census as {want} but the corpus "
                "holds no such vector: removing a transcript is a deliberate edit",
            )
        elif have != want:
            fail(
                "vectors",
                f"vector {vid!r} asserts {have}, and this suite pins {want}: an "
                "asserted code was added, removed or changed",
            )


def main() -> int:
    global CHECKS
    reg = registry()
    print(f"schema ok      {len(list(SCHEMA_DIR.glob('*.json')))} schemas")

    vectors = sorted(VECTOR_DIR.glob("*.json"))
    assertions = 0
    collected: dict[str, list[str]] = {}
    for path in vectors:
        try:
            document = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            fail(path.name, f"not JSON: {error}")
            continue
        count, codes = check_vector(reg, document, path.name)
        assertions += count
        vid = document.get("id") if isinstance(document, dict) else None
        collected[vid if isinstance(vid, str) else path.name] = codes

    check_counts(len(vectors), assertions)
    check_codes(collected)

    for problem in FAILURES:
        print(f"FAIL           {problem}")
    print(
        f"vectors        {len(vectors)} files, {CHECKS} frame checks, "
        f"{assertions} assertion steps"
    )
    print(f"result         {'FAIL' if FAILURES else 'OK'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
