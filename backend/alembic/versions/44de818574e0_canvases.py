"""canvases: a free board of cards and lines, a block of one document

Three new tables, one per kind of thing: `canvases` (each belongs to one
document and goes with it), `canvas_cards` (a passage of Markdown at a place on
the board, which may point at a task) and `canvas_edges` (a line from one card
to another, with a few words on it or none).

Nothing existing changes. The new foreign keys point at `documents` and
`tasks`, which the backend this deploy replaces is serving while this runs, so
both are locked up front, all at once or not at all.

Revision ID: 44de818574e0
Revises: 2a0ab4e3349a
Create Date: 2026-10-12 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "44de818574e0"
down_revision: str | Sequence[str] | None = "2a0ab4e3349a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("documents, tasks")
    op.create_table(
        "canvases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey(
                "documents.id",
                ondelete="CASCADE",
                name="fk_canvases_document_id_documents",
            ),
            nullable=False,
        ),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("next_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_canvases_document_id", "canvases", ["document_id"])
    op.create_table(
        "canvas_cards",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "canvas_id",
            sa.Uuid(),
            sa.ForeignKey(
                "canvases.id",
                ondelete="CASCADE",
                name="fk_canvas_cards_canvas_id_canvases",
            ),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("width", sa.Float(), nullable=True),
        sa.Column("height", sa.Float(), nullable=True),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey(
                "tasks.id", ondelete="SET NULL", name="fk_canvas_cards_task_id_tasks"
            ),
            nullable=True,
        ),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "canvas_id", "number", name="uq_canvas_cards_canvas_number"
        ),
    )
    op.create_index("ix_canvas_cards_task_id", "canvas_cards", ["task_id"])
    op.create_table(
        "canvas_edges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "canvas_id",
            sa.Uuid(),
            sa.ForeignKey(
                "canvases.id",
                ondelete="CASCADE",
                name="fk_canvas_edges_canvas_id_canvases",
            ),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey(
                "canvas_cards.id",
                ondelete="CASCADE",
                name="fk_canvas_edges_source_id_canvas_cards",
            ),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.Uuid(),
            sa.ForeignKey(
                "canvas_cards.id",
                ondelete="CASCADE",
                name="fk_canvas_edges_target_id_canvas_cards",
            ),
            nullable=False,
        ),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_id", "target_id", name="uq_canvas_edges_source_target"
        ),
    )
    op.create_index("ix_canvas_edges_target_id", "canvas_edges", ["target_id"])
    op.create_index("ix_canvas_edges_canvas_id", "canvas_edges", ["canvas_id"])


def downgrade() -> None:
    op.drop_table("canvas_edges")
    op.drop_table("canvas_cards")
    op.drop_table("canvases")
