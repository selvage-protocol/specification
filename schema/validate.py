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

A schema in this directory cannot forbid a *member*: tolerance is the rule (`CANONICAL.md` §3), so
what the `selvage/2` files close is a *value*. Three of them are run here in both directions, because
a constraint that refuses everything fails beside one that accepts what the version does not write:
the fault vocabulary of the version's session layer (`session-v2.json`), the two sealed payloads a
host signs, and the reasons a receiver reports a refused sealed frame in (`sealed.json`).

**The peer layer.** `vectors/peer/*.json` is the second corpus: `selvage/2`'s *client* rules, whose
subject is a client implementation rather than the server, and whose bytes are sealed frames
(`CANONICAL.md` §6.1). Each peer vector declares a `"kind"`: a **frame** vector hands
`runner/run_peer.py` recipes, and it seals, verifies and refuses without a client at all; a
**decision** vector is about what a real client did with a frame it received and needs a subject —
a client named by `--subject`, which the runner plays the relay for; with none named, `run_peer.py`
reports those as **not attempted**, with the reason, because a vector that did not run must not read
like one that passed.
This half is checked here for the things that need no key: the step vocabulary, each recipe's shape,
the refusal vocabulary a vector asserts, the mutation each vector declares it catches, and the
absence rules below. It imports no crypto and knows no fixture value — the sealed bytes are
`run_peer.py`'s, and this file only says which checks do not need them.

**The absence scan.** No server-authored frame carries a path, a role or a byte of content in
`selvage/2`, and that is checkable with no key at all. Three legs of it are here: the shipped
`selvage/2` session shapes are scanned for the members the version deletes, every peer vector's
sealed frames are scanned for the strings its own plaintext names, and the scan is run over the
`selvage/1` corpus as its own positive control — a scan that read nothing reports zero, and the
counts it finds there are pinned so that a walker which stopped walking is a red run.

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
# deliberate edit, and these numbers are what makes the opposite edit — a silent deletion — a red
# run instead of a smaller number in a line of output. Update them in the same commit that changes
# the corpus. The two layers are counted apart on purpose: they are committed and replayed by
# different tools, and one number would let one layer's loss be paid by the other's gain.
EXPECTED_WIRE_VECTORS = 36
EXPECTED_PEER_VECTORS = 25
EXPECTED_FRAME_CHECKS = 35022
EXPECTED_ASSERTIONS = 8676
# The peer layer's own counts. `PEER_CHECKS` is one per peer step plus one per recipe, and
# `PEER_ASSERTIONS` counts the assertion steps of the **frame** vectors, which is what
# `runner/run_peer.py` runs without a client; a decision vector's `expectSubject` steps are checked
# here and are not in this number, because they are asserted against a *subject* — a client named by
# `--subject` — and are counted in that run's own summary rather than in a corpus-wide pin.
EXPECTED_PEER_CHECKS = 210
EXPECTED_PEER_ASSERTIONS = 74

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

# The refusal reasons each peer vector asserts, in sorted order, and the mutation it must go red
# under. The same argument as `EXPECTED_CODES`, one level out: a reason substituted inside
# §6.1's closed vocabulary (`replayed_counter` for `stale_issued`) is schema-valid and
# count-identical, so without `EXPECTED_REFUSALS` it is a green run that asserts something else.
# `EXPECTED_MUTATIONS` is the only pin in this corpus that is a statement about what the corpus
# *catches* rather than what it contains: `None` is a vector that must stay green under every
# mutation there is, because the alternative way to pass a corpus of refusals is to refuse
# everything. `runner/run_peer.py --mutation-census` is what drives it, and a decision vector's
# entry names a mutation of the *subject*, which nothing can remove yet.
EXPECTED_REFUSALS = {
    "101": [],
    "102": ["bad_signature"],
    "103": ["replayed_counter"],
    "104": ["stale_issued", "stale_issued"],
    "105": ["uncommitted_key"],
    "106": ["uncommitted_key"],
    "107": ["uncommitted_key"],
    "108": ["replayed_counter"],
    "109": ["bad_payload", "bad_payload"],
    "110": ["bad_payload", "bad_payload"],
    "111": ["unauthorised_content"],
    "112": ["stale_issued", "stale_issued"],
    "113": ["bad_envelope", "bad_envelope"],
    "114": ["replayed_counter"],
    "115": ["unknown_kind"],
    "116": ["unknown_epoch"],
    "117": [],
    "118": ["unauthorised_content"],
    "119": [],
    "151": ["unauthorised_content"],
    "152": ["stale_issued"],
    "153": [],
    "154": [],
    "155": [],
    "156": [],
}
EXPECTED_MUTATIONS = {
    "101": None,
    "102": "no-verify",
    "103": "no-mark",
    "104": "no-issued",
    "105": "no-commit",
    "106": "merge-peers",
    "107": "kind-any",
    "108": "no-mark",
    "109": "lenient-key",
    "110": "no-payload",
    "111": "no-roles",
    "112": "no-issued",
    "113": "lenient-layout",
    "114": "no-mark",
    "115": "lenient-kind",
    "116": "lenient-epoch",
    "117": "refuse-bad-path",
    "118": "no-roles",
    "119": "keep-long-path",
    "151": "ignore-roles",
    "152": "ignore-issued",
    "153": "announce-once",
    "154": "no-lease",
    "155": "any-closing",
    "156": "wait-for-ever",
}

# The positive control of the absence scan, and the reason it is a control rather than a
# comment: the rule is "no server-authored frame carries a path, a role or a byte of content",
# and every `selvage/1` transcript carries one, so running the scan over the layer that exists
# is how the scan is shown to read something. These are the counts it finds there. A scan that
# read nothing reports zero and is a red run; a `selvage/1` vector deleted or re-baselined
# moves one of these, which is a deliberate edit and not a silent one.
EXPECTED_V1_MEMBERS = {"role": 8335, "documents": 397, "paths": 10}
EXPECTED_V1_EVENTS = {
    "doc.opened": 167,
    "doc.closed": 13,
    "doc.granted": 10,
    "host.attached": 2,
    "host.detached": 5,
}
EXPECTED_V1_NEEDLES = {
    "expect frames naming `src/main.rs`": 58,
    "binary steps carrying its bytes": 6,
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

# Every step op the wire layer's runner knows, and nothing else. It used to be an `else` clause
# that passed whatever it did not otherwise reach, and `apply` was in it while
# `runner/run_vectors.py` raises on an op it does not know — so an `apply` step would have
# validated here and failed the replay. `runner/test_runner.py` compares this set against the
# runner's own, so the two halves of the tooling cannot disagree about a step name again.
WIRE_OPS = frozenset(
    {
        "open",
        "send",
        "expect",
        "sendBinary",
        "expectBinary",
        "expectClose",
        "close",
        "wait",
        "http",
        "expectStatus",
        "expectBody",
        "expectDoc",
        "expectSameState",
    }
)

# The peer layer's two step vocabularies. A **frame** vector is replayed by
# `runner/run_peer.py` with no client: it seals a recipe, corrupts a frame on purpose, and asks a
# receiver for a verdict or for what it holds. A **decision** vector drives a real client through
# the subject protocol; the runner plays the relay, so its ops are checked here and run there, and
# reported as not attempted only when no `--subject` is named.
PEER_FRAME_OPS = frozenset(
    {
        "seal",
        "corrupt",
        "expectVerify",
        "expectReject",
        "expectPlaintext",
        "expectListing",
        "expectHolds",
        "expectDoc",
    }
)
PEER_DECISION_OPS = frozenset(
    {
        "start",
        "stop",
        "deliver",
        "expectSubject",
        "wait",
    }
)
# The ops in a peer vector that assert something, split by kind: a frame vector's are the ones
# `run_peer.py` counts, and a decision vector's `expectSubject` is the decision channel.
PEER_ASSERTION_OPS = frozenset(
    {"expectVerify", "expectReject", "expectPlaintext", "expectListing", "expectHolds",
     "expectDoc"}
)
PEER_OPS = {
    "frame": PEER_FRAME_OPS,
    "decision": PEER_DECISION_OPS,
}

#: The mutations a frame vector may declare it catches: the guards `runner/sealed.py` removes,
#: one rule each. A decision vector declares a *subject's* mutation instead, and those live in
#: `runner/subject.py`; this file pins which name each vector declares and leaves the question of
#: whether the name is implemented to `run_peer.py --mutation-census`, which is the tool that can
#: answer it.
PEER_FRAME_MUTATIONS = frozenset(
    {
        "lenient-layout",
        "lenient-kind",
        "lenient-epoch",
        "no-commit",
        "kind-any",
        "no-mark",
        "no-verify",
        "no-payload",
        "lenient-key",
        "no-issued",
        "no-roles",
        "refuse-bad-path",
        "keep-long-path",
        "merge-peers",
    }
)
PEER_SUBJECT_MUTATIONS = frozenset(
    {
        "ignore-roles",
        "ignore-issued",
        "announce-once",
        "no-lease",
        "any-closing",
        "wait-for-ever",
    }
)

#: The member names no server-authored `selvage/2` frame may carry, and the events the version
#: deletes. `host` was in the corpus study's table of member names and is not one: `host` appears
#: in this corpus as the first half of an *event* name, `host.attached` and `host.detached`, and
#: the study's 186 is a count of something else. A member census and an event census are two
#: different measurements, so they are two, and the events are pinned as well as scanned.
FORBIDDEN_MEMBERS = ("role", "path", "paths", "documents", "grant")
FORBIDDEN_EVENTS = frozenset(
    {"doc.opened", "doc.closed", "doc.granted", "host.attached", "host.detached"}
)

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
        elif op in WIRE_OPS:
            pass
        else:
            fail(at, f"unknown op {op!r}")

    if not assertions:
        fail(where, "asserts nothing: no step reads or asserts a frame")
    return assertions, sorted(codes)


def check_counts(vectors: int, assertions: int) -> None:
    """Fails when the wire corpus is not the one the pinned counts describe."""
    global CHECKS
    if vectors != EXPECTED_WIRE_VECTORS:
        fail(
            "vectors",
            f"{vectors} files, and this suite pins {EXPECTED_WIRE_VECTORS}: adding or "
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
# — the state's `peers` keyed by `peer_id` again, or a second role — with every other check in this
# suite still green. The values below are per payload and there is one copy of each: the state's
# shape is the one this pass changed most, and a second, dead copy of it is a copy that goes stale.
SEALED_KEY_A = "GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM"
SEALED_KEY_B = "laGjN1Xs9WCrmojozXGfP9_-1ppPlIwOcog-ef1AdIc"


SEALED_STATE_CONFORMING = {
    "a state at the first `issued` a host writes": {"issued": 1, "listing": [], "peers": {}},
    "a listing of one path": {"issued": 2, "listing": ["src/main.rs"], "peers": {}},
    "a host and a viewer, keyed by key": {
        "issued": 3,
        "listing": ["README.md", "src/main.rs"],
        "peers": {
            SEALED_KEY_A: {"peer_id": "p-0f1e2d3c4b5a6978", "role": "host"},
            SEALED_KEY_B: {"peer_id": "p-8796a5b4c3d2e1f0", "role": "viewer"},
        },
    },
    "a state carrying a member this version does not define": {
        "issued": 1,
        "listing": [],
        # The shape before CANONICAL.md §6.1 keyed `peers` by `peer_id` and put `key` inside
        # the entry. A receiver drops the unknown `key` member and reads the entry by its name,
        # which is what tolerance means (CANONICAL.md §3), so this is conforming and not one of
        # the refusals below: what a schema cannot do is forbid the older shape's member.
        "peers": {SEALED_KEY_A: {"key": SEALED_KEY_A, "peer_id": "p-1", "role": "host"}},
    },
    # The next two are the *drop* side of a rule `PROTOCOL.md` §13.3 re-homes, and their being
    # here rather than below is the whole of what step 8 does with them: it reads the member set
    # and each member's type, so a listing is a list of strings and the value of one string is
    # not its business. Whether a receiver carries the path is §13.3's rule — it drops the path
    # and applies the rest of the listing and the state's roles — and no schema can express a
    # drop, which is why the fixture is the value a receiver must *accept* and then not offer.
    "a listing whose path carries a control character, which §13.3 drops and does not refuse": {
        "issued": 1,
        "listing": ["src/main\u0000.rs"],
        "peers": {},
    },
    "a state at `issued` 0, which step 9 refuses `stale_issued` and step 8 does not refuse": {
        "issued": 0,
        "listing": [],
        "peers": {},
    },
}
SEALED_STATE_REFUSED = {
    "a state with no `issued`": {"listing": [], "peers": {}},
    "a state that names a `peer_id` where a key belongs": {
        "issued": 1,
        "listing": [],
        "peers": {"p-1": {"peer_id": "p-1", "role": "host"}},
    },
    "a peer with no seat": {"issued": 1, "listing": [], "peers": {SEALED_KEY_A: {"role": "host"}}},
    "a peer with no role": {
        "issued": 1,
        "listing": [],
        "peers": {SEALED_KEY_A: {"peer_id": "p-1"}},
    },
    "a role this version has not": {
        "issued": 1,
        "listing": [],
        "peers": {SEALED_KEY_A: {"peer_id": "p-1", "role": "admin"}},
    },
    "a key that is not 32 bytes": {
        "issued": 1,
        "listing": [],
        "peers": {SEALED_KEY_A[:-1]: {"peer_id": "p-1", "role": "host"}},
    },
    "a key with a character base64url has not": {
        "issued": 1,
        "listing": [],
        "peers": {SEALED_KEY_A[:-1] + "+": {"peer_id": "p-1", "role": "host"}},
    },
    "a key whose final character carries non-zero pad bits": {
        "issued": 1,
        "listing": [],
        "peers": {SEALED_KEY_A[:-1] + "N": {"peer_id": "p-1", "role": "host"}},
    },
    # `$` may match before a trailing newline in Python's `re`, which is the engine behind the
    # shipped `jsonschema`; the length bounds are what refuse this one.
    "a key with a trailing newline": {
        "issued": 1,
        "listing": [],
        "peers": {SEALED_KEY_A + "\n": {"peer_id": "p-1", "role": "host"}},
    },
    "a state at a negative `issued`, which is not a count": {
        "issued": -1,
        "listing": [],
        "peers": {},
    },
    "a listing whose member is not a string": {
        "issued": 1,
        "listing": [7],
        "peers": {},
    },
    "a listing that is not a list": {"issued": 1, "listing": "README.md", "peers": {}},
    "a closing offered as a state": {"closing": True, "issued": 2},
}
SEALED_CLOSING_CONFORMING = {
    "a closing": {"closing": True, "issued": 2},
    # `0` is a count (CANONICAL.md §2.4), so step 8 reads the type and the value is `issued`'s
    # own step: a receiver's mark starts at `0` and a closing at `0` is refused `stale_issued`
    # at §6.1's step 9. No schema can express a comparison against the receiver's mark.
    "a closing at `issued` 0, which step 9 refuses `stale_issued` and step 8 does not refuse": {
        "closing": True,
        "issued": 0,
    },
}
SEALED_CLOSING_REFUSED = {
    "a closing with no `issued`": {"closing": True},
    "a closing whose `closing` is false": {"closing": False, "issued": 3},
    "a closing whose `issued` is not a count": {"closing": True, "issued": "2"},
    "a closing at a negative `issued`, which is not a count": {"closing": True, "issued": -1},
    "a room state offered as a closing": {"issued": 2, "listing": [], "peers": {}},
    "an `issued` above the JavaScript bound": {"closing": True, "issued": 9007199254740992},
}
SEALED_HOLDS_CONFORMING = {
    "a hold set of one path": {"holds": ["src/main.rs"]},
    "an empty hold set": {"holds": []},
    "a hold set of several paths": {"holds": ["README.md", "src/main.rs"]},
    "a hold set carrying a member this version does not define": {
        "holds": ["README.md"],
        "renewed": True,
    },
    # §13.7's drop, on the same reading as the state's listing above: a holds message whose set
    # carries a path §5 refuses is a message of the right member set and types, applied, with
    # the path left out of the set the receiver keeps for that peer.
    "a hold carrying a control character, which §13.7 drops and does not refuse": {
        "holds": ["src/main\u0000.rs"],
    },
    "a hold that is blank, which §13.7 drops and does not refuse": {"holds": [""]},
}
SEALED_HOLDS_REFUSED = {
    "holds that is not a list": {"holds": "src/main.rs"},
    "a message with no `holds`": {"renewed": True},
    "a hold that is not a string": {"holds": [7]},
    "a room state offered as holds": {"issued": 1, "listing": [], "peers": {}},
}
SEALED_ANNOUNCEMENT_CONFORMING = {
    "an announcement of a key": {"key": SEALED_KEY_A},
    "one that declares `viewer`": {"key": SEALED_KEY_B, "role": "viewer"},
    "one that declares `guest`": {"key": SEALED_KEY_A, "role": "guest"},
    "one carrying a member this version does not define": {"key": SEALED_KEY_A, "peer_id": "p-1"},
}
SEALED_ANNOUNCEMENT_REFUSED = {
    "an announcement with no `key`": {"role": "guest"},
    "a key that is not 32 bytes": {"key": SEALED_KEY_A[:-1]},
    "a key whose final character carries non-zero pad bits": {"key": SEALED_KEY_A[:-1] + "N"},
    "a key with a trailing newline": {"key": SEALED_KEY_A + "\n"},
    "a declaration of `host`, which no peer can make": {"key": SEALED_KEY_A, "role": "host"},
    "a declaration this version has not": {"key": SEALED_KEY_A, "role": "admin"},
    "a room state offered as an announcement": {"issued": 1, "listing": [], "peers": {}},
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
    (
        "the sealed holds",
        "sealed.json#/$defs/roomHolds",
        SEALED_HOLDS_CONFORMING,
        SEALED_HOLDS_REFUSED,
    ),
    (
        "the sealed session-key announcement",
        "sealed.json#/$defs/sessionAnnouncement",
        SEALED_ANNOUNCEMENT_CONFORMING,
        SEALED_ANNOUNCEMENT_REFUSED,
    ),
)


# `selvage/2`'s session-layer shapes, as `schema/session-v2.json` and `PROTOCOL.md`'s `selvage/2`
# passages fix them. A `selvage/2` `/meta` body and its `session.hello` replies are not session
# frames — `/meta` carries no `v`, and the transcripts are `selvage/1`'s until the corpus is
# re-baselined — so nothing else here reaches them, and a `roles` made required again, a `documents`
# readmitted, a peer record's `display_name` dropped, an advertisement naming `selvage/1`, a
# `peer.joined` whose peer is not this version's record, a `host_present` readmitted to the fault
# vocabulary, or a `keepalive` with no `room_grace_ms` would leave every other check in this suite
# green. Both directions are checked, so a shape that refuses everything fails too. A *member* these
# values carry and the shape does not define is deliberate: a receiver tolerates one (`PROTOCOL.md`
# §4.1), so the model must not forbid it, and what holds a *server* to the member set of its version
# is the corpus's exact comparison rather than a schema. A fault *code* is a closed value and not a
# member, which is why the vocabulary is the one place one of these shapes refuses a value: see
# `check_session_v2`'s last block, which checks that the two versions' vocabularies differ in exactly
# the codes §11 says they do.
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
    "a server that also accepts a later minor": {
        **SESSION_V2_ADVERTISED,
        "wire_versions": ["selvage/2", "selvage/2.1"],
    },
    "a server advertising a name of its own": {
        **SESSION_V2_ADVERTISED,
        "capabilities": ["y-protocols/1", "awareness", "x.selvage.demo"],
    },
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
    "a body that advertises only `selvage/1`": {
        **SESSION_V2_ADVERTISED,
        "wire_versions": ["selvage/1"],
    },
    "a body whose `keepalive` has no `room_grace_ms`": {
        **SESSION_V2_ADVERTISED,
        "keepalive": SESSION_V2_KEEPALIVE,
    },
    "a body missing the `y-protocols/1` capability": {
        **SESSION_V2_ADVERTISED,
        "capabilities": ["awareness"],
    },
    "a body missing the `awareness` capability": {
        **SESSION_V2_ADVERTISED,
        "capabilities": ["y-protocols/1"],
    },
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

# The one event of the seven `PROTOCOL.md` §6 gives a `selvage/2` server whose params are this
# version's peer record, and therefore the one that differs from `selvage/1`'s shape. `peer.left`,
# `peer.renamed` and `room.gone` are `events.json`'s unchanged and are checked there; `session.error`
# is the same event, and its params in this version are `session-v2.json#/$defs/errorObject`.
SESSION_V2_PEER_JOINED_CONFORMING = {
    "a `peer.joined`": {"peer": {"peer_id": "p-3d33", "display_name": "Bob"}},
    "one whose peer carries an awareness id": {
        "peer": {"peer_id": "p-3d33", "display_name": "Bob", "awareness_client_id": 42},
    },
    "one carrying a member this version does not define": {
        "peer": {"peer_id": "p-3d33", "display_name": "Bob", "role": "guest"},
    },
}
SESSION_V2_PEER_JOINED_REFUSED = {
    "a `peer.joined` with no `peer`": {},
    "one whose peer has no `display_name`": {"peer": {"peer_id": "p-3d33"}},
    "one whose peer has no `peer_id`": {"peer": {"display_name": "Bob"}},
}

# The fault vocabulary of this version (`PROTOCOL.md` §11): `selvage/1`'s nine codes that survive,
# and an implementation's own `x.` name. The two codes that go are the ones whose fault this
# server's shape cannot produce, and the second set below is what makes that a difference between
# two shipped schemas rather than a claim: the same value must still validate against
# `errors.json#/$defs/errorObject`, or the version's enum would be a copy that happened to drop a
# value rather than the version's vocabulary.
SESSION_V2_FAULT_CONFORMING = {
    "a refusal this version carries": {"code": "room_unknown", "message": "no such room"},
    "a fault a seated connection is answered with": {
        "code": "already_seated",
        "message": "session.hello sent twice",
    },
    "a capacity code of the implementation's own": {
        "code": "x.room_full",
        "message": "the room is full",
    },
}
SESSION_V2_FAULT_REFUSED = {
    "`host_present`, whose fault this server cannot have": {
        "code": "host_present",
        "message": "a host is already connected",
    },
    "`doc_not_open`, reserved for a method this version does not have": {
        "code": "doc_not_open",
        "message": "no such error to raise",
    },
    "a name that is neither code nor `x.` name": {
        "code": "cursor.teleport",
        "message": "no such method",
    },
    "a fault with no `message`": {"code": "room_unknown"},
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
        "a `selvage/2` `peer.joined`",
        "session-v2.json#/$defs/peerJoined",
        SESSION_V2_PEER_JOINED_CONFORMING,
        SESSION_V2_PEER_JOINED_REFUSED,
    ),
    (
        "a `selvage/2` fault",
        "session-v2.json#/$defs/errorObject",
        SESSION_V2_FAULT_CONFORMING,
        SESSION_V2_FAULT_REFUSED,
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
    writes fails beside a shape that accepts what it does not. The fault vocabulary is the one
    shape here that refuses a *value* rather than tolerating it, so its last block checks the other
    half of that claim: the two codes this version drops must still validate against `selvage/1`'s
    vocabulary, or the version's `enum` would be a copy that happens to be missing two entries.
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

    v1_fault = Draft202012Validator(
        {"$ref": f"{BASE}errors.json#/$defs/errorObject"}, registry=reg
    )
    v2_fault = Draft202012Validator(
        {"$ref": f"{BASE}session-v2.json#/$defs/errorObject"}, registry=reg
    )
    for code in ("host_present", "doc_not_open"):
        value = {"code": code, "message": f"{code} as `selvage/1` carries it"}
        checks += 1
        if list(v1_fault.iter_errors(value)):
            fail("schema", f"`selvage/1`'s fault vocabulary refuses its own code `{code}`")
        checks += 1
        if not list(v2_fault.iter_errors(value)):
            fail("schema", f"`selvage/2`'s fault vocabulary accepts `{code}`")
    return checks


def check_sealed_payloads(reg: Registry) -> int:
    """Runs the shipped `sealed.json` against what `CANONICAL.md` §6.1 says it describes.

    Nothing else in this suite reads that file — a sealed frame is bytes rather than JSON, and
    the vectors for one are the corpus layer's — so a member set relaxed here (the state's `key`
    back to the `key_id` that verifies nothing, or a fourth role) would leave every other check
    green. Both directions are checked, so a constraint that refuses everything fails too. What
    step 8 reads is the member set and each member's *type* (`CANONICAL.md` §6.1), so a value a
    rule elsewhere re-homes — a listing's path and a holds' path, which `PROTOCOL.md` §13.3 and
    §13.7 have a receiver drop rather than refuse — is conforming here and its drop is that
    rule's; the alternative, a `$ref` to `common.json`'s `documentPath`, would make the schema
    refuse a frame the prose applies and put two conforming receivers at odds about one state.
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


def check_refusals(reg: Registry) -> int:
    """Runs `sealed.json`'s report vocabulary against `CANONICAL.md` §6.1's table.

    The reasons a receiver reports a refused sealed frame in are a closed vocabulary and nothing
    else in this suite reads them: a reason added to the schema with no rule behind it, a rule's
    reason dropped from it, and an implementation's invented spelling would all leave every other
    check green. The census below is that table in the order the table reads — `bad_payload` at
    §6.1's step 8, before `stale_issued` at step 9 — and the two bad ones are the two ways a
    vocabulary like this goes wrong in practice: a reason no conforming receiver can produce
    (`bad_tag`, which the signature's coverage of the ciphertext makes unreachable, measured in
    `docs/studies/peer-corpus.md` §5.4) and a misspelling of one that exists.
    """
    expected = [
        "bad_envelope",
        "unknown_kind",
        "unknown_epoch",
        "uncommitted_key",
        "replayed_counter",
        "bad_signature",
        "bad_aead",
        "bad_payload",
        "stale_issued",
        "unauthorised_content",
    ]
    checks = 0
    try:
        schema = json.loads((SCHEMA_DIR / "sealed.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail("sealed.json", f"not readable as JSON: {error}")
        return 0
    enum = schema.get("$defs", {}).get("refusalReason", {}).get("enum")
    if enum != expected:
        fail(
            "sealed.json",
            f"the report vocabulary is {enum!r}, and `CANONICAL.md` §6.1's table is {expected!r}",
        )
    validator = Draft202012Validator(
        {"$ref": f"{BASE}sealed.json#/$defs/refusalReason"}, registry=reg
    )
    for reason in expected:
        checks += 1
        if list(validator.iter_errors(reason)):
            fail("schema", f"the report vocabulary refuses `{reason}`, a reason §6.1 names")
    for name, value in {
        "`bad_tag`, which the signature's coverage of the ciphertext makes unreachable": "bad_tag",
        "a reason this version has not": "unknown_peer",
        "a reason with a typo": "stale_issue",
    }.items():
        checks += 1
        if not list(validator.iter_errors(value)):
            fail("schema", f"the report vocabulary accepts {name}")
    return checks


def _member_names(value: object) -> set[str]:
    """Every member name anywhere in a JSON value, at any depth.

    The scan the absence rule is asserted with, and the reason it is a walk and not four
    `in` tests: a `role` nested inside a peer record is the same defect as one at the top of
    a frame, and a scan that only looked at the top level would report zero.
    """
    names: set[str] = set()
    if isinstance(value, dict):
        for member, entry in value.items():
            names.add(member)
            names |= _member_names(entry)
    elif isinstance(value, list):
        for entry in value:
            names |= _member_names(entry)
    return names


def absence_violations(document: object) -> list[str]:
    """What a `selvage/2` transcript breaks the absence rule with, if anything.

    The rule, as `docs/studies/peer-corpus.md` §6 corrects it — the study's own sentence said
    "no server-authored frame contains a path, a role, a file name or a character of text",
    which the reference server fails on a `display_name` — is:

    > No server-authored frame carries a `path`, a `documents`/`paths`/`grant` member, a
    > `role`, or any byte of document content or cursor state.

    A frame carrying one of the five member names fails, and so does an `event` naming one of
    the five events this version deletes: the member is what a receiver would read and the event
    is what would tell it to look. The three keys the server *does* author — the room id, the
    token, and a peer's `display_name` — are how a scan that read nothing is told apart from a
    scan that passed, which is why the caller asserts them separately.

    This is a pure function so that `runner/test_runner.py` can drive it in both directions: a
    transcript that carries a `role` must be caught and a conforming one must not, and a rule
    that only ever runs against an empty corpus is not a check.
    """
    if not isinstance(document, dict) or document.get("selvage") != "selvage/2":
        return []
    violations: list[str] = []
    for index, step in enumerate(document.get("steps", []) or []):
        if not isinstance(step, dict):
            continue
        if step.get("op") not in ("expect", "expectBody", "send", "sendBinary", "expectBinary"):
            continue
        at = f"step {index} ({step.get('op')})"
        frame = parse_or_none(step.get("text")) if "text" in step else None
        if isinstance(frame, dict):
            names = sorted(_member_names(frame) & set(FORBIDDEN_MEMBERS))
            if names:
                violations.append(f"{at} carries {names}")
            if frame.get("event") in FORBIDDEN_EVENTS:
                violations.append(f"{at} is a `{frame['event']}`, which this version deletes")
        if "hex" not in step:
            continue
        try:
            raw = bytes.fromhex(step["hex"].replace(" ", ""))
        except (ValueError, AttributeError):
            continue
        for needle in document.get("secret", []) or []:
            if isinstance(needle, str) and needle and needle.encode() in raw:
                violations.append(f"{at} carries {needle!r} in its bytes")
    return violations


def check_absence() -> str:
    """The one negative class checkable with no key at all, and the control that it reads.

    Three legs. **The shipped model**: every `selvage/2` session shape is walked for a member of
    the forbidden list — a `role` made a *property* or a `required` entry of `session-v2.json`
    is a red run, which is the structural half and the strongest of the three, because it
    refuses the member rather than scanning for one. **The rule itself**: `absence_violations`
    is run over every wire vector declaring `selvage/2` and over every peer vector's sealed
    frames. **The control**: the same walk is run over the `selvage/1` corpus, where it must
    find something, and the counts it finds are pinned — a scan that read nothing reports zero,
    and a pinned zero is a check while an unpinned one is a green line.
    """
    # -- the shipped `selvage/2` session shapes --
    shapes = 0
    try:
        session_v2 = json.loads((SCHEMA_DIR / "session-v2.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail("session-v2.json", f"not readable as JSON: {error}")
        session_v2 = {}
    for definition, shape in sorted(session_v2.get("$defs", {}).items()):
        if not isinstance(shape, dict):
            continue
        shapes += 1
        declared = set(shape.get("properties", {})) | set(shape.get("required", []))
        wrong = sorted(declared & set(FORBIDDEN_MEMBERS))
        if wrong:
            fail(
                "session-v2.json",
                f"`{definition}` declares {wrong}, which no server-authored `selvage/2` frame "
                "may carry",
            )

    # -- the rule, over the corpus that declares this version --
    live = 0
    for path in sorted(VECTOR_DIR.glob("*.json")) + sorted(PEER_VECTOR_DIR.glob("*.json")):
        try:
            document = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and document.get("selvage") == "selvage/2":
            live += 1
            for problem in absence_violations(document):
                fail(path.name, f"a server-authored frame breaks the absence rule: {problem}")

    # -- the control: the same walk over `selvage/1`, where it must find something --
    members = {name: 0 for name in EXPECTED_V1_MEMBERS}
    events = {name: 0 for name in EXPECTED_V1_EVENTS}
    needles = {name: 0 for name in EXPECTED_V1_NEEDLES}
    wire_frames = 0
    binary_steps = 0
    for path in sorted(VECTOR_DIR.glob("*.json")):
        try:
            document = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for step in document.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            if step.get("op") in ("expect", "expectBody"):
                frame = parse_or_none(step.get("text"))
                if frame is None:
                    continue
                wire_frames += 1
                for name in sorted(_member_names(frame) & set(members)):
                    members[name] += 1
                if frame.get("event") in events:
                    events[frame["event"]] += 1
                if isinstance(step.get("text"), str) and "src/main.rs" in step["text"]:
                    needles["expect frames naming `src/main.rs`"] += 1
            elif step.get("op") in ("sendBinary", "expectBinary") and step.get("hex"):
                binary_steps += 1
                try:
                    raw = bytes.fromhex(step["hex"].replace(" ", ""))
                except ValueError:
                    continue
                if b"src/main.rs" in raw:
                    needles["binary steps carrying its bytes"] += 1
    for label, found, pinned in (
        ("member names", members, EXPECTED_V1_MEMBERS),
        ("deleted events", events, EXPECTED_V1_EVENTS),
        ("needles", needles, EXPECTED_V1_NEEDLES),
    ):
        if found != pinned:
            fail(
                "vectors",
                f"the absence scan finds {found} in the selvage/1 corpus for its {label}, and "
                f"this suite pins {pinned}: the scan is shown to read something by finding what "
                "that layer carries, and a walker that stopped walking finds nothing",
            )

    # -- the sealed frames, against the strings their own plaintext names --
    sealed_frames = 0
    secrets = 0
    for path in sorted(PEER_VECTOR_DIR.glob("*.json")):
        try:
            document = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        secret = [s for s in document.get("secret", []) if isinstance(s, str) and s]
        if not secret:
            continue
        present = False
        for index, step in enumerate(document.get("steps", []) or []):
            if not isinstance(step, dict) or step.get("op") not in ("seal", "deliver", "publish"):
                continue
            at = f"{path.name} step {index} (seal)"
            plaintext = _recipe_plaintext(step.get("recipe"))
            if plaintext is not None and any(s.encode() in plaintext for s in secret):
                present = True
            try:
                raw = bytes.fromhex((step.get("hex") or "").replace(" ", ""))
            except ValueError:
                continue
            sealed_frames += 1
            for needle in secret:
                secrets += 1
                if needle.encode() in raw:
                    fail(
                        at,
                        f"the sealed bytes carry {needle!r}, which this frame's own plaintext "
                        "names: a sealed frame is the whole of the concealment",
                    )
        if not present:
            fail(
                path.name,
                f"declares `secret` {secret} and no plaintext of the vector carries one, so "
                "the scan proves nothing about this vector",
            )
    return (
        f"{shapes} selvage/2 shapes, {live} vectors of this version, {wire_frames} selvage/1 "
        f"frames ({sum(members.values())} carrying a deleted member, "
        f"{sum(events.values())} a deleted event), {sealed_frames} sealed frames, "
        f"{secrets} needle checks"
    )


def _recipe_plaintext(recipe: object) -> bytes | None:
    """The plaintext a recipe names, without sealing it: hex, or the canonical JSON of a
    payload. The absence scan needs the plaintext and not the frame, and it has no key."""
    if not isinstance(recipe, dict):
        return None
    if isinstance(recipe.get("plaintext"), str):
        try:
            return bytes.fromhex(recipe["plaintext"].replace(" ", ""))
        except ValueError:
            return None
    if "payload" in recipe:
        return json.dumps(
            recipe["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    return None


# --- the peer layer's vectors ---------------------------------------------------

PEER_VECTOR_DIR = VECTOR_DIR / "peer"
FIXTURE_PATH = VECTOR_DIR / "fixture" / "keys.json"
NONCE_HEX = re.compile(r"[0-9a-f]{24}")


def refusal_vocabulary() -> list[str]:
    """The reasons a receiver reports, from the file that fixes them."""
    try:
        schema = json.loads((SCHEMA_DIR / "sealed.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return list(schema.get("$defs", {}).get("refusalReason", {}).get("enum", []))


def load_fixture() -> dict:
    """The fixture's key names and its host key. Values are never read here: this file stays
    key-free, and what a public key's bytes *mean* is `runner/sealed.py`'s business."""
    try:
        document = json.loads(FIXTURE_PATH.read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail("fixture", f"{FIXTURE_PATH.name} is not readable as JSON: {error}")
        return {}
    keys = document.get("keys")
    if not isinstance(keys, dict):
        fail("fixture", "the fixture has no `keys` object")
        return {}
    room = document.get("room")
    if not isinstance(room, dict) or room.get("host") not in keys:
        fail("fixture", "the fixture's `room.host` does not name one of its keys")
    return {"keys": sorted(keys), "host": (room or {}).get("host")}


def check_recipe(at: str, step: dict, fixture: dict) -> None:
    """A recipe is what a vector *says*, so its shape is the vector's readability.

    A recipe names a fixture key, a kind, a counter, a nonce and exactly one of `plaintext` and
    `payload`. `plaintext` is hex and is how a `kind = 0` stream is written and how a plaintext
    that is deliberately not its kind's object is expressed; `payload` is the object itself, so
    that a reader sees what the frame means and not only what it is.
    """
    recipe = step.get("recipe")
    if not isinstance(recipe, dict):
        fail(at, "`seal` needs a `recipe` object")
        return
    for member in ("sign", "kind", "counter", "nonce"):
        if member not in recipe:
            fail(at, f"the recipe has no {member!r}")
    sign = recipe.get("sign")
    if sign not in fixture.get("keys", []):
        fail(at, f"the recipe signs with {sign!r}, which the fixture does not have")
    kind = recipe.get("kind")
    if not isinstance(kind, int) or isinstance(kind, bool) or kind < 0:
        fail(at, f"`kind` is a count, not {kind!r}")
    epoch = recipe.get("epoch", 0)
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        fail(at, f"`epoch` is a count, not {epoch!r}")
    counter = recipe.get("counter")
    if not isinstance(counter, int) or isinstance(counter, bool) or counter < 1:
        fail(
            at,
            f"`counter` is {counter!r}: a sender gives its first frame under one key ``1``, "
            "and each later frame a strictly greater one",
        )
    nonce = recipe.get("nonce")
    if not isinstance(nonce, str) or not NONCE_HEX.fullmatch(nonce):
        fail(at, f"`nonce` is 12 bytes of lowercase hex, and {nonce!r} is not")
    has_plaintext = "plaintext" in recipe
    has_payload = "payload" in recipe
    if has_plaintext == has_payload:
        fail(at, "the recipe carries exactly one of `plaintext` and `payload`")
    if has_plaintext:
        text = recipe["plaintext"]
        wrong = (
            [byte for byte in text.split() if not TWO_HEX_DIGITS.fullmatch(byte)]
            if isinstance(text, str)
            else [text]
        )
        if wrong:
            fail(at, f"`plaintext` is two hex digits per byte: {wrong}")
    if has_payload and kind not in (1, 2, 3, 4):
        fail(
            at,
            f"`payload` is for the kinds whose plaintext is a JSON object, and kind {kind} 's "
            "plaintext is a y-protocols stream",
        )


def check_peer_step(at: str, step: dict, kind: str, fixture: dict, vocab: list[str]) -> list[str]:
    """One peer step. Returns the refusal reasons it asserts."""
    op = step.get("op")
    refusals: list[str] = []
    if op == "seal":
        if not isinstance(step.get("frame"), str) or not step["frame"]:
            fail(at, "`seal` needs a `frame` name")
        check_recipe(at, step, fixture)
    elif op == "corrupt":
        if not isinstance(step.get("as"), str) or not step["as"]:
            fail(at, "`corrupt` needs an `as` name for the frame it produces")
        variants = [name for name in ("xor", "truncate", "append") if name in step]
        if len(variants) != 1:
            fail(at, f"`corrupt` needs exactly one of `xor`, `truncate` and `append`, not {variants}")
        if "xor" in step and not isinstance(step.get("at"), int):
            fail(at, "`xor` needs the `at` byte it flips")
        if "append" in step:
            text = step["append"]
            wrong = (
                [byte for byte in text.split() if not TWO_HEX_DIGITS.fullmatch(byte)]
                if isinstance(text, str)
                else [text]
            )
            if wrong:
                fail(at, f"`append` is two hex digits per byte: {wrong}")
    elif op in ("expectVerify", "expectReject", "expectPlaintext"):
        if not isinstance(step.get("frame"), str):
            fail(at, f"`{op}` needs the `frame` it reads")
        if op == "expectReject":
            reason = step.get("reason")
            if reason not in vocab:
                fail(at, f"`{reason!r}` is not one of the reasons {vocab}")
            else:
                refusals.append(reason)
        if op == "expectPlaintext" and "signed_by" in step and step["signed_by"] not in fixture.get("keys", []):
            fail(at, f"`signed_by` names {step['signed_by']!r}, which the fixture does not have")
    elif op == "expectListing":
        listing = step.get("listing")
        if not isinstance(listing, list) or any(not isinstance(p, str) for p in listing):
            fail(at, "`listing` is a list of paths")
    elif op == "expectHolds":
        if step.get("sign") not in fixture.get("keys", []):
            fail(at, f"`sign` names {step.get('sign')!r}, which the fixture does not have")
        holds = step.get("holds")
        if not isinstance(holds, list) or any(not isinstance(p, str) for p in holds):
            fail(at, "`holds` is a list of paths")
    elif op == "expectDoc":
        if not isinstance(step.get("path"), str) or not isinstance(step.get("text"), str):
            fail(at, "`expectDoc` needs a `path` and the `text` held there")
    elif op == "expectSubject":
        if not [
            member
            for member in ("applied", "dropped", "published", "ended", "listing", "text",
                           "holds", "at_least", "frozen")
            if member in step
        ]:
            fail(at, "`expectSubject` asserts nothing about the subject's report")
        dropped = step.get("dropped")
        if dropped is not None:
            if not isinstance(dropped, list):
                fail(at, "`dropped` is a list of `{frame, reason}` entries")
            else:
                for entry in dropped:
                    reason = entry.get("reason") if isinstance(entry, dict) else None
                    if reason not in vocab:
                        fail(at, f"`{reason!r}` is not one of the reasons {vocab}")
                    else:
                        refusals.append(reason)
        for member in ("text", "holds", "at_least"):
            if member in step and not isinstance(step[member], dict):
                fail(at, f"`{member}` is an object")
        if "applied" in step and not isinstance(step["applied"], list):
            fail(at, "`applied` is a list of `{frame, kind}` entries")
        frozen = step.get("frozen")
        if frozen is not None and (
            not isinstance(frozen, list) or any(not isinstance(m, str) for m in frozen)
        ):
            fail(at, "`frozen` names the report members that must not move")
        if "within_ms" in step and (
            not isinstance(step["within_ms"], int)
            or isinstance(step["within_ms"], bool)
            or step["within_ms"] < 0
        ):
            fail(at, "`within_ms` is the deadline the predicate is polled to")
    elif kind == "decision":
        # A decision vector drives a subject through the frames the runner hands it. Its
        # `expectSubject` is the decision channel and its members are the
        # observables `PROTOCOL.md` §13.11 fixes: the exact members are compared exactly, `at_least`
        # is a monotone bound it may exceed, `frozen` names the members that must not move over one
        # more window, and `within_ms` is the deadline the predicate is polled to rather than slept
        # through.
        if op != "wait" and "conn" not in step:
            fail(at, f"`{op}` needs a `conn`")
        if op in ("deliver", "publish"):
            if not isinstance(step.get("frame"), str) or not step["frame"]:
                fail(at, f"`{op}` needs a `frame` name")
            check_recipe(at, step, fixture)
        if op == "start" and step.get("key") not in fixture.get("keys", []):
            fail(at, f"`key` names {step.get('key')!r}, which the fixture does not have")
        if op == "wait" and (
            not isinstance(step.get("ms"), int)
            or isinstance(step["ms"], bool)
            or step["ms"] < 0
        ):
            fail(at, "`wait` needs `ms`")
    if op in ("seal", "corrupt", "deliver", "publish"):
        text = step.get("hex")
        wrong = (
            [byte for byte in text.split() if not TWO_HEX_DIGITS.fullmatch(byte)]
            if isinstance(text, str)
            else [text]
        )
        if wrong:
            fail(at, f"`hex` is two hex digits per byte: {wrong}")
    return refusals


def check_peer_vector(
    document: object, name: str, fixture: dict, vocab: list[str]
) -> tuple[int, int, list[str]]:
    """One peer vector: its steps, its recipes and the reasons it asserts."""
    if not isinstance(document, dict):
        fail(name, "a peer vector is a JSON object")
        return 0, 0, []
    where = f"{name} [{document.get('id')}]"
    for member in ("id", "title", "spec", "layer", "kind", "fixture", "steps"):
        if member not in document:
            fail(where, f"missing {member!r}")
    for member, want in (("layer", "peer"), ("selvage", "selvage/2"), ("canonical", "SJ-C/1")):
        if document.get(member) != want:
            fail(where, f"{member} must be `{want}`")
    kind = document.get("kind")
    if kind not in PEER_OPS:
        fail(where, f"kind must be one of {sorted(PEER_OPS)}, not {kind!r}")
        return 0, 0, []
    if document.get("fixture") != "fixture/keys.json":
        fail(where, "a peer vector reads the one fixture, `fixture/keys.json`")
    secret = document.get("secret", [])
    if not isinstance(secret, list) or any(not isinstance(s, str) or not s for s in secret):
        fail(where, "`secret` is a list of non-empty strings")
    catches = document.get("catches")
    allowed = PEER_FRAME_MUTATIONS if kind == "frame" else PEER_SUBJECT_MUTATIONS
    if catches is not None and catches not in allowed:
        fail(
            where,
            f"`catches` is {catches!r}, which is not one of the {kind} layer's mutations "
            f"{sorted(allowed)}",
        )

    steps = document.get("steps")
    if not isinstance(steps, list) or not steps:
        fail(where, "steps must be a non-empty list")
        return 0, 0, []
    checks = 0
    assertions = 0
    refusals: list[str] = []
    nonces: dict[str, str] = {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            fail(f"{where} step {index}", "a step is a JSON object")
            continue
        at = f"{where} step {index} ({step.get('op')})"
        checks += 1
        if step.get("op") not in PEER_OPS[kind]:
            fail(at, f"`{step.get('op')}` is not a step of a {kind} vector")
            continue
        if step.get("op") in PEER_ASSERTION_OPS:
            assertions += 1
        if step.get("op") in ("seal", "deliver"):
            # One check per step and one per recipe: a recipe is a claim of its own, and a
            # vector that keeps its bytes while losing what they are made of is the drift the
            # recipe exists to catch.
            checks += 1
            recipe = step.get("recipe")
            nonce = recipe.get("nonce") if isinstance(recipe, dict) else None
            if isinstance(nonce, str):
                # A nonce is fresh for one frame, so two steps may share one only when they
                # carry the same bytes — the shape a vector has when it delivers one frame
                # twice, as 155 delivers a closing before and after the state.
                hex_text = " ".join((step.get("hex") or "").split())
                if nonce in nonces and nonces[nonce] != hex_text:
                    fail(
                        at,
                        f"the nonce {nonce} is also {nonces[nonce]}'s, and these are two "
                        "different frames: a nonce is fresh for one",
                    )
                else:
                    nonces[nonce] = hex_text
        refusals.extend(check_peer_step(at, step, kind, fixture, vocab))
    if kind == "frame" and not assertions:
        fail(where, "asserts nothing: no step reads a verdict or what the receiver holds")
    return checks, assertions, sorted(refusals)


def check_peer_corpus() -> dict:
    """Every peer vector, the two censuses, and the peer layer's own counts."""
    fixture = load_fixture()
    vocab = refusal_vocabulary()
    paths = sorted(PEER_VECTOR_DIR.glob("*.json"))
    if not paths:
        fail("vectors", f"no peer vectors in {PEER_VECTOR_DIR}")
    checks = 0
    assertions = 0
    frames = 0
    decisions = 0
    refusals: dict[str, list[str]] = {}
    mutations: dict[str, object] = {}
    for path in paths:
        try:
            document = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            fail(path.name, f"not JSON: {error}")
            continue
        count, asserts, reasons = check_peer_vector(document, path.name, fixture, vocab)
        checks += count
        assertions += asserts
        vid = document.get("id") if isinstance(document, dict) else None
        key = vid if isinstance(vid, str) else path.name
        refusals[key] = reasons
        mutations[key] = document.get("catches") if isinstance(document, dict) else None
        if isinstance(document, dict):
            if document.get("kind") == "frame":
                frames += 1
            elif document.get("kind") == "decision":
                decisions += 1

    for label, found, pinned in (
        ("files", len(paths), EXPECTED_PEER_VECTORS),
        ("checks", checks, EXPECTED_PEER_CHECKS),
        ("assertion steps", assertions, EXPECTED_PEER_ASSERTIONS),
    ):
        if found != pinned:
            fail(
                "vectors",
                f"the peer layer holds {found} {label}, and this suite pins {pinned}: adding or "
                "removing one is a deliberate edit",
            )

    for vid in sorted(set(refusals) | set(EXPECTED_REFUSALS)):
        want = EXPECTED_REFUSALS.get(vid)
        have = refusals.get(vid)
        if want is None:
            fail("vectors", f"peer vector {vid!r} asserts {have} and this suite pins no census for it")
        elif have is None:
            fail("vectors", f"peer vector {vid!r} is in the pinned census and the corpus has no such vector")
        elif have != want:
            fail(
                "vectors",
                f"peer vector {vid!r} asserts {have}, and this suite pins {want}: an asserted "
                "reason was added, removed or substituted inside a closed vocabulary",
            )
    for vid in sorted(set(mutations) | set(EXPECTED_MUTATIONS)):
        want = EXPECTED_MUTATIONS.get(vid)
        have = mutations.get(vid)
        if vid not in EXPECTED_MUTATIONS:
            fail(
                "vectors",
                f"peer vector {vid!r} declares `catches: {have}` and this suite pins no census "
                "for it: a vector is added with the mutation it must go red under",
            )
        elif vid not in mutations:
            fail("vectors", f"peer vector {vid!r} is in the pinned census and the corpus has no such vector")
        elif have != want:
            fail(
                "vectors",
                f"peer vector {vid!r} declares `catches: {have}`, and this suite pins {want}",
            )
    return {
        "vectors": len(paths),
        "frame": frames,
        "decision": decisions,
        "checks": checks,
        "assertions": assertions,
    }


def main() -> int:
    """Checks every schema and every claim the corpus makes, and reports what it found."""
    global CHECKS
    reg = registry()
    check_method_map()
    refusals_by_the_rule = check_control_refusal(reg)
    sealed = check_sealed_payloads(reg)
    refusals = check_refusals(reg)
    session_v2 = check_session_v2(reg)
    absence = check_absence()
    print(
        f"schema ok      {len(list(SCHEMA_DIR.glob('*.json')))} schemas, {refusals_by_the_rule} values "
        "checked against the control-character refusal"
    )
    print(f"sealed         {sealed} values checked against the sealed payloads of selvage/2")
    print(f"refusals       {refusals} values checked against selvage/2's local report vocabulary")
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

    peer = check_peer_corpus()

    for problem in FAILURES:
        print(f"FAIL           {problem}")
    print(
        f"vectors        {len(vectors)} files, {CHECKS} frame checks, "
        f"{assertions} assertion steps"
    )
    print(
        f"peer vectors   {peer['vectors']} files, {peer['frame']} frame, "
        f"{peer['decision']} decision, {peer['checks']} checks, "
        f"{peer['assertions']} assertion steps"
    )
    print(f"absence        {absence}")
    print(f"result         {'FAIL' if FAILURES else 'OK'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
