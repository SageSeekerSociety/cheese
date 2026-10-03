---
title: 骨架
kind: 参考
summary: 平台驱动 Claude Code、Codex、pi 三种骨架的统一抽象和各自差异。
covers:
  - backend/app/domain/agent/harness/
  - backend/app/domain/agent/capability/
  - backend/app/domain/room_task/binding.py
  - backend/app/domain/machine/pi_dist.py
  - backend/scripts/test_harness_contracts.py
  - backend/tests/fixtures/harness-contract/
---

# 骨架 {#harness}

骨架是「谁在跑这条会话」：把平台的一轮翻译成某个 agent 程序（Claude Code、Codex、pi）听得懂的话，再把它的输出翻译回房间的事件。它是一个部署选项，不是模型的属性。

> 讲：骨架是谁、谁在选、注册与不注册的分别、四条硬性要求、能力矩阵、驱动层。不讲：一个会话怎么跑起来、跑在哪台机器，见[一条消息怎么变成芝士的一轮](/dev/turn)；平台工具怎么送到会话里，见[平台工具与会话侧 MCP](/dev/mcp)。

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

注册表列的是答得出下面四条硬性要求的骨架：答不出的留着代码不注册，能力矩阵里也就不占一列，答出四条的那天回到表里。`deployment_harnesses()` 因此只放注册了的骨架过去。

## 四条硬性要求 {#subagents}

派一条活是 agent 对骨架**原生 subagent** 的工具调用，平台这一侧没有「派活」的路径（结论 43）。那条路成立的前提是四条，它们不是能力位：

- 起子 agent，并指定它跑哪个模型
- 子 agent 的每个事件带可归到卡的线程标识
- 父线程能改它的指令
- 父线程能停掉它

pi 核心没有子 agent，四条由平台给它的 extension 和 runner 答：`Task` 在中心机上起第二个 pi，手在同一台执行机、同一个工作区里，模型经平台准入，子会话的每条记录带着线程标识写进会话自己的记录，`SendMessage` 与 `TaskStop` 改它、停它（`harness/pi/subagents.py`）。

`SubagentRequirement` 就是这四条。`Harness.__post_init__` 逐条要一个非空的 `str`：**答不全根本造不出来**，判在构造上而不是判在一条守卫测试上——注册表是一个字面量，一个造得出来的条目总会有人写进去。值只能是一句话，而 `Difference` 是 `StrEnum`、填进来照样是个 `str`，所以 `__post_init__` 认的是类型本身：硬性要求没有「暂缺」那一档。`backend/tests/contract/test_subagent_requirements.py` 还核这两件事：引的路径存在，引的符号真的**参与过代码**（被定义、被赋值、被读）。

## 平台只认这几个动词 {#contract}

`AgentRuntime`（Protocol）是平台对一个骨架的全部要求：`ensure`、`send`、`backlog`、`deliver`、`interrupt`、`close`，加上 `holds`、`memory()`、`keeps_memory` 这几个事实，以及三条回路：`bind_reader` 把会话说的、做的一条条交给房间（说了什么、开始和停下干活、读到了哪条输入、活怎么结束、机器够不够得着、正在写什么，都是 `agent/reads.py` 的 `Read`，由 `room_reads.py` 分给房间各自的账），`bind_memory` 在输入之前和一轮之后对记忆，`bind_unread_probe` 问房间还有没有输入没被读。四条硬性要求不在这里当第七个动词：平台不起子 agent，它们是骨架的事实，各写一句「怎么做到的」落在 `HARNESSES[harness].subagents` 上。

旁边几个小协议：`Backlog`（`unread` / `assemble` / `unfinished` / `landed` / `forget`）、`SessionControls`、`Opening`（一轮开场给会话的那些东西，含「这一轮要不要一双手」的 `needs_place`）、`SessionRef`（`(topic, agent_handle, harness)`，`agent_sessions` 的键）。

## 能力矩阵：一格都不许空 {#matrix}

每个骨架的适配层带一份 `Declaration`（词汇表在 `backend/app/domain/agent/capability/__init__.py`）：`pinned_version`（引用适配层那一个常量，不写第二遍字面量）、`built_ins`、`how_disabled`（每个概念一格）、`verified_against`（人手写的「对着哪个 build 读出来的」）。矩阵由 `capability/matrix.py` 汇总，**校验就在生成里**：一张画得出来、只是有几格空着的表会被当成一张填过的表读。

一格只有三种可能：一句「怎么关的」（那就是「有」）、`Difference` 里的一条码、或一个带 issue 和到期 pin 的 `Missing`。`Difference` 的名单是封闭的，所以「这一格我说不清」也得选一个已经存在的说法。`Missing` 到期即红：`pinned_version != until_pin` 时 `matrix()` 直接报，逼人按 issue 对着新 build 重核。

四格（提问、待办、提醒、自动同步）今天长这样：

| | Claude Code 2.1.282 | Codex 0.154.0 | pi 1.0.0 |
| --- | --- | --- | --- |
| 提问 | 自己带，两侧都拒（`DISALLOWED_TOOLS` + `settings.json` 的 deny），平台用 `cheese_ask` | 自己带；同步那个关得掉，异步那个这个 build 关不掉（#1880） | 不自带（工具联合八个里没有，内建扩展四个里也没有），平台用 `cheese_ask` |
| 待办 | 自己带，`TodoWrite`/`Task*` 两侧都拒，平台用 `todo_write` | 自己带，`tools.update_plan.enabled=False`，平台用 `todo_write` | 不自带，清单由平台建 |
| 提醒 | 自己带，Cron/Schedule 参数拒掉，平台用投递记录 | 未核 | 不自带 |
| 自动同步 | 不自带（同步是平台在 Stop 上装的 checkpoint） | 未核 | 不自带（同步是平台自己的机制，和骨架无关） |

`declarations()` 只认注册表，注册表里多一个而 `_DECLARED` 里没有就红；`written()` 连不在注册表里的骨架也认——摘掉一个骨架不是把它从树里拿走，它的 pin 和声明仍然归守卫管。

## 驱动层不是第四个骨架 {#driven}

Codex 和 pi 的驱动方式一样：会话机上一个 runner 拥有 agent 进程、说它的协议、把它产出的东西按稳定序号记进本地 journal，并且**每个输入至多接受一次**；后端从一个游标镜像那份 journal，每条会话一个 poller 把镜像到的东西交给房间。poller 一读完就接着读；runner 收到读，要等到游标之后有了新记录、或者 agent 正在写的内容变了才回答，最长等 `READ_WAIT_S`（25 秒），所以安静的会话每 25 秒才读一次，写下的东西又当场就到。agent 正在写的内容是它此刻生成的那一块：一段文字，或者一个工具调用和流到这里的参数原文（Claude Code 靠 `--include-partial-messages` 的 `stream_event`，pi 靠 `message_update`），只在 runner 内存里，不进 journal；跟着读的回答到后端，作为一帧 `live` 发到房间，不落库，那一块写完、它的记录进 journal 时清空（`driven/runner.py`、`live_frames.py`）。runner 在 `ping` 里声明 `long_poll`（和 `live`），后端在接上 runner 时核对；不声明 `long_poll` 的 runner 不受支持，接上时就被拒绝。只有协议不同，所以只有协议住在 `codex/` 和 `pi/` 里；journal、runner 的 socket 和输入账、drain 循环、poller 都在 `harness/driven/`。它是**共用的一层**，不是第四个骨架。

三种骨架的会话都在中心会话机上，手在房间的执行机上，用到才领（`CentralChannel`）。pi 的工具是 pi 自己的 read、write、edit、bash、ls、find、grep，平台的扩展换掉的只是它们底下的文件与进程操作（pi 的 `Operations` 注入点），经 runner（`pi/machine.py`）、走另外两个骨架同一个 `RemoteClient` 到执行机：一个文件操作是执行器自己答的一次调用（`control` 的 `files`，`remote_execution/machine_files.py`，路径不限于工作区，和 Claude Code 的 Read、Write 一样），bash 是一条命令，和 Codex 的一样在用户的 shell 里、先载入 profile 的快照。grep 的搜索本身不经注入的操作（pi 在自己那台机器上起 ripgrep），扩展把它整个换成执行器上的一次搜索（用平台装在执行机上的 ripgrep，见 `agent/toolchain.py`；还没装好时用机器自己 PATH 上的，都没有才按 git 不忽略的文件自己找），输出照 pi 的格式。执行器在 ping 里声明 `machine_files`；还没升级的旧执行器上，这些操作明说执行服务是旧版本，等它空闲升级。后台任务在执行机上有自己的终端（`pi/relay.py`），runner 把它的输出抄回中心机（`pi/jobs.py`）。仓库自己的说明和技能在会话到了机器上时读（`pi/repository.py`、`pi/project_skills.py`）；会话还在占位工作区时第一次领到机器，那次操作不执行，先把仓库的说明交给它，和另外两个骨架一样。

镜像里的记录只在两小时之内落进房间（`driven/subscription.py` 的 `STALE_S`，按 journal 记下的时间算：Claude Code 和 Codex 用会话机记录的时间，pi 用后端镜像到它的时间）。更老还没落的，只可能是这期间没人在读：后端或机器不在，或者每次 drain 都卡在同一条记录上。那时房间早已不等它了，这一轮已经结束，消息也重发过或告诉过人，所以游标直接越过它，房间不会再收到这些记录。

输入账（`driven/runner.py` 的 `Runner.accept`）是重连安全的那一半：id 是平台的，一个没看到回话的后端重发同一个 id 拿到的是同一个结果，而不是第二轮；同一个 id 配不同的正文当场拒绝——拿第一次的结果回答它会报告一件从没发出去的事。写进会话的一个输入，到的次数因此不由重连次数决定。

## pin 与契约夹具 {#pins}

| 骨架 | pin | 在哪 | 谁管 |
| --- | --- | --- | --- |
| Claude Code | `2.1.282` | `claude_code/device_launch.py` 的 `CLAUDE_PINNED_VERSION` | `backend/scripts/test_harness_contracts.py` |
| Codex | `0.154.0` | `codex/host.py` 的 `VERSION` | 同上；不在注册表也照样被它管着 |
| pi | `1.0.0` | `pi/launch.py` 的 `VERSION` | 不走那份脚本：它是按平台分的 tarball（`app/domain/machine/pi_dist.py`），契约由 `backend/tests/fixtures/harness-contract/` 那套夹具核 |

pin 的版本号只写一处：那份脚本从每份 `Declaration.pinned_version` 取，不再自己 `ast` 解文件或抄一个字面量。`.github/workflows/mcp-contract.yml` 管另一个方向的契约：执行器借这台机器的 `claude mcp serve` 做文件读写、从它的 Bash 工具取 shell 快照，两样都没有公开契约，所以 `scripts/remote_execution/mcp_contract.py` 就是契约本身（同处的 `headless_contract.py`、`equivalence.py`、`refresh_contract.py` 各管一段）。

## 骨架能指向什么、一条活用哪个模型 {#models}

`Harness` 的字段：`name`、`label`、`subagents`、`capabilities`、`speaks_gateway`、`carries_subscription`。这几个事实写在骨架上而不是模型上——以前是反过来的（每个模型带一张「允许哪些骨架驱动我」的名单），方向错得付出过代价：加一个骨架要改模型目录，拒绝一个组合时报的错还是关于模型的，而模型对这件事什么意见都没有。`speaks_gateway` 说它说不说平台网关自己那套形状（能，就所有模型都能驱动它）；`carries_subscription` 说它能不能承载 Anthropic 订阅凭据——那份凭据只为**一个**骨架铸造。`capabilities` 是可选能力（`Capability`），和四条硬性要求不同，答不出不妨碍注册，只是要它的地方用不了这个骨架；每一项也写一句「怎么做到的」，由同一份 `test_subagent_requirements.py` 核引文。今天只有一项「远端执行」，Claude Code 和 pi 声明了，Codex 没注册。

一条活具体用哪个模型由 `backend/app/domain/room_task/binding.py` 的 `resolve()` 定：显式绑在这条活上的 → 队友的 → 调用方给的默认 → 项目主模型，逐个往下；一个都没有就报「当前项目没有可用的默认模型」。各个骨架怎么把平台工具交到模型手里，见[平台工具与会话侧 MCP](/dev/mcp)。
