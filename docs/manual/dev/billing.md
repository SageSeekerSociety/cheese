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

## 两处计量 {#metering}

| 路 | 谁记 | 怎么进账 |
|---|---|---|
| 网关路 | LiteLLM 逐次记在项目虚拟 key 上 | 主 API 按天、按模型读取 `/spend/logs` 的累计差值，只消费一次 |
| 订阅路 | 计量代理每个 `/v1/messages` 响应写一行 `usage.jsonl` | 主 API 定时把新行收进 `resource_usage`，和检查点在同一个事务里推进，只收一次（`subscription_ingest.py`） |

交互式的 Claude Code 自己不报告用量，所以两条路都只能在流量经过的地方计量。

## 折算成额度 {#credits}

`backend/app/domain/usage/credits.py`：

- 默认按 token 折算：**1 额度 = 1 万 token**（`COMPUTE_CREDIT_TOKENS`，默认 10000）。订阅路把四类 token（输入、输出、缓存读、缓存写）全部计入。缓存读不免费，而且占大头：一次观测到 290 万缓存 token 对 14 万新 token。
- 网关路在设置了 `LLM_GATEWAY_CREDIT_USD` 时，按网关给出的真实花费折算：额度 = 花费 ÷ 每额度价格。缓存折扣因此会体现出来。

## 两道刹车 {#brakes}

1. **准入**：每个请求之前，主 API 比较已用额度和总额度，用完就拒绝。
2. **网关预算**：项目虚拟 key 的 `max_budget` 按额度设置，网关直接拒绝超额调用。

两道刹车读的是同一份额度，一个按额度数、一个按美元。

## 待拍板 {#open}

「1 额度 = 0.04 美元」这个口径，和网关里 GLM 的单价（配置为每 token 约 7.04×10⁻⁷ 美元）差约 5.7 倍：按 0.04 美元换算，1 额度实际能买约 5.7 万 token，而不是 1 万。要让两条口径一致，单价应是约 0.00704 美元。根因要等智谱真实账单核对。
