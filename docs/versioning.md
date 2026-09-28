# Versioning

Three numbers move together, and nothing here is allowed to move independently of the prose:

1. **The wire version**, `selvage/2`, in the `v` member of every text frame and in
   `GET /meta`'s `wire_versions`. It changes when a member's meaning or presence changes, and the
   protocol has one of them.
2. **The canonical form version**, `SJ-C/1`, in `CANONICAL.md` at the repository root. It changes
   when the *bytes* of a frame change: a different member order, a different number form, even
   when the members' meaning does not.
3. **The spec revision**, the git history of this repository. Prose edits that change no bytes
   are commits, not version bumps.

Each release tags the commit it was cut from and attaches one zip holding `schema/`, `vectors/`,
`PROTOCOL.md`, `CANONICAL.md` and `LICENSE`. That zip is what an implementation pins against.

Every vector carries both versions in `selvage` and `canonical`, and the runner refuses to replay
a vector bound to anything else. That is the version binding: a vector set without
one rots, because nothing can say whether it is out of date or the implementation is wrong.

The version member itself is `PROTOCOL.md` §10: one version, `selvage/2`, written exactly so in
every frame, with nothing to negotiate and no other value a receiver reads as this protocol's
(`CANONICAL.md` §2.5).
