# 一轮什么时候算卡住

这份文档描述现状：一轮活（从提示词送进会话，到会话自己收尾）在什么情况下会被平台结束、由谁
判、结束之后房间里看到什么。

## 一句话

平台不按时间杀任何一轮。结束一轮的只有三种「卡住」的证据（进程没了、只说话不干活、发给它
的话它不读）加一种明确的失败（API 拒绝）。跑得再久，只要还在干活，就不动它。

谁来判，看骨架的 runner 说不说生命周期协议（`agent/harness/driven/lifecycle.py`，
`Harness.reports_lifecycle`）：

- **说的（Claude Code）**：后两条「卡住」由 runner 自己的两只表（`lifecycle.Watchdog`）在会话机
  上判，时限由平台登记 runner 时交给它（`agent_no_progress_s`、`agent_unread_grace_s`）；进程没了
  由 runner 报一个 `exited` 事件；API 拒绝由 runner 归类成带类型的失败，随会话回到空闲的那条
  `status` 报上来。房间不为它保留任何时钟。
- **不说的（Codex、pi，仍用房间的旧时钟）**：后两条由房间的会话账（`agent/room/sessions/`）判，读房间自己的事件词汇
  （说了话、调了工具、工具返回、一轮结束）；进程没了由会话核心（`agent/session_host/host.py`）
  判。

## 会结束一轮的四种判定

| 情况 | 证据是什么 | 什么时候结束 | 房间看到什么 |
|---|---|---|---|
| 进程没了 | Claude Code：runner 报 `exited`（它自己没来得及报的，下一个 runner 在同一份状态上替它报）。Codex、pi：runner 答读时带 `alive: false`，或连续 `RUNNER_GONE_S`（120 秒）够不着 | 立刻 | 「会话进程已退出」的失败结束；没读过的输入就此结算为「不会再被读到」，可以再递 |
| 只说话不干活 | 最近 `TALKING_S`（5 分钟）内有输出，但最近一次「动作」（工具调用、工具返回、一轮结束）已经是 `agent_no_progress_s`（30 分钟）以前 | 到点即结束 | 轮次超时事件（`TURN_TIMEOUT`） |
| 发给它的消息它一直不读 | 写给它的一条消息，`agent_unread_grace_s`（30 分钟）内没有回显 | 到点即结束 | 「没读到你」事件（`PROMPT_UNDELIVERED`） |
| API 拒绝 | Claude Code 的 `result` 带 `is_error: true`（额度用完、密钥失效、529 都是它），runner 报成 `api_error` / `error` 并带上状态码和构建自己的原话 | 立刻，作为一次失败结束 | Claude Code 自己的那句话原文进房间；要退避续上的临时故障（见下面「结束之后」）不发这张失败卡 |

三条「卡住」的判定互不重叠，一条长前台命令（跑 20 分钟的 pytest）一条都不触发：进程在，
它不输出，也没有人给它发消息。

**够不着不是没了。** 说协议的 runner 每答一次读就续一次租约；`LEASE_S`（120 秒）没续上，房间只
被告知「在等机器」，不结束任何一轮、不结算任何输入：runner 回来时会把这段时间里发生的事按顺序
报上来。

第二条只看「最近在说话」的会话：安静下来的会话不归它管，那由进程是否还在来回答。

第三条的两个细节：工具进行中不计时（输入只在工具边界被读），工具返回之后从返回那一刻起算；
它只在真有消息等着的时候存在，所以是四条里唯一有人在另一头等的那条。回执是什么由骨架决定：
Claude Code 是那条消息被原样回显（`--replay-user-messages` 的 `isReplay` 记录），由 runner
报成该输入的 `read`；写进 stdin 不算读到。

到点时 runner（Codex、pi 是房间）会打断会话（`interrupt`），让它停下手上那一轮；之后那条
`result` 就是这一轮的结束，失败种类是 `stuck`，不会再结束一次。

额度用完时，我们的计量代理回 429：知道真相的 admission 接口一拒就把平台自己那句额度
耗尽的提示发进房间，不等 Claude Code 重试十次、把连续的 429 读成 `authentication_failed`。
这一轮走到第四条、以出错的 `result` 收尾时，房间里重复的还是同一句平台的话，不是 Claude
Code 那句「Invalid API key」。

## 时限只记录，不结束

`agent_turn_hard_ceiling_s`（3 小时）是一个指标，不是闸门。过线时轮次记录里写
`ceiling_crossed_s`、打一条带频道的 warning，然后继续；没有任何东西因此结束。runtime 的外层
包装通过 `turn_ceiling` 帧拿到这个数。理由是墙钟分不出「重构到第三小时」和「卡住了」，而三条
判定各抓一种卡法之后，墙钟单独还能结束的只剩下正在干活、只是没干完的那一轮，那不是故障。

## 第一份输出之前

**冷启动熔断** `agent_first_output_timeout_s`（5 分钟）：从这轮的循环开始算，既没有一个
assistant 文本、也没有一次工具调用，就按「环境没起来」结束（没有容器、没有盘、没有模型
连接）。它问的是「这轮起来了没有」，所以准备阶段算在内；第一份输出到达它就退役。凭证
已知过期时熔断缩到 15 秒（`credential_expired_fuse_s`）。

## 结束之后

每种结束各发一次事件（`who=human`），交给人看一眼，修好后在房间里重新 @；下一轮接着同一个会话
（`agent-principles.md` 第十四条）。前三种平台不自动重跑。API 拒绝里属于模型服务临时故障的那些
（限流、过载、5xx/529、流中断、写明了重置时间的额度）由 `agent/turn_resume.py` 退避后自动续上，
连续次数有上限；密钥失效这类不会自己过去的不续，预计恢复时间超过 `RESUME_HORIZON`（30 分钟，
`agent/resumable_failures.py`）的也不续，这一轮按失败结束。两种情况对话里都留一句：排了自动重试的
说「这一轮没完成，约几点自动重试」，不续的说原因和预计恢复的时间。另外，发版替换 runner 时，在干活的旧 runner
先排空，到 `runner_drain_s` 还没干完才打断，被打断的那一轮（`runner_replaced`）立刻续上，不算次数。

## 设置一览

| 设置 | 默认 | 管什么 |
|---|---|---|
| `agent_no_progress_s` | 1800 | 有输出没动作多久算「只说话不干活」 |
| `runner_drain_s` | 1800 | 发版后，上一版在干活的 runner 最多再干多久才被打断（`RUNNER_DRAIN_S`） |
| `agent_unread_grace_s` | 1800 | 说给它的消息多久没回执算「不读」 |
| `agent_turn_hard_ceiling_s` | 10800 | 过线只记录，不结束 |
| `agent_first_output_timeout_s` | 300 | 一轮多久没有任何输出算「没起来」 |
| `RUNNER_GONE_S`（常量） | 120 | Codex、pi：一轮开着时 runner 够不着多久算没了 |
| `LEASE_S`（常量，`session_host/host.py`） | 120 | 说协议的 runner 够不着多久算「够不着」（不结束任何东西） |
| `TALKING_S`（常量） | 300 | 多近的输出算「正在说话」 |

数值和它们的取舍理由都写在 `backend/app/core/config.py` 各项的注释和 `room/sessions/`
常量旁的注释里；改数值先读那段注释。

## 相关

- `backend/app/domain/agent/harness/driven/lifecycle.py`：生命周期协议、`Watchdog`。
- `backend/app/domain/agent/harness/claude_code/runner.py`：Claude Code 的 runner 怎么得出每个
  事实。
- `backend/app/domain/agent/harness/driven/reporting.py`：读协议的那一侧，失败在这里翻译成房间
  的结束（`result_of`）。
- `backend/app/domain/agent/session_host/host.py`：读循环、租约（`_pump_reports`）；不说协议的
  骨架进程退出或够不着太久时交出 `Ended`。
- `backend/app/domain/agent/room/sessions/`：`Clock`（`records.py`）、`verdict`
  （`books.py`）、`_died`（`reading.py`），不说协议的骨架的后两条判定所在。
- `backend/app/domain/agent/turn/steps/execute.py`：冷启动熔断、外层时限、`ceiling_crossed_s`、结束后的
  事件。
- `docs/topics/turn活跃度检测.md`：两层机制（疑似卡死→探活、3 小时硬顶）的来历。
