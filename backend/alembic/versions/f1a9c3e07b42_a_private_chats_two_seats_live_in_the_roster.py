"""存量私聊的名册，收成正好两席

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

补席位只 INSERT，``ON CONFLICT DO NOTHING``：``e7d2b91a4c06`` 已经把 peer 那一席补
过一遍，建私聊时 ``TopicMemberService.seed_private`` 也一直在写这两行，所以绝大多数
房间这里一行不动。撤过席位的房间会被补回来——这一点和 ``d5c48f1a6b73`` 里那条「替身
还坐在名册上才动」的判断不同，因为这里补的是私聊的当事人：一间两席的房间撤掉一席
就不再是私聊，而不是「这个人被移出了房间」。

**补齐还不够，多的那些席位也得拿掉**：两席是私聊的定义，不是它的下限。名册超过两
席，「对面是谁」就和没有名册一样答不出来——AI 私聊退回项目默认那位（换了人，也换
了记忆池），个人记忆的读写被拒，未读角标整间消失。这样的房间今天就有：
``e7d2b91a4c06`` 给每间 AI 私聊补上了队友那一席，而 ``d5c48f1a6b73`` 在「房里坐着别
的实例」时跳过了删替身，于是留下 [人, 队友的席位, ``cheese-<房间 hex12>`` 替身] 这
种三席的私聊。``d5c48f1a6b73`` 那条顾虑（坐着别的就说不出替身站的是谁）在私聊里不
成立：两列已经指名了谁是当事人，剩下的席位按定义就是多余的。

代价写在这里：拿着 ``cheese-<房间 hex12>`` 那张凭证的线下进程，在这些私聊里会从 200
变 403——而这正是结论 19 要的，私聊只有两席，席位就是那份授权。被删席位的人看不见
的东西本来也看不见：上一版的话题列表对私聊认的是那两列，第三席从来就没让谁看见过
这间房。

``d5c48f1a6b73`` 的记忆重键在这里原样再跑一遍，那条迁移的 docstring 就是这么写的：
它跑完到容器换掉之间，上一版镜像还在按 ``<项目>:cheese-<房间 hex12>`` 写新行，第一
遍扫不到它们，而那之后没有代码再读那个键。记忆是显式写进去的、不可再生的（结论
61），所以这一遍把窗口里落下的那批搬过来。幂等，跑完第二遍零行。

降级不做：补进去的席位行和本来就该在的席位行长得一模一样，分不出哪些是这一条写
的；删掉的那些行连记都没地方记。要回到上一版，靠的是那两列还在——上一版读的就是
它们，这条再跑一遍就是了。
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

#: 名册上不是这两位的那些行，删掉。
#:
#: ``private_owner``/``private_peer`` 空着的房间一行不动：这两列是判断「谁是当事人」
#: 的唯一依据，空着不等于「名册上谁都不是当事人」，而是什么也判不出来——照删就是
#: 把一间私聊的名册清空。``e7d2b91a4c06`` 之后没有这样的房间，这道判断防的是它之外
#: 的来路，代价是一次白判。
#:
#: 幂等：跑完之后私聊名册上只剩这两位，第二遍零行。
UNSEAT_THE_REST = """
    DELETE FROM topic_memberships tm
     USING topics t
     WHERE tm.topic_id = t.id
       AND t.is_private
       AND t.private_owner IS NOT NULL
       AND t.private_peer IS NOT NULL
       AND tm.member_handle <> t.private_owner
       AND tm.member_handle <> t.private_peer
"""

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


def only_the_two_parties(execute) -> None:
    """名册上留下的正好是这间私聊的两位：缺的补进来，多的拿掉。``upgrade()`` 和
    ``tests/integration/test_a_private_chats_two_seats_live_in_the_roster.py``
    调的是同一个函数，所以用例跑的就是真要发布的这份 SQL。

    先补后删：两步都对着同一对列判，顺序不改变结果，而先补的话，中途崩在两步之间
    留下的是一间席位多了的房间，不是一间空名册的房间。"""
    for statement in SEAT_THE_TWO_PARTIES:
        execute(statement)
    execute(UNSEAT_THE_REST)


def upgrade() -> None:
    only_the_two_parties(op.execute)
    op.execute(_agent_signs().REKEY_AGENT_MEMORY)


def downgrade() -> None:
    pass
