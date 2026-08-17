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

### 分身身份塌缩：不可追责，且撤权只能等过期

来自 @Agent 一等公民机制对比，三条证据我逐条复核确认：

- <&backend/app/core/sandbox_auth.py>：`mint_scoped_token` 的 payload 只有 `{"p": project_id, "t": topic_id, "exp": ...}`——**没有任何身份信息**，token 说的是「哪个话题」，不是「谁」
- <&backend/app/domain/authz/policy.py>:45：`if not actor.authenticated or actor.is_agent: return True`——agent 直接短路，鉴权只剩 token 的作用域
- 所有分身折叠成同一个 `cheese` user row

后果：**出了事查不到是哪个分身干的；要撤一个分身的权，只能等 `_SCOPED_TTL_S = 3600` 那一小时过期。**

好消息是解法已经在仓库里跑着了：<&backend/app/api/auth.py>:126 的设备屏幕 agent 已经是「每个屏幕一个独立 agent-user」的形态（`resolve_screen_actor` 返回自己的 `agent_handle` / `agent_user_id`）。把这个形态铺到话题分身上即可，**不需要引入任何密码学**。

#### ✅ 阶段一已完成（待采纳）

@分身独立身份与 authz 收敛 已落地：per-topic agent-user（`cheese-<topic hex>`）、scoped token 带 `a=` 身份声明、actor 落地、三处硬编码 `"cheese"` 字符串改成认 agent binding。

#### 而它顺带修的第二个 bug，是我自己一小时前亲手演示出来的

我在催办子话题时用父话题的 scoped token 往子话题发评论，观察到作者被记成 `anonymous`，当时只当作「身份塌缩的活样本」记了一笔。**根因比我以为的严重得多**：

> 越界的 scoped token **校验不通过 → 掉进 Phase-0 fallback → 变成 `anonymous`**；而 `anonymous` 是**未认证**，<&backend/app/domain/authz/policy.py>:45 第一行 `if not actor.authenticated ... return True` 直接放行。
>
> **也就是说：带一个错 token，比不带 token 权限还大。** 而且写入真的落了地，作者被抹掉。

这条把「Phase-0 fallback 保持宽松以兼容旧调用方」这个设计假设**证伪了**——它不只是"宽松"，它是**失败降级到全放行**。已从上游堵死：越界的有效 scoped token 一律 403。

**方法论上值得记一笔**：这个洞不是读代码读出来的，是**在正常使用平台的过程中撞出来的**——我当时只是想催个办。

### 🚨 私聊可被任意已认证用户读取（新发现，未修，main 上是活的）

同一路挖出，**我已独立复核，成立**：

| 事实 | 位置 |
|---|---|
| 群话题创建时 seed roster | <&backend/app/domain/topic/services.py>:127 `self._members.seed(...)` |
| **私聊创建时不 seed** | `get_or_create_private()` → repo 直接 `session.add(topic)`，全程无 seed 调用 |
| 无 roster 即放行 | <&backend/app/domain/authz/policy.py>:53 `return not await roster_exists(topic_id)` |

三条串起来：私聊永远没有 roster → `roster_exists` 恒 False → **任何已认证用户（甚至不必是项目成员）拿到私聊的 topic id 就能读别人的私聊。**

注意 policy.py:52 的注释写的是「No roster exists yet → **legacy** topic, stay permissive」——**这个兼容口子是为老话题开的，私聊是从设计上就永远落在这个口子里的，不是"还没迁移"。**

**这是本轮对标挖出的第三处漏洞级问题，也是唯一一处现在还开着的。** 已列为那一路的下一步第 1 项。

### 活文档会被整块覆盖，且捞不回来

来自 @分身高风险动作的护栏调研。**这是它去查「该不该给分身加权限护栏」时，发现真正的洞根本不在权限上。**

活文档现在是**整块覆盖、无版本历史、无并发控制**。人在面板上 2.5 秒自动保存，分身一次 `doc set` 覆盖上去，人刚写的东西就捞不回来了。

**这是缺版本，不是缺权限**——加确认档挡不住它，加版本历史才行。顺带还修了「人盖人」这个一直存在的问题。

### 规则靠自觉 = 规则会烂：本仓反证

<&.claude/rules/backend-tests.md> 白纸黑字写着「别加第九个 `_auth()`」——现在有 9 个，**算上改名和内联的共 14 个**。这正好从反面印证了下面第 1 条。

**而且时间线是决定性的**：这条规则**是在它以纯文字形式存在的那段时间里被突破的**。不是没写清楚，是**没有执行面**。「规则要么能被机器检查、要么会衰减」这句话，到这里从推论变成了一手实证。

## 两条贯穿主线

### 主线一：规矩该长在机器上，不是长在散文里

四个子话题各读各的源码，最后指向同一件事：**Buzz 把架构铁律写成机器能执行的检查，我们把它写成给人和 agent 看的散文。**

| 同一条规矩 | Buzz | 我们 |
|---|---|---|
| 文案/文件体积规范 | `check-px-text.mjs` / `check-file-sizes.mjs` 接进 CI | —— |
| 多租户列必须存在且打头 | `buzz-db/src/migration.rs:1038`、`:1057` 两个普通单测**解析迁移 SQL** 强制 | <&CLAUDE.md> 里的一段话 |
| 迁移链不许分叉 | ~~`check-branch-skew.sh`~~ → 我们自己写的 `check-migration-fork.py`（已落地，见下） | 原先只有 <&.claude/rules/migrations.md> 里的一段话 |
| 分层不许被穿透 | Cargo 编译期禁环 | <&CLAUDE.md> 里的一段话 |

**反证就在本仓**：<&.claude/rules/backend-tests.md> 白纸黑字写着「别加第九个 `_auth()`」——现在有 9 个一模一样的 `def _auth(handle: str) -> dict[str, str]`。

**而真实数字是 14。** @测试规则还债与 _auth 去重 那一路挖出来、我逐条复核过：

| 形态 | 文件数 | 会被「禁 `_auth` 这个名字」的守卫抓到吗 |
|---|---|---|
| `def _auth(handle: str) -> dict[str, str]` 逐字相同 | **9** | ✅ |
| 改名成 `_login` / `_login_real` | **3** | ❌ |
| 直接内联进 `client.headers` | **2** | ❌ |

（复核命令：`grep -rl mint_session_token backend/tests/integration | wc -l` → **14**）

这张表推出一条独立成立的教训，值得单独记住：

> ### 禁名字的守卫，只抓得住守规矩的人
>
> 躲过散文规则的那 5 份，**恰恰就是已经改名或没起名的那 5 份**。按名字做的守卫会精确地抓住 9 个老实人，放过全部 5 个规避者——**它的命中率和它想解决的问题负相关。**
>
> 正确做法是**盯稀缺资源**：那一路的守卫改成用 `ast` 查 `mint_session_token` 的 import（含 `as` 别名）和属性访问。名字可以改，**这个函数绕不过去**。白名单 3 项各写理由，另加一个测试防白名单自己烂掉。

这也顺带划清了两种守卫的分工，和 #264 合入的 `check-repo-rules.sh` 不重叠：

**grep 能诚实表达的 → shell 脚本；需要看语法结构的 → `backend/tests/` 下的 pytest 守卫。**

### 主线二：给 agent 走旁路，就得为 agent 单独设计一套权限模型

来自 @Agent 一等公民机制对比，一句话说清：

> **Buzz 让 agent 用和人一样的身份、走和人一样的检查，所以它根本不需要为 agent 设计权限模型；我们让 agent 走一条绕过检查的专用旁路，所以权限模型退化成了一个 bearer token 的作用域。**

有意思的是，README 那句 "Scoped by identity, not by permission flags" **字面成立，但原因很朴素**：它 `buzz-auth` 里那 16 个权限 Scope 是**死代码**（`require_scope` 在 crate 之外零调用点，relay 直接发全量 scope），真正拦人的只有「relay 成员 + channel 成员」两层。它不是设计了一套更好的权限系统，而是**没有另设一套**。

它的 agent 有独立密钥对，靠 owner 的 NIP-OA 签名背书准入：owner 是成员 → 名下 agent 全放行；移除 owner → **全部秒失权**。且规范硬性禁止 relay 改写作者身份——背书 ≠ 冒充。

下面「值得学的」几乎全是这两条主线的具体落点。

## 值得学的（随子话题回流累积，按 ROI 排）

### -1. 在 CLAUDE.md 加一节「沙箱能力边界」⭐⭐ 半小时，不需拍板 —— 全场第一优先级

来自 @仓库自解释能力对比（第二次回流，**推翻了它自己的第一版结论**）。

原论点是「我们的文档结构不如 Buzz」。改后的论点更狠：

> **我们最大的缺口不是文档过期，是「能力存在但没有入口，等于不存在」。**

**为什么这比文档过期严重一个量级**：过期文档至少会被读到、会被质疑；没入口的能力**连被质疑的机会都没有**。

#### 反面案例一：`cheese gh-token`（我复核过，成立）

它能读完整 CI 日志。全仓 `.md` 文件里提到它的次数是 **0**（`grep -rn gh-token --include='*.md' .` → 零命中）；唯一写明它的是 <&backend/app/api/routes/topics.py>:216 的一句**代码注释**。agent 要发现它，得先好奇去读一个响应构造函数——**没有任何路径会把 agent 带到那里。**

对照 Buzz：`buzz-cli` 每个能力都有 `AGENTS.md` → skill → `--help` 三层入口。**它的能力发现率是设计出来的，我们的是碰运气。**

#### 反面案例二：工具能力边界没写下来 → 沉默的成功假象

我在本话题的盒子里**独立复现了**最关键的一条：

```console
$ git --version
git version 2.39.5
$ jj git fetch --remote upstream
Error: Git does not recognize required option: porcelain
       (note: Jujutsu requires git >= 2.41.0)
```

**`jj git fetch` 在盒子里根本不能用。** 加上 `gh` 只有 actions/checks 权限（读 PR 冲突文件 403，wangchangxin 实测），合起来就是：**能改能推，推完看不见结果。** #267 已经出现过「汇报推送成功、但分支尖端没有新提交」。

**这类失败不报错，测试和 lint 都拦不住**——只能靠把工具能力边界显式写下来。Buzz 的 Common Gotchas 有一半正是这个类型：记的不是「怎么做对」，是**「这个工具在哪儿会骗你」**。

#### 正面案例：我们自己已经有一个模板

<&.claude/scripts/dev-db.sh>（无 docker 起 PG + Redis，今天跑通 3924 条测试）。**它对的地方不是脚本，是 <&CLAUDE.md> 把这条反直觉能力写进了常驻文档**，还写了 Redis 必需、两个 wheel 为何不进依赖、65 条注定失败的测试别再诊断。照这个写法办。

#### 要写什么

一节，三段：**能做 / 不能做 / 失败长什么样**。必含：

- `jj git fetch` 不可用（git 2.39.5 < 2.41），要同步 upstream 走什么路
- `gh` 的权限边界：actions/checks 可读，PR 冲突文件 403
- **推送后必须回查分支尖端 sha**（`#267` 的教训）
- 查 CI 的正路是 `cheese gh-token` + `gh api`

> **核实边界**：`jj git fetch` 失败和 `gh-token` 零文档提及是我本轮亲手跑的；`gh` 的 403、#267 空推送、3924 条测试是 wangchangxin 实测，我未独立复现，按其事实采信。

### 0. 闸门按路径分流 + 偏差写进注释 ⭐ 已确证

Buzz 真正领先我们的是三件事，前两件在这里：

- **按路径分流**：`lefthook.yml` 用 glob 决定跑什么——改 `web/**` 只跑 web 的 fix，不碰 Rust 测试。
- **偏差写进注释**：该文件顶部逐条列出「本地闸门与 CI 的**故意**偏差及理由」。这条纪律比分流本身更值钱——它让「本地和 CI 不一致」从暗坑变成了有据可查的决定。

> ⚠️ **本节原有一条建议已被推翻，2026-08-11**。原文说「最划算的单点抄袭是 `check-branch-skew.sh`，直接命中 alembic 链分叉」——**这条不成立**。该脚本的判据是两侧 `git diff --name-only` 的 `comm -12`（**文件名交集**），而 alembic 分叉恰恰是两个 PR 各加一个**不同名**的 `versions/*.py`，交集恒为空，一条都抓不到。
>
> 实际落地的是另写的 `check-migration-fork.py`：用 `ast` 解析 `down_revision`（**merge revision 的 `down_revision` 是 tuple，正则会读错**），把 `origin/main` 和工作树两张 revision 图并起来算 head 数。已随 #264 合并。
>
> 教训记在这：**「约 30 行、直接命中」这种判断，是我没实际拿它跑一遍反例就下的结论。** 抄别人的脚本前，先构造一个我们真正怕的场景喂给它。

`lefthook.yml` 的两条（按路径分流 + 偏差写进注释）仍然成立，是本节剩下的价值。

### 1. 文档里的每条硬规则，背后都该配一个可执行守卫 ⭐ 已确证

来自 @仓库自解释能力对比（已回流）。

它的 `check-px-text.mjs` / `check-file-sizes.mjs` 不是躺在 `scripts/` 里的摆设——从 `package.json` → `Justfile` → `ci.yml:192` 一路接进了 CI（逐跳核实过）。规则违反不了，因为机器会拦。

我们的 <&.claude/rules/> 全靠 agent 自觉去读，<&.claude/scripts/check.sh> 只跑 ruff / pyright / pytest。**建议先加三条 grep 守卫**，都是我们文档里已经白纸黑字写死、但没有任何强制手段的规则：

- `tzinfo=None`（<&CLAUDE.md> Datetime 一节明令禁止）
- 遮蔽内建的方法名（`list` / `set` / `dict` / `type`）
- domain 层出现裸 `HTTPException`（约定要用 `app.core.errors`）

> ✅ **已落地（#264，2026-08-11）**：三条守卫做成了 `check-repo-rules.sh`（naive datetime / 方法名遮蔽 builtin / 领域层裸 `HTTPException`），带 `--self-test` 接在 `repo-guards.yml` 上——**先自证再判树**。这个 `--self-test` 的形状值得记住：和「agent 能力发现」那一路的「拿改动前的文件跑同一断言」是同一个东西——**守卫必须先证明自己会红，绿才有意义。**

### 1.5 把架构铁律下沉成「解析迁移 SQL 的 lint 单元测试」⭐ 约 1 人日

来自 @架构与领域模型对比。这是上一条的**高配版**，也是整轮对标里单点价值最高的一条。

Buzz 用两个**普通 Rust 单测**（`buzz-db/src/migration.rs:1038` 和 `:1057`）强制「每张业务表必须有 `NOT NULL community_id`；主键 / 唯一键 / 外键必须以它打头」，白名单只有 11 张、写死在代码里。**这个机制和 Nostr 一点关系都没有**，我们能照抄一个小号版：

- `TIMESTAMPTZ` 铁律（<&CLAUDE.md> Datetime 一节，现在只是散文）
- 外键必须有索引
- 软删列命名统一

62 个存量迁移先进白名单豁免，新增迁移一律受检。规矩从 <&CLAUDE.md> 里的一段话变成 CI 里的红叉。

### 1.6 禁止跨域直接 import 别人的 `repositories` ⭐ 约 0.5 人日 + 还债

同样来自 @架构与领域模型对比，附实测数据：我们 42 个领域包里 **26 个处在同一个强连通分量中、20 对双向依赖**。

但拆开 224 条跨包 import 看，结论比这个数字乐观得多：

| 被 import 的层 | 条数 | 判断 |
|---|---|---|
| `models` | 77 | ORM 关系，难免 |
| `repositories` | 77 | **真问题** |
| `services` | 16 | 且子图**无环** |

也就是说**业务逻辑层其实还算干净**，环几乎全是「20 个 `services.py` 直接调别的领域的 repository」造成的——纯纪律问题，不是设计烂。加一条 lint + 存量白名单就能止血。

### 1.7 鉴权决策收敛成策略表 ⭐ 约 3–5 人日

<&backend/app/domain/authz/policy.py> 现在只有 67 行、只覆盖 topic，还带两个宽松档（**未认证放行、无 roster 放行**）。建议扩成「域 × 动作 × actor → 允许/拒绝」的纯函数策略表。

注意**不要**照搬 Buzz 的「所有写入过同一个 `ingest` 入口」——我们是 REST 语义，强行统一写入口是倒退。可学的只是「鉴权决策集中在一处、可穷举、可测试」。

### 1.8 给每个话题分身独立的 agent-user 身份 ⭐ 主线二的落点

对应上方「分身身份塌缩」那条漏洞。收益三样：

1. **可追责**——日志里能看出是哪个分身干的
2. **撤权从「等 1 小时过期」变成「删一行」**
3. **能拆掉 <&backend/app/domain/authz/policy.py> 里的 `is_agent` 短路**，让分身走和人一样的检查（这正是主线二说的事）

不需要引入密码学，形态在设备屏幕 agent 上已经跑通，属于铺开而非新建。

### ~~1.9 提案式高风险动作~~ ❌ 已被推翻

@分身高风险动作的护栏调研 查完给了否定结论，**这条不学**：

- **「提案 → 人确认」我们已经有，而且比它硬**：代码走 分支 → 验收卡 → 闸门 → 人采纳，还有一条「AI 不能验收自己做的东西」的规矩。Buzz 只是让 agent 在画布上 draft 一下。
- **它的「画布只能 draft」不该抄到活文档上**。类比不成立：Buzz 的画布是人的协作产物，而我们的活文档**同时是分身的状态摘要和跨轮记忆**（子话题的 split 简报就是活文档）。给它加确认档 = 掐死分身自主干活。

保留这条记录，是因为「哪些对标发现经查是错的」和「哪些是对的」一样值钱。

### ~~1.10 `_Stop` 钩子~~ ⚠️ 已改形

想法成立，**但不该走钩子层**：我们的远程后端是 spool-only，没法同步 block，硬做会让本地/远程分叉。

改成：**一轮结束后按 stage 查硬事实，不满足就再 summon 一次**。零新增管道。

### 1.10b 「facts decide, timers are a last-resort backstop」——排查结果：我们很干净

系统扫过了：**产品代码干净**，找到的两处计时器用法反而是**正面范本**。

唯一一条真问题在 <&deploy/deploy.sh>：`sleep 5` 之后只探一次健康检查。影响是**误报**（说部署失败其实成功了），不是误动作。

> ⚠️ 归属待定：`deploy/` 属 CI/CD territory，已交给外部 agent 的范围边缘。见下方分工表。

### 1.11 让 agent 知道「有什么能用」：把需要同步的东西降到最少 ⭐

起因是一个具体痛点：**我们的分身连 jj 都不会用。**

去查 Buzz 怎么解的，答案有点反直觉——**它没有「同步机制」，它是把需要同步的东西降到最少**：

1. **CLI 自己描述自己**：`buzz-cli` 用 clap derive，`crates/buzz-cli/src/lib.rs:176` 那个 `enum Cmd` 每个变体头上的 `///` 注释**就是** `--help` 文本。这一层**永不过期，因为它和代码是同一份东西**。
2. **散文明确划界**（枢纽所在）：`.claude/skills/sprout-cli/SKILL.md` 写着 —— *"This skill documents only what `--help` cannot tell you."* 散文只写 `--help` 说不了的：语义、坑、输出契约、什么时候**不**该用。所以它短，加个 flag 也不会让它过期。
3. MCP server 的工具 schema 从代码生成。

**它的同步机制其实不存在，别去抄**：grep 整个 `Justfile` / `.github/workflows/` / `lefthook.yml`，**零条**校验 `AGENTS.md` / `ARCHITECTURE.md` 的机制；四份 SKILL.md 逐字节相同（md5 验证）且无同步脚本。

对照出我们两个洞：

| 洞 | 证据 |
|---|---|
| **jj 从来没写过** | <&CLAUDE.md> 零命中、<&.claude/rules/> 零命中。更糟：环境给 agent 的信号是 `Is a git repository: false`，它收到的是「这儿没有版本控制」 |
| **cheese CLI 的 `--help` 是反过来的** | 顶层很好（每个子命令都有中文描述），**子命令层几乎是裸的**：`split --help` 不说 `--brief` 该写什么、分身在什么环境跑；`conclude`/`remember`/`recall`/`milestone`/`ask` 全是裸参数名 |

**一句话**：能永不过期的那一层（`--help`）是空的，容易过期的那一层（散文）在扛所有事。

#### ✅ 已做（@agent 能力发现：补 jj 与 CLI 自描述，待采纳）

补了三层：

1. **cheese CLI**：18 个子命令原本**全无 description**，`--help` 只有裸参数名。现在每个都写了语义 / 坑 / 什么时候**不**该用。重点是 `split`——分身看不到对话历史、工作区是从 main 新建的、gitignored 的东西不跟过去、简报不必重复父话题文档（平台自动附快照）。
2. **jj 进 <&CLAUDE.md> 而非 <&.claude/rules/>**，理由站得住：rules 按路径触发，而 `Is a git repository: false` 这个误导信号**在碰任何文件之前就到了**，路径触发太晚。写成「症状 → 误判 → 真因」的紧凑表。
3. **立了分界**：散文只写 `--help` 说不了的。

**守卫**：`backend/tests/unit/test_cheese_cli.py` 加两个测试（每个对外子命令必须有 description、每个参数必须有 `help=`）。**拿改动前的文件跑同一断言，18 个全部命中——绿是修出来的，不是本来就绿。** 这个验证姿势值得当范本。

简报里我标「没验证过」的那条有结论了：**CLI 源码在 `backend/sandbox/cheese`，能改**，不用降级成建议文案。

**故意没补的**（判断合理，记下来）：`.agents/skills/lark-*` 的漂移隐患（另见下方待拍板）、「Pre-commit hook enforces this」不实断言（在外部 agent 手上）、以及一堆「知道了更好但不知道也不会做错事」的（比如 Taskfile 有哪些 task）——**不写进去是对的，那是大文件膨胀的起点**。

### 2. Gotcha 该写成「症状 → 会被误判成什么 → 真因」

它 `AGENTS.md` 的 Common Gotchas 一节不写「应该怎么做」，而是从症状倒推。这个写法对 agent 更有用——agent 遇到的是症状，不是规范。

我们 <&.claude/rules/e2e.md> 已经是这个写法，<&.claude/rules/backend-tests.md> 该补齐。成本极低。

### 3. agent 自查 UI 的回路（我们是真缺口）

`just desktop-screenshot` + 截图贴回 PR，且**贴之前用 `shasum` 卡「多张截图哈希相同」**——防的是 agent 截了一堆其实没变化的图充数。

我们只有 e2e 的 pass/fail，agent 改完前端看不见自己改成了什么样。缺口是真实的，但补起来不便宜，待其余子话题回流后统一排序。

### 4. 不吹保证的写法

Buzz 的 `tenant.rs` 注释里直说自己是 **"lint-and-review fence, not a compiler fence"**——`resolved` 字段是 `pub`，故意绕仍然能绕。

把「这道防线到底防不防得住」写清楚，比宣称「已隔离」诚实得多。对照我们 <&CLAUDE.md> 那句「Pre-commit hook enforces this」（实际上 hook 没有安装入口），这个对比很刺眼。零成本，改措辞而已。

## 明确不学的

- **多端签名发布 / canary / DCO / CLA / 许可证白名单**：它要给陌生人自托管，这块占它工程投入一大块，我们用不上。
- **Renovate 自动合并**：我们是盒子上的单 runner，自动合并的依赖 PR 会把 runner 堵死。
- **Hermit**（它的工具链管理器）：我们 uv + pnpm + Taskfile 这套够用，换的收益抵不上迁移成本。
- **8 个 `VISION_*.md` 进仓**：我们没人维护 Status 表，飞书 wiki 策略继续。
- **2.9 万字的常驻大文件**：其中 36% 篇幅是截图流程——改后端的 agent 每一轮都在为这些 token 白付费。
- **`.claude/` `.agents/` `.goose/` `.codex/` 四份逐字节相同的 skill 拷贝**：无同步脚本、无 CI 校验，纯粹的漂移隐患。
- **单一签名事件日志 + `kind` 作唯一扩展点**：它换来的三样（老客户端不 break、统一搜索、统一审计）我们要么不需要、要么已有替代；代价却是实打实的——`ingest_event_inner` 约 1100 行直线校验，每加一个结构化 kind 还要配 100+ 行 envelope 校验器。
- **28 个 crate 的切法**：它边界不腐化的真正原因是 **Cargo 编译期禁环**，不是切得细（体量差 150 倍：relay 6.4 万行 vs search 429 行）。Python 抄不到禁环，抄形状只会多一堆包。
- **Host → 租户的多租户模型**：它是「一个 URL = 一个社区」，community 从 Host 解析，四层保障（类型 / schema 复合主键 / CI lint / 不可变触发器）+ TLA+ 规约。我们的 space/team/project 不是租户、是授权域，**跨 space 协作是我们的产品特性**，那套会直接把它挡死。隔离强度我们结构性弱于它，但这是选择不是缺陷——只学它第 1.5 条那个 lint 机制。

- **Nostr 密钥体系**：它干净的前提是「自托管 + 用户自持私钥」。我们是托管平台，照搬只会得到伪去中心化。
- **「不留管理通道」的 M1 公理**：那是 agent 跑在**用户自己的 k8s 上**逼出来的。我们的自托管沙箱直连控制是**资产**，不是债。
- **消息即事件的同室模型**：代价是它至今没修的 A→B→A 回复循环。我们的话题/子话题拓扑**用结构消灭了这类循环**，是更强的保证——这一条我们领先。
- **110 个 CLI 子命令的堆法**：它的动作面长在**内容**上（发消息、编画布、开频道），我们的该长在**协作过程**上（拆活、验收、回流、钉里程碑）。方向不同，不是数量差距。

**顺便戳破两个说法**：

其一，README 宣称的 8 项 agent 能力，逐条核实下来：开仓库 / 发补丁 / 审 PR / 跑工作流**为真**；编画布为真但只有 `get`/`set` 两个命令；「编排其他 agent」**打折**（只能提案）；「进语音 huddle」在 agent CLI 里**根本没有入口**。

其二，Buzz 并不是纯事件溯源。`channels` / `users` / `thread_metadata` 都是**派生投影表**，由 `side_effects.rs` 手工维护（19 处 db 写），删除是 soft delete（置 `deleted_at`）。所以「不可变审计链」是打过折的——别把它当成我们该追赶的标杆。

**腐烂实证**（这条值得记住）：它 28 个 crate 里有 5 个（`buzz-voice`、`buzz-relay-mesh`、`buzz-conformance`、`buzz-push-gateway`、`buzz-backend-kubernetes`）在 `AGENTS.md` 和 `ARCHITECTURE.md` 里**一次都没出现过**。所以「大文件 = 更完整」是假的——我们把知识拆进按路径自动加载的小规则，在防过期这件事上并不输它。

## 落地清单（建议顺序，待人拍板）

| # | 动作 | 估工 | 备注 |
|---|---|---|---|
| **-1** | **<&CLAUDE.md> 加一节「沙箱能力边界」** | 半小时 | **现在的第一优先级**，不需拍板。见上方 -1 节 |
| ~~0~~ | ~~补前端 CI 检查（`vue-tsc` + `eslint`）~~ | — | ✅ **已落地 #264**，见下方「已落地」 |
| 1 | 迁移 SQL 的 lint 单测（TIMESTAMPTZ / 外键索引 / 软删命名） | 1 人日 | 62 个存量迁移白名单豁免。**仍未做**——#264 的 `check-migration-fork.py` 只管链分叉，不管列约定 |
| ~~2~~ | ~~抄 `check-branch-skew.sh` 防 alembic 链分叉~~ | — | ❌ **建议被推翻**（文件名交集抓不到）→ 改为 `check-migration-fork.py`，✅ 已落地 #264 |
| ~~3~~ | ~~三条 grep 守卫（`tzinfo=None` / 遮蔽内建 / 裸 `HTTPException`）~~ | — | ✅ **已落地 #264**（`check-repo-rules.sh`，带 `--self-test`） |
| 3.5 | **给每个话题分身独立 agent-user 身份** | 中 | 漏洞级。形态已在设备屏幕 agent 跑通，铺开即可；做完才能拆 `is_agent` 短路 |
| 4 | `repositories` 跨域 import lint | 0.5 人日 + 还债 | 20 对双向依赖要还 |
| **P0** | **活文档版本历史 + 乐观并发** | 一个迁移 + 约 50 行 | **数据丢失级**。工作流不变，顺带修「人盖人」。已发决策请求给 andy |
| P1 | `split` 加深度/频率上限 | 小 | `split` 是唯一会生出另一个 agent 的动作，今天的兜底是「额度花完」 |
| 4.6 | `_Stop` 想法改形：一轮结束后按 stage 查硬事实再 summon | 小 | 不走钩子层（远程 spool-only 会分叉） |
| ~~4.5~~ | ~~高风险动作降级成提案~~ | — | ❌ **不做**，经查我们已有更硬的机制 |
| — | <&deploy/deploy.sh> 的 `sleep 5` 后单次探活 | 小 | 误报非误动作。**归属待定**，见分工表 |
| ~~5~~ | ~~<&.claude/rules/backend-tests.md> 改成「症状→误判→真因」写法~~ | — | ✅ **已做**（六条症状，照 `e2e.md` 的写法），**待采纳**。连带 14 处 token 铸造收敛进 `tests/integration/conftest.py`，加了 `ast` 守卫 |
| 6 | <&CLAUDE.md> 里没有事实支撑的断言改成实话 | 极小 | 「pre-commit enforces this」等 |
| 7 | authz 策略表 | 3–5 人日 | 收敛两个宽松档 |
| 7 | authz 策略表 | 3–5 人日 | 收敛两个宽松档；与 3.5 同属 authz 改造，宜连做 |
| — | 其余（事件日志 / crate 切法 / Host 租户 / Nostr 密钥 / M1 公理 / 同室模型 / 多端发布 / canary / Renovate / Hermit / VISION 进仓） | **不做** | 理由见上 |

### ✅ 已落地：CI 那一路全部合进 GitHub main（2026-08-11）

交接给外部 agent 的那批（见 <&docs/topics/CI补洞-交接简报.md>）已执行完毕，三个 PR：

| PR | 内容 |
|---|---|
| **#264** | `frontend.yml`：ESLint 零错误 + `vue-tsc` 走 **ratchet**（`tsc-baseline.json` 冻结 31 个存量错误、分布在 9 个文件，**只许降不许升**）。顺带修了 `AuditTask.vue` 一个真 bug（`catch` 少了绑定变量）。同 PR 还有 `check-repo-rules.sh` 与 `check-migration-fork.py`，都带 `--self-test`，接在 `repo-guards.yml` 上 |
| **#265** | 补 `cli/` 的 Go 闸门（`gofmt`/`vet`/`build`/`test -race`，**此前 Go 代码零 CI**）；33 个 job 全部补 timeout 和最小权限；concurrency key 改成 **PR 按 ref、main 按 sha**——**这条正是「最近两次 accept 没能实际部署」的根因** |
| **#266** | 17 个 action 全部钉到 40 位 commit SHA + 防回退守卫。理由硬：`build`/`build-tmux`/`deploy-dev` 跑在自建 runner，也就是 **dev 和生产机器本身** |

三条值得单独记住的做法：

1. **ratchet 而不是一刀切**：31 个存量错误冻进 baseline，只许降不许升。这正好是交接简报里担心的「别让新 job 一上来就是红的」的更好解——比 `continue-on-error` 强，因为它**真的会拦新增**。
2. **`--self-test`**：守卫先证明自己会红，再去判树。
3. **concurrency key 的 bug 是被这轮顺带抓到的**，不是这轮的目标——补闸门的副产品是发现了一个一直在偷偷吃掉部署的真 bug。

### 分工（2026-08-11 起）

**外部 agent 负责**：已完成，见上。**动 `.github/workflows/` 和 <&.claude/scripts/check.sh> 的只有他一个。**

**本话题负责**，已拆四个子话题并行：

| 子话题 | 对应清单项 | 性质 |
|---|---|---|
| @分身独立身份与 authz 收敛 | 3.5 + 7 | 漏洞级，风险最高，三阶段严格顺序 |
| @领域包解环与 import 守卫 | 4 | 守卫做成 `backend/tests/` 下的测试，绕开 `check.sh` |
| @测试规则还债与 _auth 去重 | 5 + 9 个 `_auth` 去重 | 小而实 |
| @分身高风险动作的护栏调研 | 4.5 + 4.6 + 计时器排查 | ✅ **已回流**。给了否定结论 + 挖出活文档 P0 |
| @agent 能力发现：补 jj 与 CLI 自描述 | 1.11 | ✅ **已回流，待采纳**。18 个子命令补齐 description + 两个守卫测试 |

**防撞车约定**：四个子话题都被明令不碰 `.github/workflows/` 和 `check.sh`；需要加守卫的一律实现成 `backend/tests/` 下的测试（`check.sh` 本来就跑 pytest，效果等价）。

**待明确的归属**：<&deploy/deploy.sh> 的 `sleep 5` 单次探活。`deploy/` 严格说不是 workflow，但属 CI/CD 范畴，且 <&CLAUDE.md> 规定盒子操作只走 `deploy/deploy-docker.sh`。**在人拍板前双方都不动它。**

## 五路进展

| 子话题 | 状态 |
|---|---|
| @架构与领域模型对比 | ✅ 已回流（详见 `docs/topics/对标buzz-架构与领域模型.md`）。**贡献了单点价值最高的迁移 lint** |
| @Agent 一等公民机制对比 | ✅ 已回流（详见 <&docs/topics/对标buzz-agent机制.md>）。**挖出分身身份塌缩这个第二处漏洞** |
| @质量闸门与 CI/CD 对比 | ✅ 已回流，结论已并入上方（详见 `docs/topics/对标buzz-质量闸门.md`）。**顺带挖出前端 CI 零检查这个真漏洞** |
| @测试策略与可运行性对比 | ⏸ **卡住**：容器被重建 + AI 服务报错，`running=false`，无产出。**唯一还没给过任何结论的一路** |
| @仓库自解释能力对比 | ✅ **已两次回流**。第二次**推翻了自己的第一版结论**，贡献了现在的第一优先级（见 -1 节） |

### 后拆的五路 + 收口动作（2026-08-11）

| 子话题 | 状态 | 收口动作 |
|---|---|---|
| @分身高风险动作的护栏调研 | ✅ 已回流（否定结论 + 挖出活文档 P0） | 无需再动 |
| @agent 能力发现：补 jj 与 CLI 自描述 | ✅ 已回流，产出在自己工作区**待采纳** | 等验收 |
| @分身独立身份与 authz 收敛 | ✅ **阶段一已完成**（催办后当轮回流），另修两个真 bug，**待采纳**。阶段二卡在一个事实缺口，见下 | 已催递卡；私聊漏洞已单独发通知 |
| @领域包解环与 import 守卫 | 🔄 深度进行中（302 blocks），当前不在跑 | 已催：守卫能单独成立就先递卡，还债还到哪算哪 |
| @测试规则还债与 _auth 去重 | ✅ **已回流**（催办后当轮完成）。两件都做完，ruff 干净 / pyright 0 / 3924 passed / 0 failed，**待采纳** | 已催它补递验收卡 |

#### authz 那一路：阶段二卡在一个**事实缺口**，不是工作量

> 老话题的分身座位是**「下一次开轮时」就地迁移**的。存量话题里还有多少没迁，**没人知道这个数**。
> 这个数没查清就拆 `is_agent` 短路，**未迁移话题的分身会当场失去访问权**。

这是个漂亮的自我拦截：它没有硬着头皮往下做，而是**指出了下一步依赖一个尚不存在的事实**。查清之前，阶段二要么不做，要么必须带灰度开关。**查这个数需要生产库访问权，子话题和我都没有——这是需要人接的一步。**

它的下一步排序（按急迫度）已并入本文：**1) 修私聊鉴权口子（见上方 🚨）→ 2) 查存量迁移率再定阶段二 → 3) 收掉未认证 fallback。**

#### 顺带定案了四条语义（已记入决策记录）

这四条以前是散在各处的默契，现在写死了：

- **roster ⊆ member**
- **`@` 只通知，不授权**
- **读权限的门是「项目成员」；roster 管的是群播范围和名单管理权**
- **私聊必须单独判定**（正是上面那个漏洞的正解）

它另修的第一个 bug 也来自这里：`@` 提及在**空名单**下把「我没有名单」误判成「你不是项目成员」——私聊和掉线补录两条路都中招，**补录路径还静默丢通知**。又一个「失败降级」的例子：空集合被当成了否定答案。

#### 这一路留下两个必须有人接的尾巴

1. **它的分支基于旧 main，需要 rebase。** 而 `jj git fetch` 在沙箱里不可用（见 -1 节）——**这正是「沙箱能力边界」那一节要防的场景，第一次真实撞上。** 采纳时要留意。
2. **`check-repo-rules.sh` 的头部注释会变成假话。** #264 里那句注释拿「现在有九份 `_auth`」当**现行证据**；这一路落地后就只剩 0 份了，注释需要改成指向新的 `ast` 守卫测试。**这条属于 CI 文件，归外部 agent，我不动。**

**收口原则（wangchangxin 定的）**：能递卡的递卡，递不出的说清楚卡在哪，**不要无限期 active**。九个子话题已全部按此发出定向催办，每条都带了当天的实测事实（`jj git fetch` 不可用 / 基线已绿 / `--self-test` 的做法），避免它们重新开题。

> **催办本身撞出一条新证据**：父话题用自己的 scoped token 往子话题发评论，后端把作者记成了 **`anonymous`**。这是 @Agent 一等公民机制对比 说的「分身身份塌缩」的**活样本**——不是理论推演，是刚才在这条留言上直接观察到的。已同步给那一路写进结论。

## 收口判定表（2026-08-11，wangchangxin 指派）

判据一律是**工作区实际 diff**，不是印象。所有话题分支都在同一个 git 仓库里，所以父话题能直接算：

```bash
jj diff --stat --from "fork_point(main | topic/<hex8>)" --to "topic/<hex8>"
```

| 子话题 | 实际 diff | 结局 |
|---|---|---|
| `f7a66e89` 架构与领域模型 | 1 file，`docs/` 277+ | **纯调研，不递卡** |
| `5afbd67a` Agent 一等公民 | 1 file，`docs/` 164+ | **纯调研，不递卡**（wangchangxin 已定案） |
| `d8990b22` 仓库自解释 | 1 file，`docs/` 179+ | **纯调研，不递卡** |
| `a6e422c7` 高风险护栏调研 | 1 file，`docs/` 200+ | **纯调研，不递卡** |
| `4d158f8c` 质量闸门与 CI/CD | **`CLAUDE.md` 1 行** + `docs/` 356+ | **递卡**——见下 |
| `5e2249e5` 测试策略 | `dev-db.sh` / `e2e/` 共 4 files | ✅ 已采纳 → **PR #270** |
| `9dbf90ee` authz 收敛 | **26 files, 980+/91-** | **递卡**，冲突已消 |
| `fbea1a5e` 领域包解环 | 12 files, 937+/26- | ⛔ 卡在 `pending_gate` 锁死，另有专线处理，本话题不碰 |

**`4d158f8c` 那 1 行不是可有可无的**：它把 `CLAUDE.md` 首行的语言面从「backend + frontend + e2e」改成「backend + frontend + **`cli/` (Go)** + e2e + **`evals/`**」，并加了一句「要完整清单请在仓库根 `ls`，别信这一行」。**这正是 Go 代码此前零 CI 的根因写照**——清单漏了它，谁去审计 CI 覆盖都审计不到。只进文档没用，必须落到 main。

**`9dbf90ee` 的冲突已经没了**（核实：`jj log -r 'main::topic/9dbf90ee'` 同时返回 main 与其尖端 → 它已是 main 的后代；尖端 `edd6673a` 坐在当前 main `2e72632e` 之上）。

> ⚠️ **核实边界**：以上全部是对**本机可见的 main**（28 分钟前，含当天采纳）算的。**远端 main 未验证**——`jj git fetch` 不可用（git 2.39.5 < 2.41），`gh` token 只有 actions/checks 权限，`git/refs`、`branches`、`pulls` 三条读路径实测全部 **403**。

### 母话题不再递卡（已记入决策记录）

母话题的职责是组织与收口，不是交付。本次的卡 `14a2f2d3` 带的是 `CLAUDE.md` 新增 Sandbox Capability Boundaries 一节（40 行）+ 3 份话题文档，**无 backend/frontend/e2e 代码，也未混入任何子任务的代码**。此后这类跨子话题的公共约定改动，应单开一个子话题承载并由它递卡。

### 由此浮出的一条平台级观察

wangchangxin 盘出来的数是「27 个活着的话题只有 4 张卡在流转」，瓶颈**不在状态机怎么变，而在大量做完的活压根没进入流程**。

而我这一轮做的事说明：**这个状态是机器可判的，而且用的是已经存在的原语。**

> **分支对 main 的 diff 非空 + 无验收卡 = 做完了没交。** 一条 `jj diff --stat` 就够。

这和 <&docs/topics/并行话题冲突预警设计.md> 的 P0 是**同一个函数的两种用法**：那边遍历开着的话题算「文件重叠」，这边遍历开着的话题算「diff 是否非空」。`touched_files(repo, ref, base)` 一次实现，两处受益。

**建议合并成一件事做**，别当两个需求排期。

## 衍生设计：并行话题冲突预警（待拍板，未开工）

起因是 wangchangxin 反馈「conflict 现在经常遇到」。查 Buzz 怎么做的，答案是 **它没有登记表，它从 git 算**——`check-branch-skew.sh` 取「main 改过的文件」∩「本分支改过的文件」，非空就拦 push。

> **注意**：这条「从 git 算而不是让人报」的**思路**成立，是本设计的地基；但它那个**具体脚本**只做文件名交集，抓不到语义冲突（alembic 分叉就是反例，已被 #264 的 `check-migration-fork.py` 证伪并取代）。设计文档里的 P2 从一开始就是按语义冲突写的，未受影响。

完整设计、对「登记表」方案的四条保留意见、以及三条局限，见 <&docs/topics/并行话题冲突预警设计.md>。

一句话：**同样的目的，算出来而不是报上来。** 地基（`branch_for_topic`、`merge_topic` 单一入口、同项目共仓、`summon` 通路）全部已存在。

## 顺带发现（与 Buzz 无关）——已拍板并执行

`docs/` 下曾有 7 个受版本控制的商业材料文件：`bp.md` / `bp-v2.md` / `bp-brief.md` / `bp.docx` / `bp-v2.docx` / `bp.pdf` / `gen-bp.js`，与 <&CLAUDE.md> 「`docs/` 严格只放工程文档」的约定直接冲突。

已按该约定移出版本控制，暂存本机 `tmp/bp/` 待上传飞书 wiki。git 历史里仍有，需要旧版内容从那里取。

### 我们自己也有一份 Buzz 式的 skill 漂移隐患

已核实：`.agents/skills/` 下 5 个 `lark-*` 目录与 `.claude/skills/` 下的**逐字节相同**（`diff -rq` 全部无差异），**零同步脚本、零 CI 校验**——和我们批评 Buzz 的那条一模一样。

**为什么没直接删**：删哪一份取决于我们到底在用哪套 agent 工具链，这是人的决定不是技术判断。需要拍板后单开一路。

---

*参照仓库已 clone 至各子话题工作区的 `tmp/buzz`（gitignored）。*

**已知未核实项**（不要当成已确认的事实用）：

- 浅克隆（depth 50）导致无法判断 Buzz 文档的更新频率，故本文未就「它的文档有多新」下任何结论。
- 我们各条 CI/闸门的**实际耗时**没实测过，「60s pytest」是估值。
- jj 环境下 git hooks 到底触没触发，没验证。
- 前端**存量**类型错误有多少个，没跑过 `vue-tsc` —— 补 CI 之前得先跑一次摸底，否则可能一上来就是红的。
- Buzz 的 4 个 canary workflow 没有逐个读。
- Buzz 的 relay 事件摄取主路径、`buzz-acp` 会话池/队列调度、`llm.rs` 模型适配层、整个桌面端都**没读**；mesh / push-gateway / voice 三条子系统、pubsub 投递语义、TLA+ 规约本身也没读。所以对它的**实时投递可靠性、mesh 一致性、ACP 运行时调度不下结论**。
- 我们这侧**没有系统排查过「让计时器做决定」的代码**，所以「facts decide, timers are backstop」那条建议目前只有方法论、没有落点。
