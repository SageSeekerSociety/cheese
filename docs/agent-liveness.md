# 一轮什么时候算卡住

这份文档描述现状：一轮活（从提示词送进会话，到会话自己收尾）在什么情况下会被平台结束、由谁
判、结束之后房间里看到什么。

## 一句话

平台不按时间杀任何一轮。结束一轮的只有三种「卡住」的证据（进程没了、只说话不干活、发给它
的话它不读）加一种明确的失败（API 拒绝）。跑得再久，只要还在干活，就不动它。

前三种由 `harness/driven/runtime.py` 判，对每种骨架都一样：它们读的是房间自己的事件词汇
（说了话、调了工具、工具返回、一轮结束），不读任何骨架私有的东西。第四种是骨架自己报的
失败，平台只负责把它原话交给房间。

## 会结束一轮的四种判定

| 情况 | 证据是什么 | 什么时候结束 | 房间看到什么 |
|---|---|---|---|
| 进程没了 | 一轮开着时 runner 的 `ping` 答 `alive: false`（会话进程已退出）；或者 runner 连续 `RUNNER_GONE_S`（120 秒）够不着 | 立刻 | 「会话进程已退出」的失败结束 |
| 只说话不干活 | 最近 `TALKING_S`（5 分钟）内有输出，但最近一次「动作」（工具调用、工具返回、一轮结束）已经是 `agent_no_progress_s`（30 分钟）以前 | 到点即结束 | 轮次超时事件（`TURN_TIMEOUT`） |
| 发给它的消息它一直不读 | 一轮进行中又说给它的一条消息，`agent_unread_grace_s`（30 分钟）内没有回执 | 到点即结束；没读的消息带进下一轮 | 「没读到你」事件（`PROMPT_UNDELIVERED`） |
| API 拒绝 | Claude Code 的 `result` 带 `is_error: true`（额度用完、密钥失效、529 都是它） | 立刻，作为一次失败结束 | Claude Code 自己的那句话原文进房间 |

三条「卡住」的判定互不重叠，一条长前台命令（跑 20 分钟的 pytest）一条都不触发：进程在，
它不输出，也没有人给它发消息。

它们读的时钟（`Clock`）由订阅在读到每批记录时推（`pulse`）：订阅把记录翻成房间事件，
`subscription.marks_of` 从事件里认出「说了话」「有动作」「哪个工具开始 / 返回了」，runtime
按这些标记更新时钟。判定在读循环每次读完之后、`ping` 过 runner 之后做。

第二条只看「最近在说话」的会话：安静下来的会话不归它管，那由进程是否还在来回答。

第三条的两个细节：工具进行中不计时（输入只在工具边界被读），工具返回之后从返回那一刻起算；
它只在真有消息等着的时候存在，所以是四条里唯一有人在另一头等的那条。回执是什么由骨架决定：
Claude Code 是那条消息被原样回显（`--replay-user-messages` 的 `isReplay` 记录），由 runner
记在日志里；写进 stdin 不算读到。

按判定结束一轮之后，平台还会打断会话（`interrupt`），让它停下手上那一轮；同一轮之后再到的
`result` 不会再结束一次。

额度用完时，我们的计量代理回 429：知道真相的 admission 接口一拒就把平台自己那句额度
耗尽的提示发进房间，不等 Claude Code 重试十次、把连续的 429 读成 `authentication_failed`。
这一轮走到第四条、以出错的 `result` 收尾时，房间里重复的还是同一句平台的话，不是 Claude
Code 那句「Invalid API key」。

## 时限只记录，不结束

`agent_turn_hard_ceiling_s`（3 小时）是一个指标，不是闸门。过线时轮次记录里写
`ceiling_crossed_s`、打一条带话题的 warning，然后继续；没有任何东西因此结束。runtime 的外层
包装通过 `turn_ceiling` 帧拿到这个数。理由是墙钟分不出「重构到第三小时」和「卡住了」，而三条
判定各抓一种卡法之后，墙钟单独还能结束的只剩下正在干活、只是没干完的那一轮，那不是故障。

## 第一份输出之前

**冷启动熔断** `agent_first_output_timeout_s`（5 分钟）：从这轮的循环开始算，既没有一个
assistant 文本、也没有一次工具调用，就按「环境没起来」结束（没有容器、没有盘、没有模型
连接）。它问的是「这轮起来了没有」，所以准备阶段算在内；第一份输出到达它就退役。凭证
已知过期时熔断缩到 15 秒（`credential_expired_fuse_s`）。

## 结束之后

平台不自动重跑。每种结束各发一次事件（`who=human`），交给人看一眼，修好后在房间里重新 @；
下一轮接着同一个会话（`agent-principles.md` 第十四条）。

## 设置一览

| 设置 | 默认 | 管什么 |
|---|---|---|
| `agent_no_progress_s` | 1800 | 有输出没动作多久算「只说话不干活」 |
| `agent_unread_grace_s` | 1800 | 说给它的消息多久没回执算「不读」 |
| `agent_turn_hard_ceiling_s` | 10800 | 过线只记录，不结束 |
| `agent_first_output_timeout_s` | 300 | 一轮多久没有任何输出算「没起来」 |
| `RUNNER_GONE_S`（常量） | 120 | 一轮开着时 runner 够不着多久算没了 |
| `TALKING_S`（常量） | 300 | 多近的输出算「正在说话」 |

数值和它们的取舍理由都写在 `backend/app/core/config.py` 各项的注释和 `driven/runtime.py`
常量旁的注释里；改数值先读那段注释。

## 相关

- `backend/app/domain/agent/harness/driven/runtime.py`：`Clock`、`verdict`、`_gone`、`_died`，
  三条判定所在。
- `backend/app/domain/agent/harness/driven/subscription.py`：`marks_of`，时钟读的标记。
- `backend/app/domain/agent/harness/claude_code/events.py`：出错的 `result` 在这里翻译成一次
  失败的结束。
- `backend/app/domain/agent/runtime.py`：冷启动熔断、外层时限、`ceiling_crossed_s`、结束后的
  事件。
- `docs/topics/turn活跃度检测.md`：两层机制（疑似卡死→探活、3 小时硬顶）的来历。
