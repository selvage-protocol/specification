#!/usr/bin/env python3
"""The guard around `scripts/ci-local.sh`: a `validate` whose inputs differ from HEAD must stop,
not run the checks and then report success.

`job_validate` refuses when the paths the flake checks read differ from HEAD, because CI checks
out the committed ref and the local run does not. That refusal is the precondition of the gate,
and it has to fail closed. It did not. The mode arms invoked `job_validate` as the left operand of
`&&` (`validate) job_validate && job_links ;;`, `all) job_lint && job_validate && job_links ;;`),
and a command in a `&&` list that is not the last one has `errexit` suppressed — for the whole
function body. So `inputs_clean` printed its refusal, returned 1, and `job_validate` carried on to
`nix eval` and every `nix build`, returning the status of its last command; the script exited 0.
The same suppression swallowed a check that failed in the middle of the list.

What must hold:

1. **A dirty `validate` refuses, exits nonzero, and runs no command at all** — not the evaluation
   that names the system, not a `nix build`, not the link check — whichever of the guarded paths
   is the one that differs.
2. **A dirty `all` runs `lint` and nothing after the refusal** — `lint` reads the working tree and
   needs no guard, so it stays where it is; the refusal stops the run before the first check.
3. **A clean `validate` runs every check the script declares, in the order it declares them, then
   the link check**, and exits 0 — the fix must not turn the guard into a refusal that never lets
   a good run through.
4. **A clean `all` runs `lint`, then the same checks, then the link check.**
5. **A check that fails in the middle of the list stops the run** — the same `errexit` suppression
   was what swallowed it, and the checks are the load-bearing part of the gate.

The script is run, not a copy of one of its functions: this harness copies the bytes of
`scripts/ci-local.sh` into a throwaway git repository laid out like the checkout, puts a `nix` and
a `lychee` that record their calls in front of `PATH`, and drives the real script. `git` is real,
so the guard reads a real dirty tree.

Run it directly:

    python3 scripts/test_ci_input_guard.py

or as the flake check this repository's gate runs: `nix build .#checks.<system>.ci-input-guard`.

Point it at other bytes with `SELVAGE_CI_LOCAL_SCRIPT=<path>` to see the assertions fail against
the script before the fix.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DEFAULT_SCRIPT = HERE / "ci-local.sh"
# The script under test. `SELVAGE_CI_LOCAL_SCRIPT` is how the same assertions are shown red
# against the bytes before the fix, without touching the file the gate runs.
SCRIPT = Path(os.environ.get("SELVAGE_CI_LOCAL_SCRIPT") or DEFAULT_SCRIPT).resolve()

# Nothing is built in `/tmp`: it is a RAM-backed tmpfs on some hosts, where a build has taken a
# machine down. `scripts/ci-local.sh` points `TMPDIR` at `.tmp/` inside the checkout and a nix
# build at its own `/build`; with neither, the scratch comes from the same place the gate's does.
WORKDIR = Path(os.environ.get("TMPDIR") or (HERE.parent / ".tmp")) / "ci-input-guard"

BASH = shutil.which("bash") or "/bin/bash"
GIT = shutil.which("git")

# A caller's Git repository and config selections are dropped, not inherited: a repository
# selection overrides the fixture's working directory, and a config parameter changes what its
# `git status` reports. A local-Git policy over these names, not a hostile-environment sanitizer.
GIT_SELECTION = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
)

# A commit needs an identity and, in a sandbox, a home inside the tree: nothing here reads or
# writes the machine's own git config.
IDENTITY = {
    "GIT_AUTHOR_NAME": "Selvage",
    "GIT_AUTHOR_EMAIL": "selvage@example.invalid",
    "GIT_COMMITTER_NAME": "Selvage",
    "GIT_COMMITTER_EMAIL": "selvage@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}

# The system `nix eval` names, so the build targets this harness expects are the ones the script
# asks for.
SYSTEM = "x86_64-linux"
EVAL = "nix eval --raw --impure --expr builtins.currentSystem"
LINT = "nix develop . -c actionlint"
LINKS = "lychee --config lychee.toml --no-progress README.md docs"

# `check <name> "<description>"`, the one place `job_validate` declares a step. The clean tests
# read the list back from the script rather than repeating it, so they hold for the script before
# the fix and after it, and a scan that reaches no step fails instead of reporting success.
CHECK = re.compile(r"^\s*check\s+([A-Za-z0-9][A-Za-z0-9._-]*)\s+\"")

# The directories and files `inputs_clean` refuses a change in, each paired with a name to dirty
# it by: a directory gets an untracked file, a file is modified in place.
GUARDED = [
    (".github", ".github/dirty.txt"),
    ("schema", "schema/dirty.txt"),
    ("scripts", "scripts/dirty.txt"),
    ("vectors", "vectors/dirty.txt"),
    ("runner", "runner/dirty.txt"),
    ("flake.nix", "flake.nix"),
    ("flake.lock", "flake.lock"),
]

NIX_MOCK = """#!@BASH@
set -euo pipefail
printf 'nix %s\\n' "$*" >> "$MOCK_LOG"
case "$1" in
  eval) printf '@SYSTEM@\\n' ;;
  build)
    name=
    for argument in "$@"; do
      case "$argument" in
        .#checks.*) name=${argument##*.} ;;
      esac
    done
    if [[ -n ${MOCK_FAIL_CHECK:-} && $name == "${MOCK_FAIL_CHECK}" ]]; then
      printf '%s: FAILED\\n' "$name" >&2
      exit 1
    fi
    mkdir -p "$MOCK_REPORTS"
    printf '%s: passed\\n' "$name" > "$MOCK_REPORTS/$name"
    printf '%s\\n' "$MOCK_REPORTS/$name"
    ;;
esac
"""

LYCHEE_MOCK = """#!@BASH@
set -euo pipefail
printf 'lychee %s\\n' "$*" >> "$MOCK_LOG"
"""


def build(name: str) -> str:
    """The `nix build` the script runs for one check, as the mock records it."""
    return f"nix build .#checks.{SYSTEM}.{name} --no-link --print-out-paths"


def declared_checks(text: str) -> list[str]:
    """The checks `job_validate` declares, in the order it declares them."""
    names = [match.group(1) for match in map(CHECK.match, text.splitlines()) if match]
    if not names:
        raise AssertionError("no `check <name>` step found: the scan would report a clean tree")
    return names


def setUpModule() -> None:
    """Fail, loudly, without the two programs the harness drives the script with."""
    missing = [name for name, found in (("bash", BASH), ("git", GIT)) if found is None]
    if missing:
        raise AssertionError(f"not on PATH: {', '.join(missing)}")
    WORKDIR.mkdir(parents=True, exist_ok=True)


class Repo:
    """A throwaway repository laid out like this checkout, with the real script committed in it."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.repo = self.root / "repo"
        self.bin = self.root / "bin"
        self.log_path = self.root / "calls.log"
        self.reports = self.root / "reports"
        for directory in ("scripts", "schema", "vectors", "runner", ".github/workflows", "docs"):
            (self.repo / directory).mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "scripts" / "ci-local.sh")
        self.checks = declared_checks((self.repo / "scripts" / "ci-local.sh").read_text())
        (self.repo / "schema" / "keep.txt").write_text("schema\n")
        (self.repo / "vectors" / "keep.txt").write_text("vectors\n")
        (self.repo / "runner" / "keep.txt").write_text("runner\n")
        (self.repo / ".github" / "workflows" / "validate.yml").write_text("name: validate\n")
        (self.repo / "flake.nix").write_text("{}\n")
        (self.repo / "flake.lock").write_text("{}\n")
        (self.repo / "README.md").write_text("# readme\n")
        (self.repo / "docs" / "index.md").write_text("# docs\n")
        self.bin.mkdir()
        self.mock("nix", NIX_MOCK.replace("@BASH@", BASH).replace("@SYSTEM@", SYSTEM))
        self.mock("lychee", LYCHEE_MOCK.replace("@BASH@", BASH))
        self.must(self.git("init", "--quiet"))
        self.must(self.git("add", "--all"))
        self.must(self.git("commit", "--quiet", "-m", "the layout CI would check out"))

    def mock(self, name: str, text: str) -> None:
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def environment(self, **extra: str) -> dict[str, str]:
        env = {name: value for name, value in os.environ.items() if name not in GIT_SELECTION}
        env.update(IDENTITY)
        env["HOME"] = str(self.root)
        # Nothing here may reach a repository above the throwaway tree, whatever TMPDIR points
        # at on the machine this runs on.
        env["GIT_CEILING_DIRECTORIES"] = str(self.root)
        env["MOCK_LOG"] = str(self.log_path)
        env["MOCK_REPORTS"] = str(self.reports)
        env["PATH"] = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        env.update(extra)
        return env

    def git(self, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [GIT, "-c", "init.defaultBranch=main", *arguments],
            cwd=str(cwd or self.repo),
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def must(self, done: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
        if done.returncode != 0:
            raise AssertionError(f"{' '.join(done.args)}: {done.stdout}{done.stderr}")
        return done

    def dirty(self, path: str) -> None:
        target = self.repo / path
        if target.is_dir():
            (target / "dirty.txt").write_text("not committed\n")
        else:
            target.write_text(target.read_text() + "dirty\n")

    def run(self, *arguments: str, **environment: str) -> subprocess.CompletedProcess:
        """One run of the script, in the throwaway tree, through the shell its shebang names."""
        return subprocess.run(
            [BASH, str(self.repo / "scripts" / "ci-local.sh"), *arguments],
            cwd=str(self.repo),
            env=self.environment(**environment),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def calls(self) -> list[str]:
        """What the mock `nix` and `lychee` recorded, in the order they were called."""
        if not self.log_path.exists():
            return []
        return self.log_path.read_text().splitlines()


class Harness(unittest.TestCase):
    """The one thing every test below needs: a repository holding the real script."""

    def make(self) -> Repo:
        directory = tempfile.TemporaryDirectory(prefix="ci-input-guard-", dir=str(WORKDIR))
        self.addCleanup(directory.cleanup)
        return Repo(Path(directory.name))


class RefusalTest(Harness):
    """A dirty validate stops before the first check, in either mode."""

    def test_a_dirty_validate_refuses_and_runs_nothing(self):
        repo = self.make()
        repo.dirty("vectors")
        done = repo.run("validate")
        self.assertNotEqual(
            done.returncode, 0, f"a dirty validate exited 0:\n{done.stdout}{done.stderr}"
        )
        self.assertIn("refusing:", done.stderr)
        self.assertIn("vectors/dirty.txt", done.stderr)
        self.assertEqual(repo.calls(), [], "a refused validate went on to run a command")

    def test_every_guarded_path_a_dirty_change_refuses_and_runs_nothing(self):
        for path, name in GUARDED:
            with self.subTest(path=path):
                repo = self.make()
                repo.dirty(path)
                done = repo.run("validate")
                self.assertNotEqual(
                    done.returncode, 0, f"a dirty {path} exited 0:\n{done.stdout}{done.stderr}"
                )
                self.assertIn("refusing:", done.stderr)
                self.assertIn(name, done.stderr)
                self.assertEqual(repo.calls(), [], f"a dirty {path} went on to run a command")

    def test_a_dirty_all_runs_lint_and_nothing_after_the_refusal(self):
        repo = self.make()
        repo.dirty("schema")
        done = repo.run("all")
        self.assertNotEqual(
            done.returncode, 0, f"a dirty all exited 0:\n{done.stdout}{done.stderr}"
        )
        self.assertIn("refusing:", done.stderr)
        self.assertIn("schema/dirty.txt", done.stderr)
        self.assertEqual(
            repo.calls(), [LINT], "the refusal did not stop the run before the first check"
        )


class CleanRunTest(Harness):
    """A clean run reaches every declared check and the link check, in order."""

    def test_a_clean_validate_runs_every_declared_check_then_the_link_check(self):
        repo = self.make()
        done = repo.run("validate")
        self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}")
        self.assertEqual(repo.calls(), [EVAL] + [build(name) for name in repo.checks] + [LINKS])

    def test_a_clean_all_runs_lint_then_every_declared_check_then_the_link_check(self):
        repo = self.make()
        done = repo.run("all")
        self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}")
        self.assertEqual(
            repo.calls(), [LINT, EVAL] + [build(name) for name in repo.checks] + [LINKS]
        )

    def test_links_runs_only_the_link_check(self):
        repo = self.make()
        done = repo.run("links")
        self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}")
        self.assertEqual(repo.calls(), [LINKS])

    def test_lint_runs_only_actionlint(self):
        repo = self.make()
        done = repo.run("lint")
        self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}")
        self.assertEqual(repo.calls(), [LINT])

    def test_a_dirty_links_still_runs_because_the_guard_is_the_validate_half(self):
        repo = self.make()
        repo.dirty("vectors")
        done = repo.run("links")
        self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}")
        self.assertEqual(repo.calls(), [LINKS], "the link check reads the working tree, not HEAD")


class FailingCheckTest(Harness):
    """A check that fails mid-list stops the run: the same suppression swallowed it."""

    def test_a_check_that_fails_in_the_middle_stops_the_run(self):
        repo = self.make()
        self.assertGreater(len(repo.checks), 2, "a middle check needs a later one to stop before")
        failing = repo.checks[1]
        done = repo.run("validate", MOCK_FAIL_CHECK=failing)
        self.assertNotEqual(
            done.returncode, 0, f"a failing {failing} exited 0:\n{done.stdout}{done.stderr}"
        )
        self.assertIn(f"{failing}: FAILED", done.stderr)
        self.assertEqual(
            repo.calls(),
            [EVAL] + [build(name) for name in repo.checks[:2]],
            "the run did not stop at the failing check",
        )


class ScriptTest(unittest.TestCase):
    """The file the gate runs is the program its shebang and its mode say it is."""

    def test_ci_local_is_installed_as_a_program_with_a_bash_env_shebang(self):
        self.assertTrue(os.access(DEFAULT_SCRIPT, os.X_OK), f"{DEFAULT_SCRIPT} is not executable")
        self.assertEqual(
            DEFAULT_SCRIPT.read_text(encoding="utf-8").splitlines()[0], "#!/usr/bin/env bash"
        )

    def test_the_script_declares_the_checks_this_harness_expects_it_to_run(self):
        self.assertTrue(declared_checks(DEFAULT_SCRIPT.read_text(encoding="utf-8")))


class GitSelectionIsolationTest(Harness):
    """A caller's Git variables must not reach the throwaway tree.

    A repository selection or a config parameter would change what the fixture's own `git` and the
    script's `git status` read, so a dirty fixture could pass as clean. Each test names a throwaway
    repository and shows the fixture still reads itself.
    """

    def decoy(self, repo: Repo) -> Path:
        """A second throwaway repository, the one a leaked selection variable would point at."""
        decoy = repo.root / "decoy"
        decoy.mkdir()
        (decoy / "decoy.txt").write_text("decoy\n")
        repo.must(repo.git("init", "--quiet", cwd=decoy))
        repo.must(repo.git("add", "--all", cwd=decoy))
        repo.must(
            repo.git(
                "commit",
                "--quiet",
                "-m",
                "the repository a leaked variable selects",
                cwd=decoy,
            )
        )
        return decoy

    def caller_selection(self, decoy: Path) -> dict[str, str]:
        """What a caller inside a repository has set, aimed at the decoy."""
        return {
            "GIT_DIR": str(decoy / ".git"),
            "GIT_WORK_TREE": str(decoy),
            "GIT_INDEX_FILE": str(decoy / ".git" / "index"),
        }

    def decoy_state(self, repo: Repo, decoy: Path) -> tuple[str, str, str]:
        """The decoy's logical state: its HEAD, its status and the files it tracks."""
        return (
            repo.must(repo.git("rev-parse", "HEAD", cwd=decoy)).stdout.strip(),
            repo.must(repo.git("status", "--porcelain", cwd=decoy)).stdout,
            repo.must(repo.git("ls-files", cwd=decoy)).stdout,
        )

    def test_a_dirty_validate_is_not_masked_by_a_callers_git_selection(self):
        repo = self.make()
        decoy = self.decoy(repo)
        repo.dirty("vectors")
        caller = dict(os.environ)
        with mock.patch.dict(os.environ, self.caller_selection(decoy), clear=False):
            done = repo.run("validate")
        self.assertNotEqual(
            done.returncode, 0, f"a dirty validate exited 0:\n{done.stdout}{done.stderr}"
        )
        self.assertIn("refusing:", done.stderr)
        self.assertIn("vectors/dirty.txt", done.stderr)
        self.assertEqual(repo.calls(), [], "a refused validate went on to run a command")
        self.assertEqual(dict(os.environ), caller, "the run changed the caller's environment")

    def test_the_fixtures_git_calls_are_not_redirected_by_a_callers_git_selection(self):
        repo = self.make()
        decoy = self.decoy(repo)
        repo.dirty("vectors")
        before = self.decoy_state(repo, decoy)
        with mock.patch.dict(os.environ, self.caller_selection(decoy), clear=False):
            status = repo.git("status", "--porcelain")
        self.assertEqual(status.returncode, 0, f"{status.stdout}{status.stderr}")
        self.assertIn(
            "vectors/dirty.txt",
            status.stdout,
            "the fixture's git read a repository the caller's environment named",
        )
        self.assertEqual(self.decoy_state(repo, decoy), before, "the run left the decoy changed")

    def test_a_dirty_validate_is_not_masked_by_a_callers_git_config_parameters(self):
        repo = self.make()
        repo.dirty("vectors")
        caller = dict(os.environ)
        injected = {"GIT_CONFIG_PARAMETERS": "'status.showUntrackedFiles=no'"}
        with mock.patch.dict(os.environ, injected, clear=False):
            done = repo.run("validate")
            status = repo.git("status", "--porcelain")
        self.assertNotEqual(
            done.returncode, 0, f"a dirty validate exited 0:\n{done.stdout}{done.stderr}"
        )
        self.assertIn("refusing:", done.stderr)
        self.assertIn("vectors/dirty.txt", done.stderr)
        self.assertEqual(repo.calls(), [], "a refused validate went on to run a command")
        self.assertEqual(status.returncode, 0, f"{status.stdout}{status.stderr}")
        self.assertIn(
            "vectors/dirty.txt",
            status.stdout,
            "the fixture's git did not see its own untracked file",
        )
        self.assertEqual(dict(os.environ), caller, "the run changed the caller's environment")

    def test_a_dirty_validate_is_not_masked_by_a_raw_caller_git_selection(self):
        """The script's own read drops a repository selection, not only the harness filter.

        The tests above reach the script through `Repo.environment`, which removes the
        selectors before it starts: they show the harness is isolated, not that the guard is.
        Here the selection is handed to the script itself, the way a caller sitting in another
        repository has it, and the guard must still refuse the dirty tree.
        """
        repo = self.make()
        decoy = self.decoy(repo)
        repo.dirty("vectors")
        done = repo.run("validate", **self.caller_selection(decoy))
        self.assertNotEqual(
            done.returncode, 0, f"a dirty validate exited 0:\n{done.stdout}{done.stderr}"
        )
        self.assertIn("refusing:", done.stderr)
        self.assertIn("vectors/dirty.txt", done.stderr)
        self.assertEqual(repo.calls(), [], "a refused validate went on to run a command")

    def test_a_dirty_validate_is_not_masked_by_raw_caller_config_parameters(self):
        """A caller's command config must not hide the untracked file that makes the tree dirty."""
        repo = self.make()
        repo.dirty("vectors")
        done = repo.run("validate", GIT_CONFIG_PARAMETERS="'status.showUntrackedFiles=no'")
        self.assertNotEqual(
            done.returncode, 0, f"a dirty validate exited 0:\n{done.stdout}{done.stderr}"
        )
        self.assertIn("refusing:", done.stderr)
        self.assertIn("vectors/dirty.txt", done.stderr)
        self.assertEqual(repo.calls(), [], "a refused validate went on to run a command")

    def test_a_clean_validate_runs_every_check_with_a_raw_caller_git_selection(self):
        """The same poison over a clean tree must reach every check: the fix is no blind refusal."""
        repo = self.make()
        decoy = self.decoy(repo)
        done = repo.run("validate", **self.caller_selection(decoy))
        self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}")
        self.assertEqual(repo.calls(), [EVAL] + [build(name) for name in repo.checks] + [LINKS])

    def test_a_dirty_validate_is_not_masked_by_a_local_untracked_visibility_setting(self):
        """A repository-local untracked-visibility setting must not hide the untracked dirty file.

        The caller's config parameters are dropped for the guard's read, but the setting can
        also live in the discovered repository's own config, which no environment unset removes.
        The read must ask for the untracked files it is there to see.
        """
        repo = self.make()
        repo.must(repo.git("config", "--local", "status.showUntrackedFiles", "no"))
        repo.dirty("vectors")
        done = repo.run("validate")
        self.assertNotEqual(
            done.returncode, 0, f"a dirty validate exited 0:\n{done.stdout}{done.stderr}"
        )
        self.assertIn("refusing:", done.stderr)
        self.assertIn("vectors/dirty.txt", done.stderr)
        self.assertEqual(repo.calls(), [], "a refused validate went on to run a command")


if __name__ == "__main__":
    unittest.main()
