"""A routine names the channels it covers

Revision ID: 9c1f4b7d2e35
Revises: 44de818574e0
Create Date: 2026-10-13

``routines.scope_topic_ids``: 执行范围 —— 这条规则管哪几个频道，一串频道 id。
以前它藏在 ``spec["scope"]`` 里，只有两个值（``room`` / ``project``），界面上
也就只能是个二选一；现在界面上是那排勾，勾了几个就写几个。

老的行按原意搬过来，行为不变：``room`` 是它自己那一个频道，``project`` 是
「不限定」（空数组 —— 事件那一侧照旧看整个项目）。搬完把 ``spec["scope"]``
摘掉：同一个意思不留在两处，读的人也就不会挑到旧的那一份。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9c1f4b7d2e35"
down_revision: str | Sequence[str] | None = "44de818574e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "routines",
        sa.Column(
            "scope_topic_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE routines SET scope_topic_ids = to_jsonb(ARRAY[topic_id::text]) "
        "WHERE spec ->> 'scope' = 'room'"
    )
    op.execute("UPDATE routines SET spec = spec - 'scope'")


def downgrade() -> None:
    op.execute(
        "UPDATE routines SET spec = spec || jsonb_build_object('scope', "
        "CASE WHEN scope_topic_ids = to_jsonb(ARRAY[topic_id::text]) "
        "THEN 'room' ELSE 'project' END)"
    )
    op.drop_column("routines", "scope_topic_ids")
