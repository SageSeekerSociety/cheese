"""房间攒下的那些记忆，重键到 agent 自己名下

Revision ID: d5c48f1a6b73
Revises: b4d1a70c9e52
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

降级不做：把一位芝士的记忆再按房间劈开需要知道每条当初属于哪间房，而重键之后那个
事实已经不在行上了。
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "d5c48f1a6b73"
down_revision: str | Sequence[str] | None = "b4d1a70c9e52"
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
REKEY_AGENT_MEMORY = """
    UPDATE memory_entries AS m
       SET scope_id = p.id::text || ':' || a.handle,
           updated_at = now()
      FROM topics AS t
      JOIN projects AS p ON p.id = t.project_id
      JOIN agent_instances AS a ON a.id = p.default_agent_instance_id
     WHERE m.scope = 'agent_project'
       AND m.scope_id =
           p.id::text || ':cheese-' || left(replace(t.id::text, '-', ''), 12)
       AND m.scope_id <> p.id::text || ':' || a.handle
"""

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


def upgrade() -> None:
    # 先把席位补齐，再重键：重键读的是 `projects.default_agent_instance_id`，
    # 而补齐那一步正是把这个指针填上的地方。
    _seats().upgrade()
    op.execute(REKEY_AGENT_MEMORY)


def downgrade() -> None:
    pass
