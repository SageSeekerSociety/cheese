# 一轮什么时候算卡住

这份文档描述现状：一轮活（从提示词送进会话，到会话自己收尾）在什么情况下会被平台结束、由谁
判、结束之后房间里看到什么。设计上的取舍在合入它们的 PR 里（#679、#685、#689、#691、#699，
都叠在 #703 之上）；这里只写落地之后的样子。

## 一句话

平台不按时间杀任何一轮。结束一轮的只有三种「卡住」的证据（进程没了、只说话不干活、发给它
的话它不读）加一种明确的失败（API 拒绝）。跑得再久，只要还在干活，就不动它。

## 会结束一轮的四种判定

| 情况 | 证据是什么 | 什么时候结束 | 房间看到什么 |
|---|---|---|---|
| 会话没有任何动静 | `agent_idle_suspect_s`（5 分钟）内一个 hook 都没到 | 先探进程（`confirm_alive`）；进程确认没了才结束，活着就继续等 | 轮次超时事件（`TURN_TIMEOUT`） |
| 只说话不干活 | 最近有输出（`MessageDisplay`），但最近一次「动作」（`PreToolUse` / `PostToolUse` / `Stop`）已经是 `agent_no_progress_s`（30 分钟）以前 | 到点即结束 | 轮次超时事件（`TURN_TIMEOUT`） |
| 发给它的消息它一直不读 | 提示词已送达之后又注入了一条消息，`agent_unread_grace_s`（30 分钟）内没有它的消费收据（`UserPromptSubmit`） | 到点即结束；没读的消息带进下一轮 | 「没读到你」事件（`PROMPT_UNDELIVERED`） |
| API 拒绝 | Claude Code 发 `StopFailure` 而不是 `Stop`（额度用完、密钥失效、529 都是它） | 立刻，作为一次失败结束 | Claude Code 自己的那句话原文进房间 |

三条「卡住」的判定互不重叠，一条长前台命令（跑 20 分钟的 pytest）一条都不触发：进程在，
它不输出，也没有人给它发消息。判定在两个时刻做——消费完一个 hook 之后、一次等待到期之后
——因为只有这两个时刻的时钟是新鲜的；不在循环顶部做，那里队列里可能还躺着没读的
`PreToolUse`。

第三条的两个细节：工具进行中不计时（输入只在工具边界被读），工具返回之后从返回那一刻起算；
它只在真有消息等着的时候存在，所以是四条里唯一有人在另一头等的那条。

第四条的一个已知缺口：额度用完时，我们的计量代理回 429，Claude Code 重试十次后把它读成
`authentication_failed`，于是房间里看到的是「Invalid API key」。知道真相的是拒掉它的
admission 接口，从那里告诉房间是另一处改动，见 #715。

## 时限只记录，不结束

`agent_turn_hard_ceiling_s`（3 小时）是一个指标，不是闸门。过线时监控记一次
`tracker.ceiling_crossed_at`、打一条带话题的 warning，轮次记录里写 `ceiling_crossed_s`，
然后继续；没有任何东西因此结束。只有这一个数：监控直接读它，runtime 的外层包装通过
`turn_ceiling` 帧拿到同一个数。理由是墙钟分不出「重构到第三小时」和「卡住了」，而三条判定
各抓一种卡法之后，墙钟单独还能结束的只剩下正在干活、只是没干完的那一轮，那不是故障。

时限从 `prompt_delivered` 那一帧起算（传输接受了写入），准备容器、连屏幕的时间不算在内。

## 一轮开始之前的两道口子

这两道不在「卡住」的范畴里，发生在会话真正开始干活之前，这次改动没有碰它们：

- **冷启动熔断** `agent_first_output_timeout_s`（5 分钟）：从这轮的循环开始算，既没有一个
  assistant 文本、也没有一次工具调用，就按「环境没起来」结束（没有容器、没有盘、没有模型
  连接）。它问的是「这轮起来了没有」，所以准备阶段算在内；第一份输出到达它就退役。凭证
  已知过期时熔断缩到 15 秒（`credential_expired_fuse_s`）。
- **送不到**：提示词写进传输之后，`DELIVERY_TIMEOUT_S`（25 秒）内没有收到会话的消费收据
  （`UserPromptSubmit`），按「没送到」结束（`PROMPT_UNDELIVERED`）。平台什么时候原样重发、
  什么时候只发事件，见 `spec.md` 里「平台自己收拾干净的事不通报」那一条。

## 结束之后

平台不自动重跑。每种结束各发一次事件（`who=human`），交给人看一眼，修好后在房间里重新 @；
下一轮接着同一个会话（`agent-principles.md` 第十四条）。

## 设置一览

| 设置 | 默认 | 管什么 |
|---|---|---|
| `agent_idle_suspect_s` | 300 | 多久没 hook 才去探进程 |
| `agent_no_progress_s` | 1800 | 有输出没动作多久算「只说话不干活」 |
| `agent_unread_grace_s` | 1800 | 注入的消息多久没被读算「不读」 |
| `agent_turn_hard_ceiling_s` | 10800 | 过线只记录，不结束 |
| `agent_first_output_timeout_s` | 300 | 一轮多久没有任何输出算「没起来」 |
| `DELIVERY_TIMEOUT_S` | 25 | 提示词多久没有消费收据算「没送到」 |

数值和它们的取舍理由都写在 `backend/app/core/config.py` 各项的注释里；改数值先读那段注释。

## 相关

- `backend/app/domain/agent/harness/claude_code/hooks_substrate.py`：`monitor_session_activity`
  是四条判定和时限记录所在，`ActivityTracker` 是它读的几个时钟。
- `backend/app/domain/agent/harness/claude_code/hook_events.py`：`StopFailure` 在这里翻译成一次
  失败的结束。
- `backend/app/domain/agent/runtime.py`：冷启动熔断、`prompt_delivered` 起算的外层时限、
  `ceiling_crossed_s`、结束后的事件。
- `docs/topics/turn活跃度检测.md`：两层机制（疑似卡死→探活、3 小时硬顶）的来历；其中「硬顶
  到点结束一轮」已被上面的「时限只记录」取代。
