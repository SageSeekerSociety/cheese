# admin 组逐接口分析（65 条）

只读分析，未改任何代码。判断顺序：GitHub REST 惯例 → 项目自己的既定契约
（`backend/design/common/parameters.yaml`、`responses.yaml`，以及 `backend/alembic/versions/a9c4e7f12b60_*.py`
里写下的那套「时间范围读要打索引」的纪律）→ skills.sh / Stripe 等。引不到的写「业界通行，未逐条查证」。

清单与源码的比对：**表里的 method / path / file / line 与源码全部一致**，没有发现行号或前缀错位。
`routes/admin_common.py` 里没有 `APIRouter`（只有 `PlatformAdminDep`），所以它不在任何清单里，这是对的。

---

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | GET | `/admin/feedback` | 可优化 | 分页用 `page_start` 但当 **offset** 用，与 `design/common/parameters.yaml` 里「第一项的 id」两个意思；响应只给 `total`，没有 `has_more`/`next_start` |
| 2 | GET | `/admin/feedback/{feedback_id}` | 暂无 | `visible_row` + `detail_of` 全部批量查（注释、笔记、头像、时间线各一条），无 N+1 |
| 3 | PATCH | `/admin/feedback/{feedback_id}` | 可优化 | 改 priority / assignee **不留任何记录**（只有 security 会写时间线） |
| 4 | POST | `/admin/feedback/{feedback_id}/status` | 暂无 | 同状态幂等、时间线与状态同事务、响应前已 commit（`test_admin_feedback_commit.py` 钉着） |
| 5 | POST | `/admin/feedback/{feedback_id}/notes` | 暂无 | 只增不改的一行，作者与时间在行上 |
| 6 | POST | `/admin/memory/migration/projects/{project_id}/dry-run` | 可优化 | 几分钟的模型循环跑在请求里（无总时限），且无重复提交保护——连点两次付两次模型钱 |
| 7 | GET | `/admin/memory/migration/projects/{project_id}/plans` | 可优化 | 无 limit；为只返回计数却把整行（含 `report`、`files[].content` 全文 JSONB）读进内存 |
| 8 | GET | `/admin/memory/migration/plans/{plan_id}` | 可优化 | `files_preview` 把每个文件的**全文**塞进响应，无上限、无 ETag |
| 9 | POST | `/admin/memory/migration/plans/{plan_id}/approve` | 暂无 | 只认 `settings.memory_migration_reviewer`，判断在服务层且从路由体可达（真的挡得住） |
| 10 | POST | `/admin/memory/migration/plans/{plan_id}/apply` | 可优化 | **操作人丢了**：`apply(plan_id, by=admin)` 的 `by` 函数体里一次都没用，表也没有 `applied_by` 列 |
| 11 | GET | `/admin/memory/reads` | 可优化 | `blocks` 上没有任何以 `created_at` 打头的索引，不带 `project_id` 时是全表顺序扫 |
| 12 | GET | `/admin/gateway/models` | 暂无 | 网关答案 15s 缓存 + 一次 usage 窗口，形状与契约 §3.1 一致 |
| 13 | GET | `/admin/gateway/models/{name}` | 可优化 | `_platform_usage` 为取一行而跑窗口内 top-200 的 `by_model` 聚合（还要排序），且这次读**不在** 15s 缓存里 |
| 14 | POST | `/admin/gateway/models` | 暂无 | 202/校验/审计/缓存失效都齐（`add` → `_record` → `_after_write`） |
| 15 | PATCH | `/admin/gateway/models/{name}` | 暂无 | 合并后就地校验不变式（`_require_price_when_selectable`），config 模型 400 拒 |
| 16 | DELETE | `/admin/gateway/models/{name}` | 暂无 | 审计含 `before`/`after`，失败也落行 |
| 17 | POST | `/admin/gateway/models/{name}/blocked` | 暂无 | 同上，且 config 模型拒绝停用 |
| 18 | GET | `/admin/gateway/projects` | 可优化 | `ProjectService.list_all()` 全平台项目一页吐完，无分页；加载页 = 全部项目 × 每项一行 |
| 19 | PUT | `/admin/gateway/projects/{project_id}/budget` | 暂无 | `project_id` 走 `uuid.UUID` 解析、审计带 before/after、网关错误分 503/502 |
| 20 | GET | `/admin/gateway/audit` | 可优化 | 只有 `limit`，没有游标/offset：第 200 条之前的记录**永远取不到**；也不能按 actor/action 过滤 |
| 21 | GET | `/admin/spaces` | 可优化 | 信封缺 `message`（`{"code":200,"data":{...}}`，全仓其它地方都走 `ok()`）；响应无 `total` |
| 22 | POST | `/admin/spaces/{space_id}/review` | 可优化 | 同一个信封缺 `message` 的问题；审计留在 Space 行上（`reviewed_by`/`reviewed_at`），这一点没问题 |
| 23 | GET | `/admin/stats/feedback` | 可优化 | 为了取一个 `unread`，把 `counts(handle, is_admin=True)` 的七八条查询全跑了（只 `.get("unread")`） |
| 24 | GET | `/admin/stats/usage` | 可优化 | 同一窗口对 `resource_usage` 发了 6–7 条聚合；`totals` 与 `series` 可合并成一条 |
| 25 | GET | `/admin/stats/platform` | 可优化 | `_health_snapshot` 里每次新建 Redis 连接（每次连、每次 `aclose`），见 health 那三条 |
| 26 | GET | `/admin/stats/performance` | 可优化 | 每个请求都反射遍历整张路由表（`_http_endpoints`），而路由表启动后不变 |
| 27 | GET | `/admin/stats/pipeline` | 暂无 | 十几个 `count(*)`，但都在 `accept_cards`/`questions` 这类小表上，代价可接受（模式见模块级第 4 条） |
| 28 | GET | `/admin/stats/product` | 暂无 | 三组都是按天 GROUP BY，窗口必填，无 N+1 |
| 29 | GET | `/admin/stats/integrations` | 暂无 | 四个 `count(*)`（凭据表量级），无窗口是刻意的 |
| 30 | POST | `/admin/subscriptions/device-flows` | 暂无 | 「一座一订阅」409 在服务层兑现，审计落 `subscription.start` |
| 31 | POST | `/admin/subscriptions/device-flows/{flow_id}/poll` | 可优化 | 前端每次轮询都 `SELECT … FOR UPDATE` + 写 `flow_last_poll_at` + 提交，被节流的那次也照写 |
| 32 | POST | `/admin/subscriptions/device-flows/{flow_id}/cancel` | 暂无 | 行锁 + `subscription.cancel` 审计 |
| 33 | GET | `/admin/subscriptions` | 暂无 | 无分页，但这张表天然是「一座一订阅」的个位数量级（`list_all` 仍是全量，见模块级第 5 条） |
| 34 | POST | `/admin/subscriptions/{subscription_id}/refresh` | 暂无 | 行锁内完成，三类结局各自落审计 |
| 35 | GET | `/admin/subscriptions/{subscription_id}/quota` | 暂无 | 传输错误回旧快照（`stale: true`），无快照才 503 |
| 36 | PATCH | `/admin/subscriptions/{subscription_id}/upstream-model` | 暂无 | 显式 null 与「没提」靠 `model_fields_set` 区分，审计落 failed |
| 37 | DELETE | `/admin/subscriptions/{subscription_id}` | 暂无 | 终态必落库、网关失败只影响这次响应，审计落 failed |
| 38 | GET | `/ai/quota` | 可优化 | 读路径会 INSERT（懒建额度行），而 `user_ai_quota.user_id` **没有唯一约束**——并发首次访问会插两行，之后每次 `scalar_one_or_none()` 直接 500 |
| 39 | GET | `/ai/models` | 待确认 | 全模块唯一**没有**任何鉴权依赖的端点（静态列表，风险低，但同模块不一致） |
| 40 | GET | `/ai/conversations` | 可优化 | 参数名驼峰 `pageStart`/`pageSize`（本模块外一律下划线），且这里的 `pageStart` 是**游标 id**，与 `/admin/feedback` 的同名参数（offset）语义相反 |
| 41 | POST | `/ai/conversations` | 可优化 | `payload: dict = Body(...)` 无 schema；`modelId` 收下即丢（死参数）；无幂等键，重试即多一条会话 |
| 42 | GET | `/ai/conversations/{conversationId}` | 可优化 | 一次把该会话**全部**消息塞进响应，无 limit、无游标 |
| 43 | DELETE | `/ai/conversations/{conversationId}` | 待确认 | 204 空体（符合 GitHub DELETE 惯例）与本仓 `{"code","message","data"}` 信封冲突——要留哪种得定 |
| 44 | PATCH | `/ai/conversations/{conversationId}` | 可优化 | `title` 只判「是不是 None」：传 dict/超长串会直达 DB 列；`payload: dict` 无长度校验 |
| 45 | POST | `/ai/chat` | 可优化 | 每个请求都新建一个 `openai.AsyncOpenAI`（新的 httpx 连接池 + TLS 握手）；无显式超时；历史消息不截断 |
| 46 | POST | `/projects/{project_id}/alerts` | 可优化 | 广播按收件人逐条 `user_by_handle` + 逐行 flush+refresh（30 人 ≈ 90 条语句）；agent 重发无幂等保护 |
| 47 | GET | `/projects/{project_id}/alerts` | 可优化 | 无分页；`total` 恒等于这一页的条数（`page(items, len(items))`），一旦加上 limit 就会说谎 |
| 48 | GET | `/projects/{project_id}/inbox` | 可优化 | 同上，`list_inbox` 无 LIMIT |
| 49 | GET | `/projects/{project_id}/alerts/unread-count` | 暂无 | 服务端一条 `count(*)`，正是为了不拉整份列表 |
| 50 | POST | `/projects/{project_id}/alerts/read-all` | 可优化 | 把未读**全部行**读进 ORM 再逐行置 `read=True`，应是一条 `UPDATE` |
| 51 | POST | `/alerts/{notification_id}/read` | 暂无 | `get_or_404` 两次调的是 `session.get`（identity map 命中，不重复发 SQL），授权落在收件人上 |
| 52 | POST | `/alerts/{notification_id}/feedback` | 暂无 | 同上 |
| 53 | POST | `/alerts/{notification_id}/resolve` | 暂无 | `resolved_at` 让它天然幂等；决定记在验证过的调用者名下 |
| 54 | GET | `/spaces/{space_id}/dashboard` | 可优化 | 单请求 ≈ 11N+1 条查询：门里每项目最多 6 条、板子上每项目 5 条（含一条**每项目一条 UPDATE**），且 `list_ids_for_space_tasks` 跑了两次 |
| 55 | GET | `/projects/{project_id}/members/{user_handle}/summary` | 可优化 | 为筛出两小段，把整个项目的非私密话题树全量载入内存；`waiting_on_you` 走无 LIMIT 的 `list_inbox` |
| 56 | GET | `/projects/{project_id}/usage` | 暂无 | 一条 `_agg`，走 `project_id` 索引 |
| 57 | GET | `/projects/{project_id}/credits` | 暂无 | 两条查询（grants + team），字段注释解释了 `source_task_id` 为何是 int |
| 58 | GET | `/projects/{project_id}/contributions` | 可优化 | 对项目**全部历史** block 做 GROUP BY，无时间窗；`by_author` 的键数随作者数无界增长 |
| 59 | GET | `/users/{handle}/profile` | 暂无 | 系列查询都批量、有窗口，`understanding` 只在本人页出 |
| 60 | GET | `/users/{handle}/topics` | 暂无 | `limit` 有上界（≤50）、时间窗半开、日期顺序有校验 |
| 61 | DELETE | `/users/me/understanding/{entry_id}` | 暂无 | 不是自己的、不存在的、别人的一律同一个 404（不可枚举），无 IDOR |
| 62 | GET | `/healthz` | 暂无 | 检查的是「路由模块有没有挂载失败」，形状固定，`{"status":"ok"}` |
| 63 | GET | `/health/detailed` | 可优化 | 每次调用新建一个 Redis 客户端（`from_url` + `ping` + `aclose`）；一次 DB 会话另开。它是 `/readyz` 与 `/admin/stats/platform` 的公共下游 |
| 64 | GET | `/metrics` | 可优化 | 无任何鉴权，公开导出按路由的请求量/耗时与平台计数（业界惯例是只在内网暴露） |
| 65 | GET | `/readyz` | 暂无 | 只有 `_REQUIRED_CHECKS` 能把它压成 503，判定与 `/health/detailed` 同源（代价见 63） |

**统计**：可优化 27 / 暂无 36 / 待确认 2，共 65 条。

---

## 二、详细分析

### 二·0 授权与审计覆盖（管理端逐条核对）

这是本题的先决问题，先给结论再给逐条。

**谁被授权、检查落在哪**：

- 清单第 1–37 条（`/admin/*`）**每一条 handler 的签名上都有 `PlatformAdminDep`**，已逐条核对：`admin_feedback.py:87/140/151/170/189`、`admin_memory.py:88/101/111/121/133/144`、`admin_models.py:105/120/133/148/164/175/196/211/236`、`admin_spaces.py:20/33`、`admin_stats.py:51/65/81/140/159/174/189`、`admin_subscriptions.py:96/124/138/150/160/175/191/211`。没有一条漏挂。
- 这道门做两件事（`admin_common.py:41-77`）：`AdminService.require_admin`（配置里的根管理员 ∪ `platform_admins` 表，403）与 `policy.refuse_management_action`（带 agent 绑定的 actor 一律拒）。**注意 `/admin/*` 不在 `main.py` 的 `_CHEESE_WRITE_PATHS` 白名单里，中间件不看这个前缀**——所以路由体里这一次拒绝就是全部的拒绝，而它确实存在（`admin_common.py:71-77` 在依赖里，先于 handler 执行）。
- 已经验证过的（不是靠假设）：`test_admin_stats.py` / `test_admin_members.py` / `test_admin_models.py` 里有「非管理员 403」的用例。
- 第 38–45 条 `/ai/*`：7 条走 `require_auth_user`（`ai.py:39/68/86/102/117/130/147`），**`GET /ai/models`（`ai.py:57`）一条鉴权都没有**（见第 39 条）。越权面由 `AIChatService` 的 `conv.owner_id != user_id → ForbiddenError` 兜住（`llm/chat_service.py:120/137/151/184`），我看过是每条读改删都判了。
- 第 46–53 条 alerts：收件人经 `ActorResolver.resolve_recipient` 解析，写路径 `allow_anonymous=False`（`alerts.py:51-55/167`）；`/alerts/{id}/*` 先 `get_or_404` 把「人对人、无收件人」的行排除掉，再核收件人——这条在 `notification/services.py:362-374` 的 docstring 里写明了为什么必须在服务层。
- 第 54–61 条 dashboard：`authorize_project`（usage/contributions/member_summary）、`_require_project_access`（credits）、`_signed_in`（profile/topics/understanding）、`_require_board_reader`（space dashboard，且是「至少要在这个板子里一个项目里有权限」而不是「板子 id 就是凭证」）。
- 第 62–65 条 health：**四条全部无鉴权**，任何人都能读池状态、错误原文与指标。

**破坏性动作留没留记录**（逐条核过）：

| 动作 | 路由 | 记录 | 结论 |
|---|---|---|---|
| 模型增删改 / 停用 | `/admin/gateway/models*` | `gateway_admin_audit`：actor、action、target、ok/failed、`before`/`after`（`gateway_models.py:712-738`，成功失败都落） | 有，且最完整 |
| 改项目刹车值 | `PUT …/budget` | 同上 | 有 |
| 订阅完成/刷新/撤销/取消/改上游 | `/admin/subscriptions*` | 同上（自动动作 actor=`system`），快照脱敏（`_audit_snapshot`） | 有 |
| 反馈状态推进 | `POST …/status` | `feedback_timeline`（`by_handle`）与状态同事务 | 有 |
| 反馈改优先级 / 指派人 | `PATCH …/{id}` | **无**（只有 `security` 翻转会写一条时间线，`feedback/services.py:825-829`） | **缺** |
| 反馈内部备注 | `POST …/notes` | `FeedbackNote` 行（作者、时间） | 有 |
| 空间审批 / 驳回 | `POST /admin/spaces/{id}/review` | Space 行上的 `reviewed_by`/`reviewed_at`/`review_reason` | 有 |
| 记忆迁移 出报告 | `POST …/dry-run` | 计划行 `created_by` | 有 |
| 记忆迁移 复核 | `POST …/approve` | `approved_by`/`approved_at` | 有 |
| 记忆迁移 **落笔**（真正改写记忆树） | `POST …/apply` | **只有 `applied_at`，没有 `applied_by`**；`apply()` 收了 `by=` 一次都没用（`migration_service.py:371-441`，全文只有 `row.project_id`），房间里的公告也「谁的名都不点」（`_say_report` 的注释、`_say_applied:491-506`） | **缺** |

**其它安全面**：全仓 `git grep` 过一遍，管理端响应里没有 token/密文（订阅 DTO 在 `_dto` 一处脱敏、`_KEY_FIELD_NAMES` 把 OAuth 三件套也纳入了审计剔除），`account_email` 是管理页有意显示的 PII。速率限制：`/admin/*` 与 `/ai/chat` 都没有限流（`/ai/chat` 每次真花钱，见第 45 条）。

---

### GET /spaces/{space_id}/dashboard

- **现状**：`api/routes/dashboard.py:73-81` 先过 `_require_board_reader`（`dashboard.py:37-70`），再 `DashboardService.space_board`（`domain/dashboard/services.py:502-520`）逐个项目调 `_project_card`（`services.py:58-114`）。
- **问题**：（a）`list_ids_for_space_tasks(space_id)` 被查**两次**——`dashboard.py:66` 与 `services.py:511`；（b）门里 `for project_id in await …: await may_read_project(…)` 是逐项目最多 6 条查询（`auth/project_access.py:58-98`：member、project、user、asker、题目板管理员、排除行），项目排在后面时全跑满；（c）每个项目 5 条查询，其中 `milestones.list_calendar` 里第一句是 **`mark_overdue(project_id)` 一条 UPDATE**（`domain/milestone/repositories.py:16-33/71-86`）——一个 GET 对每项目发一条写语句并持行锁。N=20 的项目板大约是 220 条往返。
- **优化**：把门那条查询复用给服务；四组卡片数据改成按 `IN (ids)` 的批量聚合，`mark_overdue` 合成一条 UPDATE。

```python
# app/api/routes/dashboard.py —— 一次性取 id，门与服务共用（不再查两遍）
@router.get("/spaces/{space_id}/dashboard")
async def space_dashboard(
    space_id: int,
    db: DbSession,
    resolver: ActorResolverDep,
    space_service: SpaceService = Depends(get_space_service),
) -> dict:
    project_ids = await ProjectRepository(db).list_ids_for_space_tasks(space_id)
    await _require_board_reader(
        db, resolver, space_id, space_service, project_ids=project_ids
    )
    return ok(
        await DashboardService(db).space_board(space_id, project_ids=project_ids)
    )
```

```python
# app/domain/milestone/repositories.py —— 新增：一次 UPDATE 翻转所有项目的过期里程碑
async def mark_overdue_many(self, project_ids: Sequence[uuid.UUID]) -> int:
    if not project_ids:
        return 0
    stmt = (
        update(Milestone)
        .where(
            Milestone.project_id.in_(list(project_ids)),
            Milestone.status == MilestoneStatus.upcoming,
            Milestone.due_date.is_not(None),
            Milestone.due_date < datetime.now(UTC),
        )
        .values(status=MilestoneStatus.missed)
        .execution_options(synchronize_session="fetch")
    )
    result = await self._session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)
```

```python
# app/domain/dashboard/services.py —— 批量版卡片：5N 条 → 5 条
    async def space_board(
        self, space_id: int, *, project_ids: list[uuid.UUID] | None = None
    ) -> dict:
        space = await self._spaces.get_by_id(space_id)
        if space is None:
            raise NotFoundError("Space not found")
        ids = (
            project_ids
            if project_ids is not None
            else await self._projects.list_ids_for_space_tasks(space_id)
        )
        cards = await self._cards(ids)
        return {
            "space_id": str(space_id),
            "name": space.name,
            "teams": cards,
            "total": len(cards),
        }

    async def _cards(self, ids: list[uuid.UUID]) -> list[dict]:
        # 顺序与 `list_for_space` 一致（created_at asc, id asc），逐条版的顺序不变。
        projects = await self._projects.list_for_space_by_ids(ids)
        if not projects:
            return []
        ids = [p.id for p in projects]
        # 1) 话题按状态分组
        status_rows = (
            await self._s.execute(
                select(Topic.project_id, Topic.status, func.count())
                .where(Topic.project_id.in_(ids), Topic.is_private.is_(False))
                .group_by(Topic.project_id, Topic.status)
            )
        ).all()
        by_status: dict[uuid.UUID, dict[str, int]] = {i: {s.value: 0 for s in TopicStatus} for i in ids}
        topic_total: dict[uuid.UUID, int] = {i: 0 for i in ids}
        for pid, status, n in status_rows:
            by_status[pid][status.value] = int(n)
            topic_total[pid] += int(n)
        # 2) 最后活跃时间（一次）
        last = dict(
            (
                await self._s.execute(
                    select(Block.project_id, func.max(Block.created_at))
                    .where(Block.project_id.in_(ids))
                    .group_by(Block.project_id)
                )
            ).all()
        )
        # 3) 人/AI 署名混合（一次）
        mix_rows = (
            await self._s.execute(
                select(Block.project_id, Block.author, func.count())
                .where(Block.project_id.in_(ids), participant_blocks())
                .group_by(Block.project_id, Block.author)
            )
        ).all()
        mix: dict[uuid.UUID, dict[str, int]] = {i: {"human": 0, "ai": 0} for i in ids}
        for pid, author, count in mix_rows:
            mix[pid]["ai" if looks_like_agent_handle(author) else "human"] += int(count)
        # 4) 里程：先一次 UPDATE 过期，再一次 SELECT 取所有项目的日历
        await self._milestones.mark_overdue_many(ids)
        upcoming = await self._milestones.list_calendar_many(ids)
        # 5) 拼装用一个纯函数，逐条版与批量版共用同一份字段定义
        return [
            _card(p, topics=topic_total[p.id], by_status=by_status[p.id],
                  last_activity=last.get(p.id), mix=mix[p.id],
                  milestones=upcoming.get(p.id, []))
            for p in projects
        ]
```

- **契约**：响应形状不变（`space_id`/`name`/`teams`/`total`，每张卡片的键一字不改），只是条数从 1+6N+5N 降到 1+1+5。需要新增两个仓储方法（`ProjectRepository.list_for_space_by_ids`、`MilestoneRepository.list_calendar_many`）与一个 `mark_overdue_many`，都是只读/等价语义，无需数据迁移。风险点在「门」那条：`may_read_project` 的判据不做批量化替换（`list_visible_to` 少了「出题者/题目板管理员」两条主张，换成它会**改门**），只在拿到 id 之后省掉重复查 id 的那一条。
- **测试**：`tests/integration/test_admin_stats.py` 的写法（`as_admin` monkeypatch + `client`），新增 `tests/integration/test_space_board_query_count.py`：`test_board_cards_match_the_per_project_reads`（同一份数据下批量版与逐条版逐字段相等）、`test_board_issues_one_overdue_update_per_request`（用 SQLAlchemy 事件计数器断言 UPDATE 条数 == 1）、`test_board_refuses_a_caller_in_none_of_its_projects`（403，保持现有语义）。

### POST /projects/{project_id}/alerts

- **现状**：`api/routes/alerts.py:58-99` → `ProjectNotificationService.create`（`domain/notification/services.py:239-289`）。
- **问题**：（a）广播展开时**逐收件人**调 `user_by_handle`（`services.py:275`）再 `self._repo.add(...)`，而 `add` 每行做一次 `flush()` + 一次 `refresh()`（`notification/repositories.py:197-231`）——30 人的项目一条广播 ≈ 30 SELECT + 30 INSERT(各自 flush) + 30 条 refresh SELECT。（b）这个端点主要给 agent 的 `cheese_notify` 用，agent 的自动续跑会重发同一动作，而**没有幂等保护**：仓库里已经有 `app/domain/idempotency/`，`milestones.py:60-88` 就是这个模式的标准写法。
- **优化**：批量译名 + 一次 flush；收进已有的幂等账本。

```python
# app/domain/notification/services.py
from app.domain.user.services import users_by_handle   # 已有批量版，判据同为 deleted_at IS NULL

        accounts = await users_by_handle(self._session, recipients)   # 1 条查询
        rows = [
            self._repo.build(                      # 新增：只构造，不 flush
                project_id=project_id,
                recipient_handle=handle,
                receiver_id=(user.id if (user := accounts.get(handle)) else None),
                level=level,
                type_=kind,
                title=title,
                body=body,
                topic_id=topic_id,
                payload=payload,
            )
            for handle in recipients
        ]
        await self._repo.add_many(rows)             # 一次 flush，一条 INSERT … VALUES
        return rows
```

```python
# app/domain/notification/repositories.py —— 新增
    def build(self, **kwargs) -> Notification:
        now = datetime.now(UTC)
        return Notification(
            **kwargs, read=False, is_aggregatable=False, finalized=True, version=0,
            metadata_payload=kwargs.get("payload") or {},
            created_at=now, updated_at=now,
        )

    async def add_many(self, rows: Sequence[Notification]) -> None:
        if not rows:
            return
        self._session.add_all(rows)
        await self._session.flush()          # 一条多行 INSERT，不再逐行 refresh
```

```python
# app/api/routes/alerts.py —— 幂等（照 `routes/milestones.py:60-88` 的写法）
from app.api.deps import get_work_runner
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key

    continuation = get_work_runner().continuation_for(body.topic_id)
    key = (
        action_key(continuation, "alert", project_id, body.kind, body.title,
                   body.target_handle or "")
        if continuation
        else None
    )
    if key is not None and not await idem.claim(
        db, key, action="alert", scope_id=str(project_id)
    ):
        return ok(page([], 0))               # 已发过，这一轮不重发
    rows = await ProjectNotificationService(db).create(...)
    if key is not None:
        await idem.record_result(db, key, {"count": len(rows)})
```

- **契约**：响应形状不变（`page([...], n)`）。失败重放那一支返回空数组是唯一的新形状，若要更强可回放 `stored_result`。需要 `tests/` 里补一条「重放不再写行」。
- **测试**：`tests/integration/test_alerts_move_into_the_one_notification_table.py` 旁边新增 `test_a_broadcast_is_one_insert.py`（用事件计数器断言 INSERT 条数不随人数长）与 `test_a_replayed_notify_writes_no_second_row`（同一 continuation 调两次，行数不变）。

### GET /admin/memory/reads

- **现状**：`api/routes/admin_memory.py:142-164` → `domain/memory/reads.py:115-155` 的 `body_reads`。
- **问题**：`body_reads` 的 WHERE 是 `kind='event' AND created_at >= since AND created_at < until AND meta->>'tool'='Read' AND meta->>'detail' LIKE '%…%'`（`reads.py:128-139`），而 `blocks` 的四条索引打头列分别是 `topic_id`、`task_id`、`topic_id`、`topic_id`（`block/models.py:166-210`），`project_id` 那条是单列，`Timestamps`（`domain/common.py:20-24`）不给 `created_at` 建索引。**没有任何一条索引能服务 `created_at` 的范围条件**，所以不带 `project_id` 时这是一次全表顺序扫；带 `project_id` 时走 `ix_blocks_project_id`，但读的是该项目**全部** block（所有 kind、所有时间），窗口一条行都没少读。这条正是 `alembic/versions/a9c4e7f12b60` 修 `resource_usage` 与 `feedback_timeline` 的同一类问题（那份迁移里写着「扫描的行数和窗口无关」）。
- **优化**：加一条 partial 索引（`kind='event'` 的块只占全表一小部分，索引很小，写路径几乎无感，形状照 `ix_blocks_cloud_provisioning`）：

```python
# app/domain/block/models.py —— __table_args__ 里追加
        # `/admin/memory/reads` 按时间窗扫事件块（`kind='event'`）。既有四条索引
        # 打头列都是 topic_id / task_id，范围条件落在 created_at 上时一条都用不上，
        # 计划只能是全表顺序扫。partial：事件块是少数，索引因此保持很小。
        Index(
            "ix_blocks_event_created_at",
            "created_at",
            "project_id",
            postgresql_where=text("kind = 'event'"),
        ),
```

```python
# alembic/versions/xxxxxx_index_blocks_event_by_time.py
def upgrade() -> None:
    op.create_index(
        "ix_blocks_event_created_at",
        "blocks",
        ["created_at", "project_id"],
        postgresql_where=sa.text("kind = 'event'"),
    )

def downgrade() -> None:
    op.drop_index("ix_blocks_event_created_at", table_name="blocks")
```

- **契约**：不动请求/响应（`days`、`since`、`until`、`projects[].days[]` 一字不改）。无数据迁移，只有一条索引；`CREATE INDEX` 在 `blocks` 这种大表上建议 `CONCURRENTLY`（放迁移外手动执行，或在 alembic 里用 `autocommit_block`）。
- **测试**：`tests/unit/test_the_index_fits_its_budget.py` 是这个仓库校验索引的地方，按它的写法加一条断言；再加 `tests/integration/test_memory_body_reads.py::test_body_reads_counts_only_event_blocks_in_the_window`（窗口外一条、非 event 一条、非 Read 工具一条都不许进）。

### POST /ai/chat（以及 `/ai/*` 的服务装配）

- **现状**：`api/routes/ai.py:25-33` 的 `get_chat_service` 每个请求调一次 → `AIChatService.__init__`（`domain/llm/chat_service.py:66-69`）里 `openai.AsyncOpenAI(api_key=…, base_url=…)` **就地新建一个客户端**。
- **问题**：`AsyncOpenAI` 每次构造都会建一个自己的 `httpx.AsyncClient`（连接池 + TLS 上下文），所以每次对话都是一次新的 TCP/TLS 握手，连接无法复用；而且这个客户端随请求对象一起被丢弃，从来没有 `aclose()`（依赖里也没人收）。另外 `chat()` 的 `self._client.chat.completions.create(...)`（`chat_service.py:202-207`）**没有传 `timeout`**，用的是 SDK 默认 10 分钟；`list_by_conversation` 取全量历史直接当 prompt（`chat_service.py:194-198`），会话越长 prompt 越大且没有截断/窗口。
- **优化**：客户端做成进程级单例（照 `deps.get_llm_gateway` 那种「配置一处、实例一处」的做法），并把超时写死在调用上。

```python
# app/domain/llm/chat_service.py
from functools import lru_cache

@lru_cache(maxsize=1)
def _shared_client() -> openai.AsyncOpenAI:
    """整个进程一个客户端：每次请求新建一个等于每次重开连接池 + TLS 握手，
    而且没人会去 aclose 它。配置变了重启进程即可（和 `deps.get_llm_gateway` 同规矩）。"""
    return openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.ai_chat_timeout_s,      # 新增配置，默认 60
        max_retries=1,
    )

class AIChatService:
    def __init__(self, conversation_repo, message_repo, quota_repo) -> None:
        ...
        self._client = _shared_client()
```

```python
# chat_service.py —— 历史只带最近 N 条（新增配置，默认 20）
        history = await self._message_repo.list_by_conversation(
            conversation_id, limit=settings.ai_chat_history_limit
        )
```

- **契约**：响应不变（`conversationId`/`message`/`tokensUsed`/`seuConsumed`/`quotaRemaining`）。历史截断是**行为**变化（长会话的上下文变短），要单独提一句，并让 `list_by_conversation` 保持原签名、只加可选 `limit`。
- **测试**：`tests/integration/test_ai_chat.py` 里加 `test_the_openai_client_is_built_once`（monkeypatch `openai.AsyncOpenAI`，连打两次断言只构造一次）与 `test_chat_uses_a_bounded_history`。

### GET /admin/feedback

- **现状**：`api/routes/admin_feedback.py:84-133`，签名里是 `page_start: int = Query(default=0, ge=0)` / `page_size`，主体交给 `service.list_admin(..., limit=page_size, offset=page_start)`（`admin_feedback.py:115-125`），响应 `ok({**page(items, total), "counts": …})`。
- **问题**：`page_start` 在本仓**有两套语义**。`design/common/parameters.yaml` 里 `PageStart` 的定义是「ID of the first item on the page」（游标 id），`/ai/conversations` 也是这么用的（`llm/repositories.py:122-123` 是 `id <= page_start`）；而这里把它当**偏移量**（`offset=page_start`）。同一个参数名在两个端点里意思相反，而 `page()`（`api/response.py:21-22`）只给 `{data, total}`：客户端无法判断有没有下一页，只能拿 `total` 和 `offset` 自己算——这正是 GitHub 要用 Link 头 / `has_more`+游标（<https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api>）解决的问题；偏移分页在「新反馈不断插入」的分诊队列里还会让翻页漂移。
- **优化**：保留现有参数（不破前端），补一个明确的游标出口；把「本页还有没有」明说出来。

```python
# app/api/response.py
def page(items: list[Any], total: int, *, has_more: bool | None = None) -> dict[str, Any]:
    """`has_more` 是给游标分页用的：客户端不该靠 total - offset 自己推。
    不传时保持原形状（老调用点一字不改）。"""
    out: dict[str, Any] = {"data": items, "total": total}
    if has_more is not None:
        out["has_more"] = has_more
    return out
```

```python
# app/api/routes/admin_feedback.py
    rows, total = await service.list_admin(..., limit=page_size, offset=page_start, ...)
    return ok(
        {
            # page_start 在这里是偏移量（与 /ai/conversations 的游标语义不同名不同义），
            # has_more 让客户端不必自己算，也给以后换成游标留了出口。
            **page(
                await _cards(service, rows, handle=handle),
                total,
                has_more=page_start + len(rows) < total,
            ),
            "counts": await service.counts(handle=handle, is_admin=True),
        }
    )
```

- **契约**：**纯增量**（多一个 `has_more` 键），`data`/`total`/`counts` 一字不改，前端不改也不坏。真正的语义统一（把 `/admin/feedback` 的 offset 改成游标）是破坏性的，不建议在同一次里做；如要做，走「新参数名 + 旧参数保留一个版本」。
- **测试**：`tests/contract/` 下有既有的契约测试写法；新增 `tests/integration/test_admin_feedback.py::test_page_tells_the_client_whether_more_follow`（`page_size=1` 连翻两页，第一页 `has_more=True` 且 `len(data)==1`，最后一页 `False`）。

### GET /admin/stats/feedback

- **现状**：`domain/platform_stats/services.py:58-100`。第 72–73 行连着两次读：
  `board = await self._feedback.admin_board_counts()` 与 `mine = await self._feedback.counts(handle=handle, is_admin=True)`，而第 86 行只用了 `mine.get("unread", 0)`。
- **问题**：`counts()`（`domain/feedback/services.py:352-390`）为了这一个键会跑：`public_counts()`（4 条 count，`repositories.py:1638-1646` 那种）+ `unassigned_count()` + `get_read_state()` + `count_activity_since()` + 一次 `is_admin()`（`services.py:386`）。**其中前四个的结果在这个端点上全被丢掉**——一张本来就大的表上白扫四到五遍。`admin_board_counts` 那边又自己跑了 12 条 count（`repositories.py:1628-1663`），两次调用之间没有共享任何东西。
- **优化**：把「未读」这一个数拆成自己的方法，`counts()` 改为调它，`platform_stats` 直接调它。行为对 `counts()` 的其它调用者完全不变。

```python
# app/domain/feedback/services.py —— 新增：未读数自己的入口（判据与原来那一支逐字相同）
    async def unread_for(self, *, handle: str | None, is_admin: bool = False) -> int:
        """铃铛上的那个数。`counts()` 的 `unread` 那一支与这里是同一份实现，
        看板只要这一个数时不必连带把四栏计数也数一遍。"""
        if not handle:
            return 0
        state = await self._repo.get_read_state(handle)
        since = state.last_read_at if state else None
        return await self._repo.count_activity_since(
            [handle], since, is_admin=is_admin or await self.is_admin(handle)
        )

    async def counts(self, *, handle, is_admin=False) -> dict[str, int]:
        counts = await self._repo.public_counts()
        if is_admin:
            counts["unassigned"] = await self._repo.unassigned_count()
        counts["unread"] = await self.unread_for(handle=handle, is_admin=is_admin)
        return counts
```

```python
# app/domain/platform_stats/services.py
        board = await self._feedback.admin_board_counts()
        unread = await self._feedback.unread_for(handle=handle, is_admin=True)
        ...
            "unread": unread,
```

- **契约**：响应一字不改（`total`/`columns`/`status`/`unread`/`series`/`prev`）。无迁移。
- **测试**：`tests/integration/test_admin_stats.py` 里加 `test_feedback_stats_does_not_count_the_tabs_twice`（用事件计数器：`/admin/stats/feedback` 触发的 `SELECT count` 数量 ≤ 一个上界，且 `unread` 与 `/admin/feedback` 的 `counts.unread` 相等）。

### GET /admin/stats/usage

- **现状**：`domain/platform_stats/services.py:102-147`，对同一窗口依次调 `platform_totals(since,until)`、`platform_totals(prev_since,since)`、`platform_series`、`top_projects`、`by_model`、`by_route`，再加 `gaps.credits_burnout(days)`（内部 `_burn_rate(since,until)` 又按 `route` 聚合一次，`gaps.py:114-130`）。
- **问题**：同一窗口的 `resource_usage` 上发了 **6 条**（含 `credits_burnout` 的 1 条），而 `platform_totals` 与 `platform_series` 读的是**完全相同**的行集——`totals` 就是 `series` 里各天之和（`repositories.py:163-226`，两边窗口条件逐字相同）。`by_route`（`repositories.py:271-304`）与 `_burn_rate`（`gaps.py:114-130`）也是同一行集、同一个 `GROUP BY route`。`resource_usage` 是全平台增长最快的表，而这两条索引（`ix_resource_usage_created_at`）每次都要走一遍。
- **优化**：`totals` 从 `series` 求和得出（`unpriced_tokens` 一并挪进 series 的 select），`by_route` 的结果传给 `credits_burnout` 复用。

```python
# app/domain/usage/repositories.py —— platform_series 多带一列 unpriced
        stmt = (
            select(
                day.label("day"),
                func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
                func.count(),
                func.coalesce(func.sum(ResourceUsage.cost_usd), 0.0),
                func.coalesce(unpriced_tokens(), 0),
            )
            .where(ResourceUsage.created_at >= since, ResourceUsage.created_at < until)
            .group_by(day).order_by(day)
        )
        ...
            row[0].date(): {"tokens": int(row[1]), "calls": int(row[2]),
                            "cost_usd": float(row[3]), "unpriced_tokens": int(row[4])}
            for row in rows
```

```python
# app/domain/platform_stats/services.py
        raw = await self._usage.platform_series(since=since, until=until)
        prev_totals = await self._usage.platform_totals(since=prev_since, until=since)
        # totals 就是 series 的和（两边窗口条件逐字相同）——不再单独扫一遍这张表。
        totals = {
            "tokens": sum(r["tokens"] for r in raw.values()),
            "calls": sum(r["calls"] for r in raw.values()),
            "cost_usd": sum(r["cost_usd"] for r in raw.values()),
            "unpriced_tokens": sum(r["unpriced_tokens"] for r in raw.values()),
        }
        ...
        routes = await self._usage.by_route(since=since, until=until)
        ...
            # by_route 与燃烧速率读的是同一行集、同一个 GROUP BY route，传下去复用。
            "credits": await self._gaps.credits_burnout(days=days, by_route=routes),
```

- **契约**：响应形状不变（`totals` 的四个键、`series` 的三条线、`by_route` 的行一字不改）。数值上 `totals` 由同一批行相加得出，与原来那条独立聚合相等（浮点相加的舍入差异在 `float` 上可忽略，但值得在测试里断言相等）。`credits_burnout` 新增一个必填 kwarg，调用点只有这一处。
- **测试**：`tests/integration/test_admin_stats_new_sections.py` 有现成的造数与断窗口边界的写法；加 `test_usage_totals_equal_the_series_sum`（造跨越窗口边界的数据，断言 `totals` 与 `sum(series)` 相等，且窗口外那条不计入）。

### PATCH /admin/feedback/{feedback_id}

- **现状**：`api/routes/admin_feedback.py:146-162` → `FeedbackService.patch_admin`（`domain/feedback/services.py:808-831`）。
- **问题**：`priority` 与 `assignee_handle` 的改动**不留任何记录**——`set_priority` / `set_assignee` 只改列，只有 `security` 翻转会 `append_timeline`（`services.py:825-829`，注释里说明了「这是提交者看得见的路由决定」）。于是「谁把这条从 urgent 降成 low、谁改了指派」在库里没有答案，而这一页是分诊台：指派本身就是一条要被人看见的决定。同类的网关写操作全都落了 actor（`gateway_models._record`），管理端内部反而缺一块。
- **优化**：照 `security` 那一支的既有形状，给 priority / assignee 也写时间线（同一事务，事故面与状态推进一致）。

```python
# app/domain/feedback/services.py
        if body.priority is not None and body.priority != row.priority:
            await self._repo.set_priority(row, body.priority)
            # 和 security 那条同一个理由：改优先级是分诊里的一个动作，不是一次编辑。
            await self._repo.append_timeline(row.id, row.status, by_handle)
        if body.assignee_handle is not None and (body.assignee_handle or None) != row.assignee_handle:
            await self._repo.set_assignee(row, body.assignee_handle or None)
            await self._repo.append_timeline(row.id, row.status, by_handle)
```

- **契约**：响应形状**不变**（`_detail` 里多出的时间线条目落在已有的 `timeline` 数组里，键不变）。前端如果按「时间线条数」做过度推断需要知道，但语义上这是修正。**状态码不变**。无迁移。
- **测试**：`tests/unit/test_feedback_sort_ordering.py` 邻近新增 `tests/integration/test_admin_feedback.py::test_changing_the_assignee_lands_in_the_timeline`（改一次指派 → 时间线多一条且 `by_handle` 是管理员；同值重复提交不写第二条）。

### POST /admin/memory/migration/plans/{plan_id}/apply

- **现状**：`api/routes/admin_memory.py:130-139` → `MemoryMigrationService.apply(plan_id, by=admin)`（`domain/memory/migration_service.py:371-441`）。
- **问题**：**`by` 收下就丢了**。函数体里只出现 `row.project_id`（`migration_service.py:384-434`），`MemoryMigrationPlan` 也只有 `created_by` 与 `approved_by`（`memory/models.py:384-389`），**没有 `applied_by`**；`_plan_out` 因此也给不出这一项。而这是整个后台里最重的一次写：它按计划改写项目的记忆树（`store.write` 写文件与索引，`updated_by=MIGRATION_AUTHOR` 这个常量，不是操作人）。房间公告也不点名（`_say_applied:491-506`，`_say_report` 的注释写着「谁的名都不点」）。**结论：这次写没有「谁干的」**。
- **优化**：加一列 + 落一次（列有默认值，不用回填）。

```python
# app/domain/memory/models.py —— MemoryMigrationPlan 追加
    #: 落笔的人。**复核人是另一列**（`approved_by`）：复核只认一个人，落笔是任何
    #: 平台管理员都能点的动作，两者不是同一件事，所以不能共用一列。
    applied_by: Mapped[str] = mapped_column(String(64), default="")
```

```python
# app/domain/memory/migration_service.py —— apply() 里落上操作人
        row.status = MemoryMigrationStatus.applied.value
        row.applied_at = datetime.now(UTC)
        row.applied_by = by
        row.summary = self._summary_of(row)
        await self._session.flush()
        await self._say_applied(gathered, row, written, by=by)
```

```python
# app/api/routes/admin_memory.py —— _plan_out 里带出来（纯增量键）
        "approved_by": row.approved_by,
        "applied_by": row.applied_by or None,
```

- **契约**：`_plan_out` 多两个键（`applied_by`），响应形状**只增不改**，前端不改也不坏。要一条 alembic 迁移（`op.add_column("memory_migration_plans", sa.Column("applied_by", sa.String(64), nullable=False, server_default=""))`），存量行回填成空串=「不知道」，这是诚实的默认。`apply()` 的签名不变。
- **测试**：`tests/unit/test_migration_collision_notice.py` 旁边加 `tests/integration/test_memory_migration_audit.py::test_apply_records_who_applied_it`（admin A 出报告、复核人点头、admin B 落笔 → `applied_by == "B"`，且与 `approved_by` 是两个 handle）。

### GET /health/detailed（以及 /readyz、/admin/stats/platform 的同一处）

- **现状**：`api/routes/health.py:42-55`，其中 `_check_redis`（`health.py:87-97`）每次 `AsyncRedis.from_url(settings.redis_url)` → `ping()` → `aclose()`。
- **问题**：每次调用新建一条 Redis 连接（还要建连接池对象），读完立刻关掉——而 `/readyz`（`health.py:107-126`）每次都调 `detailed_health_check()`，也就是**每一次健康探针**都重建一次 Redis 连接；`/admin/stats/platform` 也会经 `_health_snapshot`（`platform_stats/services.py:263-286`）走到同一条路。DB 那边每次另起一个会话（`health.py:75-84`），那是可接受的（池是复用的），Redis 这条不是。
- **优化**：进程级一个客户端，照 `cache` 那种「一处配置、一处实例」的写法；测试靠依赖覆盖换掉它。

```python
# app/api/routes/health.py
from functools import lru_cache

@lru_cache(maxsize=1)
def _redis() -> AsyncRedis:
    """健康检查专用客户端：进程一个，连接池由它自己复用。
    每次 `from_url` 再 `aclose` 等于每个探针重建一条连接。"""
    return AsyncRedis.from_url(settings.redis_url)

async def _check_redis() -> dict[str, Any]:
    try:
        await _redis().ping()
        return {"status": "up"}
    except Exception as e:
        logger.warning("Redis health check failed: %s", e)
        return {"status": "down", "error": str(e)}
```

- **契约**：响应形状一字不改（`{"status", "checks": {"database", "redis", "event_loop"}}`）。注意：`aclose()` 不再调用是有意的（客户端活到进程结束），进程退出时由 redis-py 的池回收。
- **测试**：`tests/test_health.py`（httpx + ASGITransport 直打，无需库）加 `test_detailed_health_reuses_one_redis_client`：monkeypatch `AsyncRedis.from_url`，连打两次 `/health/detailed`，断言只构造一次。

### GET /metrics

- **现状**：`api/routes/health.py:100-104`，直接返回 `registry.export()`，签名里没有任何依赖。
- **问题**：任何人无需任何凭据即可读到按路由的请求量、`error_count`、2xx/3xx/4xx/5xx 分布、分位数与 `uptime_seconds`（`core/metrics.py` 的 `export()`），也就是平台的流量画像与失败率。这是判断「谁在打/什么时候打」的现成材料。惯例上 Prometheus 端点只在内网暴露或需要 token；GitHub 这类公开 API 干脆不暴露它。
- **优化**：给一条最省的判据即可（本仓库已有 `settings` 与内部调用方），或交给反代挡：

```python
# app/api/routes/health.py
from fastapi import Header
from app.core.errors import ForbiddenError

@router.get("/metrics", summary="Application metrics")
async def get_metrics(
    x_metrics_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """未配置 `settings.metrics_token` 时保持现状（本地/内网部署不为难）；
    配了就要求 `X-Metrics-Token` 对上——`/metrics` 的标签里有按路由的流量与失败率，
    不该是不带凭据就能读的公开面。"""
    if settings.metrics_token and x_metrics_token != settings.metrics_token:
        raise ForbiddenError("需要指标访问令牌")
    from app.core.metrics import registry
    return registry.export()
```

- **契约**：默认部署行为不变（新增配置项省略即旧行为）；配了 token 的部署多一个 403 分支，返回体形状不变。若这一分支不想引入，退一步：在反代上把 `/metrics` 限制到内网来源，代码不动。
- **测试**：`tests/test_health.py` 加两条：`test_metrics_is_open_when_no_token_is_configured` 与 `test_metrics_refuses_a_wrong_token`。

### GET /admin/memory/migration/plans/{plan_id} 与 …/plans

- **现状**：`api/routes/admin_memory.py:98-115`（列表）与 `:108-115`（详情），序列化在 `_plan_out`（`admin_memory.py:51-82`）；读在 `MemoryMigrationService.plans_of` / `get_or_404`（`migration_service.py:351-367`）。
- **问题**：（a）列表为只返回 `len()` 计数（`admin_memory.py:57-61`），却用 `select(MemoryMigrationPlan)` 把整行读进内存——包括 `report`（全文）、`sources`（**旧记忆正文原文**，`models.py:373`）、`files`（每个文件的 `content`，`models.py:379`）。一次搬迁的报告动辄几十到几百 KB，`plans_of` 又是无 limit 的全量列表。（b）详情把 `files_preview` 里每个文件的 `content` 全文塞进响应（`admin_memory.py:71-79`），没有上限也没有 ETag——人复核时看的其实是 `report`，正文只在极少数时候需要。
- **优化**：列表用 `load_only` 只取计数需要的列（SQLAlchemy 的 `deferred`/`load_only` 会把未取列变成懒加载，注意不要在循环里触发它——只取真正要用的列更稳）；详情把正文改成显式索取。

```python
# app/domain/memory/migration_service.py
    async def plans_of(self, project_id: uuid.UUID) -> list[MemoryMigrationPlan]:
        """这个项目搬过几次。**不取大列**：列表只数个数，`report`/`sources`/
        `files` 是详情才读的东西（`sources` 存的是旧记忆正文原文）。"""
        rows = await self._session.scalars(
            select(MemoryMigrationPlan)
            .options(
                load_only(
                    MemoryMigrationPlan.id,
                    MemoryMigrationPlan.project_id,
                    MemoryMigrationPlan.status,
                    MemoryMigrationPlan.created_by,
                    MemoryMigrationPlan.approved_by,
                    MemoryMigrationPlan.summary,
                    MemoryMigrationPlan.created_at,
                    MemoryMigrationPlan.approved_at,
                    MemoryMigrationPlan.applied_at,
                )
            )
            .where(MemoryMigrationPlan.project_id == project_id)
            .order_by(MemoryMigrationPlan.created_at.desc(), MemoryMigrationPlan.id.desc())
            .limit(100)          # 上限：一个项目不会有 100 次搬迁，而这是个全量列表
        )
        return list(rows.all())
```

- **契约**：`_plan_out(with_report=False)` 依赖 `len(row.files)` / `len(row.source_ids)` 等计数——`load_only` 之后这些列是懒加载，所以要么把它们留在 `load_only` 名单里（`source_ids`/`files`/`suggestions`/`indexes` 是计数的来源，仍然是大列），要么按下面这样只取长度：

```python
# 更强的做法：直接用列表达式取 jsonb 的长度，行里不进大列
from sqlalchemy import func
        stmt = select(
            MemoryMigrationPlan.id, MemoryMigrationPlan.project_id,
            MemoryMigrationPlan.status, MemoryMigrationPlan.created_by,
            MemoryMigrationPlan.approved_by, MemoryMigrationPlan.summary,
            MemoryMigrationPlan.created_at, MemoryMigrationPlan.approved_at,
            MemoryMigrationPlan.applied_at,
            func.jsonb_array_length(MemoryMigrationPlan.files).label("files"),
            func.jsonb_array_length(MemoryMigrationPlan.suggestions).label("suggestions"),
            func.jsonb_array_length(MemoryMigrationPlan.indexes).label("indexes"),
            func.jsonb_array_length(MemoryMigrationPlan.source_ids).label("sources"),
        ).where(...)
```

（注意这三列在模型里是 `JSON` 类型；Postgres 上 `jsonb_array_length` 可用，但需要 `files` 实际是 jsonb——若是 `json` 类型则先 `cast(..., JSONB)`。落地前按 `\d memory_migration_plans` 确认。）

- **契约**：列表响应形状不变（同一个 `_plan_out` 的键）。详情若要改 `files_preview` 的形状（只给摘要、正文按需 `?include=content`）**就是破坏性的**，必须跟前端一起改；建议先只做列表那一半。无迁移。
- **测试**：`tests/integration/test_admin_stats.py` 同风格的 `tests/integration/test_memory_migration_plans.py::test_the_plans_list_does_not_load_the_report_bodies`（造两条计划，用 `sqlalchemy.event` 断言取回的列里没有 `report`，或断言响应大小与文件数无关）。

### GET /admin/gateway/audit

- **现状**：`api/routes/admin_models.py:233-244` → `GatewayAdminModels.audit(limit)`（`agent/gateway_models.py:444-467`），`select(...).order_by(created_at.desc()).limit(limit)`，`limit ≤ 200`。
- **问题**：只有「最近 N 条」，没有游标或 offset：第 N 条之前的记录（比如上周那次删模型）**在 API 上取不到**，而这张表是写审计、只会增长（`ix_gateway_admin_audit_created_at` 是 DESC 索引，正好够做游标）。也不能按 `actor_handle` / `action` / `target` 过滤——「这个模型被谁改过」是最常问的一句。GitHub 的审计日志用游标 + `per_page`（<https://docs.github.com/en/rest/orgs/orgs#get-the-audit-log-for-an-organization>）。
- **优化**：加游标（按 `created_at DESC, id DESC` 的复合游标）与几个精确过滤。

```python
# app/domain/agent/models.py —— 现有的 DESC 索引补上 id 作为并列时的次序
    __table_args__ = (
        Index("ix_gateway_admin_audit_created_at", text("created_at DESC"), text("id DESC")),
    )
```

```python
# app/domain/agent/gateway_models.py
    async def audit(
        self,
        *,
        limit: int,
        before: datetime | None = None,
        actor: str | None = None,
        action: str | None = None,
        target: str | None = None,
    ) -> dict:
        stmt = select(GatewayAdminAudit).order_by(
            GatewayAdminAudit.created_at.desc(), GatewayAdminAudit.id.desc()
        )
        # 游标：只看比 before 更早的。`created_at` 可能并列，所以 (created_at, id) 一起比。
        if before is not None:
            stmt = stmt.where(GatewayAdminAudit.created_at < before)
        if actor:
            stmt = stmt.where(GatewayAdminAudit.actor_handle == actor[:64])
        if action:
            stmt = stmt.where(GatewayAdminAudit.action == action[:32])
        if target:
            stmt = stmt.where(GatewayAdminAudit.target == target[:128])
        rows = (await self._db.scalars(stmt.limit(limit))).all()
        return {"items": [...], "next_before": rows[-1].created_at.isoformat() if len(rows) == limit else None}
```

```python
# app/api/routes/admin_models.py
@router.get("/audit")
async def audit_log(
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None),
    actor: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=32),
    target: str | None = Query(default=None, max_length=128),
) -> dict:
    return ok(await _answered(service.audit(
        limit=limit, before=before, actor=actor, action=action, target=target
    )))
```

- **契约**：纯增量（`actor`/`action`/`target`/`before` 都是可选，`items` 的键不变，多一个 `next_before`）。唯一需要迁移的是索引形状（把 `id` 加进去，可省——现有一条索引已经能服务 `created_at DESC` 的范围扫，`id` 只在同毫秒并列时影响顺序稳定性）。**这个表还没有保留期/清理**（只增），长期是运营问题，不是这次要改的。
- **测试**：`tests/unit/test_gateway_admin.py` 旁边加 `tests/integration/test_admin_models.py::test_audit_pages_backwards_and_filters_by_actor`（造 3 条审计，`limit=2` 拿两页且第二页不比第一页新；`actor=` 过滤只回那一人的）。

### POST /admin/subscriptions/device-flows/{flow_id}/poll

- **现状**：`api/routes/admin_subscriptions.py:120-131` → `SubscriptionService.poll_flow`（`domain/subscription/services.py:196-329`）。
- **问题**：前端每隔几秒轮询一次，而每次进服务就先 `get_locked(flow_id)`（`services.py:203`，`repositories.py:33-45` 是 `SELECT … FOR UPDATE`）——**被节流的那次也照样加行锁**，而且 `sub.flow_last_poll_at = now`（`services.py:213`）会让每一轮都产生一次 UPDATE 与一次提交（`_record` 之外，`get_db` 收尾 commit 也会写）。一次授权流程几十轮轮询，等于几十次写 + 几十次锁等待，而其中绝大多数只是「还没好」。
- **优化**：节流判定不需要写锁——先做一次非锁定读，被节流就直接返回；只有真要去问上游时才拿锁。

```python
# app/domain/subscription/services.py
    async def poll_flow(self, *, handle: str, flow_id: uuid.UUID) -> dict:
        # 快路：节流窗口内的轮询不拿行锁、不写库 —— 前端每几秒来一次，
        # 唯一要做的事就是回答「还没好」，而 FOR UPDATE 与 UPDATE 都只为真轮询服务。
        peek = await self._repo.get(flow_id)          # 非锁定读
        if peek is None or peek.status != "pending":
            raise NotFoundError("这个授权流程不存在或已结束")
        now = _utcnow()
        if peek.flow_expires_at is not None and peek.flow_expires_at <= now:
            peek.status = "flow_expired"
            _clear_flow(peek)
            return {"state": "expired"}
        if _poll_throttled(peek.flow_last_poll_at, now):
            return {"state": "pending"}

        sub = await self._repo.get_locked(flow_id)    # 真要去上游了才拿锁
        if sub is None or sub.status != "pending":
            raise NotFoundError("这个授权流程不存在或已结束")
        if sub.flow_expires_at is not None and sub.flow_expires_at <= now:
            sub.status = "flow_expired"
            _clear_flow(sub)
            return {"state": "expired"}
        now = _utcnow()
        if _poll_throttled(sub.flow_last_poll_at, now):   # 锁内复检，语义与原来一致
            return {"state": "pending"}
        sub.flow_last_poll_at = now
        ...
```

- **契约**：响应三态一字不改（`pending`/`complete`/`expired`），过期与终态的判断顺序不变（锁内复检把并发的那一支守住）。`flow_last_poll_at` 的写入频率下降，但语义（多久算「刚问过」）不变。
- **测试**：`tests/integration/test_admin_subscriptions.py` 旁边加 `tests/unit/test_subscription_flow.py::test_a_throttled_poll_does_not_ask_for_a_row_lock`（monkeypatch 仓储记录 `get_locked` 调用次数：节流窗口内连打两次，第二次不调 `get_locked`）。

### GET /admin/stats/performance

- **现状**：`api/routes/admin_stats.py:137-153`，第 152 行是 `endpoints = list(_http_endpoints(request.app))`，而 `_http_endpoints`（`admin_stats.py:93-134`）对每个 `app.routes` 条目递归展开 `_IncludedRouter` 壳，逐条 `APIRoute` 取 `methods`。
- **问题**：整张路由表的反射遍历**每个请求都做一遍**，而路由表在 `main.py` 的 `_discover_routers(app)`（`main.py:339-371`，import 时跑一次）之后不再变化。这个端点还返回**每一条**注册路由（`platform_stats/performance.py:43-65`，最多 2000 行）——也就是说，为了看「哪条最慢」，每次都把 190+ 条路由重新枚举、并与内存里的样本合并、排序、序列化。
- **优化**：路由表在启动后枚举一次，挂到 `app.state`；路由读它，读不到才回退到遍历（测试里动态加路由的场景仍然成立）。

```python
# app/main.py —— 在 `loaded_routers = _discover_routers(app)` 之后
from app.api.routes.admin_stats import _http_endpoints  # 或者把这个函数挪到 api/ 下的公共位置

app.state.http_endpoints = _http_endpoints(app)
```

```python
# app/api/routes/admin_stats.py
@router.get("/performance")
async def performance_stats(
    service: StatsServiceDep,
    handle: PlatformAdminDep,
    request: Request,
) -> dict:
    # 路由表 import 之后就不再变，遍历一次挂在 `app.state` 上；读不到才回退（测试会加路由）。
    endpoints = getattr(request.app.state, "http_endpoints", None) or list(
        _http_endpoints(request.app)
    )
    return ok(await service.performance(routes_registered=endpoints))
```

- **契约**：响应一字不改（`routes`/`routes_registered`/…）。无迁移。风险：`_http_endpoints` 现在被 `admin_stats.py` 定义，挪去 `main.py` 引用会形成路由→main 的反向依赖（`main.py` 里已经有 `from app.api.routes.admin_stats import ...` 之外的先例吗？没有，方向是 main→routes）。**更稳的做法**：把 `_http_endpoints` 挪到 `app/api/` 下一个中立模块（例如 `app/api/routes_table.py`），`main.py` 与 `admin_stats.py` 都从那里取。
- **测试**：`tests/integration/test_admin_stats_new_sections.py` 里加 `test_performance_lists_every_registered_route`（现有断言应该已经覆盖）+ 一条「枚举只发生一次」的计数断言，或至少在测试里动态挂一条路由后断言它仍然出现（这条正是回退分支的意义）。

### POST /projects/{project_id}/alerts/read-all

- **现状**：`api/routes/alerts.py:154-172` → `ProjectNotificationService.mark_all_read` → `NotificationRepository.mark_all_read_in_project`（`notification/repositories.py:317-328`）。
- **问题**：`select(Notification).where(...read=False)` 把**所有未读行**取进 ORM，再逐行 `row.read = True`，`flush()` 时逐行 UPDATE。未读 500 条就是 500 行 + 500 条 UPDATE（外加一次 `updated_at` 逐行回写）。一次「全部已读」在语义上就是一条 UPDATE。
- **优化**：

```python
# app/domain/notification/repositories.py
    async def mark_all_read_in_project(
        self, project_id: uuid.UUID, *, recipient_handle: str
    ) -> int:
        """全部标记已读 —— 一条 UPDATE，不再把每一行读进 ORM 再逐行置位。
        没拍板的决策请求照样留在收件箱里（判据不变：只置 `read`）。"""
        stmt = (
            update(Notification)
            .where(
                Notification.project_id == project_id,
                Notification.recipient_handle == recipient_handle,
                Notification.deleted_at.is_(None),
                Notification.read.is_(False),
            )
            .values(read=True, updated_at=datetime.now(UTC))
            .execution_options(synchronize_session="fetch")
        )
        result = await self._session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)
```

- **契约**：响应不变（`{"unread": …, "marked": n}` 里的 `marked` 仍是改动的条数，由 `rowcount` 给出，与原来 `len(items)` 相等）。无迁移。`synchronize_session="fetch"` 保持会话内对象与库一致（本请求后续若再读同一批行不会拿到旧值）。
- **测试**：`tests/contract/test_notifications_unread_count_auth.py` 邻近加 `tests/integration/test_alerts_read_all.py::test_read_all_is_one_update`（事件计数断言 UPDATE 条数 == 1，且 `marked` 等于未读数、`read-all` 后再读未读数为 0）。

### GET /projects/{project_id}/members/{user_handle}/summary

- **现状**：`api/routes/dashboard.py:84-112` → `DashboardService.member_summary`（`domain/dashboard/services.py:116-197`）。
- **问题**：为了挑出「他建的话题」和「他在忙的话题」，把该项目**整棵非私密话题树**载入内存（`services.py:134` 的 `list_for_project`，返回完整 `Topic` ORM 行），再在 Python 里按 `t.created_by == user_handle` 和 `t.id in worked_topic_ids` 两个条件过滤（`services.py:135-158`）；`worked_topic_ids` 那条 DISTINCT 查询（`services.py:141-154`）本来可以并进同一个查询。项目有上千个话题时，为一个成员页读一千行。`waiting_on_you` 走 `list_inbox`（`services.py:179`，无 LIMIT）。
- **优化**：两个筛选下推到 SQL，一次查询。仓库里已有 `list_for_projects` 那种「按条件取树」的形状可参照。

```python
# app/domain/topic/repositories.py —— 新增：谁建过哪些 / 谁在忙哪些（一条查询）
    async def authored_and_touched(
        self, project_id: uuid.UUID, handle: str
    ) -> tuple[list[Topic], set[uuid.UUID]]:
        """他建的话题 + 他写过 block 的话题 id。原来是「把整棵树读回来再在
        Python 里筛」——项目话题多的时候，为一页成员读的都是别人的话题。"""
        authored = list(
            (
                await self._session.scalars(
                    select(Topic).where(
                        Topic.project_id == project_id,
                        Topic.is_private.is_(False),
                        Topic.created_by == handle,
                    )
                )
            ).all()
        )
        touched = set(
            (
                await self._session.execute(
                    select(Block.topic_id)
                    .where(Block.project_id == project_id, Block.author == handle)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        return authored, touched
```

（若要保持「`topics_active` 只含 active 且作者写过」的语义，第二个查询再加 `Topic.status == 'active'` 的 join；这里保持与原文逐字等价只是把两处筛选各自下推。）

- **契约**：响应形状不变（`topics_started` / `topics_active` / `source` / `waiting_on_you`），排序语义要照着原实现（原来是在整棵树的顺序上筛，顺序来自 `_project_topics_stmt` 的 `ORDER BY`），这一点在测试里钉住。`waiting_on_you` 建议同时给 `list_inbox` 加一个上界（比如 20），否则它是同一条「无 LIMIT」的问题。
- **测试**：`tests/integration/` 下加 `test_member_summary_matches_the_whole_tree_filters`（同一份数据，逐字段与整棵树筛出来的结果相等；含「写了别人的话题」与「自己被排除」两种边界）。

### GET /admin/spaces 与 POST /admin/spaces/{space_id}/review

- **现状**：`api/routes/admin_spaces.py:17-28` 与 `:31-44`，两个 handler 都手写 `return {"code": 200, "data": …}`。
- **问题**：**信封缺 `message`**。全仓的既定语信是 `api/response.py:6-18` 的 `ok()`（`{"code","message","data"}`），这两个端点是 `/admin/*` 里唯一两处不走 `ok()` 的——同一页的其它请求都带 `message`，客户端只要有一处按 `data["message"]` 读就会在这里炸。另外 `list_applications` 只回 `{"items": items}`（`admin_spaces.py:25-28`），没有 `total`，而它明明支持 `offset`/`limit`：翻页的人算不出还有没有下一页。`review_space` 的审计留在 Space 行上（`review_service.py:108-115` 的 `reviewed_by`/`reviewed_at`/`review_reason`），这一点是够的。
- **优化**：

```python
# app/api/routes/admin_spaces.py
from app.api.response import ok, page

@router.get("")
async def list_space_reviews(
    db: DbSession,
    handle: PlatformAdminDep,
    status: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    items, total = await SpaceReviewService(db).list_applications(
        status=status, offset=offset, limit=limit
    )
    # 走既定语信（`ok`）：手写 `{"code","data"}` 会少一个 message，
    # 同一页的其它请求都带它，客户端没有理由对这两条特殊。
    return ok({**page(items, total), "counts": await SpaceReviewService(db).counts_by_status()})

@router.post("/{space_id}/review")
async def review_space(
    space_id: int, body: ReviewSpaceRequest, db: DbSession, handle: PlatformAdminDep
) -> dict:
    item = await SpaceReviewService(db).review(
        space_id, approved=body.approved, reason=body.reason, reviewer=handle
    )
    await db.commit()
    return ok({"application": item}, "批过了" if body.approved else "驳回了")
```

```python
# app/domain/space/review_service.py —— list_applications 回 (items, total)
    async def list_applications(self, *, owner_id=None, status=None, offset=0, limit=50):
        base = [Space.deleted_at.is_(None)]
        if status:
            base.append(Space.review_status == status)
        ...
        total = int((await self.session.scalar(
            select(func.count()).select_from(Space).where(*base)
        )) or 0)
        ...
        return [...], total
```

- **契约**：`message` 的出现是**加法**（老客户端忽略它即可）；`data.items` 的形状不变，`total` 是新增键。`list_applications` 返回类型从 `list` 改成元组，调用点需要一起改（除 `/admin/spaces` 之外还有 `resubmit`/空间自己的申请页——`grep -rn list_applications` 确认后统一改）。**一句话：只增键，不改形状，可安全上线**。
- **测试**：`tests/contract/test_spaces_contract.py` 是这个域的契约测试所在地，加两条：`test_admin_space_reviews_use_the_platform_envelope`（断言 `set(body) == {"code","message","data"}` 且 `code == 200`）、`test_admin_space_reviews_report_a_total`（`limit=1` 时 `total` 大于 1）。

### GET /ai/conversations 与 GET /ai/conversations/{conversationId}

- **现状**：`api/routes/ai.py:64-80`（列表）与 `:99-109`（详情）；参数名 `pageStart`/`pageSize`（`ai.py:66-67`），返回 `page` 对象在 `domain/llm/repositories.py:106-139`；详情在 `domain/llm/chat_service.py:111-126`。
- **问题**：（a）**同一个名字两套语义**——这里的 `pageStart` 是**游标 id**（`repositories.py:122-123` `id <= page_start`），`/admin/feedback` 的 `page_start` 是 **offset**（`admin_feedback.py:121`）。而且这里是驼峰（`pageStart`/`pageSize`），本模块之外的查询参数一律下划线（`page_size`、`target_handle`、`unread_only`），`design/common/parameters.yaml` 也定的是 `page_start`/`page_size`。（b）详情把该会话**全部**消息一次返回（`chat_service.py:123-125`），没有 limit——聊得久的会话响应会一直长下去（`chat` 每轮都会往这里加两行，见第 45 条）。
- **优化**：详情收口（最小的改动），语义冲突单独记一笔待统一。

```python
# app/domain/llm/chat_service.py
    async def get_conversation(
        self, *, conversation_id: int, user_id: int,
        message_limit: int = 200, message_before: int | None = None,
    ) -> dict:
        ...
        messages = await self._message_repo.list_by_conversation(
            conversation_id, limit=message_limit, before_id=message_before
        )
        result = self._conversation_to_dict(conv)
        result["messages"] = [self._message_to_dict(m) for m in messages]
        result["hasMoreMessages"] = len(messages) == message_limit
        return result
```

- **契约**：列表**不改**（游标形状是设计里定过的）。详情多一个 `hasMoreMessages` 是加法，但**截断是行为变化**——若前端假设「拿到全部」，必须一起改；建议默认 limit 取一个明显高于现网任何会话的值（如 500），把「不截断」的现状维持住，同时给一个 `messageLimit` 参数让前端显式索取更多。命名统一（`pageStart` → `page_start`）是破坏性的，要走「两个都收」的过渡，单独提。
- **测试**：`tests/integration/test_ai_advice_conversations_belong_to_their_asker.py` 旁边加 `test_conversation_detail_is_bounded`（造 3 条消息 + `messageLimit=2`，断言只回 2 条且 `hasMoreMessages` 为真）。

### POST /ai/conversations 与 PATCH /ai/conversations/{conversationId}

- **现状**：`api/routes/ai.py:83-96`（`payload: dict = Body(...)`，取 `title`/`modelId`）与 `:126-141`（`payload: dict`，`title is None` 就 400）。
- **问题**：三个都值得改：（a）**无 schema**——`title` 可以是 dict、list、10 MB 字符串，直接进 `AIConversation.title`（`String(255)`）；（b）`modelId` 收下即丢（`ai.py:90` 读了它，`create_conversation` 的签名里没有它，`chat_service.py:84-97` 也没用）——一个说了不算的参数比没有更坏；（c）PATCH 只判「是不是 None」，空串与超长串都放过；（d）`POST /ai/conversations` 无幂等键，前端重试会多出一条空会话（Stripe 的做法是 `Idempotency-Key` 头，<https://docs.stripe.com/api/idempotent_requests>；业界通行，未逐条查证本仓是否已有对应设施——`app/domain/idempotency/` 是 agent 副作用那一条线，不覆盖浏览器请求）。
- **优化**：给这两个端点补 Pydantic 模型（仓库里到处都在用，`admin_spaces.py:12-14` 的 `ReviewSpaceRequest` 就是最小范例）。

```python
# app/api/routes/ai.py
from pydantic import BaseModel, Field

class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    model_id: str | None = Field(default=None, alias="modelId", max_length=64)

    model_config = {"populate_by_name": True}

class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)

@router.post("/conversations", summary="Create AI Conversation", status_code=201)
async def create_conversation(
    payload: ConversationCreate,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AIChatService = Depends(get_chat_service),
) -> dict:
    conv = await service.create_conversation(
        user_id=auth_user.user_id, title=payload.title, model_id=payload.model_id
    )
    return {"code": 201, "message": "Created", "data": {"conversation": conv}}
```

- **契约**：超长/错类型的输入从「写进库」变成 **422**（FastAPI 的校验错误形状），这是**行为变化**但要和全仓其它端点保持一致（其它端点都是 422）。`modelId` 要真的用起来（写进 `AIConversation.model_type` 或直接删参数）——两者都算改契约，选一个并写进文档。若要走幂等键，那是新增请求头，纯增量。
- **测试**：`tests/integration/test_ai_chat.py` 加 `test_a_non_string_title_is_rejected`（422，且库里没有新行）、`test_a_title_longer_than_the_column_is_rejected`。

### GET /admin/gateway/projects

- **现状**：`api/routes/admin_models.py:193-204` → `GatewayModelsService.projects`（`agent/gateway_models.py:371-408`）→ `_project_items`（`:394-408`）里的 `ProjectService(self._db).list_all()`。
- **问题**：`list_all()`（`domain/project/services.py:239-240` → `project/repositories.py:70`）是**全平台项目一张不落**，响应里 `projects` 就是这些项目一行一条（`gateway_models.py:377-392` 还顺手算了 `totals`）。项目上百之后，这一页每次加载都要读全部项目行、全部额度汇总（`project_credits_batch` 已经批量了，这点是对的）并序列化全部条目。网关 key 那边本来就是全量（缓存的 `_keys_raw`），这一点没法只取一页；但「全平台项目」这一半是可以收口的（按名字过滤、或按用量排序取前 N）。
- **优化**：给一个 `q`/`limit` 收口，默认行为不变。

```python
# app/api/routes/admin_models.py
@router.get("/projects")
async def list_projects(
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    return ok(await _answered(service.projects(days=days, q=q, limit=limit)))
```

- **契约**：加两个可选参数是纯增量；`limit` 默认给一个高于现网项目数、但有上界的值，因此**默认行为在现实规模下不变**。`totals` 的语义（`projects` / `with_key` / `over_budget` / `unlimited`）要跟着说明「是这一页的还是全平台的」——按现实现（`gateway_models.py:380-392`）是全平台的，加了 limit 之后要么继续全平台（需要单独 count），要么明确改成这一页的；**这一句必须写进响应或文档，否则读的人一定会误读**。
- **测试**：`tests/integration/test_admin_models.py` 加 `test_projects_can_be_filtered_and_bounded`（`limit=1` 时只回一条，`totals` 的口径与文档一致）。

### GET /ai/quota

- **现状**：`api/routes/ai.py:36-53` → `AiAdviceService.get_quota`（`domain/llm/services.py:30-34`）→ `AIUserQuotaRepository.get_quota`（`domain/llm/repositories.py:60-68`）→ `get_or_create`（`:14-41`）。
- **问题**：一个 **GET 会写库**（首次访问 `INSERT` 一行，跨天时 `UPDATE` 重置，`repositories.py:22-40`）。而 `user_ai_quota.user_id` 只有普通索引，**没有唯一约束**（`domain/llm/models.py:26`），`get_or_create` 用的是 `scalar_one_or_none()`（`:20`）：同一用户两个并发首次请求可以各插一行，此后**每一次**读这一行都会 `MultipleResultsFound` → 500（`/ai/quota` 与 `/ai/chat` 都走这里）。触发面很窄（只有首次并发），但代价是永久性的。
- **优化**：唯一约束 + 冲突时取其一行。

```python
# app/domain/llm/models.py
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True, index=True
    )
```

```python
# 迁移：先清重复（保留每用户最早那行），再加唯一索引
def upgrade() -> None:
    op.execute("""
        DELETE FROM user_ai_quota a USING user_ai_quota b
        WHERE a.user_id = b.user_id AND a.user_id IS NOT NULL AND a.id > b.id
    """)
    op.create_unique_constraint(
        "uq_user_ai_quota_user_id", "user_ai_quota", ["user_id"]
    )
```

```python
# app/domain/llm/repositories.py —— 并发首次访问不再 500
    async def get_or_create(self, user_id: int, daily_total: float) -> AIUserQuota:
        entity = await self._first_for(user_id)
        if entity is None:
            try:
                entity = AIUserQuota(...)
                self._session.add(entity)
                await self._session.flush()
                return entity
            except IntegrityError:                 # 并发下另一个人刚插进去
                await self._session.rollback()
                entity = await self._first_for(user_id)
        ...
```

- **契约**：响应形状不变（`quota.remaining`/`daily`/`used`/`resetTime`）。**要一条数据迁移**（清重复行 + 唯一约束），这是本次分析里唯一动存量数据的建议，需要人拍板；若不想动存量，至少把读取从 `scalar_one_or_none()` 换成「取最早一行」以免 500。
- **测试**：`tests/integration/test_ai_chat.py` 加 `test_quota_reads_a_row_when_duplicates_exist`（手工插两行同 `user_id`，断言 200 且不抛 `MultipleResultsFound`）。

### GET /admin/memory/migration/projects/{project_id}/dry-run

- **现状**：`api/routes/admin_memory.py:85-95`，服务在 `migration_service.py:231-300`；每个批次有 `timeout=settings.memory_migration_timeout_s`（`:318-324`），但批次数 = `len(entries)/memory_migration_chunk`，**总时长无上限**。
- **问题**：（a）一个要几分钟的模型循环跑在 HTTP 请求里，期间占着 `get_db` 给的那条连接（`release_read_session`，`core/db.py:182-191` 就是为「等远程 I/O 前先还连接」写的，这里没用上）；（b）**无重复提交保护**：dry-run 会在 `memory_migration_plans` 里 `INSERT` 一行（`:248-298`），连点两次就是两份报告、两次模型账单，而这份工作本身是可重入的（第二次的 `gather` 会把已搬过的 `source_ids` 排除，但已批准的 draft 仍在）。
- **优化**：先加最省的那一道——同一项目已有未处理的 draft 时直接复用/拒绝，并在循环前释放读连接。

```python
# app/domain/memory/migration_service.py
    async def dry_run(self, project_id: uuid.UUID, *, by: str) -> MemoryMigrationPlan:
        existing = await self._session.scalars(
            select(MemoryMigrationPlan).where(
                MemoryMigrationPlan.project_id == project_id,
                MemoryMigrationPlan.status == MemoryMigrationStatus.draft.value,
            ).order_by(MemoryMigrationPlan.created_at.desc()).limit(1)
        )
        draft = existing.first()
        if draft is not None:
            # 同一项目上已经有一份等着复核的草稿：再问一次模型是**另付一次钱**，
            # 而且两份草稿的 `source_ids` 会互相把对方判成「已搬过」。要重来就先
            # 让人把这一份作废（状态推到 failed），这一步是显式的。
            raise ConflictError(
                f"这个项目上已经有一份等复核的报告（计划 {draft.id}）。"
                "复核它，或先把它作废，再重新出报告。"
            )
        gathered = await self.gather(project_id)
        ...
        # 等模型这几分钟里先把读事务的连还回池子（`core/db.release_read_session`
        # 的用途就是这个；此时还没有任何待写的行）。
        await release_read_session(self._session)
        raw = await self._ask(gathered)
```

- **契约**：409 是**新状态码**（原来重复调用会 200 并返回一份新报告）。这是有意的行为收紧，要跟前端说一句（前端应把 409 显示成「已有一份在等复核」）。若不想加状态码，退一步只在日志里警告不够——建议还是 409，因为「付了两次钱」是真金白银。同步阻塞本身若真要解决，正确形状是 **202 Accepted + 一个进度资源**（GitHub 对长任务是这么做的，例：`POST /repos/{o}/{r}/dispatches` 回 204/202），那是接口形状的变化，需要单独排期。
- **测试**：`tests/integration/` 加 `test_a_second_dry_run_is_refused_while_a_draft_awaits_review`（沿用 `tests/unit/test_migration_timeouts.py` 的桩法把模型换成假客户端，断言第二次 409 且没有第二次模型调用）。

### GET /admin/stats/platform 的 `_health_snapshot`

- **现状**：`domain/platform_stats/services.py:263-286`，其中第 274 行的 docstring 写着「**不 import 路由模块**」，而紧接着就是 `from app.api.routes import health as health_routes`（`:274`）。
- **问题**：注释与代码相反——领域层 import 了路由模块，而这个 docstring 正是为了解释**为什么不该**这么做（「import 它会把整条路由装配拖进领域层」）。实际后果是每次 `/admin/stats/platform` 都会经 `health_routes.detailed_health_check()` 建一个 Redis 连接（见第 63 条）。这是本次看到的最干净的一处「注释说的和代码做的是两件事」。
- **优化**：把「怎么测健康」判据挪到一个中立模块（`app/core/health_checks.py`），`api/routes/health.py` 与 `platform_stats` 都从那里取，注释与代码就一致了；顺手把 Redis 客户端做成单例（第 63 条的补丁）。这条与第 63 条是同一个改动的两半。
- **契约**：`/admin/stats/platform` 的 `health` 块形状不变（`overall` + `checks{name:{status,detail}}`）。
- **测试**：`tests/integration/test_admin_stats.py::test_platform_health_block_matches_detailed_health`（两处同源，逐字段相等）。

### GET /projects/{project_id}/contributions

- **现状**：`api/routes/dashboard.py:161-172` → `DashboardService.contributions`（`domain/dashboard/services.py:477-500`）。
- **问题**：`select(Block.author_type, Block.author, func.count()).where(Block.project_id == …).group_by(...)` 是**该项目有史以来全部 block** 的聚合，没有时间窗（`services.py:482-488`），而同一张表上别的读法都已经按窗口收口（`/admin/stats/*` 一律必填窗口）。`by_author` 的键数 = 历史上在这个项目写过东西的作者数，无界。
- **优化**：给一个可选的窗口（默认全量以保持现状），把「人/AI 比例」这类图的成本按需收口：

```python
    async def contributions(self, project_id: uuid.UUID, *, since: datetime | None = None) -> dict:
        stmt = (
            select(Block.author_type, Block.author, func.count())
            .where(Block.project_id == project_id)
            .group_by(Block.author_type, Block.author)
        )
        if since is not None:
            # 给「最近一段时间」这类读法用；默认仍是全量（现状）。
            stmt = stmt.where(Block.created_at >= since)
```

- **契约**：默认行为不变（纯增量参数）。无迁移。索引上 `blocks(project_id)` 单列能领路，但聚合本身要读命中的全部行——所以这是「按需收口」而非「现在坏了」。**若项目会被长久使用，这一页会随历史线性变慢**，值得在页面上给一个时间范围控件。
- **测试**：`tests/integration/` 加 `test_contributions_can_be_windowed`（窗口外写一条 block，带 `since` 时不计入；不带时计入）。

---

## 三、模块级建议

### 1. 分页协议统一（GitHub 用 Link 头 + 游标 / 本仓 `design/` 用 `page_start`+`has_more`）

- **适用**：`/admin/feedback`、`/admin/spaces`、`/admin/gateway/audit`、`/admin/gateway/projects`、`/admin/subscriptions`、`/admin/memory/migration/…/plans`、`/projects/{id}/alerts`、`/projects/{id}/inbox`。
- **做法**：本仓已经有自己的答案——`design/common/parameters.yaml` 的 `PageStart`（游标 id）+ `design/common/responses.yaml` 的 `Page`（`has_more`/`next_start`），`/ai/conversations` 是唯一严格按它实现的端点。建议：（a）所有集合端点至少回一个「还有没有下一页」（`api/response.py` 的 `page()` 加可选 `has_more`，见第 5 条的具体补丁）；（b）`total` 一律是**真总数**，不是这一页的条数（`alerts.py:114-117` 现在写的是 `page(items, len(items))`）；（c）**同名参数必须同义**：`page_start` 在 `/admin/feedback` 是 offset、在 `/ai/conversations` 是游标 id，这个必须收敛，改的那一侧走「新参数名 + 旧名保留」。
- **为什么**：GitHub 的分页就是这么定的（Link 头 + `per_page`，<https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api>）；偏移分页在「一边翻页一边有新数据写入」的队列里会漏行/重行，而分诊队列正是这种。

### 2. `/admin/*` 的响应一律走 `api/response.ok()`

- **适用**：`/admin/spaces` 两条（`admin_spaces.py:28/44`）。
- **做法**：改成 `ok(...)`，`message` 写一句人话（`"批过了"`）。`ok()` 已经支持 `warnings`，需要时别自己拼信封。
- **为什么**：全仓一致的信封是 `{"code","message","data"}`（`api/response.py:1-18`），手写字典是唯一会让客户端按 `data["message"]` 读时炸的地方。GitHub 的错误体是 `message`/`documentation_url`（<https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api>），同理：形状不一致的代价由客户端承担。

### 3. 管理端的写动作要有统一的审计出口

- **适用**：`PATCH /admin/feedback/{id}`（优先级/指派人）、`POST /admin/memory/migration/plans/{id}/apply`（落笔）。
- **做法**：仓库里已经有一个成熟的形状——`gateway_admin_audit`（`domain/agent/models.py:120-156`）：actor、action、target、result、detail、before/after，**成功失败都落**，且 `_record` 独立 commit（`gateway_models.py:712-738` 的注释解释了为什么不能跟着请求回滚）。两条建议都按它做：反馈那两条补时间线（同一事务，改状态用的就是这条路），记忆落笔补 `applied_by` 列。若以后再有新的管理写动作，直接复用 `GatewayAdminAudit` 那一套（`gateway_models._record` 可以抽成域外的公共函数），而不是每处各想一种记录法。
- **为什么**：「谁在什么时候对哪个对象做了什么」是这个后台存在的理由之一（`domain/agent/models.py:121-129` 自己写的）。**现状是三条路各留各的**：网关那批最完整，反馈留时间线，迁移只留 created/approved，反馈的优先级/指派与迁移的落笔两处没有。

### 4. `count(*)` 的次数要当成设计约束，不是实现细节

- **适用**：`feedback/repositories.py:1628-1663`（`admin_counts` 12 条）、`platform_stats/pipeline.py:85-112/188-203`（`backlog`/`needs_you` 各 4–5 条）、`platform_stats/repositories.py:28-50`（4 张表 4 条）、`platform_stats/integrations.py:60-75`（OAuth 4 条）。
- **做法**：同表同窗口的多个计数合并成**一条** `select(func.count(case((predicate, 1))))`（本仓 `usage/repositories.py:16-31` 的 `unpriced_tokens()` 就是现成的写法，注释里那段「一个定义三个读者」正是同一个理由）；不同表之间不必合。至少：`admin_board_counts` 的 12 条可以按表折成 1–2 条（`columns`/`status`/`total` 的谓词都在 `Feedback` 上）。
- **为什么**：`MachineInventoryRepository`（`:28-50`）与 `admin_counts`（`:1628-1663`）都在注释里写了「这个规模上值得分开」——那个判断对小表是对的，对 `feedback` 这张随产品使用的表则不然；把判据写在一处（「这张表是台账还是流水」），下次加一类计数时就不必再想一遍。**注意**：这只是往返次数与可读性的取舍，不是正确性问题。

### 5. 「规模天然有界」的表要写明界在哪里

- **适用**：`/admin/subscriptions`（`list_all`，`subscription/repositories.py:68-70`）、`/admin/gateway/projects`（`ProjectService.list_all`）、`/admin/gateway/audit`（只增）、`/admin/memory/migration/…/plans`（每次 dry-run 一行）。
- **做法**：无分页的端点必须在 docstring 里写清上界来自什么（例如「一座一订阅」），并在**界面**上留可见的计数；一旦上界不再成立就补分页。`/admin/subscriptions` 的 docstring 现在没写这一句。
- **为什么**：业界通行（GitHub 的所有集合端点一律分页，未逐条查证具体规模阈值），而本仓已经有「参数上界写死在签名上」的纪律（`admin_stats.py:16-20` 那段注释）——同一套纪律应该覆盖「无分页」这一种。

---

### 附：这次没查到问题的几处（避免下次重复看）

- `FeedbackService.detail_of`（`services.py:572-632`）：看了一遍，注释/笔记/头像/时间线**全是批量**，`page_comments` 自带游标，没有 N+1。
- alerts 的三个单条动作（`read`/`feedback`/`resolve`）：`get_or_404` 重复调用命中的是 `session.get` 的 identity map（`notification/repositories.py:233-234`，且 `async_session_factory` 是 `expire_on_commit=False`，`core/db.py:74`），**不发第二条 SQL**；`resolve` 用 `resolved_at` 做幂等。
- `resource_usage(created_at)`、`feedback_timeline(at)`、`gateway_admin_audit(created_at DESC)` 三条索引都在（`usage/models.py:47`、`agent/models.py:141-143`、迁移 `a9c4e7f12b60`），看板的时间范围读有索引可走。**唯一缺的是 `blocks` 上按时间的范围扫**（第 11 条）。
- `GET /users/{handle}/topics`、`DELETE /users/me/understanding/{id}`、`GET /projects/{id}/credits`、`GET /projects/{id}/usage`：上界/窗口/授权都齐。
