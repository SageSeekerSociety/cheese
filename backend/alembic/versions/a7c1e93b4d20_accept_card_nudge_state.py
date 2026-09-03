"""accept_cards.nudge_state —— PR 回流的去重账本

「这批 CI 失败已经叫过芝士了」以前只能靠 note_code 这一个位子表达，于是它同时
是状态、是文案、也是去重键：另一件事写一次 note 就把去重键顶掉，一次 token 抖
动就能让同一批失败被重发。现在每一类回流各记自己的内容签名和轮数，互不打架。

存在列上而不是进程里，是因为它必须活过后端重启 —— 否则重启一次就是所有在飞的
PR 各被重新叫一遍。

server_default 是 '{}'：既有的行不能是 NULL，读账本的一方才不用先判空。

Revision ID: a7c1e93b4d20
Revises: e4c9a2f60b18
Create Date: 2026-08-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c1e93b4d20"
down_revision: str | Sequence[str] | None = "e4c9a2f60b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accept_cards",
        sa.Column(
            "nudge_state",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "nudge_state")
