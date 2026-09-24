#!/usr/bin/env python3
"""Replays `vectors/*.json` against a running `selvaged`, and asserts the bytes.

The vectors are a transcript of real exchanges, bound to a wire version and to the
canonical form of `CANONICAL.md`. This is the language-neutral reader of them: it
opens WebSocket connections, sends exactly the bytes a step names, and compares what
comes back. Nothing here reads Rust; the only thing it needs from the reference
implementation is a running server, whose path it takes from `SELVAGE_SELVAGED`.

**The corpus is `selvage/1`'s**, and the server this runner spawns is started with
`--serve-version-1-only` for that reason: two of the thirty-six vectors are claims about a
server that seats that version alone (001's `/meta`, 005's refused `selvage/2` hello), and a
server that seats both answers them differently. `replay` refuses any vector bound to another
version, so the flag is right for every vector this file can run.

Run it from this directory:

    export SELVAGE_SELVAGED=/path/to/reference_server/target/debug/selvaged
    python3 runner/run_vectors.py

`--schema-only` runs the frame-level checks alone, which need no server and are what
CI can run while the reference server is private.

**Two layers, and this runner is the wire one's.** A vector declares its `layer`:
`vectors/*.json` is `wire` and its subject is the server, which is what this file replays;
`vectors/peer/*.json` is `peer` and its subject is a client, which is `runner/run_peer.py`'s.
`--layer all` replays the wire layer and prints one line per peer vector saying it was **not
attempted** and where it is run, so a corpus with two layers cannot be read as one that all
passed; `--layer peer` refuses rather than attempting nothing and exiting zero. The peer
directory is not a glob this file reaches, so nothing about the wire replay changes.

The comparison rules are the ones `README.md` lists: member sets are exact in both
directions, `peers` is a set, and a text frame is compared twice — structurally, so a
failure names the member, and then as whole bytes, because the vector is written in
the canonical form. A `$name` placeholder binds the first value it sees and must be
that value again; `$_` matches anything and is never remembered.

An `expect` step reads the connection's next frame and compares it, so a vector that omits
an expectation leaves that connection one frame ahead, and the failure lands later on a frame
that belongs to an earlier moment. A failure therefore names the connection, the step's place
in the transcript and how many frames it had read; and when the transcript stops reading,
whatever each connection still holds that no step reads fails the vector outright — a server
that sends more than the transcript reads is chattier than the vector allows, and a passing
run reads every frame it is sent.
"""

from __future__ import annotations

import argparse
import asyncio
import http.client
import importlib.util
import json
import os
import pathlib
import re
import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import yprotocols  # noqa: E402  (the path insert above makes it importable)

try:
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    WEBSOCKETS_ERROR = None
except ImportError as error:  # a missing dependency, not a failure of a vector
    connect = None

    class ConnectionClosed(Exception):
        """Unreachable: replay is refused before a socket is opened."""

    WEBSOCKETS_ERROR = error

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"
VECTOR_DIR = ROOT / "vectors"

SERVER_ENV = "SELVAGE_SELVAGED"
DEFAULT_GRACE_MS = 30_000
FRAME_TIMEOUT = 10.0
# The audit that names a frame no step reads runs when the transcript stops reading, so the
# frame it is meant to catch is normally already on the socket; a healthy connection has nothing
# to send and pays only this window.
DRAIN_TIMEOUT = 0.1
ANY = "$_"
# The members whose prose promises no order: a comparison matches each as a multiset and
# reconciles the order before the bytes are built (`CANONICAL.md` §2.7). `documents` is not
# here — PROTOCOL.md §6.2 promises it first-opened order, and the comparison holds it to that.
UNORDERED = frozenset({"peers", "capabilities", "wire_versions", "roles"})
# The byte form of `CANONICAL.md`: ascending member names, no whitespace, and no `\u`
# escape for a printable character.
CANONICAL = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}

class Mismatch(Exception):
    """A frame, a status or a replica does not hold what the vector claims."""


class ServerError(Exception):
    """The server could not be started."""


class ReplayError(Exception):
    """One step of a vector did not hold."""


# --- placeholders and comparison ---------------------------------------------


@dataclass
class Bindings:
    """What the vector's placeholders are bound to.

    `named` holds the ones that mean one thing — a room id, a token, a peer id — and
    must be the same every time they are seen. `matched` holds every placeholder's
    value in the order the frame's members appear, which is what the byte comparison
    needs: it rebuilds the vector's frame with the wire's own values.
    """

    named: dict[str, str] = field(default_factory=dict)
    matched: list[str] = field(default_factory=list)

    def clone(self) -> "Bindings":
        return Bindings(dict(self.named), list(self.matched))

    def bind(self, name: str, value: str) -> None:
        self.matched.append(value)
        if name == ANY:
            return
        if name in self.named:
            if self.named[name] != value:
                raise Mismatch(
                    f"`{name}` was {self.named[name]!r} earlier and is {value!r} here"
                )
        else:
            self.named[name] = value


def matches(expected: object, actual: object, bindings: Bindings) -> None:
    """Matches one vector value against one on the wire, binding placeholders.

    Objects must have the same member set in both directions: a version-locked vector
    is checking that no member was silently added or renamed.
    """
    if isinstance(expected, str) and expected.startswith("$"):
        if not isinstance(actual, str):
            raise Mismatch(
                f"`{expected}` is a placeholder but the wire has {json.dumps(actual)}"
            )
        bindings.bind(expected, actual)
        return
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise Mismatch(
                f"expected an object, the wire has {json.dumps(actual)}"
            )
        for member, value in expected.items():
            if member not in actual:
                raise Mismatch(f"missing member `{member}` in {json.dumps(actual)}")
            if member in UNORDERED:
                matches_unordered(value, actual[member], bindings)
            else:
                matches(value, actual[member], bindings)
        for member in actual:
            if member not in expected:
                raise Mismatch(f"the wire has an unexpected member `{member}`")
        return
    if isinstance(expected, list):
        if not isinstance(actual, list):
            raise Mismatch(f"expected a list, the wire has {json.dumps(actual)}")
        if len(expected) != len(actual):
            raise Mismatch(
                f"expected {len(expected)} members, the wire has {len(actual)}"
            )
        for left, right in zip(expected, actual):
            matches(left, right, bindings)
        return
    if isinstance(expected, bool) != isinstance(actual, bool) or expected != actual:
        raise Mismatch(
            f"expected {json.dumps(expected)}, the wire has {json.dumps(actual)}"
        )


def matches_unordered(want: object, have: object, bindings: Bindings) -> None:
    """Matches an array the prose leaves unordered: as a multiset, then in the wire's order.

    The members are matched first, each against a distinct member of the wire, so the frame
    is checked member by member. The vector's list is then put into the order the wire sent,
    because the byte comparison after this one would otherwise see the order and reject a
    frame that is the same frame: for these arrays the order is not part of any claim, and
    the server's order is not something the vector can name.
    """
    if not isinstance(want, list) or not isinstance(have, list):
        raise Mismatch(f"expected a list, the wire has {json.dumps(have)}")
    # The placeholders already bound before this array; the re-order below rebuilds only the
    # ones this array contributes, and it must not drop the ones in front of it.
    base = len(bindings.matched)
    if len(want) != len(have):
        raise Mismatch(
            f"expected {len(want)} members, the wire has {len(have)}"
        )
    used = [False] * len(have)
    # Where each of the vector's members was found on the wire.
    at: list[int] = []
    for value in want:
        found: tuple[int, Bindings] | None = None
        for index, candidate in enumerate(have):
            if used[index]:
                continue
            trial = bindings.clone()
            try:
                matches(value, candidate, trial)
            except Mismatch:
                continue
            found = (index, trial)
            break
        if found is None:
            raise Mismatch(
                f"no member in {json.dumps(have)} matches {json.dumps(value)}"
            )
        index, trial = found
        used[index] = True
        at.append(index)
        bindings.named = trial.named
        bindings.matched = trial.matched

    order = sorted(range(len(want)), key=at.__getitem__)
    if order != list(range(len(want))):
        want[:] = [want[position] for position in order]
        # Bind again, in the order the frame will be written in. The placeholders bound
        # before this array are kept; only this array's are rebuilt.
        trial = bindings.clone()
        del trial.matched[base:]
        matches(want, have, trial)
        bindings.named = trial.named
        bindings.matched = trial.matched


def canonical_text(value: object) -> str:
    """The value in the byte form a conforming frame is written in."""
    return json.dumps(value, **CANONICAL)


def expected_bytes(text: str, matched: list[str]) -> str:
    """The vector's frame with every placeholder replaced by the value it matched."""
    out: list[str] = []
    rest = text
    index = 0
    while True:
        at = rest.find('"$')
        if at < 0:
            break
        out.append(rest[:at])
        span = rest[at + 1 :]
        end = span.find('"')
        if end < 0:
            raise Mismatch("a placeholder is not closed")
        if index >= len(matched):
            raise Mismatch("a placeholder has no matched value")
        out.append(json.dumps(matched[index], ensure_ascii=False))
        rest = span[end + 1 :]
        index += 1
    out.append(rest)
    return "".join(out)


def check_text(actual: str, expected: str, bindings: Bindings) -> None:
    """Checks one text frame, structurally and then byte for byte."""
    try:
        want = json.loads(expected)
    except json.JSONDecodeError as error:
        raise Mismatch(f"the vector frame is not JSON: {error}") from error
    try:
        have = json.loads(actual)
    except json.JSONDecodeError as error:
        raise Mismatch(f"the wire frame is not JSON: {error}: {actual}") from error
    # The vector's own bytes are checked before matching, which may reorder a `peers`
    # array: a vector's claim about the wire is only readable if the vector is written the
    # way a frame is written (CANONICAL.md §2).
    form = canonical_text(want)
    if form != expected:
        raise Mismatch(
            "the vector frame is not in the canonical form of CANONICAL.md \u00a72:\n"
            f"  vector: {expected}\n  form:   {form}"
        )
    trial = bindings.clone()
    trial.matched.clear()
    try:
        matches(want, have, trial)
    except Mismatch as problem:
        raise Mismatch(
            f"frame does not match:\n  vector: {expected}\n  wire:   {actual}\n  {problem}"
        ) from problem
    # Matching puts a `peers` array into the wire's order; the frame is then written the
    # way the wire wrote it, so the bytes compared are the ones that frame has and not the
    # order the vector happened to list its peers in.
    wanted = expected_bytes(canonical_text(want), trial.matched)
    bindings.named = trial.named
    bindings.matched = trial.matched
    if actual != wanted:
        raise Mismatch(
            "the wire frame is not the canonical bytes the vector claims:\n"
            f"  vector: {wanted}\n  wire:   {actual}"
        )


def hex_bytes(hex_text: str) -> bytes:
    try:
        return bytes(int(byte, 16) for byte in hex_text.split())
    except ValueError as error:
        raise Mismatch(f"`{hex_text}` is not hex: {error}") from error


def bytes_hex(data: bytes) -> str:
    return " ".join(f"{byte:02x}" for byte in data)


def check_frame_spec(spec: dict, frame: bytes) -> None:
    """Checks a binary frame against a description: the framing is fixed, the payload not."""
    try:
        message = yprotocols.decode_message(frame)
    except yprotocols.DecodeError as error:
        raise Mismatch(
            f"the frame is not y-protocols: {error}: {bytes_hex(frame)}"
        ) from error
    if spec["message_type"] == 0:
        if not isinstance(message, yprotocols.SyncMessage):
            raise Mismatch(f"expected a sync frame, the frame is {message}")
        if "sync_type" in spec and spec["sync_type"] != message.subtype:
            raise Mismatch(
                f"expected sync_type {spec['sync_type']}, the frame has {message.subtype}"
            )
        return
    if not isinstance(message, yprotocols.AwarenessMessage):
        raise Mismatch(f"expected an awareness frame, the frame is {message}")
    want = spec.get("awareness")
    if want is None:
        raise Mismatch("an awareness frame needs an awareness spec")
    by_client = {entry.client: entry for entry in message.entries}
    for client in want["clients"]:
        entry = by_client.get(client)
        if entry is None:
            raise Mismatch(f"no awareness state for client {client}")
        if entry.clock != want["clock"]:
            raise Mismatch(f"client {client} has clock {entry.clock}, not {want['clock']}")
        state = json.loads(entry.state)
        if state != want["state"]:
            raise Mismatch(f"client {client} published {state}, not {want['state']}")


# --- connections --------------------------------------------------------------


@dataclass
class Incoming:
    kind: str  # text, binary, closed
    text: str = ""
    data: bytes = b""
    code: int = 0


@dataclass
class Peer:
    name: str
    ws: object
    timeout: float = FRAME_TIMEOUT
    doc: yprotocols.Replica = field(default_factory=yprotocols.Replica)
    frames: int = 0

    def wrong(self, what: str) -> str:
        return f"on `{self.name}`, after {self.frames} frames: {what}"

    async def incoming(self) -> Incoming:
        try:
            message = await asyncio.wait_for(self.ws.recv(), self.timeout)
        except TimeoutError as error:
            raise Mismatch(
                self.wrong(f"no frame within {self.timeout:g}s")
            ) from error
        except ConnectionClosed as closed:
            code = closed.code if closed.code is not None else 1005
            return Incoming("closed", text=closed.reason or "", code=code)
        self.frames += 1
        if isinstance(message, str):
            return Incoming("text", text=message)
        return Incoming("binary", data=message)

    async def text(self, expected: str) -> str:
        incoming = await self.incoming()
        if incoming.kind == "text":
            return incoming.text
        if incoming.kind == "binary":
            raise Mismatch(self.wrong("a binary frame where a text frame was expected"))
        raise Mismatch(
            self.wrong(f"closed with {incoming.code} ({incoming.text}) before {expected}")
        )

    async def binary(self) -> bytes:
        incoming = await self.incoming()
        if incoming.kind == "binary":
            return incoming.data
        if incoming.kind == "text":
            raise Mismatch(
                self.wrong(f"a text frame where binary was expected: {incoming.text}")
            )
        raise Mismatch(
            self.wrong(f"closed with {incoming.code} ({incoming.text}) before the binary frame")
        )

    async def closed(self) -> int:
        incoming = await self.incoming()
        if incoming.kind == "closed":
            return incoming.code
        if incoming.kind == "text":
            raise Mismatch(
                self.wrong(f"a text frame where a close was expected: {incoming.text}")
            )
        raise Mismatch(self.wrong("a binary frame where a close was expected"))

    async def stop(self) -> None:
        try:
            await self.ws.close()
        except Exception:  # the socket is already gone; nothing left to close
            pass

    async def drain(self, window: float) -> list[Incoming]:
        """The frames still on the connection when the transcript stopped reading.

        A frame that arrives here is one no step of this run has read; whether a step still to
        run would have read it is what `unread_frames` decides. A close ends the stream and is
        not a frame.
        """
        unread: list[Incoming] = []
        while True:
            try:
                message = await asyncio.wait_for(self.ws.recv(), window)
            except (TimeoutError, ConnectionClosed):
                return unread
            except Exception:  # the socket is gone; nothing left to read
                return unread
            if isinstance(message, str):
                unread.append(Incoming("text", text=message))
            else:
                unread.append(Incoming("binary", data=message))


@dataclass
class Session:
    peers: dict[str, Peer] = field(default_factory=dict)
    bindings: Bindings = field(default_factory=Bindings)
    body: str | None = None
    status: int | None = None

    def peer(self, step: dict) -> Peer:
        name = step.get("conn")
        if name is None:
            raise Mismatch("this step needs a conn")
        if name not in self.peers:
            raise Mismatch(f"no connection named {name}")
        return self.peers[name]

    def fill(self, target: str) -> str:
        """Substitutes the bound names into a URL, longest name first."""
        out = target
        for name in sorted(self.bindings.named, key=len, reverse=True):
            out = out.replace(name, self.bindings.named[name])
        return out

    async def stop(self) -> None:
        for peer in self.peers.values():
            await peer.stop()

    async def drain(self, window: float = DRAIN_TIMEOUT) -> dict[str, list[Incoming]]:
        """What each connection holds once the transcript has stopped reading."""
        unread: dict[str, list[Incoming]] = {}
        for name, peer in self.peers.items():
            frames = await peer.drain(window)
            if frames:
                unread[name] = frames
        return unread


def describe_frame(incoming: Incoming) -> str:
    """A frame as one line of a report."""
    if incoming.kind == "text":
        return incoming.text
    return f"binary {bytes_hex(incoming.data)}"


def describe_unread(unread: dict[str, list[Incoming]]) -> list[str]:
    """One line per connection: what it holds that no step of the transcript reads.

    A frame here is the shape a vector missing an expectation has, and the thing that shifts
    every later read on that connection. Naming the connection and the frame is what points at
    the omission, rather than at the failure it causes several steps later.
    """
    report = []
    for name, frames in unread.items():
        held = "frame" if len(frames) == 1 else "frames"
        shown = ", ".join(describe_frame(frame) for frame in frames)
        report.append(
            f"`{name}` holds {len(frames)} {held} the transcript does not read: {shown}"
        )
    return report


def describe_failure(
    session: Session,
    total: int,
    index: int,
    step: dict,
    before: int | None,
    error: Exception,
) -> str:
    """A failed step, placed in the transcript and in its connection's stream.

    Every step reads the frame at the head of its connection's queue, so a queue that is one
    frame behind fails on a frame from an earlier moment. The step's position and the number
    of frames that connection had already read are what make that visible.
    """
    where = f"step {index} of {total} `{step.get('op')}`"
    name = step.get("conn")
    peer = session.peers.get(name) if name is not None else None
    if peer is None or before is None:
        return f"{where}: {error}"
    read = peer.frames - before
    reading = (
        f"reading frame {peer.frames} ({before} read before it)"
        if read
        else f"after {before} frames read"
    )
    return f"{where} on `{name}`, {reading}: {error}"


# The steps that take a frame off a connection. Everything else either sends, asks the doc,
# or waits, so it reads nothing that a leftover frame could belong to.
READING_OPS = frozenset({"expect", "expectBinary", "expectClose"})

# Every step op this runner knows. `schema/validate.py` holds the same set as `WIRE_OPS` and
# `runner/test_runner.py` compares the two, because the halves once disagreed: `apply` was in the
# validator's pass-through list and this file raises on an op it does not know, so a step could
# validate there and fail the replay here with nothing saying which half was wrong. `apply` is a
# *member* of `sendBinary`/`expectBinary` and never a step of its own.
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


def pending_reads(steps: list[dict], start: int) -> dict[str, int]:
    """How many frames each connection's steps from `start` on would read."""
    pending: dict[str, int] = {}
    for step in steps[start:]:
        if step.get("op") in READING_OPS:
            name = step.get("conn")
            if name is not None:
                pending[name] = pending.get(name, 0) + 1
    return pending


def unread_frames(
    held: dict[str, list[Incoming]], pending: dict[str, int]
) -> dict[str, list[Incoming]]:
    """The frames a connection holds that no step of the transcript reads.

    A reading step takes the head of its connection's queue, so the first `pending` frames
    are the ones the steps still to run would read and belong to a transcript that stopped
    short. What sits behind them is read by no step at all.
    """
    unread: dict[str, list[Incoming]] = {}
    for name, frames in held.items():
        left = frames[pending.get(name, 0) :]
        if left:
            unread[name] = left
    return unread


def failure_message(failure: str, unread: dict[str, list[Incoming]]) -> str:
    """The failure, followed by what each connection holds that no step reads."""
    report = describe_unread(unread)
    if not report:
        return failure
    return "\n".join([failure, *report])


class Server:
    """A `selvaged` process on an ephemeral port, for the length of one vector."""

    def __init__(self, binary: str, grace_ms: int) -> None:
        self.binary = binary
        self.grace_ms = grace_ms
        self.process: subprocess.Popen | None = None
        self.host_port: str | None = None
        self._drain: threading.Thread | None = None

    def command(self) -> list[str]:
        # The corpus is the version-1 one — `replay` refuses any vector that is not — so the
        # server is told to seat `selvage/1` alone: vector 001's `/meta` advertises one
        # version and vector 005 has a `selvage/2` hello refused, and both are claims about
        # that server rather than about the default, which seats both. The Rust harness says
        # the same thing at its own spawn site (`tests/vectors/runner.rs`).
        return [
            self.binary,
            "--listen",
            "127.0.0.1:0",
            "--room-grace-ms",
            str(self.grace_ms),
            "--serve-version-1-only",
        ]

    def start(self) -> None:
        command = self.command()
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        line = self._read(FRAME_TIMEOUT, whole=False)
        match = re.search(r"ws://([^/\s]+)/session", line.partition("\n")[0])
        if match is None:
            if self.process.poll() is not None:
                line += self._read(1.0, whole=True)
            self.stop()
            raise ServerError(
                f"`{self.binary}` did not print a listening address; it needs the\n"
                "`--room-grace-ms` and `--serve-version-1-only` options (the vectors\n"
                "set the grace period per transcript, and the corpus is `selvage/1`'s\n"
                "alone), so rebuild `crates/selvaged` from the reference server\n"
                f"and point ${SERVER_ENV} at the new binary. It said: {line.strip()!r}"
            )
        self.host_port = match.group(1)
        # Everything after the address is the server's log, which nothing here reads. It is
        # still read, and dropped: a pipe nobody reads fills, and a server blocked writing its
        # log stops answering in the middle of a vector.
        self._drain = threading.Thread(
            target=self._discard, args=(self.process.stdout,), daemon=True
        )
        self._drain.start()

    def _read(self, wait: float, *, whole: bool) -> str:
        """The server's output, read within `wait` seconds however it arrives.

        `readline` after a `select` is not bounded: a server that writes half a line and stalls
        would hold it past the deadline. This reads what is there until a newline (or, `whole`,
        the end of the output), the end of the output, or the deadline — whichever comes first.
        """
        assert self.process is not None and self.process.stdout is not None
        fd = self.process.stdout.fileno()
        end = time.monotonic() + wait
        data = b""
        while whole or b"\n" not in data:
            left = end - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        return data.decode("utf-8", "replace")

    @staticmethod
    def _discard(pipe) -> None:
        try:
            while os.read(pipe.fileno(), 65536):
                pass
        except (OSError, ValueError):
            pass  # the pipe is gone; there is nothing left to drop

    @property
    def ws_base(self) -> str:
        return f"ws://{self.host_port}"

    @property
    def http_base(self) -> str:
        return f"http://{self.host_port}"

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
        if self._drain is not None:
            self._drain.join(timeout=1.0)
            self._drain = None
        self.process = None


# --- steps --------------------------------------------------------------------


async def run_step(session: Session, server: Server, step: dict) -> None:
    op = step.get("op")
    if op == "open":
        name = step["conn"]
        url = server.ws_base + session.fill(step["target"])
        # A close must be noticed at once: the reference server learns a peer is gone
        # from the transport, not from the close frame, and a vector that tests the
        # grace period has milliseconds, not the close-handshake timeout, to spend.
        ws = await connect(
            url, open_timeout=FRAME_TIMEOUT, close_timeout=0, max_size=None
        )
        session.peers[name] = Peer(name, ws)
    elif op == "send":
        await session.peer(step).ws.send(step["text"])
    elif op == "expect":
        peer = session.peer(step)
        actual = await peer.text(step["text"])
        check_text(actual, step["text"], session.bindings)
    elif op == "sendBinary":
        data = hex_bytes(step["hex"])
        peer = session.peer(step)
        await peer.ws.send(data)
        if step.get("apply"):
            peer.doc.apply(data)
    elif op == "expectBinary":
        peer = session.peer(step)
        seen = peer.frames
        data = await peer.binary()
        if "hex" in step:
            wanted = hex_bytes(step["hex"])
            if data != wanted:
                raise Mismatch(
                    f"binary frame {seen} differs:\n"
                    f"  vector: {step['hex']}\n  wire:   {bytes_hex(data)}"
                )
        elif "frame" in step:
            check_frame_spec(step["frame"], data)
        else:
            raise Mismatch("expectBinary needs hex or frame")
        if step.get("apply"):
            peer.doc.apply(data)
    elif op == "expectClose":
        code = await session.peer(step).closed()
        if code != step["code"]:
            raise Mismatch(f"closed with {code}, the vector claims {step['code']}")
    elif op == "close":
        await session.peer(step).stop()
    elif op == "wait":
        await asyncio.sleep(step["ms"] / 1000)
    elif op == "http":
        target = session.fill(step["target"])
        host, _, port = server.host_port.partition(":")
        connection = http.client.HTTPConnection(host, port, timeout=FRAME_TIMEOUT)
        try:
            connection.request("GET", target)
            reply = connection.getresponse()
            session.status = reply.status
            session.body = reply.read().decode("utf-8")
        finally:
            connection.close()
    elif op == "expectStatus":
        if session.status != step["status"]:
            raise Mismatch(
                f"the reply's status is {session.status}, not {step['status']}"
            )
    elif op == "expectBody":
        if session.body is None:
            raise Mismatch("no HTTP request has been made")
        check_text(session.body, step["text"], session.bindings)
    elif op == "expectDoc":
        actual = session.peer(step).doc.text_at(step["path"])
        if actual != step["text"]:
            raise Mismatch(
                f"`{step['conn']}` holds {actual!r} in {step['path']}, not {step['text']!r}"
            )
    elif op == "expectSameState":
        names = step["conns"]
        if not names:
            raise Mismatch("expectSameState needs conns")
        first = session.peer({"conn": names[0]})
        wanted = first.doc.state()
        for name in names[1:]:
            held = session.peer({"conn": name}).doc.state()
            if held != wanted:
                raise Mismatch(
                    f"`{names[0]}` and `{name}` have different state vectors: "
                    f"{yprotocols.describe_state(wanted)} and "
                    f"{yprotocols.describe_state(held)}"
                )
    elif op in WIRE_OPS:
        # Unreachable while every member of `WIRE_OPS` has a branch above it. It is what keeps
        # the table and the dispatch one thing instead of two that agree by inspection, which is
        # how `apply` came to be validated and un-runnable.
        raise Mismatch(f"`{op}` is in WIRE_OPS and has no step handling")
    else:
        raise Mismatch(f"`{op}` is not a step this runner knows")

async def replay(vector: dict, binary: str) -> dict[str, list[Incoming]]:
    """Replays one vector against a fresh server.

    Returns what each connection still held once the steps were over — empty for a vector
    whose transcript reads every frame it is sent. `replay_all` fails the vector when the
    transcript leaves anything unread.
    """
    if vector.get("selvage") != "selvage/1" or vector.get("canonical") != "SJ-C/1":
        raise ReplayError(
            f"vector {vector.get('id')} is bound to {vector.get('selvage')} / "
            f"{vector.get('canonical')}, but this runner speaks selvage/1 / SJ-C/1"
        )
    harness = vector.get("harness") or {}
    server = Server(binary, harness.get("room_grace_ms", DEFAULT_GRACE_MS))
    try:
        server.start()
    except ServerError as error:
        raise ReplayError(str(error)) from error
    session = Session()
    steps = vector["steps"]
    failure: str | None = None
    # Where the transcript got to: past the failing step, or past the last one.
    start = len(steps)
    try:
        for index, step in enumerate(steps):
            name = step.get("conn")
            peer = session.peers.get(name) if name is not None else None
            before = peer.frames if peer is not None else None
            try:
                await run_step(session, server, step)
            except ReplayError:
                raise
            except Exception as error:
                failure = describe_failure(
                    session, len(steps), index, step, before, error
                )
                start = index + 1
                break
        held = await session.drain()
    finally:
        await session.stop()
        server.stop()
    unread = unread_frames(held, pending_reads(steps, start))
    if failure is not None:
        raise ReplayError(failure_message(failure, unread))
    return unread


# --- schema checks and the command line ---------------------------------------


def load_validate_module():
    """Loads `schema/validate.py` unchanged, for its checks and its corpus pins."""
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


def run_schema_checks() -> tuple[int, int]:
    """Runs `schema/validate.py` unchanged, returning (exit code, frame checks)."""
    module = load_validate_module()
    return module.main(), module.CHECKS


def vector_dir() -> pathlib.Path:
    """Where the replay reads its transcripts; `SELVAGE_VECTORS` overrides it."""
    return pathlib.Path(os.environ.get("SELVAGE_VECTORS", VECTOR_DIR))


def peer_vector_dir() -> pathlib.Path:
    """The layer this runner does not run, and reports rather than passing."""
    return pathlib.Path(
        os.environ.get("SELVAGE_PEER_VECTORS", vector_dir() / "peer")
    )


def load_peer_vectors() -> list[dict]:
    """The peer vectors' names and ids, for the report and not for any replay."""
    peers = []
    for path in sorted(peer_vector_dir().glob("*.json")):
        try:
            document = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            raise ReplayError(f"{path.name} is not JSON: {error}") from error
        peers.append({"_file": path.name, "id": document.get("id", "?"),
                      "kind": document.get("kind", "?")})
    return peers


PEER_LAYER_NOTE = (
    "not attempted: a peer vector's subject is a client, and this runner replays "
    "transcripts against a server — run `python3 runner/run_peer.py` for the frame layer"
)


def load_vectors() -> list[dict]:
    vectors = []
    for path in sorted(vector_dir().glob("*.json")):
        with path.open() as handle:
            vector = json.load(handle)
        vector["_file"] = path.name
        vectors.append(vector)
    return sorted(vectors, key=lambda vector: vector.get("id", ""))


def check_corpus_size(vectors: list[dict], expected: int) -> None:
    """Fails when the directory does not hold the corpus the pins describe.

    The replay checks whatever it finds, so a shrunk directory replays green on less:
    a deleted transcript changes this number, and the number is a check. `expected` is
    `schema/validate.py`'s `EXPECTED_WIRE_VECTORS` — the count has one home, and the schema
    half already pins it; this is the same pin where the real server is tested.
    """
    if len(vectors) != expected:
        raise ReplayError(
            f"the directory holds {len(vectors)} vectors, and this suite pins "
            f"{expected}: adding or removing a transcript is a deliberate edit"
        )


def replay_all(binary: str) -> tuple[int, int]:
    vectors = load_vectors()
    try:
        check_corpus_size(vectors, load_validate_module().EXPECTED_WIRE_VECTORS)
    except ReplayError as error:
        print(f"FAIL   corpus: {error}")
        # No vector was attempted: the failure is the corpus itself, not a vector,
        # so it must not count as an attempted one (`main` derives passed from these).
        return 0, 1
    failed = 0
    for vector in vectors:
        name = vector["_file"]
        try:
            unread = asyncio.run(replay(vector, binary))
        except ReplayError as error:
            failed += 1
            print(f"FAIL   {name:<33} {error}")
        else:
            report = describe_unread(unread)
            if report:
                failed += 1
                print(
                    f"FAIL   {name:<33} "
                    f"{failure_message('holds frames the transcript does not read', unread)}"
                )
            else:
                print(f"ok     {name}")
    return len(vectors), failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="check the schemas and the frames, and start no server",
    )
    parser.add_argument(
        "--server",
        metavar="PATH",
        help=f"the selvaged binary (default: ${SERVER_ENV})",
    )
    parser.add_argument(
        "--layer",
        choices=("wire", "peer", "all"),
        default="wire",
        help="which corpus layer to replay; `all` also reports the peer layer it cannot run",
    )
    args = parser.parse_args(argv)

    if args.layer == "peer":
        # Refused, not exited zero: this runner cannot run a peer vector, and a run that
        # attempted nothing must not read as one that passed everything.
        print(
            "the peer layer's subject is a client, and this runner replays transcripts against "
            "a server.\nRun its frame layer with `python3 runner/run_peer.py`; `--layer all` "
            "reports what it did not attempt.",
            file=sys.stderr,
        )
        return 2

    if args.schema_only:
        code, _ = run_schema_checks()
        return code

    if WEBSOCKETS_ERROR is not None:
        print(
            f"the runner needs the `websockets` package to replay a vector: "
            f"{WEBSOCKETS_ERROR}\n"
            "install it with `pip install websockets`, or run the frame checks alone\n"
            "with `python3 runner/run_vectors.py --schema-only`.",
            file=sys.stderr,
        )
        return 2

    binary = args.server or os.environ.get(SERVER_ENV)
    if not binary:
        print(
            f"{SERVER_ENV} is not set, and this runner replays the wire vectors\n"
            "against a running server. Build one and point the variable at it:\n"
            "\n"
            "    cd ../reference_server\n"
            "    nix develop . -c cargo build -p selvaged\n"
            f"    export {SERVER_ENV}=$PWD/target/debug/selvaged\n",
            file=sys.stderr,
        )
        return 2
    if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
        print(
            f"{SERVER_ENV}={binary} is not an executable file;\n"
            "build the reference server (`cargo build -p selvaged`) and point the\n"
            f"variable at the resulting binary.",
            file=sys.stderr,
        )
        return 2

    schema_code, checks = run_schema_checks()
    vectors, failed = replay_all(binary)
    # A corpus failure attempts no vectors, so `vectors` is 0 and the failure is
    # the corpus itself; the clamp keeps that from reading as negative passes.
    passed = max(vectors - failed, 0)
    attempted = vectors
    if args.layer == "all":
        # The layer that is not this runner's is reported and not passed. `not attempted` is a
        # third outcome beside ok and FAIL, because a reader of a green run has to be able to
        # see how much of the corpus it did not run.
        peers = load_peer_vectors()
        try:
            expected = load_validate_module().EXPECTED_PEER_VECTORS
        except (ReplayError, AttributeError) as error:
            print(f"FAIL   corpus: {error}")
            peers = []
            expected = 0
        if peers and len(peers) != expected:
            print(
                f"FAIL   corpus: the peer layer holds {len(peers)} vectors, and this suite "
                f"pins {expected}"
            )
            failed += 1
        for peer in peers:
            print(f"not attempted  {peer['_file']:<33} {PEER_LAYER_NOTE}")
    print(
        f"summary        {attempted} files, {checks} frame checks, "
        f"{passed} vectors passed, {failed} failed"
        + (", 0 peer vectors attempted" if args.layer == "all" else "")
    )
    return 1 if schema_code or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
