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
  - backend/scripts/gateway_conformance.py
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
- 发布走 **Release gateway** 工作流（`main` 上、镜像构建成功的完整 SHA），不断流，做法见[发布不断流](#release)。凭据从 `$HOME/gateway/compose/.env` 读，每次发布的配置存进 `$HOME/gateway/releases/`。
- 调用方找的是 `gateway-netns` 占着的地址，后面是 HAProxy：沙箱和计量代理走 `172.17.0.1:4000`（docker0 网桥地址，容器够得着、局域网够不着），后端走应用网络上的别名 `http://litellm:4000`。两个 LiteLLM 实例 `litellm-blue`、`litellm-green` 不发布端口，也不在应用网络上。

## 发布不断流 {#release}

Claude Code 的一条流常常要几分钟，流到一半断掉的不会重放，所以发布不能重启正在服务的实例。

- **拓扑**：地址归 `gateway-netns` 所有，它只占着网络命名空间：发布的端口 `172.17.0.1:4000`、应用网络上的别名 `litellm`、内部网络 `colors` 上的地址。HAProxy（`gateway-proxy-a` 或 `-b`）跑在它的命名空间里，后面是两个颜色 `litellm-blue`、`litellm-green`，同一份 compose 定义，共用 `litellm-db` 和 `litellm-redis`。平时只运行一个代理、一个颜色。
- **为什么平时只留一个颜色**：路由器的冷却状态和 `deepseek-flash` 的会话亲和都在进程内存里（`router_settings` 没配 Redis），两个颜色同时接流量，各管各的冷却和亲和，同一个会话会在两边的 RUC 节点之间来回、前缀缓存一直不命中。第二个颜色常驻也只有网关自己崩掉时才有用；真到那一步再考虑，并先把路由状态放进 Redis。
- **一次发布**：先认出正在服务的颜色：容器在运行、在 HAProxy 里既没被排空也不在维护状态的那一个，看的是管理状态而不是某一刻的健康检查。两个都符合（重启发生在排空中途）就取较新的那个；有颜色在运行却一个都不符合，就什么都不动、报失败。然后在空闲的颜色上起新镜像，依次过四关：docker 健康检查；`/health/readiness`（查数据库）；直接对新颜色发一次 `GET /v1/models`（带 master key），它列出的模型必须包括正在服务的颜色列出的每一个，只有本次发布从 `config.yaml` 删掉的除外，所以管理页加在数据库里的模型没加载完不会接流量；HAProxy 自己的检查看到它起来。这里不用 `/health`，它会真的去调每个模型。四关都过，再通过 HAProxy 的运行时 API 一条命令把它设成 ready、把旧颜色设成 drain。HAProxy 不重启，调用方的连接一条都不断；HAProxy 按请求选后端，所以计量代理复用的旧连接，下一个请求就落到新颜色。旧颜色继续把手上的流都传完，脚本每 5 秒读一次它在 HAProxy 里的 `scur`，到 0 才停它，最多等 30 分钟（`GATEWAY_DRAIN_SECONDS`）。到点还有请求没完，就照样停，并在工作流里打一条 `::warning::`，写明切断了几个。
- **停的时候**：LiteLLM 收到 SIGTERM 会先把在途的流都传完，再把内存里的花销计数和花销日志写进数据库；`stop_grace_period: 60s` 就是留给这一步的，docker 默认的 10 秒到点会 SIGKILL。
- **重试只在连不上时**：`retry-on conn-failure` 加 `option redispatch`，即颜色拒绝或没接住连接时换另一个颜色再试（HAProxy 3.2 不写 `retry-on` 时默认也是这个，这里写明）。请求已经发出去之后的失败一律不重试：那时 LiteLLM 可能已经调了上游，再发一次就付两次钱。HAProxy 的持续检查用 `/health/liveliness` 而不是 readiness：数据库一抖，readiness 会让两个颜色同时下线。
- **失败与回滚**：新颜色过不了任何一关，就删掉它，流量一直留在旧颜色上，发布报失败。上线后要回退，就用同一个工作流发布上一个 SHA，走的也是不断流的路。
- **颜色用固定地址**（内部网络 `10.213.47.0/28`），不用容器名：docker 内置 DNS 不认识的名字会转给宿主机的解析器，dev 机器的解析器对任何名字都回一个 `198.18.x` 的地址，于是没在运行的颜色照样解析得到一个地址，请求会被送到一个不属于任何颜色的地址。
- **代理本身的变化也不断流**：`deploy/compose/gateway-proxy.cfg` 或代理的镜像、定义一变，发布就在同一个命名空间里起另一个代理，两个都监听 `:4000`（SO_REUSEPORT），新代理带着从旧代理导出的服务器状态（`server-state-file`，含 drain 标记）启动；它一起来，这个文件就删掉，因为之后颜色都靠运行时 API 切换，留着它，下次重启或崩溃重启的代理会读到过时的 drain 标记，把正在服务的颜色当成已排空。确认它看得到正在服务的颜色后，给旧代理 SIGUSR1 软停：它马上不再接新连接，已经排在它监听队列里的连接由内核转给新代理（`net.ipv4.tcp_migrate_req`），手上的连接照常服务完，空闲连接上的下一个请求照常处理、回 `Connection: close` 再关（`option idle-close-on-response`）。它最多活 35 分钟（`hard-stop-after`）；旧颜色要等它退出才停，因为它带着的流不在新代理的计数里。
- **只有换地址本身会断流**：`gateway-netns` 的端口、网络或镜像一变就得重建它，所有连接都断，工作流要勾上 `interrupt` 才会执行。
- **从单容器迁过来那一次**：旧的 `cheese-gateway-litellm-1` 占着 `172.17.0.1:4000` 和别名 `litellm`，docker 不会把它们从一个还在运行的容器上挪走，所以第一次发布要勾 `interrupt`。脚本先把 `litellm-blue` 起好、过完四关（模型清单对照旧容器），然后最多等 10 分钟（`GATEWAY_MIGRATION_QUIET_SECONDS`），等到旧容器没有一条连接在 30 秒内收发过数据（从它的网络命名空间里用 `ss` 看，需要 sudo），再给它 SIGTERM，给在途请求 30 秒（`GATEWAY_MIGRATION_GRACE`），然后启动 `gateway-netns` 和 `gateway-proxy-a`。这段时间里新请求被拒，计量代理连到旧容器的空闲长连接全部作废，每条上的下一个请求会失败一次。代理起不来，或者发布在这一步被取消，都会把旧容器重新拉起。
- **两个颜色同时运行的那几分钟**：LiteLLM 启动时自己跑数据库迁移，新颜色会在旧颜色还在服务的时候改共享的数据库；LiteLLM 的迁移一般只加不删，但升级上游镜像的那次发布要看一眼迁移里有没有删列或改列。新颜色的冷却和亲和从空开始，所以每次发布后每个会话第一次请求会重新选一次 RUC 节点、丢一次前缀缓存。预算刹车的花销计数在 `coordination_redis` 里，两个颜色共用，不受影响。

## 一个项目一把虚拟 key {#keys}

- `LlmGateway.mint_project_key` 给项目铸一把虚拟 key，沙箱拿到的是它，不是 master key；`set_key_budget` 把 `max_budget` 落到这把 key 上。
- 额度从项目的计算额度折算（`chat._gateway_budget_target`：项目还能花的额度 × 0.01 美元；不限量的方案是 `None`，也就是不设上限）。
- key 与环境变量的写入要分读/写两条路：读不取锁，写整段握着项目设置的行锁（`repo.lock_settings`）串行；曾经这里还有一把进程内的 `_gateway_lock` 挂在读写两条路上，2026-09-23 实测过 20 个并发准入、尾部 5.8 秒，于是把锁从读路上拿掉，写路最终也只留行锁。

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

还有一项要按模型手动标：**`supports_mid_conversation_system: false`**。Claude Code 会把技能清单、运行环境和日期放在对话中间的 system 消息里；有的线路收不了它，有的是网关转换时丢掉，有的是上游丢掉或整条请求拒绝。标了 `false` 的模型，平台启动 Claude Code 时设 `CLAUDE_CODE_MODEL_CAPABILITIES`，让它把这些内容并进第一条用户消息（`gateway_usage.model_capabilities`）。没标的照 Claude Code 的默认。这一项只在量过之后手动标，不按上游推断：线路以后支持了，把标记去掉就行。量法是从后端经网关发一条对话中间带暗号的 system 消息，问模型暗号是什么。`config.yaml` 里的模型写在条目里，运行时模型在管理页的能力开关里关掉。

两条路欠同一个不变式，服务层与 `check_config.py` 各守一边：**没价的可选模型等于不上架**。它的 token 会按零计费，项目的 `max_budget` 永远不会跳，第一个征兆是发票——模型从选单里消失会被发现，一个悄悄失灵的刹车不会。唯一的例外是上游真不收钱的模型：`model_info` 里写 `cheese_free: true`，并把输入、输出单价都**写明**为 0（`usage.pricing.declared_free`；`check_config.py` 守同一条）。没有标记的 0 仍是没价。改完清单跑：

```sh
docker exec -i cheese-gateway-litellm-blue-1 python - /app/config.yaml < deploy/gateway/check_config.py   # 或正在服务的 green
```

价格用人民币rate 除以「7.1 人民币/美元」这个既有约定折算，是估算而不是实时汇率（`kimi-k3`、`mimo-v2.6-pro` 的注释里都写了各自的原始人民币价）。Kimi K3 走原生 Messages 端点 `https://api.moonshot.cn/anthropic`，发布带它的配置前要在网关 `.env` 里备好 `MOONSHOT_API_KEY`。GLM 系列必须用 `anthropic/` 前缀而不是 `openai/`：调用方说的是 Anthropic 协议，被 `openai/` 前缀一转，LiteLLM 会翻成 OpenAI Responses API——智谱那条路是 404。

## 上线前：跑一遍一致性套件 {#conformance}

新模型、新 key、换中转站、升级 LiteLLM，上线前都对着网关跑一遍 `backend/scripts/gateway_conformance.py`。它发的就是 Claude Code 发的那种请求（流式 `/v1/messages`、Claude Code 的 beta 头和 system 布局、adaptive thinking 加 effort），每一项对应一类曾经在生产上才被发现的问题：

| 检查 | 断言 |
| --- | --- |
| `stream_text` | 流式文本到达、以 `message_stop` 收尾、`stop_reason` 是 `end_turn`、usage 非零 |
| `tool_call` | 一次工具调用，参数非空且是合法 JSON |
| `parallel_tool_calls` | 一次要三个并行调用，每个参数都非空；模型只调一次记 warn |
| `tool_result_roundtrip` | 回传 tool_result 后模型用它作答 |
| `image_in_tool_result` | tool_result 里放一张写着单词的 PNG，回答必须含这个词；没声明 `supports_vision` 就 skip |
| `thinking_effort` | `cheese_efforts` 里每一档都不被拒；声明了 `supports_reasoning` 的要有 thinking 块 |
| `mid_conversation_system` | 对话中间的 system 消息被遵守，或模型已标 `supports_mid_conversation_system: false` |
| `cache_hit` | 两次相同的约 6k token 前缀，第二次 cache_read 超过提示的一半；没有缓存读价格的模型只 warn |
| `chat_completions` | pi 走的 `/v1/chat/completions`（pi 的 compat 形状），文本与工具调用各一次 |

```sh
GATEWAY_KEY=<项目 key> python3 backend/scripts/gateway_conformance.py \
  --base <网关地址> --model deepseek-flash --json conformance.json
```

- 不带 `--model` 就查 `/model/info` 里每个 `cheese_selectable` 的模型；`--checks stream_text,tool_call` 只跑其中几项；`--list` 列出模型和每个部署的 `model_info.id`。
- `--deployment <model_info.id>` 只打模型组里的一个部署：LiteLLM 把 `model` 是部署 id 的请求直接路由到那一个部署。按模型组生效的 pre-call 钩子（去掉计费行、会话亲和）这时不起作用，所以钉住部署时缓存未命中只记 warn。
- 请求逐个发，一个模型几美分；有一项 fail 就退出码 1。失败原因里带供应商的报错原文和模型回答的片段，所以输出是给操作的人看的，别贴进共享日志。

### 订阅与中转：同一个模型挂多条供给 {#supply-rows}

一个模型组可以有多行（多个 deployment），路由器按 `litellm_params.order` 从小往大用：先用最小 `order` 里健康的，任何错误（配额用完的 429 在内，上下文超长与内容审查除外）都在同一个请求里落到下一档；没写 `order` 的行只在所有带 `order` 的都不健康时才用。所以「订阅额度优先、按量付费兜底」就是订阅那行 `order: 1`、官方 API 那行在后面。被 429 的行冷却约 5 秒后再试。

有的上游要自己的请求头（`upstream_headers.py`），按行声明在 `model_info` 里，只发给声明了的那一行，同组兜底的官方行收不到：

- `cheese_upstream_headers: {user-agent: cheese-gateway/1.0}`：原样发送的固定头。
- `cheese_session_header: x-opencode-session`：用这个头名带上会话。值是 Claude Code 的 `X-Claude-Code-Session-Id`（校验同 `chatgpt_session.caller_session`）；调用方没有（pi 走 `/v1/chat/completions`、后端 `GatewayChat` 的功能）时，用「调用方虚拟 key + 第一条用户消息」的哈希，同一段对话每次相同、不同 key 永不相同；只有连用户消息都没有的请求才给随机值。声明了就一定发——OpenCode Go 缺这个头直接 400 `MissingSessionID`。

上游只有 OpenAI 兼容的 `chat/completions`、没有 Anthropic Messages 时，前缀用 `hosted_vllm/`，`api_base` 写到 `/v1` 为止：`openai/` 会被翻成 Responses API（`/responses`），`custom_openai/` 会丢掉 reasoning effort、还把 OpenAI SDK 的名字拼进 User-Agent。`test_chat_route.py` 用假上游守着 thinking、工具调用、工具历史和图片两个方向都完整。有原生 Messages 的上游用 `anthropic/`，`api_base` 写到 `/v1` 之前（LiteLLM 自己拼 `/v1/messages`）。

这类行通常在管理页（数据库）加，不进 `config.yaml`，因为改 `config.yaml` 要发布网关、会打断正在流的会话。数据库行与 `config.yaml` 行可以同名同组，`model_info` 里的自定义键原样到达钩子。生效时间（2026-10-14 在固定版本上实测）：`POST /model/new` 和 `/model/delete` 一两秒内；改一行用 `PATCH /model/{id}/update`（约 1 秒）——`POST /model/update` 会丢掉 `order` 且不改 `model_info`；直接改库要等网关每 30 秒一次的轮询。

## 边界与坑 {#traps}

- 健康检查只验 `/health/readiness` 与带认证的模型目录，**不验供应商配额，也不真的推理**。要一次有界的推理验证，跑 `backend/scripts/gateway_supply_probe.py --generate --model kimi-k3`：一次原生 Messages 请求、512 token 输出上限、60 秒期限，缺终止事件、认证、配额、限流、传输错误都算失败；打到输出上限与流断掉是分开报的。探针只打分类与 token 数，不打供应商原文或生成内容。
- 网关最多留五个 20 MB 日志文件，重建容器不保留 stdout 历史。
- 换一个模型是在下一个任务边界刷新原生会话；scoped 会话凭据带着所选模型的路由，分身的别名跟着 API 模型走。
- 中央会话的 `AGENT_SESSION_API_BASE` 必须从它自己的私有执行容器里够得着：填宿主机环回地址，容器里的平台工具会 connection refused。
