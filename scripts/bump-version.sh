#!/usr/bin/env bash
#
# Compute this repository's next release version.
#
#   scripts/bump-version.sh <major|minor|patch> [--dry-run]
#
# This repository carries no version string in its tree: nothing in `PROTOCOL.md`, the
# schema, the vectors or the flake is a release version, and the tag is the only record of
# the number the release is named by (`README.md`, Versioning; `docs/runbook-release.md`
# §1 names this repository as the one whose version lives nowhere in the tree). So the
# current version is this repository's release tags, and the next one is computed from
# them rather than written into a manifest:
#
#   v0.5.1 + minor -> 0.6.0      v0.5.1 + major -> 1.0.0      v0.5.1 + patch -> 0.5.2
#
# The new version is printed as the last line of stdout and shares that line with nothing
# else, so the caller can name the tag `v<version>`, the bundle and the Release from it:
# `release.yml` reads it with `$(scripts/bump-version.sh "$bump")`.
#
# There is no file to write, in either mode. `--dry-run` exists so that a caller's
# `--dry-run` means the same thing here as in the sibling repositories, which have a
# manifest to move: it prints the same version and skips the writing that neither mode
# does. A reader looking for the file a bump sets is looking in a repository that has
# none.
#
# The tags decide the version, not the commit graph: of every release tag the highest
# wins, so a release cut from a commit behind the newest tag still moves forward. `git
# describe` is the wrong question here — it names the reachable tag nearest HEAD, which on
# this repository's own history is a lower version than the highest tag — so it is not
# used.
#
# What counts as a release tag is `scripts/check-release-version.sh`'s rule, the same one
# `release.yml` applies to the version this prints, so the two cannot disagree about what
# a release version is; a prerelease or a `vnext` is not one to bump from. The caller
# brings the tags: this reads the checkout it sits in, which `actions/checkout` supplies
# with `fetch-tags`, and it refuses rather than guessing when there is no release tag.
#
# Exit 2 is "this is not a call this takes" (no word, a second argument that is not
# `--dry-run`, more arguments than that); exit 1 is a refusal with a reason (a word that
# is not one of the three, a machine or a tree the tags cannot be read from). Neither
# prints a version.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
cd "$repo_root"

usage() {
  printf 'usage: %s <major|minor|patch> [--dry-run]\n' "${0##*/}" >&2
  exit 2
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  usage
fi

word=$1
dry_run=false
if [ "$#" -eq 2 ]; then
  if [ "$2" != '--dry-run' ]; then
    usage
  fi
  dry_run=true
fi

case "$word" in
  major | minor | patch) ;;
  *)
    printf 'refusing: %q is not a bump; name major, minor or patch\n' "$word" >&2
    exit 1
    ;;
esac

# The tags are the version and `git` is what reads them; a machine without git and a tree
# that is not a checkout each get their own reason rather than computing from nothing.
if ! command -v git >/dev/null 2>&1; then
  printf 'refusing: git is not on PATH, and the release tags are the only place this repository carries a version\n' >&2
  exit 1
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  printf 'refusing: %s is not a git repository, so there is no release tag to bump from\n' "$repo_root" >&2
  exit 1
fi

# The highest release tag. Each candidate is put through the shape rule, so a tag it
# refuses is not a version here either; `sort -t.` compares the three components as
# numbers, which a plain sort does not (`v0.10.0` sorts below `v0.9.9` read as text). The
# rule runs through `bash` rather than by its own shebang: this is a build-sandbox check as
# well as a release step (`scripts/test_bump_version.py`), and a nix build sandbox has no
# `/usr/bin/env` for that shebang to find.
highest=$(
  git tag --list 'v*' |
    while read -r tag; do
      if bash -- "$script_dir/check-release-version.sh" "${tag#v}" >/dev/null 2>&1; then
        printf '%s\n' "${tag#v}"
      fi
    done |
    sort -t. -k1,1n -k2,2n -k3,3n |
    tail -n 1
)

if [ -z "$highest" ]; then
  printf 'refusing: no vX.Y.Z release tag here to bump from; fetch the tags before releasing\n' >&2
  exit 1
fi

major=${highest%%.*}
minor=${highest#*.}
minor=${minor%%.*}
patch=${highest##*.}

case "$word" in
  major)
    major=$((major + 1))
    minor=0
    patch=0
    ;;
  minor)
    minor=$((minor + 1))
    patch=0
    ;;
  patch) patch=$((patch + 1)) ;;
esac

new="$major.$minor.$patch"

if [ "$dry_run" = true ]; then
  printf 'dry run: this repository has no version file, so the tag the release creates is what records v%s and nothing is written\n' "$new" >&2
fi

printf '%s\n' "$new"
