# room 与 task 的设计，以及审批流程

> 目标：把「房间（room）／一件事（task）」这个模型，和「从做完到合进 main」这条审批流程，
> 按**代码里现在真的是什么样**和**文档里当初怎么设计的**分别讲清楚，并列出两边不一致的地方。
> 提问人 @fulu，2026-08-17。
>
> 现状部分最后一次对着 main 核实是 2026-08-23（`3e945077c`）。中间「一件活」的载体换过一次
> （#611 → #612 → #613：从一行 `topics` 换成 `tasks` 表里的一条支线），本文已按新形状重写；
> 那次改造本身的记录在 <&docs/topics/task设计现状.md>。

## 0. 先排掉一个歧义：「task」在本仓库有两个不同含义

这两个东西同名但毫无关系，讨论时必须先指明是哪一个：

| 说法 | 是什么 | 代码位置 |
|---|---|---|
| **`tasks` 表 / `room_task`** | 「一件活」——房间里的一条支线：有唯一的主、自己的对话、自己的分支和工作区 | <&backend/app/domain/room_task/models.py> |
| **`domain/task/`** | 老知是社区的作业任务（team/space 那套），与 CheeseX 话题体系无关 | <&backend/app/domain/task/> |

下文说 task 一律指第一个。

`TopicKind` 里还留着 `task`/`subtopic` 两个值，但它们是**历史值**：迁移 `a9f3c7e21b04`
把每一行这样的话题转成了 `tasks` 行并删掉，现在没有任何行携带它们、也没有任何代码写它们。
留在枚举里只为一件事——从迁移之前的备份恢复出来的库里还有这些值，一个读不了自己历史的枚举
会把恢复变成崩溃。

---

## 1. room / task 模型

### 1.1 设计意图（文档侧）

出处：<&docs/plans/2026-08-08-topic-model-places-and-actors-design.md>。

它要解决的 bug 是一句话：**"采纳之后什么都追不到了"**。原因不是缺功能，而是一个「话题」
同时是四样东西，四样东西的自然寿命完全不同：

- **房间**：有成员名册、@all 能触达 —— 应该永远活着
- **一件事**：有验收卡、会被采纳、会归档 —— 做完就结束
- **工作区**：一个 git 分支 + 一个容器 —— 可以随时销毁重建
- **会话**：一个 tmux 进程 —— 就是个进程

四个挤成一个对象，**最短的寿命赢**：一采纳，房间、工作区、记忆全跟着没了。

于是设计成四层，各自寿命分明：

```
房间          永久        消息、成员名册
 └─ 一件事      做完即止     分支/PR、验收卡、进度
     ├─ 会话     可续        --resume
     └─ 工作区    可丢        从分支重建
```

关键取舍两条：
- **一件事不能再拆一件事**（不嵌套）。要拆就在同一个房间里再开一件事，而不是把树挖深一层。
  Claude Code 的 agent team 也是这个选择（队员不能再招队员）。
- **判断某个东西该不该是平台概念，只问一句**：它的产出会不会落进房间里所有人都能看见的公共记录？
  会 → 平台概念（一件事就是）；不会 → 实现细节（子 agent、容器里的辅助进程）。

### 1.2 代码里现在是什么样

四层里的前两层已经分开了，而且分得比设计图更彻底——**一件事不再是一行话题**：

- **房间 = `topics` 行**，`kind` 只有 `root`（项目本体）和 `topic`（房间）两个值在用。
  房间之下不能再建房间：`_require_room(parent)` 直接拒绝
  （<&backend/app/domain/topic/services.py>）。
- **一件事 = `tasks` 行 + `blocks.task_id`**（<&backend/app/domain/room_task/models.py>）。
  它有而房间没有的：唯一的主（`owner_handle`，不是名册）、`open`/`closed` 两态、
  自己的分支和工作区、自己的一条对话、自己的交付戳。
  它没有而房间有的：名册、未读游标、归档决策、侧栏里的一行——
  这正是这次改造的全部收益，一件活不再需要付「一个房间」的成本。
- **两者由 `Place(room, task)` 粘起来**（<&backend/app/domain/room_task/place.py>）：
  所有会跑的东西——一轮对话、一个工作区、一张验收卡、一笔用量——地址都是这一对。
  一条支线沿用了它替代掉的那行 `topics` 的 id，所以一个 id 仍然能定位一个地点。
- **不嵌套是机制，不再只是约定**：从一件活里再拆，拿到的是同一个房间里的另一条支线，
  不是它的子节点；房间之下再建房间被 `_require_room` 挡住。
- agent 收到的操作说明按阶段分发：`resolve_stage()`（<&backend/app/domain/agent/stages.py>）
  把房间算成 `delegating`（拆活）、一件事算成 `working`（干活），再据此只注入对应那一段 skill。

---

## 2. 审批流程（从做完到进 main）

### 2.1 现在实际跑的这一条（#718：采纳就是合并）

```
芝士自己把检查跑绿（这是干活的一部分，平台不替你跑）
  → cheese accept-request <人> --subject '...'   递验收卡，卡直接是 pending，平台随手开 PR
     → GitHub Actions 按 .github/workflows 跑真 CI
        → 轮询器每 60 秒把合并态镜像到卡上（CLEAN/UNSTABLE/BLOCKED/BEHIND/DIRTY），
          按「谁的活」表发事件（红了叫芝士、落后平台自己换基、绿了通知验收人）
           → 人在卡上点「采纳」＝ 当场调合并 API，合的是卡面显示的那个 commit
             （merge API 带 sha；head 变了 GitHub 409 → 卡刷新、票作废，人重看）
              → 合并成功 → 卡进 accepted，本地 base 同步下来，给房间和这条支线各盖交付戳
                （`accepted_by`/`accepted_at`），结果回房间。**话题不归档**
```

「只在绿的时候合」由**分支保护规则**执行，按项目配置
（`PUT /projects/{id}/branch-protection`：必跑检查名单可带路径域、strict 追平、
新提交作废采纳默认开、绿了自动合、人工放行名单、批准人数）。GitHub 自己开了保护的
项目直接听 GitHub（405 就是被拦住）；判定不了的（free 计划私有仓）平台按同一套规则补位，
判定只有一处：`merge_state.compute_merge_state`，点击时和轮询时都调它。

**用谁的凭据推、以及失败了怎么办，由「forge」一次性决定**
（<&backend/app/domain/review/forge.py>，二选一）：

| forge | 什么时候 | 用什么推 | 失败了 |
|---|---|---|---|
| `github_app` | 接了平台 GitHub App **且**上游是 GitHub | App 自己的 write token | **停下**，绝不退回本地合并 |
| `platform` | 项目根本没接 GitHub | 什么都不推：平台自己的裸仓库就是权威 main，合进去就是终点，GitHub 上不动一个字节（平台手里也没有能推它的凭据） | 本地合并**不是降级**，是这个项目唯一合法的采纳（#363），卡上有一条 note 说明 |

分清这两条很重要：#362 就是一个接了 GitHub 的项目，采纳掉进了本地合并、直接推进 main，
没 PR 也没 CI，一天里发生两次。判不出绑定状态时这里**报错而不是猜**——猜错的那一边会推进 main。

### 2.2 人在这条流程里的几个动作

| 动作 | 接口 | 含义 |
|---|---|---|
| 采纳 | `POST /accept-cards/{id}/accept` | **当场合并**卡面显示的那个 commit；规则没满足直接拒绝 |
| 绿了自动合 | `POST /accept-cards/{id}/auto-merge` | 验收人在 BLOCKED/BEHIND 时布防；规则满足平台以布防人的名义合并。新提交作废布防 |
| 人工放行 | `POST /accept-cards/{id}/merge-anyway` | 明知没全绿也要合。默认拒绝、显式放行，署名留痕；准入是项目的 override 名单（默认 owner+lead） |
| 作废 | `POST /accept-cards/{id}/void` | 把卡片推进终态，解开 `conflict` 等卡死。**不是放行** |

三条都要登录，且都由 `_forbid_ai` 挡住芝士——协作模式下 **AI 不能验收自己做的东西**（spec §4.4 硬规则）。
撤销采纳（`revoke`）只有原采纳人或项目组长能做，它**只清交付戳**（话题回到「还没交付过」，因此又能递卡），
**不动归档状态**——采纳既然不再顺手归档，一张卡的撤销就没有理由覆盖某个人「把这个话题收起来」的决定。
文案上也要说清：撤销的是这次**验收记录**，不是这次**合并**——PR 已经在 main 上了，git 层面什么都没撤。

### 2.3 平台的私有闸门已经退休了（重要，且与好几处文档/帮助文本冲突）

`review/gate.py` 第一行就写着 **THIS RUNNER IS NO LONGER DISPATCHED**。核实结论：

- `create_card()` 现在**总是**把卡建成 `pending`，不再进 `pending_gate`。
- <&backend/app/api/routes/accept.py> 里已经没有 `gate.dispatch` 调用，只剩 `pr_publish.dispatch`。
- `project.settings.check_command` 这个字段还在（设置页还能改），但**没有任何代码会去跑它**。
- 只有 `gate_sweep` 还在跑，作用是清理退休前留在库里的历史 `pending_gate` 行。

设计理由（<&docs/accept-is-merge.md>）：一张卡就是一个 PR 的视图，**判断改动好不好是 PR 上的真 CI 的事，
不是平台自己写一个私有检查器**。旧闸门跑在没有 Postgres 的机器上，只能 `check.sh --no-tests`，
所以"卡是绿的"历史上只等于 ruff+pyright 过了，一个测试都没跑；而且它是个纯内存
`asyncio.create_task`，dev 上 155 张卡有 29 张（19%）死在里面。

---

## 3. 文档与代码不一致的地方（按重要性排）

③ **"房间不带分支、不走验收卡"这条约定本身被推翻了，不再是差异**。
现在的形状恰恰相反：**开 PR 的就是房间**（一个房间一条分支一个 PR），一条支线的提交在它的结论
被采信时折进房间分支（`fold_into_room`）。所以 `branch_for_place()` 对房间也给分支名、
`create_card` 不看 kind，都是对的，不再是"后端没强制"。

④ **"一件事是房间时间线上的一张卡片"仍然只做到一小半**。房间时间线上有一行读时派生的
「已派出《X》」标记（<&frontend/src/components/DispatchedMarker.vue> ＋
<&frontend/src/lib/splitMarkers.ts>，按支线的 `room_id + created_at` 现算）。
它能说的只有标题、状态、一个跳转链接，说不了"做到哪了、改了什么文件、检查绿没绿"。
根因还在：`cheese split` 不往房间主线写任何 block（`SplitIn` 只有 title/created_by/brief），
所以"哪几条消息／哪一项待办归了别人"派生不出来——数据库里没有那条边。

⑤ **fusion-design §3 与 places-and-actors 直接对立**。前者（2026-07）说"队友把群聊和事项拆两层，
**我们不拆**"；后者（2026-08-08）说必须拆，理由就是不拆导致采纳后什么都追不到。
后者是更晚的结论，而且已经落地，但 <&docs/fusion-design.md> 那句还在。

⑥ **退回验收卡不通知任何人**（代码核实）：`reject()` 只 `notes.record` 一行，既不发消息也不 summon 芝士
——跟 CI 失败会 summon 的行为不一致。所以每次退回一张卡，必须额外用 `cheese tell` 说明理由，
不能假设对方会知道自己被打回了。

---

## 4. 拟定的两项修改（@fulu 2026-08-17 提出）：落地结果

### 修改一：task 结束 ≠ 归档。归档由人决定

**已落地**（#442 decision 1）。采纳不再归档任何东西，它盖的是交付戳
（`accepted_by`/`accepted_at`，房间和支线上各一份）；归档只有人能做，含义是「从列表里收起来」，
顺带冻结工作面。

当初列的五个洞，各自的下场：

**① "卡结束"必须精确成"卡被采纳"** —— 成立并保持。一个 task 生命周期里可以有多张卡：
递卡 → 被驳回 → 改完再递。`reject()` 之后话题保持 active，而 `create_card` 的阻塞集里
**故意不含** `gate_failed`——红了作废重递就是正常流程。所以 `rejected`/`revoked`/`void`
都是"卡结束"但绝不是"task 结束"。

**② 计费云机器的回收** —— 拆开了，但**没有全拆**，而且是故意的。容器和设备屏各有自己的闲置回收
（`scheduler.reap_idle_containers` / `reap_idle_device_screens`）——"还有没有人在用"是个关于活跃度的
问题，跟"某条分支落没落地"无关，所以它们不再挂在采纳上。**唯一的例外是计费云 VM**：它是唯一按小时
烧钱、又唯一没有 reaper 的东西，释放路径只有「人去归档」和「采纳时释放」两条
（`TopicService._release_cloud_machine`，#442 decision 3）。所以采纳仍然会释放它，而且不是
best-effort——MicroCloud 没接受删除，采纳就不许报成功。
**代价要知道**：刚交付完的云话题手上没有 VM 了，而重新开机要一个有权限的人来调，
所以它的下一轮需要人先说句话。**回收策略本身仍然没定。**

**③ 工作面冻结** —— 归档仍然是那个开关，采纳不再顺手按它，防空 PR 的职责改由
「已有一张 `accepted` 卡」在 `create_card` 里挡住（代码注释原话：「那一半由下面的 accepted 卡挡着」）。

**④ "已交付但未归档"这个状态** —— 有了，只是没叫 `delivered_at`：话题和支线上都加了
`accepted_by`/`accepted_at`。`TopicStatus` 保持 `active/archived/draft` 三个值不变。

**⑤ 没人会去归档** —— **仍然没解**。我没有在代码里找到「列表默认折叠已交付的」或
「交付 N 天后自动归档」这类默认收敛，所以这条还是原样：清理成本在人身上，而人不做。

**附带**：`revoke()` 的尴尬被拆掉了——它不再动归档状态，只清交付戳。文案仍要说清：
撤销的是这次**验收记录**，不是这次**合并**（PR 已经在 main 上了，git 层面什么都没撤）。

### 修改二：开子话题要慎重（小任务交给 subagent）

同意慎重，但**判据建议换掉**——"任务大小"没有客观界线，agent 每次都得猜。
places-and-actors 里已经有一条更可操作的：**它的产出会不会落进房间里所有人都能看见的公共记录？**
会 → 子话题；不会 → subagent。

这才是 subagent 真正的成本：它的产出只在容器 transcript 里，房间里的人看不见，跑偏了没人能中途
纠正、挂了没人知道。所以"小任务给 subagent"隐含一个赌注——这件事不会失败到需要人介入。而 subagent
最常见的失败恰恰是**自信地返回一个错结论**。所以判据该是"错了要不要有人能看见"。

当初列的四个缺陷，两个已经不成立了：

**A. 真正该慎重的不是"开不开"，是简报。** 仍然成立，但**不再是单向的**：现在有
`cheese tell`（`POST /topics/{id}/tell`），父子之间能互相说话并唤醒对方，只开这一条边。
所以"简报发出去就联系不上"已经不对了——不对的部分是**简报本身仍然改不了**，
一条支线开工时看到的仍然只有 `--brief` 原文 ＋ 房间文档快照，看不到房间的对话历史。
规则仍然是「简报写不全就不许拆」，而不是「任务小就不许拆」。

**B. ~~工作区从 main 新建~~ —— 已经不是这样了。** `_ensure_worktree` 现在从
**它所在房间的分支**长出来（`_fork_point`），因为"一个房间一条分支一个 PR"。
所以"依赖房间尚未合并的改动的活不能拆出去"这条硬判据**已经作废**，可以拆。

**C. "人为开"会把瓶颈压回人身上。** 仍然成立。更好的形式是**默认允许 + 可见 + 可拦**：
agent 可以拆，但拆之前要在房间里一句话说清为什么这件事该独立开。而现在
`cheese split` 不往房间主线写任何 block，房间里完全无痕，前端只能读时派生一行「已派出」标记。
**要让人能拦，先得让人看见——这是个真实缺口。**

**D. ~~"不嵌套"没有代码强制~~ —— 现在是机制了。** 工作本来就不再嵌套：从一条支线里再拆，
拿到的是同一个房间里的另一条支线；房间之下再建房间被 `_require_room` 直接拒绝。

## 5. GitHub issue #184（@andylizf）声称的三条改动，落地程度核对

| issue 里说的 | 代码里的实际状态 |
|---|---|
| 闲置容器回收：8 小时闲置、每小时扫 | **完全落地**。`sandbox_idle_hours = 8`、`sandbox_reap_interval_seconds = 3600` |
| 每个芝士有自己的记忆 | **落地，而且比 #184 想的走得更远**：记忆挂在 `agent_instances` 上（一个 agent × 一个项目一个池），不再由话题 id 派生，所以它真的能跨房间累积 |
| 任务独立成一层，且**不能再往下拆** | **完全落地**。一件活不再是话题行，从活里再拆得到的是同房间的另一条支线 |
| 任务该是时间线里的卡片，「下个 PR 处理」 | **仍然只做到一小半**：有一行读时派生的「已派出」标记，没有卡（见下） |

两点补充：

**① 「任务变成时间线卡片」不是前端活。** 数据层缺一条边：`cheese split` 不往房间主线写任何 block
（`SplitIn` 只有 title/created_by/brief），所以父话题时间线上"一件活被派出去"完全无痕。
前端现在只能**读时派生**一行「已派出」标记，而 <&frontend/src/lib/splitMarkers.ts> 的注释自己
划清了界限：能派生"此刻从这个房间派出了 X"，派生不出"这一项待办不归这里了"。
要做的是 split 时写一条 block，不是改渲染。

**② 「跨芝士的运维经验没有落点」这个缺口已经小了很多。** 记忆的池不再按话题分——
它挂在 `agent_instances`（一个 agent × 一个项目一个池，<&backend/app/domain/agent_instance/models.py>），
所以同一个芝士在五个房间学到的东西互相看得见。像"沙箱里跑 jj 会把全项目打停"这类经验
不再只留在踩到它的那一个分身脑子里。
注入侧也分了层：一条 core（每轮在场，4000 字符预算）＋ 其余按需检索
（20000 字符预算），两道上限**都不静默**，被丢掉的会在 prompt 里明说漏了几条
（<&backend/app/domain/memory/store.py>）。

## 6. 参考：buzz 怎么区分不同的 agent（2026-08-17 直接读了源码）

沙箱里没有 buzz 的本地副本（`reference/` 是 gitignored，本沙箱没有），但 `raw.githubusercontent.com`
和加了 User-Agent 的 GitHub API 在沙箱里都通，所以下面是**读真实代码**得到的，不是转述。
仓库：`github.com/block/buzz`（Apache-2.0，Rust，4010 个文件）。

**一句话：身份是密钥，不是字符串。** 一条完整的链：

| 层 | Nostr 事件类型 | 说的是 | 寻址／保护 |
|---|---|---|---|
| 这个 agent 是谁 | `KIND_MANAGED_AGENT` = 30177 | owner 签名发布的 agent 定义 | 按 `(owner_pubkey, kind, d_tag)` 寻址，**d_tag 就是 agent 自己的公钥** |
| 它的凭据／运行时 | `KIND_PRIVATE_MANAGED_AGENT` = 30179 | 私钥、env、runtime | NIP-44 加密给 owner。30177 那条是世界可读的，代码里写死"MUST never carry the agent's secret key / auth tag / env vars / runtime fields" |
| 它是什么样 | `KIND_PERSONA` = 30175 | 配置（人设） | 默认作者可见，要共享得显式打 `["shared","true"]` **标签**（不是内容字段，这样切换共享不改内容哈希） |
| 它学到了什么 | NIP-AE engram | 记忆 | 见下 |

记忆那条最值得看（`crates/buzz-core/src/engram.rs`）：`build_event(agent_keys, owner_pubkey, …)`
——事件**由 agent 自己的密钥签名**，用 `conversation_key(agent_secret, owner_pubkey)` 加密到
「agent ↔ owner」这一对，连条目名都不是明文（`d_tag = HMAC(K_c, "agent-memory/v1/d-tag" ‖ 0x00 ‖ slug)`）。
双方都能读：agent 用自己的私钥＋owner 公钥，owner 用自己的私钥＋agent 公钥。

**所以「记忆按 agent 分池」在 buzz 那边不是一个 WHERE 条件，是密码学**：没有那把私钥，
事件既不是那个 agent 发的、也解不开。

对照我们：我们区分芝士靠 **handle 字符串**（`cheese-<话题 hex>`）＋后端的 scope 字段
（`MemoryScope.agent_project`，scope_id = 项目 id + agent handle）。语义一样，失效方式不一样——
字符串**可以忘记传**。这正是 2026-08-10 那个坑：沙箱里装的 cheese CLI 是旧构建、`remember` 不带
`topic`，后端认不出是哪个芝士，于是所有记忆**静默落进共享 project 池**，没有任何报错。
密钥不会有这种失效模式：签名不对，事件就不是那个 agent 的。

另外两条与我们直接相关：

- **进程与身份解耦。** `VISION_AGENT.md`：一个 buzz-agent 进程最多挂 8 个并发 session，
  各自独立的 MCP server／历史／上下文。身份不住在进程里，所以换进程不改变"它是谁"。
- **community 是状态边界。** 原文：agent 连的 relay URL 选定它的 community；profile、presence、
  DM、记忆、jobs、频道成员都 scope 在那个 community 内，「同一个 npub 可以加入另一个 community，
  但不继承任何 agent state」。这和我们把记忆 scope 在**项目**上是同一个选择
  （places-and-actors 里那句「项目是我们的 community」）。

### 6.1 再往下读一层：buzz 的「一个身份，N 个可互换的进程」

`crates/buzz-acp/src/pool.rs`（8141 行）里，pool 持有 `agents: Vec<Option<OwnedAgent>>`——
**这些槽位是进程，不是身份**。派活的 `try_claim(channel_id)` 只有两趟：

1. 优先找**已经有这个频道 session** 的空闲槽（亲和）；
2. 找不到就用**任何**空闲槽。

第 2 趟是关键：一个频道的下一轮可能落到没有该频道 session 的槽上，于是新建 session、
上下文从头开始。这条逻辑只有在「所有槽都是同一个身份（同一把密钥）的 worker」时才成立——
**所以 buzz 的「同一个 agent」＝同一把密钥，而不是同一个进程**；亲和只是优化，不是保证。
`AcpClient` 刻意不实现 `Clone`：claim 时所有权移出槽，用完移回。

`SessionState` 里有三样我们没有的东西，都值得单独想：

- `core_sections: channel_id → 渲染好的 core 记忆`，注释写明 **session 创建时填一次，中途不刷新**；
- `canvas_sections`：这个频道的协作文档（Channel Canvas），同样只在 session 出生前填一次，
  且**取不到就不注入、绝不阻塞**（fail open）；
- `turn_counts` + proactive session rotation：**按轮数主动轮换 session**，而不是等上下文撑爆。

### 6.2 从 buzz 提炼出的三条，对我们真正有用的

**① 「core」这一层我们后来补上了。** buzz 只把**一条** `core` engram（agent 自己写的身份/规则/目标）
注进 prompt，其余记忆是 agent 用 `buzz mem` **按需查**的。这条判断被采纳了：
现在池子分两层（<&backend/app/domain/memory/store.py>）——`core` 每轮在场，
预算 4000 字符，故意比另一条紧一个数量级（撑破它本身就是「core 不再是 core」的信号）；
`fact` 是学到的一切，20000 字符预算的职责不是装下全部，而是把位置花在这一轮用得上的事实上，
剩下的一句 `cheese recall` 之外。两道上限**都不静默**。
架构上的道理没变：**「我是谁」必须每轮在场，「我知道什么」应该按需检索。**

**② 记忆读不出来时，绝不能渲染成「你还没有记忆」。** `engram_fetch.rs` 有一条专门的规则和一条
回归测试（`decode_undecryptable_candidate_is_err_not_absent`）：有候选但一条都解不开时返回 `Err`、
**不注入任何段落**——因为渲染「你还没有 core，去建一个」会**诱使 agent 用新 profile 覆盖掉真实
但读不出来的记忆**。这条我们结构上基本免疫（同库同事务，读不到就整轮失败），但**池子为空时的
文案**值得照抄：现在我们池空时是静默的，什么都不说。

**③ 我们这边这两条已经修好了，别再照旧说法办事**（2026-08-17 读代码核实，对应上文的过时记忆）：
`search()` 已从整句 ILIKE 改成关键词切分（拉丁词＋CJK bigram＋短语）＋覆盖度排序；
注入截断已不再静默——`RecallResult.omitted` 会让 prompt 明说「另有 N 条没放进来」。
代码注释里那句话正好是 buzz 同一条教训的另一种表达：
*"A recall named better than it works is worse than a weak one."*

## 7. 待办 / 下一步

已经做掉的不再列在这里（释放算力解绑、「已交付」冻结点、记忆的 core 层、`reject` 之外的
父子传话通道），它们的结果写在上面对应的小节里。剩下的：

**还没解**
- [ ] 改 `cheese accept-request` 的帮助文本，删掉「递卡后平台会自动跑一遍检查，红了卡片会被打回」
      那段（第 3 节差异 ①）——闸门早退役了，**这句话正在让每个芝士以为有兜底而不自己跑测试**
- [ ] `cheese split` 的帮助文本还写着「工作区是从 main 新建的」，而它现在是从**房间的分支**
      长出来的（`_fork_point`）——照它办事会得出错误的边界判断
- [ ] `reject()` 补通知（第 3 节差异 ⑥）——退回一张卡至今不叫醒任何人
- [ ] 「已交付但没人去归档」的默认收敛（第 4 节洞 ⑤）：列表默认折叠，或交付 N 天后自动归档
- [ ] split 时往房间主线写一条 block（第 4 节缺陷 C、第 5 节 ①）——
      这是「一件活是时间线上的一张卡」的数据前提，不是渲染问题

**要拍板**
- [ ] 云 VM 的回收策略：保留到人归档 / 授权额度内芝士自动续租 / 闲置 N 小时释放且提前说一声
      —— 请 @fulu 或 @andy 定。它是唯一按小时烧钱又没有 reaper 的东西

**别的文档里的过期句子**
- [ ] `docs/accept-is-merge.md` 的 Card states 表（差异 ②）
- [ ] `docs/fusion-design.md` §3「我们不拆」（差异 ⑤，已被 places-and-actors 推翻并落地）
- [ ] `docs/spec.md` 里还留着两处与「不嵌套」冲突的旧句子：话题「可以嵌套成树」，
      以及「采纳 = merge = 归档」——采纳早就不归档了
