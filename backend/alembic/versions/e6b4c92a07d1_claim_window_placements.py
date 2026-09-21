"""把部署窗口里写在房间上的位置，认领到它自己那条会话上

位置在 #1308（`c8a1d5e73f20`）搬到了 `agent_sessions` 的 `work_lease` 与
`runtime_location` 两列上。dev 是先跑迁移后换容器（`deploy/deploy-docker.sh`），
所以 #1308 的迁移跑完到新镜像起来之间，还有一个旧镜像在往 `topics.session_placement`
写位置——那批会话在新代码眼里没有位置。这一条把 #1308 那条回填一字不改地再跑一遍，
把它们接住；接住了，后端才敢不再回落去读房间那一列。

**`DROP COLUMN` 不在这一条里，它要等 owner 发过一轮。** app 的发布不换 device
connection owner 的镜像（`deploy-docker.sh` 逐字「leaving device connection owner
… running across this app release」），所以这次发布跑完，还有一个进程在读
`topics.session_placement`：列在这里掉，它服务的每一条房间命令都会报
`column topics.session_placement does not exist`，#1240 那次三小时、1218 次失败就是
这个形状。顺序是这一条先上线 → dispatch「Release device connection owner」
（environment=dev，ref=main）→ 再发一条只做 `DROP COLUMN` 的迁移。那一条落地之前
把这段回填再跑一遍，接住这中间又写下来的位置。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6b4c92a07d1"
down_revision: str | Sequence[str] | None = "a1286f09c001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: #1308 那条回填，一字不改——这一遍接的是它跑完之后、旧镜像还在写的那个窗口。
#: 认领的判据也和那次一样：骨架要对得上那条位置自己写的（缺省 claude-code），
#: 同骨架多行时归最后动过的一条。一个房间只有一条老位置，摊给所有行会造出假的
#: 位置，让中心通道去 restore 一块其实归别人的屏。
#:
#: 幂等：只碰还没有位置的行。`->` / `-` 要 jsonb，老那一列是 json，来回转一次。
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
