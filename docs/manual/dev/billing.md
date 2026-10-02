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

额度以「算力额度」（credits）为单位，一笔一笔的额度包（`compute_grants` 表）只挂在团队上，不发给团队里的个人。准入、扣费和余额都走 `usage/ledger.py` 这一处。每个包记着：

- `source`：来源。方案按期发放（`plan_period`）、题目发给项目的定向额度（`task_earmark`）、购买（`purchase`）、管理员发放（`admin_grant`）。
- `project_id`：有值时只给这个项目用。
- `expires_at`：到期后不能再用；为空的不过期。

一次调用由它发生的地方决定扣哪个团队：团队项目扣该团队；项目之外的调用和本人名下的项目扣本人名下的个人团队，名下项目里别人召唤芝士也扣所有者的。个人名下项目暂时不扣个人团队的月度方案包，月度包只付项目之外的调用。扣费按这个顺序，前一类用完再扣下一类，最后一次调用允许透支：

1. 题目发给这个项目的定向额度
2. 付钱团队本期的方案额度
3. 另购或管理员发放的额度，先到期的先扣

付钱的团队手上一个可用的额度包都没有时，是否放行由 `CREDITS_UNLIMITED` 决定（默认开，即不限量）。额度用完之后记录仍在，新建项目也绕不过去。包都过期之后才到账的花费，记在这个团队最近的一个包上。

每次扣费写的那一行 `resource_usage` 同时记下付钱的团队（`team_id`）和这一行扣了多少额度（`credits`），由 `Ledger.record` 一并写入和扣除。

## 方案 {#plans}

每个团队挂在一个方案上（`team.plan_key`，指向 `plans` 表），新建的个人团队和共享团队都是 Free。方案是表里的记录，不写在代码里：每期发多少额度、时间窗口限额（`[{hours, credits}]`）、允许哪些档位的模型、能不能用订阅模型、适用于个人、团队还是两者。迁移建了两条：Free（每月约 5 美元，按 `LLM_GATEWAY_CREDIT_USD` 折算，没设时 125 额度；只许 included 档的网关模型）和 Reserve（不限量、不限模型，只给平台自己的团队）。方案改了从下一期起生效，本期已发的包不变。

方案的这些规则目前只存储，准入和扣费还没有按它执行（#2397）。

方案、团队挂哪个方案、给团队发额度都只能由平台管理员在 `/admin/plans`、`/admin/teams` 下操作，每一次写都记进 `credit_admin_audit`（谁、对什么、改前改后），`/admin/credits/audit` 和 `/admin/teams/{id}/history` 读它。管理员发的额度可以写一句原因，只有管理员看得到。

## 个人额度 {#personal}

不在项目里、由本人发起的 AI 调用（文档站和题目页的问芝士、从 PDF 生成题目草稿）扣本人名下个人团队的额度。平台自己发起的调用（旧记忆搬迁）不扣个人额度，走一把自带预算的网关 key。

- 个人团队每月一个方案包，数额是 `PERSONAL_CREDITS_MONTHLY`（默认 200），到平台时区的下月初作废，不结转。本人第一次在项目之外用到时写入，`(team_id, period_start)` 上的部分唯一索引保证并发时也只写一条。
- 调用前查余额，用完就拒绝，并写明哪天重置；调用后按实际花费扣，所以余额最多被最后一次调用透支一次。
- 按网关上这个模型的单价折算，和网关自己算花费的方式一致：缓存命中的输入 token 按缓存价、其余输入按输入价、输出按输出价，合计的美元再除以 `LLM_GATEWAY_CREDIT_USD`。不同模型扣得不同。模型没配缓存价时，缓存命中的 token 按输入价算。没有单价或没设这个旋钮时无法计费，调用被拒绝，不退回按 token 折算。
- 题目页的芝士不自己算：每个人有一把自己的网关虚拟 key，芝士的每次模型调用都走它，而且只在这个人的一个问题正在回答时才放行。每次提问后读这把 key 上新增的花费（和项目 key 一样按天、按模型读累计差值，只消费一次），直接按网关算出的美元扣（[个人芝士](/dev/assistant#ask)）。
- 每次调用写一行 `resource_usage`：`project_id` 为空，`user_id` 是发起的人。平台看板的项目额度燃尽不算方案包。

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
2. **网关预算**：项目虚拟 key 的 `max_budget` 按额度设置，网关直接拒绝超额调用。key 上的花费是累计的，所以这个数按项目所有用得上的额度包算，已经过期的也算在内。

两道刹车读的是同一份额度，一个按额度数、一个按美元。

## 待拍板 {#open}

「1 额度 = 0.04 美元」这个口径，和网关里 GLM 的单价（配置为每 token 约 7.04×10⁻⁷ 美元）差约 5.7 倍：按 0.04 美元换算，1 额度实际能买约 5.7 万 token，而不是 1 万。要让两条口径一致，单价应是约 0.00704 美元。根因要等智谱真实账单核对。
