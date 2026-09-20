"""the project's artifact manifest, and what a delivery declares into it

Revision ID: b4e7a1c95d33
Revises: b7c2e91f4a03
Create Date: 2026-09-20 15:00:00

一个项目做出来的东西，一项一行（#1085 结论二、三）：名字就是身份。清单由交付长出
来，所以这张表没有「新建」入口 —— 递卡时点名的名字不在表里就当场长出一项，而
`accept_cards.artifact_id` 记下那张卡声明的是哪一项。当前版本不存在这张表上：一版
是一次交付，所以它就是采纳了的、点名这一项的卡的条数。

清单之前的卡这一列为空，而且留空：那些交付确实发生过，但没人说过它们更新的是哪一
项，事后替它们猜一个名字就是往已经落地的历史上安一个声明。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4e7a1c95d33"
down_revision: str | Sequence[str] | None = "b7c2e91f4a03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_project_artifact_name"),
    )
    op.create_index(
        "ix_project_artifacts_project_id", "project_artifacts", ["project_id"]
    )
    op.add_column(
        "accept_cards",
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_accept_cards_artifact_id", "accept_cards", ["artifact_id"])
    op.create_foreign_key(
        "fk_accept_cards_artifact_id",
        "accept_cards",
        "project_artifacts",
        ["artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_accept_cards_artifact_id", "accept_cards", type_="foreignkey"
    )
    op.drop_index("ix_accept_cards_artifact_id", table_name="accept_cards")
    op.drop_column("accept_cards", "artifact_id")
    op.drop_index("ix_project_artifacts_project_id", table_name="project_artifacts")
    op.drop_table("project_artifacts")
