"""Model connections an account holds, and own agents bound to one

Revision ID: 0cfaea35ed30
Revises: a7c3e9d1f520
Create Date: 2026-10-13

`account_model_connections`: an Anthropic API key or a model service a person
keeps on their account rather than on a machine, its key sealed. A Claude
subscription is never one (its login stays on its machine), which the kind's
CHECK holds.

`own_agents.account_connection_id`: a member's own agent may run on one of
those instead of a machine's login (`device_id`), never both. Existing rows are
all bound to machines or to nothing, so nothing is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "0cfaea35ed30"
down_revision: str | Sequence[str] | None = "a7c3e9d1f520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNER_FK = "fk_account_model_connections_owner_user_id_user"
_OWNER_INDEX = "ix_account_model_connections_owner_user_id"
_KIND = "ck_account_model_connections_kind"
_FK = "fk_own_agents_account_connection_id_account_model_connections"
_INDEX = "ix_own_agents_account_connection_id"
_ONE = "ck_own_agents_one_connection"


def upgrade() -> None:
    op.create_table(
        "account_model_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "api_key",
                "model_service",
                native_enum=False,
                length=16,
                create_constraint=True,
                name=_KIND,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("key_last4", sa.String(length=4), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["user.id"], ondelete="CASCADE", name=_OWNER_FK
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(_OWNER_INDEX, "account_model_connections", ["owner_user_id"])

    # The new foreign key locks the table it points at as well.
    with_lock_retries("own_agents, account_model_connections")
    op.add_column("own_agents", sa.Column("account_connection_id", sa.Uuid()))
    op.create_foreign_key(
        _FK,
        "own_agents",
        "account_model_connections",
        ["account_connection_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    op.execute(
        f"ALTER TABLE own_agents ADD CONSTRAINT {_ONE}"
        " CHECK (device_id IS NULL OR account_connection_id IS NULL) NOT VALID"
    )
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE own_agents VALIDATE CONSTRAINT {_FK}")
        op.execute(f"ALTER TABLE own_agents VALIDATE CONSTRAINT {_ONE}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.create_index(
            _INDEX,
            "own_agents",
            ["account_connection_id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    # Own agents on an account's connection lose it, as they would had their
    # owner removed it; the rest are untouched.
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="own_agents",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_constraint(_ONE, "own_agents", type_="check")
    op.drop_constraint(_FK, "own_agents", type_="foreignkey")
    op.drop_column("own_agents", "account_connection_id")
    op.drop_index(_OWNER_INDEX, table_name="account_model_connections")
    op.drop_table("account_model_connections")
