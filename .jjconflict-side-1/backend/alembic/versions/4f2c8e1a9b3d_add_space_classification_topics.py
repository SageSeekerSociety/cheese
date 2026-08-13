"""Add space_classification_topics_relation table.

The Vue frontend's `Space.classificationTopics: Topic[]` field is the
source of truth: stores/space.ts:69 fetches it via
`SpacesApi.detail(spaceId, { queryClassificationTopics: true })` and
PATCHes back a `classificationTopics: number[]` array. The NT (Kotlin)
backend persists this in a dedicated relation table; this migration
brings the same table into the Python schema.

Revision ID: 4f2c8e1a9b3d
Revises: 3d0e04dc8e65
Create Date: 2026-05-09
"""

import sqlalchemy as sa

from alembic import op

revision = "4f2c8e1a9b3d"
down_revision = "3d0e04dc8e65"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "space_classification_topics_relation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_classification_topics_space",
        "space_classification_topics_relation",
        ["space_id"],
    )
    op.create_index(
        "ix_space_classification_topics_topic",
        "space_classification_topics_relation",
        ["topic_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_space_classification_topics_topic",
        table_name="space_classification_topics_relation",
    )
    op.drop_index(
        "ix_space_classification_topics_space",
        table_name="space_classification_topics_relation",
    )
    op.drop_table("space_classification_topics_relation")
