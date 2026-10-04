---
title: 模型网关
kind: 参考
summary: LiteLLM 网关：项目虚拟 key、预算刹车和记账。
covers:
  - deploy/gateway/
  - backend/app/domain/agent/gateway.py
  - backend/app/domain/agent/gateway_admin.py
  - backend/app/domain/agent/gateway_catalog.py
  - backend/app/domain/agent/gateway_models.py
  - backend/app/api/routes/admin_models.py
---

# 模型网关 {#gateway}

自托管的 LiteLLM：所有走 API-key 池的模型请求都从这里出去，平台对模型的认知（有哪些、多少钱、谁烧了多少、谁的刹车该跳）全部来自它。

> 讲：网关这一摞自己是什么、记账口径、模型目录从哪来、上游镜像打了几处补丁、日志里有什么。不讲：请求怎么被路由到这里（见[计量代理](/dev/metering-proxy)），准入怎么决定用池还是订阅（见[准入与供给](/dev/admission)），用量怎么折算成 credit（见[计费](/dev/billing)）。

## 为什么有它 {#why}

平台曾经连着几周不知道自己在花什么钱：两个星期里用量表只有四行，同期三百块模型额度流干了，而那四行里没有一个项目名、一个回合、一个 token。这不是 bug——hooks 后端跑的是交互式 Claude Code，它本地根本不上报 token 用量，所以设计改成从自托管网关读账（`backend/app/domain/agent/gateway.py`）。当时代码写了、配置项留了，网关没部署，于是 `gateway` 解析成 `None`，每一行用量都被静静地跳过。

它给三样东西：

| 给什么 | 怎么给 |
| --- | --- |
| 记账 | 网关自己数据库里每次调用一行，抽成「每项目、每模型、当天累计增量」写进 `resource_usage` |
| 归属 | 一个项目一把虚拟 key，花销可归因，沙箱手里永远不是上游 key |
| 刹车 | 那把 key 上的 `max_budget`，花超了网关直接拒，而不是等到发票上才发现 |

## 独立的一摞 {#stack}

- 它有自己的一套 compose，故意的：`deploy-docker.sh` 只认一个 compose 文件、只拉起 `backend frontend`，而那个文件跟生产共用。**应用发布不碰网关，重启网关也不会重启应用。**
- 镜像流水线只在 `deploy/gateway/` 变了的时候重建网关，否则复用上一版镜像、打成当前 commit 的 tag。构建过程会跑适配与时序测试，不发真实请求。
- 发布走 **Release gateway** 工作流（`main` 上、镜像构建成功的完整 SHA、显式确认会打断正在流的会话）；凭据从 `$HOME/gateway/compose/.env` 读，旧镜像与配置先存进 `$HOME/gateway/releases/`，健康检查不过就回滚。
- **不发布端口**：它加入应用所在的 docker 网络。绑宿主机环回，容器够不着（应用在自己的网桥上）；绑网桥地址，等于把上游 key 和管理 API 摊在机器的局域网上。加入网络就不用选了。

## 一个项目一把虚拟 key {#keys}

- `LlmGateway.mint_project_key` 给项目铸一把虚拟 key，沙箱拿到的是它，不是 master key；`set_key_budget` 把 `max_budget` 落到这把 key 上。
- 额度从项目的计算额度折算（`chat._gateway_budget_target`：项目还能花的额度 × 0.01 美元；不限量的方案是 `None`，也就是不设上限）。
- key 与环境变量的写入要分读/写两条路：读不取锁，写在 `_gateway_lock` 下；2026-09-23 实测过 20 个并发准入，尾部 5.8 秒。

## 记账口径：每模型、当天累计增量 {#accounting}

- 每次 drain 拉 `/spend/logs`，按项目的虚拟 key 过滤（行里的 `api_key` 是 key 的 sha256；实测过——key 的 `user_id` 到不了这些行），按行里的 `model` 分组，跟存的检查点做差，把差值消费掉。累计量是单调的，所以晚记的日志只会把后一次 drain 撑大，不会重复计。
- **每模型分开这一条不是可选项**：把行加总、盖一个模型名，等于把混着好几个模型的一天记到调用方碰巧拿着的那一个上——`mimo` 就是这么在面板上消失的。
- 每次 drain 先把检查点以来的每一天按模型结清，再开今天。
- `_TIMEOUT = 60.0` 秒是有原因的：`/spend/logs` 没有可用索引、全表扫 LiteLLM 的花销表，2026-09-23 实测 24–102 秒。之前写 8 秒，每次 drain 都超时、静默记成零（异常被吞掉返回 `None`，调用方照样写一行「未计量」）。
- 这里所有东西都是尽力而为：网关或管理 API 挂掉只记日志、返回明确的「不知道」，既不能弄失败一个回合，也不能被当成一个权威的零。

## 模型目录：只有它一份清单 {#catalog}

只有网关知道它路由什么——它不路由的模型，平台里任何一张表写着也跑不起来。所以平台自己不存清单，只存网关上一次的答案，外加一个从没有过答案时的下限：

- `GatewayCatalog.snapshot()` 是同步的，`model_choices` 在开回合和校验 agent 的路径上，不能发网络请求，调用方也不该为了问有哪些模型而变成 async。定时刷新（`REFRESH_INTERVAL_SECONDS = 300`）；从来没拿到过答案时快速重试（`FIRST_ANSWER_RETRY_SECONDS = 5`），因为网关是独立的一摞、很可能比应用起得晚。
- **刷新失败绝不让目录变小**：空列表和「网关不可达」在选单里长得一样，事实上相反——前者是「这个部署什么都不提供」，后者是「待会儿再问」。混淆两者，一次网络抖动就能把整个部署的 agent 全下线。
- 从没有过答案时的下限是 `settings.agent_model` 的那一条：这是这个部署配置了要跑的模型，也是不问任何人就能说出的唯一名字。
- `price_is_set` 要求双向都有价：只在一边定价的模型，一半流量按零计费，跟完全没价是同一种静默刹车失灵，只是以一半的速度到达。两个来源（`litellm_params` 与 `model_info`）都要读，因为这个仓库自己的配置两种都用了（GLM 系列写在前者，`deepseek-flash` 写在后者），只读一个会把所有 GLM 报成没价、从目录里掉出去。

## 上游镜像与补丁 {#patches}

Dockerfile 从上游镜像的 digest 派生，打补丁前先核对被改文件哈希；`deploy/gateway/patch_*.py` 各管一件事：

| 文件 | 修什么 |
| --- | --- |
| `patch_stream_timing.py` | 上游流式 logger 从包装器创建时才起表，漏掉前面的请求时间；补丁保留日志对象原本的请求开始时间，并让流拿到一个由自己持有的 HTTP 客户端 |
| `retry_stream.py` | `ClientOwnedStream` 让客户端活到流关闭，避免重试期间连接被提前回收 |
| `patch_deepseek_images.py` | 上游只保留 user 消息里的图片；工具结果（Claude Code 读本地图片）在适配后是 tool 消息，补丁让它也保留图片 |
| `patch_empty_anthropic_text.py` | 丢掉空的 system 文本块、剥掉嵌套 tool_result 里的空文本 |
| `provider_http_timing.py` | 打 `provider_http_timing` 记录：DNS/TLS/超时/401·403/429/1113 配额/其它 HTTP 错误分类，未知的 500 不会被说成网络原因 |
| `check_config.py` | 用 config.yaml 建一个 Router，断言 thinking 与 effort 的转换行为 |

## 日志里有什么 {#logs}

| 日志行 | 谁打的 | 字段 | 不含什么 |
| --- | --- | --- | --- |
| `gateway_request_timing` | 计量代理（`_log_gateway_timing`） | 请求与响应时间戳、选路结束时刻、准入耗时、`x-litellm-call-id`、状态 | 正文、凭据、任意请求头 |
| `provider_http_timing` | 网关（补丁） | 进入请求、响应头、流完成/中断/提前关闭的时刻与时长、状态、结果分类 | URL、头、凭据、正文 |

两个区间都含代理与网关自身的工作，也都含连接建立、重试与传输，所以它们**不是**「上游推理耗时」。失败的请求保留已有的时间戳。`x-litellm-call-id` 是把两边日志与网关花销日志对起来的那根线。

## backend 侧的这一摞 {#backend}

| 模块 | 是什么 |
| --- | --- |
| `gateway.py` | 管理客户端，L0/L1/L2 三个层次的定义就写在这个文件的 docstring 里；铸 key、设额度、drain 用量 |
| `gateway_admin.py` | 「会抛」的管理客户端（管理页必须看得见失败）：`GatewayUnreachable` / `GatewayRefused` 分开抛，凭据从不回显或落日志 |
| `gateway_catalog.py` | 目录快照与定时刷新（上面那一节） |
| `gateway_models.py` | 管理页的服务：模型与逐模型用量、项目额度；`_TTL_SECONDS = 15`，因为 `/model/info` 与一周用量都是百 KB 级 |
| `api/routes/admin_models.py` | `/admin/gateway` 的九条路由：模型列表/详情/新建/改/删/停用、项目额度、审计 |

翻译的规矩：**连接性失败由路由层翻**（不可达 503、被拒 502），页面才能告诉人「是网关挂了，过会儿再试」还是「你这个动作本身不被允许」；「模型不存在」404、「请求体不合法 / 不变式被违反 / 对 config 模型写」400 由服务层抛，因为那几种要读库或读网关才知道。

## 怎么加一个模型 {#add-model}

两条路，落点相同，因为平台读的是 `/model/info`：

- **写 `config.yaml`**：加一条路由和价格，在 `model_info` 下写 `cheese_selectable: true`；`cheese_tier` 写这个模型的档位（included / premium / frontier，缺省 included），方案按它限定可用的模型。这是随镜像发布的基线，改它要发布网关。标记是 opt-in 的，因为网关也路由不上菜单的模型——`glm-4.5` 是分身别名指向的地方。
- **管理页**：管理员增删改停运行时模型（`STORE_MODEL_IN_DB` 打开），不用发布；每次写都记审计（谁做的），写成功后刷新目录，模型立刻可选。`config.yaml` 里声明的模型在页面上是只读的，网关不许写它们。

两条路欠同一个不变式，服务层与 `check_config.py` 各守一边：**没价的可选模型等于不上架**。它的 token 会按零计费，项目的 `max_budget` 永远不会跳，第一个征兆是发票——模型从选单里消失会被发现，一个悄悄失灵的刹车不会。改完清单跑：

```sh
docker exec -i cheese-gateway-litellm-1 python - /app/config.yaml < deploy/gateway/check_config.py
```

价格用人民币rate 除以「7.1 人民币/美元」这个既有约定折算，是估算而不是实时汇率（`kimi-k3`、`mimo-v2.6-pro` 的注释里都写了各自的原始人民币价）。Kimi K3 走原生 Messages 端点 `https://api.moonshot.cn/anthropic`，发布带它的配置前要在网关 `.env` 里备好 `MOONSHOT_API_KEY`。GLM 系列必须用 `anthropic/` 前缀而不是 `openai/`：调用方说的是 Anthropic 协议，被 `openai/` 前缀一转，LiteLLM 会翻成 OpenAI Responses API——智谱那条路是 404。

## 边界与坑 {#traps}

- 健康检查只验 `/health/readiness` 与带认证的模型目录，**不验供应商配额，也不真的推理**。要一次有界的推理验证，跑 `backend/scripts/gateway_supply_probe.py --generate --model kimi-k3`：一次原生 Messages 请求、512 token 输出上限、60 秒期限，缺终止事件、认证、配额、限流、传输错误都算失败；打到输出上限与流断掉是分开报的。探针只打分类与 token 数，不打供应商原文或生成内容。
- 网关最多留五个 20 MB 日志文件，重建容器不保留 stdout 历史。
- 换一个模型是在下一个任务边界刷新原生会话；scoped 会话凭据带着所选模型的路由，分身的别名跟着 API 模型走。
- 中央会话的 `AGENT_SESSION_API_BASE` 必须从它自己的私有执行容器里够得着：填宿主机环回地址，容器里的平台工具会 connection refused。
