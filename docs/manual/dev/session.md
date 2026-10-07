---
title: 会话与轮次
kind: 参考
summary: 一轮是什么、会话身份与座位、后台运行和发版交接。
covers:
  - backend/app/domain/agent/chat.py
  - backend/app/domain/agent/runtime.py
  - backend/app/domain/agent/models.py
  - backend/app/domain/agent/dispatch_log.py
  - backend/app/domain/agent/repositories.py
  - backend/app/domain/agent_session/
  - backend/app/domain/agent_instance/
---

# 会话与轮次 {#session}

会话是「一个 AI 队友在一段对话里的上下文」，一段对话是一个话题，或话题里的一条任务；轮次是「一次投喂到它停下来之间的那段区间」。这一页讲这两样东西在库里长什么样、后台由谁推着走、进程换人以后怎么接上。

> 讲：会话与轮次的数据模型、后台运行者、派发记录、发版交接。不讲：一条消息怎么一步步变成一轮，见[一条消息怎么变成芝士的一轮](/dev/turn)；机器怎么接进来，见[设备与机器接入](/dev/machines)。

## 三层：类型、实例、会话 {#layers}

`agent_instance/models.py` 和 `agent_session/models.py` 各占一层，加上「类型」，回答三个不同的问题：

| 层 | 表 | 回答 |
| --- | --- | --- |
| 类型 | （出厂设置） | 这个 agent 是什么 |
| 实例 | `agent_instances` | 它在这个项目里是谁、在这里学到了什么 |
| 会话 | `agent_sessions` | 一段对话（话题或任务）里的会话，可以随时丢掉 |

`agent_sessions` 的唯一索引是 `(conversation_id, agent_handle, harness)`（`uq_agent_sessions_conversation`），所以一间房可以同时坐几个队友、各留各的对话，每条任务也有自己的会话。`conversation_id` 指向登记表 `conversations(id, project_id, kind)`：`kind` 是 `room` 或 `task`，id 就是话题或任务自己的 id，由数据库触发器在话题、任务插入和删除时维护，应用不写它。房间范围的问题（房间的机器、换机、清理）问的是房间自己和它所有任务的会话：`conversation/services.of_room`。`agent_handle` 取的是 `ResolvedAgent.handle`——和这个 agent 的记忆池同名，不是实例的 uuid，也不是署名的 handle（`cheese-<话题十六进制>`）：后者答「谁做了这件事」，它答「这是谁的对话」。一个从没配过 agent 的项目根本没有实例行，而 `NULL` 在唯一索引里不等于 `NULL`。把话题交给另一个队友不丢东西：新队友查一个不存在的键、从头开始，交回来时旧行还在。

任务的会话和同一位队友在房间里的会话分开：状态目录按 `<队友>@<任务 id>` 取（`room/sessions.py`），会话凭证带 `k` = 任务 id，只能对这条任务动手（`core/sandbox_auth.py`）；任务还没「开始」时凭证带 `scratch`：改动留不下（见[支线与未开始的任务](#scratch)），启动环境里是 `CHEESE_TASK` 和 `CHEESE_KEEPS_NOTHING`，开始之后空闲的会话带着改动留得下的凭证重开。broker 上任务的频道就是任务 id。

## 一轮是一个区间 {#turn}

`agent_turns`（`agent/models.py`）一行就是一次投喂和它的下场。它有三个时刻：`started_at`、`delivered_at`、`stopped_at`，跑着就是 `stopped_at IS NULL`。没有 `created_at`——这一行本身就是它的时间戳。

- `delivered_at` 是传输收下了这次写入。它空着，就是平台自己说「会话没听到这句话」，这是唯一一种重发它算安全、而不是把一件有人正在做的事再做一遍的条件。
- `continuation_id` 是这条活的 id。一次重试继承被打断那一轮的 id，于是第一次已经做过的副作用认得出（`domain/idempotency/keys.py`）。
- `resendable` 说「把 `content` 原样再交一次」是不是等于这条活还该发生。人的消息、任务的开场提示、平台自己的提醒都是；续跑的提醒不是——「从上一轮的断点继续」对一个从没听过任务的会话什么也没说。
- `agent_handle` 是这一轮跑在谁的对话里，和 `agent_sessions` 同一个键（`ResolvedAgent.handle`）。组装这一轮时写上（`note_context`），会话自己开的那一轮开行时就带着；还没组装的一轮是 `NULL`。一间房同时坐着几个队友，房间答不了「这一轮的会话还在不在」，扫底按它逐个席位问。

行只关不删。一个 turn id 活在它产出的每一个块上，而被抹掉的区间是事后谁都问不到的那种。原来的样子是一个 `{workspace_root}/.turns-inflight.json`：它只能证明「此刻在跑」，所以扫底只能靠现场痕迹（有没有块带这个 turn id）反推。写入的一次性在 `AgentTurnRepository.mark_delivered`（`agent/repositories.py`）——只算第一笔。

## 会话的身份和它的两个地点 {#machine}

`AgentSession.runtime_location` 是这条会话的进程落在哪台会话机上，连同开它的通道和骨架自己的运行状态（平台不解析）；`place()` 是读它的唯一入口，没有它就没有地点，下一轮重新租，而不是去猜房间上记着什么。`placed_at` 排的是「谁最后开的屏」——不能拿 `updated_at` 排：那一列每轮存 `resume_token` 时也在动，「最后开屏的」会变成「最后说过话的」。

`work_lease` 是另一回事，它是算力选择落到这条会话上的那一份。一个话题一个容器（2026-09-28 决定，推翻结论 60 的后半）：一间房只有一条算力选择；任务没有自己的选择（`tasks.compute_config`）时用房间那一项，有就用自己的。解析时问的是选择本身，不问这一列。选自有设备时每条会话都工作在它算出来的那台机器上；选云端时每条会话各有一个沙箱，落在平台云主机池的哪台宿主机上由池子决定。为什么这么定、几个队友共用一台机器怎么不打架，见[同一话题里的几个 AI 队友](/dev/turn#seats)。

### 支线与未开始的任务 {#scratch}

支线的会话，和负责人还没「开始」的任务的会话，改动留不下：判断在 `Place.keeps_work`（`room_task/place.py`），支线里例行任务执行的那一轮除外。这时一轮的执行凭证带 `scratch`（`core/sandbox_auth.py`），执行路由（`api/routes/execution.py`）按机器分两种：

- 机器是这条会话独占的（它自己的沙箱，租约上 `own` 为真）：读、跑命令、改文件都照常，只拒绝把改动带出机器的调用——同步检查点和项目的 MCP 服务。
- 机器和别的会话或机器主人共用（整台机器授权的设备、Windows、整台云虚拟机）：只能读，和文档芝士借房间机器时一样，外加 Claude Code 自己的 `Read`。

执行路由由设备连接的属主服务，发布应用时属主留在旧镜像上；所以这张凭证同时带 `ro`，认不得 `scratch` 的旧属主照只读处理，换成新属主后才按上面两种走。

推送不在执行路由上拦：机器上的命令拿推送凭证时，`/sandbox/forge-token` 和 GitHub 中转的 `git-receive-pack` 按凭证里的会话问 `session_keeps_work`，留不下就拒绝。

## 后台的 runner 与 broker {#background}

`agent/runtime.py` 里是两个进程内的东西。`InProcessBroker` 把帧发给订阅者、并给每个频道留一段重放缓冲，所以 WebSocket 断开只掉订阅者，模型请求和会话照跑。`AgentWorkRunner` 管准入、超时和恢复账：`submit()` 立刻返回 turn id，同一话题的轮次在 `ChatService` 的话题锁上排队。

`submit()` 没有一个参数说「跑一轮」——一轮是 agent 那一档收件人的到达形态，调用点只能点名，跑不跑由 `_a_turn_was_addressed` 从寻址结果读出来。跑着的一轮记在 `_live` 里，记的是那个 `asyncio.Task` 而不只是 id：一轮也可能在里面而卡住了（子容器死了、流永远不结束），认领它得先取消它。`_last_frame_at` 记每个活轮最后一次发帧的时刻，帧包含不落块的工具调用，所以数据库看不见的动静它看得见。

超时有两档：`turn_timeout_s`（默认 900 秒）问「这一轮是不是太久了」，`first_output_timeout_s`（默认 300 秒，0 关掉）问「这一轮到底起没起」。这一轮的模型凭据已经过期时，冷启动那一档会缩到 `credential_expired_fuse_s`，房间里那句原因说的也是这一条，而不是去猜容器、磁盘或网络。

## 扫底：孤儿、卡住的轮、丢了的消息 {#sweep}

- `sweep_orphans` 收拾还开着的区间。`SWEEP_MIN_AGE_S`（60 秒）挡掉刚出现、可能正被别的路径处理的那些；启动时用 `resume_orphans` 跑同一个函数，把这道门关掉（`min_age_s=0.0`）——那一刻开着的区间全属于上一任，没有东西在跟它赛跑。
- `_wedged_turns` 找**两个**信号都安静了的轮：话题里最新块的时间，以及这个进程为它发过的最后一帧；阈值 `SILENT_TURN_S`（1800 秒）。启动时不传 `last_activity`，这个探针整个跳过。
- 一轮算不算被接着做（`_adopted`）问的是**这一轮自己那个席位**的会话还在不在，不是房间里有没有哪个会话还在：同房间另一个队友还在干活，不能让一个会话已经没了的队友那一轮一直开着。
- `resume_lost_messages` 补上重启丢掉的排队：项目级并发闸是纯 asyncio 的，进程一换就没了。它按席位判：点名的那个队友没有轮次在跑就起一轮，每个队友最多一轮；别的队友在跑不挡它，因为那一轮不会读一条不是点给它的消息。还没组装、说不出是谁的一轮，按整间房在忙算。它不只在接手时跑：一轮结束时它的完成、空闲和 Stop 各唤醒一次（Stop 是关掉这一轮区间的那一下，排在它后面的消息要等它），另有每 10 秒一次的兜底扫描（`queued_message_sweep_interval_s`），读的是部分索引 `ix_blocks_queued_messages`，所以一次唤醒漏了，消息最多再等一个间隔。

## 派出去过什么 {#dispatch}

一次执行器调用今天只有两种落点：返回结果，或者抛异常。中间那一种——发出去了、而结果永远不会回来——过去没有名字，崩溃恢复之后分不开「确定没做」和「可能做过」。`dispatches` 表（`agent/dispatch_log.py`）给这一种命名：三态 `done` / `failed` / `unknown`，其中 `unknown` 在库里就是「没有人写回来」（`outcome IS NULL`），读出来补成 `Outcome.unknown`。

只有带 id 的调用在这里留一行：带 id 是执行器说「这次重放要按 key 判重」的方式；不带 id 的（`ping`、`context`、`prepare`）问两遍和问一遍一样。行在**发出之前**提交——这份记录存在的全部理由就是它要比发出它的那个进程活得久。

`settle()` 第一笔算数，判重写在 `WHERE outcome IS NULL` 里，一条 UPDATE。理由是 `unknown` 写下时房间里已经有一条通知、有人正照着它去查那次改动落地没有；一个迟到的 `done` 把它抹平之后，留下的是一个仍然被要求确认的人，而没有人还记着他为什么被叫来。

`unsettled()` 不按年龄过滤。它曾经按「比一次调用能在飞的时间（660 秒）还老」判，那在真实时序下整档落空：崩溃和属主重启通常发生在派发之后几秒到几分钟，启动扫底紧跟着就跑，每一行都还太年轻。分开「还没写回来」和「不会写回来」的不是时间，是**谁在读**：这张表只有一个读者，就是孤儿扫底里的重派路径，而送到它面前的话题屏幕已经没了、或者那条消息根本没送到屏幕——要这些调用的那一轮已经关了，所以无论那台机器后来怎么样，结果都到不了 agent 面前。调用方另给一个 `since`，取的是这几轮里最早的开始时刻。

## 发版时怎么交接 {#handover}

属主换人时，进来的一侧先 `hold_turns()`：不是主人的进程不知道房间里在跑什么，而一轮是按「我知道什么在跑」起的。`let_go()` 停下手里的活，其中 `settle_deliveries()` 先等每一轮已经发出去的提示被会话收下——一条写进正在跑的会话的消息，要到会话的回执读回来才算消费掉；没等完就停读，下一轮会把它再发一遍。

新主人这边 `ChatService.recover_sessions`（`chat.py`）重新监听活过这个进程的会话，并补上没人听的那段时间里它们说过的话。它分开做两件事：`recover` 确立「我们在听」，`replay` 交回尾巴。房间里已经显示过的是平台这一侧的（`_said()` 拿它避免同一条落两次），骨架自己还有哪些记录没落是骨架那一侧的。一台会话机够不着（`DeviceOffline`）或者回话说不行（`DeviceCallError`）都只记一行日志，下一次连接会做这件事。锁和超时的细节见[部署拓扑](/dev/topology#handover)。
