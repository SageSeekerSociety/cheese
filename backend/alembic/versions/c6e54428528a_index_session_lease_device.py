"""agent_sessions: index the device a session's lease is on

Revision ID: c6e54428528a
Revises: c20dfc7a2e2f

A sandbox asks the platform through its device link, and every such request
(`/machine/api/...`, `/machine/llm/...`) first finds the session holding
that sandbox: the sessions whose `work_lease ->> 'device_id'` is the device
the request came in on (`machine.caller.caller_of`). The idle reaper asks the
same of each host it looks at. Nothing indexes that expression, so each of
these reads every row of `agent_sessions`. Measured locally on 60,000
sessions: a sequential scan removing all 60,000 rows to keep one, per request.

An index on the expression answers it from the few sessions on that device.

Built CONCURRENTLY: `agent_sessions` is written on every session placement.
An invalid index left by a failed concurrent build is dropped before the retry.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c6e54428528a"
down_revision: str | Sequence[str] | None = "c20dfc7a2e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "ix_agent_sessions_lease_device"
DEVICE = "(work_lease ->> 'device_id')"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        valid = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT indisvalid FROM pg_index "
                    "WHERE indexrelid = to_regclass(:index)"
                ),
                {"index": NAME},
            )
            .scalar_one_or_none()
        )
        if valid is False:
            op.drop_index(NAME, postgresql_concurrently=True)
        op.create_index(
            NAME,
            "agent_sessions",
            [sa.text(DEVICE)],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(NAME, postgresql_concurrently=True, if_exists=True)
