---
title: 一条消息怎么变成芝士的一轮
kind: 流程
summary: 从有人在话题里点名 AI 队友，到结果回到房间。
covers:
  - backend/app/domain/agent/chat.py
  - backend/app/domain/agent/runtime.py
  - backend/app/domain/agent/compute.py
  - backend/app/domain/machine/session_work.py
  - backend/app/domain/agent/host_failure.py
  - backend/app/domain/agent/dispatch_log.py
  - backend/app/domain/topic/naming.py
---

# 一条消息怎么变成芝士的一轮 {#turn}

从有人在话题里点名 AI 队友，到结果回到房间。

> 讲：一轮经过哪几步、每步在路径上做什么。不讲：每一步的内部实现，各节链到那一页；模型请求本身见[模型调用流程](/dev/llm)，机器怎么接进来见[设备与机器接入](/dev/machines)。

下面把这个过程放一遍。每一步下面的链接指到本节对应的那一段。

```demo-steps
title: 一条消息怎么变成芝士的一轮
note: 七步都在这一页里有对应的一节，按顺序走一遍
embed: turn
steps:
  - label: 发消息与寻址
    desc: 消息落库，点名通知和它在同一个短事务里写好。跑不跑一轮只看寻址结果：点到的人里有没有 AI 队友。
    link: /dev/turn#address
  - label: 排队还是插话
    desc: 人的消息先广播出去，再进这位队友的座位锁。它没有在跑的一轮就开一轮，有就把消息送进正在跑的会话；另一位队友在跑不挡它。
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

用户消息由 `ChatService.post_user_message` 在一个短事务里落库，并同时写好点名通知；浏览器重试同一条消息时按 `client_id` 去重。跑不跑一轮只看寻址结果：`runtime._a_turn_was_addressed` 判断这条消息点到的人里有没有 AI 队友。没点名 AI 队友，房间里人和人的对话不会触发一轮；平台自己的投递也一样，只能点名，不能凭空起一轮。一轮在库里是什么、后台谁推着走，见[会话与轮次](/dev/session)。

## 2. 排队还是插话 {#serialize}

`ChatService.converse` 先把人的消息持久化并广播，再进这位队友在这个话题上的串行锁（座位锁）。所以发消息永远不会被正在跑的一轮挡住：没有在跑的一轮就开一轮新的；已经有一轮在跑就把消息并进正在运行的会话（`merge_into_running_turn`）；在跑的是另一位队友就不等它，在这位自己的座位上并行开一轮。座位是什么、锁按什么加，见下面[同一话题里的几个 AI 队友](#seats)。

## 3. 找到会话、选机器 {#compute}

`ComputePool`（`compute.py`）回答「这一轮在哪跑」，有四种供给：本机沙盒容器、中心会话加远端执行（`central_provider.py`）、设备（`device_provider.py`）、云机器（`cloud_provider.py`）。每种都保持一个可以重连的长会话，而不是一次性子进程。

会话进程和干活的那台机器是两层，见[「会话机」这一层](/dev/overview#session-machine)；四种机器怎么被选中和准备见[设备与机器接入](/dev/machines#kinds)；一个工具调用怎么从会话机落到工作机器上见[执行通道](/dev/execution)。

## 4. 启动或续跑骨架 {#harness}

骨架是 Claude Code、Codex、Pi 三种之一。会话 id 存在话题上，下一轮续跑同一个会话。本次跑哪个由部署和项目设置决定，怎么把协议翻译成统一的事件见[骨架](/dev/harness)。按话题所处阶段注入哪一段操作说明，见[技能](/dev/skills#stage)和[提示词注入与上下文管理](/dev/context)。

## 5. 芝士怎么说话 {#publish}

芝士的普通输出不进房间，要发言必须调用平台工具 `chat_send`。工具调用、施工现场的进度作为活动块记录下来；一轮很久没有发言时，平台会给它投一条内部提醒。平台工具表怎么送到每种骨架手里、机器够不着时哪些工具还在，见[平台工具与会话侧 MCP](/dev/mcp)；只有必须在机器上跑的动作才走 `cheese` 命令行，见[cheese CLI 原理](/dev/cli#sandbox)。

## 6. 失败、超时与发版 {#failure}

- **机器的错**：记在设备上而不是话题上，同一台机器连续两次同类失败会被隔离一段时间（`host_failure.py`）；话题不会被悄悄换到另一台机器，由人决定怎么处理。见[设备与机器接入](/dev/machines#failure)。
- **结果未知的副作用**：带幂等 id 的执行器调用会先在平台侧记一行（`dispatch_log.py`），机器突然没了之后，重派时能分清「确定没做」和「可能做过」。见[会话与轮次](/dev/session#dispatch)。
- **会话没起来**：按 runner 日志里的记录归类（`platform_failures.classify_session_start`），认不出的原因也只说「原因没能识别」；那次启动打印的原文在现场同一行下面，点开可看全文。
- **额度用完**：准入拒绝，房间里出现平台提示。见[准入与供给](/dev/admission)。
- **发版**：旧的主 API 进程把正在跑的轮交给新进程，见下面「发版时的交接」。

### 发版时的交接 {#resume}

交接时新旧两个进程同时连着同一个数据库：新进程重新监听活过这个进程的会话，补上没人监听那段时间里它们说过的话；开会头的轮被收掉（`resume_orphans`），消息收了却没开跑的轮补上（`resume_lost_messages`）。谁在跑哪些轮、哪些会话归谁监听由一把数据库锁决定，细节见[会话与轮次](/dev/session#handover)和[部署拓扑](/dev/topology#handover)。

## 同一话题里的几个 AI 队友 {#seats}

一个话题可以同时请几个 AI 队友。它们**共用一个容器、各有各的会话、可以同时跑**。

```demo-steps
title: 两个队友在同一个话题里并行
note: 机器只有一份，会话和串行都按座位分开
embed: seats
steps:
  - label: 一个话题一个容器
    desc: 房间的算力选择就是房间里每条会话的选择。所有队友落在同一台机器上，每位队友在上面有自己的一份工作目录；换机器是整个房间一起搬。
    link: /dev/turn#seats-machine
  - label: 会话按座位分开
    desc: 座位是（话题, 队友）。每位队友续跑自己的会话，运行时和算力池也按座位记，一位开会话不会停掉另一位的。
    link: /dev/turn#seats-session
  - label: 串行只在座位内
    desc: 发给同一位队友的消息并进它正在跑的那一轮；发给另一位的，在另一个座位上并行开一轮。
    link: /dev/turn#seats-serial
  - label: 没点名的消息归谁
    desc: 每个在跑的轮次都看得见，但只有被点名唤起的轮次或房间默认队友的轮次认领它。
    link: /dev/turn#seats-claim
  - label: 共用一台机器时怎么不打架
    desc: 每位队友一份工作目录，改动落在各自任务卡的分支上；装依赖、跑大测试先占房间的重资源锁。
    link: /dev/turn#seats-share
  - label: 在界面上分开看
    desc: 轮次帧带着队友，现场可以按队友筛，组头写是谁的轮次，工作标签列出正在干活的几位。
    link: /dev/turn#seats-ui
```

### 一个话题一个容器 {#seats-machine}

房间只有一条算力选择（`compute_configs.room_choice`），房间里每条会话要手时都从它解析（`machine/session_work._attempt`）；会话行上的 `execution_request.choice` 只是它的副本。选的是「系统挑一台」时，第一条要手的会话挑，后来的会话跟着房间里已经站着的那台（`_roommates_device`），不会一人一台。机器是房间的，工作目录是队友的：每条会话在那台机器上按自己这一代（`execution_request.generation`，落在租约的 `resource_id` 上）开一份工作目录和执行器状态（`device_home_dir`），几位队友互不看见对方没推送的改动。

改房间的机器（`PUT /topics/{id}/compute-profile`，人从成员名册改，或芝士用 `cheese_machine` 改）就是整个房间搬：`request_choice` 让每条会话先在离开的那台上把改动推到分支，全部推上去才写房间那一项、再移钉子；有一条推不上去，整个房间留在原地并说明原因。原来那台连不上时只有人可以选择不推送直接换。没有按会话单独换机器的接口。

这条规则在 2026-09-28 取代了原来的结论 60（「手是 agent 的，不是房间的」）。

### 会话按座位分开 {#seats-session}

会话记录按（话题, 队友, 骨架）存（`agent_sessions`），每位队友续跑自己的会话。内存里的运行时状态和算力池的归属按座位（话题, 队友）记（`DrivenRuntime`、`ComputePool._owners`）：`activate` 只停同一座位上换下来的旧骨架，不碰同一房间里别的队友。后端重启后，每个座位的会话都会被接回来（`placed_everywhere`）。

### 串行只在座位内 {#seats-serial}

组装 prompt 用的锁按座位加（`ChatService._seat_lock_for`）。开一轮之前先解析这一轮是谁的座位（`_turn_seat_handle`）：明确点名的实例、消息落库时记下的收件人、最早一条待处理消息的收件人，都没有就是房间默认队友。所以：

- 同一位队友：一次只有一轮，后来的消息并进正在跑的那一轮。
- 不同队友：各开各的，同时跑。

### 没点名的消息归谁 {#seats-claim}

每个在跑的轮次都能在 prompt 里看到房间里还没被处理的消息。但只有「被人点名唤起的轮次」和「房间默认队友的轮次」会给没点名的消息盖上已处理的戳（`meta.consumed_turn`）；其他轮次只认领点名给自己的那几条。否则几个并行轮次会给同一条消息各盖一个戳，谁都没回它，它却被所有人收走了。

芝士在一轮里 `chat_send` 发的话，归到发言者自己在跑的那一轮上（`publish-chat-message` 按作者归因），沉默提醒也按这一轮算。

### 共用一台机器时怎么不打架 {#seats-share}

几位队友的工作目录是分开的，同一份文件不会被两位同时改。共用的是这台机器的算力、端口和同一个远端仓库，靠两条约定：

- **改动落在任务卡的分支上。** 每张任务卡有自己的工作树和分支（`cheese worktree <任务 id>`），两位队友并行做的是两张卡、推的是两个分支，不会互相覆盖。
- **重活先占锁。** 装依赖、跑大型测试、起服务前用 `cheese_lock` 占房间的重资源锁（30 分钟自动过期），占不到就说谁占着，不排队等。

### 在界面上分开看 {#seats-ui}

`turn_started` / `turn_finished` 帧和重连快照都带着队友。施工现场顶上钉着一排「全部 + 每位队友」的切换，现场再长也不用滚回顶部；「全部」下每一轮的组头写出是哪位队友的。工作面板的标签列出此刻在干活的几位。成员名册底下只有一行「本话题运行在：…」，不再每位队友各写一台机器。

## 7. 话题命名 {#naming}

给话题起名不在一轮里做，由平台在后台单独调用一次小模型（`backend/app/domain/topic/naming.py`，网关上的 `topic_naming_model`，用自己的虚拟 key 和预算）。触发点和判断：

| 时机 | 触发 | 做什么 |
|---|---|---|
| 起名 | 还叫「新话题」的话题收到第一条有内容的人话（`post_user_message`），或第一轮结束 | 起一个名字，不在房间里发提示 |
| 校准 | 第一轮结束，或人发满 3 条消息；只做一次 | 结合对话、实况文档目标段和任务清单，判断要不要换 |
| 跟进 | 实况文档改动、拆出任务、递验收卡这类信号，或上次判断后又多了 30 条消息 | 先判断要不要改；同一话题 30 分钟最多一次、每天最多 3 次 |

- 每次都把当前标题交给模型，默认保留；只改了措辞和标点按不改处理。
- 标题由谁定记在 `topics.title_source`：`placeholder`、`auto`、`human`。人在侧栏改名、让芝士用 `cheese_title` 改名、撤销一次自动改名，都记为 `human`。此后平台不再自动改，也没有把话题交还给自动命名的入口：要换名字只能由人再改一次。
- 自动改名按 `title_version` 比较后写入：生成期间有人改了名，这次结果作废。
- 每次改名记进 `topic_titles`；非首次改名会在房间里发一条带撤销按钮的事件，并推送 `state: topics` 让侧栏刷新。
- 项目设置 `topic_naming = manual` 时平台不起名，主 agent 也不会被要求起名。
- 平台起不了名（没配网关）时，退回旧办法：系统提示词要求主 agent 在第一轮先用 `cheese_title` 起名。
