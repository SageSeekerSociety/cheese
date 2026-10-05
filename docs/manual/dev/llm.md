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

```demo-steps
title: 一次模型请求经过哪几站
note: 右下角「幕后」是这次请求走过的几站，顶上是项目额度
embed: llm
steps:
  - label: 一个出口：计量代理
    desc: 所有会话的模型流量都经过计量代理。沙盒容器走 :443 反向代理，裸进程走 :8444 CONNECT 代理。
    link: /dev/llm#one-exit
  - label: 每个请求先问准入
    desc: 转发之前调主 API 的准入接口，回答能不能跑、走哪条路、用哪个模型名。每个请求现查，改绑模型不用重启会话。
    link: /dev/llm#admission
  - label: 走网关路，流式回来
    desc: 网关路换上项目的虚拟 key，按 key 记账。上游 key 不出平台主机。
    link: /dev/llm#routes
  - label: 额度用完：拒绝并说一声
    desc: 返回 allow false、reason_kind budget，话题里出现一条平台提示，这一轮不执行。
    link: /dev/llm#admission
  - label: 绑定的模型解析不出来
    desc: 返回 allow false、reason_kind binding，不会悄悄换到另一条路。
    link: /dev/llm#admission
  - label: 问不到主 API：软放行
    desc: 准入这一道是软的：计量代理放行，退回订阅路，由订阅路自己的滚动 token 上限兜底。
    link: /dev/llm#admission
  - label: 分身指定模型
    desc: 分身请求头带着和父会话不同的模型名，才算显式指定。在项目模型目录里、在允许范围内就用它，否则拒绝并列出能指定的。
    link: /dev/llm#subagent
```

## 一个出口：计量代理 {#one-exit}

计量代理内部怎么鉴权、计量、拒绝、换凭证，以及它的前身 ccproxy，见[计量代理](/dev/metering-proxy)。

所有会话的模型流量都经过计量代理（mitmproxy，`deploy/metering-proxy/`）。它有两个入口：

| 入口 | 谁用 | 怎么把流量引过来 |
|---|---|---|
| `:443` 反向代理 | 本机的沙盒容器 | 容器里改写域名解析（`--add-host`） |
| `:8444` CONNECT 代理 | 本机设备屏幕、云机器等裸进程 | `HTTPS_PROXY` 环境变量 |

订阅方式只能在传输层引流：设置 `ANTHROPIC_BASE_URL` 会让 Claude Code 切到 API key 模式、不再用订阅登录。

下面这张图把入口摊开：换入口、换场景，都能看到包从哪个口进、停在哪一站，每一站的面板写着它收到什么、又交出什么。额度用完时四个入口都停住，停的位置却不一样——容器、裸进程、云机器停在准入，Codex、Pi 停在网关。

```demo-arch
title: 换入口：包从哪进、在哪拦
note: 换入口、换场景，看包停在哪一站；被拦下的那一站在图上标出来
kind: llm
entries: sandbox, bare, cloud, codex
scenes: ok, budget
blocks: sandbox/budget, bare/budget, cloud/budget, codex/budget
```

## 每个请求先问准入 {#admission}

准入怎么判预算、怎么解析供给，见[准入与供给](/dev/admission)。

计量代理转发每个 `/v1/messages` 之前，调用主 API 的 `POST /llm/admission`（`backend/app/api/routes/llm_proxy.py`），用沙盒自己的短期令牌说明是哪个房间，再附上代理自己的凭据（`X-Cheese-Token`）——只有带着它，网关池的答复里才有项目 key。这是唯一的控制点，回答三件事：

```demo-sim
title: 每个请求先问准入
note: 准入只看这三件事，默认位置是「能跑、走绑定的那条路」
vars:
  - key: route
    label: 绑定的路
    type: choice
    options: 网关路 | 订阅路
    value: 网关路
  - key: left
    label: 项目还剩的额度
    unit: '%'
    min: 0
    max: 100
    step: 5
    value: 100
  - key: bound
    label: 绑定的模型能解析出来
    type: toggle
    value: true
  - key: reachable
    label: 主 API 能问到
    type: toggle
    value: true
rules:
  - label: 问不到主 API
    when: '!reachable'
    text: 准入这一道是软的：计量代理放行，退回订阅路，由订阅路自己的滚动 token 上限兜底。
    tone: warn
  - label: 绑定的模型解析不出来
    when: '!bound'
    text: 返回 allow: false、reason_kind: binding。不会悄悄换到另一条路。
    tone: bad
  - label: 额度用完
    when: 'left <= 0'
    text: 返回 allow: false、reason_kind: budget，并在话题里发一条平台提示「可用的 tokens 额度已用完，这轮没有执行」。
    tone: bad
  - label: 放行
    text: 返回 allow: true、reason_kind: budget，supply.pool 是{route}，模型名由计量代理写进请求体。
    tone: ok
out:
  - label: 这一轮能不能跑
    expr: '(bound && left > 0) || !reachable'
  - label: 剩的额度
    expr: left
    unit: '%'
```

它回答的三件事就是这段 JSON：

```json
{
  "allow": true,
  "reason": "1234.5000 of budget remaining",
  "reason_kind": "budget",
  "supply": { "pool": "gateway", "model": "<写进请求体的模型名>", "key": "<项目虚拟 key>" }
}
```

- **能不能跑**：问账本（`Ledger.admit`）：额度用完或方案的时间窗口满了就拒绝，拒绝的话说明何时恢复，并在话题里发一条平台提示。
- **走哪条路**：`supply.pool`，由这个 AI 队友或项目绑定的模型决定，每个请求现查，所以改绑模型不用重启会话。
- **用哪个模型名**：`supply.model`，由计量代理写进请求体。

绑定的模型解析不出来时，返回 `allow: false` 和 `reason_kind: "binding"`，不会悄悄换到另一条路。主 API 不可达时，计量代理放行并退回订阅路，同时靠它自己的滚动 token 上限兜底。

拒绝的形状由 `reason_kind` 决定，不是由「额度」这一件事决定：绑定解析不出去充值是白跑一趟，所以它回的是「重试没用」那个形状。下面这张图把不放行的两种、软放行的一种，和分身指定模型放在一起看（云机器走的是和裸进程同一条路，只多了隧道那两站）。

```demo-arch
title: 另外三种情形：绑错、问不到、分身指定
note: 绑定解析不出拦在准入，问不到主 API 退回订阅路，分身指定模型在准入这一站被翻译
kind: llm
entries: sandbox, bare
scenes: binding, failopen, subagent
blocks: sandbox/binding, bare/binding
```

## 两条路 {#routes}

网关那一侧（虚拟 key、预算刹车、补丁）见[模型网关](/dev/gateway)。

- **订阅路**：计量代理把请求转给模型厂商，把会话里的占位凭证换成平台的订阅凭证，并按准入结果把模型名写进请求体，正文其余部分不改（订阅要求客户端就是 Claude Code 本身）。
- **网关路**：计量代理把请求改写到 LiteLLM 网关，换上这个项目的虚拟 key。网关按 key 记账，超过 `max_budget` 就拒绝。

## Codex、Pi 和远端机器 {#others}

Codex 和 Pi 不能用 `HTTPS_PROXY` 引流，它们被指向 `{平台地址}/llm/v1`。主 API 的这条路由校验调用方的短期令牌，换上项目的虚拟网关 key，再把上游响应原样流回去。云机器的 Claude Code 则通过模型隧道把 CONNECT 流量带回主机上的计量代理（`backend/app/domain/agent/machine_tunnel.py`）。隧道助手跑在机器上、沙箱之外，拨的是这台机器到后端的那条路：平台的云机器拨自己回环上的 `127.0.0.1:18080`，那是 `deploy/cloud-control.py` 从后端主机反向转发过来的口，落在 api-front 上，由它把 `/llm/tunnel` 交给隧道进程，所以云机器不需要能到后端所在私网的任何地址；其余机器拨配置里的 `subscription_tunnel_url`（`machine_address.tunnel_url`）。

无论哪种，上游 key 都不出平台主机：机器上只有它自己的短期令牌。

## 分身用哪个模型 {#subagent}

计量代理从分身的请求体里读出模型名，放在请求头上交给准入接口；主 API 的 `_bind_requested_subagent_model`（`backend/app/api/routes/llm_proxy.py`）把它翻译成目录 id 并校验：

- `x-cheese-child-model` 带着**不同于**父会话的模型名时，才是主 agent 的显式指定，按指定处理。Claude Code 给每个分身都打这个头；值只是回显父会话模型时，计量代理把它认作继承、删掉这个头。
- 只有 `x-cheese-requested-model`、且名字就是父会话自己的模型时，说明分身只是继承，按「没有指定」处理，走项目的默认分身模型（`default_subagent_model`）。
- 小快家族的名字（含 `haiku` 的）从来不算指定：CLI 自己的后台类请求——会话标题、路径建议、WebFetch 的摘要子请求——带着它，而会话路径上没有映射这个别名（设备屏幕刻意不钉这三个家族别名）。计量代理在读到它的两条路上都按「没有指定」处理，头部那条快捷路与请求体那条一致；少了这条判据，一个目录外的 haiku 名字会被当成显式指定，把整个工具调用 400 掉。
- 指定的模型在项目模型目录里、且在允许范围内：就用它。
- 目录里没有，或不在允许范围内：拒绝，并在理由里列出可以指定的模型，不会悄悄换成默认模型。
