# 平台 API 逐接口优化调研 — 组 `tasks`

- 负责文件：`backend/app/api/routes/tasks.py`（3906 行，37 个接口）、`routes/milestones.py`（152 行，5 个）、`routes/routines.py`（301 行，11 个）、`routes/git_http.py`（182 行，5 个），共 59 个接口
- 仓库（只读）：`$WT`；行号均指上述四个文件，除注明外
- 行为约定：`ApproveType` 用 SMALLINT（`APPROVED=0` / `DISAPPROVED=1` / `NONE=2`）；响应信封固定为 `{"code","message","data"}`（`api/response.py:6`），列表另有 `page(items, total)`（`api/response.py:21`）
- 判断依据：优先 GitHub REST 惯例（URL 见第三节），其次 Stripe / RFC 9110；查不到的按业界通行做法并在文中注明
- **清单核对**：`tasks.list.md` 里 59 行的 method / path / 行号与源码装饰器逐条比对过，**没有不一致**。四个 router 的前缀分别是 `/tasks`（`tasks.py:79`）、`/projects`（`git_http.py:16`）、`""`（`milestones.py:26`、`routines.py:26`），代码层没有任何全局 `/api` 前缀（`/api` 由 nginx 按 `docs/api-conventions.md` 的「一个 /api」规则加），所以清单里的裸路径就是后端路径，无需差异说明。

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | PUT | `/projects/{project_id}/git/tasks/{task_id}/snapshots/{snapshot_sha}` | 可优化(低) | 每个请求块一次 `asyncio.to_thread`；整包先落盘再校验摘要，坏包要重传一遍 |
| 2 | GET | `/projects/{project_id}/git/tasks/{task_id}/snapshots/latest` | 暂无 | 单查询 + `limit(1)`；没有备份时抛 `NotFoundError` 而不是回空 |
| 3 | GET | `/projects/{project_id}/git/tasks/{task_id}/snapshots/{snapshot_id}` | 可优化 | 整份 bundle 读成 `bytes` 再交给 `Response`，最长 512 MiB 全驻内存 |
| 4 | POST | `/projects/{project_id}/git/tasks/{task_id}` | 可优化 | 把 GET handler 当函数调，token 与 task 都又验/又取了一遍；提交在响应前（正确） |
| 5 | GET | `/projects/{project_id}/git/tasks/{task_id}` | 可优化 | GET 里向外发 1–2 次远程请求（30s 超时）且 `binding` 查两遍；语义上不是安全方法 |
| 6 | POST | `/projects/{project_id}/milestones` | 可优化 | 人这条路没有显式 `commit`（见 M2）；无可选幂等键，重发多钉一个里程碑 |
| 7 | GET | `/projects/{project_id}/milestones` | 可优化 | 读接口里跑一条 UPDATE（`mark_overdue`）+ 另一条 COUNT；没有任何 limit |
| 8 | GET | `/projects/{project_id}/calendar` | 可优化 | 同上；`total` 返回值就是本页条数，语义与 `/milestones` 不同 |
| 9 | PUT | `/milestones/{milestone_id}` | 可优化(低) | 无显式提交（见 M2） |
| 10 | DELETE | `/milestones/{milestone_id}` | 可优化(低) | 回 200 + 信封，而 `/tasks` 的同名动作回 204（见 M4） |
| 11 | GET | `/projects/{project_id}/routines` | 可优化(低) | 无 limit；`total` 是本页条数 |
| 12 | POST | `/topics/{topic_id}/routines` | 可优化 | 无幂等键，重发建两条 routine；`TopicService` 同一请求里造了两次 |
| 13 | GET | `/routines/{routine_id}` | 暂无 | 2 条定长查询，`get` 未命中会抛，作用域由 routine 自己带 |
| 14 | PATCH | `/routines/{routine_id}` | 暂无 | `exclude_unset` 传字段，显式 commit |
| 15 | POST | `/routines/{routine_id}/confirm` | 暂无 | 要求是人（`_person`），显式 commit |
| 16 | POST | `/routines/{routine_id}/pause` | 暂无 | 不要求是人是有意的：停的是活，不是开活 —— 与 resume 的不对称正确 |
| 17 | POST | `/routines/{routine_id}/resume` | 暂无 | 要求是人 + 显式 commit |
| 18 | POST | `/routines/{routine_id}/run-now` | 可优化 | 在请求里跑平台级 `dispatch_pending(limit=100)`，响应要等别人的投递发完 |
| 19 | DELETE | `/routines/{routine_id}` | 暂无 | 要求是人 + 显式 commit |
| 20 | GET | `/routines/{routine_id}/runs` | 可优化(低) | `runs()` 内部有 50 条上限，但 `total` 恒等于本页条数，客户端看不出还有 |
| 21 | POST | `/routine-runs/{run_id}/report` | 暂无 | 显式 commit；只有执行的那个队友能报（有测试） |
| 22 | POST | `/tasks` | 可优化 | 无幂等键，超时重发建两道题；其余（显式提交、发题门）是对的 |
| 23 | GET | `/tasks/{taskId}/attachments` | 暂无 | 三道闸齐全；清单与「能不能下载」一起算，无 N+1 |
| 24 | POST | `/tasks/{taskId}/attachments` | 可优化(低) | 无显式提交（见 M2）；大小限制只在上游 nginx |
| 25 | GET | `/tasks/{taskId}/attachments/{attachmentId}/download` | 可优化(低) | 整个文件读进内存；`download_count` 的自增靠 teardown 提交 |
| 26 | DELETE | `/tasks/{taskId}/attachments/{attachmentId}` | 可优化(低) | 无显式提交（见 M2） |
| 27 | POST | `/tasks/publish/from-pdf/preview` | 可优化(低) | 在请求里同步跑模型；无幂等键，重试再花一次 token |
| 28 | POST | `/tasks/publish/from-pdf/confirm` | 可优化(低) | 每道草稿都重查同一个 space / 分类 / `may_publish_in_space` |
| 29 | POST | `/tasks/{taskId}/participants` | 可优化 | 「先查后插」无唯一约束兜底（见 M3）；无显式提交 |
| 30 | POST | `/tasks/{taskId}/participations/user` | 可优化 | 同上 |
| 31 | POST | `/tasks/{taskId}/participations/team` | 可优化 | 同上 |
| 32 | PATCH | `/tasks/{taskId}/participants/{participantId}` | 可优化(低) | 无显式提交（见 M2） |
| 33 | GET | `/tasks/{taskId}` | 可优化 | 同一个请求里三组查询各查两遍；`_enrich_task_models` 顺带拉整个板的报名表 |
| 34 | PATCH | `/tasks/{taskId}` | 可优化(低) | 单函数约 240 行、字段逐个 if；topics 覆盖写不做任何校验（其余正确） |
| 35 | GET | `/tasks` | 可优化 | 全板报名表全量载入 + `accessDomainGroupIds` N+1 + `distinctParticipants` 再跑同一条查询 |
| 36 | DELETE | `/tasks/{taskId}` | 可优化(低) | Python 循环逐行软删成员；无显式提交（见 M2） |
| 37 | DELETE | `/tasks/{taskId}/participants/{participantId}` | 可优化(低) | 无显式提交（见 M2） |
| 38 | DELETE | `/tasks/{taskId}/participants` | 可优化(低) | 同上 |
| 39 | PATCH | `/tasks/{taskId}/participants` | 可优化 | 返回全部参与者、每人只有 `{id}`，与 GET 名单的形状不一致；无分页、无提交 |
| 40 | POST | `/tasks/{taskId}/resubmit` | 可优化(低) | 无显式提交：客户被告知「已重提」后立刻读，可能还是旧状态（见 M2） |
| 41 | GET | `/tasks/{taskId}/participants` | 可优化(低) | 无分页、整份报名表返回（含 email/phone） |
| 42 | GET | `/tasks/{taskId}/participants/{participantId}` | 暂无 | 判据与列表版一字不差，403/404 口径在 docstring 里写明 |
| 43 | GET | `/tasks/{taskId}/teams` | 可优化(低) | `filter=eligible` 与 `all` 行为相同 —— 参数是个空承诺（源码自认） |
| 44 | GET | `/tasks/{taskId}/participants/{participantId}/submissions` | 可优化 | 每份提交 1–2 条查询（N+1）；membership 用全量名单挑一个 |
| 45 | POST | `/tasks/{taskId}/participants/{participantId}/submissions` | 可优化 | 版本号「先查最新再 +1」无唯一约束兜底（见 M3） |
| 46 | PATCH | `/tasks/{taskId}/participants/{participantId}/submissions/{version}` | 暂无 | 校验链齐全，`version` 交给 service 判在不在 |
| 47 | POST | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | 可优化 | 「先查有没有评审再插」+ 无唯一约束（见 M3）；无显式提交 |
| 48 | GET | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | 暂无 | 鉴权照抄提交列表版（出题人/管理员/本人/小队成员） |
| 49 | PATCH | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | 可优化(低) | 无显式提交（见 M2） |
| 50 | PUT | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | 可优化(低) | 名为 "Full Replace"，实际调的是 `patch_review`，缺字段不覆盖 —— 与 PATCH 无差别 |
| 51 | DELETE | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | 可优化(低) | 同一请求里 `get_review_dto` 查两遍；回 200 且带删除后的空 review |
| 52 | POST | `/tasks/{taskId}/ai-advice` | 可优化 | 在请求里同步跑一次完整 LLM 生成；无幂等键 |
| 53 | GET | `/tasks/{taskId}/ai-advice` | 可优化(低) | `list_by_task` 无 limit，每条含 `rawResponse` 之外的 4 段 JSON |
| 54 | GET | `/tasks/{taskId}/ai-advice/status` | 暂无 | 一次查询，只回状态 |
| 55 | GET | `/tasks/{taskId}/ai-advice/conversations/grouped` | 可优化(低) | 无分页：对话攒起来就是一整份返回 |
| 56 | GET | `/tasks/{taskId}/ai-advice/conversations/{conversationId}` | 可优化 | 就是这个先注册的路由吃掉了下面的 `/stream`（见 #59）；messages 无 limit |
| 57 | POST | `/tasks/{taskId}/ai-advice/conversations` | 可优化 | 在请求里同步等一次 LLM completion（非流式）；无幂等键 |
| 58 | DELETE | `/tasks/{taskId}/ai-advice/conversations/{conversationId}` | 可优化(低) | 无显式提交（见 M2） |
| 59 | GET | `/tasks/{taskId}/ai-advice/conversations/stream` | 可优化 | **当前永不可达**（被 #56 遮蔽）；且前端按 `[PARTIAL]/[RESPONSE]/[DONE]` 解析，与本端发的 JSON 帧不是一套 |

统计：可优化 46（其中 低 21），暂无 13，待确认 0。

---

## 二、详细分析（按收益从高到低）

### GET /tasks/{taskId}/ai-advice/conversations/stream —— 整个功能当前不可达

- **现状**：`tasks.py:3791` 注册。GET SSE，query 参数 `question` 必填，`StreamingResponse(event_generator(), media_type="text/event-stream")`，把模型输出包成 `data: {"type":"content"|"done"|"error",...}` 逐块推出（`tasks.py:3837-3906`）。
- **问题**：
  - **路由被遮蔽，永远走不到这里**。同文件 `tasks.py:3666` 先注册了 `GET /{taskId}/ai-advice/conversations/{conversationId}`。FastAPI/Starlette 按注册顺序**先注册先匹配**（FastAPI 自己的规矩：静态段要排在 `{param}` 之前，官方文档 "Path parameters" 一节即以 `/users/me` 必须在 `/users/{user_id}` 之前为例）。于是 `/tasks/1/ai-advice/conversations/stream?question=x` 会被上面那条吃掉，`conversationId="stream"` 走到 `service.get_conversation` → `ValueError` → `NotFoundError`（`tasks.py:3686-3691`），**404**。SSE 一个字节也不会推出来。
  - 现有的契约守卫抓不住这一类：`tests/contract/test_api_addressing_contract.py:247` 与 `:268` 都先把路径里的 `{...}` 归一成 `{}` 再比，所以 `…/conversations/{}` 与 `…/conversations/stream` 在它们眼里毫不相干；`api_catalog.json` 又是从旧 Kotlin 的 `NT-API.yml` 生成的，里面没有 `stream` 这条。
  - 即便打通路由，**两端协议也不是一套**：前端 `frontend/src/network/api/tasks/index.ts:458` 用 `new EventSource(...)` 连 `.../conversations/stream?${params}`，按 `[DONE]`（:474）、`[REASONING_PARTIAL]`（:483）、`[PARTIAL]`（:510）、`[RESPONSE]`（:515）这些**前缀文本**解析；本端发的是 JSON 帧（`{"type":"content","data":...}`）。认不出来的分支落到「收到未知格式消息」。
  - 另外 `except Exception as exc` 兜底会把内部错误原文（含连接串、堆栈相关字符串）直接写进数据帧（`tasks.py:3892-3896`），错误文本不该原样出网。
- **优化**：把 `/stream` 这条整体挪到 `{conversationId}` 之前（改一次注册顺序，不动实现），并给契约测试补一条「字面量路径不许被更早的 `{param}` 吃掉」的守卫。

  ```python
  # tasks.py —— 把 3791 行那段定义整体上移，插在 3666 行 get_ai_advice_conversation 之前。
  # Starlette 先注册先匹配：只要 {conversationId} 在前面，这条 URL 就会被当成
  # conversationId="stream" 交给上面那条路由，404，永远读不到这里。
  @router.get(
      "/{taskId}/ai-advice/conversations/stream",
      summary="Stream AI Advice Conversation (SSE)",
  )
  async def stream_ai_advice_conversation(...):
      ...
  ```

  ```python
  # backend/tests/contract/test_api_addressing_contract.py（文件末尾新增；沿用文件里
  # _module_routers() 的用法）
  def _literal_routes_eaten_by_a_parameter() -> dict[tuple[str, str], str]:
      """字面量路径被更早注册的 {param} 路由吃掉的那些。

      上面两条守卫先把 {x} 归一成 {} 再比，所以字面量与参数在它们眼里永不冲突 ——
      这正是 .../conversations/stream 能静静地死 .../conversations/{id} 后面的原因。
      这里反过来：把更早的那条里的参数放大成「匹配任意一段」，逐条试。
      """
      eaten: dict[tuple[str, str], str] = {}
      for module, routes in _module_routers().items():
          seen: list[tuple[str, str]] = []  # (pattern, method)
          for path, methods in routes:
              for method in methods:
                  for pattern, _ in seen:
                      if pattern == path:
                          continue
                      regex = re.compile(
                          "^" + re.sub(r"\{[^}]+\}", "[^/]+", pattern) + "$"
                      )
                      if regex.match(path):
                          eaten[(path, method)] = pattern
                          break
                  seen.append((path, method))
      return eaten


  def test_a_literal_path_is_not_eaten_by_an_earlier_parameter_route() -> None:
      """先注册的 {param} 会吃掉后面同 URL 的字面量路由（first-registered-wins）。"""
      eaten = _literal_routes_eaten_by_a_parameter()
      assert not eaten, (
          "these routes sit behind a {param} route that matches the same URL "
          f"first, so a caller can never reach them: {dict(sorted(eaten.items()))}"
      )
  ```
- **契约**：不动请求/响应契约（这条现在压根到不了）；改的是注册顺序。SSE 帧格式要不要跟前端对齐是**另一个**决定（见第三节 M6），两条要一起做才算修好这个功能。风险：把 `/stream` 提前后，`{conversationId}` 拿不到 `stream` 这个取值 —— 那本来也不是一条合法对话 id（`secrets.token_hex(12)`，24 位十六进制）。
- **测试**：`backend/tests/contract/test_api_addressing_contract.py::test_a_literal_path_is_not_eaten_by_an_earlier_parameter_route`（新守卫，先红后绿）；`backend/tests/integration/test_ai_advice_stream_is_reachable.py::test_stream_route_answers_sse_instead_of_404`（对 `/tasks/{id}/ai-advice/conversations/stream?question=…` 断言 `content-type` 是 `text/event-stream` 且首帧是数据行，而不是 404 信封）。现有 `backend/tests/unit/test_task_ai_advice_routes.py` 只覆盖了 `POST .../conversations` 两条（`:89`、`:135`），没有 stream。

### GET /tasks/{taskId}

- **现状**：`tasks.py:2078`。取 task → `_ensure_task_readable` 三道闸 → 组装 participation（两种身份）→ `_task_to_api_model` → `_enrich_task_models(db, [task_dict], space_id=task.space_id)`（单元素列表）。
- **问题**（行号都可复核，全是同请求内的重复往返）：
  - `tasks.py:2119-2122` 与 `tasks.py:2182-2185`：同一个 `(task_id, auth_user.user_id)` 的 `get_user_membership` 查了两遍。
  - `tasks.py:2139-2142` 与 `tasks.py:2186-2189`：同一个 `list_team_memberships_for_user` 查了两遍。
  - `tasks.py:2143-2145` 与 `tasks.py:2200-2204`：同一批 `member_id` 的 `TeamRepository.get_by_ids` 查了两遍。
  - `tasks.py:2264` 只为**一道题**进 `_enrich_task_models`，而它内部 `tasks.py:551` 调的是 `list_memberships_for_space(space_id)` —— `domain/task/repositories.py:561-574` 的 SQL 是「这个板下所有未删任务的所有未删报名行」，只为算一道题的 `participants.total`。板子大起来，读一道题要拉整板报名表。
- **优化**：三个「查两遍」提到最前面查一遍、两处共用；报名表按题取。

  ```python
  # tasks.py:2105 之前，一次性算好；下面 participation 分支与 2179 的分支都从这里取。
  user_membership = None
  team_memberships: list[TaskMembership] = []
  teams_map: dict[int, Team] = {}
  if auth_user.user_id > 0:
      user_membership = await membership_service.get_user_membership(
          task_id=task_id, user_id=auth_user.user_id
      )
      team_memberships = list(
          await membership_service.list_team_memberships_for_user(
              task_id=task_id, user_id=auth_user.user_id
          )
      )
      team_ids = [m.member_id for m in team_memberships]
      teams_map = (
          await TeamRepository(session=db).get_by_ids(team_ids) if team_ids else {}
      )
  ```

  ```python
  # domain/task/repositories.py（TaskMembershipRepository 内，紧挨 list_memberships_for_space）
  async def list_memberships_for_tasks(
      self, *, space_id: int, task_ids: Sequence[int]
  ) -> Sequence[TaskMembership]:
      """**这几道题**的报名行 —— 一页题只要这些，不是整个板的。

      ``list_memberships_for_space`` 回答的是「这个板里谁报过名」；列表与详情
      只渲染手上这几道题，把 id 传进来让索引去干活。
      """
      if not task_ids:
          return []
      stmt: Select[tuple[TaskMembership]] = (
          select(TaskMembership)
          .join(Task, Task.id == TaskMembership.task_id)
          .where(
              Task.space_id == space_id,
              Task.deleted_at.is_(None),
              TaskMembership.deleted_at.is_(None),
              TaskMembership.task_id.in_(list(task_ids)),
          )
      )
      return list((await self._session.execute(stmt)).scalars().all())
  ```

  ```python
  # tasks.py:551（_enrich_task_models 内）
  memberships = await membership_repo.list_memberships_for_tasks(
      space_id=space_id, task_ids=task_ids
  )
  ```
- **契约**：不动。`participants.total` 的语义与今天完全一致（同一批题的行数）。风险：`TaskMembership` 上没有 `task_id` 索引（见 M3），`in_` 会全表扫 `task_membership`；这条改动应与 M3 的索引一起上。
- **测试**：`backend/tests/integration/test_task_detail_queries_once.py::test_get_task_does_not_load_the_whole_boards_memberships`（造两块板、板上各 20 道题 + 报名，用 `sqlalchemy` 事件计数 `task_membership` 上的 SELECT 行数，断言读一道题加载的报名行 ≤ 这道题的报名数）。

### GET /tasks

- **现状**：`tasks.py:2529`。校验排序/状态 → 权限（`get_relation`）→ `enumerate_tasks` + `count_tasks` → `_enrich_task_models` → 可选的 schema/topics/用户态 → `_count_distinct_participants`。
- **问题**：
  - `tasks.py:2593` 与 `tasks.py:2597-2598`：`approved=NONE` 时同一个 `(space, user)` 的 `get_relation` 查两遍（第二条还要再 new 一个 repository）。
  - `tasks.py:551`：又一处全板报名表全量载入（同上）。
  - `tasks.py:663-672`：`accessDomainGroupIds` 是**每道开了访问控制的题两条查询**（本题 domains + 这些 domains 反查 groups）。一页 20 道全开着 = 41 条。
  - `tasks.py:2707`：`queryDistinctParticipants` 时 `_count_distinct_participants` 又调一次 `list_memberships_for_space`（`tasks.py:509`）—— 函数注释自己承认「与 `_enrich_task_models` 里那次是同一条查询，不该让每个调 `/tasks` 的页面都付两遍」，但代价现在是**付两遍**，只是默认不问。
- **优化**：把 membership 与 access-domain 都改成整页取一次。

  ```python
  # tasks.py:2590-2598 —— 只查一次，两处共用。
  admin_repo = SpaceAdminRelationRepository(session=db)
  is_space_admin = (
      await admin_repo.get_relation(space, auth_user.user_id) is not None
  )
  if approved_value == 2 and not is_space_admin:  # NONE = 未审批
      raise ForbiddenError("Only space admins can view unapproved tasks")
  ```

  ```python
  # tasks.py:660-672 —— 从「每题两条」改成「整页三条」。
  # 顶部 import 需补：from app.domain.task.models import TaskAccessDomain
  # （现在只 import 了 TaskAccessDomainRepository，见 tasks.py:40-59）
  access_domain_repo = TaskAccessDomainRepository(session=db)
  domain_group_domain_repo = SpaceDomainGroupDomainRepository(session=db)
  controlled_ids = [
      m["id"] for m in task_models if m.get("accessControlEnabled")
  ]
  by_task: dict[int, set[str]] = {}
  if controlled_ids:
      rows = await db.execute(
          select(TaskAccessDomain.task_id, TaskAccessDomain.domain).where(
              TaskAccessDomain.task_id.in_(controlled_ids),
              TaskAccessDomain.deleted_at.is_(None),
          )
      )
      for task_id, domain in rows.all():
          by_task.setdefault(int(task_id), set()).add(domain)

  group_domains: dict[int, set[str]] = {}
  union = set().union(*by_task.values()) if by_task else set()
  if union:
      candidate_groups = await domain_group_domain_repo.list_group_ids_by_domains(
          space_id=space_id, domains=sorted(union)
      )
      for group_id, domains in (
          await domain_group_domain_repo.list_domains_for_groups(candidate_groups)
      ).items():
          group_domains.setdefault(int(group_id), set()).update(domains)

  for task_model in task_models:
      task_model.setdefault("accessDomainGroupIds", [])
      domains = by_task.get(task_model["id"])
      if not domains:
          continue
      # 与逐题版同一句话：本题的 domains 落在哪些组里。
      task_model["accessDomainGroupIds"] = sorted(
          group_id for group_id, gd in group_domains.items() if gd & domains
      )
  ```

  ```python
  # tasks.py:2704-2709 —— 这一页已经不再持有全板报名表，去重算法不变，只换数据来源。
  if queryDistinctParticipants:
      data["distinctParticipants"] = await _count_distinct_participants(
          db,
          space_id=space,
          task_ids=[item["id"] for item in items],
          # 新签名（tasks.py:487）：改成按这几道题取，而不是整个板
      )
  ```
- **契约**：不改响应形状（`accessDomainGroupIds` 的取值集合与逐题版逐条等价：候选组由本题 domains 的并集反查得到，再按每个组自己的 domains 与本题 domains 求交）。风险：`db.execute(select(...))` 这条新增语句要进 `tasks.py` 的 import（`select` 已在 `:10`）。若 `accessControlEnabled` 的题多、且每组 domains 多，第三条查询会比逐题版回报更大，所以它必须保持「整页一次」而不是「每题一次」。
- **测试**：`backend/tests/integration/test_task_list_queries_once.py::test_task_list_hits_a_constant_number_of_queries_per_page`（同一页 5 道题与 20 道题的 SQL 语句数相同）；`::test_distinct_participants_reuses_the_membership_load`（用一个 `before_cursor_execute` 钩子统计 `task_membership` 上的 SELECT 次数 == 2）。

### GET /tasks/{taskId}/participants/{participantId}/submissions

- **现状**：`tasks.py:3122` → `TaskSubmissionService.list_submissions`（`domain/task/services.py:862-923`）。
- **问题**：
  - `services.py:907-911`：**每份提交**一条 `list_by_submission_id`（拿 entries），一页 100 份 = 100 条。
  - `services.py:912-914`：`query_review` 时**每份提交**再一条 `get_by_submission_id`，最高再 +100 条。
  - `services.py:893-900`：为了把 `membership_id` 映射成会员行，先把**整道题的报名表**拉回来再筛（`list_memberships_for_task(approved=None)`）—— 一页提交通常只涉及 1 个 participant。
- **优化**：entries 与 review 各改成按 submission_id 集合批量取；membership 直接按 id 取。

  ```python
  # domain/task/services.py:892-921 —— 三条 N+1/全量，改成三条批量。
  submission_ids = [s.id for s in submissions]
  entries_by_submission: dict[int, list[TaskSubmissionEntry]] = defaultdict(list)
  if submission_ids:
      for entry in await self._entry_repo.list_by_submission_ids(submission_ids):
          entries_by_submission[entry.submission_id].append(entry)
  reviews: dict[int, TaskSubmissionReview] = {}
  if query_review and submission_ids:
      reviews = {
          r.submission_id: r
          for r in await self._review_repo.list_by_submission_ids(submission_ids)
      }
  membership_ids = {s.membership_id for s in submissions}
  memberships = await self._membership_repo.get_by_ids(sorted(membership_ids))
  membership_map = {m.id: m for m in memberships} if isinstance(memberships, dict) else {
      m.id: m for m in memberships if m.id in membership_ids
  }

  items: list[dict] = []
  for submission in submissions:
      membership = membership_map.get(submission.membership_id)
      if membership is None:
          continue
      items.append(
          await self._build_submission_dto(
              submission=submission,
              membership=membership,
              entries=entries_by_submission.get(submission.id, []),
              review=reviews.get(submission.id),
          )
      )
  return items, total
  ```
  对应的两个仓储方法（与既有 `list_by_submission_id` 同形，只把等号换成 `in_`）：

  ```python
  # domain/task/repositories.py（TaskSubmissionEntryRepository 内）
  async def list_by_submission_ids(
      self, submission_ids: Sequence[int]
  ) -> Sequence[TaskSubmissionEntry]:
      if not submission_ids:
          return []
      stmt = select(TaskSubmissionEntry).where(
          TaskSubmissionEntry.task_submission_id.in_(list(submission_ids)),
          TaskSubmissionEntry.deleted_at.is_(None),
      )
      return list((await self._session.execute(stmt)).scalars().all())


  # domain/task/repositories.py（TaskSubmissionReviewRepository 内）
  async def list_by_submission_ids(
      self, submission_ids: Sequence[int]
  ) -> Sequence[TaskSubmissionReview]:
      if not submission_ids:
          return []
      stmt = select(TaskSubmissionReview).where(
          TaskSubmissionReview.submission_id.in_(list(submission_ids)),
          TaskSubmissionReview.deleted_at.is_(None),
      )
      return list((await self._session.execute(stmt)).scalars().all())
  ```
  （`TaskMembershipRepository.get_by_ids` 若不存在，加一个 `select(TaskMembership).where(TaskMembership.id.in_(ids), TaskMembership.deleted_at.is_(None))` 的批量版即可，与 `TaskRepository.get_by_id` 同一风格。）
- **契约**：不动响应；`entries` / `review` 的取值与逐条版一致（同一谓词）。风险：`entry.deleted_at` 的过滤要与现有 `list_by_submission_id` 完全一致（照抄，别自己加条件）。
- **测试**：`backend/tests/integration/test_submission_list_queries_once.py::test_a_page_of_submissions_costs_a_constant_number_of_queries`（同一 participant 造 10 份提交，断言语句数不随份数增长；带 `queryReview=1`）。

### POST /tasks/{taskId}/resubmit（无显式提交这一类，见 M2）

- **现状**：`tasks.py:2897`。校验「是出题者/管理员」+「当前是 DISAPPROVED」→ 改 `approved=2`、清 `reject_reason` → `task_repo.save` → 直接返回，**没有 `await db.commit()`**。
- **问题**：`app/core/db.py:171-179` 的 `get_db` 是在 `yield` 之后的收尾里提交的，而 FastAPI 的 `request_stack` 在响应发出**之后**才关（本仓 `tasks.py:1334-1339`、`tasks.py:2501-2505`、`tasks.py:1718-1722` 三处注释把这件事写得很清楚，并提到曾经实测踩到：合并队列 run 36296605673）。于是客户拿到 `200 {"task": {...approved:"NONE"}}` 之后立刻 `GET /tasks/{id}`，可能仍读到 `DISAPPROVED` —— 审核队列的上一条题还在「被驳回」栏里。
- **优化**：与同文件另外三处一样，响应前提交。

  ```python
  # tasks.py:2922 之后，return 之前
      task = await task_repo.save(task)
      # 与 create_task / patch_task 同一句、同一个理由：get_db 的提交在响应发出
      # 之后才跑，客户被告知「已重提」后紧接着读到的可能还是旧状态。
      await db.commit()
  ```
- **契约**：不动。风险：无（这条路由只有一次写入，提交提前不改变任何可见语义）。
- **测试**：`backend/tests/integration/test_a_write_is_committed_before_its_response.py::test_resubmit_is_readable_right_after_the_response`（先 `POST /tasks/{id}/resubmit` 再 `GET /tasks/{id}`，断言第二次读到的 `approved == "NONE"`）。同文件已有 `test_a_create_commits_before_its_response_is_sent.py` 这个先例可照。

### GET /projects/{project_id}/milestones

- **现状**：`milestones.py:97` → `MilestoneService.list_for_project`（`domain/milestone/services.py:46-54`）→ `repo.list_for_project` + `repo.count_for_project`。
- **问题**：
  - `domain/milestone/repositories.py:61-69`：**每次读**先跑 `mark_overdue`（`repositories.py:16-33`，一条 UPDATE）。GET 带写副作用：它让这个读接口无法被缓存、无法被条件请求（ETag/304 要求 GET 无副作用），也把读请求放进了写事务。
  - 同一次调用里有两条查询（列表 + `count_for_project`，`repositories.py:88-94`），而「总数」在**无分页**的接口里没有第二个人用（`milestones.py:103` 的 `total` 只被前端拿去显示）。
  - 没有任何 `limit`（`repositories.py:61-69` 无 limit/offset）。
- **优化**：把 `mark_overdue` 从读路径挪到写路径与定时任务；列表加可选 limit；`total` 只在真的分页时才另查。

  ```python
  # domain/milestone/repositories.py —— 读不带写。
  async def list_for_project(
      self, project_id: uuid.UUID, *, limit: int | None = None
  ) -> list[Milestone]:
      stmt = (
          select(Milestone)
          .where(Milestone.project_id == project_id)
          .order_by(nulls_last(Milestone.due_date.asc()), Milestone.created_at)
      )
      if limit is not None:
          stmt = stmt.limit(limit)
      return list((await self._session.scalars(stmt)).all())
  ```

  ```python
  # domain/milestone/services.py —— 惰性翻转只保留在写操作与日历/定时任务里；
  # 「过期」的判定本身可以无副作用地算（status == upcoming and due_date < now），
  # 需要落库的那一次由 update/create 与 delivery.timer 里的既有扫描负责。
  async def list_for_project(
      self, project_id: uuid.UUID, *, limit: int | None = None
  ) -> tuple[list[Milestone], int | None]:
      if await self._projects.get(project_id) is None:
          raise NotFoundError("Project not found")
      rows = await self._repo.list_for_project(project_id, limit=limit)
      return rows, None if limit is not None else len(rows)
  ```
- **契约**：`total` 的语义从「项目下全部里程碑数」变成「本页条数」时会**改变**，所以这里刻意保持默认路径（`limit=None`）原语义：不传 limit 时 `total = len(items)`，与今天在数量上一致（今天也是全量）。真正要做的是加 `?limit=`/`?cursor=`（与 M5 一起），那时 `total` 才需要另查 COUNT。风险：`mark_overdue` 挪走后，`GET /calendar`（#8）与前端倒计时的「临近」判定必须在读时按时间算而不是按 status 读 —— 这是本次改动里唯一需要小心的语义点。
- **测试**：`backend/tests/integration/test_milestones.py::test_listing_does_not_write`（在 `mark_overdue` 会命中的 fixture 上调 `GET /projects/{id}/milestones`，断言该项目的 `Milestone.status` 行未被 UPDATE —— 用会话事件或重新读一遍数据库）；`::test_list_limit_caps_and_reports_page_total`。

### GET /projects/{project_id}/calendar

- **现状**：`milestones.py:107` → `repo.list_calendar`（`repositories.py:71-86`，同样先 `mark_overdue`）→ `ok(page(items, len(items)))`。
- **问题**：与 #7 同一处读副作用；另外 `total` 传的是 `len(items)`，与 `/milestones` 的 `total`（`count_for_project`，全量）**同名不同义** —— 两个接口的 `data.total` 一个说「本页」一个说「全部」。
- **优化**：同上把 `mark_overdue` 挪走；`total` 口径统一（见 M5），或在字段名上直接消歧。
- **契约**：`data.total` 的语义属于**已发布契约**，两个同名接口给出两种含义是 bug 级的不一致；建议统一成「满足筛选条件的总条数」，并在没有分页的接口里让它等于页面长度（此时两者本就相等）。风险：前端若按「本页」使用 `/milestones` 的 total，会看到数字变大 —— 要一起改前端或先只改注释。
- **测试**：`backend/tests/integration/test_milestones.py::test_calendar_does_not_write`、`::test_both_milestone_lists_agree_on_total_meaning`。

### GET /projects/{project_id}/git/tasks/{task_id}

- **现状**：`git_http.py:137`。校验头里的 scoped token（两次 `verify_scoped_token`）→ 取 task/room/binding → `identity.attribution` → 若 `who.author` 则 `ensure_author_email(project_id, db, who.author.email)`（`git_http.py:165-168`）→ 回一份元数据。
- **问题**：
  - `git_http.py:168` 是本文件里**唯一**在 GET 里发远程请求的地方：`domain/project/forge.py:50-79` 会 `httpx` GET（必要时再 POST，再 GET）`{api}/user/emails`，**每次 timeout=30**（`forge.py:65`），并在邮箱未验证/服务不可达时抛 `GatewayUnavailableError`（`forge.py:68`、`79`）。也就是说「读一份任务的工作区元数据」会因为另一个服务的状态而失败（502/503），哪怕平台自己一切正常。
  - `forge.py:58` 又查了一遍 `binding_for_project`，而调用方 `git_http.py:157` 刚查过 —— 同一请求两条相同查询。
  - `git_http.py:143` 与 `:153` 对同一个 token 调了两次 `verify_scoped_token`（第二次多带 `topic_id`）。这本身是对的（先确认项目、再确认房间），但第二次的 `project_id` 校验是重复的。
  - 语义上：GET 不该有副作用，而这条 GET 会在外部系统里**创建**作者的邮箱（`forge.py:71` 的 POST）。
- **优化**：把「确保作者邮箱」挪到写路径（`open_task_workspace` POST，`git_http.py:115`）或后台任务，GET 只读；`binding` 传下去复用。

  ```python
  # git_http.py:157-168 —— GET 不再写外部系统。
      binding = await binding_for_project(project_id, db)
      if binding is None:
          raise NotFoundError("这个项目还没有代码仓库")
      room = await TopicService(db).get_or_404(task.room_id)
      who = await identity.attribution(db, room, task_id=task.id)
      # 作者邮箱的登记是一次写（forge.py:50 的 POST /user/emails），也是 GET 之外
      # 唯一需要它的地方（打开工作区那一刻，见下面 open_task_workspace）。
      # 放在这里意味着：一个只读元数据接口会向另一个服务写、并因它不可用而失败。
      return ok({...})
  ```

  ```python
  # git_http.py:115-133（POST open_task_workspace）—— 写的那条路顺手把邮箱登记了，
  # binding 已经从 _task_for 之后的查询里拿到，不重复查。
  async def open_task_workspace(...):
      from app.domain.project.forge import binding_for_project, ensure_author_email
      from app.domain.repository import identity

      task = await _task_for(db, project_id, task_id, x_cheese_token)
      acting = token_agent_handle(x_cheese_token or "")
      if not acting:
          raise AuthenticationRequiredError("Opening a task needs an agent identity")
      if task.status == "closed":
          raise ValidationError("这条任务已结束，请创建新任务")
      await TaskService(db).record_author(task, acting)
      room = await TopicService(db).get_or_404(task.room_id)
      who = await identity.attribution(db, room, task_id=task.id)
      if who.author:
          await ensure_author_email(project_id, db, who.author.email)
      result = await task_workspace(project_id, task_id, db, x_cheese_token)
      await db.commit()
      return result
  ```
- **契约**：不改响应形状。风险：客户端如果在只 GET 的情况下依赖「邮箱已被登记」，会推迟到第一次 POST 才成立 —— 而唯一会用到它的就是 POST（打开工作区）那条路，语义反而更正。另一个选项（更小改动）是保留调用但加 `asyncio.timeout` 与降级：`GatewayUnavailableError` 时只记警告、响应里带 `"author_email_registered": false`，不做远程调用。
- **测试**：`backend/tests/integration/test_git_http.py::test_workspace_manifest_does_not_call_the_forge`（注入一个会断言「未被请求」的 `httpx.MockTransport`，调 GET 断言它没被碰到，且 200）；`::test_open_workspace_registers_the_author_email`（POST 那条仍会调、且失败时请求整体失败）。

### POST /projects/{project_id}/git/tasks/{task_id}（open workspace）

- **现状**：`git_http.py:115`。`_task_for` → `record_author` → **直接调用上面的 GET handler 函数** `task_workspace(project_id, task_id, db, x_cheese_token)` → `db.commit()` → 返回。
- **问题**：`git_http.py:131` 复用了 GET handler，于是 `_task_for` 验过的 token（`git_http.py:22-31`）在 `:143`、`:153` 又验两遍；`TaskService(db).get(task_id)`（`:24`）与 `:150` 又取两遍。一次的元数据装配最后做了 3 次 token 校验 + 2 次 task 读。提交在响应前（`:132`，正确）。
- **优化**：把「装配元数据」抽成一个普通协程，两条路由都调用它；`_task_for` 的结果与已解析的 `binding` 一起传进去。

  ```python
  # git_http.py —— 新增一个纯读的装配函数，两条路由共用（token 只验一次）。
  async def _task_workspace_payload(db, *, project_id, task, binding) -> dict:
      from app.domain.repository import identity
      from app.domain.topic.services import TopicService

      room = await TopicService(db).get_or_404(task.room_id)
      who = await identity.attribution(db, room, task_id=task.id)
      return {
          "task_id": str(task.id),
          "room_id": str(task.room_id),
          "branch": task.branch_name,
          "base": task.base_branch,
          "closed": task.status == "closed",
          "remote": binding.url,
          "forge_kind": binding.kind,
          "forge_repo": binding.repo,
          "author": str(who.author) if who.author else None,
          "coauthors": [str(person) for person in who.coauthors],
      }
  ```
- **契约**：不动。风险：无（纯重构，两条路由的鉴权顺序保持不变；GET 那条仍自己验 token，因为它没有 `_task_for` 的 `record_author` 前置）。
- **测试**：`backend/tests/integration/test_git_http.py::test_both_workspace_routes_answer_the_same_payload`（GET 与 POST 的 `data` 除 `author` 外逐字段相等），配合一个记录 token 校验次数的包装（断言 ≤2）。

### GET /tasks/{taskId}/participants

- **现状**：`tasks.py:2937`。`may_teach_task` 门 → `list_memberships_for_task(approved=…)` → 批量取 team/user/profile 名册 → 逐行 `_build_participant_user_info`。
- **问题**：没有 `limit`，整份报名表一次返回；报表里每行带 `email` / `phone`（`tasks.py:876-877`）。板子里一道热门题几百人报名时，这一条同时是慢查询与大响应。查询本身是批量的（`tasks.py:2980-2987`），没有 N+1。
- **优化**：加 offset/limit 的既有风格参数（`pageStart`/`pageSize`，与 `get_task_submissions` 的 `tasks.py:3128-3129` 同款），`total` 用现成的 count。

  ```python
  # tasks.py:2937-2976 —— 与 submissions 的列表参数保持一致。
  async def get_task_participants(
      task_id: Annotated[int, Path(ge=1, alias="taskId")],
      approved: str | None = Query(default=None),
      queryRealNameInfo: bool = Query(default=False),
      pageStart: int = Query(default=0, ge=0),
      pageSize: int = Query(default=50, ge=1, le=200),
      ...
  ) -> dict:
      ...
      memberships = await membership_service.list_memberships_for_task(
          task_id=task_id, approved=approved_value, limit=pageSize, offset=pageStart
      )
      total = await membership_service.count_memberships_for_task(
          task_id=task_id, approved=approved_value
      )
      ...
      return {
          "code": 200,
          "message": "OK",
          "data": {
              "participants": participants,
              "page": {
                  "pageStart": pageStart,
                  "pageSize": len(participants),
                  "hasMore": pageStart + len(participants) < total,
                  "nextStart": (
                      pageStart + len(participants)
                      if pageStart + len(participants) < total
                      else None
                  ),
                  "total": total,
              },
          },
      }
  ```
- **契约**：**加**字段（`data.page`）不是改字段，老客户端忽略即可；但同一路径下加 limit 会改变默认返回量，所以默认值要给得足够大或先用 `limit` 可选参数（不传=全量）过渡一轮。风险：`list_memberships_for_task` 目前没有 limit/offset 参数，需要加（`domain/task/repositories.py` 里对应的 `list_memberships_for_task`）。
- **测试**：`backend/tests/integration/test_task_participants_page.py::test_participants_are_paged_and_total_is_the_whole_roster`。

### PATCH /tasks/{taskId}/participants

- **现状**：`tasks.py:2829`。按 `(taskId, member)` 改一条报名 → `activate_participation` → **返回当前任务下所有参与者**（`tasks.py:2879-2889`），每行由 `_membership_to_api_model(m)` 生成 —— `participant_info` 没传，于是 `member`/`participant` 落到默认的 `{"id": membership.member_id}`（`tasks.py:863`）。
- **问题**：
  - **形状不一致**：同名资源 `participants` 在 `GET /tasks/{taskId}/participants`（`tasks.py:2990-2996`）里是「带 username/nickname/avatarId 的人」，在这里只是 `{id}`（`tasks.py:2879-2882`）。同一个列表，两条接口给出两种元素 —— 前端按其中一条写渲染，另一条就会「名字是 undefined」（这类 bug 本文件在 `tasks.py:719-722`、`:2197-2199` 的注释里已经踩过两次）。
  - 返回整份名单＋无分页：一次 PATCH 的响应体随报名人数线性增长。
  - 无显式提交（见 M2）。
- **优化**：复用 `get_task_participants` 的装配（同样的批量取数），或干脆只回被改的那一条。

  ```python
  # tasks.py:2874-2890 —— 与 GET 名单同一套装配：批量取名册，逐行带人。
  await ProjectService(db).activate_participation(task=task, membership=membership)
  await db.commit()

  memberships = await membership_service.list_memberships_for_task(
      task_id=task_id, approved=None
  )
  user_ids = [m.member_id for m in memberships if not m.is_team]
  team_ids = [m.member_id for m in memberships if m.is_team]
  team_map = await TeamRepository(session=db).get_by_ids(team_ids)
  user_map = await UserRepository(session=db).get_by_ids(user_ids) if user_ids else {}
  profile_map = (
      await UserProfileRepository(session=db).get_profiles_by_user_ids(user_ids)
      if user_ids
      else {}
  )
  participants = [
      _membership_to_api_model(
          m,
          participant_info=_build_participant_user_info(
              m, user_map=user_map, profile_map=profile_map, team_map=team_map
          ),
      )
      for m in memberships
  ]
  ```
- **契约**：响应里 `participants[].member` / `.participant` 会从 `{id}` 变成完整对象 —— 这是**修 bug 式**的加字段（老客户端若只读 `.id` 不受影响）。风险：两条接口的元素形状从此必须一起维护，建议把这段装配直接抽成 `_participants_payload(db, task_id, approved, membership_service)` 给两处用（现在 `tasks.py:2989-2996` 与 `2879-2882` 是两份手写）。
- **测试**：`backend/tests/integration/test_task_participants_shape.py::test_patch_and_get_return_the_same_participant_shape`（同一条报名，两条接口的 `member` 字段逐键相等）。

### GET /projects/{project_id}/routines 与 GET /routines/{routine_id}/runs

- **现状**：`routines.py:146`（`RoutineService.list`，无 limit）与 `routines.py:277`（`RoutineService.runs`）。
- **问题**：两条都是 `ok(page(items, len(items)))` —— `api/response.py:21` 的 `page()` 返回 `{"data": items, "total": total}`，被 `ok()` 包一层后，客户端拿到的是 **`data.data`** 与 `data.total`。于是：(1) 同一个文件里 `total` 恒等于本页条数，客户端无法判断后面还有没有；(2) 信封里嵌了一个叫 `data` 的 `data`，与 `tasks.py` 那套 `data.tasks` + `data.page` 的写法不是一个形状。`runs()` 内部有默认上限（`domain/routine/service.py` 的 `runs(..., limit=50)`），也就是说确实会截断，而 `total` 却谎报「就这么多」。
- **优化**：短的那条（`runs`）把真实关系说清楚：要么返回 `hasMore`，要么给出 `limit` 参数。

  ```python
  # routines.py:277-282 —— total 说真话：要么把内部上限暴露成参数，要么明确「本页」。
  @router.get("/routines/{routine_id}/runs")
  async def list_runs(
      routine_id: uuid.UUID,
      db: DbSession,
      resolver: ActorResolverDep,
      limit: int = 50,
  ) -> dict:
      row, _ = await _routine_actor(db, resolver, routine_id)
      runs = list(await RoutineService(db).runs(row.id, limit=limit))
      return ok(
          {
              **page([_run(r) for r in runs], len(runs)),
              # 内部上限==请求的上限，所以「到底了」是可判定的：
              "hasMore": len(runs) == limit,
          }
      )
  ```
- **契约**：加字段（`hasMore`）不改现有字段；`data.data` 这个嵌套是既有形状，若要改（推荐 `{"routines": [...]}`）是**破坏性**的，需要与前端一起（见 M5）。风险：低。
- **测试**：`backend/tests/integration/test_routines.py::test_runs_report_whether_more_exist`（造 51 条执行记录，断言 `hasMore is True`，且 `limit=100` 时为 `False`）。

### POST /routines/{routine_id}/run-now

- **现状**：`routines.py:249`。`_person` 门 → `run_now` → `db.commit()` → **在请求里** `await routines.dispatch_pending(chat.session_factory, chat=chat, runner=get_work_runner())`（`routines.py:259-261`）。
- **问题**：`domain/delivery/agent.py:105` 的 `dispatch_pending` 是**平台级**的：它按 `Delivery.sent_at is None and state in (pending/claimed/sending)` 扫（`:113-127`，`limit=100`，带 `FOR UPDATE SKIP LOCKED`），然后对每条投递做真实发送。也就是说「立即执行这条 routine」这个请求的响应时间取决于**整个平台**此刻有多少待投递，以及它们要多久。同文件其它地方（`topics.py:944`、`accept.py:434`、`block/editing.py:97`、`review/pr_poll.py:255`）都是同款，所以这是既有模式；但模式本身在 API 层面等于「把后台队列的推进挂在用户的 HTTP 请求上」。
- **优化**：请求只负责「提交这次执行」，推进交给后台（`app/core/background.py` 的 `spawn`，仓库里已有这个共用件）。

  ```python
  # routines.py:248-262
  @router.post("/routines/{routine_id}/run-now")
  async def run_routine_now(
      routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
  ) -> dict:
      from app.api.deps import get_chat_service, get_work_runner
      from app.core.background import spawn

      row, actor = await _routine_actor(db, resolver, routine_id)
      _person(actor, "立即执行")
      run = await RoutineService(db).run_now(row, by=actor.handle)
      await db.commit()
      chat = get_chat_service()
      runner = get_work_runner()
      # 响应不该等整个平台的待投递队列：把推进交给后台，请求只回答「这次执行已记下」。
      spawn(
          routines.dispatch_pending(chat.session_factory, chat=chat, runner=runner),
          name="routine:run-now:dispatch",
      )
      return ok(_run(run))
  ```
- **契约**：不改响应；语义上「返回时投递已发出」→「返回时投递已排队」，要写进接口文档（`dispatch_pending` 本身幂等、有租约，重复触发是安全的）。风险：靠 `spawn` 后请求可能先返回，测试里需要 `inflight_count()` 或 `tests/support/` 的既有等待件来观察（routines 的既有测试用 `_sweep()`，可直接复用）。
- **测试**：`backend/tests/integration/test_routines.py::test_run_now_answers_without_waiting_for_the_platform_dispatch`（把 `dispatch_pending` 换成一个「只被调用、不真的发」的假件，断言 200 立刻返回且假件被排入）。

### POST /tasks/publish/from-pdf/confirm

- **现状**：`tasks.py:1667`。先整批校验附件 → 对每道草稿调 `_create_task_entity`（`tasks.py:1694-1706`）→ 挂附件 → 显式 commit（`:1722`）→ 富化。
- **问题**：`_create_task_entity`（`tasks.py:1058`）每被调用一次都会：查 space（`tasks.py:1206`，`space_repo.get_by_id(space_id)`）、查/建分类（`tasks.py:1222-1227`）、跑 `may_publish_in_space`（`tasks.py:1216`）。这 20 道草稿来自**同一次预览、同一个 space、同一个分类**，所以三件事各被重复问了 20 遍（分类那条尤其：`_validate_and_get_category_id` 会再查一次 space 与分类）。20 道上限是 `tasks.py:1676-1677` 写死的。
- **优化**：把「发题门 + space + 分类」在这一批开始前算一次，作为参数传进 `_create_task_entity`。

  ```python
  # tasks.py:1692-1706 —— 每道草稿重复的三件事提到循环外。
  # _create_task_entity 增加可选入参（默认 None，保持 POST /tasks 那条调用不变）：
  #   space_checked: bool = False, effective_category_id: int | None = None
  first_payload = _apply_pdf_task_options(
      draft=drafts[0], task_options=task_options
  )
  space_id = int(first_payload["space"])
  space = await SpaceRepository(session=db).get_by_id(space_id)
  if space is None or space.review_status != "APPROVED":
      raise BadRequestError("Space must be approved before creating tasks")
  if not await may_publish_in_space(
      session=db, space_id=space_id, user_id=auth_user.user_id
  ):
      raise ForbiddenError("Only a member of this board can publish tasks here")
  effective_category_id = await _validate_and_get_category_id(
      space_repo=SpaceRepository(session=db),
      category_repo=SpaceCategoryRepository(session=db),
      space_id=space_id,
      category_id=_apply_pdf_task_options(
          draft=drafts[0], task_options=task_options
      ).get("categoryId"),
  )

  created_tasks: list[Task] = []
  for draft in drafts:
      task_payload = _apply_pdf_task_options(draft=draft, task_options=task_options)
      created = await _create_task_entity(
          payload=task_payload,
          db=db,
          creator_user_id=auth_user.user_id,
          space_checked=True,
          effective_category_id=effective_category_id,
      )
      created_tasks.append(created)
  ```
- **契约**：不动响应。风险：`_apply_pdf_task_options` 对不同 draft 可能给出不同 `categoryId`/`space`（`tasks.py:1695` 说明它是按 draft + options 合成的）；如果确实会不同，就不能共用一个 `effective_category_id` —— 上策是先断言这 20 份的 `space`/`categoryId` 一致（不一致就退回逐条校验），把「同批同板」写成一个显式前提，而不是默默假设。这条要在实现时先读 `_apply_pdf_task_options` 确认（本报告只读到了它的调用点）。
- **测试**：`backend/tests/integration/test_task_pdf_publish.py::test_confirm_checks_the_board_gate_once_for_the_whole_batch`（用 `before_cursor_execute` 统计 `space_admin_relation` 上的查询次数，20 份草稿仍为常数）。

### GET /tasks/{taskId}/attachments/{attachmentId}/download 与 GET /projects/{p}/git/tasks/{t}/snapshots/{snapshot_id}

- **现状**：`tasks.py:1483`（`TaskAttachmentService.download` → `attachment_service`，把整份内容读成 `bytes` 交给 `Response(content=...)`）；`git_http.py:93`（`snapshots.download(row)` → `Response(await snapshots.download(row), media_type="application/x-git-bundle")`）。
- **问题**：两条都把整个对象读进内存后才交给 ASGI 层：任务备份单次上限 512 MiB（`git_http.py:54-57`），附件受 nginx 100 M 限制（`tasks.py:1458-1460` 的注释）。并发几份就是几倍内存，而且首字节要等整份下完。**流式**是这条路的业界通行做法（大文件下载用 `Content-Disposition` + 分块传输，不算新协议）。
- **优化**：存储层若能给出可迭代/分块的读，就交给 `StreamingResponse`。

  ```python
  # git_http.py:93-112 —— 边读边发，不再整份驻内存。
  from fastapi.responses import StreamingResponse

  @router.get("/{project_id}/git/tasks/{task_id}/snapshots/{snapshot_id}")
  async def download_task_snapshot(...):
      row = await db.get(TaskSnapshot, snapshot_id)
      if row is None or row.task_id != task_id:
          raise NotFoundError("这条任务没有此备份")
      return StreamingResponse(
          snapshots.download_chunks(row),           # 见下面 snapshots.py 的新函数
          media_type="application/x-git-bundle",
          headers={
              "Cache-Control": "no-store",
              "Content-Disposition": f'attachment; filename="{row.snapshot_sha}.bundle"',
          },
      )
  ```

  ```python
  # domain/room_task/snapshots.py —— 逐块读（storage 若只有 download()，就用 to_thread
  # 在后台线程里把整份读完后按 1 MiB 切，仍然避免了在事件循环上做序列化）。
  _CHUNK = 1024 * 1024

  async def download_chunks(
      row: TaskSnapshot, *, storage: StorageBackend | None = None
  ) -> AsyncIterator[bytes]:
      content = await download(row, storage=storage)   # 现成的「整份 + 校验摘要」
      for offset in range(0, len(content), _CHUNK):
          yield content[offset : offset + _CHUNK]
  ```
  （`snapshots.download` 里的摘要校验（`snapshots.py:110-111`）必须保留 —— 分块发出去前先校验整份，这正是当前实现的价值；真正省掉的是 ASGI 层的二次复制与一次性驻留。）
- **契约**：不动；`Content-Disposition` 是新增头（对下载接口是正向）。风险：`Response(content=bytes)` 与 `StreamingResponse` 的差异在于前者会设 `Content-Length`，后者默认用 chunked；代理链（nginx）需要允许 chunked（`X-Accel-Buffering` 一族在本仓 `tasks.py:3904` 已有先例）。
- **测试**：`backend/tests/integration/test_git_http.py::test_snapshot_download_streams_without_buffering`（打一个 2 MiB 的假 bundle，断言响应按块产出且 `Cache-Control: no-store` 仍在）。

### PUT /projects/{project_id}/git/tasks/{task_id}/snapshots/{snapshot_sha}

- **现状**：`git_http.py:37`。`request.stream()` 逐块收，落到 `SpooledTemporaryFile(max_size=8MiB)`，`git_http.py:58` 对**每个块**做一次 `await asyncio.to_thread(file.write, chunk)`；收完由 `snapshots.save` 校验 sha/摘要/bundle 头、按 digest 去重、带 120s 超时上传。
- **问题**：每个请求块一次线程池往返（`git_http.py:58`）。Uvicorn 的默认请求块很小（几十 KB 级），512 MiB 的包就是上千次 `to_thread` 调度；而 `SpooledTemporaryFile.write` 本身是内存/页缓存内的短操作，为它付线程调度的代价不划算。另外整包先落盘、**再**校验摘要（`snapshots.py:54-58`）—— 传一个坏包要全程传完才知道（这是设计取舍，不算 bug，但结合上面那条会放大代价）。
- **优化**：块先攒到阈值再落盘，或者干脆同步写（`SpooledTemporaryFile` 在内存里时是纯 Python 对象操作，落盘后是一次 `write(2)`）。

  ```python
  # git_http.py:50-58 —— 攒到 1 MiB 再写一次（线程池往返从「每块一次」降到「每 MiB 一次」）。
  size = 0
  pending = bytearray()
  with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as file:
      async for chunk in request.stream():
          size += len(chunk)
          if size > 512 * 1024 * 1024:
              raise ValidationError("单次任务备份超过 512 MiB，请将大文件移入附件存储")
          pending += chunk
          if len(pending) >= 1024 * 1024:
              await asyncio.to_thread(file.write, bytes(pending))
              pending.clear()
      if pending:
          await asyncio.to_thread(file.write, bytes(pending))
      row = await snapshots.save(...)
  ```
- **契约**：不动；`snapshots.save` 的 `file.seek(0)`（`snapshots.py:52`、`:59`、`:62`）在收完最后一块之后才第一次被调用，攒批不改变它的语义。风险：低（唯一要保证的是循环结束时 `pending` 必须落盘）。
- **测试**：`backend/tests/integration/test_git_http.py::test_snapshot_upload_writes_in_batches`（注入一个计数用的假 `to_thread` 或对 `SpooledTemporaryFile.write` 计数：一个 4 MiB 的包写入次数 ≤ 8）。

### PATCH /tasks/{taskId}（代码质量）

- **现状**：`tasks.py:2281`，单函数约 240 行（`2281-2522`），把 20 多个字段一个一个 `if payload.x is not None:` 地往 ORM 对象上贴，中间夹杂权限、审批副作用、`TaskTagRelation` 的先软删后插入、分类校验。
- **问题**：新增一个字段要在这个 240 行的函数里找位置插入（`patch_task` 与 `_create_task_entity` 的 dict 分支已经有过一次同字段两处维护的历史，见 `tasks.py:1315` 的注释）；`topics` 覆盖写（`tasks.py:2474-2497`）对 `tag_id` 不做任何校验、也不判重（`list(dict.fromkeys(...))` 在这个函数里没有），与 `create_task` 的 dict 分支行为要靠人记住。这条是纯代码质量，不是性能或安全问题。
- **优化**：把字段赋值抽成表驱动的一段（同一份 `_FIELD_SETTERS` 表给 create/patch 两侧共用），并把 `topics` 覆盖写交给一个既有先例式的仓储方法。

  ```python
  # tasks.py —— 与 _create_task_entity 共用一份「字段 → ORM 属性」的映射。
  # 时间戳类的转换收在一处：三种字段今天各自手写一遍 fromtimestamp（2329/2339/2385）。
  _MILLIS_TO_COLUMN: dict[str, str] = {
      "deadline": "deadline",
      "registration_start_at": "registration_start_at",
      "ended_at": "ended_at",
  }

  def _apply_millis_field(task: Task, payload: PatchTaskRequest, name: str) -> None:
      """payload.<name> 是毫秒时间戳或 None，落到同名（下划线）列上。"""
      if f"has_{name}" in payload.model_fields_set and getattr(
          payload, f"has_{name}"
      ) is False:
          setattr(task, _MILLIS_TO_COLUMN[name], None)
          return
      raw = getattr(payload, name)
      if raw is None:
          return
      try:
          setattr(
              task,
              _MILLIS_TO_COLUMN[name],
              datetime.fromtimestamp(int(raw) / 1000.0, tz=UTC),
          )
      except (TypeError, ValueError) as exc:
          raise BadRequestError(f"Invalid {name}: {exc}") from exc
  ```
  并在 `tasks.py:2474-2497` 用与 `TaskAccessDomainRepository.replace_domains`（`domain/task/repositories.py:1386`）同款的 `replace_topics`（去重 + 先软删后插，同一个 `now`）：

  ```python
  # domain/task/repositories.py（新增，与 TaskAccessDomainRepository.replace_domains 同形）
  async def replace_topics(self, *, task_id: int, topic_ids: Sequence[int]) -> None:
      now = datetime.now(UTC)
      stmt: Select[tuple[TaskTagRelation]] = select(TaskTagRelation).where(
          TaskTagRelation.task_id == task_id,
          TaskTagRelation.deleted_at.is_(None),
      )
      for item in (await self._session.execute(stmt)).scalars().all():
          item.deleted_at = now
          item.updated_at = now
      for topic_id in dict.fromkeys(topic_ids):
          self._session.add(
              TaskTagRelation(
                  task_id=task_id,
                  tag_id=int(topic_id),
                  created_at=now,
                  updated_at=now,
                  deleted_at=None,
              )
          )
      await self._session.flush()
  ```
- **契约**：不动（`topics` 去重是行为微调：同一 id 传两次今天会插两行，之后插一行 —— 这正是 `patch_task` 缺的校验，需要一条测试钉住）。风险：`PATCH` 是核心接口，重构要一步一测；建议先只抽上面那张时间戳表（收益最直接、风险最小）。
- **测试**：`backend/tests/contract/test_tasks_contract.py::test_patch_task_accepts_the_same_fields_as_create`（对同一组字段，create 与 patch 后 `GET` 出的值一致）；`backend/tests/integration/test_task_topics_replace.py::test_duplicate_topic_ids_are_stored_once`。

### GET /tasks/{taskId}/teams

- **现状**：`tasks.py:3068`。校验是要 TEAM 题、`filter ∈ {eligible, all}`、返回 `team_service.get_teams_of_user(user_id=auth_user.user_id)`。
- **问题**：函数 docstring 自己写着「当前 `filter=eligible` 与 `filter=all` 行为一致」（`tasks.py:3079`）。参数存在、被校验、被忽略 —— 调用方按文档传 `eligible` 期待「有资格报这道题的队伍」，拿到的是「我所有的队伍」。这是**契约层面的空承诺**：客户端与 API 目录都会认为这个参数有意义。
- **优化**：要么实现 eligibility（用现成的 `get_participation_eligibility`，`tasks.py:2228` 同款），要么把参数去掉/降级为文档说明。最小正确做法是把两条取值的行为差异写进响应，让调用方立刻能发现：

  ```python
  # tasks.py:3082-3091 —— 两种 filter 给出各自的答案（eligibility 走已有的判据）。
  teams = await team_service.get_teams_of_user(user_id=auth_user.user_id)
  if filter == "eligible":
      eligible: list = []
      for team in teams:
          eligibility = await membership_service.get_participation_eligibility(
              task=task, user_id=team.member_user_id
          )
          if eligibility.get("eligible"):
              eligible.append(team)
      teams = eligible
  ```
  （`get_participation_eligibility` 是按 user 算的（`domain/task/services.py:331`），TEAM 题要按队伍成员展开 —— 上面这段是示意，落地前需要确认它接受的 user_id 语义；若这条路太绕，就按 docstring 的做法**把 `filter` 标记为 deprecated 并在响应里回 `"filterApplied": filter`**，让空承诺消失。）
- **契约**：若真实现 eligibility，`eligible` 的返回集合会**变小**（今天返回全部）—— 这是行为变更，需要与前端确认它在用哪一档。风险：中。
- **测试**：`backend/tests/integration/test_task_teams_filter.py::test_eligible_and_all_differ_or_the_parameter_is_gone`。

### GET /tasks/{taskId}/ai-advice 与 GET /tasks/{taskId}/ai-advice/conversations/{conversationId} 与 GET .../grouped

- **现状**：`tasks.py:3606`（`service.list_advices` → `AIConversationRepository`/`TaskAIAdviceRepository.list_by_task`）、`tasks.py:3670`（`get_conversation` → `_message_repo.list_for_conversation(convo.id)`）、`tasks.py:3644`（`list_for_task`）。
- **问题**：三条都无 limit。`domain/task/repositories.py:1076`（`list_by_task`）与 `:1180`（`list_for_task(owner_id=…)`）都是「where + order by」没有 limit；`list_for_conversation` 同理。对话攒多了，`GET conversations/{id}` 会把**整个对话的每一轮**（一问一答逐条）返回并逐条配成 Q&A（`tasks.py:3695-3714`）。
- **优化**：`advices` 是「一道题的建议」—— 按 `created_at desc` 取最新一条其实就够（`request_advice` 里用的正是 `get_latest`）；`conversations/{id}` 加 `?limit=&before=`（按消息 id 往前翻，与仓库里 `feedback.py` 的 replies 游标同一套写法）。

  ```python
  # tasks.py:3606-3618 —— 加 limit，默认沿用「最近一条」这个事实语义。
  async def list_task_ai_advice(
      task_id: Annotated[int, Path(ge=1, alias="taskId")],
      limit: int = Query(default=20, ge=1, le=100),
      ...
  ) -> dict:
      await _ensure_task_visible_for_advice(db=db, task_id=task_id, auth_user=auth_user)
      advices = await service.list_advices(task_id=task_id, limit=limit)
      ...
  ```
- **契约**：加参数的默认值决定行为：`limit=20` 会改变「返回全部」的既有行为，建议先用 `limit: int | None = None`（不传=全量）过渡，并在 M5 的统一分页里一次收口。风险：低。
- **测试**：`backend/tests/integration/test_ai_advice_conversations_belong_to_their_asker.py::test_conversation_messages_can_be_paged`（造 60 条消息，`limit=20` 只回 20 条，`before` 能翻到更早）。

### GET /tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review 与 DELETE 同名

- **现状**：GET `tasks.py:3395`（先 `_bind_review_path` 三段绑定 → `get_review_dto`）；DELETE `tasks.py:3521`（`_bind_review_path` → `get_review_dto` **在 `:3540` 判在不在** → `delete_review` → **`:3545` 再 `get_review_dto` 一次**只为了把「删除后」的状态放进响应）。
- **问题**：DELETE 里同一个 `submission_id` 的评审行查了两遍（`tasks.py:3540`、`:3545`），而第二遍必然返回 `{"reviewed": False}` —— 一个「删除成功」的响应里带一个空 review 对象，读者要靠 `reviewed` 字段才知道刚才删掉了什么。这是重复查询 + 形状含糊两者叠加。
- **优化**：`get_review_dto` 只调一次（删除前那次就是「删了什么」），响应给出「删了什么」而不是「删完之后剩什么」。

  ```python
  # tasks.py:3540-3546
      existing = await review_service.get_review_dto(submission_id)
      if not existing.get("reviewed"):
          raise NotFoundError.for_resource("review", submission_id)

      await review_service.delete_review(submission_id=submission_id)
      # 不再查第二遍：删完必然是「没有评审」，第二遍只是把同一行再读一次。
      # 回的是「删掉了什么」——调用者拿到的就是刚才那一份。
      return {"code": 200, "message": "OK", "data": {"review": existing}}
  ```
- **契约**：`data.review.reviewed` 从 `false` 变成 `true`（且带 `detail`）—— 对「删除」这件事来说，回「删掉了什么」比回「现在什么都没有」有用，但如果已有客户端按 `reviewed:false` 判成功，就是一次破坏性变更。折中：只去掉第二次查询，响应保持 `{"review": {"reviewed": False}}` 也行（省一条查询、零契约风险）——**建议先做这一步**，形状的调整留给前端一起。
- **测试**：`backend/tests/integration/test_submission_review_delete.py::test_delete_reads_the_review_once`（统计 `task_submission_review` 上的 SELECT 次数为 1，且接口仍回 200）。

### PUT /tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review

- **现状**：`tasks.py:3484`，summary 写着 "Update Submission Review (Full Replace)"，实现是 `review_service.patch_review(submission_id, accepted, score, comment)`（`tasks.py:3504-3509`），而 `patch_review`（`domain/task/services.py:1060-1087`）对 `None` 的字段**跳过不写**。
- **问题**：请求体用的是 `CreateSubmissionReviewRequest`（三个字段都必填，所以从 HTTP 这一层看不出差别），但实现是 PATCH 语义 —— 名字（Full Replace）、HTTP 方法（PUT，RFC 9110 定义 PUT 为「用请求里的表示替换目标资源」）与行为三者对不上。今天能对上纯粹是因为请求体把三个字段都设成了必填；一旦有人给这个 schema 加个可选字段（比如 `comment` 可空），PUT 就会静默变成 PATCH。DELETE/PATCH/PUT 三条路由复用同一个 service 方法、`may_teach_task` 判据与错误文案也逐字相同（`tasks.py:3464-3465`、`3501-3502`、`3537-3538`），重复度很高。
- **优化**：让 PUT 真的整份替换（缺字段写默认值），或把 PUT 从路由表里去掉、只留 PATCH（更省事，也更少一个要维护的入口）。若保留：

  ```python
  # domain/task/services.py —— 给 PUT 用的整份替换：三个字段都写。
  async def replace_review(
      self, *, submission_id: int, accepted: bool, score: int, comment: str
  ) -> dict:
      review = await self._review_repo.get_by_submission_id(submission_id)
      if review is None:
          raise NotFoundError.for_resource("review", submission_id)
      previous_accepted = review.accepted
      review.accepted = accepted          # 三个都无条件写 —— 这就是 PUT 与 PATCH 的差别
      review.score = score
      review.comment = comment
      review.updated_at = datetime.now(UTC)
      await self._review_repo.save(review)
      dto = await self.get_review_dto(submission_id)
      dto["hasUpgradedParticipantRank"] = await self._maybe_award_rank(
          submission_id=submission_id,
          previous_accepted=previous_accepted,
          new_accepted=review.accepted,
      )
      return dto
  ```
- **契约**：不改响应与请求形状（请求体本来就三个必填字段）；改的是「传 `score=0` 或 `comment=""` 时今天会被 `None` 判断放过的路径」—— 实际上 `0`/`""` 不是 `None`，今天也会写。所以**行为无变化**，只是让实现与名字一致。风险：低。若决定删 PUT，是破坏性变更，需要先确认没有客户端在用（本仓前端 `TasksApi` 里没看到 PUT 这条）。
- **测试**：`backend/tests/integration/test_submission_review_put_is_a_replace.py::test_put_overwrites_every_field`。

### 其余可优化项（一句一条，避免与本报告前面重复展开）

- **POST /tasks（#22）**：`tasks.py:1302` 没有可选幂等键。超时重发 = 两道一模一样的题（`_create_task_entity` 不做任何去重）。做法见 M1。
- **POST /tasks/{taskId}/participants（#29）、/participations/user（#30）、/participations/team（#31）**：`create_membership`（`domain/task/services.py:224-233`）先查 `get_by_task_and_member` 再插，`task_membership` 上没有 `(task_id, member_id)` 唯一约束（`domain/task/models.py:138-180` 无 `UniqueConstraint`，`alembic/versions/a95752502bb0_initial_schema.py:849` 建表时也没有）。双击/并发重发会留下两行；之后 `get_by_task_and_member` 用的 `scalar_one_or_none`（`domain/task/repositories.py:589`）会抛 `MultipleResultsFound`，把这个 pair 的所有后续请求变成 500。做法见 M3。
- **POST .../submissions（#45）**：`domain/task/services.py:755-764` 的 `get_latest_version_for_membership` + `create_submission(version=latest+1)` 是「先查后插」；`task_submission` 上无 `(membership_id, version)` 唯一约束。并发两次提交会得到两行同版本，而 PATCH 那条路由是按 `version` 寻址的（`tasks.py:3263` 的 path 参数），于是地址变歧义。做法见 M3。
- **POST .../review（#47）**：`tasks.py:3374-3376` 先 `get_review_dto` 判「是否已有评审」再 `create_review`（`domain/task/services.py:1045`，一条裸 INSERT），`task_submission_review` 无 `submission_id` 唯一约束（`domain/task/models.py:278-301`）。双击打分 = 两行评审，`get_by_submission_id`（`scalar_one_or_none`）从此抛错；且 `_maybe_award_rank`（`services.py:1095-1125`）在两次并发里都会看到 `previous_accepted is None`，两次都可能发奖。做法见 M3（唯一约束把「重复发奖」这件事直接挡在事务外）。
- **POST /tasks/publish/from-pdf/preview（#27）**：`tasks.py:1537` 在请求里同步走模型（PDF → 草稿）。无幂等键，客户端重试就再花一次 token；15 MiB 的 PDF 也整份进内存（`:1555`，有上限，可接受）。
- **POST /tasks/{taskId}/ai-advice（#52）**：`task_ai_advice_service.py:48-61` 的 `request_advice` 同步调 `_generate_advice` → `_generate_advice`（`:323-349`）一次模型调用生成四段建议，然后 `_ensure_contexts`（`:351-364`）对**每一段里的每一条**做一次 `get_or_create` —— 一个 SELECT、可能加一条 INSERT，条数是模型的输出长度决定的（几十条很常见）。请求时长 = 模型时长 + 几十条往返。建议：`_ensure_contexts` 批量插入（一次 `select` 出现在集合 + 一次 `add_all`），生成本身改成「先落 PENDING 再后台跑、客户端轮询 `GET /ai-advice/status`」（status 接口已经在了，今天的 `request_advice` 却是同步等完成，等于把已有的一对接口用成了一条阻塞调用）。
- **POST /tasks/{taskId}/ai-advice/conversations（#57）**：`task_ai_advice_service.py:73-144` 的 `create_conversation` 同步等一次完整的非流式 completion；同一件事的流式版已有（`stream_conversation`，`:146`）。非流式的这条路是给「没有 EventSource 的调用方」用的，保留合理，但应加超时与幂等键（重发就是两条问答、两次配额）。
- **GET /tasks/{taskId}/ai-advice/status（#54）**：暂无（一次 `get_latest`），是给上面那条轮询用的现成件。
- **DELETE /milestones/{milestone_id}（#10）**：`milestones.py:152` 回 `ok(None)`（HTTP 200 + `{"code":200,"data":null}`），而 `tasks.py` 里所有 DELETE（`tasks.py:2719`、`:2758`、`:2791`、`:1518`）都是 204 无体。同一个平台两套删除口径，见 M4。
- **GET /tasks/{taskId}/participants/{participantId}（#42）**：暂无 —— 判据与列表版一字不差（`tasks.py:3032` 与 `:2958`），403 的措辞在 docstring 里解释了为什么不是 404，是本文件里鉴权写得最清楚的一处。
- **PUT /milestones/{milestone_id}（#9）**、**PATCH /tasks/{taskId}/participants/{participantId}（#32）**、**DELETEs（#36/#37/#38）**、**POST /tasks/{taskId}/attachments（#24）**、**DELETE /tasks/{taskId}/attachments/{attachmentId}（#26）**、**DELETE .../conversations/{conversationId}（#58）**、**PATCH/PUT review（#49/#50）**：都只差一句 `await db.commit()`，见 M2。
- **POST /projects/{project_id}/milestones（#6）**：除 M2 外，幂等键只在 `body.source_topic_id` 存在时才生效（`milestones.py:63-77`）—— 人从 UI 钉的那条路没有去重，而 UI 的「保存」按钮正是最容易被点两次的那个。建议把幂等键从「有没有 room」解耦成「请求头带没带 `Idempotency-Key`」，见 M1。

---

## 三、模块级建议（跨接口）

### M1. 创建类 POST 支持 `Idempotency-Key` 请求头

- **范围**：`POST /tasks`（`tasks.py:1302`）、`POST /tasks/{taskId}/resubmit`（写一次，语义幂等，可以不收）、`POST /tasks/publish/from-pdf/confirm`（`tasks.py:1667`，一次最多 20 道题）、`POST /projects/{project_id}/milestones`（`milestones.py:32`）、`POST /topics/{topic_id}/routines`（`routines.py:167`）、`POST /tasks/{taskId}/ai-advice`（`tasks.py:3574`，一次模型计费）、`POST /tasks/{taskId}/ai-advice/conversations`（`tasks.py:3722`，一次模型计费）、`POST /tasks/{taskId}/participants`（`tasks.py:1782`）。
- **做法**：平台已经有现成的键与存储 —— `app/domain/idempotency/` 的 `action_key`、`idem.claim` / `stored_result` / `record_result`，`POST /projects/{project_id}/milestones` 正在用它（`milestones.py:63-88`），`POST /topics/{topic_id}/decision` 是模板级写法。把它从「route 自己拼 key」升级成一个共享依赖，读请求头、拼 key、命中就回原结果：

  ```python
  # app/api/idempotency.py（新增；或放进 app/api/deps.py）
  from typing import Annotated
  from fastapi import Header, Depends
  from sqlalchemy.ext.asyncio import AsyncSession

  from app.api.response import ok
  from app.core.errors import ConflictError
  from app.domain.idempotency import store as idem

  IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]

  async def replay_or_run(
      *,
      db: AsyncSession,
      key: str | None,
      action: str,
      scope: str,
      run,
  ) -> dict:
      """同一个 Idempotency-Key 重放首次的结果；没有键就照常跑。

      与 milestones.py:63-88 同一句话：键 → 首次的状态码与响应体，命中就原样回。
      """
      if not key:
          return await run()
      storage_key = f"{action}:{scope}:{key}"
      if not await idem.claim(db, storage_key, action=action, scope_id=scope):
          prior = await idem.stored_result(db, storage_key)
          if prior is None:
              raise ConflictError("This Idempotency-Key is still in flight")
          return prior
      result = await run()
      await idem.record_result(db, storage_key, result)
      return result
  ```

  ```python
  # tasks.py —— create_task 的用法。
  async def create_task(
      payload: CreateTaskRequest,
      idempotency_key: IdempotencyKey = None,
      db=Depends(get_db),
      auth_user: AuthUserInfo = Depends(require_auth_user),
  ) -> dict:
      async def run() -> dict:
          task = await _create_task_entity(
              payload=payload, db=db, creator_user_id=auth_user.user_id
          )
          ...
          await db.commit()
          return {...}

      return await replay_or_run(
          db=db,
          key=idempotency_key,
          action="task.create",
          scope=str(auth_user.user_id),
          run=run,
      )
  ```
- **为什么**：Stripe 的公开契约是「客户端生成 UUID v4 放进 `Idempotency-Key`，服务端记住键 → 首次的状态码与响应体 24 小时，同键重放原结果，参数不一致则报错，GET/DELETE 不收」：https://docs.stripe.com/api/idempotent_requests 。本平台今天的幂等只覆盖了「从房间里发起」的那批（靠 continuation），人点按钮的那批（发题、钉里程碑、建 routine、问 AI）全部裸奔 —— 而人点按钮恰恰是网络抖动与双击的高发区。

### M2. 写路由一律「响应前显式提交」

- **范围**（全部依赖 `get_db` teardown 提交的写路由）：
  - `tasks.py`：`upload_task_attachment`（:1451）、`download_task_attachment`（:1483，`download_count` 自增）、`remove_task_attachment`（:1520）、`create_task_participant`（:1782）、`join_task_as_user`（:1886）、`join_task_as_team`（:1950）、`patch_task_participant`（:2019）、`delete_task`（:2722）、`delete_task_participant`（:2760）、`delete_task_participant_by_member`（:2794）、`patch_task_membership_by_member`（:2829）、`resubmit_task`（:2897）、`post_task_submission`（:3205）、`patch_task_submission`（:3266）、评审五条（:3354、:3447、:3484、:3521）、`create_ai_advice_conversation`（:3722）、`delete_ai_advice_conversation`（:3770）
  - `milestones.py`：`create_milestone` 的人这条路（:89-92 只在 `source_topic_id is not None` 时提交）、`update_milestone`（:117）、`delete_milestone`（:140）
- **做法**：写入之后、`return` 之前加 `await db.commit()`。理由本仓已经写了三遍（`tasks.py:1334-1339`、`:2501-2505`、`:1718-1722`），照抄那句话即可：

  ```python
  # 写到这里就完了 —— 内容都已落库，下面全是读。
  # 先提交再构造响应：``get_db`` 的提交在 ``yield`` 的退出码里，而那段跑在响应
  # 发出**之后**（FastAPI 0.137 的 ``request_stack`` 在 ``await response(...)``
  # 之后才关），不在这里提交，客户端拿到响应时这次写入还没落地，紧接着来读它的
  # 请求就看不到（同 ``spaces.create_space``，合并队列 run 36296605673 实测）。
  await db.commit()
  ```
- **为什么**：这不是「风格不一致」而是可复现的时序 bug —— 本仓自己的注释给出了实测编号与修复先例（`create_task`、`patch_task`、`confirm_publish_task_from_pdf` 三处专门为此加了提交）。现状是同一个文件里三种做法并存：有的显式提交、有的靠 teardown、`milestones.create_milestone` 甚至只在一条分支上提交。修法机械、风险为零（一次写入的请求，提前提交不改可见语义），适合一次清完。
- **测试**：`backend/tests/integration/test_a_create_commits_before_its_response_is_sent.py` 这个文件已经是这条规则的守卫，按同一写法给上面每条补一个用例；关键断言是「响应之后立刻发起的第二个请求能读到本次写入」。

### M3. 给报名 / 提交 / 评审三张表补唯一约束，把「先查后插」交给数据库

- **范围**：`task_membership`（`domain/task/models.py:138`）、`task_submission`（`:220`）、`task_submission_review`（`:278`）；对应写入点 `POST /tasks/{taskId}/participants`、`/participations/user`、`/participations/team`、`POST .../submissions`、`POST .../review`。
- **做法**：加三个部分唯一索引（软删的行不参与，与仓库里 `user_account`（`alembic/versions/872d78113ce9`）、`push_subscription`（`d3f1a7c52e08`）等既有迁移同一手法），然后让 handler 把 `IntegrityError` 翻成 409：

  ```python
  # backend/alembic/versions/xxxx_task_uniqueness.py
  def upgrade() -> None:
      # 同一个人/队伍在一道题上只能有一行活着的报名（软删的旧行不挡重新报名）。
      op.create_index(
          "uq_task_membership_live",
          "task_membership",
          ["task_id", "member_id"],
          unique=True,
          postgresql_where=sa.text("deleted_at IS NULL"),
      )
      # 一份报名下的版本号唯一 —— PATCH .../submissions/{version} 按它寻址。
      op.create_index(
          "uq_task_submission_version",
          "task_submission",
          ["membership_id", "version"],
          unique=True,
          postgresql_where=sa.text("deleted_at IS NULL"),
      )
      # 一份提交只有一条活着的评审（今天靠 Python 先查一遍）。
      op.create_index(
          "uq_task_submission_review_live",
          "task_submission_review",
          ["submission_id"],
          unique=True,
          postgresql_where=sa.text("deleted_at IS NULL"),
      )
      # 上面三条约束的 WHERE 子句要能走索引，顺带把等值查询的索引补上。
      op.create_index("ix_task_membership_task_id", "task_membership", ["task_id"])
      op.create_index("ix_task_submission_membership", "task_submission", ["membership_id"])
  ```

  ```python
  # domain/task/services.py:224-233（create_membership）—— 把「先查」留给友好报错，
  # 真正的裁决交给约束；两个并发请求里必然有一个拿到 IntegrityError。
  from sqlalchemy.exc import IntegrityError

  existing = await self.get_membership_by_task_and_member(
      task_id=task.id, member_id=member_id
  )
  if existing is not None and existing.deleted_at is None and existing.approved != 1:
      raise BadRequestError("Member already participating in this task.")
  ...
  try:
      return await self._repo.save(membership)
  except IntegrityError as exc:          # 与上面那句话同一个答案，只是这回是并发
      raise BadRequestError("Member already participating in this task.") from exc
  ```
- **为什么**：`get_by_task_and_member`（`domain/task/repositories.py:589`）用的是 `scalar_one_or_none` —— 它**假设**这个 pair 最多一行，而数据库今天不保证这一点（表上没有任何唯一约束）。所以一旦出现重复行，不只是「多了条脏数据」：这个 pair 的**所有后续读都会抛 `MultipleResultsFound`**（`upload`、评审、报名列表全挂）。同样的「先查后插」在 `create_review`（`services.py:1045` 裸 INSERT）与 `submit_task`（`services.py:755-764`）各有一份，其中评审那份还连着**重复发奖**（`_maybe_award_rank` 两次都看到 `previous_accepted is None`）。把一个唯一索引加上，这三处就从「靠时序」变成「靠约束」。风险：加索引前要先清历史重复行（迁移里可以先 `DELETE`/软删重复项，或先跑一遍检测查询把冲突 id 打出来人工确认）。
- **测试**：`backend/tests/integration/test_task_uniqueness.py::test_duplicate_participation_is_rejected_by_the_database`（在测试里直接插第二行断言 `IntegrityError`）、`::test_double_click_on_apply_yields_one_membership`（并发两个 `POST /participations/user`，断言恰好一行、另一个得到 400/409）；评审那条同理 `::test_concurrent_reviews_award_the_rank_once`。仓库里已有 `test_account_unique_indexes_migration.py` 这个先例可照。

### M4. DELETE 的口径统一到 204

- **范围**：`DELETE /milestones/{milestone_id}`（`milestones.py:140`，今天 200 + `{"data": null}`）、`DELETE /tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review`（`tasks.py:3517`，今天 200 + 一个「删除后的空 review」）、`DELETE /tasks/{taskId}/ai-advice/conversations/{conversationId}`（`tasks.py:3766`，今天 200 + `data: null`）。
- **做法**：`tasks.py` 里已经有现成的样板（`@router.delete(..., status_code=status.HTTP_204_NO_CONTENT)` + `-> None`，见 `tasks.py:2717-2722`、`:2755-2760`、`:2789-2794`），把这四个对齐过去；确实需要回体的（评审那条想告诉调用者「删了什么」）就保持 200 并明确 `data` 的含义 —— **不要**让同一个动作在一个平台上有两种成功码。
- **为什么**：GitHub 的 HTTP 状态码约定一节把「删除成功」与「创建成功（201 + Location）」分开写明，其余一律 200：https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api#http-response-codes 。同一组接口里三种删除口径（204 / 200+null / 200+空对象）会让客户端只能一个接口写一条分支。风险：前端若按 `resp.data.data` 判成功，改成 204 后会读不到体 —— 要一起改（本仓 `frontend/src` 里对这些删除调用的判读方式需要确认）。

### M5. 分页：统一参数边界 + `hasMore/nextStart`，别再多一种 `data.data`

- **范围**：本组所有列表接口 —— `GET /tasks`（已经是 `pageStart/pageSize/…/total`，`tasks.py:2694-2703`，最好的一套）、`GET /tasks/{taskId}/participants`（无分页）、`.../submissions`（有 `page`，`tasks.py:3186-3192`）、`GET /projects/{project_id}/milestones`（无）、`GET /projects/{project_id}/calendar`（无）、`GET /projects/{project_id}/routines`（无，且是 `data.data`）、`GET /routines/{routine_id}/runs`（无）、`GET /tasks/{taskId}/ai-advice`（无）、`.../conversations/grouped`（无）。
- **做法**：
  1. 参数一律 `pageStart: int = Query(default=0, ge=0)` / `pageSize: int = Query(default=20, ge=1, le=100)`（`tasks.py:2536-2537` 就是这个形状，直接推广）；响应里回 `page: {pageStart, pageSize, hasMore, nextStart, total}`，`total` 统一为「满足筛选条件的总条数」。
  2. 消掉 `data.data`：`ok(page(items, total))` 那几条（`milestones.py:103`、`:114`、`routines.py:163`、`:282`）改成 `ok({"milestones": items, **page(...)})` 这类具名键 —— 与 `tasks.py` 的 `data.tasks` 对齐。
  3. 上限一律静默夹到 100，不报错（GitHub 的 `per_page` 行为）。
- **为什么**：GitHub 的约定是 `per_page`（默认 30、**上限 100，超限静默夹住**）+ `link` 头里的 `rel="next"`，并明确「`page` 偏移只适合小数据集、大数据集用游标」：https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api 。Stripe 同形的做法是 `limit`（上限 100）+ `starting_after` + `has_more`：https://docs.stripe.com/api/pagination 。本平台的 `pageStart/pageSize/hasMore/nextStart/total` 已经是 Stripe 形状，**缺的不是协议，而是覆盖面与语义统一**：现在同一组里有三种列表（有分页的、`data.data` 的、压根没有的），以及两种 `total` 含义（`/milestones` 是全量 COUNT、`/calendar` 是 `len(items)`）。风险：`data.data` → 具名键是**破坏性**的，要带前端一起发；建议顺序是先补分页参数（加法），再一轮改形状（破坏性，集中发）。

### M6. AI 建议的 SSE 帧：两端定一套（并给出「不是 SSE」的兜底）

- **范围**：`GET /tasks/{taskId}/ai-advice/conversations/stream`（`tasks.py:3791`）与它的唯一客户端 `frontend/src/network/api/tasks/index.ts:432-613`；以及 `POST .../conversations`（`tasks.py:3722`，非流式的那条）。
- **做法**：二选一，别两套并存：
  1. **后端改成前端认的那套**（改动最小、前端不用动）：帧体用 `[PARTIAL]/[RESPONSE]/[DONE]/[ERROR]` 前缀文本，`[CONVERSATION_ID]`、`[TITLE]` 这些元信息帧也照前端已解析的名字发（前端已经把解析器写全了，`index.ts:474-590`）。同时把 `except Exception` 的兜底改成只发固定的 `[ERROR] internal` + `X-Request-Id`，不回显异常原文（`tasks.py:3892-3896`）。
  2. **前端改成后端这套**：解析 JSON `{"type": "content"|"done"|"error", ...}` —— 但 `type: "done"` 的 payload 只有 `tokens`，前端要的 `conversationId`/`messageId`/`title`/`references` 后端今天一概没发（`tasks.py:3846-3856`），所以这条路要后端先补帧。
- **为什么**：SSE 的公开约定是「`data:` 行 + 事件名 + 客户端 `EventSource` 只支持 GET」（MDN：https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events ）；帧的**内容**各家自己定（OpenAI 用 `data: {"choices":[...]}` + `data: [DONE]`，Stripe/Anthropic 同理），所以「定哪一套」是产品决定，但**两端必须是同一套** —— 今天不是，而这条链路连路由都到不了（见 #59）。风险：这条链路今天就不可用，所以改动没有「破坏在用客户端」的风险。

### M7. 抽公共依赖，别让每条路由自己装配

- **范围**：本组四个文件里重复出现的装配：
  - `tasks.py` 里 `{"code": 200, "message": "OK", "data": ...}` 手写了 30 多次，而 `api/response.py` 的 `ok()` / `page()` 就在旁边（`milestones.py`、`routines.py` 都在用）。统一用 `ok()` 能顺手把 `warnings` 这类新增字段一次带给所有接口。
  - `_enrich_task_models` / `_enrich_task_topics` / `_enrich_task_user_state` 三个富化函数都要「批量取名册」这一套（`tasks.py:549-551`、`2980-2987`、`730-749`），而 `get_task_participants`（`:2973-2996`）与 `patch_task_membership_by_member`（`:2879-2882`）又各写了一份「membership → 人」的装配。抽一个 `_load_people(db, user_ids, team_ids)` 返回 `(user_map, profile_map, team_map)` 给这五处共用。
  - `routines.py` 里 `TopicService(db)` 在同一请求里造两次（`:170`、`:172`），`_routine_actor`（`:125`）与 `_speaker`（`:133`）都在重复「取 room → 取 handle」。
  - `git_http.py` 的 `_task_for`（`:19`）与 `task_workspace`（`:137`）对 token 的两次校验应当收进一个 `ScopedTask` 依赖（见 #4 的 `_task_workspace_payload`）。
- **为什么**：这几处重复不是风格问题 —— `tasks.py:719-722` 与 `:2197-2199` 的注释记录了两次因为「同一份装配写了两遍、只修了一处」而出的前端 bug（`joinedTeams[0].name` 是 undefined、退出对话框显示 `"undefined"`），`PATCH participants` 的 `{id}` 名单（#39）是第三次。抽成一份装配是这三类 bug 的根治手段。风险：纯重构，按文件分批做、每批跑既有测试。

---

**报告口径说明**：文中「可优化(低)」指问题真实但代价小或修法机械；行号与代码片段均取自本次只读阅读的源码（未运行、未修改仓库、未提交）；引用 GitHub 惯例处给了对应文档 URL，未逐条查证的（如 PUT/PATCH 的方法语义、SSE 帧内容各家自定）已注明「业界通行」。
