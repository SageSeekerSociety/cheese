"""平台侧的执行记录：发出去过什么，哪些结果未知

Revision ID: b9e4c17d0a52
Revises: e3a7c15d80b4
Create Date: 2026-09-21 09:00:00

结论 57。执行器自己已经记了一份同样的东西 —— ``requests`` 表里 ``result`` 为空的
那一行 —— 但它记在那台机器上，而这份记录存在的理由正是那台机器可能已经没了。

纯建表，没有回填。dev 不停机：旧镜像不认识这张表，也不会往里写；它写不出的那些行
的含义恰好是「结果未知」，也就是这张表为空时重派路径读到的答案，和今天一样。

部分索引只盖 ``outcome IS NULL``：重派路径唯一的问题是「这个房间还有没有悬着的
调用」，而已经结清的行只有出事之后有人回头查时才会被读。
"""

import sqlalchemy as sa

from alembic import op

revision = "b9e4c17d0a52"
down_revision = "e3a7c15d80b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dispatches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column(
            "place_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool", sa.String(128), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dispatches_key", "dispatches", ["key"])
    op.create_index("ix_dispatches_place_id", "dispatches", ["place_id"])
    op.create_index(
        "ix_dispatches_unsettled",
        "dispatches",
        ["place_id"],
        postgresql_where=sa.text("outcome IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_dispatches_unsettled", table_name="dispatches")
    op.drop_index("ix_dispatches_place_id", table_name="dispatches")
    op.drop_index("ix_dispatches_key", table_name="dispatches")
    op.drop_table("dispatches")
