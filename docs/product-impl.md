# CheeseX（知是）· 产品行为 + 实现 Spec

> 这份文档描述**当前真实落地**的产品行为与实现方式（截至 spec-align 第一轮 + 复审 + Batch J）。
> 与 `docs/spec.md`（原始产品愿景）、`docs/evals.md`（验收场景）配套：spec 是"要做成什么"，
> 本文是"现在做成了什么、怎么做的"。每条行为都标注实现位置（`backend/app/...` / 前端组件 / 接口）
> 和**现状**（✅ 完整 / 🟡 部分 / ⛔ 未做）。

---

## 0. 一句话

CheeseX 是"AI 全过程学生项目平台"：每个**项目**是一个 git 仓库（jj colocate），里面用**话题**组织工作，
**芝士**（AI 队友，本体协调 + 分身干活）**在隔离沙箱容器里**（一个房间一个容器，房间里每个话题一个 tmux 会话）用原生工具干活、用 `cheese` CLI 改平台状态，**文档是状态、对话是过程**，
**采纳即归档即 merge**。后端 FastAPI + PostgreSQL，前端 Vue 3 + Vuetify，AI 走 `claude-agent-sdk`
路由到智谱 GLM。

---

## 1. 核心概念与数据模型

### 1.1 三层话题（本体 / 话题 / 分身）

| 概念 | 实体 | `TopicKind` | 说明 |
|---|---|---|---|
| 项目 = 本体 | 根话题 | `root` | 每个项目自动建一个根话题，芝士本体在此协调全局 |
| 话题 | 二级话题 | `topic` | 一条工作线（≈ GitHub PR）；默认挂在本体下 |
| 分身 | 子话题 | `subtopic` | 从话题拆出，芝士分身专注做一件事 |

- 模型：`backend/app/domain/topic/models.py`（`Topic`）。
- **新建话题默认挂到项目根话题下**（`TopicService.create`，Batch A 修），树形：本体 ▸ 话题 ▸ 分身。
- 实现位置：`TopicService`（`backend/app/domain/topic/services.py`）。

### 1.2 万物皆块（双树 + 引用）

`Block`（`backend/app/domain/block/models.py`）是最小单元，同时挂在两棵树上 + 引用列表：

| 字段 | 作用 | 视图 |
|---|---|---|
| `reply_to` | 对话树 | 群聊回复线程 |
| `struct_parent` | 文档树 | 结构化文档（🟡 列已存在，暂未被写入逻辑使用） |
| `refs[]` | 引用 | 决策/结论指回来源（如 `return_conclusion` 写 `refs=[sub_id]`） |
| `upgraded_to_topic_id` | 活引用 | 升级过的块指向其新话题 |
| `kind` | 块类型 | `message`/`doc`/`decision`/`event`/`attachment` |
| `author_type` | 作者 | `human`/`ai`/`system` |

这些字段经 `BlockOut`（Batch A）全部暴露给前端。

### 1.3 话题生命周期

`TopicStatus`：`active`（进行中）/ `archived`（已归档）/ `draft`（草稿）。
**采纳即归档，归档即工作面冻结**（spec §6.3）：归档话题禁止拆子话题、禁止改文档、禁止升级块、禁止写文件、禁止再递验收卡（Batch A/G/J 在 split / edit_doc / upgrade / write_file / create_card 五处统一加守卫）。

### 1.4 项目 = git 仓库（jj colocate）

每个项目在 `workspace_root/<project_id>/` 有一个独立 git 仓库（`backend/app/domain/workspace/service.py`），
用 Jujutsu (jj) colocate（`.git` + `.jj`），默认分支 `main`。每话题 = 一个 jj workspace = 一个 git 分支；
产出（代码/报告/数据）= 文件，由 jj 自动快照成提交。详见 §3.10。

---

## 2. 界面与行为

顶栏（`frontend/src/App.vue`）：品牌 + 导航 Tab（工作台 / 项目总览 / 日历 / 机构看板）+ 项目切换 + 记一笔 + 通知铃 + 用户。
对标产品保真（spec §1）：工作台=Cursor/Replit，对话=飞书群聊，文档=飞书文档，总览/看板=Linear，个人主页=LinkedIn/GitHub，整体=Vuetify Material。设计语言克制：95% 中性灰 + 琥珀橙 `#F57F17` 只点主按钮/激活态/品牌。

| 界面 | 组件 | 行为 |
|---|---|---|
| 工作台 | `views/WorkspaceView.vue` | 左栏(话题树/项目文档/私聊) ｜ 对话 ｜ 实况文档 + 跨区输入栏；栏宽可拖拽(持久化) |
| 对话 | `components/ChatPanel.vue` | 飞书群聊式；只显示 message + 系统行（doc/decision/🔧 事件不混入）；本地时区 |
| 实况文档 | `components/DocPanel.vue` | 飞书文档式；TipTap 编辑器，块手柄(＋插入/⠿ 拖动排序，真功能)；右侧工具可**钉住停靠** |
| 左栏 | `components/TopicSidebar.vue` | 话题树(本体▸话题▸分身) + 项目文档(章程/决策/周报) + 私聊；右缘可拖拽调宽 |
| 项目总览 | `views/OverviewView.vue` | 一页纸总结 + 等你处理的事 + 里程碑 + 话题分布 + 人/AI 贡献 + 成员 |
| 日历 | `views/CalendarView.vue` | 里程碑倒排 |
| 机构看板 | `views/SpaceBoardView.vue` | Linear 表：团队/负责人/AI模式/话题数/活跃/**最近活动**/下个里程碑/状态 |
| 个人主页 | `views/MemberView.vue` | 封面+头像+技能+芝士眼中的TA+参与项目；本项目中：发起/在忙/本周贡献 |
| 项目文档 | `views/ProjectDocsView.vue` | 章程/决策记录/周报集；**在工作台内打开、保留左栏**（docs 模式，Batch f3c631b） |

---

## 3. 功能模块（具体行为 + 实现）

### 3.1 对话与召唤 @芝士  ✅

- **行为**：输入栏发消息，默认**不** @芝士（人与人对话）；点 `@芝士` 芝士才回复。所有消息都会进库；**未 @ 的消息芝士下次被召唤时也会看到**，每条带 `[发言人]:` 标签（多人话题里芝士能分清谁说的，Batch I）。
- **实现**：WebSocket `GET /api/topics/{id}/chat`（`backend/app/api/routes/chat.py`）。
  帧：`user_block` → `delta`*（流式 token）→ `tool`*（工具调用：原生 Bash/Write/Edit/Read + cheese Skill）→ `assistant_block` → `done`（或 `error`）。
  编排：`ChatService.converse`（`backend/app/domain/agent/chat.py`）：tx1 存用户块+拼带标签的上下文+加载记忆 → 流式（不持事务）→ tx2 存 🔧 事件块 + 芝士消息 + token 用量。每话题一把 `asyncio.Lock` 串行。

#### 3.1.1 双实时：活消息(working log) + 实况文档  🟡 目标

@ 后**立刻**冒一条"芝士在看…"的占位消息，它是一条**活消息**——维护一个 **todo 勾选清单** + "此刻在做什么" + 流式答案，**原地不断更新同一条**（不是刷一堆新消息），做完去掉 spinner、留一句小结。

**两层都实时，但是两种不同的实时**（这是过程/状态分离的关键，混成一种会毁掉「文档=稳定状态」）：

- **消息 = 过程：连续流式实时**。todo/状态/流式答案，讲"怎么做"——越快越好、抖动无所谓，token 级跳动。todo 复用 Claude Code 的结构化 **Task 工具**（`TaskCreate`/`TaskUpdate`/`TaskGet`/`TaskList`，v2.1.142 起取代 `TodoWrite`，三态 pending/in_progress/completed；沙箱里芝士原生可用）——平台捕获其事件渲染成活清单，机制同源。
- **文档 = 状态：就绪式实时（离散、整段、不打扰）**。结论/产物进实况文档（§3.4，`cheese doc set`），讲"结果是什么"。**不是逐字流**：每次 `cheese doc set` = 一个自洽的完整版本就刷新一次（架构天然如此——整文件覆盖 + 一次 jj 提交）；回合中途也可多次更新（先计划后结果），只要每次都自洽。**不打断正在读/编辑文档的人**：用"芝士更新了文档 ⟳"的温和提示，别抢光标/别强行滚动重排。
- **分工纪律**：todo/状态留在消息、结论进文档，**不重复**；消息收尾只给一句小结 + 指向文档，不堆全文（避开 Claude Code `track_progress` 结束塞大段 final summary 的"吵"问题）。这正是 §2.2「对话是过程、文档是状态」的双实时落地。
- **现状**：✅ 流式 token（`delta` 累积成一条预览→定稿）+ 现场工具事件 + 实况文档读写/工具事件刷新面板；🟡 待做：@ 秒回占位消息、把进行中消息结构化成 todo+状态(捕获 Task 工具事件)、文档回合中途增量刷新。
- **参考 / prior art**：①范式——Anthropic **Claude Tag**（2026-06，常驻 Slack 的 AI 队友：@Claude、一频道一共享实例多人接力、拆 stages、ambient 盯/催、自排任务跨小时·天、审计日志），与本平台的 @芝士/话题/巡检/分身/现场高度同构，CheeseX 可定位为「Claude Tag for 学生项目制学习，但以文档为中心、git 原生、采纳=merge」（[anthropic.com](https://www.anthropic.com/news/introducing-claude-tag)）。②活消息机制——Claude Code 交互模式单条 tracking comment + `- [ ]/- [x]` 清单原地更新 + Task 工具（[github-actions](https://code.claude.com/docs/en/github-actions)、[todo-tracking](https://docs.claude.com/en/docs/agent-sdk/todo-tracking)）。

### 3.2 芝士（Agent）  ✅ 链路 / 🟡 部分能力

- **是什么**：`claude-agent-sdk` 拉起 `claude` CLI，路由到 GLM（`AgentService`，`backend/app/domain/agent/service.py`）。流式 `include_partial_messages`，`resume` 续会话。
- **在沙箱里跑（spec §9.1）**：`claude` 不在宿主机跑，而是通过 `cli_path` shim（`backend/sandbox/claude-sbx`）进**每话题一个 Docker 容器**里跑——SDK 仍负责流式 + 会话，而 agent 连同它的**原生工具**（Bash/Read/Write/Edit/Grep/Glob）被容器牢笼隔离。容器常驻、跨回合复用（`cheesex-sbx-<topic>`），挂载该话题的 jj workspace（`/work`）+ 持久 session 目录（`~/.claude`）。无 Docker 时退化成无工具的纯模型回合（测试）。
- **平台动作走 `cheese` CLI（不走 MCP）**：改平台状态（设实况文档、记决策、记记忆、拆子话题、发通知/决策请求、递验收卡、回流结论、钉里程碑）一律调容器里的 `cheese` CLI（`backend/sandbox/cheese`），它经 REST 打回后端（`host.docker.internal`）。cheese 是一个 **Claude Code Skill**（`backend/sandbox/skills/cheese/SKILL.md`，挂进 `~/.claude/skills`，`setting_sources=["user"]` + `skills=["cheese"]` 自动发现），不是 system-prompt 大块塞工具。代码/产物则直接用原生工具写、跑。
- **cheese 写接口有 token 鉴权**：`X-Cheese-Token`（每容器注入 `CHEESE_TOKEN`），后端 middleware 只网关 cheese 写路径（`app/main.py:cheese_token_gate`），前端只读这些路径、不受影响。私聊里 `CHEESE_MEMORY_SCOPE=personal` 让 `cheese remember` 写主人个人记忆。
- **记忆注入**：每轮把项目记忆（私聊则个人记忆）+ 实况文档拼进 system prompt。🟡 会话收尾**自动提取**记忆未做；项目话题里加载**成员个人记忆**未做。
- **巡检（本体心跳）**：`POST /api/projects/{id}/heartbeat` / 定时 `scheduler`（§3.8）。走根话题串行锁、注入今天日期 + 里程碑剩余天数（Batch E）。🟡 巡检暂未注入各话题文档/项目记忆、逾期里程碑未入视野（剩余 backlog）。

### 3.3 话题升级 / 拆分 / 回流  ✅ 主干 / 🟡 活引用回写

| 行为 | 接口 | 现状 |
|---|---|---|
| 讨论升级为话题（A1） | `POST /api/blocks/{id}/upgrade` | ✅ 幂等(双击返回同话题)；原块变可点活引用(前端 ChatPanel)；归档话题禁升级；私聊块升级重挂到根(Batch J) |
| 从上往下拆子话题（A2） | `POST /api/topics/{id}/split` | ✅ 建子话题；🟡 发起拆解的 todo 块**未**变成活引用(缺 source_block_id) |
| 子话题结论回流（C4） | `POST /api/topics/{id}/return-conclusion` | 🟡 只往父话题对话追加一条结论块(带 refs)；**未**写回父文档、**未**通知本体 |
| 母子传话（双向，`cheese tell`） | `POST /api/topics/{id}/tell` | ✅ URL 里的 topic 是**发方**，收方在 body（id/`<#id>`/标题/`parent`），只认「父 → 直接子」和「子 → 父」；对方正在跑一轮就直接插进那一轮，否则叫醒它，同期多条合成一轮 |

实现：`TopicService.upgrade_block_to_topic / split_to_subtopic / return_conclusion`、
`app/domain/topic/relay.py`（传话；为什么不能用 `/comments` 见该模块 docstring）。

### 3.4 实况文档（改文档即指令）  ✅ / 🟡

- **行为**：右栏是芝士维护的 markdown 实况文档（状态，不是流水账）；用户可直接编辑，**改了等于给芝士下指令**（芝士下轮读到最新文档）。
- **实现**：`GET/PUT /api/topics/{id}/doc`（`TopicService.get_doc/edit_doc`），doc 块 `kind=doc`。芝士侧用 `cheese doc set` 覆盖式更新。归档话题文档定格(只读)。
- 🟡 未做：编辑文档后对话流出现「编辑了文档」系统事件（edit_doc 已写 event 块，但前端对话流过滤了 ai-event；human/system event 会显示）；AI `update_doc` 与用户编辑的冲突合并（当前后写覆盖）。

### 3.5 验收 / 采纳（状态机）  ✅

- **行为**：成果做完 → 把**验收卡递给一个具体的人**（非广播）→ 对方在"成果待采纳"框点**采纳并归档**（话题归档=PR Merged）或**退回**；可**改验收人**；采纳后可**撤回**（话题回 active）。
- **铁律**：协作模式下 AI 不能验收自己的活（必须人来）；同话题**只允许一张待处理卡**；归档话题不能重复采纳；撤销需身份（原采纳人/owner/组长）；空 `required_topic` 协议条件不再误判全员须导师验收。
- **采纳 = git merge**：采纳时把话题分支合并回 base（best-effort，冲突不阻断归档）。
- **实现**：`AcceptService`（`backend/app/domain/review/services.py`）；接口 `POST /api/topics/{id}/accept-card`、`/api/accept-cards/{id}/{accept|reject|reassign|revoke}`；前端 `WorkspaceView` 合并框。
- 🟡 剩余：合并冲突时仍归档(产物未入 main)、`reviewer_role` 只认 `mentor`、子话题采纳直接合 main 未走父分支。

### 3.6 记忆（项目 / 个人）  ✅ 基础 / 🟡

- **行为**：项目记忆（章程/决策/进展，任何话题可引用）+ 个人记忆（跨项目，记录"芝士眼中的 TA"）。私聊里 `remember` 写个人记忆。
- **实现**：`MemoryEntry`（scope=project/user）、`DbMemoryStore`（`backend/app/domain/memory/`）。当前是 DB 全量加载（spec 的 OpenViking 分层加载为后续）。
- 🟡 剩余：会话收尾自动提取、项目话题里给个人记忆、L0/L1/L2 分层。

### 3.7 通知（分级 / 收件箱 / 拍板）  ✅

- **行为**：通知分 `silent`/`light`/`strong` 三级；铃铛**不显示 silent**、不计未读，`strong` 琥珀强调 + @目标人。广播（无目标人）对所有人可见。
- **决策请求拍板**：`decision_request` 带选项，铃铛里渲染成**一键选项按钮**，点一下即定 → 记 `resolved_at` + `payload.resolved_choice`，并把决策**回流进话题**（芝士下轮看到）。
- **收件箱（等你处理的事）**：决策请求**拍板后**才移出（不是读了就移出）；验收卡进收件箱。
- **分级限流**：每话题每天 ≤2 轻 / 每周 ≤1 强（`NotificationRepository.over_quota`）；**决策/验收请求永不被限流丢弃**（Batch J）。
- **实现**：`NotificationService`（`backend/app/domain/notification/`）；接口 `GET /api/projects/{id}/notifications`、`/inbox`、`POST /api/notifications/{id}/{read|feedback|resolve}`。前端 `App.vue` 铃铛。

### 3.8 里程碑 / 日历 / 调度  ✅ / 🟡

- **行为**：芝士 `cheese milestone` 钉关键节点 → 排进日历、冒泡到机构看板；**逾期里程碑自动转 `missed`**（读时惰性，`MilestoneRepository.mark_overdue`），日历/下个里程碑只显未来项。
- **调度**：`scheduler`（`backend/app/domain/scheduler/`）定时 `tick` → 各项目 `run_heartbeat`。`projects.last_heartbeat_at` 列已加（迁移批，用于"每项目每天一次"，🟡 tick 逻辑待接）。
- **实现**：`MilestoneRepository`、接口 `GET /api/projects/{id}/milestones`、`/calendar`、`POST /api/scheduler/tick`、`/api/projects/{id}/heartbeat`。

### 3.9 仪表盘（总览 / 看板 / 个人主页）  ✅ / 🟡

- **项目总览**（`DashboardService.project_overview`）：一页纸总结（`POST /api/projects/{id}/summary` 由芝士生成）、等你处理的事、里程碑、话题×状态、人/AI 贡献、成员。🟡「风险」板块未做。
- **机构看板**（`/spaces/{id}/dashboard`）：每个团队一行 + **最近活动时间**、人/AI 比例；**停滞按时间判定**（>7 天无活动）。
- **个人主页**（`/users/{handle}/profile`）：跨项目简历。**贡献只算本人 human 块**（排除 system 生命周期块）、发起话题数排除私聊（Batch D）。
- **成员页**（`/projects/{id}/members/{handle}/summary`）：发起的话题 + **在忙的话题** + **本周贡献**（Batch D）。

### 3.10 Git 工作区 + 沙箱执行  ✅

- **行为**：芝士在话题的隔离工作区里用**原生工具**写产物、跑代码/测试（真执行，在容器里）。改动由平台**自动快照**成版本历史（无需手动提交）。**话题=分支=jj workspace=tmux 会话**（容器按**房间**分配，母话题和它派出的 task 共用一个），并行话题互不污染。**采纳=merge**（§3.5）。Git/文件/diff 面板可看。
- **VCS = Jujutsu (jj) colocate**：每个项目主仓 `jj git init --colocate`（`.git` + `.jj` 并存，git 照常可用），每话题一个 `jj workspace`（取代 git worktree）。回合末 `snapshot_worktree` 把原生编辑做成一次 `jj commit`，话题分支用 jj bookmark 导出成 git branch，所以**采纳/diff 仍走 git**（colocation）。`backend/app/domain/workspace/service.py`。
- **沙箱执行**：每**房间**一个常驻 Docker 容器（§3.2），`--memory/--cpus/--pids-limit` 是一份**房间**预算（`SANDBOX_MEMORY_GB`/`SANDBOX_CPUS`/`SANDBOX_PIDS_LIMIT`）；房间里每个话题占一个 tmux 会话，各自的话题 id、回调令牌、`CLAUDE_CONFIG_DIR`、工作目录、预览端口都写在会话环境里（`tmux new-session -e`），互不串。agent 的原生 Bash 在容器里跑，碰不到宿主机。`exec_in_sandbox` 另提供 `--network none` 的一次性执行（强隔离场景）。
- **文件面板（重点：看代码 / 轻量改代码）**：用户很看重**在平台里直接看代码、并能少量改代码**——文档面板的「文件」标签列出**当前话题工作区**的文件树，点开看内容(代码高亮)，未来支持就地小改并自动快照。所以：文件接口必须带 `?topic=`(读话题 worktree,不是空的 base 仓);芝士产物必须写进工作区(`./`)而非 `/tmp`,否则文件面板看不到、也不进版本库。`GET /api/projects/{id}/{files|file}?topic=` · `DocPanel` 文件标签。
- **实现**：接口 `GET /api/projects/{id}/{files|file|git/log|git/diff}`（`files|file|git/diff` 带 `?topic=` 看话题工作区/分支；`git/diff` 拒 ref 选项注入）。

### 3.11 Space / Task Template / Task  ✅ 协议侧 / 🟡 资源侧

- **行为**：机构（Space）发布 Task Template（协议：资源包 + 条件）→ Task；项目**链接 Task = 接受协议**，可**断开**；链接时把模板的默认 agent 类型给这个项目的默认 agent。验收时按协议条件强制（如某话题须导师验收）。
- **实现**：`backend/app/domain/{space,task,project}/`；接口 `POST/DELETE /api/projects/{id}/tasks/{task_id?}`、`/api/spaces/{id}/templates`、`/api/templates/{id}/tasks`。协议强制在 `AcceptService._enforce_protocol`。
- 🟡 资源包（`resource_pack`）只存不发放；多 reviewer 协议、必做话题自动创建未做。

---

## 4. 技术架构

- **分层**：Route → Service → Repository → Model（`backend/app/api/routes/` → `domain/**/services.py` → `repositories.py` → `models.py`）。路由自动发现。
- **后端**：Python 3.13、FastAPI、SQLAlchemy 2.0 async（asyncpg）、PostgreSQL、Alembic 迁移、Pydantic v2。响应封套 `{code,message,data}`，错误用 `app.core.errors`。
- **前端**：Vue 3 + TS + Vite + Vuetify 4 + vue-router + TipTap（实况文档）+ marked/DOMPurify。
- **AI**：`claude-agent-sdk` → `claude` CLI（经 `cli_path` shim 进**每话题 Docker 沙箱**）→ GLM（`ANTHROPIC_BASE_URL`/`AUTH_TOKEN`/`AGENT_MODEL` 在 `backend/.env`，`AGENT_SANDBOX_ENABLED`/`SANDBOX_*` 控沙箱）。平台动作走容器里的 `cheese` CLI（Claude Code Skill）+ token 鉴权；无 MCP。
- **VCS**：Jujutsu (jj) colocate 每个项目主仓，话题用 jj workspace + 自动快照；git 经 colocation 同时可用（采纳/diff 走 git）。
- **测试**：`backend/tests/`（unit/integration/contract），内存 SQLite + StubAgent，行为测试。当前 **121 passed**，ruff/pyright/vue-tsc 全绿。真模型 smoke 脚本 `backend/scripts/smoke_*.py`，真实全流程 `scripts/sim_real.py`。

---

## 5. 实现现状总览

- ✅ **完整**：三层话题树、双树块 schema、对话/召唤/全消息感知+发言者标签、实况文档读写、验收状态机(单卡/归档冻结/撤销鉴权/采纳=merge)、通知分级/收件箱/拍板/限流、里程碑逾期、仪表盘度量、Space/Task 协议链接/断开、项目文档保留左栏、栏宽拖拽、工具钉住、现场(Claude Code 风格)。
- ✅ **沙箱架构**：每话题在隔离 Docker 容器里跑 claude + 原生工具（真代码执行）；平台动作走 cheese CLI（Claude Code Skill）+ token 鉴权，已删 MCP；每话题 = jj workspace（原生编辑自动快照）= 常驻容器里的一个 tmux 会话（跨回合复用），容器按房间共用；采纳/diff 经 colocation 走 git。activity/heartbeat/summary/私聊 全路径统一走沙箱+cheese。
- 🟡 **部分**：结论回流写回父文档+通知本体、拆解活引用(A2)、巡检注入文档/记忆、记忆自动提取/个人记忆入项目话题、总览风险板块、改文档对话事件、资源包发放。
- ⛔ **依赖外部基础设施**：会议 ASR。

> 剩余项的精确清单见 spec-align 复审 backlog（`tmp_review/backlog2.md`，工作区临时文件）。开发/测试/UI 迭代流程见 `docs/workflows.md`。

---

## 6. 接口速查（按域）

```
项目   POST /api/projects · GET /api/projects[/{id}] · GET /{id}/{overview,decisions,private-chat,contributions,summary,usage}
话题   POST /api/topics · GET /api/topics?project_id= · GET /{id}[/blocks|transcript|children|doc|docs|usage]
       PUT /{id}/doc · POST /{id}/{split,return-conclusion} · POST /api/blocks/{id}/upgrade
对话   WS  /api/topics/{id}/chat?token=<会话 token>（必带；连接即认人，消息里的 author 不作数）
验收   POST /api/topics/{id}/accept-card · GET 同路径 · POST /api/accept-cards/{id}/{accept,reject,reassign,revoke}
通知   GET /api/projects/{id}/{notifications,inbox} · POST /api/projects/{id}/notifications
       POST /api/notifications/{id}/{read,feedback,resolve}
里程碑 GET/POST /api/projects/{id}/milestones · GET /{id}/calendar · PUT/DELETE /api/milestones/{id}
成员   GET/POST /api/projects/{id}/members · PUT/DELETE .../{handle} · GET .../{handle}/summary
机构   POST/GET /api/spaces · GET /spaces/{id}/dashboard · POST/GET /api/spaces/{id}/templates
任务   POST/GET /api/templates/{id}/tasks · POST/GET/DELETE /api/projects/{id}/tasks[/{task_id}]
工作区 GET /api/projects/{id}/{files,file,git/log,git/diff} · POST /{id}/{activities,heartbeat,summary}
个人   GET /api/users/{handle}/profile · GET/PUT /api/users/{handle}
调度   POST /api/scheduler/tick
```

（路径前缀以各 router 为准；部分 router 用空前缀并写全路径，见 `backend/app/api/routes/`。）
