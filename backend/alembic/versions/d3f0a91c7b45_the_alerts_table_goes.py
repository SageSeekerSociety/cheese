"""the alerts table goes, after its rows have landed twice

Revision ID: d3f0a91c7b45
Revises: c8d3a1e07f54
Create Date: 2026-09-21 12:00:00

`c8d3a1e07f54` 把 `alerts` 的行搬进了 `notification`，表和数据原样留着，因为换
镜像那段窗口里旧镜像还在往 `alerts` 里写 —— 那一批不是「晚一点到」，是第一遍搬家
再也扫不到（取舍第四条第 2 点）。这条迁移是第二遍：接住那一批，逐行核对，然后才
删表。

**三步在同一条迁移里，同一个事务里**（`env.py` 整趟 `upgrade` 只开一个事务）：

1. **再跑一遍搬家。** 跑的是 `c8d3a1e07f54.MOVE_ALERTS` 那一句**原文** —— 从那个
   模块里取，不抄一份。抄一份就有两份声明，而这一句的幂等（按原 uuid 整条跳过，
   不逐行撞唯一键）正是「第二遍不会凭空多出收件人」所依赖的东西，差一个字都不行。
2. **逐行核对。** 每一条 `alerts` 在 `notification` 里都得有同一份内容。对不上的
   **一行都不删**：整条迁移抛错停在这里，表和数据原样留着，错误信息里点名是哪几条，
   由人看过再说（结论 61：这些行是人和 agent 显式写进去的，写歪了没有第二份）。
3. **`DROP TABLE`。** 只有全部对上才走到这一步。

## 核对的是哪几列

**这条通知说的是什么**：类别、项目、房间、等级、标题、正文、发生时刻。这七样写
下去之后没有任何一条路径再改它们，所以两边不一样只可能是窗口里旧镜像动过手。

**人在收件箱里留下的状态不在里面**：`read`、`resolved_at`、`feedback`，以及
`metadata` —— 拍板会把选的那一项写进 `metadata.resolved_choice`
（`notification/services.py` 的 `resolve`）。上一次部署之后收件箱就是
`notification` 了，人读掉一条、答掉一条决策请求、按一个赞，动的都是 `notification`
那一行，而 `alerts` 那一行冻在搬家那一刻。把这四样也比一遍，等于要求「这两次部署
之间没有人用过收件箱」—— 那不是数据对不上，那是正常使用，而代价是删表这条迁移
从此再也跑不过去。

## 一条也没落下的那一种

广播（`target_handle IS NULL`）碰上空名册时一个收件人也算不出来，一行也落不下
（`c8d3a1e07f54` 的说明里点了这一种）。它不算「对不上」：这条 alert 从来没有送到
过任何人手上，也没有任何人能读到它 —— 删表之后不多不少还是没人读得到。所以它
**按 id 写进迁移日志**，不挡删表。

点名给某个人的那一种落不下行是另一回事：`target_handle` 非空时搬家必定算出恰好
一个收件人，所以「点名的那一条一行都没落下」只可能是搬家这一句自己坏了。那一种
和对不上的一样拦住。
"""

import importlib.util
import logging
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "d3f0a91c7b45"
down_revision: str | Sequence[str] | None = "c8d3a1e07f54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FIRST_HALF = Path(__file__).with_name(
    "c8d3a1e07f54_two_notification_tables_become_one.py"
)

#: 迁移自己的日志通道：`alembic upgrade head` 的输出里就是这一条。
log = logging.getLogger("alembic.runtime.migration")


def the_same_move() -> sa.TextClause:
    """上半场那一句搬家的**原文**。

    `alembic/versions/` 不是包，取不到 `import`；按文件路径加载是这个仓库里已经
    在用的办法（`tests/integration/test_alerts_move_into_the_one_notification_table.py`
    加载同一个模块跑它的 `upgrade()`）。
    """
    spec = importlib.util.spec_from_file_location("_alerts_first_half", _FIRST_HALF)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件跟着仓库走
        raise RuntimeError(f"读不到上半场那条迁移：{_FIRST_HALF}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MOVE_ALERTS


#: 每一条 `alerts` 在 `notification` 里落了几行、其中几行内容对不上。
#:
#: 搬过来的行在 `delivery_key` 的第二段上写着原 uuid（`alert:<uuid>:<收件人>`），
#: uuid 里没有冒号，所以 `split_part(..., ':', 2)` 取的就是它 —— 和上半场认「搬过
#: 没有」用的是同一句话。
#:
#: `IS DISTINCT FROM` 而不是 `<>`：`topic_id` 和 `level` 两边都可以是 NULL，而
#: `NULL <> NULL` 是 NULL 不是 true，用 `<>` 的话两边都空的那几列会被当成「没对
#: 上」，于是每一条都进 `differing`，删表永远走不到。
UNACCOUNTED = sa.text(
    """
    SELECT id, broadcast, copies, differing FROM (
        SELECT
            a.id::text AS id,
            a.target_handle IS NULL AS broadcast,
            count(l.alert_id) AS copies,
            count(l.alert_id) FILTER (
                WHERE l.type IS DISTINCT FROM (
                          CASE a.kind WHEN 'mention' THEN 'MENTION' ELSE a.kind END
                      )
                   OR l.project_id IS DISTINCT FROM a.project_id
                   OR l.topic_id IS DISTINCT FROM a.topic_id
                   OR l.level IS DISTINCT FROM a.level
                   OR l.title IS DISTINCT FROM a.title
                   OR l.body IS DISTINCT FROM a.body
                   OR l.created_at IS DISTINCT FROM a.created_at
            ) AS differing
        FROM alerts a
        LEFT JOIN (
            SELECT split_part(n.delivery_key, ':', 2) AS alert_id,
                   n.type, n.project_id, n.topic_id, n.level,
                   n.title, n.body, n.created_at
              FROM notification n
             WHERE n.delivery_key LIKE 'alert:' || '%'
        ) l ON l.alert_id = a.id::text
        GROUP BY a.id, a.target_handle
    ) t
    WHERE differing > 0 OR copies = 0
    ORDER BY id
    """
)


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(the_same_move())

    stuck: list[str] = []
    for row in bind.execute(UNACCOUNTED).mappings():
        if row["copies"] == 0 and row["broadcast"]:
            log.warning(
                "alerts %s 是一条谁也没收到的广播（当时名册是空的，搬家算不出收件人）"
                "—— 删表之后它照旧不在任何人的收件箱里",
                row["id"],
            )
            continue
        stuck.append(
            f"{row['id']}（落了 {row['copies']} 行，其中 {row['differing']} 行内容对不上）"
        )

    if stuck:
        named = "、".join(stuck[:20])
        more = f"，另有 {len(stuck) - 20} 条" if len(stuck) > 20 else ""
        raise RuntimeError(
            "不删 `alerts`：这几条在 `notification` 里没有同一份内容 —— "
            f"{named}{more}。表和行原样留着（这条迁移整条回滚了，第二遍搬家也没落"
            "下）。逐条看过这几行是什么、该不该补进 `notification`，补完或者确认"
            "可以丢掉之后再跑一遍。"
        )

    op.drop_table("alerts")


def downgrade() -> None:
    raise RuntimeError(
        "不可逆：`alerts` 是在逐行核对过每一条都在 `notification` 里之后删掉的，"
        "行已经不在了。把空表建回来只还原形状、还原不了内容；真要回到并表之前，"
        "从 c8d3a1e07f54 的上一条起。"
    )
