"""后台模型管理页的写操作审计表。

`gateway_admin_audit` 是「谁对网关做过什么」的唯一记录 —— 网关自己只留最后一次的
现状，不记改动者，而模型页上的删/停/改预算都能影响别人正在用的路由，所以平台必须
自己留一份。模型见 `app/domain/agent/models.py::GatewayAdminAudit`。

Revision ID: d4c1a7f83b96
Revises: 8c9ea105b7d2
"""

import sqlalchemy as sa

from alembic import op

revision = "d4c1a7f83b96"
down_revision = "8c9ea105b7d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_admin_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_handle", sa.String(64), nullable=False),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # 后台只按时间倒序取最近 N 条，索引就照这个读法建成倒序。
    op.create_index(
        "ix_gateway_admin_audit_created_at",
        "gateway_admin_audit",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_gateway_admin_audit_created_at", table_name="gateway_admin_audit")
    op.drop_table("gateway_admin_audit")
