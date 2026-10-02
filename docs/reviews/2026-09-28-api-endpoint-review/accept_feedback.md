# accept_feedback 组 — 逐接口优化调研

**路径核对**：清单 34 条的 `path` 与源码装饰器逐条比对**全部一致**，无差异（`accept.py` 15 条、`feedback.py` 15 条、`feedback_proposals.py` 4 条；前缀分别是 `""`、`/feedback`、`/topics`）。清单里的 `line` 是 `def` 行，与源码一致。

**这一组最重的一条**：`GET /topics/{topic_id}/accept-card` 与 `GET /topics/{topic_id}/pr-checks` 在不带 `?task=` 时**一次鉴权都不做**（`resolver` 只在那一个分支里被用到）。web 前端正是不带 `task` 的那个调用方（`frontend/src/api.ts:2128`）。详见第二节第 1、2 条。

---

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | POST | `/topics/{topic_id}/tasks/{task_id}/accept-card` | 可优化 | 超时重试会撞上「已有待处理的验收卡」回 422，而不是把第一次那张卡还回来 |
| 2 | POST | `/topics/{topic_id}/tasks/{task_id}/push-fix` | 暂无 | 观察式同步，幂等；GitHub 不通时转成 200 + reason 而不是 500，做法是对的 |
| 3 | POST | `/topics/{topic_id}/tasks/{task_id}/ready` | 可优化 | 路由刚 `require_in_room`，服务层又查同一条任务/话题一次 |
| 4 | POST | `/topics/{topic_id}/tasks/{task_id}/accept-card/describe` | 可优化 | 同上重复解析；末尾那次 `describe` 又是一整套按卡的查询 |
| 5 | GET | `/accept-cards/{card_id}/deliverable` | 可优化 | 不可变快照没有 ETag/条件请求；整份字节读进内存、无流式、无上限 |
| 6 | GET | `/topics/{topic_id}/accept-card` | 可优化 | 不带 `?task=` 时**无任何鉴权**；`describe` 按卡逐张查（N+1）；无分页、`task` 过滤在 Python |
| 7 | GET | `/topics/{topic_id}/pr-checks` | 可优化 | 同样缺鉴权；每次轮询 2 次 GitHub 调用、每次新建连接；为找一张卡拉出整个话题的卡 |
| 8 | POST | `/accept-cards/{card_id}/approve` | 可优化 | `body.approver_handle` 必填却被完全忽略；卡与话题各被查两遍 |
| 9 | POST | `/accept-cards/{card_id}/accept` | 可优化 | `body.decided_by` 必填却被忽略（只用了 `head_sha`） |
| 10 | POST | `/accept-cards/{card_id}/reassign` | 可优化 | 复用 `AcceptCardCreate`，其中 8 个字段被静默忽略 |
| 11 | POST | `/accept-cards/{card_id}/reject` | 可优化 | 同一次请求里同一个话题被解析 3 次、卡被取 2 次 |
| 12 | POST | `/accept-cards/{card_id}/void` | 可优化 | 同 #11 的重复查询一类 |
| 13 | POST | `/accept-cards/{card_id}/merge-anyway` | 可优化 | 同 #11 的重复查询一类 |
| 14 | POST | `/accept-cards/{card_id}/auto-merge` | 可优化 | 同 #11 的重复查询一类 |
| 15 | POST | `/accept-cards/{card_id}/revoke` | 可优化 | 同 #11；请求体整个没被读过 |
| 16 | GET | `/feedback/meta` | 暂无 | 静态词表 + 一次管理员判定，无问题 |
| 17 | GET | `/feedback/counts` | 可优化 | 铃铛轮询一次发 5 条独立聚合查询（其中 hot 带 union + outer join） |
| 18 | POST | `/feedback/read` | 暂无 | 一跳 upsert，幂等，写的就是游标 |
| 19 | GET | `/feedback/mine` | 可优化 | 每翻一页重算一整套 counts；「指派给我的」那一支逐行 `may_see`（1–2 条 SQL/行） |
| 20 | GET | `/feedback` | 可优化 | 每翻一页重算 8 条聚合；`sort` 不认识的取值被静默当成 `new`（admin 端同名参数会 400） |
| 21 | POST | `/feedback` | 暂无 | 24h 上限 + advisory lock + agent 拒之门外，写侧规则完整 |
| 22 | GET | `/feedback/{feedback_id}` | 暂无 | 可见性只有一处判据；详情里的计数全部按页批量取 |
| 23 | DELETE | `/feedback/{feedback_id}` | 暂无 | 软删连带评论；404（看不见）与 403（不是你的）分得清 |
| 24 | GET | `/feedback/{feedback_id}/comments` | 暂无 | 游标分页 + 每页一批；跨帖评论取不到 |
| 25 | POST | `/feedback/{feedback_id}/comments` | 可优化 | 本域唯一没有配额/限流的写入口（反馈与提案都有） |
| 26 | DELETE | `/feedback/{feedback_id}/comments/{comment_id}` | 暂无 | `can_delete` 与删除用同一个判据 |
| 27 | POST | `/feedback/{feedback_id}/comments/{comment_id}/likes` | 暂无 | `ON CONFLICT DO NOTHING`，回写后计数 |
| 28 | DELETE | `/feedback/{feedback_id}/comments/{comment_id}/likes` | 暂无 | 同上，幂等 |
| 29 | POST | `/feedback/{feedback_id}/supports` | 暂无 | 已办完回 412，幂等，回写后计数 |
| 30 | DELETE | `/feedback/{feedback_id}/supports` | 暂无 | 同上，幂等 |
| 31 | GET | `/topics/{topic_id}/feedback-proposals` | 可优化 | 拉全话题**所有带 meta 的消息**再在 Python 里筛，无上限、无 LIMIT |
| 32 | POST | `/topics/{topic_id}/feedback-proposals` | 暂无 | 三道限流 + 话题级锁，顺序与理由都对 |
| 33 | POST | `/topics/{topic_id}/feedback-proposals/{block_id}/dismiss` | 暂无 | 落一行、幂等 |
| 34 | POST | `/topics/{topic_id}/feedback-proposals/{block_id}/accept` | 暂无 | 事务级 advisory lock + 卡上幂等标记，两处写在一个事务里 |

---

## 二、详细分析

### 1. GET `/topics/{topic_id}/accept-card` — 读路径完全没有鉴权

**现状**：`backend/app/api/routes/accept.py:251-264`。`svc.list_for_topic(topic_id)`（`domain/review/repositories.py:102-116`，无 LIMIT、无 offset）取回该话题名下**全部**卡，然后 `[await svc.describe(c) for c in cards]` 逐张渲染（`domain/review/services.py:951-1127`）。

**问题**：

1. **鉴权只在 `?task=` 那一支里发生，而 `task` 是可选的**：

   ```python
   cards, total = await svc.list_for_topic(topic_id)      # 先取了
   if task is not None:
       await _task_actor(topic_id, task, db, resolver)     # 才鉴权
       cards = [card for card in cards if card.task_id == task]
   ```

   `resolver` 这个依赖在整条路由里只被第 261 行用到。默认（不传 `?task=`）走的是「取全部 → 直接渲染 → 返回」。`authorize_topic` 一次都没被调用，`_task_actor` 里那句 `raise AuthenticationRequiredError()` 也就没机会执行。

   证据：`backend/tests/integration/test_accept_card_room_only.py:87` 在**未带 Authorization** 的 `TestClient` 上 `client.get(f"/topics/{room}/accept-card")` 并读到 `["data"]["total"]`（`backend/tests/conftest.py:1116` 只设了 `X-Cheese-Token`）。前端也是不带 `task` 的那个调用方：`frontend/src/api.ts:2128` `getAcceptCards(topicId, taskId?)`。

   泄漏的是 `AcceptCardOut` 的全部字段（`domain/review/schemas.py:115-154`）加上 `describe` 的富化：`change_subject`/`change_body`（PR 正文）、`gate_output`（检查命令的输出尾巴）、`note`（卡上的告警正文）、`pr_url`/`pr_repo`、`deliverable.url`（可能是不该公开的地址）、`approvals`、`routing_reason`。任何拿到（或猜到）一个话题 UUID 的调用方都能读。

   注意边界：`authorize_topic` 对**全局沙箱 token** 是放行的（`api/auth.py:466-467`），所以这条门挡的是「无凭证」和「有身份但不是成员」，不是平台的 CLI/agent。写路由（`_card_actor`/`_task_actor`）一直是这个标准，读路由漏了。

2. **`describe` 是每张卡一整套查询**。每张卡至少 4 次真实 SQL（不走 identity map 的 SELECT）：
   - `artifacts.summary`（`domain/project/artifacts.py:416`，SELECT + claims 子查询）
   - `_repo.list_approver_handles(card.id)`（`repositories.py:60-66`）
   - `_resolve_forge` → `binding_for_project`（`domain/project/forge.py:32` `session.scalar(select(...))`）**再** `tokens_for_project`（同文件 :37-47 又调一次 `binding_for_project`）

   另外 `_topic_or_404`/`_projects.get`/`TaskService.get` 是 `session.get`，同话题的重复调用命中 identity map 不产生 SQL —— 这部分不算 N+1，但它也说明「每张卡都重新走一遍同样的解析」本来就是多余的。一个 50 张卡的话题 = 200+ 次往返，且随历史单调增长。

3. **没有分页**：`list_for_topic` 无 `LIMIT`，路由也没有 `limit`/`offset` 参数，`total` 就是 `len(cards)`（`services.py:897-899`）。`?task=` 的过滤在 Python 里做（accept.py:262），而 `task_id` 上本来就有索引（`list_for_task` 用它）。

**优化**（可直接抄）：

```python
# backend/app/api/routes/accept.py —— 与 `_card_actor` 同一句话，少一步 task 校验
async def _room_actor(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> Actor:
    """房间本身的那道门，给「不点名一条活」的读路径用。

    `_task_actor` 是这句话加一条 task 校验；`?task=` 是可选的（前端就是不带
    的那个调用方），所以少了这一支，整条路由一次鉴权都没有。
    """
    topic = await AcceptService(db)._topic_or_404(topic_id)
    actor = await resolver.resolve(
        project_id=topic.project_id, topic_id=topic.id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic.id
    )
    return actor


@router.get("/topics/{topic_id}/accept-card")
async def list_accept_cards(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    if task is not None:
        await _task_actor(topic_id, task, db, resolver)
    else:
        await _room_actor(topic_id, db, resolver)
    svc = AcceptService(db)
    cards, total = await svc.list_for_topic(topic_id, task_id=task, limit=limit, offset=offset)
    return ok(page(await svc.describe_page(cards), total))
```

```python
# backend/app/domain/review/repositories.py —— 过滤与分页一起下推
    async def list_for_topic(
        self,
        topic_id: uuid.UUID,
        *,
        task_id: uuid.UUID | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[AcceptCard], int]:
        """The cards this room filed — 过滤与总数一起下推给数据库。

        `task_id` 上本来就有索引（`list_for_task` 用它），过去却在路由里对
        已经取回来的整份清单做 Python 过滤：既拉多了行，`total` 也只能是
        `len(rows)`（也就是「一页」而不是「一共有多少张」）。
        """
        where = [AcceptCard.topic_id == topic_id]
        if task_id is not None:
            where.append(AcceptCard.task_id == task_id)
        total = await self._session.scalar(
            select(func.count(AcceptCard.id)).where(*where)
        )
        stmt = (
            select(AcceptCard)
            .where(*where)
            # `id` 是必要的二级排序：`created_at` 来自应用时钟，同一毫秒建的两行
            # 比相等，OFFSET 翻页会在两次请求之间漏行或重复（同 `_list_stmt` 里
            # `display_no` 那条理由）。
            .order_by(AcceptCard.created_at.desc(), AcceptCard.id.desc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = list((await self._session.scalars(stmt)).all())
        return rows, int(total or 0)

    async def approver_handles_for(
        self, card_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """一页卡的批准人，一次查询（同 `supports_counts` 的理由）。

        `describe` 每张卡各查一次，是这条列表路径上真正的 N+1 —— 而批准数是
        每张卡面上都要画的东西。
        """
        if not card_ids:
            return {}
        stmt = (
            select(AcceptApproval.card_id, AcceptApproval.approver_handle)
            .where(AcceptApproval.card_id.in_(list(card_ids)))
            .order_by(AcceptApproval.card_id, AcceptApproval.created_at)
        )
        out: dict[uuid.UUID, list[str]] = {}
        for card_id, handle in (await self._session.execute(stmt)).all():
            out.setdefault(card_id, []).append(handle)
        return out
```

```python
# backend/app/domain/review/services.py —— `describe` 接受已解析好的事实，并新增按页渲染
_UNSET: Any = object()   # `None` 是合法值（「解析不出来」），所以哨兵不能是 None

    async def describe(
        self,
        card: AcceptCard,
        *,
        topic: Topic | None = None,
        project: Project | None = None,
        forge: Any = _UNSET,
        approvals: list[str] | None = None,
    ) -> dict:
        ...
        data["approvals"] = (
            await self._repo.list_approver_handles(card.id) if approvals is None else approvals
        )
        topic = topic if topic is not None else await self._topic_or_404(card.topic_id)
        project = project if project is not None else await self._projects.get(topic.project_id)
        if forge is _UNSET:
            try:
                forge = await self._resolve_forge(topic.project_id, card=card)
            except ValidationError:
                forge = None
        ...

    async def describe_page(self, cards: list[AcceptCard]) -> list[dict]:
        """一页卡一次渲染：话题/项目/托管方/批准人这四样按页取一次。

        `list_for_topic` 回来的一页同一个话题，`_resolve_forge` 的
        `proposal_url` 只用来比对 netloc，同一项目下逐张传同值，所以拿
        `cards[0]` 解析一次与逐张解析结论一致。
        """
        if not cards:
            return []
        topic = await self._topic_or_404(cards[0].topic_id)
        project = await self._projects.get(topic.project_id)
        try:
            forge = await self._resolve_forge(topic.project_id, card=cards[0])
        except ValidationError:
            forge = None
        approvals = await self._repo.approver_handles_for([c.id for c in cards])
        return [
            await self.describe(
                card, topic=topic, project=project, forge=forge,
                approvals=approvals.get(card.id, []),
            )
            for card in cards
        ]
```

（剩下每卡一次的是 `artifacts.summary` 与 `TaskService.get`；前者需要一个按 `ids` 批量取版本的兄弟方法（`ArtifactSummary` 的 `version` 是 `_claims()` 聚合，可以按一批 id 一次算出），后者 `session.get` 在 identity map 里，通常不产生 SQL。上面几段先把「话题/项目/托管方/批准人」这四条按卡的事实去掉。）

**契约**：`GET` 响应形状不变（仍是 `ok(page(items, total))`）；新增 `limit`/`offset` 两个可选查询参数，缺省 `limit=50`，调用方不传时行为从「全部」变成「最新 50 张」—— **这是一次可见的行为变化，需要前端确认**（`frontend/src/api.ts:2128` 的调用点要加 `limit`，或把缺省设成不限制以保持兼容）。`total` 语义从「本页张数」变成「全部张数」，前端若拿它做「共 N 条」是修正而不是破坏。加 `?task=` 的鉴权分支对既有测试无影响（测试客户端带全局沙箱 token，走 `is_global_sandbox_token` 那一支放行）。无数据迁移。

**测试**：`backend/tests/integration/test_accept_authorization.py` 加

- `test_the_card_list_needs_a_credential_too`：**显式去掉** `X-Cheese-Token`（conftest 的 `client` 默认带一个全局沙箱 token，不脱掉它照不出这道门），断言 401；再用 `session_auth_headers("mallory")`（非成员）断言 403。
- `test_the_card_list_is_the_same_shape_for_a_member`：成员拿到的 `data`/`total` 与改造前一致。
- 分页：`test_a_long_card_list_pages_without_losing_rows`（同 `created_at` 的两张卡在 `offset=1` 下不漏不重）。

### 2. GET `/topics/{topic_id}/pr-checks` — 同一道门缺失，且每次轮询打两次 GitHub

**现状**：`accept.py:267-293`。`task` 给定时才 `_task_actor`（285-286）；`_pr_checks_payload`（296-330）先 `svc.list_for_topic(topic_id)` 取全部卡挑第一张带 PR 的，再 `client.pr_view(pr_number)` + `client.check_runs(head_sha)`。

**问题**：

1. 与 #6 同一个缺口：不传 `?task=` 时没有任何鉴权，返回 `pr_number`/`pr_url`/`state`/`mergeable`/`checks[].name,url`。这个端点还**被定时轮询**（docstring 自己说的），所以是一遍遍地把别人的 PR 信息发出去。
2. 每次调用 **2 次 GitHub API 往返**，而 `pr_view`（`domain/review/github_pr.py:1570`）与 `check_runs`（同文件 :1726）**各自 `async with httpx.AsyncClient(...)`** —— 每次新建客户端，每次重新 TCP+TLS 握手到 api.github.com；`check_runs` 还要铸一次 installation token。按秒级轮询 × 打开的卡片数，这是在拿安装限额换一个几乎不变的答案。
3. 为了拿一张卡，把整个话题的卡列表 materialize 成 ORM 对象（`list_for_topic`），而问题其实是「最新那张带 PR 的卡」。

**优化**：

```python
    # 鉴权那半：#6 的 `_room_actor`，同一个修法
    if task is not None:
        await _task_actor(topic_id, task, db, resolver)
    else:
        await _room_actor(topic_id, db, resolver)
```

```python
# backend/app/domain/review/repositories.py —— 「最新一张骑 PR 的卡」一次查询
    async def latest_with_pr(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> AcceptCard | None:
        """最新一张骑着 PR 的卡 —— 端点要的是这一张，不是整个话题的历史。"""
        where = [AcceptCard.topic_id == topic_id, AcceptCard.pr_number.is_not(None)]
        if task_id is not None:
            where.append(AcceptCard.task_id == task_id)
        stmt = (
            select(AcceptCard)
            .where(*where)
            .order_by(AcceptCard.created_at.desc(), AcceptCard.id.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()
```

```python
# backend/app/domain/review/services.py —— 几秒的缓存，键里带 head_sha
#: PR 的检查态变化慢、客户端按秒轮询，所以把「同一次读」在几秒内复用。
#: 键里带 head_sha：新提交立刻换一个新键，不会读到上一版的检查结果。
_CHECKS_TTL_S = 5.0
_checks_cache: dict[tuple[int, str], tuple[float, dict]] = {}

async def _pr_checks_payload(topic_id, db, *, task_id=None) -> dict:
    svc = AcceptService(db)
    card = await svc._repo.latest_with_pr(topic_id, task_id=task_id)
    if card is None or card.pr_number is None:
        return {"available": False}
    ...
    view = await client.pr_view(card.pr_number)
    head_sha = (view.get("head") or {}).get("sha")
    if not head_sha:
        return {"available": False, "reason": "PR has no head commit"}
    key = (card.pr_number, head_sha)
    hit = _checks_cache.get(key)
    if hit is not None and hit[0] > time.monotonic():
        checks = hit[1]
    else:
        checks = await client.check_runs(head_sha)
        _checks_cache[key] = (time.monotonic() + _CHECKS_TTL_S, checks)
    return {...}
```

更好的下一步是把 `pr_view`/`check_runs` 换成**条件请求**：GitHub 对带 `If-None-Match` 的 GET 回 304，**304 不计入主限额**（GitHub 文档与社区讨论一致：<https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api>、<https://github.com/orgs/community/discussions/156480>；**注意**：ETag 的算法把 `Authorization` 也哈希进去，而这里的 App 安装令牌每小时轮换，所以缓存必须按 **installation id + URL** 键，不能按 token 键，否则每次换令牌 ETag 都失效）。`httpx` 侧还要把 `AsyncClient` 提到实例上复用（连接池），别每个方法新建一个。

**契约**：`_pr_checks_payload` 的返回形状不变（`available` / `pr_number` / `pr_url` / `state` / `mergeable` / `checks`），只是其中 `checks` 可能来自 5 秒前的缓存 —— 这是一个语义变化（「实时」→「最多 5 秒前」），要写清。加鉴权对既有测试无影响（同 #6）。无数据迁移。

**测试**：`backend/tests/integration/test_accept_pr_publish.py` 里已有 `client.get(f"/topics/{tid}/pr-checks")` 的用例，补两条：
- `test_pr_checks_needs_a_credential_too`（脱 token → 401；非成员 → 403）。
- `test_pr_checks_reuses_a_checks_read_within_its_ttl`：stub 客户端，断言同一 head_sha 在 TTL 内只打一次 `check_runs`。

### 3. 六个 `/accept-cards/{card_id}/*` 决议路由 — 同一次请求把卡与话题查两遍

**现状**：`approve`(333)、`accept`(346)、`reassign`(363)、`reject`(384)、`void`(438)、`merge-anyway`(465)、`auto-merge`(498)、`revoke`(525)。每一条的骨架都是「`_card_actor(card_id, db, resolver)`（`accept.py:54-77`：`_card_or_404` + `_topic_or_404` + `resolver.resolve` + `authorize_topic`）→ 服务层再 `_card_or_404(card_id)` / `_topic_or_404(card.topic_id)`」。

**问题**（以 `/reject` 为例，其余同形）：

| 这一步 | 位置 | 查了什么 |
|---|---|---|
| `_card_actor` | accept.py:69 | `AcceptCard`（`session.get`） |
| 同上 | accept.py:70 | `Topic`（`session.get`） |
| 同上 | accept.py:74 | `authorize_topic` → `can_access_topic` → `TopicRepository.get` **再一次** + 名册/项目成员 |
| `svc.reject` | services.py:3612 | `AcceptCard` **再取一次** |
| `svc.describe` | services.py:993 | `_topic_or_404` **第三次** |
| 路由 | accept.py:416 | `svc._topic_or_404(topic_id)` **第四次** |

（`session.get` 命中 identity map 时不出 SQL，`authorize_topic` 的 `TopicRepository.get` 与 `_topic_or_404` 走的是 `session.get`，所以真实多出的往返主要是 `authorize_topic` 里的名册/成员查询与 `describe` 的那几条 SELECT；但「同一次请求里同一个对象被解析四遍」是结构问题，不是运气问题：`self._session.get(...)` 一夜之间换成 `select(...)` 就会变成 4 次真实查询。）另外 `reject`(384-435) 里 `svc.describe(card)` 与 `svc._topic_or_404(topic_id)` 在两行之内各来一次。

**优化**：让 `_card_actor` 把已经拿到的东西交给服务层：

```python
async def _card_actor(
    card_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> tuple[Actor, AcceptCard, Topic]:
    """返回 (actor, card, topic)：调用方与 `AcceptService` 都不必再取一遍。

    卡与话题在这条路由的入口就取到了，服务层第二次 `_card_or_404` 只是为了
    拿同一个对象 —— 把结果递下去，顺带让「这道门问的是这张卡所在的房间」在
    签名上就是显式的。
    """
    service = AcceptService(db)
    card = await service._card_or_404(card_id)
    topic = await service._topic_or_404(card.topic_id)
    actor = await resolver.resolve(
        project_id=topic.project_id, topic_id=topic.id
    )
    await resolver.authorize_topic(actor, project_id=topic.project_id, topic_id=topic.id)
    return actor, card, topic
```

服务层对应地把 `card_id` 换成 `card`（`approve`/`accept`/`reassign`/`reject`/`void`/`revoke`/`merge_despite_checks`/`arm_auto_merge` 各一个签名参数），路由里 `svc.describe(card, topic=topic)`（用 #1 里那个扩展过的 `describe`）。`/reject` 尾部那次 `_topic_or_404` 直接用 `topic`。

**同一节里的 API 设计问题（更值得改，因为它骗的是调用方）**：

- `ApprovalCreate.approver_handle`（schemas.py:112）是**必填**，而 `approve_card` 从头到尾没读过 `body`（accept.py:333-343 用的是 `actor.handle`）。
- `AcceptDecision.decided_by`（schemas.py:75）是**必填**，`accept_card` 只用了 `body.head_sha`（accept.py:357-359），`revoke_card` 连 `body` 都没读（accept.py:525-534）。
- `reassign_card` 的请求体是 `AcceptCardCreate`（accept.py:366），里面有 `change_subject`/`change_body`/`artifact`/`new_artifact`/`about`/`deliver`/`deliver_url` 八个字段，`svc.reassign` 只读 `reviewer_handle` 与 `routing_reason`（accept.py:375-380）。

这不是「少了个校验」，而是**契约在撒谎**：客户端按 schema 填了两个字段，服务端一个都不看，而且没有任何东西会告诉它。修法是把这些 schema 收窄成它们真正的形状：

```python
class ApprovalCreate(BaseModel):
    """没有 `approver_handle`：投票人只能来自 session token。

    它曾经是必填字段而路由从不读它（`approve` 用的是 `actor.handle`），于是
    调用方以为自己在指定批准人，实际指定的那个名字被丢掉 —— 这类「必填却被
    忽略」的字段比缺字段更坏：它让客户端以为自己控制了一件它并不控制的事。
    """
    #: 空模型：批准这个动作除了「谁在按」不需要任何输入。


class AcceptDecision(BaseModel):
    """只剩 `head_sha`：`decided_by` 同理，只来自 token。"""
    head_sha: str | None = _SEEN_HEAD_FIELD


class ReassignIn(BaseModel):
    """改验收人只吃这两个字段 —— 不再是 `AcceptCardCreate` 的整份。"""
    reviewer_handle: str | None = Field(default=None, max_length=64)
    routing_reason: str = ""
```

**契约**：**这是一次破坏性请求契约变更**（去掉必填字段是放宽，旧客户端继续能发；但若前端依赖 `decided_by` 必填做本地校验，需要同步）。`AcceptDecision` 的收窄要同时改 `accept_card` 与 `revoke_card` 两处签名。无数据迁移。

**测试**：`backend/tests/integration/test_accept_authorization.py` 已有 `test_accept_by_non_reviewer_403_even_with_matching_body_field`（:70）、`test_approve_uses_verified_identity_not_body`（:213）、`test_revoke_ignores_spoofed_body_identity`（:160）—— 这三条正是「body 里的身份被忽略」的守卫，收窄 schema 后它们应当**保持通过**（少传字段仍然 200/403 而不是 422）；再补

- `test_a_reassign_body_with_extra_fields_does_not_change_anything`：往 `/reassign` 塞 `deliver`/`artifact` 确认它们既不生效也不报错（收窄后应报 422 —— 这才是它该有的样子）。
- `test_every_card_decision_route_loads_the_card_once`（`business_db_factory` + SQL 计数），把 #3 的重复查询钉住。

### 4. GET `/feedback/counts` + 两个列表里的 counts — 每次轮询 5 条聚合

**现状**：`feedback.py:169-194` → `FeedbackService.counts`（`services.py:352-390`）→ `repository.public_counts()`（`repositories.py:772-814`）。

**问题**：`public_counts` 是**五条独立 SELECT**（`all`/`active`/`resolved`/`deployed`/`hot`），条条都是对 `feedback` 表的全表聚合并带 `OR` 谓词；其中 `hot` 走 `_hot_ids`（`repositories.py:634-670`），是「outer join + GROUP BY + UNION 两段」的子查询，还要再包一层 `id IN (...)`。这个端点的角色是**铃铛在没打开反馈中心时轮询**（docstring 自己说的），所以这五条是按轮询频率重复的。

同一个 `counts()` 还被嵌进 `GET /feedback`（feedback.py:274）与 `GET /feedback/mine`（feedback.py:233）的**每一页**响应里，即翻页也要重算这 5 条（登录用户再加 `get_read_state` 1 条 + `count_activity_since` 2 条）。

**优化**：五条同一批行的计数合成一条 `FILTER` 聚合（表达式与现有 `_count([...])` 逐字相同，只是从五条 SELECT 变成同一条 SELECT 里的五个 FILTER）：

```python
# backend/app/domain/feedback/repositories.py
    async def public_counts(self) -> dict[str, int]:
        """四个栏位 + 上线，**一条 SQL**。

        这五个数数的是同一批行（`PUBLIC_ONLY` + `deleted_at IS NULL`），只有每组
        WHERE 不同 —— 一个 `count(*) FILTER (WHERE ...)` 就够。它们各自一条
        SELECT 时是一条请求五次往返，而这条请求是铃铛按秒轮询的。
        谓词仍从 `_tab_where` / `_hot_ids` 来，拼接方式与 `list_public` 一致，
        所以「数字和列表不许对不上」这条约束没有被绕开。
        """
        base = list(PUBLIC_ONLY)
        hot_base = [*base, *self._tab_where("hot")]
        stmt = (
            select(
                func.count().filter(*self._tab_where("all")),
                func.count().filter(*self._tab_where("active")),
                func.count().filter(*self._tab_where("resolved")),
                func.count().filter(Feedback.status == FeedbackStatus.deployed),
                func.count().filter(
                    *hot_base, Feedback.id.in_(self._hot_ids(hot_base))
                ),
            )
            .where(Feedback.deleted_at.is_(None), *base)
        )
        all_count, active_count, resolved_count, deployed_count, hot_count = (
            await self._session.execute(stmt)
        ).one()
        return {
            "all": int(all_count),
            "hot": int(hot_count),
            "active": int(active_count),
            "resolved": int(resolved_count),
            "deployed": int(deployed_count),
        }
```

再往前一步（可与上一条分开做）：列表响应里的 `counts` 可以改成只在第一页给（或加 `?counts=0` 让调用方声明「这次翻页不需要标签数字」），因为标签数字不随 `offset` 变。这是**契约层面**的小改动，需要前端配合。

**契约**：数值与键名都不变，纯实现替换。无迁移。风险：`FILTER` 里 `_hot_ids` 子查询与当前 `_count([*hot_base, Feedback.id.in_(self._hot_ids(hot_base))])` 是同一个表达式，语义不变；上线前应对着同一份数据跑一次新旧对拍。

**测试**：`backend/tests/integration/test_feedback.py` 里已有 `test_meta_reports_the_vocabulary_and_my_admin_flag`、`test_the_unread_cursor_counts_activity_and_clears_when_read` 等；补一条对拍：
- `test_the_tab_numbers_are_one_query_and_the_same_numbers`（`business_db_factory` 捕 SQL：断言 1 条 SELECT；并把结果与逐条 `_count` 的旧口径比对）。

### 5. GET `/topics/{topic_id}/feedback-proposals` — 拉全话题的消息再筛

**现状**：`feedback_proposals.py:87-100` → `ProposalService.live_cards`（`domain/feedback/proposals.py:189-234`）。

**问题**：

```python
        stmt = (
            select(Block)
            .where(Block.topic_id == topic_id, Block.meta.is_not(None))
            .order_by(Block.created_at.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
```

没有 `LIMIT`（路由也没有分页参数），谓词是「这个话题里所有 `meta IS NOT NULL` 的 block」，然后**在 Python 里**逐行 `proposal_from_meta` 挑出 `meta.feedback_proposal` 的。也就是说：一个聊过一万句的房间，每次打开卡片栏都把一万条消息 materialize 成 ORM 对象。挑出来的通常是个位数。同文件里的 `_proposals_since`（:123-147）是被「一天」这个窗框住的，这条没有。

**优化**：这张表**已经有同形状的先例** —— `domain/block/models.py:203-208` 的 `ix_blocks_cloud_provisioning` 就是 `(topic_id, created_at)` 上加 `postgresql_where=text("(meta ->> 'event_type') = 'cloud_provisioning'")`。照抄一条：

```python
# backend/app/domain/block/models.py —— 放进 Block.__table_args__
        # 提案卡：`live_cards` 每次都要从这个房间说过的每一句话里挑出带
        # `meta.feedback_proposal` 的那几条，而挑出来的通常是个位数。
        # 局部索引让这次扫描只看提案卡本身（`ix_blocks_cloud_provisioning`
        # 是同一张表、同一个 `meta ->>` 形状的先例）。
        Index(
            "ix_blocks_feedback_proposal",
            "topic_id",
            "created_at",
            postgresql_where=text("(meta ->> 'feedback_proposal') IS NOT NULL"),
        ),
```

```python
# backend/app/domain/feedback/proposals.py
    async def live_cards(self, topic_id: uuid.UUID, *, limit: int = 200) -> list[dict]:
        ...
        dismissed = await self._dismissed(topic_id)
        stmt = (
            select(Block)
            .where(
                Block.topic_id == topic_id,
                # 与局部索引的谓词逐字相同，才会走 `ix_blocks_feedback_proposal`。
                # 它只是**预筛**：`proposal_from_meta` 仍是「这是不是一张卡」的
                # 唯一判据（手改过的 meta 不该在这里被当成卡）。
                Block.meta["feedback_proposal"].is_not(None),
            )
            .order_by(Block.created_at.desc())
            # 一栏「还活着的提案」不会需要两百张；真到了那一天，这一条要和别的
            # 列表一样上客户端游标，而不是把上限往上调。
            .limit(limit)
        )
```

**契约**：响应形状不变（`ok(cards)` 的数组）。`limit` 只在「一个话题攒了两百多张未处理的提案卡」时才会被触到，正常情况行为不变。**需要一次 migration**（新增索引，`alembic revision` + `postgresql_where`；`CREATE INDEX` 在 `blocks` 上对写入影响很小，参考同文件那条的注释）。

**测试**：`backend/tests/integration/test_feedback.py` 里提案那几条（`test_dismissing_a_proposal_is_remembered`:1595 等）已覆盖行为；补
- `test_live_cards_does_not_read_the_topics_other_messages`（`business_db_factory` 计数：返回的 Block 行数 = 提案数，不是话题的消息数）。
- migration 测试放 `backend/tests/integration/` 下与该索引同名的用例（参照 `test_accept_card_orphans.py` 那一类 migration 守卫的写法）。

### 6. GET `/accept-cards/{card_id}/deliverable` — 不可变快照没有条件请求

**现状**：`accept.py:214-248`。按项目成员判权（:229-230，理由写在注释里，是刻意的），然后 `asyncio.to_thread(library.read_artifact_snapshot, ...)`（:234-239）把整份字节读进内存，`Response(content=data, ...)` 一次性发出。

**问题**：

1. **没有任何缓存/条件请求**。这一份按定义是**不可变的**（`library/service.py:222-238`：快照在递卡那一刻落下、按 card 分目录、之后没有任何入口改它；`download_card_deliverable` 的 docstring 也这么说）。同一份字节第二次请求要重读磁盘、重传一遍。而交付物可能是几十 MB 的幻灯片。
2. 整份读进内存（`read_artifact_snapshot` → `target.read_bytes()`，`library/service.py:249-257`），再整份进响应体，**没有大小上限、没有流式**：并发下就是「同时若干个交付物大小的内存」。

**优化**：平台里已经有这套共用件（`app/api/conditional.py`，头像与管理员名单在用）：

```python
# backend/app/api/routes/accept.py
from app.api.conditional import if_none_match_hits

@router.get("/accept-cards/{card_id}/deliverable")
async def download_card_deliverable(
    card_id: uuid.UUID,
    request: Request,
    db: DbSession,
    resolver: ActorResolverDep,
) -> Response:
    ...
    data = await asyncio.to_thread(
        library.read_artifact_snapshot, topic.project_id, card.id, card.deliverable_name
    )
    etag = hashlib.sha256(data).hexdigest()
    filename = quote(card.deliverable_name, safe="")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        "X-Content-Type-Options": "nosniff",
        # 这一版**不可变**：快照在递卡那一刻落下、按卡分目录，之后没有任何入口
        # 改它（`artifact_snapshot_path`）。所以它可以带强 ETag 被浏览器反复取，
        # 重取一次只是一个 304 —— 而这份字节可能很大。
        "ETag": f'"{etag}"',
        "Cache-Control": "private, max-age=0, must-revalidate",
    }
    if if_none_match_hits(request.headers.get("if-none-match", ""), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type="application/octet-stream", headers=headers)
```

再把「读整份」换掉是第二步（要动 `library` 的接口）：新增 `artifact_snapshot_file(...) -> Path | None`（只做 `is_file()` 与路径校验），路由用 `FileResponse`（Starlette 会流式发并带 `Content-Length`/`Last-Modified`），`read_artifact_snapshot` 留给别处。

**契约**：请求不变；响应在客户端带 `If-None-Match` 时可能变成 304（标准行为，`request` 客户端本来就要处理）。无迁移。风险：`ETag` 由内容算出，需要先读完文件 —— 若要省掉这次读，得用 `mtime+size` 的弱 ETag（`FileResponse` 自己会带 `Last-Modified`，配 `If-Modified-Since` 即可）。

**测试**：`backend/tests/integration/test_artifact_deliverable.py`（已有交付物用例）补
- `test_the_same_snapshot_answers_304_to_its_own_etag`：先取一次拿 `ETag`，再带 `If-None-Match` 取，断言 304 且 body 为空。
- `test_a_snapshot_route_still_refuses_a_non_member`（守住上面那次鉴权收窄）。

### 7. POST `/topics/{topic_id}/tasks/{task_id}/accept-card` — 重试拿到的是 422，不是那张卡

**现状**：`accept.py:112-157` → `AcceptService.create_card`（`services.py:580-710`）。:624-629 先查这条任务上已有的卡，命中 `_CARD_BLOCKS_NEW_CARD`（`pending`/`pending_gate`/`conflict`，`services.py:259-265`）就 `raise ValidationError`。

**问题**：这是一个可安全重试的创建（自然键就是「这条任务上那张活着的卡」），但重试的语义是错的：客户端在**读超时**后重发（第一次其实成功了），拿回的是 422「已有待处理的验收卡，请改验收人而不是再递一张」—— 与「有人抢在你前面递了另一张」是同一句话，调用方分不出该不该改验收人还是该去读那张卡。这个平台没有幂等键机制，而这恰好是它最有理由有的一个创建口（`Cheese-Task` 那种由 agent 反复触发的路径）。

**优化**：

```python
        existing = await self._repo.list_for_task(task.id)
        blocking = next((c for c in existing if c.status in _CARD_BLOCKS_NEW_CARD), None)
        if blocking is not None:
            # 同一个请求重发（客户端超时 / 芝士重跑一轮）不是「有人抢在前面」：
            # 标题与验收人都一样时，把第一次那张卡原样还回去，而不是回一句
            # 调用方无法与「真的冲突」区分开的话。标题不同就仍然拒绝 —— 那才是
            # 「这条分支上有人递了另一件事」，改验收人是对的下一步。
            if (
                blocking.change_subject == subject
                and (reviewer_handle or "").strip() in ("", blocking.reviewer_handle)
            ):
                return blocking
            raise ValidationError(_BLOCKED_BY_CARD_MESSAGES[blocking.status])
```

（位置要在 `subject = commit_message.check_subject(...)` 之后；`reviewer_handle` 此刻还是请求体原值，所以用「要么没点名、要么点的是同一个人」这个宽松判据。**这是一个产品判断**：要不要把重发当成同一次递卡，得由需求方点头 —— 另一种做法是引入显式的 `Idempotency-Key` 请求头（Stripe 的做法），但那要给所有创建类路由统一加一层，比这一处局部规则大得多。）

顺带：#7 也顺带解决 #3 那一节的重复解析（`_task_actor` 之后 `create_card` 又 `_topic_or_404` + `require_in_room`）。末尾 `await svc.describe(card)`（:157）是一整套按卡查询 —— 单张卡的响应，这个开销是合理的，不必动。

**契约**：**响应语义变化**：重发的请求从 422 变成 200 + 第一次那张卡。旧客户端把 422 当「失败」处理的话，这次会得到一张看起来「刚建出来」的卡 —— 而它确实是同一张，`id` 相同，所以是修正。无迁移。

**测试**：`backend/tests/integration/test_accept_card_room_only.py`（已有「同一棵树一次只允许一张未决卡」的用例，:77-91）加
- `test_refiling_the_identical_card_returns_the_first_one`：同样的 body 连发两次，断言第二次 200 且 `id` 与第一次相同。
- `test_refiling_a_different_subject_is_still_refused`：换标题仍 422（守住上面那个宽松判据没有把它放开）。

### 8. POST `/feedback/{feedback_id}/comments` — 本域唯一没有配额的写入口

**现状**：`feedback.py:419-449` → `FeedbackService.comment`（`services.py:856-905`）。

**问题**：这个域里每个「无界增长」的写入口都有配额或锁：`POST /feedback` 有 24 小时每作者上限 + advisory lock（`services.py:696-708`，`repositories.py:454-470`）、提案有三道限流 + 话题锁（`proposals.py:236-290`）。**评论一条都没有** —— 一个登录用户可以对一条公开反馈无限次发言。这不是「已经发生的 bug」，而是这个域里唯一一处不对称：同一份设计在另外两个入口上都认为「刷屏要在提交侧挡」（`services.py:230-232` 的自己写着这句）。

**优化**（最小形状，与报告上限同构）：

```python
# backend/app/domain/feedback/services.py —— `create` 里那对 lock+count 照搬
        await self._repo.lock_author(actor_handle)      # 键改成 feedback-comments:{handle}
        recent = await self._repo.count_author_since(
            actor_handle, datetime.now(UTC) - timedelta(hours=1), table="comments"
        )
        if recent >= settings.feedback_comments_per_author_per_hour:
            raise PreconditionFailedError(...)
```

或者更省事：直接复用同一个 `count_author_since` 的一个变体（把表作为参数，`FeedbackComment` 上已有 `author_handle` 索引）。**先加设置项**（`core/config.py`，默认给一个宽的值比如 200/小时），再决定是否真的在路由上生效 —— 这一条建议的形态（限流口径与阈值）是产品决定，代码里只缺那一句。

**契约**：可能新增 412（与提案、支持那两个入口同一状态码家族，理由也一致：客户端该做的是「别这么干」）。无迁移（除非要把计数建成索引覆盖）。

**测试**：`backend/tests/integration/test_feedback.py` 补 `test_commenting_has_a_cap_like_every_other_write_route`（把上限 monkeypatch 成 2，第三条 412）。

### 9. GET `/feedback`、GET `/feedback/mine` — 每页重算的 counts 与「指派给我的」逐行判定

**现状**：`feedback.py:236-274`（列表 + `counts`）与 `feedback.py:214-233`（`list_mine` + `counts`）。

**问题**（除 #4 那 5 条聚合之外的）：

1. `GET /feedback/mine` → `FeedbackService.list_mine`（`services.py:320-350`）在 `list_related_to` 之后**逐行** `await self.may_see(...)`。`may_see` 对「不是我提的、我不是管理员」的行会落到两次查询：`filed_in_a_room_of_mine`（`repositories.py:549-565`）+ `may_read_topic`（`services.py:183-185`）。这一支只在「指派给我的」那一臂命中的行上走到，所以规模 = 一页里指派给我的条数（正常会很小，但它是按行线性增长的，而列表模板里其它计数**全部**是按页批量的 —— `_cards` 的 docstring 就写着「never one per row」）。
2. `GET /feedback` 的 `sort` 参数（`feedback.py:242`）**不校验**：不认识的值静默当 `new`（`repositories.py:577-579` 的注释承认了这是刻意的：「The public route keeps accepting an unknown sort silently」）。同一个域的管理端列表（`services.list_admin`、`services.py:299-307`）对同名参数**回 400**，理由写得很好（「asking for `hottest` and getting `new` reads the top of the page as the most supported reports」）。同一个词在两个面上两个答案，是这一组里唯一的「同一件事两处拼写」。

**优化**：

1. `list_mine` 的 Python 那一刀是刻意的（`visible_to` 是 `may_see` 的超集，最后一刀必须用同一条规则 —— 这段设计是对的，不该拆）。可优化的是**把这一刀批量问**：新增 `FeedbackRepository.filed_in_a_room_of(handle, feedback_ids) -> set[uuid.UUID]`（一条 `EXISTS ... AND feedback.id IN (...)`），`list_mine` 一次问完所有需要问的行，再对留下来的问 `may_read_topic`（也可以用同一个批量形状：`may_read_topic` 是四张表四条主张，`app/auth/project_access.py` 里若已有批量版就直接用；没有就先只批第一问，收益已经是从 1 次/行降到 1 次/页）。
2. `sort` 校验在服务层加一句，与 `list_admin` 逐字相同的形状：

```python
        if sort not in feedback_services.SORTS:
            # 与 `list_admin` 同一句话：不认识的排序被换成 `new` 时，读的人会把
            # 页首那几条当成「最受支持的」，而它只是「最新的」——顺序就是答案，
            # 用另一个顺序回答是在同一个标题下回答了另一个问题。
            raise BadRequestError(f"未知的排序：{sort}")
```

**契约**：#1 纯实现；#2 是**新的 400**（旧客户端传了不认识的值时会从「静默 `new`」变成报错）—— 需要先确认前端只传 `new`/`supports`（`SORTS` 这两个），以及 `tab=hot` 时服务端自己会改用 `hot` 排序（`repositories.py:748-752`），不要误伤。

**测试**：`backend/tests/integration/test_feedback.py`：`test_an_unknown_sort_is_refused_like_the_admin_queue_does`（对照已有的 `test_an_unknown_tab_is_refused_rather_than_silently_shown_as_all`:493）；批量可见性：`test_the_mine_list_asks_the_room_roster_once_per_page`。

### 10. POST `/topics/{topic_id}/tasks/{task_id}/push-fix`、`/ready`、`/accept-card/describe` — 与 #3 同类的重复解析（低优先）

**现状**：`accept.py:160-171`、`174-181`、`184-211`。

**问题**：三条都先 `_task_actor(topic_id, task_id, ...)`（内部已 `TopicService.require_in_room` + `authorize_topic`），随后服务层又各查一遍：`push_fix`→`list_live_for_places`（不带 task 过滤，取回后自己筛）；`mark_ready`→`TaskService.require_in_room` **再一次** + `_topic_or_404`（`services.py:2166-2169`）；`describe_card`→`redescribe` 再 `_topic_or_404`（`services.py:2239`）+ 末尾 `AcceptService(db).describe(...)`（又 new 了一个 service 实例）。`push_fix` 的 `drop_dependency` 是个**布尔查询参数**（`accept.py:166`，POST 没有请求体），是本文件里唯一一个「动作参数走 query」的写法 —— 与 `describe`/`decisions` 全走 body 不一致，但没有正确性代价。

**优化**：`mark_ready`/`redescribe` 接受已解析的 `topic`/`task`（同 #3 的做法）；`describe_card` 复用同一个 `AcceptService` 实例。这条的收益（每请求 2–3 次往返）远小于 #3、#6，建议与 #3 一起改，不必单独立项。

**契约**：无变化。**测试**：并入 #3 的计数用例。

---

## 三、模块级建议

### 3.1 「鉴权是每一步显式调用」这件事没有兜底，缺一次就是静默放行

**范围**：所有 `/topics/{id}/...` 与 `/accept-cards/{id}/...` 路由（本组 #6、#7 已经踩到）。

**做法**：`app/main.py:455` 那条注释自己已经把结论说完了：「"不加白名单"本身拦不住任何东西（没列进去的写路由压根不过那个中间件，症状是**静默放行**而不是 401）」。读路径是同一个形状 —— `ActorResolverDep` 只是把解析器注进来，鉴权要靠 handler 自己喊。已经有两处踩空（两条 accept 读路由）。可用的三条防线，任选：

1. 路由层加一条依赖：`router = APIRouter(prefix="", dependencies=[Depends(require_verified_caller)])`（仅适用于每条都该要身份的组），把「有没有身份」先钉住，「是不是成员」再由 handler 喊。
2. `backend/tests/unit/` 加一条**棘轮测试**：扫 `app/api/routes/*.py`，凡是签名里有 `resolver: ActorResolverDep` 却在函数体里一次都没出现 `authorize_topic`/`authorize_project`/`authorize_task`/`_actor_in_topic`/`_card_actor`/`_task_actor` 的 handler 就失败。仓库里已经有同形状的棘轮（`tests/unit/test_is_private_read_points.py`、`tests/unit/test_no_adhoc_auth_helpers.py`），这是它们的自然延伸。
3. 最小一步：`_room_actor`（#6 的代码）落进 `accept.py`，两条读路由各接一行。

**为什么**：GitHub 的 REST 文档把「每个端点自己声明所需 scope」写成了契约的一部分（<https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps>，**未逐条查证**）：门必须挂在每条路由上。这里缺的不是规则，是「漏了会响」。

### 3.2 分组列表的分页协议不统一：两套并存

**范围**：`GET /topics/{id}/accept-card`（无分页）、`GET /topics/{id}/feedback-proposals`（无分页）、`GET /feedback` 与 `/feedback/mine`（`page_start`/`page_size` offset 分页）、`GET /feedback/{id}/comments`（`after`+`next_cursor` 游标分页）。

**做法**：以**已有的那一套游标**为准（`domain/feedback/repositories.py:158-188` 的 `cursor_of`/`parse_cursor`/`after_cursor` 已经是一个完整、带二级排序、带「看不懂的游标回 400」的实现），把它推广到两个无分页的列表；`/feedback` 的 offset 分页保留（它已有 `created_at`+`display_no` 全序，换成游标是渐进可做的第二步）。

**为什么**：offset 分页在这个代码库里有个已知的坑，作者自己写了两次 —— `_list_stmt`（`repositories.py:602-606`）为「`created_at` 会撞，OFFSET 翻页会漏行或重复」补了 `display_no` 二级排序，`cursor_of`（:158-168）为同一个问题把 `id` 编进游标。也就是说：**offsets 在这个仓库是被判过刑的**，而评论那条路已经改用游标。GitHub 的列表分页用 `per_page` + `Link` 头 + cursor/`since`（<https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api>，**未逐条查证**），与本仓库已有的游标写法同构；不建议引入 `Link` 头（平台信封已经是 `{code,message,data}`），把 `next_cursor` 放在 body 里继续用即可。

### 3.3 counts 随每一页重算

**范围**：`GET /feedback`、`GET /feedback/mine`（内嵌 `counts`）、`GET /feedback/counts`。

**做法**：① `public_counts` 合成一条 SQL（#4 的代码）；② 列表响应里的 `counts` 只在第一页（`page_start == 0`）带上，或加 `?counts=0`。

**为什么**：标签数字不随 `offset` 变，而它现在是每次翻页 8 条聚合（登录态）。这是「可合并的往返」里收益最直接的一处，且完全不改语义。

### 3.4 请求体里「必填却被忽略」的字段（三个端点）与超宽请求体（一个）

**范围**：`POST /accept-cards/{id}/approve`（`ApprovalCreate.approver_handle`）、`/accept` 与 `/revoke`（`AcceptDecision.decided_by`）、`POST /accept-cards/{id}/reassign`（整份 `AcceptCardCreate`）。

**做法**：见 #3 的三段 schema。收窄成真正的形状，而不是「文档里写一句它被忽略」。

**为什么**：这三个字段不是无害的多余 —— 它们让客户端**以为自己控制了一件它并不控制的事**（「我指定了批准人」「我指定了撤销的人」），而服务端用 token 覆盖它。`test_approve_uses_verified_identity_not_body`、`test_revoke_ignores_spoofed_body_identity` 这两条测试名说明作者知道这回事，只是把答案留在了服务端而没有反馈到契约上。

### 3.5 读路径上的外部调用没有条件请求与连接复用

**范围**：`GET /topics/{id}/pr-checks`（轮询）、`POST .../ready`、`POST .../push-fix`、`POST .../accept-card/describe`（写路径内的 GitHub 调用）。

**做法**：`httpx.AsyncClient` 提到实例级复用；GET 类调用上 `If-None-Match`（304 不计主限额，来源见 #2；**App 安装令牌每小时轮换，缓存键要用 installation id 而不是 token**）。

**为什么**：`pr-checks` 是文档里写着「被定时轮询」的端点，而它每次新建两个 TLS 连接 + 两次 GitHub 调用；GitHub 对条件请求的承诺正好是为这种用法设计的。

### 3.6 错误格式与状态码：**已一致，不需要改**

**范围**：本组 34 条全部。

**结论**：所有失败都过 `app/core/errors.py` 的 `BaseError.to_response_body()`，形状统一为 `{code, message, error:{name, message, data, retryable}}`；412 的用法（提案配额、已办完不再支持）与 429 的区别也在代码里写明并一致。GitHub 的 `message`/`documentation_url` 是另一套（它没有信封），不建议为对齐 GitHub 而动这套平台级约定。唯一值得记一句的是：错误体里没有可点的文档链接，`data` 也基本是空的 —— 如果哪天要给模型/agent 更好的自愈提示，`documentation_url` 那一列的位置就是 `error.data`。

### 3.7 创建类路由没有幂等键

**范围**：`POST /topics/{id}/tasks/{id}/accept-card`（#7）、`POST /feedback`、`POST /feedback/{id}/comments`、提案卡（后三者靠指纹/配额/锁把「重复」变成了 412，可接受）。

**做法**：只在最需要对上（agent/CLI 反复触发、且失败信息会被误读）的那一条上做局部规则（#7 的代码），或统一引入 `Idempotency-Key` 头（Stripe 的做法，<https://docs.stripe.com/api/idempotent_requests>，**业界通行，未逐条查证**）。不建议在平台信封里另发明一套。

**为什么**：`create_accept_card` 是这四条里唯一「重试的答案是 422」的，而 422 的文案（「改验收人而不是再递一张」）会把调用方指向一个错误的下一步。
