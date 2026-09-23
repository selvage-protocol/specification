"""The y-protocols framing the binary vectors carry, read without a CRDT library.

A vector's `sendBinary` and `expectBinary` steps are y-protocols messages. Most are
compared as bytes and nothing here is needed; the value of a vector is the bytes. Two
things are not bytes: the `frame` description of an awareness update, which names the
state a peer published, and the `apply`/`expectDoc`/`expectSameState` steps of vector
009, which ask what a replica holds once a frame is applied.

This module implements only what those need: the lib0 varint primitives, the sync and
awareness envelopes, and enough of the v1 update format to rebuild the text of one
root type. It is not a CRDT. Concurrent insertions at one position are ordered by
origin alone, which is enough for the fixed transcripts and not for a general client.

The format lives in `yjs`'s `UpdateDecoderV1`, `readClientsStructRefs` and
`Item.write`; the reference client is the same library, so the vectors are the same
bytes these functions decode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


class DecodeError(ValueError):
    """A frame is not y-protocols, or uses a part of it this module does not read."""


def read_varuint(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if i >= len(data):
            raise DecodeError("truncated varint")
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7
        if shift > 63:
            raise DecodeError("varint is longer than 64 bits")


def read_varstring(data: bytes, i: int) -> tuple[str, int]:
    length, i = read_varuint(data, i)
    end = i + length
    if end > len(data):
        raise DecodeError("truncated string")
    return data[i:end].decode("utf-8"), end


def read_varbytes(data: bytes, i: int) -> tuple[bytes, int]:
    length, i = read_varuint(data, i)
    end = i + length
    if end > len(data):
        raise DecodeError("truncated byte array")
    return data[i:end], end


def read_id(data: bytes, i: int) -> tuple[tuple[int, int], int]:
    client, i = read_varuint(data, i)
    clock, i = read_varuint(data, i)
    return (client, clock), i


@dataclass
class SyncMessage:
    subtype: int  # 0 SyncStep1, 1 SyncStep2, 2 Update
    payload: bytes


@dataclass
class AwarenessEntry:
    client: int
    clock: int
    state: str


@dataclass
class AwarenessMessage:
    entries: list[AwarenessEntry]


def decode_message(frame: bytes) -> SyncMessage | AwarenessMessage:
    message_type, i = read_varuint(frame, 0)
    if message_type == 0:
        subtype, i = read_varuint(frame, i)
        payload, i = read_varbytes(frame, i)
        return SyncMessage(subtype, payload)
    if message_type == 1:
        payload, i = read_varbytes(frame, i)
        count, j = read_varuint(payload, 0)
        entries = []
        for _ in range(count):
            client, j = read_varuint(payload, j)
            clock, j = read_varuint(payload, j)
            state, j = read_varstring(payload, j)
            entries.append(AwarenessEntry(client, clock, state))
        return AwarenessMessage(entries)
    raise DecodeError(f"unknown message type {message_type}")


def carries_content(frame: bytes) -> bool:
    """Whether a `kind = 0` stream carries document content, anywhere in it.

    `PROTOCOL.md` §13.5: content is a sync message of sub-type 1 (SyncStep2) or 2 (Update),
    and a SyncStep1 is a state vector and a request rather than content. The whole stream,
    not its first message: a frame whose first message is a SyncStep1 and whose second is an
    Update carries its content behind the request (`CANONICAL.md` §6.1's step 10). The walk
    stops where the stream stops making sense, and what it has already seen is what the
    frame carries — the same read the receiver that would apply the frame makes.
    """
    i = 0
    content = False
    while i < len(frame):
        try:
            message_type, i = read_varuint(frame, i)
            if message_type == 0:
                subtype, i = read_varuint(frame, i)
                content = content or subtype in (1, 2)
                _, i = read_varbytes(frame, i)
            elif message_type == 1:
                _, i = read_varbytes(frame, i)
            elif message_type == 2:
                # `yrs` reads Auth as a status varint, and a reason only when the status is
                # `PERMISSION_DENIED`; a length-prefixed read loses alignment and can miss an
                # Update behind it.
                status, i = read_varuint(frame, i)
                if status == 0:
                    _, i = read_varbytes(frame, i)
            elif message_type == 3:
                pass
            else:
                _, i = read_varbytes(frame, i)
        except DecodeError:
            break
    return content


@dataclass
class Item:
    client: int
    clock: int
    length: int
    origin: tuple[int, int] | None
    right: tuple[int, int] | None
    parent: str | tuple[int, int] | None
    parent_sub: str | None
    kind: str
    text: str | None


def _read_content(data: bytes, i: int, ref: int) -> tuple[str, str | None, int, int]:
    if ref == 1:  # Deleted
        length, i = read_varuint(data, i)
        return "Deleted", None, length, i
    if ref == 2:  # JSON
        text, i = read_varstring(data, i)
        return "JSON", text, 1, i
    if ref == 3:  # Binary
        _, i = read_varbytes(data, i)
        return "Binary", None, 1, i
    if ref == 4:  # String
        text, i = read_varstring(data, i)
        return "String", text, utf16_length(text), i
    if ref == 5:  # Embed, JSON in the v1 encoding
        text, i = read_varstring(data, i)
        return "Embed", text, 1, i
    if ref == 6:  # Format
        _, i = read_varstring(data, i)
        _, i = read_varstring(data, i)
        return "Format", None, 1, i
    raise DecodeError(f"content ref {ref} is not one this reader knows")


def decode_update(data: bytes) -> list[Item]:
    """The items of a v1 update, in the order the update writes them."""
    count, i = read_varuint(data, 0)
    items: list[Item] = []
    for _ in range(count):
        structs, i = read_varuint(data, i)
        client, i = read_varuint(data, i)
        clock, i = read_varuint(data, i)
        for _ in range(structs):
            if i >= len(data):
                raise DecodeError("truncated update")
            info = data[i]
            i += 1
            ref = info & 0x1F
            if ref in (0, 10):  # GC, Skip: neither holds content
                length, i = read_varuint(data, i)
                clock += length
                continue
            origin = None
            right = None
            parent: str | tuple[int, int] | None = None
            parent_sub = None
            if info & 0x80:
                origin, i = read_id(data, i)
            if info & 0x40:
                right, i = read_id(data, i)
            if (info & 0xC0) == 0:  # parent is not implied by an origin
                is_key, i = read_varuint(data, i)
                if is_key:
                    parent, i = read_varstring(data, i)
                else:
                    parent, i = read_id(data, i)
                if info & 0x20:
                    parent_sub, i = read_varstring(data, i)
            kind, text, length, i = _read_content(data, i, ref)
            items.append(
                Item(
                    client, clock, length, origin, right, parent, parent_sub,
                    kind, text,
                )
            )
            clock += length
    return items


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _text_units(text: str) -> list[int]:
    raw = text.encode("utf-16-le")
    return [raw[i] | raw[i + 1] << 8 for i in range(0, len(raw), 2)]


def _units_text(units: list[int]) -> str:
    raw = b"".join(unit.to_bytes(2, "little") for unit in units)
    return raw.decode("utf-16-le")


def _cell_index(cells: list[tuple[int, int, int]], at: tuple[int, int]) -> int | None:
    client, clock = at
    for index, (_, cell_client, cell_clock) in enumerate(cells):
        if cell_client == client and cell_clock == clock:
            return index
    return None


class Replica:
    """The text and state vector of one peer, as the vector's `apply` steps build it.

    `apply` decodes a sync frame and integrates its items; `text_at` rebuilds the
    string a root type holds by placing each inserted run after its origin, or before
    its right origin; `state` is the state vector the reference client would report.
    """

    def __init__(self) -> None:
        self.items: list[Item] = []

    def apply(self, frame: bytes) -> None:
        message = decode_message(frame)
        if isinstance(message, SyncMessage) and message.subtype in (1, 2):
            self.items.extend(decode_update(message.payload))

    def _item_at(self, at: tuple[int, int]) -> Item | None:
        client, clock = at
        for item in self.items:
            if item.client == client and item.clock <= clock < item.clock + item.length:
                return item
        return None

    def _root_of(self, item: Item) -> object:
        seen = 0
        while item.parent is None:
            anchor = item.origin if item.origin is not None else item.right
            if anchor is None:
                return None
            found = self._item_at(anchor)
            if found is None:
                return None
            item = found
            seen += 1
            if seen > len(self.items) + 1:
                return None
        return item.parent

    def text_at(self, path: str) -> str:
        cells: list[tuple[int, int, int]] = []
        for item in self.items:
            if item.kind != "String" or self._root_of(item) != path:
                continue
            units = _text_units(item.text or "")
            inserted = [
                (unit, item.client, item.clock + offset)
                for offset, unit in enumerate(units)
            ]
            if item.origin is not None:
                at = _cell_index(cells, item.origin)
                if at is None:
                    cells.extend(inserted)
                else:
                    cells[at + 1 : at + 1] = inserted
            elif item.right is not None:
                at = _cell_index(cells, item.right)
                if at is None:
                    cells.extend(inserted)
                else:
                    cells[at:at] = inserted
            else:
                cells.extend(inserted)
        return _units_text([unit for unit, _, _ in cells])

    def state(self) -> tuple[tuple[int, int], ...]:
        clocks: dict[int, int] = {}
        for item in self.items:
            end = item.clock + item.length
            if end > clocks.get(item.client, 0):
                clocks[item.client] = end
        return tuple(sorted(clocks.items()))


def describe_state(state: tuple[tuple[int, int], ...]) -> str:
    return json.dumps(dict(state), sort_keys=True)
