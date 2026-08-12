#!/usr/bin/env python3
"""Fail when merging this branch would fork the alembic chain.

WHY THIS EXISTS, given test.yml already has a `migration-heads` job: that job
checks the tree it is handed. On a pull request that tree is the branch merged
with main *as of when CI last ran*. Two branches that each add a migration on
top of the same parent therefore both pass — each has exactly one head — and
the fork only appears once the second one lands. `migration-heads` then reds
main, `alembic upgrade head` refuses, and the deploy aborts. That happened four
times on 2026-08-09/10 (see .claude/rules/migrations.md).

This closes the other half: it compares against origin/main *now*, so a branch
that has gone stale is caught before the merge instead of after. It parses the
revision graph and touches no database.

Usage:
    check-migration-fork.py [--base origin/main] [--versions-dir DIR]
    check-migration-fork.py --self-test
"""

import argparse
import ast
import subprocess
import sys

VERSIONS_DIR = "backend/alembic/versions"


def parse_revision(source: str) -> tuple[str | None, list[str]]:
    """Return (revision, parents) for one alembic version file.

    AST rather than a regex because `down_revision` is legitimately a tuple on
    merge revisions (`= ("d3b8f1a20c11", "f6b7c8d9e0a1")`), and a regex that
    reads only the first string would silently drop the second parent — turning
    a merge into a fake head and this guard into a false alarm.
    """
    revision: str | None = None
    parents: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None, []

    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        else:
            continue
        if value is None:
            continue
        if name == "revision":
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                revision = value.value
        elif name == "down_revision":
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parents = [value.value]
            elif isinstance(value, ast.Tuple | ast.List):
                parents = [
                    e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
    return revision, parents


def find_heads(graph: dict[str, list[str]]) -> list[str]:
    """Revisions nobody points back to — the tips of the chain."""
    referenced = {parent for parents in graph.values() for parent in parents}
    return sorted(rev for rev in graph if rev not in referenced)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def collect(ref: str | None, versions_dir: str) -> dict[str, list[str]]:
    """Revision graph from a git ref, or from the working tree when ref is None."""
    graph: dict[str, list[str]] = {}
    if ref is None:
        from pathlib import Path

        paths = sorted(str(p) for p in Path(versions_dir).glob("*.py"))
        sources = {p: Path(p).read_text(encoding="utf-8") for p in paths}
    else:
        listing = _git("ls-tree", "-r", "--name-only", ref, f"{versions_dir}/")
        paths = [p for p in listing.splitlines() if p.endswith(".py")]
        sources = {p: _git("show", f"{ref}:{p}") for p in paths}

    for source in sources.values():
        revision, parents = parse_revision(source)
        if revision:
            graph[revision] = parents
    return graph


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--versions-dir", default=VERSIONS_DIR)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    try:
        base_graph = collect(args.base, args.versions_dir)
    except subprocess.CalledProcessError:
        print(f"note: {args.base} not available — nothing to compare against, skipping")
        return 0

    branch_graph = collect(None, args.versions_dir)

    # --- HEAD sentinel -----------------------------------------------------
    # The chain's tip, duplicated into ONE shared file (backend/alembic/HEAD).
    # The graph itself lives spread across file contents, so git cannot see two
    # branches claim the same parent — different files, clean textual merge.
    # Forcing every migration PR to move this one line makes two concurrent
    # migration PRs collide in GIT (same line), and GitHub re-evaluates
    # CONFLICTING continuously as main moves — unlike checks, whose green goes
    # stale the moment a sibling merges. That staleness is exactly how main
    # ended up with three heads on 2026-08-12 despite this very script.
    from pathlib import Path

    sentinel_path = Path(args.versions_dir).parent / "HEAD"
    branch_heads = find_heads(branch_graph)
    if len(branch_heads) == 1:
        tip = branch_heads[0]
        sentinel = (
            sentinel_path.read_text(encoding="utf-8").strip()
            if sentinel_path.is_file()
            else None
        )
        if sentinel != tip:
            if sentinel is None:
                print(f"FAIL: {sentinel_path} is missing.")
            else:
                print(f"FAIL: {sentinel_path} names {sentinel}, but the chain's tip is {tip}.")
            print()
            print("Every migration PR must move backend/alembic/HEAD to its new revision id")
            print("(one line). That is what makes two concurrent migration PRs collide in git")
            print("instead of silently forking alembic. See .claude/rules/migrations.md.")
            print(f"Fix: echo {tip} > {sentinel_path}")
            print("::error::backend/alembic/HEAD does not name the migration chain's tip")
            return 1

    # The union is what main would hold after this merge. Branch wins on
    # conflict so an edited migration is judged as edited.
    merged = {**base_graph, **branch_graph}
    heads = find_heads(merged)

    if len(heads) <= 1:
        print(f"PASS: one migration head after merging with {args.base} ({len(merged)} revisions)")
        print(f"PASS: backend/alembic/HEAD names the tip ({branch_heads[0] if branch_heads else '—'})")
        return 0

    only_here = set(branch_graph) - set(base_graph)
    print(f"FAIL: merging this branch into {args.base} would leave {len(heads)} alembic heads:")
    for head in heads:
        origin = "this branch" if head in only_here else args.base
        print(f"  {head}  (from {origin})")
    print()
    print("Both sides added a migration on top of the same parent. `alembic upgrade head`")
    print("refuses a forked chain, so this would red main and abort the deploy.")
    print(f"Fix: rebase onto {args.base} and re-chain your migration's down_revision onto")
    print("its current head (or `uv run alembic merge heads`). See .claude/rules/migrations.md.")
    print(f"::error::merging would fork the alembic chain into {len(heads)} heads")
    return 1


def self_test() -> int:
    """Prove the parser and the head detection agree with reality."""
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    check(
        "annotated assignment",
        parse_revision('revision: str = "abc"\ndown_revision: str | None = "def"\n'),
        ("abc", ["def"]),
    )
    check(
        "plain assignment",
        parse_revision('revision = "abc"\ndown_revision = "def"\n'),
        ("abc", ["def"]),
    )
    check(
        "base revision",
        parse_revision('revision = "abc"\ndown_revision = None\n'),
        ("abc", []),
    )
    # The case a regex would get wrong.
    check(
        "merge revision keeps both parents",
        parse_revision('revision: str = "m"\ndown_revision: str | Sequence[str] | None = ("a", "b")\n'),
        ("m", ["a", "b"]),
    )
    check("linear chain has one head", find_heads({"a": [], "b": ["a"], "c": ["b"]}), ["c"])
    check("a fork has two heads", find_heads({"a": [], "b": ["a"], "c": ["a"]}), ["b", "c"])
    check(
        "a merge revision closes the fork",
        find_heads({"a": [], "b": ["a"], "c": ["a"], "m": ["b", "c"]}),
        ["m"],
    )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}")
    if failures:
        return 1
    print("PASS: check-migration-fork self-test (parser variants, merge parents, head detection)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
