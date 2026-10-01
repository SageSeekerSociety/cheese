---
title: 计费流程
kind: 流程
summary: 用量怎么计、记在谁头上、用完了怎么办。
covers:
  - backend/app/domain/usage/
  - backend/app/domain/agent/budget_proxy.py
  - backend/app/domain/agent/gateway.py
---

# 计费流程 {#billing}

用量怎么计、记在谁头上、用完了怎么办。

> 讲：两处计量、折算、两道刹车。不讲：请求怎么走，见[模型调用流程](/dev/llm)。

## 额度从哪来 {#grants}

额度以「算力额度」（credits）为单位，挂在团队上（`compute_grants` 表）：

- 没有 `project_id` 的额度，团队下所有项目共用。
- 有 `project_id` 的额度只给这个项目用，例如某道题目的项目集发的资源包。

一个项目没有任何可用额度记录时，视为不限量。团队额度用完之后记录仍在，新建项目也绕不过去。

## 个人额度 {#personal}

不在项目里、由本人发起的 AI 调用（目前是文档站的问芝士）扣个人额度（`usage/personal.py`）：

- 额度记录有 `user_id` 和 `month`（平台时区下的月初），没有团队和项目。每人每月一条，数额是 `PERSONAL_CREDITS_MONTHLY`（默认 200）；第一次用到时写入，`(user_id, month)` 上的唯一索引保证并发时也只写一条。月底作废，不结转。
- 调用前查余额，用完就拒绝；调用后按实际 token 扣，所以余额最多被最后一次调用透支一次。
- 按网关上这个模型的单价折算，和网关自己算花费的方式一致：缓存命中的输入 token 按缓存价、其余输入按输入价、输出按输出价，合计的美元再除以 `LLM_GATEWAY_CREDIT_USD`。不同模型扣得不同。模型没配缓存价时，缓存命中的 token 按输入价算。没有单价或没设这个旋钮时无法计费，调用被拒绝，不退回按 token 折算。
- 每次调用写一行 `resource_usage`：`project_id` 为空，`user_id` 是付钱的人。平台看板的项目额度燃尽不算这部分。

## 两处计量 {#metering}

| 路 | 谁记 | 怎么进账 |
|---|---|---|
| 网关路 | LiteLLM 逐次记在项目虚拟 key 上 | 主 API 按天、按模型读取 `/spend/logs` 的累计差值，只消费一次 |
| 订阅路 | 计量代理每个 `/v1/messages` 响应写一行 `usage.jsonl` | 主 API 定时把新行收进 `resource_usage`，和检查点在同一个事务里推进，只收一次（`subscription_ingest.py`） |

交互式的 Claude Code 自己不报告用量，所以两条路都只能在流量经过的地方计量。

## 折算成额度 {#credits}

`backend/app/domain/usage/credits.py`：

- 默认按 token 折算：**1 额度 = 1 万 token**（`COMPUTE_CREDIT_TOKENS`，默认 10000）。订阅路把四类 token（输入、输出、缓存读、缓存写）全部计入。缓存读不免费，而且占大头：一次观测到 290 万缓存 token 对 14 万新 token。
- 走网关的轮次上，设置了 `LLM_GATEWAY_CREDIT_USD`、网关也报回了花费时，按真实花费折算：额度 = 花费 ÷ 每额度价格。缓存折扣因此会体现出来。

## 两道刹车 {#brakes}

```demo-sim
title: 两道刹车读同一份额度
note: 按 1 额度 = 1 万 token 折算，拖一拖看两道刹车各自什么时候拦
vars:
  - key: tokens
    label: 已经用掉的 token
    unit: 千
    min: 0
    max: 1000
    step: 20
    value: 300
  - key: total
    label: 团队给的额度
    unit: 额度
    min: 10
    max: 100
    step: 10
    value: 50
derived:
  - key: spent
    expr: tokens / 10
  - key: left
    expr: total - spent
rules:
  - label: 额度用完
    when: 'left <= 0'
    text: 准入拒绝，房间里出现平台提示；项目虚拟 key 的 max_budget 也已经在网关上把调用挡住了。
    tone: bad
  - label: 放行
    text: 准入放行，网关那边也还没到 max_budget。两道刹车读的是同一份额度，一个按额度数、一个按美元。
    tone: ok
out:
  - label: 已用，折算成额度
    expr: spent
    unit: 额度
  - label: 还剩（负数即超了）
    expr: left
    unit: 额度
```

1. **准入**：每个请求之前，主 API 比较已用额度和总额度，用完就拒绝。这一道在计量代理那边是软的：拿不到准入答案时放行，由订阅路自己的滚动 token 上限兜底。
2. **网关预算**：项目虚拟 key 的 `max_budget` 按额度设置，网关直接拒绝超额调用。

两道刹车读的是同一份额度，一个按额度数、一个按美元。

## 待拍板 {#open}

「1 额度 = 0.04 美元」这个口径，和网关里 GLM 的单价（配置为每 token 约 7.04×10⁻⁷ 美元）差约 5.7 倍：按 0.04 美元换算，1 额度实际能买约 5.7 万 token，而不是 1 万。要让两条口径一致，单价应是约 0.00704 美元。根因要等智谱真实账单核对。
