{
  description = "The Selvage protocol: its schemas, its vectors and the checks that hold them";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }:
    flake-utils.lib.eachDefaultSystem (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};

        # What the validator and the runner import, which is what `.github/workflows/validate.yml`
        # installs with pip and what `scripts/ci-local.sh` used to install into a venv. The
        # workflow pins jsonschema 4.26.0, referencing 0.37.0, websockets 16.1 and cryptography
        # 50.0.0; these are the versions nixpkgs ships, so the sandbox runs what the runner runs.
        #
        # `cryptography` is the peer layer's one dependency: AES-256-GCM, HKDF-SHA256 and Ed25519
        # for `runner/sealed.py`. nixpkgs ships it as a wheel for this interpreter and the binary
        # cache has it, so the sandbox substitutes rather than building a Rust extension; and the
        # wire half still needs only the three above, which is what `schema/validate.py` says when
        # it prints its own dependency.
        python = pkgs.python3.withPackages (ps: [
          ps.jsonschema
          ps.referencing
          ps.websockets
          ps.cryptography
        ]);

        # The one check that is not the validator's or the runner's: the `dry_run` guard reads the
        # workflow files, and PyYAML is what reads YAML. `validate.yml` installs it with pip in
        # the step that runs that check.
        pythonWithPyYaml = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);

        # One check per step of `scripts/ci-local.sh validate`, so a failure names the step that
        # failed. The command's output goes to stderr as the build runs; `$out` gets one stable
        # line, because the runner prints per-test timings and a derivation whose output differs
        # from build to build is one `nix build --rebuild` rightly calls non-deterministic.
        # `bash` is here for the checks that drive a shell guard over a table of values — the
        # workflows check and the version bump — and `extra` is whatever else a check needs.
        mkCheck = mkCheckWith python [];

        # `extra` is for a check that needs a tool the others do not: the version bump runs
        # `git` over the throwaway repositories it builds for itself.
        mkCheckUsing = mkCheckWith python;

        # The interpreter comes first because one check needs one the others do not: the `dry_run`
        # guard reads the workflow files, so it needs PyYAML — and giving it to the interpreter
        # the validator's checks use would make `scripts/check-workflows.py`'s "the flake's Python
        # has no YAML parser" something it would have to stop saying.
        mkCheckWith = interpreter: extra: name: command:
          pkgs.runCommand "specification-${name}" {
            nativeBuildInputs = [interpreter pkgs.bash] ++ extra;
          } ''
            cd ${self}
            # The source tree is the store's, which is read-only; a bytecode cache belongs
            # neither there nor in the copy of it a sandbox would make.
            export PYTHONDONTWRITEBYTECODE=1
            log=$TMPDIR/${name}.log
            if ${command} >$log 2>&1; then
              cat $log >&2
              echo "${name}: passed" >$out
            else
              cat $log >&2
              echo "${name}: FAILED" >$out
              exit 1
            fi
          '';
      in {
        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.actionlint
          ];
        };

        checks = {
          # Every schema, every frame in `vectors/` against it, the canonical byte form of what a
          # vector claims, and the three pinned counts of the corpus.
          schemas = mkCheck "schemas" "python3 schema/validate.py";
          # The comparison the replay decides with, driven in both directions: each rule gets a
          # pair that must match and a pair that must not.
          runner = mkCheck "runner" "python3 runner/test_runner.py";
          # The y-protocols decoder the replay leans on, straight from vectors 009 and 010.
          yprotocols = mkCheck "yprotocols" "python3 runner/test_yprotocols.py";
          # The frame layer of the peer corpus: `runner/sealed.py` driven in both directions
          # (a recipe to bytes, a verdict for every refusal the vocabulary names, the counter
          # mark, the subject protocol's framing and its deadlines), and the corpus replayed by
          # `runner/run_peer.py`. The replay and the census are separate steps so a failure names
          # which one failed: the replay says whether the vectors hold, and the census says
          # whether they would notice if they did not.
          peer = mkCheck "peer" "python3 runner/run_peer.py";
          peer-census = mkCheck "peer-census" "python3 runner/run_peer.py --mutation-census";
          # Every peer vector's frames re-derived from the recipes they carry, and the fixture
          # checked against the derivation `CANONICAL.md` §6.1 fixes.
          recipes = mkCheck "recipes" "python3 runner/test_recipe.py";
          # Every `uses:` in the workflows pinned to a commit sha, and the release-version
          # guard of `release.yml` driven over the values it must take and refuse.
          workflows = mkCheck "workflows" "python3 scripts/check-workflows.py";
          # The version bump `release.yml` computes its version with, driven over the words it
          # takes and the tags it refuses, in git repositories the check builds for itself.
          bump-version = mkCheckUsing [pkgs.git] "bump-version" "python3 scripts/test_bump_version.py";
          # The guard around what a workflow's `dry_run` input promises
          # (`scripts/check_dry_run_gating.py`). `release.yml` declares the input, its plan step
          # is the last step of the `validate` job and the whole `release` job below it carries
          # `inputs.dry_run != true`, which is what keeps a rehearsal from tagging and attaching
          # a bundle; a step added below the plan with no condition does what the plan said it
          # would not. `actionlint` lints that file clean, because the defect is the condition a
          # step does *not* carry. It reads the workflow files, so it runs under its own Python
          # with PyYAML; the second command is this repository's own workflows.
          dry-run-gating =
            mkCheckWith pythonWithPyYaml [] "dry-run-gating"
              "python3 -B scripts/test_check_dry_run_gating.py && python3 -B scripts/check_dry_run_gating.py .github/workflows";
        };
      }
    );
}
