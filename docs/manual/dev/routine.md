---
title: 例行与巡检
kind: 参考
summary: 频道里的常驻工作：规则与执行两张表、钟点与事件两种触发、一次执行的一生（主线上的一条消息和它的支线），以及平台自己的巡检钟。
covers:
  - backend/app/domain/routine/
  - backend/app/api/routes/routines.py
  - backend/app/core/background.py
  - backend/app/periodic_jobs.py
---

# 例行与巡检 {#routine}

频道里的 AI 队友可以被安排一件**常驻**的活：到点就跑，或者项目里发生了某件事就跑。每一次执行是 AI 队友在频道主线上的一条消息，执行本身在这条消息的支线里；跑完结果写进这条消息，再通知安排它的人。

> 讲：一条周期任务从起草到执行到结账的全过程，以及平台自己的那几口钟。不讲：通知怎么发出去（见[通知](/dev/notifications#ledger)），一次执行在房间里怎么变成芝士的一轮（见[一条消息怎么变成芝士的一轮](/dev/turn)），任务与交付本身（见[任务与工作目录](/dev/tasks)、[任务 → 分支 → PR → 验收合并](/dev/delivery)）。

## 规则是规则，执行是执行 {#model}

两张表，`backend/app/domain/routine/models.py`：

| 表 | 是什么 | 关键列 |
| --- | --- | --- |
| `Routine` | 一条**规则** | `title`、`instructions`（目标）、`note`（补充说明）、`trigger`、`spec`、`timezone`、`state`、`agent_handle`、`owner_handle`、`next_run_at`、`event_cursor`、`revision` |
| `RoutineRun` | 规则**响了一次** | `occurrence_key`、`routine_revision`、`status`、`message_id`、`delivery_event_id`、`turn_id`、`summary`、`outputs`、`error`、`notified` |

规则两态：`active`、`paused`。执行五态：`queued` / `running` / `succeeded` / `failed` / `skipped`，末三者是终结态（`TERMINAL_RUN_STATUSES`）。

**执行行先落，活才派**：`_fire` 先把 `RoutineRun` 写进去，唯一约束 `uq_routine_occurrence(routine_id, occurrence_key)` 保证同一个「计划时刻」或同一个「事件」只会有一行 —— 第二次扫描、第二个后端进程、重启，找到的都是同一行，而不是把同一件事再派一次。这就是「至少一次」被压回「恰好一次」的那一步。

`revision` 每次编辑加一，执行行记下**派出去时**的版本：事后能分清「这条规则当时是这么写的」。

## 谁来执行，通知谁 {#agent}

`agent_handle` 是**这个房间里**的那个芝士席位，`owner_handle` 是安排它的人，也是每次结果的通知对象。两条限制：

- `_agent_for` 只认这个房间里的 AI 队友席位，写别处的 handle 直接拒。
- 芝士起草时（`by_agent=True`）必须给 `owner_handle`，而且那个人必须**已经在这个频道里**（`people_in`）：结果通知给他，每次执行还读他的私人记忆、答在这个频道的支线里，和他自己在这里说话时一样。芝士不会为了让谁当主人而把他拉进频道。

**没有待确认这一步**：`create` 当场就 `_activate`，人和芝士建的都停在 `active`。芝士建的那一条**不落任何事件块**：它自己在对话里说一句，话里带上这条规则的链接（`/projects/<项目>/channels/<房间>?tab=routines&routine=<规则 id>`，点开还是这个频道，右栏「例行事务」那一格开在这一条上），平台不加一行、也不在那一行上放按钮。`delete` 里的 `_settled` 只收拾旧版本留在房间里的那些行。`update` 也不再退回任何状态：改的就是现在这条，下一次按改后的来。

路由层的缝有两处：暂停 / 立即执行 / 修改和读取同一个口径（读得到就改得动）；恢复只能由人来做（`_person`），芝士停下的规则由人决定要不要再跑；删除也要 `_person(actor, …)`，而且得是规则的 `created_by` 或项目管理员（`api/routes/routines.py`）。

## 两种触发 {#triggers}

| 触发 | `spec` | `occurrence_key` |
| --- | --- | --- |
| `schedule` | `{freq: hourly\|daily\|weekly\|monthly, time, minute, weekdays, day}` | 计划时刻（UTC ISO） |
| `library_file_added` | `{scope}`（强制 project） | `library:<path>:<mtime>` |
| `task_closed` | `{scope: room\|project}` | `task:<id>` |
| `card_accepted` | `{scope: room\|project}` | `card:<id>` |

定时规则的 `spec` 还可以带 `feedback_batch`（1–10）：每次执行附上一批没人处理的反馈，没有就记 `skipped`，不派活也不通知；只有做平台本身的项目里的房间能用，见[每日分诊](/dev/feedback#triage)。

钟点一律**按人自己的时区**算（`timezone`；建规则时没写，就用主人的浏览器报上来的时区，都没有才是 `Asia/Shanghai`），`schedule.next_after` 返回严格晚于给定时刻的下一个 UTC 瞬间：`hourly` 只认第几分钟，`weekly` 的 `weekdays` 是 0=周一，`monthly` 的号数会按当月长度收敛（`min(day, last)`，所以「31 号」在二月落在月末）。`normalize` 把「说不清是哪一刻」的计划全部拒掉，而不是猜一个。

事件触发靠游标：`event_cursor` 之前的从不算这条规则的事，`_activate` 把它设成「现在」—— 所以一条刚建的规则不会去补跑这个项目的历史。`_events_for` 把项目里的事翻出来（资料库文件按 mtime、任务按 `closed_at`、验收卡按 `decided_at`），排序后取最新的时刻当新游标，但游标最多推到这一趟开始前 `EVENT_LOOKBACK`（2 分钟）：一件事的时间写在提交之前，扫描之后才提交的那件时间比游标早，留这一截才找得到它。同一件事被找到两次还是一次执行（`occurrence_key`）。改一条启用中的规则而触发方式没变，游标不动，改之前发生的事照样算。

## 一次执行的一生 {#run-life}

这一节讲一次执行从响起到结账，在频道里各留下什么。

```
到点/事件 → _fire：写 RoutineRun（queued）→ 主线上落 AI 队友的一条消息（message_id）
        → answered_in 给它开支线 → 支线里落「开始执行」→ 往支线派一个投递事件（ROOM_NOTICE）
        → 芝士的一轮在支线里跑起来 → run.turn_id → 交回结果 → report
        → _announce_finished：结果写进那条消息、消息挪到结账的时刻 → 通知规则的主人
```

**一次执行是主线上的一条消息。** `_fire` 先在规则所在频道的主线落一条 AI 队友署名、正文为空的消息，`RoutineRun.message_id` 记下它；再用 `thread.services.answered_in` 决定在哪里答：频道主线上的消息在它的支线里，私聊没有支线，就在私聊里。「开始执行」那一行和投递都落在那里。消息下面那一行（规则名、运行中 / 未能执行、查看这次运行）由 `routine/reads.runs_under` 在频道翻页时挂上，前端是 `RoutineRunLine`。规则的主人算这条支线的参与者（`thread/reads._participants`），所以支线里有人追问，回复会通知到他。

**这一轮留得下，追问留不下。** 支线里芝士的改动本来留不下；执行的那一轮从投递上认出来（`turn_speakers.is_routine_run`，`eventType == routine_run`），凭证不带 `scratch`，开场提示也换成「这一轮就是这次执行，可以保存结果」。之后有人在这条支线里追问，那几轮照支线的规矩改动留不下，会话按新参数重启一次，接着同一段对话。执行那一轮也不改代码：它和支线一样只拿到只读的仓库令牌，要改代码就转为任务（见[支线的会话](/dev/session#scratch)）。

**投递和交回。** 投递事件的 payload 里是这段工作本身：`run_prompt` 把目标、补充说明（有才写）、上一次成功执行交回的结果，以及**怎么交回**（`POST /routine-runs/{id}/report`）都写给芝士看。执行用哪些资料、结果放哪，提示词里明说由它这次自己定。执行的 `turn_id` 就是那一轮。

**交回是必须的**：`report` 只认这条规则的 `agent_handle` 交，成功失败都要交，失败还必须写明原因，`outputs` 最多 50 条。成功时交回的 `summary` 就是那条消息的正文，频道里的人直接读它。没交回的那一次会被记成失败。

结账在 `_settle_open_runs` 里，按几口不同的钟判：

| 现象 | 判据 | 结论 |
| --- | --- | --- |
| 投递本身就失败 | `Delivery.state == failed` | 失败 |
| 没读到就没能开始 | 会话没读到这份输入，房间里却有平台为这次投递记下的 failed/timeout 事件 | 失败（附那句失败通知） |
| 会话读到了 | 这份输入的回执（`NativeInput.echoed_at`） | `queued` → `running` |
| 读进去的那段工作失败了 | runner 报的「读了，然后失败」（`NativeInput.failed_at`，会话退出也在内） | 失败（附房间里那句失败通知，没有就用失败的种类） |
| 一直没交回 | 读到之后超过 `REPORT_TIMEOUT`（`agent_turn_hard_ceiling_s` + 5 分钟） | 失败，「AI 队友一直没有交回结果」 |
| 两小时没开始 | `created_at` 超过 `START_TIMEOUT` | 失败（设备离线或排队过久） |

为什么这不看「这一轮什么时候结束」：轮次的起止说明不了队友有没有照着做。判成功的只有它交回（`cheese_routine(action=report)`）；平台这一路只看核得了的事：投递、会话读没读到、runner 报没报读进去的工作失败了，再加一口「最长能做多久」的钟。迟到的失败如果后来交回了成功，`report` 会把 `notified` 复位、把错误清掉，**重新通知一次** —— 那次通知说的是它实际怎么样了。

`_announce_finished` 只扫 `notified = false` 且已经终结的执行：成功的把交回的结果写进那条消息，没成功的消息正文留空（不以 AI 队友的名义替平台说话，原因由 `runs_under` 带出、画在消息下面那一行）；两种都把消息的时间改成结账的这一刻，挪到主线最下面，再给 `owner_handle` 发 `CHANGE_ALERT` 通知，一次都不重复。改过的消息在提交后整行推给开着频道的人（`publish_run_messages`，前端只有时间变了的那一行才重新落位）。`skipped` 的执行没有消息，只通知主人。

## 抢跑、补跑与熔断 {#once}

- **错过了就不补**：计划时刻晚于现在超过 `MISSED_GRACE`（15 分钟）的，执行记成 `skipped`，理由写「平台当时没有运行，这一次不补跑」，频道里不落消息。补跑一条几小时前的定时任务，通常比不跑更糟。
- **不重复**：唯一约束那一步返回空就说明这个时刻已经响过了，`_fire` 直接返回。
- **不叠着跑**：到点时这条规则上一次执行还没结束（`queued` / `running`），这一次记成 `skipped`（`STILL_RUNNING`），不派活。
- **不会自己绕圈**：规则自己的队友开的任务（`Task.created_by` 是它）关闭，不算这条规则的事；事件规则一小时内最多 `EVENT_RUNS_PER_HOUR`（6）次，超出的落 `skipped`，主人一小时只听说一次（`OVER_EVENT_LIMIT`），不是每件超出的事一条通知。
- **一条坏规则只坏它自己**：每条规则在自己的保存点里响，出错就退回这一条、记日志，同一趟的别的规则照常；定时规则照样排到下一次，不会每 30 秒重试同一个时刻。
- **扫描是并发的**：`_fire_schedules` / `_fire_events` / `announce_archived_rooms` / `_settle_open_runs` / `_announce_finished` 都 `with_for_update(skip_locked=True)`，多个后端进程同时在跑也不会互相排队或重复处理。

## 平台自己的钟：PeriodicRunner {#sweep}

`backend/app/periodic_jobs.py` 集中装配平台全部 35 项常驻巡检：工单 PR 轮询、草稿 PR 扫描、孤儿轮次清扫、定时投递、周期任务（interval 30 秒）、任务截止、记忆整理、邮件与推送队列……各领域登记自己的任务；应用层保留名称和顺序清单，缺项、重复或未列出的任务都会报错。通用运行器仍在 `backend/app/core/background.py`。

`PeriodicRunner` 把每条常驻任务都会踩的四个坑一次收掉：强引用（`spawn`）、**interval ≤ 0 表示这台机器不跑它**（部署和测试共用的那个开关）、一次崩掉只算一次而不是让循环死掉、以及只在**这一轮真的做了点什么**的时候打日志（每分钟「扫了 0 条」的日志没人看，`_worth_reporting` 于是让 `{"failed": 0}` 闭嘴）。

周期任务那一口钟叫 `sweep`：一轮里跑完 `_fire_schedules` → `_fire_events` → `announce_archived_rooms` → `_settle_open_runs` → `_announce_finished`，有东西被触发才 `dispatch_pending` 去派活。

`announce_archived_rooms` 是这一串里唯一不碰规则的：归档**不写规则**那一行（带走的是执行，取消归档之后规则从下一个时刻继续），所以它只把归档**说出来** —— 哪间归档了的房间还有启用中的规则、又还没被说过，就在那间房里落一行「N 条规则已随归档停止」，并通知每条规则的主人。说话的是这一口钟而不是归档那个接口，因为归档是别处做的动作：手工归档、整个项目一起归档、脚本归档走的是同一个答案（认得它的只有房间的状态，频道那一域不必认识周期任务）。同一段归档只说一次，房间自己的 `archived_at` 就印在那行上；取消归档后再归档是新的一段，会再说一次。

## 边界与坑 {#traps}

- **执行行是幂等的锚，不是日志。** 想重跑一次，走 `run_now`（`occurrence_key = manual:<新 uuid>`），不要删执行行 —— 删了它就等于允许同一时刻再响一次。
- **事件驱动的规则和它的产物会互相触发。** 熔断（每小时 6 次）和「自己的队友开的任务不算触发」是两条独立的防线，缺一条就会出现环。
- **暂停不补、恢复不补。** `resume` 从下一个未来时刻接着算（`_activate` 里 `next_after(…, now)`），中间空过的时段不会被回填。
- **改了就按新的跑。** `update` 改一条 `active` 的规则会重新算 `next_run_at`，没有人再确认一次；`revision` 加一，已经派出去的执行行留的还是当时那个版本。
- **契约测试看着 `cheese_*` 工具面。** `backend/sandbox/cheese` 里的工具表和后端是两份，双方不一致由 `backend/tests/contract/` 拦下（同一形状的约束见 `MACHINE_PROFILES`）。
- **花费不写规则表，每次问账本。** 一条规则的花费是它的轮次们扣掉的额度之和（`service/spend.py`）：账本按轮次记（`resource_usage.turn_id` 就是 `RoutineRun.turn_id`），对它们求和即可。规则表上加一列累计就有第二个数要在每次结算时记得更新。求 credits 时**不能**套 `MODEL_CALLS` —— 那个过滤说的是「算不算一次模型调用」，而网页搜索那几行没有 token 也照样扣点数，团队额度页算的也是这个口径。窗口（`month_start` / `month_end`）由调用方传进来，这一域不自己算一个「月」。
