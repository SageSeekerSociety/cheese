"""topics 放掉 private_owner 与 private_peer 两列

Revision ID: b8e3f2a10c64
Revises: c9f41b7a2e08
Create Date: 2026-09-21 23:55:00

私聊是项目内名册两席的房间（结论 19），「对面是谁」只有名册一个出处。#1386 那一版
起没有代码再读写这两列：谁答这间房、个人记忆记在谁名下、哪些私聊算我的、未读按谁
归类，全部从 ``topic_memberships`` 上取。

留在表上的两列因此是第二份声明（I4a）：没有人写，所以它只会越来越旧；查得到，所以
下一个人照样会信；而它和名册对不上的时候对的从来是名册——加席位、换席位、撤席位改
的只有名册那一份，席位就是授权。

``DROP COLUMN`` 和 ``#1386`` 隔一次发布，不是隔一个提交，这也是 ``f1a9c3e07b42``
当时没有顺手把列删掉的原因：迁移先跑、容器后换，窗口里服务请求的还是上一版镜像，
它的 ``Topic`` 还映射着这两列，而一个映射着不存在的列的 ``select(Topic)`` 不是少答
一个字段，是整句 ``column topics.private_owner does not exist``。#1386 已经发布过
一轮，上一版镜像不再映射它们，这一条才跑得了。

**删之前先把 ``f1a9c3e07b42`` 的 ``only_the_two_parties`` 原样再跑一遍**，调的就是
那条迁移自己的函数，不是照抄一份 SQL。它当时跑完到 #1386 的容器换掉之间，旧镜像还
在建只写两列、不写席位行的私聊；那一批在名册上答不出对面是谁，而补席位的唯一输入正
是这两列，列一删就再也补不回来了。这一遍是它最后一次有输入。幂等（只 INSERT、
``ON CONFLICT DO NOTHING``、只补给名册还不到两席的房间），窗口里没有新私聊的话零行。

两句 ``DROP COLUMN`` 只动系统表，不重写 ``topics`` 的行。

降级不做：两列的值随这一条一起没了，把两个空列加回去给回的是列，不是答案，而答案在
名册上，一直都在。
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "b8e3f2a10c64"
down_revision: str | Sequence[str] | None = "c9f41b7a2e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TWO_SEATS = (
    Path(__file__).resolve().parent
    / "f1a9c3e07b42_a_private_chats_two_seats_live_in_the_roster.py"
)


def _two_seats():
    spec = importlib.util.spec_from_file_location("_two_seats_again", _TWO_SEATS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    _two_seats().only_the_two_parties(op.execute)
    for column in ("private_owner", "private_peer"):
        op.drop_column("topics", column)


def downgrade() -> None:
    """值跟着列一起没了，加两个空列回来给不回「对面是谁」。"""
