#!/usr/bin/env bash
#
# Runs the steps of .github/workflows/validate.yml on this machine, without containers (this
# host has no Docker or Podman, so `act` cannot run here).
#
#   scripts/ci-local.sh validate  # the `schemas` job: the schemas, the vectors, the runner, the decoder
#   scripts/ci-local.sh lint      # actionlint over the workflow files
#   scripts/ci-local.sh all       # lint + validate
#
# Keep this in step with the workflow — it runs the same commands, so that a red job is found
# here rather than on a runner. `lint` catches unknown actions, bad expressions and shell
# mistakes statically; the workflow has no actionlint step of its own, so that one is local-only
# and needs `nix`.
#
# The workflow installs its three pinned Python dependencies into the runner's Python; this
# machine's `python3` has none of them, so `validate` keeps a virtualenv under `.tmp/venv` and
# installs those same pins into it. Run `scripts/ci-local.sh validate` once with the network up.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

# `/tmp` is a RAM-backed tmpfs on some hosts, and building there has taken a machine down
# before; keep every artefact inside the checkout.
export TMPDIR="$repo_root/.tmp"
mkdir -p "$TMPDIR"

say() { printf '\n=== %s ===\n' "$*"; }

python="$repo_root/.tmp/venv/bin/python"

job_validate() {
  say "validate: the validator's dependencies"
  if [ ! -x "$python" ]; then
    python3 -m venv "$repo_root/.tmp/venv"
  fi
  "$python" -m pip install --quiet --disable-pip-version-check \
    jsonschema==4.26.0 referencing==0.37.0 websockets==16.1
  say "validate: the schemas and the vectors"
  "$python" schema/validate.py
  say "validate: the runner's comparison code"
  "$python" runner/test_runner.py
  say "validate: the binary decoder"
  "$python" runner/test_yprotocols.py
}

job_lint() {
  say "lint: actionlint over the workflows"
  nix shell nixpkgs#actionlint -c actionlint
}

case "${1:-all}" in
  validate) job_validate ;;
  lint) job_lint ;;
  all) job_lint && job_validate ;;
  *)
    printf 'usage: %s [validate|lint|all]\n' "$0" >&2
    exit 2
    ;;
esac
