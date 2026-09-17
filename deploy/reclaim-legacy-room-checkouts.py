#!/usr/bin/env python3
"""Reclaim the room checkouts the old layout left behind.

Until #936 (2026-09-09) a room's working directory was
``~/.cheese/work/<project>/<room>`` and held a full checkout of the repository.
That commit moved it: a room's cwd became a scratch directory under its own home
(``<room home>/room``), and the repository work moved to tasks, which
``cheese worktree`` puts under ``<room home>/.cheese/tasks``.

So nothing writes ``~/.cheese/work`` any more — ``device_work_dir()`` has had no
caller since, and `agent/resource_cleanup.py` already calls what is there
"legacy checkouts". On dev, 2026-09-17, it was still 107GB across 102 rooms.

Archival removes these along with the rest of a room, but only once somebody
archives the room, and nothing archives a room for being idle. This reclaims
them where they stand, without touching the room.

WHAT MAKES IT SAFE is not that they are unreachable — it is that they are
PUBLISHED. A pre-#936 checkout can hold commits or edits that never left the
box, and those are the user's only copy. So every directory goes through the
same two checks archival uses before it deletes anything, from the same module,
so there is one definition of "this is safe to delete" and not two:

    check_no_writers   nothing holds a file or a cwd in there
    check_published    no uncommitted changes, and no commits that are not
                       already on origin or in refs/cheese/published/*

Anything that fails either check is KEPT and reported with the reason. That is
the whole point: the reclaim is large, so the gate has to be exact.

Reports by default. Pass --apply to actually delete.
"""

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESOURCE_CLEANUP = HERE.parent / "backend/app/domain/agent/resource_cleanup.py"


def load_resource_cleanup(path: Path = RESOURCE_CLEANUP):
    """The device-side checks, as the module that owns them.

    Loaded by path rather than imported: this runs from a deploy, outside the
    backend's package and its virtualenv, and the module is deliberately
    standard-library only because it is also shipped to machines.
    """
    spec = importlib.util.spec_from_file_location("cheese_resource_cleanup", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def size_of(path: Path) -> int:
    result = subprocess.run(
        ["du", "-sxb", str(path)], capture_output=True, text=True, check=False
    )
    head = result.stdout.split("\t", 1)[0].strip()
    return int(head) if head.isdigit() else 0


def human(count: int) -> str:
    size = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:,.1f}{unit}"
        size /= 1024
    return ""  # pragma: no cover - unreachable


def is_room_path(project: str, resource: str) -> bool:
    """Both segments must be room ids. A directory that is not named like one
    was not put here by the platform, and nothing here will delete it."""
    try:
        uuid.UUID(project), uuid.UUID(resource)
    except ValueError:
        return False
    return True


def reclaim(root: Path, *, apply: bool, cleanup, out=sys.stdout) -> int:
    if not root.is_dir():
        print(f"no legacy checkouts at {root} — nothing to reclaim", file=out)
        return 0
    verb = "reclaiming" if apply else "reclaimable"
    print(f"{verb} legacy room checkouts under {root}", file=out)
    freed = kept = skipped = 0
    held: dict[str, int] = {}

    def hold(exc: RuntimeError) -> None:
        nonlocal kept
        kept += 1
        held[str(exc)] = held.get(str(exc), 0) + 1

    # The publication check first, and it is the cheap one: `git status` reads an
    # index, while `lsof +D` costs ~2s per CALL no matter what it is pointed at
    # (see `check_no_writers`). Checked in this order, a directory held back for
    # unpublished work never reaches lsof at all — and those are exactly the
    # directories that survive every sweep and would otherwise pay that 2s
    # forever.
    candidates: list[Path] = []
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir() or project_dir.is_symlink():
            continue
        for work in sorted(project_dir.iterdir()):
            if not work.is_dir() or work.is_symlink():
                continue
            if not is_room_path(project_dir.name, work.name):
                skipped += 1
                continue
            try:
                cleanup.check_published(work)
            except RuntimeError as exc:
                hold(exc)
                continue
            candidates.append(work)

    # Then one lsof for all of them. If it comes back clean — the ordinary case,
    # since these rooms have not run in weeks — that is the entire writer check,
    # two seconds for the whole box. Only a hit makes it worth asking per
    # directory which one it was, and that answer costs 2s each.
    if candidates:
        try:
            cleanup.check_no_writers(candidates)
        except RuntimeError:
            remaining = []
            for work in candidates:
                try:
                    cleanup.check_no_writers([work])
                except RuntimeError as exc:
                    hold(exc)
                    continue
                remaining.append(work)
            candidates = remaining

    for work in candidates:
        freed += size_of(work)
        if apply:
            cleanup.remove_tree(work)
    print(f"  {human(freed)} {'reclaimed' if apply else 'reclaimable'}", file=out)
    # Loudly, and per reason: a checkout held back is the user's only copy of
    # something, and the number is how anyone learns that before it is lost to
    # a box being rebuilt.
    for reason, count in sorted(held.items()):
        print(f"  {count} kept: {reason}", file=out)
    if skipped:
        print(f"  {skipped} skipped: not named like a room", file=out)
    return freed


def _self_test() -> int:
    """Three checkouts, one of each kind, through the real checks."""
    cleanup = load_resource_cleanup()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        root = tmp / "work"
        project = str(uuid.uuid4())

        def git(*args, cwd):
            done = subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t",
                    # A machine's own signing / hook / template settings are not
                    # part of what this is testing, and one of them turning the
                    # fixture red would read as a bug in the reclaim.
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_SYSTEM": "/dev/null",
                },
            )
            if done.returncode:
                raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")

        origin = tmp / "origin.git"
        origin.mkdir()
        git("init", "--bare", "-q", cwd=origin)

        made = {}
        for name in ("published", "dirty", "unpushed"):
            work = root / project / str(uuid.uuid4())
            work.parent.mkdir(parents=True, exist_ok=True)
            git("clone", "-q", str(origin), str(work), cwd=tmp)
            (work / "f").write_text("one")
            git("add", "-A", cwd=work)
            git("commit", "-qm", "one", cwd=work)
            git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=work)
            made[name] = work
        (made["dirty"] / "f").write_text("edited, never committed")
        (made["unpushed"] / "f").write_text("two")
        git("add", "-A", cwd=made["unpushed"])
        git("commit", "-qm", "two", cwd=made["unpushed"])

        junk = root / "not-a-room" / "either"
        junk.mkdir(parents=True)
        (junk / "keep").write_text("x")

        import io

        report = io.StringIO()
        reclaim(root, apply=True, cleanup=cleanup, out=report)
        printed = report.getvalue()

        failures = []
        if made["published"].exists():
            failures.append("kept a published checkout")
        for name in ("dirty", "unpushed"):
            if not made[name].exists():
                failures.append(f"deleted a checkout with {name} work")
        if not junk.exists():
            failures.append("deleted a directory that is not a room")
        if "2 kept" not in printed and printed.count("kept:") != 2:
            failures.append(f"held-back checkouts not reported:\n{printed}")
        for failure in failures:
            print(f"self-test FAIL: {failure}")
        if failures:
            return 1
    print("self-test OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete, not report")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--root",
        default=None,
        help="the legacy work root (default: $CHEESE_DEVICE_HOME/work)",
    )
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    device_home = os.environ.get("CHEESE_DEVICE_HOME") or str(Path.home() / ".cheese")
    root = Path(args.root) if args.root else Path(device_home) / "work"
    reclaim(root, apply=args.apply, cleanup=load_resource_cleanup())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
