"""topics 放掉 private_owner 与 private_peer 两列

Revision ID: b8e3f2a10c64
Revises: 6c3f0a1d92b7
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

两句 ``DROP COLUMN`` 只动系统表，不重写 ``topics`` 的行；删之前两列先抄进
``topics_private_parties_b8e3f2a10c64``。

降级不做：把两个空列加回去给回的是列，不是答案，而答案在名册上，一直都在。
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "b8e3f2a10c64"
down_revision: str | Sequence[str] | None = "6c3f0a1d92b7"
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


#: 删之前先抄一份。CLAUDE.md 的「备份后再删」对迁移一样成立，而 ``downgrade()`` 不
#: 做，这张表就是那条回头路 —— ``f1a9c3e07b42`` 删名册行之前抄
#: ``topic_memberships_unseated_f1a9c3e07b42``，同一个形状。
#:
#: 上面那遍回填只补给「名册还不到两席」的房间，所以它接不住已经两席、而两席和两列记
#: 的不是同一位的私聊：换过队友的，以及窗口里旧镜像写歪的。那些房间两列一删就再也查
#: 不回当时记的是谁，dev 上不可再生。
#:
#: 没有任何代码读这张表：它不是兼容层，也不是双写，是留底。
BACK_UP_THE_TWO_COLUMNS = """
    CREATE TABLE IF NOT EXISTS topics_private_parties_b8e3f2a10c64 AS
    SELECT id, private_owner, private_peer FROM topics WHERE is_private
"""


def upgrade() -> None:
    _two_seats().only_the_two_parties(op.execute)
    op.execute(BACK_UP_THE_TWO_COLUMNS)
    for column in ("private_owner", "private_peer"):
        op.drop_column("topics", column)


def downgrade() -> None:
    """加两个空列回来给不回「对面是谁」，那个答案在名册上。"""
