"""私聊的两席只住在名册上：``topics`` 的那两列退场

Revision ID: b3f7a1c9d204
Revises: c5e7d2a91f30
Create Date: 2026-09-21 18:00:00

``f1a9c3e07b42`` 之后没有代码再读 ``topics.private_owner`` / ``private_peer``：
谁答这间房、个人记忆记在谁名下、哪些私聊算我的、未读按谁归类，全部从名册上取
（结论 19）。这一条把只写不读的那两列拿掉，同一件事就只剩名册一份声明。

**一条迁移里两步，次序是死的：先回填，再删列。**

回填是 ``f1a9c3e07b42`` 的 ``only_the_two_parties`` 原样再跑一遍——调的是它自己
那个函数，不是照抄一份 SQL。它幂等，在已经收干净的库上一行不动。要它，是因为
上一次发布到这一次之间有一段窗口：那段时间里旧镜像建的私聊只写了那两列，名册
上一行也没有。列一删，它们就再也说不出对面是谁，而那两列正是唯一还记着的地方。
所以回填必须在 ``DROP COLUMN`` 之前，且在同一条迁移里——分成两条，中间任何一次
中断都会留下一批答不出对面是谁的私聊。

删列没有回头路：``downgrade()`` 不做。要回到上一版，靠的是上一版代码建私聊时同
时写名册和这两列，而名册那一份这一条一个字没动。
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "b3f7a1c9d204"
down_revision: str | Sequence[str] | None = "c8f2a4e91d30"
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


#: 删列写成 SQL 而不是 ``op.drop_column``，这样回填和删列是同一个可调用的两步，
#: ``upgrade()`` 和用例跑的是同一份次序——测「先回填再删」的用例不必自己拼一遍。
DROP_THE_TWO_COLUMNS = (
    "ALTER TABLE topics DROP COLUMN private_peer",
    "ALTER TABLE topics DROP COLUMN private_owner",
)


def the_columns_go(execute) -> None:
    """先把两席回填到名册上，再把那两列删掉。次序见 docstring：反过来跑，窗口里
    旧镜像建的那批私聊就永远答不出对面是谁了。"""
    # 最后一遍回填：接住上一次发布的窗口里，旧镜像只写了两列、没写席位的私聊。
    _two_seats().only_the_two_parties(execute)
    for statement in DROP_THE_TWO_COLUMNS:
        execute(statement)


def upgrade() -> None:
    the_columns_go(op.execute)


def downgrade() -> None:
    pass
