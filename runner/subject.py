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

**The runner plays the relay.** A decision vector's `start` seats the subject without a socket:
`join` carries the invite (which names the room and whose fragment carries both keys), the
seat the roster shows it under, the roster itself, the session's `keepalive` clock, and — the
one seam — the session keypair the vector wants it to use. Every frame after that is
`deliver`, one sealed frame's bytes, and the subject's answer is a decision about it. The
relay the earlier text said was missing is therefore this runner: it reads the vector's
recipes, seals them and hands them over, and it is the same code that replays a frame vector
so the two layers seal one way.

**A link the client refuses is an answer to `join` and not a failure.** `PROTOCOL.md` §5.1's
partial fragment and §10's version-1-only server are decided before a socket, so there is no
frame to decide about: the subject refuses the `join` itself, in its own words, and seats
nothing. `join_or_refusal` reads that, `run_peer.py` records it, and the vector asserts it with
`expectRefusal`. A subject left unseated is free to be handed another link, which is what lets
one vector hold a refusal and the control leg beside it.
"""

from __future__ import annotations

import json
import os
import select
import shlex
import subprocess
import threading
import time

from dataclasses import dataclass, field

#: How long the runner waits for a subject's answer by default. Every request carries its own
#: deadline; a subject that misses one is a failure with the last report it gave, and not a
#: hang.
DEFAULT_TIMEOUT = 10.0

#: How much of a subject's stderr the runner keeps, from the end, to quote when the subject
#: fails. The rest is read and discarded: a subject's log is its own, and a pipe nobody reads
#: blocks the subject once it fills.
STDERR_TAIL = 16 * 1024

#: The mutations a *subject* must be able to remove, one rule each, and the name a vector
#: declares in its `catches`. `PROTOCOL.md` §13.11's table is the list of rules a conformance
#: test observes; these are the wrong implementations it must fail. They are the counterpart
#: of `sealed.MUTATIONS`, which removes a *receiver's* guard: these remove a client's. The last
#: two are guards on the **link**, which a client reads before any session exists; `LINK_MUTATIONS`
#: names them, and the runner removes those before the `join` rather than after it.
SUBJECT_MUTATIONS = {
    "ignore-roles": "§13.5: apply document content from a key the state gives role `viewer`",
    "ignore-issued": "§13.3: apply a room state that is not above the mark the client holds",
    "announce-once": "§13.1: announce the session key once and wait, with no renewal clock",
    "no-lease": "§13.7: keep a peer's holds for ever instead of expiring them",
    "any-closing": "§13.10: end the session on a closing that verifies, without requiring a "
    "verified state below it",
    "wait-for-ever": "§13.3: stay seated with no state applied and never end",
    "accept-partial-fragment": "§5.1: join a link whose fragment names one of the two keys, "
    "with the other one missing",
    "fall-back-to-version-1": "§2, §10: connect to a server whose reachable `/meta` names no "
    "version at major 2 by speaking `selvage/1` instead of refusing",
}

#: The guards of that table a client has read **before any session exists**, and which a caller
#: therefore has to remove before the `join` that reads its link: §5.1's fragment and §2/§10's
#: version gate are decided about the link itself, so a subject asked for one of these after it
#: is seated could not have refused the link anyway. Every other guard sits in a session and is
#: removed after the join.
LINK_MUTATIONS = frozenset({"accept-partial-fragment", "fall-back-to-version-1"})


class SubjectError(Exception):
    """A subject that will not start, will not answer within its deadline, or answers wrong."""


@dataclass
class Refusal:
    """A link this client refused **locally**, in its own words.

    `PROTOCOL.md` §5.1's fragment rule and §10's no-fallback rule are decided before a socket
    is opened, so a client that holds them has no frame to report the refusal in and answers
    the `join` itself: the subject protocol's `{"ok": false, "error": …}` is that answer, and
    the words are the client's. A decision vector asserts them with `expectRefusal`, which
    asserts the naming rather than the sentence.
    """

    words: str


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

    The members that carry a decision are:

    - `published` counts the frames this client **published** under `PROTOCOL.md` §13's rules —
      its session-key announcements, its document content, its holds messages, and the room
      states and closings a host publishes. §7's sync handshake is counted apart, in
      `handshake`: §13.1's step 6 obliges a client that applies a state committing its own key
      to send a SyncStep1, so a count that folded the two together could not tell a
      republished request from a publication, and vector 151 asserts `published` where a
      conforming client's handshake has already been sent.
    - `handshake` counts the §7 frames the client sent — a SyncStep1 or a SyncStep2 — which are
      requests about convergence rather than §13's publications.
    - `applied` is one `{frame, kind}` entry per binary frame the subject **applied**, in the
      order it applied them, where `frame` is the index of the `deliver` that handed it over.
    - `dropped` is one `{frame, reason}` entry per frame it **refused**, with the reason
      `CANONICAL.md` §6.1 names. A frame that verified and applied nothing — a closing
      delivered to a client holding no state (§13.10) — is in neither list: it was not refused
      and it changed nothing.
    - `holds` maps a key to the paths **that key** holds: it is the receiver's view of its
      peers, not of itself, which is `documents`. The key is any spelling the client has for
      it — the state's canonical public-key spelling, or the key id in hex — and the runner
      resolves a fixture key's name, spelling and id to the one name a vector writes.
    - `listing` is the paths of the last state the client applied, with the paths §5 refuses
      dropped as §13.3 drops them.
    """

    text: dict[str, str] = field(default_factory=dict)
    documents: list[str] = field(default_factory=list)
    applied: list[dict] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    published: int = 0
    handshake: int = 0
    ended: bool = False
    peers: list[dict] = field(default_factory=list)
    holds: dict[str, list[str]] = field(default_factory=dict)
    listing: list[str] = field(default_factory=list)
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
        handshake = value.get("handshake", 0)
        if not isinstance(handshake, int) or isinstance(handshake, bool) or handshake < 0:
            raise SubjectError(f"`handshake` is a count, not {handshake!r}")
        listing = value.get("listing", [])
        if not isinstance(listing, list) or any(not isinstance(p, str) for p in listing):
            raise SubjectError("`listing` is a list of paths")
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
            handshake=handshake,
            ended=value["ended"],
            peers=list(value.get("peers", [])),
            holds={k: list(v) for k, v in holds.items()},
            listing=list(listing),
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
    stderr_tail: bytes = b""
    _stderr_reader: threading.Thread | None = None

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
        # stderr is drained for the life of the process, so that a subject that logs is never
        # blocked writing it, and only its tail is kept for a failure to quote.
        self.stderr_tail = b""
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr, args=(self.process.stderr,), daemon=True
        )
        self._stderr_reader.start()

    def _drain_stderr(self, pipe) -> None:
        try:
            while chunk := os.read(pipe.fileno(), 65536):
                self.stderr_tail = (self.stderr_tail + chunk)[-STDERR_TAIL:]
        except (OSError, ValueError):
            pass  # the pipe is gone; there is nothing left to keep

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
        pipes = [self.process.stdin, self.process.stdout, self.process.stderr]
        if self._stderr_reader is not None:
            # The child is gone, so its stderr reaches EOF and the reader ends. One that is still
            # blocked means something the child spawned holds the pipe open; its descriptor is
            # left to the reader rather than closed under it, where the number could be reused.
            self._stderr_reader.join(timeout=1.0)
            if self._stderr_reader.is_alive():
                pipes.pop()
            self._stderr_reader = None
        for pipe in pipes:
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
                # The stderr reader finishes once the subject's stderr closes; a subject that
                # closed stdout and kept stderr open is not waited for past this bound.
                if self._stderr_reader is not None:
                    self._stderr_reader.join(timeout=1.0)
                code = self.process.poll()
                stderr = self.stderr_tail
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
        reply = self.exchange(command, wait)
        if not reply.get("ok"):
            raise SubjectError(f"the subject refused `{command.get('cmd')}`: {reply.get('error')!r}")
        return reply

    def exchange(self, command: dict, wait: float | None = None) -> dict:
        """Send one command and read one reply, whether the subject accepted it or refused it.

        A refusal is part of the protocol — `{"ok": false, "error": …}` is what a client says
        for a link it will not join with — and every other way a reply can be wrong is still a
        named failure: a subject that answers rubbish, closes its stdout or misses its
        deadline fails the vector rather than being read as a decision.
        """
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
        return reply

    # -- the commands of the protocol ------------------------------------------

    def join(self, invite: str, path: str | None = None, **options: object) -> Report:
        """Seat the subject, and raise when it refused the link instead.

        A refusal is a decision and not a failure, so a caller that has to read one asks for it
        with `join_or_refusal`; this is the same command for a caller that needs a session.
        """
        answer = self.join_or_refusal(invite, path, **options)
        if isinstance(answer, Refusal):
            raise SubjectError(f"the subject refused `join`: {answer.words!r}")
        return answer

    def join_or_refusal(
        self, invite: str, path: str | None = None, **options: object
    ) -> "Report | Refusal":
        """Hand the subject the link it starts from, and read what it did with it.

        Both answers are decisions: a `Report` is a seated subject, and a `Refusal` is a link
        this client will not join with, in its own words, with no session behind it — which is
        what §5.1's partial fragment and §10's version-1-only server are. A subject left
        unseated by one refusal is free to be handed another link, which is how a vector
        carries a refusal and its control leg.

        The invite is the vector's template with `$room`, `$token`, `$room_key` and
        `$host_key` substituted. `offline` says the caller will hand the frames over with
        `deliver` instead of a socket, which is what a decision vector is: the runner is the
        relay, so no invite in this layer addresses a server at all.

        `mutation` is not a member of this command: a guard is removed with `mutate`, and a caller
        that removes one **before** the `join` is removing a guard on the link, which a client
        reads before any session exists (`LINK_MUTATIONS`). `meta` is what `GET /meta` answered
        and `pin` the version this client's own setting pins it to, both of which §2 and §10
        read before a socket; the layer that opens none carries them here.

        `key` names a **fixture entry** — `"guest-1"` — and the runner resolves it to that
        keypair's 32-byte seed and sends it as `session_key`. It is a **test seam and not
        production surface**: `PROTOCOL.md` §13.1 mints the session keypair in memory for the
        connection and never persists it, and a production client has no member that fixes
        one. It exists because a decision vector's delivered state commits a *fixture* key's
        public half, and no protocol lets a host hand a peer a chosen key — so without a way
        to fix the keypair there is no vector that could assert what a client does once a
        state commits it. A client that ignores the member mints its own key and fails those
        vectors, which is the correct outcome for a client a caller has not told to be a
        subject.

        `roster` is the seats the server shows as present, `seat` is this connection's own,
        and `keepalive` is the session's clock (`awareness_renew_ms`, `awareness_expire_ms`,
        `ping_interval_ms`, `room_grace_ms`); all three arrive on `room.created`/
        `room.joined` in a real session and there is no frame here to carry them.
        """
        command: dict = {"cmd": "join", "invite": invite}
        if path is not None:
            command["path"] = path
        command.update({name: value for name, value in options.items() if value is not None})
        reply = self.exchange(command)
        if not reply.get("ok"):
            words = reply.get("error")
            return Refusal(words=words if isinstance(words, str) else json.dumps(words))
        return self._report(reply)

    def deliver(self, frame: bytes) -> Report:
        """One sealed frame's bytes, as the relay would hand them over.

        The frame is a *decision input* and not a message: the subject reads it the way
        `CANONICAL.md` §6.1 says and reports what it did, so the bytes are the vector's and
        not the runner's.
        """
        return self._report(
            self.request({"cmd": "deliver", "frame": frame.hex()})
        )

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
        so here rather than passing the census silently.

        A guard may be asked for at any time: one that sits on the link has to be gone before the
        link is read, and one that sits in a session is removed once there is a session
        (`runner/run_peer.py` does both, from `LINK_MUTATIONS`).
        """
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
