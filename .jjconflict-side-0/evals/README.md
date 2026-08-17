# Agent Evals — 回归基建

> docs/evals.md 承诺的"改 prompt/换模型不许退化"的场景回归框架。
> 🔧 平台侧行为用确定性断言验，🤖 agent 行为用真 turn + judge agent 按 rubric 打分。

## 怎么跑

依赖全部来自 `backend/` 的 venv（httpx / websockets / python-dotenv，无新增依赖），
网关凭据读 `backend/.env`（和 dev 后端同一份 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`）。

```bash
# 在仓库根目录
uv run --project backend python evals/runner.py --list        # 列出场景
uv run --project backend python evals/runner.py --scenario C3 # 跑单场景
uv run --project backend python evals/runner.py --all         # 全量回归
```

- 退出码：全部 PASS → 0；有 PARTIAL/FAIL → 1（可直接接 CI）。
- 单场景超时/异常只标 FAIL，不中断整个 run。

## 隔离环境

Runner 自己起一个**隔离后端**（真实 `app.main:app`）：

| 维度 | 隔离方式 |
|---|---|
| 端口 | 8097（`--port` 可换），启动前检查占用 |
| 数据库 | 每 run 一个全新 sqlite（`results/<run>/eval.db`，schema 由 `lib/bootstrap_db.py` create_all） |
| 工作区 | `WORKSPACE_ROOT` 指到 run 目录（含 in-flight turn registry，绝不碰 dev 后端） |
| 调度器 | `SCHEDULER_INTERVAL_SECONDS=0` |
| 沙箱 | `AGENT_SANDBOX_ENABLED=false`（见下"沙箱模式"） |
| 模型 | 走 `backend/.env` 的真实网关（真 agent turn，不 mock） |

dev 后端（8099）与 dogfood 数据全程不被触碰。

**沙箱模式**：目前 eval turn 以"无沙箱纯模型 turn"运行（无 Bash/cheese CLI 等平台
工具）——够覆盖记忆问答、开场白、不接话这类对话行为；C2 这类"真的改代码/发验收卡"
的场景需要给隔离后端开 Docker 沙箱（`AGENT_SANDBOX_ENABLED=true` +
`SANDBOX_API_BASE=http://host.docker.internal:8097/api`），并解决 eval 容器的回收，
是下一步的扩展点。涉及工具动作的 rubric 都已注明"不因无工具而扣分"。

## 产物（可复现性军规）

每次 run 落在 `evals/results/<UTC 时间戳>/`：

- `report.md` — 人读的汇总：每场景 verdict、平台检查、judge 分数与理由、关键 I/O 摘录
- `results.jsonl` — 每场景一行**自包含**记录：完整输入（种入记忆/发的每条消息）、
  完整证据（blocks/WS frames/文档/turn 记录）、judge 的完整 prompt + 原始输出。
  凭这一行可复查、可单独重跑（`--scenario <id>`）
- `runner.log` / `backend.log` — 双端时间戳日志
- `eval.db` / `workspaces/` — 当次运行的现场，保留供事后检查

## Judge 怎么配

Judge 直接 `httpx` 调 Anthropic 兼容网关（`POST /v1/messages`），要求输出严格 JSON
`{"score": 0|1|2, "reason": "..."}`（0=未达标, 1=部分, 2=达标）。verdict 规则：
平台检查全过 + judge 2 分 → PASS；judge 1 分 → PARTIAL；其余 → FAIL。

配置优先级（高→低）：

| 项 | CLI | 环境变量 | 默认（backend/.env） |
|---|---|---|---|
| 模型 | `--judge-model` | `EVAL_JUDGE_MODEL` | `AGENT_MODEL` |
| 网关 | — | `EVAL_JUDGE_BASE_URL` | `ANTHROPIC_BASE_URL` |
| Key | — | `EVAL_JUDGE_API_KEY` | `ANTHROPIC_AUTH_TOKEN` |

例：换 Claude 当 judge（被测模型不变）：
`EVAL_JUDGE_MODEL=claude-opus-4-8 EVAL_JUDGE_BASE_URL=https://api.anthropic.com EVAL_JUDGE_API_KEY=sk-ant-... uv run --project backend python evals/runner.py --all`

## 怎么加场景

在 `evals/scenarios/` 建一个模块，导出顶层 `scenario`（自动注册，不用改 runner）：

```python
from evals.lib.client import ai_messages, new_id
from evals.lib.context import EvalContext
from evals.lib.records import Check, Scenario, ScenarioOutcome

RUBRIC = """（judge 的 0-2 分验收标准，写清 2/1/0 各长什么样；
纯平台侧场景可以没有 rubric）"""

async def run(ctx: EvalContext) -> ScenarioOutcome:
    api = ctx.api
    # 1. setup：每场景自己建全新 project/topic（互不污染）
    project = await api.create_project(f"eval-X-{new_id()}", "eval-owner")
    topic = await api.create_topic(project["id"], "话题名", "eval-owner")
    # 2. steps：真实驱动——WS 发消息（api.send_chat）、REST 调结构接口、
    #    api.wait_turn_done() 等后台 turn（结构化 turn 状态，不解析文本）
    # 3. 收证据 + 平台侧确定性断言
    return ScenarioOutcome(
        inputs={...},          # 完整输入（进 results.jsonl，军规）
        evidence={...},        # 完整结构化产物
        checks=[Check(name="...", passed=True, detail="...")],
        judge_evidence="（给 judge 看的 markdown 现场，None=不判）",
    )

scenario = Scenario(
    id="X9", name="场景名", description="一句话", run=run,
    rubric=RUBRIC,           # None → 纯确定性场景
    timeout_s=600.0,
)
```

约定：

- **平台能确定性断言的，写 Check，别扔给 judge**（结构、链接、计数、状态机）；
  judge 只判语义质量（"回答用没用上记忆"、"开场白像不像接活的队友"）。
- 感知 agent 只用结构化数据：block 的 `author_type`/`kind`、`/debug/turns` 的
  turn 状态、WS frame 类型。**禁止对 AI 自然语言输出做模式匹配断言**（CLAUDE.md
  硬性规定）——语义判断交给 judge。
- `api.seed_memory()` 等 cheese-gated 写接口已带本次隔离后端的 sandbox token。

## 现有场景

| id | 场景 (docs/evals.md) | 验法 |
|---|---|---|
| A1 | 开话题（线上升格） | 平台检查（话题树/双向链接/简报文档）+ judge 判分身开场白 |
| C1 | @芝士带记忆回答 | 平台检查（种入/turn/回复/✅ack）+ judge 判记忆使用与依据 |
| C3 | 默认不接话 | 纯确定性：零 AI block、零 agent frame、零 in-flight turn |
