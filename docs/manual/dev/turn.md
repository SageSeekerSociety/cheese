---
title: 一条消息怎么变成芝士的一轮
kind: 流程
summary: 从有人在话题里点名 AI 队友，到结果回到房间。
covers:
  - backend/app/domain/agent/chat.py
  - backend/app/domain/agent/runtime.py
  - backend/app/domain/agent/activity.py
  - backend/app/domain/block/waits.py
  - backend/app/domain/agent/compute.py
  - backend/app/domain/machine/session_work.py
  - backend/app/domain/agent/host_failure.py
  - backend/app/domain/agent/dispatch_log.py
  - backend/app/domain/room_task/naming.py
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
    desc: ComputePool 回答这一轮在哪跑：本机沙盒容器、中心会话加远端执行、用户接入的设备，或者云端沙箱。需要落会话时先花最多 15 秒探一下机器还在不在。
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
  - label: 任务命名
    desc: 不在这一轮里做。平台在后台用一个小模型给没名字的任务起名、校准、跟进，标题由谁定记在 tasks.title_source 上。
    link: /dev/turn#naming
```

## 1. 发消息与寻址 {#address}

人和 AI 队友发消息走同一条路由 `POST /topics/{id}/messages`，发送者在这个房间里有没有 AI 席位决定这是哪一种：人说的话由 `ChatService.post_user_message` 在一个短事务里落库，并同时写好点名通知；重发同一条消息时按 `request_id` 去重。房间的 WebSocket 只把落下的东西推出去，不收消息。跑不跑一轮只看寻址结果：`runtime._a_turn_was_addressed` 判断这条消息点到的人里有没有 AI 队友。没点名 AI 队友，房间里人和人的对话不会触发一轮；平台自己的投递也一样，只能点名，不能凭空起一轮。AI 队友发的消息点名另一位队友，同样会叫起它，见[队友之间点名](#seats-mention)。一轮在库里是什么、后台谁推着走，见[会话与轮次](/dev/session)。

## 2. 排队还是插话 {#serialize}

`ChatService.converse` 先把人的消息持久化并广播，再进这位队友在这个话题上的串行锁（座位锁）。所以发消息永远不会被正在跑的一轮挡住：没有在跑的一轮就开一轮新的；已经有一轮在跑就把消息并进正在运行的会话（`merge_into_running_turn`），芝士要先回话（见[芝士怎么说话](#publish)）；在跑的是另一位队友就不等它，在这位自己的座位上并行开一轮。座位是什么、锁按什么加，见下面[同一话题里的几个 AI 队友](#seats)。

## 3. 找到会话、选机器 {#compute}

`ComputePool`（`compute.py`）回答「这一轮在哪跑」，有四种供给：本机沙盒容器、中心会话加远端执行（`central_provider.py`）、设备（`device_provider.py`）、云端沙箱（`cloud_provider.py`）。每种都保持一个可以重连的长会话，而不是一次性子进程。

会话进程和干活的那台机器是两层，见[「会话机」这一层](/dev/overview#session-machine)；四种机器怎么被选中和准备见[设备与机器接入](/dev/machines#kinds)；一个工具调用怎么从会话机落到工作机器上见[执行通道](/dev/execution)。

## 4. 启动或续跑骨架 {#harness}

骨架是 Claude Code、Codex、Pi 三种之一。会话按「对话 × 队友 × 骨架」记在 `agent_sessions` 上（见[会话与轮次](/dev/session#layers)），下一轮续跑同一个会话。一段对话是一个房间，或房间里的一条任务：任务的一轮跑在任务自己的会话里（`converse(topic_id=<任务 id>)`），这一轮写下的块、轮次和用量都记在任务这段对话上（`conversation_id` = 任务 id）。本次跑哪个由部署和项目设置决定，怎么把协议翻译成统一的事件见[骨架](/dev/harness)。会话开场时芝士读到什么、接着跑时怎么补上变化，见[提示词注入与上下文管理](/dev/context)。

## 5. 芝士怎么说话 {#publish}

芝士的普通输出不进房间，要发言必须调用平台工具 `chat_send`。工具调用、施工现场的进度作为活动块记录下来；一轮很久没有发言时，平台会给它投一条内部提醒。平台工具表怎么送到每种骨架手里、机器够不着时哪些工具还在，见[平台工具与会话侧 MCP](/dev/mcp)；只有必须在机器上跑的动作才走 `cheese` 命令行，见[cheese CLI 原理](/dev/cli#sandbox)。

人点名芝士说的话，芝士先在房间里回一句，再做别的：开这一轮的那条消息，和一轮进行中插进来的那条，都一样。从这条消息送进会话起，会话调 `chat_send` 或 `cheese_ask` 之前，除了读这个房间的聊天记录（`cheese_chat_list`、`cheese_chat_get`、`cheese_chat_search`、`cheese_chat_replies`），别的工具一律被拒，拒绝的原因会告诉它先回话。读房间不算回话：拒绝可能先于那条消息送到模型眼前，人的消息也常常只是一个 @、指着上面几条，不让它读，它只能回一句「还没看到你写了什么」。读文件、跑命令、查项目状态都照样被拒，那正是人在等的活。平台自己的通知、巡检、没有点名芝士的消息不算；分身向启动它的会话汇报，不受这条约束。规则写在所有骨架共用的 runner 里（`harness/driven/runner.py`），每种骨架只负责在自己的工具路径上照它拒绝：Claude Code 的函数钩子（`remote_execution/proxy.js`）、Codex 的动态工具（`codex/tools.py`）、pi 的扩展（`pi/platform.ts`）。

一轮也不能在这时候结束：模型不调任何工具、只在自己那边写完就停，会话会被拦下一次，要它先在房间里回话。怎么拦是各骨架自己的：Claude Code 用 Stop 钩子把这一轮接着跑下去；Codex 没有能让一轮继续的东西，于是 runner 先不交出这一轮的结束，在同一件事里再开一轮带着提醒；pi 已经把最后一条写下了，于是 runner 给会话开一轮它自己的（房间照看后台任务唤醒的那种轮次记账）。只拦一次：拦过之后还是不回话，这一轮照常结束，runner 的日志记下这件事。

消息只能在两次工具调用之间送到模型面前，所以人说话时如果芝士正卡在一条长命令上，这条命令会被转到后台继续跑，调用立刻返回，芝士马上读到消息（相当于在终端里按 Ctrl+B）：Claude Code 由 runner 发 `background_tasks` 控制请求，前台的 Bash 和分身一起转；Codex 的 Bash 在执行器上跑，由执行器停止等待、把命令交还成后台任务（`runtime.bash`）；pi 的 bash 由平台扩展接管，转成后台任务后用 `bash_read` / `bash_kill` 读和停；前台的分身（扩展的 `Task`）停止等待，转到后台接着做，结束时 runner 告诉会话（`pi/subagents.py`）。文件读写和 MCP 调用转不了，它们本来就短。

## 6. 失败、超时与发版 {#failure}

- **机器的错**：记在设备上而不是话题上，同一台机器连续两次同类失败会被隔离一段时间（`host_failure.py`）；话题不会被悄悄换到另一台机器，由人决定怎么处理；云端沙箱坏了则直接换新的。见[设备与机器接入](/dev/machines#failure)。
- **结果未知的副作用**：带幂等 id 的执行器调用会先在平台侧记一行（`dispatch_log.py`），机器突然没了之后，重派时能分清「确定没做」和「可能做过」。见[会话与轮次](/dev/session#dispatch)。
- **会话没起来**：按 runner 日志里的记录归类（`platform_failures.classify_session_start`），认不出的原因也只说「原因没能识别」；那次启动打印的原文在现场同一行下面，点开可看全文。
- **额度用完**：准入拒绝，房间里出现平台提示。见[准入与供给](/dev/admission)。
- **发版**：旧的主 API 进程把正在跑的轮交给新进程，见下面「发版时的交接」。

### 发版时的交接 {#resume}

交接时新旧两个进程同时连着同一个数据库：新进程重新监听活过这个进程的会话，补上没人监听那段时间里它们说过的话；开会头的轮被收掉（`resume_orphans`），消息收了却没开跑的轮补上（`resume_lost_messages`）。谁在跑哪些轮、哪些会话归谁监听由一把数据库锁决定，细节见[会话与轮次](/dev/session#handover)和[部署拓扑](/dev/topology#handover)。

## 同一话题里的几个 AI 队友 {#seats}

一个话题可以同时请几个 AI 队友。它们**用房间的同一条算力选择、各有各的会话、可以同时跑**。

```demo-steps
title: 两个队友在同一个话题里并行
note: 机器只有一份，会话和串行都按座位分开
embed: seats
steps:
  - label: 一个话题一个容器
    desc: 房间的算力选择就是房间里每条会话的选择。选自有设备时所有队友落在同一台机器上、每位一份工作目录；选云端时每位队友一个沙箱。换机器是整个房间一起搬。
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

房间只有一条算力选择（`compute_configs.room_choice`），房间自己的会话和没有自己选择的任务的会话要手时都从它解析（`machine/session_work._attempt`）；任务第一次要手时把它抄成自己的一份（`fix_task_choice`），之后各走各的，见[任务](/dev/tasks)。会话行上的 `execution_request.choice` 只是它的副本。选的是「系统挑一台」时，第一条要手的会话挑，后来的会话跟着房间里已经站着的那台（`_roommates_device`），不会一人一台。选的是云端时，每条会话的沙箱由平台的云主机池安排在任意一台有空位的宿主机上，同一房间的会话不一定在同一台（见 `docs/microcloud.md`）。工作目录是队友的：每条会话在它那台机器上按自己这一代（`execution_request.generation`，落在租约的 `resource_id` 上）开一份工作目录和执行器状态（`device_home_dir`），几位队友互不看见对方没推送的改动。

改房间的机器（`PUT /topics/{id}/compute-profile`，人从成员名册改，或芝士用 `cheese_machine` 改）就是整个房间搬：`request_choice` 让房间自己的每条会话（以及还没有自己选择的任务的会话）在离开的那台上做一次尽力而为的 checkpoint（`session_work.checkpoint`，等价 `cheese sync --all`，最多等 `PUSH_WAIT_S`），推没推上去、那台连不连得上都照常搬，全部搬完才写房间那一项、再移钉子；已有自己选择的任务不跟着搬。用任务的 id 改的是那个任务的选择，只搬它自己的会话，也不动房间的钉子。推不上去丢的只是上一轮 Stop checkpoint 之后的改动，那一轮的快照在平台上。checkpoint 前那台上的执行器若还是旧版本，先按新版本重新拉起再做，否则修过的 `cheese sync` 在那台上还是旧的行为。人和芝士换机的规则一样。离开云端沙箱的会话把它在宿主机上的位置还给池子（已经归档进存储的那份归档留给房间清理）；离开整台云虚拟机时那台虚拟机随即删掉。没有按会话单独换机器的接口。

这条规则在 2026-09-28 取代了原来的结论 60（「手是 agent 的，不是房间的」）。

### 会话按座位分开 {#seats-session}

会话记录按（话题, 队友, 骨架）存（`agent_sessions`），每位队友续跑自己的会话。内存里的运行时状态和算力池的归属按座位（话题, 队友）记（`RoomSessions`、`ComputePool._owners`）：`activate` 只停同一座位上换下来的旧骨架，不碰同一房间里别的队友。后端重启后，每个座位的会话都会被接回来（`placed_everywhere`）。

一位队友在房间里和在房间的某个任务里是两个座位：任务的会话是一段独立的对话，座位名是「队友名@任务 id」（`place.seat_key`），房间里的座位名就是队友名。启动脚本（`$HOME/.cheese/launch/`）、runner 的状态目录和下面这些文件都按座位名分。

机器上的文件也照这个分。**属于一个座位的，写进这个座位的目录**（`place.seat_dir`，`$HOME/.cheese/seats/<sha256(座位名) 前 12 位>`）：执行目标 `remote-target.json`、每轮配置 `remote-session/`、系统提示 `cheese-system-prompt.md`、执行凭据 `remote-session/execution.token`、Claude 设置与技能（座位下的 `.claude/`），以及 `remote-execution/` 辅助程序。第二位队友开屏不会改写第一位的 hook 或辅助程序。

留在房间层的是工作目录、环境运行器的状态（`$HOME/.cheese-environment/status.json`）、store 和会话记录（`$HOME/.claude/projects/`）。每个座位的 `.claude/projects` 指向这份记录，续跑、迁机和发布前的忙闲扫描仍能找到原会话。辅助程序按座位更新；一个座位的更新不会覆盖另一位正在使用的文件。

旧屏幕仍读房间层的文件。launch contract 变更后，平台等它空闲再退休，并按座位目录重开（`screen_identity.launch_identity`）；新座位的开屏不改写旧屏幕依赖的文件。

### 串行只在座位内 {#seats-serial}

组装 prompt 用的锁按座位加（`ChatService.live.seat_lock_for`）。开一轮之前先解析这一轮是谁的座位（`_turn_seat_handle`）：明确点名的实例、消息落库时记下的收件人、最早一条待处理消息的收件人，都没有就是房间默认队友。所以：

- 同一位队友：一次只有一轮，后来的消息并进正在跑的那一轮。
- 不同队友：各开各的，同时跑。

### 队友之间点名 {#seats-mention}

点名对人对 AI 是同一句：一条消息 `<@席位>` 点到的每位 AI 队友都起一轮，不管是人发的还是另一位 AI 用 `chat_send` 发的（`delivery/mention.py`）。人发的消息，第一位点到的队友照旧由消息本身唤起；第二位起和 AI 发的点名一样，记成投递账本里的一行（`record_agent`，和定时投递、周期任务同一本账），按「消息 × 席位」去重，重试发布不会叫醒两次。点自己的名、点到人，都不起轮次。

AI 发起的点名有熔断：同一话题一小时最多叫起 `AGENT_MENTIONS_PER_HOUR`（20）轮，超出的那次不叫醒，房间里落一行 `mention_fused` 提示。人点名不受这个限制。被叫醒的队友读到的 prompt 带着原消息，并提醒它只在要对方接着动手时才点名，只回话不点名。

### 没点名的消息归谁 {#seats-claim}

每个在跑的轮次都能在 prompt 里看到房间里还没被处理的消息。但只有「被人点名唤起的轮次」和「房间默认队友的轮次」会给没点名的消息盖上已处理的戳（`meta.consumed_turn`）；其他轮次只认领点名给自己的那几条。否则几个并行轮次会给同一条消息各盖一个戳，谁都没回它，它却被所有人收走了。

芝士在一轮里 `chat_send` 发的话，归到发言者自己在跑的那一轮上（`publish-chat-message` 按作者归因），沉默提醒也按这一轮算。

### 共用一台机器时怎么不打架 {#seats-share}

几位队友的工作目录是分开的，同一份文件不会被两位同时改。共用的是这台机器的算力、端口和同一个远端仓库，靠两条约定：

- **改动落在任务的分支上。** 每个任务有自己的会话、工作树和分支（`cheese worktree <任务 id>`），只由任务自己的会话打开；两个任务并行推的是两个分支，不会互相覆盖。
- **重活先占锁。** 装依赖、跑大型测试、起服务前用 `cheese_lock` 占房间的重资源锁（30 分钟自动过期），占不到就说谁占着，不排队等。

### 在界面上分开看 {#seats-ui}

`turn_started` / `turn_finished` 帧和重连快照都带着队友，对话里在动的头像认的是它。施工现场顶上钉着一排「全部 + 每位队友」的切换，现场再长也不用滚回顶部；「全部」下每一轮的组头写出是哪位队友的。谁此刻在干活由成员动态说（见下一节）。成员名册底下只有一行「本话题运行在：…」，不再每位队友各写一台机器。

### 成员动态：谁在这个房间里忙 {#activity}

房间自己没有「在跑」「卡住了」这种状态。有的是成员在做什么，而人和 AI 队友一样有：人在这个房间的输入框里打字，是 `typing`；队友在这个房间里有一轮在跑，是 `working`。两者都是某一位成员在某一个房间里的事，自动产生，过了就没（`agent/activity.py`，只在 broker 里，不落库）。

- **房间的 socket**：一位成员开始或停下时发一帧 `activity`（`member`、`kind`、`active`、`since`；打字另带 `expires_in`）；连上时有人在忙，先发一帧 `activity_snapshot`。浏览器在输入框内容变了时至多每三秒发一次 `{"type": "typing"}`，清空时发 `{"type": "typing", "active": false}`；是谁由这条 socket 的凭据定，不看帧上写了什么。打字五秒没有新的一下就算停了，这个人的消息落进房间也算停了。
- **干活从哪来**：broker 从轮次帧上认出「这一轮是哪位队友的」（`turn_started` 上的 `agent`），这位队友在这个房间里的第一轮开始时报 `working`，最后一轮结束时报停。同一位队友在别的房间里干活，这个房间听不到。
- **界面**：和 Slack 一样贴在输入框正下方一行小字：「Alice 正在输入…」「Alice 和 Bob 正在输入…」「多人正在输入…」；队友是「Cedar 正在工作… · 此刻那一步 · 用了多久」。现场顶上是同一个组件，只说在干活的队友。
- **侧栏**：`GET /topics` 每一行带 `activity`（和快照同一份条目），侧栏随列表一起刷新，画在干活的队友的小头像。打字不画：列表隔一阵才读一次，打字几秒就过去了。
- **在等谁**：每一行还带 `waits`，房间在等的那几位成员（`block/waits.py`）：它那一轮报错了（`failed`，立刻算）、有人点了它的名还没回（`mention`）、卡停在要它修的地方（`check` / `conflict` / `rejected` / `gate`，等的是最后在这里干活的那位队友，从它最后一次动手算起），或期间机器出了状况。多久算太久由侧栏按当下的钟判，红点画在那位成员的头像上。此刻正在干活的成员不算在等。

## 7. 任务命名 {#naming}

给任务起名不在一轮里做，由平台在后台单独调用一次小模型（`backend/app/domain/room_task/naming.py`，网关上的 `topic_naming_model`，用自己的虚拟 key 和预算）。频道由建它的人起名，平台不碰。触发点和判断：

| 时机 | 触发 | 做什么 |
|---|---|---|
| 起名 | 还叫「新任务」的任务收到第一条有内容的人话（`post_user_message`），或第一轮结束 | 起一个名字 |
| 校准 | 第一轮结束，或人发满 3 条消息；只做一次 | 结合对话和任务文档，判断要不要换 |
| 跟进 | 任务文档改动、递验收卡这类信号，或上次判断后又多了 30 条消息 | 先判断要不要改；同一任务 30 分钟最多一次、每天最多 3 次 |

- 每次都把当前标题交给模型，默认保留；只改了措辞和标点按不改处理。
- 标题由谁定记在 `tasks.title_source`：`placeholder`、`auto`、`human`。人起的名（建任务时填的、之后改的）记为 `human`，此后平台不再自动改。芝士用 `cheese_task` 创建任务时写的标题、任务自己的会话用 `cheese_title` 起的名记为 `auto`，并算作已校准：只在跟进时可能再改。
- 自动改名按 `title_version` 比较后写入：生成期间有人改了名，这次结果作废。
- 每次改名记进 `task_titles`，不在任务里发消息，只推送 `state: topics` 让侧栏刷新。
- 项目设置 `task_naming = manual` 时平台不起名，芝士也不会被要求起名。
- 平台起不了名（没配网关）时，没名字的任务每一轮都会提醒它自己的会话：弄清要做什么后用 `cheese_title` 起名。
