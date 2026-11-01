#!/usr/bin/env python3
"""Exercise the public wire-token scan without any private-repository assumptions."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
WORK = HERE.parent / ".tmp"


class WireTokenTest(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=WORK, prefix="wire-token-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.git("init", "--quiet")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], env=self.env,
                              capture_output=True, check=True)

    def track(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.git("add", "--", name)

    def run_check(self):
        return subprocess.run([sys.executable, "-B", str(HERE / "check-wire-token.py"),
                               "--root", str(self.root)], capture_output=True, text=True)

    def test_public_text_needs_no_private_documents(self):
        self.track("PROTOCOL.md", b"The wire version is selvage/2.\n")
        self.assertEqual(self.run_check().returncode, 0)

    def test_every_major_typo_is_reported_with_location(self):
        self.track("docs/protocol.md", b"salvage/1\nsalvage/2\nsalvage/999\n")
        done = self.run_check()
        self.assertEqual(done.returncode, 1)
        self.assertIn("docs/protocol.md:3:", done.stdout)
        self.assertIn("3 typos", done.stdout)

    def test_scanner_and_test_are_the_only_exemptions(self):
        self.track("scripts/check-wire-token.py", b"salvage/\n")
        self.track("scripts/test_check_wire_token.py", b"salvage/\n")
        self.track("PROTOCOL.md", b"selvage/2\n")
        self.assertEqual(self.run_check().returncode, 0)
        self.track("scripts/another.py", b"salvage/2\n")
        self.assertEqual(self.run_check().returncode, 1)

    def test_empty_binary_and_exempt_only_scans_refuse(self):
        for data, name in ((b"", "empty"), (b"\x00binary", "binary"),
                           (b"salvage/", "scripts/check-wire-token.py")):
            with self.subTest(name=name):
                self.track(name, data)
                self.assertEqual(self.run_check().returncode, 2)

    def test_no_tracked_files_refuses(self):
        (self.root / "untracked.md").write_text("selvage/2")
        self.assertEqual(self.run_check().returncode, 2)

    def test_missing_tracked_file_refuses(self):
        self.track("PROTOCOL.md", b"selvage/2")
        (self.root / "PROTOCOL.md").unlink()
        self.assertEqual(self.run_check().returncode, 2)

    def test_working_tree_changes_are_scanned(self):
        self.track("PROTOCOL.md", b"selvage/2")
        (self.root / "PROTOCOL.md").write_text("salvage/2")
        self.assertEqual(self.run_check().returncode, 1)

    def test_git_failure_refuses(self):
        self.git("rev-parse", "--git-dir")
        import shutil
        shutil.rmtree(self.root / ".git")
        self.assertEqual(self.run_check().returncode, 2)


if __name__ == "__main__":
    unittest.main()
