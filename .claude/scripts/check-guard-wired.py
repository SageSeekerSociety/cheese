#!/usr/bin/env python3
"""check-guard-wired.py — every guard under `.claude/scripts/` has to be called
by something.

WHY THIS EXISTS. Three guards landed in one day and none of them was wired:
`check-i18n-lists.py` (#4019), `check-seam-tests.py` (#4010) and
`check-split-frontend.py` (#4005) were named by nothing but their own self-tests,
and nothing ran those either. A guard a lane never starts is not a guard: it
reports on one laptop, once, and never again, and the only reader who can tell
it apart from a clean tree is someone listing the directory by hand. That is how
the round-24 diagnosis found these, and it would have found the next one the
same way. This makes it a gate.

WHAT "WIRED" MEANS. The script's file name appears in a wiring file —
`.pre-commit-config.yaml`, `Taskfile.yml`, `.cheese/pre-push`, or any
`.github/workflows/*.yml`. The two files a guard usually lands in are
repo-guards.yml (a step of its own, or the Guards self-test list) and
pre-commit; a workflow that owns what the guard reads is just as good — the
`claude-md-review.yml` step that runs `check-claude-md-review.py` is why the set
is "any wiring file" rather than those two by name.

Named by its own self-test counts too, and only because repo-guards.yml now
collects `test_*.py` from `.claude/scripts`: `test_check_x.py` beside
`check-x.py` is that guard's self-test by convention, and it is started on every
push. Before that collection this was the shape of the whole problem — all three
guards the round-24 diagnosis found were named by their own self-tests and by
nothing else, which is why "a sibling test exists" is not on its own a lane. Two
things make it one: the collection runs the file, and the file runs the guard
against the repository rather than only against fixtures. The naming pairing is
the part this can check; the second is a convention the guard cannot see and the
reviewer has to.

Comments do not count. Each line is stripped of a `#` that starts it or follows
whitespace before the search, so the paragraph in frontend.yml that explains
which self-test repo-guards runs does not wire anything.

A guard that no lane CAN run is named in ON_DEMAND with the reason: one reads a
refactor rather than the tree, one is red wherever it runs. The list is
self-checking in both directions — an entry whose script is gone, or whose
script has since been wired, is a failure — so it cannot quietly become the
place the next unwired guard is forgotten.

SCOPE. This judges whether a guard is started, not whether the thing that would
notice it going wrong is started. The second half is repo-guards.yml's CI helper
tests step, which collects `test_*.py` from both script trees and skips
`test_<stem>.py` for every guard named in ON_DEMAND below, so that one list
decides both. Its comment says the rest.

Usage: check-guard-wired.py [--root DIR]   check a tree (default: repo root)
       check-guard-wired.py --self-test    prove this guard catches what it claims
Exit 0 every guard is wired, 1 at least one is not, 2 the tree could not be judged.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCRIPTS_DIR = Path(".claude/scripts")

#: What counts as a guard here: the tree-wide checks live under this name.
GUARD_GLOBS = ("check-*.py", "check-*.sh")

#: Files that run things. A guard named in one of these is wired.
WIRING_FILES = (".pre-commit-config.yaml", "Taskfile.yml", ".cheese/pre-push")
WIRING_GLOBS = (".github/workflows/*.yml", ".github/workflows/*.yaml")

#: Guards with no lane that could run them, and why. Every entry is checked:
#: the script must exist, and must really be unwired — wire it and this entry
#: goes red until it is deleted. Keep the list empty unless the script truly
#: cannot run against the tree.
ON_DEMAND: dict[str, str] = {
    "check-split-frontend.py": (
        "judges one refactor, not the tree: the run it is built for takes "
        "--base-ref/--old-file/--new-file. Its --self-test is green but needs "
        "frontend/node_modules and takes 5.4 minutes, which the repo-guards job "
        "has neither an install for nor the budget for; wiring it belongs in "
        "frontend.yml's check job, where the install is, as a decision of its own"
    ),
    "check-seam-tests.py": (
        "red on the tree it would judge: two split tests under backend/tests/unit "
        "hit the recursive-class-methods pattern for reasons the hint's rewrite "
        "would break — one walks into a ClassDef on purpose to find a method by "
        "name, the other wants the file's own bindings, not the top level's. "
        "Wiring it before those two are rewritten is a lane that can only fail; "
        "the run belongs with that rewrite, and test_check_seam_tests.py asserts "
        "the tree is clean, so it is red too"
    ),
}

#: A `#` starting a line, or following whitespace, begins a comment. YAML and
#: shell agree on that much; a `#` inside a word (a URL fragment, a hex colour)
#: is not one.
COMMENT = re.compile(r"(?:(?<=\s)|^)#.*$")


class CannotJudge(Exception):
    """A tree this check needs is missing or unreadable."""


def guards(root: Path) -> list[str]:
    """Every guard script in the tree, by file name."""
    where = root / SCRIPTS_DIR
    if not where.is_dir():
        raise CannotJudge(f"{SCRIPTS_DIR} is not a directory")
    return sorted(
        {path.name for pattern in GUARD_GLOBS for path in where.glob(pattern)}
    )


def runs(text: str) -> str:
    """The text with everything after a `#` comment marker dropped, per line."""
    return "\n".join(COMMENT.sub("", line) for line in text.splitlines())


def wiring(root: Path) -> dict[str, str]:
    """Every wiring file's runnable text, by path relative to the tree."""
    paths = [root / name for name in WIRING_FILES]
    paths += [path for pattern in WIRING_GLOBS for path in sorted(root.glob(pattern))]
    found = {
        str(path.relative_to(root)): runs(path.read_text(encoding="utf-8", errors="replace"))
        for path in paths
        if path.is_file()
    }
    if not found:
        raise CannotJudge(
            "no wiring file found (looked for "
            + ", ".join([*WIRING_FILES, *WIRING_GLOBS])
            + ")"
        )
    return found


def names(script: str) -> re.Pattern[str]:
    """`script` as a whole file name: `check-x.py` does not match `check-x.pyc`."""
    return re.compile(rf"(?<![\w.-]){re.escape(script)}(?![\w.-])")


def wired_in(script: str, wiring_files: dict[str, str]) -> list[str]:
    """The wiring files that name this guard, in path order."""
    pattern = names(script)
    return [path for path, text in sorted(wiring_files.items()) if pattern.search(text)]


def self_tests(root: Path) -> dict[str, str]:
    """Sibling self-tests by stem, as paths relative to the tree.

    Only the underscore form: repo-guards.yml collects `test_*.py`, so a
    `test-check-x.py` beside a guard is started by nothing and is not a lane.
    """
    return {
        path.stem: str(path.relative_to(root))
        for path in sorted((root / SCRIPTS_DIR).glob("test_*.py"))
    }


def self_test_stem(script: str) -> str:
    """`check-i18n-lists.py` -> `test_check_i18n_lists`, the sibling's stem."""
    return "test_" + re.sub(r"\W+", "_", Path(script).stem)


def lanes(
    script: str, wiring_files: dict[str, str], tests: dict[str, str]
) -> list[str]:
    """Everything that starts this guard, as paths relative to the tree.

    A wiring file naming it is the direct form. The sibling self-test is the
    other, and it counts on the same terms as everything else here: something
    has to start it. The collection in repo-guards.yml does, for every
    `test_*.py` under `.claude/scripts` except the ones ON_DEMAND derives — a
    guard with no lane has no lane for its self-test either, which is why an
    excused guard is judged on its wiring files alone.
    """
    found = wired_in(script, wiring_files)
    if script not in ON_DEMAND and (sibling := tests.get(self_test_stem(script))):
        found.append(sibling)
    return found


HOW_TO_WIRE = (
    "name it where something runs: a step in .github/workflows/repo-guards.yml "
    "(its own `run:`, or the Guards self-test list), a hook in "
    ".pre-commit-config.yaml, the workflow that owns what it reads, or a sibling "
    "test_check_<script>.py under .claude/scripts that runs it against the tree"
)


def judge(root: Path) -> tuple[int, list[str], str]:
    """The exit code, the problems, and the sentence that says how to wire.

    A guard nothing runs is one problem per script, so the instruction is said
    once at the end rather than three times: the same sentence repeated down a
    list of five is a sentence nobody finishes reading.
    """
    try:
        scripts = guards(root)
        wiring_files = wiring(root)
        tests = self_tests(root)
    except CannotJudge as exc:
        return 2, [f"cannot judge: {exc}"], ""

    problems: list[str] = []
    unwired = [
        script
        for script in scripts
        if script not in ON_DEMAND and not lanes(script, wiring_files, tests)
    ]
    problems += [f"{SCRIPTS_DIR / script}: nothing runs it" for script in unwired]

    for script, reason in sorted(ON_DEMAND.items()):
        if script not in scripts:
            problems.append(
                f"ON_DEMAND names {script}, which is not in {SCRIPTS_DIR}: delete the entry"
            )
            continue
        found = lanes(script, wiring_files, tests)
        if found:
            problems.append(
                f"ON_DEMAND still excuses {script}, but {', '.join(found)} names it: "
                f"the entry ({reason}) is spent, delete it"
            )
    return (1 if problems else 0), problems, HOW_TO_WIRE if unwired else ""


def self_test() -> int:
    """The sibling test file, which is where the cases live."""
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve().with_name("test_check_guard_wired.py"))],
        check=False,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(REPO_ROOT), help="the tree to check")
    parser.add_argument("--self-test", action="store_true", help="prove the guard fires")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    code, problems, hint = judge(Path(args.root))
    if code == 0:
        print("ok: every guard under .claude/scripts is named by something that runs it")
    else:
        for problem in problems:
            print(problem, file=sys.stderr)
        if hint:
            print(f"Wire each one: {hint}.", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
