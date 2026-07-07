"""agent chat and connector substrate

Squashed migration for the 知是 2.0 agent layer (chat + connector), collapsing the
device flow, agent screens, the thread/block substrate, project-independent chat and
the read-receipt watermark into one table-creation step. The retired bootstrap
``chat_message`` table is intentionally absent (it was created then dropped across the
original migrations — net zero).

Creates: device / device_auth_code / device_project (connector device flow +
project assignment); agent_screen (agent execution bindings, project nullable);
thread / thread_membership (+ attention_policy_override, last_read_block_id) /
thread_membership_application / block (the 万物皆块 chat + document substrate).

Revision ID: 6a987e64b29c
Revises: f6b7c8d9e0a1
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a987e64b29c"
down_revision: str | Sequence[str] | None = "f6b7c8d9e0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SEQUENCES = (
    "device_project_seq",
    "thread_seq",
    "thread_membership_seq",
    "thread_application_seq",
    "block_seq",
)


def upgrade() -> None:
    for seq in _SEQUENCES:
        op.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq}")

    # -- connector: devices + auth codes + project assignment ------------------
    op.create_table(
        "device",
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index(op.f("ix_device_owner_user_id"), "device", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_device_token"), "device", ["token"], unique=True)

    op.create_table(
        "device_auth_code",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "device_project",
        sa.Column(
            "id",
            sa.BigInteger(),
            server_default=sa.text("nextval('device_project_seq')"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "project_id", name="uq_device_project"),
    )
    op.create_index(
        op.f("ix_device_project_device_id"), "device_project", ["device_id"], unique=False
    )
    op.create_index(
        op.f("ix_device_project_project_id"), "device_project", ["project_id"], unique=False
    )

    # -- agent execution bindings (a screen is an agent) ----------------------
    op.create_table(
        "agent_screen",
        sa.Column("sid", sa.String(length=32), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("agent_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sid"),
    )
    op.create_index(
        op.f("ix_agent_screen_device_id"), "agent_screen", ["device_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_screen_project_id"), "agent_screen", ["project_id"], unique=False
    )

    # -- chat: thread / membership / application / block (万物皆块) ------------
    op.create_table(
        "thread",
        sa.Column(
            "id", sa.BigInteger(), server_default=sa.text("nextval('thread_seq')"), nullable=False
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_thread_project_id"), "thread", ["project_id"], unique=False)

    op.create_table(
        "thread_membership",
        sa.Column(
            "id",
            sa.BigInteger(),
            server_default=sa.text("nextval('thread_membership_seq')"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.SmallInteger(), nullable=False),
        sa.Column("attention_policy_override", sa.String(length=32), nullable=True),
        sa.Column("last_read_block_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["thread.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_thread_membership_thread_user",
        "thread_membership",
        ["thread_id", "user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_thread_membership_user_id"), "thread_membership", ["user_id"], unique=False
    )

    op.create_table(
        "thread_membership_application",
        sa.Column(
            "id",
            sa.BigInteger(),
            server_default=sa.text("nextval('thread_application_seq')"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("initiator_id", sa.BigInteger(), nullable=False),
        sa.Column("approver_id", sa.BigInteger(), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("role", sa.SmallInteger(), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("processed_by_id", sa.BigInteger(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["thread.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_thread_membership_application_approver_id"),
        "thread_membership_application",
        ["approver_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_thread_membership_application_user_id"),
        "thread_membership_application",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "block",
        sa.Column(
            "id", sa.BigInteger(), server_default=sa.text("nextval('block_seq')"), nullable=False
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.SmallInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("reply_to_id", sa.BigInteger(), nullable=True),
        sa.Column("struct_parent_id", sa.BigInteger(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["thread.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_block_project_id"), "block", ["project_id"], unique=False)
    op.create_index("ix_block_thread_id_id", "block", ["thread_id", "id"], unique=False)


def downgrade() -> None:
    op.drop_table("block")
    op.drop_table("thread_membership_application")
    op.drop_table("thread_membership")
    op.drop_table("thread")
    op.drop_table("agent_screen")
    op.drop_table("device_project")
    op.drop_table("device_auth_code")
    op.drop_table("device")
    for seq in _SEQUENCES:
        op.execute(f"DROP SEQUENCE IF EXISTS {seq}")
