"""存量私聊的名册，收成正好两席

Revision ID: f1a9c3e07b42
Revises: d5c48f1a6b73
Create Date: 2026-09-21 12:00:00

私聊是项目内名册两席的房间（结论 19）。「对面是谁」以前有第二个出处，就是
``topics.private_owner`` 与 ``private_peer`` 两列；从本次发布起没有代码再读它们：
谁答这间房、个人记忆记在谁名下、哪些私聊算我的、未读按谁归类，全部从名册上取。

所以存量私聊里那些名册不是两席的，必须在读侧切过去之前收干净，否则它们在新代码眼里
就是「答不出对面是谁」的房间：AI 私聊会退回项目默认的那位芝士（换了默认就换了人），
个人记忆的读写会被拒，未读角标会整间房消失。

收的是两头：多出来的席位拿掉，缺的补回来。**次序是先拿掉、再补**，因为补那一步的闸
是「名册还不到两席」，它要数的是这一条自己删完之后的名册，不是删之前的。反过来跑，
[人, 替身] 这种两席的房间会被闸挡在外面，删完只剩一席，而补种那一步已经过去了。

补席位的输入是那两列，它们记的正是这件事，只是记错了地方。只 INSERT，
``ON CONFLICT DO NOTHING``，而且只补给名册还不到两席的房间。``e7d2b91a4c06`` 已经把
peer 那一席补过一遍，建私聊时 ``TopicMemberService.seed_private`` 也一直在写这两行，
所以绝大多数房间这里一行不动。「已经两席」那道闸省下的不是一次扫描，是不去动一间已
经答得出对面是谁的房间：换过队友的私聊，名册上是 [人, 队友B] 而 ``private_peer`` 还
停在队友A，没有这道闸就会把 A 插回来，而撤席位就是撤授权。

补不到两席的房间只剩两列这一个输入。一间被撤到只剩一席的私聊，和一间从来没写过席位
的私聊，在数据上分不出来，而两者今天都答不出对面是谁；补回去至少让它重新是一间私聊。

**多出来的席位得拿掉，而且是两席，不是一席**：两席是私聊的定义，不是它的下限。名册
超过两席，「对面是谁」就和没有名册一样答不出来。

今天真长成这样的是哪一批，上一条迁移的 SQL 说得很死。``d5c48f1a6b73`` 里
``SEAT_THE_PROJECTS_CHEESE`` 的 WHERE 只有一条「替身还坐着」，对**每一间**还坐着替身
的房间都插入项目默认那位芝士的席位，不分私聊与否；而同一条迁移的 ``ROOM_STAND_INS``
只在「房里没有别的 agent 实例」时才退役替身。两句合起来：替身今天还留在名册上的私
聊，必然同时坐着项目芝士，也必然坐着另一个 agent 实例（那正是替身没被退役的原因，
坐着别的就说不出替身站的是哪一个）。加上 ``e7d2b91a4c06`` 补的队友那一席，形状是四
席：[人, 队友的席位, 替身, 项目芝士的席位]。

反过来，[人, 替身] 那种两席的私聊已经不在了：没有别的实例坐着，``d5c48f1a6b73`` 当
场退役了替身、补上了项目芝士，它们早就是 [人, 项目芝士] 两席。

所以只删替身会停在三席上，答不出对面是谁的房间还是答不出，这一条就只动得到它修不好
的那一批。两席一起拿掉才收得干净：替身，以及 ``d5c48f1a6b73`` 刚插进来的那一席项目
芝士。剩下的 [人, 队友的席位] 才是这间 DM 从来就是的两位。

判据不能是那两列。两列和名册不一致的时候对的是名册，加席位、换席位、撤席位改的只有
名册那一份，而席位就是授权（``holds_an_agent_seat``）。照「不在这两列里就删」判，两
种真会发生的房间会被弄坏：换过队友的私聊（名册 [人, 队友B]，``private_peer`` 还留着
队友A）会被插回 A、删掉 B，一次真的撤销就这么被恢复；``private_peer`` 写着房间派生
替身的那些私聊（``e7d2b91a4c06`` 的第三种情况，当时项目还没有默认芝士）已经被
``d5c48f1a6b73`` 补上项目芝士的席位、退役了替身，照两列判就会把替身插回来、把项目芝
士那一席删掉，正好把那条迁移倒过来。补席位那一步因此也跳过替身：它被退役过，不该再
被种回去。

两条删各自的判据都只问名册：替身那一条问「这一行是不是这间房派生出来的替身」；项目
芝士那一条问「这间私聊的名册上替身还坐着，而且还坐着替身和项目芝士以外的 agent 席
位」，也就是 ``d5c48f1a6b73`` 当时说不出替身站的是哪一个的那一批。项目芝士自己就是
对面的那些私聊不在内：它们的替身早被退役了，这道闸的第一问就为假。

代价写在这里。拿着 ``cheese-<房间 hex12>`` 那张凭证的线下进程，在这些私聊里会从 200
变 403；拿着项目那张凭证的（本地 agent、bot、CI）也一样。``d5c48f1a6b73`` 补那一席
是为了别让项目凭证在房间里静默地变 403，而私聊是它管不到的地方：两席就是那份授权，
一间人和队友B 的 DM 本来也不该凭项目凭证读得到。被删席位的人看不见的东西本来也看不
见：上一版的话题列表对私聊认的是那两列，第三、第四席从来就没让谁看见过这间房。

``d5c48f1a6b73`` 的记忆重键在这里原样再跑一遍，那条迁移的 docstring 就是这么写的：
它跑完到容器换掉之间，上一版镜像还在按 ``<项目>:cheese-<房间 hex12>`` 写新行，第一
遍扫不到它们，而那之后没有代码再读那个键。记忆是显式写进去的、不可再生的（结论
61），所以这一遍把窗口里落下的那批搬过来。幂等，跑完第二遍零行。

降级不做，但删掉的行找得回来：删之前先抄进
``topic_memberships_unseated_f1a9c3e07b42``，要回滚就
``INSERT INTO topic_memberships SELECT * FROM topic_memberships_unseated_f1a9c3e07b42``。
补进去的那一半没有这个待遇，也不需要：新插的席位行和本来就该在的长得一模一样，分不
出哪些是这一条写的，而要回到上一版靠的是那两列还在，上一版读的就是它们。

发布前在 dev 的副本上对一遍：跑完
``SELECT count(*) FROM topics t WHERE t.is_private
  AND (SELECT count(*) FROM topic_memberships tm WHERE tm.topic_id = t.id) <> 2;``
必须是 0。
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

#: 私聊名册上那一行房间派生的替身。
A_ROOM_STAND_IN = f"""
       t.is_private
       AND tm.member_handle = {ROOM_STAND_IN}
"""

#: ``d5c48f1a6b73`` 插进这间私聊的那一席项目默认芝士。
#:
#: 三问都问名册：这一行是不是项目默认那位芝士；替身是不是还坐在这间房里；房里是不是
#: 还坐着替身和项目芝士以外的 agent 席位。后两问合起来正是 ``d5c48f1a6b73`` 当时说
#: 不出替身站的是哪一个、于是把替身留下来的那一批，也正是它照样插了项目芝士的那一
#: 批。项目芝士本来就是对面的那些私聊，第二问为假，一行不动。
#:
#: 替身那一行在它之后才删，这一问才问得到（``only_the_two_parties`` 的次序）。
A_STAND_IN_ERA_PROJECT_SEAT = f"""
       t.is_private
       AND tm.member_handle = (
           SELECT 'cheese-' || left(replace(a.id::text, '-', ''), 12)
             FROM projects p
             JOIN agent_instances a ON a.id = p.default_agent_instance_id
            WHERE p.id = t.project_id)
       AND EXISTS (
           SELECT 1 FROM topic_memberships s
            WHERE s.topic_id = t.id
              AND s.member_handle = {ROOM_STAND_IN})
       AND EXISTS (
           SELECT 1
             FROM topic_memberships o
             JOIN agent_instances other
               ON o.member_handle =
                  'cheese-' || left(replace(other.id::text, '-', ''), 12)
             JOIN projects p ON p.id = t.project_id
            WHERE o.topic_id = t.id
              AND other.id <> p.default_agent_instance_id)
"""

#: 私聊名册上要拿掉的那两行，按删的先后排：项目芝士那一行的判据问「替身还坐着吗」，
#: 所以它得赶在替身被删掉之前跑。
#:
#: 判据是名册，不是那两列：两列和名册不一致时对的是名册，照两列删会把换过的队友删
#: 掉、把退役过的替身留下来（docstring 第七段）。
#:
#: 幂等：跑完私聊名册上没有替身了，两条的第二遍都是零行。
UNSEAT_FROM_A_PRIVATE_ROSTER = (A_STAND_IN_ERA_PROJECT_SEAT, A_ROOM_STAND_IN)

#: 删之前先抄一份。CLAUDE.md 的「备份后再删」对迁移一样成立，而 ``downgrade()`` 不
#: 做，这张表就是那条回头路。
#:
#: 建表与抄行分开：``CREATE TABLE IF NOT EXISTS`` 让重跑不报错，而抄行那几句每一遍都
#: 照抄，所以不会出现「表已经在了，这一遍删掉的行没留下」。抄和删用的是同一份判据，
#: 走散不了。
BACK_UP_THE_UNSEATED = (
    """
    CREATE TABLE IF NOT EXISTS topic_memberships_unseated_f1a9c3e07b42
        (LIKE topic_memberships)
    """,
    *(
        f"""
    INSERT INTO topic_memberships_unseated_f1a9c3e07b42
    SELECT tm.*
      FROM topic_memberships tm
      JOIN topics t ON t.id = tm.topic_id
     WHERE {seat}
    """
        for seat in UNSEAT_FROM_A_PRIVATE_ROSTER
    ),
)

#: 抄完就删，一条判据一句。
UNSEAT_THE_EXTRA_SEATS = tuple(
    f"""
    DELETE FROM topic_memberships tm
     USING topics t
     WHERE tm.topic_id = t.id
       AND {seat}
    """
    for seat in UNSEAT_FROM_A_PRIVATE_ROSTER
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


def only_the_two_parties(execute) -> None:
    """名册上留下的正好是这间私聊的两位：多出来的席位拿掉，缺的补进来。``upgrade()``
    和 ``tests/integration/test_a_private_chats_two_seats_live_in_the_roster.py``
    调的是同一个函数，所以用例跑的就是真要发布的这份 SQL。

    次序有两处是死的。拿掉那两席里，项目芝士得赶在替身之前，它的判据要问替身还坐不
    坐着。补种整个放在最后，它的闸是「名册还不到两席」，要数的是删完之后的名册：反
    过来跑，[人, 替身] 这种两席的房间会被闸挡住，删完只剩一席，而补种已经过去了。

    中途崩在哪一句上都可以直接重跑：三步各自幂等，而重跑一遍的次序和第一遍一样。"""
    for statement in BACK_UP_THE_UNSEATED:
        execute(statement)
    for statement in UNSEAT_THE_EXTRA_SEATS:
        execute(statement)
    for statement in SEAT_THE_TWO_PARTIES:
        execute(statement)


def upgrade() -> None:
    only_the_two_parties(op.execute)
    op.execute(_agent_signs().REKEY_AGENT_MEMORY)


def downgrade() -> None:
    pass
