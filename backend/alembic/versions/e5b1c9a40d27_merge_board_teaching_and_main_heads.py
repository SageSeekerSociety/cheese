"""merge the board-teaching default with main's document-ai drop

两条头在把 main 并进来时撞上了：本分支那条到头是 `d2a8f04c71e5`（一块空间带一份
给 AI 队友的指导默认，落在 `c1f7a09b34d2` 空间资料库之上），main 那条到头是
`650de6af85af`（drop document ai）。两边都不该被砍，所以合一条空迁移把两头收成
一条线。没有任何 schema 改动。

Revision ID: e5b1c9a40d27
Revises: d2a8f04c71e5, 650de6af85af
Create Date: 2026-10-02
"""

from collections.abc import Sequence

revision: str = "e5b1c9a40d27"
down_revision: str | Sequence[str] | None = ("d2a8f04c71e5", "650de6af85af")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError("e5b1c9a40d27 无法反向，恢复整库转储")
