"""topics 丢掉那一列混着会话和机器的位置

位置在 P18（`c8a1d5e73f20`）搬到了 `agent_sessions` 的 `work_lease` 与
`runtime_location` 两列上，`topics.session_placement` 从那时起全仓零写。这一条分
两步，顺序不能换：

1. 把 P18 那条回填一字不改地再跑一遍。dev 是先跑迁移后换容器
   （`deploy/deploy-docker.sh`），所以 P18 的迁移跑完到新镜像起来之间，还有一个
   旧镜像在往 `session_placement` 写位置——那批会话在新代码眼里没有位置。这一遍
   把它们接住。只碰 `runtime_location IS NULL` 的行，跑几遍结果都一样。
2. `DROP COLUMN`。

所以这一条要等 P18 部署过一轮之后才发：两步之间隔着一次发布，才轮得到「接住上
一个窗口」；紧跟着 P18 发，拿掉的就是一列正在被写的位置。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6b4c92a07d1"
down_revision: str | Sequence[str] | None = "a1286f09c001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: P18 那条回填，一字不改——这一遍接的是它跑完之后、旧镜像还在写的那个窗口。
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
    op.drop_column("topics", "session_placement")


def downgrade() -> None:
    op.add_column("topics", sa.Column("session_placement", sa.JSON(), nullable=True))
    # 降级后的镜像只认房间那一列，所以把会话上的位置写回去，否则它看不到任何一
    # 台已经租下的机器。
    op.execute(
        """
        UPDATE topics AS t
           SET session_placement = (
                   COALESCE(s.runtime_location::jsonb, '{}'::jsonb)
                   || jsonb_build_object('execution', s.work_lease::jsonb)
               )::json
          FROM agent_sessions AS s
         WHERE s.topic_id = t.id
           AND s.task_id IS NULL
           AND s.runtime_location IS NOT NULL
           AND s.work_lease IS NOT NULL
        """
    )
