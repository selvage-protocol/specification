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
        # workflow pins jsonschema 4.26.0, referencing 0.37.0 and websockets 16.1; these are the
        # versions nixpkgs ships, so the sandbox runs what the runner runs.
        python = pkgs.python3.withPackages (ps: [
          ps.jsonschema
          ps.referencing
          ps.websockets
        ]);

        # One check per step of `scripts/ci-local.sh validate`, so a failure names the step that
        # failed. The command's output goes to stderr as the build runs; `$out` gets one stable
        # line, because the runner prints per-test timings and a derivation whose output differs
        # from build to build is one `nix build --rebuild` rightly calls non-deterministic.
        mkCheck = name: command:
          pkgs.runCommand "specification-${name}" {
            nativeBuildInputs = [python];
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
        };
      }
    );
}
