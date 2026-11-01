#!/usr/bin/env python3
"""Compare public cross-repository contracts at explicit, immutable Git revisions.

No fetch, checkout, working-tree read, or implicit revision selection is performed.
See docs/workspace-contracts.md for the inventory and CI/local invocation.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class ContractError(Exception):
    pass


@dataclass(frozen=True)
class Copy:
    source_repo: str
    source: str
    consumer: str
    target: str
    sync: str
    directory: bool = False


COPIES = tuple(
    Copy("vscode_client", f"src/{part}", repo, f"{base}/{part}",
         f"{repo}/scripts/sync-engine.sh vscode_client", True)
    for repo, base in (("nvim_client", "vendor"), ("mcp", "vendor"), ("web_client", "src"))
    for part in ("engine", "bridge")
) + (
    Copy("specification", "vectors", "reference_server", "vectors",
         "reference_server/scripts/sync-vectors.sh specification", True),
    Copy("specification", "vectors/anchors/relative-position.json", "vscode_client",
         "test/fixtures/anchors/relative-position.json",
         "cp specification/vectors/anchors/relative-position.json vscode_client/test/fixtures/anchors/relative-position.json"),
    Copy("specification", "schema/limits.json", "reference_server", "crates/harness/tests/limits.json",
         "reference_server/scripts/sync-limits.sh specification"),
    Copy("specification", "schema/limits.json", "vscode_client", "test/fixtures/limits.json",
         "cp specification/schema/limits.json vscode_client/test/fixtures/limits.json"),
)
GUARD = "scripts/check_dry_run_gating.py"
GUARD_OWNERS = ("specification", "vscode_client", "nvim_client", "web_client",
                "jetbrains_client", "reference_server")
COPIES += tuple(Copy("specification", GUARD, repo, GUARD,
                     f"cp specification/{GUARD} {repo}/{GUARD}")
                for repo in GUARD_OWNERS if repo != "specification")
LOCKFILES = {
    "vscode_client": "package-lock.json",
    "nvim_client": "package-lock.json",
    "web_client": "package-lock.json",
    "jetbrains_client": "engine/src/test/node/package-lock.json",
    "mcp": "package-lock.json",
}
PACKAGES = ("yjs", "lib0", "y-protocols")
REPOS = tuple(sorted(set(GUARD_OWNERS) | set(LOCKFILES)))
CONSUMERS = tuple(repo for repo in REPOS if repo != "specification")
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./@{}~^+\-]*")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")


def git(repo: Path, *arguments: str) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1")
    try:
        done = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *arguments],
                              capture_output=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError(f"{repo}: Git could not run: {error}") from error
    if done.returncode:
        raise ContractError(f"{repo}: git {arguments[0]} failed: "
                            f"{done.stderr.decode('utf-8', errors='replace').strip()}")
    return done.stdout


def json_object(data: bytes, label: str) -> dict:
    def unique(pairs: list[tuple]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ValueError(f"non-JSON constant {value}")

    try:
        value = json.loads(data, object_pairs_hook=unique, parse_constant=invalid)
        if not isinstance(value, dict) or not value:
            raise ValueError("expected a nonempty JSON object")
        return value
    except (ValueError, UnicodeError) as error:
        raise ContractError(f"{label}: malformed JSON: {error}") from error


class Repository:
    def __init__(self, path: Path, ref: str):
        self.path = path.resolve()
        top = Path(os.fsdecode(git(self.path, "rev-parse", "--show-toplevel")).strip()).resolve()
        if top != self.path:
            raise ContractError(f"{path}: not a repository root (found {top})")
        self.sha = git(self.path, "rev-parse", "--verify", "--end-of-options",
                       f"{ref}^{{commit}}").decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.sha):
            raise ContractError(f"{path}: revision did not resolve to a full commit SHA")
        self.cache: dict[tuple[str, bool], dict[str, tuple[str, bytes]]] = {}

    def read(self, path: str, directory: bool = False) -> dict[str, tuple[str, bytes]]:
        key = (path, directory)
        if key in self.cache:
            return self.cache[key]
        label = f"{self.path.name}/{path} at {self.sha}"
        entries = git(self.path, "ls-tree", "-r", "-z", "--full-tree", self.sha, "--", path)
        result = {}
        for entry in entries.split(b"\0"):
            if not entry:
                continue
            meta, raw_name = entry.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            name = os.fsdecode(raw_name)
            if directory:
                if not name.startswith(path + "/"):
                    raise ContractError(f"{label}: expected a directory")
                relative = name[len(path) + 1:]
            else:
                if name != path:
                    raise ContractError(f"{label}: expected a file")
                relative = ""
            if kind != "blob" or mode not in ("100644", "100755"):
                raise ContractError(f"{label}: {name}: expected a regular file, not {mode} {kind}")
            data = git(self.path, "cat-file", "blob", oid)
            if not data.strip():
                raise ContractError(f"{label}: {name}: empty file")
            if name.endswith(".json"):
                json_object(data, f"{self.path.name}/{name}")
            result[relative] = (mode, data)
        if not result:
            raise ContractError(f"{label}: missing or empty {'directory' if directory else 'file'}")
        self.cache[key] = result
        return result

    def pins(self, path: str) -> dict[str, str]:
        lock = json_object(self.read(path)[""][1], f"{self.path.name}/{path}")
        packages = lock.get("packages")
        if lock.get("lockfileVersion") not in (2, 3) or not isinstance(packages, dict):
            raise ContractError(f"{self.path.name}/{path}: expected npm lockfile v2/v3 packages")
        pins = {}
        for name in PACKAGES:
            entry = packages.get(f"node_modules/{name}")
            version = entry.get("version") if isinstance(entry, dict) else None
            if not isinstance(version, str) or not VERSION.fullmatch(version):
                raise ContractError(f"{self.path.name}/{path}: missing or malformed {name} version")
            for location, nested in packages.items():
                if location.endswith(f"/node_modules/{name}"):
                    if not isinstance(nested, dict) or nested.get("version") != version:
                        raise ContractError(f"{self.path.name}/{path}: conflicting {name} pin at {location}")
            pins[name] = version
        return pins


def arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path,
                        help="parent containing repositories under their public repository names")
    parser.add_argument("--consumer", choices=CONSUMERS,
                        help="compare only this consumer against its canonical sources")
    parser.add_argument("--revision", action="append", default=[], metavar="REPO=REF",
                        help="required once for every needed repository; no implicit refs")
    args = parser.parse_args(argv)
    revisions = {}
    for item in args.revision:
        repo, separator, ref = item.partition("=")
        if not separator or repo not in REPOS or not REF.fullmatch(ref):
            parser.error(f"invalid revision {item!r}; use a known REPO and a non-option Git commit ref")
        if repo in revisions:
            parser.error(f"duplicate revision for {repo}")
        revisions[repo] = ref
    args.revisions = revisions
    return args


def main(argv: list[str] | None = None) -> int:
    args = arguments(sys.argv[1:] if argv is None else argv)
    copies = [copy for copy in COPIES if args.consumer is None or copy.consumer == args.consumer]
    locks = [repo for repo in LOCKFILES if args.consumer is None or repo == args.consumer]
    needed = {repo for copy in copies for repo in (copy.source_repo, copy.consumer)}
    if locks:
        needed.update(["vscode_client", *locks])
    missing = needed - args.revisions.keys()
    if missing:
        print("cannot: explicit --revision REPO=REF required for " + ", ".join(sorted(missing)), file=sys.stderr)
        return 2
    repositories = {}
    errors = 0
    for name in sorted(needed):
        try:
            repositories[name] = Repository(args.workspace / name, args.revisions[name])
            print(f"revision {name} {args.revisions[name]} {repositories[name].sha}", flush=True)
        except ContractError as error:
            print(f"cannot: {error}", file=sys.stderr)
            errors += 1
    if errors:
        return 2
    drift = 0
    checked = 0
    for copy in copies:
        label = f"{copy.consumer}/{copy.target} <- {copy.source_repo}/{copy.source}"
        try:
            source = repositories[copy.source_repo].read(copy.source, copy.directory)
            target = repositories[copy.consumer].read(copy.target, copy.directory)
            changes = [name or copy.target for name in sorted(source.keys() | target.keys())
                       if source.get(name) != target.get(name)]
            checked += 1
            if changes:
                drift += 1
                print(f"drift: {label}\n  added, deleted, or changed: {', '.join(changes)}")
                print(f"  sync from the selected canonical revision, from the workspace: {copy.sync}")
            else:
                print(f"same: {label}")
        except ContractError as error:
            errors += 1
            print(f"cannot: {label}: {error}\n  restore/sync: {copy.sync}", file=sys.stderr)
    if locks:
        try:
            baseline = repositories["vscode_client"].pins(LOCKFILES["vscode_client"])
        except ContractError as error:
            errors += 1
            baseline = None
            print(f"cannot: {error}; restore the canonical CRDT pins", file=sys.stderr)
        for name in locks:
            try:
                pins = repositories[name].pins(LOCKFILES[name])
                checked += 1
                if baseline is not None:
                    if pins != baseline:
                        drift += 1
                        print(f"drift: {name}/{LOCKFILES[name]}: {pins}; canonical {baseline}")
                        print("  sync: pin yjs, lib0, and y-protocols to the canonical VS Code versions "
                              "in the consumer package manifest, regenerate its lockfile, and rerun")
                    else:
                        print(f"same: {name}/{LOCKFILES[name]} CRDT pins {pins}")
            except ContractError as error:
                errors += 1
                print(f"cannot: {error}; restore/regenerate the consumer lockfile", file=sys.stderr)
    print(f"workspace: {checked} comparisons, {drift} drifted, {errors} errors")
    return 2 if errors else 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
