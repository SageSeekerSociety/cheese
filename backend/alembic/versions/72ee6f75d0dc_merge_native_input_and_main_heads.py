"""merge the grouped-ask native-input chain with main's heads

把 main 并进分组提问重建时又撞出一条：本分支那条头是 `e7b3c2d1a0f4`
（native input 的 work termination），main 那条头是 `f3a9c81d4e26`
（它自己已经把 board-teaching 与 plan-billing / materials 收成一条）。
两条头各自动的表不相交——这边是 native_input_*，那边是空间指导默认、
计费计划与共享资料库——所以合一条空迁移把两头收成一条线，没有任何
schema 改动。

Revision ID: 72ee6f75d0dc
Revises: e7b3c2d1a0f4, f3a9c81d4e26
Create Date: 2026-10-02
"""

from collections.abc import Sequence

revision: str = "72ee6f75d0dc"
down_revision: str | Sequence[str] | None = ("e7b3c2d1a0f4", "f3a9c81d4e26")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError("72ee6f75d0dc 无法反向，恢复整库转储")
