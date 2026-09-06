# 分身三问核实

## 目标

<@wangchangxin> 发来一张表，列了三条关于「分身」（Claude Code 后台 subagent）的待核结论，涉及平台要不要按 agent_id 归卡、插话机制、跨轮次收尸。逐条实测核实，给出证据。

## 结论总览（2026-09-06 实测，claude 2.1.224，本平台沙箱内）

| 待核的 | 图中结论 | 核实结果 |
|---|---|---|
| ① 分身的进度能不能被平台看见 | 能，hooks 带 agent_id | **成立**，字段名分毫不差 |
| ② 跑着的分身能不能被中途插话 | 能（2.1.198 起），TaskStop 能单停 | **成立**，本机 2.1.224 实测通过 |
| ③ 父轮次结束后后台分身会不会死 | 不会，完成时通知父 agent 跑一轮 | **成立**，跨轮次实测通过 |

## ① hooks 在分身里照样触发，事件带 agent_id / agent_type —— 成立

实验：嵌套起一个 headless claude（挂了把每条 hook 事件 stdin 追加到日志的 settings），让它派一个 general-purpose 分身跑 `echo`。收到的事件序列：

- 分身内的 `PreToolUse` / `PostToolUse` 每条都带 `agent_id`（如 `a8a5aea...`）和 `agent_type: "general-purpose"`；**父线程自己的事件没有这两个字段**——和图里「主线程没有这个字段」的说法一致，可以直接拿「有无 agent_id」区分归卡还是归房间。
- `SubagentStart` 存在，带 `agent_id`、`agent_type`。
- `SubagentStop` 存在，带 `last_assistant_message`（实测值就是分身的收尾话"DONE"），另外还带 `agent_transcript_path`（分身完整对话的 JSONL 路径，图里没提，做卡片详情时可能用得上）。

## ② SendMessage 能插话运行中的分身，TaskStop 能单停 —— 成立

实验：在本会话里同时起两个后台分身，各自做 15~20 轮 `sleep` 循环。

- 对第一个中途 `SendMessage`（内容：立刻写文件并提前收工）：消息在它下一个工具间隙送达，它第 1 轮后就照做写出了 `INTERRUPTED-AT-ITERATION-1` 的佐证文件、提前结束并回了 `MESSAGE-RECEIVED`。**不用停掉重派，「回复归房间芝士 → 芝士插话给分身」的链路成立。**
- 对第二个 `TaskStop`：单独停掉，第一个不受任何影响。

## ③ 父轮次结束后后台分身不会死 —— 成立

实验：放出一个前台睡满 3 分钟的后台分身，父轮次随即主动收尾结束。结果：分身跨过轮次边界继续跑（总时长约 4 分钟），跑完写出了带时间戳的佐证文件（`2026-09-06T00:36:25 SURVIVED-ACROSS-TURNS`），**其完成通知直接把父会话唤起了新一轮**——图里「不会死、完成时父 agent 收到通知据此跑一轮」两个半句都实测成立。

两个补充事实，对平台设计有用：

- 完成通知在会话里是一条**系统消息**（明确标注 NOT USER INPUT），不是平台送的提示词——对应图里「平台要认一种新的轮次」那条。这种轮次在本平台沙箱里**今天就已经在发生**（本次实验就是在 cheese 的轮次机制下跑通的），说明轮次区间那套（开/关/收尸）目前至少没把它掐死，但要不要给它单独的轮次语义仍是设计问题。
- 一个容易踩的坑：分身自己把长命令放进**它的**后台再停下等通知，会被判定为"完成"并触发一次 task-notification（同一个分身可以多次触发完成通知，续跑后再完成又是一条）。也就是说「完成通知」≠「活干完了」，平台归卡/收尸逻辑不能把第一条完成通知当成终态。

## ④ 版本号核实

- 本机（这台 dev 机的沙箱）跑的是 **2.1.224**。
- changelog 里 **2.1.198** 的原文是："Subagents now run in the background by default, so Claude keeps working while they run and is notified when they finish"，同版本还有 "Subagents now treat messages from the agent that launched them as normal task direction"。图里「2.1.198 起」的说法有据。
- **2.1.261 是当前 changelog 最新版**，#708 钉它「够」成立（≥2.1.198）。而且 2.1.224 → 2.1.261 之间有几条对这套玩法很相关的修复，钉新不钉旧是对的：
  - 2.1.246：分身到 maxTurns 停下时结果标为 partial，提示可用 SendMessage 续跑；
  - 2.1.251：分身来的消息被明确框定为"本会话内的 worker"，不会被当成无关会话；
  - 2.1.257：网络断流/休眠导致的响应中断，分身自动续跑而不是带着残缺结果结束；停掉后台分身时会连带清掉它的 monitor；
  - 2.1.260：修了"A 分身用 SendMessage 续跑 B，B 完成时 A 永远收不到唤醒"的 bug。

## 设计评估（更新版）：话题=单独沙箱，任务=分身

<@wangchangxin> 提的最终形态：**隔离的粒度放在话题（一话题一沙箱），并行的粒度放在任务（一任务一分身）**。评估：赞成。

- 这等于把现有「支线」机制"半个话题"的中间态整个删掉：没有独立 token（403 那批权限配错消失）、没有独立工作区（被拆丢提交、每支线重下依赖占重活道消失）、没有分支要合（共用分支互相覆盖消失）、conclude/tell 自制通道换成原生消息。「只有主话题才能递卡」这条一直没实现的规则在此结构下自动成立。
- 三个要提前设计的点：
  1. **agent_id ↔ 卡的绑定**：hooks 带 agent_id，但平台得知道它属于哪张卡。可靠做法是房间建卡后把 agent_id 报给平台绑定（cheese CLI 加一个绑定动作）——唯一非做不可的新接口。
  2. **进程死亡的表现（最大风险）**：分身跨轮次可以、跨不过进程重启。`SubagentStop` 永远不来的卡要标「失联」，不能永远转圈。反面是优点：一沙箱一爆炸半径，杀屏幕即收尸干净。
  3. **同树并行写**：默认不隔离 + `--paths` 纪律，大改动才开 worktree 隔离开关（隔离引回合并成本）。
- 实测提醒：**完成通知≠终态**，同一分身可多次报完成，卡状态机别拿第一条当结束。

结论：投赞成票，先把「失联」状态设计出来再动手最稳。

### 初版评估（保留背景）

<@wangchangxin> 问：所以就把任务改成 subagent？评估结论：**把「支线/split」这层执行机制换成分身，方向成立；房间/话题这层保留，按活的大小分流。**

分身解掉的旧痛点：简报单向改不了（→可插话）；支线沙箱搭建贵且坑多——重 clone、重装依赖、共用分支互相覆盖、uv 缓存按话题重复（→分身直接用房间现成工作区）；过程黑盒（→hooks 按 agent_id 归卡）。

分身给不了的三样，决定不能全替：

1. **持久性**：分身活在房间进程里，跨轮次可以，进程/机器重启就没了。隔夜、多天的长活仍需话题（平台级对象、可恢复、可单独召唤）。
2. **隔离**：默认共用工作树，并行改同一片文件会打架；worktree 隔离选项救得了打架，但又引回合并成本。
3. **人够不着**：人不能直接 @ 分身，插话必须房间芝士转达（单点）；验收单位从「分支+PR」变成「卡」。

改造清单（图中两条 + 实测补一条）：hook 接收端按 agent_id 归卡；轮次机制认「通知唤起的轮次」；**完成通知≠终态**（同一分身可多次报完成，收尸别拿第一条当结束）；#708 钉 2.1.261。

## 实施方案（<@wangchangxin> 已拍板「去做」，2026-09-06）

### 现状摸底结论（两轮代码勘察，关键事实）

- 「任务」已经是房间里的 `tasks` 行（`backend/app/domain/room_task/models.py:258`），不是话题；但**执行层每个任务仍起一整套**：独立 tmux 屏幕、独立 claude 进程、独立 $HOME、独立 git clone（共享房间机器和树分支）。launch 路径在 `cloud_provider.py` / `device_launch.py`。改造要拆的就是这一层。
- hooks 归属**只按话题（place）分**：`POST /sandbox/hooks/{topic_id}`（`routes/sandbox.py:76`），`hook_key = str(topic_id)`。`SubagentStart/SubagentStop` **没注册**（`session_launch.py:97-109` 只注册六种），`agent_id` 在后端代码里零实现，唯一引用是一条断言它被丢弃的负向测试（`test_hook_events.py:103`）。
- 轮次机制有个现成的口子：hook 到达而没有进行中轮次时，`hooks_substrate.py:1227` 会当场造一个 `platform_unsolicited` 的内存轮次——分身通知唤起的轮次今天就走它。但它**没有 `agent_turns` 行、不记用量、不结卡、不消费待读消息、不受收尸保护**。要把它变成正式轮次。
- 分身工具事件今天已经混进房间时间线（PreToolUse/PostToolUse 的 matcher 是 `*`，分身里照样触发上报），只是没带标签、无法区分——所以第一步的翻译层改造对现状是纯增益。

### 分阶段拆活（顺序做，共用房间分支 topic/80027df3）

**T1 hooks 认分身事件（✅ 完成）**：已合入分支（daf331bd1 + 6a255c5db + 7f2d9d074，全绿：unit 3634 passed、相关 integration 232 passed、ruff/pyright 干净）。落地要点：SubagentStart/SubagentStop 已注册（SubagentStop 特意不挂 cheese-sync——分身停下不是轮次停下）；新事件类型 AgentSubagentStart/Stop；AgentToolUse/AgentToolResult/AgentMessage/AgentResult 带 agent_id/agent_type（缺省 None）；拼装层（_PendingMessage）也穿透了 id；消费端零改动。

**T1 顺带实测出的三个关键事实（各复现两次，直接约束 T2/T3 设计）：**
- **分身自己的发言完全不产生 MessageDisplay**——它的话只出现在 SubagentStop.last_assistant_message 里。拼装层穿透属于防御性保留（防 Claude Code 未来悄悄改行为）。
- **存在来路不明的晚到 SubagentStop**：会话 Stop 之后才到、agent_id 与真分身不同、agent_type 为空、last_assistant_message 是提示词碎片（疑似 Claude Code 内部工具 agent）。**所以任何按 SubagentStop 落结论/落卡的逻辑必须只认平台绑定过的 agent_id，来路不明的一律不落。**
- 晚于 Stop 到达的 SubagentStop 今天会掉进 hooks_substrate.py:1227 的 platform_unsolicited 内存轮次——T3 的活证据。

**T2 任务绑定分身 + split 切换（进行中）**：`tasks` 加 `subagent_id` 列（迁移）；新路由 bind（只有房间能调、只认自己房间的 open 任务、同房间内 agent_id 不得重复绑定）；`cheese split` 不再起屏幕/kickoff/占驻留槽，改为建 task 行 + 返回 id + 提示房间芝士自己 spawn 分身后 `cheese bind`；房间 hook 流里带绑定 agent_id 的事件按绑定归到该任务的时间线（blocks 打 task_id）；SubagentStop 只对绑定 id 记录成任务时间线上的事件（**不自动落结论**——见 T1 实测第二条）；新增 `cheese conclude-task <task_id> "<结论>"`，由房间在收到完成通知、验过货之后显式走现有 return_conclusion 流程开结论卡。split 路径上随之死掉的代码（对任务的 submit_kickoff、驻留槽 admit）同阶段删除。

**T3 轮次与收尸认新形态**：`platform_unsolicited` 轮次落 `agent_turns` 行（新 reason）、记用量、结卡；#689 的 unread 闸门和 PROMPT_UNDELIVERED 不误伤通知唤起的轮次；presentation 给绑定了分身的任务算「失联」（房间屏幕死了或 SubagentStop 永不到）；完成通知非终态（同一分身可多次报完成）；晚到的来路不明 SubagentStop 的归宿要明确。

**T4 收尾清扫**：人在任务视图留言 → 唤醒房间转达（SendMessage 续跑分身）；`cheese tell` 对分身任务的语义重定义或删除；旧每任务屏幕路径的残余大扫除（retire_thread_storage 对任务、ghost sweep、device_launch 的任务分支、residency 机制去留）。

### 设计依据与风险

见上文「设计评估（更新版）」。最大风险：进程死亡时任务的表现（T3 的失联态），故 T2 上线前 T3 必须跟上。

## 待办

- [x] 三条待核结论全部实测核实
- [x] 设计评估 + 拍板
- [x] T1 hooks 认分身事件（含拼装层补丁与三条实测新事实，已完成）
- [ ] T2 任务绑定分身 + split 切换（子任务进行中）
- [ ] T3 轮次与收尸认新形态
- [ ] T4 收尾清扫（转达路 + 旧路径残余）
