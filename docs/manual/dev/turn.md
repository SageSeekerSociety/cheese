---
title: 一条消息怎么变成芝士的一轮
kind: 流程
summary: 从有人在话题里点名 AI 队友，到结果回到房间。
covers:
  - backend/app/domain/agent/chat.py
  - backend/app/domain/agent/runtime.py
  - backend/app/domain/agent/compute.py
  - backend/app/domain/agent/harness/
  - backend/app/domain/agent/host_failure.py
  - backend/app/domain/agent/dispatch_log.py
  - backend/app/domain/topic/naming.py
---

# 一条消息怎么变成芝士的一轮 {#turn}

从有人在话题里点名 AI 队友，到结果回到房间。

> 讲：一轮的生命周期和各环节的模块。不讲：模型请求本身，见[模型调用流程](/dev/llm)；机器怎么接进来，见[设备与机器接入](/dev/machines)。

下面把这个过程放一遍。每一步下面的链接指到本节对应的那一段。

```demo-steps
title: 一条消息怎么变成芝士的一轮
note: 七步都在这一页里有对应的一节，按顺序走一遍
steps:
  - label: 发消息与寻址
    desc: 消息落库，点名通知和它在同一个短事务里写好。跑不跑一轮只看寻址结果：点到的人里有没有 AI 队友。
    link: /dev/turn#address
  - label: 排队还是插话
    desc: 人的消息先广播出去，再进这个话题的串行锁。没有在跑的一轮就开一轮，有就把它送进正在跑的会话。
    link: /dev/turn#serialize
  - label: 找到会话、选机器
    desc: ComputePool 回答这一轮在哪跑：本机沙盒容器、中心会话加远端执行、用户接入的设备，或者云机器。需要落会话时先花最多 15 秒探一下机器还在不在。
    link: /dev/turn#compute
  - label: 启动或续跑骨架
    desc: 骨架是 Claude Code、Codex、Pi 三种之一，会话 id 存在话题上，下一轮续跑同一个会话。同时按话题所处阶段注入那一段操作说明。
    link: /dev/turn#harness
  - label: 芝士怎么说话
    desc: 普通输出不进房间，要发言必须调用 chat_send。工具调用和施工现场的进度记成活动块，太久不发言平台会投一条内部提醒。
    link: /dev/turn#publish
  - label: 失败、超时与发版
    desc: 机器的错记在设备上、由人决定怎么处理；带幂等 id 的副作用先记一行，重派时分得清做没做过；发版时旧进程把在跑的轮交给新进程。
    link: /dev/turn#failure
  - label: 话题命名
    desc: 不在这一轮里做。平台在后台用一个小模型起名、校准、跟进，标题由谁定记在 topics.title_source 上。
    link: /dev/turn#naming
```

## 1. 发消息与寻址 {#address}

用户消息由 `ChatService.post_user_message` 在一个短事务里落库，并同时写好点名通知（`backend/app/domain/agent/chat.py`）。浏览器重试同一条消息时按 `client_id` 去重，不会发两遍。

跑不跑一轮只看寻址结果：`runtime._a_turn_was_addressed` 判断这条消息点到的人里，有没有谁是「靠一轮收到」的，也就是 AI 队友。没点名 AI 队友，房间里人和人的对话不会触发一轮。平台自己的投递也一样，只能点名，不能凭空起一轮。

## 2. 排队还是插话 {#serialize}

`ChatService.converse` 先把人的消息持久化并广播，再进同一话题的串行锁。所以发消息永远不会被正在跑的一轮挡住。

- 这个话题没有在跑的一轮：开一轮新的。
- 已经有一轮在跑：`merge_into_running_turn` 把新消息直接送进正在运行的会话。芝士在下一步之前读到它，读到的格式和开场时的消息一样。

## 3. 找到会话、选机器 {#compute}

`ComputePool`（`compute.py`）回答「这一轮在哪跑」，有几种供给：

- **本机沙盒容器**：主 API 通过 docker.sock 起的兄弟容器。
- **中心会话 + 远端执行**（`central_provider.py`）：会话进程在中心主机上，文件和命令经执行器落到租用的机器上。需要把会话重新落到那台机器上时（没有会话在跑，或该重启了），开跑前最多花 15 秒（`MACHINE_PROBE_TIMEOUT_S`）问一下租用的机器还在不在。
- **设备**（`device_provider.py`）：用户接入的电脑，会话直接开在那台机器的「屏幕」里。
- **云机器**（`cloud_provider.py`）：每个话题一台 MicroCloud 机器。

每种供给都保持一个可以重连的长会话，而不是一次性子进程。

## 4. 启动或续跑骨架 {#harness}

骨架有三种：Claude Code、Codex、Pi（`backend/app/domain/agent/harness/`）。每种骨架在自己的目录下把协议翻译成统一的事件，由 `backend/app/domain/agent/service.py` 接住。会话 id 存在话题上，下一轮续跑同一个会话。

注入给芝士的操作说明按话题当前所处的阶段决定（`stages.py` 的 `TopicStage`）：任务执行、闸门、等采纳、合并冲突、已归档，五个阶段各注入各的那一段。「拆活」和「干活」是同一段：分开注入会让房间在派活时读不到该怎么交付。

## 5. 芝士怎么说话 {#publish}

芝士的普通输出不进房间。要发言必须调用平台工具 `chat_send`；平台的其他动作（任务卡、验收、记忆等）也是会话侧的 MCP 工具，只有必须在机器上跑的动作才走 `cheese` 命令行，见 [cheese CLI 原理](/dev/cli)。

工具调用、施工现场的进度作为活动块记录下来。一轮很久没有发言时，平台会给它投一条内部提醒（`remind_silent_turns`）。

## 6. 失败、超时与发版 {#failure}

- **机器的错**：记在设备上而不是话题上。同一台机器连续两次同类失败会被隔离一段时间；话题不会被悄悄换到另一台机器，由人决定怎么处理（`host_failure.py`）。
- **结果未知的副作用**：带幂等 id 的执行器调用会先在平台侧记一行（`dispatch_log.py`），机器突然没了之后，重派时能分清「确定没做」和「可能做过」。
- **会话没起来**：会话（Claude Code、pi 或 Codex）在启动时就退出了（拿不到工作电脑、缺程序、执行容器建不起来、另一个会话进程还占着目录等），房间里只有平台的一句话，按 runner 日志里的记录归类（`platform_failures.classify_session_start`），认不出的原因也只说「原因没能识别」；那次启动打印的原文在现场同一行下面，点开可看全文。
- **额度用完**：准入拒绝，房间里出现平台提示。
- **发版**：旧的主 API 进程把正在跑的轮交给新进程；新进程用 `recover_sessions` 重新监听这些会话，并补上没人监听那段时间里它们说过的话。

## 7. 话题命名 {#naming}

给话题起名不在一轮里做，由平台在后台单独调用一次小模型（`backend/app/domain/topic/naming.py`，网关上的 `topic_naming_model`，用自己的虚拟 key 和预算）。触发点和判断：

| 时机 | 触发 | 做什么 |
|---|---|---|
| 起名 | 还叫「新话题」的话题收到第一条有内容的人话（`post_user_message`），或第一轮结束 | 起一个名字，不在房间里发提示 |
| 校准 | 第一轮结束，或人发满 3 条消息；只做一次 | 结合对话、实况文档目标段和任务清单，判断要不要换 |
| 跟进 | 实况文档改动、拆出任务、递验收卡这类信号，或上次判断后又多了 30 条消息 | 先判断要不要改；同一话题 30 分钟最多一次、每天最多 3 次 |

- 每次都把当前标题交给模型，默认保留；只改了措辞和标点按不改处理。
- 标题由谁定记在 `topics.title_source`：`placeholder`、`auto`、`human`。人在侧栏改名、让芝士用 `cheese_title` 改名、确认「智能重命名」的建议、撤销一次自动改名，都记为 `human`，平台之后不再自动改。
- 自动改名按 `title_version` 比较后写入：生成期间有人改了名，这次结果作废。
- 每次改名记进 `topic_titles`；非首次改名会在房间里发一条带撤销按钮的事件，并推送 `state: topics` 让侧栏刷新。
- 项目设置 `topic_naming = manual` 时平台不起名，主 agent 也不会被要求起名。
- 平台起不了名（没配网关）时，退回旧办法：系统提示词要求主 agent 在第一轮先用 `cheese_title` 起名。
