"""opening work leaves a branch, a card and an owner — and no second session

开一条活留下的是一个分支、一张卡和一个负责人（结论 31）。做它的是房间某条会话里
的一个原生子 agent，用的是父进程那双手（结论 43），所以它既不开第二条会话，也不
另租一份地点。一个 agent 在一个房间里因此只有一条会话，这条迁移把这句话立成库里
的一条唯一索引。

**这一次发布只做上一版镜像读得懂的那一半**：`deploy/deploy-docker.sh:570` 在换容器
（`:716`）之前跑迁移，中间还隔着一步给 2.2M 个文件换 uid，所以有分钟级的一段时间里
旧镜像面对新库。那一版的 `AgentSession` 与 `Task` 仍然映射着 `agent_sessions.task_id`
和 `tasks.transcripts_archived_at`，`select(AgentSession)` / `session.get(Task, …)` 一发
就是 `UndefinedColumn`：每一轮对话、每一次读卡都 500。两列因此原样留在这里，连同
`uq_agent_sessions_thread` 一起，由紧随其后、隔一次部署的那条迁移删掉。

留得下的只有加得进去的：`task_id IS NOT NULL` 的行先删掉 —— 那些行是活还是一个地点
的年代留下的，今天没有任何查询读得到它们（每条读路径都带着那句 `task_id IS NULL`），
平台也早已不写新的；不删的话它们会和同房间同 agent 的主线行在新索引上撞车。

索引用改名腾位子，不用第二个名字：旧镜像的 upsert 写着
`index_where="task_id IS NULL"`，ON CONFLICT 的推断认的是列加谓词而不是名字，所以那条
partial index 改叫 `uq_agent_sessions_room_task_null` 之后它照样推断得到；新镜像不带谓
词，只有不带谓词的索引才是候选，于是 `uq_agent_sessions_room` 这个名字从此归那条新的
plain unique index，下一条迁移只需删，不需要再改一次名。

Revision ID: c3b9e41f75a2
Revises: a7f2c4d86b13
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3b9e41f75a2"
down_revision: str | Sequence[str] | None = "a7f2c4d86b13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.opening_work_leaves_no_second_session")


def upgrade() -> None:
    # 数出来再删：一条带 `work_lease` 的老行说的是「当年有一台机器是按这条活租
    # 的」，行没了就没有第二处能说出它是哪一台。真删掉了这样的行，部署日志里至少
    # 还留着数目这条线索。
    doomed = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) AS rows, count(work_lease) AS leased "
                "FROM agent_sessions WHERE task_id IS NOT NULL"
            )
        )
        .mappings()
        .one()
    )
    logger.info(
        "opening_work_leaves_no_second_session: deleting %s thread session row(s), "
        "%s of them holding a work lease",
        doomed["rows"],
        doomed["leased"],
    )
    op.execute(sa.text("DELETE FROM agent_sessions WHERE task_id IS NOT NULL"))
    op.execute(
        sa.text(
            "ALTER INDEX uq_agent_sessions_room "
            "RENAME TO uq_agent_sessions_room_task_null"
        )
    )
    op.create_index(
        "uq_agent_sessions_room",
        "agent_sessions",
        ["topic_id", "agent_handle", "harness"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_agent_sessions_room", table_name="agent_sessions")
    op.execute(
        sa.text(
            "ALTER INDEX uq_agent_sessions_room_task_null "
            "RENAME TO uq_agent_sessions_room"
        )
    )
