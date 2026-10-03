# projects 接口组分析（47 个接口）

负责文件：`backend/app/api/routes/projects.py`、`project_context.py`、`project_export.py`、`project_sites.py`。
为写清事实，另读了它们调用的域层文件（`domain/project/export.py`、`domain/repository/forge_files.py`、`domain/project/forge.py`、`domain/project/protection.py`、`domain/machine/session_work.py`、`domain/site/services.py`、`domain/library/service.py`、`domain/topic_membership/repositories.py`、`domain/topic/services.py`、`app/api/auth.py`、`app/api/place.py`、`app/auth/project_access.py`、`app/domain/authz/policy.py`、`app/core/db.py`、`app/core/errors.py`）。

借鉴顺序按 BRIEF：先 GitHub REST 公共约定（下文引用均为 docs.github.com 实读），其次业界通行做法（未逐条查证的都写明）。不引入新协议。

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | GET | `/projects/{project_id}/context/search` | 可优化 | 逐房间做一次 `can_access_topic`，每个房间约 8 条查询；同一件事在别处是 3 条。 |
| 2 | GET | `/projects/{project_id}/export` | 可优化 | 同样的逐房间鉴权 N+1，外加逐产物 `artifacts.versions()` 各一次查询。 |
| 3 | GET | `/projects/{project_id}/site` | 可优化 | 候选人 `index.html` 逐个串行取 blob，每个 blob 重解绑凭据并发起新 HTTP 客户端。 |
| 4 | POST | `/projects/{project_id}/site` | 可优化 | 正确地在响应前提交（值得保持），但 `_snapshot` 仍逐个串行取 blob。 |
| 5 | GET | `/projects/resource-limits` | 暂无 | 只答部署级默认值（未传 `team_id`，不走团队覆写），不需要调用方身份。 |
| 6 | POST | `/projects` | 暂无 | 所有者就是已登录的调用者；请求体没有所有者字段，`team_id` 有成员校验。 |
| 7 | GET | `/projects` | 暂无 | 三方载荷（shell/team handle）已批量化；无调用方时明说 401 而不是空列表。 |
| 8 | GET | `/projects/by-task/{task_id}` | 暂无 | 门在 `authorize_task`，载荷复用批量化 helper。 |
| 9 | GET | `/projects/by-team/{team_id}` | 暂无 | 与 `?team_id=` 用同一道 `authorize_team`，无 N+1。 |
| 10 | GET | `/projects/{project_id}` | 暂无 | 单行读取，多一次 `MemberService.manages` 是有意的权限字段。 |
| 11 | GET | `/projects/{project_id}/agents` | 暂无 | 一个项目的 agent 数量有天然上界，逐行 `resolved()` 是内存操作。 |
| 12 | POST | `/projects/{project_id}/agents` | 可优化 | 只 flush 不 commit：响应发出时事务还开着（前端保存后立刻重列，见详细分析）。 |
| 13 | PUT | `/projects/{project_id}/agents/{agent_id}` | 可优化 | 同上，只 flush。 |
| 14 | DELETE | `/projects/{project_id}/agents/{agent_id}` | 可优化 | 同上（连 flush 都交给 service 内部），答 `deleted: true` 时尚未提交。 |
| 15 | PUT | `/projects/{project_id}/default-agent` | 可优化 | 同上。 |
| 16 | GET | `/projects/{project_id}/library/raw` | 可优化 | 在 `async` 路由里同步读文件（`read_library_file`），阻塞事件循环；响应头也没有条件请求。 |
| 17 | GET | `/projects/{project_id}/library` | 可优化 | `root.rglob("*")` + 逐项 `stat()` 在事件循环里同步跑，且结果不分页。 |
| 18 | GET | `/projects/{project_id}/artifacts` | 可优化 | 全量返回，无分页参数。 |
| 19 | GET | `/projects/{project_id}/artifacts/{artifact_id}` | 可优化 | 同一件事问三遍库：`get_or_404` → `summary`（再 select 一次）→ `versions`。 |
| 20 | GET | `/projects/{project_id}/artifacts/{artifact_id}/compare` | 可优化 | 文本比较逐个串行取 blob；快照读盘同步。 |
| 21 | GET | `/projects/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file` | 可优化 | 为了取一版而把全部版本列一遍；快照同步读盘。 |
| 22 | PATCH | `/projects/{project_id}/artifacts/{artifact_id}` | 可优化 | 请求体是无类型 `dict`，无 schema、无 OpenAPI 文档。 |
| 23 | POST | `/projects/{project_id}/artifacts/{artifact_id}/merge` | 可优化 | 同上；`into` 的存在性靠事后 `get_or_404`。 |
| 24 | DELETE | `/projects/{project_id}/artifacts/{artifact_id}` | 暂无 | 权限路径 `_artifact_keeper` 干净，显式 commit。 |
| 25 | DELETE | `/projects/{project_id}/library` | 暂无 | 不同步返回字节，纯文件系统操作，无 DB 写。 |
| 26 | GET | `/projects/{project_id}/decisions` | 可优化 | `list_by_kind_for_project` 无 limit，项目越大返回越大。 |
| 27 | GET | `/projects/{project_id}/weeklies` | 可优化 | 同上。 |
| 28 | GET | `/projects/{project_id}/tasks` | 可优化 | 四批查询都做了，但没有分页；一个项目已含约 170 个房间时全量返回。 |
| 29 | POST | `/projects/{project_id}/memory` | 可优化 | 已永久关闭，但答 422（参数错）而不是 410 Gone（这个端点没有了）。 |
| 30 | GET | `/projects/{project_id}/private-chat` | 可优化 | 用 `GET` 创建资源；且沙箱签名令牌调用时，参与人检查整段被跳过。 |
| 31 | GET | `/projects/{project_id}/forge` | 暂无 | 单行 + 单绑定查询。 |
| 32 | GET | `/projects/{project_id}/forge-attribution` | 暂无 | 纯读 `project.settings`。 |
| 33 | PUT | `/projects/{project_id}/forge-attribution` | 可优化 | flush 后回身调用本组 GET 路由：同一道鉴权做两遍。 |
| 34 | GET | `/projects/{project_id}/default-model` | 暂无 | 目录组装在内存里，`can_manage` 是唯一一次额外查询。 |
| 35 | PUT | `/projects/{project_id}/default-model` | 可优化 | 只 flush 不 commit。 |
| 36 | GET | `/projects/{project_id}/compute-configs` | 暂无 | 一批查询 + 内存聚合，无逐行往返。 |
| 37 | GET | `/projects/{project_id}/devices/{device_id}/sessions` | 可优化 | 两层 N+1 叠加：逐房间 `authorize_topic`，逐会话 `_agent_name`。 |
| 38 | PUT | `/projects/{project_id}/compute-configs` | 可优化 | 只 flush 不 commit。 |
| 39 | GET | `/projects/{project_id}/tier-policy` | 暂无 | 纯读 settings。 |
| 40 | PUT | `/projects/{project_id}/tier-policy` | 可优化 | 无类型 body + flush 后回身调用 GET 路由。 |
| 41 | GET | `/projects/{project_id}/topic-naming` | 暂无 | 纯读 settings。 |
| 42 | PUT | `/projects/{project_id}/topic-naming` | 可优化 | 无类型 body + flush 后回身调用 GET 路由。 |
| 43 | PUT | `/projects/{project_id}/owner` | 可优化 | 无类型 body；只 flush 不 commit，而这条路由的后果最重。 |
| 44 | GET | `/projects/{project_id}/branch-protection` | 可优化 | 每次打开设置页最多 3 次串行 GitHub 调用（各 10s 超时），无缓存。 |
| 45 | PUT | `/projects/{project_id}/branch-protection` | 可优化 | 无类型 body；只 flush 不 commit。 |
| 46 | GET | `/projects/{project_id}/upstream` | 暂无 | 纯读 settings。 |
| 47 | PUT | `/projects/{project_id}/upstream` | 可优化 | 无类型 body；只 flush 不 commit。 |

合计：可优化 31、暂无 16、待确认 0。

## 二、详细分析（只写有发现的，按收益从高到低）

### B. `GET /projects/{id}/context/search` — 逐房间鉴权的 N+1（#1）

现状：`backend/app/api/routes/project_context.py:86`（加载房间）、`project_context.py:88`（循环）、`project_context.py:89`（`resolver.can_access_topic`）。

```python
    rooms = list(await db.scalars(select(Topic).where(Topic.project_id == project_id)))
    readable: dict[uuid.UUID, Topic] = {}
    for room in rooms:
        if await resolver.can_access_topic(
            actor, project_id=project_id, topic_id=room.id
        ):
            readable[room.id] = room
```

问题：每次 `can_access_topic`（`app/api/auth.py:475`）固定发 2 条查询（`TopicRepository.get` + `TopicMembershipRepository.get`），当调用者不是每个房间的名册成员时再走 `may_read_project`（`app/api/auth.py:616`）——后者最多 6 条（名册行、项目行、用户行、赛题出题者、题目板管理员、排除行、小队成员）。房间在 `project_context.py:86` 已经整批加载过了，`is_private` 就在这些行上，而 `may_read_project` 是「(handle, project)」这一对的事实、与房间无关。一个约 170 个房间的项目（`projects.py:919` 的注释给了这个量级）一次搜索就是上千条查询。仓库里同一个问题已经有一处批量化先例：`TopicMembershipRepository.topic_ids_for_member` 的 docstring 写着「asking it one row at a time is the N+1 that makes a hundred-topic project unopenable」，`domain/topic/services.py:512` 用它一次问完整页。

优化：给 `ActorResolver` 加一个与 `can_access_topic` 同判据、但按批的读法，然后三处 N+1（本接口、`export`、`device sessions`）共用。加在 `app/api/auth.py` 的 `can_access_topic`（`auth.py:475`）旁边：

```python
    async def readable_topic_ids(
        self, actor: Actor, *, project_id: uuid.UUID, topics: Sequence[Topic]
    ) -> set[uuid.UUID]:
        """Which of these rooms this actor may read — three queries, whatever
        the batch size. The rooms are already loaded by the caller.

        The per-room form is ``can_access_topic`` and it costs ~8 statements a
        room: the room row, its roster row, and then ``may_read_project``
        (roster, project, user, task, board, team) again for every room.
        ``may_read_project`` is a fact about (handle, project), not about a
        room, so it is asked once; the roster is asked for the whole set in one
        query. The judgment is the same one ``authorize_topic_access`` makes.
        """
        if not actor.authenticated:
            return set()
        ids = [room.id for room in topics]
        roles = await TopicMembershipRepository(self._session).roles_for_member(
            ids, actor.handle
        )
        readable = {room.id for room in topics if room.id in roles}
        rest = [
            room for room in topics if room.id not in roles and not room.is_private
        ]
        if rest and await self._is_project_member(project_id, actor.handle):
            readable.update(room.id for room in rest)
        return readable
```

调用处（`project_context.py:88-92`）变成：

```python
    readable = {
        room.id: room
        for room in rooms
        if room.id in await resolver.readable_topic_ids(
            actor, project_id=project_id, topics=rooms
        )
    }
```

判据等价性逐条对齐 `authorize_topic_access`（`domain/authz/policy.py:25`）：未认证 → 空集；名册有行 → 可读；私密房间只认名册（`not room.is_private` 那一支把私密房间排除在成员兜底之外）；否则看项目成员。`resolver` 侧只多一个 import：`from app.domain.topic_membership.repositories import TopicMembershipRepository`（`auth.py` 已在 `can_access_topic` 里用它）。行为上唯一的变化是：投影期一次 `may_read_project` 的结果被整批复用，而这正是原实现里每个房间重复计算同一个值的地方。

另注（同一接口内的第二处）：`project_context.py:162` 的 `library.list_library_files(project_id)` 是同步文件系统遍历，见 J。

契约：请求与响应形状完全不变（`searched_rooms` / `skipped_rooms` / `hits` 的取值不变）。`test_a_private_room_and_another_project_stay_out`（`backend/tests/integration/test_project_context_search.py:97`）就是钉这个语义的，必须保持绿。

测试：`backend/tests/integration/test_project_context_search.py:65`、`:97`、`:130`、`:137` 四条现状用例应全部保持通过；新增一条查询计数的用例，照 `backend/tests/integration/test_teaching_context.py:90` 的 `_for_project` 写法（`event.listen(engine, "before_cursor_execute", _count)`）——建 N 个房间与 2N 个房间两次搜索，断言语句数不随 N 增长，命名 `test_search_costs_the_same_whatever_the_room_count`。

### C. `GET /projects/{id}/export` — 同一条 N+1，加逐产物列版本（#2）

现状：`backend/app/domain/project/export.py:185`（逐房间鉴权）、`export.py:241`（逐产物）、`export.py:244`（`artifacts.versions`）、`export.py:266`（`await db.commit()`，提交在所有 DB 读之后、远端 I/O 之前）。

```python
    visible = []
    for topic in topics:
        if await resolver.can_access_topic(
            actor, project_id=project_id, topic_id=topic.id
        ):
            visible.append(topic.id)
```

问题：与 B 同一件事（`can_access_topic` 逐房间），并且 `export.py:241-246` 对每个产物再单独调一次 `artifacts.versions(db, artifact.id)`（`domain/project/artifacts.py:462`），每次一条查询。导出是一次性重活，用户会等；房间数与产物数都在百的量级。

优化：`visible` 改用 B 的批量化读法：

```python
    visible = list(
        await resolver.readable_topic_ids(actor, project_id=project_id, topics=topics)
    )
```

逐产物那一段换成一次批量查询：`ProjectArtifactVersion` 的读取条件已经是 `card_id in allowed_cards`，把「按产物分组」搬进 SQL：

```python
    versions_by_artifact = await artifacts.versions_for_project(
        db, project_id, card_ids=allowed_cards
    )
    for artifact in await artifacts.list_for_project(db, project_id):
        versions = [asdict(v) for v in versions_by_artifact.get(artifact.id, ())]
```

`versions_for_project` 与现有 `versions`（`domain/project/artifacts.py:462`）同一张表、同一个 `_claims()` 投影，只是 `where(ProjectArtifactVersion.project_id == project_id)` 加一次 `in_(card_ids)`、`order_by(artifact_id, number)`，在 `domain/project/artifacts.py` 里紧挨着 `versions` 添加即可。

契约：`GET /projects/{id}/export` 的 tar 内容与 `manifest` 形状不变（`rooms`、`artifacts[].versions` 仍按原顺序）。`export.py:266` 的提交时机保持不变——它是这个文件里做对的地方，不要动。

测试：`backend/tests/integration/test_project_export.py:127`（内容可离线读取、校验和一致）、`:171`（真实项目访问要求）、`:219`（资料库缺失在 manifest 里明说）应保持通过；新增 `test_export_asks_the_database_a_constant_number_of_questions`，用 B 里的计数写法，断言语句数不随房间数/产物数增长。

### D. `GET /projects/{id}/devices/{device_id}/sessions` — 两层 N+1 叠在一起（#37）

现状：`backend/app/api/routes/projects.py:1250`（逐会话鉴权）、`projects.py:1252`（`resolver.authorize_topic`），以及 `domain/machine/session_work.py:206` 的循环里 `session_work.py:215` 的 `await _agent_name(...)`。

路由里：

```python
    for topic, session in await device_sessions(db, project_id, device_id):
        try:
            await resolver.authorize_topic(
                actor, project_id=project_id, topic_id=topic.id
            )
        except ForbiddenError:
            hidden += 1
            continue
        listed.append(session)
```

域层里：

```python
    for row, topic in sorted(placed, key=lambda pair: pair[0].updated_at, reverse=True):
        out.append((topic, {..., "agent_name": await _agent_name(db, project, topic, row.agent_handle), ...}))
```

问题（两处都是查询往返，各 N 次）：
1. `authorize_topic`（`auth.py:453`，默认 `enforce=False`）在 `settings.authz_enforce_topic_access` 关掉时整段跳过，开着时每次走 `can_access_topic`（同 B 的 2~8 条）。
2. `_agent_name`（`session_work.py:274`）每次都调 `AgentInstanceService.for_seat_handle`，而后者（`domain/agent_instance/services.py:116`）内部是 `list_for_project(project.id)` 整表列出再线性查找——**每一条会话一次**；查不到时再落到 `TopicService.resolve_agent`（`domain/topic/services.py:275`，又是 1~3 条）。

优化：两处都按批。

路由侧：`authorize_topic` 的 kill-switch 语义要保留（开关关掉时今天就是全部列出、`hidden` 为 0），所以这里照抄开关而不是直接换成 B 的无开关读法：

```python
    from app.core.config import settings

    rows = await device_sessions(db, project_id, device_id)
    if not settings.authz_enforce_topic_access:
        listed, hidden = [session for _topic, session in rows], 0
    else:
        readable = await resolver.readable_topic_ids(
            actor, project_id=project_id, topics=[topic for topic, _ in rows]
        )
        listed = [s for topic, s in rows if topic.id in readable]
        hidden = len(rows) - len(listed)
```

域层侧（`session_work.py:206`）：把席位表一次建好，逐行只查表；查不到的才回落到 `resolve_agent`：

```python
    from app.domain.agent_instance.services import AgentInstanceService
    from app.domain.identity.handles import agent_instance_handle

    seats = {
        agent_instance_handle(instance.id): AgentInstanceService.resolved(instance)
        for instance in await AgentInstanceService(db).list_for_project(project.id)
    }
    names: dict[str, str] = {}
    for row, topic in placed:
        seated = seats.get(row.agent_handle)
        if seated is None:
            seated = await TopicService(db).resolve_agent(topic)
        names[row.agent_handle] = seated.display_name
```

（`agent_instance_handle` 在 `domain/identity/handles.py:41`，`for_seat_handle` 内部做的正是这个比较，见 `domain/agent_instance/services.py:130`。）常见的「会话属于某个已保存队友」这一档从 N 次查询降到 1 次。

契约：响应体不变（`sessions` 的字段与顺序、`hidden` 的计数语义不变）。`hidden` 的语义在 kill-switch 关掉时仍是 0。

测试：`backend/tests/integration/test_project_machines.py`（设备会话与分布）、`test_project_compute_configs.py:222`（`test_the_project_shows_where_its_started_agents_work`）覆盖现状；新增 `test_sessions_on_a_device_cost_the_same_whatever_the_room_count`，用 B 的计数写法断言语句数与房间数无关。

### E. `GET /projects/{id}/private-chat` — 沙箱签名令牌下参与人检查整段被跳过（#30）

现状：`backend/app/api/routes/projects.py:1009`（`get_private_chat`）。

```python
    actor = await resolver.require_verified_caller(project_id=project_id)
    if actor.authenticated:
        await resolver.authorize_project(actor, project_id=project_id)
        participants = {user_handle, peer_handle} - {None}
        if actor.handle not in participants:
            raise ForbiddenError("只能打开自己参与的私聊")
```

问题（两条，都是事实）：
1. 这段的唯一一道「你只能开自己的私聊」的门，写在 `if actor.authenticated:` 里面。`require_verified_caller`（`auth.py:325`）在凭据是全局沙箱令牌时**故意返回未认证 actor**（docstring：「The global ``SANDBOX_TOKEN`` stays gate-only (dev / trusted-single-host override): it opens the surface but never becomes an identity」）。于是持沙箱签名令牌的调用方跳过 `authorize_project` 与参与人检查两层，直接用 `user_handle`/`peer_handle` 参数打开任意成员在任意项目里的私聊。该令牌不是空默认值：`SANDBOX_TOKEN = settings.sandbox_signing_secret`（`core/sandbox_auth.py:39`），由 `settings.sandbox_token` 或从 `jwt_secret` 派生（`core/config.py:680`），即每个部署都有一个真实存在的秘密；而它同时也是签发 scoped token 的那把密钥（`mint_scoped_token`，`core/sandbox_auth.py:51`，被 `api/auth.py`、`api/routes/git_http.py`、`api/routes/workspace.py`、`domain/agent/harness/*/channel.py`、`domain/agent/device_provider.py` 调用），所以持有它的不止一个人。`require_verified_caller` 的 docstring 把它称作「dev / trusted-single-host override」——这个描述在只有一台机器、没有别人持有时成立；本报告的结论按这个前提陈述，不假设它总能成立。
2. 用 `GET` 做 get-or-create（`TopicService.get_or_create_private`），即一个「读」请求会创建话题。业界通行（未逐条查证）：GitHub 的 `POST /user/repos` 是「Creates a new repository for the authenticated user」，创建一律是 POST，配套 `Location`/响应体给出新资源；Slack/Discord 的同类「打开/创建私聊」都是 POST。

优化：把两件事分开。

```python
    actor = await resolver.require_verified_caller(project_id=project_id)
    # 沙箱令牌不是身份，所以它也就没有一个「自己」可以往参与者里放。要开私聊就得
    # 说出是谁在开——这是 require_verified_caller 之外的那一句话，写在路由里，
    # 因为它是这条路自己的语义，不是通用门。
    if not actor.authenticated:
        raise AuthenticationRequiredError("打开私聊需要登录：沙箱令牌不能代表任何成员")
    await resolver.authorize_project(actor, project_id=project_id)
    participants = {user_handle, peer_handle} - {None}
    if actor.handle not in participants:
        raise ForbiddenError("只能打开自己参与的私聊")
```

第 2 条按 BRIEF「不要另造协议」的处理：**不改动词**，只在文档与路由注释里说清它是 get-or-create，并把创建与后续读取的语义差别写进 docstring；真正要改的话是加 `POST /projects/{id}/private-chat`、把 GET 保留为纯读（找不到就 404），但那是一次前端联动改动，本报告只列为建议、不作为必改项（理由见「三」）。

契约：`GET` 的请求参数、响应体不变；持沙箱令牌且调用方未认证的请求从 200 变成 401。仓库内自查：`backend/app/api/routes/topics.py` 与前端 `frontend/src/api.ts` 里对 `/private-chat` 的调用都带用户会话（`test_private_chat_per_agent.py` 的用例也是客户端身份），不受影响。

测试：`backend/tests/integration/test_private_chat_per_agent.py:109`（每人一个房间且保持）、`:155`（一个 DM 是两个成员）已覆盖正向；新增 `test_the_sandbox_token_cannot_open_someone_elses_private_chat`，用 `SANDBOX_TOKEN` 直接请求别人的 `user_handle`，断言 401。

### F. 远端 blob 串行取：`GET /site`、`POST /site`、`compare`（#3、#4、#20）

现状：
- `domain/repository/forge_files.py:179`：`committed_blobs` 是字典推导里的串行 `await`。
  ```python
      async def committed_blobs(self, oids: list[str]):
          return {oid: await self._blob({"sha": oid}) for oid in dict.fromkeys(oids)}
  ```
- 每个 `_blob`（`forge_files.py:403`）经 `_data`（`forge_files.py:61`）落到 `repository_data`（`domain/project/forge.py:108`）：**每次**重查绑定与凭据（`binding_for_project` + `tokens_for_project`，2 条查询）、`installation_token()`，再 `async with httpx.AsyncClient(timeout=30)` 新建一个客户端发一次请求，然后关掉。
- 调用点：`domain/site/services.py:255`（`publication_source` 对每个候选 `index.html` 各调一次 `committed_blobs([entry["oid"]])`，即一次一个）、`domain/site/services.py:302`（`_snapshot` 对整棵子树一次调用）、`forge_files.py:182`（`compare_revisions` 按变更文件两两调用）。

问题：这些都是「一次取多个已知 oid」，却按 oid 串行，每个 oid 都重付 2 条查询 + 令牌 + 一次 TLS 握手；N 个文件就是 N 次往返的墙钟时间。`GET /projects/{id}/site` 的打法是「每个候选 `index.html` 一次 `committed_blobs([一个 oid])`」，候选多时尤其明显。

优化：把「取多个 blob」变成一次绑定/凭据解析 + 一个共享客户端 + 一次并发扇出。约束是要守住的：`AsyncSession` 不能被并发使用，所以解析必须发生在 `gather` 之前（这正好也是 `repository_data` 现在的顺序）。在 `domain/project/forge.py` 里加一个批量入口：

```python
# forge.py 今天没有 import asyncio / base64，错误族里也没有 NotFoundError，需要一并补上。
async def blob_batch(
    project_id: uuid.UUID,
    session: AsyncSession,
    oids: list[str],
    *,
    release_session: bool = False,
) -> dict[str, bytes]:
    """``/git/blobs`` for many oids: one binding read, one token, one client.

    ``repository_data`` resolves binding + tokens and mints a token on EVERY
    call, which is right for a one-off read and wrong for a tree walk. The
    session is not touched after the token is minted, so the fetches may run
    concurrently — an ``AsyncSession`` must not be, which is why the token is
    resolved before the gather, not inside it.
    """
    if not oids:
        return {}
    binding = await binding_for_project(project_id, session)
    tokens = await tokens_for_project(project_id, session)
    if binding is None or tokens is None:
        raise GatewayUnavailableError("项目的代码仓库或凭据不可用")
    if release_session:
        await release_read_session(session)
    token, _ = await tokens.installation_token()
    unique = list(dict.fromkeys(oids))
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    semaphore = asyncio.Semaphore(8)

    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        async def one(oid: str) -> bytes:
            async with semaphore:
                response = await client.get(
                    f"{binding.api_url.rstrip('/')}/repos/{binding.repo}/git/blobs/{oid}"
                )
            if response.status_code == 404:
                raise NotFoundError("代码仓库里没有这个对象")
            if response.is_error:
                raise GatewayUnavailableError(
                    f"读取代码仓库失败（HTTP {response.status_code}）"
                )
            body = response.json()
            if not body or body.get("encoding") != "base64":
                raise GatewayUnavailableError("代码托管服务没有返回文件内容")
            return base64.b64decode(body["content"])

        results = await asyncio.gather(*(one(oid) for oid in unique))
    return dict(zip(unique, results))
```

`ProjectFiles.committed_blobs`（`forge_files.py:179`）随即变成一行转发（`release_session` 时先 `await self._release()`），调用方一处都不用改。并发上限 8 是保守值，可按部署调整。

契约：对外无变化（`GET /site` 的 `source_revision`/`candidates`、`POST /site` 的发布结果、`GET .../compare` 的 `files` 都不变）。代价是并发时对代码托管的瞬时压力上升，所以用信号量封顶；`GET /branch-protection`（见 H）那三个调用不在此列——它们有先后依赖。

测试：`backend/tests/integration/test_project_sites.py:67`（只有被采纳的文件被发布且在机器与仓库消失后仍在）、`:105`、`:121`、`:145`、`test_forge_transport.py`、`test_artifacts.py` 覆盖现状。新增：在 `test_forge_transport.py` 里加一条断言「一次 `committed_blobs(N 个 oid)` 只发一次绑定/凭据解析与一次令牌请求」，用 `before_cursor_execute` 计数，命名 `test_a_tree_of_files_is_fetched_with_one_set_of_credentials`。

### G. 十二条写路由「先发响应、后提交」（#12–#15、#33、#35、#38、#40、#42、#43、#45、#47）

现状（`backend/app/api/routes/projects.py`，行号是各自 `flush` 的位置）：

| 路由 | 定义 | 提交? |
|---|---|---|
| `POST /{id}/agents` | `:424` | `flush :442` |
| `PUT /{id}/agents/{agent_id}` | `:451` | `flush :474` |
| `DELETE /{id}/agents/{agent_id}` | `:486` | 无（`deactivate` 内部 flush） |
| `PUT /{id}/default-agent` | `:509` | 无（`set_project_default` 内部 flush） |
| `PUT /{id}/forge-attribution` | `:1082` | `flush :1098` |
| `PUT /{id}/default-model` | `:1150` | `flush :1179` |
| `PUT /{id}/compute-configs` | `:1263` | `flush :1282` |
| `PUT /{id}/tier-policy` | `:1317` | `flush :1345` |
| `PUT /{id}/topic-naming` | `:1374` | `flush :1387` |
| `PUT /{id}/owner` | `:1419` | `flush :1485` |
| `PUT /{id}/branch-protection` | `:1621` | `flush :1655` |
| `PUT /{id}/upstream` | `:1671` | `flush :1692` |

对照做对的四条：`create_project`（`projects.py:246`）、`rename_artifact`（`:786`）、`merge_artifact`（`:809`）、`delete_artifact`（`:826`）都显式 `await db.commit()`，`publish_project_site`（`routes/project_sites.py:83`）也有，并写了理由。

问题：`app/core/db.py:171` 的 `get_db` 在 `yield` 的退出块里提交，而 FastAPI 在 `await response(scope, receive, send)` **之后**才跑这个退出块。于是这十二条路由在客户端拿到 200 时事务还开着——这正是仓库自己用一个专门的文件钉住的那类 bug（`backend/tests/integration/test_a_create_commits_before_its_response_is_sent.py` 的 docstring：「先发响应、后提交 … Committing where the writes end — 只留读在后面 — removes the gap」，并配了 `test_a_space_is_committed_before_its_201_is_sent` 等五条用例）。这些路由的**响应体本身是对的**（回身读的那几条在同一个 session 里看得见 flush 过的行）；受影响的是「下一个请求看不见这一笔」。

这一条在仓库里是有具体复现路径的，不必靠推测：`frontend/src/components/agents/AgentEditorDialog.vue:96` 创建成功后 `emit('saved')`，父组件 `frontend/src/components/settings/AgentTeamSettings.vue:269` 上挂的是 `@saved="refresh"`，而 `refresh`（同文件 `:52`）立刻调 `listProjectAgents(props.projectId)`。也就是「保存一个新 AI 队友」这一个动作，紧跟着一个 `GET /projects/{id}/agents`——第一笔还没提交，新队友可能不在列表里。

优化：在这十二条路由的写操作结束处提交，和同文件里已经做对的那四条一致。以 `create_project_agent`（`projects.py:442`）为例：

```python
    await db.flush()   # 保持：响应体要读到实例 id
    await db.commit()  # 提交在这里，不留到响应之后 —— 理由见 app/core/db.py:171
    return ok(...)
```

其余十一条同形；`flush()` 在需要响应体读到新值时保留，不需要的可以直接 `await db.commit()`。`deactivate`/`set_project_default` 内部已经 flush，路由末尾补一行 `await db.commit()` 即可。

契约：状态码、响应体、字段都不变；变化的是「响应发出时这一笔已经落库」。注意 `PUT /owner`（`projects.py:1419`）这条：它的日志（`projects.py:1499`、`:1509`、`:1517`）在注释里被称作「permanent record」，而日志在 flush 后、commit 前就打出来了——提交前的那几行日志描述的是可能被回滚的转移，所以这条路由提交的意义比别的更大。

测试：文件就是 `backend/tests/integration/test_a_create_commits_before_its_response_is_sent.py`，写法照 `test_a_space_is_committed_before_its_201_is_sent`（`:232`，它自己驱动 ASGI app 并在路由的 ASGI 边界上读时序，因为普通 `TestClient` 看不见这个顺序）。为这组补两条就够：`test_an_agent_is_committed_before_its_response_is_sent`（POST /agents 后立刻 GET /agents）、`test_a_setting_is_committed_before_its_response_is_sent`（PUT /tier-policy 后立刻 GET /tier-policy）。

### H. `GET /projects/{id}/branch-protection` — 每次打开设置页最多 3 次串行 GitHub 调用，无缓存（#44）

现状：`projects.py:1564` 路由；`projects.py:1593` 调 `github_repo_snapshot`；实现在 `domain/project/protection.py:274`。

```python
        async with httpx.AsyncClient(
            transport=transport, timeout=10.0, headers=headers
        ) as client:
            r = await client.get(f"{api_base}/repos/{repo}")
            ...
            prot = await client.get(f"{api_base}/repos/{repo}/branches/{branch}/protection")
            ...
            rules = await client.get(f"{api_base}/repos/{repo}/rules/branches/{branch}")
```

问题（陈述事实）：一次 `GET /branch-protection` 最多发 3 次串行 HTTPS 请求，每次超时 10 秒（`protection.py:296`），所以最坏 30 秒；失败一律降级为 `unknown`（这是做对的地方，不 500）。这三个调用之间有真实依赖（要先拿到 `default_branch` 才能问保护），所以顺序不能打散——可省的是**重复**：这是设置页的一个只读附注，GitHub 侧的保护开关不会每秒变，而每次打开/刷新都要重新问一遍。仓库里已有条件请求/ETag 的工具：`app/api/conditional.py` 的 `if_none_match_hits` / `etag_for_json`（`admin_members.py` 与 `avatars.py` 在用）。

优化：两件独立的小事。

1. 给快照加一个进程内的短 TTL 缓存（把 `(repo, merge_method, GitHubProtection)` 按 `repo` 存 60 秒），实现放在 `domain/project/protection.py` 里 `github_repo_snapshot` 的上方：

```python
# 这个文件今天只 import 了 fnmatch/re/dataclass/httpx/Project，需要补 `import time`。
_SNAPSHOT_TTL = 60.0
_snapshots: dict[str, tuple[float, tuple[str, GitHubProtection]]] = {}


async def cached_repo_snapshot(repo: str, token: str | None, **kwargs):
    """``github_repo_snapshot`` behind a 60-second memory.

    The settings page reads this on every open; the answer is a read-only
    annotation about someone else's control plane and does not change between
    two page loads. A failed lookup is NOT cached — ``unknown`` is exactly the
    answer a retry can improve.
    """
    now = time.monotonic()
    hit = _snapshots.get(repo)
    if hit is not None and now - hit[0] < _SNAPSHOT_TTL:
        return hit[1]
    result = await github_repo_snapshot(repo, token, **kwargs)
    if result[1].status != "unknown":
        _snapshots[repo] = (now, result)
    return result
```

（多进程部署下这是每进程一份的缓存，够用；要全局一致就换共享存储，但那不是这条路由的问题。）

2. 给这条路由本身加 `ETag`/`If-None-Match`：`_branch_protection_payload(bp)` 只依赖 `project.settings` + GitHub 侧快照，把两者一起算个 ETag，命中就 304。GitHub 自己的规定是「Making a conditional request does not count against your primary rate limit if a 304 response is returned」（https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api）。

契约：响应体不变；新增 `ETag` 响应头与 304 分支（客户端不发 `If-None-Match` 时行为完全不变）。缓存最多让 `github_protection` 落后 60 秒，而它是「另一个控制台的事实」的附注，不是判定依据——判定用的是 `_branch_protection_payload(bp)`（平台侧键），它不走缓存。

测试：`backend/tests/integration/test_branch_protection.py:63`（默认值与 GitHub 页对齐）、`:77`（局部更新只碰出现过的键）等应当保持通过。缓存的行为契约用 `protection.py` 自己的传输注入（`transport` 参数）就能测：新增 `test_a_second_page_load_does_not_ask_github_again`，注入一个计数的 transport，连续两次调用断言只发一次。

### I. 五个列表端点没有分页（#18、#26、#27、#28，以及 #17 的返回体）

现状：`projects.py:854`（decisions）、`:882`（weeklies）、`:908`（tasks）、`:578`（artifacts），以及 `:564`（library）。四条的返回都是 `ok(page(items, len(items)))`，`items` 是全部行。`BlockRepository.list_by_kind_for_project`（`domain/block/repositories.py:1133`）没有 limit；`TaskRepository.list_for_project` 也没有。

问题（事实）：这些集合随项目增长，`list_project_tasks` 的 docstring 自己给了量级——「a project here already holds ~170 rooms」（`projects.py:919`）。`page()` 的 `total` 传的是 `len(items)`，即「本页有多少」，所以调用方看不出还有没有更多；同时这些端点也没有 `page_start`/`page_size` 之类的入参。

优化：仓库里已经有量好的两套约定，直接用，不新造：

- 偏移式：`page_start`/`page_size`（`Query(default=0, ge=0)` / `Query(default=20, ge=1, le=100)`），见 `api/routes/feedback.py`、`admin_spaces.py`、`admin_feedback.py`。
- 游标式：`(created_at, id)` 行值游标，见 `domain/block/repositories.py:965`（`tuple_(Block.created_at, Block.id) < (before.created_at, before.id)`，注释解释了为什么用行值而不是单一列）与 `notifications_flat.py` 的 `_encode_cursor`。

`decisions`（`projects.py:854`）用后者最自然，因为它已经按时间序，而且它是「持续增长的历史」：

```python
@router.get("/{project_id}/decisions")
async def list_decisions(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: str = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    await project_reader(db, resolver, project_id, topic)
    await ProjectService(db).get_or_404(project_id)
    blocks, next_cursor = await BlockRepository(db).list_by_kind_for_project_page(
        project_id, BlockKind.decision, cursor=cursor, limit=limit
    )
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in blocks]
    return ok({**page(items, len(items)), "next_cursor": next_cursor})
```

`list_by_kind_for_project_page` 在 `domain/block/repositories.py` 里紧挨着 `list_by_kind_for_project`（`:1133`）添加，`where` 条件与它相同，再加上 `:965` 那套行值游标（照抄 `tuple_(Block.created_at, Block.id) < (before.created_at, before.id)` 与 `order_by(created_at.desc(), id.desc())`），并照 `:983` 的注释一次多取一行来判断「还有没有下一页」，不要多一次 `COUNT`。`artifacts`（`:578`）按 `delivered_at` 同理。

契约：新增可选入参（不传时**保持今天的全量行为**，这样前端不必同步改），并在响应里多一个 `next_cursor`/`total` 语义；`total` 在分页时应当是「这个集合一共有多少」（`count_for_project` 之类的现成查法），而不是 `len(页)`。要不要提供 `Link` 头（GitHub 约定，https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api，`rel=prev/next/last/first`，`per_page` 上限 100）由三里的统一决定处理，本条先不单独引入。

测试：`test_project_task_tree.py`、`test_project_context_search.py:65` 覆盖现状。新增两条：`test_a_decision_page_carries_the_next_cursor`（造 3 条、`limit=2`，断言拿到 2 条 + 游标，用游标再取到剩下 1 条）、`test_the_last_page_has_no_cursor`。

### J. 在 `async` 路由里同步读写文件（#16、#17、#21，以及 #2 提交后的落盘）

现状：
- `projects.py:541`（`library_file_raw`）→ `domain/library/service.py:184` `read_library_file`：同步 `Path.read_bytes()`。
- `projects.py:573`（`list_library`）→ `service.py:202` `list_library_files`：同步 `root.rglob("*")` + 逐项 `entry.stat()`。
- `projects.py:731`（`download_artifact_version`）→ `service.py:249` `read_artifact_snapshot`：同步 `read_bytes()`；`projects.py:673-674`（compare）同样。
- `project_context.py:162`（search）也直接调 `list_library_files`。
- `domain/project/export.py`：`_copy_tree`（`:305`、`:307`）与 `shutil.copyfile`（`:332`）都正确套了 `run_sync`，但同一段里的 `_json(...)`（`:312`、`:318`）、`path.write_text(doc["content"])`（`:317`）没套。

问题（事实）：这些都是 `async def` 路由内的阻塞调用，整个事件循环在同步 I/O 期间停住。仓库自己的约定是反过来做的：`asyncio.to_thread` 在 `accept.py:234`、`room_files.py:94`、`projects.py:675`，`run_sync` 在 `project_export.py:27`、`domain/project/export.py:296`。产物快照尤其值得注意——`service.py:222` 的注释自己说这条路上有「五十版 20MB 的幻灯片」量级的东西，一次同步 `read_bytes()` 就是 20MB 的停顿。

优化：把同步函数留在域层（它们本身没有错），在调用点套 `await asyncio.to_thread(...)`：

```python
    data = await asyncio.to_thread(library.read_library_file, project_id, name)
```

`list_library_files` 同理：`files = await asyncio.to_thread(library.list_library_files, project_id)`（`projects.py:573` 与 `project_context.py:162` 两处）。`export.py` 里剩下那三处 `_json`/`write_text`（`:312`、`:317`、`:318`）一并用 `run_sync`（该文件已经 import 了 `run_sync`）。

契约：无（响应字节、状态码、头部都不变）。

测试：`test_library.py:43`（重名取下一个编号）、`:89`（房间读到的是资料库那一份）、`:344`（只有项目成员能读资料库）、`test_artifacts.py:51`（按路径读房间文件）覆盖现状，行为不变即保持通过。这类「不阻塞事件循环」的性质用现有测试很难断言，所以本条不强行加测试，靠代码评审 + 与既有约定一致来保证。

### K. `POST /projects/{id}/memory` 答 422，而它的含义是 410 Gone（#29）

现状：`projects.py:981`，函数体就是一句：

```python
    raise ValidationError(
        "记忆改为直接写 `~/.cheese/memory/` 下的文件：……`cheese_remember` 已停用……"
    )
```

问题（事实）：`ValidationError` 是 `AppError`，`code = 422`（`core/errors.py:279`）。422 的含义是「请求体不合法」，而这里的意思是「这个端点不存在了，去别处写」；参数一个都不看，正是说它跟请求内容无关。再试一次不会成功，这正是 410 与 422 的分界。GitHub 的做法是把「删掉的东西」答 410：删除一个 issue 后访问它的 API 返回 410 Gone（https://docs.github.com/en/rest/issues/issues）。仓库的错误族里没有 410（`core/errors.py` 有 400/401/403/404/409/412/422/500/502/503 与 499）。

优化：加一个类就够，`_handle_app_error`（`core/errors.py:417`）已经按 `exc.code` 出状态码与包络，不需要别的管线：

```python
class GoneError(AppError):
    """This endpoint used to exist and no longer does — 410, not 422."""

    code = 410
    message = "Gone"
```

路由改为 `raise GoneError("记忆改为直接写 ...")`。

契约：状态码从 422 变成 410，包络形状不变（`{code, message, data, error}`）。旧会话里还在调 `cheese_remember` 的调用方读到的是同一句话；把状态码从「你写错了」改成「这里没有了」，才是这句话真正的意思。

测试：现状用例是 `backend/tests/integration/test_what_everyone_sees_is_a_document.py:77` 的 `test_writing_memory_through_this_endpoint_is_retired_and_says_where`（在 `:96` 断言 422、`:97` 断言「停用」、`:98` 断言 `~/.cheese/memory/`）；改状态码后把 `:96` 的断言从 422 改成 410 即可，另两句不变。

### L. 七个请求体是无类型 `dict`（#22、#23、#40、#42、#43、#45、#47；#29 是第八个但不读 body）

现状：`projects.py` 里 `body: dict` 出现在 `:775`（rename）、`:794`（merge）、`:983`（memory）、`:1318`（tier-policy）、`:1375`（topic-naming）、`:1421`（owner）、`:1622`（branch-protection）、`:1672`（upstream）。同一文件里已经有类型化的对照组：`ProjectCreate`、`ForgeAttributionUpdate`、`ProjectDefaultModelUpdate`、`AgentInstanceCreate/Update`、`ProjectComputeConfigs`（`domain/project/schemas.py`、`domain/agent_instance/schemas.py`、`domain/agent/compute_configs.py`）。

问题（事实）：这些路由不得不用 `body.get(...)` + 手写校验（`projects.py:785`、`:806`、`:1331`、`:1382`、`:1461`、`:1648`、`:1686`），于是 ① OpenAPI 里这些请求体是 `{}`，前端与任何生成式客户端拿不到形状；② 校验散在各路由里、错误文案各自手写；③ 像 `rename_artifact` 的 `str(body.get("name") or "")`（`:785`）这种写法把「没给 name」和「name 是空串」合成同一个结果。对照 GitHub 的约定，`POST /user/repos` 的请求体是有 schema 的字段集合（https://docs.github.com/en/rest/repos/repos），多余字段与类型错误由框架在入口处拒绝。

优化：在 `domain/project/schemas.py` 里补模型，路由签名改成模型，校验逻辑随之删掉。举两例：

```python
class ProjectRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ArtifactMergeIn(BaseModel):
    into: uuid.UUID


class TierPolicyUpdate(BaseModel):
    """Omitted fields stay as they are; ``allowed_tiers=None`` means 不限档."""

    allowed_tiers: list[str] | None = None
    over_tier: str | None = None


class TopicNamingUpdate(BaseModel):
    mode: Literal["auto", "manual"]


class ProjectOwnerUpdate(BaseModel):
    owner_handle: str = Field(min_length=1, max_length=64)
```

`rename_artifact`（`projects.py:772`）随之变成：

```python
async def rename_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    body: ProjectRenameIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    await _artifact_keeper(project_id, db, resolver)
    row = await artifacts.get_or_404(db, project_id=project_id, artifact_id=artifact_id)
    renamed = await artifacts.rename(db, row, name=body.name)
    await db.commit()
    return ok({"id": str(renamed.id), "name": renamed.name})
```

`merge_artifact` 里 `_artifact_ref(body.get("into"))`（`projects.py:806`，函数定义在 `projects.py:830`）整个函数可以删掉——`ArtifactMergeIn.into` 已经保证是 UUID，非法值由 422 在入口拦下。

契约：响应体不变；请求体的**校验失败**从「200 之外的自定义 422 文案」变成了框架的 422（`code: 422` 不变，`message` 变成 `Validation failed` + 详情）。`set_tier_policy` 的 `allowed_tiers` 语义要小心：今天「键不存在」与「键为 `null`」是两种意思（`projects.py:1331` 用 `gate.ALLOWED_TIERS_KEY in body` 区分），而 Pydantic 的 `model_fields_set` 正好能表达同一件事（`save_default_model` 在 `projects.py:1169` 已经这么用了），所以保留语义、不改变行为。

测试：`test_artifacts.py:180`（拒绝路径穿越）、`:187`（要求路径）、`test_branch_protection.py:158`（拒绝坏的 approvals 与坏形状）、`test_project_owner_write.py` 覆盖现状；因为校验挪到了入口，这些用例的断言（状态码 422 + 被拒绝）应当继续成立，只需确认文案位置。新增 `test_a_bad_body_shape_is_refused_before_the_route_runs` 一条即可覆盖这一类。

### M. 三条写路由回身调用同组的 GET 路由（#33、#40、#42；#35 是同一模式的变体）

现状：
- `projects.py:1099`：`return await get_forge_attribution(project_id, db, resolver)`。
- `projects.py:1346`：`return await get_tier_policy(project_id, db, resolver)`。
- `projects.py:1388`：`return await get_topic_naming(project_id, db, resolver)`。
- `projects.py:1180` 起（`save_default_model`）：没有回身调用，但把 GET 的组装函数 `_default_model_state` 再跑一遍。

问题（事实）：这三条写路由因此把 GET 路由的入口重做了一遍——`resolve()` 一次、`authorize_project()` 一次、`ProjectService.get_or_404()` 一次，都是第二次。`PUT /tier-policy` 更甚：`MemberService.require_manager`（`projects.py:1328`）之后，被调用的 `get_tier_policy` 里又做一次 `resolve` + `authorize_project` + `get_or_404`（`projects.py:1298-1300`）。这些请求多花 3~6 条查询；`authorize_project` 关掉时会跳过，开着时每次都在。

优化：把「读状态」抽成一个小函数（不经过 FastAPI 参数解析、也不重复鉴权），写路由在已经鉴权之后直接调它：

```python
async def _tier_policy_state(db: DbSession, project: Project) -> dict:
    """The ``GET /tier-policy`` payload, for a caller already authorized."""
    policy = gate.policy_of(project.settings)
    return {
        gate.ALLOWED_TIERS_KEY: (
            None if policy.allowed_tiers is None else sorted(policy.allowed_tiers)
        ),
        gate.OVER_TIER_KEY: policy.over_tier,
        "tiers": sorted(
            {choice["tier"] for choice in model_choices(project.settings)}
            | set(COMPUTE_TIERS.values())
        ),
    }


@router.get("/{project_id}/tier-policy")
async def get_tier_policy(project_id, db, resolver) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    return ok(await _tier_policy_state(db, project))
```

`set_tier_policy` 的尾部随之变成 `return ok(await _tier_policy_state(db, project))`——它手里已经有 `project`。`set_forge_attribution`、`set_topic_naming`、`save_default_model` 同形（后者的 `_default_model_state` 已经是这个形状，只是多跑了一次 `get_or_404`）。

契约：响应体逐字段不变（这正是抽同一个函数的目的）。附带修正一处：今天 `PUT /default-model` 的响应 `can_manage` 恒为 `True`（`projects.py:1181`），而 `GET /default-model`（`projects.py:1141-1145`）是按实际管理权算的；抽成同一个状态函数后需要显式决定这个差异是保留还是统一——建议保留（写成功即意味着能管理），但要在函数注释里写明，别让它成为无声的第二份实现。

测试：`test_project_compute_configs.py:48`、`:141`、`test_branch_protection.py:103`（`override_handles` 与 `default_reviewer` 往返）覆盖现状，响应不变即通过。

### N. `GET /projects/{id}/artifacts/{artifact_id}` 同一件事问三遍库（#19）

现状：`projects.py:603`：

```python
    row = await artifacts.get_or_404(db, project_id=project_id, artifact_id=artifact_id)
    listed = await artifacts.summary(db, row.id)
    history = await artifacts.versions(db, row.id)
```

问题（事实）：`get_or_404`（`domain/project/artifacts.py:315`）已经取到了这一行；`summary`（`artifacts.py:416`）又 select 一次同一行（再算 `_claims()`），`versions`（`artifacts.py:462`）第三次查版本集合。三次都返回同一个聚合的三个方面，路由却分三趟。（`compare_artifact_versions`（`projects.py:650`）与 `download_artifact_version`（`projects.py:705`）也各调一次 `versions`，但它们确实只需要版本集合，不算重复。）

优化：让 `summary` 接受已经加载好的行，别在它内部再取一次：

```python
    row = await artifacts.get_or_404(db, project_id=project_id, artifact_id=artifact_id)
    claims = artifacts.claims_of(row)          # 纯函数：原 summary 的内层
    history = await artifacts.versions(db, row.id)
```

即把 `summary` 里 `_claims()` 之前的那次 `select(ProjectArtifact)` 提出来，`summary` 保留一个「按 id 取」的重载给真的只有 id 的调用方。`_claims()` 本身若已经是一条查询，就一并暴露成 `claims_of(row)` 的批量版，路由一次拿齐。

契约：响应体不变（`version`、`delivered_at`、`versions[]` 逐字段相同）。

测试：`test_project_artifacts.py`、`test_artifact_deliverable.py`、`test_artifact_is_the_repository.py` 覆盖现状；新增一条计数用例（照 B 的写法）断言读取单个产物是固定条数，命名 `test_reading_one_artifact_asks_a_fixed_number_of_questions`。

## 三、模块级建议

1. **把「读哪些房间」批量化收进信任边界，作为共享依赖。** 本组三个端点（search `project_context.py:88`、export `export.py:185`、device sessions `projects.py:1250`）各自写了逐房间的鉴权循环，每个房间付一次 `can_access_topic` 的 2~8 条查询；被重复算的那一项（`may_read_project`）与房间无关。B 里给的 `ActorResolver.readable_topic_ids` 是这一处的正解，建议落在 `app/api/auth.py` 里、紧挨 `can_access_topic`，并把三个调用点一并改掉——判据只有一份（`domain/authz/policy.py:25` 的 `authorize_topic_access`），批量化只改查询次数、不改判断。

2. **远端读的「一次解析、一次客户端、一次扇出」应当成为域层的默认形状。** `repository_data`（`domain/project/forge.py:108`）每调一次就重查绑定与凭据、重铸令牌、重建 HTTP 客户端；单次读没问题，树遍历（`committed_blobs`，`forge_files.py:179`）就成了 N 倍。F 里的 `blob_batch` 把它收敛成一次。同样的形状建议覆盖到任何「带着同一份凭据问对方很多次」的地方，并守住那条约束：凭据在 `gather` 之前解析完，`AsyncSession` 不并发。

3. **写路由在响应前提交，作为一条可检查的规则。** `app/core/db.py:171` 的提交在响应之后，这是框架的时序，不是 bug；但每条写路由都得自己决定「这一笔什么时候算数」，而本组十二条选了「不决定」（G）。它已经有专门的文件与五条用例钉着（`test_a_create_commits_before_its_response_is_sent.py`），建议把这条规则做成一个廉价的机械检查（例如一个测试：遍历所有非 GET 路由，断言其调用栈里出现 `commit()`），而不是靠每次评审想起它。

4. **错误语义按 HTTP 的原意分档，而不是都落到 422。** `core/errors.py` 里有 400/401/403/404/409/412/422/500/502/503，独缺 410；而「这个端点没有了」正是 410 的定义（GitHub：https://docs.github.com/en/rest/issues/issues）。K 里加的 `GoneError` 是最小改动。建议顺带盘点其它把「状态」压成「参数错」的地方（`add_memory` 之外，`projects.py:983` 这一档是本组唯一一处）。

5. **请求体一律有 schema。** 本组八个 `body: dict`（L）让 OpenAPI、前端类型与校验三样东西都缺位，而仓库对别的路由是给 schema 的（`domain/project/schemas.py` 已有四个）。建议把「路由签名里不出现 `body: dict`」也做成机械检查——它对 `rename`/`merge` 这类小接口的边际成本接近零。

6. **列表端点统一一套分页协议，并写进文档。** 现在仓库里并存两套（`page_start`/`page_size` 与 `(created_at, id)` 游标），本组五个端点一套都没用（I）。GitHub 的公共约定是 `per_page`/`page` 加 `Link` 头（`rel=prev/next/last/first`，`per_page` 上限 100，https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api）；本仓库的两套都合规，重要的是**选定一套**并在 OpenAPI 描述里写明，让调用方能编程地翻页。建议：时间序的历史（decisions/weeklies）用行值游标，其余用 `page_start`/`page_size`，`total` 一律是集合总数而不是本页条数。

7. **条件请求是廉价的读放大解药。** `app/api/conditional.py` 的 `if_none_match_hits`/`etag_for_json` 已经存在并被 `admin_members.py`、`avatars.py` 使用，而本组里最容易命中的两处（`GET /branch-protection` 的 GitHub 侧附注、`GET /library/raw` 的字节）都没有用。GitHub 对这条有明确背书：命中 304 的条件请求不计入速率限制（https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api）。建议把「有 ETag 的读端点」列成清单逐步补齐，别新造协议。

8. **`GET` 不做写。** `GET /projects/{id}/private-chat`（`projects.py:1009`）今天会 get-or-create。不改动词也能活下去（它本来就是幂等的 get-or-create），但如果要动，唯一与「不另造协议」相容的做法是按 GitHub 的形状来：`POST /user/repos` 这类创建一律是 POST 并返回新资源（https://docs.github.com/en/rest/repos/repos）。本报告不把它列为必改项，只把它记成一处需要显式声明的设计选择。

---

写报告时对本组 47 个接口逐条读过实现与其调用链；判定为「暂无」的 15 条都写明了理由，未发现真实问题的不做包装。GitHub 约定的四条引用均来自 docs.github.com 实读；其余标注「业界通行，未逐条查证」的仅为旁证，不作为结论依据。
