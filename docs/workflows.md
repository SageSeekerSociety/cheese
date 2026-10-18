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
| 频道 | **GitHub PR** | #id + Open/Merged 徽章 + "合并到 main" + 末尾合并框 |
| 对话 | **飞书群聊** | 左对齐消息行、同人连发分组、@高亮；谁做的事落在正文轴上，房间里发生的事居中 |
| 文档 | **飞书文档** | 居中文档页、大标题、块悬停手柄 |
| 工作台 | **Cursor/Replit** | 频道树｜对话｜文档 + 右侧工具抽屉 |
| 总览/看板 | **Linear** | 冷静中性、状态用圆点、等宽数字 |
| 个人主页 | **LinkedIn/GitHub profile** | 头像+活动图+参与项目+参与的频道=简历 |
| 私聊 | **飞书私聊** | 左栏会话入口 → 主区域普通 1:1 聊天 |

### Token（`frontend/src/style.css :root` + `plugins/vuetify.ts` 镜像）
- 中性灰阶：`--ink/--text/--muted/--faint`、`--line/--line-2/--fill`、`--canvas/--surface`。
- 强调：`--accent`(#F57F17) 只点主按钮/激活态/品牌；`--accent-press`；`--accent-ink`(文字)。
- 状态：`--ok/--warn/--danger`，**以圆点+中文文字呈现，不做彩色 chip**。
- 形状：卡片圆角 12、控件 8、chip 6、头像 8；卡片**只用 1px 边框、不用阴影**（阴影只给菜单/弹窗/抽屉）。
- 字体：Inter + 苹方（正文）、JetBrains Mono（meta/时间/数字/id）。

### 枚举 → 中文
所有后端枚举（通知 kind、频道 status/kind、角色、AI 模式）经 `frontend/src/labels.ts` 映射成中文再显示，绝不裸奔。

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
- 标题由本仓库自己的 Required CI 检查（`.github/scripts/check-commit-title.py`）：`type(scope): description`、结尾不加句号、描述是英文句子（句中出现芝士、支线这类中文产品名可以）。平台不替托管仓库定提交格式，这条规范只靠本仓库的 CI 守。PR 上判红；主干 push 那次只报 warning（`--warn-only`），因为那些提交已经在 main 上、没人能改标题，判红只会把一次已验证的提交和 dev 一起卡住。
- `.env`、`tmp_*`、`tmp_review/`、`.workspaces/` 不进版本库。

---

## 6. 开 PR 前：task ci:fast（本地快检）

Required CI 第一条有用信号约 8 分钟墙钟。`task ci:fast` 在本地按 merge diff 选出命中的检查并真实执行，让 lint 错误、超 cap 文件、被改模块的 pure 测试和窄类型这类问题在开 PR 前就暴露。完整 Required CI 仍是唯一合并门禁，ci:fast 绿不代表完整 CI 通过。

```bash
task ci:fast                 # quick 档（按可用 CPU 默认 180–720s）：先跑静态检查，再跑选中的 pure 测试 + 窄类型/前端 related；先把分支跟进 main（fetch + merge/rebase）
task ci:fast -- --types      # 改动碰组件 props / 共享类型时，追加 pyright / vue-tsc 全量 manual hook
```

三档：`quick`（`task ci:fast` 默认，测试+类型）、`static`（只跑 pre-commit 静态 hook，不跑测试/类型）、`extended`（同样的选中范围，默认预算 900s）。quick/static 默认按可用 CPU 数给 180–720s：1 核 720s、2 核 360s、3 核 240s、4 核及以上 180s。`CI_FAST_BUDGET_SECONDS` 可覆盖默认值，至少 180s；显式 `--budget` 优先且保留原值。`--tier` 决定档位；`--title` 传拟用 PR 标题，不传就按 `gh` 读到的真实 PR 标题判 Conventional——这一次读取顺带取回 `draft` 标志，读到是草稿就整条记未执行（草稿的标题是平台的占位标题，判它没有意义）；两样都拿不到按未验证（unknown）处理并提示传 `--title`，绝不用可能无关的 HEAD 标题顶替已有 PR。

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
- **推前自检 `.cheese/pre-push`**：项目根下这份可执行文件就是推前入口，由平台给任务工作目录装的 `pre-push` 钩子调用（钩子只在收到的那份检出里有可执行的 `.cheese/pre-push` 时才跑它，没有就一次 `[ -x ]` 走人——同一个安装点服务所有项目，别的项目不替这个项目付代价）。它执行 `ci-fast.py --tier static --pre-push`：选中表以草稿档那张为底，加上只在这里跑的 `openapi-document`（`backend / static` 里建 API 文档、核 untyped 棘轮的那一步，本机能跑而此前没有别的检查覆盖它，见 `.github/scripts/ci-fast.md`），但**不吃 node 工具链的那三个**（eslint、stylelint、boundary-check-frontend）——它们在小机器上会被杀，把被杀掉的检查报成红比不报更糟——这三个写进报告的「未执行」，不静默拿掉；预检跟着这次会起的检查走，选中里没有吃 node 的钩子时，没跑过 `task fe:install` 的检出不会被缺 `frontend/node_modules` 卡成 blocked。**结果不影响推不推得上去**：钩子在这份脚本之后再 `exit 0`，所以检查失败、跑不完、缺工具、脚本自己被信号杀死，推送都照常。推送被拦住就是这次提交没上去，而工作目录活不到重试——该拦的是 CI，不是这里。脚本自己直接跑时——没人要报告的那一次——仍会以 1 退出（复现用），这个码不吞，所以链到自己的钩子上就是一道真的闸门；平台那次要的是报告，末尾自带一个 `exit 0`。另有一道 240s 墙钟兜住卡死（`CI_FAST_PRE_PUSH_TIMEOUT` 可调）。没过的每一项都印出复现命令——就是这次实际跑的那条 pre-commit 命令（默认带 `--python` 把 uvx 钉在本脚本的解释器上：uvx 自己挑的那个会让 `language: system` 的钩子跑在别的 python 上，见 `.github/scripts/ci-fast.md`），被留下的那三个也各印一条，照着能直接跑；不是钩子的检查（`--pre-push` 配非 static 档才会遇到）改印真正跑过的那条命令，不再印一句跑不通的 `pre-commit run <id>`。报告那一行的 JSON 只记检查名、判定、耗时和命令，不记日志：改哪个文件看钩子自己打在上面的输出（复现命令会再打一遍）。**那份报告由平台转给提交的人**：agent 的提交是被 `post-commit` 钩子推上去的，推送时它的终端早没了，报告打到管道上没人接，所以平台自己设 `CI_FAST_PRE_PUSH_REPORT` 让这份脚本把**没通过**的那一次的输出留在那个文件里，再读回来发到任务的对话里（通过的报告不送，那是给手动跑的人看的；推送本身失败也照样送——报告在推送打到远端之前就写好了，而正是那一次最该有人看见；房间收不到时同一份内容落到提交那条命令的 stderr 上，那次 `cheese sync` 就是从那条命令的 `post-commit` 钩子起来的）。`--no-verify` 跳过的只是这份报告，草稿档照样判；外层 shell 里 `SKIP` / `PRE_COMMIT_COLOR` 这类会改判定的变量仍由 `ci-fast.py` 在起子进程前清掉。本地开发想在自己机器上拿到这道推前自检，把 `.cheese/pre-push` 链到自己的 `.git/hooks/pre-push` 就行——那次没被平台设报告变量，退出码就是检查自己的判定，git 会照它拦下没过的推送（`task hooks` 只装 `pre-commit` 那一类钩子，不装推前入口）。

设计取舍（范围、退出码语义、反例清单）写在 `.github/scripts/ci-fast.py` 的文件头注释。

### PR 快检、合并与主干完整门禁

Required CI（`.github/workflows/required-ci.yml`）在每个 PR 上跑 quick 档。草稿的聚合检查名为 `CI required (draft quick)`，正式 PR 的名字为 `CI required`，正式档还检查标题。草稿转正式会触发新的 quick run。预算耗尽或环境缺失会写入步骤摘要，PR 上的绿不代表完整套件已经执行。

main 的规则只有必过状态检查，没有合并队列。用 `.claude/scripts/pr-watch.sh --enqueue --deploy <PR>` 在当前会话的后台监控下跟踪：`--enqueue` 等当前 head 的 `ready` 输出可递，再读一次 head，用 `gh pr merge --squash --match-head-commit <sha>` 合并，不启用 auto-merge。head 变化后重新检查，GitHub 也会拒绝不匹配的 SHA。

提交落入 main 后，`push` 触发完整 Required CI。范围基点是 `refs/heads/main-green`，即上一次主干完整门禁通过的提交；绿了才推进锚点，红了保持原值。并发组 `required-ci-push-main` 的 `cancel-in-progress` 为 `false`，同时保留一跑一排队，新排队替换旧排队，被替换提交的改动由后续批次覆盖。首次没有锚点时选择全部已跟踪路径。工作流仍接受 `merge_group` 事件，但当前规则不会创建队列条目。

监控器跟踪合入提交的主干 run；它被取消或未创建时，跟踪包含该提交的后续批次。主干失败会明确报红并以失败结束。主干通过后，监控包含合入提交的 dev 部署，并核对线上版本；部署被取消时等待后续发布。部署门禁依据主干完整 CI 和同一提交的镜像构建结果，收敛到最新的可部署提交。

平台递卡会把仍打开的草稿 PR 转正式（`backend/app/domain/review/pr_publish.py`），重新触发 quick 档；采纳等待同一 head 上的 `CI required`。完整的主干验证发生在合并之后。
