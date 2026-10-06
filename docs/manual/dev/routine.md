---
title: 例行与巡检
kind: 参考
summary: 房间里的常驻工作：规则与执行两张表、钟点与事件两种触发、一次执行的一生，以及平台自己的巡检钟。
covers:
  - backend/app/domain/routine/
  - backend/app/api/routes/routines.py
  - backend/app/core/background.py
---

# 例行与巡检 {#routine}

房间里的 AI 队友可以被安排一件**常驻**的活：到点就跑，或者项目里发生了某件事就跑，跑完把结果交回房间、再通知安排它的人。

> 讲：一条周期任务从起草到执行到结账的全过程，以及平台自己的那几口钟。不讲：通知怎么发出去（见[通知](/dev/notifications#ledger)），一次执行在房间里怎么变成芝士的一轮（见[一条消息怎么变成芝士的一轮](/dev/turn)），任务与交付本身（见[任务与工作目录](/dev/tasks)、[任务 → 分支 → PR → 验收合并](/dev/delivery)）。

## 规则是规则，执行是执行 {#model}

两张表，`backend/app/domain/routine/models.py`：

| 表 | 是什么 | 关键列 |
| --- | --- | --- |
| `Routine` | 一条**规则** | `title`、`instructions`、`context_scope`、`output_dir`、`trigger`、`spec`、`timezone`、`state`、`agent_handle`、`owner_handle`、`next_run_at`、`event_cursor`、`revision` |
| `RoutineRun` | 规则**响了一次** | `occurrence_key`、`routine_revision`、`status`、`delivery_event_id`、`turn_id`、`summary`、`outputs`、`error`、`notified` |

规则三态：`draft`（芝士起草，人没确认之前什么都不跑）、`active`、`paused`。执行五态：`queued` / `running` / `succeeded` / `failed` / `skipped`，末三者是终结态（`TERMINAL_RUN_STATUSES`）。

**执行行先落，活才派**：`_fire` 先把 `RoutineRun` 写进去，唯一约束 `uq_routine_occurrence(routine_id, occurrence_key)` 保证同一个「计划时刻」或同一个「事件」只会有一行 —— 第二次扫描、第二个后端进程、重启，找到的都是同一行，而不是把同一件事再派一次。这就是「至少一次」被压回「恰好一次」的那一步。

`revision` 每次编辑加一，执行行记下**派出去时**的版本：事后能分清「这条规则当时是这么写的」。

## 谁来执行，通知谁 {#agent}

`agent_handle` 是**这个房间里**的那个芝士席位，`owner_handle` 是安排它的人，也是每次结果的通知对象。两条限制：

- `_agent_for` 只认这个房间里的 AI 队友席位，写别处的 handle 直接拒。
- 芝士起草时（`by_agent=True`）必须给 `owner_handle`，而且那个人必须**是房间里的人** —— 否则结果没法通知给他。

**芝士起草的规则停在 `draft`**：`create` 落一个房间事件块说「芝士起草了「…」，要你确认后才会执行」；人在页面上点确认才 `confirm` → `_activate`。人自己建的则当场就 `active`。同样地，`update` 里芝士改一条已经生效的规则，会把状态**退回 `draft`**、清掉 `next_run_at`：芝士写的东西，人读过才算数。

路由层把这条缝画得很直：确认 / 暂停 / 恢复 / 立即执行 / 删除都是 `_person(actor, …)`，芝士只能起草和修改（`api/routes/routines.py`）。

## 两种触发 {#triggers}

| 触发 | `spec` | `occurrence_key` |
| --- | --- | --- |
| `schedule` | `{freq: hourly\|daily\|weekly\|monthly, time, minute, weekdays, day}` | 计划时刻（UTC ISO） |
| `library_file_added` | `{scope}`（强制 project） | `library:<path>:<mtime>` |
| `task_closed` | `{scope: room\|project}` | `task:<id>` |
| `card_accepted` | `{scope: room\|project}` | `card:<id>` |

定时规则的 `spec` 还可以带 `feedback_batch`（1–10）：每次执行附上一批没人处理的反馈，没有就记 `skipped`，不派活也不通知；只有做平台本身的项目里的房间能用，见[每日分诊](/dev/feedback#triage)。

钟点一律**按人自己的时区**算（`timezone`，默认 `Asia/Shanghai`），`schedule.next_after` 返回严格晚于给定时刻的下一个 UTC 瞬间：`hourly` 只认第几分钟，`weekly` 的 `weekdays` 是 0=周一，`monthly` 的号数会按当月长度收敛（`min(day, last)`，所以「31 号」在二月落在月末）。`normalize` 把「说不清是哪一刻」的计划全部拒掉，而不是猜一个。

事件触发靠游标：`event_cursor` 之前的从不算这条规则的事，`_activate` 把它设成「现在」—— 所以一条刚建的规则不会去补跑这个项目的历史。`_events_for` 把项目里的事翻出来（资料库文件按 mtime、任务按 `closed_at`、验收卡按 `decided_at`），排序后取最新的时刻当新游标。

## 一次执行的一生 {#run-life}

```
到点/事件 → _fire：写 RoutineRun（queued）→ 派一个投递事件（ROOM_NOTICE）
        → 芝士的一轮跑起来 → run.turn_id → 交回结果 → report → _announce_finished
```

`_fire` 拿的是投递账本那一条（`route/notification` 侧的 `DeliveryEvent`，类型 `ROOM_NOTICE`），它的 payload 里是这段工作本身：`run_prompt` 把工作内容、资料范围、结果目录、以及**怎么交回**（`POST /routine-runs/{id}/report`）都写给芝士看。执行的 `turn_id` 就是那一轮。

**交回是必须的**：`report` 只认这条规则的 `agent_handle` 交，成功失败都要交，失败还必须写明原因，`outputs` 最多 50 条。没交回的那一次会被记成失败。

结账在 `_settle_open_runs` 里，按几口不同的钟判：

| 现象 | 判据 | 结论 |
| --- | --- | --- |
| 投递本身就失败 | `Delivery.state == failed` | 失败 |
| 那一轮没能开始 | `turn.delivered_at is None` 且 `stopped_at` 有值 | 失败（附房间里那句失败通知） |
| 轮子已经接住 | `turn.delivered_at` 有值 | `queued` → `running` |
| 一轮里报过错 | 房间里出现了该轮的 failed/timeout 事件 | 失败 |
| 一直没交回 | 超过 `REPORT_TIMEOUT`（`agent_turn_hard_ceiling_s` + 5 分钟） | 失败，「AI 队友一直没有交回结果」 |
| 两小时没开始 | `created_at` 超过 `START_TIMEOUT` | 失败（设备离线或排队过久） |

为什么这不看「这一轮什么时候结束」：活会话里每条输入的 turn 行都是毫秒级交付又停止的，后端看不见设备那边这一轮到底还忙不忙。所以唯一的钟是「一轮最长能跑多久」再加一点交回的余量，而真正的失败更早会被房间里那条 turn-failed 通知抓到。迟到的失败如果后来交回了成功，`report` 会把 `notified` 复位、把错误清掉，**重新通知一次** —— 那次通知说的是它实际怎么样了。

`_announce_finished` 只扫 `notified = false` 且已经终结的执行：房间事件块 + 给 `owner_handle` 的 `CHANGE_ALERT` 通知，一次都不重复。

## 抢跑、补跑与熔断 {#once}

- **错过了就不补**：计划时刻晚于现在超过 `MISSED_GRACE`（15 分钟）的，落一行 `skipped`，理由写「平台当时没有运行，这一次不补跑」。补跑一条几小时前的定时任务，通常比不跑更糟。
- **不重复**：唯一约束那一步返回空就说明这个时刻已经响过了，`_fire` 直接返回。
- **不会自己绕圈**：事件规则一小时内最多 `EVENT_RUNS_PER_HOUR`（6）次，超出的落 `skipped`；「任务完成触发工作、工作又开任务」也绕不起来：任务只能由人创建，周期任务跑出来的一轮最多提议任务。
- **扫描是并发的**：`_fire_schedules` / `_fire_events` / `announce_archived_rooms` / `_settle_open_runs` / `_announce_finished` 都 `with_for_update(skip_locked=True)`，多个后端进程同时在跑也不会互相排队或重复处理。

## 平台自己的钟：PeriodicRunner {#sweep}

`backend/app/core/background.py` 里列着平台所有常驻巡检，一处读完就知道有哪些活：工单 PR 轮询、草稿 PR 扫描、孤儿轮次清扫、定时投递、周期任务（interval 30 秒）、任务截止、记忆整理、邮件与推送队列……

`PeriodicRunner` 把每条常驻任务都会踩的四个坑一次收掉：强引用（`spawn`）、**interval ≤ 0 表示这台机器不跑它**（部署和测试共用的那个开关）、一次崩掉只算一次而不是让循环死掉、以及只在**这一轮真的做了点什么**的时候打日志（每分钟「扫了 0 条」的日志没人看，`_worth_reporting` 于是让 `{"failed": 0}` 闭嘴）。

周期任务那一口钟叫 `sweep`：一轮里跑完 `_fire_schedules` → `_fire_events` → `announce_archived_rooms` → `_settle_open_runs` → `_announce_finished`，有东西被触发才 `dispatch_pending` 去派活。

`announce_archived_rooms` 是这一串里唯一不碰规则的：归档**不写规则**那一行（带走的是执行，取消归档之后规则从下一个时刻继续），所以它只把归档**说出来** —— 哪间归档了的房间还有启用中的规则、又还没被说过，就在那间房里落一行「N 条规则已随归档停止」，并通知每条规则的主人。说话的是这一口钟而不是归档那个接口，因为归档是别处做的动作：手工归档、整个项目一起归档、脚本归档走的是同一个答案（认得它的只有房间的状态，话题那一域不必认识周期任务）。同一段归档只说一次，房间自己的 `archived_at` 就印在那行上；取消归档后再归档是新的一段，会再说一次。

## 边界与坑 {#traps}

- **执行行是幂等的锚，不是日志。** 想重跑一次，走 `run_now`（`occurrence_key = manual:<新 uuid>`），不要删执行行 —— 删了它就等于允许同一时刻再响一次。
- **事件驱动的规则和它的产物会互相触发。** 熔断（每小时 6 次）和「自己开的任务不算触发」是两条独立的防线，缺一条就会出现环。
- **暂停不补、恢复不补。** `resume` 从下一个未来时刻接着算（`_activate` 里 `next_after(…, now)`），中间空过的时段不会被回填。
- **草稿不执行。** `run_now` 对 `draft` 直接报错「还没确认的规则不能执行」；芝士改过的规则会退回草稿，于是也就不跑了。
- **契约测试看着 `cheese_*` 工具面。** `backend/sandbox/cheese` 里的工具表和后端是两份，双方不一致由 `backend/tests/contract/` 拦下（同一形状的约束见 `MACHINE_PROFILES`）。
