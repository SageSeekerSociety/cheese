"""卡自己记简报和结论，结论卡整张表退役

Revision ID: a1c4e7b09f36
Revises: e4a92b1c7d30
Create Date: 2026-09-06 18:30:00.000000

一件活是房间时间线上的一张卡 (#713)。卡要记的东西里少了两样：派它出去时说的
那份简报，和分身交回来的那句结论。以前简报存在一份"活自己的实况文档"里——而活
是房间会话里的一个分身，拿的是房间的 token，够不着那个文档地址，所以那份文档从
播种那一刻起就再没人改过。结论存在 `conclusion_cards` 里，那张表连同它的默认
采信、补证据、升级和 60 秒扫描一起退役：分身的结果原生回到房间芝士手里，中间
不需要再有一张卡替它们传话。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4e7b09f36"
down_revision: str | Sequence[str] | None = "e4a92b1c7d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("brief", sa.Text(), server_default="", nullable=False),
    )
    op.add_column("tasks", sa.Column("conclusion", sa.Text(), nullable=True))
    # 存量结论搬到它说的那条活上：卡没了，但它记下的那句话还是那条活的结论。
    op.execute(
        """
        UPDATE tasks
           SET conclusion = c.conclusion
          FROM conclusion_cards AS c
         WHERE c.task_id = tasks.id
           AND c.conclusion <> ''
        """
    )
    # 索引和外键随表一起走，不用逐条 drop。
    op.drop_table("conclusion_cards")


def downgrade() -> None:
    op.create_table(
        "conclusion_cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("receiver_topic_id", sa.Uuid(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "accepted",
                "returned",
                "escalated",
                "superseded",
                name="conclusionstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("settled_by", sa.Text(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settle_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("blocking_ref", sa.Text(), nullable=True),
        sa.Column("returned_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("digest_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["receiver_topic_id"], ["topics.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conclusion_cards_topic_status",
        "conclusion_cards",
        ["topic_id", "status"],
    )
    op.create_index(
        "ix_conclusion_cards_receiver_status",
        "conclusion_cards",
        ["receiver_topic_id", "status"],
    )
    op.create_index("ix_conclusion_cards_task_id", "conclusion_cards", ["task_id"])
    op.create_index(
        op.f("ix_conclusion_cards_project_id"), "conclusion_cards", ["project_id"]
    )
    op.create_index(
        op.f("ix_conclusion_cards_topic_id"), "conclusion_cards", ["topic_id"]
    )
    op.create_index(
        op.f("ix_conclusion_cards_receiver_topic_id"),
        "conclusion_cards",
        ["receiver_topic_id"],
    )
    # 卡上的结论回不来：它们已经在活自己那一列上了，而一张没有收方、没有截止
    # 时间的历史卡是造出来的，不是恢复出来的。
    op.drop_column("tasks", "conclusion")
    op.drop_column("tasks", "brief")
