"""房间攒下的那些记忆，重键到 agent 自己名下

Revision ID: d5c48f1a6b73
Revises: d3b8f1c72a94
Create Date: 2026-09-21 10:00:00

记忆以前按房间记：一间房的芝士写进 ``agent_project`` 池，scope_id 是
``<项目>:cheese-<房间 id 前 12 位 hex>``——一个从**房间**派生的名字。房间不是参与
者，一间房可以坐好几个 agent，同一个 agent 也会进好几间房，所以那个名字既会把两个
agent 叫成同一个，又会把同一个 agent 在两间房里叫成两个（结论 31、32）。今天起
agent 只有一个名字，从它自己派生，它签的字、它的席位、它的池全用这一个。

这一条把存量搬过来：那些房间池的行改写到项目那位芝士自己的池
（``<项目>:<agent_instances.handle>``）。目标池通常已经有行了——那是同一位芝士在
新键下写的——两边就此并成一个池，这正是要的结果：一个项目里的芝士只有一份记忆。

**先迁数据，再删读侧。**本次发布之后没有任何代码再读 ``cheese-<房间 hex>`` 那个键
（``legacy_topic_pool`` 与它的两个调用点一起删掉了），所以扫不到的行就是再也读不到
的行。记忆是人和 agent 显式写进去的、不可再生的（结论 61），而 dev 是先跑迁移后换
容器：这一条跑完到新镜像起来之间，旧镜像还在往房间键上写新行，第一遍扫不到它们。
所以这段 SQL 写成幂等的，由 P12 的迁移原样再跑一遍；两次之间那批行只是读不到，没有
被删。

``b4d1a70c9e52`` 的回填在这里原样再跑一遍，它自己的 docstring 就是这么写的：它跑完
到容器换掉之间，旧镜像还在建「有实例行、有指针、没有席位」的项目，而从今天起没有
席位的房间答不出「我是谁」——不是回落到一个房间派生的名字，是没有答案。它幂等，
第二遍只花一次扫描。

**替身退役从总览放开到每一间房**：``b4d1a70c9e52`` 只解析总览
（``WHERE s.root_topic_id IS NOT NULL``），因为它要办的事是「项目有它的芝士和一个
席位」。而 ``aeb21133e`` 之前建的**每一间**房，名册上坐的都是那间房派生出来的替身
``cheese-<房间 hex12>``，项目自己那位芝士在那些房里根本没有席位。从今天起名册就是
全部答案（``holds_an_agent_seat`` 不再给项目凭证留例外），席位也是「我是谁」的唯一
出处，所以那些房里：线下那张项目凭证会从 200 变 403，``resolve_agent_handle`` 也还
是答出替身的名字——署名、commit identity、令牌的 ``a`` 于是继续用房间派生的名字。
所以这里把那套形状（改署名、并反应、删替身行）对每一间房再跑一遍。判据和
``b4d1a70c9e52`` 一样：替身还在名册上，且这间房没有坐着别的 agent 实例——坐着别的
就说不出替身站的是谁，那几步一行不动。

**补席位那一步不受这个判据管**，它对每一间还坐着替身的房间都跑。有歧义的只是「替
身站的是哪一个」；「项目的芝士该不该有席位」没有歧义，答案在哪一间房里都是该有。
跳过它，那批房间里线下那张项目凭证就会从 200 变 403，而且没有任何报错指向原因——
和下面 ``REPOINT_PROJECT_MEMBER`` 要挡的是同一件事。补的是一行席位，改署名和删替
身那几步照旧跳过。

项目名册上那一行也跟着重指：项目凭证认证成的名字从 ``cheese-<根房间 hex12>`` 换成
了芝士自己的 handle，而 ``project_members.user_handle`` 是个字符串。不重指，旧那行
授的项目级访问（``authorize_topic_access`` 的 ``is_project_member`` 那一档）就悄没声
地作废了，还没有任何报错指向原因。

降级不做：把一位芝士的记忆再按房间劈开需要知道每条当初属于哪间房，而重键之后那个
事实已经不在行上了。
"""

import importlib.util
from collections.abc import Callable, Sequence
from pathlib import Path

from alembic import op

revision: str = "d5c48f1a6b73"
down_revision: str | Sequence[str] | None = "d3b8f1c72a94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 把房间池的行搬到项目那位芝士自己的池上。
#:
#: 判据是「这个 hex12 是不是本项目里某间房的 id」，不是「长得像不像 cheese-<hex>」：
#: agent 自己的席位 handle 也长这样（``agent_instance_handle``），一个项目里的人还
#: 可以把队友取名叫 ``cheese-abcdef123456``。对着 ``topics`` 认，认错的余地就只剩
#: 「某间房的 id 前 12 位刚好等于某个队友的 handle 后半截」，而最后那一句 ``<>``
#: 把那种情况也挡在外面——它只会让这条 UPDATE 不动那一行，不会把两个池搅在一起。
#:
#: 幂等：跑完之后没有一行的 scope_id 还等于某间房派生出来的键，所以第二遍零行。
#:
#: ``':' || 'cheese-'`` 拆成两段不是排版：这段 SQL 走 ``sqlalchemy.text()``，
#: ``':cheese-'`` 会被当成一个名叫 cheese 的绑定参数，跑起来直接报「A value is
#: required for bind parameter 'cheese'」。
REKEY_AGENT_MEMORY = """
    UPDATE memory_entries AS m
       SET scope_id = p.id::text || ':' || a.handle,
           updated_at = now()
      FROM topics AS t
      JOIN projects AS p ON p.id = t.project_id
      JOIN agent_instances AS a ON a.id = p.default_agent_instance_id
     WHERE m.scope = 'agent_project'
       AND m.scope_id = p.id::text || ':' || 'cheese-'
           || left(replace(t.id::text, '-', ''), 12)
       AND m.scope_id <> p.id::text || ':' || a.handle
"""

#: 每一间房里那条「唯一的 agent 席位就是本房间派生的替身」。
#:
#: 判据与 ``b4d1a70c9e52`` 的 ``STAND_INS`` 逐字相同，只是不再限于总览：替身还坐在
#: 名册上，且这间房没有别的 agent 实例的席位。坐着别的就说不出替身站的是哪一个，
#: 那一行留着不动——和 ``f3a8c5d2e917`` 当时的判断一致。
#:
#: 线程不会进这张表：线程没有自己的名册，``EXISTS`` 那一段就为假。
ROOM_STAND_INS = """
    CREATE TEMP TABLE cheese_room_stand_ins AS
    SELECT t.id AS topic_id,
           'cheese-' || left(replace(t.id::text, '-', ''), 12) AS stand_in,
           'cheese-' || left(replace(a.id::text, '-', ''), 12) AS own
      FROM topics t
      JOIN projects p ON p.id = t.project_id
      JOIN agent_instances a ON a.id = p.default_agent_instance_id
     WHERE EXISTS (
           SELECT 1 FROM topic_memberships tm
            WHERE tm.topic_id = t.id
              AND tm.member_handle =
                  'cheese-' || left(replace(t.id::text, '-', ''), 12))
       AND NOT EXISTS (
           SELECT 1
             FROM topic_memberships tm
             JOIN agent_instances other
               ON tm.member_handle =
                  'cheese-' || left(replace(other.id::text, '-', ''), 12)
            WHERE tm.topic_id = t.id
              AND other.id <> a.id)
"""

#: 替身说过的话记到芝士自己名下。
RETIRE_ROOM_STAND_INS = (
    """
    UPDATE blocks b SET author = m.own
      FROM cheese_room_stand_ins m
     WHERE b.topic_id = m.topic_id AND b.author = m.stand_in
    """,
    # 芝士已经用自己的席位留过的那个表情，胜过替身留的同一个。
    """
    DELETE FROM block_reactions br
     USING cheese_room_stand_ins m, blocks b
     WHERE b.id = br.block_id AND b.topic_id = m.topic_id
       AND br.author = m.stand_in
       AND EXISTS (SELECT 1 FROM block_reactions o
                    WHERE o.block_id = br.block_id AND o.emoji = br.emoji
                      AND o.author = m.own)
    """,
    """
    UPDATE block_reactions br SET author = m.own
      FROM cheese_room_stand_ins m, blocks b
     WHERE b.id = br.block_id AND b.topic_id = m.topic_id
       AND br.author = m.stand_in
    """,
    """
    DELETE FROM topic_memberships tm
     USING cheese_room_stand_ins m
     WHERE tm.topic_id = m.topic_id AND tm.member_handle = m.stand_in
    """,
)

#: 项目自己那位芝士，在每一间还坐着存量替身的房间里补上席位。
#:
#: 这一步不问房里还坐着谁。``ROOM_STAND_INS`` 要答的是「替身站的是哪一个」，房里坐
#: 着别的 agent 实例就答不出，那批房间它一行不动；而这一步要答的是「项目的芝士该不
#: 该有席位」，那个问题在每一间房里都是同一个答案。从今天起名册就是全部答案
#: （``holds_an_agent_seat`` 不再给项目凭证留例外），没有席位就是 403，所以漏掉那批
#: 房间等于让线下那张项目凭证（本地 agent、bot、CI）在那里静默地从 200 变 403。
#:
#: 判据是替身**还坐在**名册上：替身已经被人撤掉的房间不在内，那是一次真的撤席位，
#: 补回去就把「撤席位即撤授权」又撤销了。
#:
#: 用户行、execution binding 和展示资料上一步已经补好了（``cheese_seats`` 不挑房间）。
#:
#: 幂等：``ON CONFLICT DO NOTHING``。
SEAT_THE_PROJECTS_CHEESE = """
    INSERT INTO topic_memberships (id, topic_id, member_handle, role, created_at, updated_at)
    SELECT gen_random_uuid(), t.id,
           'cheese-' || left(replace(a.id::text, '-', ''), 12),
           'member', now(), now()
      FROM topics t
      JOIN projects p ON p.id = t.project_id
      JOIN agent_instances a ON a.id = p.default_agent_instance_id
     WHERE EXISTS (
           SELECT 1 FROM topic_memberships tm
            WHERE tm.topic_id = t.id
              AND tm.member_handle =
                  'cheese-' || left(replace(t.id::text, '-', ''), 12))
    ON CONFLICT (topic_id, member_handle) DO NOTHING
"""

#: 项目名册上那一行：项目凭证以前认证成 ``cheese-<根房间 hex12>``，现在认证成芝士
#: 自己的 handle。先删掉会撞唯一约束的那种情况（两行都在，留芝士自己那行），再把
#: 剩下的重指过去。
REPOINT_PROJECT_MEMBER = (
    """
    DELETE FROM project_members pm
     USING projects p, agent_instances a
     WHERE pm.project_id = p.id
       AND a.id = p.default_agent_instance_id
       AND p.root_topic_id IS NOT NULL
       AND pm.user_handle =
           'cheese-' || left(replace(p.root_topic_id::text, '-', ''), 12)
       AND EXISTS (
           SELECT 1 FROM project_members own
            WHERE own.project_id = p.id
              AND own.user_handle =
                  'cheese-' || left(replace(a.id::text, '-', ''), 12))
    """,
    """
    UPDATE project_members pm
       SET user_handle = 'cheese-' || left(replace(a.id::text, '-', ''), 12),
           updated_at = now()
      FROM projects p
      JOIN agent_instances a ON a.id = p.default_agent_instance_id
     WHERE pm.project_id = p.id
       AND p.root_topic_id IS NOT NULL
       AND pm.user_handle =
           'cheese-' || left(replace(p.root_topic_id::text, '-', ''), 12)
    """,
)

_SEAT_BACKFILL = (
    Path(__file__).resolve().parent
    / "b4d1a70c9e52_a_project_has_its_cheese_and_its_seat.py"
)


def _seats():
    spec = importlib.util.spec_from_file_location(
        "_cheese_seat_backfill", _SEAT_BACKFILL
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def retire_stand_ins(execute: Callable[[str], object]) -> None:
    """替身退役那几步，按顺序。``upgrade()`` 和
    ``tests/integration/test_the_stand_in_leaves_every_room.py`` 调的是同一个函数，
    所以用例跑的就是真要发布的这份顺序，不是照着抄出来的一份。"""
    execute(SEAT_THE_PROJECTS_CHEESE)
    execute(ROOM_STAND_INS)
    for statement in RETIRE_ROOM_STAND_INS:
        execute(statement)
    execute("DROP TABLE cheese_room_stand_ins")
    for statement in REPOINT_PROJECT_MEMBER:
        execute(statement)


def upgrade() -> None:
    # 先把席位补齐，再退役替身、再重键：后两步读的都是
    # `projects.default_agent_instance_id`，而补齐那一步正是把这个指针填上的地方。
    _seats().upgrade()
    retire_stand_ins(op.execute)
    op.execute(REKEY_AGENT_MEMORY)


def downgrade() -> None:
    pass
