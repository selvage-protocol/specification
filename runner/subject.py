#!/usr/bin/env python3
"""The subject protocol: a client implementation the runner spawns and speaks to.

The peer layer's decision half is about what a client does with a frame it has already
received — whether it applied it, dropped it and why, what it published, whether it ended —
and none of that is on a socket. So the corpus drives a **subject**: a client, in any
language, that answers one JSON object per line on stdout for one it is handed on stdin. It
generalises a pair of programs the project already has
(`reference_server/crates/harness/examples/interop_peer.rs` and
`vscode_client/test/helpers/interop_peer.ts`), and it is what makes the decision layer run
against *anyone's* client rather than this project's:

    python3 runner/run_peer.py --subject "my-client --drive"
    python3 runner/run_peer.py --subject "node ./subject.js"
    python3 runner/run_peer.py --subject ./target/debug/subject

**Four rules, each from a defect the project has paid for.** The subject answers and never
speaks first, so a vector cannot read a state that arrived for a different reason. The runner
does the waiting, so a subject that stops answering fails the vector with its last report
rather than hanging the run. Every assertion is on a decision and not on internals —
`dropped` is a list of `(frame, reason)` the subject must be able to produce, because a client
that cannot say *why* it dropped a frame cannot be held to `PROTOCOL.md` §13.2. And the
subject is named by a command rather than a language, which is the seam a stranger's client
comes through.

**Nothing here is run against a vector in this phase**, and the runner says so rather than
pretending otherwise: a decision vector needs a relay that speaks `selvage/2` to seat the peer
in a room, and no server does yet. What is here is the protocol, the deadline handling and the
report's shape, driven against a scripted subject by `runner/test_runner.py`.
"""

from __future__ import annotations

import json
import os
import select
import shlex
import subprocess
import time

from dataclasses import dataclass, field

#: How long the runner waits for a subject's answer by default. Every request carries its own
#: deadline; a subject that misses one is a failure with the last report it gave, and not a
#: hang.
DEFAULT_TIMEOUT = 10.0

#: The mutations a *subject* must be able to remove, one rule each, and the name a vector
#: declares in its `catches`. `PROTOCOL.md` §13.11's table is the list of rules a conformance
#: test observes; these are the wrong implementations it must fail. They are the counterpart
#: of `sealed.MUTATIONS`, which removes a *receiver's* guard: these remove a client's.
SUBJECT_MUTATIONS = {
    "ignore-roles": "§13.5: apply document content from a key the state gives role `viewer`",
    "ignore-issued": "§13.3: apply a room state that is not above the mark the client holds",
    "announce-once": "§13.1: announce the session key once and wait, with no renewal clock",
    "no-lease": "§13.7: keep a peer's holds for ever instead of expiring them",
    "any-closing": "§13.10: end the session on a closing that verifies, without requiring a "
    "verified state below it",
    "wait-for-ever": "§13.3: stay seated with no state applied and never end",
}


class SubjectError(Exception):
    """A subject that will not start, will not answer within its deadline, or answers wrong."""


@dataclass
class Dropped:
    """One frame a subject refused, and the reason it reports — `CANONICAL.md` §6.1's
    vocabulary, which is the whole of what a client owes the rule it is held to."""

    frame: int
    reason: str

    @classmethod
    def from_json(cls, value: object) -> "Dropped":
        if not isinstance(value, dict):
            raise SubjectError(f"a `dropped` entry is an object, not {value!r}")
        frame = value.get("frame")
        reason = value.get("reason")
        if not isinstance(frame, int) or isinstance(frame, bool) or frame < 0:
            raise SubjectError(f"a `dropped` entry needs a frame index, not {frame!r}")
        if not isinstance(reason, str) or not reason:
            raise SubjectError(f"a `dropped` entry needs a reason, not {reason!r}")
        return cls(frame, reason)


@dataclass
class Report:
    """What a subject says about itself when it is asked.

    Every member is one of `PROTOCOL.md` §13.11's observables and none is an internal: the
    text it holds per path, the paths it has open, the frames it **applied**, the frames it
    **dropped** with the reason §6.1 names, how many frames it **published**, whether it
    **ended** its session, and what it holds each peer to. `frames` counts every binary frame
    the subject was handed, in order, which is what a `dropped` entry's index is into.
    """

    text: dict[str, str] = field(default_factory=dict)
    documents: list[str] = field(default_factory=list)
    applied: list[dict] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    published: int = 0
    ended: bool = False
    peers: list[dict] = field(default_factory=list)
    holds: dict[str, list[str]] = field(default_factory=dict)
    frames: int = 0
    mutation: str | None = None

    @classmethod
    def from_json(cls, value: object) -> "Report":
        """Read a report, refusing a shape the vector could not be held to.

        A subject that answers with the wrong members is a named failure and not a `KeyError`
        several steps later: the report is the decision channel, so its shape is part of what
        the corpus can assert.
        """
        if not isinstance(value, dict):
            raise SubjectError(f"a report is an object, not {value!r}")
        for member in ("text", "documents", "applied", "dropped", "published", "ended"):
            if member not in value:
                raise SubjectError(f"a report has no {member!r}: {json.dumps(value)[:200]}")
        text = value["text"]
        if not isinstance(text, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in text.items()
        ):
            raise SubjectError("`text` maps a path to the string the subject holds there")
        documents = value["documents"]
        if not isinstance(documents, list) or any(not isinstance(d, str) for d in documents):
            raise SubjectError("`documents` is a list of paths")
        applied = value["applied"]
        if not isinstance(applied, list):
            raise SubjectError("`applied` is a list")
        published = value["published"]
        if not isinstance(published, int) or isinstance(published, bool) or published < 0:
            raise SubjectError(f"`published` is a count, not {published!r}")
        if not isinstance(value["ended"], bool):
            raise SubjectError(f"`ended` is a boolean, not {value['ended']!r}")
        duration = value.get("frames", 0)
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
            raise SubjectError(f"`frames` is a count, not {duration!r}")
        mutation = value.get("mutation")
        if mutation is not None and mutation not in SUBJECT_MUTATIONS:
            raise SubjectError(f"the subject reports a mutation nothing defines: {mutation!r}")
        holds = value.get("holds", {})
        if not isinstance(holds, dict) or any(
            not isinstance(k, str) or not isinstance(v, list) for k, v in holds.items()
        ):
            raise SubjectError("`holds` maps a key to the set of paths that key holds")
        return cls(
            text=dict(text),
            documents=list(documents),
            applied=list(applied),
            dropped=[Dropped.from_json(entry) for entry in value["dropped"]],
            published=published,
            ended=value["ended"],
            peers=list(value.get("peers", [])),
            holds={k: list(v) for k, v in holds.items()},
            frames=duration,
            mutation=mutation,
        )


@dataclass
class Subject:
    """A subject process, and the line protocol the runner drives it with."""

    command: list[str]
    timeout: float = DEFAULT_TIMEOUT
    cwd: str | None = None
    process: subprocess.Popen | None = None
    buffer: bytes = b""
    last: Report | None = None
    asked: int = 0

    @classmethod
    def from_command(cls, command: str, **kwargs) -> "Subject":
        """A subject named by a command line, which is the seam a stranger's client comes
        through: `--subject "node ./subject.js"`."""
        return cls(shlex.split(command), **kwargs)

    # -- the process -----------------------------------------------------------

    def start(self) -> None:
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise SubjectError(
                f"the subject {self.command!r} could not be started: {error}"
            ) from error

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass  # the child's own end is closed; nothing here is left to do
        self.process = None

    # -- the line protocol -----------------------------------------------------

    def _deadline(self, wait: float) -> float:
        return time.monotonic() + wait

    def _line(self, wait: float) -> bytes:
        """One line from the subject's stdout, or a failure naming what it did instead.

        The read is bounded by the deadline and not by the child's behaviour: a subject that
        writes half a line and stalls must fail the vector, not hang the run.
        """
        assert self.process is not None and self.process.stdout is not None
        stdout = self.process.stdout
        end = self._deadline(wait)
        while b"\n" not in self.buffer:
            left = end - time.monotonic()
            if left <= 0:
                raise SubjectError(
                    f"the subject did not answer within {wait:g}s"
                    + (f"; its last report was {json.dumps(self.last.__dict__)}" if self.last else "")
                )
            ready, _, _ = select.select([stdout], [], [], left)
            if not ready:
                continue
            chunk = os.read(stdout.fileno(), 65536)
            if not chunk:
                code = self.process.poll()
                stderr = b""
                if self.process.stderr is not None:
                    stderr = self.process.stderr.read() or b""
                raise SubjectError(
                    f"the subject closed its stdout"
                    + (f" with {code}" if code is not None else "")
                    + (f": {stderr.decode('utf-8', 'replace').strip()}" if stderr else "")
                )
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return line

    def request(self, command: dict, wait: float | None = None) -> dict:
        """Send one command and read one reply. The runner does the waiting, always."""
        if self.process is None or self.process.stdin is None:
            raise SubjectError("the subject is not running")
        self.asked += 1
        payload = json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            self.process.stdin.write(payload)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise SubjectError(f"the subject is not reading its input: {error}") from error
        line = self._line(self.timeout if wait is None else wait)
        try:
            reply = json.loads(line)
        except json.JSONDecodeError as error:
            raise SubjectError(f"the subject answered something that is not JSON: {line!r}") from error
        if not isinstance(reply, dict):
            raise SubjectError(f"a reply is a JSON object, not {line!r}")
        if not reply.get("ok"):
            raise SubjectError(f"the subject refused `{command.get('cmd')}`: {reply.get('error')!r}")
        return reply

    # -- the commands of the protocol ------------------------------------------

    def join(self, invite: str, path: str | None = None) -> Report:
        command: dict = {"cmd": "join", "invite": invite}
        if path is not None:
            command["path"] = path
        return self._report(self.request(command))

    def insert(self, path: str, index: int, text: str) -> Report:
        return self._report(
            self.request({"cmd": "insert", "path": path, "index": index, "text": text})
        )

    def announce(self, path: str) -> Report:
        """A hold: the subject's whole set, replaced, announced as a client announces one."""
        return self._report(self.request({"cmd": "announce", "path": path}))

    def report(self) -> Report:
        """Ask what the subject holds. This is the decision channel, and the only one."""
        return self._report(self.request({"cmd": "report"}))

    def mutate(self, name: str) -> Report:
        """Remove one guard, for the mutation census. A subject that cannot remove one says
        so here rather than passing the census silently."""
        if name not in SUBJECT_MUTATIONS:
            raise SubjectError(f"no mutation is named {name!r}")
        return self._report(self.request({"cmd": "mutate", "name": name}))

    def quit(self) -> None:
        if self.process is None:
            return
        try:
            self.request({"cmd": "quit"})
        except SubjectError:
            pass  # a subject that exits without answering has still done what was asked
        self.stop()

    def _report(self, reply: dict) -> Report:
        self.last = Report.from_json(reply.get("report"))
        return self.last
