# integrations 组接口分析

组内 6 个路由模块（`connector.py` / `github_install.py` / `installer.py` / `integrations.py` / `remote_mcp.py` / `webhooks.py`）每个只导出一个 `APIRouter`，逐条核对装饰器后：**清单 50 条与源码一致，路径、handler、行号都对得上**，没有发现清单与源码的出入。

（仅一处描述性不一致，不改任何行为：`app/api/routes/webhooks.py:1-8` 的 docstring 说本路由 "Root-mounted (not under /api)"，而 `docs/api-conventions.md` 现在的规则是「所有路由都是裸路径，对外统一挂在 `/api` 下」。文案过时，清单里的 `/webhooks/{topic_id}` 是对的。）

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | POST | `/connector/auth/device/start` | 可优化 | 匿名可调、无限流，`device_auth_code` 只增不删（全仓无清理），响应缺 `expires_in` |
| 2 | POST | `/connector/auth/device/poll` | 可优化 | 明文返回长效设备令牌，却没有 `Cache-Control: no-store`（RFC 6749 §5.1），也没有轮询限流 |
| 3 | GET | `/connector/auth/device/proposed-name` | 可优化 | `code_device_name` 不校验 TTL，过期 code 仍能读到设备名，与 `_live_code` 的语义不一致 |
| 4 | POST | `/connector/connect` | 暂无 | 审批在登录后、幂等（`approve` 对已批 code 返回同一 device）、项目授权走 `authorize_project` |
| 5 | GET | `/connector/my/devices` | 可优化 | `list_devices_by_owner` 1+2N 条 SQL；`_device_screens` 每台设备重扫一遍全部在线 screen |
| 6 | PATCH | `/connector/my/devices/{device_id}` | 暂无 | 属主校验在 service（`_require_hosted_owned`），改名走非空校验，无越权面 |
| 7 | DELETE | `/connector/my/devices/{device_id}` | 暂无 | 属主校验 + 级联清理绑定；200 `{deleted:true}` 而非 204，是本项目惯例 |
| 8 | POST | `/connector/my/devices/{device_id}/teams` | 可优化 | 属主查询做了两遍，末尾 `_device_view(device)` 对可能为 `None` 的值加了 `type: ignore` |
| 9 | GET | `/connector/teams/{team_id}/devices` | 可优化 | 单次请求 SQL 量 = `1+3N` + `Σ项目(2+3×设备数)`，团队越大越慢 |
| 10 | DELETE | `/connector/my/devices/{device_id}/teams/{team_id}` | 暂无 | 属主校验（`unassign_from_team` → `_require_hosted_owned`）后解绑，无问题 |
| 11 | GET | `/projects/{project_id}/github/connection` | 暂无 | 登录 + `authorize_project`，只回 `{connected,repo,account}`，无敏感字段 |
| 12 | POST | `/projects/{project_id}/github/connect` | 可优化 | 逐个 installation 串行调 GitHub，且每次调用都新建 `httpx.AsyncClient`（每装一次就一次 TLS 握手） |
| 13 | GET | `/projects/{project_id}/github/install-url` | 暂无 | state 有 TTL + `single_use_state.reserve`，manager 才可调，是组内的正面样板 |
| 14 | GET | `/github/app/callback` | 暂无 | 验签 state + 单次消费 + 用用户 token 反查 installation 归属 + 写权限校验，防重放已做全 |
| 15 | GET | `/connector/install.sh` | 暂无 | 无秘密、无业务逻辑，`_origin` 在生产由 `connector_public_base` 决定；纯生成脚本 |
| 16 | GET | `/connector/install.ps1` | 暂无 | 同 15，模板替换，无状态无查询 |
| 17 | GET | `/connector/claude/{version}/{platform}/{name}` | 可优化 | 匿名可触发 40MB 级上游拉取并落盘，无鉴权无限流；未回 `X-Checksum-SHA256` |
| 18 | GET | `/connector/pi/{version}/{platform}/pi.tar.gz` | 可优化 | 同 17，匿名可触发上游拉取 + 磁盘缓存 |
| 19 | GET | `/connector/toolchain/{tool}/{platform}/artifact` | 可优化 | 同 17；另：`X-Checksum-SHA256` 只有它发，17/18/20 都不发，口径不一 |
| 20 | GET | `/connector/latest/{target}/{name}` | 暂无 | target 白名单 + `binary_name` 比对，无上游拉取；`FileResponse` 自带 ETag/Last-Modified |
| 21 | GET | `/me/integrations` | 暂无 | 本人连接列表，量级小；`public()` 只对 feishu 行解密，未回 secret |
| 22 | POST | `/me/integrations/mail` | 可优化 | `_check` 在请求里串行做 DNS 解析 + IMAP 登录 + SMTP 登录（各 30s 超时），且期间占着连接 |
| 23 | POST | `/me/integrations/feishu` | 可优化 | 同 22（换 tenant_token 拉取，20s 超时），建立连接时占池 |
| 24 | PATCH | `/me/integrations/{integration_id}` | 可优化 | 改 folders/secret 会隐式触发一次上游探活（`update` → `_check`），同样占池 |
| 25 | POST | `/me/integrations/{integration_id}/check` | 可优化 | 整个请求就是一次上游探活，池里的连接被压住直到探活返回 |
| 26 | DELETE | `/me/integrations/{integration_id}` | 暂无 | 属主校验后删除，回 `{deleted:id}`，无问题 |
| 27 | GET | `/me/integrations/{integration_id}/feishu/authorize` | 可优化 | state 只有 HMAC，无 `exp`、无一次性消费，链接永久有效（对照 13/14 的写法） |
| 28 | GET | `/integrations/feishu/callback` | 可优化 | 同上；另外把上游错误原文截 200 字回显到跳转 query |
| 29 | GET | `/me/mail-drafts` | 可优化 | `.limit(100)` 静默截断，无 cursor/offset，也没有 `total` 之外的「还有没有下一页」信号 |
| 30 | POST | `/me/mail-drafts/{draft_id}/send` | 可优化 | 无行锁/无幂等键，两个并发 `/send` 能各发一封；单请求最多 3 次串行 IMAP/SMTP 建连 |
| 31 | POST | `/me/mail-drafts/{draft_id}/discard` | 暂无 | 状态机校验 `drafted` 才能丢弃，单事务 |
| 32 | GET | `/projects/{project_id}/integrations` | 可优化 | `granted()` 是全表 `select(Integration)` 后在 Python 里筛 grants，等于把平台所有连接读进内存 |
| 33 | POST | `/integrations/{integration_id}/mail/search` | 可优化 | IMAP 调用（≤30s）期间 DB 连接一直处于 idle-in-transaction |
| 34 | GET | `/integrations/{integration_id}/mail/messages/{uid}` | 可优化 | 同 33 |
| 35 | GET | `/integrations/{integration_id}/mail/messages/{uid}/attachments/{index}` | 可优化 | 同 33；另把第三方邮件的 `Content-Type` 原样回给浏览器，缺 nosniff/CSP |
| 36 | POST | `/integrations/{integration_id}/mail/drafts` | 可优化 | 同 33；且 handler 一次做完草稿写入 + Block + 通知三件事 |
| 37 | POST | `/integrations/{integration_id}/feishu/search` | 可优化 | 飞书调用（≤20s）期间连接 idle-in-transaction |
| 38 | GET | `/integrations/{integration_id}/feishu/docs/{document_id}` | 可优化 | 同 37（`read` 内部还会翻页 + 再取一次文档链接，往返更多） |
| 39 | POST | `/integrations/{integration_id}/feishu/docs` | 可优化 | 同 37（建文档 + 追加内容，两次上游往返都压在同一个事务里） |
| 40 | PATCH | `/integrations/{integration_id}/feishu/docs/{document_id}` | 可优化 | 同 37（append + replace + read 三次往返） |
| 41 | GET | `/projects/{project_id}/mcp/servers` | 暂无 | 成员校验 + `settings_view`，只回 `set: true/false` 不回值 |
| 42 | POST | `/projects/{project_id}/mcp/servers/{name}/connect` | 暂无 | state 加密 + `exp` + `single_use_state`，PKCE S256，`resource` 参数齐全 |
| 43 | DELETE | `/projects/{project_id}/mcp/servers/{name}/connection` | 暂无 | `with_for_update` + 尽力撤销上游 token，未连接回 404 合理 |
| 44 | PUT | `/projects/{project_id}/mcp/secrets/{name}` | 暂无 | 只存密文、回 `ok(None)`，变量名必须在 `.mcp.json` 里出现过 |
| 45 | DELETE | `/projects/{project_id}/mcp/secrets/{name}` | 暂无 | 幂等删除；与 43 的 404 语义不一致，属小口径问题（见模块建议） |
| 46 | GET | `/mcp/oauth/client.json` | 暂无 | 按规范裸 JSON 不带信封，是 CIMD 文档要求，改不得 |
| 47 | GET | `/mcp/oauth/callback` | 暂无 | state 加密且带 `exp` + 一次性 claim + RFC 9207 iss 校验，组内最完整 |
| 48 | POST | `/topics/{topic_id}/mcp/{name}` | 暂无 | 房间凭证 + project/topic 双重比对；把上游错误包成 200 `{error}` 是给 agent 看的既定约定 |
| 49 | GET | `/topics/{topic_id}/mcp/servers` | 可优化 | `room_view` → `settings_view(fresh=True)` 每次强制读 forge 的 `.mcp.json` 并解密全部 secret |
| 50 | POST | `/webhooks/{topic_id}` | 可优化 | 请求内同步重试最长 35s（GitHub 10s 就判投递失败），无 `X-GitHub-Delivery` 去重，失败也回 200 |

**合计 50 条**：`可优化` 29，`暂无` 21，`待确认` 0。

## 二、详细分析（按收益从高到低）

### 【12 个接口同型】`POST /integrations/{integration_id}/mail/search` 等：上游调用期间把池里的连接压在 idle-in-transaction

同型还有 #34 #35 #36 #37 #38 #39 #40（`/integrations/{id}/...`）与 #22 #23 #24 #25（`/me/integrations/...`）。

- **现状**：`integrations.py:394-400`（`mail_search`）先 `_usable()`（内部 `_in_room` → `TopicService.place_or_404` 发 SELECT，会话 autobegin），然后 `service.search(...)` → `IntegrationService._mail`（`service.py:314-324`）→ `await asyncio.to_thread(mail.search, ...)` 阻塞等 IMAP（`mail.py:26` `TIMEOUT = 30`；飞书是 `feishu.py:94` `timeout=20`），最后 `finally: await db.commit()` 才放连接。
- **问题**：`asyncio.to_thread` 让事件循环不阻塞，但**数据库连接在这 20–30s 里仍被这个会话占用并处于 idle-in-transaction**（SQLAlchemy autobegin 从 `place_or_404` 的 SELECT 起就没结束）。池是 `pool_size=20` + `max_overflow=15`（`app/core/db.py:103-105`），`pool_timeout=30s`。35 个并发邮件/飞书调用就能把池抽干，之后**所有**请求（含页面加载、后台任务）一起等 30s 然后 `QueuePool limit ... connection timed out`——这正是仓库里 09-18/09-19 两次事故的形状（`connector.py:80-88` 与 `app/core/db.py:130-150` 都在讲同一件事）。飞书 `search` 在无个人授权时还会对每个 folder 发一次 `drive/v1/files` 往返（`feishu.py:236-243`），把窗口拉得更长。
- **优化**：读事务用完就把连接还回池，然后再做上游调用。仓库里已经有现成样板：`app/core/db.py:182` 的 `release_read_session`（`llm_proxy.py:325` 与 `forge_files.py:73` 都在用）。对本组更简单的是就地 `commit()`——`async_session_factory` 是 `expire_on_commit=False`（`db.py:79`），commit 后 ORM 行仍可读、仍可写，行为完全不变：

```python
@router.post("/integrations/{integration_id}/mail/search")
async def mail_search(
    integration_id: uuid.UUID,
    body: SearchIn,
    topic: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    row, *_ = await _usable(db, resolver, integration_id, topic)
    label = row.label
    # 改动点：结束读事务，把连接还回池，再进最长 30s 的 IMAP 调用。
    # expire_on_commit=False，row 仍然可读；_mail 里失败时写 row.status 也仍会落库。
    await db.commit()
    try:
        hits = await IntegrationService(db).search(row, **body.model_dump())
    finally:
        await db.commit()
    return ok({"source": label, "messages": hits})
```

同样的两行加在 `mail_read`(403-417)、`mail_attachment`(420-441)、`mail_draft`(444-511)、`feishu_search`(514-525)、`feishu_read`(528-539)、`feishu_create`(542-555)、`feishu_edit`(558-582) 的 `_usable` 之后。`_check` 路径（`mail_search` 之外的 #22–#25）同理：`connect`/`update`/`recheck` 里的 `await self._check(row)` 之前先 `await self._session.commit()`（`row` 是新加的、尚未 flush，注意 `connect` 的顺序：`seal` → `_check` → `add` → `flush`，所以在 `_check` 之前不能 commit，应改成先 `add`+`flush` 拿事务、commit、再 `_check`）。
- **契约**：不改请求/响应形状，不改状态码，不改信封。唯一可见差别是突发并发下的延迟消失。无数据迁移。
- **测试**：`backend/tests/integration/test_an_upstream_call_holds_no_connection.py`
  - `test_a_mail_search_returns_its_connection_before_the_mailbox_call`：`monkeypatch.setattr(mail, "search", probe)`，`probe` 内断言请求会话此刻 `not session.in_transaction()`（会话从 `app.dependency_overrides[get_db]` 取，参照 `tests/integration/conftest.py` 的 portal 写法）。
  - `test_ten_concurrent_mail_searches_do_not_wait_for_a_connection`：起 10 个并发 `/mail/search`（stub 每次 `time.sleep(1.0)`），断言全部在 3s 内返回（修复前会各自独占连接）。
  - 文件内可复用 `tests/integration/test_integrations.py:20-55` 的 `Mailbox`/`_store` fixture。

### POST /me/mail-drafts/{draft_id}/send

- **现状**：`integrations.py:331-344` 读草稿 → `service.send(...)`（`service.py:430-473`）→ `finally: await db.commit()`。`send` 只在 `service.py:432` 做了一次 `if draft.status != "drafted"` 的读检查。
- **问题**：**两个并发 `/send` 会各发一封邮件**。`get_draft`（`service.py:424-428`）是普通 `session.get`，不带 `with_for_update`；两个请求都读到 `status == "drafted"`，都进 `mail.send`，`status="sent"` 的写入只有一个能留下。用户端看到的是「点了两次/网络重试了一次 → 对方收到两封一模一样的邮件」。除了竞态，单请求最多串 3 次建连：`mail.send`（`service.py:447`）、`mail.save_sent`（458）、`mail.remove_by_message_id`（466），每次都是 `_imap(settings)` 里 `IMAP4_SSL(...)` + `login()`（`mail.py:63-90`），各 30s 超时 → 最坏 90s+，客户端超时重试正好撞上第一封还没落库的窗口。没有 `Idempotency-Key` 之类的幂等入口，重试无从收敛。
- **优化**：先原子认领再发信，把「状态检查」变成一次条件 UPDATE（Stripe 的幂等键思路在没法加 header 时的等价物）：

```python
from sqlalchemy import update
from app.domain.integration.models import MailDraft

@router.post("/me/mail-drafts/{draft_id}/send")
async def send_draft(
    draft_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = IntegrationService(db)
    draft = await service.get_draft(draft_id)
    # 改动点：原子认领。并发的两个 /send 只有一个能让 rowcount == 1。
    claimed = await db.execute(
        update(MailDraft)
        .where(MailDraft.id == draft.id, MailDraft.status == "drafted")
        .values(status="sending")
    )
    await db.commit()  # 释放连接：发信是 SMTP 往返，不能占着事务
    if claimed.rowcount != 1:
        raise ValidationError("这封草稿已经在发送中，或者已经不是待发送状态")
    draft.status = "sending"
    try:
        result = await service.send(draft, owner_user_id=actor.user_id, by=actor.handle)
    finally:
        await db.commit()
    return ok(result)
```

配套一处小改：`service.py:432` 的守卫要接受刚认领的状态，否则 `send` 会拒绝自己刚写下的 `sending`：

```python
        if draft.status not in ("drafted", "sending"):
            raise ValidationError(f"这封草稿已经是「{draft.status}」状态，不能再发")
```

（`discard`（`service.py:475-484`）保持只认 `drafted`，语义不动。不建议用 `with_for_update` 顶替：那样会把行锁跨过整个 SMTP 往返。）

- **契约**：请求/响应形状不变；新增一种可见失败态——并发重放会得到 `ValidationError`（422 信封），这也是唯一新增的状态码路径。**不要**改成回 200 假装成功，那会让第二个用户以为信发了。`"sending"` 会短暂出现在 `draft.status`（`draft_view` 直接透出），若前端按枚举渲染需要加上这一档。
- **测试**：`backend/tests/integration/test_a_draft_is_sent_once.py`
  - `test_two_concurrent_sends_deliver_one_message`：`mail.send` 里 `time.sleep(0.5)` 并用 `threading.Event` 让两个线程同时进，断言 stub 的 `sent` 只有 1 条。
  - `test_a_second_send_of_a_sent_draft_is_refused`：第一次成功后再发，断言 422 且消息不重复。
  - `test_a_send_that_fails_records_failed_and_the_claim_is_released`：`mail.send` 抛 `IntegrationError` 后断言 `status == "failed"`。

### POST /webhooks/{topic_id}

- **现状**：`webhooks.py:40-68`：`_caller_token` 取 token → `webhook_service.verify` 比对版本 → 校验 `content`/`source` → `TopicRepository.get` 再查一次房间 → `post_with_retries(...)`，返回 `ok({"accepted": landed})`。`post_with_retries`（`service.py:50-108`）在**请求内**按 `_RETRY_DELAYS_SECONDS = (0, 5, 30)`（`service.py:27`）重试，每次 `asyncio.sleep` 后新开一个会话写 Block。
- **问题**：
  1. **请求被拖到最长 35s**。GitHub 的 webhook 最佳实践是「10 秒内回 2xx，重活异步做」（https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks ）；这里第一次写库失败就要睡 5s、再睡 30s。外部系统（CI）超时后重发，就是重复落块——**同一份 CI 结果在房间里出现两遍**。
  2. **没有任何投递去重**。GitHub 每次投递带唯一的 `X-GitHub-Delivery`（同一页文档建议用它防重放），本接口不看任何投递标识，`announce`（`domain/agent/announce.py`）也不去重，重发/手动 redeliver 一定重复。
  3. **失败也回 200**。`landed=False` 时仍是 `code: 200, data.accepted: false`，调用方（CI 脚本）从状态码上读不出「没落库」，而那正是唯一需要重试的情形。GitHub 的约定是 5xx/超时才代表「请重投」。
  4. `TopicRepository(db).get(topic_id)`（`webhooks.py:58`）是多余查询：`verify` 已经要求 `webhook_tokens` 里存在该 topic 的行（`service.py:46`，FK `ondelete=CASCADE`），令牌里的 `t` 也已经被比对过；`post_with_retries` 对房间消失的情形自己有 `block is None` 分支（`service.py:91-97`）。
  5. `content` 没有长度上限（`webhooks.py:51`，`body: dict` 无 schema 约束），持有 token 的调用方可以 POST 任意大的正文进 `blocks`。
- **优化**：① 立即返回、后台落库；② 按投递 id 去重；③ 失败回 503 而不是 200。

```python
import uuid
from typing import Annotated
from app.core.background import spawn

_MAX_CONTENT = 64 * 1024  # 一条 webhook 正文的上限；超出是调用方的错，不是重试能解决的

@router.post("/{topic_id}", status_code=202)
async def receive_webhook(
    topic_id: uuid.UUID, body: dict, request: Request, db: DbSession
) -> dict:
    token = _caller_token(request)
    valid = bool(token) and await webhook_service.verify(db, topic_id=topic_id, token=token)
    if not valid:
        raise AuthenticationRequiredError("Invalid or revoked webhook token")

    content = (body.get("content") or "").strip()
    source = (body.get("source") or "").strip()
    if not content:
        raise ValidationError("content 不能为空")
    if not source:
        raise ValidationError("source 不能为空")
    if len(content) > _MAX_CONTENT:
        raise ValidationError(f"content 超过 {_MAX_CONTENT} 字节")

    # 改动点 1：投递去重。X-GitHub-Delivery 之类的一次性投递 id，同 id 只落一次。
    delivery = request.headers.get("x-github-delivery") or request.headers.get(
        "x-delivery-id"
    )
    if delivery and not await webhook_service.claim_delivery(db, topic_id, delivery):
        return ok({"accepted": True, "duplicate": True})  # 2xx，重复不是错误

    # 改动点 2：落库挪到后台，请求立刻回；GitHub 只等 10 秒。
    spawn(
        webhook_service.post_with_retries(
            async_session_factory, topic_id=topic_id, content=content, source=source
        ),
        name="webhook land",
    )
    return ok({"accepted": True})
```

去重落地只需要一张小表（或复用 Redis）：

```python
# app/domain/webhook/repositories.py
class WebhookDeliveryRepository:
    async def claim(self, topic_id: uuid.UUID, delivery: str) -> bool:
        """True 表示这次投递第一次见。INSERT ... ON CONFLICT DO NOTHING 的 rowcount。"""
        stmt = (
            insert(WebhookDelivery)
            .values(topic_id=topic_id, delivery_id=delivery)
            .on_conflict_do_nothing(index_elements=[WebhookDelivery.delivery_id])
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1
```

若要保留「客户端能确认落库」的能力，比 202 更保守的做法是保留同步路径但**把重试移出请求**：`post_with_retries` 只试一次，失败就 `raise GatewayUnavailableError(...)` → 503（信封仍是 `{"code","message","data"}`），让外部系统重投——重投这时是安全的，因为去重已生效。两种改法二选一，别都不做。

- **契约**：**会改状态码**——「同步、200 `{accepted:false}` 表示没落库」变成「202 已受理 / 503 请重投」。`data` 里新增 `duplicate`（只在重复投递时出现）。信封不动。这条是把 `accepted` 的语义说清楚，不是新增协议；但如果有 CI 脚本按 200 判成功，需要一起改（仓库内 `post_with_retries` 的另一个调用方是 merge 结果回房间，走的是进程内直调，不受影响）。响应正文长度上限是新增的 4xx 路径。
- **测试**：`backend/tests/unit/test_webhook.py`（已存在，在其上追加）
  - `test_a_redelivery_with_the_same_delivery_id_lands_one_block`：两次带同一 `X-GitHub-Delivery` 的 POST，断言 `blocks` 只多一行且第二次响应带 `duplicate`。
  - `test_a_failing_land_answers_5xx_not_200`：monkeypatch `announce` 抛错，断言响应是 503 而不是 `code: 200`。
  - `test_the_route_does_not_sleep_in_the_request`：monkeypatch `asyncio.sleep` 记录调用，断言请求路径上没有 `5`/`30` 的睡眠。
  - `test_an_over_long_content_is_refused`：65KB 正文 → 422 信封。

### GET /projects/{project_id}/integrations

- **现状**：`integrations.py:364-383` → `IntegrationService.granted(project_id)`（`service.py:222-224`）：
  ```python
  rows = await self._session.scalars(select(Integration))
  return [r for r in rows if str(project_id) in (r.grants or [])]
  ```
- **问题**：**没有 WHERE，把平台所有集成连接读进内存**再在 Python 里筛 JSON 数组。任何项目的任何房间成员调一次，都是全表扫描 + 全量 ORM 实例化（每行还带一个密文 `secret` 字段）。这是本组最直白的一处「随平台规模变慢」：连接数翻倍，这个接口就翻倍，且与提问者无关。GitHub 的列表接口约定是「列表必须分页、按页取」（https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api ）。
- **优化**：把过滤下推到 SQL。`grants` 是 JSON 列，Postgres 用 JSONB 包含操作符（该列在模型里是 `JSON`/`JSONB`，用 `cast` 保证两端一致）：

```python
# app/domain/integration/service.py
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB

    async def granted(self, project_id: uuid.UUID) -> list[Integration]:
        # 改动点：过滤下推到数据库，别再全表捞回 Python 筛。
        return list(
            await self._session.scalars(
                select(Integration)
                .where(cast(Integration.grants, JSONB).contains([str(project_id)]))
                .order_by(Integration.created_at)
            )
        )
```

若 `grants` 列类型不想依赖方言差异，退一步也可以只把需要的列取出来（`select(Integration.id, Integration.provider, ...)`）——但那是治标：真正要的是 `WHERE`。顺带给 `grants` 建一个 GIN 索引（`CREATE INDEX ... USING gin ((grants::jsonb))`），否则每次仍是顺序扫描。

- **契约**：响应形状不变（同一批行、同一字段）。需要一条 alembic 迁移建索引（可选但建议）。风险：`cast(...JSONB)` 在 SQLite 测试库上行为不同——`tests/integration` 跑的是 Postgres（见 `tests/integration/migration_docs.md` 与 conftest 的 SAVEPOINT 用法），但 `tests/unit` 里若有 fake repository 不受影响；若测试用 SQLite，需加 `Integration.grants` 为 JSONB 的方言分支。
- **测试**：`backend/tests/integration/test_project_integrations_lists_only_granted.py`
  - `test_only_granted_connections_are_listed`：造 3 个连接（1 个授权本项目、2 个授权别处），断言只回 1 条。
  - `test_the_listing_does_not_read_the_whole_table`：用 `tests/integration/test_hot_path_queries.py:27-42` 的 `counting_sql()` 包住请求，断言发出的 SELECT 只有一个且包含 `WHERE`（不含 `grants` 的全表语义），并可断言语句里出现 `grants`。

### 【2 个接口同型】GET /me/integrations/{integration_id}/feishu/authorize 与 GET /integrations/feishu/callback

- **现状**：`integrations.py:265-267` 的 `_state()` = `f"{integration_id}.{kid}.{digest.hex()}"`，`digest` 是 `keyed_digest(Purpose.INTEGRATION_STATE, integration_id.bytes)`（`crypto.py:153-158`，纯 HMAC）；callback（`integrations.py:287-317`）用 `keyed_digests(...).get(kid)` + `hmac.compare_digest` 验签后即换 token。
- **问题**：**state 里只有「哪个连接」，没有过期时间、没有一次性凭据**。
  1. 无 `exp`：一条 `.../feishu/authorize` 返回的授权链接**永远有效**。授权码本身是一次性的（飞书侧会拒重复 code），但 state 不过期意味着链接可以长期留在一个被转发过的聊天记录/工单里，谁拿到谁就能把**自己的**飞书账号绑到那条连接上（这会让连接所有者之后的读文档操作落在别人的账号语义下）。
  2. 无单次消费：同一 state 的 callback 可以重复触发（每次都会再打一次飞书 token 端点），没有 `single_use_state.claim` 那层保护。
  对照同一个仓库里同一类流程的写法：#13/#14 的 GitHub 安装用了 `github_install_state.py:14-16` 的 600s `exp` + `_install_url` 里的 `reserve`（`github_install.py:89-97`）；#47 的 MCP OAuth 用了 `_seal`（加密 + `exp`）+ `finish_connect` 里的 `claim`（`remote_mcp/service.py:302-307`）。**本组的这条是三个里唯一没跟上的。** OAuth 2.0 对 `state` 的要求正是「绑定到用户代理 + 防 CSRF」，业界一致做法是短期且一次性。
  次要：错误分支把上游原文 `str(exc)[:200]` 直接拼进跳转 query（`integrations.py:313`），虽有 `urlencode` 无注入，但把飞书的内部错误回显给了访问者。
- **优化**：把 `exp` 与 `jti` 塞进 state 的被签名内容（保持同一个 `Purpose` 与 `kid` 轮换机制），并在 callback 里消费 `jti`：

```python
import json, time, uuid
from app.core.single_use_state import claim, reserve, SingleUseUnavailableError

_STATE_SCOPE = "integration-feishu"
_STATE_TTL_S = 600

def _state(integration_id: uuid.UUID) -> tuple[str, str]:
    """(state, jti)。jti 由调用方 reserve，与 github_install 的 _install_url 同形。"""
    jti = uuid.uuid4().hex
    body = json.dumps(
        {"id": str(integration_id), "jti": jti, "exp": int(time.time()) + _STATE_TTL_S},
        separators=(",", ":"),
    ).encode()
    kid, digest = keyed_digest(Purpose.INTEGRATION_STATE, body)
    return f"{body.hex()}.{kid}.{digest.hex()}", jti

def _read_state(state: str) -> tuple[uuid.UUID, str]:
    raw, kid, digest = state.split(".")
    body = bytes.fromhex(raw)
    expected = keyed_digests(Purpose.INTEGRATION_STATE, body).get(kid)
    if expected is None or not hmac.compare_digest(expected.hex(), digest):
        raise ValueError
    payload = json.loads(body)
    if payload.get("exp", 0) < time.time():
        raise ValueError  # 过期与签错一样，都是「链接无效」
    return uuid.UUID(payload["id"]), payload["jti"]

@router.get("/me/integrations/{integration_id}/feishu/authorize")
async def feishu_authorize(...) -> dict:
    ...
    state, jti = _state(row.id)
    try:
        await reserve(_STATE_SCOPE, jti, ttl_s=_STATE_TTL_S)
    except SingleUseUnavailableError:
        raise InternalServerError("暂时无法发起飞书授权，请稍后重试") from None
    url = FeishuClient(feishu_settings(row)).authorize_url(_redirect_uri(), state)
    return ok({"url": url, "redirect_uri": _redirect_uri()})
```

callback 侧在验签后、换 token 前加一次 `if not await claim(_STATE_SCOPE, jti): return 无效链接`。同样把 `str(exc)[:200]` 换成固定文案 + 服务端日志。

- **契约**：`GET /me/integrations/{id}/feishu/authorize` 响应形状不变（`{url, redirect_uri}`），只多一次 Redis 写；旧 state（老格式）在部署后一律判无效 —— 用户重开一次授权即可，不需要数据迁移。新增对 Redis 的依赖（与 #13/#14/#47 相同，Redis 不可用时应 fail closed 而不是放行）。
- **测试**：`backend/tests/integration/test_integrations.py` 追加
  - `test_a_feishu_state_expires`：用 `exp` 手工构造一个过期 state（或 monkeypatch `time.time`），断言 callback 回跳带「授权链接无效」且不去换 token。
  - `test_a_feishu_state_is_spent_once`：同一个 state 打两次 callback，断言第二次不调用 `exchange_code`（可用 `feishu_stub` 计数）。
  - `test_a_forwarded_feishu_state_from_another_browser_is_refused`：与 `tests/integration/test_github_link_state_single_use.py` 同形（#222 那个故事的回归测试）。

### 【3 个接口同型】GET /connector/teams/{team_id}/devices（及 GET /connector/my/devices、POST /connector/my/devices/{id}/teams）

- **现状**：`connector.py:578-621`：`ProjectService(db).list_for_team(team_id)` → `service.list_devices_for_team(team_id)` → `service.list_devices_attached_to_projects(list(names))` → `device_users(...)` → 每台设备 `_device_view(d)`。
- **问题**：SQL 条数按设备数线性增长，而且是三重嵌套：
  - `list_devices_by_owner`（`sql_repository.py:144-152`）= `1 + 2N`（每台设备 `_to_device` 再发 2 条，`sql_repository.py:80-98`）。
  - `list_devices_by_team`（185-196）= `1 + 3N`（每台 `get_hosted_device` 1 条 + `_to_device` 2 条）。
  - `list_devices_attached_to_projects`（service.py:264-274）对**每个项目**再调 `list_devices_by_project`（176-183），每次 = `2 + 3×该项目设备数`（`device_ids_by_project` 自己就是 2 条，154-174）。于是 `team_devices` 一次请求 = `1 + 3N + Σ项目(2 + 3×设备数) + 1`，团队里 10 个项目、每个 3 台设备时已过百条 SQL，且同一台设备会被反复取。
  - 另有纯 CPU 的重复扫描：`_device_screens`（`connector.py:490-505`）对每台设备都遍历一次 `device_hub.all_online_screens()`（`device_hub.py:477-478`，内部对每个 screen 再调 `is_online`）。量级不大（内存字典），但把 O(N×M) 摊在每个行上没有必要。
- **优化**：批量取设备 + 会话内一次映射。

```python
# app/domain/device/sql_repository.py —— 用 3 条 SQL 替掉 3×len(ids)
    async def _hosted_devices(self, device_ids: Sequence[str]) -> list[Device]:
        ids = list(dict.fromkeys(device_ids))
        if not ids:
            return []
        rows = (
            await self._session.scalars(
                select(DeviceRow)
                .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
                .where(DeviceRow.device_id.in_(ids))
            )
        ).all()
        projects: dict[str, list[uuid.UUID]] = {}
        for did, pid in await self._session.execute(
            select(DeviceProjectRow.device_id, DeviceProjectRow.project_id).where(
                DeviceProjectRow.device_id.in_(ids)
            )
        ):
            projects.setdefault(did, []).append(pid)
        teams: dict[str, list[int]] = {}
        for did, tid in await self._session.execute(
            select(DeviceTeamRow.device_id, DeviceTeamRow.team_id).where(
                DeviceTeamRow.device_id.in_(ids)
            )
        ):
            teams.setdefault(did, []).append(tid)
        return [
            self._device(row, projects.get(row.device_id, []), teams.get(row.device_id, []))
            for row in rows
        ]

    async def list_devices_by_team(self, team_id: int) -> list[Device]:
        ids = (
            await self._session.scalars(
                select(DeviceTeamRow.device_id).where(DeviceTeamRow.team_id == team_id)
            )
        ).all()
        return await self._hosted_devices(ids)

    async def list_devices_by_owner(self, owner_user_id: int) -> list[Device]:
        ids = (
            await self._session.scalars(
                select(HostedDeviceRow.device_id).where(
                    HostedDeviceRow.owner_user_id == owner_user_id
                )
            )
        ).all()
        return await self._hosted_devices(ids)
```

`_device` 就是把现在 `_to_device`（80-109）里拼 dataclass 的那段提出来，接收已取好的 `project_ids`/`team_ids`；`_to_device` 保留给单台读取路径调用（内部改为 `self._device(row, ...)`）。`list_devices_by_project` 同样改成「先 `device_ids_by_project`（2 条）再 `_hosted_devices(ids)`」。handler 侧把 `list_devices_attached_to_projects` 的循环换成一次 `device_ids_by_project` 的并集查询（或在 service 里合并去重后一次取）。

- **契约**：响应形状不变（同一批设备、同样的 `project_ids`/`team_ids`/`screens`）。无迁移。
- **测试**：`backend/tests/integration/test_device_listing_queries.py`
  - `test_team_devices_issues_a_query_count_that_does_not_grow_with_devices`：用 `tests/integration/test_hot_path_queries.py:27-42` 的 `counting_sql()`，造 5 台设备调一次、再造 10 台调一次，断言两次的语句数之差 ≤ 3（修复前是 3/台 + 项目维度）。
  - `test_my_devices_still_reports_projects_and_teams`：行为回归（`tests/integration/test_connector_device_flow.py` 已有 device flow fixture 可复用）。

### POST /connector/auth/device/start（及 POST /connector/auth/device/poll）

- **现状**：`connector.py:168-185` 无鉴权、无限流，`service.start`（`service.py:69-83`）为每次调用写一行 `device_auth_code`；`poll`（188-194 → `service.poll`，144-158）在批准后明文返回 `token` + `device_id`。
- **问题**：
  1. **`device_auth_code` 只增不减**。全仓 `grep DeviceAuthCodeRow` 只有 `sql_repository.py:39-56` 的读写，没有任何清理任务，也没有按 `created_at` 的索引（`device/models.py:85-101` 只声明了主键）。`start` 又是匿名的，脚本刷一次就是一行永久数据；`_code_ttl` 只管读不认，行不会消失。
  2. **没有限流**。这条路径被 `app.main` 的 cheese-token gate 明确排除（`webhooks.py` 顶部注释说明同一类豁免），是一个纯匿名写库入口。
  3. **令牌类响应缺 `Cache-Control: no-store`**。`poll` 的响应体里有长效设备令牌（`service.py:153-158`），RFC 6749 §5.1 要求「任何含 token/凭据的响应必须带 `Cache-Control: no-store`」，HTTP 缓存/中间代理不该有机会留下它。
  4. 响应缺 RFC 8628 的 `expires_in`（`connector.py:185` 只回 `device_code`/`approve_url`/`interval`）。客户端只能硬编码假设 10 分钟；这是**加字段**，不破坏冻结的 cli 契约。
  5. `code_device_name`（#3，`service.py:173-177`）不校验 TTL，与 `_live_code`（85-91）语义不一致：过期 code 仍能读到设备名。
- **优化**：

```python
# connector.py —— ① 给匿名端点一个按客户端地址的预算（复用登录那条路径已有的原语）
from app.core.client_address import resolved_client_address
from app.core.redis import get_redis_client
from app.domain.user.login_security import ClientFailureBudget

@router.post("/auth/device/start", response_class=JSONResponse)
async def device_start(
    body: DeviceStartRequest, request: Request, service: DeviceServiceDep, db: DbSession
) -> JSONResponse:
    redis = get_redis_client()
    budget = ClientFailureBudget(redis, "device-flow") if redis is not None else None
    client = resolved_client_address(request)
    if budget is not None and await budget.spend(client):
        raise SystemBusyError("请求过于频繁，请稍后再试")
    code = await service.start(body.device_name)
    await db.commit()
    ...
    payload = {
        "device_code": code,
        "approve_url": approve_url,
        "interval": 2,
        "expires_in": int(service._code_ttl.total_seconds()),  # 加字段，不改已有字段
    }
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})
```

`poll` 同样包 `Cache-Control: no-store`（`service.poll` 的返回值里含 token），并给 `device_auth_code` 加一条定期清理（可以在导出状态维护的既有 job 里挂一条 `DELETE FROM device_auth_code WHERE created_at < now() - interval '1 day'`，或在 `save_code` 时顺带删掉本进程里早已过期的少量行）。`code_device_name` 改成走 `_live_code`：

```python
    async def code_device_name(self, code_value: str) -> str | None:
        try:
            return (await self._live_code(code_value)).device_name
        except NotFoundError:
            return None  # 过期与不存在，对审批页是同一件事
```

（这一条会把 #3 的行为从「过期也能读到名字」改成「过期即 null」，审批页本来就把未知 code 显示为空。）

- **契约**：`start` 响应**新增** `expires_in`（冻结 cli 忽略未知字段，是安全的加字段）；`poll` 只加响应头，不动 body —— 但要注意 `poll` 现在是裸 dict，改成 `JSONResponse` 只为加头，body 必须逐字节保持原样（cli 是冻结契约，`service.poll` 的返回直接是 body）。新增 429/503 路径（限流命中）与 `retry_after` 语义，需要 cli 侧不把它当成「code 无效」。
- **测试**：`backend/tests/unit/test_device_service.py` + `backend/tests/integration/test_connector_device_flow.py`
  - `test_start_is_rate_limited_per_client_address`：同一地址连打 N 次，断言第 N+1 次被拒（Redis 用 `fakeredis`/已有 fixture）。
  - `test_poll_response_is_not_cacheable`：断言 `Cache-Control: no-store`。
  - `test_start_reports_expires_in`：断言字段存在且等于 `_code_ttl`。
  - `test_an_expired_code_has_no_proposed_name`：`_now` 注入过期时间后断言 `null`。
  - `test_expired_codes_are_swept`：直接断言清理函数删掉旧行。

### 【3 个接口同型】GET /connector/claude/{version}/{platform}/{name}、/connector/pi/...、/connector/toolchain/...

- **现状**：`installer.py:208-224`、`232-246`、`259-277`。三者都在缓存未命中时上调用的上游（`claude_dist.ensure_cached`，`claude_dist.py:110-154`，`_FETCH_TIMEOUT_S = 180`），`file_lock` 与「校验和验证 + 原子 rename」做得很扎实（`claude_dist.py:116-147`）。
- **问题**：这三个路由**无鉴权、无限流**，而一次请求可能触发数十 MB 的上游拉取并落盘（`_dist_dir()` 下）。`VERSION_RE`（`claude_dist.py:50`）允许 `^\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$` 的任意版本，攻击者只要猜中一个真实存在的版本就能让后端替他去上游取一份并缓存；猜一批版本就是一次磁盘填充 + 上游带宽消耗。另外这几个路由的**校验和口径不一致**：只有 toolchain（`installer.py:276`）回 `X-Checksum-SHA256`，claude/pi 不回，而 claude/pi 的内部验证同样拿到了 sha256 —— 调用方（机器上的启动器）本来可以用它做二次校验。
- **优化**：① 给这三条加一个按客户端地址的粗预算（同 #1 的 `ClientFailureBudget`，`step="connector-dist"`），或限制成「只服务本部署 `connector_build.TARGETS` 覆盖的版本/平台集合」；② 补齐校验和响应头：

```python
@router.get("/claude/{version}/{platform}/{name}")
async def download_claude(version: str, platform: str, name: str) -> Response:
    ...
    binary = await claude_dist.ensure_cached(_dist_dir(), version, platform)
    # 改动点：与 toolchain 路由同口径，把已验证的 sha256 交给调用方。
    digest = await asyncio.to_thread(
        lambda: hashlib.sha256(binary.read_bytes()).hexdigest()
    )
    return FileResponse(
        binary,
        media_type="application/octet-stream",
        filename=name,
        headers={"X-Checksum-SHA256": digest},
    )
```

（更好的是让 `ensure_cached` 返回 `(path, sha256)`，避免再读一遍文件；`_checksum()` 已经取到了 `want`，直接把它带回即可。）
- **契约**：响应**新增**响应头（向后兼容）；限流会新增 429/503。无迁移。
- **测试**：`backend/tests/unit/test_connector_downloads.py` 追加
  - `test_claude_download_carries_the_verified_checksum`：断言头存在且等于 manifest 里的值（stub 上游）。
  - `test_dist_routes_are_rate_limited`：断言超预算后不再触发 `ensure_cached`。

### GET /topics/{topic_id}/mcp/servers

- **现状**：`remote_mcp.py:175-187` → `service.room_view(db, project_id)`（`service.py:206-222`）→ `settings_view(db, project_id)`（165-203），其中 `declared.read(db, project_id, fresh=True)`。
- **问题**：`fresh=True` 会**绕过 60 秒缓存**（`declared.py:31-32`、`:134-141`），每次房间读都去 forge 取一次 `.mcp.json`（`ProjectFiles(...).text(".mcp.json", "committed")`，一次跨服务往返），随后 `_secret_values` 把该项目所有密钥全解密一遍。房间页上的服务器列表是「有哪些服务器、连着没有」，和 `.mcp.json` 是否在一分钟前刚改过无关；而设置页（`GET /projects/{id}/mcp/servers`）才是需要 fresh 的地方。
- **优化**：

```python
async def room_view(db: AsyncSession, project_id: uuid.UUID) -> list[dict]:
    """What a room's members see: each server, its state and who authorized it.

    不强制 fresh：房间视图与 `.mcp.json` 是否在一分钟内改过无关，
    forge 往返留给设置页（settings_view 的默认行为）。
    """
    found = await declared.read(db, project_id)          # 走 60s 缓存
    connections = await _connections(db, project_id)
    values = _secret_values(project_id, await _secret_rows(db, project_id))
    return [
        {
            "name": s.name,
            "host": _host(s.url),
            "auth": "oauth" if s.uses_oauth else "headers",
            "status": _status(s, connections.get(s.name), values),
            "authorized_by": connections[s.name].authorized_by if s.name in connections else None,
            "authorized_at": _when(connections[s.name].authorized_at) if s.name in connections else None,
        }
        for s in found.servers
    ]
```

（即把 `room_view` 从复用 `settings_view` 改成自己组装，顺带省掉 `settings_view` 里为设置页准备的 `all_variables()` 展开。）
- **契约**：响应形状不变（`{servers:[{name,host,auth,status,authorized_by,authorized_at}]}`）。`.mcp.json` 改动后房间视图最多晚 60 秒可见，与 `declared` 缓存的设计意图一致（该缓存本来就是给 session 启动/房间读用的）。若连线时刚改过 `.mcp.json`，`begin_connect`（`service.py:248`）仍用 `fresh=True`，不影响连接正确性。
- **测试**：`backend/tests/integration/test_remote_mcp.py` 追加
  - `test_the_room_view_does_not_read_the_forge_every_time`：连打两次 `GET /topics/{id}/mcp/servers`，用计数 stub 断言 `.mcp.json` 只读了一次。
  - `test_the_settings_page_still_reads_fresh`：设置页在文件改动后立刻看到新值。

### POST /projects/{project_id}/github/connect

- **现状**：`github_install.py:160-179`：`for installation in await list_user_installations(token): for repo in await fetch_user_installation_repos(token, installation["id"])`，两个函数都走 `_user_installation_items`（`github_app.py:299-322`），**每次调用新建一个 `httpx.AsyncClient`**（`github_app.py:302`）。
- **问题**：串行 + 每个 installation 一次新连接池/TLS 握手。用户的 installation 有 k 个，就要 `2k` 次串行往返、`2k` 次 TLS 建连（分页本身是对的：`per_page=100` 翻页到不足 100 为止，这点没问题）。这段在请求路径上，manager 点「连接仓库」时要等完。好在只在实际要用 GitHub 时才发生。
- **优化**：一个 client 走完全程，并在命中后立刻返回（现在也是立刻 return，只是串行不可避免）：

```python
# app/domain/agent/github_app.py
async def _user_installation_items(
    token: str, path: str, key: str, *, client: httpx.AsyncClient | None = None
) -> list[dict]:
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    try:
        ...
    finally:
        if owns:
            await client.aclose()
```

调用侧在一次 `connect` 里复用一个 client：`async with httpx.AsyncClient(timeout=20.0) as client:` 后把 client 透传进 `list_user_installations` / `fetch_user_installation_repos`。（`httpx.AsyncClient` 自带连接池，keep-alive 会把 k 次 TLS 握手降为 1 次。）
- **契约**：不变，纯内部。
- **测试**：`backend/tests/unit/test_github_app_tokens.py` 追加
  - `test_walking_user_installations_reuses_one_client`：注入 `transport=` 计数建连次数，断言 k 个 installation 只建立了 1 个 client（现有测试已有 `_transport` 注入的写法，见 `github_app.py:174/207`）。

### GET /me/mail-drafts

- **现状**：`integrations.py:320-328` → `drafts_of`（`service.py:486-497`），`.order_by(MailDraft.created_at.desc()).limit(100)`。
- **问题**：`limit(100)` 是硬编码的静默截断，响应里只有 `total = len(items)`（即 ≤100），调用方无法区分「正好 100 条」和「还有更多」。GitHub 的列表接口用 `Link: <...>; rel="next"` 明确给出下一页（https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api ），本项目的列表信封（`app/api/response.py:21` `page(items, total)`）没有这个位置。
- **优化**：最小改动是让 `total` 说实话并接受 `limit`/`offset`，形状不动：

```python
@router.get("/me/mail-drafts")
async def my_drafts(
    db: DbSession,
    resolver: ActorResolverDep,
    status: str | None = "drafted",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    actor = await _person(resolver)
    assert actor.user_id is not None
    service = IntegrationService(db)
    rows, total = await service.drafts_of(
        actor.user_id, status or None, limit=min(limit, 100), offset=offset
    )
    return ok(page([draft_view(r) for r in rows], total))
```

```python
# service.py
    async def drafts_of(
        self, owner_user_id: int, status: str | None, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[MailDraft], int]:
        base = (
            select(MailDraft)
            .join(Integration, Integration.id == MailDraft.integration_id)
            .where(Integration.owner_user_id == owner_user_id)
        )
        if status:
            base = base.where(MailDraft.status == status)
        total = await self._session.scalar(
            select(func.count()).select_from(base.subquery())
        )
        rows = list(
            await self._session.scalars(
                base.order_by(MailDraft.created_at.desc()).limit(limit).offset(offset)
            )
        )
        return rows, int(total or 0)
```

（`base.subquery()` 上再 `count()` 需要去掉 `ORDER BY`，实际写法用 `select(func.count()).select_from(base.order_by(None).subquery())`。）
- **契约**：`data.total` 的含义从「本页条数」变成「总条数」—— 这是**可见变化**，前端如果拿 `total` 当页长做分页控件会受影响；`limit`/`offset` 是新增可选参数，默认值保持「50 条」而非原来的 100，若要保持兼容用 `limit: int = 100`。只在确知前端用法后再合并这条。
- **测试**：`backend/tests/integration/test_integrations.py` 追加 `test_drafts_are_paged_and_the_total_is_the_real_total`（造 3 条，`limit=2` 断言第一页 2 条、`total=3`）。

### GET /integrations/{integration_id}/mail/messages/{uid}/attachments/{index}

- **现状**：`integrations.py:420-441` 回 `Response(data, media_type=content_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})`，`content_type` 直接来自邮件部件（`mail.py:395` `part.get_content_type()`）。
- **问题**：把**第三方（发件人）完全可控**的 MIME 类型原样透出到本站源上，且不带 `X-Content-Type-Options: nosniff`。`Content-Disposition: attachment` 在主流浏览器会下载而不渲染，但这是浏览器行为而不是保证；`text/html` + 站点源上的一层渲染就是不落盘的存储型 XSS 面。
- **优化**：

```python
    return Response(
        data,
        media_type="application/octet-stream",  # 改动点：站内一律不可渲染
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )
```

（把原始类型放进 `Content-Disposition` 之外的 `X-Original-Content-Type` 供前端提示图标。）
- **契约**：`Content-Type` 从真实类型变成 `application/octet-stream`。若前端靠它挑图标，需要一起改成读新头 —— 这是唯一可见变化。
- **测试**：`backend/tests/integration/test_integrations.py` 追加 `test_an_attachment_is_never_served_as_renderable_html`：stub 一个 `text/html` 附件，断言响应头是 `application/octet-stream` + `nosniff`。

### 其余（低收益，仅记结论）

- **POST /integrations/{id}/mail/drafts（#36）**：一次请求里写草稿 + 落 `Block` + 发通知（`integrations.py:452-511`）——三件事在同一事务里，原子性是对的，不做改动；但 handler 拉到 60 行，建议把「落 Block + 发通知」抽成 `_announce_draft(db, row, project, room, draft)`，与其余 `/integrations/...` 路由保持同一行数。
- **POST /me/integrations/mail（#22）/ feishu（#23）**：`_check`（`service.py:239-256`）里 IMAP 与 SMTP 两次探活是串行 `asyncio.to_thread`，可 `asyncio.gather` 并行（省掉一次握手时间）。注意别把两次 `_mail` 风格的会话写入并行化——同一个 `AsyncSession` 不能并发 flush。
- **POST /connector/my/devices/{id}/teams（#8）**：`get_hosted_device` 查两遍（`connector.py:568` 与 `574`），第二遍只为拿视图；末尾的 `type: ignore[arg-type]` 掩盖了「设备在两步之间被删掉 → `_device_view(None)` → 500」。改成 `assign_to_team` 后直接用第一次的 `device` 重新取一次绑定即可，或 `if device is None: raise NotFoundError`。
- **`DELETE /projects/{id}/mcp/secrets/{name}`（#45）** 对不存在的变量回 200，而同组的 `DELETE .../connection`（#43）回 404（`service.py:402-403`）。同一模块内两种删除语义，建议统一（幂等删除回 200 更适合密钥这种「本来就没有」的状态）。

## 三、模块级建议

1. **「上游 I/O 前先还连接」应当成为本组的一条硬规则，并抽成一个共用依赖。**
   适用：上面第一节里所有打第三方网络（IMAP/SMTP/飞书/GitHub/forge）的接口。做法：在本组路由里统一用 `app/core/db.py:182` 现成的 `release_read_session`（或就地 `commit()`，因为 `expire_on_commit=False`），并在 `IntegrationService._mail` / `feishu` 的调用点前完成。为什么：池只有 35 条连接、等待上限 30s（`app/core/db.py:103-105`），一个 30s 的 IMAP 调用就吃掉 1/35；仓库里 09-18/09-19 两次事故（以及 `connector.py:80-88` 的 `_RECOVERY_AT_ONCE`、`app/core/db.py:130-150` 的池饱和告警）都是同一个形状。已有先例：`llm_proxy.py:325`、`forge_files.py:73`。
   落地方式建议：在 `app/api/routes/integrations.py` 里把 `_usable` 改成返回「已快照好的纯数据 + 已释放的连接」，让每个 handler 无从忘记。

2. **列表信封里补上「还有没有下一页」。**
   适用：#5 #9 #21 #29 #32 #41 #49。现状：全部 `ok(page(items, len(items)))`（`app/api/response.py:21`），`total` 常常就是本页条数，没有 cursor 也没有 `Link` 头。做法（不破坏信封）：`page(items, total, has_more)` 或让 `total` 一律是真实总数 + 接受 `limit`/`offset`。优先级：#29（硬截断 100）、#32（全表）最高。#9/#5 属于「一次回全量」，在设备数量上不封顶，建议顺手加 `limit`。参考：GitHub 用 `Link: rel="next"`（https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api ）。

3. **拿 token 的响应统一 `Cache-Control: no-store`。**
   适用：#2（`poll` 回长效设备令牌）、#14 #28 #47（换完 token 的跳转路径）。RFC 6749 §5.1 明确要求含凭据的响应带 `no-store`；本项目目前一处都没有。做法：在返回这些响应的地方显式加头（`JSONResponse(..., headers={"Cache-Control": "no-store"})` 或 `RedirectResponse(..., headers=...)`）。#17/#18/#19 的二进制下载反而应该显式给长缓存（`Cache-Control: public, max-age=31536000, immutable`，内容按版本/摘要寻址，永不变化），两个方向都别交给默认值。

4. **匿名入口需要按客户端地址的预算，仓库里已有现成原语。**
   适用：#1 #2 #3 #17 #18 #19（以及 #28 的 callback）。做法：`ClientFailureBudget(get_redis_client(), "<step>").spend(resolved_client_address(request))`（`app/domain/user/login_security.py:178-216` + `app/core/client_address.py:20-35`），失败时回 503/429 并带 `reason`。为什么：这些路径都在 `app.main` 的 cheese-token gate 之外，是纯匿名入口，其中 #1 还会写库（`device_auth_code` 当前**没有任何清理**）。注意 `resolved_client_address` 在代理配置缺失时返回 `None`，此时计数会被跳过——这是该原语刻意的语义（不能把所有客户端算成一个），要接受。

5. **Webhook 的「失败」必须能从状态码上读出来。**
   适用：#50。现状里 `code: 200, data.accepted: false` 是唯一线索，且请求被 0/5/30s 的重试拖长到 GitHub 的 10 秒窗口之外（https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks ），失败重投又与「没有投递去重」叠加成重复落块。做法见第二节；`X-GitHub-Delivery` 去重 + 202/503 是这一组里唯一需要跨端对齐（CI 脚本）的改动，建议单独一条任务做。

6. **错误体口径：二进制/脚本类路由可以不是信封，但别混着来。**
   适用：#15–#20 全部 `PlainTextResponse("bad version", 400)`。#15/#16/#17–#20 是给 curl/机器读的，纯文本可接受；#46 是规范要求的裸 JSON。除此之外本组所有路由都走 `{"code","message","data"}`，**保持不要动**（`app/api/response.py:1-18`）。唯一建议：#17–#20 的 4xx 文案带上 `documentation_url` 式的自解释（GitHub 错误体风格：`message` + `documentation_url`），但这不是信封的一部分，属于可选。

7. **OAuth/回调类 state 的三件套（`exp` + `jti` + 一次性 claim）应当在三个流程里写法一致。**
   #13/#14（`github_install_state.py` + `single_use_state`）与 #47（`_seal` + `claim`）已经对齐；#27/#28 是唯一没跟上的（无 `exp`、无 claim），#42 的 MCP begin_connect 做对了。建议把「mint state → reserve(jti)」收成一个共用小工具（形如 `github_install_state.mint_account_link_state` 的返回 `(state, jti)` 约定），新加入的回调流程照抄即可，不会再漏。#4/#14 的 callback 还有一处顺序问题值得一并统一：state 在**做任何工作之前**就被 claim 掉（`github_install.py:215`），因此一次上游抖动会让用户必须重新发起整个流程；把 claim 挪到「校验都过了、只差写库」时更友好（代价是重放窗口稍微变长，需与防重放权衡）。
