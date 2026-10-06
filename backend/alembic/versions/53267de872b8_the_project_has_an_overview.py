"""The project has an overview; a channel has no living document; pins

- **`projects.overview_document_id`**: the project's overview, one document of
  the project's own that every conversation's AI teammate reads. It was 综合's
  living document, which keeps its id, text and history and now belongs to no
  channel.
- **A channel has no living document.** One that was never written is deleted;
  one that was becomes a document of the project's own, titled after its
  channel, so nothing written is lost. Archived rooms keep theirs: they are
  the old rooms still waiting to become tasks, and their documents go with
  them.
- **`pins`**: what a channel's members keep at the top of its overview, one row
  a pinned message or file of its main line.

Revision ID: 53267de872b8
Revises: 694b0dbaf5eb
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "53267de872b8"
down_revision: str | Sequence[str] | None = "694b0dbaf5eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pins",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "block_id",
            sa.Uuid(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("pinned_by", sa.String(128), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "projects",
        sa.Column(
            "overview_document_id",
            sa.Uuid(),
            sa.ForeignKey(
                "documents.id",
                ondelete="SET NULL",
                name="fk_projects_overview_document_id",
            ),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE projects p SET overview_document_id = d.id
        FROM documents d
        WHERE d.room_id = p.root_topic_id
        """
    )
    op.execute(
        """
        UPDATE documents d SET room_id = NULL
        FROM projects p
        WHERE p.overview_document_id = d.id
        """
    )
    # The remaining living documents of channels and private chats that are
    # not archived. A written one keeps what it says under its channel's name.
    op.execute(
        """
        UPDATE documents d SET title = left(coalesce(t.title, ''), 200), room_id = NULL
        FROM topics t
        WHERE d.room_id = t.id
          AND t.status <> 'archived'
          AND btrim(d.content) <> ''
        """
    )
    op.execute(
        """
        DELETE FROM documents d
        USING topics t
        WHERE d.room_id = t.id AND t.status <> 'archived'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE documents d SET room_id = p.root_topic_id
        FROM projects p
        WHERE p.overview_document_id = d.id AND p.root_topic_id IS NOT NULL
        """
    )
    op.drop_column("projects", "overview_document_id")
    op.drop_table("pins")
