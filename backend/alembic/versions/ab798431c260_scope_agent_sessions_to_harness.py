"""Keep each harness's resume token under its own session key."""

import sqlalchemy as sa

from alembic import op

revision = "ab798431c260"
down_revision = "6c42a790e158"
branch_labels = None
depends_on = None


def _indexes(include_harness: bool) -> None:
    for name, place, predicate in (
        ("uq_agent_sessions_room", "topic_id", "task_id IS NULL"),
        ("uq_agent_sessions_thread", "task_id", "task_id IS NOT NULL"),
    ):
        op.drop_index(name, table_name="agent_sessions")
        columns = [place, "agent_handle"]
        if include_harness:
            columns.append("harness")
        op.create_index(
            name,
            "agent_sessions",
            columns,
            unique=True,
            postgresql_where=sa.text(predicate),
        )


def upgrade() -> None:
    # Every session written before harness selection was a Claude Code session.
    op.add_column(
        "agent_sessions",
        sa.Column(
            "harness",
            sa.String(64),
            nullable=False,
            server_default="claude-code",
        ),
    )
    _indexes(True)


def downgrade() -> None:
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM agent_sessions "
                "GROUP BY topic_id, task_id, agent_handle HAVING count(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicate:
        raise RuntimeError("Cannot discard separate harness histories during downgrade")
    _indexes(False)
    op.drop_column("agent_sessions", "harness")
