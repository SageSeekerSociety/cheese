#!/usr/bin/env python3
"""Fail when merging this branch would fork the alembic chain.

WHY THIS EXISTS, given test.yml already checks for one alembic head (the
`static` job's "Exactly one alembic head" step): that step checks the tree it
is handed. On a pull request that tree is the branch merged
with main *as of when CI last ran*. Two branches that each add a migration on
top of the same parent therefore both pass — each has exactly one head — and
the fork only appears once the second one lands. That step then reds
main, `alembic upgrade head` refuses, and the deploy aborts. That happened four
times on 2026-08-09/10 (see .claude/rules/migrations.md).

This closes the other half: it compares against origin/main *now*, so a branch
that has gone stale is caught before the merge instead of after. It parses the
revision graph and touches no database.

`--rechain` reads the same two graphs to repair rather than report: it re-points
this branch's migrations at the base's current head and moves the sentinel, so
the fork the check refuses to let through is one command to fix. It writes only
after the whole rewrite is known to leave exactly one head.

Usage:
    check-migration-fork.py [--base origin/main] [--versions-dir DIR]
    check-migration-fork.py --rechain [--base origin/main]
    check-migration-fork.py --self-test
"""

import argparse
import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

VERSIONS_DIR = "backend/alembic/versions"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Two situations have no re-chain answer and need a human: the branch forked on
# its own (two of its revisions claim the same parent, so there is no single one
# to move), and one of its revisions is a base revision with no parent to move
# at all. Both get the same two options rather than a guess.
MERGE_ADVICE = (
    "Fix it one of two ways:\n"
    "  * `uv run alembic merge heads` — join them with a merge migration. Check\n"
    "    first that no merge migration for that parent pair already exists: two\n"
    "    identical merge migrations are themselves two heads.\n"
    "  * split this branch's migrations so one continues from the other.\n"
    "See .claude/rules/migrations.md."
)


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


def _git_ok(*args: str) -> str | None:
    """stdout of a git command, or None when it fails (used for optional refs)."""
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def collect(ref: str | None, versions_dir: str) -> dict[str, list[str]]:
    """Revision graph from a git ref, or from the working tree when ref is None."""
    graph: dict[str, list[str]] = {}
    if ref is None:
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


def rewrite_down_revision(source: str, parents: list[str]) -> str | None:
    """`source` with its `down_revision` set to `parents`, or None if it has none.

    A splice of the value's own source span, not a re-render: everything around
    it — the docstring's `Revises:` line, comments, formatting — has to come back
    byte for byte. Substituting by line would rewrite the `Revises:` comment
    instead, which is where the *old* parent is also written; that line is only
    documentation to alembic, so that mistake reads as a fix and changes nothing.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    value: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, candidate = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, candidate = node.targets[0].id, node.value
        else:
            continue
        if name == "down_revision" and candidate is not None:
            value = candidate
    if value is None:
        return None

    # One parent stays a bare string: alembic accepts a one-element tuple, but a
    # diff between `"abc"` and `("abc",)` reads like a change of shape.
    rendered = repr(parents[0]) if len(parents) == 1 else repr(tuple(parents))
    lines = source.splitlines(keepends=True)
    before = "".join(lines[: value.lineno - 1]) + lines[value.lineno - 1][: value.col_offset]
    after = lines[value.end_lineno - 1][value.end_col_offset :] + "".join(lines[value.end_lineno :])
    return before + rendered + after


def rechain_plan(
    branch_graph: dict[str, list[str]], base_graph: dict[str, list[str]]
) -> tuple[dict[str, list[str]] | None, str]:
    """Which parents to move, as `{revision: new parents}` for this branch's own.

    Returns ({}, "") when there is nothing to move (so the caller still repairs a
    stale sentinel), and (None, reason) when the answer is a choice rather than a
    rewrite. Pure, so --self-test can decide every wrong answer here without a
    repository.
    """
    base_heads = find_heads(base_graph)
    if len(base_heads) != 1:
        return None, (
            f"the base side has {len(base_heads)} heads "
            f"({', '.join(base_heads) or 'none'}), so there is no single revision to continue from"
        )
    base_head = base_heads[0]

    own = sorted(set(branch_graph) - set(base_graph))
    if not own:
        return ({}, "")

    if base_head not in branch_graph:
        return None, (
            f"the base head {base_head} is not in this checkout, so merge {base_head}'s "
            "branch into this one first — re-chaining onto a revision this tree does not "
            "have would name a parent that is not there"
        )

    own_heads = [head for head in find_heads(branch_graph) if head in set(own)]
    if len(own_heads) != 1:
        return None, (
            f"this branch has {len(own_heads)} heads of its own "
            f"({', '.join(own_heads) or 'none'}), and a chain can only continue from one"
        )

    rewrites: dict[str, list[str]] = {}
    for revision in own:
        parents = branch_graph[revision]
        # A parent that is already on the base side moves to its head; one that
        # is this branch's own stays where it is. Deduplicated, so a merge
        # revision whose two parents both live on the base side loses one.
        mapped = [base_head if parent in base_graph else parent for parent in parents]
        deduped = list(dict.fromkeys(mapped))
        if deduped != parents:
            rewrites[revision] = deduped
    return rewrites, ""


def write_sentinel(path: Path, tip: str) -> str:
    """Point the sentinel at `tip`, the way reanchor-migration-head.py does.

    A complete temporary beside it and one atomic rename: a write that fails
    leaves the previous file in place instead of half of a new one.
    """
    desired = f"{tip}\n".encode()
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        current = None
    if current == desired:
        return f"UNCHANGED: {path} already names {tip}"

    old = current.decode("utf-8", errors="replace").strip() if current is not None else "<missing>"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(desired)
            handle.flush()
            os.chmod(temporary, path.stat().st_mode if path.exists() else 0o644)
            handle.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return f"UPDATED: {path} {old} -> {tip}"


def stale_base_refusal(base: str) -> str | None:
    """Why a `--base` that is a local branch behind its upstream must be refused.

    `origin/main` is a remote-tracking ref: nothing local can be behind it, and
    whether the remote moved since the last fetch is invisible from here. A local
    branch is different — `git rev-list --count <base>..<base>@{upstream}` counts
    what its upstream has and it does not, and everything below reads that branch
    as the truth. Comparing against a stale base answers the question about a
    tree nobody is merging; refusing costs one fetch.
    """
    upstream = _git_ok("rev-parse", "--abbrev-ref", f"{base}@{{upstream}}")
    if upstream is None or upstream == base:
        return None
    behind = _git_ok("rev-list", "--count", f"{base}..{base}@{{upstream}}")
    if behind is None or behind == "0":
        return None
    return (
        f"local branch `{base}` is {behind} commit(s) behind `{upstream}`, so the "
        f"revision graph would come from a stale ref. Update it first:\n"
        f"    git fetch\n"
        f"    git merge --ff-only {upstream}      # while on {base}"
    )


def rechain(args) -> int:
    try:
        base_graph = collect(args.base, args.versions_dir)
    except subprocess.CalledProcessError:
        print(f"FAIL: {args.base} is not available, so there is no head to re-chain onto.")
        print("Fetch it first (`git fetch`), or name another --base.")
        return 1

    sentinel_path = Path(args.versions_dir).parent / "HEAD"

    sources: dict[str, tuple[Path, str]] = {}
    duplicates: list[str] = []
    for path in sorted(Path(args.versions_dir).glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision, _ = parse_revision(text)
        if not revision:
            continue
        if revision in sources:
            duplicates.append(f"{revision}: both {sources[revision][0]} and {path} declare it")
            continue
        sources[revision] = (path, text)

    if duplicates:
        print("FAIL: two files declare the same revision id, so there is no one file to rewrite:")
        for duplicate in duplicates:
            print(f"  {duplicate}")
        return 1

    branch_graph = {revision: parse_revision(text)[1] for revision, (_, text) in sources.items()}

    rewrites, reason = rechain_plan(branch_graph, base_graph)
    if rewrites is None:
        print(f"FAIL: cannot re-chain: {reason}.")
        print()
        print(MERGE_ADVICE)
        print(f"::error::cannot re-chain the alembic chain onto {args.base}")
        return 1

    new_graph = dict(branch_graph)
    new_graph.update(rewrites)
    heads = find_heads({**base_graph, **new_graph})
    if len(heads) != 1:
        print(f"FAIL: re-chaining would still leave {len(heads)} alembic heads:")
        for head in heads:
            print(f"  {head}")
        print()
        print(MERGE_ADVICE)
        print(f"::error::re-chaining would leave {len(heads)} alembic heads")
        return 1

    # Everything is decided and validated before the first write, so a refusal
    # never leaves half of the branch re-chained.
    rendered: list[tuple[Path, str, list[str]]] = []
    for revision, parents in sorted(rewrites.items()):
        path, text = sources[revision]
        rewritten = rewrite_down_revision(text, parents)
        if rewritten is None:
            print(f"FAIL: {path} declares {revision} but has no down_revision to rewrite.")
            return 1
        rendered.append((path, rewritten, parents))

    for path, rewritten, parents in rendered:
        path.write_text(rewritten, encoding="utf-8")
        target = parents[0] if len(parents) == 1 else tuple(parents)
        print(f"RECHAINED: {path}: down_revision -> {target}")

    print(write_sentinel(sentinel_path, heads[0]))
    if not rendered:
        print("NOTE: nothing to re-chain — this branch's migrations already continue from it.")
    print(f"PASS: one alembic head ({heads[0]}), continuing from {find_heads(base_graph)[0]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--versions-dir", default=VERSIONS_DIR)
    parser.add_argument("--rechain", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    # The default --versions-dir, the sentinel beside it and the git refs are all
    # relative to the checkout, not to wherever this was called from.
    os.chdir(REPO_ROOT)

    stale = stale_base_refusal(args.base)
    if stale:
        print(f"FAIL: {stale}")
        print("::error::--base is behind its upstream")
        return 1

    if args.rechain:
        return rechain(args)

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
    print(f"Fix: merge {args.base} into this branch, then run")
    print("  python3 .claude/scripts/check-migration-fork.py --rechain")
    print("which re-points this branch's migrations at its head and moves the sentinel.")
    print("A fork this branch made itself still needs `uv run alembic merge heads` or a split.")
    print("See .claude/rules/migrations.md.")
    print(f"::error::merging would fork the alembic chain into {len(heads)} heads")
    return 1


def self_test() -> int:
    """Prove the parser, the head detection and the re-chain agree with reality."""
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

    # --- the re-chain's source splice ---------------------------------------
    linear = (
        '"""add widget\n\n'
        "Revision ID: n1\n"
        "Revises: old\n"
        "Create Date: 2026-01-01\n"
        '"""\n'
        "from alembic import op\n"
        "\n"
        'revision: str = "n1"\n'
        'down_revision: str | None = "old"\n'
        "branch_labels = None\n"
    )
    # Expected built from the input so a re-render that drops the docstring, the
    # import or the blank line fails here rather than in a real migration.
    check(
        "splice changes the value and nothing around it",
        rewrite_down_revision(linear, ["new"]),
        linear.replace('"old"', "'new'"),
    )
    check(
        "a merge revision's tuple becomes one parent",
        rewrite_down_revision('revision = "m"\ndown_revision = ("a", "b")\nbranch_labels = None\n', ["new"]),
        'revision = "m"\ndown_revision = \'new\'\nbranch_labels = None\n',
    )
    check(
        "one parent becomes a tuple",
        rewrite_down_revision('revision = "n"\ndown_revision = "old"\n', ["a", "b"]),
        "revision = \"n\"\ndown_revision = ('a', 'b')\n",
    )
    # The shape the one 3-parent merge migration in the tree actually has: a
    # multi-line annotated tuple. Only the value's span is replaced — the
    # annotation stays, and the three lines it covered go with it.
    check(
        "a multi-line tuple collapses onto the assignment's first line",
        rewrite_down_revision(
            'revision = "m"\n'
            "down_revision: str | Sequence[str] | None = (\n"
            '    "24f052347875",\n'
            '    "a7c31f92e6d0",\n'
            '    "f2d7c410a9e3",\n'
            ")\n"
            "branch_labels = None\n",
            ["new"],
        ),
        'revision = "m"\n'
        "down_revision: str | Sequence[str] | None = 'new'\n"
        "branch_labels = None\n",
    )
    check(
        "a base revision can be given a parent",
        rewrite_down_revision('revision = "n"\ndown_revision = None\n', ["a"]),
        "revision = \"n\"\ndown_revision = 'a'\n",
    )
    check(
        "no down_revision is reported, not guessed",
        rewrite_down_revision('revision = "n"\nbranch_labels = None\n', ["a"]),
        None,
    )
    check("unparsable source is reported", rewrite_down_revision("<<<<<<< HEAD\n", ["a"]), None)

    # --- which parents move -------------------------------------------------
    base = {"a": [], "b": ["a"]}
    fork = {"a": [], "b": ["a"], "n": ["a"]}
    check("a stale branch re-chains onto the base head", rechain_plan(fork, base), ({"n": ["b"]}, ""))
    check(
        "the branch's own chain keeps its link",
        rechain_plan({"a": [], "b": ["a"], "n1": ["a"], "n2": ["n1"]}, base),
        ({"n1": ["b"]}, ""),
    )
    check(
        "a branch merge migration drops only its base parent",
        rechain_plan({"a": [], "b": ["a"], "n": ["a"], "m": ["n", "b"]}, base),
        ({"n": ["b"]}, ""),
    )
    check(
        "two base parents collapse onto one",
        rechain_plan({"a": [], "b": ["a"], "m": ["a", "b"]}, base),
        ({"m": ["b"]}, ""),
    )
    check("a branch with no migration of its own is a no-op", rechain_plan(base, base), ({}, ""))
    # The counts, not just the refusals: a re-chain that moved something it
    # should not would come out as "0 heads" or a cycle here.
    check(
        "the re-chained chain has exactly one head",
        find_heads({**base, **fork, **rechain_plan(fork, base)[0]}),
        ["n"],
    )
    check(
        "and so does a re-chained branch of two",
        find_heads({**base, **{"a": [], "b": ["a"], "n1": ["a"], "n2": ["n1"]}, **rechain_plan({"a": [], "b": ["a"], "n1": ["a"], "n2": ["n1"]}, base)[0]}),
        ["n2"],
    )

    own_fork = rechain_plan({"a": [], "b": ["a"], "n1": ["a"], "n2": ["a"]}, base)
    check("this branch's own fork is refused", own_fork[0], None)
    check("... naming both of its heads", "n1, n2" in own_fork[1], True)

    unmerged = rechain_plan({"a": [], "n": ["a"]}, base)
    check("a branch that has not merged the base is refused", unmerged[0], None)
    check("... and told to merge it first", "merge" in unmerged[1] and "first" in unmerged[1], True)

    two_headed_base = rechain_plan({"a": [], "n": ["a"]}, {"a": [], "b": ["a"], "c": ["a"]})
    check("a base side with two heads is refused", two_headed_base[0], None)
    check("... naming the count", "2 heads" in two_headed_base[1], True)

    empty_base = rechain_plan({"a": [], "n": ["a"]}, {})
    check("an empty base side is refused", empty_base[0], None)

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}")
    if failures:
        return 1
    print(
        "PASS: check-migration-fork self-test (parser variants, merge parents, head "
        "detection, re-chain splice and re-chain decisions)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
