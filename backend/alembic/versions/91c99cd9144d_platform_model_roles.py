"""platform_model_roles: the gateway model each platform model role runs on

A role (default model, voice input, topic naming, ...) used to be a Settings
field, so repointing one took a backend redeploy. A row here is an
administrator's choice for one role; a role with no row reads its Settings
field, so this migration adds an empty table and changes nothing until someone
points a role.

Revision ID: 91c99cd9144d
Revises: e4a7c2d9b6f1
Create Date: 2026-10-21 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "91c99cd9144d"
down_revision: str | Sequence[str] | None = "e4a7c2d9b6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_model_roles",
        sa.Column("role", sa.String(length=64), primary_key=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("platform_model_roles")
