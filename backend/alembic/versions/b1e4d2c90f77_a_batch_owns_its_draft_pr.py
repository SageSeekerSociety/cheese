"""a batch owns its draft PR

一棵树 = 一个分支 = 一个 PR = 一批活 has been the model since trees existed, but
the PR half of it was never written down: the PR lived on the accept card, so it
could not exist before somebody filed one. #718 拍板① moves the moment a PR opens
to the batch's FIRST COMMIT — 有东西就有 PR, and draft is GitHub's word for
进行中 — which is earlier than any card, so the PR has to hang on the tree.

Two columns and no backfill: an open tree that predates this simply has no PR
recorded, and the sweep opens one for it on its next commit (or, for a branch
that already has commits, on its next tick). A backfill would have to ask GitHub
per tree for a value that is about to be written correctly anyway.

Revision ID: b1e4d2c90f77
Revises: a4f1c73b2e60
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1e4d2c90f77"
down_revision: str | Sequence[str] | None = "f8c1a9073e62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("work_trees", sa.Column("pr_number", sa.Integer(), nullable=True))
    op.add_column("work_trees", sa.Column("pr_url", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("work_trees", "pr_url")
    op.drop_column("work_trees", "pr_number")
