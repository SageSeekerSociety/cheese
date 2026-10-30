---
title: 一条消息怎么变成芝士的一轮
kind: 流程
summary: 从有人在频道里点名 AI 队友，到结果回到房间。
covers:
  - backend/app/domain/agent/chat.py
  - backend/app/domain/agent/runtime.py
  - backend/app/domain/agent/realtime/activity.py
  - backend/app/domain/block/waits.py
  - backend/app/domain/agent/compute.py
  - backend/app/domain/machine/session_work.py
  - backend/app/domain/agent/host_failure.py
  - backend/app/domain/agent/dispatch_log.py
  - backend/app/domain/room_task/naming.py
---

# 一条消息怎么变成芝士的一轮 {#turn}

从有人在频道里点名 AI 队友，到结果回到房间。

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
    desc: ComputePool 回答这一轮用哪个骨架跑：会话在中心会话机上，工具经同一条执行通道落到会话的机器上。需要落会话时先花最多 15 秒探一下机器还在不在。
    link: /dev/turn#compute
  - label: 启动或续跑骨架
    desc: 骨架是 Claude Code、Codex、Pi 三种之一，会话 id 存在频道上，下一轮续跑同一个会话。同时按频道所处阶段注入那一段操作说明。
    link: /dev/turn#harness
  - label: 芝士怎么说话
    desc: 普通输出不进房间，要发言必须调用 chat_send。工具调用和输出留在会话记录里，施工现场从那里读；太久不发言平台会投一条内部提醒。
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

`ChatService.converse` 先把人的消息持久化并广播，再进这位队友在这个频道上的串行锁（座位锁）。所以发消息永远不会被正在跑的一轮挡住：这位队友的会话在干活（本进程持有的座位、runner 自己说在干活，`ComputePool.working_seat`）就把消息并进那个会话（`merge_into_running_session`），芝士要先回话（见[芝士怎么说话](#publish)）；会话没在干活就开一轮新的；在干活的是另一位队友就不等它，在这位自己的座位上并行开一轮。投递账本里的一行（PR 的检查结果、定时投递、一条消息里第二位起被点名的队友、任务收尾的那份总结、任务的开场指令）也一样：收件的那一席的会话在这条对话里正在干活，就送进那个会话，在下一个工具调用的间隙读到（`ChatService.join_running_session`），会话回执读回来才算送到；平台的投递不让会话把手头的前台工作挪去后台，人点的名照人发消息的规矩挪。只有周期任务的那次执行仍等自己的一轮：它的会话带着规则主人的记忆组装。座位是什么、锁按什么加，见下面[同一频道里的几个 AI 队友](#seats)。

## 3. 找到会话、选机器 {#compute}

`ComputePool`（`compute.py`）回答「这一轮用哪个骨架跑」：每个骨架一个后端，会话都在中心会话机上（`central_provider.py`），工具经同一条执行通道（`device_provider.py`）落到会话的机器上，不管那台是人接入的电脑还是平台的机器。每个后端都保持一个可以重连的长会话，而不是一次性子进程。

会话进程和干活的那台机器是两层，见[「会话机」这一层](/dev/overview#session-machine)；四种机器怎么被选中和准备见[设备与机器接入](/dev/machines#kinds)；一个工具调用怎么从会话机落到工作机器上见[执行通道](/dev/execution)。

## 4. 启动或续跑骨架 {#harness}

骨架是 Claude Code、Codex、Pi 三种之一。会话按「对话 × 队友 × 骨架」记在 `agent_sessions` 上（见[会话与轮次](/dev/session#layers)），下一轮续跑同一个会话。一段对话是一个房间，或房间里的一条任务：任务的一轮跑在任务自己的会话里（`converse(topic_id=<任务 id>)`），这一轮写下的块、轮次和用量都记在任务这段对话上（`conversation_id` = 任务 id）。本次跑哪个由部署和项目设置决定，怎么把协议翻译成统一的事件见[骨架](/dev/harness)。会话开场时芝士读到什么、接着跑时怎么补上变化，见[提示词注入与上下文管理](/dev/context)。

## 5. 芝士怎么说话 {#publish}

芝士的普通输出不进房间，要发言必须调用平台工具 `chat_send`。工具调用和它们的输出留在会话的原始记录里（`session_records`，见[会话记录](/dev/session-records)），施工现场从那里读；一轮很久没有发言时，平台会给它投一条内部提醒。平台工具表怎么送到每种骨架手里、机器够不着时哪些工具还在，见[平台工具与会话侧 MCP](/dev/mcp)；只有必须在机器上跑的动作才走 `cheese` 命令行，见[cheese CLI 原理](/dev/cli#sandbox)。

人点名芝士说的话，芝士先在房间里回一句，再做别的：开这一轮的那条消息，和一轮进行中插进来的那条，都一样。从这条消息送进会话起，会话调 `chat_send` 或 `cheese_ask` 之前，除了读这个房间的聊天记录（`cheese_chat(action=list)`、`cheese_chat(action=get)`、`cheese_chat(action=search)`、`cheese_chat(action=replies)`），别的工具一律被拒，拒绝的原因会告诉它先回话。读房间不算回话：拒绝可能先于那条消息送到模型眼前，人的消息也常常只是一个 @、指着上面几条，不让它读，它只能回一句「还没看到你写了什么」。读文件、跑命令、查项目状态都照样被拒，那正是人在等的活。平台自己的通知、巡检、没有点名芝士的消息不算；分身向启动它的会话汇报，不受这条约束。规则写在所有骨架共用的 runner 里（`harness/driven/runner.py`），每种骨架只负责在自己的工具路径上照它拒绝：Claude Code 的函数钩子（`remote_execution/proxy.js`）、Codex 的动态工具（`codex/tools.py`）、pi 的扩展（`pi/platform.ts`）。

一轮也不能在这时候结束：模型不调任何工具、只在自己那边写完就停，会话会被拦下一次，要它先在房间里回话。怎么拦是各骨架自己的：Claude Code 用 Stop 钩子把这一轮接着跑下去；Codex 没有能让一轮继续的东西，于是 runner 先不交出这一轮的结束，在同一件事里再开一轮带着提醒；pi 已经把最后一条写下了，于是 runner 给会话开一轮它自己的（房间照看后台任务唤醒的那种轮次记账）。只拦一次：拦过之后还是不回话，这一轮照常结束，runner 的日志记下这件事。

回过一句也不等于回完了。先 `chat_send` 一句「收到」、接着干活、最后在自己那边写一段结论就停，人只看到那句「收到」，结论只进了现场，他会一直等下去。所以欠着回话的一轮，最后一次回话之后又调过别的工具，最后一条消息里还有字（去掉空白后非空），也会被拦下一次（`CLOSING_INSIST`）：要么用 `chat_send` 把它发出去，要么什么都不写就停。紧跟在回话后面的文字是在复述刚发的话，不拦；最后一条是空文本，也不拦。回话之后只记了自己的账也不算又干了活：改步骤清单（`todo_write`）、在看板上说这件任务在哪一步（`cheese_task(action=state)`），这两样只记这一轮自己的状态，不会产生人还不知道的结果（`BOOKKEEPING`，挨着 `REPLY_TOOLS`）；跑命令、写文件、改文档都算。每拦一次，runner 的日志记一行 `closing hold: message=… work=… after_reply=…`，写明回话之后调了哪些工具，用来看线上拦的频率、有没有拦出重复消息。每种骨架把会话自己调的每个工具告诉 runner（`called`），并把最后一条消息的字交给 `insist`：Claude Code 用 Stop 钩子输入里的 `last_assistant_message`，Codex 用这一轮最后一个 `agentMessage` 的文字（它之后又有工具调用之类的条目就算空），pi 用 `agent_end` 里最后一条 assistant 消息的文字。两种拦法分开记，同一条消息各拦至多一次，所以一轮至多被拦两次：先因为没回话被拦、回了一句再干活再用文字收尾，还会因为没发的结论再拦一次。Claude Code 的 build 在一轮停在空文本上时会自己追问要一段可见的回复，所以 runner 把回话工具登记成它的「终结工具」（`CLAUDE_CODE_TERMINAL_MCP_TOOLS`），回完话就停才是正常结束。

消息只能在两次工具调用之间送到模型面前，所以人说话时如果芝士正卡在一条长命令上，这条命令会被转到后台继续跑，调用立刻返回，芝士马上读到消息（相当于在终端里按 Ctrl+B）：Claude Code 由 runner 发 `background_tasks` 控制请求，前台的 Bash 和分身一起转；Codex 的 Bash 在执行器上跑，由执行器停止等待、把命令交还成后台任务（`runtime.bash`）；pi 的 bash 由平台扩展接管，转成后台任务后用 `bash_read` / `bash_kill` 读和停；前台的分身（扩展的 `Task`）停止等待，转到后台接着做，结束时 runner 告诉会话（`pi/subagents.py`）。文件读写和 MCP 调用转不了，它们本来就短。

## 6. 失败、超时与发版 {#failure}

- **机器的错**：记在设备上而不是频道上，同一台机器连续两次同类失败会被隔离一段时间（`host_failure.py`）；频道不会被悄悄换到另一台机器，由人决定怎么处理；云端沙箱坏了则直接换新的。见[设备与机器接入](/dev/machines#failure)。
- **结果未知的副作用**：带幂等 id 的执行器调用会先在平台侧记一行（`dispatch_log.py`），机器突然没了之后，重派时能分清「确定没做」和「可能做过」。见[会话与轮次](/dev/session#dispatch)。
- **会话没起来**：按 runner 日志里的记录归类（`platform_failures.classify_session_start`），认不出的原因也只说「原因没能识别」；那次启动打印的原文在现场同一行下面，点开可看全文。
- **额度用完**：准入拒绝，房间里出现平台提示。见[准入与供给](/dev/admission)。
- **发版**：旧的主 API 进程把正在跑的轮交给新进程，见下面「发版时的交接」。

### 发版时的交接 {#resume}

交接时新旧两个进程同时连着同一个数据库：新进程重新监听活过这个进程的会话，补上没人监听那段时间里它们说过的话；开会头的轮被收掉（`resume_orphans`），消息收了却没开跑的轮补上（`resume_lost_messages`）。Claude Code 的 runner 把没确认的事件留在自己那边，新进程从上一任停下的地方接着读；不说这一版协议（`lifecycle.PROTOCOL`）的 runner 不读，直接换成这一版的，它正在跑的那一轮以 `runner_replaced` 结束、随即接着跑（见[会话与轮次](/dev/session#handover)）。谁在跑哪些轮、哪些会话归谁监听由一把数据库锁决定，细节见[会话与轮次](/dev/session#handover)和[部署拓扑](/dev/topology#handover)。

## 同一频道里的几个 AI 队友 {#seats}

一个频道可以同时请几个 AI 队友。它们**各有各的执行环境、各有各的会话、可以同时跑**。

```demo-steps
title: 两个队友在同一个频道里并行
note: 执行环境、会话和串行都按座位分开
embed: seats
steps:
  - label: 每位队友一个执行环境
    desc: 每位队友在这个频道里有自己的执行环境：某种规格的沙箱，或者一台人自己接入的电脑。房间只存新队友的默认值；换环境只换这一位。
    link: /dev/turn#seats-machine
  - label: 会话按座位分开
    desc: 座位是（频道, 队友）。每位队友续跑自己的会话，运行时和算力池也按座位记，一位开会话不会停掉另一位的。
    link: /dev/turn#seats-session
  - label: 串行只在座位内
    desc: 发给同一位队友的消息并进它正在跑的那一轮；发给另一位的，在另一个座位上并行开一轮。
    link: /dev/turn#seats-serial
  - label: 每个座位读自己的
    desc: 每位队友各记一个「读到哪」，读它没读过的所有参与者消息，不管说给谁。
    link: /dev/turn#seats-claim
  - label: 几位队友同时干活时怎么不打架
    desc: 每位队友一份工作目录，改动落在各自任务卡的分支上。
    link: /dev/turn#seats-share
  - label: 在界面上分开看
    desc: 轮次帧带着队友，现场可以按队友筛，组头写是谁的轮次，工作标签列出正在干活的几位。
    link: /dev/turn#seats-ui
```

### 每位队友一个执行环境 {#seats-machine}

执行环境按 `(对话, 队友)` 记在 `agent_environments` 上（`agent.environment`），不跟会话行，所以换骨架开出的新会话行还是同一个环境。它是两种之一：某种规格的沙箱（`{"kind": "sandbox", "spec": ...}`），或者指定一台团队设备、不进沙箱（`{"kind": "self_hosted", "device_id": ...}`）。规格之间是平级的：`standard` 和 `vm` 是平台的机器，`team_devices` 是项目所在团队接入的设备。沙箱从不点名机器，放在哪台由规格定（`machine/environments.py` 的 `device_for_spec`，平台的规格交给 `HostPool.place`）；指定整台设备只有设备所在团队的管理员能选，AI 队友不能。

一位队友第一次要手时（`machine/session_work._attempt`），平台按 任务的默认 → 房间的默认（`topics.default_environment`）→ 项目的默认 → 标准沙箱 取一份写成它自己的（`environments.environment_of`），之后默认再改也不动它。工作目录是队友的：每条会话在它那台机器上按自己这一代（`execution_request.generation`，落在租约的 `resource_id` 上）开一份工作目录和执行器状态（`device_home_dir`），几位队友互不看见对方没推送的改动。

换一位队友的环境（`PUT /topics/{id}/sessions/{session}/environment`，人从成员名册改，或芝士用 `cheese_session(action=environment)` 改自己的）只搬这一位：`switch_environment` 让它在离开的那台上做一次尽力而为的 checkpoint（`session_work.checkpoint`，等价 `cheese sync --all`，最多等 `PUSH_WAIT_S`），推没推上去、那台连不连得上都照常换。推不上去丢的只是上一轮 Stop checkpoint 之后的改动，那一轮的快照在平台上。checkpoint 前那台上的执行器若还是旧版本，先按新版本重新拉起再做，否则修过的 `cheese sync` 在那台上还是旧的行为。离开平台机器上的沙箱时，它在宿主机上的位置还给池子；离开虚拟机沙箱时那台虚拟机随即删掉。改房间或任务的默认（`PUT /topics/{id}/environment-default`）不搬任何人。

### 会话按座位分开 {#seats-session}

会话记录按（频道, 队友, 骨架）存（`agent_sessions`），每位队友续跑自己的会话。内存里的运行时状态和算力池的归属按座位（频道, 队友）记（`RoomSessions`、`ComputePool._owners`）：`activate` 只停同一座位上换下来的旧骨架，不碰同一房间里别的队友。后端重启后，每个座位的会话都会被接回来（`placed_everywhere`）。

一位队友在房间里和在房间的某个任务里是两个座位：任务的会话是一段独立的对话，座位名是「队友名@任务 id」（`place.seat_key`），房间里的座位名就是队友名。启动脚本（`$HOME/.cheese/launch/`）、runner 的状态目录和下面这些文件都按座位名分。

机器上的文件也照这个分。**属于一个座位的，写进这个座位的目录**（`place.seat_dir`，`$HOME/.cheese/seats/<sha256(座位名) 前 12 位>`）：执行目标 `remote-target.json`、每轮配置 `remote-session/`、系统提示 `cheese-system-prompt.md`、执行凭据 `remote-session/execution.token`、模型隧道 helper 的凭据、端口、pid 和日志（`cheese-tunnel.token`/`.port`/`.pid`/`.log`）、Claude 设置与技能（座位下的 `.claude/`），以及 `remote-execution/` 辅助程序。第二位队友开屏不会改写第一位的 hook 或辅助程序。

留在房间层的是工作目录、环境运行器的状态（`$HOME/.cheese-environment/status.json`）、store 和会话记录（`$HOME/.claude/projects/`）。每个座位的 `.claude/projects` 指向这份记录，续跑、迁机和发布前的忙闲扫描仍能找到原会话。辅助程序按座位更新；一个座位的更新不会覆盖另一位正在使用的文件。

旧屏幕仍读房间层的文件。launch contract 变更后，平台等它空闲再退休，并按座位目录重开（`screen_identity.launch_identity`）；新座位的开屏不改写旧屏幕依赖的文件。

### 串行只在座位内 {#seats-serial}

组装 prompt 用的锁按座位加（`ChatService.live.seat_lock_for`）。开一轮之前先解析这一轮是谁的座位（`_turn_seat_handle`）：明确点名的实例、消息落库时记下的收件人、最早一条待处理消息的收件人，都没有就是房间默认队友。所以：

- 同一位队友：一次只有一轮，后来的消息并进正在跑的那一轮。
- 不同队友：各开各的，同时跑。

这个串行是**跨进程**的：座位和房间的准入在 Valkey 上拿一条租约（`admission.Lease`，座位是 `admission:seat:<频道>:<队友>`，房间是 `admission:room:<频道>`）。进程内那把 `asyncio.Lock` 看不见进程边界，消息落到没持有座位的进程会拿到准入、谎报投递失败；租约不会。Valkey 不可用时退回进程内锁，行为和从前一样。

两半是**一起拿、一起放**的：进程内的 `asyncio.Lock` 先拿，Valkey 的那条钥匙在它下面拿，`async with` 结束时同时放。`asyncio.Lock` 没有删掉——删掉会退两步：进程内等的人不再按先来后到（Valkey 的 `SET NX PX` 不排队），而且一次够得着 Valkey 的占用会和一次退回进程内的占用同时进行，等于一个座位两个回合。够不着 Valkey 的那一半只丢跨进程语义，不影响进程内。

租约和 `asyncio.Lock` 有三处不同，都写在 `admission.Lease` 的注释里：

- **可重入靠 `seat_admission` 的 `ContextVar`**：同一个任务嵌套取同一个座位不自锁；租约本身取第二次会等自己。
- **跨进程没有先来后到**：Valkey 那条钥匙是 `SET NX PX`，等的人每 `POLL_S` 重试一次，谁先抢到谁上。进程内还是先来后到——下面那把 `asyncio.Lock` 按等待最久的叫醒，和从前一样；要连跨进程也排队得换 `admission.Pool`。
- **持有者进程被 `kill -9` 后座位要等 `LEASE_S`（30 s）**：正常放手和任务被取消都是立刻放，差别只在这条崩溃路径上。

### 队友之间点名 {#seats-mention}

点名对人对 AI 是同一句：一条消息 `<@席位>` 点到的每位 AI 队友都会收到，不管是人发的还是另一位 AI 用 `chat_send` 发的（`delivery/mention.py`）：空着就起一轮，正有一轮在跑就送进那一轮。人发的消息，第一位点到的队友照旧由消息本身唤起；第二位起和 AI 发的点名一样，记成投递账本里的一行（`record_agent`，和定时投递、周期任务同一本账），按「消息 × 席位」去重，重试发布不会叫醒两次。点自己的名、点到人，都不起轮次。

AI 发起的点名有熔断：同一频道一小时最多叫起 `AGENT_MENTIONS_PER_HOUR`（20）轮，超出的那次不叫醒，房间里落一行 `mention_fused` 提示。人点名不受这个限制。被叫醒的队友读到的 prompt 带着原消息，并提醒它只在要对方接着动手时才点名，只回话不点名。

### 每个座位读自己的 {#seats-claim}

每位队友在每段对话里有一个读取游标（`agent_reads`，键是对话 × 队友座位）。一轮把它还没读过的所有参与者消息交给它：人和队友说的都算，说给谁也算——别的队友说过的话，对它就是「别人说的」，和人在房间里看到的一样。只有自己上一轮的产出不给它，那些本来就在它自己的对话里。

`meta.consumed_turn` 仍然是「某一轮读过这条」的痕迹，等待判定、编辑提醒和平台通知用它；它不再决定下一轮读什么，也不再把一条消息从别的座位那里收走。

沉默提醒按座位算（`turn/steps/reminders.py`）：这一席的会话在干活、为手上这段工作读进去过一句人话，从读到那句话或它之后最近一次署名发言起又沉默了一个阈值（`chat_progress_reminder_after_s`，十分钟），就叫它一声，沉默不断就每过一个阈值再叫一声；别的队友说话不算它说了。那句人话还没回时，叫它回话；回过了，叫它发一句短话，具体说正在做什么、接下来做什么，不 @ 人。一次只投一句。

### 几位队友同时干活时怎么不打架 {#seats-share}

几位队友的工作目录是分开的，同一份文件不会被两位同时改。共用的是同一个远端仓库，所以**改动落在任务的分支上**：每个任务有自己的会话、工作树和分支（`cheese worktree <任务 id>`），只由任务自己的会话打开；两个任务并行推的是两个分支，不会互相覆盖。

### 在界面上分开看 {#seats-ui}

`turn_started` / `turn_finished` 帧和重连快照都带着队友，对话里在动的头像认的是它。施工现场顶上钉着一排「全部 + 每位队友」的切换，现场再长也不用滚回顶部；「全部」下每一轮的组头写出是哪位队友的。谁此刻在干活由成员动态说（见下一节）。成员名册底下只有一行「本频道运行在：…」，不再每位队友各写一台机器。

### 成员动态：谁在这个房间里忙 {#activity}

房间自己没有「在跑」「卡住了」这种状态。有的是成员在做什么，而人和 AI 队友一样有：人在这个房间的输入框里打字，是 `typing`；队友在这个房间里有一轮在跑，是 `working`。两者都是某一位成员在某一个房间里的事，自动产生，过了就没（`agent/realtime/activity.py`，只在 broker 里，不落库）。

- **房间的 socket**：一位成员开始或停下时发一帧 `activity`（`member`、`kind`、`active`、`since`；打字另带 `expires_in`）；连上时有人在忙，先发一帧 `activity_snapshot`。浏览器在输入框内容变了时至多每三秒发一次 `{"type": "typing"}`，清空时发 `{"type": "typing", "active": false}`；是谁由这条 socket 的凭据定，不看帧上写了什么。打字五秒没有新的一下就算停了，这个人的消息落进房间也算停了。
- **干活从哪来**：broker 从轮次帧上认出「这一轮是哪位队友的」（`turn_started` 上的 `agent`），这位队友在这个房间里的第一轮开始时报 `working`，最后一轮结束时报停。同一位队友在别的房间里干活，这个房间听不到。
- **界面**：和 Slack 一样贴在输入框正下方一行小字：「Alice 正在输入…」「Alice 和 Bob 正在输入…」「多人正在输入…」；队友是「Cedar 正在工作… · 此刻那一步 · 用了多久」。现场顶上是同一个组件，只说在干活的队友。
- **侧栏**：`GET /topics` 每一行带 `activity`（和快照同一份条目），侧栏随列表一起刷新，画在干活的队友的小头像。打字不画：列表隔一阵才读一次，打字几秒就过去了。
- **在等谁**：每一行还带 `waits`，房间在等的那几位成员（`block/waits.py`）：它那一轮报错了（`failed`，立刻算）、有人点了它的名还没回（`mention`）、卡停在要它修的地方（`check` / `conflict` / `rejected` / `gate`，等的是最后在这里干活的那位队友，从它最后一次动手算起），或期间机器出了状况。多久算太久由侧栏按当下的钟判，红点画在那位成员的头像上。此刻正在干活的成员不算在等。

## 7. 任务命名 {#naming}

给任务起名不在一轮里做，由平台在后台单独调用一次小模型（`backend/app/domain/room_task/naming.py`，网关上的 `topic_naming_model`，用自己的虚拟 key 和预算）。频道由建它的人起名，平台不碰。触发点和判断：

| 时机 | 触发 | 做什么 |
|---|---|---|
| 起名 | 还叫「新任务」的任务收到人话、芝士发布文字说明，或第一轮结束 | 有足够文字说明时起一个名字 |
| 校准 | 第一轮结束，或人发满 3 条消息；只做一次 | 结合对话和任务文档，判断要不要换 |
| 跟进 | 任务文档改动、递验收卡这类信号，或上次判断后又多了 30 条消息 | 先判断要不要改；同一任务 30 分钟最多一次、每天最多 3 次 |

- 每次都把当前标题交给模型，默认保留；只改了措辞和标点按不改处理。
- 起名材料包含未读附件标记，不把文件名或路径当成附件内容。有附件、尚无后续文字解释的任务先保持「新任务」，等芝士说明、后续人话或实况文档补充后再尝试。转为任务时原消息同一轮的附件也一并保留。附件不算人发的独立消息，不会提前触发校准。
- 首次命名也允许 `keep=true`：文字只是问候、称呼、指向未读附件，无法判断主题时，暂不起名，后续触发仍可继续首次命名。不是拿账号表过滤生成结果。
- 新名字宽度超过侧栏一行（`title_width.py` 的 `TITLE_WIDTH`，12 个汉字宽）时，让模型重起，最多再问 2 次，几次加起来不超过 `topic_naming_timeout_seconds`。重问失败、答 keep 或始终太宽，就保留旧名；未起名的任务仍叫「新任务」。不按长度裁剪。校准和跟进时模型选择保留的旧名太宽，也可重问，但没有合格替代时仍保留原文。
- 标题由谁定记在 `tasks.title_source`：`placeholder`、`auto`、`human`。人起的名（建任务时填的、之后改的）记为 `human`，此后平台不再自动改。芝士用 `cheese_task(action=open)` 创建任务时写的标题、任务自己的会话用 `cheese_room(action=rename)` 起的名记为 `auto`，并算作已校准：只在跟进时可能再改。
- 自动改名按 `title_version` 比较后写入：生成期间有人改了名，这次结果作废。
- 每次改名记进 `task_titles`，不在任务里发消息，只推送 `state: topics` 让侧栏刷新。
- 项目设置 `task_naming = manual` 时平台不起名，芝士也不会被要求起名。
- 平台起不了名（没配网关）时，没名字的任务每一轮都会提醒它自己的会话：弄清要做什么后用 `cheese_room(action=rename)` 起名。
