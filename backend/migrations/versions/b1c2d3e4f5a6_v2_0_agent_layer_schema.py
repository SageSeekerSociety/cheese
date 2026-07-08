"""知是 2.0 agent 层 schema（合并迁移）

Revision ID: b1c2d3e4f5a6
Revises: 6a987e64b29c
Create Date: 2026-07-08 14:00:00.000000

把 2.0 agent 层的五步 schema 变更合并为一个迁移（发版 0.16.0），在 substrate
(6a987e64b29c: block/thread/agent_screen/project 等) 之上一次性建立：

1. block 文档树列 (struct_order/node_type/refs) + 内容全文检索索引
2. project 文档树: `document` 表 + 序列
3. 飞书式消息动作: `message_reaction` 表 + block 软删除(deleted_at)/置顶(pinned_at)
4. 独立项目: project.team_id/start_date/end_date 改为 nullable
5. agent recreate/clone: agent_screen.claude_session_id / cwd

原本对应 7c1d2e3f4a5b / 8d2e3f4a5b6c / 9e3f4a5b6c7d / a0b1c2d3e4f5 / b1c2d3e4f5a6
五个迁移，现合并；revision id 沿用 b1c2d3e4f5a6，故已升级到该 head 的库无需重标。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "6a987e64b29c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REACTION_SEQ = "message_reaction_seq"


def upgrade() -> None:
    # --- 1. block 文档树列 + 全文检索索引 ---
    op.add_column("block", sa.Column("struct_order", sa.Float(), nullable=True))
    op.add_column("block", sa.Column("node_type", sa.String(length=16), nullable=True))
    op.add_column("block", sa.Column("refs", sa.JSON(), nullable=True))
    op.create_index(
        "ix_block_struct_parent_order",
        "block",
        ["struct_parent_id", "struct_order"],
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_block_fts "
        "ON block USING gin (to_tsvector('simple', coalesce(content, '')))"
    )

    # --- 2. document 表 + 序列 ---
    op.execute(sa.schema.CreateSequence(sa.Sequence("document_seq")))
    op.create_table(
        "document",
        sa.Column(
            "id",
            sa.BigInteger(),
            server_default=sa.text("nextval('document_seq')"),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("doc_type", sa.SmallInteger(), nullable=False),
        sa.Column("doc_root_block_id", sa.BigInteger(), nullable=True),
        sa.Column("sort_order", sa.Float(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_project_id"), "document", ["project_id"], unique=False)

    # --- 3. message_reaction 表 + block 软删除/置顶列 ---
    op.add_column("block", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("block", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.schema.CreateSequence(sa.Sequence(_REACTION_SEQ)))
    op.create_table(
        "message_reaction",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            server_default=sa.text(f"nextval('{_REACTION_SEQ}')"),
        ),
        sa.Column("block_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("emoji", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "block_id", "user_id", "emoji", name="uq_reaction_block_user_emoji"
        ),
    )
    op.create_index("ix_reaction_block_id", "message_reaction", ["block_id"])

    # --- 4. project 独立化: 放松 NOT NULL ---
    op.alter_column("project", "team_id", existing_type=None, nullable=True)
    op.alter_column("project", "start_date", existing_type=None, nullable=True)
    op.alter_column("project", "end_date", existing_type=None, nullable=True)

    # --- 5. agent_screen: claude session id + cwd ---
    op.add_column("agent_screen", sa.Column("claude_session_id", sa.String(64), nullable=True))
    op.add_column("agent_screen", sa.Column("cwd", sa.String(1024), nullable=True))


def downgrade() -> None:
    # reverse of upgrade
    op.drop_column("agent_screen", "cwd")
    op.drop_column("agent_screen", "claude_session_id")

    op.alter_column("project", "end_date", existing_type=None, nullable=False)
    op.alter_column("project", "start_date", existing_type=None, nullable=False)
    op.alter_column("project", "team_id", existing_type=None, nullable=False)

    op.drop_index("ix_reaction_block_id", table_name="message_reaction")
    op.drop_table("message_reaction")
    op.execute(sa.schema.DropSequence(sa.Sequence(_REACTION_SEQ)))
    op.drop_column("block", "pinned_at")
    op.drop_column("block", "deleted_at")

    op.drop_index(op.f("ix_document_project_id"), table_name="document")
    op.drop_table("document")
    op.execute("DROP SEQUENCE IF EXISTS document_seq")

    op.execute("DROP INDEX IF EXISTS ix_block_fts")
    op.drop_index("ix_block_struct_parent_order", table_name="block")
    op.drop_column("block", "refs")
    op.drop_column("block", "node_type")
    op.drop_column("block", "struct_order")
