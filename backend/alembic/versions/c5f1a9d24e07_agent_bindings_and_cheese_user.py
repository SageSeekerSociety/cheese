"""agent_bindings + seed 芝士 as a real agent-user (agent-as-user, fusion-design §2)

Revision ID: c5f1a9d24e07
Revises: a1c9f3e70b21
Create Date: 2026-07-09 00:00:00.000000

An agent is a first-class user whose agent-ness is DERIVED from an execution
binding, never a column. This creates the ``agent_bindings`` table and seeds
芝士 (handle ``cheese``) as a real ``users`` row with one ``platform`` binding —
idempotently, so re-running or a pre-existing cheese row never duplicates.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5f1a9d24e07"
down_revision: Union[str, Sequence[str], None] = "a1c9f3e70b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_agent_binding_user"),
    )
    op.create_index(
        op.f("ix_agent_bindings_user_id"), "agent_bindings", ["user_id"], unique=True
    )

    # --- Seed 芝士 as a real agent-user (idempotent) ---
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT id FROM users WHERE handle = :h"), {"h": "cheese"}
    ).first()
    if row is None:
        user_id = uuid.uuid4()
        # JSON columns written as SQL literals to avoid driver-specific codecs.
        conn.execute(
            sa.text(
                "INSERT INTO users "
                "(id, handle, name, email, bio, interests, skills, "
                " created_at, updated_at) "
                "VALUES (:id, :handle, :name, NULL, '', "
                " '[]'::json, '[]'::json, now(), now())"
            ),
            {"id": user_id, "handle": "cheese", "name": "芝士"},
        )
    else:
        user_id = row[0]

    has_binding = conn.execute(
        sa.text("SELECT 1 FROM agent_bindings WHERE user_id = :u"), {"u": user_id}
    ).first()
    if has_binding is None:
        conn.execute(
            sa.text(
                "INSERT INTO agent_bindings "
                "(id, user_id, kind, created_at, updated_at) "
                "VALUES (:id, :u, 'platform', now(), now())"
            ),
            {"id": uuid.uuid4(), "u": user_id},
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_bindings_user_id"), table_name="agent_bindings")
    op.drop_table("agent_bindings")
