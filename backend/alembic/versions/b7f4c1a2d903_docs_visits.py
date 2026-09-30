"""docs_visits —— 文档站「谁来了」，一个访问者一天一行

Revision ID: b7f4c1a2d903
Revises: 7e4b9d2c1a60

The docs site is static files; a page load left no trace anywhere, so 「有多少
人读了文档」 had no answer and 「用问芝士的人占多少」 had no denominator. The
page now fires one beacon (`POST /docs/visit`), which lands here.

The table is one row per visitor per UTC **day**, and that is a promise the
schema keeps rather than one the insert path remembers: `uq_docs_visits_day_visitor`
is the only thing standing between a replayable beacon and a write amplifier.
`day` is a stored `date` column, not `date_trunc('day', created_at)`, because the
unique constraint has to be over a value that does not depend on the session's
time zone — this deployment's sessions are not UTC.

What is deliberately absent: no IP address, no user agent, no per-page trail.
`visitor_id` is `u:<user_id>` for a signed-in reader and `v:<random>` for one
who is not (the random value lives in that browser's localStorage); `user_id` is
kept beside it, `SET NULL` on account deletion, because 「其中登录用户多少」 is
a number the report prints.

Rows age out with `docs_questions` through the same sweep
(`docs_site.assistant.purge_old_questions`), on the same 90-day window.

## Downgrade

Drops the table and its index. Nothing else references it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7f4c1a2d903"
down_revision: str | Sequence[str] | None = "7e4b9d2c1a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "docs_visits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("visitor_id", sa.String(48), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("page", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("day", "visitor_id", name="uq_docs_visits_day_visitor"),
    )
    # The report's every query filters on the day window; the unique constraint
    # above is (day, visitor_id) and so cannot serve a bare range scan on day
    # alone past its leading column.
    op.create_index("ix_docs_visits_day", "docs_visits", ["day"])


def downgrade() -> None:
    op.drop_index("ix_docs_visits_day", table_name="docs_visits")
    op.drop_table("docs_visits")
