#!/usr/bin/env python3
"""`selvage/2`'s sealed frame: the bytes, and the verdict a receiver reaches on them.

[`CANONICAL.md`](../CANONICAL.md) §6.1 fixes the envelope, the key schedule, the associated
data, the signature input, the key id, the counter and the order a receiver reads the bytes
in. This module is that section in Python, so that the peer corpus can pin the wire's bytes
with no client, no server and no Rust toolchain: it seals and signs a frame from a recipe,
and it reads a frame the way §6.1 says, reporting the first step that refuses it.

**What this is and is not.** It is a format check and a self-consistency check. It is not a
second implementation in the sense this project means elsewhere: a Python verifier and a
TypeScript one written from the same prose can share a reading, so agreement between them is
weak evidence (`docs/studies/peer-corpus.md` §4). What it buys is that the envelope's bytes
are pinned at all, in the repository that reads the specification, and that a stranger can
run the pin without reading an implementation.

**One dependency.** AES-256-GCM, HKDF-SHA256 and Ed25519 come from `cryptography`; hand-
rolling any of them is a few hundred lines of security code in the repository whose whole
claim is that a stranger can trust its bytes. Everything else here is the standard library
and [`yprotocols.py`](yprotocols.py), which reads a `kind = 0` plaintext.

**The mutations.** `Reader(mutations=...)` removes one guard each, and the reason is the
corpus's: a vector that no wrong receiver fails is not evidence (`docs/studies/peer-corpus.md`
§7). Each name below is a rule of §6.1's table with the rule taken out, and `run_peer.py
--mutation-census` requires every vector to go red under the one it declares. They are not a
test-only build of anything: they are a switch on this reader, which is the reader the corpus
is replayed by.
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import sys
import unicodedata

from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import yprotocols  # noqa: E402  (the path insert above makes it importable)

# The five kinds this version defines (`CANONICAL.md` §6.1). A frame that uses another is
# refused `unknown_kind`; the corpus seals one to pin that, so a recipe is not restricted to
# these.
KINDS = (0, 1, 2, 3, 4)
# The ten reasons a receiver reports, in the order §6.1's table reads them. The order is
# normative because the report is observable.
REASONS = (
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
)

# The canonical form of a sealed payload's plaintext (`CANONICAL.md` §2): ascending member
# names, no whitespace, and no `\u` escape for a printable character. A *producer*'s rule;
# a receiver reads the member set and the types and not the spelling.
CANONICAL = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}

MAX_COUNT = 9007199254740991
# RFC 4648 §3.5 over 32 bytes: 43 characters, the last of which carries four bits of the value
# and two that are zero. `§6.1`'s own rule, and the same set `schema/sealed.json` pins.
KEY_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
KEY_LAST = frozenset("AEIMQUYcgkosw048")
KEY_LENGTH = 43

# The only character `PROTOCOL.md` §5 refuses in a path, and the one a receiver drops a path
# for rather than refusing the frame (`§13.3`, `§13.7`).
CONTROL_CATEGORIES = frozenset(("Cc",))


class SealedError(ValueError):
    """Bytes that are not an envelope, or a recipe that cannot produce one."""


# --- the mutation table --------------------------------------------------------


MUTATIONS = {
    "lenient-layout": "step 1: bytes left over after the signature are ignored",
    "lenient-kind": "step 2: an unknown kind is read as kind 0",
    "lenient-epoch": "step 3: an epoch this version does not define is not refused",
    "no-commit": "step 4: a kind 0 or 3 frame resolves against every key the receiver holds",
    "kind-any": "step 4: a kind 1 or 2 frame may verify against any committed key, not only the host key",
    "no-mark": "step 5: there is no counter mark, so every counter is accepted",
    "no-verify": "step 6: the signature is not checked",
    "no-payload": "step 8: the plaintext's member set and types are not read; the frame is accepted and nothing is folded",
    "lenient-key": "step 8: a key that is not the canonical encoding is resolved to 32 bytes",
    "no-issued": "step 9: a state or a closing at or below the mark is applied",
    "no-roles": "step 10: a committed viewer's document content is applied",
    "refuse-bad-path": "step 8: a listing's or a holds' path that PROTOCOL.md §5 refuses refuses the frame, where §13.3 and §13.7 have the receiver drop it",
    "merge-peers": "an applied state merges into the keys the receiver holds instead of replacing them",
}


# --- keys, and the encoding the invite's fragment uses --------------------------


def b64url(raw: bytes) -> str:
    """32 bytes in the encoding `CANONICAL.md` §6.1 gives the invite's two values."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_key(text: str, *, lenient: bool = False) -> bytes | None:
    """The 32 bytes a canonical base64url value spells, or None.

    A 43-character string whose last character carries non-zero pad bits spells no 32-byte
    value however leniently a decoder reads it (§6.1). `lenient` is the mutation that
    resolves it anyway; it is the reader's own switch and never a default.
    """
    if not isinstance(text, str):
        return None
    if len(text) != KEY_LENGTH:
        if not lenient or not text:
            return None
        text = text[:KEY_LENGTH]
    if not lenient:
        if not set(text) <= KEY_ALPHABET or text[-1] not in KEY_LAST:
            return None
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except Exception:
        return None
    return raw if len(raw) == 32 else None


def key_id(public: bytes) -> bytes:
    """The first 8 bytes of the SHA-256 of a public key's 32 bytes (§6.1)."""
    return hashlib.sha256(public).digest()[:8]


def hex_key_id(public: bytes) -> str:
    return key_id(public).hex()


@dataclass(frozen=True)
class Key:
    """One fixture keypair. The private half is what a vector seals and signs with."""

    name: str
    public: bytes
    private: bytes | None

    @property
    def id(self) -> bytes:
        return key_id(self.public)

    @property
    def hex_id(self) -> str:
        return self.id.hex()

    def sign(self, message: bytes) -> bytes:
        if self.private is None:
            raise SealedError(f"fixture key {self.name!r} has no private half")
        return Ed25519PrivateKey.from_private_bytes(self.private).sign(message)

    def verifies(self, message: bytes, signature: bytes) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(self.public).verify(signature, message)
        except (InvalidSignature, ValueError):
            return False
        return True


class Fixture:
    """`vectors/fixture/keys.json`: public test values whose authority is reproducibility.

    The note in the file says the same thing in the place a reader looks. Two internal
    consistency rules are checked on load rather than trusted: a key's declared id must be
    the derivation §6.1 fixes, and its private half must be the one its public half belongs
    to. A fixture that failed either would make every derived frame wrong in a way no
    verdict would name.
    """

    def __init__(self, path: pathlib.Path | str) -> None:
        self.path = pathlib.Path(path)
        try:
            document = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise SealedError(f"{self.path} is not a readable fixture: {error}") from error
        self._load(document, str(self.path))

    @classmethod
    def from_document(cls, document: object, where: str = "<fixture>") -> "Fixture":
        """A fixture from an already-parsed document, so its own checks can be driven without
        writing a file: a fixture with a wrong `key_id` or a mismatched keypair must be refused,
        and a check that can only run against the good fixture is not a check."""
        fixture = cls.__new__(cls)
        fixture.path = pathlib.Path(where)
        fixture._load(document, where)
        return fixture

    def _load(self, document: object, where: str) -> None:
        if not isinstance(document, dict):
            raise SealedError(f"{where} is not a fixture object")
        room = document.get("room")
        if not isinstance(room, dict):
            raise SealedError(f"{where} has no `room` object")
        self.room_id = room.get("id")
        self.room_key = bytes.fromhex(room.get("key", ""))
        self.host_name = room.get("host")
        if not isinstance(self.room_id, str) or len(self.room_key) != 32:
            raise SealedError(f"{where}: `room` needs an `id` and a 32-byte `key`")
        self.note = document.get("note", "")
        self.keys: dict[str, Key] = {}
        raw_keys = document.get("keys")
        if not isinstance(raw_keys, dict):
            raise SealedError(f"{where} has no `keys` object")
        for name, entry in raw_keys.items():
            public = bytes.fromhex(entry.get("public", ""))
            private_text = entry.get("private")
            private = bytes.fromhex(private_text) if private_text else None
            key = Key(name, public, private)
            if len(public) != 32:
                raise SealedError(f"{where}: {name} is not a 32-byte public key")
            if entry.get("key_id") != key.hex_id:
                raise SealedError(
                    f"{where}: {name}'s key_id is {entry.get('key_id')!r} and "
                    f"SHA-256(public)[0..8] is {key.hex_id}"
                )
            if private is not None:
                derived = (
                    Ed25519PrivateKey.from_private_bytes(private)
                    .public_key()
                    .public_bytes(Encoding.Raw, PublicFormat.Raw)
                )
                if derived != public:
                    raise SealedError(
                        f"{where}: {name}'s private half is not the one its public half "
                        "belongs to"
                    )
            self.keys[name] = key
        if self.host_name not in self.keys:
            raise SealedError(f"{where}: the host key {self.host_name!r} is not a fixture key")

    @property
    def host(self) -> Key:
        return self.keys[self.host_name]

    def key(self, name: str) -> Key:
        try:
            return self.keys[name]
        except KeyError:
            raise SealedError(f"the fixture has no key {name!r}") from None

    def frame_key(self) -> bytes:
        """`HKDF-SHA256(ikm = room key, salt = room id, info = "selvage/2 frame", L = 32)`."""
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.room_id.encode("utf-8"),
            info=b"selvage/2 frame",
        ).derive(self.room_key)


# --- varints, and the two byte strings §6.1 builds out of them ------------------


def varuint(value: int) -> bytes:
    if value < 0:
        raise SealedError(f"{value} is not a varUint")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def varuint8array(raw: bytes) -> bytes:
    return varuint(len(raw)) + raw


def read_varuint(data: bytes, at: int) -> tuple[int, int]:
    """One LEB128 value and the offset after it.

    A byte read at shift 63 may set only bit 0. Anything above it spells a value past 64
    bits, and no writer of `CANONICAL.md` §6.1's `varUint` fields — a counter, a kind, an
    epoch, a length — produces one: Python would widen it into an integer no reader of the
    frozen layout holds, which is a different frame from the one the bytes name. The
    overlong *spelling* of a value below 64 bits (a `80 00` for `0`) is a separate question
    (`NOTES.md` §B.38) and is read here as it is written, since its bytes are unambiguous.
    """
    value = 0
    shift = 0
    while True:
        if at >= len(data):
            raise SealedError("the bytes run out inside a varUint")
        byte = data[at]
        at += 1
        low = byte & 0x7F
        if shift == 63 and low & 0x7E:
            raise SealedError("a varUint longer than 64 bits")
        value |= low << shift
        if not byte & 0x80:
            return value, at
        shift += 7
        if shift > 63:
            raise SealedError("a varUint longer than 64 bits")


# --- the envelope ---------------------------------------------------------------


@dataclass
class Envelope:
    """One `selvage/2` binary frame, as §6.1's table writes its fields."""

    key_id: bytes
    kind: int
    epoch: int
    counter: int
    nonce: bytes
    ciphertext: bytes
    signature: bytes = b""

    def bytes(self) -> bytes:
        return b"".join(
            (
                self.key_id,
                varuint(self.kind),
                varuint(self.epoch),
                varuint(self.counter),
                self.nonce,
                varuint8array(self.ciphertext),
                self.signature,
            )
        )

    @classmethod
    def parse(cls, raw: bytes, *, ignore_trailing: bool = False) -> "Envelope":
        """Read the layout of §6.1's step 1.

        The three fixed-width fields are fixed: bytes that run out inside one, or any byte
        left over after the signature, are malformed rather than a variant. `ignore_trailing`
        is the mutation that reads the prefix and drops the rest.
        """
        if len(raw) < 8 + 12 + 64:
            raise SealedError("shorter than the envelope's fixed-width fields")
        key_id = raw[:8]
        at = 8
        kind, at = read_varuint(raw, at)
        epoch, at = read_varuint(raw, at)
        counter, at = read_varuint(raw, at)
        if at + 12 > len(raw):
            raise SealedError("the bytes run out inside the nonce")
        nonce = raw[at : at + 12]
        at += 12
        length, at = read_varuint(raw, at)
        if at + length + 64 > len(raw):
            raise SealedError("the bytes run out inside the ciphertext or the signature")
        ciphertext = raw[at : at + length]
        at += length
        signature = raw[at : at + 64]
        at += 64
        if at != len(raw) and not ignore_trailing:
            raise SealedError(f"{len(raw) - at} bytes left over after the signature")
        return cls(key_id, kind, epoch, counter, nonce, ciphertext, signature)


def associated_data(room_id: str, kind: int, epoch: int, key_id_bytes: bytes) -> bytes:
    """`aad = varUint8Array("selvage/2") ‖ varUint8Array(room) ‖ varUint(kind) ‖
    varUint(epoch) ‖ varUint8Array(key_id)`."""
    return b"".join(
        (
            varuint8array(b"selvage/2"),
            varuint8array(room_id.encode("utf-8")),
            varuint(kind),
            varuint(epoch),
            varuint8array(key_id_bytes),
        )
    )


def signing_input(aad: bytes, envelope: Envelope) -> bytes:
    """`signed = aad ‖ varUint(counter) ‖ varUint8Array(nonce) ‖ varUint8Array(ciphertext)`."""
    return b"".join(
        (
            aad,
            varuint(envelope.counter),
            varuint8array(envelope.nonce),
            varuint8array(envelope.ciphertext),
        )
    )


def plaintext_of(recipe: dict) -> bytes:
    """The bytes a recipe seals: hex bytes, or a payload written as canonical JSON.

    A recipe carries exactly one of `plaintext` and `payload`. `plaintext` is hex and is how
    a vector seals a `kind = 0` stream, and how it seals a plaintext that is deliberately
    *not* its kind's object — the one thing a `payload` cannot express. `payload` is a JSON
    object, written in `CANONICAL.md` §2's form, and is what a reader can see the meaning of.
    """
    hex_text = recipe.get("plaintext")
    payload = recipe.get("payload")
    if hex_text is not None:
        return bytes.fromhex(hex_text.replace(" ", ""))
    if payload is None:
        raise SealedError("a recipe needs `plaintext` or `payload`")
    return json.dumps(payload, **CANONICAL).encode("utf-8")


def seal(fixture: Fixture, recipe: dict, *, room_id: str | None = None) -> Envelope:
    """Produce the envelope a recipe describes: seal it, then sign it.

    AES-256-GCM is a function of its key, nonce, plaintext and associated data, and Ed25519
    is deterministic (RFC 8032), so the whole envelope is a pure function of the recipe —
    which is what lets a vector carry a recipe and still be byte-exact. One exception, and it
    is why a vector also carries its `hex`: Safari's Ed25519 randomises signatures, so a
    conforming implementation need not produce these bytes and the vector's claim about a
    signature is that it verifies (`NOTES.md` §B.31).
    """
    room = room_id if room_id is not None else fixture.room_id
    signer = fixture.key(recipe["sign"])
    kind = recipe["kind"]
    epoch = recipe.get("epoch", 0)
    counter = recipe["counter"]
    nonce = bytes.fromhex(recipe["nonce"])
    if len(nonce) != 12:
        raise SealedError(f"a nonce is 12 bytes, and {recipe['nonce']!r} is not")
    plaintext = plaintext_of(recipe)
    aad = associated_data(room, kind, epoch, signer.id)
    ciphertext = AESGCM(fixture.frame_key()).encrypt(nonce, plaintext, aad)
    envelope = Envelope(signer.id, kind, epoch, counter, nonce, ciphertext)
    envelope.signature = signer.sign(signing_input(aad, envelope))
    return envelope


def opens(fixture: Fixture, envelope: Envelope, *, room_id: str | None = None) -> bytes:
    """The AEAD's output under the frame key, with no verdict about the frame.

    The one leg a vector needs to say that a frame it must refuse is a real frame of this
    room and not garbage: §6.1's steps 9 and 10 refuse bytes whose signature has already
    verified and whose AEAD has already opened.
    """
    room = room_id if room_id is not None else fixture.room_id
    aad = associated_data(room, envelope.kind, envelope.epoch, envelope.key_id)
    return AESGCM(fixture.frame_key()).decrypt(
        envelope.nonce, envelope.ciphertext, aad
    )


def authentic(fixture: Fixture, envelope: Envelope, key: Key, *, room_id: str | None = None) -> bool:
    """Whether the envelope's signature verifies against `key` over §6.1's signature input."""
    room = room_id if room_id is not None else fixture.room_id
    aad = associated_data(room, envelope.kind, envelope.epoch, envelope.key_id)
    return key.verifies(signing_input(aad, envelope), envelope.signature)


# --- the plaintext, and what step 8 reads in one --------------------------------


def _parsed(plaintext: bytes) -> object:
    """The plaintext as JSON, or None when it is not JSON at all.

    A payload's plaintext is "the UTF-8 bytes of its canonical form" (§6.1), and a receiver
    reads the *value*: `CANONICAL.md` §2 is a producer's rule and a receiver tolerates another
    spelling of the same value, exactly as it does in a text frame. What is not tolerated is
    bytes that are not JSON, and that is step 8's `bad_payload` and not step 1's.
    """
    try:
        return json.loads(plaintext)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _is_count(value: object) -> bool:
    """A count in `CANONICAL.md` §2.4's form and inside its bound."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_COUNT
    )


def _read_state(payload: object, lenient_key: bool = False, strict_path: bool = False) -> dict | None:
    """`kind = 1`'s plaintext: §6.1's and `schema/sealed.json`'s member set and types.

    Step 8 reads the member set and each member's *type*, and no value a rule elsewhere
    governs: a listing's path is `PROTOCOL.md` §13.3's to drop and not this reader's to
    refuse, and a state carrying a member this version does not define is tolerated
    (`CANONICAL.md` §3). `strict_path` is the mutation that reads a path here anyway.
    """
    if not isinstance(payload, dict):
        return None
    if not _is_count(payload.get("issued")):
        return None
    listing = payload.get("listing")
    if not isinstance(listing, list) or any(not isinstance(p, str) for p in listing):
        return None
    if strict_path and any(not usable_path(path) for path in listing):
        return None
    peers = payload.get("peers")
    if not isinstance(peers, dict):
        return None
    for name, entry in peers.items():
        if decode_key(name, lenient=lenient_key) is None:
            return None
        if not isinstance(entry, dict):
            return None
        if not isinstance(entry.get("peer_id"), str):
            return None
        if entry.get("role") not in ("host", "guest", "viewer"):
            return None
    return payload


def _read_closing(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("closing") is not True:
        return None
    if not _is_count(payload.get("issued")):
        return None
    return payload


def _read_holds(payload: object, strict_path: bool = False) -> dict | None:
    if not isinstance(payload, dict):
        return None
    holds = payload.get("holds")
    if not isinstance(holds, list) or any(not isinstance(p, str) for p in holds):
        return None
    if strict_path and any(not usable_path(path) for path in holds):
        return None
    return payload


def _parse_announcement(payload: object, lenient_key: bool = False) -> tuple[dict, Key] | None:
    if not isinstance(payload, dict):
        return None
    raw = decode_key(payload.get("key"), lenient=lenient_key)
    if raw is None:
        return None
    declared = payload.get("role")
    if declared is not None and declared not in ("guest", "viewer"):
        return None
    return payload, Key("announced", raw, None)


#: `PROTOCOL.md` §5's rule for a path, as a receiver reads it: a blank path or one carrying a
#: control character is dropped from a listing or a hold set rather than refusing the frame.
def carries_control(path: str) -> bool:
    return any(unicodedata.category(character) in CONTROL_CATEGORIES for character in path)


def usable_path(path: str) -> bool:
    return bool(path) and not carries_control(path)


# --- the verdict ----------------------------------------------------------------


@dataclass
class Verdict:
    """What a receiver did with one frame: accepted it, or refused it and why.

    `sender` is the key id that verified, which is what §6.1 makes a frame's attribution —
    and not a value the frame carries. `payload` is the plaintext read, for the kinds whose
    plaintext is an object; `message` is the y-protocols message a `kind = 0` plaintext is.
    """

    ok: bool
    reason: str | None = None
    sender: str | None = None
    kind: int | None = None
    counter: int | None = None
    plaintext: bytes = b""
    payload: object = None
    message: object = None

    def __str__(self) -> str:
        if self.ok:
            return f"ok (kind {self.kind}, key {self.sender}, counter {self.counter})"
        return f"refused {self.reason}"


@dataclass
class Peer:
    """One entry of an applied room state: the key, its role, and the seat it is labelled."""

    key: Key
    role: str
    peer_id: str


@dataclass
class Reader:
    """A conforming receiver, and the state §6.1's marks and §13.3's roles live in.

    It holds the two values the invite's fragment carries — the room key and the host key —
    and nothing else to begin with, which is a joiner's state before a state arrives. Every
    `read` is one frame and one verdict; an accepted frame folds into the receiver exactly as
    `PROTOCOL.md` §13.2, §13.3, §13.7 and §13.10 describe, so a vector is a sequence of
    decisions and not a list of independent ones.
    """

    fixture: Fixture
    mutations: frozenset[str] = frozenset()
    #: The keys an applied state commits, under the name the state gave each one — its public
    #: key in the fragment's encoding — with the role and the seat label beside it.
    committed: dict[str, Peer] = field(default_factory=dict)
    announced: dict[str, Key] = field(default_factory=dict)
    marks: dict[str, int] = field(default_factory=dict)
    holds: dict[str, list[str]] = field(default_factory=dict)
    issued: int = 0
    ended: bool = False
    #: The listing of the last state the receiver applied, with the paths `PROTOCOL.md` §5
    #: refuses left out — the receiver drops such a path and applies the rest (§13.3).
    listing: list[str] = field(default_factory=list)
    replica: yprotocols.Replica = field(default_factory=yprotocols.Replica)
    applied: list[Verdict] = field(default_factory=list)
    dropped: list[Verdict] = field(default_factory=list)

    def __post_init__(self) -> None:
        unknown = self.mutations - MUTATIONS.keys()
        if unknown:
            raise SealedError(f"no such mutation: {sorted(unknown)}")

    # -- what the receiver holds ------------------------------------------------

    @property
    def host(self) -> Key:
        return self.fixture.host

    def by_key_id(self, hex_id: str) -> list[Peer]:
        """The committed entries one 8-byte id names, in UTF-16 code-unit order of the key.

        §6.1 makes the `key_id` an **index** and not an identity: a receiver resolves it by
        verifying against every key that id names and attributes the frame to the key that
        verified, so a collision costs a verification that fails and never a mis-attribution.
        The state's `peers` is keyed by the key and one key cannot appear twice, so the two
        errors left are one `peer_id` under two keys and two `host` roles — and both are read
        the same way, by the entry whose key comes first in that order, so that two
        conforming receivers read one state the same way.
        """
        return [
            peer
            for name, peer in sorted(self.committed.items())
            if peer.key.hex_id == hex_id
        ]

    def role_of(self, hex_id: str) -> str | None:
        entries = self.by_key_id(hex_id)
        if not entries:
            return None
        # Two keys given `host`: the one whose key comes first is the host's connection.
        for peer in entries:
            if peer.role == "host":
                return "host"
        return entries[0].role

    def role_of_key(self, key: Key) -> str | None:
        """The role the applied state gives **this key**, which is the role a frame that verified
        against it is read with (`PROTOCOL.md` §13.4).

        `role_of` resolves an 8-byte id and cannot tell two colliding keys apart — that is what
        `CANONICAL.md` §6.1 makes the id: an index and not an identity. A frame's role is read
        from the key that verified, so a collision between a `guest` and a `viewer` refuses the
        `viewer`'s content rather than applying it under the `guest`'s role.
        """
        for peer in self.committed.values():
            if peer.key.public == key.public:
                return peer.role
        return None

    # -- the read --------------------------------------------------------------

    def read(self, frame: bytes) -> Verdict:
        """§6.1's table, in its order, with the first step that refuses the frame reported."""
        mutations = self.mutations
        try:
            envelope = Envelope.parse(
                frame, ignore_trailing="lenient-layout" in mutations
            )
        except SealedError:
            return self._refuse(None, None, "bad_envelope")

        kind = envelope.kind
        if kind not in KINDS:
            if "lenient-kind" not in mutations:
                return self._refuse(envelope, None, "unknown_kind")
            # The mutation reads it by the nearest rule it knows; the AAD stays the bytes'.
            kind = 0
        if envelope.epoch != 0 and "lenient-epoch" not in mutations:
            return self._refuse(envelope, kind, "unknown_epoch")

        aad = associated_data(
            self.fixture.room_id, envelope.kind, envelope.epoch, envelope.key_id
        )
        signed = signing_input(aad, envelope)
        if kind == 4:
            return self._read_announcement(envelope, aad, signed)
        return self._read_ordinary(envelope, kind, aad, signed)

    def _read_announcement(self, envelope: Envelope, aad: bytes, signed: bytes) -> Verdict:
        """`kind = 4`'s own order: the AEAD (7), the payload (8), the key (4), the signature
        (6), the mark (5).

        Its signer is inside its plaintext, so a receiver cannot verify anything before it
        opens the AEAD — which is the one place the table's order is not the read's.
        """
        mutations = self.mutations
        try:
            plaintext = opens(self.fixture, envelope)
        except InvalidTag:
            return self._refuse(envelope, 4, "bad_aead")
        read = _parse_announcement(_parsed(plaintext), "lenient-key" in mutations)
        if read is None:
            if "no-payload" not in mutations:
                return self._refuse(envelope, 4, "bad_payload")
            return self._accept(envelope, 4, None, b"", None, None)
        payload, key = read
        if key.id != envelope.key_id:
            return self._refuse(envelope, 4, "uncommitted_key")
        if not key.verifies(signed, envelope.signature) and "no-verify" not in mutations:
            return self._refuse(envelope, 4, "bad_signature")
        if self._replayed(key.hex_id, envelope):
            return self._refuse(envelope, 4, "replayed_counter")
        self.announced[key.hex_id] = key
        return self._accept(envelope, 4, key.hex_id, plaintext, payload, None)

    def _read_ordinary(
        self, envelope: Envelope, kind: int, aad: bytes, signed: bytes
    ) -> Verdict:
        mutations = self.mutations
        if kind in (1, 2):
            # The host key is the room's root of trust and the fragment names it; no state
            # commits it and no other key may sign these two kinds.
            held = [self.host]
            if "kind-any" in mutations:
                held += [peer.key for peer in self.committed.values()]
        else:
            held = [peer.key for peer in self.by_key_id(envelope.key_id.hex())]
            if "no-commit" in mutations:
                held += list(self.announced.values())
        candidates = [key for key in held if key.id == envelope.key_id]
        if not candidates:
            return self._refuse(envelope, kind, "uncommitted_key")

        # The `key_id` is an index and not an identity (`CANONICAL.md` §6.1, `PROTOCOL.md`
        # §13.4): every key that id names is tried and the frame belongs to the one whose
        # signature verified, so a collision costs a verification that fails and never a
        # mis-attribution. Step 5 stays where the table puts it and is read against the id,
        # which is what a mark is kept under, so a replayed frame that is also badly signed
        # is still `replayed_counter` and the two readers report one reason.
        if kind in (0, 3) and self._replayed(envelope.key_id.hex(), envelope):
            return self._refuse(envelope, kind, "replayed_counter")

        key = next(
            (
                candidate
                for candidate in candidates
                if candidate.verifies(signed, envelope.signature)
            ),
            None,
        )
        if key is None:
            if "no-verify" not in mutations:
                return self._refuse(envelope, kind, "bad_signature")
            key = candidates[0]

        try:
            plaintext = opens(self.fixture, envelope)
        except InvalidTag:
            return self._refuse(envelope, kind, "bad_aead")

        payload: object = None
        message: object = None
        if kind == 0:
            try:
                message = yprotocols.decode_message(plaintext)
            except yprotocols.DecodeError:
                if "no-payload" not in mutations:
                    return self._refuse(envelope, kind, "bad_payload")
        else:
            reading = _parsed(plaintext)
            reader = {
                1: lambda value: _read_state(
                    value,
                    "lenient-key" in mutations,
                    "refuse-bad-path" in mutations,
                ),
                2: _read_closing,
                3: lambda value: _read_holds(value, "refuse-bad-path" in mutations),
            }[kind]
            payload = reader(reading)
            if payload is None and "no-payload" not in mutations:
                return self._refuse(envelope, kind, "bad_payload")

        if kind in (1, 2) and isinstance(payload, dict):
            if payload["issued"] <= self.issued and "no-issued" not in mutations:
                return self._refuse(envelope, kind, "stale_issued")

        if kind == 0 and self._is_content(message):
            if self.role_of_key(key) == "viewer" and "no-roles" not in mutations:
                return self._refuse(envelope, kind, "unauthorised_content")

        return self._accept(envelope, kind, key.hex_id, plaintext, payload, message)

    # -- the pieces the read leans on ------------------------------------------

    def _replayed(self, hex_id: str, envelope: Envelope) -> bool:
        if "no-mark" in self.mutations:
            return False
        return envelope.counter <= self.marks.get(hex_id, 0)

    @staticmethod
    def _is_content(message: object) -> bool:
        """Document content is a `kind = 0` plaintext carrying a SyncStep2 or an Update.

        `PROTOCOL.md` §13.5: a SyncStep1 is a state vector and a request rather than content,
        and a viewer may send one.
        """
        return isinstance(message, yprotocols.SyncMessage) and message.subtype in (1, 2)

    def _refuse(self, envelope: Envelope | None, kind: int | None, reason: str) -> Verdict:
        verdict = Verdict(
            ok=False,
            reason=reason,
            kind=kind if kind is not None else (envelope.kind if envelope else None),
            counter=envelope.counter if envelope else None,
            sender=envelope.key_id.hex() if envelope else None,
        )
        self.dropped.append(verdict)
        return verdict

    def _accept(
        self,
        envelope: Envelope,
        kind: int,
        sender: str | None,
        plaintext: bytes,
        payload: object,
        message: object,
    ) -> Verdict:
        verdict = Verdict(
            ok=True,
            sender=sender,
            kind=kind,
            counter=envelope.counter,
            plaintext=plaintext,
            payload=payload,
            message=message,
        )
        self.applied.append(verdict)
        if sender is not None and "no-mark" not in self.mutations:
            self.marks[sender] = max(self.marks.get(sender, 0), envelope.counter)
        if kind == 0 and message is not None:
            self.replica.apply(plaintext)
        elif kind == 1 and isinstance(payload, dict):
            peers: dict[str, Peer] = {}
            for name, entry in payload["peers"].items():
                lenient = "lenient-key" in self.mutations
                raw = decode_key(name, lenient=lenient)
                peers[name] = Peer(Key(name, raw, None), entry["role"], entry["peer_id"])
            if "merge-peers" in self.mutations:
                self.committed.update(peers)
            else:
                # `PROTOCOL.md` §13.3: a state replaces the receiver's keys and roles for
                # the whole room. A key it does not name is uncommitted from that moment.
                self.committed = peers
                # `CANONICAL.md` §6.1: an announcement's key is held until the state that
                # does not commit it.
                self.announced = {
                    name: key for name, key in self.announced.items() if name in peers
                }
            self.listing = [p for p in payload["listing"] if usable_path(p)]
            self.issued = payload["issued"]
        elif kind == 2 and isinstance(payload, dict):
            self.issued = payload["issued"]
            self.ended = True
        elif kind == 3 and isinstance(payload, dict):
            self.holds[sender] = [p for p in payload["holds"] if usable_path(p)]
        return verdict


def read_all(fixture: Fixture, frames: list[bytes], **kwargs) -> list[Verdict]:
    """A convenience for tests: one receiver, one frame, one verdict, in order."""
    reader = Reader(fixture, **kwargs)
    return [reader.read(frame) for frame in frames]
