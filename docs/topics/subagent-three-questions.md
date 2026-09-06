# 分身三问核实

## 目标

@wangchangxin 发来一张表，列了三条关于「分身」（Claude Code 后台 subagent）的待核结论，涉及平台要不要按 agent_id 归卡、插话机制、跨轮次收尸。逐条实测核实，给出证据。

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

## 待办

（无——三条全部核实完毕。）
