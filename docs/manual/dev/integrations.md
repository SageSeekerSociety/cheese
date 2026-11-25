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

连接在团队的「飞书应用」那一栏里做。团队里每个成员都读得到这一栏（`GET /teams/{teamId}/feishu-app`，门是 `view_members`）：团队用的是哪个应用（不含 Secret）、本人能不能设（`can_manage`）、本人通过它的连接（`connection`：行 id、状态、授权完没有）。成员点「连接我的飞书账号」，`POST /me/integrations/feishu`（`{team_id}`，只认本人所在、且设了应用的团队）落一行：这一行只记 `config.team_id`（外加下面说的 `app_id` 记号），不复制应用的任何凭据；同一个团队再连一次是同一行。随后 `GET /me/integrations/{id}/feishu/authorize` 拿授权地址跳过去，用的是本人自己的飞书账号，回调把 `user_access_token` / `refresh_token` 写进**这一行**；「重新连接」对已有的那一行再走一次授权，「断开」删掉这一行。「我的连接」是总览：`GET /me/integrations/feishu` 回本人所在每个团队（个人团队在前）、它的应用和本人的连接，每个团队指到它的「飞书应用」那一栏；一个都没设时，指到个人设置里个人团队的那一栏。

`feishu_settings_for(session, row)` 每次调用时从团队读应用：换了 Secret，所有连接下一次调用就用新的。成员的令牌只在发它的那个 App ID 下用：令牌和它的发放者（`config.authorized_app_id`）记在一起，团队现在的 App ID 和它对不上、或者根本没记，这些令牌一概不用，也不拿应用自己的身份顶替（`FeishuSettings.user_only`），调用报 `auth_failed`，授权地址照常给，重新授权就回来。团队换 App ID 或删掉应用时，保存那一下把成员连接的令牌清掉、`authorized_app_id` 记成 `None`、标成要重新授权：授权过一次的团队连接在主人重新授权之前什么也不读，个人团队也一样——不拿应用自己的身份顶替（从没授权过的个人团队连接照旧可以用应用的身份读）。领取授权时（`_claim_feishu`）先看团队的应用是不是正被保存：被保存的那一下拿着它，就回 `busy`、不花掉领取号，页面把领取号放回地址栏，刷新就能再领。然后不持任何锁去飞书换授权码（从这里起领取号已经花掉），换完才对团队的应用加共享锁；团队的应用还是换码时那一个——App ID 和封好的 Secret 密文都没变，删掉再用同一个 App ID 建一次也算变了，因为重建会重新密封——才存下令牌，否则什么也不存（`app_changed`）。换码之后等不到锁、或者写这一行时等不到行锁，回 `authorize_again`：领取号已经没了，页面不放回去、只说请重新连接。两处等锁都在保存点里、最多等 `TEAM_APP_LOCK_WAIT`，等不到不会让事务中止，拿到之后把请求原来的等锁时间放回去（`_within_wait`）。写的时候只写令牌、`authorized_app_id` 和状态，不写回换码前读到的整份 `config`，主人期间改的目录留着。保存应用先拿新凭据向飞书换一次 tenant token、这期间不持有任何锁，只在写的那一下锁住团队那一行。团队删了应用，或者主人已不在这个团队，连接报 `auth_failed`、说清楚到哪里重新连接。共享团队的应用对成员只走本人授权，不借应用自己的身份，所以只持有一个连接、没授权的人读不到分享给团队应用的文档；个人团队的应用是本人自己的，照旧可以用。团队的应用只有团队里的人读写得到：没有席位、只凭平台管理员身份管着这个团队的人也不行。两次保存同时到：各自先验证、再锁住那一行写（第一次配置先 `ON CONFLICT DO NOTHING` 预留）；空着 Secret 的那一次不写 Secret，所以不会把期间换上的新 Secret 盖回去；期间另一次保存把 App ID 换成了别的，空着 Secret 的这一次就没有为这个 App ID 验证过的 Secret，报 `feishuTeamAppSavedMeanwhile`、什么也不写。成员算不算「授权完」（`user_authorized`）按调用实际会用的那份令牌算：有刷新令牌，而且团队连接上它是在团队现在的 App ID 下发的（`member_authorized`）。一个人在一个团队上只有一个连接（`uq_integrations_owner_feishu_team`），两次连接同时插入时后到的拿先建的那一行。

新建的团队连接 `config` 是 `{"team_id", "app_id": "team-app:<team_id>", "folders"}`：`app_id` 是一个没有哪个飞书应用会用的记号，团队应用之前的版本（回退镜像时还在跑的那一版）把 `config` 里没有 `app_id` 的行当成平台应用的连接、拿平台应用的身份去调；有了这个记号，它拿一个飞书不认的 App ID 去调、被拒，也不会把这一行当成自己能复用的那一行。从较早的连接（自带应用的、平台应用时代的）走团队重新连接时，新的那一行不借给任何项目：主人自己勾选；较早的那一行还列着它借给了哪些项目，页面提示新连接不沿用、要重新勾选。

**较早的连接原样读。** 团队应用的四个迁移（都不碰已有的行；旧的 API 允许同一个人、同一个 App ID 连好几次，这些行原样留着，没有按 App ID 建唯一索引）（`4288e24987e2` 建表、`6bfa39e854c4` 加外键 `NOT VALID`、`8a9d4083c7c7` 验证外键（先用 `with_lock_retries` 拿下两张表）、`499940a660b1` 用 `CONCURRENTLY` 建 `uq_integrations_owner_feishu_team`）一行已有的连接都不改；建表用 `IF NOT EXISTS`、加外键先按名字和所在的表查有没有，中途断掉可以重跑。显式地把库降级到它们之前会删掉 `team_feishu_apps`，各团队设好的应用一并没了；只回退镜像不降级数据库则不受影响。自带 `app_id` / `app_secret` 的连接照旧用它自己那套凭据（续期也是），页面提示主人用团队的飞书应用重新连接；平台应用时代建的、`config` 为空的连接显示需要重新连接（不算正常），页面上不给它「授权个人账号」的按钮（没有应用可授权），只指到团队的飞书应用；`feishu_settings_for` 对它抛 `credentials_invalid`，续期跳过它。`feishu_apps` 表这一版不再读写、仍留在库里，按两次发布的规矩由下一个 PR 删。

**一次调用写回的东西只落在它用的那份凭据上。** 刷新令牌一刷新就在自己的短事务里存下（见下面），请求的事务从不持有没存下的令牌。长调用中途令牌到期时，客户端不自己刷新，而是走同一个上锁、存下的续期（`FeishuClient` 的 `renew`，即 `_renew_locked`）：别的请求等锁、拿到已存下的新令牌，期间主人重新授权了就接着用新的授权。调用失败时只写「只有主人能修」的失败（授权失效、应用凭据不对），由 `IntegrationService._write_back` 写，而且是条件写：只在这一行的 `secret` 还是这次调用用的那一份（续期返回的、令牌从它解出来的那份密文；密封每次都换密文，正好当版本号用）时才写；期间主人重新授权了，这一笔就什么也不写。它先结束请求的事务、在同一个连接上单独提交。续期刷新成功时把状态改回「正常」：之前记在旧凭据上的失败不再成立。改目录（`PATCH` 带 `folders`）在写这一行之前先检查：先换应用的 tenant token、再刷新本人的令牌，飞书这时不可用就停在前一步。转给人、写进地址栏和记录的飞书原话里，这次调用带的每一样凭据都换成 `***`，不论长短：App Secret、tenant token、本人的令牌、OAuth 授权码、刷新令牌；授权页拒绝后跳回来的 `error` 里回显的授权码和 `state` 也一样。

错误码按「调用方还能做什么」分类：`AUTH_CODES` / `FORBIDDEN_CODES` / `NOT_FOUND_CODES` → `IntegrationError` 的 kind；刷新令牌被拒（无效、过期、撤销、已用过）都算 `auth_failed`；换 tenant token 时只有飞书自己的凭据错误码（`CREDENTIAL_CODES`：10003、10014、10015）是 `credentials_invalid`、回 422。只有飞书自己的信封（带整数 `code` 的 JSON 对象）才算飞书在说话：没有信封的回答（空的、网关的 HTML 页、JSON 数组、没有 `code` 的对象）从不算成功，也从不算成「文档不存在」「授权失效」这类只有飞书能下的结论，只按 HTTP 状态判：限流（429、99991400）、超时（408）或出错（5xx）回 503 `busy`、可以重试；其余（比如网关的 404、401）是上游错误，回 502，每小时的续期把它留到下一轮。每小时的续期一行出了任何异常都记日志、接着处理别的行。

**刷新令牌只能用一次，大约一周过期。** 由此有三条：

- **刷新是一个单独的短事务**（`service._refreshed`）。`user_access_token` 离到期不到十分钟时，结束请求到这时为止只读过的事务，在**同一个连接**上开一个短事务：`SELECT … FOR UPDATE` 重读这一行，确实还要刷才刷，新令牌写下、提交，然后才发文档调用。锁只在刷新加提交那一下持有；不另要一个连接，并发的刷新不会各占一个连接再等第二个、把连接池耗光。两个调用同时发现过期，后一个等锁、读到已经提交的新令牌，不会拿已经用掉的那个再刷一次；前一个的文档调用随后失败也不影响。请求在这之前不能写过东西（不然会被这次提交带走）：会话里有没 flush 的改动、或者这个事务已经经 ORM flush 过改动，续期都直接拒绝（`_wrote`；经会话直接执行的 Core `UPDATE` 看不出来，目前没有调用方在续期前这样做），所以改目录先检查、后写。
- **到期前续上**（`service.renew_feishu`，每小时一次）。刷新令牌离过期不到 `RENEW_BEFORE_S`（两天）就刷一次，过期时间（`refresh_token_expires_at`）跟令牌一起封在 `secret` 里。一周没人用的授权照样能用；飞书规定授权满一年必须重新授权，到那时刷新被拒，这一行标成 `auth_failed`。
- **「重新检查」验的是本人授权**。有用户授权的行，检查时刷新一次：刷得动才算正常，刷不动就是 `auth_failed`，页面如实显示。只验应用凭据的话，个人授权早已失效，状态也还是「正常」。检查的结论只在它用的凭据都没变时才写下（`_settle`：这一行的 `secret` 是检查用的那一份，团队应用的 Secret 密文也是）：检查期间管理员换了 Secret、主人又重新授权了，旧凭据上的失败不会盖到新的授权上。

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
