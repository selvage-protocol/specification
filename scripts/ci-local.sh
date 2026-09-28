#!/usr/bin/env bash
#
# Runs the steps of .github/workflows/validate.yml on this machine, without containers (this
# host has no Docker or Podman, so `act` cannot run here).
#
#   scripts/ci-local.sh validate  # the `schemas` job: the schemas, the vectors, the runner,
#                                 # the decoder, the peer layer, the workflows, the version bump,
#                                 # the workflows' dry_run gating, and the link check it ends with
#   scripts/ci-local.sh links     # lychee over README.md and docs/, on its own
#   scripts/ci-local.sh lint      # actionlint over the workflow files
#   scripts/ci-local.sh all       # lint + validate, the whole `schemas` job including its link check
#
# Keep this in step with the workflow — it runs the same commands, so that a red job is found
# here rather than on a runner. `lint` catches unknown actions, bad expressions and shell
# mistakes statically; the workflow has no actionlint step of its own, so that one is local-only.
#
# `validate` is `nix build .#checks.<system>.{schemas,runner,yprotocols,peer,peer-census,recipes,`
# `workflows,bump-version,dry-run-gating}`, one check per step. `flake.nix` supplies the pinned
# Python and the
# four packages the workflow installs with pip — at the versions that workflow pins — so this
# needs no virtualenv, no `pip` and no network. The `dry_run` guard is the one check whose Python
# carries PyYAML as well. Each
# check's store path is the report of the command it ran, which is what is printed here: a check
# that had to be built says nothing until it is done, and a cached one still says what it found.
# A failure exits this script with the check's own report, which nix prints as the log tail.
#
# `links` is not a flake check: it is `lychee` over `README.md` and `docs/`, the reader-facing prose
# this repository writes itself (`PROTOCOL.md` and `NOTES.md` are read as documents rather than
# swept here with it). The runner has no nix, so it installs the pinned lychee release and calls
# this mode; lychee comes from `PATH` when it is there and from `nix shell` otherwise. `validate`
# runs it last, as the `schemas` job does, so that the mode its banner names mirrors that job.
#
# What the checks read is the tracked tree at its working-tree content: a file that is
# new and untracked is invisible to them, while a modified or deleted tracked file is
# read as it stands. Either way the run is not the run CI would do — CI checks out the
# committed ref — so `validate` refuses when its inputs differ from HEAD, and says so.
# Commit, not just stage: staged-but-uncommitted is visible to the local build and
# absent from CI. `lint` runs actionlint on the working tree, so it needs no guard.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

# `/tmp` is a RAM-backed tmpfs on some hosts, and building there has taken a machine down
# before; keep every artefact inside the checkout.
export TMPDIR="$repo_root/.tmp"
mkdir -p "$TMPDIR"

say() { printf '\n=== %s ===\n' "$*"; }

run_lychee() {
  if command -v lychee >/dev/null 2>&1; then
    lychee "$@"
  else
    nix shell nixpkgs#lychee -c lychee "$@"
  fi
}

inputs_clean() {
  local dirty
  dirty=$(git status --porcelain -- .github schema scripts vectors runner flake.nix flake.lock)
  if [[ -n $dirty ]]; then
    printf 'refusing: the validate inputs differ from HEAD, so this is not the run CI would do.\n' >&2
    printf 'commit these paths first, then re-run:\n' >&2
    printf '%s\n' "$dirty" >&2
    return 1
  fi
}

check() {
  local name=$1 description=$2 out
  say "validate: $description"
  out=$(nix build ".#checks.${system}.${name}" --no-link --print-out-paths)
  cat "$out"
}

job_validate() {
  inputs_clean
  # The system this host evaluates for; the flake has checks for each of flake-utils' four. Read
  # here rather than at the top of the file, so that `links` runs without nix.
  system=$(nix eval --raw --impure --expr builtins.currentSystem)
  check schemas "the schemas and the vectors"
  check runner "the runner's comparison code"
  check yprotocols "the binary decoder"
  check peer "the peer corpus's frame layer"
  check peer-census "the mutation census over the peer corpus"
  check recipes "the peer corpus's frames, re-derived from its recipes"
  check workflows "the workflows' pins and the release-version guard"
  check bump-version "the version bump the release computes from its tags"
  check dry-run-gating "the workflows' dry_run gating"
}

job_links() {
  say "links: lychee over README.md and docs/"
  run_lychee --config lychee.toml --no-progress README.md docs
}

job_lint() {
  say "lint: actionlint over the workflows"
  nix develop . -c actionlint
}

case "${1:-all}" in
  validate) job_validate && job_links ;;
  links) job_links ;;
  lint) job_lint ;;
  all) job_lint && job_validate && job_links ;;
  *)
    printf 'usage: %s [validate|links|lint|all]\n' "$0" >&2
    exit 2
    ;;
esac
