"""Every block stored from now on takes the next number of its conversation

Revision ID: 0dd66b328211
Revises: 22d5c7f33018
Create Date: 2026-10-09

The number is given by the database, not by the application. Every writer
gets one, the ten or so places that build a `Block` and the release still
serving while this deploy runs alike, which does not know the column exists.

An insert takes a lock on its conversation that it holds until it commits,
then numbers itself one past the largest number there (`ix_blocks_conversation_seq`).
So two writers in one conversation are numbered in the order they commit, and a
reader never sees n + 1 before n. The lock is a transaction-level advisory lock
in the two-key space, which nothing else in this codebase takes: a key from
the one-key space could collide with a lock a process holds for its whole life.
It is not an update of a counter row: a statement that inserts many blocks into
one conversation would rewrite that row once per block, and each rewrite walks
the versions before it (16,000 blocks took 6.6 s that way, 0.9 s this way).

The trigger reads the largest number with sequential scans turned off. Its plan
is made once per connection from the statistics of that moment, and on a table
analyzed while nearly empty the planner picks a scan of the whole table, which
every block then repeats: 33,000 blocks inserted at once took 37 s that way
and 0.5 s through the index.

The blocks a conversation already holds are numbered 1 … n by `5f1b7d54bffa`
in the order rooms show them. `seq_floor` keeps those n numbers for them: a
block stored before they are filled in still numbers itself above n. Counting
under the lock is what makes n exact; on dev (2026-10-09, 498,893 blocks in
1,585 conversations) the count took 0.16 s.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "0dd66b328211"
down_revision: str | Sequence[str] | None = "22d5c7f33018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The first key of every conversation's numbering lock; the second is the
#: conversation's hash. Copied into the trigger below.
NUMBERING_LOCK = 1_734_632_801


def upgrade() -> None:
    with_lock_retries("conversations, blocks")
    op.execute("""
        UPDATE conversations c SET seq_floor = held.n
        FROM (SELECT conversation_id, count(*) AS n FROM blocks GROUP BY 1) held
        WHERE c.id = held.conversation_id
    """)
    op.execute(f"""
        CREATE FUNCTION block_numbered() RETURNS trigger
        LANGUAGE plpgsql SET enable_seqscan = off AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                {NUMBERING_LOCK}, hashtext(NEW.conversation_id::text)
            );
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
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER blocks_numbered
        BEFORE INSERT ON blocks
        FOR EACH ROW EXECUTE FUNCTION block_numbered()
    """)


def downgrade() -> None:
    with_lock_retries("conversations, blocks")
    op.execute("DROP TRIGGER blocks_numbered ON blocks")
    op.execute("DROP FUNCTION block_numbered()")
    op.execute("UPDATE conversations SET seq_floor = 0")
