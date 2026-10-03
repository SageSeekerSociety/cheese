"""A credit is a hundredth of a US dollar; rescale every stored amount

Revision ID: ddf13581d122
Revises: f3a9c81d4e26
Create Date: 2026-10-03

A credit was whatever ``LLM_GATEWAY_CREDIT_USD`` said, 0.04 on dev. It is now
fixed at USD 0.01, the 点 people see, and the setting is gone. Every amount
stored in credits is multiplied by the old price over the new one: packs,
plans and their windows, window use, usage rows and the compute credits a
项目集 or a 赛题 provides. A deployment that set no price is taken at 0.04,
the price Free's 125 monthly credits were sized for.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ddf13581d122"
down_revision: str | Sequence[str] | None = "f3a9c81d4e26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CREDIT_USD = 0.01
UNSET_PRICE = 0.04


def _old_price() -> float:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class _Deployment(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", extra="ignore")
        llm_gateway_credit_usd: float | None = None

    price = _Deployment().llm_gateway_credit_usd
    return price if price and price > 0 else UNSET_PRICE


def seed_rows() -> list[dict]:
    """The plans a new deployment ends up with once every migration has run.
    The test suite re-seeds them from here after wiping its database."""
    return [
        {
            "key": "free",
            "name": "Free",
            "audience": "both",
            "credits_per_period": 500.0,
            "period": "month",
            "windows": [],
            "model_tiers": ["included"],
            "unlimited": False,
            "admin_only": False,
            "rank": 0,
        },
        {
            "key": "reserve",
            "name": "Reserve",
            "audience": "team",
            "credits_per_period": None,
            "period": "month",
            "windows": [],
            "model_tiers": None,
            "unlimited": True,
            "admin_only": True,
            "rank": 100,
        },
    ]


def _scale_json(conn, table: str, key: str, column: str, path, factor: float) -> None:
    """Multiply the number at ``path`` inside each row's JSON ``column``."""
    rows = conn.execute(
        sa.text(f"SELECT {key}, {column}::text FROM {table} WHERE {column} IS NOT NULL")
    ).all()
    for ident, raw in rows:
        value = json.loads(raw)
        changed = path(value, factor)
        if changed:
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET {column} = CAST(:v AS json) WHERE {key} = :k"
                ),
                {"v": json.dumps(value), "k": ident},
            )


def _windows(value, factor: float) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for window in value:
        window["credits"] = window["credits"] * factor
    return True


def _pack(value, factor: float) -> bool:
    if not isinstance(value, dict) or not isinstance(
        value.get("compute_credits"), int | float
    ):
        return False
    value["compute_credits"] = value["compute_credits"] * factor
    return True


def _override(value, factor: float) -> bool:
    return isinstance(value, dict) and _pack(value.get("resource_pack"), factor)


def _rescale(factor: float) -> None:
    if factor == 1:
        return
    conn = op.get_bind()
    f = {"f": factor}
    conn.execute(
        sa.text(
            "UPDATE compute_grants SET credits_total = credits_total * :f, "
            "credits_used = credits_used * :f"
        ),
        f,
    )
    conn.execute(
        sa.text(
            "UPDATE plans SET credits_per_period = credits_per_period * :f "
            "WHERE credits_per_period IS NOT NULL"
        ),
        f,
    )
    _scale_json(conn, "plans", "key", "windows", _windows, factor)
    conn.execute(
        sa.text("UPDATE plan_window_use SET credits_used = credits_used * :f"), f
    )
    conn.execute(
        sa.text("UPDATE resource_usage SET credits = credits * :f WHERE credits <> 0"),
        f,
    )
    _scale_json(conn, "space_categories", "id", "resource_pack", _pack, factor)
    _scale_json(conn, "task", "id", "protocol_override", _override, factor)


def upgrade() -> None:
    _rescale(_old_price() / CREDIT_USD)


def downgrade() -> None:
    _rescale(CREDIT_USD / _old_price())
