# CheeseX 开发工作流

> 本文件记录这个项目实际怎么开发、怎么测试、怎么迭代 UI。配合 `spec.md`（产品）、`evals.md`（验收场景）使用。新进来的人/agent 按这个来。

---

## 0. 起服务（本地全栈）

三件套：PostgreSQL（Docker）+ 后端（uvicorn）+ 前端（Vite）。

```bash
# 1. 数据库
cd backend && docker compose up -d            # PG，端口 5433

# 2. 后端（8000 常被占用，用 8099）
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8099
#   首次/拉取后：uv run alembic upgrade head

# 3. 前端（Vite 5173，/api 代理到 8099，含 WebSocket）
cd frontend && npm run dev

# 4. 灌入 demo 场景（书院 + 记忆充足的项目 + 话题/里程碑/验收卡…）
cd backend && PYTHONPATH=. uv run python scripts/seed_demo.py
```

模型走**智谱 GLM**（Anthropic 兼容网关），配置在 `backend/.env`（`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `AGENT_MODEL`）。芝士 = `claude-agent-sdk` 拉起 `claude` CLI，路由到 GLM（spec §9，不绑模型）。

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
PYTHONPATH=. uv run python scripts/smoke_tools.py    # 芝士真的调工具改平台状态
```
新增 🤖 行为（工具、巡检、活动消化、一页纸总结等）时，写/扩 smoke 脚本，对真模型跑一遍再说"通了"。

### 迁移
改了 model（新表/新列）：`uv run alembic revision --autogenerate -m "..."` → `upgrade head`。
- 给已有行加 `NOT NULL` 列要带 `server_default`。
- 循环外键（projects↔topics、topics↔blocks）用 `use_alter=True`。

---

## 2. UI 迭代 / 改进（截图驱动的设计 review 循环）

这是本项目 UI 提质的核心方法。**不靠想象，靠看真实截图自审，像设计总监一样挑毛病，逐轮收敛。**

### 循环

```
设计方向 → 构建 → 截全屏 → 逐屏自审(挑毛病) → 修 → 重新截 → 再审 …
```

### 截图工具
`scripts/shots.py`：一条命令截全部关键屏到 `tmp_review/`（工作台/总览/日历/看板/个人主页/章程/决策/私聊）。

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
| 对话 | **飞书群聊** | 左对齐消息行、同人连发分组、@高亮、系统行居中 |
| 文档 | **飞书文档** | 居中文档页、大标题、块悬停手柄 |
| 工作台 | **Cursor/Replit** | 话题树｜对话｜文档 + 右侧工具抽屉 |
| 总览/看板 | **Linear** | 冷静中性、状态用圆点、等宽数字 |
| 个人主页 | **LinkedIn/GitHub profile** | 封面+头像+技能+参与项目=简历 |
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
- commit/PR/代码注释**英文**；产品文档（本文件、spec、evals）中文。
- `.env`、`tmp_*`、`tmp_review/`、`.workspaces/` 不进版本库。
