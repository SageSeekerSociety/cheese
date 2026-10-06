"""blocks: partial index for messages still waiting for their turn

Revision ID: c7e2a91f4d3b
Revises: 7d2e9a41c6b3
Create Date: 2026-10-06 03:00:00

The pending-message scan (`pending_messages.queued_messages`) finds every
message that named an agent and has not had its turn. It used to run only when
something nudged it, and now also runs on a clock, because a nudge that finds
the seat still busy is never repeated: on 2026-10-05 two messages on dev waited
10.5 and 5.7 minutes past the end of the turn ahead of them, until an unrelated
wake-up or a deploy found them. Without an index it reads the whole table.
Measured on dev (2026-10-06, 440,861 blocks): Parallel Seq Scan, 205 ms,
returning 70 rows of which 52 match this predicate.

The predicate is spelled out here as the snapshot a migration must be; the live
copy is `app.domain.block.indexed_rows.QUEUED_MESSAGE_ROWS`, and
`tests/integration/test_indexed_rows.py` fails when the two drift apart.

Built CONCURRENTLY for the same reason as b42af333ed1d: `blocks` takes an
insert for every message and every line of agent output. An invalid index left
by a failed concurrent build is dropped before the build is retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7e2a91f4d3b"
down_revision: str | Sequence[str] | None = "7d2e9a41c6b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "ix_blocks_queued_messages"
WHERE = (
    "kind = 'message'"
    " AND ((meta -> 'agent_recipient') ->> 'mentioned') = 'true'"
    " AND (meta -> 'consumed_turn') IS NOT NULL"
    " AND (meta ->> 'consumed_turn') IS NULL"
    " AND COALESCE(meta ->> 'prompt_attempts', '0') = '0'"
    " AND (meta -> 'delivery_event_id') IS NULL"
    " AND (meta -> 'answer_to') IS NULL"
)


def _invalid() -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": NAME},
        )
        .scalar()
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        if _invalid():
            op.drop_index(NAME, table_name="blocks", postgresql_concurrently=True)
        op.create_index(
            NAME,
            "blocks",
            ["created_at", "id"],
            postgresql_where=sa.text(WHERE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            NAME, table_name="blocks", postgresql_concurrently=True, if_exists=True
        )
