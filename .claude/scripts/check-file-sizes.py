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


def resolve_base(root: Path, base: str) -> tuple[str, str]:
    """The commit to compare against, and a phrase describing how it was found.

    The merge base is the honest answer — a file is judged against the size it
    had where this branch left main. CI's checkouts are shallow (`git fetch
    --depth=1`), where there is no common history to compute one from, so the
    fallback is main's tip: tree-only, like check-migration-fork.py, and it
    answers the question that actually matters, which is whether the merge
    result would be over the cap.
    """
    try:
        return git("merge-base", base, "HEAD", cwd=root).strip(), "merge base"
    except subprocess.CalledProcessError:
        pass
    try:
        sha = git("rev-parse", "--verify", f"{base}^{{commit}}", cwd=root).strip()
    except subprocess.CalledProcessError:
        raise LookupError(base) from None
    return sha, f"tip of {base} (no merge base: shallow checkout?)"


def judge_tree(root: Path, base: str) -> tuple[int, list[str]]:
    """(exit code, lines to print) for the tree at `root`."""
    try:
        against, how = resolve_base(root, base)
    except FileNotFoundError:
        return 2, ["cannot judge: git is not on PATH"]
    except LookupError:
        return 2, [
            f"cannot judge: {base} is not a ref this checkout has.",
            "run `git fetch origin main` (or pass --base <ref>), then try again.",
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
                f"it was {finding.base} lines at the {how}, and a file already over "
                "its cap may only shrink"
                if finding.base is not None and finding.base > finding.cap
                else (
                    f"it was {finding.base} lines at the {how}"
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
        lines.append("If origin/main is behind, `git fetch origin main` first.")
        lines.append("::error::file size cap exceeded")
        return 1, lines

    lines.append(
        f"PASS: {judged} changed file(s) judged against the {how} for {base}, "
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
        git = lambda *args: subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            git("init", "-q")
            _write(root / "backend/app/legacy.py", 3000)
            _write(root / "frontend/src/Legacy.vue", 2000)
            git("add", "-A")
            git("commit", "-qm", "base")
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            print(f"SELF-TEST FAIL: could not build the fixture repository: {exc}")
            return 1

        def run(base: str = "HEAD") -> tuple[int, str]:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--root", str(root), "--base", base],
                capture_output=True,
                text=True,
            )
            return result.returncode, result.stdout + result.stderr

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

        # 6. no merge base (the shape of a shallow CI checkout): the tip of the
        #    named ref is used instead, so the check still judges and still
        #    fires — it does not quietly pass just because history is missing.
        (root / "backend/app/small.py").unlink()
        tip = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        git("checkout", "-q", "--orphan", "elsewhere")  # unrelated history, so
        _write(root / "backend/app/legacy.py", 3100)  # `merge-base <tip> HEAD` fails
        git("add", "-A")
        git("commit", "-qm", "unrelated root")
        code, out = run(tip)
        check("no merge base falls back to the ref's tip", code, 1)
        check("... saying which it used", "no merge base" in out, True)
        check("... naming the ref", f"at the tip of {tip}" in out, True)
        _write(root / "backend/app/legacy.py", 2900)
        code, out = run(tip)
        check("... and judges against that tip's count", code, 0)

        # 7. an unjudgeable base is a 2, never a pass
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(root), "--base", "no-such-ref"],
            capture_output=True,
            text=True,
        )
        check("a missing base exits 2", result.returncode, 2)
        check("... and says so", "cannot judge" in result.stdout + result.stderr, True)

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}")
    if failures:
        return 1
    print(
        "PASS: check-file-sizes self-test (caps per tree, new vs oversized files, "
        "shrinking allowed, a shallow checkout still judges, a missing base is a 2)"
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
