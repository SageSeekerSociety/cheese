#!/usr/bin/env python3
"""Gate: no new write route declares ``ROUTE_DECIDES``.

    cd backend && uv run python scripts/check_route_decides.py
    cd backend && uv run python scripts/check_route_decides.py --base origin/main

    0 = no write route declares ROUTE_DECIDES that the base tree did not
    1 = one does       2 = could not judge

A write route says who may call it (``app/api/write_access.py``).
``ROUTE_DECIDES`` says only that its handler decides, which is how routes were
written before ``Can(permission)`` could judge every caller — a person, their
芝士, a project's AI teammates. The routes that still declare it move onto
``Can`` one batch at a time; a route added now declares what it needs.

The baseline is the tree at ``git merge-base HEAD <base>``, measured each run
the way ``check_boundaries.py`` measures its own: nothing is stored, so no pull
request edits a list of exemptions. Both trees are loaded as the app and asked
for their write routes (``write_access.write_routes``). A route the base tree
already had, under the same method and path or the same handler, is that
tree's to keep; a route this tree adds is the verdict.

A change that renames a route's path *and* its handler in one go leaves the
route matched by neither name. It is still the base tree's route, and the run
prints both counts so a reader can see the population did not grow; the two
leftovers are paired by method and shape instead (:func:`_introduced`). Same
method and same number of path parameters, one for one: a route added beside
one removed is that swap, a route left over is this tree's own.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: What a path leaves for the router to fill: ``{block_id}`` names one.
_PARAM = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)(?::[a-zA-Z_][a-zA-Z0-9_]*)?}")

#: Printed by a fresh interpreter for one tree: its write routes that declare
#: ROUTE_DECIDES, as [method, path, endpoint].
_LIST = """
import json
from app.main import app
from app.api.write_access import write_routes
print(json.dumps(sorted(
    [r.method, r.path, r.endpoint]
    for r in write_routes(app)
    if r.declared == ("route_decides",)
)))
"""


class Unjudgeable(Exception):
    pass


def _routes(tree: Path) -> list[list[str]]:
    env = {**os.environ, "PYTHONPATH": str(tree), "ENVIRONMENT": "test"}
    done = subprocess.run(
        [sys.executable, "-c", _LIST],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
    )
    lines = done.stdout.strip().splitlines()
    if done.returncode or not lines:
        why = (done.stderr or done.stdout).strip().splitlines()
        raise Unjudgeable(
            f"cannot load the app at {tree}" + (f": {why[-1]}" if why else "")
        )
    return json.loads(lines[-1])


def _base_tree(rev: str, dest: Path) -> Path:
    """The backend at ``rev``, unpacked under ``dest`` with the sentence
    catalogs the app reads at import (``core.sentences``), by ``git archive``
    as ``check_boundaries._base_tree`` does, so concurrent checkouts leave no
    worktree entry behind."""
    root = subprocess.run(
        ["git", "-C", str(BACKEND), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    prefix = BACKEND.relative_to(root)
    archive = subprocess.run(
        [
            "git",
            "-C",
            root,
            "archive",
            "--format=tar",
            rev,
            str(prefix),
            "frontend/src/i18n/messages",
        ],
        capture_output=True,
    )
    if archive.returncode:
        raise Unjudgeable(f"cannot read {prefix} at {rev}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return dest / prefix


def _renamed_one_for_one(p: str, q: str) -> bool:
    """Whether two paths differ only in the words that are not a parameter:
    same parameter names in the same places, same number of segments. The
    rename this pairs is ``/blocks/{block_id}/answer`` -> ``/blocks/{block_id}/skip``;
    a route with a differently shaped path is not the same route."""
    return _params(p) == _params(q) and len(p.strip("/").split("/")) == len(
        q.strip("/").split("/")
    )


def _params(path: str) -> tuple[str, ...]:
    return tuple(_PARAM.findall(path))


def _introduced(before: list[list[str]], now: list[list[str]]) -> list[list[str]]:
    """The routes among ``now`` that the base tree did not have.

    A route is the base's under the same method and path, or under the same
    method and handler. What neither name matches is paired by method and
    shape: same method, same path parameters, one for one. A route renamed in
    path *and* handler at once is that pair — the counts a run prints say
    whether the population actually grew — and a route left over once the
    pairs are spent is this tree's own. A pair whose halves do not line up is
    two routes for the verdict: one removed, one added.
    """
    paths, handlers = {(m, p) for m, p, _ in before}, {(m, e) for m, _, e in before}
    left = [
        r for r in now if (r[0], r[1]) not in paths and (r[0], r[2]) not in handlers
    ]
    gone = sorted(
        r
        for r in before
        if (r[0], r[1]) not in {(m, p) for m, p, _ in now}
        and (r[0], r[2]) not in {(m, e) for m, _, e in now}
    )
    spent: set[int] = set()
    added: list[list[str]] = []
    for wanted in sorted(left):
        fits = next(
            (
                i
                for i, (method, path, _) in enumerate(gone)
                if i not in spent
                and method == wanted[0]
                and _renamed_one_for_one(path, wanted[1])
            ),
            None,
        )
        if fits is None:
            added.append(wanted)
        else:
            spent.add(fits)
    return added


def judge(base: str) -> int:
    merged = subprocess.run(
        ["git", "-C", str(BACKEND), "merge-base", "HEAD", base],
        capture_output=True,
        text=True,
    )
    if merged.returncode or not merged.stdout.strip():
        raise Unjudgeable(f"no merge base with {base}")
    now = _routes(BACKEND)
    with tempfile.TemporaryDirectory(prefix="route-decides-") as tmp:
        before = _routes(_base_tree(merged.stdout.strip(), Path(tmp)))
    new = _introduced(before, now)
    print(
        f"ROUTE_DECIDES write routes: {len(before)} at the merge base, {len(now)} now"
    )
    if not new:
        return 0
    print("\nNew write routes declaring ROUTE_DECIDES:")
    for m, p, e in new:
        print(f"  {m} {p} ({e})")
    print(
        "\nDeclare what the route needs instead: Can(Permission.<x>) for a team,"
        " space, problem, question, project or channel (app/api/write_access.py)."
    )
    return 1


#: What git hands a hook about the repository it runs for: with GIT_DIR set
#: and no GIT_WORK_TREE, git takes backend/ (where the hook cds) for the top of
#: the work tree and `git archive` finds no backend/ at the merge base. As
#: `check_boundaries.py` does, the checkout is found by this file's own path.
_INHERITED_GIT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


#: What the verdict must answer, decided on written-out route lists: the pairs
#: a reader of this file would argue about. The third entry is the route's
#: handler, which a rename changes with the path; only the route that the base
#: tree never had may come back as new.
_CASES: tuple[tuple[str, list[list[str]], list[list[str]], list[str]], ...] = (
    (
        "a route renamed in path and handler at once is the base's",
        [
            [
                "DELETE",
                "/blocks/{block_id}/answer",
                "app.api.routes.blocks.withdraw_answer",
            ]
        ],
        [["DELETE", "/blocks/{block_id}/skip", "app.api.routes.blocks.undo_skip"]],
        [],
    ),
    (
        "a route left alone is the base's",
        [["POST", "/blocks/{block_id}/skip", "app.api.routes.blocks.skip_question"]],
        [["POST", "/blocks/{block_id}/skip", "app.api.routes.blocks.skip_question"]],
        [],
    ),
    (
        "a route the base tree never had is new",
        [],
        [
            [
                "POST",
                "/projects/{project_id}/probe-ratchet",
                "app.api.routes.projects.probe",
            ]
        ],
        ["POST /projects/{project_id}/probe-ratchet"],
    ),
    (
        "an added route beside an unrelated removal is still new",
        [
            [
                "DELETE",
                "/blocks/{block_id}/answer",
                "app.api.routes.blocks.withdraw_answer",
            ]
        ],
        [
            ["DELETE", "/blocks/{block_id}/skip", "app.api.routes.blocks.undo_skip"],
            [
                "POST",
                "/projects/{project_id}/probe-ratchet",
                "app.api.routes.projects.probe",
            ],
        ],
        ["POST /projects/{project_id}/probe-ratchet"],
    ),
    (
        "a path that kept its shape but not its parameters is two routes",
        [
            [
                "DELETE",
                "/blocks/{block_id}/answer",
                "app.api.routes.blocks.withdraw_answer",
            ]
        ],
        [["DELETE", "/blocks/{answer_id}/skip", "app.api.routes.blocks.undo_skip"]],
        ["DELETE /blocks/{answer_id}/skip"],
    ),
)


def self_test() -> int:
    """Each case above, against :func:`_introduced`.

    A guard that quietly stopped matching looks exactly like a clean tree,
    which is why every guard in this repository proves it still fires. This one
    decides the cases on lists written here, so it needs no app and no git.
    """
    failures: list[str] = []
    for description, before, now, want in _CASES:
        got = [f"{m} {p}" for m, p, _ in _introduced(before, now)]
        if got != want:
            failures.append(f"  {description}: wanted {want}, got {got}")
    if failures:
        print("FAIL: check_route_decides self-test\n" + "\n".join(failures))
        return 1
    print(f"PASS: check_route_decides self-test ({len(_CASES)} case(s))")
    return 0


def main() -> int:
    for name in _INHERITED_GIT:
        os.environ.pop(name, None)
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the verdict still fires, on written-out route lists",
    )
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    try:
        return judge(args.base)
    except Unjudgeable as exc:
        print(f"could not judge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
