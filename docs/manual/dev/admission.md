---
title: 准入与供给
kind: 参考
summary: 每个模型请求能不能跑、走哪条路、用哪个模型名，由谁决定。
covers:
  - backend/app/api/routes/llm_proxy.py
  - backend/app/domain/agent/supply.py
  - backend/app/domain/agent/profiles.py
  - backend/app/domain/room_task/binding.py
  - backend/app/domain/agent_instance/configuration.py
---

# 准入与供给 {#admission}

每个模型请求跑不跑得成、走订阅还是走网关、请求体里那个模型名该写成什么——这三件事都在 `POST /llm/admission` 一次问出来。

> 讲：判定本身——谁问、问出什么、答不出来时怎么办、供给和绑定从哪里来。不讲：请求怎么走到这里（见[模型调用流程](/dev/llm)），代理拿到答案后怎么执行（见[计量代理](/dev/metering-proxy)），用量怎么折算、刹车怎么配（见[计费](/dev/billing)）。

## 唯一控制点 {#control-point}

结论 46 说的是：模型往哪走只能有一个地方说了算。这一页就是那个地方——**每个请求**问一次，而不是在会话启动时把答案签进环境里。

这个改动的实际差别：绑定的模型改在一张卡上，下一个请求就生效，而不是下一块屏幕。以前池子来自铸在会话凭据里的一个模型名——那是几小时前开出的一次性 claim，后面任何改动都够不着它，而且它与卡上的绑定是同一件事的第二个声明处。

判定归判定，答案只是答案：**答不出来就拒绝，不换池（I27）**。一个目录里说不出模型名的部署，没有第二个池可以悄悄把这单接了——那种静默换池正是「一个控制点」要拆掉的东西，所以拒绝里带的是解析器自己的话，回合就停在这。

## 三个答案 {#answers}

接口返回 `{allow, reason, reason_kind, reopens_at, supply}`：

| 字段 | 取值 | 谁看它 |
| --- | --- | --- |
| `allow` | `true` / `false` | 代理决定转发还是拒 |
| `reason` | 人能读的一句话 | 拒绝时进响应正文 |
| `reason_kind` | `"budget"` / `"binding"` | 代理据此决定拒绝的姿态：`budget` 走 429 `billing_error` 加额度用完的头（正文前缀 `cheese project budget: …`，见[计量代理](/dev/metering-proxy#refuse)），`binding` 走 400 `invalid_request_error` |
| `reopens_at` | Unix 秒，或 `null` | 预算拒绝什么时候自己解除（时间窗口重置、月度额度重置）；额度包花完、不会自己恢复时是 `null`。代理拿它写 `retry-after` 和 reset 头 |
| `supply` | `{pool, model}`；网关池、放行、且调用方同时出示了计量代理自己的凭据（`X-Cheese-Token` 为后端的 `SANDBOX_TOKEN`）时多一个 `key` | 代理拿它选路、改请求体里的模型名 |

只凭会话的 scoped token 问，拿到的是判定、不带 `key`：会话手里就是这枚令牌，把项目的网关 key 答给它，等于把一把共用的池子凭据放进每个房间。pi 的 runner 为分身问准入就是这样问的，它只要模型名。

`reason_kind` 不是装饰：代理把它见过的每一个 `allow=false` 都渲染成 429 加「cheese project budget」，一张写着目录服务不了的模型的项目卡会被告诉「额度用完了」。拒绝必须说出真实原因，否则 I27 也没满足——一个没人能照着行动的拒绝，只是静默换池换了个帽子。

`supply` 的解析在**拒绝时也做**：记录一次拒绝的调用方仍然要能说出它是被哪个池拒的。

## 席位 {#seat}

凭据只证明它是给哪位队友、哪个房间签的，证明不了这位队友现在还在不在房间里，而会话凭据能活好几十天。所以准入和 catch-all 每次都问一遍名册（`api/auth.py` 的 `require_seated_in_its_room`）：凭据写着房间，而它的队友已经不在那个房间的名册上，准入答 `allow=false`、`reason_kind` 为 `binding`（代理回 400、不重试，正文是名册拒绝的那句话）；catch-all 直接回 403。准入不用 HTTP 错误来拒，是因为代理把任何非 200 都当成后端连不上，按 fail-open 放行。不写房间的凭据（平台自己的项目凭据、不在任何房间里的文档的凭据）没有名册可问，照常放行。

队友被移出房间时，它正在跑的那一轮先被停下（`DELETE /topics/{id}/members/{handle}`），之后这枚凭据再来的请求都被拒。代理会把一次准入的答案缓存 30 秒，所以移出之后最多还有这么久，同一枚凭据可能凭缓存里那次放行再发出请求。

## 预算这一半 {#budget}

- 项目来自 scoped token 的已验证 claims（`claims["p"]`），不是请求头——头是可以自称的。没有 token 或没有 `p` 就是 401。
- `UsageService.admit_project()`（`usage/ledger.py` 的 `Ledger.admit`）答能不能跑：放行时 `reason` 是 `admitted`，拒绝时是账本的那句话（「本月额度已用完，11月1日重置。」或时间窗口满了几点恢复），见[计费流程](/dev/billing#plans)。
- 同一份额度，网关那把 `max_budget` 也按它折成美元——**一个预算，两个执行点**。
- 拒绝时顺手告诉房间：`agent/credits_notice.py` 的 `note_credits_refusal` 给正在跑的那个回合（主线或它开的分身）发一条事件，说的是账本那句拒绝理由，和回合开始前被拒时房间里那句一样（#715）。它用自己的数据库会话。

## 供给这一半 {#supply}

- 池来自**模型绑定**，在这里按请求解析：`binding.resolve()` 依次看任务绑的模型 → agent 自己的 `model` → 项目默认 → 目录默认；一个都定不下来就抛 `ValidationError`（「当前项目没有可用的默认模型…」/「这个任务绑定的模型 {bound!r} 在当前项目中不可用，需要重新选择」）。
- `supply.resolve_pool(settings)`：项目显式配的、合法的设置优先，否则默认 `GATEWAY`。
- 写进请求体的那个名字是 `WorkBinding.wire_model`：订阅路的模型在目录里是短 id（`sonnet`），发出去要用全名，只有目录知道这个对应关系，所以翻译落在绑定上，不在路由里。**请求体是两条路唯一会读模型名的地方**，这也是启动环境现在可以不写任何模型名的原因。

## 分身指定的模型 {#subagent}

主 agent 开分身时可以点名模型，这个能力属于 subagent 机制本身，跟「有身份的 AI 队友」无关——可指定的集合是**项目的模型目录**，不是任何参与者的配置（2026-09-23 拍板）。判定靠三个请求头：

| 头 | 谁写 | 意思 |
| --- | --- | --- |
| `x-cheese-subagent: 1` | 计量代理 | 这是分身请求 |
| `x-cheese-child-model` | 计量代理从请求体顶层 `model` 解析出来 | 指名（explicit） |
| `x-cheese-requested-model` | 计量代理 | 请求体里的模型名，作为回退 |

判定规则：

- **继承不算指定**：Claude Code 对每个分身请求都会写一个顶层 `model` 成员；fork、以及定义里不写模型的那些分身，写的是**父会话的模型**——那才是「没指定」在请求体里真正的长相。体里的名字翻译回来等于父会话绑定（或目录默认）的，就退回分身默认。
- **可指定的集合**是目录里属于本项目池的那些 id，再加项目显式配的两个默认（`default_model` / `default_subagent_model`，且只在目录里还在的时候列）；池外的目录项（网关项目里的订阅短名、订阅项目里的网关模型）对主 agent 不可指定——绑上它们请求就得走另一条供给路，而那条路这个项目没有凭据。
- 拒绝有两种说法，都带可指定清单：`分身指定的模型 {requested!r} 当前项目的模型目录里没有；可指定：…`（名字压根不在目录里）与 `…不在当前项目可用的模型范围内；可指定：…`（在目录里但不属于本池）。**指定了要么绑它、要么明说为什么不行**，悄悄改回默认模型正是「指定了却不生效」那个旧行为。
- 分身点到高 tier 的模型还要过策略门（`gate.Resource.model`，带 label 与 tier，审批人是项目负责人）：没过就把提议的内容当拒绝原因抛出来。

## 这几个模块各管什么 {#modules}

| 模块 | 管什么 |
| --- | --- |
| `api/routes/llm_proxy.py` | `/llm/admission` 本身，以及给 Codex / Pi 这类走 base URL 的 harness 用的 catch-all 转发 |
| `domain/agent/supply.py` | `SUBSCRIPTION` / `GATEWAY` 两个池的名字与 `resolve_pool`，池的定义只有这一处 |
| `room_task/binding.py` | `WorkBinding(model, supply, effort)`、`wire_model`、`catalog()`、`resolve()` |
| `agent_instance/configuration.py` | `project_pool()` 与 `model_choices()`：订阅的清单加网关目录里可上架的（tier 取网关上的 `cheese_tier`，缺省 included），按 harness 过滤掉跑不起来的模型；项目的 harness 没注册时是空列表 |
| `domain/agent/profiles.py` | 执行 profile：一个项目跑哪个模型、带哪套供应商环境；`full_env()` 显式把 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `CLAUDE_CODE_OAUTH_TOKEN` 清成空串，免得从后端 `os.environ` 漏进来的别的供应商配置把路由劫走 |

## 反过来那半：不能改 base URL 的 harness {#catch-all}

同一个文件里的 catch-all 服务的是**没法被代理牵走**的那类 harness：Codex 和 Pi 被指向 `{api_base}/llm/v1`。MicroCloud 上的机器够不着池子网关（它监听盒子本地地址），而把供应商 key 交给机器，等于把一份共享凭据放在平台控制不了的硬件上，花销也没法归因。所以机器只带自己的 scoped cheese token：这条路由认证它、换成项目的虚拟网关 key（跟它本机回合用的是同一把，额度和归因都不变），把上游响应原样流回去。协议无关是故意的——客户端要什么路径就转发什么路径，客户端换协议这里不用改。

项目没有 key 时拒绝，不退回池子自己的凭据——那样每个项目都会记到同一个桶里。拒绝正文里写「no model call was made」，因为这时候确实一个模型调用都没发生。

## 边界与坑 {#traps}

- **fail-open 有方向**：问不到准入时，代理回退到订阅路——它一直就有的那条路——绝不回退到网关（网关的按项目 key 它手里没有）。方向写反了，一个连不上的控制面会把请求送进一条没有额度的路。
- 准入说「可以」不等于一定跑得成：代理那边还有滚动 token 上限兜底，网关那边还有 `max_budget`。
- 判定用到的读连接要在开始网关操作**之前**还回去（`release_read_session`），否则并发准入会在等锁时把连接池耗干。
- 项目不存在是 404 `Unknown project`，不是 400：uuid 解析不了和查不到是同一件事，对外不该区分。
- 目录为空的项目不是「随便跑一个」：`model_choices` 返回空列表，绑定就解析不出模型，请求被拒并附上可指定清单。
