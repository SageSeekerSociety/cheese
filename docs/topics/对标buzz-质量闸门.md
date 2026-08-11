# 对标 block/buzz：质量闸门与 CI/CD

对照对象：`tmp/buzz`（block/buzz，depth-50 浅克隆）。我们这边看的是 <&Taskfile.yml>、
<&backend/Taskfile.yml>、<&frontend/Taskfile.yml>、<&.claude/scripts/check.sh>、
<&.claude/scripts/pre-commit>、<&.claude/scripts/post-pull.sh>、<&.github/workflows/>（16 个）、
<&backend/pyproject.toml>、<&.claude/rules/>、<&deploy/deploy-docker.sh>。

一句话结论：**buzz 在这个面上真正领先我们的不是"工具更好"，而是"闸门按路径分流、偏差被写下来、
反复踩的坑被固化成机器检查"这三件事。我们最大的洞不在后端——后端闸门其实做得不差——而在前端：
前端在 CI 里一个检查都没有。**

---

## 关键差异

### 1. 我们的 pre-commit 不是"一刀切跑全量"，是"一刀切跑后端全量"

<&.claude/scripts/pre-commit> 无条件调 <&.claude/scripts/check.sh>，而 check.sh 第 14 行
就 `cd "$REPO_ROOT/backend"`，之后只跑 ruff + pyright + alembic heads + 全量 pytest。

所以对"改一行前端"这个问题的答案是：**是的，会跑全量 pytest（~60s，`-n 4` 并行全量，
check.sh 注释里说明 testmon 因共享 DB 状态不安全已弃用）；并且前端本身一个检查都不跑**
——没有 eslint，没有 vue-tsc。方向是反的。

buzz 那边 <&tmp/buzz/lefthook.yml> 用 glob 分流：改 `crates/**` 才 `just fmt`，
改 `web/**` 才 `web-fix`，改 `mobile/**` 才 `mobile-fix`；pre-push 同理，
`rust-tests` 只在 `crates|migrations|schema|Cargo.*|rust-toolchain.toml|deny.toml|justfile`
命中时跑。

### 2. 偏差被显式写下来 —— 这是 buzz 最值得抄的"形式"

<&tmp/buzz/lefthook.yml> 顶部 16 行注释，先声明 glob 与 `ci.yml` 的 `dorny/paths-filter`
分组保持同步，然后逐条列出**故意的偏差及理由**，共四条：

- CI 的 rust/mobile filter 里有 `.github/workflows/ci.yml`，本地故意不加——只改 workflow 不需要本地跑测试；
- `desktop-check/typecheck/test` 不跟着 `rust` 改动触发（CI 的 Desktop Core 会），因为这三个命令是纯 TS，本地跟着触发是虚报；
- 只删文件不触发本地 hook（lefthook 2.1.x 的 `extractFiles` 会丢掉已删路径），**"故意接受，不绕过"**，CI 的 paths-filter 兜底；
- `commit-msg` 没有 glob，因为它改的是 commit message 不是文件，且 Git 只在 `git commit`/`git merge` 时跑它。

我们并非完全没有这个习惯——check.sh 的 alembic 段注释写了 "Mirror of CI's migration-heads job
so the fork is caught before push"，<&backend/pyproject.toml> 里 ruff 的 `ignore = ["UP042"]`
和 `extend-immutable-calls` 也都带了完整理由。**但这是孤例，没成为惯例**：pre-commit 与
16 个 workflow 之间的对应关系，全仓库没有一处写明。

### 3. 前端在 CI 里完全没有闸门（这是最实的一条）

- `grep -rn "vue-tsc\|eslint\|typecheck" .github/workflows/` → **零命中**。
- <&.github/workflows/test.yml> 的 `paths` 只有 `backend/**` 和它自己。
- 前端唯一被 CI 碰到的地方是 <&.github/workflows/build.yml> 的 `build-frontend` job，
  它做的是 `docker build`；而 <&frontend/Dockerfile> 跑的是 `pnpm build` = `vite build`
  ——**vite 不做类型检查**。

也就是说：前端的类型错误和 lint 错误可以一路合并进 main 并部署到盒子上，只要它不让打包器崩。

对比 buzz：`web/package.json` 和 `desktop/package.json` 的 `build` 都是 `tsc && vite build`
（类型检查焊死在构建里），CI 的 `Desktop Core` job 另外单独跑 `desktop-check` /
`desktop-test` / `desktop-build` / `desktop-tauri-clippy`。

附带两个坑：

- <&frontend/package.json> 的 `lint` 是 `eslint . --fix`——**会改文件**。<&frontend/Taskfile.yml>
  的 `fe:check` 调的就是它，所以 `task check` 会以"检查"的名义改你的工作区。这个脚本原样搬进 CI 是错的。
- <&frontend/.husky/> 里躺着一套 husky（`pre-commit` → `pnpx lint-staged`，`commit-msg` → commitlint），
  由 `package.json` 的 `postinstall: husky install` 装。它跟 <&.claude/scripts/pre-commit> 是两套互不知道对方存在的东西。

### 4. 我们的本地闸门，仓库里没有安装入口

`grep -rn "scripts/pre-commit"` 在 md/sh/yml 里 **零命中**：没有任何脚本、任务或文档把
<&.claude/scripts/pre-commit> 装到 hooks 目录。<&CLAUDE.md> 却写着"Tests MUST pass before
any commit. Pre-commit hook enforces this."——这条断言在仓库层面无从保证。

（我只能证明"仓库里没有安装入口"，不能证明"每个人机器上都没装"。但新克隆一份肯定没有。）

buzz 的对应物是 <&tmp/buzz/Justfile> 的 `hooks` recipe：显式 `git config --local core.hooksPath`
+ `lefthook install --force`，且用 `--path-format=absolute` 以保证 linked worktree 下也能派发；
`just setup` 自动调它；<&tmp/buzz/AGENTS.md> 的 Quality Gates 一节把这件事写给 agent 看。

### 5. check 的分层：我们缺"无基础设施"那一档

buzz 分四层，边界写在 recipe 注释和 AGENTS.md 里：

| recipe | 内容 | 需要基础设施 |
|---|---|---|
| `just check` | `fmt-check` + `clippy` + 各端 check | 否 |
| `just test-unit` | 挑指定 crate 的 `--lib`，注释明说 "no infra needed" | 否 |
| `just test` | `./scripts/run-tests.sh all` | 需要 PG + Redis |
| `just ci` | check + test-unit + 各端 test/build | 否（构建为主） |

我们这边 <&backend/Taskfile.yml> 的 `test` 和 `test:full` 命令**逐字相同**
（都是 `uv run pytest tests/ -n 4 --reruns 2 -q`），而 `test` 的 desc 还写着
"incremental via testmon"——描述与实现已经脱节。<&CLAUDE.md> 里明确写了
`tests/unit/` 不需要任何服务器，但这个事实没有对应的任务入口。

### 6. 固化的自定义检查：buzz 一堆，我们一个

buzz `<&tmp/buzz/scripts/>` 下（挑我实际读了的）：

- **<&tmp/buzz/scripts/check-branch-skew.sh>**（pre-push）：分支落后 origin/main **且与 main 的改动有文件重叠**
  时才拦。理由写在头三行：CI 测的是与 main 合并后的树，本地测的是旧树,"Local checks ran on a tree CI will never test."
- **`check-file-sizes-core.mjs`**：文件行数**棘轮**——超限文件不许再长，但允许维持现状
  （`allowedLineCount(base, max)` = `base <= max ? max : base`）。它自己带单测
  `check-file-sizes-core.test.mjs`，并且在 ci.yml 的 `changes` job 里 `node --test` 跑。
  ci.yml 的 `desktop`/`web`/`mobile` filter 都把 `scripts/check-file-sizes-core.mjs` 自己列了进去——
  **检查脚本变了就重跑受它管的那一端**。
- **`check-pubkey-truncation-core.mjs`**：正则守卫，禁止手搓 pubkey 截断显示。注释写清坑的形状：
  截断前缀可以靠 vanity grinding 伪造，所以必须走 canonical 的 `truncatePubkey`；
  "before this guard existed" 已经裂成五种写法。带 `overrides` 白名单放行非展示用途。
- **`check-px-text-core.mjs`**：禁止硬编码 px 字号（缩放只缩 rem，px 会冻住）——是一个**已修 bug 的回归守卫**。
- **`check-pr-image-urls.sh`**：PR markdown 里不许出现 relay media URL（GitHub 的 Camo 代理匿名拉取会 404）。
- 一批 `test-*.sh` workflow 合约测试（release ref / desktop release candidate / mobile release / worktree identity），
  全在 `changes` job 里跑。

我们：**自定义检查脚本 0 个**。坑写在 <&.claude/rules/> 三个文件共 91 行里，靠 agent 自觉。
唯一被机器强制的是 alembic 单头（check.sh + <&.github/workflows/test.yml> 的 `migration-heads` job，
且该 job 刻意不受 `scope` 跳过门控）——这条我们做对了，可以当模板。

**这套"写规则"的做法已经被实际证伪了一次**：<&.claude/rules/backend-tests.md> 白纸黑字写着
"Eight files already carry a copy-pasted `_auth()` helper — don't add a ninth."
现在 `grep -rln "def _auth(" backend/tests/` 数出来是 **9 个**。规则写下来了，第九个还是进来了。

### 7. 依赖门禁：buzz 有，我们没有

<&tmp/buzz/deny.toml>：许可证白名单 + RUSTSEC advisory ignore，**每条 ignore 都写清"经由哪条依赖链、
为什么现在不能修、什么时候能移除"**（例：quick-xml 的两条 DoS 只在解析可信输入的路径上，
patched 版本要等 rust-s3 和 plist/netdev 先 bump）。
<&tmp/buzz/renovate.json>：patch/minor 自动合并、major 需人工，外加三条"这个包踩过坑所以钉版本"的
packageRule（evalexpr v13 改 AGPL、tiptap 3.23 破坏编辑器生命周期、redis/deadpool-redis 必须同组升级）。

我们 `grep pip-audit|safety|npm audit|trivy|codeql|osv|dependabot|renovate` → 零命中。

### 8. 发布形态：不是差距，是形态不同

buzz 18 个 workflow，重心在多端签名发布 + 四条 canary（linux/windows/macos-intel/signed-macos）
+ release candidate + 自动 tag + helm chart + <&tmp/buzz/RELEASING.md>（338 行）+ `.release/desktop-candidate.json`。

我们 16 个 workflow，重心完全不同：build / deploy / deploy-dev / deploy-prod
+ 一批**部署后可观测性**探针（box-heartbeat、box-uptime、backup-freshness、backup-restore-test、
deploy-drift、device-smoke、device-wiring、microcloud-smoke）。<&deploy/deploy-docker.sh>（422 行）
按 commit sha 同时钉住 backend+frontend 镜像、带健康检查和回滚。

在"东西部署上去之后还活着吗"这一块，**我们比 buzz 厚**——buzz 把二进制发出去就完了，没有"我们的盒子"要看。
这个方向不需要向 buzz 学。

---

## 值得学的（按投入产出比排序）

### ① 前端接上 CI 闸门 —— 今天就能做，洞最大

**它怎么做的**：buzz `web/package.json`、`desktop/package.json` 的 `build` = `tsc && vite build`；
ci.yml 的 `Desktop Core` job 另跑 `just desktop-check`（biome + file-sizes + px-text + pubkey）
/ `desktop-test` / `desktop-build`。

**我们现在什么样**：前端在 CI 里零检查；`vite build` 不做类型检查；`pnpm run lint` 带 `--fix` 不能当闸门用。
工具其实都装好了——<&frontend/package.json> 里有 `vue-tsc ^2.2.12` 和 `eslint ^9.32.0`，
<&frontend/eslint.config.mjs> 存在，<&frontend/Taskfile.yml> 甚至已经有 `fe:typecheck` 调
`pnpm exec vue-tsc --noEmit`。**只是从来没有人在 CI 里调用它。**

**具体怎么改**：

1. 拆 lint 脚本（<&frontend/package.json>）：`"lint": "eslint ."`、`"lint:fix": "eslint . --fix"`，
   <&frontend/Taskfile.yml> 的 `fe:lint` 指向前者、新增 `fe:lint:fix`。**否则 `task check` 会改你的文件。**
2. 新增 `.github/workflows/frontend.yml`，仿 <&.github/workflows/test.yml> 里那个 `lint` job 的形状
   （跑 `ubuntu-latest` hosted，不占盒子的单 runner）：
   `paths: [frontend/**, .github/workflows/frontend.yml]` → `pnpm install --frozen-lockfile`
   → `pnpm exec eslint . --max-warnings=0` → `pnpm exec vue-tsc --noEmit`。
3. **不要**把 `vue-tsc` 焊进 <&frontend/Dockerfile>。那里的 `vite build` 已经需要
   `NODE_OPTIONS=--max-old-space-size=4096`（注释记着它 OOM 过 CI，exit 134），串上 vue-tsc 只会更险。
   独立 job 更快也更好定位。

**代价**：workflow ~40 行 + package.json 两行 ≈ 半小时。
**风险（重要）**：我**没有实际跑过** `vue-tsc --noEmit` 和无 `--fix` 的 eslint，不知道存量错误有多少。
**先跑一次数数，再决定第一天就阻塞、还是先 `continue-on-error: true` 观察一两周**。
存量清理才是这条的真实成本，不是 workflow 本身。

### ② 抄 `check-branch-skew.sh` —— 改动最小，直接命中我们已知反复踩的坑

**它怎么做的**：<&tmp/buzz/scripts/check-branch-skew.sh>，30 行 bash，pre-push 跑。
`git merge-base` 判断是否落后 origin/main；落后时取本分支改动与 main 改动的**文件交集**，
交集为空就放行，非空才拦并列出重叠文件。

**我们现在什么样**：没有这道闸。而 <&.claude/rules/migrations.md> 记的
"2026-08-09/10 两天内 alembic 链被并行 PR 分叉四次，每次都杀掉 `alembic upgrade head` 并中止 dev 部署"
正是这个问题的一个特例——两个 PR 各自本地绿，合到一起双头。<&CLAUDE.md> 也写着
"Multiple agents work this repo concurrently"，我们比 buzz 更需要这道闸。

**具体怎么改**：脚本几乎可以原样搬到 `.claude/scripts/check-branch-skew.sh`。
只有一处要改：本仓库 VCS 是 jj，`git rev-parse --abbrev-ref HEAD` 在 colocate 下能用但要实测；
更稳的是用 `jj log -r 'main@origin'` / `jj diff --name-only -r`。
最省事的降级版：只做迁移这一档——若本分支新增了 `backend/alembic/versions/*` 且 main 也新增了，直接拦。

**代价**：一小时以内。**我认为这是全篇最划算的一条。**

### ③ pre-commit 按路径分流 + 提供安装入口

**它怎么做的**：<&tmp/buzz/lefthook.yml> 的 glob 分流 + `just hooks` 显式安装 + AGENTS.md 写明。

**我们现在什么样**：见「关键差异 1/4」——不分流，且没有安装入口。

**具体怎么改**（不必引入 lefthook，我们只有两个语言面，一个 `--changed` 模式就够）：

1. <&.claude/scripts/check.sh> 加 `--changed`：拿改动路径（jj：`jj diff --name-only`；
   git 路径保留兜底），命中 `backend/**` 才跑 ruff/pyright/pytest，命中 `frontend/**` 才跑 eslint/vue-tsc。
   alembic heads 那段很便宜（只读迁移图，不连 DB），**建议一直跑**，别分流。
2. 加 `.claude/scripts/install-hooks.sh` + `task hooks`：写
   `$(git rev-parse --path-format=absolute --git-common-dir)/hooks/pre-commit` 并设 `core.hooksPath`。
3. 在 <&.claude/scripts/pre-commit> 顶部照抄 buzz 的注释形式：逐条写"本地这条对应 CI 哪个 job、
   故意不跑的是哪些、为什么"。check.sh 的 alembic 段已经是这个写法，把它变成惯例。

**代价**：半天。
**最大不确定点（诚实标注）**：**jj colocate 下 git hooks 到底会不会触发，我没有实测。**
如果 `jj commit` 根本不走 git hooks，那第 2 步要换成别的挂载点（或干脆接受"只在 `git commit` 路径生效"），
这条建议的收益要打折。做之前先花十分钟验证这一点。

### ④ 把 `_auth()` 那条规则升级成机器检查

**它怎么做的**：`check-pubkey-truncation-core.mjs` 就是这个形状——一条正则 + 一个
`overrides` 白名单 + 一句"canonical 的写法在哪"的报错提示。

**我们现在什么样**：<&.claude/rules/backend-tests.md> 写了"别加第九个 `_auth()`"，
现在有 9 个。**规则已经失效一次，有明确的重复计数，够格固化了。**

**具体怎么改**：`.claude/scripts/check-no-adhoc-auth.sh`，~30 行：
`grep -rln "def _auth(" backend/tests/`，把现有 9 个文件路径作为白名单常量，
出现白名单外的第 10 个就失败，报错里指向 `auth_headers` / `authed_client` / `seed_user` 三个 canonical 入口
（这三个在规则文件里已经写清了各自适用哪个目录）。挂到 check.sh 的 backend 分支里。

**代价**：半小时。
**判断**：buzz 那几个守卫都是"同一个坑踩到第 N 次才固化的"，不要一次上一堆。
`.claude/rules/e2e.md` 里"负向登录不许用 alice"那条我**想不出写得准的正则**，倾向于先不做——
写一个会误报的守卫比没有守卫更糟。

### ⑤ 补一个"无基础设施"的快速测试档

**它怎么做的**：`just test-unit`，注释明说 no infra needed，pre-push 跑它，`just test` 才要 PG+Redis。

**我们现在什么样**：<&backend/Taskfile.yml> 的 `test` 和 `test:full` 命令逐字相同，
`test` 的 desc "incremental via testmon" 是过时描述（check.sh 里解释了 testmon 为何弃用，Taskfile 没跟着改）。

**具体怎么改**：加 `be:test:unit` = `uv run pytest tests/unit/ -n 4 -q`；
把 `test` 的 desc 改成实话，或直接把 `test`/`test:full` 合并成一个。

**代价**：十分钟。纯粹是把 <&CLAUDE.md> 里已经写明的事实（`tests/unit/` 不需要服务器）接进闸门。

### ⑥ 依赖漏洞门禁（最小档）

**它怎么做的**：<&tmp/buzz/deny.toml> + <&tmp/buzz/renovate.json>，每条例外带理由和退出条件。

**我们现在什么样**：完全没有。不过"每条例外带理由"这个写法我们**已经在用**了
（<&backend/pyproject.toml> 的 ruff ignore 就是范本），要补的是"给依赖也上一道门"。

**具体怎么改**：只做最小一档——在 test.yml 那个已经跑在 hosted runner 上的 `lint` job 里加一步
`uv run pip-audit`（或 `uvx pip-audit`），先 `continue-on-error: true` 跑几周看噪音，可控了再阻塞。
许可证白名单**不做**（理由见下）。

**代价**：几行。**收益不确定**——可能一堆无法处理的传递依赖告警。放在最后一位，是因为
"不知道自己有没有洞"本身算个洞，但它显然排在前端没闸门后面。

---

## 不值得学的

1. **多端签名发布与 canary**（`signed-macos-canary.yml` / `windows-canary.yml` /
   `macos-intel-canary.yml` / `linux-canary.yml` / `desktop-release-candidate.yml` /
   `promote-oss-desktop-release.yml` / `mobile-release-candidate.yml` + 338 行 <&tmp/buzz/RELEASING.md>
   + `.release/desktop-candidate.json`）。
   buzz 要把二进制发到别人机器上，签名、公证、分平台灰度是刚需。我们是自己盒子上的 web 服务，
   <&deploy/deploy-docker.sh> 按 commit sha 同时钉住 backend+frontend、健康检查失败即回滚——
   **这就是我们这个形态下的 canary 等价物**。抄一条 canary workflow 只会多一个永远不跑的文件。

2. **cargo-deny 的许可证白名单那半边**。私有仓库、不对外分发二进制、没有向下游证明许可证合规的需求。
   <&tmp/buzz/deny.toml> 里 `[licenses]` 那几十行对我们是纯负担。（`[advisories]` 那半边的**思路**值得，见上面 ⑥。）

3. **Renovate 全量自动合并**。buzz 有 18 个 workflow 加四条 canary 兜着自动合并的风险；
   我们的重活（test / e2e / build / deploy）全挤在盒子的**单个** self-hosted runner 上——
   <&.github/workflows/test.yml> 和 <&.github/workflows/build.yml> 的 concurrency 注释
   已经在为排队和互相取消的问题做优化了。Renovate 的 PR 洪水会直接把这个 runner 堵死。
   要上也得先解决 runner 并发，并限定成"只 patch、分组、每周一批"。现在不值得。

4. **Hermit 工具链固定**（`bin/activate-hermit`，buzz 的 AGENTS.md 甚至专门叮嘱 agent 先激活它）。
   buzz 要给 Rust + Node + pnpm + Flutter + Tauri 五套工具链在多端 CI 上对齐版本。
   我们是 uv（<&.github/workflows/test.yml> 里已经钉死 `astral-sh/setup-uv@v4` version `0.12.1`
   和 Python 3.13）+ pnpm，工具链面窄得多，再套一层 Hermit 是纯开销。

5. **workflow 合约测试**（`scripts/test-release-ref-contract.sh` 等一批，跑在 ci.yml 的 `changes` job 里）。
   它们守的是"多条发布 workflow 之间引用的 ref/tag 约定不要漂"。我们没有那么多互相引用的发布 workflow。
   **但记一笔**：<&.github/workflows/deploy-dev.yml> 靠 `workflow_run: workflows: ["Build and Push Docker Image"]`
   串接 <&.github/workflows/build.yml>——这是一条**靠 workflow 显示名字符串匹配**的隐式契约，
   改 build.yml 的 `name:` 会静默断掉 dev 自动部署。以后这类跨 workflow 契约再多一条，这个模式就该回头看。

---

## 没做到 / 没验证的（诚实清单）

- **没有实测任何耗时**。"我们太慢/太快"的判断全部基于代码里已有的注释（如 test.yml 记的
  "measured median 5.1m"）和命令内容推断，我没有跑过 `task check`，也没跑过 buzz 的 `just ci`（缺 Rust 工具链）。
- **jj 下 git hooks 是否触发，没验证**。建议 ③ 的第 2 步整个压在这个假设上。
- **前端 `vue-tsc --noEmit` / 无 fix eslint 的存量错误数，没跑过**。建议 ① 的真实成本因此估不准。
- <&tmp/buzz/Justfile> 4.3 万字，我只读了 recipe 名单和 `check`/`ci`/`test-unit`/`web-*`/`mobile-*` 几段；
  `ci.yml` 1140 行只读了前 200 行加抽查（deny/renovate 相关段落）；CHANGELOG 完全没读。
  **发布那一面（第 4 个焦点）我是靠文件名单 + RELEASING.md 的规模 + `.release/` 内容判断的，
  没有逐个读 canary workflow 的内容**——结论"我们这个规模用不上"我有把握，但"它们具体怎么做的"我说不细。
- 我没有核对 buzz 的 lefthook glob 与 ci.yml paths-filter 是否**真的**逐条对齐（那需要把两边分组做集合比对）。
  我验证的是"它写了这个契约并列出了偏差"这件事本身。
