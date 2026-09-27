---
title: 模型调用流程
kind: 流程
summary: 一次模型请求从芝士出发，到拿回 token 的完整路径。
covers:
  - backend/app/api/routes/llm_proxy.py
  - deploy/metering-proxy/
  - backend/app/domain/agent/machine_tunnel.py
  - backend/app/llm_tunnel_app.py
---

# 模型调用流程 {#llm}

一次模型请求从芝士出发，到拿回 token 的完整路径。

> 讲：请求经过哪些节点、每个节点做什么决定。不讲：用量怎么折算成额度，见[计费流程](/dev/billing)。

## 一个出口：计量代理 {#one-exit}

所有会话的模型流量都经过计量代理（mitmproxy，`deploy/metering-proxy/`）。它有两个入口：

| 入口 | 谁用 | 怎么把流量引过来 |
|---|---|---|
| `:443` 反向代理 | 本机的沙盒容器 | 容器里改写域名解析（`--add-host`） |
| `:8444` CONNECT 代理 | 本机设备屏幕、云机器等裸进程 | `HTTPS_PROXY` 环境变量 |

订阅方式只能在传输层引流：设置 `ANTHROPIC_BASE_URL` 会让 Claude Code 切到 API key 模式、不再用订阅登录。

## 每个请求先问准入 {#admission}

计量代理转发每个 `/v1/messages` 之前，调用主 API 的 `POST /llm/admission`（`backend/app/api/routes/llm_proxy.py`），用沙盒自己的短期令牌鉴权。这是唯一的控制点，回答三件事：

```json
{
  "allow": true,
  "reason": "1234.5000 of budget remaining",
  "reason_kind": "budget",
  "supply": { "pool": "gateway", "model": "<写进请求体的模型名>", "key": "<项目虚拟 key>" }
}
```

- **能不能跑**：比较项目可用额度和已用额度（`budget_proxy.decide`）。额度用完时拒绝，并在话题里发一条平台提示「可用的 tokens 额度已用完，这轮没有执行」。
- **走哪条路**：`supply.pool`，由这个 AI 队友或项目绑定的模型决定，每个请求现查，所以改绑模型不用重启会话。
- **用哪个模型名**：`supply.model`，由计量代理写进请求体。

绑定的模型解析不出来时，返回 `allow: false` 和 `reason_kind: "binding"`，不会悄悄换到另一条路。主 API 不可达时，计量代理放行并退回订阅路，同时靠它自己的滚动 token 上限兜底。

## 两条路 {#routes}

- **订阅路**：计量代理把请求转给模型厂商，把会话里的占位凭证换成平台的订阅凭证，并按准入结果把模型名写进请求体，正文其余部分不改（订阅要求客户端就是 Claude Code 本身）。
- **网关路**：计量代理把请求改写到 LiteLLM 网关，换上这个项目的虚拟 key。网关按 key 记账，超过 `max_budget` 就拒绝。

## Codex、Pi 和远端机器 {#others}

Codex 和 Pi 不能用 `HTTPS_PROXY` 引流，它们被指向 `{平台地址}/llm/v1`。主 API 的这条路由校验调用方的短期令牌，换上项目的虚拟网关 key，再把上游响应原样流回去。云机器的 Claude Code 则通过模型隧道把 CONNECT 流量带回主机上的计量代理（`backend/app/domain/agent/machine_tunnel.py`）。

无论哪种，上游 key 都不出平台主机：机器上只有它自己的短期令牌。

## 分身用哪个模型 {#subagent}

计量代理从分身的请求体里读出模型名，放在请求头上交给准入接口；主 API 的 `_bind_requested_subagent_model`（`backend/app/api/routes/llm_proxy.py`）把它翻译成目录 id 并校验：

- `x-cheese-child-model` 带着**不同于**父会话的模型名时，才是主 agent 的显式指定，按指定处理。Claude Code 给每个分身都打这个头；值只是回显父会话模型时，计量代理把它认作继承、删掉这个头。
- 只有 `x-cheese-requested-model`、且名字就是父会话自己的模型时，说明分身只是继承，按「没有指定」处理，走项目的默认分身模型（`default_subagent_model`）。
- 指定的模型在项目模型目录里、且在允许范围内：就用它。
- 目录里没有，或不在允许范围内：拒绝，并在理由里列出可以指定的模型，不会悄悄换成默认模型。
