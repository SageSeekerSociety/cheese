## 一句话结论（先行）

Buzz 和我们**产品域高度重合、技术栈完全不搭**。所以值得学的几乎全在「工程组织方式」和「人机协同的产品机制」这两层，而不在代码本身——它的 27.4 万行 Rust 我们一行都抄不了。

## 它是什么

[block/buzz](https://github.com/block/buzz)（Apache 2.0）：自托管的「人与 AI Agent 共处同一房间」的协作工作区。

底座是一个 **Nostr 中继**——消息、reaction、workflow 步骤、review 批准、git 事件，全都是同一条事件日志里的签名事件。人和 agent 的区别只是**换了一个密钥对**，成员关系、审计链、搜索索引完全同构。README 原话：

> Agents have their own keys, their own channel memberships, and their own audit trail. Scoped by identity, not by permission flags — the same way you'd scope a teammate.

这正是我们在做的事（话题 / 分身 / 验收卡 / 活文档），只是它选了「事件日志 + 密钥身份」这条路，我们选了「关系模型 + REST + 平台动作」。

## 结构对照

| | Buzz | CheeseX |
|---|---|---|
| 后端 | Rust，28 个 crate，342 个 `.rs` / 27.4 万行 | Python/FastAPI，326 个 `.py` / 6.8 万行，`backend/app/domain/` 下 40+ 领域包 |
| 分层 | crate 边界（core/db/auth/relay/search/workflow/media/voice/audit…） | route → service → repository → model 四层 |
| 数据 | Postgres + 28 个**手写 SQL** 迁移 | Postgres + Alembic，62 个迁移 |
| 前端 | 主战场是 **Tauri 桌面端**（1836 个 TS 文件）；`web/` 只有 48 个文件（仓库/邀请两个 feature）；另有 Flutter 移动端 371 个 dart | Vue 3 单 Web 端，389 个 `.vue`/`.ts` |
| 测试 | 224 个 crate 内联 `#[cfg(test)]` + 37 个集成测试文件 + 独立的 `buzz-conformance` crate + benchmarks/perf | 288 个测试文件（unit / integration / contract）+ 5 个 Playwright e2e |
| CI | 18 个 workflow（含 4 条 canary、桌面/移动发布线） | 16 个 workflow（部署/备份/心跳/漂移检测这一侧比它厚） |
| 本地闸门 | `lefthook.yml`，**按改动路径分流**，且注释里逐条记录了与 CI 的故意偏差 | <&.claude/scripts/pre-commit>，全量 |
| 给 agent 的文档 | 一个 2.9 万字的 `AGENTS.md`（`CLAUDE.md` 软链过去）+ `ARCHITECTURE.md` 4.5 万字 + 8 个 `VISION_*.md` | <&CLAUDE.md> 刻意精简，程序性知识推到 <&.claude/rules/>（按路径自动加载）和 `.claude/skills/` |
| 给 agent 的工具 | `buzz-cli` + `buzz-dev-mcp`（MCP server）+ desktop-screenshot skill；同时适配 claude/goose/codex/agents 四套工具链 | `cheese` CLI + <&.claude/scripts/> |

## 五个深挖方向

已拆成五个子话题并行开工，每个都在读真源码（不是读 README 猜）：

1. **@架构与领域模型对比** —— 事件日志 vs 关系模型换来了什么、付出了什么；crate 边界 vs domain 包谁更抗腐化；它成文的「怎么加一个新事件类型 / 新 API 端点」配方；多租户社区边界怎么被 `buzz-conformance` 强制。
2. **@Agent 一等公民机制对比** —— 五个面里和我们产品最直接相关的一个。「按身份授权而非按权限开关」在代码里落实到什么程度；agent 能力面（它很宽：开仓库、发补丁、评审、跑 workflow、编排别的 agent）对比我们收敛的动作集；ACP 协议怎么把外部 agent 接进来。
3. **@质量闸门与 CI/CD 对比** —— 最可能出「今天就能抄」的一个面。重点是 `lefthook.yml` 那套**按路径分流的本地闸门**，以及 `scripts/` 下把反复踩的坑固化成机器检查的做法（文件大小检查甚至自带单测）。
4. **@测试策略与可运行性对比** —— 针对我们沙箱无 docker 的真痛点。看它 `start-isolated-test-relay.sh` 一类的隔离手法、种子数据脚本化、以及独立的 conformance 层。
5. **@仓库自解释能力对比** —— 同题不同解：它赌「一个大文件」，我们赌「按路径自动加载的小规则」。这个面要求刻意挑刺，把它那套文档的过期风险也写出来。

## 现在就能说的三条初判

- **别被"很像"骗了**：它是个开源、多端、要给陌生人自托管的产品，所以签名发布、canary、DCO、CLA、许可证门禁这些占了它工程投入的一大块——这些我们大概率用不上。
- **`lefthook.yml` 那套按路径分流的闸门，是最可能直接落到我们身上的东西**。我们现在改一行前端也走全量检查，而它改 `web/**` 只跑 web 的 fix。
- **`scripts/check-*.mjs` 这类自定义检查值得对着我们 <&.claude/rules/> 看一遍**——我们那里记的坑（迁移、后端测试、e2e）现在只是「写给 agent 看的规则」，其中有些完全可以升级成机器强制的检查。

---

*参照仓库已 clone 至各子话题工作区的 `tmp/buzz`（gitignored）。*
