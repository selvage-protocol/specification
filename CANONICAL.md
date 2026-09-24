# Canonical form for session frames: SJ-C/1

**Status: DRAFT.** This document fixes the *byte* form of the JSON session frames that
[`PROTOCOL.md`](PROTOCOL.md) §4–§6, §10 describe, so that two independent implementations can
compare and produce the same bytes. `PROTOCOL.md` says what the members mean; this says how they
are written.

It is versioned with the prose it belongs to: this is **SJ-C/1**, the canonical form for wire
version `selvage/1`. A future wire version that changes a frame's members changes this document's
version at the same time, and [`vectors/`](vectors/) records which of the two each transcript is
valid against. §6.1 is the one passage here that is not `selvage/1`'s: it fixes the bytes of a
`selvage/2` binary frame, which is not a JSON frame at all, and it says of itself which of the two
versions reads it.

## 1. Scope

SJ-C applies to:

- **every text frame** on the session WebSocket: requests (§4.1), responses (§4.2) and events
  (§4.3), including every nested object inside `params`, `result` and `error`;
- **the `GET /meta` response body** (§2), which is JSON but not a frame.

It does not apply to binary frames, which are opaque y-protocols bytes (§7, §8) and are compared
and relayed byte for byte (§6 below), or to the WebSocket framing itself. A `selvage/2` binary
frame is the one exception the other way round: §6.1 fixes its layout, because two implementations
that read one have to agree on bytes no JSON rule reaches. The JSON inside such a frame — the room
state and the closing — is §2's, and §6.1 says which of §2's rules a value read there keeps.

## 2. The rule

A frame is conformant with SJ-C when it is the UTF-8 encoding of one JSON object with the
following properties, and nothing else: no leading or trailing whitespace, no byte-order mark,
no trailing newline.

### 2.1 Member order

Members of every object appear in **ascending order of member name**, compared as Unicode code
points. Every member name this protocol defines is ASCII, so this is also UTF-16 code-unit order
and byte order.

> **Why not the order of the tables in `PROTOCOL.md`?** Because the tables are prose, and a
> second implementation reads them; sorting is a rule that cannot be misread. The reference server
> arrived at sorted order for every nested object by accident (`serde_json::Value` maps are
> `BTreeMap`s), while its envelope and `/meta` are structs whose members are declared in ascending
> order, which is the same rule kept by hand where a map does not keep it.

### 2.2 Whitespace

No whitespace between tokens. `,` and `:` carry no surrounding space: `{"a":1,"b":2}`.

### 2.3 Strings

UTF-8. Exactly three kinds of character are escaped:

| character | written as |
|---|---|
| `"` (U+0022) | `\"` |
| `\` (U+005C) | `\\` |
| U+0000–U+001F | `\b` `\t` `\n` `\f` `\r` for U+0008/U+0009/U+000A/U+000C/U+000D, otherwise `\u00xx` with **lowercase** hex digits |

Every other character, including every non-ASCII character, is written as itself in UTF-8: no
`\uXXXX` for a printable character, no `\u` escaping of `/` or `'`, no `\u007F` for DEL. A `\u`
escape that resolves to a lone surrogate is not representable in UTF-8 and **must not** be
produced; a receiver rejects it (`bad_message`).

This is what `serde_json` and JSON's own `JSON.stringify` already do, which is why it is the rule:
it adds no new obligation to either of the two implementations that exist.

### 2.4 Numbers

A member whose type in `PROTOCOL.md` is a count (`id`, `*_ms`, and the `awareness_client_id`
of a `PeerInfo`) is a **non-negative integer** and is written in plain decimal: no `+`, no
leading zero, no fraction, no exponent, no `-0`. `1.0` and `1e2` are not SJ-C, even though they
are the same number; a producer **must not** emit them and the reference server answers one with
`bad_message` because its types for those members are unsigned integers.

`assoc` is the one signed number `PROTOCOL.md` names, and it is not a count: §8.1 gives it the
values `0` (the element *after* the position), `-1` (the element *before* it) and absent (the
same as `0`). A producer that wants the other policy on one edge of a selection writes `-1` for
it, and the rule above, a rule about counts, does not forbid that.

`id` and `awareness_client_id` **must not exceed 2 53 − 1** (9007199254740991). Above that a
JavaScript receiver (`JSON.parse` → IEEE 754 double) cannot hold the value and would answer the
wrong request; the reference types are `u64`, so it accepts what a JavaScript client cannot
round-trip. Both are non-negative.

### 2.5 The version member

`v` is present in every frame, and a frame with no `v` is rejected (§5). Its value is:

```
"selvage/" major [ "." minor ]
```

with `major` and `minor` written as in §2.4. **The canonical spelling of the current version is
`selvage/1`: a zero minor is omitted, so a producer must not write `selvage/1.0`.** A receiver
must accept both spellings: `PROTOCOL.md` §10 makes `selvage/1.0` and `selvage/1.9` the same
version as `selvage/1`, because the compatibility rule at major 1 is same-major.

### 2.6 Absent members

An optional member that has no value is **omitted**, never written as `null`. `null` for a member
that `PROTOCOL.md` types as an object, array, string or number is a malformed frame: the
reference server answers `bad_message` or `bad_params` depending on where it appears. In
particular `"params": null` is not the same as an absent `params`.

### 2.7 Array order

An array's order is part of what a frame *says* only where `PROTOCOL.md` promises one. Three
arrays are in that position:

- **`documents`**, "the room's open-document set, in first-opened order" (§6.2), which a
  comparison holds to that order;
- **a grant's `paths`** (`doc.grant`, `doc.granted`), which `PROTOCOL.md` §5 requires its
  *publisher* to write in ascending order by UTF-16 code unit and requires a server to carry
  unchanged. This one is written in an order the sender chose rather than one the server arrived
  at, which is the only thing that distinguishes it from `documents` here: the order is still a
  claim, and a comparison holds the bytes to it.
- **the sealed room state's `listing`** (§6.1), written ascending by UTF-16 code unit like a
  grant's `paths`, and the one array a `selvage/2` frame carries. Nothing else `selvage/2` writes
  is ordered: the room state's `peers` is an object whose members §2.1 orders by name, and not the
  array `PROTOCOL.md` §6.2 gives that name to.

Every other array this protocol defines is a **set**: `peers` ("No order is promised for it",
§6.2), `capabilities` (§2, §6.2, §10), `wire_versions` and `roles` (§2). There is no order to write
it in that follows from its members. There is no analogue of §2.1 here:
member names give an object a total order that is a function of its members, and an array of
`PeerInfo` has no such name. Sorting `peers` by `peer_id` does not help either, because a
`peer_id` is minted by the server and a vector that claims a frame cannot contain the value
it will be.

So a producer writes these arrays in whatever order it holds them, and two consequences
follow:

- **A receiver must not depend on the order.** §4's rule for an object is a rule for an array
  whose order the prose does not promise.
- **A comparison must not depend on it either.** A conformance comparison matches such an
  array as a multiset and brings the two sides into one order before it compares bytes, so a
  frame that lists the same members in a different order is the same frame. The vectors in
  [`vectors/`](vectors/) are byte-exact about such a frame's members and not about the order
  of one of these arrays.

### 2.8 String length

SJ-C fixes a string's bytes, not its length: a value longer than a receiver's bound is still
canonical, and a receiver that refuses it refuses the value, not the encoding. `selvage/1` fixes
one such bound on a value: a `display_name` is at most 32 **UTF-16 code units** (`PROTOCOL.md`
§5), counted in the unit a JavaScript string's `.length` reports and the unit §8.1 of that document
counts offsets in, so an astral character costs two. A name over the bound is refused `bad_params`,
the code a blank one gets; it is not a canonical-form fault, and the frame is not `bad_message`.
The control characters `PROTOCOL.md` §5 forbids in a `display_name` and a `path` are the same kind
of value refusal: the string is canonical, and it is the value a server will not carry.

A server's own limit on what it will carry is a different thing, and `PROTOCOL.md` §5 fixes no
number for it: the size of an inbound frame or message (§2.1), a listing it will not store whole, a
path longer than it will hold. A server refuses one of those with `bad_params`, and the value is
still canonical: the limit is policy, and the refusal is about the value.

## 3. Unknown members: dropped

A member that the receiver's implementation does not know is **dropped**, never preserved and
never rejected. All three implementations in this project do exactly that, because all three
deserialize into a fixed set of named members (`serde` and a plain object read, both of which
ignore the rest), and the server never copies a client's object into a frame it sends: `PeerInfo`
and the session params are built member by member.

Dropping is what makes `PROTOCOL.md` §4.1's promise true (a receiver that keeps working when a
*later* version adds a member), and it is why the vectors in [`vectors/`](vectors/) can assert the
*exact* member set of a frame: a version-locked vector is checking that no member has been silently
added or renamed. Within `selvage/1` the member set of every frame is fixed; tolerance is what
makes the transition to `selvage/2` soft, not a licence to add one now.

A receiver that preserves unknown members has not been asked to, but is not conformant with this
document: preservation is untestable (nothing observable differs) except when a peer echoes the
unknown member back, which §4.1 forbids in effect by making every frame's members enumerated.

## 4. What a receiver MUST tolerate

- **Any member order** and any insignificant whitespace: this is the whole point of §2, and a
  receiver that compares frames byte for byte is wrong, not the peer that sent them.
- **Any order of an array `PROTOCOL.md` does not order.** `peers`, `capabilities`,
  `wire_versions` and `roles` are sets (§2.7); their order is not a claim, and a receiver that
  reads one into one is wrong, not the peer that wrote them. `documents` and a grant's `paths`
  are the exceptions §2.7 names, and a receiver reads them as the order they were sent in.
- **Members it does not know**, at any depth, in either direction (§3).
- **Event names it does not know**: ignored, like an unknown member.
- **Capability names it does not know**: ignored, in `/meta` and in the `capabilities` member.
- **`x.` prefixed method, event and capability names**: reserved (`PROTOCOL.md` §10.1) and treated
  exactly like any other unknown name.
- **A `v` with an explicit zero minor**, and any minor at major 1 (§2.5).
- **A missing `params`**, which means the same as `params: {}`: for a method that requires a
  param, both are `bad_params`, and neither is a different code path.
- **A frame carrying several concatenated y-protocols messages** (§7), handled in full.
- **A close reason that is a truncation of the real message** (§11): the `session.error` event
  that precedes it carries the whole text.

## 5. What a receiver MUST reject

Rejecting means the fault code `PROTOCOL.md` §11 gives for it; a malformed frame is never silently
dropped, because silence is how version skew becomes a timeout.

- A text frame that is not one JSON object (an array, a bare string, a number, or bytes that are
  not JSON at all): `bad_message`.
- A frame with no `v`: `bad_message`, because it is not a session envelope at all (§10). A
  frame with an incompatible `v`: `unsupported_version` (§10).
- A request with no `id`: `bad_message` (§4.1).
- A first frame that is not `session.hello`, and any first frame that is not a text frame:
  `hello_required` and `bad_message` respectively (§5, §11).
- An unknown **method**: `unknown_method`, as a response, with the connection kept open (§4.2,
  §5). Unknown *members*, *events* and *capabilities* are ignored; an unknown method is not, and
  the asymmetry is deliberate.
- A member of the wrong JSON type, including `null` for a member that is not optional (§2.6).

## 6. Binary frames

In `selvage/1` a binary frame is a stream of y-protocols messages (§7); in `selvage/2` it is one
**sealed frame** (§6.1) whose plaintext is that same stream. Either way it is **not**
canonicalised, re-encoded or inspected: the server relays the bytes it received, unchanged, to
every other member of the room, and the vectors assert byte equality in both directions. Two
implementations conform on the document-sync path when the bytes they exchange encode the same
messages, which is a property of `y-protocols`, not of SJ-C.


### 6.1 Sealed frames (`selvage/2`)

**Scope.** This subsection is `selvage/2`'s and nothing above it is: a `selvage/1` binary frame is
the bare y-protocols stream §6 describes, and a `selvage/2` one is an envelope whose plaintext is
that same stream. It fixes the envelope's bytes, the two values the invite URL's fragment carries,
and the order a receiver reads the bytes in. `PROTOCOL.md` §7.1 says what the envelope carries and
§5.1 says where the two values sit in a link. Every rule here is written from the design rather
than observed on a wire.

**The envelope.** A `selvage/2` binary frame is exactly one envelope: nothing precedes it, nothing
follows it, and its bytes are these, in this order.

| field | width | meaning |
|---|---|---|
| `key_id` | 8 bytes | the sender's key id |
| `kind` | `varUint` | `0` a sealed y-protocols stream, `1` the sealed room state, `2` the sealed closing, `3` the sender's sealed holds, `4` the sender's sealed session-key announcement |
| `epoch` | `varUint` | `0`; reserved and unused in this version |
| `counter` | `varUint` | the sender's counter under the key it signed with |
| `nonce` | 12 bytes | fresh for this frame |
| `ciphertext` | `varUint8Array` | the AEAD's output, its 16-byte tag included |
| `signature` | 64 bytes | Ed25519 over the signature input |

`varUint` and `varUint8Array` are `PROTOCOL.md` §7's: LEB128, and a byte length in front of the
bytes. The three fixed-width fields are fixed: a frame whose bytes run out inside one, or that has
any byte left over after the signature, is **malformed**, and a receiver drops it (below) rather
than reading it as a variant of this layout.

**The two keys in the fragment.** An invite URL's fragment carries two values, both in one
encoding: **base64url** (RFC 4648 §5, the URL- and filename-safe alphabet) without padding, over a
**32-byte** value.

- **`k`** is the **room key**: 32 random bytes minted when the room is minted, from the platform's
  CSPRNG, and the whole of the confidentiality.
- **`h`** is the **host key**: the 32 bytes of the Ed25519 public key whose private half the host
  holds. It is the room's root of trust — it verifies a room state and a closing, and nothing else
  verifies those.

**The encoding is canonical, and a value it cannot produce is not a key.** Over 32 bytes it is 43
characters, the last of which carries four bits of the value and two that are zero (RFC 4648 §3.5),
so a key's final character is one of `AEIMQUYcgkosw048` — the sixteen whose six-bit value has its
low two bits clear — and a 43-character string whose last character is any other spells no 32-byte
value however leniently a decoder reads it. A receiver refuses such a value where it appears rather
than resolving it to something a strict decoder would not: in a sealed payload a `key` or a `peers`
name that is not a 32-byte key is refused `bad_payload` at §6.1's step 8, whose member-type rule
fixes the encoding there, and the fragment's `k` and `h` take the local refusal `PROTOCOL.md` §5.1
fixes before a socket is opened, because a value nothing encodes is not a key there either. Two
spellings of one key are one key only to a lenient reader, and a strict reader and a lenient one
reaching opposite verdicts on the same bytes is the disagreement this rule removes.

The **frame key** is derived from the room key once per room, and seals every frame of every kind:

```
frame_key = HKDF-SHA256(ikm = room key, salt = the room id in UTF-8, info = "selvage/2 frame", L = 32)
```

There is no per-sender sealing key, because every member holds the room key and could derive one
anyway. The host key is **not** derived from the room key: a signing key every guest can compute
from its own link is a key every guest can forge with.

Each connection also mints a **session keypair**, Ed25519 again, in memory and never persisted,
whose public key the host's room state commits and which the connection announces in a `kind = 4`
frame signed by that key (`PROTOCOL.md` §7.1). A session key signs one peer's `kind = 0`, `kind = 3`
and `kind = 4` frames; the host key signs `kind = 1` and `kind = 2`, and a receiver refuses those
two from any other key.

**What is authenticated.** The AEAD is **AES-256-GCM**; the tag is the 16 bytes GCM appends to the
ciphertext; the nonce is the envelope's 12 bytes, drawn fresh from the CSPRNG for every frame and
never derived from a counter, a key, or a value another sender also holds. A random 96-bit nonce is
safe under one key only up to a bound on how many frames that key seals, and here the key is shared:
every sender in a room seals under the one frame key, for as long as the room lives, and `epoch` —
the member a rekey would move — is reserved and unused in this version. SP 800-38D §8.3 bounds the
invocations of one key with random nonces at **2³²**, and that bound is the room's, summed over
every sender, and not any one sender's. No room an editor produces approaches it (a sustained
thousand frames a second takes more than a month to reach it), but nothing in this version resets
it, so the peers enforce it, because they are the parties that can count. The relay delivers every
frame to every other connection, so a client that counts the binary frames it is delivered in the
room and the frames it seals itself counts every frame sealed while it was connected, less whatever
the relay dropped on the way to it. A frame a client re-sends unchanged (`PROTOCOL.md` §7.1's
re-send of a state) is not sealed again and is not counted twice.

- **The host's count is the room's.** The host is the one peer present from the room's first frame:
  it mints the room, and its state is the first frame sealed under the frame key. A host **MUST**
  keep that count from the mint for the life of the room, across a dropped socket as across any
  other reconnect, and a host that persists its host key **MUST** persist the count with it, beside
  its `issued` (`PROTOCOL.md` §7.1), and continue it rather than restart it. It **MUST** write the
  persisted count at least once every `awareness_renew_ms` while the count moves, so that a host
  that dies loses at most one renewal interval of it.
- **Every other client's count is a backstop.** A client that joined after the mint counts from the
  moment it was seated and cannot know what was sealed before, so its count is below the room's. It
  **MUST** keep it all the same, for the life of its session and across its own reconnects, because
  it is the count that still runs while the host is away.
- **At the frame budget, 2³¹** — half the bound, which leaves room for every frame the host did not
  see — a client whose count has reached it **MUST NOT** seal another frame under the frame key: a
  host publishes one closing (`PROTOCOL.md` §7.1), whose `issued` ends the room for every peer
  holding its state, and every client, the host included, ends its session and says why
  (`PROTOCOL.md` §13.10). A room that must go on is a new room, minted with a new room key.

**What the host's count misses, and the charge that bounds it.** A host that is away counts
nothing, so the frames sealed during its absence are the ones its count lacks, and absences add up:
a host that leaves and returns inside its host-away window keeps the room going, and nothing on the
wire carries the frames it missed back to it. One absence is bounded by the peers' own rules — a
guest ends its session once its host-away window, `awareness_expire_ms`, has passed
(`PROTOCOL.md` §13.8), and a client that joins while the host is away ends once its no-state window
has passed (§13.3) and publishes nothing but its announcement meanwhile (§13.1) — so one absence
lasts at most about two windows. The number of absences is bounded by charging for them:

- **Every return costs the host's count the absence charge, 2²¹ frames.** A host **MUST** add it to
  its count each time it resumes the room after an absence: on every reconnect (`PROTOCOL.md` §9.1)
  and on every reload that continues a persisted count. The charge is a fixed ceiling on what one
  absence can hide rather than a measurement of it: 2²¹ frames in two 30 s windows is some 35 000
  frames a second across the whole room, far above anything an editor produces, and a room whose
  host has resumed 1024 times has spent the margin on charges alone and closes at the budget.
- **The charge is not read from the peers.** Each frame's counter would say how many frames its
  sender sealed while the host was away, but a counter is its sender's own word, and a peer holding
  the room key that inflated one would close the room at will. The charge is the host's alone.
- **A persisted host record without a count is a spent budget.** A host that resumes from a record
  written before the count existed cannot know what the room has sealed, so it **MUST** read the
  count as the frame budget itself: it publishes its closing at once and ends, and the room that
  must go on is a new one.

What the charge cannot bound is a client that keeps sealing after its own window has passed, which
is a client outside §13.8 rather than a gap in the count. Its **associated data** is:

```
aad = varUint8Array("selvage/2") ‖ varUint8Array(room id) ‖ varUint(kind) ‖ varUint(epoch) ‖ varUint8Array(key_id)
```

Each byte string in it is length-prefixed, so that no two different inputs write the same bytes:
the version, the room, the kind, the epoch and the key id are all authenticated by the AEAD itself,
before the signature is looked at.

**What is signed.** The **signature input** is the associated data followed by the envelope's three
authenticated values, in the order the envelope writes them:

```
signed = aad ‖ varUint(counter) ‖ varUint8Array(nonce) ‖ varUint8Array(ciphertext)
```

The signature is **Ed25519** (RFC 8032) over exactly those bytes, and it is Ed25519's own 64-byte
encoding of the result. The signature covers the counter, the nonce and the ciphertext as well as
the AAD, so one verification checks both the AEAD's input and its output. That is why a corrupted
tag is a **signature** failure and not a tag failure, and why this version has no refusal reason of
its own for one.

**`key_id`.** The first **8 bytes of the SHA-256** of a public key's 32 bytes. It is not carried as
a member anywhere and is not signed on its own: the room state carries the keys themselves and a
receiver derives each id. It is an **index** into the keys a receiver holds and not an identity — a
receiver resolves it by verifying the signature against every key that id names, and attributes the
frame to the key that verified. A collision therefore costs a verification that fails, and never a
mis-attribution.

**The counter, the mark and `issued`.** A sender **MUST** give its first frame under one key the
counter `1`, and each later frame under that key a strictly greater one. A receiver keeps, for each
key it holds — every key its applied state commits and every key it has accepted an announcement
from — the highest counter it has **not refused**, starting at `0`: a frame it refuses never
moves a mark, whatever step refused it, and neither does a frame a peer rule *ignores*. An ignored
frame is one that is neither applied nor refused — a closing that arrives while the receiver holds
no verified state (`PROTOCOL.md` §13.10) — and it leaves both marks where they were, so the state
below it is still applied when it arrives. What the rule buys is that a relay cannot poison a mark
with a forged frame and lock out the frames that follow it, and that the same bytes are refused for
the same reason however often they arrive. A `kind = 0`, `3` or `4` frame at or below the mark is
refused whatever else is right about
it, and there is no window around the mark: the transport `PROTOCOL.md` §7 describes is ordered, one
connection's frames arrive in the order they were sent, so a counter that does not advance is a
replay. A **gap** is not a fault — the relay may drop frames — and a receiver **MUST** accept a
counter above the mark however far above it is. **A receiver keeps both marks — this one and the
`issued` mark below — for as long as it holds the room's keys**: they are what the room has told it
rather than facts about the socket it heard them on, a reconnect does not reset them
(`PROTOCOL.md` §9.1, §13.10), and a client that reset them would apply a replayed state and obey a
replayed closing.

**A key an announcement names is held until a state decides it.** A receiver keeps a mark for a key
an applied state commits — now or earlier — for as long as it holds the room's keys, and for a key
only an announcement has named until the state that does not commit it, because an announcement is a
claim about a key and the state is the room's answer to it (`PROTOCOL.md` §7.1). The mark beside an
uncommitted key guards that announcement alone, since a `kind = 0` or `3` frame from such a key is
refused `uncommitted_key` whatever mark stands against it. `PROTOCOL.md` §13.3 says what a receiver
may do when a peer announces keys without bound.

The two kinds the host signs are ordered by the `issued` member of their own plaintext instead, and
their counter is not read: the mark above guards `kind = 0`, `3` and `4` frames, and a receiver
checks nothing about the counter of a room state or a closing beyond its being one of the bytes the
signature covers. A room state and a closing come from the host key, which is minted with the room
and outlives the connection any one of them was published on, so it is `issued` and not a per-key
counter that says which of two states is the later. A receiver keeps
the highest `issued` it has accepted from the host, also starting at `0`, and refuses a room state
or a closing that is not above it. The host **MUST** write the `issued` `1` on the first state it
publishes — the mark starts at `0`, so a host whose series began there would have every state it
published refused `stale_issued` — a state whose `issued` is above the one it replaces, and a
closing whose `issued` is above every state it has published, which makes `2` the first `issued` a
closing can carry.

**Reading an envelope.** A receiver reads the bytes in this order and reports the first step that
refuses the frame.

| # | step | reason |
|---|---|---|
| 1 | the layout: every field present, nothing left over | `bad_envelope` |
| 2 | `kind` is one this version defines | `unknown_kind` |
| 3 | `epoch` is one this version defines | `unknown_epoch` |
| 4 | the key: a `kind = 0` or `3` frame is signed by a committed session key, a `kind = 1` or `2` frame by the host key, and a `kind = 4` frame by the key it announces, whose 8-byte id the envelope's `key_id` must be | `uncommitted_key` |
| 5 | the mark, for `kind = 0`, `3` and `4` | `replayed_counter` |
| 6 | the signature | `bad_signature` |
| 7 | the AEAD opens | `bad_aead` |
| 8 | the plaintext is an object of the members its kind fixes, each of the type that kind gives it | `bad_payload` |
| 9 | `issued`, for `kind = 1` and `2` | `stale_issued` |
| 10 | the sender's role, for a `kind = 0` frame carrying document content: a key the state gives role `viewer` may not send one | `unauthorised_content` |

Those reasons are the receiver's **local report** and not wire values. Nothing about a refused
frame is sent, no connection is closed, no code of `PROTOCOL.md` §11 is involved, and a frame that
is dropped leaves the session as it was. The order is normative because the report is observable:
the same bytes refused at two different steps would be two reports for one frame. Two consequences
worth naming: a frame whose counter does not advance is a replay whether or not its signature
would have verified, and `bad_aead` — reachable only by a sender that signs a ciphertext its own
room's key cannot open — is a report about a sender's bug rather than about an attack.

**`kind = 4` is read in its own order, and it is the one kind that is.** Its signer is inside its
plaintext, so a receiver must open the AEAD before it can verify anything, and it walks the table's
steps in this sequence instead: the layout, the kind, the epoch, the AEAD under the frame key (7),
the plaintext as the announcement's object (8), the key (4) — the announced key's 8-byte id must be
the envelope's `key_id`, or the frame is attributed to no key at all — the signature over that key
(6), and last the mark for it (5). What is not read for this kind is the step-4 rule about
*commitment*: the announcement is the frame that makes a key knowable in the first place, so a
receiver accepts one from a key no state commits, which is the whole of what it is for
(`PROTOCOL.md` §7.1, §13.1). Everything else above holds of it unchanged.

**Step 10 is the one reason here that is not a property of the envelope.** The frame is authentic,
its key is committed, its counter advanced and its AEAD opened; what refuses it is the peer rule
that a `viewer`'s edits are not the room's (`PROTOCOL.md` §13.5). It is in this table because the
report is one vocabulary, and it is read last and only for a `kind = 0` frame whose plaintext
carries a SyncStep2 or an Update: a viewer's SyncStep1 and its awareness are applied. The last two
steps are each reached by one kind — 9 only by `kind = 1` and `2`, which carry the `issued` that
orders them, and 10 only by `kind = 0`, which does not.

**Step 8 is the added one, and it closes a hole rather than widening one.** `kind = 1`, `2`, `3` and
`4` each fix a plaintext that is one JSON object with a member set (`PROTOCOL.md` §7.1, below), and
nothing else in this document refused a plaintext that is not that object: an authentic frame from a
committed key, at an advancing counter, whose AEAD opens, and whose plaintext is a bare string or a
list of the wrong shape. The reasons above are about the envelope and one peer rule, so none of them
named it, and a receiver dropping it silently would be a refusal §13.2 does not let a client leave
unreported (`PROTOCOL.md` §13.2, §13.11). `bad_payload` is that report. Reading the plaintext at
step 8 rather than at step 1 is deliberate: the envelope's layout is what `bad_envelope` is about,
and the plaintext is not visible until the AEAD opens.

**Step 8 reads a member set and each member's type, and no value a rule elsewhere governs.** What it
refuses is a plaintext that is not an object of the members its kind fixes with each member of the
type that kind gives it: a bare string, a list, an object missing one of them, or one carrying a
member of the wrong type. A **value** of the type its kind gives it that another rule governs is not
read here, because that rule is a different rule with a different consequence: a listing's path and
a holds' path are held to `PROTOCOL.md` §5's rule for one, and a receiver that will not carry such a
path **drops the path** — it is not shown, not offered and never written out as a name — while the
rest of the listing, the rest of the set and the state's roles are applied (`PROTOCOL.md` §13.3,
§13.7). A receiver that refused the frame for one of those values instead would disagree with a
receiver that drops it on the very same bytes: such a string is canonical (§2.3 forbids a `\u007F`
*escape* and not the character, and every `Cc` character is legal inside a JSON string), U+007F is a
legal character in a filename on the platform a client runs on, and two conforming receivers
reaching opposite verdicts about one state is the one thing the `issued` ordering exists to prevent.
A path over §13.3's 4096-byte bound is the same kind of value and takes the same read: §2.8 keeps a
value over a bound canonical and leaves the bound to the receiver that will not carry it.

**The kinds.** `0` carries the y-protocols stream of `PROTOCOL.md` §7 as its plaintext, whole: one
binary frame is one envelope, and the messages inside it are that section's, exactly as they are
in `selvage/1`. `1`, `2`, `3` and `4` carry one JSON object each, as the UTF-8 bytes of its canonical
form: `1` the room state, `2` the closing, `3` the sender's holds, `4` the session-key
announcement. Values above `4`, and every `epoch` but `0`, are this version's to leave unused: a
later revision defines one, and a receiver of this version refuses a frame that uses one rather than
reading it by the nearest rule it knows.

**The four sealed payloads.** All four are JSON objects, read as §2 writes one, and none is a
session frame: none carries `v`, and §2.5 is not theirs. §3 is: a receiver drops a member it does
not know and does not refuse the value. A producer **MUST NOT** write a member this version does not
define — the member sets below are fixed, and §2.7's order for `listing` is part of them.

The **room state** has exactly three members. `PROTOCOL.md` §7.1 says what each means:

```json
{"issued":1,"listing":["README.md","src/main.rs"],"peers":{"GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM":{"peer_id":"p-0f1e2d3c4b5a6978","role":"host"}}}
```

- `issued`: a count in §2.4's form and inside its bound, `1` on the first state the host
  publishes. `0` is a count and not a malformed member: a receiver's mark starts at `0`, so a state
  or a closing at `0` is refused `stale_issued` at step 9 above, which is where the value is read.
- `listing`: an array of paths, each held to `PROTOCOL.md` §5's rule for one, written ascending by
  UTF-16 code unit. A path that breaks that rule is dropped at the receiver, and neither the frame
  nor the state is refused for it (§13.3, and step 8 above).
- `peers`: an object whose names are **public keys** — 32 bytes of Ed25519 in the fragment's
  encoding (base64url, unpadded, 43 characters) — each value an object of two members: `role`, one
  of `host`, `guest` and `viewer`, and `peer_id`, the seat the host believes the key's holder is
  seated under. The key is what attributes a frame and the key is what the role belongs to; the
  `peer_id` is a **label** the host wrote and not a binding, because the server's `peer_id` is its
  own word and it can be neither the key's source nor its authority (`PROTOCOL.md` §7.1, §13.4).
  Where one `peer_id` is named under two keys — the shape a host's error takes here, since a key can
  appear only once as a name — a receiver reads the entry whose key comes first in UTF-16 code-unit
  order, so that two conforming receivers read one state the same way. The same read applies to a
  state that gives more than one key the role `host`: `PROTOCOL.md` §7.1 has a host write exactly
  one, and a receiver handed two reads the host's connection as the one whose key comes first in
  that order.

The **closing** has exactly two members:

```json
{"closing":true,"issued":2}
```

`closing` is `true`; `issued` is a count in the same form, above every state the host published.

The **holds** message has exactly one member:

```json
{"holds":["README.md","src/main.rs"]}
```

`holds` is the set of paths the sender keeps open — the same kind of value and the same kind of
claim as a `selvage/1` `doc.open` path, and the thing `PROTOCOL.md` §13.7 leases. It is replaced
wholesale by the next holds message under the same key, its order carries nothing (§2.7), and a path
in it is held to `PROTOCOL.md` §5's rule for one, and a path that breaks it is dropped at the
receiver rather than refused (§13.7). No member names the peer the set belongs to: the
sender is the key that signed the frame, so a receiver attributes it by the verification it already
made rather than by a value inside the plaintext.

The **session-key announcement** has one required member and one optional one:

```json
{"key":"GTyGPrJPL8dWM6BbKJSHRp1PNSjzbSpwiACHGklgfeM","role":"guest"}
```

`key` is the announcing connection's session public key, 32 bytes in the fragment's encoding, and it
**MUST** be the key that signed the frame: a receiver verifies the frame against the value it carries,
and the envelope's `key_id` **MUST** be that key's id (the order above says where each of the two is
read). Nothing else in the frame is a claim about identity, which is what makes the announcement
self-certifying rather than an identification: it names no peer and no server-supplied value, so a
receiver learns from it that a key exists and that whoever holds it speaks, and nothing more
(`PROTOCOL.md` §7.1, §13.1, §13.4). `role`, when it is there, is the role the sender believes it has
been given — `guest` or `viewer`, and never `host` — because a state names exactly one `host` entry
and it is the host's own connection's, so the role is not a peer's to declare. A host **SHOULD**
honour a declaration (`PROTOCOL.md` §7.1).

All four objects are canonical as written — §2's form, with §2.7's order for `listing` — and a
plaintext is the UTF-8 bytes of one.

**The envelope's own cost.** A frame costs 104 bytes over its plaintext while the ciphertext's
length fits one varint byte — a plaintext of up to 111 bytes — and 105 from there: 8 for the key
id, 1 each for the kind, the epoch and the counter, 12 for the nonce, 1 for the ciphertext's
length, 16 for the GCM tag and 64 for the signature. Measured on the layout above with the fixture
values [`NOTES.md`](NOTES.md) §B.31 records.

## 7. `GET /meta`

The `GET /meta` body is an object with the members of `PROTOCOL.md` §2 and is canonical when it
follows §2 like any other frame. It is **not** a session frame: it has no `v`, it is not part of
the envelope, and it is read for negotiation rather than compared. A client **must parse** it and
**must not** depend on its bytes, its whitespace or its member order.

An unreachable `/meta` is not a negotiation failure: a client that cannot read it connects
anyway and lets `session.hello` decide. A reachable `/meta` whose `wire_versions` do not include a
version the client can speak **is** a failure, and it is a failure before the socket is opened.

## 8. How this document is used

- [`schema/`](schema/) describes each frame as JSON Schema, so a frame can be validated without a
  server.
- [`vectors/`](vectors/) carries transcripts of real bytes, each labelled with the wire version and
  the SJ-C version it was recorded under. Each frame in a transcript is in canonical form, and a
  test in the [reference server](https://github.com/selvage-protocol/reference_server) asserts that
  it produces and accepts exactly those bytes: the bytes of §2, and for an array §2.7 leaves
  unordered, the multiset of its members rather than the order they arrived in. A conformance
  runner for another implementation compares canonical forms rather than bytes if it prefers;
  the vectors are byte-exact so that it can.
- A second implementation that emits SJ-C bytes for the same members emits **the same bytes** as
  the reference server.
