#!/usr/bin/env python3
"""Fail when a source file that changed on this branch crossed its size cap.

    .claude/scripts/check-file-sizes.py [--base origin/main] [--root DIR]
    .claude/scripts/check-file-sizes.py --self-test

WHY A CAP AT ALL. A file does not become unreadable in one commit, and nothing
in the tree notices the commit that makes it so. `backend/app/domain/agent/chat.py`
(7211 lines), `frontend/src/api.ts` (3747) and 33 others were already past their
cap when this check landed, so this is a ratchet, not a cliff: a file that is
already over its cap may only shrink, and a file that is under it may grow up to
the cap and no further. Only files that CHANGED against the merge base are read,
which is what makes it cheap enough to run on every commit.

The caps are smell tests, not laws: 1000 lines for `frontend/src` code, 1500 for
`backend/app`. A file over the cap is not a defect — it is a file that has
stopped having one reason to change, and the honest answer at that point is a
split (see `.claude/rules/architecture.md`), not a bigger cap.

THE MERGE BASE IS NOT OPTIONAL. A file is judged at the size it had where this
branch left the base branch, and only the files this branch actually changed are
judged at all. Neither question survives being rephrased as "compare the two
tips": the base branch moves on after a branch forks, so a file only the base
branch changed is in that diff, and its shrink reads as this branch's growth.
That is not hypothetical — PR #2173 was failed for `frontend/src/api.ts`
"growing" 3610 → 3615 when main had *shrunk* it 3615 → 3610 after the branch
forked. A checkout that cannot compute a merge base is therefore a 2 (cannot
judge), never a guess against whichever tree it happens to be holding.

EXEMPTIONS. A registry, a manifest or a generated table legitimately grows line
by line, and a cap on it only teaches people to route around the cap. An
exemption is one exact repo-relative path and one reason, declared in EXEMPT
below — never a pattern, and never a line count. There are none today; adding
one is a code change that goes through review, which is the point. (This is why
they are not in a config file: `backend/tests/unit/test_domain_import_guard.py`
makes the same choice for the same reason.)

Exit 0 nothing over its cap, 1 something is, 2 the tree could not be judged.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Cap:
    """One size cap: which files it covers, how big they may be, and why."""

    prefix: str
    extensions: tuple[str, ...]
    limit: int
    why: str


CAPS: tuple[Cap, ...] = (
    Cap(
        prefix="frontend/src/",
        extensions=(".vue", ".ts", ".js"),
        limit=1000,
        why=(
            "a component or module that long is several of them, and the props/events "
            "boundary that would have kept them apart is now inside one file"
        ),
    ),
    Cap(
        prefix="backend/app/",
        extensions=(".py",),
        limit=1500,
        why=(
            "a module that long has stopped having one reason to change; splitting it "
            "is what makes the service layer a boundary instead of a directory"
        ),
    ),
)

#: Exact repo-relative path -> why it is allowed to exceed its cap. No patterns:
#: an exemption that can absorb a file nobody looked at is not an exemption.
EXEMPT: dict[str, str] = {}


@dataclass(frozen=True)
class Finding:
    path: str
    current: int
    cap: int
    base: int | None  # None: the file did not exist at the merge base
    why: str


def cap_for(path: str) -> Cap | None:
    """The cap covering `path`, or None if this file is not judged."""
    for cap in CAPS:
        if path.startswith(cap.prefix) and path.endswith(cap.extensions):
            return cap
    return None


def judge(path: str, current: int, base: int | None) -> Finding | None:
    """Is `path` over the line it is allowed to reach? Pure, so it is testable.

    `base` is the line count at the merge base, or None for a file that did not
    exist there. A new file gets the cap; an existing file gets whichever is
    larger, its current headroom or the size it already had — so an oversized
    file is frozen where it stands and may only shrink.
    """
    if path in EXEMPT:
        return None
    cap = cap_for(path)
    if cap is None:
        return None
    allowed = cap.limit if base is None else max(cap.limit, base)
    if current <= allowed:
        return None
    return Finding(path=path, current=current, cap=cap.limit, base=base, why=cap.why)


def count_lines(data: bytes) -> int:
    """Lines as an editor counts them, without decoding (a .vue file is UTF-8,
    but the cap must not depend on that)."""
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def changed_files(root: Path, merge_base: str) -> list[str]:
    """Files whose working-tree content differs from the merge base.

    Untracked-but-not-ignored files are included on purpose: the common case
    this check exists for is a brand new file that is over the cap before it is
    ever committed, and `git diff` does not know about it yet.
    """
    diff = git("diff", "--name-only", "--no-renames", merge_base, cwd=root)
    untracked = git("ls-files", "--others", "--exclude-standard", cwd=root)
    return sorted({line for line in (diff + untracked).splitlines() if line.strip()})


class NoMergeBase(Exception):
    """`base` and HEAD share no history in this checkout, so nothing honest to
    judge against: a shallow checkout has no common ancestor to compute."""


def resolve_base(root: Path, base: str) -> str:
    """The commit where this branch left `base` — its merge base, or nothing.

    The merge base is the only honest answer, and `LookupError` (no such ref)
    and `NoMergeBase` (no common history) are kept apart because they need
    different fixes. There is deliberately no fallback to `base`'s tip: a branch
    is judged at the size a file had where it left the base branch, so comparing
    against a tip that has moved on since judges the base branch's own changes
    as this branch's. See the module docstring for the PR that proved it.
    """
    try:
        git("rev-parse", "--verify", f"{base}^{{commit}}", cwd=root)
    except subprocess.CalledProcessError:
        raise LookupError(base) from None
    try:
        return git("merge-base", base, "HEAD", cwd=root).strip()
    except subprocess.CalledProcessError:
        raise NoMergeBase(base) from None


def judge_tree(root: Path, base: str) -> tuple[int, list[str]]:
    """(exit code, lines to print) for the tree at `root`."""
    try:
        against = resolve_base(root, base)
    except FileNotFoundError:
        return 2, ["cannot judge: git is not on PATH"]
    except LookupError:
        return 2, [
            f"cannot judge: {base} is not a ref this checkout has.",
            "run `git fetch origin main` (or pass --base <ref>), then try again.",
        ]
    except NoMergeBase:
        return 2, [
            f"cannot judge: {base} and HEAD have no merge base in this checkout,",
            "so there is no honest tree to compare this branch against — a shallow",
            "checkout (`git fetch --depth=1`) keeps no history the two share.",
            "Comparing against the tip of the base branch instead would fail files",
            "this branch never touched, so this is a 2 rather than a guess.",
            "Fetch the history, then try again:",
            "    git fetch --unshallow origin    # or a bounded `git fetch --deepen=<n> origin`",
        ]

    paths = changed_files(root, against)

    findings: list[Finding] = []
    exempted: list[str] = []
    judged = 0
    for path in paths:
        if cap_for(path) is None:
            continue
        if path in EXEMPT:
            exempted.append(f"{path} — {EXEMPT[path]}")
            continue
        absolute = root / path
        if not absolute.is_file():
            continue  # deleted or renamed away: nothing to weigh
        try:
            current = count_lines(absolute.read_bytes())
        except OSError as exc:
            return 2, [f"cannot judge: {path} could not be read: {exc}"]
        try:
            base_count: int | None = count_lines(
                subprocess.run(
                    ["git", "show", f"{against}:{path}"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                ).stdout
            )
        except subprocess.CalledProcessError:
            base_count = None  # not in the base tree: a new file
        judged += 1
        finding = judge(path, current, base_count)
        if finding is not None:
            findings.append(finding)

    lines: list[str] = []
    if findings:
        lines.append(f"{len(findings)} file(s) are over the size cap:")
        lines.append("")
        for finding in sorted(findings, key=lambda f: -f.current):
            was = (
                f"it was {finding.base} lines at the merge base with {base}, and a "
                "file already over its cap may only shrink"
                if finding.base is not None and finding.base > finding.cap
                else (
                    f"it was {finding.base} lines at the merge base with {base}"
                    if finding.base is not None
                    else "it is a new file"
                )
            )
            lines.append(f"  {finding.path}")
            lines.append(f"    now {finding.current} lines, cap {finding.cap} — {was}.")
            lines.append(f"    Why the cap: {finding.why}.")
            lines.append("")
        lines.append("Split the file (see .claude/rules/architecture.md), or — if it is a")
        lines.append("registry that legitimately grows — add it to EXEMPT in this script with")
        lines.append("a reason, in a commit somebody reviews.")
        lines.append(f"If {base} is behind, `git fetch origin` first.")
        lines.append("::error::file size cap exceeded")
        return 1, lines

    lines.append(
        f"PASS: {judged} changed file(s) judged against the merge base with {base}, "
        f"caps {CAPS[1].limit} (backend/app) / {CAPS[0].limit} (frontend/src)"
    )
    for entry in exempted:
        lines.append(f"  exempt: {entry}")
    return 0, lines


# ---------------------------------------------------------------------------
# Self test. A cap nobody has watched fire is a number in a file.
# ---------------------------------------------------------------------------

_LOREM = "x = 1\n"


def _write(path: Path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_LOREM * lines)


def _repo_git(root: Path):
    """A `git` runner for a throwaway repository, under a fixed test identity."""

    def git(*args: str):
        return subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )

    return git


def _script_runner(root: Path):
    """A runner for THIS script against `root`, the way CI runs it: a real
    process with a real exit code, so a 2 cannot be mistaken for a pass."""

    def run(base: str = "HEAD") -> tuple[int, str]:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(root), "--base", base],
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr

    return run


def self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # The rules, directly.
    check("a new file at the cap passes", judge("backend/app/a.py", 1500, None), None)
    check("a new backend file over the cap fails", judge("backend/app/a.py", 1501, None) is not None, True)
    check("a new frontend file over the cap fails", judge("frontend/src/a.vue", 1001, None) is not None, True)
    check("a frontend .ts file is judged too", judge("frontend/src/a.ts", 1001, None) is not None, True)
    check("a file outside the caps is not judged", judge("backend/tests/a.py", 999999, None), None)
    check("a doc is not judged", judge("docs/a.md", 999999, None), None)
    check("an oversized file may hold its size", judge("backend/app/a.py", 3000, 3000), None)
    check("an oversized file may not grow", judge("backend/app/a.py", 3001, 3000) is not None, True)
    check("an oversized file may shrink", judge("backend/app/a.py", 2999, 3000), None)
    check("cap_for picks the backend cap", cap_for("backend/app/x.py") is CAPS[1], True)
    check("cap_for ignores a .py outside app", cap_for("backend/alembic/x.py"), None)

    # The exemption mechanism itself, on a path that is not exempt today —
    # EXEMPT is empty on purpose, so exercising it means installing one.
    EXEMPT["backend/app/registry.py"] = "self-test only"
    try:
        check("an exempt path is not judged", judge("backend/app/registry.py", 99999, None), None)
        check("... and only that path", judge("backend/app/other.py", 99999, None) is not None, True)
    finally:
        del EXEMPT["backend/app/registry.py"]

    # End to end, in a throwaway repository: plant the files, run the real
    # command, and require it to come back red for the stated reason.
    with tempfile.TemporaryDirectory(prefix="file-sizes-selftest-") as raw:
        root = Path(raw)
        git = _repo_git(root)
        run = _script_runner(root)
        try:
            git("init", "-q")
            _write(root / "backend/app/legacy.py", 3000)
            _write(root / "frontend/src/Legacy.vue", 2000)
            git("add", "-A")
            git("commit", "-qm", "base")
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            print(f"SELF-TEST FAIL: could not build the fixture repository: {exc}")
            return 1

        # 1. grown, already-oversized, backend
        _write(root / "backend/app/legacy.py", 3001)
        code, out = run()
        check("a grown oversized file is blocked", code, 1)
        check("... and the message names it", "backend/app/legacy.py" in out, True)
        check("... and says it may only shrink", "may only shrink" in out, True)
        check("... and gives the cap", "cap 1500" in out, True)

        # 2. a brand new oversized backend file
        _write(root / "backend/app/fresh.py", 1600)
        code, out = run()
        check("a new oversized file is blocked", code, 1)
        check("... and is reported as new", "it is a new file" in out, True)

        # 3. a brand new oversized frontend component
        _write(root / "frontend/src/Fresh.vue", 1200)
        code, out = run()
        check("a new oversized component is blocked", code, 1)
        check("... under the frontend cap", "cap 1000" in out, True)

        # 4. shrinking the oversized ones is not blocked
        _write(root / "backend/app/legacy.py", 2900)
        _write(root / "frontend/src/Legacy.vue", 1900)
        (root / "backend/app/fresh.py").unlink()
        (root / "frontend/src/Fresh.vue").unlink()
        code, out = run()
        check("shrinking an oversized file is allowed", code, 0)
        check("... and the run says so", "PASS" in out, True)

        # 5. a file under the cap may grow up to it, and no further
        _write(root / "backend/app/small.py", 1500)
        code, _ = run()
        check("growing to exactly the cap is allowed", code, 0)
        _write(root / "backend/app/small.py", 1501)
        code, out = run()
        check("growing past the cap is blocked", code, 1)
        check("... and names the cap it passed", "cap 1500" in out, True)

        # 6. an unjudgeable base is a 2, never a pass
        (root / "backend/app/small.py").unlink()
        code, out = run("no-such-ref")
        check("a missing base exits 2", code, 2)
        check("... and says so", "cannot judge" in out, True)

    # 7. A fork, with real history on both sides — the shape of the bug. The
    #    branch forks from the base branch, the base branch moves on and SHRINKS
    #    a file the branch never touched, and the branch is then judged. That is
    #    PR #2173: "frontend/src/api.ts now 3615 lines ... it was 3610 lines at
    #    the tip of origin/main", for a file the branch never opened. A file only
    #    the base branch changed must not be in the branch's diff at all.
    with tempfile.TemporaryDirectory(prefix="file-sizes-selftest-") as raw:
        root = Path(raw)
        git = _repo_git(root)
        run = _script_runner(root)
        try:
            git("init", "-q")
            _write(root / "backend/app/legacy.py", 3000)
            _write(root / "backend/app/theirs.py", 10)
            git("add", "-A")
            git("commit", "-qm", "fork point")
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            print(f"SELF-TEST FAIL: could not build the fixture repository: {exc}")
            return 1
        base_branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

        git("checkout", "-q", "-b", "topic")
        _write(root / "backend/app/theirs.py", 11)  # this branch's own change
        git("add", "-A")
        git("commit", "-qm", "the branch's work")
        git("checkout", "-q", base_branch)
        _write(root / "backend/app/legacy.py", 2900)  # the base branch shrinks it
        git("add", "-A")
        git("commit", "-qm", "the base branch shrinks legacy.py")
        git("checkout", "-q", "topic")

        # 7a. the branch passes: the file only the base branch changed is not in
        #     its diff, so it is not judged — and the run says which tree it did
        #     judge it against, rather than leaving that to be guessed.
        code, out = run(base_branch)
        check("a file only the base branch changed is not judged", code, 0)
        check("... it is not even named", "legacy.py" not in out, True)
        check("... and the run names the merge base", f"merge base with {base_branch}" in out, True)

        # 7b. the same file grown by THIS branch is blocked, at the size it had
        #     at the merge base — not at the base branch's tip.
        _write(root / "backend/app/legacy.py", 3001)
        code, out = run(base_branch)
        check("the branch growing an oversized file is blocked", code, 1)
        check("... judged at the merge base", "it was 3000 lines at the merge base" in out, True)
        check("... and says it may only shrink", "may only shrink" in out, True)

        # 7c. no merge base at all — the shape of a shallow CI checkout, and how
        #     this bug reached CI. There is no honest tree to judge against, so
        #     it is a 2 (which never passes) and never the tip of the base branch.
        git("checkout", "-q", "--orphan", "elsewhere")  # unrelated history, so
        _write(root / "backend/app/legacy.py", 3100)  # `merge-base <base> HEAD` fails
        git("add", "-A")
        git("commit", "-qm", "unrelated root")
        code, out = run(base_branch)
        check("no merge base exits 2, never a pass", code, 2)
        check("... and says it cannot judge", "cannot judge" in out, True)
        check("... and names the fix", "git fetch --unshallow" in out, True)
        check("... and judges nothing at all", "it was" not in out, True)

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}")
    if failures:
        return 1
    print(
        "PASS: check-file-sizes self-test (caps per tree, new vs oversized files, "
        "shrinking allowed, judged at the merge base, no merge base is a 2, "
        "a missing base is a 2)"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main", help="the ref to difference against")
    parser.add_argument("--root", default=str(REPO_ROOT), help="the tree to judge")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    code, lines = judge_tree(Path(args.root).resolve(), args.base)
    print("\n".join(lines), file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
