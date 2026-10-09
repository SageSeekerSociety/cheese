"""blocks: number new blocks from the largest number alone

Revision ID: e8d439b81ab1
Revises: 74916c1948ae
Create Date: 2026-10-09

`conversations.seq_floor` kept the numbers of the blocks a conversation held
before numbering began, for a block stored before `5f1b7d54bffa` gave them
theirs. Every block has its number now, so the largest number in the
conversation is never below the floor and the trigger stops reading it. The
column is no longer mapped and is dropped in the next release.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e8d439b81ab1"
down_revision: str | Sequence[str] | None = "74916c1948ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The first key of every conversation's numbering lock (`0dd66b328211`).
NUMBERING_LOCK = 1_734_632_801


def _numbered(next_number: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION block_numbered() RETURNS trigger
        LANGUAGE plpgsql SET enable_seqscan = off AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                {NUMBERING_LOCK}, hashtext(NEW.conversation_id::text)
            );
            {next_number}
            RETURN NEW;
        END;
        $$
    """


def upgrade() -> None:
    op.execute(
        _numbered("""
            NEW.seq := coalesce(
                (SELECT max(seq) FROM blocks
                 WHERE conversation_id = NEW.conversation_id),
                0
            ) + 1;
        """)
    )


def downgrade() -> None:
    op.execute(
        _numbered("""
            SELECT greatest(
                coalesce(
                    (SELECT max(seq) FROM blocks
                     WHERE conversation_id = NEW.conversation_id),
                    0
                ),
                c.seq_floor
            ) + 1
            INTO NEW.seq
            FROM conversations c WHERE c.id = NEW.conversation_id;
        """)
    )
