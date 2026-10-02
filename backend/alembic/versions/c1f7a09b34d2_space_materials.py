"""一块题目板可以有一份共用的资料库 (#944).

Revision ID: c1f7a09b34d2
Revises: d4b7e19a2c55
Create Date: 2026-10-02
"""

import sqlalchemy as sa

from alembic import op

revision = "c1f7a09b34d2"
down_revision = "d4b7e19a2c55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema.

    又是一张关联表，不是新的一套文件存储：字节早就在 ``material`` 里
    （``POST /materials`` 上传，meta 里带 storageKey），这张表只回答「它属于哪块板」
    与「谁能看」。

    挂 ``material`` 而不是 ``attachment``：教学配置的 ``material_ids`` 记的就是
    ``material`` 的 id，题目上勾选参考资料时直接把这里的 ``material_id`` 写进去，
    中间不用做 id 翻译。见 ``app/domain/space/models.py`` 里 ``SpaceMaterial`` 的
    说明。

    ``visibility`` 有两档，存字符串而不是布尔：以后若要多一档（例如「仅发布者」），
    加一个取值就行，不必改列类型再迁移一次数据。
    """
    op.execute(sa.schema.CreateSequence(sa.Sequence("space_material_seq")))

    op.create_table(
        "space_material",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("space_material_seq"),
            nullable=False,
        ),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column(
            "visibility", sa.String(length=20), nullable=False, server_default="members"
        ),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["space_id"], ["space.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["material.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # 同一块板上同一份文件只挂一次。部分索引（deleted_at IS NULL）：撤下来之后再
    # 传回去是允许的，那时它是一条新的生命。
    op.create_index(
        "uq_space_material_live",
        "space_material",
        ["space_id", "material_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # 「这份文件在不在某块板的仅管理员档里」—— 通用读路由 `GET /materials/{id}`
    # 每次都要问一句，按 material_id 找。
    op.create_index("ix_space_material_material_id", "space_material", ["material_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_space_material_material_id", table_name="space_material")
    op.drop_index("uq_space_material_live", table_name="space_material")
    op.drop_table("space_material")
    op.execute(sa.schema.DropSequence(sa.Sequence("space_material_seq")))
