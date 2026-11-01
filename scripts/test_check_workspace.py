#!/usr/bin/env python3
"""Small real Git repositories exercise the public inventory and revision boundary."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "check-workspace.py"
SPEC = importlib.util.spec_from_file_location("check_workspace", SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)
WORK = HERE.parent / ".tmp"


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=WORK, prefix="workspace-contracts-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "arbitrary layout with spaces" / "public"
        self.workspace.mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_AUTHOR_NAME="Selvage", GIT_AUTHOR_EMAIL="test@example.invalid",
                        GIT_COMMITTER_NAME="Selvage", GIT_COMMITTER_EMAIL="test@example.invalid",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        for repo in checker.REPOS:
            (self.workspace / repo).mkdir()
            self.git(repo, "init", "--quiet", "--initial-branch=main")
        for copy in checker.COPIES:
            suffix = "/nested/fixture.json" if copy.directory else ""
            content = b'{"fixture":true}\n' if (copy.source + suffix).endswith(".json") else b"canonical\n"
            self.put(copy.source_repo, copy.source + suffix, content)
            self.put(copy.consumer, copy.target + suffix, content)
        for copy in checker.COPIES:
            if copy.directory:
                shutil.copytree(self.workspace / copy.source_repo / copy.source,
                                self.workspace / copy.consumer / copy.target, dirs_exist_ok=True)
        for repo, path in checker.LOCKFILES.items():
            self.put(repo, path, json.dumps({"lockfileVersion": 3, "packages": {
                "node_modules/" + name: {"version": "1.2.3"} for name in checker.PACKAGES
            }}).encode())
        for repo in checker.REPOS:
            self.commit(repo)
            self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(self.workspace / repo), *args], env=self.env,
                              capture_output=True, check=True, text=True).stdout.strip()

    def put(self, repo, path, content):
        target = self.workspace / repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def commit(self, repo):
        self.git(repo, "add", "--all")
        self.git(repo, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "test fixture")

    def run_check(self, consumer=None, refs=None, extra=(), env=None, script=SCRIPT):
        revisions = dict.fromkeys(checker.REPOS, "HEAD") if refs is None else refs
        args = [sys.executable, "-B", str(script), "--workspace", str(self.workspace)]
        if consumer:
            args.extend(["--consumer", consumer])
        for repo, ref in revisions.items():
            args.extend(["--revision", f"{repo}={ref}"])
        return subprocess.run([*args, *extra], cwd=self.root, env=env or self.env,
                              capture_output=True, text=True, timeout=60)

    def assert_code(self, code, **kwargs):
        done = self.run_check(**kwargs)
        self.assertEqual(done.returncode, code, done.stdout + done.stderr)
        return done.stdout + done.stderr

    def test_complete_inventory_and_full_shas(self):
        output = self.assert_code(0)
        self.assertIn("20 comparisons, 0 drifted, 0 errors", output)
        for repo in checker.REPOS:
            self.assertIn(f"revision {repo} HEAD {self.git(repo, 'rev-parse', 'HEAD')}", output)
        self.assertEqual(len(checker.COPIES), 15)
        self.assertEqual(len(checker.GUARD_OWNERS), 6)
        self.assertEqual(checker.LOCKFILES["jetbrains_client"], "engine/src/test/node/package-lock.json")

    def test_candidate_head_is_not_stale_origin_main(self):
        self.put("vscode_client", "test/fixtures/limits.json", b'{"new":true}')
        self.commit("vscode_client")
        self.assertIn("test/fixtures/limits.json", self.assert_code(1, consumer="vscode_client"))
        refs = dict.fromkeys(checker.REPOS, "HEAD")
        refs["vscode_client"] = "origin/main"
        self.assert_code(0, consumer="vscode_client", refs=refs)

    def test_canonical_candidate_is_not_stale_origin_main(self):
        self.put("specification", "schema/limits.json", b'{"new":true}')
        self.commit("specification")
        self.assert_code(1, consumer="reference_server")
        refs = dict.fromkeys(checker.REPOS, "HEAD")
        refs["specification"] = "origin/main"
        self.assert_code(0, consumer="reference_server", refs=refs)

    def test_uncommitted_work_is_never_read(self):
        self.put("nvim_client", "vendor/engine/nested/fixture.json", b"not even JSON")
        shutil.rmtree(self.workspace / "specification" / "vectors")
        self.assert_code(0)

    def test_explicit_refs_are_required_even_when_main_exists(self):
        self.assertIn("explicit --revision", self.assert_code(2, refs={}))
        refs = {"mcp": "HEAD", "vscode_client": "HEAD"}
        self.assert_code(0, consumer="mcp", refs=refs)
        del refs["vscode_client"]
        self.assert_code(2, consumer="mcp", refs=refs)

    def test_consumer_does_not_require_unrelated_repositories(self):
        for repo in checker.REPOS:
            if repo not in ("mcp", "vscode_client"):
                shutil.rmtree(self.workspace / repo)
        self.assert_code(0, consumer="mcp", refs={"mcp": "HEAD", "vscode_client": "HEAD"})
        self.assert_code(2)

    def test_every_consumer_scope_passes(self):
        for repo in checker.CONSUMERS:
            with self.subTest(repo=repo):
                self.assert_code(0, consumer=repo)

    def test_full_directory_additions_deletions_and_changes(self):
        for path, content in (("extra.ts", b"extra"), ("nested/fixture.json", b'{"changed":true}')):
            self.put("mcp", "vendor/engine/" + path, content)
        self.commit("mcp")
        output = self.assert_code(1, consumer="mcp")
        self.assertIn("extra.ts", output)
        self.assertIn("nested/fixture.json", output)
        self.assertIn("mcp/scripts/sync-engine.sh vscode_client", output)
        (self.workspace / "mcp/vendor/engine/nested/fixture.json").unlink()
        self.commit("mcp")
        self.assertIn("nested/fixture.json", self.assert_code(1, consumer="mcp"))

    def test_missing_required_guard_does_not_disappear_from_inventory(self):
        (self.workspace / "jetbrains_client" / checker.GUARD).unlink()
        self.commit("jetbrains_client")
        self.assertIn("missing or empty", self.assert_code(2, consumer="jetbrains_client"))

    def test_empty_file_fails_even_if_both_sides_agree(self):
        for repo in ("specification", "vscode_client"):
            self.put(repo, checker.GUARD, b"")
            self.commit(repo)
        self.assertIn("empty file", self.assert_code(2, consumer="vscode_client"))

    def test_missing_directory_fails(self):
        shutil.rmtree(self.workspace / "nvim_client/vendor/bridge")
        self.commit("nvim_client")
        self.assert_code(2, consumer="nvim_client")

    def test_malformed_lockfile_and_missing_or_conflicting_pins_fail(self):
        path = checker.LOCKFILES["mcp"]
        valid = json.loads((self.workspace / "mcp" / path).read_bytes())
        for data in (b"not json", b"{}", b'[]', b'{"x":1,"x":2}',
                     json.dumps({"lockfileVersion": 3, "packages": {}}).encode(),
                     json.dumps({**valid, "packages": {**valid["packages"],
                         "node_modules/yjs": {"version": "^1.2.3"}}}).encode(),
                     json.dumps({**valid, "packages": {**valid["packages"],
                         "node_modules/other/node_modules/yjs": {"version": "9.0.0"}}}).encode()):
            with self.subTest(data=data):
                self.put("mcp", path, data)
                self.commit("mcp")
                self.assert_code(2, consumer="mcp")

    def test_crdt_drift_in_jetbrains_is_compared(self):
        path = checker.LOCKFILES["jetbrains_client"]
        lock = json.loads((self.workspace / "jetbrains_client" / path).read_bytes())
        lock["packages"]["node_modules/yjs"]["version"] = "9.0.0"
        self.put("jetbrains_client", path, json.dumps(lock).encode())
        self.commit("jetbrains_client")
        self.assertIn("canonical", self.assert_code(1, consumer="jetbrains_client"))

    def test_malformed_copied_json_fails(self):
        self.put("vscode_client", "test/fixtures/limits.json", b"{broken}")
        self.commit("vscode_client")
        self.assertIn("malformed JSON", self.assert_code(2, consumer="vscode_client"))

    def test_symlinks_and_file_directory_substitutions_fail(self):
        path = self.workspace / "mcp/vendor/engine"
        shutil.rmtree(path)
        path.symlink_to("../../package-lock.json")
        self.commit("mcp")
        self.assert_code(2, consumer="mcp")
        path.unlink()
        path.write_bytes(b"not a directory")
        self.commit("mcp")
        self.assert_code(2, consumer="mcp")

    def test_invalid_duplicate_and_option_refs_fail(self):
        for ref in ("--output=/tmp/unsafe", "HEAD:path", "HEAD\nmain", "$(touch unsafe)", ""):
            with self.subTest(ref=ref):
                self.assert_code(2, consumer="mcp", refs={"mcp": ref, "vscode_client": "HEAD"})
        self.assert_code(2, extra=("--revision", "mcp=HEAD"))
        self.assert_code(2, extra=("--revision", "unknown=HEAD"))
        self.assert_code(2, refs={"mcp": "missing", "vscode_client": "HEAD"}, consumer="mcp")

    def test_non_repository_or_enclosing_repository_is_rejected(self):
        shutil.rmtree(self.workspace / "mcp/.git")
        self.git("vscode_client", "init", "--quiet", str(self.workspace))
        self.assert_code(2, consumer="mcp")

    def test_script_location_and_checkout_layout_are_independent(self):
        script = self.root / "unrelated" / "checker.py"
        script.parent.mkdir()
        shutil.copyfile(SCRIPT, script)
        self.assert_code(0, script=script)

    def test_git_environment_cannot_redirect_reads(self):
        env = {**self.env, "GIT_DIR": str(self.workspace / "mcp/.git"),
               "GIT_WORK_TREE": str(self.workspace / "mcp"), "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "alias.rev-parse", "GIT_CONFIG_VALUE_0": "!exit 0"}
        self.assert_code(0, env=env)

    def test_each_ref_resolves_once_and_object_reads_use_only_sha(self):
        original = checker.git
        with mock.patch.object(checker, "git", wraps=original) as calls:
            repo = checker.Repository(self.workspace / "mcp", "HEAD")
            repo.read("vendor/engine", True)
            repo.read("vendor/engine", True)
        resolutions = [call for call in calls.call_args_list if "--verify" in call.args]
        self.assertEqual(len(resolutions), 1)
        trees = [call for call in calls.call_args_list if "ls-tree" in call.args]
        self.assertEqual(len(trees), 1)
        self.assertIn(repo.sha, trees[0].args)
        self.assertNotIn("HEAD", trees[0].args)


if __name__ == "__main__":
    unittest.main()
