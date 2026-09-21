"""two notification tables become one

Revision ID: c8d3a1e07f54
Revises: c9f41b7a2e08
Create Date: 2026-09-21 10:00:00

`alerts`（平台报告自己）和 `notification`（人对人）互不知道对方，于是「一个人被
@ 了而人不在页面上什么都收不到」不是哪一边的 bug，是两张表都只看得见自己那一半
（结论 58）。这条迁移把 `alerts` 需要的那几列加到 `notification` 上，再把它的行
搬过去。

**表和数据都留着不动**：搬家在窗口里扫不到旧镜像后写的那几行（那批不是「晚一点
到」，是再也不到），所以这条迁移写成幂等的，由 `DROP TABLE` 那一条在删表之前原样
再跑一遍。幂等靠的是 `notification.delivery_key` 的唯一约束：每一条搬过来的行带着
`alert:<原 uuid>:<收件人>`，重跑撞上约束什么也不做。

**广播在这里展开成一人一行。** `alerts` 里 `target_handle IS NULL` 表示「这条谁都
看得见」，而 `notification` 一行只对一个收件人 —— 所以一条广播搬成当时房间里每人
一行（没说房间的，按项目名册加上项目主人）。agent 不在里面：它在自己房间的时间线
上读到这件事，往它的收件箱里塞一行写的是一条谁都不会打开的记录
（`identity/arrival.py`）。点名给 agent 的那一条照搬，广播不展开到它头上。

DDL 全部写成可重跑的（`IF NOT EXISTS`），因为整条 `upgrade()` 要能再跑一遍。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d3a1e07f54"
down_revision: str | Sequence[str] | None = "c9f41b7a2e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: 把 `alerts` 的行搬进 `notification`，一个收件人一行。重跑是空操作。
#:
#: `left(handle, 7) <> 'cheese-'` 是 `identity/handles.looks_like_agent_handle`
#: 的 SQL 孪生，判据逐字相同（不写 LIKE：`%` 要跨 DBAPI 的参数风格转义）。
MOVE_ALERTS = sa.text(
    """
    INSERT INTO notification (
        id, receiver_id, recipient_handle, type, project_id, topic_id,
        level, title, body, metadata, read, resolved_at, feedback,
        is_aggregatable, finalized, delivery_key, version,
        created_at, updated_at
    )
    SELECT
        nextval('notification_seq'),
        (SELECT u.id FROM "user" u WHERE u.username = r.handle
          ORDER BY u.id LIMIT 1),
        r.handle,
        CASE a.kind WHEN 'mention' THEN 'MENTION' ELSE a.kind END,
        a.project_id,
        a.topic_id,
        a.level,
        a.title,
        a.body,
        a.payload::jsonb,
        a.read_at IS NOT NULL,
        a.resolved_at,
        a.feedback,
        false,
        true,
        'alert:' || a.id::text || ':' || r.handle,
        0,
        a.created_at,
        a.updated_at
    FROM alerts a
    JOIN LATERAL (
        SELECT DISTINCT s.handle
        FROM (
            SELECT a.target_handle AS handle
             WHERE a.target_handle IS NOT NULL
            UNION ALL
            SELECT tm.member_handle
              FROM topic_memberships tm
             WHERE a.target_handle IS NULL
               AND a.topic_id IS NOT NULL
               AND tm.topic_id = a.topic_id
            UNION ALL
            SELECT pm.user_handle
              FROM project_members pm
             WHERE a.target_handle IS NULL
               AND a.topic_id IS NULL
               AND pm.project_id = a.project_id
            UNION ALL
            SELECT p.owner_handle
              FROM projects p
             WHERE a.target_handle IS NULL
               AND a.topic_id IS NULL
               AND p.id = a.project_id
               AND p.owner_handle IS NOT NULL
        ) s
        WHERE s.handle IS NOT NULL
          AND s.handle <> ''
          AND (
              a.target_handle IS NOT NULL
              OR (s.handle <> 'cheese' AND left(s.handle, 7) <> 'cheese-')
          )
    ) r ON true
    ON CONFLICT (delivery_key) DO NOTHING
    """
)


def upgrade() -> None:
    # 收件人在这一行上有两个名字：名册上的 handle（投递这一侧认的就是它）和账号池
    # 里的那一行。handle 在账号池里找不到对应行时后者为空 —— 少一条知是那一侧读得
    # 到的记录，好过把一条本该送到的通知整条丢掉。
    op.execute("ALTER TABLE notification ALTER COLUMN receiver_id DROP NOT NULL")
    for column in (
        "ADD COLUMN IF NOT EXISTS recipient_handle varchar(64)",
        "ADD COLUMN IF NOT EXISTS project_id uuid "
        "REFERENCES projects(id) ON DELETE CASCADE",
        "ADD COLUMN IF NOT EXISTS topic_id uuid "
        "REFERENCES topics(id) ON DELETE CASCADE",
        "ADD COLUMN IF NOT EXISTS level varchar(16)",
        "ADD COLUMN IF NOT EXISTS title varchar(300)",
        "ADD COLUMN IF NOT EXISTS body text",
        "ADD COLUMN IF NOT EXISTS resolved_at timestamptz",
        "ADD COLUMN IF NOT EXISTS feedback varchar(8)",
    ):
        op.execute(f"ALTER TABLE notification {column}")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_recipient_handle "
        "ON notification (recipient_handle)"
    )
    # 项目收件箱的每一条读都带着这两列（我的信 + 这个项目），未读数还带 `read`。
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_project_recipient "
        "ON notification (project_id, recipient_handle, read)"
    )
    # 「这个房间 @ 过我没有、还未读没有」——话题列表的相关性一次查完。
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_topic_recipient "
        "ON notification (topic_id, recipient_handle)"
    )
    op.execute(MOVE_ALERTS)


def downgrade() -> None:
    # 搬过来的行认得出来（去重键以 `alert:` 开头），`alerts` 那一侧一个字没动，所以
    # 退回去就是把这些行删掉。
    op.execute("DELETE FROM notification WHERE delivery_key LIKE 'alert:' || '%'")
    op.execute("DROP INDEX IF EXISTS idx_notification_topic_recipient")
    op.execute("DROP INDEX IF EXISTS idx_notification_project_recipient")
    op.execute("DROP INDEX IF EXISTS ix_notification_recipient_handle")
    for column in (
        "feedback",
        "resolved_at",
        "body",
        "title",
        "level",
        "topic_id",
        "project_id",
        "recipient_handle",
    ):
        op.execute(f"ALTER TABLE notification DROP COLUMN IF EXISTS {column}")
    op.execute("UPDATE notification SET receiver_id = 0 WHERE receiver_id IS NULL")
    op.execute("ALTER TABLE notification ALTER COLUMN receiver_id SET NOT NULL")
