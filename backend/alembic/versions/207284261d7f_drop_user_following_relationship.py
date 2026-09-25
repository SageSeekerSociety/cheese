"""Drop user_following_relationship once no release reads it

Following a person was removed in #1716: the endpoints and the counts on
the user payload went, and nothing in `backend/app` reads or writes this
table. The drop ships a release later because the deploy migrates while the
previous image still serves, and that image read the table on every user
payload.

Following a question is a different feature with its own table and stays.

The downgrade brings the table and its sequence back empty. The rows are not
recoverable, and nothing that could run against the downgraded schema needs
them.

Revision ID: 207284261d7f
Revises: 853d38c772dd
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "207284261d7f"
down_revision: str | Sequence[str] | None = "853d38c772dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("user_following_relationship")
    op.execute(
        sa.schema.DropSequence(sa.Sequence("user_following_relationship_id_seq"))
    )


def downgrade() -> None:
    op.execute(
        sa.schema.CreateSequence(sa.Sequence("user_following_relationship_id_seq"))
    )
    op.create_table(
        "user_following_relationship",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Sequence("user_following_relationship_id_seq"),
            nullable=False,
        ),
        sa.Column("followee_id", sa.Integer(), nullable=False),
        sa.Column("follower_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
