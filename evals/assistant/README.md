# 个人芝士评测

两套评测，都不进 CI，改个人芝士的工具、规则或模型之后手动跑，记下分数前后比较。做法和指标见 `docs/manual/dev/assistant.md#eval`。

| 文件 | 做什么 |
|---|---|
| `gate_cases.py` | 授权判断的题：说过的话、要执行的操作、标准答案 |
| `gate_eval.py` | 用平台的 `gate.judge` 判这些题，不起后端 |
| `world.py` | 模拟工作区：林溪和她的课题组、助教课、别人的项目 |
| `tasks.py` | 问芝士的题，每题的核对项和评分要点 |
| `run.py` | 起后端、建工作区、逐题问芝士、核对、评分、写报告 |
| `boot.py` | 本机起真实后端：临时数据库、真实 pi、dev 网关 |
| `report.py` | 报告 |

## 准备

1. **评测专用的网关 key**，放在 `~/.config/cheese-eval/key`（不进仓库）。它在 dev 网关上开，有预算上限，只能调芝士和评审用的模型。没有就在 dev 机器上用网关的管理 key 开一把：

   ```bash
   ssh cheese-dev 'K=$(docker exec cheese-backend-1 printenv LLM_GATEWAY_ADMIN_KEY); curl -s -X POST -H "Authorization: Bearer $K" -H "Content-Type: application/json" http://172.17.0.1:4000/key/generate -d "{\"key_alias\":\"eval-personal-assistant\",\"models\":[\"deepseek-flash\",\"glm-5.2\"],\"max_budget\":30}"'
   ```

   评测从不拿网关的管理 key：平台要开的每把 key（个人的、授权判断的）都换成这一把，所以不会在 dev 网关上多出用户，也不会删掉 dev 正在用的 key。

2. **到 dev 网关的隧道**：`ssh -f -N -L 14000:172.17.0.1:4000 cheese-dev`。
3. **本机的 Postgres 和 Valkey**：和后端测试同一套，端口 5994 和 6994（`cheese-grading-pg`、`cheese-grading-valkey`）。评测自己建库、用完删掉，Valkey 用第 9 号库。
4. **pi**：`export CHEESE_TEST_PI=…`，和后端测试用的同一个固定版本。

## 跑

在仓库根目录：

```bash
uv run --project backend python evals/assistant/gate_eval.py              # 授权判断，约 2 分钟
uv run --project backend python evals/assistant/gate_eval.py --repeat 3   # 看稳不稳
uv run --project backend python evals/assistant/run.py                    # 全部题，约 15 分钟
uv run --project backend python evals/assistant/run.py t11 t17            # 几道题
uv run --project backend python evals/assistant/run.py --no-subagents t42 # 关掉分身再跑，和开着的比
```

结果写在 `evals/results/`（不进仓库）：`report.md` 给人看，`results.jsonl` 每题一行，带问题、回答、工具调用、授权判断、改了什么和用量。

## 调整

1. 只看调整用的题的失败，把记录交给 Claude Code 读，按它看出的问题改工具描述、返回格式或规则。
2. 重跑，确认调整用的题变好、留出的题没有变差。
3. 留出的题（`holdout=True`）不为它们改任何东西。
