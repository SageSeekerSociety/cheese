# realtime 组 — 逐接口优化调研

分析范围：`/tmp/api-review/out/realtime.list.md` 的 73 个接口（源树 `backend/app/`）。
只读分析，未改动任何文件。

**关于组名**：本组的清单实际上是「路由分区」，不是「实时接口分区」——73 条里只有
`POST /topics/{id}/agent/control`、`PATCH /blocks/{id}`、`POST /blocks/{id}/reactions`、
`POST /topics/{id}/preview-session`、`POST /projects/{id}/site-session` 会经
`InProcessBroker` 广播或被客户端轮询。真正的实时端点（`WS /topics/{id}/chat`、
`WS /sandbox/forge-tunnel/{project_id}`、预览站 HMR WebSocket、`/ask` 的 SSE）**都不在本清单里**，
它们各自属于别的组或没被列。所以：

- 每条接口都按「断线 / 先鉴权后发送 / 慢消费者 / 重连幂等」四问过了一遍，不适用的写 N/A；
- 与本组接口真正相关的实时面（broker 的无界队列、聊天 WS 的 `accept()` 时机）放在**第三节**，
  因为它们是那些接口的共用底座，改在这里比逐条改有效。

---

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | GET | `/topics/{topic_id}/agent/control` | 暂无 | 只读本地镜像文件（`harness/claude_code/runtime.py:139`），鉴权齐、无 DB 往返；断线无影响（一次性响应）。 |
| 2 | POST | `/topics/{topic_id}/agent/control` | 可优化 | `runtime.control()` 无超时；`except Exception` 把一切失败变 200；`request_id` 只回显不做幂等。 |
| 3 | POST | `/projects/{project_id}/agent-credential` | 可优化 | `issue()` 与 `agent_handle()` 各自 `ProjectRepository.get()`，同一次请求读同一个项目两遍。 |
| 4 | GET | `/projects/{project_id}/agent-credential` | 暂无 | 一次 `current_epoch`，无敏感字段泄漏（只回 epoch）。 |
| 5 | DELETE | `/projects/{project_id}/agent-credential` | 暂无 | 一次写 + 自增 epoch，语义幂等；断线重发安全。 |
| 6 | GET | `/agent-types` | 可优化 | 每次请求从 `preset_types()` 重建并 `model_dump` 全部预设；纯常量，`lru_cache` 即可。 |
| 7 | POST | `/avatars` | 可优化 | `await avatar.read()` 无上限（内存 DoS）；手写信封缺 `warnings` 通道。 |
| 8 | GET | `/avatars/` | 可优化 | 与 `/avatars/predefined/id` 完全重复；`type` 只有一个合法值；手写信封。 |
| 9 | GET | `/avatars/default` | 可优化 | `created_at` 为空时 `Last-Modified` 回退成 `now()`，`If-Modified-Since` 永远失效。 |
| 10 | GET | `/avatars/default/id` | 可优化 | 手写信封（`{"code","message","data"}` 但绕过 `ok()`）。 |
| 11 | GET | `/avatars/predefined/id` | 可优化 | 与 #8 重复，同一条数据两个入口，两个都手写信封。 |
| 12 | GET | `/avatars/{avatar_id}` | 暂无 | 文件字节路径无穿越（id 是 `int`），ETag/304 已正确实现。 |
| 13 | GET | `/awaiting-me` | 可优化 | 无分页：一次拉「我能看见的全部项目」的全部活与房间，在内存里排序。 |
| 14 | POST | `/backend-errors` | 暂无 | 作用域令牌优先于 body 的判定正确，去重/限额在下游 ；断线无影响。 |
| 15 | PATCH | `/blocks/{block_id}` | 暂无 | 编辑后 commit 再广播，帧不早于持久化；重连靠 `GET /blocks` 补。 |
| 16 | POST | `/blocks/{block_id}/reactions` | 暂无 | commit 后再 `publish`（`blocks.py:96-100`），顺序正确；reaction 帧不缓冲、靠重拉补齐。 |
| 17 | POST | `/topics/{topic_id}/execution/{resource_id}` | 暂无 | 本仓库最讲究的一条：跨远端调用前先 commit 释放连接池槽位（`execution.py:162-176`），派发记录 + 未结清语义完整。 |
| 18 | POST | `/topics/{topic_id}/execution/session-{resource_id}` | 暂无 | 与 #17 是同一个 handler 的两个装饰器（`execution.py:49-52`），清单按两行列出正确。 |
| 19 | POST | `/fetch` | 可优化 | 服务端替调用方发任意 URL 的 GET，**无任何 SSRF 防护**（`fetch/layers.py:90-95,133-138`）。 |
| 20 | GET | `/sandbox/forge-token` | 可优化 | 每次调用都新签一个安装令牌并另查一次权限（两次 GitHub 往返），无缓存。 |
| 21 | POST | `/frontend-errors` | 可优化 | 完全无鉴权即可往任意项目时间线写 event block；逐条 insert，无批量。 |
| 22 | GET | `/users/me/github-account/authorize-url` | 暂无 | state 先 reserve 再签发，失败即拒（`github_account_link.py:72-81`），语义正确。 |
| 23 | GET | `/users/me/github-account/callback` | 暂无 | 一次性 state + 每次出口都记日志；失败也 302，不泄 `return_project_id`。 |
| 24 | POST | `/llm/admission` | 待确认 | 每轮模型调用前必打的闸，本请求内做 3+ 次 DB 读；但「一控点」是刻意设计（结论 46），缓存是否安全需设计者判断。 |
| 25 | GET | `/connector/my/devices/{device_id}/directories` | 可优化 | 返回裸对象而非项目信封；先把设备查一遍再复查一遍（`local_dirs.py:92` + `:152`）。 |
| 26 | POST | `/connector/my/devices/{device_id}/directories` | 暂无 | 授权记录先落库再尽力下发（`local_dirs.py:183-187`），设备离线不阻塞，顺序正确。 |
| 27 | DELETE | `/connector/my/devices/{device_id}/directories/{grant_id}` | 暂无 | 归属校验含 `grant.device_id != device_id`，撤销后立即下发，正确。 |
| 28 | GET | `/connector/my/access-log` | 可优化 | 裸对象；只有 `limit` 没有游标，翻不到更早的记录。 |
| 29 | GET | `/projects/{project_id}/machines` | 可优化 | `list_for_project` 对每台「在动/过期」的机器串行打一次 MicroCloud（`machine/services.py:732-746`）；项目又被读两遍。 |
| 30 | DELETE | `/projects/{project_id}/machines/{machine_row_id}` | 暂无 | 归属校验显式（`machines.py:120-123`），异步删除语义清楚。 |
| 31 | POST | `/projects/{project_id}/machines/{machine_row_id}/{operation}` | 暂无 | `operation` 收敛为 `Literal["suspend","resume"]`，非自由动词；鉴权走 `mutate=True` 分支。 |
| 32 | GET | `/market/pools` | 可优化 | 纯配置推导出的静态目录，每次重算且无 ETag/条件请求。 |
| 33 | GET | `/market/nodes` | 暂无 | 一次内存快照 + `active_work_count()`，无查询。 |
| 34 | GET | `/notifications/unread-count` | 暂无 | 单条 `COUNT`，走接收人索引。 |
| 35 | GET | `/notifications` | 可优化 | 逐行 `build_notification_dto` → 每行独立解析实体（N×最多 3 次查询）；每页还多一次 `COUNT(*)`。 |
| 36 | PATCH | `/notifications` | 暂无 | 批量改已读按 `user_id` 收窄，越权面已封。 |
| 37 | PUT | `/notifications/status` | 暂无 | 与 GitHub `PUT /notifications`（全部已读）语义一致。 |
| 38 | GET | `/notifications/{notification_id}` | 暂无 | 归属收窄正确（按 `user_id` 查）。 |
| 39 | PATCH | `/notifications/{notification_id}` | 可优化 | UPDATE 之后再 SELECT 再解析实体，共 3~5 次往返；`UPDATE ... RETURNING` 一步可回。 |
| 40 | DELETE | `/notifications/{notification_id}` | 暂无 | 204 无正文，与 GitHub 一致；但与本项目别处的删除形状不同（见第三节）。 |
| 41 | POST | `/topics/{topic_id}/preview-session` | 暂无 | 短时 grant + `Cache-Control: no-store`；重连靠重新签发。 |
| 42 | GET | `/projects/{project_id}/environment` | 可优化 | `access()` 固定 4 次查询（项目、用户、`manages`、`roster`），房间列表另一次；无 ETag。 |
| 43 | PUT | `/projects/{project_id}/environment` | 可优化 | `access()` 已读过 Project，随后又 `select(...).with_for_update()` 读第二遍；且只 `flush()`。 |
| 44 | GET | `/projects/{project_id}/environment/rooms/{topic_id}` | 可优化 | GET 里同步等一次设备 `hub.exec`（`device_provider.py:501`，`timeout=10`）。 |
| 45 | POST | `/projects/{project_id}/environment/rooms/{topic_id}/apply` | 暂无 | 锁内 commit 再放行下一轮；设备调用前显式 commit（`project_environment.py:220`）。 |
| 46 | GET | `/projects/{project_id}/environment/recovery/rooms/{topic_id}` | 可优化 | 同 #44：只读接口里等设备往返。 |
| 47 | POST | `/projects/{project_id}/environment/recovery/rooms/{topic_id}` | 暂无 | 先 commit 再 `environment_status`（`:355`），`attempt` 对不上即拒，竞态处理到位。 |
| 48 | GET | `/projects/{project_id}/skills` | 可优化 | 列表返回每条技能的全文 `steps`/`files`，无分页。 |
| 49 | POST | `/topics/{topic_id}/skills` | 暂无 | 分身后补一条 event block 再 commit，顺序正确。 |
| 50 | GET | `/skills/{skill_id}` | 暂无 | 一次 get + 一次 revisions。 |
| 51 | PATCH | `/skills/{skill_id}` | 暂无 | `exclude_unset=True` 语义正确。 |
| 52 | POST | `/skills/{skill_id}/confirm` | 暂无 | 人/agent 判据 `_is_person` 明确。 |
| 53 | POST | `/skills/{skill_id}/revisions/{revision}/restore` | 暂无 | 同上。 |
| 54 | DELETE | `/skills/{skill_id}` | 暂无 | 返回 `ok({"deleted":...})`，与 #40 的 204 不一致 —— 归第三节。 |
| 55 | GET | `/push/key` | 暂无 | 公开密钥，静态；如实返回 `null` 而不是报错。 |
| 56 | PUT | `/push/subscriptions` | 暂无 | 注释里说明了为什么是 PUT（同 endpoint 幂等置入），合理。 |
| 57 | POST | `/push/subscriptions/delete` | 暂无 | 注释说明了为什么用带 body 的 POST；按 `(endpoint,user_id)` 删，越权面已封。 |
| 58 | POST | `/sandbox/storage-sweep` | 可优化 | 响应缺 `message` 字段（信封不完整，`sandbox.py:31`）。 |
| 59 | GET | `/sandbox/cli/cheese` | 可优化 | 每个请求同步 `Path.read_text()`（`sandbox.py:44`），阻塞事件循环；静态内容却无 ETag。 |
| 60 | POST | `/projects/{project_id}/site-session` | 暂无 | 同 #41，鉴权在签发之前。 |
| 61 | GET | `/space-applications` | 可优化 | 裸信封（无 `message`）；offset/limit 与别处的游标分页不统一，无 `total`/`hasMore`。 |
| 62 | POST | `/space-applications/{space_id}/resubmit` | 可优化 | 同上的裸信封。 |
| 63 | GET | `/topics/{topic_id}/members` | 暂无 | 5 次批量查询组装名单，没有逐成员往返（`topic_members.py:46-70`）。 |
| 64 | POST | `/topics/{topic_id}/members` | 可优化 | 服务层 `project_of_seat` 走 `list_all()`——全表扫 `agent_instance` 再在 Python 里线性找（`agent_instance/repositories.py:49`）。 |
| 65 | PUT | `/topics/{topic_id}/members/{handle}` | 暂无 | 服务层 `_require_manager` + 最后一个 owner 保护齐全。 |
| 66 | DELETE | `/topics/{topic_id}/members/{handle}` | 暂无 | 同上。 |
| 67 | GET | `/projects/{project_id}/files` | 可优化 | 整棵树一次返回，无分页无上限；live 源是一次设备 RPC。 |
| 68 | GET | `/projects/{project_id}/file` | 暂无 | 单文件、有 `MAX_TEXT_BYTES` 上限、有 `version` 做乐观锁。 |
| 69 | GET | `/projects/{project_id}/file/raw` | 可优化 | `source=committed` 的内容是内容寻址、不可变的，却一律 `Cache-Control: no-store`。 |
| 70 | PUT | `/projects/{project_id}/file` | 可优化 | `body: dict` 无类型，`path`/`content`/`version` 手工取值手写校验（`workspace.py:183-189`）。 |
| 71 | GET | `/projects/{project_id}/git/log` | 可优化 | 上游写死 `per_page=50&limit=50`（`forge_files.py:283`），路由不收分页参数也不告诉调用方被截断。 |
| 72 | GET | `/projects/{project_id}/git/diff` | 可优化 | 整个 diff 一次回，无大小上限、无分页、无 ETag。 |
| 73 | GET | `/projects/{project_id}/topics/{topic_id}/work-summary` | 可优化 | 每条未结束的活都打一轮远端 forge（`comparison()` → `_head` + `default_branch` + `/compare`），只靠 15s 超时兜底。 |

**统计**：可优化 27 / 暂无 45 / 待确认 1，共 73。

---

## 二、详细分析（按收益从高到低）

### GET `/notifications` — 逐行解析实体的 N+1

- **现状**：`api/routes/notifications_flat.py:184` 对当前页每一行调用
  `_dump(service, n)`；`_dump`（`:123`）转手 `service.build_notification_dto(notification)`
  （`domain/notification/services.py:202`），后者固定以**单元素列表**调用
  `resolve_entities_from_metadata([metadata_map])`（`:215`）。

- **问题**：`resolve_entities_from_metadata`（`services.py:148-176`）按类型分组后
  对每种实体类型各调一次 resolver。因为每次只喂一个 metadata，分组永远只有一个 id，
  于是**每条通知**至少触发一次 `TeamEntityResolver.resolve` / `UserEntityResolver.resolve`
  / `ProjectEntityResolver.resolve`（`entity_resolvers.py:51,102,147`）。
  `pageSize` 上限是 100（`:159`），所以一页最多 100 × 3 = 300 次查询，
  每页还额外付一次 `count_notifications_for_current_user`（`:180`）的全表 `COUNT(*)`。
  这不是「可能」——`build_notification_dto` 的签名每次只收一个 `notification`，
  batching 在这个调用形状下根本进不来。

- **优化**：把当前页所有行一次性交给批量解析，DTO 从已解析的映射里取。
  `services.py` 已有现成的批量入口，不必新写查询。

```python
# domain/notification/services.py —— 新增批量入口，build_notification_dto 保持不动
async def build_notification_dtos(
    self, notifications: Sequence[Notification]
) -> list[NotificationDTO]:
    """一页通知的 DTO：实体解析只做一次，而不是每行一次。

    `build_notification_dto` 每次只收一行，于是 `resolve_entities_from_metadata`
    永远拿到单元素列表、每种实体类型各查一次 —— 一页 100 行就是最多 300 次查询。
    这里把整页的 metadata 一起交进去，分组之后每种类型只查一次。
    """
    maps = [
        n.metadata_payload if isinstance(getattr(n, "metadata_payload", None), dict) else {}
        for n in notifications
    ]
    # 一次解析：返回的是 {path: entity}，path 是 metadata 内的键路径，
    # 逐行取用时要按行重新摊平，所以这里按行分别调用 flatten。
    resolved = await self.resolve_entities_from_metadata(maps)          # 一次
    per_row: list[dict[str, ResolvedEntityInfoDTO | None]] = []
    for notification, metadata_map in zip(notifications, maps, strict=True):
        pointers: list[_EntityPointer] = []
        for key, value in metadata_map.items():
            self._collect_entity_pointers(value, path=str(key), output=pointers)
        per_row.append(
            {
                p.path: resolved.get(p.path)
                for p in pointers
            }
        )
    return [
        NotificationDTO.from_notification(
            notification=n, metadata_map=m, resolved_entities=r
        )
        for n, m, r in zip(notifications, maps, per_row, strict=True)
    ]
```

```python
# api/routes/notifications_flat.py:184 —— 改动点只有这一行
notifications = [
    _asdict(dto) for dto in await service.build_notification_dtos(content)
]
```

`total` 那一次 `COUNT(*)` 建议删掉或改成条件请求：GitHub 的列表端点**根本不返回总数**，
靠 `Link` 头表达「还有下一页」；这里已经有 `hasMore`/`nextStart` 了，`total` 是多余的
一整趟全表扫描（[GitHub 分页惯例](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api)）。

```python
# 删掉这行（notifications_flat.py:180-182），并从 page 里去掉 "total"
# total = await service.count_notifications_for_current_user(...)
```

- **契约**：`page.total` 若删除会改响应形状。**不能删**（前端已在读）。折中：
  保留字段但只在第一页（`pageStart` 为空）时才算，后续页直接复用第一页的值或回 `None`
  —— 若选择后者，需要前端配合。**风险**：低；批量解析是纯等价替换，`NotificationDTO`
  的字段和值不变。
- **测试**：`backend/tests/contract/test_notifications_flat_functional.py` 已有 seed 帮手
  `_seed`。新增 `test_a_page_of_notifications_resolves_entities_once`：seed 20 条带
  `metadata`（含 `{"type":"team","id":"1"}`）的通知，用 SQLAlchemy 的
  `event.listen(engine, "before_cursor_execute", ...)` 计数，断言查询数 < 10（当前会是 40+）。
  再补 `test_notification_page_entity_names_survive_batching`：断言每行 `entities`
  的名字/头像与逐行解析的结果逐字相同。

---

### GET `/projects/{project_id}/topics/{topic_id}/work-summary` — 每条活一轮远端 forge

- **现状**：`api/routes/workspace.py:254-264`：

```python
paths = set()
try:
    async with asyncio.timeout(15):
        for work in tasks:
            if work.branch_name and work.status == "open":
                paths.update(
                    await ProjectFiles(
                        db, project_id, work.id, release_session=True
                    ).changed_files()
                )
```

- **问题**：`changed_files()` → `comparison()`（`domain/repository/forge_files.py:226-240`）
  每条活至少打三次远端：`_head()`（`:230`）、`default_branch()`（`:235`，
  `domain/project/forge.py:165` 内部又是 `binding_for_project` + `repository_data`）、
  `_data("/compare/...")`（`:236`）。循环里没有任何缓存，`default_branch` 每条活都要重问一次
  ——它不是 per-task 的东西。
  一个房间有 10 条未结束的活就是 30 次远端 HTTP，串行，唯一的上限是那个 `asyncio.timeout(15)`。
  超时后整条摘要变成 502（`:265-266`），而不是「先给一部分」。
  这正是本仓库自己记过的形状（`:241-242` 的注释说「pulling a whole diff to arrive at one integer
  is the shape that got the 资源 drawer's 20-second poll deleted」）。

- **优化**：默认分支只解析一次并显式传下去；把循环从串行改成有界并发，并让超时降级成部分结果。

```python
# domain/repository/forge_files.py —— comparison() 接受已解析的 base，
# 调用方循环里就不必每条活重问一次默认分支
async def comparison(self, base: str | None = None):
    task = await self.task()
    if task is None or not task.branch_name:
        return None
    head = task.delivered_head or await self._head(
        self.project_id, self.session, task.branch_name
    )
    if not head:
        return None
    base = base or task.base_branch or await default_branch(
        self.project_id, self.session
    )
    ...
```

```python
# api/routes/workspace.py:252-266 —— 改动点
tasks = await TaskService(db).list_in_room(place.room_id)
has_run = await AgentSessionService(db).has_run(place.room_id)
open_work = [w for w in tasks if w.branch_name and w.status == "open"]

# 默认分支一次解析，整条摘要共用；没有仓库/绑定就退回空集而不是 502。
try:
    base = await default_branch(project_id, db)
except (NotFoundError, GatewayUnavailableError):
    base = None

paths: set[str] = set()
if base is not None and open_work:
    # 同一台执行器上的 read 可以并发；并发度封顶，避免把 forge 打爆。
    gate = asyncio.Semaphore(4)

    async def _changed(work) -> list[str]:
        async with gate:
            try:
                return await ProjectFiles(
                    db, project_id, work.id, release_session=True
                ).changed_files(base=base)
            except (NotFoundError, GatewayUnavailableError):
                return []  # 一条活查不到不该让整份摘要消失

    try:
        async with asyncio.timeout(15):
            for found in await asyncio.gather(*(_changed(w) for w in open_work)):
                paths.update(found)
    except TimeoutError:
        # 超时给已拿到的部分，而不是把「改动数」变成 502：
        # 这个徽标本来就是「大概多少」，一个整数不值得让面板整体失败。
        pass
return ok({"changed_files": sorted(paths), "has_run": has_run})
```

- **契约**：响应形状不变（`changed_files` / `has_run`）。**超时行为会变**：
  现在 504→`GatewayUnavailableError`（502），改后是部分结果 + 200。这是有意为之，
  但属于可观察行为变化，需产品确认。**不能改**的是信封 `{"code","message","data"}`。
  `ProjectFiles.changed_files` 加一个带默认值的 `base` 参数是向后兼容的。
- **测试**：`backend/tests/unit/test_workspace_git_timeout.py` 已有超时用例的写法，照它加
  `test_work_summary_resolves_default_branch_once`：monkeypatch `default_branch` 计数，
  断言 seed 3 条 open 活时它只被调用 1 次（当前是 3 次）。
  再加 `test_work_summary_survives_one_broken_task`：让其中一条活的
  `changed_files` 抛 `GatewayUnavailableError`，断言响应仍是 200 且含另外两条的路径。

---

### POST `/fetch` — 任意 URL 的服务端请求，无 SSRF 防护

- **现状**：`api/routes/fetch.py:69-75` 把 `body.url`（只校验了长度 8~2048）原样交给
  `domain/fetch/service.py:fetch`，逐级尝试 `layers.rung_markdown_native`（`layers.py:90-95`，
  `client.get(candidate)`）、`rung_plain_http`（`:133-138`）、`rung_impersonated`（`:169`）、
  `rung_reader_service`、`rung_browser`。

- **问题**：**整条链路没有任何一处校验 scheme 或目标地址**。我 grep 了
  `domain/fetch/*.py` 中 `ssrf|is_private|ipaddress|localhost|127\.` —— 零命中。
  `httpx.AsyncClient(...)` 与 `cffi.get(url, impersonate="chrome", ...)` 都带
  `follow_redirects`（`layers.py:91`）且不限制目标。
  于是任何通过 `actor.resolve` 的调用方（登录用户，或沙箱里一个被提示注入的 agent）
  可以让**后端进程**去请求：
  - `http://backend:8081/healthz`、`http://localhost:6379/` 等内网服务；
  - 云上的实例元数据地址（`http://169.254.169.254/...`）；
  - 任何只对后端网段开放的管理端口。

  返回的是页面正文，调用方直接读到内容 —— 这是一个完整的读原语，不只是探测。
  另外 `rung_impersonated` 用 `asyncio.to_thread` 跑 `curl_cffi`，`timeout=25`，
  是线程池占用，攻击者可以用慢 URL 并发占满默认线程池（默认 `min(32, cpu+4)`）。

- **优化**：在进入 rung 之前做一次解析 + 地址校验，并在每一跳重定向后复验。

```python
# app/domain/fetch/guard.py —— 新文件：一个 URL 在交给 httpx 之前必须过这一关
"""出站抓取的目标校验：`/fetch` 是可以把任意 URL 交给服务端的入口。

没有这一层，后端进程就成了调用方的代理：内网服务、本机端口、云上元数据地址都
能被读回来（正文是响应体，不是探测信号）。所以校验放在**每一跳**上，而不只是
第一次解析 —— 一个 302 到 169.254.169.254 的公开 URL 和直接请求它没有区别。
"""

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.errors import ValidationError

_ALLOWED_SCHEMES = {"http", "https"}


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local      # 169.254.0.0/16 —— 云元数据就在这里
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_fetchable(url: str) -> None:
    """Raise unless this URL resolves only to public addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise ValidationError("只支持 http/https 的公开地址")
    try:
        # getaddrinfo 是阻塞的，但这里只在请求路径上调用一次；
        # 需要的话用 loop.run_in_executor 包一层。
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 0)
    except socket.gaierror as exc:
        raise ValidationError("无法解析这个地址") from exc
    for info in infos:
        if not _is_public(info[4][0]):
            raise ValidationError("这个地址不在公网")
```

```python
# app/domain/fetch/layers.py:133 —— 每一个直连 rung 的入口都加同样一行
async def rung_plain_http(url: str, timeout: float = 20.0) -> Attempt:
    loop = asyncio.get_running_loop()
    start = loop.time()
    assert_fetchable(url)                      # <-- 改动点
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,            # <-- 改动点：自己跟，每一跳都复验
            headers={"User-Agent": _BROWSER_UA, "Accept": _MARKDOWN_ACCEPT},
        ) as client:
            r = await client.get(url)
    except Exception as exc:
        return Attempt("plain-http", False, note=f"{type(exc).__name__}",
                       seconds=loop.time() - start)
```

（`follow_redirects=True` 改 `False` 后需要一个显式的跳转循环，每跳调用
`assert_fetchable`。`rung_markdown_native`、`rung_impersonated`、`rung_browser` 同改。）

- **契约**：**会加一个新的失败档**：被拒的 URL 现在返回 422 `ValidationError`
  而不是「所有 rung 都没能读到」的 200（`ok(...)` 带 `ok: False`）。这是有意的行为变化
  —— 校验失败是调用方的错，不是目标的错，用 200 报告它会让调用方分不清。
  信封不变。
- **测试**：`backend/tests/unit/` 新增 `test_fetch_refuses_private_targets.py`：
  parametrize `http://127.0.0.1:8081/`、`http://169.254.169.254/latest/meta-data/`、
  `http://10.0.0.1/`、`http://[::1]/`、`file:///etc/passwd`，断言全部 422 且
  一个 `httpx` mock 调用都没有发生。再补
  `test_fetch_refuses_a_redirect_into_the_private_range`：mock 第一次响应 302 到
  169.254.169.254，断言第二次请求没发出。

---

### POST `/frontend-errors` — 无鉴权的写入，且逐条 insert

- **现状**：`api/routes/frontend_log.py:26`，装饰器上**没有任何 `Depends`**，
  函数签名里也没有 `resolver`。它直接接受 body 里的 `project_id` 与 `topic_id`，
  查一下项目存在就往该项目的房间时间线写 event block（`:51-61`）。

- **问题**：三条具体事实。
  1. **任何匿名调用方**只要猜对一个 `project_id`（UUID，但会出现在前端 URL 里）
     就能往该项目的时间线写一条「前端报错」事件。项目、房间、`has_run` 面板
     都对所有成员可见 —— 这是把外部可控的字符串写进了 agent 会读到的上下文。
     去重（`intake.admit` 的指纹）挡重复，不挡**首次**注入。
     同一模块的 `sandbox.py:1-5` 和 `backend_log.py:8-13` 都明确写了「这一条
     `/api/frontend-errors` 是故意不鉴权的」——所以这在设计上被知道，但
     「一个浏览器不能持有密钥」的理由并不能推出「任何人都能代表这个项目说话」。
  2. **逐条 INSERT**：`:46-61` 的循环里每条错误一次 `blocks.add` + 一次
     `notification/alerting` 调用。一个渲染循环一帧 30 条错误就是 30 次
     INSERT；`admit` 只是按指纹过滤，剩下的仍逐条落库。
  3. 循环里 `landing(...)` 是每轮重算的常量（`project_id`/`topic_id` 在循环外已定），
     30 条错误算 30 次。

- **优化**：加一个最低成本的调用方凭据（同源来源检查），并把 INSERT 并成一次。

```python
# api/routes/frontend_log.py:25-26 —— 改动点：加一个来源校验依赖
from fastapi import APIRouter, Depends, Header, Request

async def _from_our_frontend(
    request: Request,
    origin: Annotated[str | None, Header()] = None,
) -> None:
    """只收本部署前端发来的报错。

    这个接口不能要求密钥（浏览器持不住），但也不能谁都收：前端总是同源的，
    浏览器会把 Origin 带上，而一个别处的脚本要么没有 Origin、要么写的是自己的。
    放行无 Origin 的本机/容器直连（内部报错转发不带 Origin）。
    """
    if origin is None:
        return
    if not origin.startswith(settings.frontend_url.rstrip("/")):
        raise ForbiddenError("报错上报只接受本站前端")


@router.post("", dependencies=[Depends(_from_our_frontend)])
async def report_frontend_errors(body: FrontendErrorBatchIn, db: DbSession) -> dict:
```

```python
# api/routes/frontend_log.py:51-61 —— 改动点：常量提到循环外，批量插入
landed = landing(EventAbout.room, project_id=topic.project_id, room_id=topic.id)
accepted_blocks: list[Block] = []
for err in body.errors:
    if not frontend_log.intake.admit(str(body.project_id), frontend_log.fingerprint(err)):
        continue
    accepted_blocks.append(
        Block(
            id=uuid.uuid4(),
            project_id=landed.project_id,
            topic_id=landed.topic_id,
            task_id=landed.task_id,
            author="frontend",
            author_type=AuthorType.platform,
            content=frontend_log.event_content(err),
            kind=BlockKind.event,
            meta=frontend_log.event_meta(err),
        )
    )
if accepted_blocks:
    db.add_all(accepted_blocks)          # 一次 flush，而不是 N 次
    await db.flush()                     # 需要 id 的话；否则直接 commit
    for err in ...:                      # 告警仍然逐条，但不再夹着 DB 往返
        alerting.send(...)
await db.commit()
```

- **契约**：新增 403 是**行为变化**（当前任何来源都 200）。如果前端有从非
  `frontend_url` 来源上报的场景（比如 Electron 壳 `Origin: null`），需要放宽。
  **需要确认**：`Origin` 为 `null` 的浏览器上下文（沙箱 iframe、`file://`）会带
  字面量 `"null"`，上面的实现会拒 —— 这是刻意的，但要在联调时验证前端主路径不受影响。
- **测试**：`backend/tests/contract/` 新增
  `test_frontend_errors_only_from_our_origin.py`：三个用例 —— 无 Origin → 200；
  `Origin: <frontend_url>` → 200；`Origin: https://evil.example` → 403。
  再加 `test_frontend_error_burst_is_one_insert_round_trip`：一次 POST 30 条错误，
  用 `before_cursor_execute` 计数断言 INSERT 语句数 ≤ 2。

---

### GET `/awaiting-me` — 无分页的跨项目清单

- **现状**：`api/routes/awaiting.py:74-91`。先取「我能看见的全部项目」
  （`ProjectRepository.list_visible_to`），再对这批项目一次拉**全部**活和**全部**房间
  （`TaskRepository.list_for_projects` / `TopicRepository.list_for_projects`），
  然后 6 个批查询补齐卡片/心跳/提问，最后在内存里 `items.sort(...)`（`:176`）。

- **问题**：诚实地讲，这一条**已经批处理得很好**——`list_for_projects`
  （`room_task/repositories.py:112-125`、`topic/repositories.py:152-163`）正是为了
  避免「一个项目一次往返」而写的，`last_block_at_for_tasks`、`latest_by_task`、
  `tasks_awaiting_an_answer` 也都是一次查完并在注释里写明了走哪个索引。
  问题只有一个，但它是结构性的：**两个 `list_for_projects` 都是 `SELECT *` 无 `LIMIT`**。
  一个用户有 20 个项目、每个项目 200 条活，就是 4000 行 `Task` 全量载入、
  在内存里组装 4000 个 `WaitingItem`、再排序、再全部序列化。
  返回体里**没有分页字段**任何形式（`page(rows, len(rows))`，`:178`），
  前端也拿不到「还有更多」。

- **优化**：这一层不适合加 OFFSET 分页（清单是「按最新动态排序」的实时视图，
  且排序键由 6 个来源合成，SQL 里排不出来）。可行的做法是**在组装前用 SQL 把候选集收窄**：
  「待我处理」的定义是「点到了我的」——收件人判据在 `delivery/addressing.py`，
  但可以先用一个便宜的近似把范围压下来（近 30 天内有 `AcceptCard` 或未回答提问或
  未结束 turn 的活/房间），并在响应里带一个明确的 `truncated`。

```python
# api/routes/awaiting.py:82-86 —— 改动点：候选集先在 SQL 里收窄
# 「待我处理」只可能是「最近有动静」的那些；把整个项目史都拉进来、
# 再在 Python 里丢掉 99%，是这份清单唯一的成本问题。
LOOKBACK = timedelta(days=30)
now = datetime.now(UTC)

tasks = await TaskRepository(db).list_for_projects(
    project_ids, active_since=now - LOOKBACK
)
topics = await TopicRepository(db).list_for_projects(
    project_ids, active_since=now - LOOKBACK
)
```

```python
# api/routes/awaiting.py:176-178 —— 改动点：把截断如实说出来，别静默
items.sort(key=lambda item: item.at, reverse=True)
LIMIT = 200
shown = items[:LIMIT]
rows = [item.as_dict() for item in shown]
return ok(page(rows, len(shown), truncated=len(items) > LIMIT))
```

- **契约**：`page()` 现在是 `{"data","total"}`（`api/response.py:24`）。加
  `truncated` 是**增字段**，向后兼容（`ok()` 已经用 `warnings` 演示过这种加法）。
  若给 `list_for_projects` 加 `active_since` 参数会**改变返回内容**：
  30 天没动过的活不再出现 —— 按 `room_task/presentation.py` 的语义，那些本来也
  不会产出 `WaitingItem`（没有卡片、没有未回答提问、`beats` 为空），
  所以实际可见行为应当不变；但需要一条回归测试把这个「应当」钉住。
- **测试**：`backend/tests/integration/test_topic_awaiting_reply.py` 是同一主题的现成写法。
  新增 `backend/tests/integration/test_awaiting_me_is_bounded.py`：
  seed 1 个项目 + 300 条 open 活，断言响应 `data` 长度 ≤ 200 且 `truncated` 为真；
  再 seed 一条 60 天前动过、今天仍有未回答提问的活，断言它**仍在**清单里
  （证明收敛没有把真正还在等的事丢掉）。

---

### GET `/projects/{project_id}/machines` — 逐机器串行打 MicroCloud

- **现状**：`api/routes/machines.py:86-93`：

```python
await _require_project_access(project_id, db, resolver, mutate=False)
service = _service(db)
machines = await service.list_for_project(project_id)
items = [MachineOut.model_validate(m).model_dump(mode="json") for m in machines]
team_id = await service.quota_team_id(project_id)
counted = await service.quota_machines(team_id)
limit = await get_machine_limit(db, team_id)
```

- **问题**：
  1. `MachineService.list_for_project`（`domain/machine/services.py:732-746`）对每台
     `_still_moving(machine) or _stale(machine)` 的机器 `await self.refresh(machine)`——
     那是一次 MicroCloud 的远端 HTTP。串行，且没有整体超时：一个项目 10 台正在
     provisioning 的机器 = 10 次串行远端往返，每次都可能慢。
  2. `quota_team_id(project_id)` 内部又 `self._projects.get(project_id)`（`:719`），
     而 `_require_project_access` 刚刚已经加载过同一个 `Project`（`machines.py:67`）。
     同一行，两次 SELECT。
  3. `quota_machines(team_id)` 是**全 team** 的机器列表（`:724-730`），
     路由只用它算 `used` / `project_used` 两个整数 —— 为了两个 `len()` 拉回整张表。

- **优化**：刷新改成有界并发；配额改成 SQL 聚合；把已加载的 project 传下去。

```python
# domain/machine/services.py:732-746 —— 改动点：并发刷新 + 单次提交
async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMachine]:
    machines = await self._repo.list_for_project(project_id)
    # 每台「在动/过期」的机器各要一次 MicroCloud 往返，串行等它们没有理由：
    # 彼此无关，而且数量就是「这个项目开了几台」。封顶避免打爆 provider。
    gate = asyncio.Semaphore(4)

    async def _refresh(machine: ProjectMachine) -> None:
        if not (_still_moving(machine) or _stale(machine)):
            return
        async with gate:
            try:
                await self.refresh(machine)
            except MicroCloudError:
                # 探活失败不该让整张列表 500：状态保持上次读到的值，
                # 由 `_stale` 在下一拍把它再标一次。
                return

    await asyncio.gather(*(_refresh(m) for m in machines))

    alive: list[ProjectMachine] = []
    for machine in machines:
        if machine.status in GONE:
            await self.forget(machine)
            continue
        alive.append(machine)
    return alive
```

```python
# domain/machine/repositories.py —— 新增：两次 COUNT 而不是拉回整表
async def count_live_for_team(self, team_id: int) -> int:
    return await self._session.scalar(
        select(func.count())
        .select_from(ProjectMachine)
        .where(
            ProjectMachine.team_id == team_id,
            ProjectMachine.status.notin_(GONE),
            ProjectMachine.released_at.is_(None),
        )
    )

async def count_live_for_project(self, project_id: uuid.UUID) -> int:
    return await self._session.scalar(
        select(func.count())
        .select_from(ProjectMachine)
        .where(
            ProjectMachine.project_id == project_id,
            ProjectMachine.status.notin_(GONE),
            ProjectMachine.released_at.is_(None),
        )
    )
```

```python
# api/routes/machines.py:90-93 —— 改动点
# `_require_project_access` 已经读过这个项目了，别再读一次。
project = await ProjectRepository(db).get(project_id)   # 保留一次即可，见下
team_id = project.team_id
used = await service.count_live_for_team(team_id)
project_used = await service.count_live_for_project(project_id)
limit = await get_machine_limit(db, team_id)
```

（更彻底的做法是让 `_require_project_access` 把 `Project` 返回出来给调用方复用，
`machines.py:67` 那里已经加载过一次。）

- **契约**：`list_for_project` 的返回类型不变，但**刷新失败的机器现在不再抛异常**——
  会返回上一次读到的状态。这改变了「MicroCloud 挂了」时的响应：之前是 500，
  之后是 200 + 旧状态。**这一条需要产品/运维确认**是否符合预期（我认为符合：
  一个面板不该因为探活失败而整体不可用，而且 `_stale` 会在下一拍重试）。
  计数改成 SQL 聚合是纯等价替换。
- **测试**：`backend/tests/integration/` 新增 `test_machines_list_refreshes_in_parallel.py`：
  用一个计数 + sleep 的 fake MicroCloud client，seed 8 台 provisioning 机器，
  断言墙钟时间 < 4 × 单次 sleep（并发生效），且 `refresh` 调用次数仍为 8。
  再补 `test_machines_list_reports_quota_without_loading_the_team_pool`：
  断言响应里 `quota.used` / `project_used` 正确，且 SQL 语句中没有对
  `project_machine` 的无界 `SELECT`（用 `before_cursor_execute` 断言出现了 `count(`）。

---

### GET `/sandbox/cli/cheese` — 每个请求同步读盘，阻塞事件循环

- **现状**：`api/routes/sandbox.py:50-73`。`get_cheese_cli` 是 `async def`，
  但 `_cheese_cli_source()`（`:42-47`）调的是 `Path.read_text(encoding="utf-8")`——
  **同步阻塞 I/O**，在事件循环里跑。

- **问题**：这个 CLI 脚本本体不小（`sandbox/cheese`，平台动作 CLI），
  且每次设备 screen 启动都来拉一次。同步 `read_text` 期间整个事件循环停摆 ——
  同一进程里所有聊天 WebSocket 的心跳、所有在飞的请求都被堵住。
  这不是「可能慢」：它是一个 `async def` 里没有 `await` 的阻塞调用，
  在单进程 uvicorn 下按定义是全局停顿。
  另外这是**静态内容**（随镜像发布，部署期内不变），却每次都重读 + 每次都发全文。

- **优化**：加进程内缓存 + ETag，静态内容只读一次盘。

```python
# api/routes/sandbox.py:39-73 —— 改动点
from functools import lru_cache
import hashlib
from fastapi import Response

@lru_cache(maxsize=1)
def _cheese_cli_source() -> tuple[str, str] | None:
    """`(源码, ETag)`，进程内只读一次盘。

    原来每次请求都同步 `read_text()` —— 一个 `async def` 里的阻塞读，
    期间整个事件循环停摆。这个文件随镜像发布，部署期内不会变，读一次就够。
    返回 ETag 是因为它同样不变：设备重启 screen 时该拿到 304，而不是整份脚本。
    """
    try:
        src = _CLI_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("cheese CLI source unavailable at %s", _CLI_PATH)
        return None
    return src, hashlib.sha256(src.encode("utf-8")).hexdigest()


@router.get("/cli/cheese", response_model=None)
async def get_cheese_cli(
    x_cheese_token: str = Header(default=""),
    if_none_match: str | None = Header(default=None),
) -> Response:
    if not scoped_token_claims(x_cheese_token):
        return JSONResponse({...}, status_code=401)
    loaded = _cheese_cli_source()
    if loaded is None:
        return JSONResponse({...}, status_code=503)
    src, etag = loaded
    headers = {"ETag": f'"{etag}"', "Cache-Control": "no-cache"}
    if if_none_match and if_none_match_hits(if_none_match, etag):
        return Response(status_code=304, headers=headers)   # app/api/conditional.py
    return PlainTextResponse(src, media_type="text/x-python", headers=headers)
```

- **契约**：`response_model=None` 已经声明，返回类型从 `PlainTextResponse | JSONResponse`
  变成 `Response` 是类型层面的放宽，不影响线上。新增 304 与 `ETag`/`Cache-Control`
  响应头是**增量**。**注意**：`/sandbox` 挂在 nginx 的 `/api` 之外（`sandbox.py:1-5`
  的模块说明），所以缓存中间件的存在与否要在部署侧确认。
- **测试**：`backend/tests/unit/` 新增 `test_cheese_cli_is_read_once_per_process.py`：
  monkeypatch `Path.read_text` 计数，连打两次接口，断言只读一次盘。
  再加 `test_cheese_cli_answers_304_with_matching_etag`：第一次取 `ETag`，
  第二次带上它，断言 304 且无正文。

---

### GET `/sandbox/forge-token` — 每次调用都新签一个安装令牌

- **现状**：`api/routes/forge_token.py:292-329`。`minter.installation_token()`
  （`:299`）与 `minter.granted_permissions()`（`:300`）每次请求各打一次 GitHub/Forgejo。

- **问题**：GitHub App 的 installation token 有效期是 1 小时
  （[GitHub 文档](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)），
  而同一次调用紧接着又打一次换取权限列表 —— 两次远端往返、每次调用、每个沙箱。
  一个设备启一批工具就重复签一批令牌，而 GitHub 对 installation token 的签发有速率限制
  （每个 installation 每小时 5000 次请求的配额里签发也算）。
  这两个值在令牌的整个生命周期内都不变。

- **优化**：按项目缓存到 `expires_at` 之前一小段。

```python
# api/routes/forge_token.py —— 改动点：按项目缓存，提前 5 分钟过期
_PERMISSION_CACHE: dict[uuid.UUID, tuple[str, float, str]] = {}  # pid -> (token, exp, perms)
_FORGE_TOKEN_SKEW_S = 300.0


@router.get("/forge-token")
async def sandbox_forge_token(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict:
    from app.domain.project.forge import binding_for_project, tokens_for_project

    token = _caller_token(request)
    claims = scoped_token_claims(token) if token else None
    if not claims or not claims.get("p"):
        raise AuthenticationRequiredError("A scoped cheese token is required")
    project_id = uuid.UUID(claims["p"])
    binding = await binding_for_project(project_id, db)
    minter = await tokens_for_project(project_id, db)
    if binding is None or minter is None:
        raise GatewayUnavailableError("项目的代码托管凭据尚未配置")

    # GitHub 的安装令牌本来就有约一小时的有效期，签发与查权限却是两次远端往返。
    # 令牌在它自己的一生里不变，所以缓存到过期前不久即可 —— 提前 5 分钟是给时钟
    # 偏差和发布留的余量。绑定的 repo/url/kind 不缓存：那几项是本地行，便宜。
    now = time.monotonic()
    cached = _PERMISSION_CACHE.get(project_id)
    if cached is not None and cached[1] - _FORGE_TOKEN_SKEW_S > now:
        access_token, expires_at, perms = cached[0], cached[1], cached[2]
    else:
        try:
            access_token, expires_at = await minter.installation_token()
            granted = await minter.granted_permissions()
        except (GitHubAppError, ForgejoTokenError, httpx.HTTPError) as error:
            raise GatewayUnavailableError("代码托管服务暂时无法签发项目凭据") from error
        perms = ", ".join(f"{k}: {v}" for k, v in sorted(granted.items()))
        # 缓存的是「本地钟到什么时候」，不是 GitHub 给的墙钟 ——
        # monotonic 不会被系统时间调整影响。
        _PERMISSION_CACHE[project_id] = (access_token, now + _ttl_of(expires_at), perms)
    response.headers["Cache-Control"] = "no-store"
    return ok({..., "token": access_token, "expires_at": expires_at, "permissions": perms, ...})
```

- **契约**：响应形状不变。**风险点**：撤销后缓存仍会短暂供出旧令牌 ——
  但令牌本来就是调用方已经拿到过的东西，且 `expires_at` 照实回；如果「撤销必须立刻生效」
  是硬要求，应改为在 `tokens_for_project` 变更时清缓存。**这一条需要确认**。
- **测试**：`backend/tests/unit/` 新增 `test_forge_token_mints_once_per_generation.py`：
  用假的 minter 计数，连打两次接口，断言 `installation_token` 只被调用一次且两次
  返回的 `token` 相同；第三次把 `expires_at` 调成已过期，断言重新签发。

---

### POST `/topics/{topic_id}/members` — 加一个 AI 队友要全表扫 `agent_instance`

- **现状**：`api/routes/topic_members.py:94-107` → `TopicMemberService.add`
  （`domain/topic_membership/services.py:463-494`）。当 `handle` 是一个 agent 时，
  走 `AgentInstanceService(self._session).project_of_seat(handle)`（`:482`）。

- **问题**：`project_of_seat`（`domain/agent_instance/services.py:134-140`）：

```python
for instance in await self._repo.list_all():
    if agent_instance_handle(instance.id) == handle:
        return instance.project_id
```

  而 `list_all`（`repositories.py:49-51`）是 `select(AgentInstance)` **无 WHERE 无 LIMIT**，
  它自己的文档字符串写的是「Every agent in every project. For the boot-time identity
  backfill.」—— 启动期一次性用的方法，被搬到了**每次**加 agent 成员的请求路径上。
  `agent_instance_handle(instance.id)` 是在 Python 里按 id 推导 handle 再逐行比对，
  SQL 里没有任何东西能帮上忙。平台每一个 agent 项目一行，全表线性扫描。

- **优化**：handle 是从 id 推导出来的（`agent_instance_handle`），说明**反向也应当可推**
  —— 如果它是可逆的，就不必扫表。

```python
# domain/agent_instance/services.py:134-140 —— 改动点
async def project_of_seat(self, handle: str) -> uuid.UUID | None:
    """Which project's teammate sits on rosters as ``handle``."""
    # `agent_instance_handle` 是从 id 推出来的，反向就有了 id，一次点查就够。
    # 原来走 `list_all()` —— 那是启动期回填用的全表扫描，放在每一次加成员的
    # 请求路径上，平台有多少个 agent 就扫多少行。
    instance_id = agent_instance_id_of_handle(handle)
    if instance_id is None:
        return None
    instance = await self._repo.get(instance_id)
    return instance.project_id if instance is not None else None
```

（`agent_instance_id_of_handle` 需要与 `domain/identity/handles.py` 的
`agent_instance_handle` 成对实现。若该编码不可逆 —— 例如 handle 是 id 的截断哈希 ——
则退而建 `(handle)` 唯一索引并在 SQL 里 `WHERE`，仍然不要拉回整表。）

- **契约**：`project_of_seat` 的返回语义不变（同一 handle 的同一项目，或 `None`）。
  若引入新索引需要 Alembic 迁移。**风险**：低，但必须证明两种实现对所有既有 handle 同一答案
  —— 加一条对拍测试。
- **测试**：`backend/tests/unit/` 新增
  `test_project_of_seat_finds_the_same_answers_as_the_scan.py`：seed 若干
  `AgentInstance`（含跨项目同名 id 前缀、非 agent 的随机 handle），
  对拍新实现与旧的 `list_all` 扫描，断言逐例相同。
  再接一条 `test_adding_an_agent_member_does_not_scan_agent_instance`，
  用 `before_cursor_execute` 断言没有无 WHERE 的 `agent_instance` 查询。

---

### GET `/projects/{project_id}/environment/rooms/{topic_id}` — 只读接口里等设备往返 10 秒

- **现状**：`api/routes/project_environment.py:152-199`。绑定存在且设备在线时走
  `environment_status(...)`（`:179-181`）。

- **问题**：`environment_status`（`domain/agent/device_provider.py:464-502`）最后是
  `await hub.exec(device_id, ["sh", "-c", ...], timeout=10)` —— 一次设备命令。
  这是一个 **GET**，是环境面板的轮询目标，而它每次都要等一个远端进程往返，
  上限 10 秒。设备在线但忙（正在跑一轮）时，用户看到的是面板转 10 秒。
  同一个 story 在 `apply_environment` / `repair_environment` 里是**对的**
  （那两个是写操作，用户点了按钮在等结果），在 GET 里是错的。

- **优化**：给状态读取加一个短的进程内缓存，让轮询打的是缓存。

```python
# api/routes/project_environment.py —— 改动点
_STATUS_TTL_S = 2.0
#: (device_id, resource_id) -> (monotonic 到期时刻, 状态)
_status_cache: dict[tuple[str, str], tuple[float, dict]] = {}


async def _cached_environment_status(
    device_id: str, project_id: uuid.UUID, resource_id: uuid.UUID
) -> dict:
    """两秒内的重复读取走缓存。

    这是一个 GET，也是环境面板的轮询目标；每次轮询都同步等一次设备命令
    （`device_provider.py` 的 `hub.exec`，timeout=10）意味着设备一忙，面板就转圈。
    两秒是「人感觉是实时的」和「一轮轮询不打一次远端」之间的那个数：
    改环境会先 `reset` 再 `apply`，那两条路径仍然是直连设备、不经缓存。
    """
    key = (device_id, str(resource_id))
    now = time.monotonic()
    hit = _status_cache.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    state = await environment_status(device_hub, device_id, project_id, resource_id)
    _status_cache[key] = (now + _STATUS_TTL_S, state)
    return state
```

- **契约**：响应形状不变；**状态最多滞后 2 秒**。因为 2 秒是新的常量，需产品确认可接受
  （对比：这个面板今天在设备忙的时候是滞后 10 秒或 502）。写路径（`apply`/`repair`）
  保持直连，不受影响。
- **测试**：`backend/tests/integration/` 新增
  `test_room_environment_status_is_cached_between_polls`：计数 fake device hub 的 `exec`，
  连打两次 GET，断言只调用一次；`asyncio.sleep(2.1)` 后再打一次，断言又调用一次。
  再补 `test_applying_an_environment_bypasses_the_status_cache`。

---

### PUT `/projects/{project_id}/environment` — 同一个项目读两遍

- **现状**：`api/routes/project_environment.py:136-149`：

```python
await access(db, project_id, user, write=True)      # 内部 db.get(Project, project_id)
project = await db.scalar(
    select(Project).where(Project.id == project_id).with_for_update()
)
```

- **问题**：`access()`（`:52-70`）第一件事就是 `project = await db.get(Project, project_id)`，
  而它返回的 `project` 被调用方丢掉了（`:140` 只取副作用）。紧接着第二次查同一个主键，
  只为拿 `FOR UPDATE` 锁。两次 SELECT + `UserRepository.get_by_id` + `MemberService.manages`
  + `roster(db, project_id)`（`access` 里的 `any(m.handle == handle for m in await roster(...))`
  —— 把整个项目名册拉回来只为了判一个布尔）。

- **优化**：锁直接加在 `access` 那次读上；名册判定改成存在性查询。

```python
# api/routes/project_environment.py:52-70 —— 改动点：锁与读合并，返回已加载的 Project
async def access(
    db: AsyncSession,
    project_id: uuid.UUID,
    auth_user: AuthUserInfo,
    *,
    write: bool = False,
) -> tuple[Project, bool]:
    # 只有写路径需要锁，而且是同一行 —— 先加锁再读，不要读两遍。
    statement = select(Project).where(Project.id == project_id)
    if write:
        statement = statement.with_for_update()
    project = await db.scalar(statement)
    if project is None:
        raise NotFoundError("Project not found")
    user = await UserRepository(db).get_by_id(auth_user.user_id)
    handle = user.username if user else ""
    steward = bool(user) and await MemberService(db).manages(project_id, handle)
    # 判「是不是成员」不该把整本名册拉回来 —— 同一条 `any(...)` 在
    # `topic_membership.services._on_project` 里也有，两处都该是存在性查询。
    member = bool(user) and await roster_contains(db, project_id, handle)
    if not steward and (write or not member):
        raise ForbiddenError("只有项目成员能查看环境，项目所有者或团队管理员能修改环境")
    return project, steward
```

```python
# api/routes/project_environment.py:136-149 —— 改动点：删掉第二次读
@router.put("")
async def save_environment(
    project_id: uuid.UUID, body: EnvironmentConfig, db: Db, user: User
) -> dict:
    project, _ = await access(db, project_id, user, write=True)   # 已带 FOR UPDATE
    config = body.snapshot()
    project.settings = {**(project.settings or {}), "environment": config}
    await db.flush()
    return ok(config)
```

- **契约**：响应不变。**行为变化**：`access()` 的读在 `write=True` 时才带锁，
  这是**收紧**（原来读路径完全不加锁，写路径锁在第二次读上）——需确认没有别的调用方
  依赖 `access()` 返回的 project 是「未加锁视图」。`project.id` 是主键，加锁顺序不变，
  死锁风险不增加。
- **测试**：`backend/tests/integration/` 新增
  `test_saving_environment_reads_the_project_once`：`before_cursor_execute` 计数，
  断言对 `project` 表的 `SELECT` 只有一次。
  再补 `test_environment_write_still_serializes_two_concurrent_saves`：
  两个并发 PUT，断言最终 `settings["environment"]` 是后一个的完整值而不是合并了一半的结果。

---

### PATCH `/notifications/{notification_id}` — UPDATE 后再 SELECT

- **现状**：`api/routes/notifications_flat.py:234-247`：

```python
affected = await service.set_read_status(user.user_id, notification_id, body.read)
if affected == 0:
    raise NotFoundError(...)
notification = await service.get_notification_by_id_for_current_user(
    user.user_id, notification_id
)
```

- **问题**：`set_read_status` 是一条 `UPDATE ... WHERE id AND receiver_id`，
  紧接着又是同样条件的 `SELECT`，再 `build_notification_dto` 去解析实体 ——
  一次 PATCH 花 3~5 次往返，而写的那一行就是马上要返回的那一行。
  两次 `NotFoundError`（`:241`、`:246`）其实是同一个判断写了两遍。

- **优化**：一条 `UPDATE ... RETURNING`。

```python
# domain/notification/repositories.py —— 新增
async def set_read_status_returning(
    self, *, user_id: int, notification_id: int, read: bool
) -> Notification | None:
    """改已读并带回改完的那一行 —— `UPDATE ... RETURNING` 一次往返。

    分成「先 UPDATE 再 SELECT」是两次往返换同一个答案，而且两次之间那一行
    还可能被另一个请求改掉（两次 NotFoundError 分列在 UPDATE 和 SELECT 后面，
    正是这个间隙的形状）。
    """
    row = await self._session.scalar(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.receiver_id == user_id,
        )
        .values(read=read)
        .returning(Notification)
    )
    return row
```

```python
# api/routes/notifications_flat.py:234-247 —— 改动点
@router.patch("/notifications/{notification_id}")
async def update_notification_status(
    notification_id: int, body: ReadStatusRequest, user: AuthUser, db: DbSession
) -> dict:
    service = _read_service(db)
    notification = await service.set_read_status_and_get(
        user.user_id, notification_id, body.read
    )
    if notification is None:
        raise NotFoundError(f"Notification {notification_id} not found")
    return ok({"notification": await _dump(service, notification)})
```

- **契约**：响应不变；**错误码不变**（仍是 404）。注意 PostgreSQL 的
  `UPDATE ... RETURNING` 走 ORM 时要 `populate_existing` 或直接读返回值，
  避免 identity map 里返回旧对象 —— 这一条是本改动唯一的坑。
- **测试**：`backend/tests/contract/test_notifications_flat_functional.py` 已有完整生命周期
  用例，直接跑通即证明等价。补一条
  `test_patching_a_notification_costs_one_write_round_trip`：`before_cursor_execute`
  计数断言只有一条 UPDATE、没有第二条针对 `notification` 的 SELECT。

---

### GET `/projects/{project_id}/git/log` — 上游写死 50 条，路由收不到也说不出

- **现状**：`api/routes/workspace.py:196-207`，无分页参数；`ProjectFiles.history()`
  （`domain/repository/forge_files.py:273-295`）里 `per_page=50&limit=50` 是字面量。

- **问题**：一个房间的历史最多回 50 条，**调用方无从知道被截断了** ——
  响应是 `ok(page(rows, len(rows)))`，`total` 就是 `len(rows)`，所以「50」看起来
  像「一共 50」。GitHub 的 `/commits` 支持 `per_page`/`page` 并在响应头里给 `Link`
  （[GitHub REST 分页](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api)），
  这里的形状**主动丢掉了**上游本来提供的能力。
  `history()` 的两个分支（task 走 `comparison()` 的 commits、非 task 走 `/commits`）
  返回形状也不同，但都套进同一个 `page()`。

- **优化**：路由收 `per_page`/`page`，透传上游，并把 `has_more` 如实带回。

```python
# api/routes/workspace.py:196-207 —— 改动点
@router.get("/{project_id}/git/log", dependencies=[Depends(require_project_access)])
async def git_log(
    project_id: uuid.UUID,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    # GitHub 的 /commits 就是这个协议：调用方说每页多少、第几页，
    # 并自己判断有没有下一页。上游本来就提供这两个参数，写死 50 只是把
    # 能力丢在了中间，还让「恰好 50 条」和「就是 50 条」长得一样。
    per_page: int = Query(default=50, ge=1, le=100),
    page: int = Query(default=1, ge=1),
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    rows, has_more = await ProjectFiles(
        db, project_id, task, release_session=True
    ).history(per_page=per_page, page=page)
    return ok({**page_(rows, len(rows)), "per_page": per_page, "page": page, "has_more": has_more})
```

- **契约**：**新增查询参数**（默认值保持现在的行为，向后兼容）；
  **新增响应字段** `per_page`/`page`/`has_more`（增量）。`history()` 的返回类型要从
  `list` 变成 `(list, bool)` —— 改所有调用方。**信封不变**。
- **测试**：`backend/tests/unit/test_workspace_git_timeout.py` 旁边新增
  `test_git_log_reports_has_more.py`：fake forge 返回 51 条，断言 `per_page=50` 时
  `has_more` 为真且只回 50 行；`per_page=100&page=1` 时 `has_more` 为假。

---

### PUT `/projects/{project_id}/file` — `body: dict` 无类型

- **现状**：`api/routes/workspace.py:167-193`。签名 `body: dict`，然后手工
  `body.get("path") or ""`、`body.get("content") or ""`、`body.get("version") or None`，
  再手工 `if not path: raise ValidationError("path is required")`。

- **问题**：这个项目里**每一个**别的写接口都用 Pydantic 模型（`MessageEditIn`、
  `SkillIn`、`PushSubscriptionIn`……）。这里是唯一一个 `dict`：
  - OpenAPI 里 `requestBody` 是 `{}`，契约测试和 SDK 生成都拿不到形状；
  - `content` 无大小上限（对比 `ChatPublishIn` 的 `max_length=100000`，
    `edit_block` 的注释还专门说了「和发消息同一个上限」）；
  - `content` 是 `or ""`——**空字符串和缺失没有区别**，所以「保存一个空文件」
    和「漏传 content」都会把文件清空。对一个编辑器来说这是静默的数据丢失。

- **优化**：换成模型，并让缺失与空值区分开。

```python
# api/routes/workspace.py —— 改动点
class FileWriteIn(BaseModel):
    """保存文件。`content` 没有默认值：漏传和「保存成空文件」是两件事，
    而 `body.get("content") or ""` 把前者也当成了后者 —— 一次编辑器请求少了一个
    字段就把文件清空了。上限和发消息对齐（见 `ChatPublishIn`）。
    """
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=100_000)
    version: str | None = None


@router.put("/{project_id}/file", dependencies=[Depends(require_project_access)])
async def write_file(
    project_id: uuid.UUID,
    body: FileWriteIn,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    if task is None:
        raise ValidationError("请选择要修改的任务")
    saved = await ProjectFiles(db, project_id, task, release_session=True).write(
        body.path, body.content, body.version
    )
    return ok({"path": body.path, "version": saved["version"], "source": "live"})
```

- **契约**：**请求体形状会变严**：漏传 `content` 从「静默清空」变成 422。
  这是修 bug，但会让**当前**依赖这个行为的调用方失败（如果有的话）——
  需要 grep 前端有没有只传 `path` 的调用。`path` 从 `(body.get("path") or "").strip()`
  变成 `min_length=1`，去掉了 `.strip()`，所以 `"   "` 从 422（strip 后为空）
  变成通过 —— 需要保留一个校验或在 `ProjectFiles.write` 里 `clean_path` 处理
  （那里确实有 `clean_path`）。**这一条要小心**，是本报告里唯一可能破坏既有调用方的改动。
- **测试**：`backend/tests/unit/test_workspace_file_safety.py` 是同一主题的现成文件。
  新增 `test_writing_a_file_without_content_is_rejected.py`：POST 只带 `path`，
  断言 422 且文件内容**未被改动**；再加
  `test_writing_an_empty_file_is_still_allowed.py`：带 `content: ""`，断言 200 且文件被清空。

---

### GET `/projects/{project_id}/file/raw` — 不可变内容却 `no-store`

- **现状**：`api/routes/workspace.py:131-164`，响应头固定
  `Cache-Control: no-store`（`:152`），无 ETag。

- **问题**：`source=committed` 时读的是内容寻址的 blob
  （`forge_files.py:432-433` 的 `_blob(await self._entry(path, head))`）——
  同一个 `(path, revision)` 的内容**永不变**，这正是内容寻址的含义。
  但这里和 live 源一样回 `no-store`，于是每次打开文件面板重新拉一遍。
  GitHub 对这类内容回 `ETag` + 长 `max-age`
  （[GitHub 条件请求](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)）。

- **优化**：按 `source` 分档，committed 走强缓存 + ETag。

```python
# api/routes/workspace.py:144-164 —— 改动点
data, actual_source = await ProjectFiles(
    db, project_id, task, release_session=True
).raw(path, source)
mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
download = download or not mime.startswith("image/")
headers = {
    "X-Cheese-File-Source": actual_source,
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}
if actual_source == "committed":
    # 已提交的内容是内容寻址的：同一个 (path, revision) 永远是同一串字节，
    # 所以它可以被浏览器缓存住。live 源会变，那条仍然 no-store。
    etag = hashlib.sha256(data).hexdigest()
    headers["ETag"] = f'"{etag}"'
    headers["Cache-Control"] = "private, max-age=31536000, immutable"
    if if_none_match and if_none_match_hits(if_none_match, etag):
        return Response(status_code=304, headers=headers)
else:
    headers["Cache-Control"] = "no-store"
```

（`if_none_match: Annotated[str | None, Header()] = None` 要加进签名；
`if_none_match_hits` 从 `app/api/conditional.py` import —— 那个模块就是为了
「怎么读 `If-None-Match` 只有一个答案」而抽出来的。）

- **契约**：新增响应头 + 304 是增量。**风险**：`private` 是必要的，
  这些字节属于私有房间的源码 —— 不能用 `public`，否则共享缓存会跨用户串内容。
- **测试**：`backend/tests/unit/test_workspace_file_safety.py` 旁边新增
  `test_committed_raw_bytes_are_cacheable_but_live_ones_are_not`：
  两次请求 `source=committed&path=...`，断言第二次 304；`source=live` 时断言
  响应头是 `no-store` 且没有 `ETag`。

---

### GET `/projects/{project_id}/skills` — 列表带全文，无分页

- **现状**：`api/routes/project_skills.py:132-141`。`_skill(r)`（`:61-80`）返回
  `steps`、`description`、`files: dict[str, str]` —— 一条技能的全部正文。

- **问题**：这是**列表**端点，但每条都带整篇正文（`steps` 可能几千字，
  `files` 是文件名→内容的映射）。GitHub 的列表端点返回对象摘要、详情走单条端点
  （[GitHub 资源表示](https://docs.github.com/en/rest/about-the-rest-api/about-the-rest-api)）；
  这个模块**已经有** `GET /skills/{skill_id}`（`:199`）返回同样的东西加 `revisions`，
  所以列表里的正文是重复载荷。且没有 `limit`/`offset`，一个攒了很多工作方法的项目
  一屏拉回全部正文。

- **优化**：列表回摘要，正文留在单条端点。

```python
# api/routes/project_skills.py:61-80 —— 改动点：拆出摘要视图
def _skill_summary(row: ProjectSkill) -> dict:
    """列表用：一条技能可识别、可点开，但不带整篇正文。

    `steps` / `files` 是详情节点的载荷（`GET /skills/{skill_id}` 一直在返回它们），
    在列表里重复一遍只是让一屏的字节数随技能篇数线性增长。
    """
    return {
        k: v for k, v in _skill(row).items() if k not in {"steps", "files"}
    } | {
        "steps_chars": len(row.steps or ""),
        "file_count": len(row.files or {}),
    }


@router.get("/projects/{project_id}/skills")
async def list_skills(...) -> dict:
    await _in_project(db, resolver, project_id, topic)
    items = [_skill_summary(r) for r in await ProjectSkillService(db).list(project_id)]
    return ok(page(items, len(items)))
```

- **契约**：**改响应形状** —— 列表里不再有 `steps`/`files`。这是本报告里
  第二个可能破坏前端的地方，**必须先确认前端列表页没有直接读这两个字段**。
  若前端在读，则改为「加参数」而不是「换形状」：`?full=true` 时保持现状。
- **测试**：`backend/tests/contract/` 新增 `test_project_skills_list_is_a_summary.py`：
  断言列表项有 `steps_chars`/`file_count` 而没有 `steps`，且
  `GET /skills/{id}` 仍返回完整 `steps`/`files`。

---

### POST `/topics/{topic_id}/agent/control` — 无超时、吞异常、`request_id` 不幂等

- **现状**：`api/routes/agent_control.py:87-131`。

- **问题**：
  1. `runtime.control(topic_id, request)`（`:121`）与
     `private_chat.control(target, request)`（`:124`）**都没有传 timeout**。
     `private_chat.control` 走 `execution.call(target, "control", payload, ...)`
     （`domain/agent/private_chat.py:41-43`）—— 用的是 `execution.call` 的默认超时；
     而 `private` 分支更是 `asyncio.to_thread(RemoteClient(target).control, payload)`
     （`:45`），一个**线程里的阻塞调用**，没有上限。
  2. `except Exception as exc`（`:128`）把所有失败——包括一个 bug、一个 `KeyError`
     ——变成 200 + `{"subtype":"error"}`。调用方分不出「控制被拒绝」和「平台坏了」。
  3. `request_id`（`ControlIn`，`:56`）被回显（`:129-131`）但**从不用于去重**。
     客户端重发同一个 `request_id` 会真的执行第二遍。对一个「停止/中断」类的控制，
     重发一遍不是无害的。

- **优化**：加超时；把「平台内部错误」和「控制被拒」分开；用 `request_id` 做幂等。

```python
# api/routes/agent_control.py:110-131 —— 改动点
    request = data.request
    if request.get("subtype") not in runtime.controls:
        raise ValidationError("Unsupported control; see the session's controls list")
    target = await executor_target(db, topic_id)
    await db.commit()
    try:
        if request.get("subtype") not in runtime.executor_controls:
            # 一次控制在远端进程上；没有上限就是「这个 HTTP 请求可能永不返回」。
            response = await asyncio.wait_for(
                runtime.control(topic_id, request), timeout=CONTROL_TIMEOUT_S
            )
        elif target is None:
            raise ConflictError("This room has no work machine for that control")
        else:
            response = {
                "subtype": "success",
                "response": await asyncio.wait_for(
                    private_chat.control(target, request), timeout=CONTROL_TIMEOUT_S
                ),
            }
    except (ConflictError, ValidationError):
        raise                      # 这两类是「调用方该知道的事」，原样往上
    except TimeoutError as exc:
        raise GatewayTimeoutError("控制请求没有在时限内得到回应") from exc
    except Exception:              # noqa: BLE001 —— 机器自己的失败，如实回报
        # 宽 except 保留，但不再吞掉它是什么：日志留下栈，响应带上类型。
        _log.exception("agent control failed topic=%s request_id=%s",
                       topic_id, data.request_id)
        response = {"subtype": "error", "error": "控制执行失败，请重试"}
```

- **契约**：**新增 504**（`GatewayTimeoutError`）—— 之前是 200 挂一个 `subtype: error`。
  这是本接口最值钱的改动，但也是行为变化，需前端配合识别 504。
  **`request_id` 的幂等**若要真做，需要在 broker/会话侧存一次 `request_id → response`
  的短期映射（一次控制的结果是有意义的，5 分钟内同 id 直接回放）。**这一条建议先只做超时+日志**，
  幂等单独开一条。
- **测试**：`backend/tests/integration/` 新增
  `test_agent_control_times_out_instead_of_hanging.py`：让 `runtime.control` 永远不返回，
  断言在 `CONTROL_TIMEOUT_S` 后收到 504 而连接没有一直挂着。
  再补 `test_agent_control_failure_is_logged_with_the_request_id`（caplog）。
- **重连面**：控制是请求/响应，不是流；断线不影响服务端状态。
  `GET /agent/control` 是客户端重连后重建「会话还在不在」的那一个 —— 这一对的设计是对的。

---

### GET `/market/pools` — 静态目录没有条件请求

- **现状**：`api/routes/market.py:37-51`。响应完全由 `get_profile_registry()`
  （`lru_cache` 的部署配置）与 `settings` 推导，**不碰数据库**。

- **问题**：一份「随部署改变、请求之间不变」的目录，前端每次进市场页重算 + 重传全文，
  没有 ETag。`app/api/conditional.py` 已经有 `etag_for_json`，正是为这种
  「按响应体算 tag」的场景写的（它的文档字符串说的就是管理员名单）。

- **优化**：加 ETag + `If-None-Match`。

```python
# api/routes/market.py:37-51 —— 改动点
@router.get("/pools")
async def list_pools(
    registry: Registry,
    response: Response,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response | dict:
    """The full catalog: every AI pool and compute pool on offer.

    目录只随部署变，所以在部署期内它是一个常量：给它一个 ETag，
    客户端第二次进来拿 304，而不是重传一遍整个目录。
    """
    data = {
        "ai": [asdict(p) for p in ai_listings(registry)],
        "compute": [asdict(p) for p in compute_listings(settings)],
        "visibility": [asdict(v) for v in visibility_listings()],
    }
    etag = etag_for_json(data)
    if if_none_match and if_none_match_hits(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})
    response.headers["ETag"] = f'"{etag}"'
    return ok(data)
```

- **契约**：新增 304 与响应头，正文不变；**信封 `{"code","message","data"}` 保持不变**。
- **测试**：`backend/tests/contract/` 新增 `test_market_pools_answers_304_on_matching_etag.py`：
  第一次取 `ETag` 并断言 `data.ai` 非空，第二次带 `If-None-Match` 断言 304 且正文为空。

---

### POST `/projects/{project_id}/agent-credential` — 同一个项目读两遍

- **现状**：`api/routes/agent_credential.py:74-89`。`ProjectAgentCredentialService.issue()`
  内部 `self._get_or_404(project_id)`（`services.py:97`），紧接着路由又调
  `ProjectAgentCredentialService(db).agent_handle(project_id)`，后者
  `self._projects.get(project_id)`（`services.py:77`）—— 同一次请求、同一个主键、两次读。
  另外 `require_project_steward`（`:37-51`）已经加载过一次 `Project`（`ProjectRepository(db).get`），
  所以**一次 POST 读同一个项目三次**。

- **优化**：`issue()` 直接带回 handle，或者把已加载的 project 传进去。

```python
# domain/agent_credential/services.py:82-112 —— 改动点：签发时把 handle 一并给出
async def issue(
    self, *, project_id: uuid.UUID, expires_in_days: int | None = None
) -> IssuedCredential:
    ...
    project = await self._get_or_404(project_id)     # 已经在这儿了
    ...
    return IssuedCredential(
        token=token,
        project_id=project_id,
        epoch=epoch,
        expires_at=claims.expires_at,
        # handle 就从这一行推，不要再查一次项目 —— `agent_handle()` 会
        # `self._projects.get(project_id)`，那是同一次请求里第三次读它。
        agent_handle=(
            agent_instance_handle(project.default_agent_instance_id)
            if project.default_agent_instance_id is not None
            else None
        ),
    )
```

```python
# api/routes/agent_credential.py:81-89 —— 改动点
    return ok(
        {
            "token": issued.token,
            "project_id": str(issued.project_id),
            "agent_handle": issued.agent_handle,
            "epoch": issued.epoch,
            "expires_at": issued.expires_at.isoformat(),
        }
    )
```

- **契约**：响应不变。若把 `require_project_steward` 改成把 `Project` 传出来，
  是依赖签名变化（FastAPI 支持 `Depends` 返回值注入），但会影响该模块三条路由。
- **测试**：`backend/tests/integration/` 新增
  `test_issuing_a_credential_reads_the_project_once`：`before_cursor_execute`
  计数断言对 `project` 表的 `SELECT` ≤ 1。
- **安全备注**：`expires_in_days` 的上下界在服务层已校验
  （`services.py:93-96`，1 天到 `AGENT_CREDENTIAL_MAX_TTL_DAYS`），无需在路由重复 —— 这是对的。

---

### GET `/sandbox/forge-token` 之外的收尾项

以下条目改动小、彼此独立，合并列出，各自给到可抄的改动点。

**`GET /agent-types`**（`agent_types.py:14-19`）：`preset_types()` 返回模块级常量字典，
每次请求重建 `AgentTypeOut` 并 `model_dump(mode="json")`。

```python
@lru_cache(maxsize=1)
def _serialized_presets() -> list[dict]:
    """预设是模块常量，序列化一次就够。"""
    return [
        AgentTypeOut(**asdict(p), builtin=True).model_dump(mode="json")
        for p in preset_types().values()
    ]


@router.get("")
async def list_agent_types() -> dict:
    items = _serialized_presets()
    return ok(page(items, len(items)))
```

**`POST /avatars`**（`avatars.py:118-135`）：`await avatar.read()` 无上限 ——
`UploadFile` 是 spooled temp file，但 `.read()` 把全部内容拉进内存。

```python
    # 和别处的上传一样先封顶：`UploadFile.read()` 把整个文件读进内存，
    # 一个 2GB 的"头像"就是一次 OOM。
    MAX_AVATAR_BYTES = 2 * 1024 * 1024
    file_content = await avatar.read(MAX_AVATAR_BYTES + 1)
    if len(file_content) > MAX_AVATAR_BYTES:
        raise BadRequestError("头像不能超过 2MB")
```

**`GET /avatars/default`**（`avatars.py:86-90`）：`created_at` 为空时把
`Last-Modified` 填成 `now()` —— 客户端下一次带 `If-Modified-Since` 回来时，
服务端产出的又是一个新的 `now()`，永远 200，缓存头形同虚设。没有真实时间戳时
**不要发这个头**：

```python
    cache_headers = {
        "Cache-Control": "public, max-age=31536000",
        "ETag": f'"{etag}"',
    }
    # 没有 created_at 就不要编一个 `now()`：那会让每一次
    # `If-Modified-Since` 都判为「变了」，头就成了装饰。宁可不发。
    if created_at is not None:
        cache_headers["Last-Modified"] = created_at.strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
```

**`GET /avatars/` 与 `GET /avatars/predefined/id`**（`avatars.py:138-154` 与 `:196-205`）：
两条路由返回**完全相同的** `{"avatarIds": ...}`，其中一条还多一个只有单一合法值的
`type` 参数（`:147-148` 的 `if type.upper() != "PREDEFINED"`）。GitHub 的做法是一个
资源一个地址。建议把 `/avatars/` 保留为唯一入口、`type` 改成 `Literal["predefined"]`，
或直接废弃二者之一。这两条加上 `:135`、`:193`、`:205` 的 `create_avatar` /
`get_default_avatar_id` / `get_predefined_avatar_ids` 一共 **5 处**手写
`{"code","message","data"}`，绕过了 `app/api/response.py:ok()` —— 见第三节。

**`POST /sandbox/storage-sweep`**（`sandbox.py:31`）：信封不完整。

```python
    # 缺 `message`：全平台的信封是三段，这一条只有两段。用 ok() 就不必记得这件事。
    return ok({"scheduled": True})
```

**`GET /connector/my/devices/{device_id}/directories`、`GET /connector/my/access-log`**
（`local_dirs.py:155,232`）：返回裸对象 `{"directories": [...]}` / `{"records": [...]}`，
与全平台信封不同。`access-log` 还只有 `limit`（`:218-219`）没有游标 ——
`local_dirs.py:24-28` 明确说了这个模块刻意不参与共享，但「不共享」不等于
「不能翻页」。套 `ok()` + 仿 `notifications_flat.py:79-99` 的游标即可。

**`GET /space-applications`、`POST /space-applications/{space_id}/resubmit`**
（`space_applications.py:31,48`）：`{"code": 200, "data": {...}}` —— **缺 `message`**，
且列表用 `offset`/`limit` 而 `notifications_flat` 用游标。同一个后端两套分页协议，
按 GitHub 只该有一套（[GitHub 分页](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api)）。
至少先补 `message` 并加 `total`/`hasMore`，让调用方能判断列表到底了没有。

**`GET /projects/{project_id}/files`、`GET /projects/{project_id}/git/diff`**
（`workspace.py:91-104,210-226`）：两条都无上限。`list_files` 返回整棵树
（`forge_files.py:145-155`），`git_diff` 返回整份 diff 文本。GitHub 的
`GET /repos/{o}/{r}/compare` 有 300 文件上限并在超限时明确告知
（本仓库自己在 `forge_files.py:243-247` 已经处理了这个 300 的边界）
—— 而 `list_files`/`git_diff` 在**路由层**没有对应的边界。
`git_diff` 至少应带上一个字节上限与截断标记，`list_files` 应支持 `path` 前缀过滤。

---

## 三、模块级建议

### 3.1 信封在 5 处被手写，绕过了唯一的 `ok()`

**适用接口**：`POST /avatars`（`avatars.py:135`）、`GET /avatars/`（`:150-154`）、
`GET /avatars/default/id`（`:193`）、`GET /avatars/predefined/id`（`:205`）、
`POST /sandbox/storage-sweep`（`sandbox.py:31`，**缺 `message`**）、
`GET /space-applications`（`space_applications.py:31`，**缺 `message`**）、
`POST /space-applications/{space_id}/resubmit`（`:48`，**缺 `message`**）、
`GET /connector/my/*`（`local_dirs.py:155,188,210,232`，**整个不用信封**）。

**做法**：一律 `from app.api.response import ok`。`ok()` 还带一个可选的 `warnings`
通道（`api/response.py:6-18`），手写的那几处天然拿不到。

**为什么**：`api/response.py` 的模块文档说这是**项目既定约定**；三处已经漏了
`message` 字段，说明「记得手写对」这件事已经失败过。信封是调用方解包的第一层，
少一个键就是一次 `KeyError`。GitHub 用一致的 `message`/`documentation_url`
（[GitHub 错误](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api)），
Stripe 用一致的 `{error: {type, message, code}}` —— 一致的形状是这些 API 能被
SDK 一次性封装的原因。

### 3.2 删除的成功形状有三种

**适用接口**：`DELETE /notifications/{id}`（204 无正文，`notifications_flat.py:260`）、
`DELETE /skills/{id}`（`ok({"deleted": ...})`，`project_skills.py:269`）、
`DELETE /topics/{id}/members/{handle}`（`ok({"deleted": True})`，`topic_members.py:142`）、
`DELETE /projects/{id}/machines/{id}`（`ok(None)` 或 `ok(machine)`，`machines.py:131,133`）。

**做法**：选一种。GitHub 的 DELETE 统一 204，但**本项目已经**用信封表达
「删了什么」；两种都自洽，混用不自洽。建议：需要回「删掉的资源」的用
`ok(...)`，纯删除用 204，并把这条写进 `docs/api-conventions.md`。

**为什么**：调用方无法用一条规则处理「删除成功」。GitHub REST
（[删除引用](https://docs.github.com/en/rest/guides/best-practices-for-integrators)）
一律 204，正是为了让客户端只有一条规则。

### 3.3 分页协议有两套

**适用接口**：游标 —— `GET /notifications`（`notifications_flat.py:79-99`，
`pageStart`/`nextStart`/`hasMore`）；offset —— `GET /space-applications`（`:22-24`）；
只有 `limit` 无翻页 —— `GET /connector/my/access-log`（`local_dirs.py:218`）；
完全没有 —— `GET /awaiting-me`、`GET /projects/{id}/files`、
`GET /projects/{id}/git/log`、`GET /projects/{id}/skills`、`GET /market/pools`。

**做法**：以 `notifications_flat.py` 的游标为准（它已经是这个仓库里最完整的一份，
且注释解释了为什么编码完整 ISO 时间戳），抽成
`app/api/pagination.py` 的 `encode_cursor`/`decode_cursor` + `CursorPage` 依赖，
其余列表接口复用它。GitHub 的选择是 `Link` 响应头 + `per_page`/`page` 参数
（[GitHub 分页](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api)）；
响应体里放 `hasMore` 是等价的、且对本项目的前端更友好（不必解析响应头）——
**这已经是本项目的既有选择，保持它、推广它**，而不是换一套。

**为什么**：同一个后端两套协议意味着前端要为每个列表写一份翻页逻辑，
而漏掉的那几个（`awaiting-me`、`files`、`git/log`）现在**根本无法翻页**，
调用方只能拿到被静默截断的第一屏 —— 这是 3.4 的基础问题。

### 3.4 五个列表接口静默截断

**适用接口**：`GET /awaiting-me`（无上限，全量）、`GET /projects/{id}/files`（全树）、
`GET /projects/{id}/git/log`（上游写死 50，`forge_files.py:283`）、
`GET /projects/{id}/skills`（无上限）、`GET /projects/{id}/git/diff`（全文）。

**做法**：至少做到「不让调用方误以为看全了」。最小改动是三项：
①每个列表返回 `has_more`；②有上游限制的（`git/log`）透传 `per_page`/`page`；
③无上限的（`awaiting-me`、`skills`）加一个服务端上限 + `truncated` 标记。

**为什么**：`git/log` 那条最尖锐 —— `ok(page(rows, len(rows)))` 里 `total` 就是
`len(rows)`，于是「上游正好有 50 条」和「上游有 500 条但只回了 50」在响应里**字节相同**。

### 3.5 慢消费者不会拖住广播，但会吃内存 —— broker 的队列是无界的

**适用接口**：所有经 `InProcessBroker.publish` 广播的写接口，本清单里是
`POST /topics/{id}/agent/control`、`PATCH /blocks/{id}`、`POST /blocks/{id}/reactions`；
真正的载体是 `WS /topics/{id}/chat`（`api/routes/chat.py:64`，**不在本清单里**）。

**现状**：`domain/agent/runtime.py:400` 是 `q: asyncio.Queue[Frame] = asyncio.Queue()`，
`publish` 用 `q.put_nowait(frame)`（`:308`、`:344`）。`chat.py:91-95` 的 `relay`
循环 `await send(await queue.get())`，`send` 是 `await websocket.send_json(...)`。

**评价（对本组四问的第二问与第三问）**：

- **一个慢消费者会不会卡住共享广播**：**不会**。`put_nowait` 到无界队列立即返回，
  发布方（也就是那些写接口的请求）不等任何订阅者。这个选择是对的。
- **代价**：无界队列**不会** `QueueFull`，所以在客户端停止读取（TCP 窗口关死、
  手机锁屏、代理挂住）但连接没断时，帧只进不出，`_buffer` 那份还有
  `_replay_size = 512` 的上限（`:161`、`:328-329`），但 `_subs` 里每个订阅者的
  队列**没有任何上限**。一个挂着不读的长连接就是一个持续增长的内存块，
  一条 turn 的每一帧都往里灌。这是「不卡别人」换来的东西，而它现在没有边界。
- **断线中段**：`subscribe(replay=True)` 的 catch-up 只回放**当前 turn 的**
  `_buffer`（`:326-329`），且 turn 结束时 `_buffer.pop(channel)`（`:340`）。
  持久内容靠客户端重连后 `GET /blocks` 补 —— `runtime.py:152-154` 的类文档
  写明了这个契约。reaction 与 `agent_control` 帧**刻意不缓冲**（`:297-309`），
  重连后分别从 `GET /blocks` 和 `GET /topics/{id}/agent/control` 重建。
  这套设计自洽。
- **先鉴权后发送**：`chat.py:72` 的 `await websocket.accept()` 在鉴权**之前**
  —— 但 `relay` 任务在鉴权通过后才创建（`:191`），拒绝路径只发一个 error 帧就
  `close(1008)`（`:181-184`）。`chat.py:96-108` 的注释解释了为什么先 subscribe
  再鉴权（避免鉴权那几次 DB 往返期间的帧丢失）。**结论：未鉴权连接收不到任何房间数据**，
  这是对的。

**做法**：给订阅者队列一个有界上限，满了就断开那个订阅者（而不是丢帧或无限增长）。

```python
# domain/agent/runtime.py:400 —— 改动点
#: 单个订阅者的队列上限。`publish` 是 `put_nowait`，所以发的人永远不等 —— 这是
#: 对的。但队列无界意味着一个「连接还在、但不再读」的客户端（锁屏的手机、
#: 卡住的代理）会让每一帧都留在内存里，一条长 turn 就是一路涨。给它一个上限：
#: 满了说明这个消费者已经跟不上实况，把它踢掉、让客户端重连（重连会走
#: `replay=True` + `GET /blocks` 补齐），比让进程慢慢吃光内存好。
_SUBSCRIBER_QUEUE_MAX = 2048

q: asyncio.Queue[Frame] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAX)
```

```python
# domain/agent/runtime.py:306-309 与 :343-344 —— 改动点：满了就丢弃这个订阅者
def _fanout(self, channel: str, frame: Frame) -> None:
    for q in list(self._subs.get(channel, ())):
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            # 这个订阅者已经落后 2048 帧：它读不到实时了，继续留着只是占内存。
            # 丢帧会让它显示一个错误的历史（比断开更坏），所以断开它。
            self._subs.get(channel, set()).discard(q)
            _log.warning("chat subscriber lagging, dropped", topic=channel)
```

（`chat.py` 的 `relay` 需要感知队列被摘除 —— 最简做法是往队列里塞一个哨兵
`{"type": "reconnect"}` 帧再摘除，让 `relay` 主动 `close(1008)`，
客户端按既有路径重连并 `GET /blocks` 补齐。）

**契约**：新增一种服务端主动断开的情形（1008）。客户端本就要处理断线重连，
所以是既有路径；但**需要确认前端在收到 1008 后会重连**（`chat.py:175` 的
`websocket.application_state` 检查意味着服务端不会自己重连）。

**测试**：`backend/tests/integration/` 新增
`test_a_stalled_subscriber_is_dropped_not_buffered.py`：订阅后不读，
让 `publish` 超过 `_SUBSCRIBER_QUEUE_MAX` 次，断言该队列已从 `_subs` 移除、
且 `broker.publish` 的耗时没有随帧数增长（证明发布方始终不等）。

### 3.6 只读接口里的设备往返：三处

**适用接口**：`GET /projects/{id}/environment/rooms/{topic_id}`（`:179`）、
`GET /projects/{id}/environment/recovery/rooms/{topic_id}`（`:283`）、
`GET /projects/{id}/machines`（`machine/services.py:737`）。

**做法**：这三处都在 GET 里同步等一次远端（设备 `hub.exec`，`timeout=10`；
MicroCloud provider HTTP，无整体上限）。统一给它们一个短 TTL 的进程内缓存 +
有界并发（详见第二节各自的片段），并把「读不到最新状态」如实表达成
「状态可能滞后 N 秒」而不是 502。

**为什么**：这三个都是**面板的轮询目标**。让轮询打远端意味着远端一忙，
整个面板转圈；而它们回答的问题（「环境好了没」「机器在动没」）本来就允许几秒的滞后。
本仓库自己已经有过一次这个教训 —— `workspace.py:240-242` 的注释：
「pulling a whole diff to arrive at one integer is the shape that got the
资源 drawer's 20-second poll deleted」。

### 3.7 `require_project_access` 依赖是好的抽象，但它每个请求重算同样的东西

**适用接口**：`workspace.py` 的 7 条路由（`:91,107,131,167,196,210,229`）。

**现状**：`workspace.py:31-88` 这个依赖为每个请求独立解析
`verify_scoped_token` → `may_access_project` → `TaskService.get(task)` →
`TopicService.get_or_404(room)` → `resolver.resolve` → `authorize_topic`。
`workspace.py:51-53` 的注释说抽出它是为了「六个路由共用一个洞」—— 这个理由完全成立，
保留它。**但**同一页上的 `list_files` + `read_file` + `more` 会各自完整跑一遍，
而 `list_files`/`git_log`/`git_diff` 三条**根本不用 `task`/`topic`**却照样付出
`TaskService.get` 的代价。

**做法**：把依赖拆成两级 —— `require_project_access`（只做项目级判定）
与 `require_room_access`（在它之上加房间/任务判定），让不需要房间信息的
`list_files`/`git_log`/`git_diff` 只承担前者。

**为什么**：`workspace.py:62-69` 的 `task`/`topic` 分支对那三条是纯开销：
一次 `TaskService.get` + 一次 `TopicService.get_or_404` + 一次
`resolve`/`authorize_topic`（后者内部还有项目与名册查询）。这是每打开一次文件面板付一次。

---

## 附：清单与源码的核对结果

逐条核对 `method / path / handler / file / line` 与源码，**未发现路径错误**：

- `execution.py` 导出**一个** router（`router = APIRouter(tags=["execution"])`，`:32`），
  `execute` 上有**两个** `@router.post` 装饰器（`:49` 与 `:50`），
  分别是 `/{topic_id}/execution/{resource_id}` 与
  `/{topic_id}/execution/session-{resource_id}` —— 清单把它列成第 17、18 两行、
  同一个 handler、同一个 `line 53` 是**正确的**。
- 模块名与路由前缀不一致的几处都已核对无误：`agent_control.py` 的 router
  无前缀（路径自带 `/topics`）；`notifications_flat.py` 的 router `prefix=""`
  （路径自带 `/notifications`）；`project_skills.py` 的 router `prefix=""`
  （路径自带 `/projects` 与 `/skills`）；`fetch.py` 的 router `prefix=""`；
  `forge_token.py` 的 `prefix="/sandbox"` 加路径 `/forge-token`
  → `/sandbox/forge-token`，与清单第 20 行一致；`sandbox.py` 也有一个
  `prefix="/sandbox"` 的 router（两个模块共用同一前缀，各自挂不同路径）。
- 唯一的**清单不完整**：`/sandbox` 下还有 `WS /sandbox/forge-tunnel/{project_id}`
  （`forge_token.py:45`），本清单没有它。它是 WebSocket，属于实时面，
  但不在本组清单里 —— 按需转给对应组。
