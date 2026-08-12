"""merge device health quarantine + blocks topic index

main 自己分叉了（不是哪个分支带进来的）：`采纳 issue 186 (#301)` 的
b7e3c19d4f80 挂在 c8b1f4a70d29 上，而 `#289` 的 c1f7a3b90d24 挂在更早的
b8e1d4c70a92 上，两条在 b8e1d4c70a92 处岔开。两边都已经落在 main 上，按
`.claude/rules/migrations.md`，这种情况才用合并迁移（落地的迁移不可改）。
建之前查过：这对父节点还没有现成的合并迁移。

空的 upgrade/downgrade 是合并迁移的应有之义——它只接链，不改 schema。

Revision ID: f97f2d912795
Revises: b7e3c19d4f80, c1f7a3b90d24
Create Date: 2026-08-12 09:54:40.991297

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "f97f2d912795"
down_revision: str | Sequence[str] | None = ("b7e3c19d4f80", "c1f7a3b90d24")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
