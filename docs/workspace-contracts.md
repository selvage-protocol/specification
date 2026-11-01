# Public workspace contracts

The [workspace workflow](../.github/workflows/workspace.yml) compares the public
repositories without building them. It runs on pull requests, pushes to `main`,
manual dispatches, and daily. Consumer jobs use scoped comparisons on their own
candidate commits once installed in the publication order below. No private
repository or credential is required.

## Inventory and ownership

| Canonical source | Copies checked |
| --- | --- |
| VS Code `src/engine/` and `src/bridge/` | Neovim and MCP `vendor/`, web `src/` |
| Specification `vectors/` | Reference server `vectors/` |
| Specification `vectors/anchors/relative-position.json` | VS Code `test/fixtures/anchors/relative-position.json` |
| Specification `schema/limits.json` | Server `crates/harness/tests/limits.json`, VS Code `test/fixtures/limits.json` |
| VS Code `package-lock.json` | Neovim, MCP, web lockfiles and JetBrains `engine/src/test/node/package-lock.json`: exact `yjs`, `lib0`, and `y-protocols` versions |
| Specification `scripts/check_dry_run_gating.py` | VS Code, Neovim, web, server, and JetBrains copies |

The inventory is explicit in [the checker](../scripts/check-workspace.py).
Directory comparisons include additions, removals, content, and executable modes.
Missing or empty inputs, invalid JSON, missing or conflicting CRDT versions, and
unreadable revisions fail rather than count as agreement. The checker does not
fetch, build, modify checkouts, or substitute a working tree for a missing ref.

## Reproduce a cohort locally

Place public Git checkouts under a common parent using their repository names.
Every required revision must be supplied explicitly; a branch name resolves once
to a full commit SHA, which the output records. `HEAD` means the committed
candidate, not uncommitted edits. Keep that full SHA output with the CI result.
For example, from the parent of the checkouts:

```sh
python3 specification/scripts/check-workspace.py --workspace . \
  --revision specification=HEAD --revision vscode_client=HEAD \
  --revision nvim_client=HEAD --revision mcp=HEAD \
  --revision web_client=HEAD --revision jetbrains_client=HEAD \
  --revision reference_server=HEAD
```

To reproduce one consumer result, check out the logged commits in those same
public repositories and limit the inventory:

```sh
python3 specification/scripts/check-workspace.py --workspace . \
  --consumer nvim_client --revision nvim_client=HEAD \
  --revision specification=HEAD --revision vscode_client=HEAD
```

The checker and its regression suite need Python 3.8 or later and Git. Run the
suite with `python3 -B specification/scripts/test_check_workspace.py`. The specification's
normal validation job runs that suite too. Exit status is `0` for agreement,
`1` for drift, and `2` when evidence cannot be checked. Both nonzero statuses
fail CI. A successful comparison is not a successful product integration test;
the owning repositories' native, server, corpus, and browser jobs remain
responsible for behavior.

## Updating a copy

A failure names the changed path and its sync command. Run the consumer's sync
script against the selected canonical checkout, adopt any changed interface in
the same change, and run the consumer's own tests. Do not update vendored files
independently or downgrade a canonical dependency just to make a comparison pass.

## Publication order

Cross-repository changes cannot merge atomically. Publish a checker migration
in this order:

1. Merge the checker, its tests, and its census workflow into this public
   specification repository after its own gates pass.
2. Pin that reviewed, full public checker commit SHA in every consumer job.
   Never merge bootstrap references to an unpublished script or rely on a
   private checkout supplying it.
3. Prepare the canonical source update and matching consumer adoptions together.
   Merge the canonical update after its gates pass, then merge the synchronized
   consumers after their own scoped comparisons and product tests pass.
4. Run the public census over the resulting published revisions. Remove the
   superseded private `ai_notes` automation only after the replacement public
   jobs have actually run; workflow files alone are not execution evidence.

The daily census deliberately stays red while published copies disagree; it
must not silently skip a missing repository or compare a PR against its stale
published `main`. Record this transition in the related pull requests and use
the full logged revisions to distinguish it from a regression. For later
canonical updates, follow steps 3–4; checker updates also require repinning its
executable revision in the consumers.

Checker code used by consumers is pinned to a reviewed public specification
commit. Canonical product sources follow their declared public refs and are
resolved and logged once per run. The scheduled census uses current public
`main` branches to detect drift even when a consumer receives no new commits.
