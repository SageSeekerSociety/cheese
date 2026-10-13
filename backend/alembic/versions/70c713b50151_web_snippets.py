"""web snippets: the code of a document's mini-apps, by version

A mini-app (`:::app <key>@<version>` in a document) is code 芝士 wrote, run in
the platform's shell. `web_snippets` names one and says which document it
belongs to; `web_snippet_versions` holds each version's code, whether the
platform's browser ran it, its heights and where its pictures are kept.
Nothing existing changes.

Revision ID: 70c713b50151
Revises: b7e4c2a9d5f1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "70c713b50151"
down_revision: str | Sequence[str] | None = "b7e4c2a9d5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The new foreign key points at `documents`, which the backend this deploy
    # replaces is serving while this runs.
    with_lock_retries("documents")
    op.create_table(
        "web_snippets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(16), nullable=False),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey(
                "documents.id",
                ondelete="CASCADE",
                name="fk_web_snippets_document_id_documents",
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", name="uq_web_snippets_key"),
    )
    op.create_index("ix_web_snippets_document_id", "web_snippets", ["document_id"])
    op.create_table(
        "web_snippet_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "snippet_id",
            sa.Uuid(),
            sa.ForeignKey(
                "web_snippets.id",
                ondelete="CASCADE",
                name="fk_web_snippet_versions_snippet_id_web_snippets",
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("shell", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.Column("author", sa.String(128), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=True),
        sa.Column("checked", sa.Boolean(), nullable=False),
        sa.Column("heights", postgresql.JSONB(), nullable=False),
        sa.Column("shots", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "snippet_id", "version", name="uq_web_snippet_versions_snippet_id_version"
        ),
    )
    op.create_index(
        "ix_web_snippet_versions_snippet_id", "web_snippet_versions", ["snippet_id"]
    )


def downgrade() -> None:
    op.drop_table("web_snippet_versions")
    op.drop_table("web_snippets")
