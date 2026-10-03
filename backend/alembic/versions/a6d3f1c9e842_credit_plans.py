"""Credit plans: every team is on one, Free by default

Revision ID: a6d3f1c9e842
Revises: f2a9c4e7b318
Create Date: 2026-10-02

Two plans are seeded. Free is every team's: a monthly pack worth about USD 5 at
the deployment's price per credit (125 credits where none is set), no time
windows, gateway models of the included tier only. Reserve is for the
platform's own team, put on it by an administrator: unlimited, every model.

``resource_usage`` takes the team that paid and the credits charged per row, so
a plan's time window can be summed from an index alone. That table takes an
insert on every model call: the foreign key is added ``NOT VALID`` and
validated afterwards, and the index is built ``CONCURRENTLY``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6d3f1c9e842"
down_revision: str | Sequence[str] | None = "f2a9c4e7b318"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FREE_USD_PER_MONTH = 5.0
FREE_CREDITS_WITHOUT_A_PRICE = 125.0


def _price_per_credit() -> float | None:
    """``LLM_GATEWAY_CREDIT_USD`` as the deployment set it; the setting itself
    has since gone (credits are a fixed hundredth of a dollar)."""
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class _Deployment(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", extra="ignore")
        llm_gateway_credit_usd: float | None = None

    return _Deployment().llm_gateway_credit_usd


def _free_credits() -> float:
    price = _price_per_credit()
    if not price or price <= 0:
        return FREE_CREDITS_WITHOUT_A_PRICE
    return float(round(FREE_USD_PER_MONTH / price))


def seed_rows() -> list[dict]:
    """The two plans every deployment starts with. The test suite re-seeds them
    from here after wiping its database, as this migration seeds a real one."""
    return [
        {
            "key": "free",
            "name": "Free",
            "audience": "both",
            "credits_per_period": _free_credits(),
            "period": "month",
            "windows": [],
            "model_tiers": ["included"],
            "allows_subscription": False,
            "unlimited": False,
            "admin_only": False,
        },
        {
            "key": "reserve",
            "name": "Reserve",
            "audience": "team",
            "credits_per_period": None,
            "period": "month",
            "windows": [],
            "model_tiers": None,
            "allows_subscription": True,
            "unlimited": True,
            "admin_only": True,
        },
    ]


def upgrade() -> None:
    plans = op.create_table(
        "plans",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("audience", sa.String(16), nullable=False),
        sa.Column("credits_per_period", sa.Float(), nullable=True),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("windows", sa.JSON(), nullable=False),
        sa.Column("model_tiers", sa.JSON(), nullable=True),
        sa.Column("allows_subscription", sa.Boolean(), nullable=False),
        sa.Column("unlimited", sa.Boolean(), nullable=False),
        sa.Column("admin_only", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.bulk_insert(plans, seed_rows(), multiinsert=False)

    # A constant default: every existing team is on Free without a rewrite.
    op.add_column(
        "team",
        sa.Column(
            "plan_key",
            sa.String(32),
            sa.ForeignKey("plans.key"),
            nullable=False,
            server_default="free",
        ),
    )

    op.add_column("compute_grants", sa.Column("reason", sa.Text(), nullable=True))

    op.create_table(
        "credit_admin_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_handle", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_credit_admin_audit_created_at",
        "credit_admin_audit",
        [sa.text("created_at DESC")],
    )

    op.add_column(
        "resource_usage", sa.Column("team_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "resource_usage",
        sa.Column("credits", sa.Float(), nullable=False, server_default="0"),
    )
    op.execute(
        "ALTER TABLE resource_usage ADD CONSTRAINT resource_usage_team_id_fkey "
        "FOREIGN KEY (team_id) REFERENCES team (id) ON DELETE CASCADE NOT VALID"
    )
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE resource_usage VALIDATE CONSTRAINT resource_usage_team_id_fkey"
        )
        op.create_index(
            "ix_resource_usage_team_created_at",
            "resource_usage",
            ["team_id", "created_at"],
            postgresql_include=["credits"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_resource_usage_team_created_at",
            table_name="resource_usage",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_constraint("resource_usage_team_id_fkey", "resource_usage")
    op.drop_column("resource_usage", "credits")
    op.drop_column("resource_usage", "team_id")
    op.drop_index("ix_credit_admin_audit_created_at", table_name="credit_admin_audit")
    op.drop_table("credit_admin_audit")
    op.drop_column("compute_grants", "reason")
    op.drop_column("team", "plan_key")
    op.drop_table("plans")
