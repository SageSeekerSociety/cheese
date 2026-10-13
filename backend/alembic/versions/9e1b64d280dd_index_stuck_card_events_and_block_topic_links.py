"""blocks: index the events that hand a card to an agent, and links to a room

Revision ID: 9e1b64d280dd
Revises: 84a4c735db35
Create Date: 2026-10-08

`MemberWaits` reads, for every room whose card is stuck, the newest event that
handed the card to an agent. On dev (2026-10-08, 480k blocks) that read went
through the whole table in 180 ms on every `GET /topics` that had a stuck
card; the rows it looks for are a few hundred. The predicate is spelled out here
as the snapshot a migration must be; the live copy is
`app.domain.block.indexed_rows.AGENT_CHECK_ROWS`, and
`tests/integration/test_indexed_rows.py` fails when the two drift apart.

`blocks.upgraded_to_topic_id` points at `topics` with ON DELETE SET NULL, and
had no index: every room deleted read every block to find the ones linking to
it. Partial, because almost no block carries one.

Built CONCURRENTLY for the same reason as e5b1c7d29f04; an invalid index left by
a failed concurrent build is dropped before the build is retried.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9e1b64d280dd"
down_revision: str | Sequence[str] | None = "84a4c735db35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENT_CHECKS = "ix_blocks_agent_checks"
AGENT_CHECKS_WHERE = (
    "(meta ->> 'event_type') IN ('pr_review', 'pr_conflict', 'ci_failed',"
    " 'gate_failed', 'gate_blocked', 'gate_abandoned', 'merge_refused',"
    " 'accept_conflict', 'upstream_conflict', 'migration_collision',"
    " 'card_rejected')"
)
TOPIC_LINKS = "ix_blocks_upgraded_to_topic_id"


def _invalid(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": name},
        )
        .scalar()
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name in (AGENT_CHECKS, TOPIC_LINKS):
            if _invalid(name):
                op.drop_index(name, table_name="blocks", postgresql_concurrently=True)
        op.create_index(
            AGENT_CHECKS,
            "blocks",
            ["conversation_id", "created_at"],
            postgresql_where=sa.text(AGENT_CHECKS_WHERE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            TOPIC_LINKS,
            "blocks",
            ["upgraded_to_topic_id"],
            postgresql_where=sa.text("upgraded_to_topic_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in (AGENT_CHECKS, TOPIC_LINKS):
            op.drop_index(
                name, table_name="blocks", postgresql_concurrently=True, if_exists=True
            )
