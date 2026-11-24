"""integrations: one Feishu connection per owner and App ID

Revision ID: fbdb95e20845
Revises: c5e1a7d39f20
Create Date: 2026-11-24

A member connects Feishu with their own company's self-built app, and entering
the same App ID again is the same connection (`IntegrationService.connect_feishu`).
Looking the row up first leaves a window: two connects for the same App ID at
once both find none and both insert. This index closes it — the second insert
fails, and the service takes the row the first one made.

`app_id` lives in the JSON `config`, so the index is on the expression, and
partial to Feishu rows (a mailbox has no `app_id`). A row whose `config` has
none (made under the platform-wide app) indexes as NULL and conflicts with
nothing. Checked before writing this: dev and production each hold one Feishu
row, so the build finds no duplicates.

Built CONCURRENTLY: `integrations` is read by every Feishu and mail call. A
unique index on an expression cannot back a table constraint, so it stays an
index.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "fbdb95e20845"
down_revision: str | Sequence[str] | None = "c5e1a7d39f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "uq_integrations_owner_feishu_app"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            NAME,
            "integrations",
            ["owner_user_id", sa.text("(config ->> 'app_id')")],
            unique=True,
            postgresql_where=sa.text("provider = 'feishu'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            NAME,
            table_name="integrations",
            postgresql_concurrently=True,
            if_exists=True,
        )
