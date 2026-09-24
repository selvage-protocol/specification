#!/usr/bin/env python3
"""Replays the peer corpus: `selvage/2`'s frame layer, and its decision layer with a client.

The wire corpus (`runner/run_vectors.py`) replays transcripts against a running `selvaged`.
This runner has no socket in it: a **frame** vector hands it recipes and frames, and it seals,
signs, verifies and refuses them the way `CANONICAL.md` §6.1 says, in one process, with the
fixture in `vectors/fixture/keys.json` and nothing else. A **decision** vector hands the same
recipes to a *subject* — a real client, named by a command — and reads what it did with them:

    python3 runner/run_peer.py                     # every frame vector
    python3 runner/run_peer.py --vector 102        # one of them
    python3 runner/run_peer.py --mutation no-verify   # with a guard removed
    python3 runner/run_peer.py --mutation-census   # what each vector catches
    python3 runner/run_peer.py --subject "my-client --drive"

**In the decision layer the runner is the relay.** A `"kind": "decision"` vector is about
what a real client does with a frame it has already received — what it applied, what it
dropped and why, what it published, whether it ended — and the frames it would receive on a
socket are the vector's own `deliver` steps, sealed here and handed over through the subject
protocol (`runner/subject.py`). `start` seats it with the invite, the fixture session keypair,
the roster, the session's clock, what `/meta` answered and any version it is pinned to;
`expectSubject` is the decision channel. So a decision vector needs one thing and this runner
names it: a subject.

**A link is refused before a socket, and that is a decision too.** §5.1's partial fragment and
§10's version-1-only server are decided about the link itself, so a client refuses the `join`
in its own words and seats nothing; `expectRefusal` is the step that asserts what those words
carry, and the subject stays free for the vector's next `start`. A guard on the link
(`subject.LINK_MUTATIONS`) is removed before the `join` for the same reason: it has to be gone
before the link is read.

**The mutation census is what makes the corpus evidence rather than a list of assertions.** A
conforming receiver and a wrong one both pass a vector that asserts nothing, so `--mutation-census`
runs every vector twice: once as it stands, where it must pass, and once with the one
guard the vector declares it catches removed, where it must fail. A vector that stays green
under its own mutation is a vector that does not test the rule it names, and the census is the
red run that says so. A decision vector's guard is the *subject's*, so that half of the census
needs a subject and reports the frame vectors apart while it waits for one.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import time

from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import sealed  # noqa: E402  (the path insert above makes it importable)
import subject  # noqa: E402
from sealed import Envelope, Fixture, SealedError, Verdict  # noqa: E402

from cryptography.exceptions import InvalidTag  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"
PEER_DIR = ROOT / "vectors" / "peer"
FIXTURE_DIR = ROOT / "vectors"

#: What a peer vector may be. A `frame` vector is replayed by the receiver in `runner/sealed.py`;
#: a `decision` one is driven against a subject, which is a real client named by a command.
KINDS = ("frame", "decision")

#: The ops a frame vector's steps may use. `test_recipe.py` checks the recipes, and
#: `schema/validate.py` checks these names against its own table, so a step the runner does
#: not know is a failure in both halves rather than a vector that validates and cannot run.
FRAME_OPS = ("seal", "corrupt", "expectVerify", "expectReject", "expectPlaintext",
             "expectListing", "expectHolds", "expectDoc")

#: The ops a decision vector's steps may use, pinned against the same table.
DECISION_OPS = ("start", "stop", "deliver", "wait", "expectSubject", "expectRefusal")

#: The ops of that layer that assert something about the subject: the report channel, and the
#: local refusal a link is decided about in. The census reads this table, so a vector that goes
#: red under its own mutation in either of them is a caught guard, and one that goes red
#: anywhere else is a failure of the harness.
DECISION_ASSERTION_OPS = ("expectSubject", "expectRefusal")

#: The invite's token. A decision vector never reaches a server, so the token is a constant
#: whose only job is to be the string `PROTOCOL.md` §13.10 says a rejoin keeps.
DECISION_TOKEN = "t-corpus-decision"

#: The seat the subject is seated under. The vectors' states label the subject's own key with
#: a seat of their own choosing; a `peer_id` is the host's belief about a seat (§13.4) and not
#: a binding, so the two disagreeing is a shape §13.4 says is normal.
DECISION_SEAT = "p-subject"

#: How often an `expectSubject` reads the report while it waits for a predicate that has not
#: held yet. The deadline is what bounds the wait; this only decides how often it looks.
POLL_SECONDS = 0.02


class PeerError(Exception):
    """One step of a peer vector did not hold."""


class CorpusError(Exception):
    """The corpus is not the one the pins describe, so nothing is attempted."""


@dataclass
class Outcome:
    """What happened to one vector: run and held, run and failed, or not attempted."""

    name: str
    id: str
    kind: str
    assertions: int = 0
    failure: str | None = None
    not_attempted: str | None = None
    catches: str | None = None

    @property
    def attempted(self) -> bool:
        return self.not_attempted is None


# --- the corpus on disk ---------------------------------------------------------


def peer_dir() -> pathlib.Path:
    """Where the peer vectors are read from; `SELVAGE_PEER_VECTORS` overrides it."""
    return pathlib.Path(os.environ.get("SELVAGE_PEER_VECTORS", PEER_DIR))


def load_vectors() -> list[dict]:
    vectors = []
    for path in sorted(peer_dir().glob("*.json")):
        try:
            vector = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            raise CorpusError(f"{path.name} is not JSON: {error}") from error
        vector["_file"] = path.name
        vectors.append(vector)
    return sorted(vectors, key=lambda vector: vector.get("id", ""))


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "selvage_schema_validate", SCHEMA_DIR / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as error:
        print(
            f"schema/validate.py needs jsonschema and referencing: {error}\n"
            "install them with `pip install jsonschema referencing`.",
            file=sys.stderr,
        )
        raise SystemExit(2) from error
    return module


def check_corpus_size(vectors: list[dict], expected: int) -> None:
    """Fails when the directory does not hold the corpus the pin describes.

    The same argument as the wire layer's: the replay checks whatever it finds, so a deleted
    vector replays green on less. `expected` is `schema/validate.py`'s
    `EXPECTED_PEER_VECTORS` — the count has one home.
    """
    if len(vectors) != expected:
        raise CorpusError(
            f"the directory holds {len(vectors)} peer vectors, and this suite pins "
            f"{expected}: adding or removing one is a deliberate edit"
        )


def check_vector(vector: dict) -> None:
    """The cheap structural claims, before anything is sealed."""
    name = vector["_file"]
    if vector.get("selvage") != "selvage/2" or vector.get("canonical") != "SJ-C/1":
        raise CorpusError(
            f"{name} is bound to {vector.get('selvage')} / {vector.get('canonical')}, but "
            "this runner speaks selvage/2 / SJ-C/1"
        )
    if vector.get("layer") != "peer":
        raise CorpusError(f"{name} does not declare `\"layer\": \"peer\"`")
    if vector.get("kind") not in KINDS:
        raise CorpusError(f"{name} does not declare a kind of {KINDS}")
    if not isinstance(vector.get("steps"), list) or not vector["steps"]:
        raise CorpusError(f"{name} has no steps")


def fixture_for(vector: dict, cache: dict[str, Fixture]) -> Fixture:
    """The fixture a vector names, loaded once."""
    name = vector.get("fixture")
    if not isinstance(name, str) or not name:
        raise CorpusError(f"{vector['_file']} names no `fixture`")
    if name not in cache:
        cache[name] = Fixture(FIXTURE_DIR / name)
    return cache[name]


# --- the frame layer's steps ----------------------------------------------------


def _hex_text(raw: bytes) -> str:
    return " ".join(f"{byte:02x}" for byte in raw)


def _bytes_of(step: dict, member: str) -> bytes:
    text = step[member]
    if not isinstance(text, str):
        raise PeerError(f"`{member}` is hex text, not {text!r}")
    try:
        return bytes.fromhex(text.replace(" ", ""))
    except ValueError as error:
        raise PeerError(f"`{member}` is not hex: {error}") from error


def _same_bytes(where: str, step: dict, raw: bytes) -> None:
    """A vector that carries bytes claims they are these bytes.

    Where the frame is derivable — every `seal` step, and every `corrupt` step derived from
    one — this is `docs/studies/peer-corpus.md` §5.1's rule: the `hex` is a checked cache and
    not the source of truth, so a recipe that drifts from the bytes it claims is a red run and
    not a quietly different frame. A conforming implementation with a signature that is not
    these 64 bytes — Safari's Ed25519 randomises — is held to the same frame everywhere but
    the signature, and to a signature that verifies (`NOTES.md` §B.31).
    """
    want = _bytes_of(step, "hex")
    if want != raw:
        raise PeerError(
            f"{where}: the recipe produces other bytes than the vector carries:\n"
            f"  vector: {step['hex']}\n  sealed: {_hex_text(raw)}"
        )


def _corrupt(where: str, raw: bytes, step: dict) -> bytes:
    """The deliberate corruption: this is the one place a vector's bytes are not derivable,
    and the corruption itself is what is re-derived."""
    if "xor" in step:
        at = step.get("at")
        if not isinstance(at, int) or isinstance(at, bool) or not 0 <= at < len(raw):
            raise PeerError(f"{where}: `at` must name a byte of the {len(raw)}-byte frame")
        out = bytearray(raw)
        out[at] ^= step["xor"]
        return bytes(out)
    if "truncate" in step:
        count = step["truncate"]
        if not isinstance(count, int) or isinstance(count, bool) or not 0 < count < len(raw):
            raise PeerError(f"{where}: `truncate` must leave some of the frame")
        return raw[: len(raw) - count]
    if "append" in step:
        return raw + _bytes_of(step, "append")
    raise PeerError(f"{where}: `corrupt` needs `xor`, `truncate` or `append`")


def _frame(where: str, frames: dict[str, tuple], step: dict, member: str = "frame") -> tuple:
    name = step.get(member)
    if not isinstance(name, str) or name not in frames:
        raise PeerError(
            f"{where}: no frame named {name!r}; the vector has sealed {sorted(frames)}"
        )
    return frames[name]


def _refuses(verdict: Verdict) -> str:
    return verdict.reason if verdict.reason is not None else str(verdict)


def replay_frame(fixture: Fixture, vector: dict, mutations: frozenset[str]) -> int:
    """Replays one frame vector, returning its assertion steps.

    The receiver is one conforming receiver from the first step to the last, so a vector is a
    sequence of decisions and not a list of independent ones: an accepted state is the key set
    and the mark the frames after it are read against.
    """
    reader = sealed.Reader(fixture, mutations)
    frames: dict[str, tuple[Envelope | None, bytes]] = {}
    assertions = 0
    for index, step in enumerate(vector["steps"]):
        where = f"step {index} (`{step.get('op')}`)"
        op = step.get("op")
        if op == "seal":
            envelope = sealed.seal(fixture, step["recipe"])
            raw = envelope.bytes()
            _same_bytes(where, step, raw)
            frames[step["frame"]] = (envelope, raw)
        elif op == "corrupt":
            _, source = _frame(where, frames, step)
            raw = _corrupt(where, source, step)
            _same_bytes(where, step, raw)
            try:
                envelope = Envelope.parse(raw, ignore_trailing=True)
            except SealedError:
                # A frame that is deliberately not an envelope has no fields to read; the
                # verdict is the whole of what is asserted about it.
                envelope = None
            frames[step["as"]] = (envelope, raw)
        elif op == "expectVerify":
            assertions += 1
            _, raw = _frame(where, frames, step)
            verdict = reader.read(raw)
            if not verdict.ok:
                raise PeerError(
                    f"{where}: the receiver refused the frame "
                    f"`{step['frame']}` with `{_refuses(verdict)}`"
                )
        elif op == "expectReject":
            assertions += 1
            _, raw = _frame(where, frames, step)
            verdict = reader.read(raw)
            if verdict.ok:
                raise PeerError(
                    f"{where}: the receiver applied the frame `{step['frame']}`, and the "
                    f"vector claims it is refused `{step['reason']}`"
                )
            if verdict.reason != step["reason"]:
                raise PeerError(
                    f"{where}: the receiver refused `{step['frame']}` with "
                    f"`{verdict.reason}`, and the vector claims `{step['reason']}`"
                )
        elif op == "expectPlaintext":
            assertions += 1
            envelope, _ = _frame(where, frames, step)
            if envelope is None:
                raise PeerError(f"{where}: the frame `{step['frame']}` is not an envelope")
            try:
                plaintext = sealed.opens(fixture, envelope)
            except InvalidTag as error:
                raise PeerError(
                    f"{where}: the frame `{step['frame']}` does not open under the frame key"
                ) from error
            if "signed_by" in step:
                key = fixture.key(step["signed_by"])
                if not sealed.authentic(fixture, envelope, key):
                    raise PeerError(
                        f"{where}: the frame `{step['frame']}`'s signature does not verify "
                        f"against `{step['signed_by']}`"
                    )
            if "plaintext" in step and _bytes_of(step, "plaintext") != plaintext:
                raise PeerError(
                    f"{where}: the frame opens to {_hex_text(plaintext)}, and the vector "
                    f"claims {step['plaintext']}"
                )
            if "payload" in step and json.loads(plaintext) != step["payload"]:
                raise PeerError(
                    f"{where}: the frame opens to {plaintext.decode('utf-8', 'replace')}, and "
                    f"the vector claims {json.dumps(step['payload'], ensure_ascii=False)}"
                )
        elif op == "expectListing":
            assertions += 1
            if reader.listing != step["listing"]:
                raise PeerError(
                    f"{where}: the receiver's listing is {json.dumps(reader.listing)}, and "
                    f"the vector claims {json.dumps(step['listing'])}"
                )
        elif op == "expectHolds":
            assertions += 1
            key_id = fixture.key(step["sign"]).hex_id
            held = reader.holds.get(key_id, [])
            if held != step["holds"]:
                raise PeerError(
                    f"{where}: the receiver holds {json.dumps(held)} for `{step['sign']}`, "
                    f"and the vector claims {json.dumps(step['holds'])}"
                )
        elif op == "expectDoc":
            assertions += 1
            actual = reader.replica.text_at(step["path"])
            if actual != step["text"]:
                raise PeerError(
                    f"{where}: the receiver holds {actual!r} in {step['path']}, and the "
                    f"vector claims {step['text']!r}"
                )
        else:
            raise PeerError(f"{where}: `{op}` is not a step this runner knows")
    return assertions


def replay_vector(vector: dict, cache: dict[str, Fixture], mutations: frozenset[str]) -> int:
    """Replays one vector whose layer this runner can run."""
    fixture = fixture_for(vector, cache)
    if vector["kind"] == "decision":
        raise PeerError("a decision vector is not a frame vector")
    return replay_frame(fixture, vector, mutations)


# --- the decision layer: the runner plays the relay -----------------------------


def decision_reason(has_subject: bool) -> str:
    """Why a decision vector was not attempted.

    Nothing here is a pass. The runner names which half of the corpus it ran, and a green
    summary that hid a whole layer would be worse than a red one.
    """
    if not has_subject:
        return 'a decision vector needs a subject (`--subject "my-client --drive"`)'
    return ""


def run_subject_probe(command: str, timeout: float) -> str:
    """Start a subject and ask it for a report, so the seam is exercised and named.

    The decision vectors drive the same seam a moment later and more thoroughly; this is the
    one line that says the command answered at all, before a vector's first step could fail
    for a reason that looks like a decision.
    """
    peer = subject.Subject.from_command(command, timeout=timeout)
    peer.start()
    try:
        report = peer.report()
    except subject.SubjectError as error:
        peer.stop()
        raise PeerError(f"the subject {command!r} did not answer: {error}") from error
    finally:
        peer.stop()
    return (
        f"subject        {command}\n"
        f"               {report.published} frames published, {report.handshake} handshake "
        f"frames, {report.frames} received, {len(report.dropped)} dropped, "
        f"ended={str(report.ended).lower()}"
    )


def invite_of(fixture: Fixture, step: dict) -> str:
    """The invite a `start` hands a subject: the vector's template, substituted.

    `$room_key` and `$host_key` are the fixture's two keys in the fragment's own encoding
    (`PROTOCOL.md` §5.1), which is what makes a decision vector possible at all: the two
    values are what a client derives the frame key from and verifies a state against, and no
    link to a server exists for this layer to carry.
    """
    template = step.get("invite")
    if not isinstance(template, str) or not template:
        raise PeerError("`start` needs an `invite`")
    values = {
        "$room": fixture.room_id,
        "$token": DECISION_TOKEN,
        "$room_key": sealed.b64url(fixture.room_key),
        "$host_key": sealed.b64url(fixture.host.public),
    }
    invite = template
    # Longest name first: `$room` is a prefix of `$room_key`, so substituting it first would
    # leave the room id inside a key's value and the invite would carry a fragment that spells
    # no key at all.
    for name, value in sorted(values.items(), key=lambda item: -len(item[0])):
        invite = invite.replace(name, value)
    if "$" in invite:
        raise PeerError(f"`invite` names a value nothing substitutes: {invite}")
    return invite


def roster_of(vector: dict) -> list[str]:
    """The seats the server shows as present, taken from the vector's own states.

    The runner plays the relay and the roster is the relay's: the seats a delivered state
    labels are the seats it has, so a state's `host` entry is seated and §13.8's host-away
    clock stays disarmed. A vector that wants that clock armed carries the `peer.left` its
    fixture list names; nothing here invents a seat the vector's frames do not name.
    """
    seats: list[str] = []
    for step in vector["steps"]:
        if step.get("op") != "deliver":
            continue
        payload = (step.get("recipe") or {}).get("payload")
        peers = payload.get("peers") if isinstance(payload, dict) else None
        if not isinstance(peers, dict):
            continue
        for entry in peers.values():
            seat = entry.get("peer_id") if isinstance(entry, dict) else None
            if isinstance(seat, str) and seat not in seats:
                seats.append(seat)
    return seats


def session_key_of(fixture: Fixture, step: dict, where: str) -> str | None:
    """The seed a `start`'s `key` names, resolved from the fixture.

    The one test seam of this layer, and it is not production surface: `PROTOCOL.md` §13.1
    mints the session keypair in memory for the connection and never persists it, so a
    production client has no member that fixes one. It is here because a decision vector's
    delivered state commits a *fixture* key's public half, and nothing in the protocol lets a
    host hand a peer a chosen key: without a way to fix the keypair there is no vector that
    can assert what a client does once a state commits it. `runner/subject.py`'s `join`
    documents the seam where a client reads it.
    """
    name = step.get("key")
    if name is None:
        return None
    key = fixture.keys.get(name) if isinstance(name, str) else None
    if key is None:
        raise PeerError(f"{where}: `key` names {name!r}, which the fixture does not have")
    if key.private is None:
        raise PeerError(f"{where}: {name} has no private half to be a session key with")
    return key.private.hex()


def meta_of(step: dict, where: str) -> dict | None:
    """What `GET /meta` answered, for the layer that opens no socket.

    `PROTOCOL.md` §2 and §10 read one member of the body before a socket is opened, so a vector
    that pins the rule carries it: `wire_versions`, the versions the server says it accepts. A
    `start` with no `meta` is a `/meta` that could not be read, which is not an answer about
    versions and is not what a client refuses on.
    """
    meta = step.get("meta")
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise PeerError(f"{where}: `meta` is the body `GET /meta` answered")
    unknown = set(meta) - {"wire_versions"}
    if unknown:
        raise PeerError(f"{where}: `meta` names {sorted(unknown)}, which nothing here reads")
    versions = meta.get("wire_versions")
    if not isinstance(versions, list) or any(not isinstance(one, str) for one in versions):
        raise PeerError(f"{where}: `meta.wire_versions` is the list the server advertises")
    return {"wire_versions": list(versions)}


def pin_of(step: dict, where: str) -> str | None:
    """The wire version this client's own setting pins it to, or `None` for a client that has
    pinned nothing (`PROTOCOL.md` §2)."""
    pin = step.get("pin")
    if pin is None:
        return None
    if pin not in ("selvage/1", "selvage/2"):
        raise PeerError(f"{where}: `pin` is one of the two wire versions, not {pin!r}")
    return pin


def scenario_of(vector: dict) -> dict:
    """The scenario's known members, checked rather than trusted.

    `keepalive` is the session's own clock, which a real session reads from `room.created` and
    this layer has no frame to carry. `relay_withholds` names the frames the relay does not
    forward — and the runner's frames *are* the vector's `deliver` steps, so the withholding is
    already in which frames the vector hands over; the member is read here so that a scenario
    naming something this runner does not know is a failure rather than a silent pass.
    """
    scenario = vector.get("scenario") or {}
    if not isinstance(scenario, dict):
        raise PeerError(f"{vector['_file']}: `scenario` is an object")
    unknown = set(scenario) - {"keepalive", "relay_withholds"}
    if unknown:
        raise PeerError(
            f"{vector['_file']}: `scenario` names {sorted(unknown)}, which nothing here reads"
        )
    keepalive = scenario.get("keepalive")
    if not isinstance(keepalive, dict) or not keepalive:
        raise PeerError(f"{vector['_file']}: `scenario.keepalive` is the session's clock")
    withheld = scenario.get("relay_withholds", [])
    if not isinstance(withheld, list) or any(kind not in ("kind4",) for kind in withheld):
        raise PeerError(
            f"{vector['_file']}: `relay_withholds` names kinds, and {withheld!r} is not one "
            "this runner knows"
        )
    return scenario


#: The report members a vector may name in an exact comparison or in `at_least` or `frozen`.
#: `holds` is read through `holds_of`, so it is not one of these.
REPORT_MEMBERS = (
    "applied",
    "dropped",
    "published",
    "handshake",
    "ended",
    "listing",
    "text",
    "documents",
    "frames",
)


def report_member(member: str, report: subject.Report) -> object:
    """One report member as a vector writes it.

    A name the report does not carry is a failure with a name rather than an `AttributeError`:
    `frozen` and `at_least` name their members in the vector, and a typo there must be a red
    vector and not a traceback that stops the run.
    """
    if member not in REPORT_MEMBERS:
        raise PeerError(
            f"`{member}` is not a report member; the report carries "
            f"{', '.join(REPORT_MEMBERS)}, and `holds` is read under the fixture's names"
        )
    if member == "applied":
        return [dict(entry) for entry in report.applied]
    if member == "dropped":
        return [{"frame": entry.frame, "reason": entry.reason} for entry in report.dropped]
    return getattr(report, member)


def holds_of(fixture: Fixture, report: subject.Report) -> dict[str, list[str]]:
    """The report's `holds`, under the names a vector writes.

    A subject reports the key it has — the state's canonical spelling, or the id in hex — and
    the runner resolves a fixture key's name, spelling and id to the one name. The paths are
    compared as a set: `CANONICAL.md` §2.7 gives a holds array no order.
    """
    names: dict[str, str] = {}
    for name, key in fixture.keys.items():
        names[name] = name
        names[key.hex_id] = name
        names[sealed.b64url(key.public)] = name
    return {
        names.get(raw, raw): sorted(paths) for raw, paths in report.holds.items()
    }


def unmet(step: dict, report: subject.Report, fixture: Fixture) -> list[str]:
    """Every member an `expectSubject` asserts that the report does not satisfy.

    Each failure carries both values, because a poll that times out prints the last one and a
    reader has to be able to see what the subject actually held.
    """
    failures: list[str] = []
    for member in ("applied", "dropped", "published", "handshake", "ended", "listing",
                   "text"):
        if member not in step:
            continue
        actual, want = report_member(member, report), step[member]
        if actual != want:
            failures.append(
                f"`{member}` is {json.dumps(want)} in the vector and "
                f"{json.dumps(actual)} in the report"
            )
    if "holds" in step:
        actual = holds_of(fixture, report)
        for key, paths in step["holds"].items():
            if actual.get(key, []) != sorted(paths):
                failures.append(
                    f"`holds[{key}]` is {json.dumps(sorted(paths))} in the vector and "
                    f"{json.dumps(actual.get(key, []))} in the report"
                )
    for member, bound in (step.get("at_least") or {}).items():
        actual = report_member(member, report)
        if not isinstance(actual, int) or isinstance(actual, bool) or actual < bound:
            failures.append(
                f"`{member}` must be at least {bound} and is {json.dumps(actual)}"
            )
    return failures


@dataclass
class DecisionRun:
    """One decision vector, driven against one subject, with the runner as its relay."""

    vector: dict
    fixture: Fixture
    command: str
    timeout: float
    mutation: str | None = None
    run: subject.Subject | None = None
    frames: int = 0
    assertions: int = 0
    #: The link the last `start` was refused with, in the client's own words, and whether the
    #: subject is seated. A refusal seats nobody, so the subject is free for the next `start`,
    #: which is what lets one vector hold a refusal and its control leg.
    refusal: str | None = None
    seated: bool = False

    def drive(self) -> int:
        """Runs the vector's steps in order, returning its assertion steps."""
        self.run = subject.Subject.from_command(self.command, timeout=self.timeout)
        self.run.start()
        try:
            for index, step in enumerate(self.vector["steps"]):
                self.step(index, step)
        finally:
            if self.run is not None:
                self.run.quit()
            self.run = None
        return self.assertions

    def step(self, index: int, step: dict) -> None:
        where = f"step {index} (`{step.get('op')}`)"
        handler = {
            "start": self.start,
            "stop": self.stop,
            "deliver": self.deliver,
            "wait": self.wait,
            "expectSubject": self.expect,
            "expectRefusal": self.expect_refusal,
        }.get(step.get("op"))
        if handler is None:
            raise PeerError(f"{where}: `{step.get('op')}` is not a step of a decision vector")
        handler(where, step)

    def start(self, where: str, step: dict) -> None:
        """Hand the subject the link it starts from: the invite, the clock, the roster, the
        session keypair, and the two things §2 and §10 read before a socket.

        The answer is either a seating or the client's own words for a link it refuses
        (`PROTOCOL.md` §5.1's partial fragment, §10's version-1-only server), and the second is
        a decision the vector asserts with `expectRefusal` rather than a failure of the run.

        The guard this run removes is named **before** the join when it sits on the link, which
        is where a client reads it, and after the join when it sits in a session; a subject that
        cannot remove it refuses the command either way, which is a failure of the harness and
        not a red run.
        """
        if self.run is None:
            raise PeerError(f"{where}: the subject is not running")
        scenario = scenario_of(self.vector)
        on_the_link = self.mutation in subject.LINK_MUTATIONS
        if on_the_link:
            self.run.mutate(self.mutation)
        answer = self.run.join_or_refusal(
            invite_of(self.fixture, step),
            path=step.get("path"),
            offline=True,
            keepalive=scenario["keepalive"],
            seat=DECISION_SEAT,
            roster=roster_of(self.vector),
            session_key=session_key_of(self.fixture, step, where),
            relay_withholds=scenario.get("relay_withholds") or None,
            meta=meta_of(step, where),
            pin=pin_of(step, where),
        )
        if isinstance(answer, subject.Refusal):
            self.refusal = answer.words
            self.seated = False
            return
        self.refusal = None
        self.seated = True
        if self.mutation is not None and not on_the_link:
            self.run.mutate(self.mutation)

    def stop(self, where: str, step: dict) -> None:
        if self.run is None:
            raise PeerError(f"{where}: the subject is not running")
        self.run.quit()
        self.run = None

    def deliver(self, where: str, step: dict) -> None:
        """Seal the recipe and hand the frame over, as the relay would."""
        if self.run is None:
            raise PeerError(f"{where}: the subject is not running")
        if not self.seated:
            raise PeerError(
                f"{where}: the subject is not seated, so there is nothing to hand a frame to "
                f"({self.refusal!r})"
            )
        raw = sealed.seal(self.fixture, step["recipe"]).bytes()
        _same_bytes(where, step, raw)
        report = self.run.deliver(raw)
        self.frames += 1
        if report.frames != self.frames:
            raise PeerError(
                f"{where}: the subject counts {report.frames} frames received and this "
                f"step is the {self.frames}th it was handed"
            )

    def wait(self, where: str, step: dict) -> None:
        """A timer the vector is testing. The runner does the waiting, always."""
        milliseconds = step.get("ms")
        if not isinstance(milliseconds, int) or isinstance(milliseconds, bool) or milliseconds < 0:
            raise PeerError(f"{where}: `wait` needs `ms`")
        time.sleep(milliseconds / 1000)

    def expect(self, where: str, step: dict) -> None:
        """One `expectSubject`: the exact members, the bounds, and the frozen window.

        `within_ms` bounds two waits and both are the vector's own number: the poll for the
        members the step asserts, which may not hold yet, and — when `frozen` is there — one
        window in which the named members must not move. The window starts where the poll
        ends, so a step whose members already hold waits the whole of it.
        """
        self.assertions += 1
        if not self.seated:
            raise PeerError(
                f"{where}: the subject is not seated, so it has no report to read "
                f"({self.refusal!r})"
            )
        within = step.get("within_ms")
        deadline = None if within is None else time.monotonic() + within / 1000
        report = self.settle(where, step, deadline)
        if "frozen" in step:
            self.freeze(where, step, report, deadline)

    def expect_refusal(self, where: str, step: dict) -> None:
        """One `expectRefusal`: the subject refused the link, and its own words name what the
        vector says they must.

        `PROTOCOL.md` §2 and §5.1 leave the sentence to the client — "the client's to word,
        naming the server and the version it would need" — so what a vector can hold an
        implementation to is the **naming**: every string `names` lists is in the refusal. A
        subject that joined the link instead has answered the one question the step asks, and
        its report is printed so a reader sees what it did instead.
        """
        self.assertions += 1
        names = step.get("names")
        if not isinstance(names, list) or not names:
            raise PeerError(f"{where}: `names` is the list of strings the refusal must carry")
        if self.seated:
            raise PeerError(
                f"{where}: the subject joined the link instead of refusing it, and reports "
                f"{self.run.report()!r} — a client that can speak the wire's version refuses "
                f"this link locally, before a socket"
            )
        if self.refusal is None:
            raise PeerError(f"{where}: the subject has not been handed a link to refuse")
        absent = [wanted for wanted in names if wanted not in self.refusal]
        if absent:
            raise PeerError(
                f"{where}: the refusal must name {json.dumps(absent)} and it says "
                f"{self.refusal!r}"
            )

    def settle(self, where: str, step: dict, deadline: float | None) -> subject.Report:
        """The report the step asserts, polled to its deadline or refused at once."""
        while True:
            report = self.report(where)
            failures = unmet(step, report, self.fixture)
            if not failures:
                return report
            if deadline is None or time.monotonic() >= deadline:
                raise PeerError(f"{where}: " + "; ".join(failures))
            time.sleep(POLL_SECONDS)

    def freeze(self, where: str, step: dict, before: subject.Report,
               deadline: float | None) -> None:
        """Names the report's members must not move over the window `within_ms` gives."""
        if deadline is None:
            raise PeerError(f"{where}: `frozen` needs the `within_ms` it is frozen over")
        while time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
        after = self.report(where)
        moved = [
            member
            for member in step["frozen"]
            if report_member(member, after) != report_member(member, before)
        ]
        if moved:
            raise PeerError(
                f"{where}: "
                + "; ".join(
                    f"`{member}` moved over the window from "
                    f"{json.dumps(report_member(member, before))} to "
                    f"{json.dumps(report_member(member, after))}"
                    for member in moved
                )
            )

    def report(self, where: str) -> subject.Report:
        if self.run is None:
            raise PeerError(f"{where}: the subject is not running")
        return self.run.report()


# --- the run --------------------------------------------------------------------


def attempt(
    vector: dict,
    cache: dict[str, Fixture],
    mutations: frozenset[str],
    driver: "Driver",
) -> Outcome:
    outcome = Outcome(
        name=vector["_file"],
        id=vector.get("id", "?"),
        kind=vector.get("kind", "?"),
        catches=vector.get("catches"),
    )
    try:
        check_vector(vector)
    except CorpusError as error:
        outcome.failure = str(error)
        return outcome
    try:
        if vector["kind"] == "decision":
            if driver.command is None:
                outcome.not_attempted = decision_reason(False)
                return outcome
            fixture = fixture_for(vector, cache)
            outcome.assertions = DecisionRun(
                vector=vector,
                fixture=fixture,
                command=driver.command,
                timeout=driver.timeout,
                mutation=driver.mutation,
            ).drive()
        else:
            outcome.assertions = replay_vector(vector, cache, mutations)
    except (PeerError, SealedError, subject.SubjectError) as error:
        if isinstance(error, subject.SubjectError) and driver.command is not None \
                and vector["kind"] == "decision":
            outcome.failure = f"the subject {driver.command!r} failed: {error}"
        else:
            outcome.failure = str(error)
    return outcome


@dataclass
class Driver:
    """What drives the decision layer: a subject's command line, and which guard is removed.

    Both halves of this runner take it. A frame vector ignores the command — it needs no
    client — and a decision vector cannot be attempted without one, which is the whole of
    what `not attempted` means here.
    """

    command: str | None = None
    timeout: float = subject.DEFAULT_TIMEOUT
    mutation: str | None = None


def describe(outcome: Outcome) -> str:
    if outcome.not_attempted is not None:
        return f"not attempted  {outcome.name:<40} {outcome.not_attempted}"
    if outcome.failure is not None:
        return f"FAIL           {outcome.name:<40} {outcome.failure}"
    return f"ok             {outcome.name}"


def run_corpus(vectors: list[dict], mutation: str | None, selected: str | None,
               driver: Driver) -> list[Outcome]:
    cache: dict[str, Fixture] = {}
    outcomes = []
    for vector in vectors:
        if selected is not None and vector.get("id") != selected:
            continue
        mutations = frozenset({mutation}) if mutation else frozenset()
        outcomes.append(attempt(vector, cache, mutations, driver))
    return outcomes


def mutation_census(vectors: list[dict], driver: Driver) -> tuple[list[str], int]:
    """Runs every vector twice: clean, and with the guard it says it catches removed.

    The positive control is the second half of it: a vector that declares no mutation must
    stay **green under every mutation there is**, because the alternative way to pass a corpus
    of refusals is to refuse everything. A vector that fails one of those is not a positive
    control, whatever its title says.

    The two layers have two tables and neither may declare the other's: a frame vector catches
    a *receiver's* guard (`sealed.MUTATIONS`), and a decision vector catches a *client's*
    (`subject.SUBJECT_MUTATIONS`). A decision vector's census needs a subject, so without one
    it is reported not attempted rather than counted either way.
    """
    report: list[str] = []
    failures = 0
    cache: dict[str, Fixture] = {}
    for vector in vectors:
        name = vector["_file"]
        catches = vector.get("catches")
        if vector.get("kind") == "decision":
            lines, failures = decision_census(vector, cache, driver, failures)
            report.extend(lines)
            continue
        try:
            check_vector(vector)
        except CorpusError as error:
            failures += 1
            report.append(f"FAIL           {name:<40} {error}")
            continue
        if catches is not None and catches not in sealed.MUTATIONS:
            if catches in subject.SUBJECT_MUTATIONS:
                failures += 1
                report.append(
                    f"FAIL           {name:<40} declares `{catches}`, which removes a "
                    "client's rule: a frame vector can only catch a receiver's"
                )
            else:
                failures += 1
                report.append(
                    f"FAIL           {name:<40} declares `{catches}`, which no mutation "
                    "table names"
                )
            continue
        try:
            replay_vector(vector, cache, frozenset())
        except (PeerError, SealedError) as error:
            failures += 1
            report.append(f"FAIL           {name:<40} without a mutation: {error}")
            continue
        if catches is None:
            wrong = []
            for mutation in sealed.MUTATIONS:
                try:
                    replay_vector(vector, cache, frozenset({mutation}))
                except (PeerError, SealedError):
                    wrong.append(mutation)
            if wrong:
                failures += 1
                report.append(
                    f"FAIL           {name:<40} declares no mutation, and fails "
                    f"{len(wrong)} of them: {', '.join(sorted(wrong))} — it is not a "
                    "positive control"
                )
            else:
                report.append(
                    f"census         {name:<40} green under all {len(sealed.MUTATIONS)} "
                    "mutations, as the positive control must be"
                )
            continue
        try:
            replay_vector(vector, cache, frozenset({catches}))
        except (PeerError, SealedError) as error:
            report.append(
                f"census         {name:<40} red under `{catches}`: "
                f"{str(error).splitlines()[0]}"
            )
        else:
            failures += 1
            report.append(
                f"FAIL           {name:<40} stays green under `{catches}`, the mutation it "
                "declares it catches"
            )
    return report, failures


def decision_census(
    vector: dict, cache: dict[str, Fixture], driver: Driver, failures: int
) -> tuple[list[str], int]:
    """One decision vector, run clean and then under the guard it declares it catches."""
    name = vector["_file"]
    catches = vector.get("catches")
    if driver.command is None:
        return [f"not attempted  {name:<40} {decision_reason(False)}"], failures
    try:
        check_vector(vector)
    except CorpusError as error:
        return [f"FAIL           {name:<40} {error}"], failures + 1
    if catches is None:
        return [
            f"FAIL           {name:<40} declares no mutation, and every decision vector is a "
            "rule vector with one to catch"
        ], failures + 1
    if catches not in subject.SUBJECT_MUTATIONS:
        why = (
            "which removes a receiver's rule: a decision vector can only catch a client's"
            if catches in sealed.MUTATIONS
            else "which no mutation table names"
        )
        return [f"FAIL           {name:<40} declares `{catches}`, {why}"], failures + 1
    # The clean run is clean whatever `--mutation` said: the census is about each vector's own
    # declared guard, and a driver that already carries one would make the positive half run
    # mutated.
    clean = Driver(command=driver.command, timeout=driver.timeout, mutation=None)
    result = attempt(vector, cache, frozenset(), clean)
    if result.not_attempted is not None:
        return [f"not attempted  {name:<40} {result.not_attempted}"], failures
    if result.failure is not None:
        return [f"FAIL           {name:<40} without a mutation: {result.failure}"], failures + 1
    under = Driver(command=driver.command, timeout=driver.timeout, mutation=catches)
    red = attempt(vector, cache, frozenset(), under).failure
    if red is None:
        return [
            f"FAIL           {name:<40} stays green under `{catches}`, the mutation it "
            "declares it catches"
        ], failures + 1
    if not any(f"(`{op}`)" in red for op in DECISION_ASSERTION_OPS):
        # A subject that refused to remove the guard, a recipe that drifted from its bytes or a
        # step the runner would not take is a failure of the harness, and counting one of them
        # as the red run would let a vector with no guard at all pass the census.
        return [
            f"FAIL           {name:<40} failed under `{catches}` before any expectation: {red}"
        ], failures + 1
    return [f"census         {name:<40} red under `{catches}`, green without it"], failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vector", metavar="ID", help="replay one vector, by its id")
    parser.add_argument(
        "--mutation",
        metavar="NAME",
        help="replay with one guard removed (see --list-mutations)",
    )
    parser.add_argument(
        "--mutation-census",
        action="store_true",
        help="run every vector clean and under the mutation it declares it catches",
    )
    parser.add_argument(
        "--list-mutations",
        action="store_true",
        help="print the guard each mutation removes",
    )
    parser.add_argument(
        "--subject",
        metavar="COMMAND",
        help="a client to drive over the subject protocol, which the decision vectors need",
    )
    parser.add_argument(
        "--subject-timeout", type=float, default=subject.DEFAULT_TIMEOUT, metavar="SECONDS",
    )
    args = parser.parse_args(argv)

    if args.list_mutations:
        print("a receiver's guard, removed by `sealed.Reader`:")
        for name, what in sealed.MUTATIONS.items():
            print(f"  {name:<18} {what}")
        print("a client's guard, removed by the subject:")
        for name, what in subject.SUBJECT_MUTATIONS.items():
            print(f"  {name:<18} {what}")
        return 0

    try:
        vectors = load_vectors()
        check_corpus_size(vectors, load_validate_module().EXPECTED_PEER_VECTORS)
    except (CorpusError, SealedError) as error:
        print(f"FAIL   corpus: {error}")
        return 1

    if args.mutation is not None and args.mutation not in sealed.MUTATIONS \
            and args.mutation not in subject.SUBJECT_MUTATIONS:
        print(
            f"FAIL   `{args.mutation}` is not a mutation of either layer:\n"
            f"       a receiver's: {', '.join(sorted(sealed.MUTATIONS))}\n"
            f"       a client's:  {', '.join(sorted(subject.SUBJECT_MUTATIONS))}",
            file=sys.stderr,
        )
        return 2

    if args.subject is not None:
        try:
            print(run_subject_probe(args.subject, args.subject_timeout))
        except PeerError as error:
            print(f"FAIL   subject: {error}")
            return 1

    # A mutation belongs to one layer: `sealed.MUTATIONS` removes a receiver's guard and
    # `subject.SUBJECT_MUTATIONS` a client's, and neither table names the other's. Handing one
    # name to both would fail every vector of the layer that does not know it.
    driver = Driver(
        command=args.subject,
        timeout=args.subject_timeout,
        mutation=args.mutation if args.mutation in subject.SUBJECT_MUTATIONS else None,
    )
    receiver_mutation = args.mutation if args.mutation in sealed.MUTATIONS else None

    if args.mutation_census:
        report, failures = mutation_census(vectors, driver)
        for line in report:
            print(line)
        frames = sum(1 for vector in vectors if vector.get("kind") == "frame")
        decisions = sum(1 for vector in vectors if vector.get("kind") == "decision")
        print(
            f"census summary {frames} frame vectors, {len(sealed.MUTATIONS)} receiver "
            f"mutations, {decisions} decision vectors, {len(subject.SUBJECT_MUTATIONS)} "
            f"client mutations, {failures} failed"
        )
        return 1 if failures else 0

    outcomes = run_corpus(vectors, receiver_mutation, args.vector, driver)
    if not outcomes:
        print(f"FAIL   no peer vector has the id {args.vector!r}", file=sys.stderr)
        return 2
    for outcome in outcomes:
        print(describe(outcome))
    passed = sum(1 for outcome in outcomes if outcome.attempted and outcome.failure is None)
    failed = sum(1 for outcome in outcomes if outcome.failure is not None)
    skipped = sum(1 for outcome in outcomes if not outcome.attempted)
    assertions = sum(outcome.assertions for outcome in outcomes)
    frames = sum(1 for outcome in outcomes if outcome.kind == "frame")
    decisions = sum(1 for outcome in outcomes if outcome.kind == "decision")
    subject_note = args.subject if args.subject is not None else "no subject named"
    print(
        f"summary        {len(outcomes)} files, {frames} frame vectors, {decisions} "
        f"decision vectors, {assertions} assertion steps, {passed} passed, {failed} failed, "
        f"{skipped} not attempted ({subject_note})"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
