# 平台 API 逐接口优化调研 — 组 `topics`

- 负责文件：`backend/app/api/routes/topics.py`（3856 行，63 个接口）
- 仓库（只读）：`$WT`；行号均指该文件，除注明外
- 判断依据：优先 GitHub REST 惯例（已逐条查证，URL 见第三节），其次 Stripe / Slack / RFC 7807；查不到的按业界通行做法，并在文中注明

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | POST | `/topics` | 可优化 | actor 解析两次、`TopicService` 构造两次；无可选幂等键，超时重发会多开一间房 |
| 2 | GET | `/topics` | 可优化 | 全量返回、没有任何分页；`total` 由一次与结果同源的冗余 COUNT 得出 |
| 3 | GET | `/topics/{topic_id}` | 暂无 | 单房间约 14 次定长查询，均为批量件且无 N+1；未见越权 |
| 4 | GET | `/topics/{topic_id}/blocks` | 可优化 | 默认路径（无 limit）多跑一次与 `list_for_topic` 谓词完全相同的 COUNT |
| 5 | GET | `/topics/{topic_id}/history` | 可优化 | 缺 `total`（与 `/blocks` 不一致）；`q` 走三段 ILIKE，无索引可依 |
| 6 | GET | `/topics/{topic_id}/history/{block_id}` | 暂无 | 单块 + 反应各一次查，房间作用域检查到位 |
| 7 | GET | `/topics/{topic_id}/tasks` | 可优化 | 默认 `limit=None` 时把房间全部活（自述近 200 条）的对话整份序列化；无房间级上限 |
| 8 | GET | `/topics/{topic_id}/tasks/{task_id}` | 暂无 | 批量件用单元素列表调用，代价定长 |
| 9 | POST | `/topics/{topic_id}/tasks/{task_id}/messages` | 可优化 | `body: dict` 无 schema，无 OpenAPI、无字段级校验 |
| 10 | POST | `/topics/{topic_id}/tasks/{task_id}/title` | 可优化 | 同上（低） |
| 11 | POST | `/topics/{topic_id}/tasks/{task_id}/close` | 暂无 | 用 `ConclusionIn`，有校验；`close_thread` 幂等 |
| 12 | GET | `/topics/{topic_id}/transcript` | 可优化 | `total` 返回的是**本页条数**（`/blocks` 是全会话数），同名字段两种含义 |
| 13 | GET | `/topics/{topic_id}/transcript/{block_id}/output` | 暂无 | 单块读，作用域（房间/非卡/kind=event）三重校验 |
| 14 | GET | `/topics/{topic_id}/usage` | 暂无 | 一次聚合查询 |
| 15 | GET | `/topics/{topic_id}/status` | 可优化 | 验收卡无上限（含历史全部）；`shutil.disk_usage` 同步跑在事件循环上 |
| 16 | GET | `/topics/{topic_id}/children` | 暂无 | 一次查子话题；`total` 语义正确 |
| 17 | GET | `/topics/{topic_id}/docs` | 可优化 | 文档节点无 limit（低） |
| 18 | GET | `/topics/{topic_id}/comments` | 可优化 | 段落评论无 limit，全量返回（低） |
| 19 | POST | `/topics/{topic_id}/comments` | 可优化 | `body: dict` 无 schema；`uuid.UUID(anchor)` 裸抛 ValueError |
| 20 | GET | `/topics/{topic_id}/progress` | 暂无 | 一次读 |
| 21 | PUT | `/topics/{topic_id}/progress` | 暂无 | 用 `ProgressIn`；整表替换天然幂等；commit 在 publish 之前 |
| 22 | GET | `/topics/{topic_id}/doc` | 暂无 | 一次读 |
| 23 | GET | `/topics/{topic_id}/overview` | 暂无 | 一次装配 |
| 24 | PUT | `/topics/{topic_id}/doc` | 暂无 | 乐观并发（`expected_version` → 409）的正面样板 |
| 25 | GET | `/topics/{topic_id}/compute-profile` | 暂无 | 定长若干次查；无 N+1 |
| 26 | POST | `/topics/{topic_id}/sessions/{session_id}/work-lease` | 待确认 | 全文件唯一不走 `ActorResolver` 的路由；`db` 只用于委派，未见本路由写入 |
| 27 | PUT | `/topics/{topic_id}/compute-profile` | 可优化 | handler 单函数约 185 行、8 处函数内 import（代码质量） |
| 28 | POST | `/topics/{topic_id}/messages` | 暂无 | 幂等已用 `request_id` → `publication_id`；`_persist_assistant_message` 自行 commit |
| 29 | POST | `/topics/{topic_id}/ask` | 可优化 | `body: dict` 无 schema |
| 30 | POST | `/topics/{topic_id}/note` | 可优化 | `body: dict` 无 schema |
| 31 | POST | `/topics/{topic_id}/deliveries` | 可优化 | `body: dict` 无 schema；`deliver_at` 内部已 commit |
| 32 | POST | `/topics/{topic_id}/summon` | 暂无 | 只提交轮次，无写；两种情况如实回答 |
| 33 | POST | `/blocks/{block_id}/answer` | 可优化 | `meta` 读改写无行锁，双击/重发会记两次并唤醒房间两轮；无幂等键 |
| 34 | POST | `/topics/{topic_id}/webhook-token` | 可优化 | 每次 POST 都轮换；超时重发会作废调用方刚拿到的那张；无只读路由可查状态 |
| 35 | POST | `/topics/{topic_id}/decision` | 暂无 | `idem.claim` + 显式 commit，模板级写法 |
| 36 | POST | `/topics/{topic_id}/weekly` | 可优化 | 其余逻辑与 `decision` 一致，唯独缺显式 `db.commit()` |
| 37 | POST | `/topics/{topic_id}/title` | 可优化 | `body: dict` 无 schema（低） |
| 38 | POST | `/topics/{topic_id}/title/undo` | 暂无 | 显式 commit；`event_id` 解析失败有 400 |
| 39 | POST | `/topics/{topic_id}/read` | 可优化 | 依赖依赖树 teardown 提交，响应先于落库（低） |
| 40 | POST | `/topics/{topic_id}/archive` | 可优化 | 同上；且持有 `FOR UPDATE` 到 teardown 才提交（低） |
| 41 | GET | `/topics/{topic_id}/cleanup` | 暂无 | 一次读；`enforce=True` |
| 42 | POST | `/topics/{topic_id}/unarchive` | 可优化 | 同 #40（低） |
| 43 | POST | `/topics/{topic_id}/split` | 暂无 | `idem.claim` 已覆盖重发 |
| 44 | POST | `/topics/{topic_id}/tasks/{task_id}/check-result` | 暂无 | 显式 commit；`CheckResultIn` 有校验 |
| 45 | POST | `/topics/{topic_id}/lock` | 暂无 | 不等待语义明确；`LockIn` 有校验 |
| 46 | POST | `/topics/{topic_id}/unlock` | 暂无 | 同上 |
| 47 | POST | `/topics/{topic_id}/clone-from` | 暂无 | 对来源与目标**两端**分别授权，正确 |
| 48 | POST | `/topics/{topic_id}/tell` | 暂无 | `RelayIn` 有校验；显式 commit |
| 49 | POST | `/topics/{topic_id}/shown` | 可优化 | WS 帧在 commit **之前**广播（该 bug 在 `create_topic` 已修过一处） |
| 50 | GET | `/topics/{topic_id}/shown` | 可优化 | 无 limit；把房间**全部历史** artifact 载入 Python 再去重 |
| 51 | POST | `/topics/{topic_id}/shown/save` | 暂无 | 要求已验证的人（凭据过不了则 403），符合注释里说的「只有人能按」 |
| 52 | POST | `/topics/{topic_id}/documents/recalc` | 可优化 | `_document_bytes` 同步读盘 / base64 在事件循环上（低） |
| 53 | POST | `/topics/{topic_id}/documents/convert` | 可优化 | 同上（低） |
| 54 | GET | `/topics/{topic_id}/documents/revisions` | 可优化 | `revisions_in` 同步解 docx（zip+XML）阻塞事件循环 |
| 55 | POST | `/topics/{topic_id}/documents/revisions` | 可优化 | `decide` 同样是同步解析；`version` 冲突返回 409 且带 `data.version`，好 |
| 56 | GET | `/topics/{topic_id}/preview` | 暂无 | 已用 `asyncio.to_thread` 卸载阻塞调用，是本文件的正面样板 |
| 57 | GET | `/topics/{topic_id}/preview/file` | 可优化 | `library.read_*` 同步读盘在事件循环上（低） |
| 58 | POST | `/topics/{topic_id}/attachments` | 可优化 | 同步写资料库；`resolve` 走两遍（低） |
| 59 | GET | `/topics/{topic_id}/attachments/raw` | 可优化 | docstring 声称「扩展名白名单，绝不吐出可执行 HTML」，但 `download=1` 整条绕过白名单（低，见正文） |
| 60 | GET | `/topics/{topic_id}/attachments/pdf` | 暂无 | 渲染是 async；响应头（nosniff / CSP / Cache-Control）齐备 |
| 61 | GET | `/projects/{project_id}/topic-unread` | 暂无 | 一次聚合；收件人来自凭据而非查询串 |
| 62 | GET | `/projects/{project_id}/private-unread` | 暂无 | 同上 |
| 63 | POST | `/blocks/{block_id}/upgrade` | 暂无 | 房间由 block 自己带，不由请求体说；`created=False` 时不重复落事件 |

## 二、详细分析（按收益从高到低）

### GET `/topics`

- **现状**：`topics.py:385-456`。`TopicService.list_for_project`（`domain/topic/services.py:452-482`）先由 `list_for_project_with_activity`（`domain/topic/repositories.py:186-210`）把项目**全部**话题连同 `_last_activity()` 取回，随后在 `active_since is None` 时再发一条 `count_for_project`（同文件 `311-317`）。
- **问题**：
  1. **无分页**。`ok(page(items, total))` 把项目所有话题一次返回。签名里只有 `sort/order/active_since/topic`，没有 `limit`/`cursor`。参考量级不是猜的：`room_task/repositories.py:82-84` 自己写着项目「already holds ~170 rooms」，而每行还要付侧栏的派生代价（`relevance_for_topics` 4 条 + `_live_cards` 1 条 + `managed_topic_ids` 1 条 + `asked` 1 条 + `_rooms_with_running_work` 3 条 + `waiting`/`failed`）——这些是**批量**件（好），但整份响应没有上界，且这是「打开任一项目」必走的热路径。
  2. **`total` 是一次与结果同源的冗余查询**。两个分支用的是同一个 `_project_topics_stmt` 谓词（`repositories.py:165-184`），`count_for_project` 只是把 `is_private IS FALSE` 再数一遍；`active_since is None` 时 `total == len(topics)` 由构造成立。
- **优化**：
  ```python
  # domain/topic/services.py:472-482 —— 同一个谓词取回的行就是总数，不必再问一次
  rows = await self._repo.list_for_project_with_activity(
      project_id, sort=sort, order=order, active_since=_as_utc(active_since)
  )
  topics = [topic for topic, _ in rows]
  last_activity = {topic.id: last for topic, last in rows}
  # ← 改动点：两种情况下 total 都是已经拿到手的这批行数。
  #   active_since 给过时它是「筛过的这批」（调用方要的就是这个），
  #   没给时它等于 count_for_project（同一个 _project_topics_stmt 谓词）。
  total = len(topics)
  return topics, last_activity, total
  ```
  分页则按 `/blocks` 已有的一套**加法**做（不改默认行为，Agent 读历史不受影响）：
  ```python
  # routes/topics.py:list_topics 签名加两个可选参数
  limit: Annotated[int | None, Query(ge=1, le=500)] = None,
  before: uuid.UUID | None = None,   # 游标 = 上一页最后一行的 topic id
  ```
  ```python
  # domain/topic/repositories.py:_project_topics_stmt 增加游标分支
  if before is not None:
      cursor = await self._session.get(Topic, before)
      if cursor is None or cursor.project_id != project_id:
          raise NotFoundError("游标话题不存在")
      # 与 ORDER BY 用同一个键（created_at, id），理由与 BlockRepository.page_for_topic
      # 完全一致：单列 created_at 不是全序，同一瞬间建的话题会漏或重。
      stmt = stmt.where(tuple_(Topic.created_at, Topic.id) > (cursor.created_at, cursor.id))
  ```
- **契约**：`total` 的**值**不变（`test_topic_sort.py` / 现有客户端不受影响）；新增 `limit`/`before`/`has_more` 是纯加法，不传就与今天逐字节相同。无数据迁移。
- **测试**：`backend/tests/integration/test_topic_pagination.py`
  - `test_no_limit_still_returns_every_topic_and_the_same_total`（守护默认行为）
  - `test_walking_before_partitions_the_project_exactly`（与 `test_block_paging.py:77` 同构）
  - `test_topic_list_does_not_run_a_count_query`（monkeypatch `TopicRepository.count_for_project` 令其抛错，断言仍然 200）

### GET `/topics/{topic_id}/tasks`

- **现状**：`topics.py:698-791`。`TaskService.threads_for_room(topic_id, limit=limit)`（`domain/room_task/services.py:262-285`）——`limit` 只管**每条活**取最新 N 块，`limit=None`（默认）时 `blocks` 是整条对话；`conversations_for_tasks`（`repositories.py:153-180`）一次取回房间**全部**活的**全部** block。响应为 `page(items, len(items))`，`total` 是活条数，没有 `has_more`，也没有任何房间级上限（签名里只有 `limit`）。
- **问题**：`limit` 的缺失不会截断，于是整份「房间历史」一次性序列化：活数 × 每条活的块数。函数 docstring 自己给出了规模——「a long-lived room already holds close to two hundred of them」。200 条活、每条 200 块就是约 4 万个 `BlockOut.model_dump`，且每个 block 的 `meta` 会整份进 JSON。这是本文件里单次响应最大的一条路。
- **优化**：不改 `limit` 的语义（它讲的是每条活），给**房间这一层**加一个默认上限，并把「我全要」变成显式的：
  ```python
  # routes/topics.py:list_room_tasks —— 新增一个显式开关，默认给出上界
  threads_default = 50          # 与 chat 里 limit 的 50 同量级，见 /history
  @router.get("/{topic_id}/tasks")
  async def list_room_tasks(
      topic_id: uuid.UUID,
      db: DbSession,
      chat: Annotated[ChatService, Depends(get_chat_service)],
      resolver: ActorResolverDep,
      limit: Annotated[int | None, Query(ge=1, le=500)] = None,
      threads_limit: Annotated[int, Query(ge=1, le=500)] = threads_default,
      # ← 改动点：Agent 读整段历史时显式要全部（等价于今天的默认）
      all_threads: bool = False,
  ) -> dict:
      ...
      threads = await TaskService(db).threads_for_room(
          topic_id, limit=limit, threads_limit=None if all_threads else threads_limit
      )
      ...
      thread_total = await TaskRepository(db).count_for_room(topic_id)
      return ok({**page(items, thread_total), "has_more": thread_total > len(items)})
  ```
  ```python
  # domain/room_task/services.py:threads_for_room —— 顺带把上限做在 SQL 层，
  # 否则「最新的 50 条活」仍然要在内存里排序整张 tasks 表
  tasks = await self._repo.list_for_room(room_id, limit=threads_limit)  # newest-first when limited
  ```
- **契约**：**会改**。不给 `threads_limit`/`all_threads` 的调用方今天拿到全部活，改后拿到最新 50 条。上线前必须确认客户端（房间总览、`cheese` CLI）是否依赖「全部」。`total` 从「本页条数」变成真实活数——这正是要修的方向（见 #12）。建议分两步：先只加 `thread_total`/`has_more` 与 SQL 层 `limit`（不改默认），把客户端切到显式参数后再收紧默认。
- **测试**：`backend/tests/integration/test_room_tasks_limits.py`
  - `test_all_threads_flag_still_returns_every_thread`
  - `test_threads_limit_caps_the_room_and_reports_has_more`
  - `test_thread_limit_is_applied_per_thread`（现有语义不许回归）

### POST `/topics/{topic_id}/shown`

- **现状**：`topics.py:3080-3150` → `record_shown`（`3054-3077`）：`BlockRepository.add`（flush）→ 拼 payload → `await get_broker().publish(...)`（`3074`）→ 返回。**整条路径没有任何 `db.commit()`**。
- **问题**：WS 帧在**写提交之前**就发出去了。提交发生在 `get_db` 的 teardown（`core/db.py:171-179`，`yield` 之后），也就是响应送出之后。收到帧就回读的客户端（房间列表、`/preview`、`/shown`）会和这次提交赛跑，可能读到旧状态。这不是推测：`create_topic` 里有一段专门为同一个 bug 写的注释与修复（`topics.py:189-191`「Dependency teardown commits after the response, which races that request.」），而本文件里另外 6 条广播路径（`say_on_task:937`、`write_topic_progress:1486`、`ask_options:2115`、`edit_topic_doc:1624`、`answer_options:2305`、`tell_topic:2895`）都是先 commit 再 publish。`record_shown` 是唯一的例外。
- **优化**：把广播从 `record_shown` 里挪出来，或者给它一个开关，由调用方在 commit 之后广播：
  ```python
  # routes/topics.py:record_shown —— 加一个 publish 开关（默认保持兼容）
  async def record_shown(
      db: AsyncSession, place: Place, path: str, *, author: str,
      mime: str | None = None, publish: bool = True,
  ) -> dict:
      block = await BlockRepository(db).add(...)
      payload = BlockOut.model_validate(block).model_dump(mode="json")
      if publish:
          await get_broker().publish(
              str(place.room_id), {"type": "assistant_block", "block": payload}
          )
      return payload
  ```
  ```python
  # routes/topics.py:show_in_room 末尾 —— 先落库，再广播
  payload = await record_shown(db, place, path, author=author, mime=mime, publish=False)
  await db.commit()                                   # ← 改动点：顺序是这一条的意义
  await get_broker().publish(
      str(place.room_id), {"type": "assistant_block", "block": payload}
  )
  return ok(payload)
  ```
  `room_files.py:190,235,376` 三处调用点要一并核对同样的顺序。
- **契约**：不变（请求/响应形状一致，只是帧更晚一点、内容相同）。
- **测试**：`backend/tests/unit/test_shown_publish_after_commit.py`（照 `tests/unit/test_topic_creation_commit.py` 的假 session 写法：`commit` 里记序、`publish` 里断言 `committed is True`），以及 `tests/integration/test_shown_route.py::test_frame_follows_commit`。

### POST `/blocks/{block_id}/answer`

- **现状**：`topics.py:2264-2334`。`blk = await repo.get(block_id)`（`2278`）→ `BlockRepository.get` 就是 `session.get`（`domain/block/repositories.py:149-150`），**无行锁**；随后 `meta.get("answered")` 判重（`2296`）→ 写 `meta["answered"]`/`["answered_by"]`（`2300-2303`）→ `broker.receive_message(...)`（`2327`）唤醒一轮。
- **问题**：读—判—写三步之间没有锁，两个并发请求（双击、或超时后的重发）都会把 `answered` 读成空，于是**都通过判重**：`meta` 后写覆盖先写（`answered_by` 可能记成输的那一方），而 `receive_message` 被调两次——一道题答一次，房间被叫起来跑两轮。本路由也没有幂等键，而 `/decision`（`2386-2392`）和 `/weekly`（`2465-2471`）都有 `idem.claim`，说明这套机制在这里是现成可用的。
- **优化**：判重前先取行锁（主键单行锁，代价是一次 `SELECT ... FOR UPDATE`，写只有一条 `UPDATE`）：
  ```python
  # routes/topics.py:answer_options —— 把 repo.get 换成带锁的读
  from sqlalchemy import select
  from app.domain.block.models import Block

  blk = (
      await db.scalars(
          select(Block).where(Block.id == block_id).with_for_update()
      )
  ).one_or_none()
  if blk is None:
      raise NotFoundError("问题不存在")
  ```
  （`BlockRepository.get` 保持不动，因为带锁读是这条路由的**语义要求**，不是所有读都该付的代价；也可以照 `TaskService.claim_for_pr` 的样子在 `BlockRepository` 上加一个 `get_for_update`。）
  再加一层与 `/messages` 同形的幂等：`answer_options` 接受可选 `Idempotency-Key` 头，`idem.claim(db, f"answer:{block_id}:{key}", ...)`，重复的 `receive_message` 不再发生。
- **契约**：请求体不变（新增可选头是加法）；响应不变。无迁移。
- **测试**：
  - `backend/tests/integration/test_block_answer_once.py::test_concurrent_answers_record_exactly_one`（两路 `asyncio.gather` 并发 POST，断言 `meta.answered_by` 只有一方、且只起了一轮）
  - `::test_second_answer_after_the_first_is_rejected`（现有 400 语义不许回归）

### GET `/topics/{topic_id}/transcript`

- **现状**：`topics.py:1042-1104`。两个分支最后都是 `page(items, len(items))`（`1100`）→ `total` = **本页行数**。而 `limit=None` 时是 `[b for b in await repo.list_for_topic(...) if b.kind in kinds]`（`1082`）——把整条时间线载入 Python 再筛。
- **问题**：
  1. **同名字段两种含义**。`/blocks`（`topics.py:577`）的 `total` 是 `count_for_topic`＝会话总长；这里是本页条数。一个客户端同时读两个面板，`total` 对同一个词给出两个数，「共 N 条」无从显示。GitHub 的 list 端点里 `total_count`（如 `GET /repos/{o}/{r}/issues` 的 search 形态）与 `per_page` 是两个字段，不会让同一个名字指两种东西。
  2. **默认路径全量载入**。`limit=None`（默认）把整条时间线读回来，再在 Python 里只留 `kind == event`；现场事件是**最多**的一类 block（docstring 自己写的：一次工具调用一条），而 DB 侧本可以只取那一种。`page_for_topic` 已经有 `kinds` 参数（`repositories.py:943-946`），`list_for_topic` 没有。
- **优化**：
  ```python
  # domain/block/repositories.py:count_for_topic —— 让计数与取页用同一个 kinds 参数
  async def count_for_topic(
      self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None,
      kinds: Collection[BlockKind] | None = None,
  ) -> int:
      stmt = select(func.count()).select_from(Block).where(*self._in_place(topic_id, task_id))
      if kinds is not None:
          stmt = stmt.where(Block.kind.in_(list(kinds)))
      else:
          stmt = stmt.where(Block.kind.not_in(self._NON_TIMELINE))
      return int((await self._session.scalar(stmt)) or 0)
  ```
  ```python
  # domain/block/repositories.py:list_for_topic —— 同样接受 kinds，让 DB 去做筛选
  async def list_for_topic(self, topic_id, *, task_id=None, kinds=None) -> list[Block]:
      stmt = select(Block).where(*self._in_place(topic_id, task_id))
      stmt = (stmt.where(Block.kind.in_(list(kinds))) if kinds is not None
              else stmt.where(Block.kind.not_in(self._NON_TIMELINE)))
      return list((await self._session.scalars(stmt.order_by(Block.created_at, Block.id))).all())
  ```
  ```python
  # routes/topics.py:topic_transcript —— total 是现场总数，筛在库里做
  if limit is None:
      site = await repo.list_for_topic(place.room_id, kinds=kinds)
      has_more = False
  else:
      result = await repo.page_for_topic(place.room_id, limit=limit, before=cursor, kinds=kinds)
      site, has_more = result.items, result.has_more
  total = await repo.count_for_topic(place.room_id, kinds=kinds)   # ← 改动点
  ```
- **契约**：`total` 的值**变了**（本页条数 → 现场事件总数）。这是修语义错误，但要让前端确认没有依赖旧值；`/blocks` 与 `/transcript` 从此同义。无迁移。
- **测试**：`backend/tests/integration/test_transcript_paging.py`
  - `test_total_is_the_whole_transcript_not_the_page`
  - `test_no_limit_filters_events_in_the_database`（monkeypatch `BlockRepository.list_for_topic`，断言收到 `kinds={BlockKind.event}`）

### GET `/topics/{topic_id}/blocks`

- **现状**：`topics.py:523-595`。无 `limit`（默认）时 `repo.list_for_topic(place.room_id)`（`564`）之后再 `repo.count_for_topic(topic_id)`（`577`）。
- **问题**：`count_for_topic`（`repositories.py:994-1005`）的谓词与 `list_for_topic`（`802-822`）**逐字相同**：都是 `_in_place(topic_id, None)` 加 `kind NOT IN _NON_TIMELINE`。无游标时 `total == len(blocks)` 由构造成立，这次 COUNT 是对同一批 block 的第二次全扫。规模有现成数字：`tests/integration/test_block_paging.py:3-6` 记着「2.1 MB / 2226 rows on a real topic」——默认路径每次多扫 2226 行。另有 `limit=None` + `before=<cursor>` 的组合（`565-570`）：整条时间线进内存再按 Python 比大小，一次翻页付 O(n) 内存。
- **优化**：
  ```python
  # routes/topics.py:563-577 —— 只有「筛过」的那一支才真的需要总数
  if limit is None:
      blocks = await repo.list_for_topic(place.room_id)
      if cursor is not None:
          blocks = [b for b in blocks if (b.created_at, b.id) < (cursor.created_at, cursor.id)]
          total = await repo.count_for_topic(topic_id)
      else:
          total = len(blocks)          # ← 改动点：全部行已在手，不必再 COUNT
      has_more = False
  else:
      page_result = await repo.page_for_topic(place.room_id, limit=limit, before=cursor)
      blocks, has_more = page_result.items, page_result.has_more
      total = await repo.count_for_topic(topic_id)
  ```
- **契约**：不变。`total` 的值两种算法相同——`test_block_paging.py:54` 的 `assert payload["total"] == 12` 仍成立。无迁移。
- **测试**：`backend/tests/integration/test_block_paging.py` 追加 `test_no_limit_does_not_run_a_second_count`（monkeypatch `BlockRepository.count_for_topic` 抛错，断言仍 200 且 `total` 正确）。

### POST `/topics/{topic_id}/webhook-token`

- **现状**：`topics.py:2337-2363`。每次调用 `webhook_service.mint`（`domain/webhook/service.py:30-39`）→ `WebhookTokenRepository.bump_version`（`repositories.py:25-64`）用 `ON CONFLICT DO UPDATE SET version = version + 1`，**无条件自增**。
- **问题**：`bump_version` 的语义是「铸新的或轮换」——同一个房间再 POST 一次，之前发出去的每一张 token 立刻失效（`verify` 比的是 `version`，`service.py:42-47`）。而这条路由没有幂等键、也没有只读入口：客户端超时重发（提交在 teardown，响应之后，窗口真实存在）会得到一张新 token，同时把它刚刚存下的那张废掉；响应体又是这张 secret 唯一出现的地方，丢一次响应这个房间的凭据就取不回来了，只能再轮换一次。仓库里 `current_version`（`repositories.py:66-70`）**没有任何路由调用**，也就是说「这里到底有没有凭据」在 API 上问不出来。
- **优化**：两件加法。
  1. 幂等：接受可选 `Idempotency-Key` 头，用现成的 `idem.claim`/`record_result`（`app/domain/idempotency/store.py`），把铸出来的 token 记进结果，重发拿回**同一张**（注意：`WebhookToken` 表只存 `version` 计数器、不存 secret，所以「重放返回同一张」只能靠把 token 存进 `idempotency_keys.result`——这是一个需要一并同意的取舍：secret 会短暂落在另一张表里，随 key 一起被清理）。
  2. 只读状态：`GET /topics/{topic_id}/webhook-token` → `{"exists": bool, "version": int, "created_at": ..., "updated_at": ...}`，**永不返回 secret**，让客户端能区分「还没有」与「有」而不必轮换。
- **契约**：新增路由与可选头，均为加法；`POST` 的响应体不变。无迁移（`webhook_tokens` 表已有 `Timestamps`）。
- **测试**：`backend/tests/integration/test_webhook_token_mint.py::test_replay_with_the_same_idempotency_key_does_not_rotate`、`::test_get_reports_existence_without_the_secret`。

### GET `/topics/{topic_id}/status`

- **现状**：`topics.py:1190-1238`。`cards = await AcceptCardRepository(db).list_for_topic(topic_id)`（`1211`）——`review/repositories.py:102-116`：`WHERE topic_id = ? ORDER BY created_at DESC`，**无状态过滤、无 limit**；每张卡经 `_card_snapshot`（`1157-1174`）序列化，含 `gate_output[-2000:]`。`_disk_snapshot`（`1177-1187`）里的 `shutil.disk_usage` 是阻塞 `statvfs`，直接写在 `async def` 里（`1231`）。
- **问题**：这条路由自称「one snapshot of what is going on」，但「这个房间历来递过的全部验收卡」不是当下快照——一个递过 300 次卡的项目把 300 份 `_card_snapshot`（每份最多 2 KB gate 输出）塞进一次响应，而调用方是 `cheese status` 和排障的人，要的是**现在**。另外阻塞调用跑在事件循环上；本文件 `3473` 已经用 `asyncio.to_thread` 卸载了同类调用（`library.preview_file_version`），说明这条规矩在仓库里是清楚的。
- **优化**：
  ```python
  # domain/review/repositories.py:list_for_topic —— 加 limit
  async def list_for_topic(self, topic_id: uuid.UUID, *, limit: int | None = None) -> list[AcceptCard]:
      stmt = select(AcceptCard).where(AcceptCard.topic_id == topic_id).order_by(AcceptCard.created_at.desc())
      if limit is not None:
          stmt = stmt.limit(limit)
      return list((await self._session.scalars(stmt)).all())
  ```
  ```python
  # routes/topics.py:topic_status
  cards = await AcceptCardRepository(db).list_for_topic(topic_id, limit=20)   # ← 改动点
  ...
  "disk": await asyncio.to_thread(_disk_snapshot, settings.workspace_root),   # ← 改动点
  ```
- **契约**：`cards` 变短——需客户端确认（它本就是「最近若干张」的读法）。若要不改响应，就新增 `cards_limit`（默认给出上限）与 `cards_total`。无迁移。
- **测试**：`backend/tests/integration/test_topic_status_cards_bounded.py::test_status_caps_the_card_list`、`tests/unit/test_status_disk_offloaded.py`（断言 `shutil.disk_usage` 在线程里被调用）。

### POST `/topics/{topic_id}/weekly`

- **现状**：`topics.py:2438-2491`。与 `record_decision`（`2366-2413`）逐段同构：`idem.claim` → `BlockRepository.add` → `idem.record_result` → `return ok(out)`。区别只在一处：`record_decision` 有 `await db.commit()`（`2411`），`record_weekly` **没有**。
- **问题**：两条路由的幂等键、块写入、结果回填都在**调用方会话**上（`idem` 的 docstring 明确：「Both statements run on the CALLER's session, so the key and the effect commit together」）。`record_weekly` 没有显式提交，于是 `idem.claim` 抢到的键、写下的周报块、回填的结果一起等 `get_db` 的 teardown——响应已经发出去之后才落库。同一个模块里两条孪生路由一个提交一个不提交，是维护陷阱：之后谁按 `record_weekly` 的样子抄一条新路由，就会把「响应说成功、库里还没有」也一起抄走。
- **优化**：照 `record_decision` 补齐（`announce_stale` 也一并，若语义需要）：
  ```python
  # routes/topics.py:record_weekly —— 与 record_decision:2409-2412 对齐
  out = BlockOut.model_validate(block).model_dump(mode="json")
  if key is not None:
      await idem.record_result(db, key, out)
  await db.commit()          # ← 改动点：幂等键与效果一起提交，响应才说得上是事实
  return ok(out)
  ```
- **契约**：不变。
- **测试**：`backend/tests/unit/test_weekly_response_follows_commit.py`（照 `tests/unit/test_topic_creation_commit.py` 的假 session，`commit_fails=True` 时断言不返回 `ok`）。

### GET `/topics/{topic_id}/history`

- **现状**：`topics.py:607-678`。响应是 `{data, has_more, oldest_id, newest_id, direction, reply_to}`（`669-678`）——**没有 `total`**，而同族的 `/blocks` 有。搜索参数 `q` 走 `page_for_topic` 的三段 `ILIKE`（`repositories.py:947-961`）：`content`、`meta::text`、`anchor_quote`。
- **问题**：
  1. **分页协议在同族两条路上不一致**：`/blocks` 给 `total`，`/history` 不给；`/history` 多给 `newest_id`/`direction`。客户端要写两套读取逻辑判断「还有多少」。GitHub 的做法是每页响应都带同样的 `Link` 头（`rel="next"`/`rel="prev"`）而不把总数塞进 body——本仓库没有用 Link，那就至少要让同族的字段名一致。
  2. `q` 的三段 `ILIKE '%…%'` 都无法用索引；`cast(meta AS text) ILIKE` 还要把每一行的 JSONB 渲染成文本再匹配。作用域被 `topic_id` 限定（`ix_blocks_topic_id_created_at`），所以扫描量是一个房间的块数，不是全表——这是可接受的，但如果搜索要长期保留且有体积增长，值得记一笔。
- **优化**：
  ```python
  # routes/topics.py:read_chat_history —— 补 total，与 /blocks 同名同义
  return ok(
      {
          "data": [serialized[b.id] for b in result.items],
          "total": await repo.count_for_topic(
              place.room_id, task_id=task_id, kinds=[kind] if kind else [...]
          ),
          "has_more": result.has_more,
          ...
      }
  )
  ```
  （`count_for_topic` 需按上面的 #12 接受 `kinds`；`q`/`reply_to`/`author` 也要一并进谓词，否则总数与筛选后的一组不同源。更稳的做法是给 `page_for_topic` 加一个 `with_total=True`，让取页与计数共用同一段 `where` 构造，避免两处谓词漂移——这正是 `_project_topics_stmt` 存在的理由。）
- **契约**：新增 `total`，加法。无迁移。
- **测试**：`backend/tests/integration/test_chat_history_paging.py::test_total_counts_the_filtered_set`。

### 无 schema 的请求体（`body: dict`）

本文件有 13 条写路由收 `body: dict`，却与并列的 `ChatPublishIn`/`ProgressIn`/`ConclusionIn` 等在同一份文件里共存。这条不单独展开——受影响接口清单、示范代码与理由都在第三节「统一请求体 schema」。此处只登记结论（对应第 9、10、19、27、29、30、31、33、37、39、40、42、49 条为「可优化」的直接原因）。

## 三、模块级建议（跨接口）

### 1. 分页：一条文件里三套协议

**适用接口**：`/topics`(#2)、`/blocks`(#4)、`/history`(#5)、`/transcript`(#12)、`/tasks`(#7)、`/children`(#16)、`/docs`(#17)、`/comments`(#18)、`/shown`(#50)。

**现状**（都是事实，可直接核对）：`/blocks` = `{data, total, has_more, oldest_id}`；`/history` = `{data, has_more, oldest_id, newest_id, direction, reply_to}`（无 `total`）；`/transcript` = `{data, total(=本页), has_more, oldest_id}`；`/tasks` = `{data, total(=活数)}`（无 `has_more`）；`/children`、`/docs`、`/comments`、`/shown` = `{data, total(=len(items))}`；`/topics` = `{data, total}`（无分页参数）。

**做法**：把 `/blocks` 已经写好的一套（`repositories.py:910-992`，`(created_at, id)` 全序游标 + 多取一行判 `has_more`）作为**唯一**协议，其它 list 端点照抄同一个 `has_more` / `oldest_id` / `newest_id` / `total` 字段集合；`total` 全局统一为「筛选条件下的总数」，不再有「本页条数」这一种。参数名统一为 `limit` + `before`（向后翻）/`after`（向前翻），与 `/blocks`、`/history` 现名一致。

**为什么**：GitHub 每个 list 端点都用同一组 `per_page` / `page`（或 `before`/`after`）与同一个 `link` 头，`rel` 取值固定为 `next`/`prev`/`first`/`last`，客户端因此可以写一份通用翻页器（https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api）。它同时明确两条我们今天没有的规矩：**默认就有上界**（示例端点默认 30，多数端点上限 100，超过上限不报错而是静默收缩到上限），以及**上限是端点的契约**——这两条正好对应本文件 `/topics` 与 `/tasks` 的现状。业界通行（未逐条查证）：响应体里放 `has_more` + 游标、而不把总数塞进 body，是因为 `COUNT(*)` 往往比取一页贵（本文件 `/blocks` 的默认路径正是这个代价，见第二节）。

### 2. 统一请求体 schema：`body: dict` 是无声的 API

**适用接口**（本文件内，共 13 条）：#9 `messages`、#10 `tasks/{id}/title`、#19 `comments`、#29 `ask`、#30 `note`、#31 `deliveries`、#37 `title`、#39 `read`、#40 `archive`、#42 `unarchive`、#49 `shown`、#27 `compute-profile`、#33 `answer`。

**做法**：每条路由换成 `BaseModel`，标上 `Field(min_length=…, max_length=…)` 与 `Literal`（本文件已有 `ChatPublishIn`(#28)、`ProgressIn`(#21)、`ConclusionIn`(#11)、`CheckResultIn`(#44)、`LockIn`(#45)、`RelayIn`(#48)、`SplitIn`(#43)、`DocEditIn`(#24)、`UpgradeBlockIn`(#63) 可以照抄）。示范（#9，`topics.py:864-873`）：

```python
class TaskMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)   # 与 ChatPublishIn 同一上限
    author: str | None = None                                # 只作登录后的断言，见 api/auth.py

@router.post("/{topic_id}/tasks/{task_id}/messages")
async def say_on_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskMessageIn,
    db: DbSession,
    ...
) -> dict:
    content = body.content.strip()
    if not content:
        raise ValidationError("消息内容不能为空")
```

**为什么**：
- 现在这些字段**没有 max_length**：`say_on_task` 的 `content`、`ask_options` 的 `question`（只查非空）、`add_comment` 的 `content` 都只做 `.strip()`，长度由数据库列宽兜底；而并列的 `ChatPublishIn` 明确写了 `max_length=100000`。这是同一份文件里同一类输入的两种待遇。
- `dict` 骨架不产生 OpenAPI 请求体定义，客户端（本仓库的 `frontend/`、`cli/`）只能读源码；`GET /openapi.json` 上这些路由的 `requestBody` 是空的。
- 业界通行（未逐条查证）：Stripe / Slack / GitHub 的公开 API 对每个字段都给出类型与边界，越界返回 4xx 而不是静默截断或 500。
- 顺带修掉同类小洞：`add_comment`（`1308`）的 `uuid.UUID(anchor)` 对畸形输入抛未捕获的 `ValueError`（→ 500），而同样的事在 `undo_title`（`2555-2557`）和 `clone_topic_from`（`2841-2844`）都收到了 `ValidationError`。

### 3. 幂等：只有三条路有，其余 20 条写路由没有

**适用接口**：有幂等的——#28（`request_id`）、#35 `decision`、#36 `weekly`、#43 `split`、#63 `upgrade`（`created` 标志）、#24/#55（`expected_version`）。**没有的**——#1 `POST /topics`、#9、#10、#11、#19、#29、#30、#31、#33、#34、#37、#39、#40、#42、#45、#46、#47、#49、#51、#52、#53、#58。

**做法**：把 `app/domain/idempotency` 这套（`claim`→做→`record_result`，语义已在 `store.py` 写清）提到信任边界，用**请求头** `Idempotency-Key` 统一暴露，而不是每条路由各挑一个 body 字段（今天 `/messages` 用 body 里的 `request_id`，`/split` 用轮次续传 key，两种来源）。`POST /topics` 是最该有的一个：一次超时重发就是多一间房，而多出来的那间房没有 idempotency 可依、事后也认不出哪间是重复的。

**为什么**：Stripe 用 `Idempotency-Key` 头，键建议用 v4 UUID，保留 24 小时，且**同键不同参数会报错**而不是静默返回旧结果——最后这条正好补上本仓库 `idem.stored_result` 目前不回比参数的缺口（https://docs.stripe.com/api/idempotent_requests）。用头而不是 body 字段的理由是可以被一层中间件统一处理，新加路由不会忘记。

### 4. ETag / 条件请求：`app/api/conditional.py` 已经写好了，主题读接口一个没用

**适用接口**：#3 `get_topic`、#4 `blocks`、#5 `history`、#7 `tasks`、#8 `task`、#14 `usage`、#16 `children`、#17 `docs`、#18 `comments`、#22 `doc`、#23 `overview`、#50 `shown`、#54 `revisions`、#56 `preview`。这些正是「轮询着看」的读（房间头、对话、现场、进度）。

**做法**：`etag_for_json(data)` 已经存在（`app/api/conditional.py:42-58`），并且已有两处用户可照抄：`routes/avatars.py:96` 与 `routes/admin_members.py:59-64`（后者的写法就是「算 etag → 比对 `If-None-Match` → 命中返回 304」）。给 `GET /topics/{topic_id}` 和 `GET /topics/{topic_id}/blocks` 加上同样的三行即可：

```python
from fastapi import Header
from app.api.conditional import etag_for_json, if_none_match_hits
from fastapi.responses import Response

@router.get("/{topic_id}")
async def get_topic(
    topic_id: uuid.UUID,
    db: DbSession,
    ...,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response | dict:
    ...
    body = ok(_topic_out(...))
    etag = etag_for_json(body["data"])
    if if_none_match and if_none_match_hits(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})
    return JSONResponse(body, headers={"ETag": f'"{etag}"'})
```

**为什么**：GitHub 明确把条件请求列为最佳实践，并给出两个我们缺的好处——「响应没变就是 304」和「正确的条件请求**不计入主速率上限**」，后者对「房间头 + 对话 + 现场」这种轮询是直接的省额（https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api）。同一页也说清了什么时候不能用：`POST`/`PUT`/`PATCH`/`DELETE` 不支持条件请求——所以写侧的并发控制在这里仍然是 `expected_version` 那一套（#24 已经是正面样板），不要混用。

### 5. 速率限制：写接口没有看到任何限流

**适用接口**：全部写接口，尤其 #19 `comments`、#29 `ask`、#9 `messages`（每条消息都走）、#34 `webhook-token`、#33 `answer`（一次点选会起一轮，直接花钱）。

**做法**：先确认平台是否已有统一中间件（本文件范围内看不到任何 `slowapi`/`limits`/429 相关代码，`app/core/errors.py` 定义了 `HTTP_429_TOO_MANY_REQUESTS` 但本文件没有一处抛它）；若有，只需在这些路由上标限额。**若没有**，`POST /topics/{id}/ask`、`/messages`、`/answer` 是第一批要限的——它们各自会启动或唤醒一整轮模型计算。

**为什么**：GitHub 为每个集成给出主/次速率上限，并说明继续在被限时打请求会被封禁（同 best-practices 页）；429 与 `Retry-After` 是客户端能自动退避的唯一依据。本仓库 `errors.py` 已经备好 429 状态码，缺的是有人用它。

### 6. 错误格式与响应信封：统一，但 `warnings` 只出现在一条路上

**现状**：错误统一为 `{"code", "message", "error": {"name","message","data","retryable"}}`（`app/core/errors.py:BaseError.to_response_body`），成功统一为 `{"code", "message", "data"}`（`app/api/response.py:ok`）。这是项目自行规定的信封，**不是** GitHub 的 `message`/`documentation_url`，也不是 RFC 7807 `application/problem+json`——但它在本文件 63 个接口上是一致执行的，不建议为「像 GitHub」而改动（那是一次全平台契约迁移，不是本模块能决定的）。可指出的两处不对称：
- `warnings` 只由 `edit_topic_doc`（`topics.py:1649-1652`）返回，`ok()` 支持它（`response.py:6-18`）却没有第二条路用它。若 `living_doc_warnings` 的判据对其它写入也成立（比如 `progress`、`decision`），应一并用；若不成立，把它写进注释说明只属于文档。
- `ConflictError` 在写侧已被用作「乐观并发」的标准答复（#24 `expected_version`、#55 `version` + `data={"path","version"}`），但读侧没有任何 `ETag`/`If-None-Match`（见第 4 条），于是「上次看到的那一版」在读写两侧的语言不一样。

### 7. 提交边界：7 条写路由依赖依赖树 teardown 提交

**适用接口**：#39 `read`、#40 `archive`、#42 `unarchive`、#36 `weekly`、#49 `shown`、#27 `compute-profile`（后者 `request_choice` 内部已提交，故只影响返回体里的其余字段）、#26 `work-lease`（委派方提交）。

**现状**：这些路由不写 `await db.commit()`，提交发生在 `get_db` 的 teardown（`core/db.py:171-179`）。而同模块的 6 条广播路径与 `create_topic` 都显式提交，`create_topic` 的注释还把「响应先于提交」当成一个已修过的 bug 记下来了（`topics.py:189-191`）。

**做法**：要么统一显式 `await db.commit()`（与同模块多数路由一致），要么在 `get_db` 上写明这是受支持的行为并删掉各处的显式提交——但不要两种并存。#40 `archive_topic` 还多一层：`TopicService.archive` 用 `with_for_update(key_share=True)` 取锁（`topic/repositories.py:120-130`），锁要持有到这次事务结束，也就是响应之后；把提交提到响应前，锁的持有时间会变短而不是变长，是净收益。

**为什么**：GitHub 的所有写端点都保证「2xx 意味着改变已发生」；本仓库 `tests/unit/test_topic_creation_commit.py` 的文件名（「A creation response must describe a committed, immediately readable room」）说明这条规矩在这里是**已被承认**的，只是没有贯彻到全部写路由。

### 8. 同步阻塞调用：本文件已有 `asyncio.to_thread` 样例，但有 7 处没跟

**适用接口**：#52 `recalc`、#53 `convert`、#54 `revisions`（读）、#55 `revisions`（写）、#57 `preview/file`、#58 `attachments`、#59 `attachments/raw`、#60 `attachments/pdf`、#15 `status`（见第二节）。

**现状**：
- 同步文件 IO：`library.read_room_file`(`service.py:64`)、`read_room_text_file`(`:71`)、`read_attachment`(`:128`)、`read_attachment_text`(`:136`)、`read_library_file`(`:184`)、`write_library_file`(`:164`)、`write_room_file`(`:52`) 都是同步函数，却在 `async def` handler 里被直接 `await`（`_source_bytes` `3026-3028`、`_document_bytes` `3426`、`preview_file` `3505/3512`、`upload_attachment` `3576/3603/3606`）。
- 同步 CPU：`documents.revisions.revisions_in`(`:140`) 与 `decide`(`:165`) 解析 docx（zip + XML）——最坏情况 10 MB 的上限（`MAX_ARTIFACT_BYTES`）由 `_document_bytes` 保证，但解析本身是纯 CPU，跑在事件循环上。
- 同步系统调用：`shutil.disk_usage`（`topics.py:1179`）。
- **已有的正确写法**就在同一个文件：`topics.py:3473` `await asyncio.to_thread(library.preview_file_version, …)`。

**做法**：把上面这些调用点按同一形状卸载；CPU 密集的解析用 `to_thread` 也可（有 GIL，但 XML/zip 解析会释放），量大时再考虑 `run_in_executor` 的专用池。

**为什么**：FastAPI 的单个事件循环被一次 10 MB 解压 + XML 遍历占住时，同一进程里所有房间的 WS 帧、心跳、其它请求一起停摆——这是本仓库自己已经承认的代价（`release_read_session` 的 docstring、`get_preview` 的 `to_thread`）。（业界通行，未逐条查证。）

## 附：本次核查过的、结论为「无问题」的关键点（供复核，不必再查）

- **授权**：63 条接口里 61 条走 `ActorResolver.resolve` + `authorize_topic`/`authorize_project`；`_actor_in_place`（`154-163`）是它们的公共形式。#33 与 #63 由 block 自己带 `topic_id`/`project_id` 再授权（正确做法，不由请求体说）；#47 对来源**与**目标两端分别授权；#51 要求凭据能过 `authorize_project`（人）；#26 是全文件唯一手写鉴权的一条（`scoped_token_claims`）。
- **默认是开着的**：`settings.authz_enforce_topic_access` 默认 `True`（`core/config.py:896`），`authorize_topic` 的 `enforce=False` 只是允许个别路由在 kill-switch 下仍保持门（#41/#21/#28 传了 `enforce=True`）。不是漏洞，但是「哪些写在关掉开关后仍受保护」取决于路由而非策略，值得在文档里说清。
- **无 N+1**：`_live_cards`/`_own_cards`、`_rooms_with_running_work`、`list_room_tasks`、`get_topic`、`list_topics` 的每一路派生都走批量件（`reactions_for_blocks`、`latest_by_task`、`last_block_at_for_tasks`、`conversations_for_tasks`、`relevance_for_topics` 的四条、`rooms_awaiting_an_answer`、`agent_balances_for_topics` 形态）。`_rooms_with_running_work` 里 `chat.has_live_screen` 对同房间的每条活各调一次（`286`），但它是内存判断（`agent/chat.py:2370-2373` → `self._compute.holds`），不计入往返。
- **幂等已经做对的**：#28（`request_id`）、#35、#36、#43、#63、#11（`close_thread` 幂等）、#45/#46（锁的获取/释放幂等）、#24/#55（版本冲突）。
- **越权/IDOR**：#4、#6、#12、#13 的游标与块 id 都校验了 `topic_id` **且** `task_id IS NULL`（卡自己的对话只能通过卡读），#33/#63 不看 URL 里的 topic 而看 block 自带的；`_clean_artifact_path`（`2983-2992`）挡掉绝对路径、`..`、`.git`。
- **条件响应头**：#59/#60 带 `nosniff`、`CSP: default-src 'none'; sandbox`、`Content-Disposition`，并区分 `no-store`（分支/committed）与 `private, max-age=3600`（房间内文件）。
