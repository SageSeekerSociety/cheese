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
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

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
    paths = {(m, p) for m, p, _ in before}
    handlers = {(m, e) for m, _, e in before}
    new = [
        (m, p, e) for m, p, e in now if (m, p) not in paths and (m, e) not in handlers
    ]
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


def main() -> int:
    for name in _INHERITED_GIT:
        os.environ.pop(name, None)
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="origin/main")
    try:
        return judge(parser.parse_args().base)
    except Unjudgeable as exc:
        print(f"could not judge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
