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

骨架名全仓只在 `backend/app/domain/agent/harness/__init__.py` 顶上写一遍（不变量 I5）：`CLAUDE_CODE` / `CODEX` / `PI`。解析有三层，都在同一个文件里：

| 函数 | 回答 |
| --- | --- |
| 模块常量 `_UNCONFIGURED` | 部署设置没写时跑的那个 |
| `deployment_harness()` | 这套部署跑哪个：读 `settings.agent_harness`，还必须**在注册表里**，配错是起不来 |
| `harness_for(project_settings)` | 这个项目跑哪个：项目设置的 `harness` 键（`HARNESS_SETTING`）盖过部署设置 |

`_known()` 认的是「这个仓库有没有适配层」，不是「注册表里有没有」：一个项目把设置指向有适配层、这套部署却没注册的骨架，是轮次开始时要在房间里说出来的一件事，不是一次配置错误。三处都不兜底回默认值——兜底会让一个配错名字的部署安静地跑另一个骨架，而「跑的是哪个」正是只许有一个答法的那件事。

## 注册的是三个里的两个 {#registry}

`HARNESSES`（`harness/__init__.py`）有 `claude-code` 和 `pi` 两条。`codex/` 的适配层也在、也在跑、也有完整的契约夹具和行为声明，只是没注册。

注册表列的是答得出下面四条硬性要求的骨架：答不出的留着代码不注册，能力矩阵里也就不占一列，答出四条的那天回到表里。`deployment_harness()` 因此只放注册了的骨架过去。

## 四条硬性要求 {#subagents}

派一条活是 agent 对骨架**原生 subagent** 的工具调用，平台这一侧没有「派活」的路径（结论 43）。那条路成立的前提是四条，它们不是能力位：

- 起子 agent，并指定它跑哪个模型
- 子 agent 的每个事件带可归到卡的线程标识
- 父线程能改它的指令
- 父线程能停掉它

pi 核心没有子 agent，四条由平台给它的 extension 和 runner 答：`Task` 在同一台机器上起第二个 pi，模型经平台准入，子会话的每条记录带着线程标识写进会话自己的记录，`SendMessage` 与 `TaskStop` 改它、停它（`harness/pi/subagents.py`）。

`SubagentRequirement` 就是这四条。`Harness.__post_init__` 逐条要一个非空的 `str`：**答不全根本造不出来**，判在构造上而不是判在一条守卫测试上——注册表是一个字面量，一个造得出来的条目总会有人写进去。值只能是一句话，而 `Difference` 是 `StrEnum`、填进来照样是个 `str`，所以 `__post_init__` 认的是类型本身：硬性要求没有「暂缺」那一档。`backend/tests/contract/test_subagent_requirements.py` 还核这两件事：引的路径存在，引的符号真的**参与过代码**（被定义、被赋值、被读）。

## 平台只认这几个动词 {#contract}

`AgentRuntime`（Protocol）是平台对一个骨架的全部要求：`ensure`、`send`、`backlog`、`deliver`、`interrupt`、`close`，加上 `holds`、`memory()`、`keeps_memory`、`bind_reachability`、`bind_memory` 这几个事实与回路。四条硬性要求不在这里当第七个动词：平台不起子 agent，它们是骨架的事实，各写一句「怎么做到的」落在 `HARNESSES[harness].subagents` 上。

旁边几个小协议：`Backlog`（`unread` / `assemble` / `unfinished` / `landed` / `forget`）、`SessionControls`、`Opening`（一轮开场给会话的那些东西，含「这一轮要不要一双手」的 `needs_place`）、`SessionRef`（`(topic, agent_handle, harness)`，`agent_sessions` 的键）。

## 能力矩阵：一格都不许空 {#matrix}

每个骨架的适配层带一份 `Declaration`（词汇表在 `backend/app/domain/agent/capability/__init__.py`）：`pinned_version`（引用适配层那一个常量，不写第二遍字面量）、`built_ins`、`how_disabled`（每个概念一格）、`verified_against`（人手写的「对着哪个 build 读出来的」）。矩阵由 `capability/matrix.py` 汇总，**校验就在生成里**：一张画得出来、只是有几格空着的表会被当成一张填过的表读。

一格只有三种可能：一句「怎么关的」（那就是「有」）、`Difference` 里的一条码、或一个带 issue 和到期 pin 的 `Missing`。`Difference` 的名单是封闭的，所以「这一格我说不清」也得选一个已经存在的说法。`Missing` 到期即红：`pinned_version != until_pin` 时 `matrix()` 直接报，逼人按 issue 对着新 build 重核。

四格（提问、待办、提醒、自动同步）今天长这样：

| | Claude Code 2.1.282 | Codex 0.154.0 | pi 0.85.1 |
| --- | --- | --- | --- |
| 提问 | 自己带，两侧都拒（`DISALLOWED_TOOLS` + `settings.json` 的 deny），平台用 `cheese_ask` | 自己带；同步那个关得掉，异步那个这个 build 关不掉（#1880） | 未核 |
| 待办 | 自己带，`TodoWrite`/`Task*` 两侧都拒，平台用 `todo_write` | 自己带，`tools.update_plan.enabled=False`，平台用 `todo_write` | 不自带，清单由平台建 |
| 提醒 | 自己带，Cron/Schedule 参数拒掉，平台用投递记录 | 未核 | 不自带 |
| 自动同步 | 不自带（同步是平台在 Stop 上装的 checkpoint） | 未核 | 未核 |

`declarations()` 只认注册表，注册表里多一个而 `_DECLARED` 里没有就红；`written()` 连不在注册表里的骨架也认——摘掉一个骨架不是把它从树里拿走，它的 pin 和声明仍然归守卫管。

## 驱动层不是第四个骨架 {#driven}

Codex 和 pi 的驱动方式一样：会话机上一个 runner 拥有 agent 进程、说它的协议、把它产出的东西按稳定序号记进本地 journal，并且**每个输入至多接受一次**；后端从一个游标镜像那份 journal，每条会话一个 poller 把镜像到的东西交给房间。只有协议不同，所以只有协议住在 `codex/` 和 `pi/` 里；journal、runner 的 socket 和输入账、drain 循环、poller 都在 `harness/driven/`。它是**共用的一层**，不是第四个骨架。

镜像里的记录只在两小时之内落进房间（`driven/subscription.py` 的 `STALE_S`，按 journal 记下的时间算：Claude Code 和 Codex 用会话机记录的时间，pi 用后端镜像到它的时间）。更老还没落的，只可能是这期间没人在读：后端或机器不在，或者每次 drain 都卡在同一条记录上。那时房间早已不等它了，这一轮已经结束，消息也重发过或告诉过人，所以游标直接越过它，房间不会再收到这些记录。

输入账（`driven/runner.py` 的 `Runner.accept`）是重连安全的那一半：id 是平台的，一个没看到回话的后端重发同一个 id 拿到的是同一个结果，而不是第二轮；同一个 id 配不同的正文当场拒绝——拿第一次的结果回答它会报告一件从没发出去的事。写进会话的一个输入，到的次数因此不由重连次数决定。

## pin 与契约夹具 {#pins}

| 骨架 | pin | 在哪 | 谁管 |
| --- | --- | --- | --- |
| Claude Code | `2.1.282` | `claude_code/device_launch.py` 的 `CLAUDE_PINNED_VERSION` | `backend/scripts/test_harness_contracts.py` |
| Codex | `0.154.0` | `codex/host.py` 的 `VERSION` | 同上；不在注册表也照样被它管着 |
| pi | `0.85.1` | `pi/device_launch.py` 的 `VERSION` | 不走那份脚本：它是按平台分的 tarball（`app/domain/machine/pi_dist.py`），契约由 `backend/tests/fixtures/harness-contract/` 那套夹具核 |

pin 的版本号只写一处：那份脚本从每份 `Declaration.pinned_version` 取，不再自己 `ast` 解文件或抄一个字面量。`.github/workflows/mcp-contract.yml` 管另一个方向的契约：执行器借这台机器的 `claude mcp serve` 做文件读写、从它的 Bash 工具取 shell 快照，两样都没有公开契约，所以 `scripts/remote_execution/mcp_contract.py` 就是契约本身（同处的 `headless_contract.py`、`equivalence.py`、`refresh_contract.py` 各管一段）。

## 骨架能指向什么、一条活用哪个模型 {#models}

`Harness` 的字段：`name`、`label`、`subagents`、`speaks_gateway`、`carries_subscription`。这几个事实写在骨架上而不是模型上——以前是反过来的（每个模型带一张「允许哪些骨架驱动我」的名单），方向错得付出过代价：加一个骨架要改模型目录，拒绝一个组合时报的错还是关于模型的，而模型对这件事什么意见都没有。`speaks_gateway` 说它说不说平台网关自己那套形状（能，就所有模型都能驱动它）；`carries_subscription` 说它能不能承载 Anthropic 订阅凭据——那份凭据只为**一个**骨架铸造。

一条活具体用哪个模型由 `backend/app/domain/room_task/binding.py` 的 `resolve()` 定：显式绑在这条活上的 → 队友的 → 调用方给的默认 → 项目主模型，逐个往下；一个都没有就报「当前项目没有可用的默认模型」。Codex 和 pi 没有 MCP，它们把平台工具交到模型手里的方式见[平台工具与会话侧 MCP](/dev/mcp)。
