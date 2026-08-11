# CI 补洞 —— 交接简报 ✅ 已执行完毕（2026-08-11）

> **状态：已完成并全部合进 GitHub main**，PR #264 / #265 / #266。本文保留作记录。
> 执行结果、以及执行者对本文**事项 2 的正确推翻**，见 <&docs/topics/对标buzz-总览.md> 的「已落地」一节。
>
> 来源：话题「对标 Buzz 找差距」。下面每条证据都已在本仓核实过（含行号）。
> 执行者请先自己复验一遍再动手，别信简报——**事项 2 就是没复验会出错的活例子。**

## 任务边界

**做**：本文列的 4 件事，全部集中在 CI 配置 + `.claude/scripts/` + 一个新的迁移 lint 测试。
**不做**：authz / 分身身份那一拨（是另一个 PR，动 `backend/app/domain/authz/`，两拨别混）。

---

## 事项 0（漏洞级）：前端类型错误可以一路合并进 main 并部署

### 证据

```bash
grep -rniE 'vue-tsc|eslint|fe:check|fe:typecheck|fe:lint' .github/workflows/   # → 零命中
```

- <&.github/workflows/test.yml> 的 `paths` 只有 `backend/**` 和 `.github/workflows/test.yml`
- <&.github/workflows/e2e.yml> 虽对 `frontend/**` 触发，但只跑 Playwright，**不做类型检查**
- <&.github/workflows/build.yml>:369 的 `build-frontend` job 里唯一的前端检查是 `:381` 的静态资源完整性守卫（`frontend/scripts/test-check-static-assets.sh`）；真正的构建走 Dockerfile 里的 `vite build`，而 **`vite build` 不做类型检查**

### 关键：工具早就接好了，只是 CI 不调

<&frontend/Taskfile.yml>:46 —— `fe:check` = `lint`（ESLint）+ `typecheck`（`vue-tsc --noEmit`）+ `build`，三个任务都在。
<&Taskfile.yml>:68 —— `task check` 里已经写了 `fe:check`。

**所以这不是"要建一套检查"，是"把已有的检查接进 CI"。**

### 要做的

给前端加 CI 检查，触发条件 `frontend/**`。

**先摸底再动手**：

```bash
task fe:typecheck   # 存量类型错误有多少？没人跑过
task fe:lint
```

存量错误数决定策略：
- 少（<20）→ 直接修完，CI 一步到位设成 blocking
- 多 → 先接成 **non-blocking**（`continue-on-error: true`）跑一段时间，同时开单还债，还完再转 blocking。**别让新 job 一上来就是红的**，那样所有人会立刻学会无视它

**放哪个 workflow**：
- ❌ 别塞进 `build.yml` 的 `build-frontend` —— 它被 `if: needs.plan.outputs.frontend == 'true'` 门着，只在计划构建时跑，且占自建 runner
- ✅ 建议独立 job / 新 workflow，跑在 **GitHub hosted runner** 上。依据：<&.github/workflows/test.yml> 顶部注释说明重活放自建 runner 是为省 hosted 分钟数，但"scope job 便宜到可以放 hosted"——`vue-tsc` 属于同一档，而且不占那个单 runner（该 runner 还要跑部署，见 test.yml 的 concurrency 注释）

**验收**：故意在某个 `.vue` 里写个类型错误 → 开 PR → CI 变红。这一步必须实际验证，不能只看 workflow 语法通过。

---

## 事项 1：把架构铁律下沉成"解析迁移 SQL 的 lint 单测"

### 出处

Buzz 的做法：两个**普通单元测试**（`crates/buzz-db/src/migration.rs:1038` 和 `:1057`）读取全部迁移 SQL，强制「每张业务表必须有 `NOT NULL community_id`，主键/唯一键/外键必须以它打头」，白名单 11 张表写死在代码里。机制与它的技术栈无关，可直接照抄小号版。

### 要做的

在 `backend/tests/` 下加一个测试，扫描 <&backend/alembic/versions>（当前 62 个文件）强制：

1. **`TIMESTAMPTZ` 铁律** —— <&CLAUDE.md> Datetime 一节要求所有时间列 `DateTime(timezone=True)`，现在只是散文
2. **外键必须有索引**
3. **软删列命名统一**

**62 个存量迁移一律进白名单豁免**，新增迁移受检。白名单写死在测试文件里（不是配置文件）——这样加豁免必须改代码、走 review，而不是偷偷加一行配置。

估工约 1 人日。

**注意**：<&.claude/rules/migrations.md> 里已有的迁移约定要先读一遍，规则以那份为准，别和它冲突。

---

## ~~事项 2：抄 `check-branch-skew.sh` 防 alembic 迁移链分叉~~ ❌ 本条是错的，已被推翻

> **2026-08-11 更正（本文作者的错，不是执行者的）**：这条建议不成立，**不要照做**。
>
> `check-branch-skew.sh` 的判据是两侧 `git diff --name-only` 的 `comm -12`，即**文件名交集**。而 alembic 分叉的形态恰恰是两个 PR 各加一个**不同名**的 `versions/*.py`——交集恒为空，**一条都抓不到**。
>
> 我写这条时说它「直接命中反复踩的坑」，是**没有拿一个真实的分叉反例喂给这个脚本**就下的结论。
>
> **实际落地的正确做法**（执行者已自行改正并合并进 #264）：`check-migration-fork.py`——用 `ast` 解析 `down_revision`（**merge revision 的 `down_revision` 是 tuple，正则会读错**），把 `origin/main` 和工作树两张 revision 图并起来算 head 数，>1 即报。带 `--self-test`。
>
> `check-branch-skew.sh` 本身仍有价值，但价值在**别的地方**：防「本地在落后的分支上跑检查假绿」。想抄的话按那个目的抄，别按防迁移分叉抄。

---

## 事项 3：三条 grep 守卫

<&.claude/scripts/check.sh> 现在只跑 ruff / pyright / pytest。<&.claude/rules/> 里的规矩全靠 agent 自觉读——**反证：<&.claude/rules/backend-tests.md> 白纸黑字写着「别加第九个 `_auth()`」，实测现在有 9 个一模一样的 `def _auth(handle: str) -> dict[str, str]`。**

加三条 grep 守卫（<&CLAUDE.md> 里已明令、但零强制手段的规则）：

| 守卫 | 规则出处 |
|---|---|
| 禁 `tzinfo=None` | CLAUDE.md「Datetime」：Never use `.replace(tzinfo=None)` |
| 禁用 `list`/`set`/`dict`/`type` 作方法名 | CLAUDE.md「Python Conventions」 |
| domain 层禁裸 `HTTPException` | CLAUDE.md「API Design」：要用 `app.core.errors` |

存量违规先白名单，同样写死在脚本里。

---

## 顺带两条（很小，一并做掉）

### `check.sh` 开头就 `cd backend`

<&.claude/scripts/check.sh>:17 —— `cd "$REPO_ROOT/backend"`。后果是**改前端也要等 pytest 跑完，而前端自己一个检查都不跑**。

Buzz 的 `lefthook.yml` 是按改动路径分流的（改 `web/**` 只跑 web 的 fix）。做事项 0 时顺手让 `check.sh` 按改动路径决定跑什么。

### `CLAUDE.md` 里有一句没有事实支撑的断言

CLAUDE.md 的 Testing 一节写着 "Tests MUST pass before any commit. **Pre-commit hook enforces this.**"

但 <&.claude/scripts/pre-commit> 这个 hook **全仓搜不到任何安装入口**：

```bash
grep -rn "pre-commit" --include='*.sh' --include='*.yml' --include='*.md' .   # 只命中文档，无安装脚本
```

两个选择：给它加安装入口（推荐，比如并进 `post-pull.sh` 或 `task setup`），或者把那句断言改成实话。**别留着一句没人兑现的保证。**

顺带一提这个写法值得学：Buzz 的 `tenant.rs` 注释里直说自己是 *"lint-and-review fence, not a compiler fence"*——明说这道防线绕得过去。比宣称"已强制"诚实。

---

## 未核实项（别当既成事实）

- **前端存量类型错误数量**：没人跑过 `vue-tsc`。这是事项 0 的第一步，也是唯一可能让工期翻倍的变量
- **各条 CI/闸门的实际耗时**：文中"60s pytest"是估值，没实测
- **jj 环境下 git hooks 到底触不触发**：没验证。这直接影响"给 pre-commit 加安装入口"这条值不值得做——本仓 VCS 是 jj，先验证再决定

## 建议顺序

事项 0（先摸底）→ 2 → 3 → 1

0/2/3 动的是 CI + `check.sh` 同一批文件，合成一个 PR；事项 1 是独立的新测试文件，可以拆开走，也可以并进来。

按 <&CLAUDE.md> 约定：**所有提交走 PR，不直接推 main**；本仓多 agent 并行，动手前先看开着的 PR 和 main 最近提交有没有人在做同一件事。
