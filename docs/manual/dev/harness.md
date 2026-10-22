---
title: 骨架
kind: 参考
summary: 平台驱动 Claude Code、Codex、pi 三种骨架的统一抽象和各自差异。
covers:
  - backend/app/domain/agent/harness/
  - backend/app/domain/agent/session_host/
  - backend/app/domain/agent/room/
  - backend/app/domain/agent/capability/
  - backend/app/domain/room_task/binding.py
  - backend/app/domain/machine/pi_dist.py
  - backend/scripts/test_harness_contracts.py
  - backend/tests/fixtures/harness-contract/
---

# 骨架 {#harness}

骨架是「谁在跑这条会话」：把平台的一轮翻译成某个 agent 程序（Claude Code、Codex、pi）听得懂的话，再把它的输出翻译回房间的事件。它是一个部署选项，不是模型的属性。

> 讲：骨架是谁、谁在选、注册与不注册的分别、能力矩阵、驱动层。不讲：一个会话怎么跑起来、跑在哪台机器，见[一条消息怎么变成芝士的一轮](/dev/turn)；平台工具怎么送到会话里，见[平台工具与会话侧 MCP](/dev/mcp)。

## 谁在选骨架 {#which}

骨架名全仓只在 `backend/app/domain/agent/harness/__init__.py` 顶上写一遍（不变量 I5）：`CLAUDE_CODE` / `CODEX` / `PI`。选择分三步，都在同一个文件里：

| 函数 | 回答 |
| --- | --- |
| `deployment_harnesses()` | 这套部署可用哪些，按偏好排好：读 `settings.agent_harnesses`（例如 `["claude-code", "pi"]`），每个都必须**在注册表里**，配错是起不来；没配就是模块常量 `_UNCONFIGURED` |
| `harness_for(project_settings)` | 这个项目跑哪个，不问机器：项目设置的 `harness` 键（`HARNESS_SETTING`）指定的那个，前提是部署列了它；否则是部署偏好的第一个 |
| `harness_on(project_settings, offered)` | 这个项目在一台机器上跑哪个：同样的次序，取第一个这台机器挂着的；一个都没有就是 `None`，这一轮在房间里说明、不开始 |

房间的一轮问的是 `ComputePool.choose`：它拿这台机器挂着哪些骨架去问 `harness_on`。一个骨架挂不挂得上一台机器，看它能不能把工具送到那台机器的手上：被 `CentralChannel` 包起来的通道会话在中心机、手在执行机，只挂声明了 `Capability.REMOTE_EXECUTION` 的骨架（`compute.py` 的 `build_compute_pool`）。项目设置写了一个没有适配层的名字是配置错误，直接报；写了一个有适配层、部署却没列的，按部署偏好往下取。

## 注册的是三个里的两个 {#registry}

`HARNESSES`（`harness/__init__.py`）有 `claude-code` 和 `pi` 两条。`codex/` 的适配层也在、也在跑、也有完整的契约夹具和行为声明，只是没注册。

`deployment_harnesses()` 只放注册了的骨架过去，没注册的在能力矩阵里也不占一列。

骨架自己的原生 subagent（Claude Code 的 `Agent`，pi 由平台扩展给的 `Task`，见 `harness/pi/subagents.py`）是会话内部的事，和任务无关：一条任务是它自己的一条会话（见[会话](/dev/session)），不是谁起的子 agent。pi 的分身各是一个 pi 进程：一条会话同时最多跑 `RUNNING_AT_ONCE`（4）个，再起会被拒并告诉模型先等一个或停掉一个；分身的进程自己退出（崩溃、因内存被杀）时当场记为失败，等它的父会话拿到原因，会话也不再算它在忙；向平台申请模型的那段时间里这一轮被打断，分身就不起。

## 平台只认这几个动词 {#contract}

骨架之上是会话核心（`agent/session_host/`，`SessionHost`）。它不认识房间、文档和人，对三种骨架都一样：`start` 照一份 `SessionSpec` 起一条会话，`attach` 接上已经在跑的，`send` 交一个输入、`steer` 在干活时插一句、`stop` 停下，`status` 问它在不在干活，`memory` 和 `control` 转给会话的 runner，`read` 把会话说的、做的一条条交出来（说了什么、开始和停下干活、读到了哪条输入、活怎么结束、机器够不够得着、正在写什么，都是 `session_host/reads.py` 的一种）。各骨架只回答自己协议写法不同的地方：怎么起、怎么读、停用哪个调用、收下输入算不算读到，这些写在各自的驱动里（`session_host/driver.py` 的 `Driver`，实现是同目录的 `pi.py`、`claude_code.py`、`codex.py`）。会不会把记忆对账回平台是注册表上的事实（`Harness.keeps_memory`），只经 `session_host.host.keeps_memory` 一处读。

房间、文档里的芝士、个人芝士各自从核心装起来。房间的那一份是 `agent/room/sessions/` 的 `RoomSessions`：`ensure`、`send`、`backlog`、`steer`、`interrupt`、`close` 这几个动词，加上 `holds` 这类事实；它记房间自己的账（每个座位在干哪件活、干活时插进来的话被哪件活读到；runner 不报生命周期的骨架——今天是 Codex 和 pi——还有判卡住的几只钟，Claude Code 的这几只钟在它的 runner 里，见下面[驱动层](#driven)），再经 `report_to` 交给房间：读到的每一条交给 `room/reads.py` 分到房间各自的账，输入之前和一轮之后对记忆，问房间还有没有输入没被读。

旁边几个小协议：`Backlog`（`unread` / `assemble` / `unfinished` / `landed` / `forget`）、`SessionRef`（`(conversation, agent_handle, harness)`，`agent_sessions` 的键；任务的会话带 `task_id`，`topic_id` 仍是它所在的房间）。

## 能力矩阵：一格都不许空 {#matrix}

每个骨架的适配层带一份 `Declaration`（词汇表在 `backend/app/domain/agent/declaration.py`）：`pinned_version`（引用适配层那一个常量，不写第二遍字面量）、`built_ins`、`how_disabled`（每个概念一格）、`verified_against`（人手写的「对着哪个 build 读出来的」）。矩阵由 `capability/matrix.py` 汇总，**校验就在生成里**：一张画得出来、只是有几格空着的表会被当成一张填过的表读。

一格只有三种可能：一句「怎么关的」（那就是「有」）、`Difference` 里的一条码、或一个带 issue 和到期 pin 的 `Missing`。`Difference` 的名单是封闭的，所以「这一格我说不清」也得选一个已经存在的说法。`Missing` 到期即红：`pinned_version != until_pin` 时 `matrix()` 直接报，逼人按 issue 对着新 build 重核。

四格（提问、待办、提醒、自动同步）今天长这样：

| | Claude Code 2.1.299 | Codex 0.154.0 | pi 1.0.0 |
| --- | --- | --- | --- |
| 提问 | 自己带，两侧都拒（`DISALLOWED_TOOLS` + `settings.json` 的 deny），平台用 `cheese_ask` | 自己带；同步那个关得掉，异步那个这个 build 关不掉（#1880） | 不自带（工具联合八个里没有，内建扩展四个里也没有），平台用 `cheese_ask` |
| 待办 | 自己带，`TodoWrite`/`Task*` 两侧都拒，平台用 `todo_write` | 自己带，`tools.update_plan.enabled=False`，平台用 `todo_write` | 不自带，清单由平台建 |
| 提醒 | 自己带，Cron/Schedule 参数拒掉，平台用投递记录 | 未核 | 不自带 |
| 自动同步 | 不自带（同步是平台每轮结束时的 checkpoint，这里由平台装的 Stop 钩子要） | 未核（同步是平台每轮结束时的 checkpoint，由 runner 要） | 不自带（同上，由 runner 要） |

`declarations()` 只认注册表，注册表里多一个而 `_DECLARED` 里没有就红；`written()` 连不在注册表里的骨架也认——摘掉一个骨架不是把它从树里拿走，它的 pin 和声明仍然归守卫管。

## 驱动层不是第四个骨架 {#driven}

三种骨架的驱动方式一样：会话机上一个 runner 拥有 agent 进程、说它的协议、把它产出的东西按稳定序号记进本地 journal，并且**每个输入至多接受一次**。读法分两种。Codex 和 pi 的后端从一个游标镜像那份 journal（`driven/subscription.py`），会话核心的 `read` 把镜像到的东西交给听它的那一方，一轮何时开始、何时结束也从这些记录里认。Claude Code 的 runner 说生命周期协议（`driven/lifecycle.py`，注册表上 `Harness.reports_lifecycle`）：它把会话的事实写成带 worker epoch 和序号的事件——输入写进去了、读到了、被放掉了，会话在干活还是空闲（空闲时带这件活怎么结束、哪些输入读进了这件活、失败的类型），进程退出——存在自己的发件箱里；后端（`driven/reporting.py`）把一个事件和它在库里的效果放在一个事务里应用（`session_state.take`，`session_offsets` 记到哪个序号），之后才确认，所以重发一次什么也不改。不是当前 epoch 的写入一律拒绝。一个输入读到了就算送到，没有别的「完成」事实；判卡住的两只钟（说话不进展、输入没人读）也在 runner 里，到点由它打断会话，接下来的空闲事件把失败记成 `stuck`。Claude Code 的 journal 记录照样读回来，只做原始记录和房间显示，不决定任何事。两种读法都是一读完就接着读；runner 收到读，要等到游标之后有了新记录、或者 agent 正在写的内容变了才回答，最长等 `READ_WAIT_S`（25 秒），所以安静的会话每 25 秒才读一次，写下的东西又当场就到。agent 正在写的内容是它此刻生成的那一块：一段文字，或者一个工具调用和流到这里的参数原文（Claude Code 靠 `--include-partial-messages` 的 `stream_event`，pi 靠 `message_update`），只在 runner 内存里，不进 journal；跟着读的回答到后端，作为一帧 `live` 发到房间，不落库，那一块写完、它的记录进 journal 时清空（`driven/runner.py`、`live_frames.py`）。runner 在 `ping` 里声明 `long_poll`（和 `live`），后端在接上 runner 时核对；不声明 `long_poll` 的 runner 不受支持，接上时就被拒绝。一轮结束时 runner 还替会话要一次 checkpoint（`turn_ended`，见 [每轮的检查点](/dev/tasks#checkpoint)），Claude Code 那边是 Stop 钩子要的同一个动作。只有协议不同，所以只有协议住在 `codex/`、`pi/`、`claude_code/` 和核心里各自的驱动里；journal、runner 的 socket 和输入账、drain 循环、镜像、生命周期协议都在 `harness/driven/`。它是**共用的一层**，不是第四个骨架。

生命周期协议的版本（`lifecycle.PROTOCOL`）一变，发版时会话机上还活着的 runner 就都是上一版的：runner 比启动它的那一版后端活得久，新版不读旧协议。新版只读它们的 `ping`，所以 `ping` 是跨版本的握手，这几个字段名字和含义每一版都保留：`alive`、`lifecycle`、`launch`、`session_id`、`working`（忙闲：一件活从第一个迹象到结束都为真，工具在跑时也是）、`work_id`、`worker_epoch`（它持有的登记）、`endings`（它最近结束的几件活，每件带 `work`、失败类型 `failure`（干净结束时为空）和它读进去的平台输入 `inputs`）。以后改协议只能往 `ping` 里加字段，不能删或改这几个（`tests/unit/test_runner_ping_handshake.py` 用真 runner 核）。接管时（`room/sessions/lifecycle.py`），空闲的旧 runner 当场换掉：`endings` 里它读进去的输入记成已读，别的没读的丢掉、消息重新可递，座位空下来就起一个这一版的 runner 续同一个会话（`prewarm_due`）。在干活的旧 runner 先排空，和自托管 GitHub Actions runner 在分到 job 时才自我更新、`kubectl drain` 先封锁节点再按 pod 的终止宽限期驱逐、Temporal 旧版本 worker 跑完钉在它上面的 workflow 是一回事：不打断、不再给它新输入，发给这个座位的消息在原有的等待队列里等（`admitted_initial`），房间里只在确有消息在等时说一句；`ping` 说它空了就换掉：`endings` 说那件活失败了，这一轮以 `runner_replaced` 结束、在新 runner 上续上，否则按正常结束收；`endings` 里各件活读进去的输入记成已读，不再重递，没有读的记录的照旧丢掉、重新可递。它干的事是经平台工具落地的，不靠这一版读不了的记录，所以这一轮照样落地。排空期间丢的是生命周期信号、现场的更新和房间里的会话控制，换掉之后就回来；关任务、把它移出频道照样能打断它（`RoomSessions.interrupt`），这样结束的活不再续。平台自己的活（记忆整理）不过准入，排空期间在这个座位上起会话会被拒（`ensure`），那一次记忆整理就算失败，等它的认领过期后才会再做。排空有上限 `RUNNER_DRAIN_S`（`settings.runner_drain_s`，默认 1800 秒，从排空开始时算起；开始的时刻记在这个座位的会话行上（`agent_sessions.drain_started_at`），排空中途换了后端也接着同一个时刻算）：到点还在干活，就用它 `ping` 里的 `worker_epoch` 打断，再问它几次那件活怎么结束、读进去了什么（最多 `DRAIN_STOP_WAIT_S`），这一轮以 `runner_replaced` 结束、在新 runner 上立刻续上。排空开始、换掉、到点打断各记一行日志（`runner_drain_started`、`runner_replaced`、`runner_drain_timed_out`）。

三种骨架的会话都在中心会话机上，手在房间的执行机上，用到才领（`CentralChannel`）。pi 的工具是 pi 自己的 read、write、edit、bash、ls、find、grep，平台的扩展换掉的只是它们底下的文件与进程操作（pi 的 `Operations` 注入点），经 runner（`pi/machine.py`）、走另外两个骨架同一个 `RemoteClient` 到执行机：一个文件操作是执行器自己答的一次调用（`control` 的 `files`，`remote_execution/machine_files.py`，路径不限于工作区，和 Claude Code 的 Read、Write 一样），bash 是一条命令，和 Codex 的一样在用户的 shell 里、先载入 profile 的快照。grep 的搜索本身不经注入的操作（pi 在自己那台机器上起 ripgrep），扩展把它整个换成执行器上的一次搜索（用平台装在执行机上的 ripgrep，见 `agent/toolchain.py`；还没装好时用机器自己 PATH 上的，都没有才按 git 不忽略的文件自己找），输出照 pi 的格式。执行器在 ping 里声明 `machine_files`；还没升级的旧执行器上，这些操作明说执行服务是旧版本，等它空闲升级。后台任务在执行机上有自己的终端（`pi/relay.py`），runner 把它的输出抄回中心机（`pi/jobs.py`）。仓库自己的说明和技能在会话到了机器上时读（`pi/repository.py`、`pi/project_skills.py`）；会话还在占位工作区时第一次领到机器，那次操作不执行，先把仓库的说明交给它，和另外两个骨架一样。

Codex 和 pi 没有 Claude Code 的 `Monitor`，平台照 Claude Code 2.1.282 的语义补了一个同名工具（`driven/monitor.py`，工具定义也在那里）：命令在执行机上、用 Bash 同一个 shell 后台跑，调用当场返回任务 id；它每打一行 stdout，runner 就对会话说一次（200 ms 内的几行并成一条，令牌桶 10 条、每 2 秒补一条，压了 30 秒还在刷就停掉）；命令退出、到 `timeout_ms`（默认 5 分钟，最多 30 分钟）或被 `TaskStop` 停下时结束。会话正在干活时这句话插进当前这一轮（Codex 的 `turn/steer`，pi 的 `steer`），闲着时它自己开一轮（`unsolicited`，和 pi 的分身在后台结束时一样）。命令照项目对 Bash 的 `permissions.deny` 判，和 Claude Code 房间里的 Monitor 一样；分身没有它，它打的字只说给会话本身。

会话的记录只在两小时之内落进房间（`driven/subscription.py` 的 `STALE_S`，`driven/reporting.py` 读 Claude Code 时用同一个值；按 journal 记下的时间算：Claude Code 和 Codex 用会话机记录的时间，pi 用后端镜像到它的时间）。更老还没落的，只可能是这期间没人在读：后端或机器不在，或者每次 drain 都卡在同一条记录上。那时房间早已不等它了，这一轮已经结束，消息也重发过或告诉过人，所以游标直接越过它，房间不会再收到这些记录。

输入账（`driven/runner.py` 的 `Runner.accept`）是重连安全的那一半：id 是平台的，一个没看到回话的后端重发同一个 id 拿到的是同一个结果，而不是第二轮；同一个 id 配不同的正文当场拒绝——拿第一次的结果回答它会报告一件从没发出去的事。写进会话的一个输入，到的次数因此不由重连次数决定。

## pin 与契约夹具 {#pins}

| 骨架 | pin | 在哪 | 谁管 |
| --- | --- | --- | --- |
| Claude Code | `2.1.299` | `claude_code/device_launch.py` 的 `CLAUDE_PINNED_VERSION` | `backend/scripts/test_harness_contracts.py` |
| Codex | `0.154.0` | `codex/host.py` 的 `VERSION` | 同上；不在注册表也照样被它管着 |
| pi | `1.0.0` | `pi/launch.py` 的 `VERSION` | 不走那份脚本：它是按平台分的 tarball（`app/domain/machine/pi_dist.py`），契约由 `backend/tests/fixtures/harness-contract/` 那套夹具核 |

pin 的版本号只写一处：那份脚本从每份 `Declaration.pinned_version` 取，不再自己 `ast` 解文件或抄一个字面量。`.github/workflows/mcp-contract.yml` 管另一个方向的契约：执行器借这台机器的 `claude mcp serve` 做文件读写、从它的 Bash 工具取 shell 快照，两样都没有公开契约，所以 `scripts/remote_execution/mcp_contract.py` 就是契约本身（同处的 `headless_contract.py`、`equivalence.py`、`refresh_contract.py` 各管一段）。

## 骨架能指向什么、一条活用哪个模型 {#models}

`Harness` 的字段：`name`、`label`、`capabilities`、`speaks_gateway`、`carries_subscription`、`keeps_memory`、`controls` / `executor_controls`。这几个事实写在骨架上而不是模型上——以前是反过来的（每个模型带一张「允许哪些骨架驱动我」的名单），方向错得付出过代价：加一个骨架要改模型目录，拒绝一个组合时报的错还是关于模型的，而模型对这件事什么意见都没有。`speaks_gateway` 说它说不说平台网关自己那套形状（能，就所有模型都能驱动它）；`carries_subscription` 说它能不能承载 Anthropic 订阅凭据——那份凭据只为**一个**骨架铸造。`capabilities` 是可选能力（`Capability`），答不出不妨碍注册，只是要它的地方用不了这个骨架；每一项写一句「怎么做到的」，声明了却说不出的，`Harness.__post_init__` 根本造不出来。今天只有一项「远端执行」，Claude Code 和 pi 声明了，Codex 没注册。

一条活具体用哪个模型由 `backend/app/domain/room_task/binding.py` 的 `resolve()` 定：显式绑在这条活上的 → 队友的 → 调用方给的默认 → 项目主模型，逐个往下；一个都没有就报「当前项目没有可用的默认模型」。各个骨架怎么把平台工具交到模型手里，见[平台工具与会话侧 MCP](/dev/mcp)。
