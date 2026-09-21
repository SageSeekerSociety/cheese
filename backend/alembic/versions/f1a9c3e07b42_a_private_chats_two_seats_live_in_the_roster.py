"""存量私聊的两席，补进名册

Revision ID: f1a9c3e07b42
Revises: d5c48f1a6b73
Create Date: 2026-09-21 12:00:00

私聊是项目内名册两席的房间（结论 19）。「对面是谁」以前有第二个出处——
``topics.private_owner`` 与 ``private_peer`` 两列——从本次发布起没有代码再读它们：
谁答这间房、个人记忆记在谁名下、哪些私聊算我的、未读按谁归类，全部从名册上取。

所以存量私聊里那些名册不全的，必须在读侧切过去之前补齐，否则它们在新代码眼里就
是「答不出对面是谁」的房间：AI 私聊会退回项目默认的那位芝士（换了默认就换了人），
个人记忆的读写会被拒，未读角标会整间房消失。补的输入就是那两列——它们记的正是这
件事，只是记错了地方。

只 INSERT，``ON CONFLICT DO NOTHING``：``e7d2b91a4c06`` 已经把 peer 那一席补过一
遍，建私聊时 ``TopicMemberService.seed_private`` 也一直在写这两行，所以绝大多数房
间这里一行不动。撤过席位的房间会被补回来——这一点和 ``d5c48f1a6b73`` 里那条「替身
还坐在名册上才动」的判断不同，因为这里补的是私聊的当事人：一间两席的房间撤掉一席
就不再是私聊，而不是「这个人被移出了房间」。

``d5c48f1a6b73`` 的记忆重键在这里原样再跑一遍，那条迁移的 docstring 就是这么写的：
它跑完到容器换掉之间，上一版镜像还在按 ``<项目>:cheese-<房间 hex12>`` 写新行，第一
遍扫不到它们，而那之后没有代码再读那个键。记忆是显式写进去的、不可再生的（结论
61），所以这一遍把窗口里落下的那批搬过来。幂等，跑完第二遍零行。

降级不做：补进去的席位行和本来就该在的席位行长得一模一样，分不出哪些是这一条写
的；而两列还在，这条再跑一遍就是了。
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "f1a9c3e07b42"
down_revision: str | Sequence[str] | None = "d5c48f1a6b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 两席各一条。房主那一席是 ``owner``——和 ``seed_private`` 写的角色逐字相同，
#: 因为读侧（``private_seats``）就是靠这个角色把两席分成「人」和「对面」的。
SEAT_THE_TWO_PARTIES = tuple(
    f"""
    INSERT INTO topic_memberships (id, topic_id, member_handle, role, created_at, updated_at)
    SELECT gen_random_uuid(), t.id, t.{column}, '{role}', now(), now()
      FROM topics t
     WHERE t.is_private AND t.{column} IS NOT NULL
    ON CONFLICT (topic_id, member_handle) DO NOTHING
    """
    for column, role in (("private_owner", "owner"), ("private_peer", "member"))
)

_REKEY = (
    Path(__file__).resolve().parent
    / "d5c48f1a6b73_an_agent_signs_with_its_instance_handle.py"
)


def _agent_signs():
    spec = importlib.util.spec_from_file_location("_agent_signs_again", _REKEY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seat_the_two_parties(execute) -> None:
    """两席补进名册。``upgrade()`` 和
    ``tests/integration/test_a_private_chats_two_seats_live_in_the_roster.py``
    调的是同一个函数，所以用例跑的就是真要发布的这份 SQL。"""
    for statement in SEAT_THE_TWO_PARTIES:
        execute(statement)


def upgrade() -> None:
    seat_the_two_parties(op.execute)
    op.execute(_agent_signs().REKEY_AGENT_MEMORY)


def downgrade() -> None:
    pass
