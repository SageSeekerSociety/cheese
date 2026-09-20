"""agent_sessions: 一条会话记下自己的机器

地点从房间搬到会话（结论 56、60）。`topics.session_placement` 一列上混着两件事：
这条会话租的**工作机器**，和它的**进程在哪台会话机**。两件事各自会变，混在一列上
就只能一起换；挂在房间上，同一个房间的第二个 agent 连开都开不起来。

所以 `agent_sessions` 加三列：`work_lease`（这条会话的手）、`runtime_location`
（这条会话的进程在哪），以及 `placed_at`（上一次落位的时刻——房间那一块屏归最后
落位的会话，而 `updated_at` 是任何一列的写入时刻，存一次续接凭证就把它盖过去了）。
回填把老那一列拆开写进**认领它的那一条**会话行——骨架对得
上位置自己写的那个，同骨架多行时归最后动过的一条；房间那一条位置只属于一条会话，
摊给全部行会给换过骨架的房间编出一个假位置。只填 `runtime_location IS NULL` 的行，
所以重跑一遍结果不变——P19 在 `DROP COLUMN` 之前还要再跑同一条，接住本次部署窗口
里旧镜像写下的位置。

`resume_token` 同时改成可空：现在租到机器就先落行，而那一刻骨架还没有交回任何
可续的 token。「这个地方跑过没有」因此改判 `resume_token IS NOT NULL`，判据没变，
问的地方变了。

本次不 DROP `topics.session_placement`：dev 是先迁移后换容器（`deploy-docker.sh`），
迁移跑完到新镜像起来之间还有一个旧镜像在写那一列。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8a1d5e73f20"
down_revision: str | Sequence[str] | None = "b4e7a1c95d33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("work_lease", sa.JSON(), nullable=True))
    op.add_column(
        "agent_sessions", sa.Column("runtime_location", sa.JSON(), nullable=True)
    )
    # 落位的时刻。房间只有一块屏，归最后落位的那条会话——而 `updated_at` 是这一行
    # 上任何一列的写入时刻，每轮存 `resume_token` 都在动它，拿它排先后就把屏判给了
    # 最后说过话的那条会话。
    op.add_column(
        "agent_sessions",
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("agent_sessions", "resume_token", nullable=True)
    # 一个房间只有一条老位置，而 `agent_sessions` 按 (房间, agent, 骨架) 一行——
    # 换过队友或换过骨架的房间留着好几行。所以先认领：骨架要对得上那条位置自己
    # 写的（旧 `discover()` 的同一条判据，缺省是 claude-code），同骨架多行时归最
    # 后动过的那一条。摊给所有行会造出假的位置：中心通道按 `harness` 列过滤，那条
    # 假的 claude-code 行会让它去 restore 一块其实归 pi 的屏。
    #
    # 幂等：只碰还没有位置的行。`->` / `-` 要 jsonb，老那一列是 json，所以来回转一次。
    op.execute(
        """
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
    )


def downgrade() -> None:
    # 回滚前把位置写回房间那一列，否则降级后的镜像看不到本窗口里租到的机器。
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
    op.execute("DELETE FROM agent_sessions WHERE resume_token IS NULL")
    op.alter_column("agent_sessions", "resume_token", nullable=False)
    op.drop_column("agent_sessions", "placed_at")
    op.drop_column("agent_sessions", "runtime_location")
    op.drop_column("agent_sessions", "work_lease")
