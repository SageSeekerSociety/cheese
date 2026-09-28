"""Merge Alembic heads：reviewed_by/at 与 project_member_exclusions 并回一个头

Revision ID: c1a2b3c4d5e6
Revises: b7f1c3d9a2e4, a3d1f0c72b94
Create Date: 2026-09-27

``b7f1c3d9a2e4``（题目记下「谁审的、什么时候审的」）与 ``a3d1f0c72b94``
（随 #1856 进来的 ``project_member_exclusions``）都长在 ``b380c2e8f60c`` 上 ——
前者在本分支写，后者在 main 写，各自那条链都只有一个头，合起来却是两个。两条都
已经推出去过，谁的身份都不能改写，所以这里补一个 merge revision 把它们并回一个
头：`alembic upgrade head` 拒绝分叉的链，不并这两条就连不上。

没有 schema 改动 —— 两条各自建各自的表，所以 upgrade/downgrade 都是空的。留着这
个文件只是为了让图连通，不是为了再动一次库。
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = ("b7f1c3d9a2e4", "a3d1f0c72b94")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
