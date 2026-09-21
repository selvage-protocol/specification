# Canonical form for session frames: SJ-C/1

**Status: DRAFT.** This document fixes the *byte* form of the JSON session frames that
[`PROTOCOL.md`](PROTOCOL.md) §4–§6, §10 describe, so that two independent implementations can
compare and produce the same bytes. `PROTOCOL.md` says what the members mean; this says how they
are written.

It is versioned with the prose it belongs to: this is **SJ-C/1**, the canonical form for wire
version `selvage/1`. A future wire version that changes a frame's members changes this document's
version at the same time, and [`vectors/`](vectors/) records which of the two each transcript is
valid against.

## 1. Scope

SJ-C applies to:

- **every text frame** on the session WebSocket: requests (§4.1), responses (§4.2) and events
  (§4.3), including every nested object inside `params`, `result` and `error`;
- **the `GET /meta` response body** (§2), which is JSON but not a frame.

It does not apply to binary frames, which are opaque y-protocols bytes (§7, §8) and are compared
and relayed byte for byte (§6 below), or to the WebSocket framing itself.

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

An array's order is part of what a frame *says* only where `PROTOCOL.md` promises one. Two arrays
are in that position:

- **`documents`**, "the room's open-document set, in first-opened order" (§6.2), which a
  comparison holds to that order; and
- **a grant's `paths`** (`doc.grant`, `doc.granted`), which `PROTOCOL.md` §5 requires its
  *publisher* to write in ascending order by UTF-16 code unit and requires a server to carry
  unchanged. This one is written in an order the sender chose rather than one the server arrived
  at, which is the only thing that distinguishes it from `documents` here: the order is still a
  claim, and a comparison holds the bytes to it.

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

A binary frame is a stream of y-protocols messages (§7). It is **not** canonicalised, re-encoded
or inspected: the server relays the bytes it received, unchanged, to every other member of the
room, and the vectors assert byte equality in both directions. Two implementations conform on the
document-sync path when the bytes they exchange encode the same messages, which is a property of
`y-protocols`, not of SJ-C.

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
