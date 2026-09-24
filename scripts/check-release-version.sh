#!/usr/bin/env bash
#
# The release version a dispatch input or a tag name carries, checked here rather than
# inside the workflow step, so that the rule is one that `scripts/check-workflows.py`
# drives — a guard nothing exercises is a guard nobody can tell from a comment.
#
#   scripts/check-release-version.sh 0.1.0    # silent, exit 0
#   scripts/check-release-version.sh 1x2.3.4  # a reason on stderr, exit 1
#
# `X.Y.Z`, each component a plain decimal with no leading zero. The shape is not a
# `case` glob. `case "$v" in [0-9]*.[0-9]*.[0-9]*)` looks like it enforces `X.Y.Z` and
# does not: `*` matches `.` and `/`, so it admits `1x2.3.4`, `1.2.3.4`, `1.2.3-rc1` and
# `1.2.3/../../evil`. That is what this replaced.
#
# The character check is separate from the shape check on purpose: `grep`'s `$` and bash's
# `[[ =~ ]]` do not agree about a string that ends in a newline, so the first rule refuses
# any character outside the digits and the dot before the second rule reads the shape.
set -euo pipefail

version=${1-}

case "$version" in
  "" | *[!0-9.]*)
    printf 'refusing: %q is not a release version\n' "$version" >&2
    exit 1
    ;;
esac

if ! [[ $version =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  printf 'refusing: %s is not a release version\n' "$version" >&2
  exit 1
fi
