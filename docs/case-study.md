# A case study: the decisions behind the Selvage protocol

This is an informative engineering narrative. `PROTOCOL.md` and `CANONICAL.md` are the
specification; nothing here changes them, and where this page and the specification disagree, the
specification wins. It explains why a few of the protocol's shapes are what they are, and what the
public evidence for them covers.

## The layer the specification covers

The Selvage Session Protocol is the session layer above document sync: rooms, participants, roles,
which documents are open, presence, join and leave (`PROTOCOL.md` §1). It carries document sync and
awareness payloads and does not define them; those are y-protocols, which the specification cites
normatively. That division is the scope statement rather than a claim about other systems.

§1.1 says a behavior the document does not state is unspecified and that a requirement is a
sentence a reader can observe being met. The protocol has one wire version, `selvage/2` (§10), so
there is one conformance and no compatibility table to write.

## One session document, path-named text

The replicated state is one `Y.Doc` for the session with one `Y.Text` root named by each open
workspace-relative path (`PROTOCOL.md` §7, §8.1). Yjs updates encode root names and parent
references; the sealed envelope has no separate document address. The public
[anchors fixture](anchors.md) includes an update that encodes the root name `src/main.rs`.
Changing an outer path cannot redirect those unchanged update bytes to a different text.

Sync covers the session document's accumulated history, not a subset of paths. A host may introduce
a file's text when it is needed and a client may materialize only the paths it needs, but once
text is in the session document the sync exchange can carry it to any peer, whether or not that
peer holds the path open, and releasing a hold or removing a path from the listing deletes neither
the text nor its history (§7, informative; §13.3, §13.7). That is what lets lazy content and a
project-wide grant coexist.

## A relay that cannot read

The server is session-aware and payload-opaque (`PROTOCOL.md` §3). It parses text frames, owns
rooms and membership, and relays binary frames byte for byte. Document, cursor and listing payloads
are sealed under a key in the invite URL's fragment, and a receiver attributes a frame to the key
whose verification succeeded rather than to a value the server wrote.

The server holds no CRDT state and assigns no host, editor or viewer role. It can forge its own
membership events, including `peer.left`, and can drop, delay or replay genuine sealed frames
(§3). It cannot forge a valid host signature. Receiver marks reject previously observed history,
but do not establish freshness for a receiver with no verified state (§7.1, §13.10).

What the relay still learns is stated rather than hidden: the two fields a sealed envelope carries
in the clear at its front, `kind` and `key_id`, let it group frames by signing key and see which
connection publishes a room state (`CANONICAL.md` §6.1; `PROTOCOL.md` §3).

## Credentials in the fragment

A room's permission is its token, its confidentiality is the room key, and its root of trust is the
host's public key. The token travels in the query and the two keys in the fragment, which a user
agent never sends (`PROTOCOL.md` §5.1), so the server operator and the network path are not in the
set that can read a room. §12 states the cost with the benefit: a leaked URL is a leaked room and
its keys, because the fragment is in the link and in the address bar and in whatever carried it.

Host authority is a signature and not a claim. No connection is seated as the host, no server frame
marks one, and the host is whoever holds the private half of the key the invite names (§1.2, §7.1,
§13.4). That is what makes "the host's working copy is the truth" enforceable between peers without
the server deciding anything.

## Roles enforced at the endpoints

Roles are the host's signed statement about a key, and a receiver reads a role from the key that
verified against the applied room state (`PROTOCOL.md` §7.1, §13.4). A `viewer` may read the room,
send its SyncStep1, its awareness and its holds, and must not send document content; a receiver
must not apply content from a key the state gives `viewer` and reports it `unauthorised_content`
(§13.5, §13.9).

The residual is stated with the rule. A `viewer` holds the room key and can produce a perfectly
signed frame. The guarantee is that no conforming client applies it, which is a denial between
conforming peers rather than a boundary a server draws. §13.9 adds the history invariant that makes
the rule durable rather than a ban on immediate messages: a viewer's local mutations must never
enter published history, even after a later promotion or a new key.

## Counters, replay and editions

A frame's counter starts at 1 under each key and strictly increases, and a receiver keeps the
highest counter it has not refused, starting at 0. A frame at or below the mark is a replay
whatever else is right about it, and a gap is not a fault, because the relay may drop a frame
(`CANONICAL.md` §6.1). The two frames the host signs, the room state and the closing, are ordered
by their own `issued` instead, because the host key outlives any one connection.

Two host connections can publish states at the same `issued`. Receivers cannot order them and
can retain different listings until a higher edition arrives (§13.3). A client reports an
equal-`issued` refusal distinctly and retains its replay and edition marks across reconnects
(§9.1, §13.10).

A new receiver has a separate freshness limitation. With no verified state, it can accept an old
genuine state and then a genuine closing whose edition exceeds that old state. The closing can end
that receiver's session even while the room remains live (§7.1, §13.10). Persisted marks protect
history the client has already observed; they cannot prove that its initial state is current.

## The disk grant and confinement

The host picks a folder and that folder is the grant. The listing is the files under it, and a peer
read is served from the same bound (`PROTOCOL.md` §12). What a host leaves out is name-based and
policy, plus the folder's own ignore files; the desktop clients' public docs describe their
versions of that policy and its limits in detail
([VS Code](https://github.com/selvage-protocol/vscode_client/blob/main/docs/mirror-and-fetch.md),
[Neovim](https://github.com/selvage-protocol/nvim_client/blob/main/docs/grant.md),
[the page](https://github.com/selvage-protocol/web_client/blob/main/docs/hosting-from-the-page.md)).

The protocol supplies no confinement, because a path on the wire is a name no server resolves
(§12): an implementation that reads the host's disk confines the path itself, and the folder is the
bound. The clients' docs name the residual honestly, including a component a concurrent local
writer swaps for a symlink between a check and a read. A test for that seam has to try to leave the
folder, with a link or `..` rather than only a happy path.

## The save seam

Desktop guests can mirror the room into a real directory for native tools; saving writes that
local mirror. The hosting client separately saves settled room edits to its working folder
([VS Code](https://github.com/selvage-protocol/vscode_client/blob/main/docs/mirror-and-fetch.md)).
Browser guests need no local folder. Browser hosting uses a chosen folder and checks for stale
files because it has no watcher
([hosting from the page](https://github.com/selvage-protocol/web_client/blob/main/docs/hosting-from-the-page.md)).

The public behavior does not settle every editor I/O risk.
A host that keeps its own deleted file open is the case worth testing: the listing drops the path,
and a later remote edit can autosave the file back. A client should test its own version of the
seam rather than read a green corpus as evidence about it: delete and rename a file under the host
while the room holds it, refuse a save at the filesystem, and close and reopen a document. Those
are editor I/O facts the vector corpus cannot see, and a fixed product claim about them needs its
own test.

## Unicode and anchors

A cursor is a yjs `RelativePosition` and never an offset, so each receiver recomputes the position
against its own replica and a caret survives a concurrent edit (`PROTOCOL.md` §8.1). Two libraries
write one position two ways, with a scope beside the element or the element alone, and a receiver
must resolve both. The offset unit is fixed at the adapter seam and must be the editor's unit;
where that is the UTF-16 code unit, a client must configure its CRDT library for UTF-16, or a
non-ASCII document yields a wrong anchor before concurrency even enters
(§8.1.1; `NOTES.md` §A.11 is informative on what the reference client does).

## Shared engine and independent clients

The VS Code, Neovim, browser and MCP clients use copies of one TypeScript engine. The JetBrains
client has an independent Kotlin session engine and an IntelliJ Platform adapter
([its README](https://github.com/selvage-protocol/jetbrains_client/blob/main/README.md)). Its
[`CrossImplementationTest.kt`](https://github.com/selvage-protocol/jetbrains_client/blob/main/engine/src/test/kotlin/dev/dontblameme/selvage/engine/CrossImplementationTest.kt)
exercises Kotlin and TypeScript peers through a real server.

A separate pair is covered by
[`vscode_client/test/interop-v2.test.ts`](https://github.com/selvage-protocol/vscode_client/blob/main/test/interop-v2.test.ts):
the TypeScript engine and the Rust client driven by
[`interop_peer.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/examples/interop_peer.rs).
These are interoperability tests for those specific pairs, not universal conformance proofs.
[`vscode_client/test/crossing.test.ts`](https://github.com/selvage-protocol/vscode_client/blob/main/test/crossing.test.ts)
separately checks the public
[`anchors fixture`](https://github.com/selvage-protocol/specification/blob/main/vectors/anchors/relative-position.json).

## Evidence, and its scope

The public tests that exist are the wire replay in [`reference_server/crates/harness/tests/vectors.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/vectors.rs),
the peer frame and decision tests in [`peer_vectors.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/peer_vectors.rs)
and [`decisions.rs`](https://github.com/selvage-protocol/reference_server/blob/main/crates/harness/tests/decisions.rs), the language-neutral
replay and validator in this repository, and the clients' own suites. They cover what each states
and no more: a corpus pass is a statement about one layer's subject, not a claim that the project
is accepted, universally interoperable, or production ready, and the cases no vector reaches are
listed in [What the vectors do not cover](what-the-vectors-do-not-cover.md) and in `NOTES.md` Part B.

A recorded walkthrough of a first session has not been produced, so this page has none. Human
first-room acceptance is also still outstanding; the public evidence here is the tests and their
stated scope.
