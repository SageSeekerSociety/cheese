"""``migration_helpers`` emits the same SQL every migration that calls it ran.

Migrations already on main call ``with_lock_retries``; a fresh database replays
them through whatever this module says today. Its SQL was checked byte for
byte against the 13 copies it replaced (2026-10-07), and is pinned here so it
does not change under them. New behaviour goes behind a new parameter.
"""

import hashlib
import sys
from pathlib import Path

import pytest

ALEMBIC = Path(__file__).resolve().parents[2] / "alembic"
sys.path.insert(0, str(ALEMBIC))
from migration_helpers import lock_retries_sql  # noqa: E402


@pytest.mark.parametrize(
    ("nowait", "digest"),
    [
        (False, "0cff0d834d598f24e0b1acdb24a6216a10f3e7c050663a04fc8eaa85aa4fd367"),
        (True, "315956a3678241432f8f485334552b5dcf46068e53b9300e4180a638b1b06e41"),
    ],
)
def test_lock_retry_sql_is_unchanged(nowait: bool, digest: str) -> None:
    sql = lock_retries_sql("{tables}", nowait=nowait)
    assert hashlib.sha256(sql.encode()).hexdigest() == digest


def test_no_migration_carries_its_own_lock_loop() -> None:
    copies = [
        path.name
        for path in (ALEMBIC / "versions").glob("*.py")
        if "lock_not_available" in path.read_text(encoding="utf-8")
    ]
    assert copies == [], "call migration_helpers.with_lock_retries instead"
