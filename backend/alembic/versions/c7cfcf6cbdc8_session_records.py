"""Keep every session's raw records: `session_records`, append-only."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c7cfcf6cbdc8"
down_revision = "8f2c5d1a9b47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("agent_handle", sa.String(128), nullable=False),
        sa.Column("harness", sa.String(32), nullable=False),
        sa.Column("first_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("records", postgresql.JSONB(), nullable=True),
        sa.Column("object_key", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # No foreign key to conversations, on purpose: a cascade would take
        # the rows and leave their objects in the bucket with nothing naming
        # them. The retention sweep removes a gone conversation's rows and
        # objects together (session_record.service.forget_gone).
        sa.UniqueConstraint(
            "conversation_id",
            "agent_handle",
            "harness",
            "first_sequence",
            "first_at",
            name="uq_session_records_conversation_id",
        ),
        sa.CheckConstraint(
            "(records IS NULL) <> (object_key IS NULL)",
            name="ck_session_records_one_home",
        ),
    )
    op.create_index(
        "ix_session_records_conversation_id",
        "session_records",
        ["conversation_id", "first_at"],
    )
    op.create_index(
        "ix_session_records_turn_id", "session_records", ["turn_id", "first_at"]
    )
    op.create_index(
        "ix_session_records_unsealed",
        "session_records",
        ["last_at"],
        postgresql_where=sa.text("object_key IS NULL"),
    )
    # The record of what a session did is evidence: a row is written once and
    # never changed. Sealing replaces short runs with a long one by DELETE and
    # INSERT; nothing has a reason to UPDATE.
    op.execute("""
        CREATE FUNCTION session_records_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'session_records is append-only'
                USING ERRCODE = '23514';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER session_records_append_only
        BEFORE UPDATE ON session_records
        FOR EACH ROW EXECUTE FUNCTION session_records_append_only()
    """)


def downgrade() -> None:
    op.drop_table("session_records")
    op.execute("DROP FUNCTION session_records_append_only()")
