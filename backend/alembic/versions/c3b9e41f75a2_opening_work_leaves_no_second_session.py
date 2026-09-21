"""opening work leaves a branch, a card and an owner — and no second session

开一条活留下的是一个分支、一张卡和一个负责人（结论 31）。做它的是房间某条会话里
的一个原生子 agent，用的是父进程那双手（结论 43），所以它既不开第二条会话，也不
另租一份地点。

平台早已不写这样的行 —— 读写两侧每一处都要再写一遍「`task_id IS NULL`」才能把它
们挡在外面。这里把那一列连同它的索引一起删掉：留着的是同一件事的第二份声明，而
声明还在，下一个人写一行按卡建会话的代码时什么也不会响。

`agent_sessions` 里 `task_id IS NOT NULL` 的行先删掉，再建唯一索引：那些行是活还
是一个地点的年代留下的，今天没有任何查询读得到它们（每条查询都带着那句 NULL 判
断），而留着它们会和同房间同 agent 的那条主线行在新的唯一索引上撞车。

`tasks.transcripts_archived_at` 同批退场：它记的是「这条活自己的机器 home 把
transcript 交上来了」，而活没有自己的 home —— 写它的那条路径已经不存在，全仓零个
读点。

Revision ID: c3b9e41f75a2
Revises: a7f2c4d86b13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3b9e41f75a2"
down_revision: str | Sequence[str] | None = "a7f2c4d86b13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM agent_sessions WHERE task_id IS NOT NULL"))
    op.drop_index("uq_agent_sessions_thread", table_name="agent_sessions")
    op.drop_index("uq_agent_sessions_room", table_name="agent_sessions")
    op.drop_column("agent_sessions", "task_id")
    op.create_index(
        "uq_agent_sessions_room",
        "agent_sessions",
        ["topic_id", "agent_handle", "harness"],
        unique=True,
    )
    op.drop_column("tasks", "transcripts_archived_at")


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("transcripts_archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_index("uq_agent_sessions_room", table_name="agent_sessions")
    op.add_column(
        "agent_sessions",
        sa.Column("task_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_sessions_task_id",
        "agent_sessions",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_agent_sessions_task_id", "agent_sessions", ["task_id"])
    op.create_index(
        "uq_agent_sessions_room",
        "agent_sessions",
        ["topic_id", "agent_handle", "harness"],
        unique=True,
        postgresql_where=sa.text("task_id IS NULL"),
    )
    op.create_index(
        "uq_agent_sessions_thread",
        "agent_sessions",
        ["task_id", "agent_handle", "harness"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
