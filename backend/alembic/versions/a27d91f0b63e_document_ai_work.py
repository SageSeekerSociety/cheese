"""Persistent document AI requests, attempts and proposals.

Revision ID: a27d91f0b63e
Revises: f7a31e6b920c
"""

import sqlalchemy as sa

from alembic import op

revision = "a27d91f0b63e"
down_revision = "f7a31e6b920c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doc_ai_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=True),
        sa.Column("binding", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('ask', 'propose')", name="ck_doc_ai_kind"),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_doc_ai_state",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_doc_ai_generation"),
    )
    op.create_index("ix_doc_ai_requests_room_id", "doc_ai_requests", ["room_id"])
    op.create_index("ix_doc_ai_requests_state", "doc_ai_requests", ["state"])
    op.create_table(
        "doc_ai_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("doc_ai_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("request_id", "generation", name="uq_doc_ai_attempt"),
    )
    op.create_table(
        "doc_ai_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("doc_ai_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("replacement", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("accepted_by", sa.String(128), nullable=True),
        sa.Column("accepted_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_doc_ai_proposal_request"),
        sa.CheckConstraint(
            "state IN ('pending', 'accepted', 'withdrawn')",
            name="ck_doc_ai_proposal_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("doc_ai_proposals")
    op.drop_table("doc_ai_attempts")
    op.drop_table("doc_ai_requests")
