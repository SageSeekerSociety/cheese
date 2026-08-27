"""an accept card belongs to a tree

一棵树 = 一个分支 = 一个 PR = 一批活, so the card that opens that PR belongs to
the tree, not to the room.

The distinction was invisible while a room had exactly one tree, and it stops
being invisible the moment it has two: "one card at a time" is a rule about not
running two PRs on ONE branch, and applying it to the room instead would mean a
room could never open a second PR — which is the whole thing more than one tree
was for.

It also carries what the tree's own quick check last said. That check is the
agent's, not the platform's: #296 retired the machine gate because a card is a
view of a PR and the real CI on that PR is what decides. Recording the result
does not give it a vote — it makes a red check VISIBLE to the person about to
accept, which is the half that was missing. A check nobody sees is a check
nobody runs.

Sealing follows from the same fact. Filing a card is the moment a tree's content
stops being work-in-progress and starts being what CI is checking, so that is
where the tree seals and the next batch begins on a fresh one.

Revision ID: e4c9a2f60b18
Revises: d2b6e91c4a77
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4c9a2f60b18"
down_revision: str | Sequence[str] | None = "d2b6e91c4a77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accept_cards",
        sa.Column(
            "tree_id",
            sa.Uuid(),
            sa.ForeignKey("work_trees.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Every card that exists today was filed when its room had exactly one tree,
    # and that tree carries the room's id — so the answer is already known and
    # does not need guessing.
    op.execute(
        """
        UPDATE accept_cards
        SET tree_id = topic_id
        WHERE tree_id IS NULL
          AND EXISTS (SELECT 1 FROM work_trees w WHERE w.id = accept_cards.topic_id)
        """
    )
    # Nullable on purpose, and it stays that way: `SET NULL` above means a card
    # outlives the tree it delivered, and a historical card whose tree was never
    # created has no honest value to invent.
    op.create_index("ix_accept_cards_tree_id", "accept_cards", ["tree_id"])

    # 快检的结果，属于这棵树当前的内容。Not a gate: nothing reads it to decide
    # anything. It is shown on the card so a red check is visible to whoever is
    # about to accept.
    op.add_column(
        "work_trees",
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("work_trees", sa.Column("last_check_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "work_trees",
        sa.Column("last_check_detail", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("work_trees", "last_check_detail")
    op.drop_column("work_trees", "last_check_ok")
    op.drop_column("work_trees", "last_check_at")
    op.drop_index("ix_accept_cards_tree_id", table_name="accept_cards")
    op.drop_column("accept_cards", "tree_id")
