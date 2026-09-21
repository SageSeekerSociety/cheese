"""two notification tables become one

Revision ID: c8d3a1e07f54
Revises: c2e4a6b80193
Create Date: 2026-09-21 10:00:00

`alerts`（平台报告自己）和 `notification`（人对人）互不知道对方，于是「一个人被
@ 了而人不在页面上什么都收不到」不是哪一边的 bug，是两张表都只看得见自己那一半
（结论 58）。这条迁移把 `alerts` 需要的那几列加到 `notification` 上，再把它的行
搬过去。

**表和数据都留着不动**：搬家在窗口里扫不到旧镜像后写的那几行（那批不是「晚一点
到」，是再也不到），所以这条迁移写成幂等的，由 `DROP TABLE` 那一条在删表之前原样
再跑一遍。

幂等问的是**「这条 alert 落过行没有」**（`NOT EXISTS`，按 `delivery_key` 里记下
的原 uuid 找），不是逐行去撞 `delivery_key` 的唯一约束。差别在收件人
是谁算的：广播的收件人是**跑这条迁移的那一刻**从名册上现算的，第二遍跑的时候名册
已经不是第一遍那一份了 —— 窗口期里进房间的人会算出一个新 handle、一个新
`delivery_key`，撞不上任何已有的行，于是凭空多出一批：第一次部署之后才进房间的人
突然收到几周前的广播，全是未读。整条跳过，收件人就冻在第一遍那一份上。

（第一遍一个收件人都算不出来的那一条例外：它一行也没落下，第二遍还会再算一次。
名册当时是空的，这条广播本来就谁也没送到。）

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
down_revision: str | Sequence[str] | None = "c2e4a6b80193"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: 把 `alerts` 的行搬进 `notification`，一个收件人一行。已经搬过的那条 alert 整条
#: 跳过（`NOT EXISTS`），所以重跑是空操作，名册在两遍之间变了也是。
#:
#: 「搬过没有」先一次性捞成 `moved`（搬过来的行在 `delivery_key` 的第二段上写着
#: 原 uuid），再按等值反连接。写成 `starts_with(n.delivery_key, 'alert:' || a.id
#: || ':')` 的话，前缀是跟着外层行变的表达式而不是计划期常量，`delivery_key` 上
#: 的唯一索引用不上、谓词不是等值也排不出 hash anti-join —— 计划只剩「每条
#: `alerts` 全表扫一遍 `notification`」，而这条 SQL 和前面那串 `ALTER TABLE` 同在
#: 一个事务里，ACCESS EXCLUSIVE 锁要按这个时长一直握着，站内信读写跟着停。
#: `MATERIALIZED` 把那一遍扫描钉成一次。问的还是同一句话（这条 alert 落过行没
#: 有），不是逐行撞唯一键。
#:
#: `left(handle, 7) <> 'cheese-'` 是 `identity/handles.looks_like_agent_handle`
#: 的 SQL 孪生，判据逐字相同。`'alert:' || '%'` 是同一个 LIKE：`%` 直接写进去要
#: 跨 DBAPI 的参数风格转义，拼出来就不用。
#:
#: handle 查账号那一句和服务侧是同一句：`UserRepository.get_by_username` 带
#: `deleted_at IS NULL`，这里也带。`user.username` 上没有唯一约束，所以一个注销
#: 后被重新注册的 handle 在表里有两行，`ORDER BY u.id` 取的是最老的那一行 ——
#: 不挡注销账号就正好指向那个死账号，搬过来的通知永远不出现在真人的站内信里。
MOVE_ALERTS = sa.text(
    """
    WITH moved AS MATERIALIZED (
        SELECT DISTINCT split_part(delivery_key, ':', 2) AS alert_id
          FROM notification
         WHERE delivery_key LIKE 'alert:' || '%'
    )
    INSERT INTO notification (
        id, receiver_id, recipient_handle, type, project_id, topic_id,
        level, title, body, metadata, read, resolved_at, feedback,
        is_aggregatable, finalized, delivery_key, version,
        created_at, updated_at
    )
    SELECT
        nextval('notification_seq'),
        (SELECT u.id FROM "user" u
          WHERE u.username = r.handle AND u.deleted_at IS NULL
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
    WHERE NOT EXISTS (
        SELECT 1 FROM moved m WHERE m.alert_id = a.id::text
    )
    ON CONFLICT (delivery_key) DO NOTHING
    """
)


def upgrade() -> None:
    # 收件人在这一行上有两个名字：名册上的 handle（投递这一侧认的就是它）和账号池
    # 里的那一行。handle 在账号池里找不到对应行时后者为空 —— 收件箱按 handle 读，
    # 认不回账号也不该把一条本该送到的通知整条丢掉。
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
    # 项目收件箱那一侧的行整条清掉 —— 搬家搬来的（去重键以 `alert:` 开头，`alerts`
    # 那一侧一个字没动，真相还在原表上），和迁移之后新代码自己写进去的那些（房间里
    # 的一次 @ 就是一行：`repositories.add` 不写 `delivery_key`，收件人有账号时
    # `receiver_id` 还有值）。两类都靠 `project_id` / `recipient_handle` 认。
    #
    # 必须在下面 `DROP COLUMN` 之前、列还在的时候删：列一掉，旧代码的
    # `list_for_user` / `count_unread_for_user` 只按 `receiver_id` 查，这些行就全
    # 冒进知是的铃铛 —— `type` 是 `MENTION`/`decision_request`，而模板要读的
    # `payload.mentioner`/`discussionTitle` 是空的，文字原本在 `title`/`body` 上，
    # 而那两列刚被删掉。
    op.execute(
        "DELETE FROM notification "
        "WHERE project_id IS NOT NULL OR recipient_handle IS NOT NULL"
    )
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
    # `receiver_id` 还为空的行：上面按项目那两列认不出来的（两列都空），而知是那
    # 一侧按 `receiver_id` 查，本来就一条都读不到它们。把它们编成「0 号用户」是为
    # 了把 NOT NULL 加回去而造一个不存在的账号 —— 那个信箱谁也打不开，
    # `count_unread_for_user(0)` 却认得它们。删掉。
    op.execute("DELETE FROM notification WHERE receiver_id IS NULL")
    op.execute("ALTER TABLE notification ALTER COLUMN receiver_id SET NOT NULL")
