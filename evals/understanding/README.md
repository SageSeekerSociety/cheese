# 理解评测

回答议题 #4413 落到产品上之前要先知道的两件事，用平台常用的模型（glm-5.2、deepseek-flash）：

1. 芝士问人时，能不能带齐背景：在问哪件事、为什么要人定、选了会怎样。
2. 人说「没看懂」之后，芝士能不能一次讲懂。

不进 CI，改芝士说话的规矩、换模型之后手动跑，记下通过率前后比较。

## 题

`cases.py` 里 17 道题，8 道提问、9 道讲解，都从 dev 上人回「没看懂」的真实对话扩写成大学课程里的项目（`seed` 写着从哪一句来）。其中 4 道是留出题（`holdout=True`）：改规矩时不看它们，用来确认改动没有只贴合调整用的题。

每道题给判分人一位读者（例如「信息管理专业，管需求，不看代码」）和这件事的事实。判分人站在读者的位置，逐条判 true / false，全部做到才算过。标准是一个没读过代码的人也能判对错的：

- 提问：知道在问哪件事；知道为什么要他定；知道每个选项会怎样；没有没解释的生词；和事实一致。
- 讲解：读完能说出原来那条在说什么；知道对他意味着什么、要不要他做什么；没有新的生词；和事实一致；一次讲到位，不把证据当解释。
- e9 是人在纠正芝士的方向，标准换成「听出来是在纠正」「说清接下来去改对的地方」。

## 比的做法

| 做法 | 是什么 |
|---|---|
| `baseline` | main 上的提示词原样 |
| `rules` | 在说话的规矩里加两条原则（提问、被说没看懂），并改 `cheese_ask` 两个字段的说明（`run.py` 的 `ASK_RULE`、`EXPLAIN_RULE`、`ASK_FIELD_RULES`） |
| `rewrite` | 规矩不变，干活的芝士写完后，另一个芝士在发出前读一遍，保留、改写或补一句 |
| `memory` | `rules`，再把读者自己的记录（他是谁、懂什么）带进房间 |

芝士读到的系统提示词和工具说明是平台真实渲染出来的（`render_system`、`sandbox/cheese` 的工具表），改了 main 上的规矩，评测跟着变。房间的情况（项目、在场的人、对话、干活的记录）是手写的，因为真实的开场要读数据库；干活的记录以芝士自己读到一份工作记录的形式给它。

## 跑

需要一个 Anthropic 兼容的 `/v1/messages` 接口。评测不碰 dev：dev 是共用的，按生产对待。

```bash
export CHEESE_EVAL_BASE_URL=...   # 例如智谱或 DeepSeek 官方的 Anthropic 兼容地址
export CHEESE_EVAL_API_KEY=...

# 在仓库根目录
uv run --project backend python evals/understanding/run.py --dry-run a1      # 只打印拼好的请求
uv run --project backend python evals/understanding/run.py                   # 全部题、两个模型、四种做法、各 3 次
uv run --project backend python evals/understanding/run.py a1 e2 --repeat 5  # 几道题
```

两个模型在两家时，用 `名字@前缀` 分开配：

```bash
export ZHIPU_BASE_URL=... ZHIPU_API_KEY=... DEEPSEEK_BASE_URL=... DEEPSEEK_API_KEY=...
uv run --project backend python evals/understanding/run.py \
  --models glm-5.2@ZHIPU deepseek-flash@DEEPSEEK --judge 判分模型@前缀
```

判分默认用第一个模型。判分人和被测的是同一个弱模型时，分数偏松，有更强的模型就用 `--judge` 换上。

全量一次约 17 题 × 2 模型 × 4 做法 × 3 次 = 408 次，加上改写和判分约 1000 次模型调用。

结果写在 `evals/results/understanding-<时间>/`：`report.md` 给人看（通过率表、每题没做到哪条、每题第一次的消息原文），`results.jsonl` 每次一行，带完整请求、芝士发的消息、改写记录和判分理由。

## 改规矩的流程

1. 跑 `baseline`，只看调整用的题的失败，读消息原文和判分理由。
2. 先想能不能靠结构解决（比如把读者的记录带进房间），再改 `run.py` 里 `ASK_RULE`、`EXPLAIN_RULE` 那几行字；原则优先，做不到再补最少的话。
3. 重跑，调整用的题变好、留出题没有变差，才把同样的字改进 `backend/app/domain/prompts/` 和 `cheese_ask` 的说明。

## 原型

`prototype.html` 是做法一（写给读者）的任务页原型，芝士的位置留空。`prototype.py` 用真实模型把它填上：同一道题（默认 a1）分别用 main 上的规矩和改后的规矩跑两步，芝士先问，人回「没看懂」后芝士再答，页面顶上可以切换「现在」「改后」对比。

```bash
uv run --project backend python evals/understanding/prototype.py --model glm-5.2 --variant memory
```

生成的页面和完整请求写在 `evals/results/prototype-<时间>.html` / `.json`。
