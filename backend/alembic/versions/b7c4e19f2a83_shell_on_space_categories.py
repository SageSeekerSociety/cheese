"""let a 项目集 declare the 壳 its projects run under

壳 (shell) is the platform's answer to 「一套底座 + 不同的壳」: 办公 and 课程 are
the same base with a different home screen, navigation and 词表, instead of a
second frontend. A 壳 may only open, close, reorder and rename — it holds no
code (`app/domain/shell/catalog.py`).

It is declared where the 机构协议 is, for the same reason: 创研课 2026 秋 has
twenty 赛题 and one 壳, so a teacher configures it once. The value is a NAME
into the platform's catalog, not a declaration — a 项目集 picks a 壳, it cannot
ship one, which is what keeps 「加第五个壳」 a platform change rather than a
per-institution one. A single 赛题 may still override the whole key
(`task.protocol_override`, already a JSON column, so nothing to add there), and
a project's own `Project.settings["shell"]` outranks both (also already JSON).

Additive and nullable. NULL means nobody said, and `default` — today's
interface, screen for screen — is in force, so no row needs backfilling and
every existing 项目集 keeps behaving exactly as it does now.

Revision ID: b7c4e19f2a83
Revises: f3a8c5d2e917
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c4e19f2a83"
down_revision: str | Sequence[str] | None = "f3a8c5d2e917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("space_categories", sa.Column("shell", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("space_categories", "shell")
