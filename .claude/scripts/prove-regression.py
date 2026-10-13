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
new test exists on both sides; four dependency directories — backend/.venv,
frontend/node_modules, node_modules and .venv, at the repository root — are
symlinked in when they exist, so the worktree runs the same interpreter and
packages as your tree instead of installing them again. Both halves can write
where they run, so the "with the fix" half writes in your tree. What it writes
there is its runner's doing, not this script's: a `uv run` installs into a tree
that has no environment of its own, and so does `task`, which is why
`--command` gets no probe. Nothing else in your tree is touched, and the
worktree is removed afterwards unless you pass --keep.

  python3 .claude/scripts/prove-regression.py backend/tests/unit/test_x.py::test_y \
      --without <revision without the fix>

The first run must fail and the second must pass. Exit 0 proven, 1 not proven,
2 could not judge — the last for a node id that does not resolve, a revision that
is not a commit here, a layout this script has no runner for, a --copy file that
is missing, unreadable or would land outside the worktree, a runner that is not on PATH,
an inferred run that did not end in a verdict or that collected no test, a
command that exited 127 without ever running. If your test is not one of the two layouts below, say how to run it
with --command; the script will not guess a runner it does not know, because a
runner that is not installed looks exactly like a test that does not fail.

The two it does know get no guessing either: for them, only a pass and a failure
are results. Each runner is asked for its version before either half is read, and
the run itself is read only when it ends in 0 or 1. Anything else is refused,
whatever the number. That asymmetry is the point: the codes a runner uses when
the test never ran are its own to choose, and pytest spreads them over a range —
2 for a collection error the file alone names, 4 for a `::name` it found no
collector for (the code a name that is not there and a module this half cannot
import both give that form), 5 for a run that collected nothing at all — so a
table of them would always be one short.
Vitest chooses differently: 1 is a failing test *and* a spec it could not load,
so there the code is not a verdict on its own either, and the vitest half is
judged only when the run also wrote a report counting the tests it collected
(`--reporter=json`, read by `report_ran`). With
--command the script is told nothing, because the caller named that command and
the tree it runs in; its dependencies are theirs to keep installed, and a
`--command` that exits 2 is a failure like any other. A fix that adds a module the test
imports is a real regression guard, and it is refused on the inferred runner too,
because the test is copied into the worktree but that module is not there; on
pytest that is a collection error and on vitest it is a file that collected
nothing, and either way `--command` is how to have the code read as the failure
it looks like — which is the price of never calling a test that was not
collected "watched failing". A test that needs Postgres needs the dev stack up
first (`eval "$(bash .claude/scripts/dev-db.sh env)"`); this script never starts
it.

The runners it infers are the two this repository uses: pytest over `backend/`
(`uv run --no-sync`), vitest over `frontend/` (`pnpm exec`).

  python3 .claude/scripts/prove-regression.py --self-test
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: Directories symlinked into the "without the fix" worktree, so a run there uses
#: the dependencies the tree already installed. Relative to the repo root; a
#: missing one is skipped.
DEPENDENCY_DIRS = ("backend/.venv", "frontend/node_modules", "node_modules", ".venv")

#: Source-tree prefixes searched for a node id that is not already repo-relative.
SOURCE_ROOTS = ("backend", "frontend")

#: Every worktree this script makes is named this plus `<sha12>-<pid>` under the
#: temp dir, which is how a later run recognises one a killed run left behind.
WORKTREE_PREFIX = "prove-regression-"

TEST_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".vue",
)

#: A leftover worktree this old is reclaimed even when the pid in its name looks
#: alive. Pids are recycled: once a dead run's number is handed to any unrelated
#: process, liveness alone reads that leftover as "still going" and holds it in
#: `git worktree list` for as long as the number stays taken. No run of this tool
#: lasts a day, so age is the safe second signal — a concurrent run is current.
STALE_AFTER = 24 * 60 * 60

#: What a shell exits when the command it was handed does not exist. It is not a
#: test result: nothing ran. Reading it as a red test would turn "your runner is
#: not installed" into a verdict about the test — the mistake `runner_for` exists
#: to avoid, arriving by the other door.
CANNOT_RUN = 127

#: What an inferred runner exits when the test passed and when it failed.
#: Everything else is a run that did not reach a verdict, and is refused rather
#: than judged. For pytest these two are exactly the verdicts: its codes are a
#: range as wide as its failure modes — 2 "interrupted" (a collection error, but
#: only when the node id names the file alone), 3 an internal error, 4 "found no
#: collectors" for a `::name` (the code whether the name is absent or the module
#: it names cannot be imported in this half), 5 nothing collected — and `uv`
#: passes them through, so a table of them would always be one short.
#: Vitest chooses differently: 1 is a failing test *and* a spec it could not
#: load, a transform error, a run that found no test file at all, so there the
#: code alone is not a verdict and the half is judged only when it also wrote a
#: report counting the test it collected (`VITEST_REPORT_FLAGS`, `report_ran`).
#: Without that second door a fix that adds a module the spec imports — the
#: test is copied into the worktree, the module is not — reads as a watched
#: failure. This is the mistake CANNOT_RUN guards against, one door along: the
#: runner started, so the check for a program that never did misses it. It
#: closes the same way from both sides, asking the runner up front
#: (`cannot_start`) and reading what the run itself left after (`run`).
FAILURE_CODES = {0, 1}

#: How the vitest half is asked what it collected. `--reporter=json` alone would
#: replace the output the caller reads; both reporters keep it and write the
#: count to the file named by `--outputFile`, which is the only place the count
#: can be read from without parsing the console.
VITEST_REPORT_FLAGS = ("--reporter=default", "--reporter=json")

FIX_HINT = (
    "修法：把 --without 指到「不含这个修复」的提交（通常是你那条修复提交的父提交）。"
    "若测试要读没提交的文件，用 --copy 带进去；需要 Postgres 的测试先在同一个 shell 里"
    '跑 eval "$(bash .claude/scripts/dev-db.sh env)"。两侧都红多半是 node id 没写对'
    "（pytest 退出码 5 表示一条都没收集到）；两侧都跑不起来（命令不存在、布局不对）用"
    "--command 说清怎么跑。"
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
    """Find the test file the node id names, as `(absolute, repo-relative)`.

    Not being able to find it is "could not judge" (exit 2), the same as any
    other way of getting the comparison set up wrong — never "not proven", which
    would blame a test that was never run.
    """
    try:
        raw = test_path_of(node)
    except ValueError as exc:
        print(f"prove-regression: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    for relative in (raw, *(f"{prefix}/{raw}" for prefix in SOURCE_ROOTS)):
        candidate = root / relative
        if candidate.is_file():
            return candidate, relative
    print(
        f"prove-regression: no test file at {raw} "
        f"(looked under {', '.join(SOURCE_ROOTS)})",
        file=sys.stderr,
    )
    raise SystemExit(2)


def runner_for(node: str, relative: str) -> tuple[list[str], str, bool] | None:
    """The command that runs `node`, the subdirectory to run it from, and whether
    its exit code needs a report to become a verdict — or None.

    None means this script has no runner for that layout, and the caller should
    say "could not judge" rather than run something and read its exit code. The
    two layouts below are the ones this repository uses; there are more (the e2e
    suites, `node --test` over `backend/tests/extension`, the helper tests under
    `.github/scripts`), and a runner that is not installed for them fails both
    halves for a reason that has nothing to do with the fix — which would be
    reported as a verdict about the test. Naming the runner is the caller's job:
    `--command`.

    The subdirectory is repository-relative (`"backend"`, `"frontend"`), because
    the same command has to run in the worktree too. `uv run --no-sync` and
    `pnpm exec` both mean "use what is already installed", which is what the
    symlinked dependency directories are for. `relative` is the test file's path,
    so the node id is rebuilt from it plus the `::test_name` the caller wrote.
    The third element says the run has to leave a report behind before its exit
    code can be read as a verdict, and is true of the vitest layout alone; see
    FAILURE_CODES.
    """
    # The file `locate` found is the stripped path, so the `::name` has to come
    # off the same string: a node id padded with spaces would otherwise slice
    # the suffix at the wrong offset and name a file that does not exist.
    node = node.strip()
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
                False,
            )
        return None
    if relative.startswith("frontend/"):
        return (
            ["pnpm", "exec", "vitest", "run", relative[len("frontend/") :] + suffix],
            "frontend",
            True,
        )
    return None


def report_path(tag: str) -> Path:
    """A path for one half's report, before anything has written it.

    The name carries this run's pid, the way a worktree's does, so that a run
    killed before its `finally` could unlink leaves a report a later run can
    tell from a live one's — `reclaim_stale` collects them by the same rule.
    """
    fd, name = tempfile.mkstemp(
        prefix=f"{WORKTREE_PREFIX}{tag}-{os.getpid()}-", suffix=".json"
    )
    os.close(fd)
    path = Path(name)
    # Nothing wrote it yet, and a file left from an earlier run would be read as
    # this half's. The runner creates it; until then it is absent.
    path.unlink()
    return path


def report_ran(report: Path) -> bool:
    """Whether that half's report says a test was collected at all.

    The count is the runner's own, written whatever the run did, and 0 means
    the half reached no test: the spec could not be loaded (the module the fix
    adds is not in this tree), nothing matched what was named, or the run ended
    before it began. A half that collected nothing has no failure to show, so
    it cannot be the red half a fix is credited with.
    """
    try:
        collected = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(collected, dict):
        return False
    total = collected.get("numTotalTests")
    return isinstance(total, int) and total > 0


def runner_probe(argv: list[str]) -> list[str] | None:
    """A command that makes an inferred runner say whether it can run at all.

    Both layouts below run through "use what is already installed" (`uv run
    --no-sync`, `pnpm exec`), so a tree whose dependencies are missing gives a
    runner that starts and then exits without running the test — a code outside
    FAILURE_CODES, which is no result. Asking the same runner for its version
    first puts that answer where it can be told apart from a red half, and the
    question is asked greedily: anything but 0 is a runner that could not load
    the test, because that is the only answer that means it could.

    `--command` gets no probe: the caller named that command, and whether it can
    start is the caller's to know.
    """
    if argv[:3] == ["uv", "run", "--no-sync"]:
        return [*argv[:4], "--version"]
    if argv[:3] == ["pnpm", "exec", "vitest"]:
        return [*argv[:3], "--version"]
    return None


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


def remove_worktree(root: Path, tree: Path) -> None:
    git("worktree", "remove", "--force", str(tree), cwd=root)
    shutil.rmtree(tree, ignore_errors=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_like(field: str) -> bool:
    """Whether a name field is a process number this machine could have handed out.

    Not `str.isdigit`, which is True for characters `int` refuses ('²' among
    them), and not any all-digit run either: a field too large for `os.kill`
    raises OverflowError out of it. Either would come out of the reclaim rule,
    and a reclaim rule that raises ends the run on a traceback at exit 1 — the
    code for NOT PROVEN — when nothing has been judged. A name in the shared
    temp directory that merely looks like this tool's is not this tool's, so the
    answer to a field like that is to leave it alone.
    """
    return field.isascii() and field.isdigit() and int(field) < 2**31


def reclaim_stale(root: Path) -> None:
    """Clear worktrees and reports earlier runs left behind.

    A run killed outright — SIGKILL, an OOM kill, a reboot — cannot clean up
    after itself, and nothing else ever will: without this, `git worktree list`
    accumulates dead checkouts and the temp directory accumulates one report per
    interrupted frontend half. Each carries the pid that made it, so a later run
    can usually tell whether the process that owns it is still going — a
    concurrent run's worktree is left alone, and so is this one, whose pid is
    the caller's, and the same for its report. `prune` drops the registrations
    whose directory is already gone, which is the state a reboot out of /tmp
    leaves.

    The pid alone is not enough, because it is recycled: once a dead run's
    number belongs to some unrelated process, liveness reads the leftover as
    live and holds it. Nothing here lasts a day, so anything older than
    STALE_AFTER is taken as dead whatever its number says.

    This runs before the halves do, on every run, so it is total by
    construction: it reads the shared temp directory and a registration list
    that other people's processes write to, and anything it cannot make sense of
    is left where it is. A refusal here would be exit 1 — NOT PROVEN — from a
    run that judged nothing.

    What it removes it has to be sure of, and the name is not enough to be sure
    of: a registration is a directory whose name somebody else chose. Two are
    therefore held back whatever they are called — the repository's own
    checkout, and the tree this run is standing in.
    """
    git("worktree", "prune", cwd=root)
    now = time.time()
    # `git worktree list` reports every registration of the repository, the main
    # working tree first, and `remove_worktree` deletes what it is handed: `git
    # worktree remove --force` refuses a main tree and leaves the registration,
    # but the `rmtree` behind it takes the directory anyway. The first entry is
    # therefore the repository's own checkout, which no run ever abandoned, and
    # the tree this run is standing in is where a second run starts when the
    # author goes back to a `--keep`ed worktree — sweeping that one unlinks the
    # directory the process is running in, and the run then ends on a
    # FileNotFoundError at exit 1, the code for NOT PROVEN, having judged
    # nothing. Both are held back before the name is even looked at.
    standing_in = root.resolve()
    for position, here in enumerate(_worktrees(root)):
        path = Path(here)
        if position == 0 or standing_in.is_relative_to(path.resolve()):
            continue
        if not path.name.startswith(WORKTREE_PREFIX):
            continue
        pid = path.name.rsplit("-", 1)[-1]
        if _pid_like(pid) and _alive(int(pid)) and not _older_than(path, now):
            continue
        remove_worktree(root, path)
    for leftover in _stale_reports(now):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            # Someone else's file in a sticky /tmp, or a directory that
            # appeared where a report was. Not ours to remove, and not a
            # reason to fail.
            continue


def _stale_reports(now: float) -> list[Path]:
    """Report files a killed run left in the temp directory.

    A report is written by the runner, registered nowhere, and unlinked only by
    `check`'s `finally`, which a signal does not run — so without a sweep every
    interrupted frontend half adds one. Its name is `<tag>-<pid>-<random>.json`
    under the worktree prefix, so the rule that decides a worktree is the same
    one that decides a report: a concurrent run's is fresh and its owner is
    alive; everything else is litter.

    Anything the rule cannot read as one of this tool's reports is left where it
    is — a name that is not the shape, a field that is not a pid, a temp
    directory that has gone. This runs on every run, before either half, and a
    raise here would be exit 1, the code for NOT PROVEN, out of a run that
    judged nothing; the temp directory belongs to every process on the machine,
    so a name it cannot read is the ordinary case, not the exceptional one.
    """
    stale: list[Path] = []
    try:
        entries = list(Path(tempfile.gettempdir()).iterdir())
    except OSError:
        # The shared temp directory is gone from under us, or is not readable.
        # Nothing to collect, and the sweep is not the place to say so.
        return stale
    for entry in entries:
        name = entry.name
        if not name.startswith(WORKTREE_PREFIX) or not name.endswith(".json"):
            continue
        parts = name[len(WORKTREE_PREFIX) : -len(".json")].split("-")
        if len(parts) != 3 or not _pid_like(parts[1]):
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        pid = int(parts[1])
        # The rule the worktree loop uses, and the reason it is the same rule:
        # this run's own pid is alive, so its reports are kept; a killed run's
        # is not; a recycled one is settled by the age.
        if _alive(pid) and not _older_than(entry, now):
            continue
        stale.append(entry)
    return stale


def _older_than(path: Path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime > STALE_AFTER
    except OSError:
        # Gone already, which is the other reason to drop it.
        return True


def destination_for(relative: str, tree: Path) -> Path:
    """Where `relative` lands inside `tree`, refusing anything that leaves it.

    Each copied path keeps its position relative to the repository, so the
    worktree half sees the same layout. A path that climbs out with `..` — or
    that is absolute, since `tree / "/x"` is `/x` — would instead write outside
    the worktree, over your own tree or anywhere else the process may write.
    That is the comparison set up wrong, so it is "could not judge" (exit 2),
    never a verdict about the test.
    """
    destination = (tree / relative).resolve()
    if not destination.is_relative_to(tree.resolve()):
        print(
            f"prove-regression: {relative} would land outside the worktree",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return destination


def without_fix(root: Path, sha: str, tree: Path, copies: list[str], keep: bool) -> str:
    """Check `sha` out into `tree` and put the test file there too.

    `tree` is what the caller registered for cleanup before calling this, so a
    failure here — a --copy path that is not a file or that cannot be read, a
    directory it cannot write — cannot leave the worktree behind. It runs no
    test.
    """
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
        if not os.access(source, os.R_OK):
            # A regular file the caller cannot read passes `is_file`, a
            # stat-level test, and first fails when the copy opens it: a
            # PermissionError out of `shutil`, past every handler here, which
            # ends the program at exit 1 — the code for NOT PROVEN. A directory
            # and a dangling symlink are refused above; an unreadable file is
            # the third kind of source the comparison cannot be set up from, so
            # it is refused here too, before the copy, and as "could not judge".
            print(
                f"prove-regression: --copy {relative} is not readable",
                file=sys.stderr,
            )
            raise SystemExit(2)
        destination = destination_for(relative, tree)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        except OSError as exc:
            # What `os.access` cannot answer for — a race, an ACL it did not
            # read, a destination under a directory it cannot write — would
            # otherwise escape the same way, so it lands on the same refusal.
            print(
                f"prove-regression: --copy {relative} could not be copied ({exc})",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
    note = sha[:12] + (f", reusing {', '.join(linked)}" if linked else "")
    if keep:
        note += f", left at {tree} (the next run removes it)"
    return note


def cannot_start(probe: list[str], where: Path) -> None:
    """Stop when an inferred runner started and could not load its test.

    Only 0 is that answer. A runner that could not load its test has no reason
    to exit 0 from `--version`, and it chooses its own code for every other way
    of failing — 2 for `uv` with no environment, 254 for `pnpm` exec with no
    vitest — so asking which non-zero code means "cannot load" would be a table
    one short. Judging a runner that never ran would put a verdict on a test
    that was not executed — the mistake `runner_for` refuses to make for layouts
    it does not know, arriving for the ones it does.
    """
    try:
        done = subprocess.run(probe, cwd=str(where), capture_output=True, check=False)
    except FileNotFoundError as exc:
        print(f"prove-regression: {exc.filename} is not on PATH", file=sys.stderr)
        raise SystemExit(2) from exc
    if done.returncode:
        print(
            f"prove-regression: {' '.join(probe)} could not load the test "
            f"({probe[0]} exited {done.returncode}); the test did not run",
            file=sys.stderr,
        )
        raise SystemExit(2)


def run(
    argv: list[str],
    where: Path,
    probe: list[str] | None = None,
    report: Path | None = None,
) -> int:
    if probe is not None:
        cannot_start(probe, where)
    if report is not None:
        # The layout needs it: `report` non-None is how the caller says this
        # runner's exit code is not a verdict on its own (FAILURE_CODES).
        argv = [*argv, *VITEST_REPORT_FLAGS, f"--outputFile={report}"]
    print(f"  $ (cd {where}) {' '.join(argv)}", flush=True)
    try:
        done = subprocess.run(argv, cwd=str(where), check=False)
    except FileNotFoundError as exc:
        print(f"prove-regression: {exc.filename} is not on PATH", file=sys.stderr)
        raise SystemExit(2) from exc
    if done.returncode == CANNOT_RUN:
        # `sh -c 'not-a-command'` exits 127, and so does any shell that cannot
        # find what --command named. Nothing ran, so there is no test result to
        # read; a verdict here would be about the caller's runner, not the test.
        print(
            f"prove-regression: {argv[0]} exited {CANNOT_RUN} (not found); the test "
            "did not run",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if probe is not None and done.returncode not in FAILURE_CODES:
        # An inferred runner ended somewhere other than a pass or a failure, so
        # the test did not reach a verdict: pytest's 2 is a collection error when
        # the node id names the file alone, its 4 is a `::name` it found no
        # collector for — the code a name that is not there and a module this
        # half cannot import both give that form — its 5 is nothing collected at
        # all, 3 is an error inside pytest, and vitest's 254 is a binary it could
        # not load. Nothing was asserted, so a red half here would credit the fix
        # with a failure that was the run's, and a before half that only could
        # not be collected is exactly the "was this test ever red" this tool
        # exists to answer. Keyed on the probe rather than on the runner's name,
        # because --command does not name one: a caller who names the command
        # and keeps the tree it runs in owns both the code and the blame.
        print(
            f"prove-regression: {' '.join(argv)} exited {done.returncode} without "
            "the test reaching a verdict — the test was not collected, or the "
            "runner stopped. Pass --command to have a code like this read as the "
            "failure it looks like.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if report is not None and not report_ran(report):
        # The same question as above, on the runner whose code cannot answer it:
        # vitest exits 1 for a spec it could not load as well as for a test that
        # failed, so the code that got this far is not the verdict it looks like.
        # The report it wrote is the runner's own count of what it collected,
        # and a half that collected nothing failed nothing.
        print(
            f"prove-regression: {' '.join(argv)} collected no test ({report} "
            "counts none) — the file could not be loaded, nothing matched, or the "
            "run ended before it began. That is not a red half; pass --command to "
            "have it read the way you mean it.",
            file=sys.stderr,
        )
        raise SystemExit(2)
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
    reports: list[Path | None] = [None, None]
    if args.command:
        # An explicit command runs at the repository root in both trees, so it
        # can reach whatever it needs without this script guessing a runner.
        argv: list[str] = ["sh", "-c", args.command]
        subdir = ""
    else:
        inferred = runner_for(args.node, relative)
        if inferred is None:
            print(
                f"prove-regression: no runner for {relative} — this script knows "
                "pytest over backend/ and vitest over frontend/. Pass --command to "
                "say how to run it.",
                file=sys.stderr,
            )
            return 2
        argv, subdir, wants_report = inferred
        # One per half: a single path would be overwritten before it was read,
        # and each half's answer is about that half's tree.
        if wants_report:
            reports = [report_path("before"), report_path("after")]
    probe = runner_probe(argv)
    sha = resolve_rev(root, args.without)
    reclaim_stale(root)
    trees: list[Path] = []
    tree = Path(tempfile.gettempdir()) / f"{WORKTREE_PREFIX}{sha[:12]}-{os.getpid()}"
    # Registered before anything is created: every path out of this block, from
    # here on, has to remove it, and the `finally` can only remove what it knows.
    trees.append(tree)
    try:
        note = without_fix(root, sha, tree, copies, args.keep)
        print(f"without the fix ({args.without} → {note})")
        before = run(argv, tree / subdir if subdir else tree, probe, reports[0])
        print("with the fix (working tree)")
        after = run(argv, root / subdir if subdir else root, probe, reports[1])
        return judge(before, after, args.node)
    finally:
        for report in reports:
            if report is not None:
                report.unlink(missing_ok=True)
        if not args.keep:
            for path in trees:
                remove_worktree(root, path)


class _Tally:
    def __init__(self) -> None:
        self.total = 0
        self.bad = 0

    def case(self, name: str, ok: bool) -> None:
        self.total += 1
        self.bad += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def _self_test_exit(tally: _Tally) -> int:
    """The only place a bad count becomes a non-zero exit for `--self-test`.

    Built into the tail of `self_test` it would be reached only by the real run,
    which is green — so nothing held the conversion the repo-guards step reads.
    Pulled out, a case can hand it a tally that is bad on purpose.
    """
    if tally.bad:
        print(f"self-test: {tally.bad} of {tally.total} cases FAILED")
        return 1
    print(f"self-test: all {tally.total} cases passed")
    return 0


def self_test() -> int:
    """Prove the pieces that decide the verdict, and the worktree round trip.

    The end-to-end cases build a throwaway repository whose fix is a changed
    value; `--command` keeps them free of any test framework. A verdict alone
    cannot say whether the copied test arrived — a test that failed and a test
    that was never there are both red on the before half — so two cases keep the
    worktree and inspect what was copied into it, plus the marker the test writes
    where it runs. And because "could not judge" and "not proven" are one
    failure apart, the cases that must not be confused assert what was said too.
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
    # Listed literally rather than read from TEST_SUFFIXES: a case that iterated
    # the constant would stay green with the constant trimmed to one entry,
    # which is the mutation it is here to catch.
    for suffix in (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue"):
        try:
            named = test_path_of(f"src/a{suffix}")
        except ValueError:
            named = None
        tally.case(f"{suffix} names a test file", named == f"src/a{suffix}")

    for before, after, want, label in (
        (1, 0, 0, "red without, green with is PROVEN"),
        (0, 0, 1, "green without is NOT PROVEN"),
        (1, 1, 1, "red with is NOT PROVEN"),
    ):
        sink = _Muffle()
        with sink:
            got = judge(before, after, "node")
        tally.case(label, got == want)

    argv, subdir, wants_report = runner_for(
        "backend/tests/unit/test_x.py::test_y", "backend/tests/unit/test_x.py"
    )
    tally.case(
        "a backend .py node runs under pytest from backend/",
        argv is not None
        and argv[:4] == ["uv", "run", "--no-sync", "pytest"]
        and argv[4] == "tests/unit/test_x.py::test_y"
        and subdir == "backend"
        and not wants_report,
    )
    # The padded node is the same node: `locate` strips it to find the file, so
    # the `::name` has to come off the same stripped string or the runner is
    # handed a path with the caller's spaces spliced into the file name.
    padded, _, _ = runner_for(
        " backend/tests/unit/test_x.py::test_y ", "backend/tests/unit/test_x.py"
    )
    tally.case(
        "a node id with surrounding spaces names the same file",
        padded is not None and padded[4] == "tests/unit/test_x.py::test_y",
    )
    argv, subdir, wants_report = runner_for(
        "frontend/src/a.spec.ts", "frontend/src/a.spec.ts"
    )
    tally.case(
        "a frontend .ts node runs under vitest from frontend/, and needs a report",
        argv is not None
        and argv[:4] == ["pnpm", "exec", "vitest", "run"]
        and argv[4] == "src/a.spec.ts"
        and subdir == "frontend"
        and wants_report,
    )
    for relative in ("e2e/tests/a.spec.ts", ".github/scripts/test_x.py"):
        tally.case(
            f"{relative} has no inferred runner",
            runner_for(relative, relative) is None,
        )
    # The probe is what tells a runner that could not load its test from one
    # that ran and failed, so its shape matters: the layout's own runner, asked
    # for the version of the package it drives — and nothing for a --command,
    # whose caller owns whether it can start.
    tally.case(
        "the pytest layout's probe asks pytest for its version",
        runner_probe(["uv", "run", "--no-sync", "pytest", "tests/x.py::t", "-q"])
        == ["uv", "run", "--no-sync", "pytest", "--version"],
    )
    tally.case(
        "the vitest layout's probe asks vitest for its version",
        runner_probe(["pnpm", "exec", "vitest", "run", "src/a.spec.ts"])
        == ["pnpm", "exec", "vitest", "--version"],
    )
    tally.case(
        "a --command is given no probe",
        runner_probe(["sh", "-c", "pytest -q"]) is None,
    )

    # What vitest's report says about a half, which is the only thing that turns
    # its exit 1 into a verdict. A report that is missing, unreadable or shaped
    # like something else has to read as "no test", not as a failure: the cost of
    # being wrong that way is a refused run, and the other way round is a fix
    # credited with a failure that never happened.
    with tempfile.TemporaryDirectory() as scratch:
        holder = Path(scratch)
        shapes = {
            "one test collected, failed": '{"numTotalTests": 1, "numFailedTests": 1}',
            "no test collected": '{"numTotalTests": 0, "numFailedTestSuites": 1}',
            "not an object": "[1, 2]",
            "no count at all": '{"success": false}',
            # A count that is not a whole number is not a count of tests, and
            # reading it as one is how a report the runner wrote for another
            # reason gets counted as the test having run.
            "a count that is not a whole number": '{"numTotalTests": 1.0}',
            "not json": "Tests  no tests\n",
        }
        for label, body in shapes.items():
            probe = holder / f"{label.replace(' ', '-')}.json"
            probe.write_text(body, encoding="utf-8")
            tally.case(
                f"a vitest report with {label} reads as ran={label.startswith('one ')}",
                report_ran(probe) is label.startswith("one "),
            )
        tally.case(
            "a report that was never written has no test in it",
            not report_ran(holder / "absent.json"),
        )
        made = report_path("case")
        # The pid in the name is what lets a later run tell this run's report
        # from one a killed run left, and the sweep turns on it; nothing else
        # reads it, so a name carrying any live pid satisfies every other case.
        fields = made.name[len(WORKTREE_PREFIX) : -len(".json")].split("-")
        tally.case(
            "a report path is reserved but not created, and names this run's pid",
            not made.exists()
            and made.parent == Path(tempfile.gettempdir())
            and fields[0] == "case"
            and fields[1] == str(os.getpid()),
        )
        made.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory() as scratch:
        holder = Path(scratch)
        root = holder / "tree"
        (root / "backend" / ".venv").mkdir(parents=True)
        (root / "backend" / ".venv" / "marker").write_text("x", encoding="utf-8")

        bare = holder / "no-deps"
        bare.mkdir()
        tally.case(
            "an absent dependency directory is skipped, not invented",
            relink_dependencies(bare, bare) == [],
        )

        ours = holder / "ours"
        ours.mkdir()
        linked = relink_dependencies(root, ours)
        here = ours / "backend" / ".venv"
        tally.case(
            "a present dependency directory is linked into the worktree",
            linked == ["backend/.venv"]
            and here.is_symlink()
            and here.resolve() == (root / "backend" / ".venv").resolve()
            and (here / "marker").read_text(encoding="utf-8") == "x",
        )

        stale = holder / "stale"
        (stale / "backend" / ".venv").mkdir(parents=True)
        (stale / "backend" / ".venv" / "cached").write_text("x", encoding="utf-8")
        relink_dependencies(root, stale)
        tally.case(
            "a worktree's own dependency directory is replaced, not merged",
            (stale / "backend" / ".venv").is_symlink(),
        )

        # All four, not only the one this fixture happens to have: a tree that
        # installed `node_modules` or `.venv` at the repository root gets those
        # linked too, and a DEPENDENCY_DIRS trimmed to one entry would still
        # pass every case above.
        full = holder / "full"
        for relative in (
            "backend/.venv",
            "frontend/node_modules",
            "node_modules",
            ".venv",
        ):
            (full / relative).mkdir(parents=True)
        every = holder / "every"
        every.mkdir()
        linked_all = relink_dependencies(full, every)
        # The list it hands back is what the --keep note quotes, and linking is
        # what the halves run on; a case that reads only the list stays green
        # while three of the four directories are never linked at all.
        tally.case(
            "all four dependency directories are linked, not just backend/.venv",
            linked_all
            == ["backend/.venv", "frontend/node_modules", "node_modules", ".venv"]
            and all(
                (every / relative).is_symlink()
                and (every / relative).resolve() == (full / relative).resolve()
                for relative in linked_all
            ),
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
            # The throwaway repository must not inherit this machine's git
            # configuration. With `commit.gpgsign = true` and signing that
            # cannot finish without a terminal (no gpg, or a pinentry with no
            # tty), `git commit` exits 128, and `check=True` turns that into a
            # traceback partway through the suite instead of a tally — the
            # self-test failing for a reason that is not the tool's.
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }

        def g(*args: str) -> None:
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", *args],
                cwd=str(repo),
                env=env,
                check=True,
                capture_output=True,
            )

        g("init", "-q")
        (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
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
        # The fix: app.py stops lying, and both files the test needs are new —
        # so the test file itself has to travel to the worktree for the "before"
        # half to be the test at all.
        (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        (repo / "helper.py").write_text("answer = 2\n", encoding="utf-8")
        # It drops a marker where it runs, so "the test failed" and "the test was
        # never there" can be told apart: both are red on the before half.
        (repo / "test_app.py").write_text(
            "import pathlib\n"
            "import app, helper\n"
            "pathlib.Path('ran.marker').write_text('yes')\n"
            "raise SystemExit(0 if (app.value, helper.answer) == (2, 2) else 1)\n",
            encoding="utf-8",
        )
        (repo / "e2e" / "tests").mkdir(parents=True)
        (repo / "e2e" / "tests" / "a.spec.ts").write_text("// e2e\n", encoding="utf-8")
        (repo / "backend" / "tests").mkdir(parents=True)
        (repo / "backend" / "tests" / "test_app.py").write_text("pass\n", encoding="utf-8")
        (repo / "frontend" / "src").mkdir(parents=True)
        (repo / "frontend" / "src" / "a.spec.ts").write_text("// spec\n", encoding="utf-8")
        g("add", "app.py", "helper.py", "test_app.py", "e2e", "backend", "frontend")
        g("commit", "-q", "-m", "fix")

        def prove(
            rev: str,
            command: str | None,
            node: str = "test_app.py",
            copy: str | None = None,
            keep: bool = False,
            where: Path | None = None,
            path: str | None = None,
            env_extra: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess:
            argv = [
                sys.executable,
                str(Path(__file__).resolve()),
                node,
                "--without",
                rev,
            ]
            if command is not None:
                argv += ["--command", command]
            if copy is not None:
                argv += ["--copy", copy]
            if keep:
                argv.append("--keep")
            child_env = dict(env)
            if path:
                child_env["PATH"] = path
            if env_extra:
                child_env.update(env_extra)
            return subprocess.run(
                argv,
                cwd=str(where or repo),
                env=child_env,
                capture_output=True,
                text=True,
                check=False,
            )

        runs = "python3 test_app.py"
        tally.case(
            "end to end: red without the fix, green with it → PROVEN",
            prove(without, runs, copy="helper.py").returncode == 0,
        )

        # The inferred runner is what a caller gets by saying nothing, so it has
        # to work with no --command, from the subdirectory its layout names, on
        # the worktree half as well as the real tree. `uv` here is a shim that
        # appends its working directory to a file — what is being checked is
        # where the inference ran, not what pytest said. It keys on the test
        # file, which is the one thing present in both halves at the right
        # depth: it is copied into the worktree, and it is the node id itself.
        # Run from the repository root instead, the file is not there, the shim
        # says nothing, and the count below is short by one.
        shims = Path(scratch) / "shims"
        shims.mkdir()
        seen = Path(scratch) / "ran-in.txt"
        shim = shims / "uv"
        shim.write_text(
            "#!/bin/sh\n"
            # Answered the way a real `uv` answers it — without touching the
            # project — so the count below is the two halves and not the probe.
            'case "$*" in *--version*) exit 0;; esac\n'
            "if [ -f tests/test_app.py ]; then\n"
            f'  pwd >> "{seen}"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)
        inferred = prove(
            without,
            None,
            node="backend/tests/test_app.py",
            path=f"{shims}{os.pathsep}{env.get('PATH', '')}",
        )
        lines = seen.read_text(encoding="utf-8").splitlines() if seen.is_file() else []
        tally.case(
            "end to end: the inferred runner runs from the subdirectory it named",
            inferred.returncode == 1
            and len(lines) == 2
            and all(Path(line).name == "backend" for line in lines),
        )
        seen.unlink(missing_ok=True)

        # The case above cannot tell a test that failed from a test that was
        # never copied in — both are red on the before half. Keeping the
        # worktree is what makes the copy itself checkable.
        kept = prove(without, runs, copy="helper.py", keep=True)
        (repo / "ran.marker").unlink(missing_ok=True)
        worktrees = [p for p in _worktrees(repo) if Path(p).resolve() != repo.resolve()]
        left = Path(worktrees[0]) if worktrees else None

        def text_of(path: Path) -> str | None:
            return path.read_text(encoding="utf-8") if path.is_file() else None

        tally.case(
            "end to end: the test file and its --copy land in the worktree",
            left is not None
            and (left / "test_app.py").is_file()
            and text_of(left / "helper.py") == "answer = 2\n"
            and text_of(left / "app.py") == "value = 1\n",
        )
        tally.case(
            "end to end: the copied test is what ran on the without-the-fix half",
            kept.returncode == 0
            and left is not None
            and text_of(left / "ran.marker") == "yes",
        )
        # The note is what says the worktree was left on purpose: without it
        # --keep reads as an ordinary run that happened to leave a directory.
        tally.case(
            "end to end: --keep says where the worktree was left",
            "left at" in kept.stdout and "the next run removes it" in kept.stdout,
        )
        if left is not None:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(left)],
                cwd=str(repo),
                env=env,
                capture_output=True,
                check=False,
            )
        tally.case(
            "end to end: a node id naming no file is not judged",
            prove(without, runs, node="test_nope.py").returncode == 2,
        )
        tally.case(
            "end to end: a node id that is not test-shaped is not judged",
            prove(without, runs, node="README.md").returncode == 2,
        )
        # Checked before any later run gets the chance to reclaim it, so this is
        # the failing path itself leaving the worktree behind and not a cleanup
        # that happened to run first. The message is part of what is asserted:
        # the guard below refuses an unreadable source with the same exit code,
        # so without it this case passes with the not-a-file refusal deleted —
        # which is what happened when the readability check was added above it.
        refused = prove(without, runs, copy="nope.py")
        tally.case(
            "end to end: a --copy path that is not a file is not judged, and says which",
            refused.returncode == 2
            and "is not a file" in refused.stderr
            and _extra_worktrees(repo) == [],
        )
        # The third kind of --copy source that cannot set the comparison up, and
        # the one `is_file` passes: a regular file the caller cannot read, where
        # the copy is what fails and a PermissionError out of it ends the program
        # at exit 1, the code for NOT PROVEN. Refused before the copy instead.
        secret = repo / "secret.py"
        secret.write_text("x = 1\n", encoding="utf-8")
        secret.chmod(0)
        # A uid that can read a mode-000 file — root — has nothing to refuse
        # here, so the guard is only exercised where there is one to exercise.
        readable_anyway = os.access(secret, os.R_OK)
        try:
            unreadable = prove(without, runs, copy="secret.py")
        finally:
            secret.chmod(0o644)
        secret.unlink()
        tally.case(
            "end to end: an unreadable --copy source is not judged",
            readable_anyway
            or (
                unreadable.returncode == 2
                and "is not readable" in unreadable.stderr
                and "NOT PROVEN" not in unreadable.stdout
            ),
        )
        # The guards above refuse what can be seen coming. This is the copy
        # failing for a reason no test can answer — a race, an ACL, a filesystem
        # that will not take the write — which is why the copy is wrapped at all:
        # an OSError out of it is the uncaught error that ends the run at exit 1,
        # the code for NOT PROVEN. No source the fixture can build passes both
        # guards and then fails to copy, so the failure is forced instead.
        copy_tree = Path(scratch) / "copy-fails"
        remove_worktree(repo, copy_tree)
        shutil.rmtree(copy_tree, ignore_errors=True)
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", str(copy_tree), "HEAD"],
            cwd=str(repo),
            env=env,
            check=True,
            capture_output=True,
        )
        real_copy2 = shutil.copy2

        def refuse_copy(*_args: object, **_kwargs: object) -> None:
            raise OSError("forced")

        shutil.copy2 = refuse_copy
        try:
            with _Captured() as said:
                try:
                    without_fix(repo, without, copy_tree, ["test_app.py"], False)
                    code = 0
                except SystemExit as exc:
                    code = exc.code
                except OSError:
                    code = -1
        finally:
            shutil.copy2 = real_copy2
            remove_worktree(repo, copy_tree)
        tally.case(
            "a copy that fails for any other reason is refused, not raised",
            code == 2 and "could not be copied" in said.err.getvalue(),
        )
        # Refusing is only half the fix; the caller has to be told what to pass
        # instead, or the next thing they try is the same guess this avoids.
        told = prove(without, None, node="e2e/tests/a.spec.ts")
        tally.case(
            "end to end: a layout with no runner is not judged, and says why",
            told.returncode == 2 and "--command" in told.stderr,
        )
        # A runner that is not there and a test that fails are both a non-zero
        # exit, so "could not judge" has to survive both ways of not finding it:
        # the program subprocess could not start, and the shell that could not
        # find what --command named. Judging either would put a verdict on a test
        # that never ran.
        with _Captured() as said:
            try:
                run(["prove-regression-no-such-program-xyz"], repo)
                code = 0
            except SystemExit as exc:
                code = exc.code
            except OSError:
                code = -1
        # The message matters as much as the code: this refusal is the one the
        # caller sees instead of a traceback, and it has to name the program.
        tally.case(
            "a runner that is not on PATH is not judged, and is named",
            code == 2
            and "prove-regression-no-such-program-xyz is not on PATH"
            in said.err.getvalue(),
        )
        with _Muffle():
            try:
                run(["sh", "-c", "prove-regression-no-such-command-xyz"], repo)
                code = 0
            except SystemExit as exc:
                code = exc.code
            except OSError:
                code = -1
        tally.case("a --command whose program does not exist is not judged", code == 2)
        tally.case(
            "end to end: a --command that never ran is not judged",
            prove(without, "prove-regression-no-such-command-xyz").returncode == 2,
        )
        tally.case(
            "end to end: an absolute node id is not judged",
            prove(without, runs, node=str(repo / "test_app.py")).returncode == 2,
        )
        # Where each copy would land if the guard were not there: `..` from the
        # worktree is the temp directory the worktree itself sits in, not the
        # repository, and an absolute path stays itself. Watching the files at
        # those two spots is what tells "refused" apart from "written somewhere
        # harmless" — the exit code alone cannot, because a copy that lands
        # outside is only a misjudgement once something reads what it wrote.
        # Named after this run's own scratch directory. `..` from the worktree is
        # the temp directory the worktree sits in, which every other task on the
        # machine shares, so a fixed name would have two concurrent self-tests
        # writing and unlinking each other's file.
        escape_name = f"escape-{Path(scratch).name}.py"
        escape = Path(tempfile.gettempdir()) / escape_name
        escape.write_text("outside\n", encoding="utf-8")
        # What the copy would carry if it landed: it exists, or the refusal would
        # be the missing-file one, and it says something else, so a file written
        # to the watched spot is visible in what the assertion reads.
        (Path(scratch) / escape_name).write_text("copied\n", encoding="utf-8")
        outside_tree = Path(scratch) / "absolute.py"
        outside_tree.write_text("outside\n", encoding="utf-8")
        escaped = (
            prove(without, runs, copy=f"../{escape_name}").returncode == 2
            and prove(without, runs, copy=str(outside_tree)).returncode == 2
            and escape.read_text(encoding="utf-8") == "outside\n"
        )
        escape.unlink(missing_ok=True)
        tally.case(
            "end to end: copies that would land outside the worktree are not judged",
            escaped,
        )

        # A runner that is there but cannot load its test — the program exists,
        # the package it drives does not — exits with its own code: 2 under uv,
        # 254 under vitest. A test that ran and failed exits 1. Reading the first
        # as the second is the mistake the 127 guard was added to close, one door
        # along: nothing ran either way. A runner that cannot load its test
        # cannot answer for itself either, so this is the probe's door, and it is
        # a different program in each layout.
        broken = Path(scratch) / "broken-shims"
        broken.mkdir()
        for name, code in (("uv", 2), ("pnpm", 254)):
            (broken / name).write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
            (broken / name).chmod(0o755)
        stalled_path = f"{broken}{os.pathsep}{env.get('PATH', '')}"
        for node, runner in (
            ("backend/tests/test_app.py", "uv"),
            ("frontend/src/a.spec.ts", "pnpm"),
        ):
            stalled = prove(without, None, node=node, path=stalled_path)
            tally.case(
                f"end to end: a {runner} that cannot load its test is not judged",
                stalled.returncode == 2
                and "could not load the test" in stalled.stderr
                and "NOT PROVEN" not in stalled.stdout,
            )

        # The same mistake one door along again, and the door the probe cannot
        # stand in for: the runner starts — `--version` answers 0 — and the run
        # ends somewhere other than a pass or a failure. Every such code is
        # refused rather than the ones someone happened to see, because they are
        # a range: pytest exits 2 for a collection error named by the file alone,
        # 4 for a `::name` it found no collector for — the code a missing name
        # and a module this half cannot import both give that form — 3 for an
        # error inside pytest, 5 for nothing collected at all, and `uv` passes
        # each through — so a table of them is one short as soon as the test file
        # grows an import or the node id grows a name, which is the shape the
        # PR's own example took. A verdict here would be about the
        # run, not the test. The shim answers 0 for the probe, so the probe
        # cannot be what refuses it.
        for code in (2, 3, 4, 5):
            shim_dir = Path(scratch) / f"verdictless-{code}"
            shim_dir.mkdir()
            (shim_dir / "uv").write_text(
                f'#!/bin/sh\ncase "$*" in *--version*) exit 0;; esac\nexit {code}\n',
                encoding="utf-8",
            )
            (shim_dir / "uv").chmod(0o755)
            stalled_run = prove(
                without,
                None,
                node="backend/tests/test_app.py::test_add",
                path=f"{shim_dir}{os.pathsep}{env.get('PATH', '')}",
            )
            tally.case(
                f"end to end: a uv run exiting {code} is not judged",
                stalled_run.returncode == 2
                and "without the test reaching a verdict" in stalled_run.stderr
                and "--command" in stalled_run.stderr
                and "NOT PROVEN" not in stalled_run.stdout,
            )
        # The same door from the other runner, and the reason a check written
        # against uv's numbers is not enough: 254 is vitest's code for a binary
        # it could not load, and it is not 2.
        shuttle = Path(scratch) / "verdictless-pnpm"
        shuttle.mkdir()
        (shuttle / "pnpm").write_text(
            '#!/bin/sh\ncase "$*" in *--version*) exit 0;; esac\nexit 254\n',
            encoding="utf-8",
        )
        (shuttle / "pnpm").chmod(0o755)
        stalled_pnpm = prove(
            without,
            None,
            node="frontend/src/a.spec.ts",
            path=f"{shuttle}{os.pathsep}{env.get('PATH', '')}",
        )
        tally.case(
            "end to end: a pnpm run exiting 254 is not judged",
            stalled_pnpm.returncode == 2
            and "without the test reaching a verdict" in stalled_pnpm.stderr
            and "NOT PROVEN" not in stalled_pnpm.stdout,
        )
        # ... and it is the code that decides, not the shim: the same shape
        # failing with 1 is an ordinary red half and is judged like one.
        red = Path(scratch) / "red-shims"
        red.mkdir()
        (red / "uv").write_text(
            "#!/bin/sh\ncase \"$*\" in *--version*) exit 0;; esac\nexit 1\n",
            encoding="utf-8",
        )
        (red / "uv").chmod(0o755)
        ordinary = prove(
            without,
            None,
            node="backend/tests/test_app.py",
            path=f"{red}{os.pathsep}{env.get('PATH', '')}",
        )
        tally.case(
            "end to end: the same shim failing with 1 is still judged",
            ordinary.returncode == 1 and "NOT PROVEN" in ordinary.stdout,
        )
        # What vitest's exit code cannot say, and the reason that layout is read
        # twice over: 1 is both "these tests failed" and "this spec could not be
        # loaded", and the second is a test that never ran — which is exactly
        # what a fix that adds the module the spec imports looks like from the
        # without-the-fix half. So the half is judged only when the run also
        # wrote a report counting a test it collected. The report path is in the
        # command line, so a shim can write one; the first shim below never does.
        def vitest_shim(name: str, tail: str) -> str:
            body = (
                "#!/bin/sh\n"
                'case "$*" in *--version*) exit 0;; esac\n'
                # Real vitest writes the json report because the command asked
                # for a json reporter. A shim that wrote one regardless stayed
                # green with VITEST_REPORT_FLAGS emptied, which is the flag it is
                # here to hold: without one it writes nothing, as vitest would,
                # and the half is refused for having collected no test.
                'case "$*" in *--reporter=json*) : ;; *) exit 0;; esac\n'
                'for a in "$@"; do case "$a" in --outputFile=*) '
                'out="${a#--outputFile=}";; esac; done\n'
            ) + tail
            shim_dir = Path(scratch) / f"vitest-{name}"
            shim_dir.mkdir()
            (shim_dir / "pnpm").write_text(body, encoding="utf-8")
            (shim_dir / "pnpm").chmod(0o755)
            return f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"

        silent = vitest_shim("nothing-collected", "exit 1\n")
        never_ran = prove(without, None, node="frontend/src/a.spec.ts", path=silent)
        tally.case(
            "end to end: a vitest half that collected nothing is not judged",
            never_ran.returncode == 2
            and "collected no test" in never_ran.stderr
            and "NOT PROVEN" not in never_ran.stdout,
        )
        # ... and a half that did collect one is an ordinary failure, or the
        # check would refuse the case the layout exists for.
        counted = vitest_shim(
            "counted",
            'if grep -q "value = 2" ../app.py 2>/dev/null; then\n'
            '  printf \'{"numTotalTests": 1}\' > "$out"\n'
            "  exit 0\n"
            "fi\n"
            'printf \'{"numTotalTests": 1, "numFailedTests": 1}\' > "$out"\n'
            "exit 1\n",
        )
        collected = prove(without, None, node="frontend/src/a.spec.ts", path=counted)
        tally.case(
            "end to end: a vitest half that collected a failing test is judged",
            collected.returncode == 0 and "PROVEN" in collected.stdout,
        )
        # Each half answers for itself: here the report is written only where the
        # fix is absent, so the half in the caller's tree ends 0 having collected
        # nothing. Read by its exit code alone that is a green half, and green
        # with a red before half is PROVEN — for a test that never ran.
        one_sided = vitest_shim(
            "after-silent",
            'if grep -q "value = 2" ../app.py 2>/dev/null; then\n'
            "  exit 0\n"
            "fi\n"
            'printf \'{"numTotalTests": 1, "numFailedTests": 1}\' > "$out"\n'
            "exit 1\n",
        )
        half_silent = prove(without, None, node="frontend/src/a.spec.ts", path=one_sided)
        tally.case(
            "end to end: the half with the fix answers for itself too",
            half_silent.returncode == 2
            and "collected no test" in half_silent.stderr
            and "NOT PROVEN" not in half_silent.stdout,
        )
        # ... and the refusal is the probe's, not the exit code's on its own.
        # `--command` is a command the caller named and a tree they keep
        # installed, so its numbers mean what they say they mean, and a code that
        # is neither 0 nor 1 is still a failure to answer: this one fails with 2
        # without the fix — the fix's own file is what it looks for — and passes
        # with it, which is PROVEN and not "could not judge". Reading the code
        # the same way as an inferred runner's would refuse the one case the
        # escape hatch exists for.
        from_command = prove(
            without,
            'if grep -q "value = 2" app.py; then exit 0; else exit 2; fi',
        )
        tally.case(
            "end to end: a --command that exits 2 is judged, not refused",
            from_command.returncode == 0 and "PROVEN" in from_command.stdout,
        )
        # The probe runs once per half, in that half's own directory, so a runner
        # that answers in one tree and not the other is refused on the side that
        # cannot. This shim reads the tree it was pointed at and answers the
        # `--version` probe only there — the worktree is the commit without the
        # fix, whose app.py still says 1 — so the before half runs and goes red
        # while the after half's own probe refuses. Dropping the probe from that
        # second call would let the run happen and `run` would refuse it with its
        # own message, so asserting *this* message is what pins the probe rather
        # than the check that follows it.
        split = Path(scratch) / "split-shims"
        split.mkdir()
        (split / "uv").write_text(
            "#!/bin/sh\n"
            'case "$*" in\n'
            "  *--version*)\n"
            '    if grep -q "value = 2" ../app.py 2>/dev/null; then exit 2; fi\n'
            "    exit 0\n"
            "    ;;\n"
            "esac\n"
            "exit 1\n",
            encoding="utf-8",
        )
        (split / "uv").chmod(0o755)
        halved = prove(
            without,
            None,
            node="backend/tests/test_app.py",
            path=f"{split}{os.pathsep}{env.get('PATH', '')}",
        )
        tally.case(
            "end to end: the half with the fix asks its own runner too",
            halved.returncode == 2
            and "could not load the test" in halved.stderr
            and "NOT PROVEN" not in halved.stdout,
        )
        # The probe has its own FileNotFoundError branch, and nothing above
        # reaches it: those shims exist, so the program subprocess hands them
        # runs. Without it a runner that is not on PATH raises instead of saying
        # so, and the caller gets a traceback where "could not judge" belongs.
        with _Captured() as said:
            try:
                cannot_start(["prove-regression-no-such-program-xyz", "--version"], repo)
                code = 0
            except SystemExit as exc:
                code = exc.code
            except OSError:
                code = -1
        tally.case(
            "a probe whose runner is not on PATH is refused too, and is named",
            code == 2
            and "prove-regression-no-such-program-xyz is not on PATH"
            in said.err.getvalue(),
        )

        # The bookkeeping that decides whether a worktree is registered has to
        # compare real paths: `git worktree add` resolves a symlink before it
        # registers, so a worktree made under a linked path comes back from
        # `worktree list` as its real path while `tempfile.gettempdir()` hands
        # back the link. Raw strings then disagree, and the case fails on where
        # TMPDIR points rather than on the code.
        (Path(scratch) / "real").mkdir()
        linked = Path(scratch) / "link"
        os.symlink(Path(scratch) / "real", linked)
        through_link = linked / f"{WORKTREE_PREFIX}0deada0deada-7"
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", str(through_link), "HEAD"],
            cwd=str(repo),
            env=env,
            check=True,
            capture_output=True,
        )
        tally.case(
            "a worktree reached through a symlink counts as registered",
            str(through_link) not in _worktrees(repo)
            and _registered(repo, through_link),
        )
        remove_worktree(repo, through_link)
        # Both NOT PROVEN paths exit 1; which one it is *is* the diagnosis, and
        # they are one assertion apart. Reading only the code, a duplicated or
        # swapped message would keep the suite green while the tool told an
        # author the opposite of what happened to their test — so these two
        # cases assert which half was red, not just that something was.
        green_without = prove(without, "python3 -c 'raise SystemExit(0)'")
        tally.case(
            "end to end: green without the fix → NOT PROVEN, and says which half",
            green_without.returncode == 1
            and "already passes without the fix" in green_without.stdout,
        )
        red_with = prove("HEAD", "python3 -c 'raise SystemExit(1)'")
        tally.case(
            "end to end: red with the fix → NOT PROVEN, and says which half",
            red_with.returncode == 1
            and "does not pass with the fix" in red_with.stdout,
        )
        # What a NOT PROVEN exit tells the caller to do next. main() prints the
        # hint on that code and on no other, so this reads it where the caller
        # would, instead of trusting that the print is still there.
        tally.case(
            "end to end: a NOT PROVEN run prints how to fix the comparison",
            "修法" in green_without.stdout and FIX_HINT in red_with.stdout,
        )
        tally.case(
            "end to end: an unknown revision cannot be judged",
            prove("no-such-rev", runs).returncode == 2,
        )
        tally.case(
            "end to end: every path out leaves no worktree behind",
            _extra_worktrees(repo) == [],
        )

        with _Muffle():
            try:
                resolve_rev(repo, "no-such-rev")
                code = 0
            except SystemExit as exc:
                code = exc.code
        tally.case("an unknown revision is refused before any worktree is made", code == 2)

        # A revision that resolves but is not a commit — a tree, or a tag that
        # points at one. `^{commit}` is what refuses it, before `worktree add` is
        # handed a sha it could never check out.
        tree_sha = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        with _Muffle():
            try:
                resolve_rev(repo, tree_sha)
                code = 0
            except SystemExit as exc:
                code = exc.code
        tally.case("a revision that is not a commit is refused", code == 2)

        # A run killed mid-flight cannot clean up after itself; the next run has
        # to, or `git worktree list` only grows. A concurrent run's worktree has
        # a live pid in its name and is not litter.
        #
        # `plant` makes a worktree, proves it is really registered — so a case
        # cannot pass off a git that never made one — and ages it by hand rather
        # than by sleeping.
        def plant(pid: int, age: float = 0.0) -> Path:
            path = Path(tempfile.gettempdir()) / f"{WORKTREE_PREFIX}0deada0deada-{pid}"
            # Whatever a previous run of this case left, so the case cannot pass
            # or fail on /tmp's history rather than on reclaim_stale.
            git("worktree", "remove", "--force", str(path), cwd=repo)
            shutil.rmtree(path, ignore_errors=True)
            subprocess.run(
                ["git", "worktree", "add", "-q", "--detach", str(path), "HEAD"],
                cwd=str(repo),
                env=env,
                check=True,
                capture_output=True,
            )
            assert _registered(repo, path), f"{path} is not a worktree"
            if age:
                when = time.time() - age
                os.utime(path, (when, when))
            return path

        def gone(path: Path) -> bool:
            return not path.exists() and not _registered(repo, path)

        def unused_pid() -> int:
            """A pid that is dead whenever it is read, and cannot become live.

            One past the largest number this kernel hands out, because a number
            found from below is one a later subprocess can be given: between a
            plant and the sweep that judges it this case forks dozens of them,
            which is what the probe that used to stand here assumed away. Above
            `pid_max` the kernel never allocates the number, so `_alive` reads
            it as gone whenever the sweep asks.
            """
            try:
                top = int(
                    Path("/proc/sys/kernel/pid_max").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                top = 999_999
            return min(top + 1, 2**31 - 1)

        dead = plant(unused_pid())
        live = plant(os.getpid())
        # A pid that is alive and belongs to something else entirely — what a
        # dead run's number looks like after the kernel hands it out again. Only
        # the name's pid matters to reclaim_stale, so this is indistinguishable
        # from a surviving owner; only the age gives it away.
        other = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        )
        try:
            recycled = plant(other.pid, age=STALE_AFTER + 60)
            with _Muffle():
                reclaim_stale(repo)
            tally.case(
                "a worktree whose run is gone is reclaimed; a live one is left alone",
                gone(dead) and live.exists(),
            )
            # The pid is recycled, so liveness alone would hold this one forever.
            tally.case(
                "a worktree whose pid was recycled is reclaimed, not held forever",
                gone(recycled),
            )
            remove_worktree(repo, live)
        finally:
            other.kill()
            other.wait()

        # The loop reads its pid out of the name too, and the same field can be
        # one `int` or `os.kill` will not take — `'²'.isdigit()` is true, and an
        # all-digit field can be past what `os.kill` accepts. A registration like
        # that is this tool's own litter whatever its pid says, so it is removed,
        # and either way the sweep must not raise: a raise here is exit 1, NOT
        # PROVEN, out of a run that judged nothing.
        odd = []
        for suffix in ("²", "99999999999"):
            path = Path(tempfile.gettempdir()) / f"{WORKTREE_PREFIX}odd-{suffix}"
            git("worktree", "remove", "--force", str(path), cwd=repo)
            shutil.rmtree(path, ignore_errors=True)
            subprocess.run(
                ["git", "worktree", "add", "-q", "--detach", str(path), "HEAD"],
                cwd=str(repo),
                env=env,
                check=True,
                capture_output=True,
            )
            odd.append(path)
        try:
            with _Muffle():
                reclaim_stale(repo)
            swept = True
        except Exception:
            swept = False
        # Read before the test-side cleanup below, which would otherwise make
        # this true whatever the sweep did — and the title says reclaimed, so a
        # sweep that merely declined to raise on these names has not done it.
        gone = [not _registered(repo, path) for path in odd]
        for path in odd:
            remove_worktree(repo, path)
        tally.case(
            "a worktree whose name is not a pid is reclaimed, not raised on",
            swept and all(gone),
        )

        # A registration is a directory whose name somebody else chose, and the
        # loop has nothing but that name to go on. `git worktree list` reports
        # every registration of the repository — the main working tree first —
        # and `remove_worktree` deletes what it is handed: `git worktree remove
        # --force` refuses a main tree and leaves the registration behind, but
        # the `rmtree` after it takes the directory anyway. So a repository that
        # lives in a directory named with the prefix loses its own checkout to a
        # run started from one of its worktrees. A repository of its own, since
        # deleting the fixture the rest of the suite stands on would fail every
        # case after this one instead of this one.
        danger = Path(scratch) / f"{WORKTREE_PREFIX}danger"
        beside = Path(scratch) / "beside"
        danger.mkdir()
        (danger / "app.py").write_text("value = 1\n", encoding="utf-8")
        for args in (
            ("init", "-q"),
            ("add", "app.py"),
            ("commit", "-q", "-m", "one"),
            ("worktree", "add", "-q", "--detach", str(beside), "HEAD"),
        ):
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", *args],
                cwd=str(danger),
                env=env,
                check=True,
                capture_output=True,
            )
        try:
            with _Muffle():
                reclaim_stale(beside)
            swept = True
        except Exception:
            swept = False
        tally.case(
            "the repository's own checkout is not reclaimed, whatever it is called",
            swept and danger.exists() and _registered(beside, danger),
        )

        # The tree the run is standing in is the other one no name can make
        # litter, nor is any directory above it — a worktree can be registered
        # inside another one. `--keep` prints where the worktree was left, and
        # going back there to start the next run is the ordinary way to use it:
        # sweeping that tree unlinks the directory the process is running in,
        # and the run then ends on a FileNotFoundError at exit 1, the code for
        # NOT PROVEN, having judged nothing.
        outer = plant(unused_pid())
        inner = outer / "inner"
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", str(inner), "HEAD"],
            cwd=str(repo),
            env=env,
            check=True,
            capture_output=True,
        )
        try:
            with _Muffle():
                reclaim_stale(inner)
            swept = True
        except Exception:
            swept = False
        tally.case(
            "the tree this run stands in is not reclaimed, nor the one above it",
            swept and inner.exists() and outer.exists(),
        )
        remove_worktree(repo, inner)
        remove_worktree(repo, outer)

        # The number the reclaim rule turns on. An hour would take a worktree
        # out from under a run that is still using it, so the boundary is where
        # the value shows; only the age can settle a recycled pid, which is what
        # the case above rests on.
        aged = Path(scratch) / "aged"
        aged.mkdir()
        moment = time.time()
        for age, stale in ((23 * 60 * 60, False), (25 * 60 * 60, True)):
            when = moment - age
            os.utime(aged, (when, when))
            tally.case(
                f"a leftover {age // 3600}h old is stale: {stale}",
                _older_than(aged, moment) is stale,
            )

        # `prune` is what clears a registration whose directory is gone, and it
        # is the only thing that does: the loop above reaches only names that
        # carry this tool's prefix, so a registration under any other name — the
        # state a reboot out of /tmp leaves another tool's worktree in — is
        # dropped by nothing else.
        stray = Path(tempfile.gettempdir()) / f"some-other-tool-{os.getpid()}"
        git("worktree", "remove", "--force", str(stray), cwd=repo)
        shutil.rmtree(stray, ignore_errors=True)
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", str(stray), "HEAD"],
            cwd=str(repo),
            env=env,
            check=True,
            capture_output=True,
        )
        shutil.rmtree(stray)
        with _Muffle():
            reclaim_stale(repo)
        tally.case(
            "prune drops a registration whose directory is already gone",
            not _registered(repo, stray),
        )

        # A report is not a worktree, so `git worktree list` never shows it and
        # the sweep over the temp directory is the only thing that collects a
        # killed run's half. Its name carries the pid for exactly this: the
        # report of a live run is not litter.
        leaked = (
            Path(tempfile.gettempdir())
            / f"{WORKTREE_PREFIX}before-{unused_pid()}-leak.json"
        )
        leaked.write_text('{"numTotalTests": 1}', encoding="utf-8")
        mine = report_path("after")
        mine.write_text('{"numTotalTests": 1}', encoding="utf-8")
        with _Muffle():
            reclaim_stale(repo)
        tally.case(
            "a report whose run is gone is reclaimed; a live run's is left alone",
            not leaked.exists() and mine.exists(),
        )
        mine.unlink(missing_ok=True)

        # The sweep reads the shared temp directory, which is every process's on
        # the machine, so a name that only looks like this tool's has to be left
        # where it is rather than read as a pid: `'²'.isdigit()` is true and
        # `int` refuses it, and an all-digit field can be past what `os.kill`
        # takes. Either one raises out of the sweep, and a raise here is exit 1
        # — the code for NOT PROVEN — out of a run that judged nothing.
        odd = []
        for name in (
            f"{WORKTREE_PREFIX}before-99999999999-aaaa.json",
            f"{WORKTREE_PREFIX}before-²-x.json",
        ):
            path = Path(tempfile.gettempdir()) / name
            path.write_text('{"numTotalTests": 1}', encoding="utf-8")
            odd.append(path)
        try:
            with _Muffle():
                reclaim_stale(repo)
            swept = True
        except Exception:
            swept = False
        finally:
            left = [path.exists() for path in odd]
            for path in odd:
                path.unlink(missing_ok=True)
        tally.case(
            "a name that is not this tool's report does not stop the sweep",
            swept and all(left),
        )

        # The prefix is what keeps the sweep off what belongs to nobody here.
        # The temp directory is every process's on the machine, and the guard is
        # not a formality: with it gone the slice that strips the prefix takes
        # its seventeen characters off whatever name it is handed instead, so a
        # neighbour's name that stands junk where the prefix should be — this
        # one carries nineteen characters of it — still ends up with the three
        # fields the rule reads past the cut, and the sweep reads it as a
        # report of its own and unlinks it. Aged, with a pid that is gone, so
        # neither the age nor the owner is what keeps it.
        neighbour = Path(tempfile.gettempdir()) / (
            "0123456789abcdefghi" + f"before-{unused_pid()}-aaaa" + ".json"
        )
        neighbour.write_text('{"numTotalTests": 1}', encoding="utf-8")
        when = time.time() - STALE_AFTER - 60
        os.utime(neighbour, (when, when))
        try:
            with _Muffle():
                reclaim_stale(repo)
            swept = True
        except Exception:
            swept = False
        finally:
            kept = neighbour.exists()
            neighbour.unlink(missing_ok=True)
        tally.case(
            "a name outside this tool's prefix is left where it is",
            swept and kept,
        )

        # The two guards that decide the name's shape at all: the report suffix
        # and the three fields. Each is load-bearing and neither implies the
        # other — without the suffix guard a `.txt` neighbour still slices into
        # three fields, and without the field-count guard a fourth field still
        # leaves the pid in the middle where the rule reads it. So each gets a
        # name only it rejects, aged and carrying this run's pid, which is what
        # puts it one glint away from being collected.
        shaped = []
        for name in (
            f"{WORKTREE_PREFIX}before-{os.getpid()}-aaaa.txt",
            f"{WORKTREE_PREFIX}before-{os.getpid()}-aa-bb.json",
        ):
            path = Path(tempfile.gettempdir()) / name
            path.write_text('{"numTotalTests": 1}', encoding="utf-8")
            when = time.time() - STALE_AFTER - 60
            os.utime(path, (when, when))
            shaped.append(path)
        try:
            with _Muffle():
                reclaim_stale(repo)
            swept = True
        except Exception:
            swept = False
        finally:
            left = [path.exists() for path in shaped]
            for path in shaped:
                path.unlink(missing_ok=True)
        tally.case(
            "a name of the wrong shape is left where it is",
            swept and all(left),
        )

        # Looking at an entry can fail on its own — the file removed between the
        # listing and the look, a mount that stops answering. The sweep is not
        # the place to say so, and an entry it could not read is not one it has
        # read as litter. The entry has to be one the rule would otherwise
        # collect, or "left where it is" says nothing about the look: this run's
        # own fresh report is left alone whatever the guard does. Through a temp
        # directory of this case's own, because an entry the rule wants removed
        # in the shared one is what a concurrently running copy of this suite
        # wants removed too.
        was_temp = tempfile.tempdir
        tempfile.tempdir = str(Path(scratch) / "unreadable-temp")
        Path(tempfile.tempdir).mkdir()
        try:
            unreadable = (
                Path(tempfile.tempdir)
                / f"{WORKTREE_PREFIX}before-{unused_pid()}-aaaa.json"
            )
            unreadable.write_text('{"numTotalTests": 1}', encoding="utf-8")
            when = time.time() - STALE_AFTER - 60
            os.utime(unreadable, (when, when))
            real_is_file = Path.is_file

            def refuse_is_file(self: Path, *args: object, **kwargs: object) -> bool:
                if self.name == unreadable.name:
                    raise OSError("forced")
                return real_is_file(self, *args, **kwargs)

            Path.is_file = refuse_is_file  # type: ignore[method-assign]
            try:
                with _Muffle():
                    reclaim_stale(repo)
                swept = True
            except Exception:
                swept = False
            finally:
                Path.is_file = real_is_file  # type: ignore[method-assign]
                kept = unreadable.exists()
                unreadable.unlink(missing_ok=True)
        finally:
            tempfile.tempdir = was_temp
        tally.case(
            "an entry the sweep cannot look at is left where it is, not raised on",
            swept and kept,
        )

        # And the removal itself can be refused — a sticky /tmp where the file
        # belongs to someone else, a directory that appeared where a report was.
        # Left where it is, and the sweep goes on: a raise here is exit 1, NOT
        # PROVEN, out of a run that judged nothing. Through a temp directory of
        # this case's own: the report has to be one the sweep wants removed, so
        # it is aged, and an aged report in the shared temp directory is one a
        # concurrently running copy of this suite wants removed too — the case
        # would then fail on the competition rather than on the guard.
        was_temp = tempfile.tempdir
        tempfile.tempdir = str(Path(scratch) / "unremovable-temp")
        Path(tempfile.tempdir).mkdir()
        try:
            stuck = report_path("after")
            stuck.write_text('{"numTotalTests": 1}', encoding="utf-8")
            when = time.time() - STALE_AFTER - 60
            os.utime(stuck, (when, when))
            real_unlink = Path.unlink

            def refuse_unlink(self: Path, *args: object, **kwargs: object) -> None:
                if self.name == stuck.name:
                    raise OSError("forced")
                return real_unlink(self, *args, **kwargs)

            Path.unlink = refuse_unlink  # type: ignore[method-assign]
            try:
                with _Muffle():
                    reclaim_stale(repo)
                swept = True
            except Exception:
                swept = False
            finally:
                Path.unlink = real_unlink  # type: ignore[method-assign]
                kept = stuck.exists()
                stuck.unlink(missing_ok=True)
        finally:
            tempfile.tempdir = was_temp
        tally.case(
            "a report that cannot be removed is left where it is, not raised on",
            swept and kept,
        )

        # And the temp directory itself can be swept out from under a run — a
        # container's /tmp, a cleaner, a machine that is being torn down. There
        # is nothing to collect then, which is not a reason to fail.
        was = tempfile.tempdir
        tempfile.tempdir = str(Path(scratch) / "no-such-temp-directory")
        try:
            with _Muffle():
                reclaim_stale(repo)
            swept = True
        except Exception:
            swept = False
        finally:
            tempfile.tempdir = was
        tally.case("a temp directory that is gone does not stop the sweep", swept)

        # The sweep reclaims a name shape, and `report_path` is the only writer
        # of that shape: a case that writes the leaked name by hand leaves the
        # two free to drift apart, so the sweep would go on collecting nothing
        # with every case still green. Aged past the limit, because the pid in
        # the name is this run's and only the age takes it.
        # In a temp directory of its own, so that what removed it can only be
        # this call: in the shared one a concurrent copy of this suite would
        # also remove an aged report whose name is its own writer's, and the
        # case would pass without this sweep having done anything.
        was_temp = tempfile.tempdir
        tempfile.tempdir = str(Path(scratch) / "aged-temp")
        Path(tempfile.tempdir).mkdir()
        try:
            aged_report = report_path("after")
            aged_report.write_text('{"numTotalTests": 1}', encoding="utf-8")
            when = time.time() - STALE_AFTER - 60
            os.utime(aged_report, (when, when))
            with _Muffle():
                reclaim_stale(repo)
            reclaimed = not aged_report.exists()
        finally:
            tempfile.tempdir = was_temp
        tally.case(
            "a report report_path wrote is reclaimed once it ages out",
            reclaimed,
        )

        # A registration whose directory was cleared away — the same reboot out
        # of /tmp — makes the next `worktree add` fail with "missing but already
        # registered", so the add is preceded by a remove. The checkout has to
        # survive a state the fixture above never reaches.
        stranded = (
            Path(tempfile.gettempdir()) / f"{WORKTREE_PREFIX}stranded-{unused_pid()}"
        )
        git("worktree", "remove", "--force", str(stranded), cwd=repo)
        shutil.rmtree(stranded, ignore_errors=True)
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", str(stranded), "HEAD"],
            cwd=str(repo),
            env=env,
            check=True,
            capture_output=True,
        )
        shutil.rmtree(stranded)
        with _Muffle():
            try:
                without_fix(repo, without, stranded, ["test_app.py"], False)
                ok = (
                    text_of(stranded / "app.py") == "value = 1\n"
                    and text_of(stranded / "test_app.py") is not None
                )
            except SystemExit:
                ok = False
        tally.case(
            "a stranded registration does not stop the without-the-fix checkout",
            ok,
        )
        remove_worktree(repo, stranded)

        # The relink is what makes the two halves run under the same interpreter
        # and the same packages, and it is a call inside `without_fix`. The
        # fixture installs nothing, so without a case of its own the call can be
        # dropped while every other case still passes, leaving each half to find
        # its own — which is the state the tool exists to rule out.
        deps = repo / "backend" / ".venv"
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "marker").write_text("x", encoding="utf-8")
        link_tree = Path(scratch) / "relink-tree"
        remove_worktree(repo, link_tree)
        shutil.rmtree(link_tree, ignore_errors=True)
        try:
            subprocess.run(
                ["git", "worktree", "add", "-q", "--detach", str(link_tree), "HEAD"],
                cwd=str(repo),
                env=env,
                check=True,
                capture_output=True,
            )
            with _Muffle():
                try:
                    without_fix(repo, without, link_tree, ["test_app.py"], False)
                    linked_in = (link_tree / "backend" / ".venv" / "marker").is_file()
                except SystemExit:
                    linked_in = False
        finally:
            remove_worktree(repo, link_tree)
            shutil.rmtree(deps, ignore_errors=True)
        tally.case(
            "the without-the-fix checkout gets the repository's dependency directories",
            linked_in,
        )

        def relative_of(node: str) -> str | None:
            """`locate`'s answer, or None where it refuses — a case about which
            root a node id is found under has to read as a FAIL, not crash the
            suite, when the root it names is gone."""
            with _Muffle():
                try:
                    return locate(node, repo)[1]
                except SystemExit:
                    return None

        tally.case(
            "a node id that is not repo-relative is found under backend/",
            relative_of("tests/test_app.py") == "backend/tests/test_app.py",
        )
        tally.case(
            "a node id that is not repo-relative is found under frontend/",
            relative_of("src/a.spec.ts") == "frontend/src/a.spec.ts",
        )

        # The child is run from a directory outside the fixture. Where that
        # directory sits is TMPDIR's business, and if TMPDIR is somewhere inside
        # a git work tree — /work/.tmp under the checkouts this repo's own agent
        # harness makes, say — then `git rev-parse --show-toplevel` climbs out of
        # the fixture and into that enclosing repository, the tool is not refused,
        # and this case fails on a machine configured that way rather than on the
        # change. GIT_CEILING_DIRECTORIES stops the climb above the fixture's
        # parent, so the case means the same thing wherever TMPDIR points.
        outside = prove(
            "HEAD",
            runs,
            node="test_app.py",
            where=Path(scratch),
            env_extra={"GIT_CEILING_DIRECTORIES": str(Path(scratch).parent)},
        )
        tally.case(
            "run outside the repository: refused, not judged",
            outside.returncode == 2
            and "run this from inside the repository" in outside.stderr,
        )
        # And the case has to bite: from inside the fixture the same run judges,
        # so "refused" is the outside directory talking and not a tool that
        # refuses everything. HEAD is the fix commit, so both halves are green
        # and the verdict is NOT PROVEN (exit 1) — a verdict, where the outside
        # run gave none.
        inside = prove("HEAD", runs)
        tally.case(
            "run outside the repository: the same run inside it does judge",
            inside.returncode == 1
            and "NOT PROVEN" in inside.stdout
            and "run this from inside the repository" not in inside.stderr,
        )

    # The line the repo-guards step reads is the process's exit code, so the
    # conversion from a bad count to it is proven the way the gate reads it: a
    # child run forced to fail a case, not this process reporting on itself.
    # In the child the real suite runs, so the count is one it produced, and a
    # case genuinely failing is what makes it red — the mutation at the end of
    # the suite has to answer for. The child is told not to recurse: its own
    # `self_test` would otherwise reach this line and spawn another.
    if os.environ.get("PROVE_REGRESSION_EXIT_CODE_CHILD") != "1":
        child_note = (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('pr', sys.argv[1])\n"
            "pr = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(pr)\n"
            # One failing case, not two: the conversion that matters is the one
            # for a single failure, and with two the child cannot tell a
            # threshold of one from a threshold of two. The constant reddens
            # exactly one row of `judge`'s table, and the count is read out of
            # the summary so that a `>` in the conversion reddens this case
            # instead of leaving the suite green.
            "pr.judge = lambda before, after, node: 1\n"
            "raise SystemExit(pr.self_test())\n"
        )
        broken = subprocess.run(
            [sys.executable, "-c", child_note, str(Path(__file__).resolve())],
            env={**os.environ, "PROVE_REGRESSION_EXIT_CODE_CHILD": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        tail = broken.stdout.splitlines()[-1] if broken.stdout.splitlines() else ""
        # Both halves: the code the gate reads, and the summary a person reads.
        # A build that prints FAIL and exits 0 is the thing being ruled out.
        # The count is held against the FAIL lines as well, and that is what
        # makes the conversion's threshold observable: one failing case has to
        # be enough, and a conversion that fires only above one prints the same
        # `all ... passed` a green suite prints. Counting what the child printed
        # rather than pinning a number keeps the case off how many of its cases
        # the patch happens to redden.
        failed = sum(
            line.strip().startswith("FAIL") for line in broken.stdout.splitlines()
        )
        tally.case(
            "a genuinely failing case makes the whole suite exit non-zero",
            broken.returncode == 1
            and failed >= 1
            and f"{failed} of" in tail
            and "FAILED" in tail,
        )
    return _self_test_exit(tally)


class _Captured:
    """Keep what a refusal said, where `_Muffle` throws it away.

    A case about a refusal's message cannot read it through `_Muffle`, which is
    why this exists; the two are not interchangeable. This swaps `sys.stdout`
    and `sys.stderr`, which is all a refusal raised in this process needs — it
    fails before it starts a runner, so nothing reaches a file descriptor. A
    refusal printed by a child shell still needs `_Muffle`'s descriptors.
    """

    def __enter__(self) -> "_Captured":
        self._saved = sys.stdout, sys.stderr
        self.out, self.err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = self.out, self.err
        return self

    def __exit__(self, *exc: object) -> None:
        sys.stdout, sys.stderr = self._saved


class _Muffle:
    """Silence a verdict line or a refusal while the self-test reads only its code.

    Both streams: judge() writes its verdict to stdout, and the refusals write to
    stderr, and the self-test asserts on exit codes rather than on either.

    The file descriptors are swapped too, not just `sys.stdout`/`sys.stderr`:
    a case that runs a command which does not exist would otherwise print the
    shell's complaint over the self-test's own output, where a reader has no way
    to tell it from a case that failed.
    """

    def __enter__(self) -> None:
        self._saved = sys.stdout, sys.stderr
        devnull = open(os.devnull, "w")  # noqa: SIM115
        self._devnull = devnull
        self._saved_fd2 = os.dup(2)
        os.dup2(devnull.fileno(), 2)
        sys.stdout = sys.stderr = devnull

    def __exit__(self, *exc: object) -> None:
        sys.stdout, sys.stderr = self._saved
        os.dup2(self._saved_fd2, 2)
        os.close(self._saved_fd2)
        self._devnull.close()


def _worktrees(repo: Path) -> list[str]:
    done = git("worktree", "list", "--porcelain", cwd=repo)
    return [
        line.split(" ", 1)[1]
        for line in done.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def _registered(repo: Path, path: Path) -> bool:
    """Whether `path` is one of `repo`'s worktrees, compared as real paths.

    `git worktree add` resolves symlinks before it writes the registration, so a
    worktree made under a TMPDIR that is a link comes back from `worktree list`
    as its real path while the path this script built from TMPDIR is the link.
    Comparing the raw strings makes the answer depend on where TMPDIR points —
    the same trap the fixture's git environment is sealed against — rather than
    on the code.
    """
    return str(path.resolve()) in {
        str(Path(here).resolve()) for here in _worktrees(repo)
    }


def _extra_worktrees(repo: Path) -> list[str]:
    """Worktrees other than the repository itself — ones a run left behind."""
    return [p for p in _worktrees(repo) if Path(p).resolve() != repo.resolve()]


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
