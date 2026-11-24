---
title: 集成
kind: 参考
summary: 借来的邮箱与飞书账号：密钥怎么存、授权给谁、邮件为什么必须人来发，以及外部系统推进来的那把 webhook 钥匙。
covers:
  - backend/app/domain/integration/
  - backend/app/domain/webhook/
  - backend/app/api/routes/integrations.py
  - backend/app/api/routes/team_feishu_app.py
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
| `config` | **非密**设置：IMAP/SMTP 主机端口用户名；飞书是 `app_id`、`domain`、`folders`（见[飞书](#feishu)） |
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

## 飞书：应用设在团队上，授权是个人的 {#feishu}

`backend/app/domain/integration/feishu.py` 支持两种身份：

| 身份 | 凭据 | 看得见什么 |
| --- | --- | --- |
| 应用 | `app_id` + `app_secret` → tenant token | 分享给这个应用的那些文档 |
| 用户授权 | OAuth 拿到的 `user_access_token` + `refresh_token` | 这个人看得见的；**搜索 API 只在这条路上有答案** |

授权页地址带着 `scope`（`feishu.USER_SCOPES`）。少了 `offline_access`，飞书就不发 `refresh_token`，授权两小时后作废；反过来，代码里请求了应用后台没开通的权限，授权页对所有人报 20027。所以这张清单和团队设置「飞书应用」里的批量导入 JSON（`views/teams/detail/FeishuView.vue` 的 `FEISHU_SCOPES_IMPORT`）要一一对应，改一处就改另一处。

**应用设在团队上。** 企业自建应用只在它自己那家企业的飞书里能用，所以平台不持有飞书应用，应用由团队持有：`TeamFeishuApp`（`team_feishu_apps` 表，主键就是 `team_id`，一个团队至多一个），公司的团队设公司的应用，一个人自己用就设在他的个人团队上（`TeamService.ensure_personal_team`，和个人的设备、模型同一个团队）。团队设置里「飞书应用」那一栏（`GET/PUT/DELETE /teams/{teamId}/feishu-app`，门是 `Can(TeamPermission.edit_settings)`：团队管理员，个人团队是它的主人；个人团队的那一栏挂在个人设置里）填版本（飞书 / Lark）、App ID、App Secret；下面写着在飞书里建应用的几步：在开放平台创建企业自建应用；在「安全设置」里添加重定向 URL（`redirect_uri`，即 `{frontend_url}/api/integrations/feishu/callback`）并打开「刷新 user_access_token」；在「权限管理 → 批量导入/导出权限」里粘贴权限配置；创建版本、填申请理由（个人团队提示可用范围只选自己），等公司的飞书管理员审核。重定向 URL、权限配置、申请理由都能一键复制。保存前先拿这套凭据换一次 tenant token，Secret 不对当场报错、什么也不存；Secret 封在这一行上（绑在团队上）、从不回显，空着保存表示不改。

成员点「连接飞书」（`GET /me/integrations/feishu`）看到本人所在每个团队（个人团队在前）和它设的应用；选一个，`POST /me/integrations/feishu`（`{team_id}`，只认本人所在、且设了应用的团队）落一行：这一行只记 `config.team_id`，不复制应用的任何凭据；同一个团队再选一次是同一行。随后 `GET /me/integrations/{id}/feishu/authorize` 拿授权地址跳过去，用的是本人自己的飞书账号，回调把 `user_access_token` / `refresh_token` 写进**这一行**。一个都没设时，选单指到能设的地方：个人设置里的个人团队，以及本人管着的团队。

`feishu_settings_for(session, row)` 每次调用时从团队读应用：换了 Secret，所有连接下一次调用就用新的；团队删了应用，或者主人已不在这个团队，连接当场报 `auth_failed`、说清楚到「我的连接」重新连接。共享团队的应用对成员只走本人授权，不借应用自己的身份（`FeishuSettings.user_only`），所以只持有一个连接、没授权的人读不到分享给团队应用的文档；个人团队的应用是本人自己的，照旧可以用。

平台应用时代建的行 `config` 里没有 `app_id`：`feishu_settings` 对它抛 `credentials_invalid`，说明要删掉它、用自己公司的应用重新连接；每小时的续期把它标成 `auth_failed` 后接着处理别的行。`feishu_apps` 表这一版不再读写、仍留在库里，按两次发布的规矩由下一个 PR 删。随后 `GET /me/integrations/{id}/feishu/authorize` 拿授权地址跳过去，回调把 `user_access_token` / `refresh_token` 写进**这一行**。`feishu_settings(row)` 只从这一行取凭据，授权、换 token、续期都用它。

错误码按「调用方还能做什么」分类：`AUTH_CODES` / `FORBIDDEN_CODES` / `NOT_FOUND_CODES` → `IntegrationError` 的 kind；刷新令牌被拒（无效、过期、撤销、已用过）都算 `auth_failed`；换 tenant token 被拒（10003、10014、10015 这类）是 `credentials_invalid`，回 422。

**刷新令牌只能用一次，大约一周过期。** 由此有三条：

- **刷新前锁住这一行**（`service._renewed`）。`user_access_token` 快到期时，先 `SELECT … FOR UPDATE` 重读这一行，确实还要刷才刷，刷完立刻写回。两个调用同时发现过期，后一个等锁、读到新令牌，不会拿已经用掉的那个再刷一次。
- **到期前续上**（`service.renew_feishu`，每小时一次）。刷新令牌离过期不到 `RENEW_BEFORE_S`（两天）就刷一次，过期时间（`refresh_token_expires_at`）跟令牌一起封在 `secret` 里。一周没人用的授权照样能用；飞书规定授权满一年必须重新授权，到那时刷新被拒，这一行标成 `auth_failed`。
- **「重新检查」验的是本人授权**。有用户授权的行，检查时刷新一次：刷得动才算正常，刷不动就是 `auth_failed`，页面如实显示。只验应用凭据的话，个人授权早已失效，状态也还是「正常」。

刷不动时报「授权已失效，到「我的连接」里重新授权」。

授权码回来的那条路是 `GET /integrations/feishu/callback`：`state` 是 `"<id>.<kid>.<digest>"`，用 `keyed_digests(Purpose.INTEGRATION_STATE, …)` 重算再 `hmac.compare_digest` 比 —— 对不上就带着一句人能读的话跳回前端页面。这条路上所有失败都跳转（页面能显示原因），不抛 4xx。

### 权限没批下来时的手工兜底 {#feishu-fallback}

API 那条路的前提是应用拿到了文档权限（管理员在飞书后台开、审核过）。审核没下来、或者要改的文档不在这个应用/授权账号看得见的范围里时，**不要**让人去等权限 —— 飞书自己的「下载为 Word」就够走完一轮：在飞书里把那篇文档**下载为 Word**，把 `.docx` 拖进房间，让芝士改，改完的 `.docx` 在房间文件里、交给本人，再由本人把它导回飞书（新建或用飞书的导入功能替换）。

这条路是**有意保留的**：它不经过任何凭据，也不受应用权限影响，所以在接飞书之前、或者权限还在审的时候，芝士照样能改得动文档。区别只在于搬进搬出这一步是人做的。

## 内网地址与明文一律不要 {#ssrf}

`connect` / `recheck` 前先 `guard_mail_hosts`：`security == plain` 直接拒（除非 `settings.integration_allow_private_hosts`），`imap_host` / `smtp_host` 走 `refuse_internal_host`。解析只信**公共 DNS over HTTPS**（`settings.integration_doh_url`）：本机 `getaddrinfo` 在 fake-ip 代理下会返回 `198.18.0.0/15` 的占位地址，所以命中那个段就去 DoH 问真实地址，查不到就拒；其余地址只用 `address.is_global and not is_multicast` 这一条判据。这套是给「人自己填主机名」的场景用的，不是给平台内部调用用的。

## webhook：从外面推进一条消息 {#webhook}

`backend/app/domain/webhook/` 是**外部系统**（CI、部署流水线）往一个频道时间线里发消息的门。

凭据是 `webhook+jwt` 令牌，**一个字都不存**（`backend/app/core/webhook_auth.py`）：`WebhookToken` 只存一个 `version` 计数器，令牌里嵌着它。`mint` 把版本 +1 并签发一个含新版本的令牌（这就是轮换：此前发出的全部立刻失效），`revoke` 只 +1 而不发新令牌（这就是撤销）。`verify` 重新验签、并把令牌里的 `v` 和行上的当前值比对。

行的身份是「**哪段对话**」：一行一个 `conversation_id`（房间或任务的 id，唯一），`bump_version` 按它冲突更新。

| 门 | 谁 | 说明 |
| --- | --- | --- |
| `POST /api/topics/{topic_id}/webhook-token` | 项目成员 | 签发/轮换，原始令牌只出现这一次 |
| `POST /webhooks/{topic_id}` | 持令牌者 | **挂在根上**（不在 `/api` 下），也不在 CLI 作用域令牌那条闸门下 |

进门令牌从 `Authorization: Bearer` 取，验不过就是 401。正文要求 `content` 和 `source` 都非空，然后交给 `post_with_retries`。

`post_with_retries` 是**进程内**共享的落库函数，自己不做鉴权（HTTP 那条路验完才调它，而「合并结果回房间」那种调用方本来就受信，直接调它、不走 HTTP）。它按 `_RETRY_DELAYS_SECONDS`（0、5、30 秒）重试，**每次尝试用自己的 session** —— 它报告的结果已经在别处发生了（一次 CI 跑完、一次合并），所以哪怕调用方的事务正准备回滚，房间也必须知道。它落的是 room-only 的公告（不指名谁该动手），永远不抛异常，只返回有没有落地；路由于是把 `{"accepted": false}` 也回成 200，而不是 5xx。**真的去写成一条消息**的那一步不是它做的：写住在第 5 层的 `agent.announce`，`webhook` 在第 3 层、够不到它，所以「往房间里落一句话」这件事由调用方当参数递进来（`<&backend/app/domain/webhook/ports.py>` 的 `Poster`，和 session 工厂同一条规矩），两个调用点递的都是 `announce`。

## 边界与坑 {#traps}

- **`config` 与 `secret` 的界线是「密不密」。** 主机名端口放 `config`（页面要显示、要能编辑），密码和长效令牌放 `secret`。往 `config` 里塞一个令牌，它就出现在 `public()` 的返回值里了。
- **`grants` 是一个项目 id 的 JSON 列表，不是外键。** 项目删了不会自动清，`for_project` 只做一次 `in` 判断 —— 判断用的是**传进来的那个** `project_id`，所以调用方怎么解出项目就决定了授权对不对。
- **令牌的签名密钥和 sandbox 令牌共用同一个**（`webhook_auth._SECRET` 取 `SANDBOX_TOKEN`）。差别只在生命周期语义：webhook 凭据长期、可撤、嵌版本号；sandbox 令牌短 TTL 每轮一签。换成独立密钥时两处都要看一眼。
- **飞书的搜索只在用户授权那条路上有答案。** 只配了应用凭据时，`folders` 里列的目录是唯一能列的东西，别把空结果当成「文档不存在」。
- **邮件搜索的服务器答复不可信。** 复核（`_verified`）不是优化，是那条 dev 事故的修复：少了它，一次中文主题搜索就能把整个收件箱交给芝士。
- **附件只能来自房间文件。** 传别处的路径会被 `read_room_file` 拒，报错让人先 `cheese show` 把它放进房间 —— 这不是限制路径写法，是限制「发出去的东西必须先在这个房间里出现过」。
