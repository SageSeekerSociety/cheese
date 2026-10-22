---
title: 模型网关
kind: 参考
summary: LiteLLM 网关：项目虚拟 key、预算刹车和记账。
covers:
  - deploy/gateway/
  - backend/app/domain/agent/gateway.py
  - backend/app/domain/agent/gateway_admin.py
  - backend/app/domain/agent/gateway_catalog.py
  - backend/app/domain/agent/gateway_models/
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

## 平台付钱的调用都从这里走 {#platform-paid}

平台付钱的模型调用和网页搜索，都在付钱那一方的虚拟 key 上经网关发出：项目的、任务的、个人芝士的。网关按那把 key 的预算准入、给这次调用定价，花销再按 key 抽进额度（见[记账口径](#accounting)）。会话里的订阅模型走计量代理，平台的 Claude 订阅凭证只在那里（见[计量代理](/dev/metering-proxy)）。

后端代码不直接调用模型或搜索的服务商，也不把平台付钱的服务商凭据交给会话：那样的调用没有人定价、记不到任何人头上，也不受任何预算约束。唯一直接调用服务商的，是用别人自己账户的调用：成员或团队自己的 API key 或模型服务（`backend/app/domain/agent_instance/account_connections.py`），由他们自己付钱。

## 独立的一摞 {#stack}

- 它有自己的一套 compose，故意的：`deploy-docker.sh` 只认一个 compose 文件、只拉起 `backend frontend`，而那个文件跟生产共用。**应用发布不碰网关，重启网关也不会重启应用。**
- 镜像流水线只在 `deploy/gateway/` 变了的时候重建网关，否则复用上一版镜像、打成当前 commit 的 tag。构建过程会跑适配与时序测试，不发真实请求。
- 发布走 **Release gateway** 工作流（`main` 上、镜像构建成功的完整 SHA），不断流，做法见[发布不断流](#release)。凭据从 `$HOME/gateway/compose/.env` 读，每次发布的配置存进 `$HOME/gateway/releases/`。
- 调用方找的是 `gateway-netns` 占着的地址，后面是 HAProxy：沙箱和计量代理走 `172.17.0.1:4000`（docker0 网桥地址，容器够得着、局域网够不着），后端走应用网络上的别名 `http://litellm:4000`。两个 LiteLLM 实例 `litellm-blue`、`litellm-green` 不发布端口，也不在应用网络上。

## 发布不断流 {#release}

Claude Code 的一条流常常要几分钟，流到一半断掉的不会重放，所以发布不能重启正在服务的实例。

- **拓扑**：地址归 `gateway-netns` 所有，它只占着网络命名空间：发布的端口 `172.17.0.1:4000`、应用网络上的别名 `litellm`、内部网络 `colors` 上的地址。HAProxy（`gateway-proxy-a` 或 `-b`）跑在它的命名空间里，后面是两个颜色 `litellm-blue`、`litellm-green`，同一份 compose 定义，共用 `litellm-db` 和 `litellm-redis`。平时只运行一个代理、一个颜色。
- **为什么平时只留一个颜色**：路由器的冷却状态和 `deepseek-flash` 的会话亲和都在进程内存里（`router_settings` 没配 Redis），两个颜色同时接流量，各管各的冷却和亲和，同一个会话会在两边的 RUC 节点之间来回、前缀缓存一直不命中。第二个颜色常驻也只有网关自己崩掉时才有用；真到那一步再考虑，并先把路由状态放进 Redis。
- **一次发布**：先认出正在服务的颜色：容器在运行、在 HAProxy 里既没被排空也不在维护状态的那一个，看的是管理状态而不是某一刻的健康检查。两个都符合（重启发生在排空中途）就取较新的那个；有颜色在运行却一个都不符合，就什么都不动、报失败。然后在空闲的颜色上起新镜像，依次过四关：docker 健康检查；`/health/readiness`（查数据库）；直接对新颜色发一次 `GET /v1/models`（带 master key），它列出的模型必须包括正在服务的颜色列出的每一个，而且至少上架一个模型，所以管理页加在数据库里的模型没加载完不会接流量。唯一允许缺席的，是本次发布从 `config.yaml` 删掉、正在服务的颜色没有任何数据库行提供（看 `/model/info` 的 `db_model`）、并且由人在发布工作流的 `allow_drop` 输入（`GATEWAY_ALLOW_DROP`）里点了名的模型：从文件里删掉一个名字本身不等于决定不再提供它；HAProxy 自己的检查看到它起来。这里不用 `/health`，它会真的去调每个模型。四关都过，再通过 HAProxy 的运行时 API 一条命令把它设成 ready、把旧颜色设成 drain。HAProxy 不重启，调用方的连接一条都不断；HAProxy 按请求选后端，所以计量代理复用的旧连接，下一个请求就落到新颜色。旧颜色继续把手上的流都传完，脚本每 5 秒读一次它在 HAProxy 里的 `scur`，到 0 才停它，最多等 30 分钟（`GATEWAY_DRAIN_SECONDS`）。到点还有请求没完，就照样停，并在工作流里打一条 `::warning::`，写明切断了几个。
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
| `patch_hosted_vllm_reasoning.py` | 上游 `hosted_vllm/` 适配器删掉历史 assistant 消息的 `reasoning_content`；补丁让它留着。思考模式的模型要在工具调用的下一轮拿回这段推理，DeepSeek 缺了直接 400「`content[].thinking` … must be passed back」 |
| `provider_http_timing.py` | 打 `provider_http_timing` 记录：DNS/TLS/超时/401·403/429/供应商账户没钱（`provider_quota`，用 `provider_credit.py` 的判断）/其它 HTTP 错误分类，未知的 500 不会被说成网络原因 |
| `provider_credit.py` | 不是补丁：启动钩子包住路由器最外层的调用，外加一个回调。供应商账户没钱的行冷却 10 分钟，请求的应答按情况改成 429（还有别的行能接）或 402（整组都没钱），并在响应头里点名没钱的行，见下面「免费、订阅与中转」 |
| `qwen_asr.py` | 不是补丁：经 LiteLLM 公开的 `custom_provider_map` 注册的 `qwen-asr/` 提供方。阿里百炼的 Qwen-Audio 语音识别只有原生接口，它把「最后一条 user 消息里带 `input_audio`」的对话请求转成百炼请求，system 消息作上下文、`vocabulary` 作热词；按百炼每秒 25 token 的口径把音频时长报成 prompt tokens，由这一行的单价计费 |
| `check_config.py` | 用自己构造的部署建一个 Router，断言 thinking 与 effort 的转换、缓存计价、停用的行不路由 |

## 日志里有什么 {#logs}

| 日志行 | 谁打的 | 字段 | 不含什么 |
| --- | --- | --- | --- |
| `gateway_request_timing` | 计量代理（`_log_gateway_timing`） | 请求与响应时间戳、选路结束时刻、准入耗时、`x-litellm-call-id`、状态 | 正文、凭据、任意请求头 |
| `provider_http_timing` | 网关（补丁） | 进入请求、响应头、流完成/中断/提前关闭的时刻与时长、状态、结果分类 | URL、头、凭据、正文 |
| `provider_credit out of credit` | 网关（`provider_credit.py`） | 因供应商账户没钱冷却的行：模型名、上游模型、部署 id | 地址、凭据、上游原话 |
| `gateway rows out of credit` | 计量代理（`_report_out_of_credit_rows`） | 网关点名的行、flow id | 正文、凭据 |

两个区间都含代理与网关自身的工作，也都含连接建立、重试与传输，所以它们**不是**「上游推理耗时」。失败的请求保留已有的时间戳。`x-litellm-call-id` 是把两边日志与网关花销日志对起来的那根线。

## backend 侧的这一摞 {#backend}

| 模块 | 是什么 |
| --- | --- |
| `gateway.py` | 管理客户端，L0/L1/L2 三个层次的定义就写在这个文件的 docstring 里；铸 key、设额度、drain 用量 |
| `gateway_admin.py` | 「会抛」的管理客户端（管理页必须看得见失败）：`GatewayUnreachable` / `GatewayRefused` 分开抛，凭据从不回显或落日志 |
| `gateway_catalog.py` | 目录快照与定时刷新（上面那一节） |
| `gateway_models/` | 管理页的服务（`GatewayModelsService`）：模型与逐模型用量、项目额度；`_TTL_SECONDS = 15`，因为 `/model/info` 与一周用量都是百 KB 级。2026-10 由一个 1,119 行的单文件按职责拆成包，`__init__.py` 保持原来每个方法、常量与重导出 |
| `api/routes/admin_models.py` | `/admin/gateway` 的路由：模型列表/详情/新建/改/删/停用，线路的加/改/删/停用/排序（`/models/{name}/sources/...`），项目额度、审计 |

翻译的规矩：**连接性失败由路由层翻**（不可达 503、被拒 502），页面才能告诉人「是网关挂了，过会儿再试」还是「你这个动作本身不被允许」；「模型不存在」404、「请求体不合法 / 不变式被违反 / 对 config 模型写」400 由服务层抛，因为那几种要读库或读网关才知道。

## 怎么加一个模型 {#add-model}

在管理页加。`deploy/gateway/config.yaml` 不声明任何模型：网关路由的每一个模型，连同只用来计价、从不路由的那几个，都是网关数据库里的一行（`STORE_MODEL_IN_DB` 打开），改模型、改价格都不用发布。管理员增删改停运行时模型，每次写都记审计（谁做的），写成功后刷新目录，模型立刻可选。数据库因此是模型与价格唯一的来源，定时备份见 `deploy/gateway/README.md`。

一行的上游凭据写成 `os.environ/<变量名>`（例如 `os.environ/ZHIPU_API_KEY`），LiteLLM 对数据库行同样从网关的环境里解析它，所以这些变量仍放在网关的 `.env` 里，凭据本身不进数据库。

`model_info` 里平台自己的键，网关原样透传：

| 键 | 含义 |
| --- | --- |
| `cheese_selectable: true` | 在智能体设置里给人选。opt-in，因为网关也路由不上菜单的模型：平台替人路由过去的模型不该被当成本人选的 |
| `cheese_label` | 显示名；缺省用 `model_name` |
| `cheese_tier` | 方案按它限定可用的模型（included / premium / frontier，缺省 included） |
| `cheese_efforts` | 它接受的思考档（low / medium / high / max），在智能体设置里给出，Claude Code 以 `output_config.effort` 传；缺省用模型自己的默认 |
| `cheese_auto_compact_window` | 提供方建议比上下文窗口更早压缩时，Claude Code 在这里压缩；缺省按 `max_input_tokens` |
| `cheese_overseas: true` | 只有平台管理员开通过的团队能用（Claude、GPT、Gemini），别的团队看不到 |
| `cheese_free: true` | 上游不收钱，输入、输出单价都写明为 0；可选模型唯一合法的零价 |
| `cheese_upstream_headers`、`cheese_session_header` | 按行（部署）发的请求头，见下面「免费、订阅与中转」 |
| `cheese_no_explicit_cache: true`、`cheese_spend_cap_usd` | 属于上游模型：去掉 Claude Code 的 `cache_control` 标记（`no_explicit_cache.py`）；上游累计花到这个数就拒（`spend_cap.py`） |

### 一个模型，几条线路 {#sources}

同一个 `model_name` 在网关里可以有好几行（部署），网关按 `litellm_params.order` 从小到大试，相同的一起分流，没有 order 的只在所有有 order 的都冷却时才轮到。管理页把它们叫「线路」：任何模型都能加线路（`kimi-k3` 有官方 API 一行，也有 OpenCode Go 一行）。不用发布网关，加完立刻生效。

- **模型的元数据每一行都一样**：上架、名字、档位、思考档、能力，目录读 `/model/info` 列出的第一行，而哪一行排第一不由我们定。加线路时服务层从已有那一行抄（`cheese_*` 里属于模型的键，加上每一个显式写了的 `supports_*`，`supports_output_config` 这类决定思考档送不送得到上游），改模型时每一行都改；部署 id 不抄（共用一个 id，一行 429 冷却全组，#3294）。
- **新线路默认是兜底**：默认优先级排在现有带 order 的线路之后。新线路会接走在用线路的流量时，请求体必须带 `make_primary: true`，页面上要勾「让它成为首选」，不带就 400。这种情况是它的数不大于在用线路里最小的那个，或者旁边的线路都没写 order。给停用的模型加线路，新线路也是停用的。
- **请求头**：静态头写在这一行的 `litellm_params.extra_headers`，LiteLLM 原样发给上游（`hosted_vllm/` 与 `anthropic/` 两种都实测过）；接收 Claude Code 会话 id 的头名写在 `model_info.cheese_session_header`，由网关侧的钩子填值。`/model/info` 对每一把虚拟 key 都开放：`model_info` 原样回，`litellm_params` 过 LiteLLM 的掩码，而掩码只看头名——名字里有 key、token、secret、auth、password、credential、cookie 这一段的，值被打码，别的原样可读（1.103.3 上实测）。所以头的值按公开对待，上游凭据填 `api_key`；认证与传输相关的头不许按线路写。页面只列头名，审计里也只留头名。
- **协议**：`anthropic` → `anthropic/<上游名>`（原生 Messages）；`openai-chat` → `hosted_vllm/<上游名>`（只有 chat/completions 的中转站与 OpenCode Go；不能用 `openai/`，那会被翻成 Responses API）；`openai-responses` → `openai/<上游名>`。不选协议时 `upstream_model` 就是完整的 LiteLLM 模型串。原生 Messages 的地址不带 `/v1`（LiteLLM 自己拼 `/v1/messages`，服务层把多填的 `/v1` 去掉）；Chat 线路的地址要带到 `/v1`。
- **Chat 线路让整组折叠 system 消息**：Messages 翻成 chat/completions 会丢掉对话中间的 system 消息（实测 glm-5.2、MiMo），所以一个模型一旦有 `openai-chat` 线路，组里每一行都标 `supports_mid_conversation_system: false`，服务层加线路时顺手改齐运行时的行；config.yaml 的行没标就拒，先去改 config。
- **消费上限跟着上游走**：`cheese_spend_cap_usd`（`spend_cap.py`）按上游模型（`litellm_params.model`）的总账计，不分是哪一行、哪个模型名打的。所以在页面上改一条运行时线路的上限（请求体 `spend_cap_usd`，`null` 是清掉），服务层把同一上游的每一条运行时线路都改成同一个数；同一上游有 config.yaml 的行且上限不同就拒，先把那一行移出 config 或改 config。改上限不用发布网关。
- **每条线路都要有价，价只能改不能清空**：平台按模型名的单价给它的全部用量计费（`usage.pricing`；订阅里那几个 Claude 模型停用、不上架，留下的就是单价），所以不管上不上架，每条线路输入、输出都要有价；唯一的零是声明出来的免费（`cheese_free: true`，两向写 0）。一条线路已有的价（五档里的任何一档，包括一小时缓存写入 `cache_creation_1h`，即 `cache_creation_input_token_cost_above_1hr`）编辑时可以改，不能清空，服务层拒绝。页面上改价先弹框列出每一档的改前 → 改后，并说明这个模型此后的全部用量都按新价计。
- **至少留一个上架的模型**：停用、删除、下架最后一个上架的模型，或停用、删除它最后一条在用的线路，服务层都拒绝，否则设置 AI 队友时没有模型可选。
- **免费**：唯一合法的零价是声明出来的——`cheese_free: true` 且输入、输出都写成 0（`price_is_set` 认它）；没标免费的零价一律拒。免费线路和付费线路在同一个模型里时，用量估价按付费线路里最高的那一档算，免费线路上的流量也一样（`usage.pricing`）。页面在混用时会提示这一点。
- 目录对一个模型的判断看整组：有一行没停用就还能选，在用的每一行都有价（或声明免费）才算有价。

价格只用来计价、从不路由的模型（平台订阅服务的 Claude 模型）也是这样的行：带全五个单价（输入、输出、缓存读、5 分钟与 1 小时缓存写），停用，不配凭据。后端读 `/model/info` 给订阅流量计费（`usage.pricing`），所以它们留在网关里，但路由器拒绝它们。LiteLLM 对数据库行的停用只认 `/model/block` 写的那一列，不认 `model_info.blocked`。

还有一项要按模型手动标：**`supports_mid_conversation_system: false`**。Claude Code 会把技能清单、运行环境和日期放在对话中间的 system 消息里；有的线路收不了它，有的是网关转换时丢掉，有的是上游丢掉或整条请求拒绝。标了 `false` 的模型，平台启动 Claude Code 时设 `CLAUDE_CODE_MODEL_CAPABILITIES`，让它把这些内容并进第一条用户消息（`gateway_usage.model_capabilities`）。没标的照 Claude Code 的默认。这一项只在量过之后手动标，不按上游推断：线路以后支持了，把标记去掉就行。量法是从后端经网关发一条对话中间带暗号的 system 消息，问模型暗号是什么。在管理页的能力开关里关掉。

管理页背后的服务守着这条不变式：**没价的可选模型等于不上架**。它的 token 会按零计费，项目的 `max_budget` 永远不会跳，第一个征兆是发票——模型从选单里消失会被发现，一个悄悄失灵的刹车不会。唯一的例外是上游真不收钱的模型：`model_info` 里写 `cheese_free: true`，并把输入、输出单价都**写明**为 0（`usage.pricing.declared_free`）。没有标记的 0 仍是没价。`deploy/gateway/check_config.py` 在镜像构建时验的是固定版本 LiteLLM 的行为（思考档、缓存计价、停用的行不路由），用的是它自己构造的部署，不读任何一行真实配置。

### 平台模型角色 {#roles}

平台自己挑模型的那几件事叫「平台模型角色」，在模型管理页的「平台模型角色」一段里各选一个网关模型，改了立即生效，不用重新部署后端：

| 角色 | 用在哪 | 没人指过时读的设置项 |
|---|---|---|
| `agent_model` | 没设默认模型的项目跑在它上面；网关从没答过话时目录只剩它 | `AGENT_MODEL` |
| `subagent_model` | 没设分身默认、且有权用这个模型的项目，分身默认跑它。有没有权和它自己的轮次过同一道闸门（`policy.gate.check`：团队方案的档位、管理员的境外开通、项目自己的档位策略）；没权的项目照旧跟着项目的默认模型 | 无：不设就跟着项目的默认模型 |
| `assistant_model` | 个人芝士、助手的动作闸门 | `ASSISTANT_MODEL` |
| `docs_assistant_model` | 问芝士 | `DOCS_ASSISTANT_MODEL` |
| `topic_naming_model` | 话题命名 | `TOPIC_NAMING_MODEL` |
| `environment_diagnosis_model` | 让芝士看看（环境搭建失败的诊断） | `ENVIRONMENT_DIAGNOSIS_MODEL` |
| `voice_asr_model` / `voice_polish_model` | 语音输入的转写与整理 | `VOICE_ASR_MODEL` / `VOICE_POLISH_MODEL` |
| `memory_migration_model` | 旧记忆表的迁移 | `MEMORY_MIGRATION_MODEL` |

- 选择存在平台库的 `platform_model_roles` 里，审计记在 `gateway_admin_audit`（动作 `role.set`）。读的一方在 `app.core.model_roles`，后台每 30 秒从库里刷新一次，写的那个进程写完当场刷新。项目自己设了默认模型、分身默认的，在那个项目里仍以项目的为准。
- 默认模型与分身默认只能指到上架的模型（它们要提供给项目选），指着它们的模型也不能下架；其余角色只能指到在用、计价的模型：两向都写了价、至少一向 > 0，或者是免费线路（语音转写跑在一个不上架、输出价为 0 的模型上）。
- 这些工作各用一把平台自己铸的虚拟 key，key 只放行一个模型（`service_keys.KeySpec`）。改角色时服务层先把 key 放宽到新旧两个模型，存好选择；等每个进程都读到新选择（30 秒刷新之后再过 15 秒）再收窄到新模型，所以没有一刻哪个进程用的模型被 key 拒掉。收窄没做成（进程退出、网关拒绝）时 key 多放行一个模型，只记日志。
- 模型列表里每个模型带着跑在它上面的角色（`roles`）。有角色时停用、删除它，或停用、删除它最后一条在用的线路，服务层都拒绝，要先把角色指走；页面在编辑、停用、删除这个模型之前弹框列出这些角色。没人指过的角色读设置项，那个值也算「跑在它上面」：部署之后 `deepseek-flash`、`qwen-audio-asr`、`glm-5.2` 这几个设置项默认值指着的模型，同样不能直接停用或删除。
- `agent_haiku_model`、`agent_sonnet_model`、`agent_opus_model` 不是角色：现在的执行通道都自己写模型环境变量，并把这三个别名清掉（`screen_model_env.py`），它们只在没有通道的那条老路上还会被读到。

人民币标价的模型，价格用人民币价除以「7.1 人民币/美元」这个既有约定折算，是估算而不是实时汇率。Kimi K3 走原生 Messages 端点 `https://api.moonshot.cn/anthropic`，它那一行引用的 `MOONSHOT_API_KEY` 要在网关 `.env` 里。GLM 系列必须用 `anthropic/` 前缀而不是 `openai/`：调用方说的是 Anthropic 协议，被 `openai/` 前缀一转，LiteLLM 会翻成 OpenAI Responses API——智谱那条路是 404。

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

### 免费、订阅与中转：同一个模型挂多条供给 {#supply-rows}

一个模型组可以有多行（多个 deployment），路由器按 `litellm_params.order` 从小往大用：先用最小 `order` 里健康的，任何错误（配额用完的 429 在内，上下文超长与内容审查除外）都在同一个请求里落到下一档；只有所有带 `order` 的行都不健康时，才会使用没写 `order` 的行。被 429 的行冷却约 5 秒后重试。

供应商账户没钱的行（DeepSeek 的 400 `Insufficient Balance`、智谱的 `[1113]`、火山方舟的 `SetLimitExceeded`、Moonshot 停用的账户、OpenAI 的 `insufficient_quota` 等，`provider_credit.py` 里一份判断）冷却 10 分钟，到点后由一个请求再试一次账户，充值后不用动网关；路由器自己对 400 不冷却、对 429 只冷却约 5 秒。冷却期间请求直接走同组的其他行。撞上没钱的行、而同组还有只是暂时忙的行时，调用方收到 429 和那一行的 Retry-After，Claude Code 重试时就落到它上面；整组都没钱时，调用方收到 402，消息以 `cheese_upstream_credit_exhausted` 开头，Claude Code 不重试，后端据此在房间里说「平台的 AI 服务额度暂时不足」。网关在响应头 `x-cheese-upstream-out-of-credit` 里点名每一条没钱的行，计量代理逐条报给后端的错误收集，后端按行告警管理员。

新发现可用的免费供给就接入；同一个模型的供给按免费、已购买的订阅额度、按量付费 API 的顺序设置 `order`。`deepseek-flash` 的自建供给（人大）排在最前面，免费供给接在它之后、官方按量付费 API 之前。人大那份只列不带日期的别名，版本核实不了，所以菜单名不写日期版本。

接入前核对供应商当前价格，并用平台实际使用的 API 跑通请求和 `gateway_conformance.py` 的适用检查。仅在供应商客户端内免费的入口，要等平台 API 可用后再上架。

有的上游要自己的请求头（`upstream_headers.py`），按行声明在 `model_info` 里，只发给声明了的那一行，同组兜底的官方行收不到：

- `cheese_upstream_headers: {user-agent: cheese-gateway/1.0}`：原样发送的固定头。
- `cheese_session_header: x-opencode-session`：用这个头名带上会话。值是 Claude Code 的 `X-Claude-Code-Session-Id`（校验同 `chatgpt_session.caller_session`）；调用方没有（pi 走 `/v1/chat/completions`、后端 `GatewayChat` 的功能）时，用「调用方虚拟 key + 第一条用户消息」的哈希，同一段对话每次相同、不同 key 永不相同；只有连用户消息都没有的请求才给随机值。声明了就一定发——OpenCode Go 缺这个头直接 400 `MissingSessionID`。

上游只有 OpenAI 兼容的 `chat/completions`、没有 Anthropic Messages 时，前缀用 `hosted_vllm/`，`api_base` 写到 `/v1` 为止：`openai/` 会被翻成 Responses API（`/responses`），`custom_openai/` 会丢掉 reasoning effort、还把 OpenAI SDK 的名字拼进 User-Agent。`test_chat_route.py` 用假上游守着 thinking、工具调用、工具历史和图片两个方向都完整，以及下一轮请求里 assistant 消息带回上一轮的推理。有原生 Messages 的上游用 `anthropic/`，`api_base` 写到 `/v1` 之前（LiteLLM 自己拼 `/v1/messages`）。

`model_info` 里的自定义键原样到达钩子。生效时间（2026-10-14 在固定版本上实测）：`POST /model/new` 和 `/model/delete` 一两秒内；改一行用 `PATCH /model/{id}/update`（约 1 秒）——`POST /model/update` 会丢掉 `order` 且不改 `model_info`；直接改库要等网关每 30 秒一次的轮询。

## 备份与恢复 {#backup}

网关数据库是管理页加的模型和价格、每个项目的虚拟 key 与预算、花销日志的唯一一份；`config.yaml` 只能找回随镜像发的那几个基线模型。

- **Gateway DB backup** 工作流（`.github/workflows/gateway-db-backup.yml`）在 dev runner 上每 6 小时跑一次（UTC 00:23、06:23、12:23、18:23），也可以手动触发。它调 `deploy/gateway-db-backup.sh`：用容器自己的 `pg_dump -Fc` 转储 `litellm`，`pg_restore --list` 读回来、必须列出 `LiteLLM_ProxyModelTable` 和 `LiteLLM_VerificationToken` 的数据，过了才改名成 `~/backups/gateway/gateway-litellm-<UTC 时间>.dump`。本机留最新 8 份（2 天；2026-10-21 一份约 0.85 GB）。
- 每份随后用 `deploy/r2-upload.py` 传到 R2 的 dev 前缀下（`db/gateway-litellm-<UTC 时间>.dump`），桶的生命周期规则 30 天后删。数据库和本机那几份在同一台机器上，所以上传失败整次运行就算失败。
- 恢复命令（看内容、只捞回几行、整库替换）见 `deploy/gateway/README.md` 的 Backups 一节。整库恢复会丢掉转储之后写进去的一切：花销、新项目的 key、模型改动。

## 边界与坑 {#traps}

- 健康检查只验 `/health/readiness` 与带认证的模型目录，**不验供应商配额，也不真的推理**。要一次有界的推理验证，跑 `backend/scripts/gateway_supply_probe.py --generate --model kimi-k3`：一次原生 Messages 请求、512 token 输出上限、60 秒期限，缺终止事件、认证、配额、限流、传输错误都算失败；打到输出上限与流断掉是分开报的。探针只打分类与 token 数，不打供应商原文或生成内容。
- 网关最多留五个 20 MB 日志文件，重建容器不保留 stdout 历史。
- 换一个模型是在下一个任务边界刷新原生会话；scoped 会话凭据带着所选模型的路由，分身的别名跟着 API 模型走。
- 中央会话的 `AGENT_SESSION_API_BASE` 必须从它自己的私有执行容器里够得着：填宿主机环回地址，容器里的平台工具会 connection refused。
