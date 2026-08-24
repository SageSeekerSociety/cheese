# room 与 task 的设计，以及审批流程

> 目标：把「房间（room）／一件事（task）」这个模型，和「从做完到合进 main」这条审批流程，
> 按**代码里现在真的是什么样**和**文档里当初怎么设计的**分别讲清楚，并列出两边不一致的地方。
> 提问人 @fulu，2026-08-17。

## 0. 先排掉一个歧义：「task」在本仓库有三个不同含义

这三个东西同名但毫无关系，讨论时必须先指明是哪一个：

| 说法 | 是什么 | 代码位置 |
|---|---|---|
| **TopicKind.task** | 话题树里「一件活」——带分支、带验收卡，做完就结束 | <&backend/app/domain/topic/models.py> |
| **cx_task.Task** | 机构发布的**题目**（"用 AI 做推荐系统"），属于某个 Task Template（活动/项目集） | <&backend/app/domain/cx_task/models.py> |
| **domain/task/** | 老知是社区的作业任务（team/space 那套），与 CheeseX 话题体系无关 | <&backend/app/domain/task/> |

下文说 task 一律指第一个。

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

- `TopicKind` 有四个值：`root`（项目本体）、`topic`（房间）、`task`（一件事）、
  `subtopic`（`task` 的历史值，老数据用，不再新建）。
- 谁是房间谁是活，由**层级**决定，不是由谁指定的：
  `_child_kind()` —— 根话题的孩子是房间，房间的孩子是一件事。写死在
  <&backend/app/domain/topic/services.py>。
- **不嵌套已经落地**：一件事的孩子还是一件事？不会——因为父不是 root，`_child_kind` 一律给 `task`，
  但拆活入口本身没有禁止在 task 下面再 split（见下面的差异 ④）。
- agent 收到的操作说明按阶段分发：`resolve_stage()`（<&backend/app/domain/agent/stages.py>）
  把房间算成 `delegating`（拆活）、一件事算成 `working`（干活），再据此只注入对应那一段 skill。
  **这是"房间不干活、只拆活"这条规矩唯一的落地方式——靠提示词，不靠后端强制。**

---

## 2. 审批流程（从做完到进 main）

### 2.1 现在实际跑的这一条

```
芝士自己把检查跑绿（这是干活的一部分，平台不替你跑）
  → cheese accept-request <人> --subject '...'   递验收卡，卡直接是 pending
     → 平台立刻用【App 自己的 token】推分支、开 PR（不用任何人的个人 token）
        → GitHub Actions 按 .github/workflows 跑真 CI
           → 人在卡上点「采纳」＝ 授权，不是合并：卡进 pr_open，话题不归档、容器不停
              → 轮询器每 60 秒过四道阀，全过才调 GitHub 合并 API
                 → 合并成功 → 话题归档、释放算力、结果回房间
```

四道阀（都在 `_advance_pr_checks`，<&backend/app/domain/review/services.py>）：

1. **必跑检查名单**：`accept_required_check_names`，默认 `test:backend/**;.github/workflows/test.yml`。
   名单里的检查**没出现 = 还在等，不算通过**。名字后面挂的路径是"对哪些改动才要求它"——
   纯前端 PR 不该等一个只在 `backend/**` 触发的 `test`。等超过 30 分钟不会自动放行，而是**回来找人**。
2. **绿必须绿在当前基线上**：分支落后 main 就自动调 GitHub 的 update-branch 换基，等新一轮 CI；
   连换 3 次还追不上就交给人。防的是"两个各自绿在旧基上的 PR 合起来是红的"。
3. **授权范围没漂移**：人点采纳那一刻的 commit 被冻结成 `pr_authorized_sha`；之后芝士推的每个修复
   都会跟 head 一起动。合并前比对两次的文件清单，新提交越界（比如多碰了迁移、prod 配置）就回来找人。
4. **目标不是 prod**：合进 prod 永远要人自己点，机器不代劳。另外「没有任何 CI 真的跑过这次改动」
   同样不享受免人自动合并。

### 2.2 人在这条流程里的三个动作

| 动作 | 接口 | 含义 |
|---|---|---|
| 采纳 | `POST /accept-cards/{id}/accept` | **授权**："以我的名义送进 CI，全绿且没越界就合" |
| 人工放行 | `POST /accept-cards/{id}/merge-anyway` | 明知没全绿也要合。默认拒绝、显式放行，署名留痕 |
| 作废 | `POST /accept-cards/{id}/void` | 把卡片推进终态，解开 `pr_open`/`conflict` 卡死。**不是放行** |

三条都要登录，且都由 `_forbid_ai` 挡住芝士——协作模式下 **AI 不能验收自己做的东西**（spec §4.4 硬规则）。
撤销采纳（`revoke`）只有原采纳人或项目组长能做，会把话题从归档拉回 active。

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

① **`cheese accept-request` 的帮助文本还在讲闸门**（<&backend/sandbox/cheese>）：
"配了质量闸门的项目，递卡后平台会自动跑一遍检查，红了卡片会被打回"。**这条已经不成立了。**
危害是实打实的：agent 读到它会以为递卡后还有一道检查兜底，于是不自己跑测试就递卡——而现在递卡
直接开 PR，第一次真检查发生在 GitHub 上。**建议改掉这段帮助文本。**

② **`docs/accept-is-merge.md` 的「Card states」那张表从没实现**。文档设计的是
`opening/checks_pending/checks_failed/ready/merged/closed`，实际跑的是
`pending → pr_open → accepted`。文档开头的 Status 段自己承认了这一点，但表还在正文里，容易被当成现状读。

③ **spec §6 说"子话题（三级，可以继续嵌套）"，新设计和代码都是"不嵌套"**。
<&docs/spec.md> 是较早的产品 spec，places-and-actors 明确推翻了它。

④ **"房间不带分支、不走验收卡"是约定，后端不强制**：
`branch_for_topic()` 对任何话题 id 都给一个分支名，不看 kind；`AcceptService.create_card` 也完全不看 kind。
也就是说，给一个房间递验收卡在技术上是能成功的。挡住这件事的只有注入给 agent 的阶段说明。

⑤ **设计里"一件事是房间时间线上的一张卡片"，前端还没做到**。现在 task 仍是左侧树里的节点；
房间时间线上只有一行读时派生的「已派出」标记（<&frontend/src/lib/splitMarkers.ts>），
而且它自己的注释写明：`cheese split` 不往父话题写任何 block，所以"哪几条消息/哪一项待办归了别人"
派生不出来——数据库里没有那条边。

⑥ **fusion-design §3 与 places-and-actors 直接对立**。前者（2026-07）说"队友把群聊和事项拆两层，
**我们不拆**"；后者（2026-08-08）说必须拆，理由就是不拆导致采纳后什么都追不到。
后者是更晚的结论，但前者没标注失效。

⑦ **退回验收卡不通知任何人**（代码核实）：`reject()` 只改数据库那一行，既不发消息也不 summon 芝士
——跟 CI 失败会 summon 的行为不一致。所以每次退回一张卡，必须额外往子话题里发一条评论说明理由，
不能假设对方会知道自己被打回了。

---

## 4. 拟定的两项修改（@fulu 2026-08-17 提出）与必须先补的洞

### 修改一：task 结束 ≠ 归档。归档由人决定；task 与卡共存，卡结束＝task 结束

方向对，且和底层实现是对齐的——**git 层面树本来就是扁平的**：`merge_topic` 把每个话题分支直接
合进 base branch，不存在"合回父话题分支"这回事（<&backend/app/domain/workspace/service.py>）。
所以「一个 task ≈ 一张卡 ≈ 一个 PR」改起来没有 git 结构的阻力。

但有五个洞：

**① "卡结束"必须精确成"卡被采纳"。** 一个 task 生命周期里可以有多张卡：递卡 → 被驳回 →
改完再递。`reject()` 之后话题保持 active，而 `create_card` 的阻塞集里**故意不含** `gate_failed`
——红了作废重递就是正常流程。所以 `rejected`/`revoked`/`void` 都是"卡结束"但绝不是"task 结束"。
这条不写死，语义当场就漏。

**② 归档目前是计费云机器唯一的回收路径。** `_release_topic_compute` 的注释原话：
*"A billed Cloud machine is not [best-effort]: archive is its only reclamation lifecycle,
so accept must not report success if MicroCloud did not accept deletion."*
归档改成人为，等于把真金白银的机器回收交给人的记性。**必须把"释放算力"从归档解绑、挂到卡被采纳上。**

**③ 归档同时是"工作面冻结"的开关，三处在用它**：`create_card`、`split_to_subtopic`、
以及父话题唤醒判断（<&backend/app/api/routes/topics.py>）。采纳不再归档 → 已经合进 main 的 task
还能被继续写、还能再递一张卡，而它的分支已经合掉了，新卡开出来的 PR 大概率是空的
（本项目真实踩过：PR #257 空合并）。**"已交付"这个冻结点必须由采纳自动打上，不能等人。**

**④ 现在没有"已交付但未归档"这个状态。** `TopicStatus` 只有 `active/archived/draft`；
"已采纳"只存在卡上，话题上只有 `archived_at`。建议给话题加 `delivered_at`：由卡 accepted 自动写，
负责冻结工作面＋释放算力＋进度定格；`archived_at` 保持人为，只管"还要不要出现在列表里"。
（另一种做法是话题状态完全由卡推导，但侧栏列表现在只查 topics 表，推导要 join，会变复杂。）

**⑤ 没人会去归档。** 平台没有任何提醒机制，默认结果是列表堆满已交付的 task——把清理成本转给人，
而人不做。需要一个默认收敛：列表默认折叠已交付的，或交付 N 天后自动归档。

**附带**：`revoke()` 的尴尬会被放大。它把话题从 archived 拉回 active，但 **git 层面什么都没撤销**
——PR 已经在 main 上了。改动后一个"还活着"的 task 被撤销采纳，看起来像"还能接着做"，
而代码其实已经上线。文案上要说清：撤销的是这次**验收记录**，不是这次**合并**。

### 修改二：开子话题要慎重（小任务交给 subagent）

同意慎重，但**判据建议换掉**——"任务大小"没有客观界线，agent 每次都得猜。
places-and-actors 里已经有一条更可操作的：**它的产出会不会落进房间里所有人都能看见的公共记录？**
会 → 子话题；不会 → subagent。

这才是 subagent 真正的成本：它的产出只在容器 transcript 里，房间里的人看不见，跑偏了没人能中途
纠正、挂了没人知道。所以"小任务给 subagent"隐含一个赌注——这件事不会失败到需要人介入。而 subagent
最常见的失败恰恰是**自信地返回一个错结论**。所以判据该是"错了要不要有人能看见"。

四个缺陷：

**A. 真正该慎重的不是"开不开"，是简报。** `cheese split` 是单向的：简报发出去之后没有任何办法
追加或修改（只能往子话题里发评论把它唤醒）；子话题看不到父话题的对话历史，只有 `--brief` 原文
＋父话题文档快照。理解不完整就 split，等于把任务甩给一个联系不上的人。
规则该是「简报写不全就不许 split」，而不是「任务小就不许 split」。

**B. 子话题的工作区是从 main 新建的，不是从父话题分支切的**（`_ensure_worktree` 用
`jj workspace add`，没有 parent 参数；spec §6.3 那句"子话题的分支从父话题切出来"与代码不符）。
于是有一条硬判据：**依赖父话题尚未合并的改动的活，绝对不能开子话题**——子话题看不到那些改动，
只会在旧基线上重做一遍或者直接冲突。这条现在没人写在任何规则里。

**C. "人为开"会把瓶颈压回人身上。** 人的注意力是这套系统最稀缺的资源，每次"问人要不要开子话题"
就是一个来回。更好的形式是**默认允许 + 可见 + 可拦**：agent 可以开，但开之前必须在房间里一句话
说清为什么这件事该独立开。而现在恰恰相反——`cheese split` 不往父话题写任何 block，房间里完全无痕，
前端只能读时派生一行「已派出」标记（<&frontend/src/lib/splitMarkers.ts>）。
**要让人能拦，先得让人看见——这是个真实缺口。**

**D. "不嵌套"没有代码强制。** `_child_kind` 里父不是 root 就一律给 `task`，`split_to_subtopic`
只检查归档、不检查 kind，CLI 帮助还明说"没有深度/频率上限"。如果 subagent 覆盖小任务、
子话题只做中等任务，那"在 task 下面再 split"就该直接返回错误，而不是靠 docstring。

## 5. GitHub issue #184（@andylizf）声称的三条改动，落地程度核对（2026-08-17 读代码）

| issue 里说的 | 代码里的实际状态 |
|---|---|
| 闲置容器回收：8 小时闲置、每小时扫 | **完全落地**。`sandbox_idle_hours = 8`、`sandbox_reap_interval_seconds = 3600` |
| 每个芝士有自己的记忆 | **落地且已生效**。后端有 `MemoryScope.agent_project`；在用的 cheese CLI 的 `remember` 确实带 `topic` |
| 任务独立成一层，且**不能再往下拆** | **只落地一半**。kind 和 `_child_kind` 有了；「不能再拆」**没有任何代码强制** |
| 任务该是时间线里的卡片，「下个 PR 处理」 | 一周后仍未做，且**工作量被低估**（见下） |

三点补充：

**① 「任务不能再往下拆」目前只是口头约定。** `split_to_subtopic` 只检查归档、不检查 kind；
`cheese split` 的帮助文本还明写着"没有深度/频率上限"。在一个 task 里再 split 照样成功，
拿到的还是一个 `task`、parent 是 task——树实际上仍能嵌套。要按 issue 说的执行，得让它直接报错。

**② 「任务变成时间线卡片」不是前端活。** 数据层缺一条边：`cheese split` 不往父话题写任何 block
（`SplitIn` 只有 title/created_by/brief），所以父话题时间线上"一件活被派出去"完全无痕。
前端现在只能**读时派生**一行「已派出」标记，而 <&frontend/src/lib/splitMarkers.ts> 的注释自己
划清了界限：能派生"此刻从这个房间派出了 X"，派生不出"这一项待办不归这里了"。
要做的是 split 时写一条 block，不是改渲染。

**③ 记忆分池有个没落点的东西：跨芝士的运维经验。** issue 说公共知识去活文档／决策记录／CLAUDE.md，
但活文档按话题、决策按条、CLAUDE.md 要走 PR。像"沙箱里跑 jj 会把全项目打停"这类经验既不属于
任何一个话题，又太琐碎不值得开 PR，分池之后就只留在踩到它的那个芝士脑子里，别的芝士会再踩一次。
而注入本身有两道上限（50 条 ＋ 20000 字符预算，超了会在 prompt 里明说漏了几条，不是静默），
所以同一条经验被重复写进 N 个池，是在挤占每个池本就有限的预算。**这个缺口值得单独立一件事。**

**④ #184 和「归档改人为」之间有缺口。** #184 解决的是"任务做完不带走房间"；
但"那个任务话题自己怎么收尾"还没人拆——归档至今一肩挑三件事（冻结工作面／释放算力／
计费云机器唯一回收路径），第 4 节那五个洞就落在这里。

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

**① 我们缺「core」这一层。** buzz 只把**一条** `core` engram（agent 自己写的身份/规则/目标）
注进 prompt，其余记忆是 agent 用 `buzz mem` **按需查**的。我们相反：把 50 条平铺事实全量注入
（本项目现在 59 条，注入约 13k 字符，字符预算 20k——`store.py` 的注释里就是拿本项目量的）。
差别不是参数大小，是架构：**「我是谁」必须每轮在场，「我知道什么」应该按需检索**。
我们把两者混在一个池里平铺，于是每轮付全量的钱，还要在预算边界上丢最旧的。

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

**止血（漏了直接出事，建议先做）**
- [ ] **释放算力从「归档」解绑**，改成按闲置释放（容器已经是了：8 小时／每小时扫）。云 VM 另论，
      见第 4 节修改一的洞 ②——现在归档是计费机器唯一的回收路径
- [ ] **「已交付」冻结点**：卡 accepted 时自动冻结工作面（否则合并后还能在已合掉的分支上再递卡，
      会开出空 PR——PR #257 已经发生过一次）
- [ ] 改 `cheese accept-request` 的帮助文本，删掉闸门那段（第 3 节差异 ①）——**它正在误导每个 agent**

**设计（要拍板）**
- [ ] 云 VM 的回收策略：保留到人归档 / 授权额度内芝士自动续租 / 闲置 N 小时释放且提前说一声
      —— 请 @fulu 或 @andy 定
- [ ] `delivered_at` 字段（把「已交付」和「归档」拆成两件事，第 4 节洞 ④）
- [ ] `reject()` 补通知（第 3 节差异 ⑦）
- [ ] 「记忆缺 core 这一层」（第 6.2 节 ①）——与 issue #187 记忆系统三层分工是同一件事，
      建议合并讨论，别单独开

**文档失效标注**
- [ ] `docs/accept-is-merge.md` 的 Card states 表（差异 ②）
- [ ] spec §6「可继续嵌套」／§6.3「子话题分支从父话题切出来」（差异 ③、第 4 节缺陷 B）
- [ ] fusion-design §3「我们不拆」（差异 ⑥，已被 places-and-actors 推翻）
- [ ] `cheese split` 帮助里「没有深度/频率上限」与「任务不能再拆」冲突（第 5 节 ①）
