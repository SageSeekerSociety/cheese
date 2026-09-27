"""记忆整理（dream）的两行账

Revision ID: d4b8f1a20c37
Revises: c1a7e4d29b58
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b8f1a20c37"
down_revision: str | Sequence[str] | None = "c1a7e4d29b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    后台整理记忆要记两件事，一张表一件：

    - ``memory_dream_states``：一个项目一行，「上次整理是什么时候」和「现在有没有
      人在整理」。**一行**而不是每次整理一行，是因为那把锁必须是一个能被
      `FOR UPDATE` 锁住的东西（见 `domain/memory/dream.claim`）：两次整理同时起
      来只会互相覆盖，而插入一张日志表是拦不住第二个进程的。项目一行的唯一约
      束就是「一个项目一把锁」这句话在数据库里的样子。
    - ``memory_dream_runs``：每次整理一行（跑成了、拒了、还是没跑成）。它是**给人
      看的**：一次被拒绝的整理什么都没写，而那件事只能从这里读出来。

    两行账分开，是因为它们的寿命不一样：状态只有当前值有意义（旧值会被覆盖），
    记录要留一段时间给人查。
    """
    op.create_table(
        "memory_dream_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("last_dream_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # 「一个项目一把锁」：唯一约束是这把锁的实体，`claim` 的 upsert 也靠它。
    op.create_index(
        "uq_memory_dream_states_project",
        "memory_dream_states",
        ["project_id"],
        unique=True,
    )
    op.create_table(
        "memory_dream_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="running"
        ),
        sa.Column(
            "tokens_at_start", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("files_changed", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_dream_runs_project_id", "memory_dream_runs", ["project_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_memory_dream_runs_project_id", table_name="memory_dream_runs")
    op.drop_table("memory_dream_runs")
    op.drop_index("uq_memory_dream_states_project", table_name="memory_dream_states")
    op.drop_table("memory_dream_states")
