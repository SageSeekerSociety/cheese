"""把部署窗口里写在房间上的位置，认领到它自己那条会话上

位置在 #1308（`c8a1d5e73f20`）搬到了 `agent_sessions` 的 `work_lease` 与
`runtime_location` 两列上。dev 是先跑迁移后换容器（`deploy/deploy-docker.sh`），
所以 #1308 的迁移跑完到新镜像起来之间，还有一个旧镜像在往 `topics.session_placement`
写位置——那批会话在新代码眼里没有位置。这一条把 #1308 那条回填再跑一遍，把它们
接住；接住了，后端才敢不再回落去读房间那一列。

**`DROP COLUMN` 不在这一条里，它要等 owner 发过一轮。** app 的发布不换 device
connection owner 的镜像（`deploy-docker.sh` 逐字「leaving device connection owner
… running across this app release」），所以这次发布跑完，还有一个进程在读
`topics.session_placement`：列在这里掉，它服务的每一条房间命令都会报
`column topics.session_placement does not exist`，#1240 那次三小时、1218 次失败就是
这个形状。顺序是这一条先上线 → dispatch「Release device connection owner」
（environment=dev，ref=main）→ 再发一条只做 `DROP COLUMN` 的迁移。这一条之后这一列
零写：新镜像全仓没有写点，旧镜像的 owner 只挂 `connector` 与 `execution` 两个路由，
两个都只读它。所以 `DROP COLUMN` 那一条不必再补跑这段回填。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6b4c92a07d1"
down_revision: str | Sequence[str] | None = "a7f1c0d4e2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: #1308 那条回填，接的是它跑完之后、旧镜像还在写的那个窗口。认领的判据和那次
#: 一样：骨架要对得上那条位置自己写的（缺省 claude-code），同骨架多行时归最后动过
#: 的一条。一个房间只有一条老位置，摊给多条会话会造出假的位置，让中心通道去
#: restore 一块其实归别人的屏。
#:
#: 比 #1308 多一条 `NOT EXISTS`，因为这一遍面对的库不一样：#1308 是和三个列一起
#: 落的，跑的那一刻全表 `runtime_location` 皆 NULL，`DISTINCT ON (s.topic_id)` 自己
#: 就保证了一房一条。这一遍不是——上一遍认领的那一条已经非空，被 `IS NULL` 排除，
#: 同房间里次一名的 NULL 行（`agent_sessions` 的唯一索引是 (房间, agent, 骨架)，
#: 换过队友的房间就有同骨架的第二行）会顶上来，把同一份位置——同一个 resource_id、
#: 同一份 work_lease——再写到一条死会话上。而 `placed_at = now()` 比房间里任何真
#: 位置都新，`placed_in_room`、`placed_everywhere`、ccproxy 选路都按 `placed_at DESC`
#: 判「这块屏归谁」，于是冷启动重认屏、LLM 代理选机器、工具调用准入会一起命中那条
#: 死会话。所以判据是按房间的：房间里已经有任何一条会话坐在机器上，这一条就不碰
#: 这个房间。
#:
#: 幂等：只碰还没有位置的行，且只碰整间房都还没有位置的房间。`->` / `-` 要 jsonb，
#: 老那一列是 json，来回转一次。
CLAIM_ROOM_PLACEMENTS = """
    WITH claimant AS (
        SELECT DISTINCT ON (s.topic_id) s.id, s.topic_id
          FROM agent_sessions AS s
          JOIN topics AS t ON t.id = s.topic_id
         WHERE s.task_id IS NULL
           AND s.runtime_location IS NULL
           AND t.session_placement IS NOT NULL
           AND s.harness = COALESCE(
                   t.session_placement::jsonb -> 'runtime' ->> 'harness',
                   'claude-code'
               )
           AND NOT EXISTS (
                   SELECT 1
                     FROM agent_sessions AS o
                    WHERE o.topic_id = s.topic_id
                      AND o.task_id IS NULL
                      AND o.runtime_location IS NOT NULL
               )
         ORDER BY s.topic_id, s.updated_at DESC, s.id
    )
    UPDATE agent_sessions AS s
       SET work_lease = (t.session_placement::jsonb -> 'execution')::json,
           runtime_location = (t.session_placement::jsonb - 'execution')::json,
           placed_at = now()
      FROM claimant AS c
      JOIN topics AS t ON t.id = c.topic_id
     WHERE s.id = c.id
"""


def upgrade() -> None:
    op.execute(CLAIM_ROOM_PLACEMENTS)


def downgrade() -> None:
    """回填没有逆，也不需要一个。

    认领到会话上的那份位置，正是降级后的旧镜像本来就该读到的那一份；而房间那一列
    这一条从头到尾没有动过，降级之后它还在原处，值也没变。
    """
