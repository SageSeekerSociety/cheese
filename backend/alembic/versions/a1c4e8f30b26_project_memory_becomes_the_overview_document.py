"""项目记忆池那批行，搬进项目总览的实况文档

Revision ID: a1c4e8f30b26
Revises: c9a4e2f71d38
Create Date: 2026-09-21 18:00:00

没有项目记忆池（结论 7）：人和 agent 共同看的只能是文档。``scope='project'`` 的行
是「写给所有人看」的那一路留下来的，它们的去向是**项目总览那个房间的实况文档**——
一份有主、留痕、谁都能改、每间房间读到同一份的东西，池子三样都没有。

**只搬不删。** 原清单写的是「同一事务里 INSERT 文档 + DELETE 记忆行」，这里只做前
一半，两个理由：

① 这批行是人和 agent 显式写进去的，不可再生（结论 61）。文档这一份的形状要是写错
   了，没有第二份可以对照着重来。
② dev 是先跑迁移后换容器。这一条跑完到新镜像起来之间，旧镜像还在往
   ``scope='project'`` 写新行，而新代码的读点已经全部切走——删了就是孤儿，留着还
   能由 P36b 再搬一遍。（取舍第四条第 3 点。）

所以 P36b 的三步是：再跑一遍这次的搬家、按内容逐行核对每一条都在目标文档里、全部
对上才 DELETE，然后从枚举里去掉这一档。

**幂等。** 同一条内容已经在目标文档里的，不再追加一遍（``position(...) = 0``）。所
以这条迁移在窗口里被原样再跑一遍，只多一次扫描。

**顺带再跑一遍 P35 的重键**，同样为了窗口：``c9a4e2f71d38`` 跑完之后、旧镜像停掉
之前写进来的那批 ``user`` 行还是老键（``<person handle>``），新代码读不到。那一条
的三步本来就写成幂等的，这里原样调它的函数——不是抄一份，抄出来的那份会和它分叉。

降级不做：文档是整块 markdown，搬进去的那几行与人自己写的字之间没有界线，撤不回来。
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "a1c4e8f30b26"
down_revision: str | Sequence[str] | None = "c9a4e2f71d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 一个项目的那批行，按写入顺序连成一段，追加到总览房间的实况文档末尾。
#:
#: 每一条带一行「（由记忆整理迁入）」：文档是给人看的，而这几行不是他写的，也不是
#: 这一轮谈出来的——不说明出处，读的人只会当成谁偷偷改了文档。
#:
#: 目标是 ``blocks`` 里总览房间那一条 ``kind='doc'``、``task_id IS NULL`` 的行，和
#: ``BlockRepository.doc_root`` 同一个判据（房间自己那条线上最早的那一块）；房间还
#: 没有文档的，这一步不建——建一份只有迁移内容的文档，等于把一个从来没人写过的房
#: 间的文档栏变成一张记忆清单。那批行留在表里，由 P36b 接着处理。
#:
#: ``retired_at IS NULL``：记忆整理退役掉的行是芝士核对过、认定不再成立的那一批，
#: 搬进文档等于把它们全部复活。
APPEND_PROJECT_MEMORY_TO_OVERVIEW = """
    WITH target AS (
        SELECT DISTINCT ON (b.topic_id)
               b.id, b.topic_id, p.id AS project_id
          FROM projects p
          JOIN blocks b
            ON b.topic_id = p.root_topic_id
           AND b.task_id IS NULL
           AND b.kind = 'doc'
         WHERE p.root_topic_id IS NOT NULL
         ORDER BY b.topic_id, b.created_at
    ),
    moved AS (
        SELECT t.id AS block_id,
               string_agg(m.content, E'\\n\\n' ORDER BY m.created_at, m.id) AS text
          FROM target t
          JOIN memory_entries m
            ON m.scope = 'project'
           AND m.scope_id = t.project_id::text
           AND m.retired_at IS NULL
          JOIN blocks b ON b.id = t.block_id
         WHERE position(m.content in b.content) = 0
         GROUP BY t.id
    )
    UPDATE blocks b
       SET content = rtrim(b.content, E' \\n')
                     || E'\\n\\n## 项目记忆（由记忆整理迁入）\\n\\n'
                     || moved.text,
           doc_version = b.doc_version + 1,
           updated_at = now()
      FROM moved
     WHERE b.id = moved.block_id
"""


#: P35 那条迁移的搬家函数。按文件路径加载，因为 ``alembic/versions`` 不是一个包
#: （而 ``alembic`` 这个名字已经是 alembic 自己），照抄一份 SQL 则会和它分叉。
_REKEY = Path(__file__).with_name(
    "c9a4e2f71d38_a_pool_about_a_person_belongs_to_one_agent.py"
)


def _rekey_personal_memory() -> None:
    spec = importlib.util.spec_from_file_location("_p35_rekey", _REKEY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.rekey_personal_memory(op.execute)


def upgrade() -> None:
    op.execute(APPEND_PROJECT_MEMORY_TO_OVERVIEW)
    _rekey_personal_memory()


def downgrade() -> None:
    pass
