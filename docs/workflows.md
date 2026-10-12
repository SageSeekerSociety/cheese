# CheeseX 开发工作流

> 本文件记录这个项目实际怎么开发、怎么测试、怎么迭代 UI。配合 `spec.md`（产品）、`evals.md`（验收场景）使用。新进来的人/agent 按这个来。

---

## 0. 起服务（本地全栈）

三件套：PostgreSQL（Docker）+ 后端（uvicorn）+ 前端（Vite）。**端口只有一套**，
写在 [README 的 Ports 表](../README.md#ports)，
别再另起一套——这一节以前写的是 PG 5433 / 后端 8099 / Vite 5173，和 README、
Taskfile、e2e 各不相同，四套里没有一套跑得起来另外三套。

```bash
# 1. 基础设施（PG 5432 开发库 + 5433 测试库 + Redis），仓库根目录
docker compose up -d

# 2. 首次/拉取后：迁移
cd backend && uv run alembic upgrade head

# 3. 后端 8081 + 前端 3000，一条命令
task dev

# 4. 灌入 demo 项目（侧栏里点得进去的几个演示项目）
cd backend && PYTHONPATH=. uv run python scripts/seed_fusion_demo.py
```

模型走**智谱 GLM**（Anthropic 兼容网关），配置在 `backend/.env`（`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `AGENT_MODEL`）。芝士 = 平台拉起的 `claude` CLI（Claude Code 骨架），模型请求经计量代理或网关路由（spec §9，不绑模型）。

---

## 1. 后端测试

**行为测试，不审源码**（测真实 HTTP/WS 行为，不 assert 实现细节）。

```bash
cd backend
uv run ruff check app/ tests/      # 0 error
uv run ruff format app/ tests/
uv run pyright app/                 # 0 error
uv run pytest -q                    # 全绿才算数
```

- **离线确定性**：`tests/conftest.py` 用内存 SQLite（StaticPool）+ **StubAgent**（不打真模型），所以 pytest 快且稳。
- **fixture**：`client`（FastAPI TestClient，路由自动发现）；`client.test_factory` 可直接灌数据（`asyncio.run` 一段 async 种子）。
- **位置**：`tests/integration/`（API/WS 行为）、`tests/unit/`（纯函数，如 prompt 组装）。
- **新功能必须带测试**；提交前 `task check` 或上面四条全过。

### 真模型 smoke（手动，验证 AI 链路）
StubAgent 不打真模型，所以**关键 AI 行为另外用 smoke 脚本对真 GLM 验证**：

```bash
cd backend
PYTHONPATH=. uv run python scripts/smoke_agent.py   # 流式 + 记忆 + 会话恢复
```
新增 🤖 行为（工具等）时，写/扩 smoke 脚本，对真模型跑一遍再说"通了"。

### 迁移
改了 model（新表/新列）：`uv run alembic revision --autogenerate -m "..."` → `upgrade head`。
- 给已有行加 `NOT NULL` 列要带 `server_default`。
- 循环外键（projects↔topics、topics↔blocks）用 `use_alter=True`。
- **链必须始终单 head**：提交前 `uv run alembic heads` 必须恰好一条；合并 main 后重查，main 长了新迁移就把自己的 `down_revision` 接到新 head 上。平行迁移分叉过 4 次、每次都断 dev 部署；CI 的 backend `static` job 有单 head 检查，但别等它抓。细则见 `.claude/rules/migrations.md`。

---

## 2. UI 迭代 / 改进（截图驱动的设计 review 循环）

这是本项目 UI 提质的核心方法。**不靠想象，靠看真实截图自审，像设计总监一样挑毛病，逐轮收敛。**

### 循环

```
设计方向 → 构建 → 截全屏 → 逐屏自审(挑毛病) → 修 → 重新截 → 再审 …
```

### 截图工具
`scripts/shots.py`：一条命令截全部关键屏到 `tmp_review/`（工作台/总览/看板/个人主页/项目目标/决策/私聊）。

```bash
# 服务要先起好 + seed
uv run --with playwright python scripts/shots.py
# 然后用 Read 工具逐张看 tmp_review/*.png，对照下面的标准挑毛病
```
截图尺寸控制在 ~1440 宽、scale 1（太大读不了）。`tmp_review/` 是临时目录，审完可删。

### 自审清单（每屏都过一遍）
- **对标产品对不对**（见第 3 节）：这个界面像不像它该像的产品？
- **强调色是否克制**：橙红（#F57F17）只应出现在 ≤3 类地方（主按钮、激活态、品牌标）。到处橙红 = 廉价。
- **头像是否素雅**：芝士=墨色方块、人=中性灰，绝不要彩色方块。
- **状态/枚举**：状态用小圆点+中文；**绝不暴露英文枚举**（`decision_request` 这种），统一走 `frontend/src/labels.ts`。
- **字阶是否清晰**：标题/正文/meta 分明（`.t-page-title/.t-title/.t-body/.t-meta`），meta 用等宽。
- **间距是否成体系**：8px 栅格、卡片只用边框不用重阴影。
- **空状态**：要像设计过的（图标+一句话+引导），不是空白。
- **文案**：给用户看的，不写 spec/产品说明（"采纳即合并归档（spec：…）"这种要删）。

### 验收（行为，按 evals.md）
对照 `docs/evals.md` 的"对长什么样"，用 Playwright 真点真聊（真模型回复要等几秒），判断产品语义对不对，不 assert 文本相等。失败 = 改系统，不是改测试。

---

## 3. 设计语言（视觉规范 · 单一事实来源）

**铁律：每个界面先认 spec §1 点名的"对标产品"，照着真实产品抄，不即兴发挥。**（踩过坑：自己造的暖色/紫色玻璃/扁平都被打回。）

| 界面 | 对标产品 | 关键特征 |
|---|---|---|
| 整体/组件 | **Vuetify Material**（同现有 cheese-backend-py 前端） | 琥珀橙 #F57F17 主色 |
| 话题 | **GitHub PR** | #id + Open/Merged 徽章 + "合并到 main" + 末尾合并框 |
| 对话 | **飞书群聊** | 左对齐消息行、同人连发分组、@高亮；谁做的事落在正文轴上，房间里发生的事居中 |
| 文档 | **飞书文档** | 居中文档页、大标题、块悬停手柄 |
| 工作台 | **Cursor/Replit** | 话题树｜对话｜文档 + 右侧工具抽屉 |
| 总览/看板 | **Linear** | 冷静中性、状态用圆点、等宽数字 |
| 个人主页 | **LinkedIn/GitHub profile** | 头像+活动图+参与项目+参与的话题=简历 |
| 私聊 | **飞书私聊** | 左栏会话入口 → 主区域普通 1:1 聊天 |

### Token（`frontend/src/style.css :root` + `plugins/vuetify.ts` 镜像）
- 中性灰阶：`--ink/--text/--muted/--faint`、`--line/--line-2/--fill`、`--canvas/--surface`。
- 强调：`--accent`(#F57F17) 只点主按钮/激活态/品牌；`--accent-press`；`--accent-ink`(文字)。
- 状态：`--ok/--warn/--danger`，**以圆点+中文文字呈现，不做彩色 chip**。
- 形状：卡片圆角 12、控件 8、chip 6、头像 8；卡片**只用 1px 边框、不用阴影**（阴影只给菜单/弹窗/抽屉）。
- 字体：Inter + 苹方（正文）、JetBrains Mono（meta/时间/数字/id）。

### 枚举 → 中文
所有后端枚举（通知 kind、话题 status/kind、角色、AI 模式）经 `frontend/src/labels.ts` 映射成中文再显示，绝不裸奔。

---

## 4. 多 agent 编排（怎么并行干）

- **模式**：主控（设计/架构/集成）+ 后台 agent（独立领域并行）。
- **数据模型/迁移**：主控**串行**改（避免迁移冲突）。
- **领域 CRUD/独立 UI**：可并行派 agent，**每个 agent 独占自己的文件**，给统一约定（Route→Service→Repo→Model、响应封套、错误类、Pydantic v2），不许碰 main.py（路由**自动发现** `app/api/routes/*` 里的 `APIRouter`）。
- **UI 设计**：把设计判断写成**死规范**（hex/字号/间距/用色规则）再交 agent 执行，主控截图 review；不让 agent 自由发挥审美。
- **验证**：agent 报完，主控**截图/跑测试亲自验**，不轻信"已完成"。

---

## 5. 提交规范
- 不擅自 commit；都走 PR、不上 main（项目规范）。
- 提交标题（Conventional Commits 的 subject）**英文**；PR 描述与它进 main 的提交正文**中文**；代码注释**英文**；产品文档（本文件、spec、evals）**中文**。
- 标题由本仓库自己的 Required CI 检查（`.github/scripts/check-commit-title.py`）：`type(scope): description`、结尾不加句号、描述是英文句子（句中出现芝士、支线这类中文产品名可以）。平台不替托管仓库定提交格式，这条规范只靠本仓库的 CI 守。
- `.env`、`tmp_*`、`tmp_review/`、`.workspaces/` 不进版本库。

---

## 6. 开 PR 前：task ci:fast（本地快检）

Required CI 第一条有用信号约 8 分钟墙钟。`task ci:fast` 在本地按 merge diff 选出命中的检查并真实执行，让 lint 错误、超 cap 文件、被改模块的 pure 测试和窄类型这类问题在开 PR 前就暴露。完整 Required CI + merge queue 仍是唯一合并门禁，ci:fast 绿不代表完整 CI 通过。

```bash
task ci:fast                 # quick 档（180s）：先跑静态检查，再跑选中的 pure 测试 + 窄类型/前端 related；先把分支跟进 main（fetch + merge/rebase）
task ci:fast -- --types      # 改动碰组件 props / 共享类型时，追加 pyright / vue-tsc 全量 manual hook
```

三档：`quick`（`task ci:fast` 默认，180s，测试+类型）、`static`（只跑 pre-commit 静态 hook，不跑测试/类型）、`extended`（同样的选中范围，只把预算放宽到 900s）。`--tier` 决定档位；`--title` 传拟用 PR 标题，不传就按 `gh` 读到的真实 PR 标题判 Conventional；两样都拿不到按未验证（unknown）处理并提示传 `--title`，绝不用可能无关的 HEAD 标题顶替已有 PR。

**机器上没有 `task` 时**：本次核查覆盖的平台云执行机样本（cheese-de808b-27）未预装 task，422 作者当时所在执行机同样没有；可按 README 第 13 行的脚本装到 `~/.local/bin`（样本机上实测可装）。Taskfile 只是薄封装，不装 task 时用下面的等价入口——前提是机器上有 python3 和 uv/uvx（pre-commit 由 uvx 解析），装前端依赖还需要 pnpm：

```bash
python3 .github/scripts/ci-fast.py --tier quick                # ≡ task ci:fast（quick 档）
python3 .github/scripts/ci-fast.py --tier quick --types        # ≡ task ci:fast -- --types
(cd backend && uv sync)                          # ≡ task be:deps:sync（备 backend/.venv）
(cd frontend && pnpm install --frozen-lockfile)  # ≡ task fe:install（备 node_modules）
```

注意：不带 `--tier` 直接 `python3 .github/scripts/ci-fast.py` 走的是 `static` 档，不等价于 `task ci:fast`。

- **它证明什么**：本次 merge diff 命中的检查真实执行并通过（scope 与 Required CI 同一个 select() + path map，guards 恒跑；测试选择走纯静态反向 import 图 + tree/i18n guard）。报告写在 gitdir 的 `ci-fast-report.json`（记 base/head/脏树内容指纹），**绿以报告落盘为准**。
- **这些都不是绿**（退出码 2/3，机器可读）：无 merge-base 或自定义 `--base` 时 file-size/migration-fork 判不了、选中了却零执行或选中文件没被真正执行、pytest 空/跳过/无覆盖、前端 related 图为空、报告写失败、hook 或检查超时/超内存/被信号杀、hook 配置漂移、缺 pre-commit / `backend/.venv` / `frontend/node_modules`（按提示先 `task be:deps:sync` / `task fe:install`）。退出码 1 是所选检查真实失败。
- **边界**：quick/extended 只到 pure 测试 + 窄类型/前端 related；contract / integration / build / e2e / 完整前端消费方类型不在这一层（摘要会列出未跑项和定向建议）。`extended` 不扩大选中范围，只放宽预算，超预算仍是 unknown。前端 related 只在有改动源码根时可信，配置/删文件/未知路径/无 base 一律回退全量 Vitest。详细用法见 `.github/scripts/ci-fast.md`。
- **pre-push hook**：opt-in，片段在 `ci-fast.py` 文件头注释里，不默认安装；也别用 `--no-verify` 绕过——被跳过的 hook 一律按未执行处理，整体不会是绿。外层 shell 里的 `SKIP` / `PRE_COMMIT_COLOR` 等会改判定的变量在起子进程前被清掉，不会把某个嵌套检查悄悄跳过。

设计取舍（范围、退出码语义、反例清单）写在 `.github/scripts/ci-fast.py` 的文件头注释。

### 草稿 PR 只跑快检档，正式 PR / 合并队列跑完整门禁

Required CI（`.github/workflows/required-ci.yml`）按事件分两档，档位由 `.github/scripts/required-ci.py` 从事件读取（`tier_for`）：

- **草稿 PR**（`pull_request` 且 payload 里 `pull_request.draft` 为 `true`）：只跑快检档——一个内联 job 先 `gh api repos/$GITHUB_REPOSITORY/pulls/N > "$RUNNER_TEMP/pull.json"`，再跑 `python3 .github/scripts/ci-fast.py --tier quick --budget 1200 --memory-mib 9216 --pull-request "$RUNNER_TEMP/pull.json"`，所有产品 suite 一律跳过。聚合 job 的名字是 **`CI required (draft quick)`**，与正式名字不同（两个档各一个聚合 job，见下）。档位给的是**选中范围**与本地 quick 档相同，**边界不是**：本地默认的 180s / 3072 MiB 是开发机自己的限制（3072 MiB 就是它的会话上限），跑在 4 vCPU / 16 GiB runner 上会让前端套件在到达上限时被杀——10-12 03:56–06:30 窗口里 12 个跑完的草稿 run 全红，12 个都死在 `frontend-unit`（超时 8、撞上限 3、测不出 1，峰值 2771–3079 MiB），后面的检查一律记 0 秒。所以草稿 job 给 runner 自己能兜住的采样上限（9216 MiB，前端 vitest 并行度按同一个数推：每 3072 MiB 一个 fork，公开仓库的标准 runner 是 4 vCPU / 16 GiB，算出 3 个，和 `frontend/vite.config.ts` 给同一台机器算的一致）和按完整 CI 各步耗时定的预算（1200s）。首次实跑（2026-10-12，恰好是最宽的那一档：未知路径把范围放大成全量套件 + 全量类型）校准了这两个数：全量前端单测 3 个 fork 584s 通过、组峰值 3580 MiB（远低于 9216 MiB 的上限），全量类型 123s 通过，随后的 `backend-pure` 撞上 1200s 预算。也就是说定向的那一档（改动相关测试 + 改动文件类型检查）在预算内有富余，回退到全量的那一半仍可能报 unknown。预算耗尽仍然是 unknown，不会为了变绿裁掉检查。
  这个档**不判自己没有的东西**（2026-10-12 加）：草稿的标题是平台的占位标题（`WIP: <任务标题>`），所以标题一律不查——`--pull-request` 给的不是标题字符串，是 `gh api` 的 JSON 文件，`ci-fast.py` 从里面读标题和 `draft` 标志，草稿记为未执行，转正后由完整档和 `pr-title.yml` 检查；需要固定版 Claude Code / Codex / pi 的测试这里记「未执行」而不是跑出红（那三个二进制是完整 CI 装的，而伸手要它们的测试是 fail 不是 skip，`ci-fast.py` 按 import 关系从选中列表里把它们拿掉，命名出的文件写进摘要）；Redis 由这个 job 的 valkey service 提供，和完整档同一个镜像、同一个端口。合起来是**只有退出码 1（真有检查失败）才让草稿变红**：2（unknown）/3（blocked）只打一条 warning 并把每个没判到的项写进这一步的摘要（`$GITHUB_STEP_SUMMARY`），所以草稿的绿不等于「全判过了」，摘要才是那一档给的全部信息。代价是摘要没人看时，unknown 看起来和绿一样——所以 step summary 不是可选项。这几条都是从草稿 run 的日志里读出来的：截止 10-12 的两天里合并的 188 个 PR 有 83 个首推没过，而草稿档 10-12 03:47 上线后创建的 51 个 PR 只有 24% 首推通过（之前创建的 137 个是 68%）；上线后 39 个首推没过里 17 个是这几类误红。
- **正式 PR / 合并队列**（`merge_group`，或任意 `draft` 为 `false` 的 `pull_request`，含把草稿转正的 `ready_for_review`）：跑完整门禁——merge diff 选中的每个 suite 都跑，聚合 job 名字是 **`CI required`**。

关键点：

- **两个档是两个聚合 job，名字都写死**。`required`（`name: CI required`）跑完整档，`required-draft`（`name: CI required (draft quick)`）跑快检档，谁跑由 `if` 里事件的 `draft` 决定，不依赖 scope 输出——scope 失败也不会退化成裸名字。名字是字面量而不是用表达式拼的：表达式只在 GitHub 求值时才渲染，被取消的 run 会把表达式原文当成检查名显示，看起来像一条红。两边的判断仍是同一次 `required-ci.py check`（它自己按事件重算档位，和 scope 输出对不上就红），不存在两套规则。
- **草稿的绿永远不会被当成正式门禁的绿**。分支保护要求的是 `CI required` 这个名字，只有完整档那个 job 会在它名下给出结果；草稿只在 `CI required (draft quick)` 名下判绿。不跑的那个聚合 job 在 run 里显示 skipped，而 GitHub 把 skipped 当「已满足」，所以草稿上 `CI required` 是一条 skipped。这不妨碍把关：草稿本来就合不了，真正拦住的是平台自己的判断（`merge_state.py` 把缺席和 skipped 都当没通过）和合并队列——`merge_group` 上完整档会重跑一次，队列要的是那一次的结果。
- **`ready_for_review` 会重跑，且是完整的**。`pull_request` 触发器显式列了 `opened / synchronize / reopened / ready_for_review / converted_to_draft`；草稿转正触发的是一次全新的**完整** run，草稿那次快检的绿不会被复用。并发组按 ref 只取消**仍在途**的旧 run；已经跑完的草稿快检 run 不会被撤销，也不需要撤销——它那条 `CI required` 是 skipped（对草稿来说本来就等于没跑），要采信的是完整 run 在同名下的结果。
- **判定失败即红，绝不放行**：`draft` 缺失、事件 JSON 读不出、事件名未知，一律按**完整档**处理（fail closed）；聚合时按事件重算档位，scope 报的档位与事件不一致就报错。草稿 run 里若有产品 suite 真的跑了（哪怕绿）也算失败——那意味着有完整 suite 在草稿名下跑过。所选 suite 缺结果、取消、空报告、超预算都不算绿。
- **生产部署链不受影响**：部署门禁只认 `required-ci.yml` 的 `merge_group` run（`deploy/check-auto-deploy.py`）；这条链没改。

**平台递卡（提验收）**：递卡本身就会把草稿 PR 转正式——`backend/app/domain/review/pr_publish.py` 在卡递出时对还开着的 draft PR 调 `mark_ready_for_review`（`test_accept_pr.py::test_filing_a_card_takes_the_batchs_pr_out_of_draft` 覆盖），于是 `ready_for_review` 触发一次**完整** Required CI。也就是说：**递卡自动开始完整门禁，不需要先手动 `cheese_ready`／标 ready**。但要拿到的验收信号是**同一 head** 上**完整的 `CI required`**（不是 `CI required (draft quick)`）变绿——递卡时那次草稿快检的绿不构成可验收信号，采纳必须等完整 `CI required` 绿。若想在递卡之前就先看完整结果，可以提前标记 ready（可选做法，不是递卡的前提）。
