#!/usr/bin/env python3
"""Reject the near-homophone wire-version typo in tracked public text files."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

NEEDLE = "salvage/"
EXEMPT = {"scripts/check-wire-token.py", "scripts/test_check_wire_token.py"}


def check(root: Path) -> int:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        done = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached"],
                              capture_output=True, check=True, env=env, timeout=60)
        paths = [os.fsdecode(path) for path in done.stdout.split(b"\0") if path]
        text_files = 0
        findings = 0
        for relative in paths:
            if relative in EXEMPT:
                continue
            path = root / relative
            if path.is_symlink():
                raise ValueError(f"{relative}: refusing to follow a tracked symlink")
            data = path.read_bytes()
            if b"\0" in data:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not text.strip():
                continue
            text_files += 1
            for number, line in enumerate(text.splitlines(), 1):
                if NEEDLE in line:
                    print(f"{relative}:{number}: {line.strip()}")
                    findings += 1
        if not text_files:
            raise ValueError("no nonempty tracked public text files; refusing a vacuous scan")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"wire-token: cannot check: {error}", file=sys.stderr)
        return 2
    print(f"wire-token: {text_files} nonempty tracked text files, {findings} typos; "
          "the wire version is selvage/<major>")
    return 1 if findings else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    return check(parser.parse_args().root.resolve())


if __name__ == "__main__":
    sys.exit(main())
