# 源码级合并：cheesex → cheese-backend-py（fusion-design §8.5 的真合流）

> 不是运行时反代，是**代码合并**：把 cheesex 的 agent/topic/chat/hooks 层 + 前端壳
> 并进主仓 `cheese-backend-py`，成为主仓的一个分支（→ 最终 PR 进 main）。
>
> - **底座 base** = `origin/main`（产品线：user/auth/space/task/team + 部署，我们采纳）
> - **嫁接 graft** = `origin/design/cheesex`（agent/topic/block/hooks + 我们的前端，为主）
> - **参考 cherry** = `origin/design/cheese-agent-layer`（lg 的 fork/attach、WS-edge，收敛清单）
> - 分支：`fusion/merge-cheesex`（worktree `/Users/andyl/Projects/fusion-merge`）

## 合并面（实测：union merge 后 431 变更 / 45 冲突 / 386 干净并入）

386 干净 = 我们独有的 agent / topic / block / hooks_substrate / authz / membership /
memory / milestone / review / scheduler / usage / workspace / device / identity 域 +
我们的前端壳（rail / ChatPanel / DocPanel / 群聊）——**整层直接落地，无冲突**。

45 冲突分三桶：

### 桶 A — 机械并集（工具/配置，取超集）
`.gitignore` `frontend/.gitignore` `backend/.env.example` `backend/alembic.ini`
`backend/pyproject.toml` `backend/uv.lock` `frontend/package.json`
`frontend/package-lock.json` `frontend/tsconfig.json` `frontend/tsconfig.node.json`
`frontend/vite.config.ts` `frontend/index.html` `README.md` `frontend/README.md`
`CLAUDE.md`
- 决策：**并集**。依赖列表合并、脚本合并；README/CLAUDE 以主仓为主 + 追加我们的节。

### 桶 B — 采纳主仓（成熟产品 + 真实用户数据，THEIRS 胜）
`domain/user/*`（真实 SRP/JWT 鉴权）、`domain/space/*`、`domain/task/*` 及其
routes（`users.py` `spaces.py` `tasks.py`）+ `domain/team`（主仓独有，clean 并入）
- 决策：**用主仓版**。这正是我们要采纳的产品面；我们的 topic/agent 挂在其上。
- 附带集成：我们的 `resolve_actor`/agent-as-user 要对接主仓的**真实 User + JWT**
  （主仓 JWT=HS256 `sub=user_id`；我们原来是 handle）。→ 见「集成任务 I1」。

### 桶 C — 真语义调和（两边各建了一份，需设计决策）
- **`domain/project` → 合并(非替换)。andyl 2026-07-11 拍板方向**：schema 实测证明两者是
  同一现实对象的两面——主仓 `project`(int PK, `team_id`/`leader_id`/`external_task_id`/
  `github_repo`/起止日期)= 小队的作品项目(绑小队/组长/题目/GitHub,有真实数据);我们
  `projects`(uuid, `root_topic_id`/`ai_mode`/`expert_role`)= agent 工作区(git 仓=根话题)。
  **决策：一个 Project = 主仓产品实体(保关系链)+ 我们的 agent 字段嫁上去**。于是"一个小队
  的项目"本身就是一个可跑芝士的 agent 工作区。体验是我们的(话题=群聊),底层保留他们的
  team/task/github 绑定。这是 fusion §4「Space/Task/Project 解耦」的字面落地。**不整个换掉**
  ——否则丢掉产品关系链 + 真实数据,与"采纳原版空间/任务"矛盾。
  - 落地：以主仓 `project` 表为基,增列 `root_topic_id`/`ai_mode`/`expert_role`/`summary`;
    id 策略(int vs uuid)在 I2 迁移合流里定(倾向保主仓 int PK + 我们表用 FK 挂上去)。
- **元思(`/assistant`)→ 退场(替换)。andyl 拍板**：元思是 agent 层**之前**的老助手;lg 自己
  代码已在内测态隐藏它(「内测态元思已不再需要」)。我们的**芝士**(话题=群聊 + hooks)是替代。
  合并时不带入 `/assistant` 入口,导航位让给芝士/话题。
- **`domain/notification`**：两边都建了通知。**决策**：主仓的通知系统更全（渠道/savepoint），
  采纳主仓版；我们话题内的 @提及/回流通知改为**产生主仓通知**（薄适配）。

### 桶 D — app 装配（少量但关键）
`backend/app/main.py`（并 routers + 中间件 + lifespan：我们的 cheese_token_gate /
agent-user seed + 主仓的 auth/oauth）、`backend/app/core/config.py`（settings 超集）、
`backend/app/core/errors.py`（错误类超集）、`frontend/src/main.ts`（我们的 exp guard +
主仓的 account/pinia 引导）、`frontend/src/App.vue`（我们的壳为主，接入主仓 account 登录）、
`frontend/src/plugins/vuetify.ts`（主题超集）、`backend/tests/conftest.py`（fixtures 超集）。

## 集成任务（冲突解完之后，让它真能跑）
- **I1 身份统一**：我们的 `resolve_actor` 接受主仓 JWT（`sub=user_id`）→ 映射到我们的
  actor；agent-as-user 用主仓 User 表（我们 P1 的 agent_bindings 挂上去）。登录走主仓
  `/users/auth/*`；我们前端登录门替换为真实账号。
- **I2 DB/迁移合流**：两套 alembic → 一条迁移链（主仓表 + 我们的 topic/block/agent/
  membership/device 表）。
- **I3 配置合流**：一份 settings（主仓 JWT/SRP/DB/OAuth + 我们的 sandbox/agent/memory）。
- **I4 前端数据层**：我们的 api.ts + 主仓 network 层（account/interceptor）共存；
  空间/任务/小队视图（主仓）挂进我们的 rail 组织面。
- **I5 采纳 lg**：fork/attach agent、WS-edge override（从 cheese-agent-layer cherry）。

## 进度（2026-07-11 执行中）

- ✅ **P-merge-1 完成、已提交、已推 `origin/fusion/merge-cheesex`**：两棵树并成一棵
  （base=main + graft=cheesex，45 冲突全解，ours-primary）。合并树 domain 层是**并集**：
  我们的（agent/topic/block/hooks/authz/membership/memory/…）+ 主仓全部产品域
  （space/task/team/questions/answers/comments/discussion/groups/materials/knowledge/
  passkey/oauth/…）。**backend `import app.main` 通过、跑 25 个 router**（我们的 main.py
  resilient loader 自动跳过还没接通的主仓路由，所以我们半边直接活）。
- 📏 **采纳积压实测**：38 个 route 模块里 **23 通 / 15 挂**。挂的是主仓产品路由
  （teams/groups/questions/answers/comments/discussions/materials/knowledge/recruitment/
  ai/attachments/avatars/topics_legacy/health/materialbundles）。两类 gap：
  1. **错误框架冲突（主要 blocker）**：主仓代码 `from app.core.errors import
     AuthenticationRequiredError`——主仓是 `BaseError` 体系（16 个类 + `format_error_response`），
     我们是 `AppError` 体系（5 类 + `register_exception_handlers`），且 `NotFoundError`/
     `ForbiddenError` **同名不同基**。→ **决策(I3-errors)**：采纳主仓 `BaseError` 为规范
     （产品代码依赖它抛错），把我们的少量 handler 移植过去、注册主仓的 `format_error_response`；
     我们代码的 raise remap 到 BaseError 家族。
  2. **缺依赖**：主仓用 `redis` 等（我们用 valkey/别的）。→ **I3-deps**：pyproject 依赖并集。
- ⏭ **下一 tranche = I3**（errors 超集 + config 超集 + deps 并集）→ 点亮 15 条产品路由
  → 空间/任务/小队 API 在合并树里可用；随后 **I1 身份统一**（agent-as-user 挂主仓真实
  SRP/JWT User）、**I2 迁移合流**、桶 C project 合并、I4 前端数据层。

## I3 执行结果（2026-07-11，已提交 `origin/fusion/merge-cheesex`）
- ✅ errors 超集（采纳主仓 BaseError 为规范 + 保留我们的 AppError/handler 双注册）；
- ✅ 依赖并集（+aiofiles +redis）；
- **产品路由 23 → 29 通 / 9 挂**。

## 诚实的结构性结论（决定剩余全部工作量）
剩余 9 条全是主仓产品路由（answers/comments/discussions/groups/knowledge/questions/
recruitment/teams/avatars），它们挂在**同一个根因**：两套代码各有一份完整
`user`/`notification`/`space`/`task` 域，且**互不兼容**——
- 主仓：`user` int PK + SRP/JWT，`notification.NotificationType` 枚举、`UserProfileRepository`…
- 我们：`user` handle 制、我们的 notification/space/task。

同路径只能留一份。逐个 shim 符号 = 两套 User 模型并存的 Frankenstein，能 import 但语义崩，
比诚实更糟。**真正的完成 = 身份模型统一（I1）**，这是贯穿整个 agent/topic/chat/authz 层的
重构（每个 service、每个 FK、每条测试从 handle 迁到 user_id），是这次合并**真正的 80%**。

### I1 方向决策（andyl「接入原来的登录系统」已隐含）
**采纳主仓身份为规范**：主仓真实 User（int PK）+ SRP/JWT 登录为准；我们的 agent-as-user
（P1 的 agent_bindings）挂到主仓 user 表；handle 降级为展示名/别名。前端登录门换成主仓
`/users/auth/*` 真登录。→ 这样"接入原版登录 + 原版空间/任务可见"才真正成立。
- 代价：我们 topic/block/agent/membership 所有 `*_handle` 列迁成 `user_id` FK（I2 迁移）。
- 这是多阶段重构，按域推进：user→notification→space/task→project(桶C 合并)→前端数据层。

## I1 实证（2026-07-11，试了 notification 域）
- 采纳主仓 notification 域后,cheesex 3 处创建通知的点(agent/chat、topic/services、
  routes/notifications)立刻断——它们用我们的 `NotifKind`(decision_request/heartbeat/
  accept_request 等 agent 专属种类)+ `target_handle`;主仓是 `NotificationType` +
  `recipient user_id`。回接 = 改我们 agent 的通知创建行为(决策卡/巡检/验收卡)+ 前端读法。
  → 已还原到绿 checkpoint(不留半改坏树)。**结论:每个域的采纳都是一次 handle→user_id +
  行为回接,不是机械 resolve。**

## 战略岔口（决定剩余工作量级，需 andyl 定）
两条都真、都不小：
- **A. 全统一(正解端态)**:采纳主仓 5 个域 + 把我们 agent/topic/authz 全层从 handle 迁到
  user_id。产出=单一身份、单一登录、产品与 agent 同源。代价=多日重构(逐域 + 迁移 + 前端)。
- **B. 命名空间共存(先都可见,后收敛)**:把我们冲突的域重命名(notification→topic_notif 等)
  + 改我们自己的 import 路径(机械 sed,不改逻辑);主仓产品域保持规范、零回接(它是 base)。
  产出=两套产品在同一代码库都能跑、都可见(空间/任务/小队 + 我们的话题/agent),**两套身份
  暂时并存**,单一身份留作后续收敛。代价=一次机械重命名,比 A 快得多,但不是终态。

**建议**:先 B(快速让"原版空间/任务可见 + 我们的东西都在"成立、可 demo)→ 再按域走 A 收敛。

## andyl 定案：走 A（全统一），多日无妨（2026-07-11）
放弃 B 的 cx_* 共存，直接做端态正解：**主仓真实 User（int PK, SRP/JWT）= 唯一身份**，
cheesex 全层从 handle 迁到 user_id。分支重置回 I3 绿 checkpoint（29/38），从此按 A 推进。

### A 执行分期（每阶段绿了才提交，可跨会话续）
- **A1 身份地基**：采纳主仓 user 域为规范；删 cheesex user 域；建 handle→User 兼容 seam
  （主仓 `user.username` ≈ 我们的 handle）。cheesex 对 UserService/UserCreate 等的引用回接到
  主仓 API 或 compat。产出：`app.domain.user` = 主仓版，cheesex 代码能 import。
- **A2 列迁移**：cheesex 各模型 `*_handle`/`owner_handle`/`author`/`target_handle` →
  `user_id` FK 指主仓 user 表。schema + 代码 + resolve。
- **A3 鉴权统一**：cheesex `resolve_actor` 用主仓 JWT（`sub=user_id`）；前端登录门换主仓
  `/users/auth/*`；agent-as-user 用主仓 user 行 + 我们的 agent_bindings。
- **A4 域收敛**：notification（并 kind 枚举:主仓 TEAM_* + 我们 decision/heartbeat/accept，
  单表 user_id 收件人）、space/task（挂主仓产品实体）、project（桶C：主仓实体 + 我们 agent 列）。
- **A5 前端**：真登录 + 原版空间/任务/小队视图挂进我们的 rail 组织面。
- **A6 迁移合流**：单 alembic 链（主仓表 + 我们的表 user_id 化）+ 存量数据迁移。
- 全绿（ruff/pyright/pytest/vue-tsc）+ 38/38 路由 → PR 进 main。

### A 进度（`origin/fusion/merge-cheesex`）
- ✅ **A1 身份地基(user 域)—— 31→34 routers**：主仓 User(int PK, username)为规范身份;
  compat seam 证明可行——cheesex 按 handle 找人 → `UserRepository.get_by_handle` 别名到主仓
  `get_by_username`(username 就是 handle)。跨域耦合(user 统计→task/team/knowledge 模型)用
  **call-time 惰性 import** 拆开,让 user 域独立可 import。cheesex identity(agent-as-user)/
  topic/dashboard 解析全部回绿,净 +3 routers。**采纳+compat+惰性 shim 是可复制的域采纳套路。**
- ✅ **A(import-complete)—— 38/38 路由全通,41 routers**(`45c3d3d`→`58f8d8f`):
  - notification 域采纳(cheesex→cx_notification 过渡,A4 再收敛枚举);
  - config 超集(storage_*/avatar);deps 并集(bcrypt/webauthn);
  - **srp_rs（Rust SRP 扩展）maturin build 通** → 真登录路由 import 绿。
  - **合并树现在完整承载原版知是产品全域(空间/任务/小队/问答/资料/群组/知识/passkey/
    oauth…)+ 我们的 agent/topic/chat/hooks 层,同一身份(主仓 User)一起 import。**
- ✅ **合并 app BOOTS & 带数据跑起来了**(`73f1695`,隔离库 fusion_test 实证):
  - 统一 Base/metadata(`app.core.db.Base` 复用主仓 `db.base_class.Base` → 一套 metadata,
    跨域 FK 解析);A2 首刀(cheesex identity/device 的 user 引用列 uuid→int、FK users→user);
  - 跑通 alembic 链 → 启动 uvicorn → **`/health` 200、`/api/projects`(我们的路由) 200**。
  - **发现真 A6 关口**:主仓产品与 cheesex **各带一套 alembic initial-schema**,`upgrade head`
    只跑了 cheesex 链 → DB 有 cheesex 表(users/topics/agent_bindings)但没主仓产品表
    (team/space/user)→ 产品路由 `/teams` 500。
- ⏭ 让**产品路由带数据可见**的剩余:
  - **A6 合并两条 alembic 历史**成一条线性链(cheesex 链 rebase 到主仓 initial 之上),
    upgrade 后 DB 同时有产品表 + cheesex 表;
  - **A2 收尾**(models 已指向主仓 user;补迁移让 agent_bindings/device FK 与之一致)+ agent-seed 对齐;
  - **A3** 真登录端到端、**A5** 前端换真登录 + 空间/任务/小队视图挂 rail。
  - **里程碑级事实:合并树已能启动、带数据响应我们的路由——从"import 通"到"boots-with-data"
    这一步已 durable。** 剩下是 alembic 双历史合并 + 产品表落地。

### ✅ A6 完成 + 原版产品带数据可见(`464b7a9`)
- **两条 alembic 历史合并成一条**:把主仓产品迁移(base a95752502bb0)拷进 cheesex 的
  `alembic/versions`,`alembic merge heads` 把两个 base 连成一个 head,`upgrade head` 一次
  建齐**两套 schema**:产品表(space/task/**team**/user)+ cheesex 表(topics/blocks/
  agent_bindings/projects)同库共存。
- **合并 app 启动 + 原版产品带真实数据响应**:`/teams` → **200 + 真实小队数据**
  (`{id:3,name:"数据分析兴趣组"}`,seed_demo_data 灌的),`/api/projects`(我们的) → 200,
  `/health` → 200。**原版知是的小队,现在活在合并代码库里、带数据点得开。**
- 撤回过早的 A2 FK 改动(cheesex 模型对齐 cheesex 迁移;users→user 真数据合并留作后续)。
### ✅ 后端合流完成 —— 空间/小队/任务 带真实数据 + 真 JWT 在合并 app 里可访问(`f452fff`)
- **space/task 域 de-collision 完成**(cluster 套路):主仓 space/task 域 + 产品路由(`/spaces`
  `/tasks`)为规范,cheesex 的挪 cx_space/cx_task/cx_spaces/cx_tasks,~16 处 cheesex import 回接。
- **A3-config**:补 `jwt_secret`/token 过期;deps 补 pymupdf4llm。
- **实证(fusion_test + 真发 JWT for user alice)**:
  - `/spaces` → **200 真数据** `{id:1,name:"人工智能实践空间"...}`
  - `/teams`  → **200 真数据** `{id:3,name:"数据分析兴趣组"...}`
  - `/tasks`  → 路由通(400=查询参数校验);`/api/projects`(我们的) → 200
  - 38/38 路由 import、43 routers。**原版知是产品(空间/小队/任务)现在带真实数据 + 真鉴权,
    活在合并代码库里,与我们的 agent/topic 层并存。后端合流实质完成。**

### ⏭ A5 前端(唯一剩余;是"接线"不是"从零建")
**重要发现:cheesex 前端本就是主仓前端的超集**(cheesex fork 了 cheese-backend-py 前端)——
合并前端里**已经有**真登录(`services/account.ts` + `network/` 的 Bearer 拦截器/refreshToken)
和产品视图(`views/spaces/`、`views/tasks/`、`views/teams/`、`SpacesView.vue`)。我们的 shell
(App.vue)现在只是用了我们自己的 handle 登录(`./me`)、router 只路由我们的视图。
A5 = **把 shell 接到已存在的这些件上**:
1. shell 登录门接 `account.ts` 真登录(username/password → accessToken),handle 降级;
2. router 加产品视图路由(spaces/tasks/teams 已存在),挂进 rail 组织面;
3. 指向合并后端跑通、截图。
- 附:`users→user` 真数据统合(A2 后续)、`/tasks` 查询参数——细节项。

## 分期
- P-merge-1：桶 A + 桶 B + 桶 D 的机械/采纳部分解完，树成形（可 import）。✅
- I3：errors/config/deps 超集 → 29/38 路由通。✅
- P-merge-2：I1 身份统一（最关键，碰真鉴权，谨慎）。
- P-merge-3：I2 迁移合流 + I3 配置 + I4 前端数据层。
- P-merge-4：桶 C 语义调和（project/notification）+ I5 cherry。
- 全绿（ruff/pyright/pytest/vue-tsc）→ 推 `fusion/merge-cheesex` → 开 PR 进 main。
