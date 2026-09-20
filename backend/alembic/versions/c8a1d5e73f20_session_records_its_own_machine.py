"""agent_sessions: 一条会话记下自己的机器

地点从房间搬到会话（结论 56、60）。`topics.session_placement` 一列上混着两件事：
这条会话租的**工作机器**，和它的**进程在哪台会话机**。两件事各自会变，混在一列上
就只能一起换；挂在房间上，同一个房间的第二个 agent 连开都开不起来。

所以 `agent_sessions` 加两列：`work_lease`（这条会话的手）与 `runtime_location`
（这条会话的进程在哪）。回填按 `topic_id` join 过去，把老那一列拆开写进已有的
会话行；只填 `runtime_location IS NULL` 的行，所以重跑一遍结果不变——P19 在
`DROP COLUMN` 之前还要再跑同一条，接住本次部署窗口里旧镜像写下的位置。

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
down_revision: str | Sequence[str] | None = "b7c2e91f4a03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("work_lease", sa.JSON(), nullable=True))
    op.add_column(
        "agent_sessions", sa.Column("runtime_location", sa.JSON(), nullable=True)
    )
    op.alter_column("agent_sessions", "resume_token", nullable=True)
    # 幂等：只碰还没有位置的行。`->` / `-` 要 jsonb，老那一列是 json，所以来回转一次。
    op.execute(
        """
        UPDATE agent_sessions AS s
           SET work_lease = (t.session_placement::jsonb -> 'execution')::json,
               runtime_location = (t.session_placement::jsonb - 'execution')::json
          FROM topics AS t
         WHERE t.id = s.topic_id
           AND s.task_id IS NULL
           AND s.runtime_location IS NULL
           AND t.session_placement IS NOT NULL
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
    op.drop_column("agent_sessions", "runtime_location")
    op.drop_column("agent_sessions", "work_lease")
