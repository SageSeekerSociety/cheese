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
        sa.Column("meter_after", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(
        "ix_doc_ai_requests_meter_after", "doc_ai_requests", ["meter_after"]
    )
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
        sa.Column("result_hash", sa.String(64), nullable=True),
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
    op.execute("""
        CREATE FUNCTION freeze_doc_ai_data() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_TABLE_NAME = 'doc_ai_requests' THEN
            IF (to_jsonb(NEW) - ARRAY['state','generation','lease_until','answer',
                'error','meter_after']) IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['state','generation','lease_until','answer',
                'error','meter_after']) THEN
              RAISE EXCEPTION 'immutable document AI request';
            END IF;
            IF OLD.state IN ('succeeded','failed','cancelled') AND
               (to_jsonb(NEW) - 'meter_after') IS DISTINCT FROM
               (to_jsonb(OLD) - 'meter_after') THEN
              RAISE EXCEPTION 'terminal document AI request';
            END IF;
          ELSIF TG_TABLE_NAME = 'doc_ai_proposals' THEN
            IF (to_jsonb(NEW) - ARRAY['state','accepted_by','accepted_version'])
                IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['state','accepted_by','accepted_version']) OR
               (OLD.state <> 'pending' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN
              RAISE EXCEPTION 'immutable document AI proposal';
            END IF;
          ELSIF TG_TABLE_NAME = 'doc_ai_attempts' THEN
            IF (to_jsonb(NEW) - ARRAY['finished_at','usage','error','result_hash'])
                IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['finished_at','usage','error','result_hash']) OR
               (OLD.finished_at IS NOT NULL AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN
              RAISE EXCEPTION 'immutable document AI attempt receipt';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
    """)
    for table in ("doc_ai_requests", "doc_ai_proposals", "doc_ai_attempts"):
        op.execute(f"""
            CREATE TRIGGER freeze_{table} BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION freeze_doc_ai_data();
        """)


def downgrade() -> None:
    op.drop_table("doc_ai_proposals")
    op.drop_table("doc_ai_attempts")
    op.drop_table("doc_ai_requests")
    op.execute("DROP FUNCTION freeze_doc_ai_data()")
