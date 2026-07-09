# 收敛计划 / merge-readiness（fusion-design §8.5 的落点）

> 目标不是现在做 git merge，而是**达到"可以 merge 进主 repo cheese-backend-py 的水准"**：
> 我们代码为准、质量/完整度达标、命名理顺、路径清晰。本文件定义这个标准并记录达成状态。

## 1. 已达 merge-ready 的部分（P0–P4，全部我们代码、全部 test-green、已上线 dogfood）

| 阶段 | 交付 | 校验 |
|---|---|---|
| P0 | 表情 bug 修复；话题群聊化（成员名册 owner/admin/member、@all/@here 结构化 token） | 前端 53、后端集成 |
| P1 | agent-as-user（芝士=真 user、is_agent 派生自 agent_bindings、不入库）；登录发 JWT；resolve_actor（token>scoped>handle fallback）；组合式授权（token 必要非充分、群是共享单位） | 后端 407→460 |
| P2 | 现场真终端（ttyd 镜像 + WS 反代，tmux 后端下嵌真 Claude Code TUI，sdk 回退 worklog） | 端到端截图 |
| P3 | self-hosted 设备连接器：**他们 frozen cli 零改动拨入我们后端、跑真 turn、hooks 感知回流 + screen 中继**（Phase A 真机实证）；device 域/hub/link.Msg/attribution/DeviceProvider 全我们代码 | 439，Phase A logs |
| P4 | clone agent（transcript-fork，bind-mount 宿主文件直读）；resume 接通；cheese CLI raw-api 逃生口（带 token 不绕鉴权） | 460 |

统一状态：alembic 单 head `d3b8f1a20c11`、ruff/pyright 零错、后端 460 passed、前端 53。

## 2. 收敛方向（我们 ⇄ 主 repo）

- **我们贡献进主 repo**（我们更强/更全）：
  - **hooks 感知**替换他们的读屏 cheeselet（他们最脆弱、补丁最多的一块）——净收益；
  - 完整**产品面**：前端（群聊化 UI/现场/记忆页/市场…）、OpenViking 真集成、质量闸门、算力配额、角色、eval 框架；
  - 话题=群聊模型（我们的选择，非他们的 thread/workitem 拆分）。
- **我们从主 repo 采纳/对齐**（他们已建完、更完整）：
  - 连接器 Phase B 的完整度（viewer WS/adopt/resize/heartbeat/审批页）——我们 P3 是"证明 hooks 路径成立"的骨架，收敛时可用他们更完整的连接器骨架 + 我们的 hooks-cheeselet；
  - agent-as-user 我们已用**务实版**（handle 仍是 authorship 键，未做 handle→user_id 大迁移）——若主 repo 走 user_id，收敛时对齐该数据模型（大迁移单独立项）。

## 3. 命名冲突决议（merge 阻塞项，定死）

两个 `cheese` 二进制：
- **沙箱内 `cheese`**（我们的，平台动作 CLI：`cheese title/doc/ask/api`）——深植 芝士 的 skills/prompt/行为，**保留 `cheese` 名**。
- **连接器 `cheese`**（他们的，用户机器 host：`cheese auth login`/`cheese link`）——收敛时**改名 `cheesehost`**（语义：在用户机器上 host agent screens）。改动面小（Go 模块名 + install.sh + 文档），不碰 芝士 行为。self-hosted 设备上两者遂不撞。
- 附注：两者都有 `cheese api`（我们 P4 加的 sandbox 版 vs 他们 OpenAPI 生成版）——语义一致（打后端 API 带鉴权），收敛时保留沙箱版的 curated+raw 分层即可。

## 4. 达到完整 merge 的剩余 punch-list

- **P3 Phase B 对齐**：现场 viewer WS 路由、session adopt/readopt、viewer resize 透传、heartbeat、cheese-gate attribution 接线（screen 内 `cheese` 调用带 `X-Cheese-Screen` → agent-user 授权）、`/connect` 前端审批页、设备管理路由。→ 收敛时以主 repo 连接器为底 + 嫁接我们 hooks-cheeselet，不重造。
- **权限纪律完全落地（P1 收尾，有产品影响、人门控）**：
  1. 建项目时把 owner seed 为项目成员；背填现有 dogfood 用户为「知是」成员；
  2. 把 `AUTHZ_ENFORCE_TOPIC_ACCESS` 打开（现保守关着避免锁人）。
  这一步会真的开始拦非成员——独立栈验证不误伤后再在 dogfood 开。
- **命名重构**：按 §3 把连接器改 `cheesehost`（在实际 merge 时做）。
- **actual git merge**：两仓真正合流（我们代码为准），是本计划的终点，一次性大动作，独立执行。

## 5. 结论

P0–P4 已建成、test-green、我们代码、上线验证——**核心功能已达 merge-ready 质量**。收敛的剩余是"完整合流"的工程 + 两个人门控的产品动作（开鉴权强制、真 merge），路径已在 §2–§4 定清。fusion-design.md 的分阶段事项至此全部**建成或路径定清**。
