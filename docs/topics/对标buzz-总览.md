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
| 本地闸门 | `lefthook.yml`，**按改动路径分流**，且注释里逐条记录了与 CI 的故意偏差 | <&.claude/scripts/pre-commit>，一刀切跑**后端**全量；前端零检查 |
| 给 agent 的文档 | 一个 2.9 万字的 `AGENTS.md`（`CLAUDE.md` 软链过去）+ `ARCHITECTURE.md` 4.5 万字 + 8 个 `VISION_*.md` | <&CLAUDE.md> 刻意精简，程序性知识推到 <&.claude/rules/>（按路径自动加载）和 `.claude/skills/` |
| 给 agent 的工具 | `buzz-cli` + `buzz-dev-mcp`（MCP server）+ desktop-screenshot skill；同时适配 claude/goose/codex/agents 四套工具链 | `cheese` CLI + <&.claude/scripts/> |

## ⚠️ 对标过程中挖出的自身漏洞（优先于「学什么」）

来自 @质量闸门与 CI/CD 对比，我已逐条复核确认。

### 前端类型错误可以一路合并进 main 并部署

- `.github/workflows/` 16 个 workflow 里 grep **不到** `vue-tsc`、也 grep 不到 `eslint`（零命中）
- <&.github/workflows/test.yml> 的 `paths` 只有 `backend/**`
- <&.github/workflows/e2e.yml> 虽然对 `frontend/**` 触发，但只跑 Playwright，不做类型检查
- 前端唯一被碰到的地方是 `build.yml` 里的 docker build，而 Dockerfile 跑的 `vite build` **不做类型检查**

最扎心的是：**工具全都装好了，从来没人调用**。<&frontend/Taskfile.yml> 里 `lint`（ESLint）和 `typecheck`（`vue-tsc --noEmit`）两个任务写得好好的，CI 里一次都没出现。

### pre-commit 是「一刀切跑后端全量」，且没有安装入口

<&.claude/scripts/check.sh> 开头就 `cd "$REPO_ROOT/backend"`——所以改前端也要等 60s 的 pytest，而前端本身一个检查都不跑。更麻烦的是这个 hook **在仓库里搜不到任何安装入口**（全仓 grep `pre-commit` 只命中文档），而 <&CLAUDE.md> 却断言「Tests MUST pass before any commit. Pre-commit hook enforces this.」——这条断言目前没有事实支撑。

### 规则靠自觉 = 规则会烂：本仓反证

<&.claude/rules/backend-tests.md> 白纸黑字写着「别加第九个 `_auth()`」——现在有 9 个。这正好从反面印证了下面第 1 条。

## 值得学的（随子话题回流累积，按 ROI 排）

### 0. 闸门按路径分流 + 偏差写进注释 ⭐ 已确证

Buzz 真正领先我们的是三件事，前两件在这里：

- **按路径分流**：`lefthook.yml` 用 glob 决定跑什么——改 `web/**` 只跑 web 的 fix，不碰 Rust 测试。
- **偏差写进注释**：该文件顶部逐条列出「本地闸门与 CI 的**故意**偏差及理由」。这条纪律比分流本身更值钱——它让「本地和 CI 不一致」从暗坑变成了有据可查的决定。

最划算的单点抄袭是 `scripts/check-branch-skew.sh`（约 30 行）：直接命中我们「并行 PR 把 alembic 迁移链分叉」那个反复踩的坑。

### 1. 文档里的每条硬规则，背后都该配一个可执行守卫 ⭐ 已确证

来自 @仓库自解释能力对比（已回流）。

它的 `check-px-text.mjs` / `check-file-sizes.mjs` 不是躺在 `scripts/` 里的摆设——从 `package.json` → `Justfile` → `ci.yml:192` 一路接进了 CI（逐跳核实过）。规则违反不了，因为机器会拦。

我们的 <&.claude/rules/> 全靠 agent 自觉去读，<&.claude/scripts/check.sh> 只跑 ruff / pyright / pytest。**建议先加三条 grep 守卫**，都是我们文档里已经白纸黑字写死、但没有任何强制手段的规则：

- `tzinfo=None`（<&CLAUDE.md> Datetime 一节明令禁止）
- 遮蔽内建的方法名（`list` / `set` / `dict` / `type`）
- domain 层出现裸 `HTTPException`（约定要用 `app.core.errors`）

> 落地排期：与上方「前端 CI 补洞」「抄 check-branch-skew.sh」同属一次 `check.sh` / CI 改动，合并成一个 PR 做，别分三次改同一个文件。

### 2. Gotcha 该写成「症状 → 会被误判成什么 → 真因」

它 `AGENTS.md` 的 Common Gotchas 一节不写「应该怎么做」，而是从症状倒推。这个写法对 agent 更有用——agent 遇到的是症状，不是规范。

我们 <&.claude/rules/e2e.md> 已经是这个写法，<&.claude/rules/backend-tests.md> 该补齐。成本极低。

### 3. agent 自查 UI 的回路（我们是真缺口）

`just desktop-screenshot` + 截图贴回 PR，且**贴之前用 `shasum` 卡「多张截图哈希相同」**——防的是 agent 截了一堆其实没变化的图充数。

我们只有 e2e 的 pass/fail，agent 改完前端看不见自己改成了什么样。缺口是真实的，但补起来不便宜，待其余子话题回流后统一排序。

## 明确不学的

- **多端签名发布 / canary / DCO / CLA / 许可证白名单**：它要给陌生人自托管，这块占它工程投入一大块，我们用不上。
- **Renovate 自动合并**：我们是盒子上的单 runner，自动合并的依赖 PR 会把 runner 堵死。
- **Hermit**（它的工具链管理器）：我们 uv + pnpm + Taskfile 这套够用，换的收益抵不上迁移成本。
- **8 个 `VISION_*.md` 进仓**：我们没人维护 Status 表，飞书 wiki 策略继续。
- **2.9 万字的常驻大文件**：其中 36% 篇幅是截图流程——改后端的 agent 每一轮都在为这些 token 白付费。
- **`.claude/` `.agents/` `.goose/` `.codex/` 四份逐字节相同的 skill 拷贝**：无同步脚本、无 CI 校验，纯粹的漂移隐患。

**腐烂实证**（这条值得记住）：它 28 个 crate 里有 5 个（`buzz-voice`、`buzz-relay-mesh`、`buzz-conformance`、`buzz-push-gateway`、`buzz-backend-kubernetes`）在 `AGENTS.md` 和 `ARCHITECTURE.md` 里**一次都没出现过**。所以「大文件 = 更完整」是假的——我们把知识拆进按路径自动加载的小规则，在防过期这件事上并不输它。

## 五路进展

| 子话题 | 状态 |
|---|---|
| @架构与领域模型对比 | 进行中 |
| @Agent 一等公民机制对比 | 进行中（五面里与我们产品最相关） |
| @质量闸门与 CI/CD 对比 | ✅ 已回流，结论已并入上方（详见 `docs/topics/对标buzz-质量闸门.md`）。**顺带挖出前端 CI 零检查这个真漏洞** |
| @测试策略与可运行性对比 | 进行中（针对沙箱无 docker 的真痛点） |
| @仓库自解释能力对比 | ✅ 已回流，结论已并入上方 |

## 顺带发现（与 Buzz 无关，但需要拍板）

`docs/` 下有 7 个受版本控制的商业材料文件：`bp.md` / `bp-v2.md` / `bp-brief.md` / `bp.docx` / `bp-v2.docx` / `bp.pdf` / `gen-bp.js`。

这与 <&CLAUDE.md> 的约定直接冲突——「`docs/` 严格只放工程文档，非工程内容（商业计划、市场文案、运营方案、竞赛材料）**绝不可提交**，应暂存 `tmp/` 后上传飞书 wiki」。已发决策请求，未擅自动手。

---

*参照仓库已 clone 至各子话题工作区的 `tmp/buzz`（gitignored）。*

**已知未核实项**（不要当成已确认的事实用）：

- 浅克隆（depth 50）导致无法判断 Buzz 文档的更新频率，故本文未就「它的文档有多新」下任何结论。
- 我们各条 CI/闸门的**实际耗时**没实测过，「60s pytest」是估值。
- jj 环境下 git hooks 到底触没触发，没验证。
- 前端**存量**类型错误有多少个，没跑过 `vue-tsc` —— 补 CI 之前得先跑一次摸底，否则可能一上来就是红的。
- Buzz 的 4 个 canary workflow 没有逐个读。
