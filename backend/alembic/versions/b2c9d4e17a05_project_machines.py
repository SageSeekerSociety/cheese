"""project_machines: a project's compute, provisioned from MicroCloud

MicroCloud owns the machine; this table only records which machine belongs to
which project plus the tenant-side ids needed to talk about it again. Status,
IP and the AI-setup state are cached here but re-read from MicroCloud, so the
table is never the authority. Additive only.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c9d4e17a05"
down_revision: str | Sequence[str] | None = "d1e5b3a9c724"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_machines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("machine_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("offering_id", sa.BigInteger(), nullable=False),
        sa.Column("hostname", sa.String(length=64), nullable=False),
        sa.Column("login_user", sa.String(length=32), nullable=False),
        sa.Column("cores", sa.BigInteger(), nullable=False),
        sa.Column("memory_mb", sa.BigInteger(), nullable=False),
        sa.Column("disk_gb", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column(
            "ai_mode", sa.String(length=16), server_default="none", nullable=False
        ),
        sa.Column("ai_status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_project_machines_project_id"),
        "project_machines",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_machines_machine_id"),
        "project_machines",
        ["machine_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_project_machines_machine_id"), table_name="project_machines")
    op.drop_index(op.f("ix_project_machines_project_id"), table_name="project_machines")
    op.drop_table("project_machines")
