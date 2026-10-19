"""A merge commit nothing ran on has no post-merge signal

Revision ID: 3f8d2c6a1b90
Revises: 7a64b5d89c12
Create Date: 2026-10-19

The landing watch judges the checks that ran on a merge commit. A repository
whose checks run on pull requests only has none there, and the watch now says
so (`unchecked`) instead of waiting out the watch. The CHECK on
`task_landings.checks` gains the value. The previous release never writes it,
so it keeps working on either side of this migration.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "3f8d2c6a1b90"
down_revision: str | Sequence[str] | None = "7a64b5d89c12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_task_landings_checks"
_BEFORE = "'watching', 'passed', 'failed', 'preexisting', 'timeout', 'unavailable'"


def _replace(values: str) -> None:
    with_lock_retries("task_landings")
    op.drop_constraint(_NAME, "task_landings", type_="check")
    op.create_check_constraint(
        _NAME,
        "task_landings",
        f"checks IN ({values})",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE task_landings VALIDATE CONSTRAINT {_NAME}")


def upgrade() -> None:
    _replace(f"{_BEFORE}, 'unchecked'")


def downgrade() -> None:
    # The nearest earlier word for "the forge has nothing to say".
    op.execute(
        "UPDATE task_landings SET checks = 'unavailable' WHERE checks = 'unchecked'"
    )
    _replace(_BEFORE)
