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
- **`domain/project`**：我们的 Project = git repo = 根话题（agent 世界）；主仓的
  project = 产品实体。**决策(待定/建议)**：保留我们的 Project 语义为主（agent 层核心），
  但让它可**隶属于**主仓的 space/task（一个产品 task 可落成一个我们的 project/topic 树）。
  这是 fusion §4「Space/Task/Project 解耦」的落点。→ 需 andyl 拍板或我出文档版。
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

## 分期
- P-merge-1：桶 A + 桶 B + 桶 D 的机械/采纳部分解完，树成形（可 import）。
- P-merge-2：I1 身份统一（最关键，碰真鉴权，谨慎）。
- P-merge-3：I2 迁移合流 + I3 配置 + I4 前端数据层。
- P-merge-4：桶 C 语义调和（project/notification）+ I5 cherry。
- 全绿（ruff/pyright/pytest/vue-tsc）→ 推 `fusion/merge-cheesex` → 开 PR 进 main。
