#!/usr/bin/env python3
"""Replays `vectors/*.json` against a running `selvaged`, and asserts the bytes.

The vectors are a transcript of real exchanges, bound to a wire version and to the
canonical form of `CANONICAL.md`. This is the language-neutral reader of them: it
opens WebSocket connections, sends exactly the bytes a step names, and compares what
comes back. Nothing here reads Rust; the only thing it needs from the reference
implementation is a running server, whose path it takes from `SELVAGE_SELVAGED`.

Run it from this directory:

    export SELVAGE_SELVAGED=/path/to/reference_server/target/debug/selvaged
    python3 runner/run_vectors.py

`--schema-only` runs the frame-level checks alone, which need no server and are what
CI can run while the reference server is private.

The comparison rules are the ones `README.md` lists: member sets are exact in both
directions, `peers` is a set, and a text frame is compared twice — structurally, so a
failure names the member, and then as whole bytes, because the vector is written in
the canonical form. A `$name` placeholder binds the first value it sees and must be
that value again; `$_` matches anything and is never remembered.
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


class Server:
    """A `selvaged` process on an ephemeral port, for the length of one vector."""

    def __init__(self, binary: str, grace_ms: int) -> None:
        self.binary = binary
        self.grace_ms = grace_ms
        self.process: subprocess.Popen | None = None
        self.host_port: str | None = None

    def start(self) -> None:
        command = [
            self.binary,
            "--listen",
            "127.0.0.1:0",
            "--room-grace-ms",
            str(self.grace_ms),
        ]
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        ready, _, _ = select.select([self.process.stdout], [], [], FRAME_TIMEOUT)
        line = self.process.stdout.readline() if ready else ""
        match = re.search(r"ws://([^/\s]+)/session", line)
        if match is None:
            if self.process.poll() is not None:
                line += self.process.stdout.read()
            self.stop()
            raise ServerError(
                f"`{self.binary}` did not print a listening address; it needs the\n"
                "`--room-grace-ms` option (the vectors set the grace period per\n"
                "transcript), so rebuild `crates/selvaged` from the reference server\n"
                f"and point ${SERVER_ENV} at the new binary. It said: {line.strip()!r}"
            )
        self.host_port = match.group(1)

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
    else:
        raise Mismatch(f"`{op}` is not a step this runner knows")


async def replay(vector: dict, binary: str) -> None:
    """Replays one vector against a fresh server."""
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
    try:
        for index, step in enumerate(vector["steps"]):
            try:
                await run_step(session, server, step)
            except ReplayError:
                raise
            except Exception as error:
                raise ReplayError(
                    f"step {index} `{step.get('op')}`: {error}"
                ) from error
    finally:
        await session.stop()
        server.stop()


# --- schema checks and the command line ---------------------------------------


def run_schema_checks() -> tuple[int, int]:
    """Runs `schema/validate.py` unchanged, returning (exit code, frame checks)."""
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
    return module.main(), module.CHECKS


def vector_dir() -> pathlib.Path:
    """Where the replay reads its transcripts; `SELVAGE_VECTORS` overrides it."""
    return pathlib.Path(os.environ.get("SELVAGE_VECTORS", VECTOR_DIR))


def load_vectors() -> list[dict]:
    vectors = []
    for path in sorted(vector_dir().glob("*.json")):
        with path.open() as handle:
            vector = json.load(handle)
        vector["_file"] = path.name
        vectors.append(vector)
    return sorted(vectors, key=lambda vector: vector.get("id", ""))


def replay_all(binary: str) -> tuple[int, int]:
    vectors = load_vectors()
    failed = 0
    for vector in vectors:
        name = vector["_file"]
        try:
            asyncio.run(replay(vector, binary))
        except ReplayError as error:
            failed += 1
            print(f"FAIL   {name:<33} {error}")
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
    args = parser.parse_args(argv)

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
    passed = vectors - failed
    print(
        f"summary        {vectors} files, {checks} frame checks, "
        f"{passed} vectors passed, {failed} failed"
    )
    return 1 if schema_code or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
