"""merge the board-teaching default with main's plan-billing kinds

第二次把 main 并进来时又撞出一条：本分支那条头仍是 `e5b1c9a40d27`（空间带一份给
AI 队友的指导默认），main 这次在 `650de6af85af` 之上接了 `b0caecc81d00`（计费计划
改成按月包或时间窗二选一，并带上 rank）。两头都不该被砍，所以再合一条空迁移把
两头收成一条线。没有任何 schema 改动。

Revision ID: f3a9c81d4e26
Revises: e5b1c9a40d27, b0caecc81d00
Create Date: 2026-10-02
"""

from collections.abc import Sequence

revision: str = "f3a9c81d4e26"
down_revision: str | Sequence[str] | None = ("e5b1c9a40d27", "b0caecc81d00")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
