# What the vectors do not cover

Nothing here is a conformance suite for **two** clients. The wire corpus holds a server to its
transcripts, and every `expect` in it is the server speaking. The peer corpus holds **one**
client to the rules of `selvage/2`, and it does it twice over: its frame vectors hold any
*receiver* to `CANONICAL.md` §6.1 — the envelope, the key schedule, the counter mark, the ten
reasons — with no client at all, and its decision vectors hold a real client to what it does
with a frame it has received or a link it is handed, which is what a subject is for. Two of those
decisions are made before a socket is opened at all (§5.1's fragment), so what a subject shows for it
is its refusal and that it seated nothing; the rest are frames. What no vector here can show is that
two clients **agree**: a vector can hold a client to a rule it states, and it
cannot show that two implementations reach the same state. That stays the interop test's job
(`vscode_client/test/interop-v2.test.ts` against
`reference_server/crates/harness/examples/interop_peer.rs`), which is a convergence proof and
not a conformance one, and it is the reason neither is sufficient alone.

Three more limits, said rather than left to be discovered. A vector asserts the bytes of a
frame; a conforming implementation whose Ed25519 **randomises** its signatures — Safari's does —
produces a different 64-byte signature for the same frame, so what such an implementation is
held to is that its signature **verifies** over §6.1's input, which is what `NOTES.md` §B.31
records. A refusal of a link is the other way round: the vector holds the client to the *naming*
in its own words and not to the sentence, because `PROTOCOL.md` §2 and §5.1 leave the sentence to
the client. The peer corpus's mutation census shows that a vector catches a named mutation of the
runner's own reader; it does not show that a differently-wrong client fails. And the runner's
frame layer is a **format** check and a **self-consistency** check rather than a second
implementation: it was written from the same prose as any client will be, so agreement between
them is weak evidence.

Client behaviour beyond that (renewal, expiry, reconnection, the adapter seam, the two clients'
agreement) is tested in
the reference server's [`crates/harness/tests/`](https://github.com/selvage-protocol/reference_server/tree/main/crates/harness/tests)
and in the clients' own suites. The cases these vectors do not reach are listed in
[`NOTES.md`](../NOTES.md) §B, and a second implementation will find more.
