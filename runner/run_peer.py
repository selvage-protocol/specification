#!/usr/bin/env python3
"""Replays the peer corpus: `selvage/2`'s frame layer, with no client and no server.

The wire corpus (`runner/run_vectors.py`) replays transcripts against a running `selvaged`.
This runner has no socket in it: a **frame** vector hands it recipes and frames, and it seals,
signs, verifies and refuses them the way `CANONICAL.md` §6.1 says, in one process, with the
fixture in `vectors/fixture/keys.json` and nothing else.

    python3 runner/run_peer.py                     # every frame vector
    python3 runner/run_peer.py --vector 102        # one of them
    python3 runner/run_peer.py --mutation no-verify   # with a guard removed
    python3 runner/run_peer.py --mutation-census   # what each vector catches

**The decision layer is not runnable here and this runner does not pretend it is.** A
`"kind": "decision"` vector is about what a real client does with a frame it has received —
what it applied, what it dropped and why, what it published, whether it ended — and it needs
two things that do not exist: a subject (a client, `--subject`) and a relay that speaks
`selvage/2` to seat it in a room. Every such vector is reported **not attempted**, with the
reason, and `not attempted` is not `passed`: the summary counts the two separately and a
reader of a green run can see how much of the corpus it did not run.

**The mutation census is what makes the corpus evidence rather than a list of assertions.** A
conforming receiver and a wrong one both pass a vector that asserts nothing, so `--mutation-census`
runs every frame vector twice: once as it stands, where it must pass, and once with the one
guard the vector declares it catches removed, where it must fail. A vector that stays green
under its own mutation is a vector that does not test the rule it names, and the census is the
red run that says so.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys

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

#: What a peer vector may be. A `frame` vector is replayed here; a `decision` one needs a
#: subject and a `selvage/2` relay and is reported as not attempted.
KINDS = ("frame", "decision")

#: The ops a frame vector's steps may use. `test_recipe.py` checks the recipes, and
#: `schema/validate.py` checks these names against its own table, so a step the runner does
#: not know is a failure in both halves rather than a vector that validates and cannot run.
FRAME_OPS = ("seal", "corrupt", "expectVerify", "expectReject", "expectPlaintext",
             "expectListing", "expectHolds", "expectDoc")


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


# --- the decisions this runner does not take ------------------------------------


def decision_reason(vector: dict, has_subject: bool) -> str:
    """Why a decision vector was not attempted, naming both things that are missing.

    Nothing here is a pass. The runner says which half of the corpus it ran, and a green
    summary that hid a whole layer would be worse than a red one.
    """
    missing = []
    if not has_subject:
        missing.append("no subject (`--subject \"my-client --drive\"`)")
    missing.append("no server speaks `selvage/2` to seat a peer in a room")
    return "a decision vector needs " + " and ".join(missing)


def run_subject_probe(command: str, timeout: float) -> str:
    """Start a subject and ask it for a report, so the seam is exercised and named.

    The decision vectors still do not run — they need a `selvage/2` relay as much as a
    subject — but a subject that cannot answer the protocol's own liveness command is a
    failure here rather than a surprise in a later phase.
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
        f"               {report.published} frames published, {report.frames} received, "
        f"{len(report.dropped)} dropped, ended={str(report.ended).lower()}"
    )


# --- the run --------------------------------------------------------------------


def attempt(
    vector: dict, cache: dict[str, Fixture], mutations: frozenset[str], has_subject: bool
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
    if vector["kind"] == "decision":
        outcome.not_attempted = decision_reason(vector, has_subject)
        return outcome
    try:
        outcome.assertions = replay_vector(vector, cache, mutations)
    except (PeerError, SealedError) as error:
        outcome.failure = str(error)
    return outcome


def describe(outcome: Outcome) -> str:
    if outcome.not_attempted is not None:
        return f"not attempted  {outcome.name:<40} {outcome.not_attempted}"
    if outcome.failure is not None:
        return f"FAIL           {outcome.name:<40} {outcome.failure}"
    return f"ok             {outcome.name}"


def run_corpus(vectors: list[dict], mutation: str | None, selected: str | None,
               has_subject: bool) -> list[Outcome]:
    cache: dict[str, Fixture] = {}
    outcomes = []
    for vector in vectors:
        if selected is not None and vector.get("id") != selected:
            continue
        mutations = frozenset({mutation}) if mutation else frozenset()
        outcomes.append(attempt(vector, cache, mutations, has_subject))
    return outcomes


def mutation_census(vectors: list[dict], has_subject: bool) -> tuple[list[str], int]:
    """Runs every vector twice: clean, and with the guard it says it catches removed.

    The positive control is the second half of it: a vector that declares no mutation must
    stay **green under every mutation there is**, because the alternative way to pass a corpus
    of refusals is to refuse everything. A vector that fails one of those is not a positive
    control, whatever its title says.
    """
    report: list[str] = []
    failures = 0
    cache: dict[str, Fixture] = {}
    for vector in vectors:
        name = vector["_file"]
        catches = vector.get("catches")
        if vector.get("kind") != "frame":
            report.append(
                f"not attempted  {name:<40} {decision_reason(vector, has_subject)}"
            )
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
        help="a client to drive over the subject protocol, for a later phase's decision layer",
    )
    parser.add_argument(
        "--subject-timeout", type=float, default=subject.DEFAULT_TIMEOUT, metavar="SECONDS",
    )
    args = parser.parse_args(argv)

    if args.list_mutations:
        print("a receiver's guard, removed by `sealed.Reader`:")
        for name, what in sealed.MUTATIONS.items():
            print(f"  {name:<18} {what}")
        print("a client's guard, removed by the subject (a later phase):")
        for name, what in subject.SUBJECT_MUTATIONS.items():
            print(f"  {name:<18} {what}")
        return 0

    try:
        vectors = load_vectors()
        check_corpus_size(vectors, load_validate_module().EXPECTED_PEER_VECTORS)
    except (CorpusError, SealedError) as error:
        print(f"FAIL   corpus: {error}")
        return 1

    if args.mutation is not None and args.mutation not in sealed.MUTATIONS:
        print(
            f"FAIL   `{args.mutation}` is not a mutation of the frame layer:\n"
            f"       {', '.join(sorted(sealed.MUTATIONS))}",
            file=sys.stderr,
        )
        return 2

    if args.subject is not None:
        try:
            print(run_subject_probe(args.subject, args.subject_timeout))
        except PeerError as error:
            print(f"FAIL   subject: {error}")
            return 1

    if args.mutation_census:
        report, failures = mutation_census(vectors, args.subject is not None)
        for line in report:
            print(line)
        frames = sum(1 for vector in vectors if vector.get("kind") == "frame")
        print(
            f"census summary {frames} frame vectors, {len(sealed.MUTATIONS)} mutations, "
            f"{failures} failed"
        )
        return 1 if failures else 0

    outcomes = run_corpus(vectors, args.mutation, args.vector, args.subject is not None)
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
    subject_note = args.subject if args.subject is not None else "no subject named"
    print(
        f"summary        {len(outcomes)} files, {frames} frame vectors, {assertions} "
        f"assertion steps, {passed} passed, {failed} failed, {skipped} not attempted "
        f"({subject_note})"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
