"""A member's own agent runs on one model connection

Revision ID: 00cc083dc9f9
Revises: b7e4c2a9d5f1
Create Date: 2026-10-13

`own_agents.device_id`: the model connection a member's own agent runs on, the
login its owner's Claude Code has on that machine (`device_claude_login`, one
per machine). A person may now have one own agent per connection in a project,
each added by its owner.

Every existing one was made on read, one per person, and ran on whichever of
its owner's machines was online with a login. It is bound to the connection its
owner used last — the most recently confirmed login still in use, else the most
recently confirmed one at all — and named after it the way a new one is
(「<昵称>的 Claude Code（Max）」), so it keeps working where it worked. One whose
owner has no login anywhere stays unbound, and the room says so when it is
called. The table holds one row per person and project that turned the feature
on, so the backfill is two plain statements.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "00cc083dc9f9"
down_revision: str | Sequence[str] | None = "b7e4c2a9d5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "fk_own_agents_device_id_device"
_INDEX = "ix_own_agents_device_id"

# What tells one of a person's own agents from another, as
# `app.domain.agent_instance.own.label` computes it from the login row
# (`login_kind.connection_kind`: `model_service`, else a subscription when the
# method is `claude.ai` or a plan is named, else an API key).
_LABEL = """
    CASE
        WHEN l.auth_method = 'model_service'
            THEN coalesce(l.model, l.service_host, '自定义模型')
        WHEN l.auth_method = 'claude.ai' OR l.subscription_type IS NOT NULL
            THEN coalesce(
                upper(left(l.subscription_type, 1))
                || lower(substr(l.subscription_type, 2)),
                '订阅'
            )
        ELSE 'API'
    END
"""


def upgrade() -> None:
    # The new foreign key locks `device` as well as `own_agents`.
    with_lock_retries("own_agents, device")
    op.add_column("own_agents", sa.Column("device_id", sa.String(64), nullable=True))
    op.create_foreign_key(
        _CONSTRAINT,
        "own_agents",
        "device",
        ["device_id"],
        ["device_id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE own_agents VALIDATE CONSTRAINT {_CONSTRAINT}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.create_index(
            _INDEX, "own_agents", ["device_id"], postgresql_concurrently=True
        )
    op.execute(
        """
        UPDATE own_agents o SET device_id = pick.device_id
        FROM (
            SELECT DISTINCT ON (d.owner_user_id) d.owner_user_id, d.device_id
            FROM device d
            JOIN hosted_device h ON h.device_id = d.device_id
            JOIN device_claude_login l ON l.device_id = d.device_id
            WHERE d.supply = 'self_hosted'
            ORDER BY d.owner_user_id,
                     (l.logged_in AND l.revoked_at IS NULL) DESC,
                     l.checked_at DESC
        ) pick
        WHERE o.owner_user_id = pick.owner_user_id AND o.device_id IS NULL
        """
    )
    op.execute(
        f"""
        UPDATE agent_instances a
        SET display_name = left(a.display_name || '（' || {_LABEL} || '）', 64)
        FROM own_agents o
        JOIN device_claude_login l ON l.device_id = o.device_id
        WHERE a.id = o.instance_id
          AND l.logged_in AND l.revoked_at IS NULL
          AND a.display_name NOT LIKE '%）'
        """
    )


def downgrade() -> None:
    # The names keep their 「（…）」: the previous release renames its one own
    # agent per person after its owner's nickname as soon as it is read.
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="own_agents",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_constraint(_CONSTRAINT, "own_agents", type_="foreignkey")
    op.drop_column("own_agents", "device_id")
