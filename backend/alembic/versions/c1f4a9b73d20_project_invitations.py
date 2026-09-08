"""project invitations: a person joins after they accept

进项目就看得见这个项目的全部话题，那是别人的工作内容，不该由邀请方单方面决定谁
能看。所以名册上多了一个中间状态：邀请发出去了，人还没进来。

Revision ID: c1f4a9b73d20
Revises: b7e4d21c9a06
"""

import sqlalchemy as sa

from alembic import op

revision = "c1f4a9b73d20"
down_revision = "b7e4d21c9a06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("invitee_handle", sa.String(length=64), nullable=False),
        sa.Column("inviter_handle", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "invitee_handle", "status", name="uq_project_invitation"
        ),
    )
    op.create_index(
        "ix_project_invitations_project_id", "project_invitations", ["project_id"]
    )
    op.create_index(
        "ix_project_invitations_invitee_handle",
        "project_invitations",
        ["invitee_handle"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_invitations_invitee_handle", "project_invitations")
    op.drop_index("ix_project_invitations_project_id", "project_invitations")
    op.drop_table("project_invitations")
