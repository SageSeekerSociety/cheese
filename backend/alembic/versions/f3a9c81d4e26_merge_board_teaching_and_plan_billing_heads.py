"""merge the board-teaching default with main's plan-billing / materials heads

把 main 并进来时又撞出一条：本分支那条头仍是 `e5b1c9a40d27`（空间带一份给
AI 队友的指导默认），main 在 `650de6af85af` 之上接了 `b0caecc81d00`（计费计划
改成按月包或时间窗二选一，并带上 rank），再往上还有 `c1f7a09b34d2`（板上共享
资料库与两档可见性）。两头都不该被砍，所以合一条空迁移把两头收成一条线。没有
任何 schema 改动。

Revision ID: f3a9c81d4e26
Revises: e5b1c9a40d27, c1f7a09b34d2
Create Date: 2026-10-02
"""

from collections.abc import Sequence

revision: str = "f3a9c81d4e26"
down_revision: str | Sequence[str] | None = ("e5b1c9a40d27", "c1f7a09b34d2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError("f3a9c81d4e26 无法反向，恢复整库转储")
