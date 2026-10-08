"""Every place that picks the Postgres CI and local dev test against agrees.

Migrations are proven on one server: ``CI_POSTGRES_MAJOR`` in
``alembic/migration_helpers.py``, and the ParadeDB image the CI workflows pin.
A pin that drifts on its own (a workflow bumped, the resident runner left
behind) means one lane tests a different server than the rest.

Deployed databases are not pinned here. The dev and production boxes take their
database from each box's own ``.env``; ``deploy/docker-compose.prod.yml`` and
``deploy/compose/docker-compose.etrip.yml`` run ``paradedb:v0.18.8-pg16``. What
a deploy actually runs against is logged by ``alembic/env.py`` on every upgrade,
with a warning when its major version is not ``CI_POSTGRES_MAJOR``.
"""

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend" / "alembic"))
from migration_helpers import CI_POSTGRES_MAJOR  # noqa: E402

#: Files that pin the full CI image reference, digest included.
PINNED = (
    ".github/workflows/test.yml",
    ".github/workflows/e2e.yml",
    ".github/workflows/remote-execution.yml",
    "deploy/ci-runner/resident-services.sh",
    "deploy/deploy-docker.sh",
    "backend/scripts/device_connection_lifecycle_acceptance.py",
)
_IMAGE = re.compile(
    r"mirror\.gcr\.io/paradedb/paradedb:(v[\d.]+-pg(\d+))@sha256:[0-9a-f]{64}"
)


def _images(path: str) -> list[re.Match[str]]:
    return list(_IMAGE.finditer((REPO / path).read_text(encoding="utf-8")))


def test_ci_pins_one_image() -> None:
    found = {path: {m.group(0) for m in _images(path)} for path in PINNED}
    assert all(found.values()), {p: refs for p, refs in found.items() if not refs}
    distinct = set().union(*found.values())
    assert len(distinct) == 1, found


@pytest.mark.parametrize("path", PINNED)
def test_ci_image_is_the_ci_major(path: str) -> None:
    for match in _images(path):
        assert int(match.group(2)) == CI_POSTGRES_MAJOR, (path, match.group(0))


def test_local_stacks_match_ci() -> None:
    tag = _images(PINNED[0])[0].group(1)  # v0.24.0-pg17
    pg_search = tag.split("-pg")[0].removeprefix("v")
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert set(re.findall(r"paradedb/paradedb:(\S+)", compose)) == {tag}
    dev_db = (REPO / ".claude/scripts/dev-db.sh").read_text(encoding="utf-8")
    release = re.search(r"^PG_RELEASE=(\d+)\.", dev_db, re.M)
    search = re.search(r"^PG_SEARCH_VERSION=(\S+)", dev_db, re.M)
    assert release and int(release.group(1)) == CI_POSTGRES_MAJOR
    assert search and search.group(1) == pg_search
