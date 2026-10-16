#!/usr/bin/env python3
"""Census the merge conflicts a set of branches has against a base branch.

WHY THIS EXISTS. Answering "which branches conflict, and on what" by hand goes
wrong the same three ways every time, and each wrong way produces a number that
still looks like a measurement:

  * `git rev-list --remotes ... refs/remotes/pr/*` — a ref glob in argument
    position is a *pathspec*, so the command silently matches no path and
    reports zero. Here the branches come from `for-each-ref`, where a pattern is
    a ref pattern and an empty answer is a real empty answer.
  * Counting conflicted files by grepping their names out of `git merge-tree`
    output — that output also carries `Auto-merging <path>` for every file git
    merged *by itself*, so the count roughly doubles. The conflicted set here
    comes from `replay_conflict` in `arch-metrics.py`, this repository's single
    definition of what merge-tree's output means: it reads the path list that
    precedes the first blank line, and nothing after it.
  * A pattern that selects or classes the paths has to admit digits.
    `frontend/[a-z-]+-baseline\\.json` cannot match `a11y-baseline.json`, whose
    one real conflict is the whole reason the check was written.

WHAT IT REPORTS. For each branch, whether `git merge-tree` can merge the base
into it, and which files it cannot merge; then the same numbers the other way
up — per file, how many branches are contending for it — because that is the
question a census is usually run to answer. A branch whose blobs this checkout
does not have is reported as unreadable, never as clean: "no conflicts" and
"could not look" are the two answers this script exists to keep apart.

Usage:
    conflict-census.py [BRANCH...] [--base REF] [--remotes] [--top N] [--json]

With no BRANCH arguments the branches are every local branch, or — with
`--remotes` — every remote-tracking branch. The base defaults to whatever this
checkout calls main (`origin/main` first, then `main`). Exit codes follow
`arch-metrics.py`: 0 the census was taken, 2 it could not be — a base or a
branch that does not resolve, or nothing left to examine.

`git merge-tree` takes two commits and merges them at their merge base, so the
replay is `<base> <branch>`: what the forge would ask if the branch were merged
today, not whether the branch is up to date.

The tests are `.github/scripts/test_conflict_census.py`: throwaway repositories,
collected automatically by `repo-guards.yml`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCH_METRICS = Path(__file__).resolve().parent / "arch-metrics.py"

#: How many contended files the text report lists before it says how many it
#: hid. `--top 0` lists every one. The hidden ones are counted out loud — a
#: trimmed list that does not say it was trimmed reads as the whole census.
DEFAULT_TOP = 20


def load_arch_metrics() -> Any:
    """`arch-metrics.py`, loaded for the one definition of a conflict replay.

    `frontend_grade.py`, `check-file-sizes.py` and `check_deferred_imports.py`
    are loaded the same way for the same reason: the board restates none of
    them, because a second copy of "what does this output mean" drifts from the
    first and the two eventually disagree about a tree. The import cost is
    module-level definitions only — nothing runs on load.
    """
    spec = importlib.util.spec_from_file_location("_conflict_census_arch_metrics", ARCH_METRICS)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load {ARCH_METRICS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_conflict_census_arch_metrics"] = module
    spec.loader.exec_module(module)
    return module


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def full_ref(root: Path, name: str) -> str | None:
    """`refs/remotes/origin/main` for `origin/main`; None for a sha or a typo."""
    try:
        out = git(root, "rev-parse", "--symbolic-full-name", name)
    except subprocess.CalledProcessError:
        return None
    return out.strip() or None


def enumerate_branches(root: Path, pattern: str, skip: str | None) -> list[str]:
    """The refs `pattern` names, short, minus `skip` and any `*/HEAD`.

    `for-each-ref` is what keeps this a ref pattern: the same glob handed to
    `rev-list` in argument position is a pathspec, matches no path, and returns
    nothing — which reads as "no branches", the one answer that is wrong.

    The pattern is `refs/heads/**`, not `refs/heads/*`: in a ref glob `*` stops
    at a `/`, so the one-level form silently omits every namespaced branch —
    including this repository's own `task/<id>`, which is to say exactly the
    branch a census is run on. `**` crosses the `/`.
    """
    out = git(root, "for-each-ref", "--format=%(refname)%09%(refname:short)", pattern)
    branches: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        ref, _, short = line.partition("\t")
        # `origin/HEAD` is skipped by its full name, not its short one:
        # `%(refname:short)` renders it as bare `origin`, which is neither
        # `*/HEAD` nor a branch, and would be censused as one.
        if ref == skip or ref.endswith("/HEAD"):
            continue
        branches.append(short)
    return branches


def resolve_base(
    root: Path, requested: str | None, default_main: Callable[[Path], str | None]
) -> tuple[str | None, str | None]:
    """(the name to merge from, its commit) or (name, None) when it is not there."""
    name = requested or default_main(root)
    if name is None:
        return None, None
    try:
        sha = git(root, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}").strip()
    except subprocess.CalledProcessError:
        return name, None
    return name, sha


def census(
    root: Path, base: str, branches: list[str], replay: Callable[[Path, list[str]], list[str] | None]
) -> dict[str, Any]:
    """One `merge-tree` replay per branch, then the same numbers per file."""
    clean: list[str] = []
    conflicted: dict[str, list[str]] = {}
    unreadable: list[str] = []
    for branch in branches:
        paths = replay(root, [base, branch])
        if paths is None:
            unreadable.append(branch)
        elif paths:
            conflicted[branch] = paths
        else:
            clean.append(branch)

    files: dict[str, list[str]] = {}
    for branch, paths in conflicted.items():
        for path in paths:
            files.setdefault(path, []).append(branch)

    return {
        "base": base,
        "examined": len(branches),
        "clean": sorted(clean),
        "conflicted": {b: conflicted[b] for b in sorted(conflicted)},
        "unreadable": sorted(unreadable),
        "files": {p: sorted(files[p]) for p in sorted(files)},
    }


def render(result: dict[str, Any], base_sha: str | None, top: int, unresolved: list[str]) -> str:
    """The report, as text. Every trim and every failure is said out loud."""
    lines: list[str] = []
    lines.append(f"base {result['base']} ({base_sha[:8] if base_sha else 'unresolved'})")
    lines.append(
        "examined {examined} branches: {clean} clean, {conflicted} conflicted, "
        "{unreadable} unreadable".format(
            examined=result["examined"],
            clean=len(result["clean"]),
            conflicted=len(result["conflicted"]),
            unreadable=len(result["unreadable"]),
        )
    )
    if unresolved:
        lines.append(f"not examined (does not resolve): {', '.join(unresolved)}")

    files = result["files"]
    if files:
        shown = list(files.items())
        hidden = 0
        if top > 0 and len(shown) > top:
            hidden = len(shown) - top
            shown = shown[:top]
        width = max(len(str(len(files[p]))) for p, _ in shown)
        lines.append("")
        lines.append("contended files, by how many branches conflict on them:")
        for path, branches in shown:
            lines.append(f"  {len(branches):>{width}}  {path}  ({', '.join(branches)})")
        if hidden:
            lines.append(f"  … and {hidden} more contended files (--top 0 lists all)")
    else:
        lines.append("")
        lines.append("no contended files")

    if result["conflicted"]:
        lines.append("")
        lines.append("conflicted branches:")
        for branch, paths in result["conflicted"].items():
            lines.append(f"  {branch}  {', '.join(paths)}")

    if result["unreadable"]:
        lines.append("")
        lines.append("unreadable — cannot judge, not clean:")
        for branch in result["unreadable"]:
            lines.append(f"  {branch}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("branches", nargs="*", help="branches to replay; default: enumerate")
    parser.add_argument("--base", help="the branch to merge from; default: this checkout's main")
    parser.add_argument(
        "--remotes", action="store_true", help="enumerate remote-tracking branches, not local ones"
    )
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="contended files to list, 0 for all")
    parser.add_argument("--json", action="store_true", help="the census as JSON, for a reader")
    parser.add_argument("--root", help="the repository to census; default: the current directory")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path.cwd()
    arch = load_arch_metrics()
    base, base_sha = resolve_base(root, args.base, arch.main_ref)
    if base is None or base_sha is None:
        print(
            f"cannot judge: base {args.base or 'main'!r} does not resolve in {root}",
            file=sys.stderr,
        )
        return 2

    unresolved: list[str] = []
    if args.branches:
        branches = []
        for name in args.branches:
            try:
                git(root, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
            except subprocess.CalledProcessError:
                unresolved.append(name)
                continue
            branches.append(name)
    else:
        pattern = "refs/remotes/**" if args.remotes else "refs/heads/**"
        branches = enumerate_branches(root, pattern, full_ref(root, base))

    if not branches:
        print(
            "cannot judge: no branches to examine "
            f"(looked at {'remote-tracking branches' if args.remotes else 'local branches'})"
            + (f"; did not resolve: {', '.join(unresolved)}" if unresolved else ""),
            file=sys.stderr,
        )
        return 2

    result = census(root, base, branches, arch.replay_conflict)
    result["base_sha"] = base_sha
    result["unresolved"] = sorted(unresolved)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render(result, base_sha, args.top, sorted(unresolved)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
