"""Why an AI teammate proposed a work method

`project_skills.proposal` keeps what a teammate's draft or edit rests on: what
the people taught it, how the result was accepted, the existing method it was
compared with, the team memories it absorbs, and for an edit the correction it
answers. The card that asks a person to save it shows this. NULL for a method a
person wrote.

Revision ID: a7d3e91c5b20
Revises: 41a261d02e9e
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a7d3e91c5b20"
down_revision = "41a261d02e9e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_skills",
        sa.Column("proposal", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_skills", "proposal")
