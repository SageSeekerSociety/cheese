# 多 agent、harness 契约与上下文管理

> **状态：§3（harness 契约）与 §4（技术路线）已落地；§1、§2 仍是设计**（2026-08-19 写，2026-09-14 改）
>
> 已经做完并且不再是待定的：`AgentRuntime` 契约、pi 选型、pi 同机跑通、设备启动器切成「平台骨架 + harness 填洞」。这几节下面写的是**结论**，不是选项 ——
> 想知道今天怎么跑的，读 `backend/app/domain/agent/machine_launcher.py` 和
> `backend/app/domain/agent/harness/pi/`；这里只留代码里读不出来的那部分理由。
>
> 这份文档**建立在** [`2026-08-08-topic-model-places-and-actors-design.md`](2026-08-08-topic-model-places-and-actors-design.md) 之上。
> 四层模型（房间 / 一件事 / 会话 / 工作区）、actor 加入 place、以及「平台 vs 实现」的判据在那份里已经定了，
> 这里**不重述**，只在需要时引用。
> （注：那份文档没有 `状态：` 头，「已定」是正文自述——"The second half of this document — from 'Four layers'
> onward — is the settled part"。实施前值得再确认一次它是否已被批准。）
>
> 那份文档结尾写着「schema、排期、实施计划不在本文；模型已经定到可以照着写了，那是另一份文档」。
> 这份填的是它没覆盖的三块：**多个 agent 怎么协作、harness 是什么形状、上下文怎么管**，外加**技术路线**。

---

## 0. 从 places-and-actors 继承的前提

不重新论证，只列出这份文档依赖的结论：

| 前提 | 出处 |
|---|---|
| 四层各有寿命：房间永存 / 一件事结束即完 / 会话可续 / 工作区可弃 | §Four layers |
| 房间不再消失，`archived_at` 改义为「这件工作做完了」 | §Consequences |
| 任务是**房间时间线上的一张卡片**，展开是它自己的对话——不是子话题 | §What a task looks like |
| 任务不能再拆子任务（Claude Code 也做了同样的取舍） | 同上 |
| 判据：**它的产出是否落进房间能看见的公开记录**——是则平台概念，否则实现细节 | §Where the platform ends |
| 必须活过一轮的东西要离开容器，三个出口：git、房间、记忆 | §Consequences |
| 记忆按 `agent_project` 键，一个 agent 一份，无共享池 | §Memory |

一条补充观察：本文档的分析是从产品纠结（房间该活多久、子房间还要不要）独立走到同一组结论的。
**两条路径收敛，是这个模型可靠的证据**，不是重复劳动。

---

## 1. 多 agent

### 1.1 两根轴

外界这一年看起来自相矛盾（Cognition 先说「别做多 agent」后改口、Claude Code 上了点对点消息、
Anthropic 上了几百个并行 subagent 的 Dynamic Workflows），拆成两根轴就不矛盾了。

**轴一：读 vs 写。**

> 多 agent 系统今天最有效的形态是：**写保持单线程，额外的 agent 贡献的是智能，不是动作。**
> —— Cognition《Multi-Agents: What's Actually Working》

真正在生产里跑得起来的模式全是读密集的：干净上下文的评审 agent（平均每 PR 抓 2 个 bug、58% 严重）、
多视角排查与互相挑战、能力路由。仍然失败的是自由协商——**「任意的 agent 网络互相协商，基本上是个干扰项」**。

需要并行写的时候，答案不是对话，是**隔离**：一个写者一个 worktree，冲突交给合并解决。

**轴二：协调的是模型，还是机制？**

这是「点对点消息已经发货了」最容易被误读的地方。看已发货系统的真实机制：

- **Claude Code Agent Teams**：任务是带 `owner` + `status` 的文件，**文件锁防止两个 agent 抢同一个任务**；
  依赖形成执行波次；**每个写文件的 agent 一个 git worktree**；每个 teammate 独立会话、独立上下文、不继承 lead 历史。
  点对点信箱建在这之上。
- **Dynamic Workflows**：agent 自己写一段**确定性编排脚本**，拉起几十到几百个一次性 subagent，
  生成器/验证器对跑，断点续传。**几百个 agent，零协商。**

> **趋势不是「agent 开始互相聊天了」，是「协调终于有了结构化载体——一张带 owner 的任务表、一个隔离工作区、一段编排脚本」。**

### 1.2 三层分工

这三层和 places-and-actors 的「平台 vs 实现」判据是同一条线的两种说法：

| 层面 | 形态 | 对应判据 |
|---|---|---|
| **身份与寻址** | **平级**。AI 队友是房间成员，有名字、记忆、权限，人能直接 @ | 平台——产出落进房间 |
| **执行与委派** | **树**。一个 agent 干活时派一次性 subagent 到隔离上下文，返回结果 | 实现——产出只有 spawner 看得见 |
| **队友之间的协作** | **交接一个带主的对象**，不是发消息 | 平台——状态变化在房间里可见 |

所以「多 agent 是平权还是树」这个问题问错了：**平权在成员层，树在执行层**，两者不冲突。
harness 只需要提供树；平级那一半本来就该我们自己做。

### 1.3 协作靠带主的对象，不靠消息

coding agent 干完不是「给 ops 发条消息」，是**交付出去 / 开 PR / 让下一个任务的主变成 ops**。
平台看到状态变化去唤醒对应的 agent。

为什么交接物强过消息，五条全是第一性的：

| | 发消息 | 交接一个带主的对象 |
|---|---|---|
| 可观测 | 私聊，人看不见 | 对象在那儿，人看得见 |
| 可恢复 | 易失，agent 挂了就没了 | 持久，重启后还在 |
| 有主 | 说不清 | 唯一的主 → 天然 single writer |
| 会打架吗 | 会（各自局部决定） | 不会（同一时刻一个主） |
| 人能插手吗 | 插不进去 | 同一机制，可改派、可拦下 |

**这套交接物已经有了**：活文档、PR、验收卡、决策、里程碑。这是业界绕一圈才承认需要的东西，
我们因为做的是人机协作平台，一开始就被迫做了。

### 1.4 消息可以有，但它不承重

点对点消息是真实存在且已发货的，不该一刀切禁掉。但要分清承重与否：

- **承重的**：带 owner 的任务表（同一时刻唯一的主）+ 每个写者一个隔离工作区 + 依赖关系。
- **不承重的**：信箱。它买的是**延迟**（做完直接告诉下一个人，省掉绕 lead 一圈），不是正确性。

> **顺序不能反：3 之前不要做 4**（见 §5.3 落地顺序）。这一年翻车的多 agent 系统，基本都是跳过前三步直接做消息。

### 1.5 AI 队友的意义，与一个要重开的决定

判据：

> **如果两个 agent 的区别只是 system prompt，它们不该是两个 agent。**

区别必须是这三样之一，最好是第一样：

1. **能力 / 凭据** — ops 有部署密钥，coding 没有。这是安全边界，是真边界。
2. **记忆 / 专长** — places-and-actors §Memory 的 `agent_project` 键已经给了这一条。
3. **责任** — 谁签的字、算谁头上。authorship / 归属已经有了。

只有 prompt 不同的话，它就是同一个 agent 的一个 mode，做成成员只会增加「我该 @ 谁」的认知负担。

**证据最强的角色拆分是生成与评估分离**——干净上下文的评审 agent 平均每 PR 抓 2 个 bug、58% 严重。
所以「review agent」比「前端 agent / 后端 agent」有依据得多：前者是**立场**的差别，后者往往只是 prompt 的差别。

#### 要重开的决定：权限子集化

`agent_credential/services.py` 的设计写得很明确：凭据属于**项目**，
「它能做什么 = 那个项目的成员能做什么 —— **没有按路由的授权清单，没有权限子集化**。
安全不是一套更小的权限，而是 agent 做的每件事都记在 芝士 名下且可撤销。」

那个决定针对的是「**人 → agent** 的委派」（五个人对着同一个 芝士 说话，"用谁的权限"没有答案），
在那个问题上它是对的，不该动。

但「**agent 角色之间**的能力差异」是另一根轴：它不从任何人身上派生，不会把那个问题带回来。
冲突只在"没有权限子集化"那半句上。判据清楚：

- AI 队友只是**分工** → 不需要子集化，现状不动。但那样角色就只是 prompt，按上面那条判据它们大概率不该是两个 agent。
- AI 队友要成为**责任边界**（"只有 ops 能碰生产"）→ 必须子集化，那句话得改。

**倾向后者，且有实证**：ClawArena-Team 基准（41 场景 / 258 轮，执行式打分）显示
**没有任何模型在「给 subagent 授予合适的工作区权限」上超过 50% 准确率**。
也就是说——**权限不能交给模型判断，必须是平台策略**。一个角色如果不能限制它够得到什么，这个角色就是装饰。

### 1.6 编排即脚本（第三种形态）

全仓库改名、跨数千文件的加固、大迁移这类**机械扫荡**，正确形态既不是队友也不是分身，
而是**一段确定性脚本扇出一批一次性 agent**（Dynamic Workflows 的形状）。

它和房间/队友模型**正交**，是任务的一种执行方式，不要混进成员模型。

### 1.7 一条必须记住的数据

**编排是成本杠杆，不是质量杠杆。** ClawArena-Team：不同编排方式的 API 花费相差 **100 倍**，
而总分差距**不到 4 分**。

所以做多 agent 的理由**不该是「输出更好」**，而应该是**隔离、并行、责任归属**。
别用「多个 agent 更聪明」说服自己或用户。

**两条隐藏成本**（很少有人知道，都会咬人）：

1. **N 个 agent = N 份独立缓存**。系统提示不同 → 前缀从第 0 字节就分叉，每份自己付写入。
2. **并行扇出时相同前缀的 N 个请求全部付全价**——缓存要等第一个响应**开始流式返回**才可读。
   修复：先发 1 个，等到首 token，再发其余 N−1。

### 1.8 待定：指派之后发生什么

**同一个任务换主，还是链式开一个新任务？**

- *换主*：简单，但「任务的主」变成可变的，交付归属会糊。
- *链式*：干净——每个任务有唯一且不变的主，交接表达成依赖关系（部署任务依赖实现任务，前者完成后者自动解锁）。
  代价是一次交接多一个对象。

**倾向链式**：Agent Teams 的交接机制正是依赖波次，owner 在认领时设定一次而不是被反复改。
这题决定 Task 这张表长什么样，实施前要先答。

---

## 2. 上下文管理

### 2.1 三层，各有策略

| 层 | 内容 | 寿命 | 策略 |
|---|---|---|---|
| **稳定前缀** | agent 系统提示 + 工具定义 | 跨房间、跨任务 | 永不编辑。字节一致 → **跨房间共享缓存** |
| **房间上下文** | 活文档 + 已完成任务摘要 + 近期对话 | 跟房间一样长 | **只追加，不重建**。增长速率 O(任务数) |
| **任务上下文** | 任务简报 + 工具调用与结果 | 一个任务 | harness 自己 compact。**任务结束整个丢弃**（transcript 作为「续跑优化而非持久性保证」的定位见 §3.3） |

关键性质：**任务是天然的上下文闸门**。一个跑了 50 个任务的房间，从来不会有 50 个任务的上下文——
每个任务开新会话，结束时收敛成一份交付摘要（人审过的，比 LLM 压缩强），往房间历史追加一行。

### 2.2 前缀按变化频率分层

缓存是前缀匹配：**前缀任何一个字节变了，后面全废**。渲染顺序 `tools → system → messages`，每请求最多 4 个断点。

```
[系统提示 + 工具定义]        永不变   ← tools 渲染在位置 0
[项目级稳定事实]             周级
[房间状态快照]               天级/任务级
[对话历史]                   append-only
[本轮消息]                   每轮
```

断点放在层边界；每层变动只作废它后面的。

**成本模型**（以基础输入价为单位，缓存读 ≈ 0.1×，缓存写 = 1.25× / 5m TTL）：

- 追加（带缓存）：`0.1N + 1.25M`
- 重建（不缓存）：`1.0P`
- **交叉点在 N = 10P**——append-only 历史在长到投影的 10 倍之前都更便宜。

所以目标不是「不累积」，是**「变动的东西永远排在最后」**。

**注入方式**：所有平台侧变化都走**追加**，不改前缀。
Claude 系（Opus 5 / 4.8）有中途系统消息（`role: "system"` 追加到 messages 末尾，不可伪造）；
GLM/Kimi 上没有这个机制，退路是把状态作为用户回合的文本追加——**缓存效果完全一样**，只是可被伪造。
检索走**工具调用**，结果落进历史，下一轮自动被缓存。

### 2.3 活文档：快照 + 工具 + 推送

活文档是**多写者的可变状态**——`PUT /topics/{id}/doc` 的注释写着「**改文档即指令**」，
人直接改文档是一条一等输入路径。所以它可能被 agent 自己、房间里的人、另一个 agent、交付流程改。

**不能只放上下文**：把多写者的可变状态烤进不可变前缀，等于制造「拿着过期世界模型干活」这个故障。

**不能只给工具**：模型对工具是欠触发的（文档化行为）。没读活文档就开工的 agent 不会报错，
它会**编造**上下文——这恰好是 SKILL.md 已经在防的那类失败。而且每个任务开头都要多一趟往返，
为一件你明知道它一定需要的东西。

**三件事各司其职：**

1. **开场放一份快照，并标注它是快照**（带版本与时间）。它是 grounding，不是真相。
   放在任务级前缀里，任务期间不变 → 整个任务命中缓存。
2. **工具负责新鲜度，重读时机写成规则**（模型欠触发，别交给判断）：
   写之前必读（读-改-写）、收到变更通知之后、最终交付之前。
3. **变更走推送，不走轮询**：人改了文档，平台**追加**一条通知（不改前缀，缓存安全），
   **推事实和版本号 + 一两句改动摘要，不推全文**。让 agent 自己决定要不要拉。

**文档大了**换渐进披露：开场放大纲 + 摘要 + 章节索引，全文按需读（和 Agent Skills 同形状）。

#### 一个现存的 clobber bug

`PUT /{topic_id}/doc` 收的是**整份 content，没有版本号，没有前置条件**——纯粹 last-write-wins。

场景：agent 在任务开头读了文档 → 干了 20 分钟活 → 期间人改了文档（而且这是"改文档即指令"，
人**期待** agent 看到）→ agent 用 20 分钟前那份改一改写回去 → **人的编辑被静默吃掉，两边都不报错**。

修法是乐观并发：写入带 `expected_version`（或内容哈希），不匹配就拒绝，让 agent 重读后重试。
**必须由平台强制，不能靠 prompt 规则**——凡是靠模型自觉的正确性都不是正确性。
这也是 single-writer 原则在这一层的体现，和任务的 owner 是同一个道理。

### 2.4 compaction：谁做、什么时候

**不要按时间强制 compact。** 一个会话闲置很久后回来，变的是两样东西，只有一样是 compact 能解决的：

| 变了什么 | compact 能解决吗 |
|---|---|
| 缓存过期（必然，5m 默认 / 1h 上限） | 不能。compact 反而先付一次全量读 |
| **世界状态过期**（主干动了、别人改了文件、活文档更新了） | **不能**。这才是长时间之后真正的风险 |
| 上下文太长 | 能——但这跟「过了多久」没有因果关系 |

缓存那笔账（历史 N，摘要 S）：直接续跑 `1.25N`；先 compact 再续 `1.0N + 1.25S`。
N=200k、S=20k 时 compact 当轮就更便宜；N=30k 时要跑 17 轮才回本。
**所以触发条件是 token 数，不是时间。**

**按时间该触发的是「重新落地」**：下一轮开头追加一段当前状态快照——活文档现状、
离开期间发生了什么、工作区相对主干的位置。它是**追加**，不破前缀，便宜。
compact 压缩的恰好是「我以为世界是什么样」，压完还是错的。

**更根本的一点**：按这个模型，「房间闲很久后历史很长」这个前提本身不该成立。
任务 agent 不会闲置很久（闲了说明任务停滞，该重新落地或放弃重开）；
房间 agent 闲置是常态，但它的上下文是 [稳定前缀] + [任务摘要] + [上个任务之后的对话]，最后一段是几千 token 的人类聊天。
**如果房间历史大到 compact 有意义，真正的 bug 是对话没有被落成对象**（决策、活文档、任务）。

### 2.5 触发表

| 条件 | 动作 | 谁做 |
|---|---|---|
| 任务内上下文超过窗口的 ~60% | compact | **harness** |
| 任务结束 | 丢弃任务上下文，保留交付物 | 平台 |
| 房间被唤醒且距上次活动超过阈值 | 追加状态快照（重新落地） | 平台 |
| 房间状态变化（活文档、成员、新任务） | 追加一条系统消息 | 平台 |
| 房间的任务摘要列表本身过长 | 把最老的一批合并成「房间史」 | 平台（罕见） |
| 单纯「过了很久」 | **什么都不做** | — |

只有一处需要真正的 compaction，而它在 harness 里。**平台侧不实现压缩器。**

### 2.6 前缀缓存：测过了

原计划是「发两次相同前缀，看 `usage` 里有没有 `cache_read_input_tokens`」。
2026-09-14 用 pi 对 ZAI Coding Plan 的 GLM-5.2 跑了一轮真活，结果比预期简单：

**那个端点自己做前缀缓存，不需要我们发 `cache_control`，而且不收写入费。**
一轮里 assistant 消息的 usage 是 `cacheRead` 非零、`cacheWrite: 0`。

所以 §2.2「稳定前缀 + 只追加」的成本假设成立，但**理由变了**：不是因为我们
会显式打缓存点，而是因为端点按前缀自动命中。这有两个后果：

- 我们**不控制**缓存边界，所以「按变化频率分层」从一个可以精确执行的策略，
  退化成一个「让前缀尽量别动」的方向。前缀动一个字节，后面全部重算。
- 换到一个**不做**自动前缀缓存的端点时，这一节要重测，而不是照搬。

引用的 Anthropic 倍率（读 0.1×、写 1.25×/5m、2×/1h）对这条路**不适用**，
留在这里只是为了说明它不适用。


---

## 3. Harness 契约

### 3.1 契约

> **给定一个隔离工作区、一段不再变的开场、一组工具和一条权限边界，
> 跑一个 agent 直到产出交付物，把过程作为结构化事件吐出来。**

**输入**（每次执行，一次性构造后不再编辑）

- cwd（隔离工作区）
- 稳定开场：系统提示 + 工具定义 + 任务简报 + 活文档快照
- 工具集与权限边界（平台决定，模型改不了）
- 模型 + 预算上限

**输出**（流式）

整条消息 · 工具调用 · 工具结果 · 用量 · 结束原因。每个事件带稳定 id 和序号，可续传。

**控制**（执行中）

- **注入（append）**——平台追加状态的唯一通道：人插话、任务改派、活文档更新。
  pi 的 `steer`/`follow_up`、ACP 的 `session/prompt` 都是这个位置。
- 中止
- 问人（阻塞，答案回到这次执行里）

**它需要做的唯一「聪明事」：任务内部的上下文压缩。**

### 3.2 它不需要做的（这决定了选型）

- 会话持久化——房间日志与任务对象才是权威
- 多 agent、路由、成员
- 审批策略——策略在平台，它只要有一条「问出去」的通道
- TUI、MCP
- 上下文来源发现（CLAUDE.md 层叠、skill 自动发现）——**我们给什么它看什么**

### 3.3 无状态执行器，以及它和「会话可续」的关系

> **harness 是一个无状态执行器：给定输入产出事件流，自己不持有权威状态。**

places-and-actors 把「会话」列为四层之一，性质是 *resumable*（`--resume` + 每话题 transcript 持久化）。
这和「无状态」不矛盾，两者的分工是：

- **任务内**：harness 自己的会话/transcript 是续跑的手段——便宜、细粒度、保留完整工具历史。
- **跨任务与房间层**：平台的对象是权威。

也就是说 transcript 是**续跑的优化**，不是**持久性的保证**。持久性由 places-and-actors §Consequences
定的三个出口给：**git、房间、记忆**。丢掉 transcript 的后果是退化成「重新落地」，不是丢工作。

这个性质直接解决一类现存问题：**harness 随时可被杀、被换、被重启。**
#316 / #459（部署打死正在跑的轮次）本质上是"权威状态住在了一个会被杀掉的进程里"——这个契约把病根拿掉。

### 3.4 顺带消掉的东西

今天为了从一个给人看的 TUI 里抠结构化事件所付的代价，在这个契约下全部消失：

tmux + send-keys 注入 · `❯` 就绪握手 · 预接受首启门 · 固定 pane 几何 ·
命令 hook 脚本 + spool 落盘 + reconcile 去重 · MessageAssembler 拼消息 · 自造 event id ·
`--disallowedTools AskUserQuestion`（问人成为一等通道）· 交互 hooks 拿不到 usage（`hook_events.py` 置 0）。

⚠️ **订阅路除外。** 那条路的合法性建立在「客户端就是 Claude Code 本体」上（§4.6），
所以只要订阅还在，`hooks_substrate.py` / `tmux_provider.py` 这套就得留着。
**它留多久取决于待定 #3**——在那个决定之前，不要按这一节去删代码。

---

## 4. 技术路线

### 4.1 选了 pi，以及选完之后才知道的事

三个候选（留在 Claude Code / pi / DeepSeek Harness）的对比已经不用留了 ——
**选了 pi**，DSH 那条线没有开。理由是它的 RPC 模式基本就是 §3.1 的契约：
JSONL over stdio、`prompt`/`steer`/`abort`、`get_entries since=<id>` 是可续传
游标、`get_session_stats` 给 usage 和 cost。契约不用翻译，是它最大的价值。

已知的代价一条：**没有树状 subagent**，要自建。这条没有因为落地而改变。

下面是**接上去之后**才知道的，文档里推不出来，也是这一节现在唯一还值得读的部分：

- **包名是 `@earendil-works/pi-coding-agent`**（`bin: {pi: dist/bundle/cli.js}`）。
  `@earendil-works/pi` 不存在；`@mariozechner/pi` 是同一批人的另一个包（`pi-pods`）。
- **`get_entries since=null` 会被拒绝**（`Entry not found: null`），不是「从头读」。
  新会话第一次拉取不能带这个字段。
- **`PI_CODING_AGENT_DIR` 只搬 pi 自己的 config。** 配置目录已经指到会话家目录了，
  一次普通运行照样会把机器主人 `~/.agents/skills/` 下的 SKILL.md 贴进系统提示词。
  隔离要靠 `--no-skills --no-extensions --no-prompt-templates --no-themes`。
  **这是把别人的机器接进来时的一条硬边界**，不是洁癖。
- **`models.json` 的 `"apiKey": "$CHEESE_TOKEN"` 在请求时才解析**，所以房间的
  scoped token 既不落盘也不进 argv。自定义 provider 走
  `<PI_CODING_AGENT_DIR>/models.json`，`api: "openai-completions"` + `authHeader`。
- **`--append-system-prompt <路径>` 读文件内容**，不是当字面量。

### 4.2 协议这一层：已落地

`AgentRuntime`（§3.1）已经是代码，`ComputeProvider` 不再兼职决定用哪个 harness：
池子的键是 `(machine, harness)`。

```
Room（我们的，多方）
  └ 投影 → Task 会话（每任务一个，单方线性）
       └ AgentRuntime（§3.1）→ claude-code | codex | pi
            └ ComputeProvider → 容器 | 设备 | Cloud
```

当时写「如果把 `AgentRuntime` 按 ACP 的形状定，选型就从押一个变成接一类」。
实际落地下来，**接一类的那道缝不在事件契约上，在启动上**：三个 harness 的事件
形状差别有限，真正各不相同的是「这台机器上怎么把它起起来」。所以设备启动脚本
被切成平台骨架（`machine_launcher`，写平台文件、起 tunnel/preview/drainer、
监管 agent 进程）加五个洞（`staging`/`configure`/`credentials`/`prepare`/
`command`），harness 只填洞。

**权限要两道**仍然成立，且第一道已经在用：pi 的 `--tools` 一类的启动时裁剪，
比运行时弹窗可靠——基准数据说模型在权限判断上准确率不到一半。ACP 的
`session/request_permission` 那道还没接。

### 4.3 分步：做到哪了

| 步 | 做什么 | 状态 |
|---|---|---|
| **S0** | 定 `AgentRuntime` 协议 | ✅ |
| **S1a** | 端点前缀缓存实测 | ✅ §2.6 |
| **S1b** | pi RPC spike | ✅ 中文长文本注入逐字节无损 |
| **S1c** | DSH Python SDK spike | ✘ 没做，选了 pi 之后没有理由做 |
| **S2** | 适配 + 事件翻译层 + 单测 | ✅ pi 同机跑通，房间里可选 |
| **S3** | `evals/` A/B | 还没做 —— §4.5 的判据一条都还没量过 |

pi 今天的形状：runner 是屏幕跑的那个进程（不是旁边起的守护进程），跟工作区同机，
没有 executor，工具原地执行。后端通过 `hub.call_executor` 够到它——那个通道
按 state 目录算 socket 中继一行 JSON，跟 executor 本身无关，所以连接器一行没改。

### 4.4 落地顺序（多 agent 部分）

1. **Task 对象：owner + status + 依赖** ← 承重的那块
2. **每个写者一个隔离工作区**（`.worktrees` 从话题挪到任务）
3. **角色的能力边界由平台决定，不由模型决定**（§1.5 那个要重开的决定）
4. （可选）房间内 agent 之间的信箱——只买延迟，不买正确性
5. （另一条线）编排即脚本，给全仓库扫荡类任务用

> **3 之前不要做 4。**

### 4.5 判据

**转正的必要条件**

- `evals/` 现有场景不低于当前基线；
- 一轮里 `cheese` CLI 的调用正确率不降（平台动作的命门）；
- 中文长文本注入零损（今天靠 tmux 三步注入换来的，新 harness 应当白送）；
- 断线重连能补齐，且不重不漏。

**否掉的信号**

- 模型在最小工具集下明显更爱瞎逛；
- 协议在实验期内出现破坏性变更且无迁移说明；
- 「现场」（事件流渲染）拿不出比终端镜像更好的可读性。

### 4.6 订阅路

订阅席位（`provider_env.py` 的假 OAuth token + MITM CA + `--add-host` 劫持）的合法性
建立在「客户端就是 Claude Code 本体」上，**换 harness 就没了**。

所以 harness 层至少长期有两个实现，除非产品决定放弃订阅、全押 API key 池。
**这是产品/成本决定，不是技术决定**，需要单独拍板。

落地之后多知道一件事：订阅路**也是中心会话机器存在的唯一理由**。凭证不是理由
（`llm_proxy` 的 scoped token 让密钥留在后端），版本钉死也不是（`bootstrap.binary()`
会往执行机上装钉死的 claude）。要一台能被我们 MITM 的机器，才要一台单独的机器。
pi 不走订阅，所以它同机跑，`CentralChannel` 那条路它根本不经过 ——
`center == executor_id` 那道闸也就不用动。

---

## 5. 待定的决定

| # | 问题 | 影响 | 倾向 |
|---|---|---|---|
| 1 | 指派之后是同一个任务换主，还是链式开新任务？ | Task 表的形状 | 链式（§1.8） |
| 2 | 权限子集化要不要重开？ | AI 队友是不是真的责任边界 | 要（§1.5） |
| 3 | 订阅路留不留？ | harness 层永远两个实现 vs 一个 | 未定，产品决定 |
| 4 | ~~GLM/Kimi 侧前缀缓存可用吗？~~ | — | **已答**：端点自动做，见 §2.6 |

前三个仍然要人拍板。

---

## 6. 实施时要清理的文档

按 CLAUDE.md：「当一个设计落地，在同一个 PR 里删掉它作废的每一句话。」

2026-09-14 复查这张表时，它自己已经烂了两行 —— `docs/tmux-backend-spike.md`
早就不在了，`config.py` 的 `agent_backend` 也早就没了。**这正是 CLAUDE.md 说
「关于现状的陈述是一颗延时炸弹」的样子**：一张记着「将来要清理什么」的表，
本身就是一份会过期的现状陈述。所以这张表现在只剩还活着的那行，
下次谁再想往里加行，先想想是不是该直接去改那份文档。

| 文档 | 作废的部分 | 为什么 |
|---|---|---|
| `docs/fusion-design.md` §6 | 「我们"子话题带简报 + 父文档快照"就是对的，**不改**」 | 子话题降格成任务卡片（places-and-actors §What a task looks like 已定） |
| `docs/spec.md` §8.4 | 分身 = 子话题的表述 | 同上 |

另外：`docs/plans/2026-08-08-topic-model-places-and-actors-design.md` **不作废**——
这份是它的延续，实施时两份一起读。

---

## 参考

**外部（全部 2026 年）**

- [Cognition《Multi-Agents: What's Actually Working》](https://cognition.com/blog/multi-agents-working)（写单线程；"任意 agent 网络协商是干扰项"；评审 agent 每 PR 2 个 bug / 58% 严重）
- [Cognition《Don't Build Multi-Agents》](https://cognition.com/blog/dont-build-multi-agents)（前作）
- [Agent Teams 机制拆解](https://alexop.dev/posts/from-tasks-to-swarms-agent-teams-in-claude-code/) · [共享任务表如何避免冲突](https://www.mindstudio.ai/blog/claude-code-agent-teams-shared-task-list)
- [Dynamic Workflows in Claude Code](https://quasa.io/media/dynamic-workflows-in-claude-code-anthropic-s-first-real-agent-swarm-that-actually-ships)
- [ClawArena-Team 基准](https://arxiv.org/abs/2606.31174)（权限授予准确率 <50%；成本差 100× 而分数差 <4）
- [Anthropic《How we built our multi-agent research system》](https://www.anthropic.com/engineering/multi-agent-research-system)（orchestrator-worker，15× token）
- [Claude Tag（官方）](https://support.claude.com/en/articles/15594475-what-is-claude-tag)
- [Agent Client Protocol](https://agentclientprotocol.com/protocol/overview) · [Zed ACP](https://zed.dev/acp)
- [earendil-works/pi](https://github.com/earendil-works/pi)（装的是 npm 上的
  `@earendil-works/pi-coding-agent`）· 文档随包发布：`docs/rpc.md`、`docs/models.md`、
  `docs/environment-variables.md` 就在 `node_modules/@earendil-works/pi-coding-agent/docs/`
- [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) · [架构](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md) · [subagent](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/subagent.md) · [Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/README.md)

**内部**

- [`2026-08-08-topic-model-places-and-actors-design.md`](2026-08-08-topic-model-places-and-actors-design.md) — 四层模型与「平台 vs 实现」判据（本文的前提）
- `backend/app/domain/agent/compute.py` — `ComputeProvider` 契约与 `build_compute_pool`
- `backend/app/domain/agent_credential/services.py` — 「没有权限子集化」那个要重开的决定
- `backend/app/api/routes/topics.py` — `PUT /{topic_id}/doc`（§2.3 的 clobber bug）
