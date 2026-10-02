"""Who asked for a document change, and what is suggested in it.

A version records the person a change was made for when someone else (the
room's agent) made it. The collaborative state carries the suggestions still
pending in the live document, as the collaboration service reported them at
its last store.

Revision ID: 1dd568ffdeef
Revises: d4b7e19a2c55
"""

import sqlalchemy as sa

from alembic import op

revision = "1dd568ffdeef"
down_revision = "d4b7e19a2c55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "living_doc_versions",
        sa.Column("requested_by", sa.String(128), nullable=True),
    )
    op.add_column(
        "living_doc_states",
        sa.Column(
            "suggestions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
    )


def downgrade() -> None:
    op.drop_column("living_doc_states", "suggestions")
    op.drop_column("living_doc_versions", "requested_by")
