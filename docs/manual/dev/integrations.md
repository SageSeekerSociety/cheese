---
title: 集成
kind: 参考
summary: 借来的邮箱与飞书账号：密钥怎么存、授权给谁、邮件为什么必须人来发，以及外部系统推进来的那把 webhook 钥匙。
covers:
  - backend/app/domain/integration/
  - backend/app/domain/webhook/
  - backend/app/api/routes/integrations.py
  - backend/app/api/routes/admin_integrations.py
  - backend/app/api/routes/webhooks.py
  - backend/app/core/webhook_auth.py
---

# 集成 {#integrations}

一个人可以把自己**自己的**邮箱或飞书账号借给他指定的项目，让那里的芝士用它读、写、起草 —— 但**以他的身份**，且发邮件必须他本人确认。

> 讲：连接怎么存、授权模型、邮件的草稿与发送、飞书的两条进路、以及外部系统 POST 进来的 webhook。不讲：房间文件和资料库本身（见[资料库与产物](/dev/library#roots)），通知怎么送到人手上（见[通知](/dev/notifications#ledger)），项目与成员（见[团队、项目与成员](/dev/teams#roster)）。

## 一个连接是一把借出去的钥匙 {#model}

`Integration`（`backend/app/domain/integration/models.py`）一行 = 一个人的一个账号：

| 列 | 说明 |
| --- | --- |
| `owner_user_id` / `owner_handle` | 钥匙的主人。**只有他**能建、改、授权、发送 |
| `provider` | `mail` 或 `feishu` |
| `label` | 主人眼里它叫什么（地址，或飞书应用名） |
| `config` | **非密**设置：IMAP/SMTP 主机端口用户名；**自带应用时**还有 `app_id`、`domain`、`folders`（走平台应用的行没有这几个，见[飞书](#feishu)） |
| `secret` | 密封后的密码 / 自带应用的 `app_secret`，加用户令牌 |
| `grants` | **哪些项目**的芝士可以用它（项目 id 列表） |
| `status` / `last_error` / `last_checked_at` | `ok` / `auth_failed` / `unreachable` / `error` |

密钥不落明文：`seal` / `unseal` 走 `app.core.crypto`，`Purpose.INTEGRATION_SECRET`，并且 `bound_to=str(row.id)` —— 换一行搬过去就解不开。`public(row)` 是给页面看的视图，它**不带** `secret`；飞书额外报一个 `user_authorized`（有没有 refresh token）。

API 分成两半，`backend/app/api/routes/integrations.py` 开头就写明：`/me/...` 是主人（连接、授权、确认或放弃草稿），`/integrations/{id}/...?topic=` 是**被授权房间里**的芝士或人，用的是主人的账号。中间那一步是 `_usable`：先解析房间、再用 `for_project` 检查这个项目在不在 `grants` 里，不在就报一句带主人 handle 的话让他去勾选。

## 邮件：读是芝士的，发是人的 {#mail}

`backend/app/domain/integration/mail.py` 是 IMAP + SMTP 的纯 stdlib 实现，阻塞调用一律 `asyncio.to_thread`，失败统一变成 `IntegrationError`（带 `kind`，映射到 401/403/404/502 之一）。

**搜索要自己复核。** 2026-09-27 在 dev 上撞见的：QQ 邮箱对 `SEARCH CHARSET UTF-8 SUBJECT {芝士测试}` 回了**整个收件箱**，芝士于是拿到了它没要的验证码和收据。所以 `search` 只把服务器的答复当**候选**，真正决定命中的是 `_verified`：拉候选的信头（一次 `FETCH` 拿一批），本地再比主题和发件人；关键词还要在正文里出现的，最多打开 `BODY_CHECK_LIMIT`（40）封正文去确认，候选上限 `SCAN_LIMIT`（300）。非 ASCII 的关键词一次只能给一项（IMAP 把它当 UTF-8 literal 送）。

**草稿在邮箱里，不在平台里。** `draft` 组好 `EmailMessage`、`append_draft` 写进邮箱的草稿箱（`DRAFT_FOLDERS` 里挨个找本地化名字），把 Message-ID 记在 `MailDraft` 上、把找到的文件夹名写回 `config.drafts_folder`。附件只能来自**房间文件**（`library.read_room_file`），合计上限 20 MB，每条记下 `sha256`。

**发送要主人点头。** `POST /me/mail-drafts/{id}/send` 只认 `owner_user_id` 对得上的主人，而且发送前**重新读一遍每个附件、比对 sha256** —— 起草之后文件被改过就拒绝，理由是「发出去的会和你确认的不一样」。发送成功后还会去存一份到已发送（`SENT_FOLDERS`）、把草稿从草稿箱里删掉，这两件事的成败只作为 `notes` 返回，不影响「已发出」这个事实。服务器拒收的地址记在 `refused` 里。

芝士起草时，房间会收到一条事件块和一条给主人的通知（`EVENT_MAIL_DRAFTED`，标题「邮件草稿待你确认：…」），草稿状态走 `drafted → sent | failed | discarded`。

## 飞书：应用是平台的，授权是个人的 {#feishu}

`backend/app/domain/integration/feishu.py` 支持两种身份：

| 身份 | 凭据 | 看得见什么 |
| --- | --- | --- |
| 应用 | `app_id` + `app_secret` → tenant token | 分享给这个应用的那些文档 |
| 用户授权 | OAuth 拿到的 `user_access_token` + `refresh_token` | 这个人看得见的；**搜索 API 只在这条路上有答案** |

**应用是组织的，一个人建一次。** 成员以前各自去飞书建一个企业自建应用、把 App ID / Secret 填进「我的连接」——每个人做的是同一件事。现在它落在 `FeishuApp`（`feishu_apps` 表，固定一行）：平台管理员在飞书建好自建应用、开好云文档读写、搜索云文档、获取用户身份这几个权限、把回调地址填成 `{frontend_url}/api/integrations/feishu/callback`、发布并通过审核，然后在后台的「飞书应用」那一页（`PUT /admin/integrations/feishu`，门是 `PlatformAdminDep`）把凭据填一次。Secret 只写不回显，空着提交表示不改已经存下的那一个。

成员那边只有一颗「连接飞书」：`POST /me/integrations/feishu` 先落一行（不带任何凭据 —— 应用是管理员的，个人出的是自己的账号），`GET /me/integrations/{id}/feishu/authorize` 拿授权地址跳过去，回调把 `user_access_token` / `refresh_token` 写进**这一行**。平台没配应用时按钮是灰的，提示「管理员还没配置飞书应用」；没配就发起授权会得到同一句话的 422。

**自带凭据的老连接照旧能用。** `feishu_settings` 的次序是：这一行自己的 `app_id` 在先，平台应用在后，都没有才报错 —— 所以库里那些每个人自己建的连接不用迁移就还在跑，只是页面不再引导建新的那种。`public(row)` 用 `shared_app` 说出这一行走的是哪一条。

错误码按「调用方还能做什么」分类：`AUTH_CODES` / `FORBIDDEN_CODES` / `NOT_FOUND_CODES` → `IntegrationError` 的 kind。令牌刷新后**写回**（`keep_feishu_tokens` 在 `finally` 里跑），否则下次调用又刷一遍，而刷新令牌常常是一次性的；刷不动时报「授权已失效，到「我的连接」里重新授权」。

授权码回来的那条路是 `GET /integrations/feishu/callback`：`state` 是 `"<id>.<kid>.<digest>"`，用 `keyed_digests(Purpose.INTEGRATION_STATE, …)` 重算再 `hmac.compare_digest` 比 —— 对不上就带着一句人能读的话跳回前端页面。这条路上所有失败都跳转（页面能显示原因），不抛 4xx。

### 权限没批下来时的手工兜底 {#feishu-fallback}

API 那条路的前提是应用拿到了文档权限（管理员在飞书后台开、审核过）。审核没下来、或者要改的文档不在这个应用/授权账号看得见的范围里时，**不要**让人去等权限 —— 飞书自己的「下载为 Word」就够走完一轮：在飞书里把那篇文档**下载为 Word**，把 `.docx` 拖进房间，让芝士改，改完的 `.docx` 在房间文件里、交给本人，再由本人把它导回飞书（新建或用飞书的导入功能替换）。

这条路是**有意保留的**：它不经过任何凭据，也不受应用权限影响，所以在接飞书之前、或者权限还在审的时候，芝士照样能改得动文档。区别只在于搬进搬出这一步是人做的。

## 内网地址与明文一律不要 {#ssrf}

`connect` / `recheck` 前先 `guard_mail_hosts`：`security == plain` 直接拒（除非 `settings.integration_allow_private_hosts`），`imap_host` / `smtp_host` 走 `refuse_internal_host`。解析只信**公共 DNS over HTTPS**（`settings.integration_doh_url`）：本机 `getaddrinfo` 在 fake-ip 代理下会返回 `198.18.0.0/15` 的占位地址，所以命中那个段就去 DoH 问真实地址，查不到就拒；其余地址只用 `address.is_global and not is_multicast` 这一条判据。这套是给「人自己填主机名」的场景用的，不是给平台内部调用用的。

## webhook：从外面推进一条消息 {#webhook}

`backend/app/domain/webhook/` 是**外部系统**（CI、部署流水线）往一个话题时间线里发消息的门。

凭据是 HMAC 令牌，**一个字都不存**（`backend/app/core/webhook_auth.py`）：`WebhookToken` 只存一个 `version` 计数器，令牌里嵌着它。`mint` 把版本 +1 并签发一个含新版本的令牌（这就是轮换：此前发出的全部立刻失效），`revoke` 只 +1 而不发新令牌（这就是撤销）。`verify` 重新验签、并把令牌里的 `v` 和行上的当前值比对。

行的身份是「**哪段对话**」：一行一个 `conversation_id`（房间或任务的 id，唯一），`bump_version` 按它冲突更新。

| 门 | 谁 | 说明 |
| --- | --- | --- |
| `POST /api/topics/{topic_id}/webhook-token` | 项目成员 | 签发/轮换，原始令牌只出现这一次 |
| `POST /webhooks/{topic_id}` | 持令牌者 | **挂在根上**（不在 `/api` 下），也不在 CLI 作用域令牌那条闸门下 |

进门令牌从 `x-webhook-token` 或 `Authorization: Bearer` 取，验不过就是 401。正文要求 `content` 和 `source` 都非空，然后交给 `post_with_retries`。

`post_with_retries` 是**进程内**共享的落库函数，自己不做鉴权（HTTP 那条路验完才调它，而「合并结果回房间」那种调用方本来就受信，直接调它、不走 HTTP）。它按 `_RETRY_DELAYS_SECONDS`（0、5、30 秒）重试，**每次尝试用自己的 session** —— 它报告的结果已经在别处发生了（一次 CI 跑完、一次合并），所以哪怕调用方的事务正准备回滚，房间也必须知道。它落的是 room-only 的公告（不指名谁该动手），永远不抛异常，只返回有没有落地；路由于是把 `{"accepted": false}` 也回成 200，而不是 5xx。

## 边界与坑 {#traps}

- **`config` 与 `secret` 的界线是「密不密」。** 主机名端口放 `config`（页面要显示、要能编辑），密码和长效令牌放 `secret`。往 `config` 里塞一个令牌，它就出现在 `public()` 的返回值里了。
- **`grants` 是一个项目 id 的 JSON 列表，不是外键。** 项目删了不会自动清，`for_project` 只做一次 `in` 判断 —— 判断用的是**传进来的那个** `project_id`，所以调用方怎么解出项目就决定了授权对不对。
- **令牌的签名密钥和 sandbox 令牌共用同一个**（`webhook_auth._SECRET` 取 `SANDBOX_TOKEN`）。差别只在生命周期语义：webhook 凭据长期、可撤、嵌版本号；sandbox 令牌短 TTL 每轮一签。换成独立密钥时两处都要看一眼。
- **飞书的搜索只在用户授权那条路上有答案。** 只配了应用凭据时，`folders` 里列的目录是唯一能列的东西，别把空结果当成「文档不存在」。
- **邮件搜索的服务器答复不可信。** 复核（`_verified`）不是优化，是那条 dev 事故的修复：少了它，一次中文主题搜索就能把整个收件箱交给芝士。
- **附件只能来自房间文件。** 传别处的路径会被 `read_room_file` 拒，报错让人先 `cheese show` 把它放进房间 —— 这不是限制路径写法，是限制「发出去的东西必须先在这个房间里出现过」。
