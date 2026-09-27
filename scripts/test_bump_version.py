#!/usr/bin/env python3
"""The guard around `scripts/bump-version.sh`, the command `release.yml` computes a version with.

This repository carries no version string in its tree, so the version a release cuts is
computed from the release tags rather than read from a manifest, and this is the check on
that computation. It builds its own throwaway git repositories, so it reads none of this
checkout's tags, history or working tree — in a nix build sandbox or on a runner alike.

What must hold:

1. **The version is computed from the highest release tag**, and the arithmetic is the one
   a release means: `0.5.1` + `minor` is `0.6.0`, `+ major` is `1.0.0`, `+ patch` is
   `0.5.2`, and a carry moves the higher parts (`0.5.9` + `patch` is `0.5.10`, `0.9.9` +
   `minor` is `0.10.0`). The three components are compared as numbers: `0.10.0` is above
   `0.9.9`.
2. **The highest tag, not the tag `git describe` reaches.** A repository whose newest
   commit carries `v0.4.6` while `v0.5.1` hangs off the commit before it bumps from
   `0.5.1`; that test asserts `git describe` really does say `v0.4.6` there, so the case
   is the trap it claims to be rather than a comment about one.
3. **The version is the last line of stdout and the only line on stdout**, and a refusal
   prints no version at all.
4. **`--dry-run` prints what the write mode prints and writes nothing** — and neither does
   the write mode, because there is no version file here for either of them to write.
   Every run compares the whole tree, the commit and the tags before and after.
5. **A tag that is not a release version is not a version to bump from** (`v0.6.0-rc1`,
   `vnext`, `v0.6`, `v01.2.3`), by the same rule `scripts/check-release-version.sh`
   applies, and a tree with no release tag — or no repository, or no git — is refused
   rather than guessed at.
6. **A word that is not one of the three is refused**, as are a missing word, a second
   word and an option this does not take. Exit 2 is the shape of the call; exit 1 is a
   refusal with a reason. Neither writes anything.

Run it directly:

    python3 scripts/test_bump_version.py

or as the flake check this repository's gate runs: `nix build .#checks.<system>.bump-version`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "bump-version.sh"
# The shape rule that decides which tags are release versions. `bump-version.sh` reaches it
# beside itself, so a throwaway copy of the layout needs both files.
SHAPE_GUARD = HERE / "check-release-version.sh"
BASH = shutil.which("bash") or "/bin/bash"
GIT = shutil.which("git")
DIRNAME = shutil.which("dirname")

# A commit needs an identity and, in a sandbox, a home inside the tree: nothing here reads
# or writes the machine's own git config.
IDENTITY = {
    "GIT_AUTHOR_NAME": "Selvage",
    "GIT_AUTHOR_EMAIL": "selvage@example.invalid",
    "GIT_COMMITTER_NAME": "Selvage",
    "GIT_COMMITTER_EMAIL": "selvage@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def setUpModule() -> None:
    """Fail, loudly, without the three programs the script and this harness reach through PATH.

    A check that quietly skips itself is a check that reports a clean tree it never looked
    at.
    """
    missing = [
        name
        for name, found in (("bash", BASH), ("git", GIT), ("dirname", DIRNAME))
        if found is None
    ]
    if missing:
        raise AssertionError(f"not on PATH: {', '.join(missing)}")


class Repo:
    """A throwaway repository laid out like this checkout, with real commits and real tags.

    `older` names the tags the first commit carries and `head` the tags the second one
    does, which is what makes the `git describe` case a real trap: that command walks back
    from HEAD to the nearest reachable tag, so the highest version belongs on the earlier
    commit.
    """

    def __init__(
        self,
        root: Path,
        *,
        head: tuple[str, ...] = (),
        older: tuple[str, ...] = (),
        annotated: bool = False,
        git: bool = True,
    ):
        self.root = Path(root)
        self.repo = self.root / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "scripts" / SCRIPT.name)
        shutil.copy(SHAPE_GUARD, self.repo / "scripts" / SHAPE_GUARD.name)
        if git:
            self.must(self.git("init", "--quiet"))
            self.must(self.git("commit", "--quiet", "--allow-empty", "-m", "before"))
            for tag in older:
                self.tag(tag, annotated)
            self.must(self.git("commit", "--quiet", "--allow-empty", "-m", "here"))
            for tag in head:
                self.tag(tag, annotated)
        self.before = self.snapshot()

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(IDENTITY)
        env["HOME"] = str(self.root)
        # Nothing here may reach a repository above the throwaway tree, whatever TMPDIR is
        # pointing at on the machine this runs on.
        env["GIT_CEILING_DIRECTORIES"] = str(self.root)
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
            raise AssertionError(f"{' '.join(done.args)}: {done.stderr}")
        return done

    def tag(self, name: str, annotated: bool) -> None:
        if annotated:
            self.must(self.git("tag", "-a", name, "-m", name))
        else:
            self.must(self.git("tag", name))

    def run(self, *arguments: str, **environment: str) -> subprocess.CompletedProcess:
        """One run of the script, in the throwaway tree.

        Through the shell the shebang names rather than by the shebang, because a nix build
        sandbox has no `/usr/bin/env`; that the installed name needs the exec bit and that
        line is `InstallTest`'s business.
        """
        env = self.environment()
        env.update(environment)
        return subprocess.run(
            [BASH, str(self.repo / "scripts" / SCRIPT.name), *arguments],
            cwd=str(self.repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def snapshot(self) -> dict[str, object]:
        """Every file in the tree, the commit and the tags: what "wrote nothing" means."""
        taken: dict[str, object] = {}
        for path in sorted(self.repo.rglob("*")):
            relative = path.relative_to(self.repo)
            if relative.parts and relative.parts[0] == ".git":
                continue
            taken[str(relative)] = path.read_bytes() if path.is_file() else None
        taken[".git HEAD"] = self.git("rev-parse", "HEAD").stdout
        taken[".git status"] = self.git("status", "--porcelain", "--untracked-files=all").stdout
        taken[".git tags"] = self.git("tag", "--list").stdout
        return taken

    def describe(self) -> str:
        return self.git("describe", "--tags").stdout.strip()

    def tag_names(self) -> list[str]:
        return self.git("tag", "--list").stdout.split()


class Harness(unittest.TestCase):
    """The two things every test below needs: a repository, and one run of the script."""

    def make(self, **layout: object) -> Repo:
        directory = tempfile.TemporaryDirectory(prefix="bump-version-")
        self.addCleanup(directory.cleanup)
        return Repo(Path(directory.name), **layout)

    def bump(self, repo: Repo, *arguments: str, **environment: str) -> subprocess.CompletedProcess:
        done = repo.run(*arguments, **environment)
        self.assertEqual(done.returncode, 0, f"{arguments}: {done.stdout}{done.stderr}")
        self.assertEqual(repo.snapshot(), repo.before, f"{arguments}: the run wrote to the tree")
        return done

    def version(self, repo: Repo, *arguments: str) -> str:
        """The version a run prints, asserting that it is the whole of the last line of stdout."""
        done = self.bump(repo, *arguments)
        lines = done.stdout.splitlines()
        self.assertEqual(
            len(lines), 1, f"{arguments}: stdout holds {done.stdout!r}, want the version alone"
        )
        self.assertEqual(
            done.stdout, lines[0] + "\n", f"{arguments}: {done.stdout!r} is not one whole line"
        )
        return lines[0]


class BumpTest(Harness):
    """The three words, the carry, and which tag the version is computed from."""

    def test_the_three_words_move_the_named_part_and_zero_the_rest(self):
        for word, want in (("major", "1.0.0"), ("minor", "0.6.0"), ("patch", "0.5.2")):
            with self.subTest(word=word):
                self.assertEqual(self.version(self.make(head=("v0.5.1",)), word), want)

    def test_a_carry_moves_the_higher_parts(self):
        for tag, word, want in (
            ("v0.5.9", "patch", "0.5.10"),
            ("v0.9.9", "minor", "0.10.0"),
            ("v1.9.9", "major", "2.0.0"),
            ("v0.0.0", "patch", "0.0.1"),
            ("v0.99.99", "patch", "0.99.100"),
        ):
            with self.subTest(tag=tag, word=word):
                self.assertEqual(self.version(self.make(head=(tag,)), word), want)

    def test_the_highest_tag_wins_not_the_tag_git_describe_reaches(self):
        repo = self.make(older=("v0.5.1",), head=("v0.4.6",))
        self.assertEqual(repo.describe(), "v0.4.6", "this is not the trap the test claims")
        self.assertEqual(repo.tag_names(), ["v0.4.6", "v0.5.1"])
        self.assertEqual(self.version(repo, "minor"), "0.6.0")

    def test_a_numeric_comparison_not_a_lexical_one(self):
        for tags, want in ((("v0.9.9", "v0.10.0"), "0.10.1"), (("v0.10.0", "v0.9.9"), "0.10.1")):
            with self.subTest(tags=tags):
                repo = self.make(head=tags)
                self.assertEqual(self.version(repo, "patch"), want)

    def test_the_highest_tag_decides_whatever_order_the_tags_came_in(self):
        for tags in (("v0.5.1", "v0.4.6"), ("v0.4.6", "v0.5.1")):
            with self.subTest(tags=tags):
                self.assertEqual(self.version(self.make(head=tags), "patch"), "0.5.2")

    def test_a_tag_that_is_not_a_release_version_is_not_one_to_bump_from(self):
        repo = self.make(
            head=("v0.5.1", "vnext", "v0.6.0-rc1", "v0.6", "v01.2.3", "v1.2.3.4", "v0.5.1foo", "v")
        )
        self.assertEqual(self.version(repo, "minor"), "0.6.0")

    def test_an_annotated_tag_counts(self):
        """The tags this repository's releases are named by are annotated."""
        self.assertEqual(
            self.version(self.make(head=("v0.5.1",), annotated=True), "patch"), "0.5.2"
        )


class DryRunTest(Harness):
    """`--dry-run` prints what the write mode prints, and writes what it writes: nothing."""

    def test_a_dry_run_prints_the_same_version_as_the_write_mode(self):
        for word, want in (("major", "1.0.0"), ("minor", "0.6.0"), ("patch", "0.5.2")):
            with self.subTest(word=word):
                repo = self.make(head=("v0.5.1",))
                dry = self.version(repo, word, "--dry-run")
                self.assertEqual(dry, want)
                self.assertEqual(self.version(repo, word), want)
                self.assertEqual(repo.snapshot(), repo.before)

    def test_a_dry_run_writes_nothing_where_the_write_mode_would(self):
        repo = self.make(head=("v0.5.1",))
        before = repo.snapshot()
        dry = repo.run("minor", "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(
            dry.stdout, "0.6.0\n", f"a dry run printed {dry.stdout!r} and should carry the version alone"
        )
        self.assertEqual(repo.snapshot(), before, "a dry run wrote to the tree")


class RefusalTest(Harness):
    """Refusals print no version, say why, and write nothing."""

    def refused(
        self, repo: Repo, expected: int, arguments: tuple[str, ...], **environment: str
    ) -> subprocess.CompletedProcess:
        done = repo.run(*arguments, **environment)
        self.assertEqual(done.returncode, expected, f"{arguments}: {done.stdout}{done.stderr}")
        self.assertEqual(done.stdout, "", f"{arguments}: a refusal printed {done.stdout!r}")
        self.assertNotEqual(done.stderr.strip(), "", f"{arguments}: a refusal said nothing")
        self.assertEqual(repo.snapshot(), repo.before, f"{arguments}: a refusal wrote to the tree")
        return done

    def test_a_word_that_is_not_one_of_the_three_is_refused(self):
        repo = self.make(head=("v0.5.1",))
        for word in (
            "1.2.3",
            "0.6.0",
            "v0.5.1",
            "Major",
            "MAJOR",
            "patchy",
            "0",
            "latest",
            "1.2.3-rc1",
            "--dry-run",
            "",
        ):
            with self.subTest(word=word):
                done = self.refused(repo, 1, (word,))
                self.assertIn("not a bump", done.stderr)

    def test_a_call_that_is_not_the_shape_this_takes_is_refused_as_usage(self):
        repo = self.make(head=("v0.5.1",))
        for arguments in (
            (),
            ("minor", "patch"),
            ("minor", "--dry_run"),
            ("minor", "-n"),
            ("minor", "--dry-run=1"),
            ("minor", "--dry-run", "--dry-run"),
        ):
            with self.subTest(arguments=arguments):
                done = self.refused(repo, 2, arguments)
                self.assertIn("usage:", done.stderr)

    def test_a_tree_with_no_release_tag_is_refused(self):
        for tags in ((), ("vnext",), ("v0.6.0-rc1", "v1.2"), ("v",)):
            with self.subTest(tags=tags):
                done = self.refused(self.make(head=tags), 1, ("minor",))
                self.assertIn("release tag", done.stderr)

    def test_a_tree_that_is_not_a_repository_is_refused(self):
        done = self.refused(self.make(git=False), 1, ("minor",))
        self.assertIn("not a git repository", done.stderr)

    def test_a_machine_without_git_is_refused_rather_than_read_as_no_tags(self):
        repo = self.make(head=("v0.5.1",))
        only = repo.root / "bin"
        only.mkdir()
        # `dirname` is the one program the script reaches before it asks for git, so a PATH
        # holding it alone is a PATH without git.
        (only / "dirname").symlink_to(DIRNAME)
        done = self.refused(repo, 1, ("minor",), PATH=str(only))
        self.assertIn("git is not on PATH", done.stderr)

    def test_a_dry_run_refuses_where_the_write_mode_would(self):
        rejected = self.make(head=("vnext",))
        for arguments in (("minor",), ("minor", "--dry-run")):
            with self.subTest(arguments=arguments):
                self.refused(rejected, 1, arguments)
        for word in ("banana", "1.2.3"):
            with self.subTest(word=word):
                self.refused(self.make(head=("v0.5.1",)), 1, (word, "--dry-run"))


class InstallTest(unittest.TestCase):
    """The workflow runs it as a program, and the guard it reaches is one too."""

    def test_bump_version_is_installed_as_a_program_with_a_bash_env_shebang(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable")
        self.assertEqual(SCRIPT.read_text(encoding="utf-8").splitlines()[0], "#!/usr/bin/env bash")

    def test_the_shape_rule_it_reaches_beside_itself_is_installed_as_a_program(self):
        self.assertTrue(os.access(SHAPE_GUARD, os.X_OK), f"{SHAPE_GUARD} is not executable")


if __name__ == "__main__":
    unittest.main()
