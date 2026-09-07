"""a card declares the work it delivers

`Cheese-Task:` used to be filled in by listing the members of the tree the card
delivered. A task joins a tree when `cheese split` runs; its code lands on
whichever branch is open when the room files a card. A room that keeps working
across two batches makes those two different answers, and then the trailers are
wrong in both directions at once — measured on this project's own history
(2026-09-08): one PR was signed by three tasks that contributed nothing to it,
while the task that actually wrote it was signed onto the previous PR.

Nothing the platform can see fixes that. Commits inside the sandbox are authored
by the requester and co-authored by the model, so a commit range cannot say
which 分身 typed it; and any rule over the task table ("on this tree",
"not claimed by an earlier card") only establishes that a row exists, never that
its code is in this diff — a placeholder task that wrote nothing and a sibling
still running both pass. The room filing the card is the only party that knows,
so this column is what the room SAYS, and an empty one writes no trailer.

Revision ID: a4f1c73b2e60
Revises: c2d7e9f1a718
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4f1c73b2e60"
down_revision: str | Sequence[str] | None = "c2d7e9f1a718"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No backfill, deliberately. Every existing card's batch is exactly the
    # thing that cannot be reconstructed — inventing one from its tree would
    # write the wrong names into rows that are about to become permanent
    # history, which is the bug this closes.
    op.add_column(
        "accept_cards",
        sa.Column(
            "delivered_task_ids",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "delivered_task_ids")
