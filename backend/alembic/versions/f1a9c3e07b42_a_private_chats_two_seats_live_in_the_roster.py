"""存量私聊的名册，收成正好两席

Revision ID: f1a9c3e07b42
Revises: d5c48f1a6b73
Create Date: 2026-09-21 12:00:00

私聊是项目内名册两席的房间（结论 19）。「对面是谁」以前有第二个出处，就是
``topics.private_owner`` 与 ``private_peer`` 两列；从本次发布起没有代码再读它们：
谁答这间房、个人记忆记在谁名下、哪些私聊算我的、未读按谁归类，全部从名册上取。

所以存量私聊里那些名册不全的，必须在读侧切过去之前补齐，否则它们在新代码眼里就
是「答不出对面是谁」的房间：AI 私聊会退回项目默认的那位芝士（换了默认就换了人），
个人记忆的读写会被拒，未读角标会整间房消失。补的输入就是那两列，它们记的正是这
件事，只是记错了地方。

补席位只 INSERT，``ON CONFLICT DO NOTHING``，而且只补给名册还不到两席的房间。
``e7d2b91a4c06`` 已经把 peer 那一席补过一遍，建私聊时 ``TopicMemberService.seed_private``
也一直在写这两行，所以绝大多数房间这里一行不动。「已经两席」那道闸省下的不是一次扫
描，是不去动一间已经答得出对面是谁的房间：换过队友的私聊，名册上是 [人, 队友B] 而
``private_peer`` 还停在队友A，没有这道闸就会把 A 插回来，而撤席位就是撤授权。

补不到两席的房间只剩两列这一个输入。一间被撤到只剩一席的私聊，和一间从来没写过席位
的私聊，在数据上分不出来，而两者今天都答不出对面是谁；补回去至少让它重新是一间私聊。

**多出来的那一席也得拿掉，但只拿掉一种形状**：两席是私聊的定义，不是它的下限。名
册超过两席，「对面是谁」就和没有名册一样答不出来：AI 私聊退回项目默认那位（换了
人，也换了记忆池），个人记忆的读写被拒，未读角标整间消失。今天真长成这样的只有一
种房间：``e7d2b91a4c06`` 给每间 AI 私聊补上了队友那一席，而 ``d5c48f1a6b73`` 在「房
里坐着别的实例」时跳过了删替身，于是留下 [人, 队友的席位, ``cheese-<房间 hex12>``
替身] 这种三席的私聊。拿掉的就是这一种，判据是「这一行是不是这间房派生出来的替
身」。

判据不能是那两列。两列和名册不一致的时候对的是名册，加席位、换席位、撤席位改的只
有名册那一份，而席位就是授权（``holds_an_agent_seat``）。照「不在这两列里就删」判，
两种真会发生的房间会被弄坏：换过队友的私聊（名册 [人, 队友B]，``private_peer`` 还留
着队友A）会被插回 A、删掉 B，一次真的撤销就这么被恢复；``private_peer`` 写着房间派
生替身的那些私聊（``e7d2b91a4c06`` 的第三种情况，当时项目还没有默认芝士）已经被
``d5c48f1a6b73`` 补上项目芝士的席位、退役了替身，照两列判就会把替身插回来、把项目芝
士那一席删掉，正好把那条迁移倒过来。补席位那一步因此也跳过替身：它被退役过，不该
再被种回去。

代价写在这里：拿着 ``cheese-<房间 hex12>`` 那张凭证的线下进程，在这些私聊里会从 200
变 403，而这正是结论 19 要的，私聊只有两席，席位就是那份授权。被删席位的人看不见
的东西本来也看不见：上一版的话题列表对私聊认的是那两列，第三席从来就没让谁看见过
这间房。

``d5c48f1a6b73`` 的记忆重键在这里原样再跑一遍，那条迁移的 docstring 就是这么写的：
它跑完到容器换掉之间，上一版镜像还在按 ``<项目>:cheese-<房间 hex12>`` 写新行，第一
遍扫不到它们，而那之后没有代码再读那个键。记忆是显式写进去的、不可再生的（结论
61），所以这一遍把窗口里落下的那批搬过来。幂等，跑完第二遍零行。

降级不做，但删掉的行找得回来：删之前先抄进
``topic_memberships_unseated_f1a9c3e07b42``，要回滚就
``INSERT INTO topic_memberships SELECT * FROM topic_memberships_unseated_f1a9c3e07b42``。
补进去的那一半没有这个待遇，也不需要：新插的席位行和本来就该在的长得一模一样，分不
出哪些是这一条写的，而要回到上一版靠的是那两列还在，上一版读的就是它们。
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "f1a9c3e07b42"
down_revision: str | Sequence[str] | None = "d5c48f1a6b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 这间房派生出来的那个替身席位 ``cheese-<房间 hex12>``：``e7d2b91a4c06`` 的第三种
#: 情况把它写进了 ``private_peer``，``d5c48f1a6b73`` 又把它从名册上退役了。
ROOM_STAND_IN = "'cheese-' || left(replace(t.id::text, '-', ''), 12)"

#: 两席各一条。房主那一席是 ``owner``，和 ``seed_private`` 写的角色逐字相同，
#: 因为读侧（``private_seats``）就是靠这个角色把两席分成「人」和「对面」的。
#:
#: 只补给还不到两席的房间：已经两席的名册就是答案，动它就是把一次换队友倒回去。
#: 两句各自判一次，所以空名册的房间第一句补上房主、第二句仍然在两席之内补上对面。
#:
#: 也跳过替身：它已经被 ``d5c48f1a6b73`` 退役，种回去就是把那条迁移倒过来。
SEAT_THE_TWO_PARTIES = tuple(
    f"""
    INSERT INTO topic_memberships (id, topic_id, member_handle, role, created_at, updated_at)
    SELECT gen_random_uuid(), t.id, t.{column}, '{role}', now(), now()
      FROM topics t
     WHERE t.is_private AND t.{column} IS NOT NULL
       AND t.{column} <> {ROOM_STAND_IN}
       AND (SELECT count(*) FROM topic_memberships tm
             WHERE tm.topic_id = t.id) < 2
    ON CONFLICT (topic_id, member_handle) DO NOTHING
    """
    for column, role in (("private_owner", "owner"), ("private_peer", "member"))
)

#: 删之前先抄一份。CLAUDE.md 的「备份后再删」对迁移一样成立，而 ``downgrade()`` 不
#: 做，这张表就是那条回头路。
#:
#: 建表与抄行分成两句：``CREATE TABLE IF NOT EXISTS`` 让重跑不报错，而抄行那句每一
#: 遍都照抄，所以不会出现「表已经在了，这一遍删掉的行没留下」。
BACK_UP_THE_UNSEATED = (
    """
    CREATE TABLE IF NOT EXISTS topic_memberships_unseated_f1a9c3e07b42
        (LIKE topic_memberships)
    """,
    f"""
    INSERT INTO topic_memberships_unseated_f1a9c3e07b42
    SELECT tm.*
      FROM topic_memberships tm
      JOIN topics t ON t.id = tm.topic_id
     WHERE t.is_private
       AND tm.member_handle = {ROOM_STAND_IN}
    """,
)

#: 私聊名册上那一行房间派生的替身，删掉。
#:
#: 判据是「这一行是不是这间房派生出来的替身」，不是「这一行在不在那两列里」：两列
#: 和名册不一致时对的是名册，照两列删会把换过的队友删掉、把退役过的替身留下来
#: （docstring 第五段）。
#:
#: 幂等：跑完之后私聊名册上没有替身了，第二遍零行。
UNSEAT_THE_ROOM_STAND_IN = f"""
    DELETE FROM topic_memberships tm
     USING topics t
     WHERE tm.topic_id = t.id
       AND t.is_private
       AND tm.member_handle = {ROOM_STAND_IN}
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
    """名册上留下的正好是这间私聊的两位：缺的补进来，退役过的替身拿掉。``upgrade()``
    和 ``tests/integration/test_a_private_chats_two_seats_live_in_the_roster.py``
    调的是同一个函数，所以用例跑的就是真要发布的这份 SQL。

    两步互不相干：补那一步跳过替身，删那一步只认替身，所以中途崩在哪一句上，剩下的
    都是可以直接重跑的状态。"""
    for statement in SEAT_THE_TWO_PARTIES:
        execute(statement)
    for statement in BACK_UP_THE_UNSEATED:
        execute(statement)
    execute(UNSEAT_THE_ROOM_STAND_IN)


def upgrade() -> None:
    only_the_two_parties(op.execute)
    op.execute(_agent_signs().REKEY_AGENT_MEMORY)


def downgrade() -> None:
    pass
