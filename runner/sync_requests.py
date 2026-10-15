"""Finite sync request accounting for fixture-driven reference tests.

This is not a subject or a CRDT encoder. The caller verifies frames and decodes
message envelopes; this reference recognises valid Step1 vector prefixes. The
caller encodes replies from publication-safe history and records actual sealed,
queued requests with `published`. Inbound-frame/application counts belong to
Reader and are not changed by request accounting.
"""

from dataclasses import dataclass, field


WRITERS = frozenset({"host", "guest"})


@dataclass
class SyncRequests:
    local_key: str
    renew_ms: int
    roles: dict[str, str] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    pending: set[str] = field(default_factory=set)
    refused: bool = False
    readiness: bool = False
    initial: bool = False
    last_request: int | None = None

    def state(self, roles: dict[str, str]) -> None:
        previous = self.roles.get(self.local_key)
        self.roles = dict(roles)
        writers = {key for key, role in roles.items() if role in WRITERS}
        self.seen.intersection_update(writers)
        self.pending.intersection_update(writers)
        if self.initial and previous not in WRITERS and roles.get(self.local_key) in WRITERS:
            self.readiness = True
        elif roles.get(self.local_key) not in WRITERS:
            self.readiness = False

    def request(self, verified_key: str) -> None:
        if self.roles.get(verified_key) not in WRITERS or verified_key in self.seen:
            return
        self.seen.add(verified_key)
        self.pending.add(verified_key)

    def accepted(self, verdict) -> int:
        """Return the frame's reply allowance, not an encoded history reply."""
        if not verdict.ok:
            self.refusal(verdict.kind, verdict.reason)
            return 0
        if verdict.kind == 1:
            self.state({key: peer["role"] for key, peer in verdict.payload["peers"].items()})
            return 0
        if verdict.kind != 0 or verdict.verified is None:
            return 0
        from yprotocols import DecodeError, SyncMessage, read_varuint

        requested = False
        for message in verdict.message:
            if not isinstance(message, SyncMessage):
                continue
            if message.subtype not in (0, 1, 2):
                break
            if message.subtype != 0:
                continue
            try:
                count, at = read_varuint(message.payload, 0)
                for _ in range(count):
                    _, at = read_varuint(message.payload, at)
                    _, at = read_varuint(message.payload, at)
            except DecodeError:
                break
            requested = True
        if not requested:
            return 0
        self.request(verdict.verified)
        return int(self.roles.get(self.local_key) in WRITERS)

    def refusal(self, kind: int | None, reason: str) -> None:
        if kind == 0 and reason not in ("bad_envelope", "unknown_kind"):
            self.refused = True

    def due(self, now: int, *, attached: bool = True, ended: bool = False,
            budget: bool = True) -> bool:
        if not attached or ended or not budget or self.local_key not in self.roles:
            return False
        if not self.initial:
            return True
        return bool(self.pending or self.refused or self.readiness) and (
            self.last_request is None or now - self.last_request >= self.renew_ms
        )

    def published(self, now: int) -> None:
        self.initial = True
        self.last_request = now
        self.pending.clear()
        self.refused = False
        self.readiness = False

    def reseat(self, local_key: str) -> None:
        self.local_key = local_key
        self.seen.clear()
        self.pending.clear()
        self.refused = False
        self.readiness = False
        self.initial = False
        self.last_request = None
