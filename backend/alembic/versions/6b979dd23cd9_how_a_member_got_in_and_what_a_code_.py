"""how a member got in, and what a code says about itself

Two facts the board screens need and the schema never kept:

- ``space_member.invite_code_id`` — the 成员 page's 「加入方式」 column ("he came
  in on this code"). ``join_space`` spent the use but wrote nothing linking the
  member to the code, so today the fact is simply absent.
- ``space_invite_code.note`` — the 说明 the maker writes on a code ("十月这批
  同学"). ``space_invite_code.created_by`` already exists and is already filled
  in by ``create_space``; it only ever lacked a read path, so it needs nothing
  here.

**Nothing is backfilled, and that is the whole point of the column being
nullable.** The first group are rows written before the fact was recorded: the
code that admitted them was never stored anywhere, and this migration cannot
recover it. Attributing an existing member to whichever code happens to be in
the space — the board's first code, say — would turn a missing record into a
wrong one, and worse, into one that looks authoritative on screen. The same
goes for ``note``: an existing code said nothing about itself, and inventing
"默认码" for it would be inventing it.

So both columns start empty for every existing row, and the read side says
未知. Nullability is load-bearing rather than incidental: NULL is the answer
"nobody recorded this", which is not the same claim as "no code was involved".

This revision was first written on ``7c2e91a4d3f6`` and has been re-chained onto
whatever head main had reached while it was in review — ``b380c2e8f60c`` first,
then ``a3d1f0c72b94``. Those revisions touch disjoint tables from this one (成员/
邀请码 here; remote MCP connections and project exclusions there), so the order
between them does not matter; only having one head does.

Revision ID: 6b979dd23cd9
Revises: a3d1f0c72b94
Create Date: 2026-09-27 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6b979dd23cd9"
down_revision: str | Sequence[str] | None = "b7f1c3d9a2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # No server_default and no backfill: see the module docstring. Both columns
    # are nullable, so existing rows satisfy them as written.
    op.add_column(
        "space_member",
        sa.Column("invite_code_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "space_invite_code",
        sa.Column("note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("space_invite_code", "note")
    op.drop_column("space_member", "invite_code_id")
