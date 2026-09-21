#!/usr/bin/env python3
"""Checks the workflows that gate this repository, and the guards inside them.

Two rules live here, and both are about something this repository cannot observe by
running its own tests:

- **Every `uses:` is pinned to a commit sha**, with the release it is in a comment. A tag
  is a mutable ref: `actions/checkout@v7.0.1` is resolved on the runner, so whoever can
  move the tag decides what runs in a job that holds a token. `release.yml` is the one
  that matters most — it carries `contents: write` and pushes the tag — but `validate.yml`
  runs on every pull request, and a pull request is where a wrong revision would be tested
  and merged. The pin is shape and agreement rather than identity: whether the sha really
  is that release is checked against the tag by hand (`gh api repos/<owner>/<repo>/git/ref/
  tags/<tag>`, whose `.object.sha` is the value a `uses:` must carry), because this check
  runs where the network does not reach.
- **The release version a dispatch or a tag names is `X.Y.Z`**, which is
  `scripts/check-release-version.sh`'s rule and is driven here over a table of values
  rather than trusted to a `case` glob.

The scan is line-based on purpose: the flake's Python has no YAML parser, and both
relationships are readable without one — a `uses:` line, and a shell command's exit status.
It fails when it reaches no workflow file and no `uses:` line, so a scan that found nothing
cannot read as a clean tree.

Run it from the repository root:

    python3 scripts/check-workflows.py

It takes an optional directory to scan instead, which is how the failure is demonstrated
against a tree whose pins are not yet fixed.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW_DIR = pathlib.Path(".github") / "workflows"
# `- uses: <ref> # <comment>`, with or without the list dash. Deliberately loose about
# what follows the colon: a `uses:` line this did not recognise would be a line the scan
# never checked, so the rest of the line is parsed here rather than matched, and a line
# that names nothing is a failure instead of a skip.
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<rest>.*?)\s*$")
SHA = re.compile(r"^[0-9a-f]{40}$")
RELEASE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")

# The values the guard must take and the values it must refuse. The refused ones are the
# shapes the `case` glob this replaced admitted: `*` matches `.` and `/`, so every one of
# the first five would have reached `git tag` and `gh release create`.
ACCEPTED = ["0.1.0", "1.9.3", "10.0.0", "0.0.0", "12.0.7"]
REFUSED = [
    "",
    "1.2",
    "1.2.3.4",
    "v1.2.3",
    "1x2.3.4",
    "1.2.3-rc1",
    "1.2.3foo",
    "0.1.0+meta",
    "1.2.3/../../evil",
    "01.2.3",
    "1.02.3",
    "1.2.03",
    " 1.2.3",
    "1.2.3 ",
    "1.2.3\nx",
    ".1.2",
    "1..2",
    "1.2.",
]

# The `uses:` references the rule must accept and refuse, driven through it here as well as
# against the workflows: a rule only reachable from a file the repository happens to have stops
# being tested the moment that file changes, and the `$/<path>` form is not in a file here at
# all. The third member is how many problems the reference must raise.
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
REFERENCE_SHAPES = [
    (CHECKOUT, "v7.0.1", 0),
    (CHECKOUT, "v7.0.1 (verified against the tag)", 0),
    (SETUP_PYTHON, "v7.0.0", 0),
    (SETUP_PYTHON, None, 1),
    ("actions/checkout@v7.0.1", "v7.0.1", 1),
    ("actions/checkout", "v7.0.1", 1),
    ("actions/checkout@3d3c42e5", "v7.0.1", 1),
    ("./.github/actions/build", None, 0),
    ("$/.github/actions/build", None, 0),
    ("$/", None, 1),
    ("$/.github/actions/build@main", None, 1),
    ("", None, 1),
]

FAILURES: list[str] = []


def fail(where: str, problem: str) -> None:
    FAILURES.append(f"{where}: {problem}")


def workflow_files(root: pathlib.Path) -> list[pathlib.Path]:
    directory = root / WORKFLOW_DIR
    return sorted(
        [*directory.glob("*.yml"), *directory.glob("*.yaml")] if directory.is_dir() else []
    )


def reference_problems(
    at: str, ref: str, comment: str | None, pins: dict[str, tuple[str, str]]
) -> list[str]:
    """What is wrong with one `uses:` reference, as a list of problems.

    The rule the scan applies to every line, and the rule the table below drives: nothing to
    pin for a reference to this repository, and a full commit sha with the release it is in a
    comment for anything else. `$/<path>` is this repository at the running commit, which is
    why it carries no pin and no `@ref`, and a bare `$/` names nothing at all. The comment is
    the text after `#`, whose first word has to be the release; a note after it is allowed.
    """
    if not ref:
        return ["`uses:` names nothing"]
    if ref.startswith("./"):
        return []
    if ref.startswith("$/"):
        if len(ref) > 2 and "@" not in ref:
            return []
        return [f"`{ref}` is a self-repository reference: it is `$/<path>`, with no `@ref`"]
    action, _, revision = ref.partition("@")
    if not revision:
        return [f"`{ref}` names no revision"]
    if not SHA.fullmatch(revision):
        return [
            f"`{ref}` is not pinned to a commit sha: a tag is a mutable ref, and the pin is "
            f"what a runner resolves"
        ]
    words = comment.split() if comment else []
    label = words[0] if words else None
    problems = []
    if label is None or not RELEASE.fullmatch(label):
        problems.append(
            f"`{ref}` has no `# v<major>.<minor>.<patch>` comment saying which release the "
            f"sha is"
        )
    earlier = pins.setdefault(action, (revision, at))
    if earlier[0] != revision:
        problems.append(
            f"`{action}` is pinned to …{revision[-8:]} here and to …{earlier[0][-8:]} at "
            f"{earlier[1]}: one action, one pin"
        )
    return problems


def check_reference_shapes() -> int:
    """Drives the rule over the references above, returning how many values ran."""
    cases = 0
    for ref, comment, want in REFERENCE_SHAPES:
        cases += 1
        problems = reference_problems("probe", ref, comment, {})
        if len(problems) != want:
            fail(
                "probe",
                f"`{ref}` with comment {comment!r} raises {len(problems)} problem(s), and "
                f"this check says it raises {want}: {problems}",
            )
    # One action at two revisions in one tree, which is the drift that put this rule here.
    cases += 2
    pins: dict[str, tuple[str, str]] = {}
    reference_problems("a.yml:1", CHECKOUT, "v7.0.1", pins)
    other = "actions/checkout@" + "9" * 40
    second = reference_problems("b.yml:1", other, "v7.0.2", pins)
    if not second:
        fail("probe", "one action at two revisions is not reported")
    return cases


def check_pins(root: pathlib.Path) -> int:
    """Checks every `uses:` in the workflows, and that they agree, returning how many."""
    files = workflow_files(root)
    if not files:
        fail(f"{WORKFLOW_DIR}", "no workflow file: the scan would report a clean tree")
        return 0
    pins: dict[str, tuple[str, str]] = {}
    seen = 0
    for path in files:
        where = path.relative_to(root)
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            match = USES.match(line)
            if match is None:
                continue
            seen += 1
            at = f"{where}:{number}"
            ref, _, comment = match.group("rest").partition("#")
            for problem in reference_problems(at, ref.strip(), comment, pins):
                fail(at, problem)
    if seen == 0:
        fail(f"{WORKFLOW_DIR}", "no `uses:` line: the scan would report a clean tree")
    return seen


def guard(root: pathlib.Path) -> pathlib.Path:
    return root / "scripts" / "check-release-version.sh"


def check_guard(root: pathlib.Path) -> int:
    """Drives the release-version guard over both tables, returning how many values ran."""
    script = guard(root)
    if not script.is_file():
        fail(script.name, "the release-version guard is not there to drive")
        return 0
    cases = 0
    for value in ACCEPTED:
        cases += 1
        code, reason = run(script, value)
        if code != 0:
            fail(script.name, f"refuses the release version {value!r}: {reason}")
    for value in REFUSED:
        cases += 1
        code, reason = run(script, value)
        if code == 0:
            fail(script.name, f"accepts {value!r}, which is not a release version")
        elif not reason.strip():
            fail(script.name, f"refuses {value!r} without saying why")
    return cases


def run(script: pathlib.Path, value: str) -> tuple[int, str]:
    """Runs the guard on one value: its exit status, and what it said."""
    done = subprocess.run(
        ["bash", str(script), value],  # argv, never a shell: the value is data, not code
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, done.stderr or done.stdout


def main(argv: list[str]) -> int:
    root = pathlib.Path(argv[1]).resolve() if len(argv) > 1 else ROOT
    pins = check_pins(root)
    shapes = check_reference_shapes()
    cases = check_guard(root)
    for problem in FAILURES:
        print(f"FAIL           {problem}")
    print(
        f"workflows      {pins} `uses:` lines checked, {shapes} references, "
        f"{cases} release versions"
    )
    print(f"result         {'FAIL' if FAILURES else 'OK'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
