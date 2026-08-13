"""rename the 2.0 notifications table to alerts

#370, and the one rename in that issue where the 2.0 name was the wrong one.
知是's `notification` is a person telling a person something — a reply, a
reaction, an @. cheesex's is the platform reporting on itself: which machine
went offline, which turn failed, which topic wants a human to look. They shared
the word and were separated by an `s`.

`alert` says the second thing and leaves the first alone.

A rename only — rows, keys and foreign keys survive, and it reverses. There is a
single table here and no relation tables, because this generation points at
projects and topics by uuid rather than through join tables.

Deliberately unmoved: `alerts_pkey` and the indexes PostgreSQL named itself.
Nothing in the code names them, and each extra statement is another way this can
fail on a database whose history differs slightly from ours.

Revision ID: b5d90a17c3e2
Revises: f83b6c2ea174
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b5d90a17c3e2"
down_revision: str | Sequence[str] | None = "f83b6c2ea174"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The 1.0 table is `notification` (singular) and is NOT touched by any of this.
_TABLES = [("notifications", "alerts")]

_INDEXES = [
    ("ix_notifications_project_id", "ix_alerts_project_id"),
    ("ix_notifications_topic_id", "ix_alerts_topic_id"),
    ("ix_notifications_target_handle", "ix_alerts_target_handle"),
]


def _rename(pairs: list[tuple[str, str]], kind: str) -> None:
    for old, new in pairs:
        op.execute(f'ALTER {kind} IF EXISTS "{old}" RENAME TO "{new}"')


def upgrade() -> None:
    _rename(_TABLES, "TABLE")
    _rename(_INDEXES, "INDEX")


def downgrade() -> None:
    _rename([(new, old) for old, new in _INDEXES], "INDEX")
    _rename([(new, old) for old, new in _TABLES], "TABLE")
