"""A file can belong to a 题目.

Revision ID: a4f2c81d6e03
Revises: 5c1d8e7f2b90
Create Date: 2026-09-26
"""

import sqlalchemy as sa

from alembic import op

revision = "a4f2c81d6e03"
down_revision = "5c1d8e7f2b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema.

    出的是一张关联表，不是新的一套文件存储：文件本身早就在 ``attachment`` 里
    （``POST /attachments`` 上传，meta 里带 filename / contentType / storageKey /
    size / uploaderId）。这张表只回答「它属于哪道题」，以及题目这一侧才会有的
    下载次数与生命期。同一个文件记录被不同上下文引用时语义不同，所以不把
    ``task_id`` 加到 ``attachment`` 上 —— 那样交作业附的那份和出题人放的那份
    就分不开了。
    """
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_attachment_seq")))

    op.create_table(
        "task_attachment",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("task_attachment_seq"),
            nullable=False,
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("attachment_id", sa.Integer(), nullable=False),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachment.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # 同一道题上同一个文件只挂一次。部分索引（deleted_at IS NULL）：拿下来之后
    # 再挂回去是允许的，那时它是一条新的生命。
    op.create_index(
        "uq_task_attachment_live",
        "task_attachment",
        ["task_id", "attachment_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # 「这个文件已经被别的题挂走了吗」—— 建题时按 attachment_id 批量问一次，
    # 挡住拿别人上传的 attachment id（例如别人交作业附的文件）当自己的题目附件。
    op.create_index(
        "ix_task_attachment_attachment_id", "task_attachment", ["attachment_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_task_attachment_attachment_id", table_name="task_attachment")
    op.drop_index("uq_task_attachment_live", table_name="task_attachment")
    op.drop_table("task_attachment")
    op.execute(sa.schema.DropSequence(sa.Sequence("task_attachment_seq")))
