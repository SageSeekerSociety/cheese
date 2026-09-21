"""a 项目集 carries the 教学安排 its agents start inside

#8d772257: `teaching` — 本周范围 (`current_week` / `allowed_topics` /
`avoid_in_code`), a course-level system prompt 模板, and references to the 课件
and 知识材料 the week leans on.

It sits on the 项目集 for `resource_pack`'s reason (a1f4c73b5e28): 创研课 2026 秋
has twenty 赛题 and one 教学安排, so the week is configured once rather than
twenty times, with the same per-赛题 `protocol_override` for the one that
differs. It is deliberately not a table of its own — a second home for course
configuration would be a second place to look when the two disagreed.

Additive only, with a server default: an existing 项目集 has no teaching config,
which is exactly what "not a course" already meant, so nothing to backfill.

Revision ID: b7e4a1c9d0f2
Revises: d3b8f1c72a94
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4a1c9d0f2"
down_revision: str | Sequence[str] | None = "d3b8f1c72a94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "space_categories",
        sa.Column("teaching", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("space_categories", "teaching")
