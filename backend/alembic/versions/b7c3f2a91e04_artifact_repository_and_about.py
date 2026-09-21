"""产物清单：仓库那一项的标记，以及说清它是什么的那一句话

Revision ID: b7c3f2a91e04
Revises: c1a7e05d4b83
"""

import sqlalchemy as sa

from alembic import op

revision = "b7c3f2a91e04"
down_revision = "c1a7e05d4b83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 两列都给 server_default：这张表上已经有行，而它们对现存的每一行都成立 ——
    # 没有哪一项此前被认成过仓库，也没有哪一项写过那一句话。
    op.add_column(
        "project_artifacts",
        sa.Column(
            "delivers_repository",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "project_artifacts",
        sa.Column("about", sa.String(length=80), nullable=False, server_default=""),
    )
    # 已经在库里的项目也要认得出自己的仓库那一项，否则这条改动只对新项目成立：一
    # 个此前交付过合并的项目，下一次合并会因为「没有哪一项标着是仓库」而按项目名
    # 再建一行 —— 恰好是这条改动要治的那个病，只是晚发作一次。
    #
    # 交出去合并的那些卡指着哪一项，哪一项就是这个项目的仓库；有好几项的（正是出
    # 事的那个项目）取最早的一项，剩下的由人在清单上合并过来 —— 合并会把卡改指到
    # 留下的那一项，而留下的那一项已经标好了。
    op.execute(
        """
        UPDATE project_artifacts
        SET delivers_repository = true
        WHERE id IN (
            SELECT DISTINCT ON (a.project_id) a.id
            FROM project_artifacts a
            JOIN accept_cards c ON c.artifact_id = a.id
            WHERE c.deliverable_kind = 'merge'
              AND c.status IN ('accepted', 'pending', 'pending_gate', 'conflict')
            ORDER BY a.project_id, a.created_at
        )
        """
    )


def downgrade() -> None:
    op.drop_column("project_artifacts", "about")
    op.drop_column("project_artifacts", "delivers_repository")
