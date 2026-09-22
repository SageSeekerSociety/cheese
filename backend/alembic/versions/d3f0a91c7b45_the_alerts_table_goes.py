"""the alerts table goes, after its rows have landed twice

Revision ID: d3f0a91c7b45
Revises: c3b9e41f75a2
Create Date: 2026-09-21 12:00:00

`c8d3a1e07f54` 把 `alerts` 的行搬进了 `notification`，表和数据原样留着，因为换
镜像那段窗口里旧镜像还在往 `alerts` 里写 —— 那一批不是「晚一点到」，是第一遍搬家
再也扫不到（取舍第四条第 2 点）。这条迁移是第二遍：接住那一批，逐行核对，然后才
删表。

**四步在同一条迁移里，同一个事务里**（`env.py` 整趟 `upgrade` 只开一个事务）：

1. **再跑一遍搬家。** 跑的是 `c8d3a1e07f54.MOVE_ALERTS` 那一句**原文** —— 从那个
   模块里取，不抄一份。抄一份就有两份声明，而这一句的幂等（按原 uuid 整条跳过，
   不逐行撞唯一键）正是「第二遍不会凭空多出收件人」所依赖的东西，差一个字都不行。
2. **接住窗口里留在旧行上的读、拍板、反馈。** 第 1 步按原 uuid 整条跳过已经搬过
   的 alert —— 那是对的，重算名册会凭空多出收件人；但窗口里旧镜像的
   `/alerts/{id}/read`、`/resolve`、`/feedback` 改的正是这批已经搬过的行，跳过它
   们就把那几笔留在了原表上。这一步只填新那一侧还空着的格子。
3. **逐行核对。** 每一条 `alerts` 在 `notification` 里都得有同一份内容。对不上的
   **一行都不删**：整条迁移抛错停在这里，表和数据原样留着，错误信息里点名是哪几条，
   由人看过再说（结论 61：这些行是人和 agent 显式写进去的，写歪了没有第二份）。
4. **`DROP TABLE`。** 只有全部对上才走到这一步。

## 窗口里那几笔状态：第 2 步只填不盖

换完镜像之后收件箱就是 `notification` 了，人读掉一条、答掉一条决策请求、按一个
赞，动的都是新那一行 —— 这一段没有问题。**有问题的是换镜像之前那几分钟**：旧镜像
的那三个端点还活着，写的是 `alerts`，而它们改的行第一遍搬家已经搬过，于是第 1 步
整条跳过、第 3 步只比内容不比状态、然后表就删了 —— 那几笔跟着没。

**代价不是少一个已读标记。** 一条在窗口里拍过板的决策请求，`notification` 那一行
的 `resolved_at` 仍然是 NULL，而决策请求在被答复之前不离开收件箱
（`notification/models.py`）—— 换完镜像它回到「等你处理的事」里显示待答，人再拍
一次板，新那一侧的幂等只认 `notification.resolved_at`，房间里就落**第二条【决策】
块**。

所以第 2 步写成**只填不盖**（`COALESCE` / `OR`）：新那一侧已经有的值永远不被旧行
盖掉，「两次部署之间有人用过收件箱」照样跑得过去。它和第 1 步同性质 —— 一次性的
接管，不是双写，也不是留一条兼容路径。

## 核对的是哪几列（第 3 步）

**这条通知说的是什么**：类别、项目、房间、等级、标题、正文、发生时刻。这七样写
下去之后没有任何一条路径再改它们，所以两边不一样只可能是窗口里旧镜像动过手。

**人在收件箱里留下的状态不在里面**：`read`、`resolved_at`、`feedback`，以及
`metadata`。这四样两边本来就该不一样 —— 换完镜像之后动的都是 `notification` 那
一行，而 `alerts` 那一行冻在搬家那一刻。把它们也比一遍，等于要求「这两次部署之间
没有人用过收件箱」，那不是数据对不上，那是正常使用，而代价是删表这条迁移从此再
也跑不过去。窗口里那一头由第 2 步合过来，不靠核对拦。

## 一条也没落下的那一种

广播（`target_handle IS NULL`）碰上空名册时一个收件人也算不出来，一行也落不下
（`c8d3a1e07f54` 的说明里点了这一种）。它不算「对不上」：这条 alert 从来没有送到
过任何人手上，也没有任何人能读到它 —— 删表之后不多不少还是没人读得到。所以它不
挡删表，**连同项目、房间、标题、写下的时刻一起写进迁移日志**：放行就是随
`DROP TABLE` 一起删掉，日志里只留一个 uuid 的话，事后拿着它查无可查 —— 日志行
自己就得说得清这条广播是什么。

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
down_revision: str | Sequence[str] | None = "c3b9e41f75a2"
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


#: 窗口里旧镜像留在**已经搬过**的那些 `alerts` 行上的读 / 拍板 / 反馈，合进
#: `notification` 那一侧。一次性的接管，和第一步那句搬家同性质。
#:
#: **只填不盖**：`COALESCE` 与 `OR` 保证新那一侧已经有的值永远不被旧行盖掉 ——
#: 换完镜像之后人在收件箱里做的事都落在新那一行上，旧行冻在搬家那一刻，拿旧的
#: 去覆盖新的就是把正常使用擦掉。`metadata` 同理：只有新那一行还没拍过板、而旧
#: 行上拍了的时候，才把选的那一项（`resolved_choice`，`notification/services.py`
#: 的 `resolve` 写下的那个键）补进去。`SET` 右边读到的都是这一行更新前的值，所以
#: 那句 `n.resolved_at IS NULL` 问的是「合之前新那一侧空不空」。
#:
#: **广播会把一份状态摊到每个收件人头上**：`alerts` 一行广播只有一个 `read_at` /
#: `resolved_at`，而它在 `notification` 里是一人一行。这正是旧表的语义（谁读了就
#: 是大家都读了 —— 那张表一行谁都看得见），不是 bug；写在这里，免得下一个人当成
#: bug 来修。
#:
#: 认行用的还是 `delivery_key` 第二段上的原 uuid，和搬家、核对是同一句话。
TAKE_OVER_WHAT_THE_WINDOW_LEFT = sa.text(
    """
    UPDATE notification n
       SET read        = n.read OR a.read_at IS NOT NULL,
           resolved_at = COALESCE(n.resolved_at, a.resolved_at),
           feedback    = COALESCE(n.feedback, a.feedback),
           metadata    = CASE
               WHEN n.resolved_at IS NULL
                AND (a.payload::jsonb -> 'resolved_choice') IS NOT NULL
               THEN COALESCE(n.metadata, '{}'::jsonb)
                    || jsonb_build_object('resolved_choice',
                                          a.payload::jsonb -> 'resolved_choice')
               ELSE n.metadata END
      FROM alerts a
     WHERE n.delivery_key LIKE 'alert:' || '%'
       AND split_part(n.delivery_key, ':', 2) = a.id::text
       AND (a.read_at IS NOT NULL
            OR a.resolved_at IS NOT NULL
            OR a.feedback IS NOT NULL)
    """
)


#: 每一条 `alerts` 在 `notification` 里落了几行、其中几行内容对不上。
#:
#: 搬过来的行在 `delivery_key` 的第二段上写着原 uuid（`alert:<uuid>:<收件人>`），
#: uuid 里没有冒号，所以 `split_part(..., ':', 2)` 取的就是它 —— 和上半场认「搬过
#: 没有」用的是同一句话。
#:
#: `IS DISTINCT FROM` 而不是 `<>`：`topic_id` 和 `level` 两边都可以是 NULL，而
#: `NULL <> NULL` 是 NULL 不是 true，用 `<>` 的话两边都空的那几列会被当成「没对
#: 上」，于是每一条都进 `differing`，删表永远走不到。
#:
#: 投影里还带着 `alerts` 自己的项目、房间、标题、写下的时刻：放行的那一条广播要
#: 随 `DROP TABLE` 一起没，它说的是什么只剩迁移日志这一份，所以查出来的时候就得
#: 一并拿上。`a.id` 是主键，按它分组之后同一行的其余列都跟着定下来。
UNACCOUNTED = sa.text(
    """
    SELECT id, broadcast, copies, differing,
           project_id, topic_id, title, created_at FROM (
        SELECT
            a.id::text AS id,
            a.target_handle IS NULL AS broadcast,
            a.project_id::text AS project_id,
            a.topic_id::text AS topic_id,
            a.title AS title,
            a.created_at AS created_at,
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
    bind.execute(TAKE_OVER_WHAT_THE_WINDOW_LEFT)

    stuck: list[str] = []
    for row in bind.execute(UNACCOUNTED).mappings():
        if row["copies"] == 0 and row["broadcast"]:
            log.warning(
                "alerts %s 是一条谁也没收到的广播（当时名册是空的，搬家算不出收件人）"
                "—— 删表之后它照旧不在任何人的收件箱里。这一行跟着表一起删掉，拿着"
                "这个 id 再也查不回来，所以它说的是什么留在这里：项目 %s、房间 %s、"
                "标题「%s」、写于 %s",
                row["id"],
                row["project_id"],
                row["topic_id"] or "（不在任何房间里）",
                row["title"],
                row["created_at"],
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
