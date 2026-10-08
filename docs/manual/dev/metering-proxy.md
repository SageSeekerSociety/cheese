---
title: 计量代理
kind: 参考
summary: 模型流量的唯一出口：持有平台的订阅凭证，计量、准入、改道。前身是 ccproxy。
covers:
  - deploy/metering-proxy/
  - backend/app/domain/usage/subscription_ingest.py
  - backend/app/domain/agent/harness/claude_code/device_launch.py
  - backend/app/domain/agent/machine_tunnel.py
---

# 计量代理 {#metering-proxy}

模型流量的唯一出口：会话里发出的模型请求都从这里过一遍，它持有平台唯一的 Claude 订阅凭证，负责计量、准入和改道。

> 讲：代理自己怎么工作——两个入口、凭据、改名、账本、拒谁怎么拒。不讲：准入的判定怎么算出来（见[准入与供给](/dev/admission)），用量怎么折算成 credit、两道刹车怎么配（见[计费](/dev/billing)），整条调用链（见[模型调用流程](/dev/llm)）。

## 沿革：前身 ccproxy {#history}

代理的前身是 ccproxy——那是一个跟着设备走的转发器，每台设备自己配上游。2026-09-24 它退役，职责并入现在的计量代理：

- 决定在 `docs/plans/2026-09-19-refactor-decisions.md`：结论 46「一个控制点」，以及 2026-09-24 那条——所有 Claude Code 会话开机都带一个不自证的占位票，平台凭证只放在计量代理里，绝不进 LiteLLM，执行机器不持有任何模型凭据。
- 留下的痕迹在 `backend/alembic/versions/971b4765fa69_drop_ccproxy_columns.py`：删掉 `device.ccproxy_upstream`、`device.ccproxy_machine_id`、`project_machines.ccproxy_upstream`、`warm_machines.ccproxy_upstream`（downgrade 只把空列加回来）。
- 今天 `backend/app`、`cli/` 里没有 ccproxy 的运行时引用，只在历史迁移和 `docs/plans/` 里出现。旧文档里看到 ccproxy 就是旧话。

## 入口 {#listeners}

`deploy/metering-proxy/compose.yml` 起的是一条 mitmdump：`--mode regular@8444 --mode reverse:https://chatgpt.com@8445 --ssl-insecure --set connection_strategy=lazy`。第一个入口是会话把流量送进来的那一个，第二个只给网关用（见 [ChatGPT 账号](#chatgpt)）：

| 入口 | 会话侧怎么被指到这里 | 宿主监听 | 归属怎么证明 |
| --- | --- | --- | --- |
| `:8444` CONNECT | 会话里 `HTTPS_PROXY` 指到隧道端口，隧道再走到这里 | `172.17.0.1:8444` | `Proxy-Authorization` Basic 的密码必须通过 `verify_scoped_token`，否则 407 |
| `:8445` 反代 | 网关（LiteLLM）deployment 的 `api_base` 指到 `http://metering-proxy:8445/chatgpt/<账号>` | 不发布；只在内部网络 `cheese-meter-gateway` 上，别名 `metering-proxy` | Bearer 必须等于 `CHEESE_CHATGPT_KEY`，否则 401；没配就全拒 |

为什么是 CONNECT：会话主机上的 `claude` 是个普通进程，没有 root、也没有 hosts 可写，能被牵过来的只有 `HTTPS_PROXY`，也就是 CONNECT。它也不能改用 `ANTHROPIC_BASE_URL`：那会把 Claude Code 切成 API-key 模式，直接无视 OAuth token——订阅路的转向必须在传输层做。

没有哪台机器直接拨 `:8444`：会话的 `HTTPS_PROXY` 指向座位自己回环上的隧道助手，助手经模型隧道把 CONNECT 流量带到后端，隧道再在网桥地址上连 `:8444`。所以它只发布在网桥上。

- CONNECT 这道门是「关着失败」：`CHEESE_SCOPED_SECRET` 为空、或者密码验不过，一律 407，不存在放行。把会话流量引到这里的隧道见 `backend/app/domain/agent/machine_tunnel.py`。
- 出网可以配 HTTP 代理（`claude-login.sh egress set|clear|test [n]`），平台凭据即使被取走也在 egress 后面用。egress 挂了或拒了，请求就失败，绝不改直连——只有带凭据的请求（含刷新）走它，网关路、本地回答、原样转发的东西各走各的。

## 一个请求进来之后 {#flow}

1. `tls_clienthello` 只在 regular 模式下做事：SNI 不在 `ANTHROPIC_HOSTS`（`api.anthropic.com`、`console.anthropic.com`、`platform.claude.com`）就不 MITM。
2. `requestheaders`：按 SNI 定 host，正文流式转发不落盘；从 scoped token 的已验证 claims 认领 project/topic；`/v1/messages` 且带 `x-claude-code-agent-id` 或 `x-claude-code-request-class` ∈ {subagent, workflow} 的判为分身请求，取走 `x-cheese-child-model`。
3. 分身请求如果没带子模型、且 `Content-Length` 不超过 `DEFER_BODY_LIMIT`（8 MiB），这一步先不转发，把正文整个缓冲下来，等 `request` 钩子里读到顶层 `model` 再决定——缓冲意味着正文整份占代理的 RAM，所以有上限，不然这就是个调用方可控的 OOM 旋钮。主对话从不缓冲。
4. 每个 `/v1/messages` 都先调一次 `POST /llm/admission`，带会话的 scoped token 和代理自己的凭据 `CHEESE_SCOPED_SECRET`（作 `X-Cheese-Token`；不带就拿不到网关 key）（`AdmissionGate`：进程内缓存 30 秒，超时 3 秒，缓存键含 project/topic/bearer 的 sha256/分身/请求模型）。
5. 按判定走三条路：订阅路（默认，换完凭证与模型名之后转发给 Anthropic）、网关路（`_route_to_gateway`：换 host/scheme/port、清掉 `flow.server_conn.via`、`Authorization: Bearer <项目虚拟 key>`、删掉 `x-api-key`）、拒。

## 拒谁、怎么拒 {#refuse}

| 情形 | 状态 | 正文（`_refuse` 第三个参数是 JSON 里的 type） |
| --- | --- | --- |
| scoped auth 生效但调用方不能证明项目 | 401 `authentication_error` | `cheese: a valid scoped token is required` |
| 判定 `reason_kind=BINDING` | 400 `invalid_request_error` | 判定里给的原因（模型不可用一类） |
| 判定 `reason_kind=BUDGET` | 429 `billing_error`，带额度用完的头（见下） | `cheese project budget: …` |
| 网关路上 LiteLLM 回 429，`message` 以 `Budget has been exceeded` 开头（`max_budget` 刹车） | 同上，改写后再交给调用方 | `cheese project budget: 额度已用完。` |
| 网关路上 LiteLLM 回 429，`message` 里带上游的 `usage_limit_reached`（模型背后的订阅账号用完了套餐） | 同上，`resets_at` 写成 `retry-after` 和两个 `-reset` | `cheese: 模型 {group} 背后的订阅账号用量已到上限，北京时间…恢复。换一个模型可以继续。` |
| 该走网关路但没路或项目没 key | 503 `api_error` | `…no model call was made` |
| 滚动上限撞到 | 429 `rate_limit_error` | `cheese subscription cap reached: {used}/{cap} tokens in the last {n}h` |
| 平台没有 Claude 登录 | 400 `invalid_request_error` | `cheese: the platform has no Claude login…` |
| 登录过期、正在续 | 503 `api_error` | `…has expired and is being renewed; retry shortly` |
| 问不到准入（fail-open）且没有凭证 | 503 `api_error` | `…could not say where this request goes…` |

额度用完照 Claude Code 网关约定里「用户的额度到顶」那一种回（`_refuse_reached_cap`）：`x-should-retry: false`，`anthropic-ratelimit-unified-status: rejected`，`-overage-disabled-reason: org_spend_cap_reached`，利用率与阈值都写 1；准入给了 `reopens_at` 时再加 `retry-after` 和两个 `-reset`。带上 disabled-reason，Claude Code 把 `message` 原样打出来、不重试；只回一个 429，它会当成限流，重试十次，还打出「Server is temporarily limiting requests (not your usage limit)」，意思正好反了。`representative-claim` 和 `overage-status` 故意不带：带了它会换成自己的一句话，把我们的原因丢掉。网关那两条要改写：LiteLLM 的 `/v1/messages` 不管什么原因，429 一律给 Anthropic 信封、`error.type` 写 `rate_limit_error`，原因只在 `message` 里（`_answer_a_reached_gateway_cap` 按 `message` 认）。`max_budget` 那条是因为账本要等回合结束才记上花费，额度见底时拦下回合的往往是它；订阅账号用完那条是因为回合内怎么重试都过不去，换模型才行。别的 429 是真限流，原样交给调用方。

为什么过期给 503、没有给 400：Claude Code 对 5xx 会静默重试约三分钟，对 400 不重试。续期是等得好的，用 503；要人去登录的，用 400 立刻停下来把话说清楚。

**坑**：拒之前必须把 `request.body` 的流关掉（`_refuse` 里统一做）。mitmproxy 里「设响应」和「流式转发正文」互斥，一旦同时成立就抛 `Can't set a response and enable streaming at the same time`，而且这是致命错误——整个连接死掉，调用方等超时，看到的不是拒绝原因而是平台卡住。只有带正文的请求会走到那条分支，所以 GET 的拒绝一直好好的，被拒的 `POST /v1/messages` 全崩。源码注释里记着实测：48 小时内 68 个被拒的回合、0 个 503 送达、350 次崩溃。代价是被拒的正文要缓冲而不是流式——但那条路既不转发也不长期持有。

## 只换凭据与模型名 {#rewrite}

平台只动请求里的两样东西，正文其余部分一字不改：

- `Authorization`：请求里的 bearer 等于占位票 `NO_LOGIN_PLACEHOLDER`（`sk-ant-oat01-cheese-no-claude-login-on-this-host`，`device_launch.py` 写的是同一个字面量）时，换成平台凭据的 access token；别的 bearer（比如自己在本机 `claude /login` 过的人）原样放行，不抢。
- 顶层 `model`：`_write_bound_model` 流式重写，边转发边改，不解析整个 body；`PARENT_MODEL` 最多记 10000 条父子对应，主对话的模型不动（haiku 保留给 CLI 自己的后台请求），只改分身的。正文里找不到顶层 `"model"`（超过 `control_answers.json` 的 `model_rewrite_limit_bytes` = 65536，或格式不是对象）时按 I27 直接拒，不猜着改。实测过一个 245909 字节的正文，`"model"` 出现在第 1 个字节。

不改正文其余部分的理由是保持 Claude Code 的请求指纹：这是个被上游看着的接口，动得越少越不容易出问题。

## 平台唯一的 Claude 凭据 {#credential}

- `CHEESE_CLAUDE_CREDENTIAL=/etc/cheese/claude-credential/credential` 只挂给代理，读写挂载（刷新后的 token 要写回去）。`PlatformCredential` 的写入走原子替换加 `fchown`。
- 凭据文件是每个请求重读的，所以在宿主机上登录、换账号、登出，都会在所有正在跑的会话的下一个请求上生效，不用重启谁。
- 刷新打在 `https://platform.claude.com/v1/oauth/token`，提前 300 秒刷，失败退避 60 秒，过期前 3 天开始告警（`LOGIN_WARNING_S`）；代理是这对 token 唯一的持有者，所以轮转不会把别人甩掉。刷新用的请求体与 pin 住的 Claude Code 逐字节一致，由 `scripts/remote_execution/refresh_contract.py` 锁住。
- `claude-login.sh` 的子命令：

| 子命令 | 做什么 |
| --- | --- |
| `status` | 看现在的登录状态、刷新令牌什么时候到期 |
| `setup-token` | 存一枚 `claude setup-token` 出来的一年期 token；没有任何东西会续它，一年内要换 |
| `login` | 浏览器登录，在一次性配置目录里做完再把 token 对搬过来；刷新令牌的期限（约 30 天）不会因为续期而前移 |
| `logout` | 删掉凭据 |
| `egress set\|clear\|test [n]` | 设、清、测出口代理；`test` 打 n 次经它与 n 次直连的 TLS 握手耗时 |

- 没有凭据时，走 API-key 池的项目照跑：只有真账号能答的启动调用由 `NO_LOGIN_ANSWERS` 在这里答掉，订阅路上的请求给 400 并点名缺登录。

## 平台的 ChatGPT 账号 {#chatgpt}

- 一个名字一个账号，存在 `chatgpt-credential/<名字>/credential`（JSON：`access_token`、`refresh_token`、`id_token`、`expires_at` 秒、`account_id`），旁边可以有这个账号自己的 `egress`。挂载给代理的是 `CHEESE_CHATGPT_CREDENTIALS=/etc/cheese/chatgpt-credential`，读写。
- 代理是每对 token 唯一的持有者，到期前 600 秒刷新，打 `https://auth.openai.com/oauth/token`（`grant_type=refresh_token`、`client_id`、`scope=openid profile email`）。401/403 或 `refresh_token_expired|reused|invalidated` 判死，要重新登录；5xx、网络错误、认不出的 4xx 退避 60 秒再试。
- `:8445` 不对宿主机发布。计量代理和网关的 LiteLLM 共用一张内部 docker 网络 `cheese-meter-gateway`，代理在上面叫 `metering-proxy`，沙箱不在这张网上，所以这一跳走明文 http。后台的模型表单只对 `http://metering-proxy:8445/chatgpt/<账号>` 这一个地址放开 http（`api_base_allowed`）。两边的 compose 都把这张网声明为 external，两边的发布脚本发现它不存在就建，谁先发布都行；手动先起计量代理而两边都没发布过时，要先跑 `docker network create --internal cheese-meter-gateway`。
- `:8445` 只转 `/chatgpt/<账号>/responses[/…]` 和 `/chatgpt/<账号>/models`，到 `https://chatgpt.com/backend-api/codex/…`，换上账号的 `Authorization`、`ChatGPT-Account-Id`，加 `originator: cheese` 和 `version`。`version` 取自 `chatgpt-credential/client-version` 这个文件，所有账号共用，每个请求都重读，所以改了不用重启；没有这个文件就用内置默认值（`cheese_billing_core.py` 的 `DEFAULT_CODEX_CLIENT_VERSION`）。ChatGPT 按这个版本决定账号能用哪些模型。转 `/models` 时，查询参数里的 `client_version` 也改成同一个值，调用方自己带的会被替换。其它路径 404，账号不存在 404，没登录或已判死 401，过期且刷新没成 503。不计量：网关自己记这部分花费。
- 两个账号用同一个出口地址、不同的出口登录时，请求被拒（503）：mitmproxy 按出口地址复用上游连接，不分登录，放行就会让一个账号走进另一个账号的隧道。
- `chatgpt-login.sh` 的子命令：`login <名字>`（device flow，经这个账号的出口）、`import <名字> <文件>`（搬进一对别处持有的 token，缺 `expires_at` 就取 access token 的 `exp`）、`status [名字]`、`ls`、`logout <名字>`、`egress set|clear|test <名字> …`、`client-version set <版本>|show|clear`（`clear` 回到内置默认值）。`client-version` 因此不能用作账号名。

## 账本 usage.jsonl {#ledger}

- 订阅路上每次用量按四个 token 桶追加成一行：`ts`、`project_id`、`topic_id`、`model`、四个桶、`total_tokens`、`provider=subscription`。容器写 `CHEESE_USAGE_LOG`（默认 `/var/log/cheese/usage.jsonl`），宿主机由 `USAGE_LOG_DIR` 卷进来。
- 重启时 `Meter._restore` 先把这本账读回来——读不回来，滚动窗口一重启就归零，上限就形同虚设。
- 后端从 `SUBSCRIPTION_USAGE_LOG` 读同一本账（`backend/app/domain/usage/subscription_ingest.py`，`SOURCE="subscription-proxy"`）：4096 字节头部指纹认轮转，一批 2000 行，只吃完整行，同一事务里用 `IngestCheckpoint` 保证恰好一次，四个桶都计。`SUBSCRIPTION_USAGE_LOG` 不设就不进账。
- `USAGE_LOG_DIR`（部署侧卷的挂载点）与 `SUBSCRIPTION_USAGE_LOG`（后端读的路径）是两处配置，平台里没有任何东西检查它们指的是同一本账。配错的表现是「代理在写、平台什么都没记」，这是一处只能靠人守的一致性。

## 账号池快照 accounts.json {#pool-snapshot}

- 池子（`ClaudeAccounts`）的状态原先只活在这台机器上。每次状态变化，它另写一份 `accounts.json`，放在**账本同一个目录**里（`USAGE_LOG.parent`）——那是后端唯一读得到的目录：`docker-compose.subscription.yml` 已经把它只读挂进后端，所以不需要新开一条后端到这台机器的通路。内容是 `written_at`、`retry_after` 和每张账号的 `{name, state, until, failures}`，**没有任何凭据内容**；账号名是管理员可见的标识。
- 什么时候写：每次冷却状态变化（`_save`）、每个被计量的回合（`billing_addon._record_usage`）、以及 addon 起来时各一次——都搭现有的那两条路，没有新定时器。只挂在计费那一路会漏掉冷却开始的那一刻：把账号打成 429 的那次调用本身不产生用量行。
- 写是原子的（同目录临时文件 + `os.replace`，0600），失败只记一条 warning：这一份是给人看的状态，写不下去不该影响转运。
- 状态三个值：`available` 没有冷却记录、`cooling` 带解冻时刻 `until`（Unix 秒，**绝对**时刻）、`disabled` 是额度用尽且没有重置时刻、要人工重置。有哪几张账号由 `accounts()` 定：主凭据，加上 `accounts/*/credential` 每个子目录一张。
- 后端读它的是 `backend/app/domain/platform_stats/claude_pool.py`，接在 `/admin/stats/usage` 的 `claude_accounts` 上，画法见[看板](/dev/boards#platform-stats)。读不到、读不懂时返回一个原因**代号**而不是报错（开发环境不跑订阅版代理是常态）；超过一小时没写入仍返回那些行，另标 `stale`——行上的时刻是绝对的，池子静止时文件本来就不该变。

## 非模型端点与网关路观测 {#control-answers}

- `control_answers.json` 是一张「路径 + host → 固定回答」的表，代理本地答（`_answer_here`），不打给上游；host 先匹配，行里可以带 `{project}` / `{topic}` 占位。没有凭据的会话另有 `NO_LOGIN_ANSWERS`（bootstrap / penguin_mode / oauth validate），让 Claude Code 起得来。
- 走网关路的请求在这里被记一笔：`_log_gateway_timing` 打 `gateway_request_timing` 的 JSON 日志（project/topic/`x-litellm-call-id`/admission_ms/状态），失败再 POST 回 `/backend-errors`。字段含义见[模型网关](/dev/gateway)。

## 配置项留空的后果 {#config}

| 配置 | 留空/默认 | 后果 |
| --- | --- | --- |
| `CHEESE_ADMISSION_URL` | 空 | 不调准入，每个请求都走订阅路：没有预算检查、不会改道网关、也不过问绑定；只剩滚动上限兜底 |
| `CHEESE_SCOPED_SECRET` | 空 | 没有证明项目的办法：`:8444` 的 CONNECT 全 407 |
| `CHEESE_TOKEN_CAP` | 0 | 滚动上限关闭（`CAP_WINDOW_S` 默认 5 小时） |
| `CHEESE_GATEWAY_BASE` | 空 | 网关路改道的目标地址 |
| `CHEESE_ALLOW_HEADER_ATTR` | 0 | 不接受 `x-cheese-attr` 这种自称的归属 |

## 怎么上线，怎么验 {#release}

- 镜像钉住 mitmproxy 12.1.2，把 addon、billing core 和控制回答表打进镜像里；宿主机不挂任何源码目录到 `/addons`，所以跑的永远是镜像里那一版。
- 发布走 **Release metering proxy** 这个 GitHub Actions 工作流，从 `main` 上给完整 commit SHA，要求该 SHA 的镜像构建与 Required CI 都成功；已经跑着同一 digest、配置也没变的代理不动，免得打断正在流的会话。配置指要发布的 compose 文件加宿主机上的 `.env`：每次发布都把两者的 SHA-256 记在新容器的标签 `cheese.metering-proxy.config-sha256` 上，下次发布时镜像、健康状态和这个哈希都对得上才跳过；只改了 `.env` 或 compose 的，下次发布会重建容器。只存哈希，不打印也不复制 `.env`。不是由发布创建的容器、以及回滚恢复出来的容器没有这个哈希，下次发布一律重建。
- 发布保留宿主机上的 `.env`、端口、账本和 CA 挂载；重建前先把镜像 ID 与 compose 原文件存进 `releases/<sha>-<run>-<attempt>/`，健康检查不过就回滚。健康检查要求两个监听都在、且一次不带认证的 CONNECT 得到 407——它不调模型。
- 健康检查过了不等于能用：发布后要亲手验一次沙箱回合、一条归属正确的用量行、以及一个额度用尽的测试项目的拒绝。只看监听活着，说明不了平台凭据到得了 Anthropic、准入调得通、账本进得去。

## 边界与坑 {#traps}

- 它只认域名：`ANTHROPIC_HOSTS` 之外的 SNI 不 MITM，也就没有计量。
- 改分身模型要先拿父模型（`PARENT_MODEL`）判断「这算不算继承」；父模型没见过就按「指名」处理。
- 强制改名只在正文够小、且顶层确实有 `"model"` 时成立，否则拒。要支持更大的正文得改 `control_answers.json` 里的阈值，不是改代码。
