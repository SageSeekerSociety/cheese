"""记忆整理 dreaming: memory_dreams + retirable memory entries

issue #187 step 4. 芝士 reorganizes a topic's memory pools before the sandbox is
destroyed. Two schema needs come out of that:

- `memory_dreams`: one row per pass, opened when the pass STARTS. It is what
  stops the reaper from firing a second pass at a topic it already organized —
  including when the first pass failed, which is the case a "did it produce
  anything" test would miss.
- `memory_entries.retired_at/retired_by/created_by`: the pass never deletes. A
  retired row stops being recalled but stays on disk, and the two id columns
  record which pass moved it, so one pass can be undone as a unit.

Additive and reversible: downgrade drops the columns and the table, leaving the
entries exactly as they were (retired ones simply become visible again).

Revision ID: b1d47f0a3c25
Revises: d5a2f70c9b18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1d47f0a3c25"
down_revision: str | Sequence[str] | None = "d5a2f70c9b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "memory_dreams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_memory_dreams_topic_id"), "memory_dreams", ["topic_id"], unique=False
    )
    op.create_index(
        op.f("ix_memory_dreams_project_id"),
        "memory_dreams",
        ["project_id"],
        unique=False,
    )

    op.add_column(
        "memory_entries",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("memory_entries", sa.Column("retired_by", sa.Uuid(), nullable=True))
    op.add_column("memory_entries", sa.Column("created_by", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_memory_entries_retired_at"),
        "memory_entries",
        ["retired_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_memory_entries_retired_by"),
        "memory_entries",
        ["retired_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_memory_entries_created_by"),
        "memory_entries",
        ["created_by"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_memory_entries_retired_by_memory_dreams",
        "memory_entries",
        "memory_dreams",
        ["retired_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_memory_entries_created_by_memory_dreams",
        "memory_entries",
        "memory_dreams",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "fk_memory_entries_created_by_memory_dreams",
        "memory_entries",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_memory_entries_retired_by_memory_dreams",
        "memory_entries",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_memory_entries_created_by"), table_name="memory_entries")
    op.drop_index(op.f("ix_memory_entries_retired_by"), table_name="memory_entries")
    op.drop_index(op.f("ix_memory_entries_retired_at"), table_name="memory_entries")
    op.drop_column("memory_entries", "created_by")
    op.drop_column("memory_entries", "retired_by")
    op.drop_column("memory_entries", "retired_at")

    op.drop_index(op.f("ix_memory_dreams_project_id"), table_name="memory_dreams")
    op.drop_index(op.f("ix_memory_dreams_topic_id"), table_name="memory_dreams")
    op.drop_table("memory_dreams")
