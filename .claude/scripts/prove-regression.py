#!/usr/bin/env python3
"""prove-regression.py — a test written for a fix must be seen failing without it.

WHY THIS EXISTS: root AGENTS.md asks a regression test to guard a rule rather
than describe the code, and the PR template asks a bug fix for "修前失败、修后
通过的对照". A test that has never been red proves neither: it passes because it
asserts what the code already does, or because it never reaches the code the fix
touched. Today each author produces that pair by hand, in whatever way they think
of, usually not at all.

WHAT THIS IS NOT: a gate. Nothing runs it against the tree and no check reads its
exit code — CI runs only its `--self-test`, to keep the tool from rotting the way
an unused script does. It is a command you run before handing a fix off, so that
the claim in the PR body is something you watched rather than something you
believe. AGENTS.md states the rule; this only makes obeying it cheap.

HOW IT RUNS THE TWO HALVES. A detached git worktree at the revision you name is
the "without the fix" state; your working tree is the "with the fix" state. The
test file, plus anything you pass with --copy, is copied into the worktree so the
new test exists on both sides. backend/.venv and frontend/node_modules are
symlinked in, so the worktree runs the same interpreter and packages as your tree
instead of installing them again.

  python3 .claude/scripts/prove-regression.py backend/tests/unit/test_x.py::test_y \
      --without <revision without the fix>

The first run must fail and the second must pass. Exit 0 proven, 1 not proven,
2 could not judge. A test that needs Postgres needs the dev stack up first
(`eval "$(bash .claude/scripts/dev-db.sh env)"`); this script never starts it.

  python3 .claude/scripts/prove-regression.py --self-test
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: Directories symlinked into the "without the fix" worktree, so a run there uses
#: the dependencies the tree already installed. Relative to the repo root; a
#: missing one is skipped.
DEPENDENCY_DIRS = ("backend/.venv", "frontend/node_modules", "node_modules", ".venv")

#: Source-tree prefixes searched for a node id that is not already repo-relative.
SOURCE_ROOTS = ("backend", "frontend")

TEST_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".vue",
)

FIX_HINT = (
    "修法：把 --without 指到「不含这个修复」的提交（通常是你那条修复提交的父提交）。"
    "若测试要读没提交的文件，用 --copy 带进去；需要 Postgres 的测试先在同一个 shell 里"
    '跑 eval "$(bash .claude/scripts/dev-db.sh env)"。两侧都红多半是 node id 没写对'
    "（pytest 退出码 5 表示一条都没收集到）。"
)


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), text=True, capture_output=True, check=False
    )


def test_path_of(node: str) -> str:
    """The path part of a node id, rejecting anything not test-shaped.

    `backend/tests/unit/test_x.py::test_y` and `frontend/src/a.spec.ts` both give
    the part before `::`, or the whole id when there is none. Rejecting early is
    the point: this path is also what gets copied into the worktree.
    """
    path = node.split("::", 1)[0].strip()
    if not path.endswith(TEST_SUFFIXES):
        raise ValueError(
            f"{node!r} does not name a test file; pass the file and, if you have "
            "one, its ::test_name"
        )
    return path


def locate(node: str, root: Path) -> tuple[Path, str]:
    """Find the test file the node id names, as `(absolute, repo-relative)`."""
    raw = test_path_of(node)
    for relative in (raw, *(f"{prefix}/{raw}" for prefix in SOURCE_ROOTS)):
        candidate = root / relative
        if candidate.is_file():
            return candidate, relative
    raise SystemExit(
        f"prove-regression: no test file at {raw} (looked under {', '.join(SOURCE_ROOTS)})"
    )


def runner_for(node: str, relative: str) -> tuple[list[str], str]:
    """The command that runs `node`, and the subdirectory to run it from.

    The subdirectory is repository-relative (`"backend"`, `"frontend"`, or `""`),
    because the same command has to run in the worktree too. `uv run --no-sync`
    and `pnpm exec` both mean "use what is already installed", which is what the
    symlinked dependency directories are for. `relative` is the test file's path,
    so the node id is rebuilt from it plus the `::test_name` the caller wrote.
    """
    suffix = node[len(test_path_of(node)) :]
    if relative.endswith(".py"):
        if relative.startswith("backend/"):
            return (
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "pytest",
                    relative[len("backend/") :] + suffix,
                    "-q",
                ],
                "backend",
            )
        return ["uv", "run", "--no-sync", "pytest", relative + suffix, "-q"], ""
    if relative.startswith("frontend/"):
        return (
            ["pnpm", "exec", "vitest", "run", relative[len("frontend/") :] + suffix],
            "frontend",
        )
    return ["pnpm", "exec", "vitest", "run", relative + suffix], ""


def relink_dependencies(root: Path, worktree: Path) -> list[str]:
    """Point the worktree's dependency directories at the tree's own.

    A fresh worktree has none of them, and installing them again would take
    minutes and could disagree with the versions the tree actually runs.
    """
    linked: list[str] = []
    for relative in DEPENDENCY_DIRS:
        target = root / relative
        if not target.exists():
            continue
        here = worktree / relative
        if here.is_symlink() or here.is_file():
            here.unlink()
        elif here.is_dir():
            shutil.rmtree(here)
        here.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, here)
        linked.append(relative)
    return linked


def resolve_rev(root: Path, rev: str) -> str:
    done = git("rev-parse", "--verify", f"{rev}^{{commit}}", cwd=root)
    if done.returncode:
        print(f"prove-regression: --without {rev} is not a commit here", file=sys.stderr)
        raise SystemExit(2)
    return done.stdout.strip()


def without_fix(
    root: Path, rev: str, copies: list[str], keep: bool
) -> tuple[Path, str]:
    """Check `rev` out into a detached worktree that also carries the test file."""
    sha = resolve_rev(root, rev)
    tree = Path(tempfile.gettempdir()) / f"prove-regression-{sha[:12]}-{os.getpid()}"
    # A worktree whose directory was cleared away (a reboot emptied /tmp) still
    # holds a registration, and that alone makes the next add fail.
    git("worktree", "remove", "--force", str(tree), cwd=root)
    shutil.rmtree(tree, ignore_errors=True)
    added = git("worktree", "add", "-q", "--detach", str(tree), sha, cwd=root)
    if added.returncode:
        print(
            f"prove-regression: could not check out {sha}: {added.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    linked = relink_dependencies(root, tree)
    for relative in copies:
        source = root / relative
        if not source.is_file():
            print(f"prove-regression: --copy {relative} is not a file", file=sys.stderr)
            raise SystemExit(2)
        destination = tree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    note = sha[:12] + (f", reusing {', '.join(linked)}" if linked else "")
    if keep:
        note += f", left at {tree}"
    return tree, note


def run(argv: list[str], where: Path) -> int:
    print(f"  $ (cd {where}) {' '.join(argv)}", flush=True)
    try:
        done = subprocess.run(argv, cwd=str(where), check=False)
    except FileNotFoundError as exc:
        print(f"prove-regression: {exc.filename} is not on PATH", file=sys.stderr)
        raise SystemExit(2) from exc
    return done.returncode


def judge(before: int, after: int, node: str) -> int:
    """Turn the two exit codes into a verdict. This is the whole rule."""
    print()
    if before == 0:
        print(
            "NOT PROVEN: the test already passes without the fix. It does not "
            "guard the change, or it never reaches the code the fix touched."
        )
        return 1
    if after != 0:
        print(
            "NOT PROVEN: the test does not pass with the fix in the working tree, "
            "so the failure without it is not the fix's doing."
        )
        return 1
    print(
        f"PROVEN: {node} fails without the fix and passes with it "
        f"(without: exit {before}, with: exit {after})."
    )
    return 0


def check(root: Path, args: argparse.Namespace) -> int:
    _, relative = locate(args.node, root)
    copies = [*args.copy, relative]
    if args.command:
        # An explicit command runs at the repository root in both trees, so it
        # can reach whatever it needs without this script guessing a runner.
        argv: list[str] = ["sh", "-c", args.command]
        subdir = ""
    else:
        argv, subdir = runner_for(args.node, relative)
    trees: list[Path] = []
    try:
        tree, note = without_fix(root, args.without, copies, args.keep)
        trees.append(tree)
        print(f"without the fix ({args.without} → {note})")
        before = run(argv, tree / subdir if subdir else tree)
        print("with the fix (working tree)")
        after = run(argv, root / subdir if subdir else root)
        return judge(before, after, args.node)
    finally:
        if not args.keep:
            for tree in trees:
                git("worktree", "remove", "--force", str(tree), cwd=root)
                shutil.rmtree(tree, ignore_errors=True)


class _Tally:
    def __init__(self) -> None:
        self.total = 0
        self.bad = 0

    def case(self, name: str, ok: bool) -> None:
        self.total += 1
        self.bad += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def self_test() -> int:
    """Prove the pieces that decide the verdict, and the worktree round trip.

    The end-to-end cases pass `--command`, so a self-test run needs no test
    framework: what is under test here is the machinery that sets up the
    "without the fix" state and reads two exit codes.
    """
    tally = _Tally()
    print("prove-regression.py --self-test")

    tally.case(
        "a ::test_name node names its file",
        test_path_of("tests/unit/test_x.py::test_y") == "tests/unit/test_x.py",
    )
    tally.case(
        "a bare spec path is its own file",
        test_path_of("frontend/src/a.spec.ts") == "frontend/src/a.spec.ts",
    )
    for bad in ("tests", "backend/app/main.go", "src/thing.rs", "-q"):
        try:
            test_path_of(bad)
            ok = False
        except ValueError:
            ok = True
        tally.case(f"{bad!r} is rejected as not a test file", ok)

    for before, after, want, label in (
        (1, 0, 0, "red without, green with is PROVEN"),
        (0, 0, 1, "green without is NOT PROVEN"),
        (1, 1, 1, "red with is NOT PROVEN"),
    ):
        sink = _Muffle()
        with sink:
            got = judge(before, after, "node")
        tally.case(label, got == want)

    argv, subdir = runner_for(
        "backend/tests/unit/test_x.py::test_y", "backend/tests/unit/test_x.py"
    )
    tally.case(
        "a backend .py node runs under pytest from backend/",
        argv[:4] == ["uv", "run", "--no-sync", "pytest"]
        and argv[4] == "tests/unit/test_x.py::test_y"
        and subdir == "backend",
    )
    argv, subdir = runner_for("frontend/src/a.spec.ts", "frontend/src/a.spec.ts")
    tally.case(
        "a frontend .ts node runs under vitest from frontend/",
        argv[:4] == ["pnpm", "exec", "vitest", "run"]
        and argv[4] == "src/a.spec.ts"
        and subdir == "frontend",
    )

    with tempfile.TemporaryDirectory() as scratch:
        repo = Path(scratch) / "repo"
        repo.mkdir()
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }

        def g(*args: str) -> None:
            subprocess.run(
                ["git", *args], cwd=str(repo), env=env, check=True, capture_output=True
            )

        g("init", "-q")
        (repo / "app.py").write_text("value = 1\n")
        g("add", "app.py")
        g("commit", "-q", "-m", "before")
        without = (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        (repo / "app.py").write_text("value = 2\n")
        (repo / "test_app.py").write_text("from app import value\n")
        g("add", "app.py", "test_app.py")
        g("commit", "-q", "-m", "fix")

        def prove(rev: str, command: str) -> int:
            here = Path(__file__).resolve()
            return subprocess.run(
                [
                    sys.executable,
                    str(here),
                    "test_app.py",
                    "--without",
                    rev,
                    "--command",
                    command,
                ],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            ).returncode

        green = "python3 -c \"import app; raise SystemExit(0 if app.value == 2 else 1)\""
        tally.case(
            "end to end: red without the fix, green with it → PROVEN",
            prove(without, green) == 0,
        )
        tally.case(
            "end to end: the worktree is cleaned up",
            len(_worktrees(repo)) == 1,
        )
        tally.case(
            "end to end: green without the fix → NOT PROVEN",
            prove(without, "python3 -c 'import app; raise SystemExit(0)'") == 1,
        )
        tally.case(
            "end to end: red with the fix → NOT PROVEN",
            prove("HEAD", "python3 -c 'import app; raise SystemExit(1)'") == 1,
        )
        tally.case(
            "end to end: an unknown revision cannot be judged",
            prove("no-such-rev", green) == 2,
        )

    if tally.bad:
        print(f"self-test: {tally.bad} of {tally.total} cases FAILED")
        return 1
    print(f"self-test: all {tally.total} cases passed")
    return 0


class _Muffle:
    """Silence judge()'s verdict lines while the self-test reads its code."""

    def __enter__(self) -> None:
        self._stdout = sys.stdout
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115

    def __exit__(self, *exc: object) -> None:
        sys.stdout.close()
        sys.stdout = self._stdout


def _worktrees(repo: Path) -> list[str]:
    done = git("worktree", "list", "--porcelain", cwd=repo)
    return [
        line.split(" ", 1)[1]
        for line in done.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    parser = argparse.ArgumentParser(
        prog="prove-regression.py",
        description="Run a node id without a fix and with it; the first must fail.",
    )
    parser.add_argument("node", help="the test node id, e.g. tests/unit/x.py::test_y")
    parser.add_argument(
        "--without",
        required=True,
        metavar="REV",
        help="a revision that does not carry the fix",
    )
    parser.add_argument(
        "--copy",
        action="append",
        default=[],
        metavar="PATH",
        help="another file the test needs (conftest, a fixture); repeatable",
    )
    parser.add_argument(
        "--command",
        help="run this shell command for both halves instead of inferring a runner",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the without-the-fix worktree in place to inspect it",
    )
    args = parser.parse_args()
    root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or "."
    )
    if not (root / ".git").exists():
        print("prove-regression: run this from inside the repository", file=sys.stderr)
        return 2
    code = check(root, args)
    if code == 1:
        print(FIX_HINT)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
