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

The exclusion `PROTOCOL.md` §5 fixes on a `display_name`, a `path` and each grant member —
no Unicode `Cc` character — is the schema's to enforce, and this file runs the shipped
schema against values that carry one, so that a dropped constraint is a red run here
rather than a machine-readable model that quietly permits what the prose forbids.

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
EXPECTED_VECTORS = 36
EXPECTED_FRAME_CHECKS = 35022
EXPECTED_ASSERTIONS = 8676

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
    "024": [],
    "025": [],
    "026": ["close:4000", "session.error:x.room_full"],
    "027": ["close:4005", "close:4005", "error:unsupported_version",
            "session.error:unsupported_version"],
    "028": ["close:4000", "close:4000", "close:4005", "error:unsupported_version",
            "session.error:bad_message", "session.error:bad_message"],
    "029": ["close:4000", "close:4000", "error:bad_params", "error:bad_params",
            "error:bad_params", "session.error:bad_params", "session.error:bad_params"],
    "030": ["close:4000", "close:4000", "session.error:bad_message",
            "session.error:bad_message", "session.error:bad_message"],
    "031": ["close:4000", "session.error:bad_message", "session.error:bad_message",
            "session.error:bad_message"],
    "032": ["close:4002", "session.error:token_invalid"],
    "033": ["error:bad_params", "error:bad_params", "error:bad_params",
            "error:bad_params"],
    "034": [],
    "035": [],
    "036": ["session.error:bad_message", "session.error:bad_message"],
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


def load_methods_schema() -> dict | None:
    """Reads `methods.json` once, reporting through `fail(...)` instead of raising.

    `KNOWN_METHODS` is read at import, so a missing file, invalid JSON or a missing
    `$defs.knownMethod.enum` would raise before any check can report it: a broken
    schema file would crash the validator instead of failing it. Callers skip
    method-dependent checks when this returns `None`; the load failure itself is
    already in `FAILURES`.
    """
    try:
        schema = json.loads((SCHEMA_DIR / "methods.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail("methods.json", f"not readable as JSON: {error}")
        return None
    defs = schema.get("$defs") if isinstance(schema, dict) else None
    known = defs.get("knownMethod") if isinstance(defs, dict) else None
    enum = known.get("enum") if isinstance(known, dict) else None
    if not isinstance(enum, list) or not all(
        isinstance(name, str) for name in enum
    ):
        fail("methods.json", "no $defs.knownMethod.enum of method names")
        return None
    return schema


METHODS_SCHEMA = load_methods_schema()


def known_methods() -> list[str]:
    """The method names `selvage/1` defines, read from the schema that defines them."""
    if METHODS_SCHEMA is None:
        return []
    return METHODS_SCHEMA["$defs"]["knownMethod"]["enum"]


KNOWN_METHODS = known_methods()


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


# The validator for each `$ref`, and the registry it was built against. Six refs cover
# every frame in the corpus, while a build walks and meta-checks its schema, so building
# one per check spends most of the run on the same few schemas. The registry is held
# beside the validator it was built with, so a registry that is replaced — `test_runner`
# builds a fresh one per case — rebuilds rather than reuses.
_VALIDATORS: dict[str, tuple[Registry, Draft202012Validator]] = {}


def validator_for(reg: Registry, ref: str) -> Draft202012Validator:
    """The validator for one `$ref` in this registry, built once and reused.

    A validator is stateless between `iter_errors` calls: it holds the registry and the
    ref, and each call builds its own scope.
    """
    cached = _VALIDATORS.get(ref)
    if cached is None or cached[0] is not reg:
        cached = _VALIDATORS[ref] = (
            reg,
            Draft202012Validator({"$ref": ref}, registry=reg),
        )
    return cached[1]


def check(reg: Registry, ref: str, instance: object, where: str, label: str) -> None:
    global CHECKS
    CHECKS += 1
    # The ref is resolved through the registry, never by extracting a subschema: a
    # relative `#/$defs/...` inside it must keep the base URI of the file it lives in.
    validator = validator_for(reg, ref)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (
            "/".join(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    for error in errors:
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

    # `session.json` intentionally permits members no schema names: unknown fields and
    # capabilities are ignored on the wire (the compatibility rule), so neither the
    # schemas nor this check may reject them — no `additionalProperties` anywhere.
    # Strictness lives in the replay's `matches` instead, which is exact in both
    # directions because a version-locked vector is checking that nothing was added.
    check(reg, f"{BASE}session.json", frame, where, "session.json")

    if not isinstance(frame, dict):
        return
    if "method" in frame:
        if METHODS_SCHEMA is None:
            return  # the load failure is already reported; no params schema to check
        method = frame["method"]
        if not isinstance(method, str):
            return  # the schema error is already recorded above; an unhashable
            # method would raise on the params lookup instead of letting the
            # remaining frame errors report
        ref = METHOD_PARAMS.get(method)
        if ref is None:
            if method in KNOWN_METHODS:
                # A method the schema names but the map does not is a map that rotted
                # behind the schema: fail closed, like an event with no schema.
                fail(where, f"method {method!r} has no params schema")
            else:
                # Any other name is legal on the wire: methods.json answers an unknown
                # method with `unknown_method` rather than refusing the frame, and 007
                # pins exactly that. Its params are checked as opaque.
                check(reg, f"{BASE}methods.json#/$defs/anyParams", frame.get("params", {}), where,
                      f"params for unknown method {method!r}")
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
                # refusal: the frame is *meant* not to conform. One whose malformation is
                # that it is not JSON at all says so, so that the bytes a parser refuses
                # are a deliberate transcript and not a vector written wrong — and so
                # that the marker cannot be left on a frame that does parse.
                try:
                    json.loads(step["text"])
                except json.JSONDecodeError as error:
                    if not step.get("unparsable"):
                        fail(at, f"refused frame is not JSON: {error}")
                else:
                    if step.get("unparsable"):
                        fail(at, "marked `unparsable`, but the frame parses")
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
            if op == "sendBinary" and "hex" not in step:
                # A send is bytes the runner must transmit, and a `frame` description
                # names what to assert about a received frame: it is not bytes the
                # runner can send. `check_frame_description` already says a description
                # the runner could not use is a check that never runs — for a send,
                # a frame-only step is exactly that.
                fail(at, "sendBinary needs hex: a frame description is not bytes the runner can send")
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


def check_method_map() -> None:
    """Fails when `METHOD_PARAMS` no longer covers every method the schema names.

    Adding a method to `methods.json` without teaching this map its params schema would
    otherwise validate that method's params against permissive `anyParams`: a
    wire-meaning change that is validation-green. Events fail closed by construction;
    this is the same rule for methods. Truly unknown names stay permissive — the wire
    answers them with `unknown_method` — so this checks the map, not the frames.
    """
    if METHODS_SCHEMA is None:
        return  # the load failure is already reported; there is no map to check
    schema = METHODS_SCHEMA
    defined = set(schema["$defs"])
    for method in KNOWN_METHODS:
        if method not in METHOD_PARAMS:
            fail(
                "methods",
                f"method {method!r} is known to the schema but METHOD_PARAMS "
                "has no params schema for it",
            )
    for method, ref in METHOD_PARAMS.items():
        if method not in KNOWN_METHODS:
            fail("methods", f"METHOD_PARAMS names {method!r}, which the schema does not know")
        else:
            fragment = ref.split("#/$defs/", 1)
            if len(fragment) != 2 or fragment[1] not in defined:
                fail(
                    "methods",
                    f"the params schema for {method!r} does not name a $def of methods.json",
                )


# The values `PROTOCOL.md` §5 refuses — a control character in a `display_name`, a `path`
# and each member of a grant's `paths` is `bad_params` — and conforming values of the same
# shape to validate beside them, so that a constraint which refuses everything fails here
# too. The `Cc` category is C0 (U+0000–U+001F), DELETE (U+007F) and C1 (U+0080–U+009F).
CONTROL_CARRYING = {
    "U+0000": "src/main\u0000.rs",
    "U+0001": "src/main\u0001.rs",
    "TAB": "src/main\t.rs",
    "LF": "src/main.rs\n",
    "U+001F": "src/main\u001f.rs",
    "DEL": "src/main\u007f.rs",
    "C1 U+0080": "src/main\u0080.rs",
    "C1 U+0085": "src/main\u0085.rs",
    "C1 U+009F": "src/main\u009f.rs",
}
CONFORMING = {
    "a plain path": "src/main.rs",
    "a space inside": "src/my main.rs",
    "non-ASCII": "src/main\u00e9.rs",
    "non-BMP": "src/main\U0001f600.rs",
}
# Where §5 holds the rule: the two values themselves, and one list, because a grant's
# `paths` and the room's open-document set are lists of the same value.
REFUSAL_SITES = (
    ("a `path`", "common.json#/$defs/documentPath", lambda text: text),
    ("a `display_name`", "common.json#/$defs/displayName", lambda text: text),
    ("a list member", "common.json#/$defs/documentList", lambda text: [text]),
)


def check_control_refusal(reg: Registry) -> int:
    """Runs the schema's own refusal of the control characters `PROTOCOL.md` §5 forbids.

    §5 makes a control-carrying `display_name`, `path` or grant member `bad_params`, and
    the machine-readable model has to refuse it too: an implementation that reads
    `common.json` and not this file must not conclude such a value is legal. Nothing else
    here sends the schema one — a refused frame in the corpus is deliberately not
    schema-checked — so without this the exclusion could be deleted from the model with
    every other check still green.
    """
    checks = 0
    for label, ref, wrap in REFUSAL_SITES:
        validator = Draft202012Validator({"$ref": f"{BASE}{ref}"}, registry=reg)
        for name, text in CONTROL_CARRYING.items():
            checks += 1
            if not list(validator.iter_errors(wrap(text))):
                fail(
                    "schema",
                    f"{label} accepts {name} ({text!r}), which `PROTOCOL.md` §5 refuses "
                    "with `bad_params`",
                )
        for name, text in CONFORMING.items():
            checks += 1
            errors = list(validator.iter_errors(wrap(text)))
            if errors:
                fail(
                    "schema",
                    f"{label} refuses the conforming {name} ({text!r}): {errors[0].message}",
                )
    return checks


# The sealed payloads `selvage/2` carries, as `CANONICAL.md` §6.1 and `schema/sealed.json`
# fix them. Nothing else here reads that file: a sealed frame is bytes and the corpus's vectors
# for it are the next layer's, so without this check the state's member set could be relaxed
# — `key` back to the `key_id` that verifies nothing, or a fourth role — with every other
# check in this suite still green.
SEALED_CONFORMING = {
    "an empty room": {"issued": 0, "listing": [], "peers": {}},
    "a listing of one path": {"issued": 1, "listing": ["src/main.rs"], "peers": {}},
    "a host and a viewer": {
        "issued": 1,
        "listing": ["README.md", "src/main.rs"],
        "peers": {
            "p-0f1e2d3c4b5a6978": {
                "key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM",
                "role": "host",
            },
            "p-8796a5b4c3d2e1f0": {
                "key": "laGjN1Xs9WCrmojozXGfP9_-1ppPlIwOcog-ef1AdIc",
                "role": "viewer",
            },
        },
    },
    "a closing": {"closing": True, "issued": 2},
}
SEALED_REFUSED = {
    "a state with no `issued`": {"listing": [], "peers": {}},
    "a state that commits an id": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"key_id": "efc57a77c8a86a55", "role": "host"}},
    },
    "a peer with no role": {"issued": 1, "listing": [], "peers": {"p-1": {"key": "GT" * 1 + "yGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM"}}},
    "a role this version has not": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM", "role": "admin"}},
    },
    "a key that is not 32 bytes": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfe", "role": "host"}},
    },
    "a key with a character base64url has not": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfe+", "role": "host"}},
    },
    "a listing that carries a control character": {
        "issued": 1,
        "listing": ["src/main\u0000.rs"],
        "peers": {},
    },
    "a listing that is not a list": {"issued": 1, "listing": "README.md", "peers": {}}
}
SEALED_STATE_CONFORMING = {
    "an empty room": {"issued": 0, "listing": [], "peers": {}},
    "a listing of one path": {"issued": 1, "listing": ["src/main.rs"], "peers": {}},
    "a host and a viewer": {
        "issued": 1,
        "listing": ["README.md", "src/main.rs"],
        "peers": {
            "p-0f1e2d3c4b5a6978": {
                "key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM",
                "role": "host",
            },
            "p-8796a5b4c3d2e1f0": {
                "key": "laGjN1Xs9WCrmojozXGfP9_-1ppPlIwOcog-ef1AdIc",
                "role": "viewer",
            },
        },
    },
    "a state carrying a member this version does not define": {
        "issued": 1,
        "listing": [],
        "peers": {
            "p-1": {
                "key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM",
                "key_id": "efc57a77c8a86a55",
                "role": "host",
            }
        },
    },
}
SEALED_STATE_REFUSED = {
    "a state with no `issued`": {"listing": [], "peers": {}},
    "a state that commits an id instead of a key": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"key_id": "efc57a77c8a86a55", "role": "host"}},
    },
    "a peer with no key": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"role": "host"}},
    },
    "a peer with no role": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM"}},
    },
    "a role this version has not": {
        "issued": 1,
        "listing": [],
        "peers": {
            "p-1": {"key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM", "role": "admin"}
        },
    },
    "a key that is not 32 bytes": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfe", "role": "host"}},
    },
    "a key with a character base64url has not": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"key": "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfe+", "role": "host"}},
    },
    "a listing that carries a control character": {
        "issued": 1,
        "listing": ["src/main\u0000.rs"],
        "peers": {},
    },
    "a listing that is not a list": {"issued": 1, "listing": "README.md", "peers": {}},
    "a closing offered as a state": {"closing": True, "issued": 2},
}
SEALED_CLOSING_CONFORMING = {"a closing": {"closing": True, "issued": 2}}
SEALED_CLOSING_REFUSED = {
    "a closing with no `issued`": {"closing": True},
    "a closing whose `closing` is false": {"closing": False, "issued": 3},
    "a room state offered as a closing": {"issued": 2, "listing": [], "peers": {}},
    "an `issued` above the JavaScript bound": {"closing": True, "issued": 9007199254740992},
}

SEALED_SITES = (
    (
        "the sealed room state",
        "sealed.json#/$defs/roomState",
        SEALED_STATE_CONFORMING,
        SEALED_STATE_REFUSED,
    ),
    (
        "the sealed closing",
        "sealed.json#/$defs/roomClosing",
        SEALED_CLOSING_CONFORMING,
        SEALED_CLOSING_REFUSED,
    ),
)


# `selvage/2`'s session-layer shapes, as `schema/session-v2.json` and `PROTOCOL.md`'s `selvage/2`
# passages fix them. A `selvage/2` `/meta` body and its `session.hello` replies are not session
# frames — `/meta` carries no `v`, and the transcripts are `selvage/1`'s until the corpus is
# re-baselined — so nothing else here reaches them, and a `roles` made required again, a `documents`
# readmitted, or a peer record's `display_name` dropped would leave every other check in this suite
# green. Both directions are checked, so a shape that refuses everything fails too. A member these
# values carry and the shape does not define is deliberate: a receiver tolerates one
# (`PROTOCOL.md` §4.1), so the model must not forbid it, and what holds a *server* to the member
# set of its version is the corpus's exact comparison rather than a schema.
SESSION_V2_KEEPALIVE = {
    "ping_interval_ms": 30000,
    "awareness_renew_ms": 15000,
    "awareness_expire_ms": 30000,
}
SESSION_V2_ADVERTISED = {
    "server": "selvaged/0.2.0",
    "wire_versions": ["selvage/2"],
    "capabilities": ["y-protocols/1", "awareness"],
    "keepalive": {**SESSION_V2_KEEPALIVE, "room_grace_ms": 30000},
}
SESSION_V2_META_CONFORMING = {
    "a server of this version": dict(SESSION_V2_ADVERTISED),
    "a body that still advertises `roles`": {
        **SESSION_V2_ADVERTISED,
        "roles": ["host", "guest"],
    },
}
SESSION_V2_META_REFUSED = {
    "a body with no `keepalive`": {
        key: value for key, value in SESSION_V2_ADVERTISED.items() if key != "keepalive"
    },
    "a body with no `server`": {
        key: value for key, value in SESSION_V2_ADVERTISED.items() if key != "server"
    },
    "a body that advertises no version at all": {**SESSION_V2_ADVERTISED, "wire_versions": []},
}

SESSION_V2_PEER_CONFORMING = {
    "a peer as this version writes one": {"peer_id": "p-3d33", "display_name": "Bob"},
    "a peer with an awareness id": {
        "peer_id": "p-3d33",
        "display_name": "Bob",
        "awareness_client_id": 42,
    },
    "a peer still carrying a role": {"peer_id": "p-3d33", "display_name": "Bob", "role": "guest"},
}
SESSION_V2_PEER_REFUSED = {
    "a peer with no `display_name`": {"peer_id": "p-3d33"},
    "a peer with no `peer_id`": {"display_name": "Bob"},
    "a peer whose `display_name` is blank": {"peer_id": "p-3d33", "display_name": "  "},
    "a peer whose `peer_id` is empty": {"peer_id": "", "display_name": "Bob"},
}

SESSION_V2_JOIN = {
    "room_id": "r-a0bca377bb4e",
    "self": {"peer_id": "p-3d33", "display_name": "Bob", "awareness_client_id": 42},
    "peers": [{"peer_id": "p-852b", "display_name": "Ada"}],
    "capabilities": ["y-protocols/1", "awareness"],
    "keepalive": SESSION_V2_KEEPALIVE,
}
SESSION_V2_JOIN_CONFORMING = {
    "a join reply": dict(SESSION_V2_JOIN),
    "a reply carrying a member this version does not define": {
        **SESSION_V2_JOIN,
        "documents": ["src/main.rs"],
    },
}
SESSION_V2_JOIN_REFUSED = {
    "a reply with no `peers`": {
        key: value for key, value in SESSION_V2_JOIN.items() if key != "peers"
    },
    "a reply whose `self` has no `display_name`": {
        **SESSION_V2_JOIN,
        "self": {"peer_id": "p-3d33"},
    },
    "a join reply carrying the token": {**SESSION_V2_JOIN, "token": "ab" * 16},
}

SESSION_V2_CREATED_CONFORMING = {"a mint reply": {**SESSION_V2_JOIN, "token": "ab" * 16}}
SESSION_V2_CREATED_REFUSED = {"a mint reply with no `token`": dict(SESSION_V2_JOIN)}

SESSION_V2_SITES = (
    (
        "a `selvage/2` `/meta`",
        "session-v2.json#/$defs/meta",
        SESSION_V2_META_CONFORMING,
        SESSION_V2_META_REFUSED,
    ),
    (
        "a `selvage/2` `PeerInfo`",
        "session-v2.json#/$defs/peer",
        SESSION_V2_PEER_CONFORMING,
        SESSION_V2_PEER_REFUSED,
    ),
    (
        "a `selvage/2` `room.joined`",
        "session-v2.json#/$defs/roomJoined",
        SESSION_V2_JOIN_CONFORMING,
        SESSION_V2_JOIN_REFUSED,
    ),
    (
        "a `selvage/2` `room.created`",
        "session-v2.json#/$defs/roomCreated",
        SESSION_V2_CREATED_CONFORMING,
        SESSION_V2_CREATED_REFUSED,
    ),
)


def check_session_v2(reg: Registry) -> int:
    """Runs the shipped `session-v2.json` against `selvage/2`'s session-layer shapes.

    Both directions, as for the sealed payloads: a shape that refuses everything this version
    writes fails beside a shape that accepts what it does not.
    """
    checks = 0
    for label, ref, conforming, refused in SESSION_V2_SITES:
        validator = Draft202012Validator({"$ref": f"{BASE}{ref}"}, registry=reg)
        for name, value in conforming.items():
            checks += 1
            errors = list(validator.iter_errors(value))
            if errors:
                fail("schema", f"{label} refuses the conforming {name}: {errors[0].message}")
        for name, value in refused.items():
            checks += 1
            if not list(validator.iter_errors(value)):
                fail(
                    "schema",
                    f"{label} accepts {name}, which `schema/session-v2.json` does not describe",
                )
    return checks


def check_sealed_payloads(reg: Registry) -> int:
    """Runs the shipped `sealed.json` against what `CANONICAL.md` §6.1 says it describes.

    Nothing else in this suite reads that file — a sealed frame is bytes rather than JSON, and
    the vectors for one are the corpus layer's — so a member set relaxed here (the state's `key`
    back to the `key_id` that verifies nothing, or a fourth role) would leave every other check
    green. Both directions are checked, so a constraint that refuses everything fails too.
    """
    checks = 0
    for label, ref, conforming, refused in SEALED_SITES:
        validator = Draft202012Validator({"$ref": f"{BASE}{ref}"}, registry=reg)
        for name, value in conforming.items():
            checks += 1
            errors = list(validator.iter_errors(value))
            if errors:
                fail("schema", f"{label} refuses the conforming {name}: {errors[0].message}")
        for name, value in refused.items():
            checks += 1
            if not list(validator.iter_errors(value)):
                fail(
                    "schema",
                    f"{label} accepts {name}, which `CANONICAL.md` §6.1 does not describe",
                )
    return checks


def main() -> int:
    """Checks every schema and every claim the corpus makes, and reports what it found."""
    global CHECKS
    reg = registry()
    check_method_map()
    refusals = check_control_refusal(reg)
    sealed = check_sealed_payloads(reg)
    session_v2 = check_session_v2(reg)
    print(
        f"schema ok      {len(list(SCHEMA_DIR.glob('*.json')))} schemas, {refusals} values "
        "checked against the control-character refusal"
    )
    print(f"sealed         {sealed} values checked against the sealed payloads of selvage/2")
    print(f"session v2     {session_v2} values checked against selvage/2's session layer")

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
