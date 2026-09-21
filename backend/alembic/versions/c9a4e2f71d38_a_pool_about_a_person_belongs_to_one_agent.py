"""关于一个人的记忆，收进「哪个项目里的哪位芝士」名下

Revision ID: c9a4e2f71d38
Revises: 6c3f0a1d92b7
Create Date: 2026-09-21 12:00:00

``user`` 池以前是跨项目的：scope_id 就是那个人的 handle，一个池，谁都读同一份。
结论 8 把它改成 agent 实例自己的东西——关于某个人的判断是**某个项目里的某位芝士**
形成的看法，A 项目的芝士对他的判断，B 项目的芝士读不到。新键是
``<项目>:<agent handle>:<这个人的 handle>``。

**行上没有记着是谁观察到的**，所以这里要把那个事实补出来。补的依据是当初唯一可能
的写入路径：``add_memory`` 的 ``scope="user"`` 那一支要 ``_authorize_personal_memory_owner``
放行，而它只在「这个人自己的私聊」里放行。所以一条关于他的记忆，只可能是他某间私聊
对面那位芝士记下的——按他的私聊把行拆开，落到**对面那一席是谁**的名下。一间私聊都
没有的人，无从归属，才按计划落到他所在的每个项目的默认芝士名下。

**只复制，一行也不凭空丢**：一个人在两个项目里各有私聊，两个项目就各得一份。记忆
是人和 agent 显式写进去的、不可再生的（结论 61），而这两份的分歧只能靠它们各自往下
被整理来消除——这里少给一份，就是让某个项目的芝士从此不知道这件事，没有第二个地方
能把它找回来。复制完才删原行，且只删已经复制出去的那些：一个人不在任何项目、也没有
任何私聊，他那几行原样留着（新代码读不到，但留着，等 P36 的迁移再跑一遍）。

**幂等，而且要被原样再跑一遍**：dev 是先跑迁移后换容器，这一条跑完到新镜像起来之
间，旧镜像还在按 ``<person handle>`` 写新行——第一遍扫不到它们。复制那一步带
``NOT EXISTS``（同一个目标池里同样的内容不再插一遍），删那一步只删已经有副本的，
所以第二遍只花一次扫描。窗口里那批行在两遍之间只是读不到，没有被删（取舍第四条
第 2、3 点）。

降级不做：重键之后「这一行原本是跨项目那一个池里的」已经不在行上了，而把两个项目
各自的副本再并回一行，要知道它们是不是同一条——那个事实同样不在行上。
"""

from collections.abc import Callable, Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9a4e2f71d38"
down_revision: str | Sequence[str] | None = "6c3f0a1d92b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 每一个人该被拆到哪几个池里。
#:
#: 上半：他的每一间私聊——那是当初唯一写得进去的地方，而且**对面坐的不一定是项目
#: 默认那位**：``TopicService.get_or_create_private(..., agent_handle=...)`` 允许跟
#: 任意一位已保存的队友开私聊，读写两侧的池键用的都是对面那位的实例 handle。落到
#: 默认那位名下，就是把 reviewer 在它自己的私聊里记下的判断改记到一个没观察过这件
#: 事的芝士名下，而真正观察到的那位从此一条都读不到。
#:
#: 所以私聊这一支按**席位**反查实例：队友坐在私聊里用的是 ``cheese-<实例 id 前 12
#: 位十六进制>``（``agent_instance_handle``），和 ``b4d1a70c9e52`` 的 ``SEATS`` 同
#: 一套推导。反查不到实例的（旧的房间派生席位、人对人私聊）才落到项目默认那位名下
#: ——默认是兜底，不是规则。
#:
#: 一间私聊不止两席时，每一位坐在对面的队友各得一份：这种房间 ``private_seats`` 答
#: 不出对面是谁，读侧退回项目默认那位，而多给一份的代价只是多一条重复的记忆，少给
#: 一份是永久读不到（结论 61，记忆不可再生）。
#:
#: 下半：一间私聊都没有的人，他所在的每个项目的默认芝士（结论 4：每个项目自动有一
#: 个芝士实例）——这时候行上确实没有任何线索说是谁观察到的，默认那位是唯一一个
#: 「这个项目一定有」的答案。
#:
#: 「他所在的项目」要两问：``project_members`` **不存建项目的那个人**（谁是所有者
#: 记在 ``projects.owner_handle`` 上，``list_members`` 读的时候才把那一行补出来），
#: 只问成员表就会漏掉每一个项目的所有者——而他正是最可能被记下点什么的那个人。
PERSONAL_MEMORY_TARGETS = """
    CREATE TEMP TABLE cheese_personal_targets AS
    WITH in_a_dm AS (
        SELECT DISTINCT tm.member_handle AS person,
               t.project_id,
               seated.id AS agent_instance_id
          FROM topic_memberships tm
          JOIN topics t ON t.id = tm.topic_id AND t.is_private
          LEFT JOIN topic_memberships seat
                 ON seat.topic_id = t.id
                AND seat.member_handle <> tm.member_handle
          LEFT JOIN agent_instances seated
                 ON seated.project_id = t.project_id
                AND seat.member_handle
                    = 'cheese-' || left(replace(seated.id::text, '-', ''), 12)
    ),
    belongs AS (
        SELECT pm.user_handle AS person, pm.project_id
          FROM project_members pm
         UNION
        SELECT p.owner_handle, p.id
          FROM projects p
         WHERE p.owner_handle IS NOT NULL AND p.owner_handle <> ''
    )
    SELECT DISTINCT d.person,
           p.id::text || ':' || a.handle || ':' || d.person AS scope_id
      FROM in_a_dm d
      JOIN projects p ON p.id = d.project_id
      JOIN agent_instances a
        ON a.id = COALESCE(d.agent_instance_id, p.default_agent_instance_id)
     UNION
    SELECT DISTINCT b.person,
           p.id::text || ':' || a.handle || ':' || b.person
      FROM belongs b
      JOIN projects p ON p.id = b.project_id
      JOIN agent_instances a ON a.id = p.default_agent_instance_id
     WHERE NOT EXISTS (SELECT 1 FROM in_a_dm d WHERE d.person = b.person)
"""

#: 旧行复制到每一个目标池。
#:
#: ``position(':' in scope_id) = 0`` 认的是旧形状：一个 handle 里不会有冒号，而新
#: 键里至少有两个。已经重键过的行因此第二遍不会再被搬一次。
#:
#: ``retired_at`` / ``retired_by`` / ``created_by`` 一起抄过去：记忆整理退役掉的行
#: 是「芝士核对过、认定不再成立」的那一批，副本要是活的，等于把它们全部复活。
COPY_PERSONAL_MEMORY = """
    INSERT INTO memory_entries
           (id, scope, scope_id, content, layer,
            retired_at, retired_by, created_by, created_at, updated_at)
    SELECT gen_random_uuid(), 'user', tgt.scope_id, m.content, m.layer,
           m.retired_at, m.retired_by, m.created_by, m.created_at, now()
      FROM memory_entries m
      JOIN cheese_personal_targets tgt ON tgt.person = m.scope_id
     WHERE m.scope = 'user'
       AND position(':' in m.scope_id) = 0
       AND NOT EXISTS (
           SELECT 1
             FROM memory_entries c
            WHERE c.scope = 'user'
              AND c.scope_id = tgt.scope_id
              AND c.content = m.content)
"""

#: 已经有副本的旧行删掉。没有副本的（这个人不在任何项目、也没有任何私聊）留着。
#:
#: 用 ``right(...)`` 按长度截尾比，不用 ``LIKE '%:' || handle``：handle 里的 ``_``
#: 在 LIKE 里是通配符，``alice_b`` 会匹配到 ``aliceXb`` 的池，于是删掉一条其实没
#: 有副本的记忆。
DROP_COPIED_PERSONAL_MEMORY = """
    DELETE FROM memory_entries m
     WHERE m.scope = 'user'
       AND position(':' in m.scope_id) = 0
       AND EXISTS (
           SELECT 1
             FROM memory_entries c
            WHERE c.scope = 'user'
              AND position(':' in c.scope_id) > 0
              AND right(c.scope_id, length(m.scope_id) + 1) = ':' || m.scope_id
              AND c.content = m.content)
"""


#: 新键放不下的话，这条迁移自己就是第一个炸的：``COPY_PERSONAL_MEMORY`` 拼出来的
#: ``<项目>:<agent handle>:<人的 handle>`` 最长 36+1+64+1+64 = 166，而列是 128。
#: 旧键（一个 handle，或 ``<项目>:<agent handle>``，最长 101）一直在安全区，这 65
#: 个字符的余量是重键这一步新要的，所以放宽要排在搬行之前。
#:
#: PG 里放宽 varchar 长度只改目录，不重写表、不重建索引，所以它在发布窗口里不占
#: 时间——而列不够宽的后果是 ``alembic upgrade head`` 当场失败，部署停在换容器
#: 之前。
WIDEN_SCOPE_ID = 200


def rekey_personal_memory(execute: Callable[[str], object]) -> None:
    """三步，按顺序。``upgrade()`` 和
    ``tests/integration/test_a_pool_about_a_person_is_rekeyed.py`` 调的是同一个
    函数，所以用例跑的就是要发布的这一份，不是照着抄出来的一份。
    """
    execute(PERSONAL_MEMORY_TARGETS)
    execute(COPY_PERSONAL_MEMORY)
    execute(DROP_COPIED_PERSONAL_MEMORY)
    execute("DROP TABLE cheese_personal_targets")


def upgrade() -> None:
    op.alter_column(
        "memory_entries",
        "scope_id",
        existing_type=sa.String(128),
        type_=sa.String(WIDEN_SCOPE_ID),
        existing_nullable=False,
    )
    rekey_personal_memory(op.execute)


def downgrade() -> None:
    pass
