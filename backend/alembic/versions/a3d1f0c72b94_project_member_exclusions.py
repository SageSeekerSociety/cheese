"""project_member_exclusions：退出项目是一条记得下来的项目级事实

Revision ID: a3d1f0c72b94
Revises: b380c2e8f60c
Create Date: 2026-09-27 12:00:00

「退出项目」退的是这个项目，不是小队。在它之前「谁在这个项目里」只有两种来路写
得下来：所有者（``projects.owner_handle``）与外部成员（``project_members`` 一
行）；队友不写行，读的时候从 ``team_user_relation`` 继承——于是「在小队里」和
「在这个项目里」是同一句话，队友在项目这边一个能退的按钮都没有，按下去也只会拿
到 409。

这一条建的是第三种事实：``(project_id, user_handle)`` 上一行 = 「他虽在小队里，但
不属于这个项目」。``team_user_relation`` 一个字节都不动，那是另一个事实（他在小队
里的身份、名额、以及该小队名下别的项目都照旧）。键带 ``project_id``，因为同一个
人在 A 项目退出、在 B 项目照常；也正因为要按人查，它不能塞进 ``projects.settings``
那种 JSON blob。

一行数据都不用搬：这张表开局是空的，而空表正是「以前没有谁退出过项目」这件事的真
实写照——以前没有人退得成。所以这里没有存量回填，也没有 ``downgrade`` 要还原的东
西：删掉表就是回到「退不掉」的上一版，语义上干净（存量行只在有人退过之后才有，
而那时这条迁移早就跑过了）。

唯一约束与两个索引照着 ``ProjectMember`` 那张姊妹表的形状写：``(project_id,
user_handle)`` 唯一（同一个事实说一遍就够，写入侧 ``ON CONFLICT DO NOTHING``），
project_id 与 user_handle 各自可查（名册按项目问一次、列表按人问一次）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3d1f0c72b94"
down_revision: str | Sequence[str] | None = "b380c2e8f60c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_member_exclusions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_handle", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "user_handle", name="uq_project_member_exclusion"
        ),
    )
    op.create_index(
        op.f("ix_project_member_exclusions_project_id"),
        "project_member_exclusions",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_member_exclusions_user_handle"),
        "project_member_exclusions",
        ["user_handle"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_project_member_exclusions_user_handle"),
        table_name="project_member_exclusions",
    )
    op.drop_index(
        op.f("ix_project_member_exclusions_project_id"),
        table_name="project_member_exclusions",
    )
    op.drop_table("project_member_exclusions")
