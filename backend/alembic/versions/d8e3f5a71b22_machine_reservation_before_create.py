"""A project machine row may exist before the provider has created the machine.

Revision ID: d8e3f5a71b22
Revises: e7d2b91a4c06
Create Date: 2026-09-19 04:30:00

`provision` used to hold the team quota lock across the MicroCloud create call
so that counting and creating were one step. The row itself is now the
reservation: written under the lock with no provider id, then filled in once
the provider answers, with no lock held across the call.
"""

import sqlalchemy as sa

from alembic import op

revision = "d8e3f5a71b22"
down_revision = "e7d2b91a4c06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "project_machines",
        "machine_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM project_machines WHERE machine_id IS NULL")
    op.alter_column(
        "project_machines",
        "machine_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
