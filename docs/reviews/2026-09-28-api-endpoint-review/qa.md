# 平台 API 逐接口优化调研 — 组 `qa`（questions / answers / comments / tags）

清单里 51 条 `path` 与源码装饰器逐一核对一致（含 handler 行号），**无差异**；下面所有 `文件:行号` 均以 `backend/` 为根、指 handler 的 `async def` 那一行。

- 仓库根：`/home/cheese/.cheese/home/de808b13-ffd2-4b8a-9d1d-fba7babe389f/2d3b6d1f-5b7e-4e4e-b025-52bf151b88e7/.cheese/tasks/3ffa1303-11fd-4715-9484-cbe297f263d3`
- 判定依据：GitHub REST 公开惯例优先（URL 见第三节），其次是本仓库既有约定（`docs/api-conventions.md`、`page` 对象 camelCase 回归测试）。
- 贯穿全文的两条前提（都是查证过的事实，不是推测）：
  1. 所有分页路由**请求侧**只接受 snake_case：`page_start` / `page_size`（`Query(alias="page_start")` 或同名参数），而**响应侧** `page` 对象一律 camelCase（`Page` 类型 + `backend/tests/integration/test_page_camel_case_keys.py` 钉死）。
  2. 手写 API 层 `frontend/src/network/api/**` 发的正是 camelCase（`pageStart` / `pageSize`），另一套 `frontend/src/api.ts`（OpenAPI 生成）发 snake_case。两者并存，互不转换。

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | GET | `/questions/{question_id}/answers` | 可优化 | 【高】请求侧要 `page_start`、前端发 `pageStart` → 翻页永远回到第一页；且每页先用「全量 id 列表」再每答 6 条统计查询（20 条约 124 次往返） |
| 2 | POST | `/questions/{question_id}/answers` | 可优化 | 【低】「已答过」先查后插有竞态且答 400（GitHub 对重复资源用 409）；无 DB 唯一约束兜底 |
| 3 | POST | `/questions/{question_id}/answers/{answer_id}/vote` | 可优化 | 【低】空 body 默认 `"UPVOTE"` 不是合法值 → 400；`ensure_answer_in_question` 与 `vote_answer` 各查一次同一行 |
| 4 | DELETE | `/questions/{question_id}/answers/{answer_id}/vote` | 可优化 | 【低】同上重复取数；未投票也答 200（幂等没问题，但和 3 的语义不对称） |
| 5 | GET | `/questions/{question_id}/answers/{answer_id}/vote` | 可优化 | 【低】同一行查两遍；返回值与 `GET /answers/{id}` 里的 `attitudes` 重复 |
| 6 | GET | `/questions/{question_id}/answers/{answer_id}/comments` | 可优化 | 【高】`include_subs=True` + 每条评论单独查 reaction/子回复/子计数，一页 20 条约 300 次往返 |
| 7 | POST | `/questions/{question_id}/answers/{answer_id}/comments` | 可优化 | 【中】`mentionedUserIds` 元素非 int（或传字符串）在 `services.py:56` 抛 `TypeError` → 500，不是 400 |
| 8 | DELETE | `/questions/{question_id}/answers/{answer_id}/comments/{comment_id}` | 可优化 | 【低】为判作者拉整份 DTO（含子回复、reaction）；`discussion["sender"]` 为 None 时 `["id"]` → 500 |
| 9 | GET | `/questions/{question_id}/answers/{answer_id}` | 可优化 | 【中】读接口里同步写 `answer_query_log`（不可缓存/无 ETag），加上 DTO 富化共约 8 次往返 |
| 10 | PUT | `/questions/{question_id}/answers/{answer_id}` | 可优化 | 【中】用 `service.get_answer`（≈8 次往返）做父级校验，`ensure_answer_in_question`（1 次）就够，之后 `update_answer` 又取一遍 |
| 11 | DELETE | `/questions/{question_id}/answers/{answer_id}` | 可优化 | 【中】同上；函数体内临时 import `NotFoundError`（`answers.py:328`，另一处 `answers.py:270`） |
| 12 | PUT | `/questions/{question_id}/answers/{answer_id}/favorite` | 可优化 | 【低】一次收藏发 4 条 SQL（ensure + ensure + 查已收藏 + 计数），最小 1–2 条可完成 |
| 13 | DELETE | `/questions/{question_id}/answers/{answer_id}/favorite` | 可优化 | 【低】同上 |
| 14 | POST | `/questions/{question_id}/answers/{answer_id}/attitudes` | 可优化 | 【低】与 #3/#4 是同一动作的两套入口；`user_attitude` 直接回显请求值（非法值也照回） |
| 15 | POST | `/comments/{commentId}/attitudes` | 可优化 | 【低】重复 ensure 两次；成功文案是整句「You have expressed…」，其他端点一律 `"OK"` |
| 16 | GET | `/comments/{commentId}` | 可优化 | 【中】路由里又取一遍 user + profile 并 `build_user_dto`（含 4 项计数），覆盖 service 已给的作者 DTO；形状与 `GET /comments/{type}/{id}` 不一致 |
| 17 | PATCH | `/comments/{commentId}` | 暂无 | 校验与归属都在 service，形状与列表一致，未发现可优化点 |
| 18 | DELETE | `/comments/{commentId}` | 暂无 | 归属校验正确；仅响应体形状（`data: null`）与全仓不一致，已并入第三节 |
| 19 | GET | `/comments/{commentableType}/{commentableId}` | 可优化 | 【中】任意 `commentableType`（如 `FOO`）不校验、静默返回空页；无 `total`；子回复 N+1 同 #6 |
| 20 | POST | `/comments/{commentableType}/{commentableId}` | 可优化 | 【高】只校验 `QUESTION` 存在，`FOO`/`MATERIAL_BUNDLE` 一路写进 PG enum `CommentCommentabletypeEnum`（只有 3 个值）→ 500；路由里直接 `QuestionRepository(session=db)` 破坏分层 |
| 21 | GET | `/questions/trending` | 可优化 | 【高】恒为空数组：唯一写 `question_query_log` 的 `log_query` 全仓只有单测调用 |
| 22 | GET | `/questions/stats` | 可优化 | 【中】`totalViews` 恒为 0（同上）；3 条 count 可并成 1 条 |
| 23 | GET | `/questions/search-terms` | 可优化 | 【高】恒为空数组：`log_search` 无生产调用方 |
| 24 | GET | `/questions` | 可优化 | 【高】`pageStart` 不匹配同 #1；Meilisearch 分支每条命中再 `get_by_id`（最多 100 次串行）；不落搜索日志 |
| 25 | POST | `/questions` | 可优化 | 【中】`bounty` 无上限（0–20 规则只在 PUT bounty 里）、`type` 无取值域；201 只回 `{"id"}` 而 PUT 回整份 DTO |
| 26 | GET | `/questions/followed` | 可优化 | 【低】`pageStart` 参数名同 #1（这条 handler 里连 Python 形参都写成了 `pageStart`，`questions.py:181`） |
| 27 | GET | `/questions/{question_id}` | 可优化 | 【高】单次请求约 13 条串行查询；`view_count` 恒为 0；无 ETag/条件请求 |
| 28 | POST | `/questions/{question_id}/followers` | 可优化 | 【中】已关注答 400（应为 409 或幂等 200）；`service._repo` 私有属性穿透；无唯一约束，并发可落重复行 |
| 29 | DELETE | `/questions/{question_id}/followers` | 可优化 | 【低】未关注答 400；GitHub 的 DELETE 语义是 204 + 幂等 |
| 30 | PUT | `/questions/{question_id}/acceptance` | 可优化 | 【中】采纳对象放在 query（`?answer_id=`）而删除走路径；答案不属于该题时答 400（应为 404，同族其他地方都答 404） |
| 31 | DELETE | `/questions/{question_id}/accept` | 可优化 | 【低】同一资源两个名词（`acceptance` / `accept`）；权限路径与 #30 不一致（多一次角色解析，结果相同） |
| 32 | POST | `/questions/{question_id}/vote` | 可优化 | 【低】空 body 默认 `"UPVOTE"` 非法 → 400；与 `/attitudes` 重复；全仓无调用方 |
| 33 | DELETE | `/questions/{question_id}/vote` | 可优化 | 【低】同上（`/attitudes` + `UNDEFINED` 已覆盖） |
| 34 | GET | `/questions/{question_id}/vote` | 暂无 | 只读、单条查询、有鉴权，未发现可优化点 |
| 35 | GET | `/questions/{question_id}/comments` | 可优化 | 【中】问题不存在也答 200 空页（同族 answers 那条答 404）；子回复 N+1 同 #6 |
| 36 | POST | `/questions/{question_id}/comments` | 可优化 | 【中】完全不校验父级问题存在 → 可产出指向不存在题目的孤儿评论；`mentionedUserIds` 未校验同 #7 |
| 37 | DELETE | `/questions/{question_id}/comments/{comment_id}` | 可优化 | 【低】同 #8（整份 DTO 换作者 + `sender` 可能为 None → 500） |
| 38 | PUT | `/questions/{question_id}` | 可优化 | 【中】`type` 不做 `int()` 直接写 Integer 列 → 非法值 500（POST 同字段有保护）；响应 `createdAt` 与 `GET /questions/{id}` 的 `created_at` 不一致 |
| 39 | DELETE | `/questions/{question_id}` | 可优化 | 【低】204 空体，而同族删除（评论、邀请）答 200 + JSON，前端要分支处理 |
| 40 | GET | `/questions/{question_id}/followers` | 可优化 | 【中】本文件唯一**没有**鉴权的端点（见第三节）；只回 `[{"id":…}]`，调用方拿不到昵称头像 |
| 41 | PUT | `/questions/{question_id}/followers` | 可优化 | 【低】与 #28 同一动作两套入口、两种结果（幂等 200 vs 400）；同样 `service._repo` 穿透 |
| 42 | PUT | `/questions/{question_id}/bounty` | 可优化 | 【中】`int(payload.get("bounty", 0))` 无保护（`questions.py:500`）→ 非数字 500；锁定业务规则使赏金只能升不能降、无法撤回 |
| 43 | POST | `/questions/{question_id}/attitudes` | 可优化 | 【低】与 #32/#33 重复；`user_attitude` 回显原样输入 |
| 44 | GET | `/questions/{question_id}/invitations` | 可优化 | 【低】`pageStart` 同 #1；读侧口径为「任何登录用户」是已文档化的产品决定（`questions.py:538-557`），不改 |
| 45 | POST | `/questions/{question_id}/invitations` | 可优化 | 【低】重复邀请答 400（应为 409 `ConflictError`）；无频率限制（任何登录用户都能批量邀请） |
| 46 | GET | `/questions/{question_id}/invitations/recommendations` | 可优化 | 【中】「推荐」实际是 `list_profiles(limit).order_by(user_id.asc())` 的前 N 个用户（`user/repositories.py:416-425`），与题目无关、含提问者本人和已邀请者 |
| 47 | GET | `/questions/{question_id}/invitations/{invitation_id}` | 暂无 | 绑定父级、鉴权、形状都正确，未发现可优化点 |
| 48 | DELETE | `/questions/{question_id}/invitations/{invitation_id}` | 暂无 | 父级绑定 + 鉴权正确；不存在答 400 已被现有用例钉住，改 404 需另行决策（见第三节） |
| 49 | GET | `/tags` | 可优化 | 【中】不传 `q` 时直接返回空页（列表接口列不出东西）；`page_start<0` 的 404 分支不可达（`Query(ge=0)` 已先答 422）；需要登录才能列标签 |
| 50 | GET | `/tags/{tag_id}` | 暂无 | 单条查询 + 404 正确；响应键 `topic` 与路径 `/tags` 的错位是已文档化的产品口径（`routes/tags.py:1-20`） |
| 51 | POST | `/tags` | 可优化 | 【低】`name` 不 trim（`" foo "` 与 `"foo"` 并存）；`tag.name` 无唯一索引（迁移里只有 PK），并发创建同一名字可落两行 |

## 二、详细分析（只写有发现的，按收益从高到低）

### 1. 全组：分页请求参数名不一致（18 条分页端点）

- **现状**：请求侧一律 snake_case。`answers.py:58-59`、`answers.py:163-164`、`comments.py:142-143`、`tags.py:38-39`、`questions.py:114-115`、`questions.py:181-182`、`questions.py:329-330`、`questions.py:463-464`、`questions.py:566-567` 全部是 `page_start`/`page_size`（或 `alias="page_start"`）；响应侧 `page` 对象是 camelCase（`answers/services.py:80-88`、`tags/services.py:56-60`、`discussion/services.py:146-152`、`questions/services.py:97`，并由 `backend/tests/integration/test_page_camel_case_keys.py` 钉住）。
- **问题**：手写前端层发 camelCase，FastAPI 忽略未知 query 参数，于是 `pageStart` 被丢掉、永远按「从头开始」处理：
  - `frontend/src/network/api/answers/index.ts:17-18` 发 `pageStart`，消费方 `frontend/src/components/questions/AnswerList.vue:39` 用它翻页；后端 `answers/repositories.py:35-51` 收到 `cursor_id=None` 后恒返回第一批，而 `page.nextStart`（`answers/services.py:88`）每次都是同一个 id → `usePaging`（`frontend/src/utils/paging.ts:44-60`）把同一页反复追加，列表无限重复。
  - `frontend/src/network/api/questions/index.ts:29` 发 `pageStart`，`frontend/src/views/searches/Index.vue:48-52` 的搜索页同样中招。
  - `frontend/src/network/api/questions/index.ts:87` 发 `pageStart`，`frontend/src/components/questions/InvitationList.vue:99` 同样中招。
  - 现有测试发的是 snake_case（`test_page_camel_case_keys.py:73` 的 `params={"page_size": 1}`），所以这个不一致在测试里露不出来。
- **优化**：把分页参数收进一个共享依赖，请求侧同时认两种拼法（响应侧不动，仍是 camelCase）。新增 `backend/app/api/deps/paging.py`：

```python
"""分页查询参数的唯一入口。

请求侧同时接受 pageStart/page_start（响应侧 page 对象一直是 camelCase），
避免同一个字段两种拼法各答一半。
"""
from dataclasses import dataclass

from fastapi import Request

from app.core.errors import BadRequestError


@dataclass(frozen=True)
class PageQuery:
    start: int | None
    size: int


def page_query(*, default_size: int = 20, max_size: int = 100):
    """返回依赖：PageQuery。默认页大小按路由传（tags 是 50，其余 20）。"""

    def _dep(request: Request) -> PageQuery:
        qp = request.query_params
        raw_start = qp.get("pageStart") or qp.get("page_start")
        raw_size = qp.get("pageSize") or qp.get("page_size")
        try:
            start = int(raw_start) if raw_start not in (None, "") else None
            size = int(raw_size) if raw_size not in (None, "") else default_size
        except (TypeError, ValueError):
            raise BadRequestError(
                "pageStart/pageSize must be integers",
                data={"pageStart": raw_start, "pageSize": raw_size},
            ) from None
        if start is not None and start < 0:
            raise BadRequestError("pageStart must be >= 0")
        if size < 1 or size > max_size:
            raise BadRequestError(f"pageSize must be between 1 and {max_size}")
        return PageQuery(start=start, size=size)

    return _dep


page_20 = page_query(default_size=20, max_size=100)
page_50 = page_query(default_size=50, max_size=100)
```

  以 #1 为例改路由（其余 17 条同形状）：

```python
# backend/app/api/routes/answers.py
from app.api.deps.paging import PageQuery, page_20   # 改动点：新增 import


@router.get("/{question_id}/answers", summary="List Answers")
async def list_answers(
    question_id: Annotated[int, Path(ge=0)],
    page: PageQuery = Depends(page_20),              # 改动点：取代 page_start/page_size
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: AnswersService = Depends(get_answers_service),
) -> dict:
    items, page_info = await service.list_answers(
        question_id=question_id,
        page_start=page.start,                       # 改动点
        page_size=page.size,                         # 改动点
        viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
    )
    return {"code": 200, "message": "OK", "data": {"answers": items, "page": page_info}}
```

  （备选写法，FastAPI ≥ 0.100 / pydantic v2 可用一行替代：`Annotated[int | None, Query(validation_alias=AliasChoices("page_start", "pageStart"), ge=0)]`；上面的依赖写法不依赖版本。）
- **契约**：请求侧**放宽**（旧 snake_case 仍生效），响应不变，无数据迁移。风险：`page_size` 越界现在答 400 而不是 FastAPI 的 422，若前端依赖 422 需同步；建议保留 422 口径的话把 `max_size` 校验留在路由的 `Query(le=...)` 上。
- **测试**：`backend/tests/integration/test_page_camel_case_keys.py` 增加 `TestAnswersPageCamelCaseRequest.test_answers_page_accepts_camel_case_params`：造 3 个答案，`params={"pageSize": 2}` 断言 `len(data["answers"]) == 2` 且 `page["nextStart"]` 等于第二个答案 id；再请求 `params={"pageSize": 2, "pageStart": next_start}` 断言拿到第 3 个且不重复。同样给 `/questions`（`q=` + `pageStart`）和 `/questions/{id}/invitations` 各补一条。

### 2. 埋点没有写入方：`/questions/trending`、`/questions/stats`、`/questions/search-terms`、`GET /questions/{id}` 的 `view_count`

- **现状**：`GET /questions/trending`（`questions.py:75`）走 `QuestionsService.get_trending` → `repositories.py:349-373`；`/questions/stats`（`questions.py:88`）→ `repositories.py:375-398`；`/questions/search-terms`（`questions.py:99`）→ `repositories.py:400-417`；`GET /questions/{id}` 的 `view_count` 来自 `count_views`（`repositories.py:260`）。
- **问题**：这三条统计读的 `question_query_log` / `question_search_log` 在**生产代码里没有任何写入方**。全仓 grep `log_query|log_search|QuestionQueryLog|QuestionSearchLog`，命中的只有：模型定义（`questions/models.py:65,79`）、仓库方法本身（`repositories.py:242-258`、`323-347`）、以及 `backend/tests/unit/test_questions_repository.py:396,500` 两处单测。`get_question`（`questions/services.py:190-276`）只读数不写日志。结论：`trending` 恒 `[]`（与空子查询 join），`search-terms` 恒 `[]`，`stats.totalViews` 恒 `0`，题目详情的 `view_count` 恒 `0`。对照答案侧是通的：`answers.py:273-279` 在 `get_answer` 里调 `service._repo.log_view`，`answer_query_log` 有 `ix_answer_query_log_answer_id`（`alembic/versions/5a3b7c9d1e2f_add_answer_query_log.py:38`）。
- **优化**：把题目读侧埋点接上，并且**不要**在 GET 里同步写（见第 7 条，建议先同步写保证正确、后续换后台任务）。改动点三处：

```python
# 1) backend/app/domain/questions/services.py —— 新增转发（形状与 answers 的 log_view 相同）
    async def log_view(
        self,
        *,
        question_id: int,
        viewer_id: int | None,
        ip: str,
        user_agent: str | None,
    ) -> None:
        await self._repo.log_query(
            question_id=question_id,
            viewer_id=viewer_id,
            ip=ip,
            user_agent=user_agent,
        )

# 2) backend/app/api/routes/questions.py:198 get_question —— 改动点：读侧埋点
@router.get("/{question_id}", summary="Get Question")
async def get_question(
    question_id: Annotated[int, Path(ge=0)],
    request: Request,                                            # 改动点
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    question = await service.get_question(question_id, user_id)
    await service.log_view(                                     # 改动点
        question_id=question_id,
        viewer_id=user_id,
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent"),
    )
    return {"code": 200, "message": "OK", "data": {"question": question}}

# 3) 同一文件 search_questions（questions.py:112）—— 改动点：落搜索日志并统计耗时
async def search_questions(
    q: str | None = Query(default=None),
    page: PageQuery = Depends(page_20),
    request: Request = None,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    started = time.perf_counter()
    items, page_info = await service.search_questions(
        keyword=q, page_size=page.size, page_start=page.start,
        sort_by="createdAt", sort_order="desc",
    )
    await service.log_search(                                    # 改动点
        keywords=q or "",
        first_question_id=items[0]["id"] if items else None,
        page_size=page.size,
        result_count=len(items),
        duration_ms=(time.perf_counter() - started) * 1000,
        searcher_id=auth_user.user_id if auth_user.user_id > 0 else None,
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent"),
    )
    return {"code": 200, "message": "OK", "data": {"questions": items, "page": page_info}}
```

  同时补索引，否则写入一生效，`trending`/`search-terms` 的 GROUP BY 就是全表扫（两个表在迁移里只有 PK，`alembic/versions/a95752502bb0_initial_schema.py:489-512`）：

```python
# 新迁移：backend/alembic/versions/<rev>_indexes_for_question_read_logs.py
def upgrade() -> None:
    op.create_index(
        "ix_question_query_log_created_at_question_id",
        "question_query_log",
        ["created_at", "question_id"],          # get_trending 的 cutoff + group by
    )
    op.create_index(
        "ix_question_search_log_created_at", "question_search_log", ["created_at"]
    )
    op.create_index(
        "ix_question_search_log_keywords", "question_search_log", ["keywords"]
    )
```

- **契约**：请求/响应形状不变；`stats.totalViews`、`view_count`、`trending`、`search-terms` 从「恒 0/恒空」变成有真值——**这是行为变化**，前端展示逻辑需确认能接受非 0。新增写路径意味着 `GET /questions/{id}` 变成非幂等（缓存/重放要留意）。
- **测试**：`backend/tests/integration/test_questions.py` 增 `test_viewing_a_question_is_counted`（GET 详情两次 → `stats["totalViews"] >= 2`）、`test_trending_shows_viewed_question`（看一次 → `trending` 首条是它）、`test_search_terms_are_recorded`（带 `q` 搜一次 → `search-terms` 含该关键词）。仓库层单测沿用 `backend/tests/unit/test_questions_repository.py` 现有 `test_log_query`/`test_log_search` 的写法补断言。

### 3. `GET /questions/{question_id}/answers`：全量 id 列表 + 每答 6 条统计（附 #9–#14 的重复取数）

- **现状**：`answers.py:56` → `AnswersService.list_answers`（`answers/services.py:38-93`）：先 `list_all_answer_ids_for_question` 取**该题全部答案 id**（`answers/repositories.py:53-63`，无 limit），再 `list_answers_for_question` 取本页行，再对每行 `_attach_answer_stats`（`answers/services.py:95-126`）。
- **问题**：`_attach_answer_stats` 每条答案 6 条查询——`count_votes`、`get_user_vote`、`count_favorites`、`is_favorited`、`count_comments`（查 `discussion`，无索引）、`count_views`；加 1 条全量 id、1 条本页行、1 条作者档案，`page_size=20` 时约 124 次往返，随答案数线性增长（`all_ids` 还会随题目热度无限增长）。另 `page` 里 `nextStart` 用 `all_ids[end_idx]`，也就是说「更多」的判定依赖那次全量扫描。
- **优化**：把 6 条查询换成 6 条**批量**查询（`WHERE answer_id IN (...)`），并去掉全量 id 列表——用「多取一条」判 `has_more`。改动点：`answers/repositories.py` 增批量方法 + `answers/services.py` 增批量富化。

```python
# backend/app/domain/answers/repositories.py —— 改动点：新增批量方法（列名取自
# Attitude.attitudable_id/attitudable_type/attitude、AnswerFavorite.answer_id、
# AnswerQueryLog.answer_id、Discussion.model_type/model_id）
    async def count_votes_bulk(
        self, answer_ids: Sequence[int]
    ) -> dict[int, dict[str, int]]:
        if not answer_ids:
            return {}
        stmt = (
            select(Attitude.attitudable_id, Attitude.attitude, func.count(Attitude.id))
            .where(
                Attitude.attitudable_type == "ANSWER",
                Attitude.attitudable_id.in_(answer_ids),
            )
            .group_by(Attitude.attitudable_id, Attitude.attitude)
        )
        rows = (await self._session.execute(stmt)).all()
        out = {aid: {"POSITIVE": 0, "NEGATIVE": 0} for aid in answer_ids}
        for aid, attitude, count in rows:
            out[aid][attitude] = count
        return out

    async def get_user_votes_bulk(
        self, answer_ids: Sequence[int], user_id: int
    ) -> dict[int, str]:
        if not answer_ids:
            return {}
        stmt = select(Attitude.attitudable_id, Attitude.attitude).where(
            Attitude.attitudable_type == "ANSWER",
            Attitude.attitudable_id.in_(answer_ids),
            Attitude.user_id == user_id,
        )
        return {aid: att for aid, att in (await self._session.execute(stmt)).all()}

    async def count_favorites_bulk(self, answer_ids: Sequence[int]) -> dict[int, int]:
        if not answer_ids:
            return {}
        stmt = (
            select(AnswerFavorite.answer_id, func.count(AnswerFavorite.id))
            .where(AnswerFavorite.answer_id.in_(answer_ids))
            .group_by(AnswerFavorite.answer_id)
        )
        return {aid: c for aid, c in (await self._session.execute(stmt)).all()}

    async def list_favorited_ids(
        self, user_id: int, answer_ids: Sequence[int]
    ) -> set[int]:
        if not answer_ids:
            return set()
        stmt = select(AnswerFavorite.answer_id).where(
            AnswerFavorite.user_id == user_id,
            AnswerFavorite.answer_id.in_(answer_ids),
        )
        return {aid for (aid,) in (await self._session.execute(stmt)).all()}

    async def count_comments_bulk(self, answer_ids: Sequence[int]) -> dict[int, int]:
        if not answer_ids:
            return {}
        stmt = (
            select(Discussion.model_id, func.count(Discussion.id))
            .where(
                Discussion.model_type == DiscussableModelType.ANSWER.value,
                Discussion.model_id.in_(answer_ids),
                Discussion.deleted_at.is_(None),
            )
            .group_by(Discussion.model_id)
        )
        return {aid: c for aid, c in (await self._session.execute(stmt)).all()}

    async def count_views_bulk(self, answer_ids: Sequence[int]) -> dict[int, int]:
        if not answer_ids:
            return {}
        stmt = (
            select(AnswerQueryLog.answer_id, func.count(AnswerQueryLog.id))
            .where(AnswerQueryLog.answer_id.in_(answer_ids))
            .group_by(AnswerQueryLog.answer_id)
        )
        return {aid: c for aid, c in (await self._session.execute(stmt)).all()}
```

```python
# backend/app/domain/answers/services.py —— 改动点：list_answers 用批量富化，去全量 id
    async def list_answers(self, *, question_id, page_start, page_size, viewer_id=None):
        await self._ensure_question_exists(question_id)
        rows = await self._repo.list_answers_for_question(
            question_id=question_id,
            limit=page_size + 1,                      # 改动点：多取一条判 has_more
            cursor_id=page_start,
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        profiles = await self._profile_repo.get_profiles_by_user_ids(
            list({row.created_by_id for row in rows})
        )
        items = [
            _answer_to_dto(row, author=_profile_to_dto(profiles.get(row.created_by_id)))
            for row in rows
        ]
        await self._attach_answer_stats_bulk(              # 改动点
            items, answer_ids=[row.id for row in rows], viewer_id=viewer_id
        )
        next_start = rows[-1].id + 1 if has_more and rows else None
        page = {
            "pageStart": items[0]["id"] if items else None,
            "pageSize": len(items),
            "hasPrev": page_start is not None,
            "prevStart": None,
            "hasMore": has_more,
            "nextStart": next_start,
        }
        return items, page

    async def _attach_answer_stats_bulk(
        self, dtos: list[dict], *, answer_ids: list[int], viewer_id: int | None
    ) -> None:
        votes = await self._repo.count_votes_bulk(answer_ids)
        favs = await self._repo.count_favorites_bulk(answer_ids)
        comments = await self._repo.count_comments_bulk(answer_ids)
        views = await self._repo.count_views_bulk(answer_ids)
        mine = (
            await self._repo.get_user_votes_bulk(answer_ids, viewer_id)
            if viewer_id
            else {}
        )
        my_favs = (
            await self._repo.list_favorited_ids(viewer_id, answer_ids)
            if viewer_id
            else set()
        )
        for dto in dtos:
            aid = dto["id"]
            counts = votes.get(aid, {"POSITIVE": 0, "NEGATIVE": 0})
            pos, neg = counts["POSITIVE"], counts["NEGATIVE"]
            dto["attitudes"] = {
                "positive_count": pos,
                "negative_count": neg,
                "difference": pos - neg,
                "user_attitude": mine.get(aid, "UNDEFINED"),
            }
            dto["favorite_count"] = favs.get(aid, 0)
            dto["is_favorite"] = aid in my_favs
            dto["comment_count"] = comments.get(aid, 0)
            dto["view_count"] = views.get(aid, 0)
            dto["is_group"] = False
```

  同一族的重复取数一并消掉（改动点）：`answers.py:293` `update_answer` / `answers.py:322` `delete_answer` 把 `await service.get_answer(...)`（≈8 次往返）换成 `await service.ensure_answer_in_question(answer_id=answer_id, question_id=question_id)`（`answers/services.py:159-181`，1 次）；`answers.py:102/123/142`（vote 三条）与 `answers.py:343/360`（favorite 两条）删掉重复的 `ensure_answer_in_question`，让 `vote_answer`/`remove_answer_vote`/`add_favorite` 内部那次 `_ensure_answer_exists` 兼任父级校验（把 `question_id` 传进去，或统一在 service 里先 `ensure_answer_in_question` 再算，两处只留一处）。
- **契约**：响应 `page.nextStart` 语义由「下一批首个 id」变为 `last_id + 1`（与 `tags/repositories.py` 的 `next_id = rows[-1].id + 1` 一致），`pageStart` 由 `all_ids[0]` 变为本页首个 id；`hasPrev/prevStart` 恒 `False/None`（当前实现也不被前端使用，`frontend/src/utils/paging.ts` 只读 `nextStart`/`hasMore`）。需要一次同步：`frontend/src/network/api/answers/types` 若声明了 `hasPrev` 保持不变即可。
- **测试**：`backend/tests/integration/test_answers.py` 增 `test_answers_list_paginates_without_duplicates`（造 3 条，`page_size=2`，第二页不含第一页的 id）；`backend/tests/unit/test_answers_service.py` 增 `test_attach_answer_stats_bulk_issues_six_queries`（用 `db_session` 的 statement 计数或 `sqlalchemy.event` 断言查询数 ≤ 6）。

### 4. 评论列表 N+1（`#6`、`#19`、`#35`、`#7`、`#36`、`#8`、`#37`）

- **现状**：三条列表路由（`answers.py:160`、`questions.py:327`、`comments.py:139`）都走 `DiscussionService.list_discussions`（`discussion/services.py:110-153`）→ `_build_discussion_dtos`（`discussion/services.py:217-242`）→ 每行 `_build_discussion_dto`（`discussion/services.py:244-315`）。
- **问题**：`_build_discussion_dtos` 只批量预取了 `user_map`（`:227-230`），而 `_build_discussion_dto` 对**每一行**又做：`get_reaction_summary`（`:264-268` → `ensure_default_reaction_types` + reaction 汇总 + 用户档案）和 `include_subs=True` 时的 `list_discussions(parent_id=entity.id, page_size=2)`（`:272-283`，内含 `find_all` + `count_children`，并再递归构造 2 条子 DTO、各自再查一遍 reaction）。三条路由都传 `include_subs=True, with_reactions=True`，一页 20 条 → 数百次往返，且随子回复数增长。`discussion` 表在迁移里没有 `(model_type, model_id)` 索引（`alembic/versions/a95752502bb0_initial_schema.py:190-201`），`discussion_reaction` 也没有 `discussion_id` 索引（`:210-222`）。
- **优化**：在 `_build_discussion_dtos` 里一次性预取「本页 reactions」「本页子回复计数」「本页子回复样例」，`_build_discussion_dto` 改为消费预取结果（新增可选入参，单条路径保持原样）。改动点：

```python
# backend/app/domain/discussion/services.py —— 改动点：批量预取，逐行不再发查询
    async def _build_discussion_dtos(
        self, rows, *, current_user_id, include_subs, with_reactions
    ) -> list[dict]:
        if not rows:
            return []
        ids = [row.id for row in rows]
        user_ids = {row.sender_id for row in rows}
        for row in rows:
            user_ids.update(row.mentioned_user_ids or [])
        user_map = await self._load_user_map(user_ids)
        reactions = (
            await self._reaction_service.summaries_for(ids, current_user_id)
            if with_reactions
            else {}
        )
        children: dict[int, list[dict]] = {}
        counts: dict[int, int] = {}
        if include_subs:
            children_raw, counts = await self._repo.children_examples_and_counts(ids)
            child_dtos = await self._build_discussion_dtos(
                children_raw,
                current_user_id=current_user_id,
                include_subs=False,
                with_reactions=with_reactions,
            )
            for dto in child_dtos:
                children.setdefault(dto["parentId"], []).append(dto)
        return [
            await self._build_discussion_dto(
                row,
                current_user_id=current_user_id,
                user_map=user_map,
                include_subs=include_subs,
                with_reactions=with_reactions,
                preloaded_reactions=reactions.get(row.id),
                preloaded_children=children.get(row.id),
                preloaded_child_count=counts.get(row.id, 0),
            )
            for row in rows
        ]
```

```python
# backend/app/domain/discussion/repositories.py —— 改动点：新增
    async def children_examples_and_counts(
        self, parent_ids: Sequence[int]
    ) -> tuple[list, dict[int, int]]:
        """一次取回每个父评论的计数 + 最多 2 条最新子回复。"""
        count_stmt = (
            select(Discussion.parent_id, func.count(Discussion.id))
            .where(Discussion.parent_id.in_(parent_ids), Discussion.deleted_at.is_(None))
            .group_by(Discussion.parent_id)
        )
        counts = {pid: c for pid, c in (await self._session.execute(count_stmt)).all()}
        rank = func.row_number().over(
            partition_by=Discussion.parent_id,
            order_by=Discussion.created_at.desc(),
        ).label("rn")
        inner = (
            select(Discussion.id, Discussion.parent_id, rank)
            .where(Discussion.parent_id.in_(parent_ids), Discussion.deleted_at.is_(None))
            .subquery()
        )
        ids_stmt = select(inner.c.id).where(inner.c.rn <= 2)
        ids = [i for (i,) in (await self._session.execute(ids_stmt)).all()]
        rows = await self.find_all_by_ids(ids)
        return rows, counts
```

  `_build_discussion_dto` 里 `if preloaded_reactions is not None: summary = preloaded_reactions`、`if preloaded_children is not None: sub_info = {"count": preloaded_child_count, "examples": preloaded_children}`（未传时保持现有行为，`get_discussion` 单条路径不受影响）。配套索引：

```python
    op.create_index("ix_discussion_model_type_model_id", "discussion", ["model_type", "model_id", "created_at"])
    op.create_index("ix_discussion_parent_id", "discussion", ["parent_id"])
    op.create_index("ix_discussion_reaction_discussion_id", "discussion_reaction", ["discussion_id"])
```

- **契约**：响应形状不变（`subDiscussions.examples` 仍是 2 条，排序不变）；`ix_*` 迁移只加索引，无数据迁移，但 `discussion` 是大表，加索引需在低峰或 `CREATE INDEX CONCURRENTLY`（迁移里用 `op.execute("CREATE INDEX CONCURRENTLY ...")` 时注意不能在事务内）。
- **测试**：`backend/tests/unit/test_discussion_service.py` 增 `test_list_discussions_preloads_reactions_and_children`（monkeypatch `_reaction_service.get_reaction_summary` 断言不被逐行调用）；`backend/tests/integration/test_comments.py` 增 `test_comment_list_shape_unchanged_with_subs`（3 条父评论各 2 条子回复 → `subDiscussions.count==2`、`examples` 长度 2）。

### 5. 输入校验缺口（能拿到 500）：#42、#38、#7/#36、#25

- **现状 / 问题**（都是「同族另一条路有校验，这条没有」）：
  - `#42` `questions.py:500`：`bounty = int(payload.get("bounty", 0))` 无 `try`；`{"bounty": "many"}` → `ValueError` → 500。POST 建题（`questions.py:144-147`）对同名字段有 `try/except` 并答 400。
  - `#38` `questions.py:417`：`type_ = payload.get("type")` 原样交给 `repositories.py:431-432` 的 `question.type = type_`，`Question.type` 是 `Integer`（`questions/models.py:18`），写 `"abc"` 在 flush 时抛数据库错误 → 500。POST 建题对 `type` 有 `int()` 保护（`questions.py:145`）。
  - `#7`/`#36` `answers.py:208`、`questions.py:365`：`mentionedUserIds` 原样传进 `create_discussion`，`discussion/services.py:56-58` 做 `{uid for uid in (mentioned_user_ids or []) if uid > 0}`；传字符串会按字符迭代、传 `[{}]` 会有 `dict > int` → `TypeError` → 500。
  - `#25` `questions.py:144-147`：`type`/`bounty` 只做了 `int()`，没有取值域（`type` 任意整数都入库，`bounty` 只在下游 PUT 里被限 0–20）。
- **优化**：把「body 里必有类型的字段」统一走一次校验，改动点放在路由（与现有风格一致，`BadRequestError` → 400）：

```python
# backend/app/api/routes/questions.py:494 set_question_bounty —— 改动点
    raw_bounty = payload.get("bounty", 0)
    try:
        bounty = int(raw_bounty)
    except (TypeError, ValueError):
        raise BadRequestError("bounty must be an integer") from None
    result = await service.set_bounty(question_id=question_id, user_id=auth_user.user_id, bounty=bounty)
```

```python
# backend/app/api/routes/questions.py:409 update_question —— 改动点：type 与 POST 同口径
    raw_type = payload.get("type")
    type_ = None
    if raw_type is not None:
        try:
            type_ = int(raw_type)
        except (TypeError, ValueError):
            raise BadRequestError("type must be an integer") from None
        if type_ not in QUESTION_TYPES:          # 改动点：按产品允许的取值集合校验
            raise BadRequestError("Invalid question type", data={"type": type_})
```

```python
# backend/app/api/routes/questions.py:357 / answers.py:195 —— 改动点：@ 的人先过一遍
def _mentioned_user_ids(payload: dict) -> list[int]:
    raw = payload.get("mentionedUserIds") or []
    if not isinstance(raw, list):
        raise BadRequestError("mentionedUserIds must be an array")
    out: list[int] = []
    for uid in raw:
        if isinstance(uid, bool):
            continue
        try:
            value = int(uid)
        except (TypeError, ValueError):
            raise BadRequestError("mentionedUserIds must be integers") from None
        if value > 0:
            out.append(value)
    return out

    mentioned_user_ids = _mentioned_user_ids(payload)      # 取代 payload.get(...)
```

- **契约**：非法输入由 500 变 400（`{code, message, error}` 信封不变）；`type`/`bounty` 的取值域是**新增约束**，若历史数据里有越界 `type`，PUT 回写会被拒（读不受影响）。
- **测试**：`backend/tests/integration/test_questions.py` 增 `test_bounty_rejects_non_numeric`（`{"bounty": "x"}` → 400）、`test_put_question_rejects_non_numeric_type`（→ 400）；`backend/tests/integration/test_comments.py` 增 `test_mentioned_user_ids_must_be_ints`（`{"content": "x", "mentionedUserIds": ["a"]}` → 400）。

### 6. `/comments/{commentableType}/{commentableId}`：枚举与 PG 类型不一致（#19、#20）

- **现状**：`comments.py:166` `create_comment` 只对 `ctype == "QUESTION"` 查一次存在性（`comments.py:178-182`），其余类型直接进 `CommentService.create_comment`（`comments/services.py:91-105`），后者不校验类型。
- **问题**：列是 PG 枚举 `CommentCommentabletypeEnum`，只有 `ANSWER`/`COMMENT`/`QUESTION`（`alembic/versions/a95752502bb0_initial_schema.py:34-36`、`comments/models.py:19-25`）。`POST /comments/FOO/1` 或 `POST /comments/MATERIAL_BUNDLE/1` 会带着非法枚举值 INSERT → 数据库报错 → 500。而 Python 侧 `CommentableType`（`comments/models.py:11-16`）里有 `MATERIAL_BUNDLE`/`KNOWLEDGE`，全仓 grep 只有定义、无任何使用——说明这两个值是「打算支持但没落地」。读侧 `comments.py:139` 对未知类型不报错，静默返回 200 空页（同一路径两种口径）。另外路由里 `QuestionRepository(session=db)` 直接在 handler 里 new 仓库（`comments.py:179`，`db=Depends(get_db)` 见 `:172`），跨过了 service 层。
- **优化**：类型先过 Python 枚举、再把「父级是否存在」交给 service；不允许的类型答 400 而不是让它变成 500。改动点：

```python
# backend/app/domain/comments/services.py —— 改动点：类型闸门放在领域侧
SUPPORTED_COMMENTABLE_TYPES = ("QUESTION", "ANSWER", "COMMENT")   # 与 PG 枚举一致

    async def create_comment(self, *, commentable_type, commentable_id, content, created_by_id):
        ctype = commentable_type.upper()
        if ctype not in SUPPORTED_COMMENTABLE_TYPES:
            raise BadRequestError(
                "Unsupported commentableType",
                data={"commentableType": commentable_type},
            )
        comment = await self._repo.create(...)
        return {"id": comment.id}
```

```python
# backend/app/api/routes/comments.py:166 —— 改动点：父级存在性交给注入的依赖，不在路由里 new 仓库
async def create_comment(
    commentableType: str,
    commentableId: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: CommentService = Depends(get_comment_service),
    question_repo: QuestionRepository = Depends(get_question_repo),   # 改动点
) -> dict:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise BadRequestError("content is required")
    ctype = commentableType.upper()
    if ctype == "QUESTION":
        if await question_repo.get_by_id(commentableId) is None:
            raise NotFoundError("Question not found", data={"id": commentableId})
    result = await service.create_comment(
        commentable_type=ctype, commentable_id=commentableId,
        content=content, created_by_id=auth_user.user_id,
    )
    return {"code": 201, "message": "Comment created successfully", "data": result}
```

  读侧同理：`comments.py:139` 的 `get_comments` 在不支持的类型上应 400/404，而不是 200 空页（当前 `list_comments` 用 `commentable_type` 过滤，`"FOO"` 查不到任何行）。
- **契约**：新增 400 分支（`FOO` 从 500/200 变成 400），`MATERIAL_BUNDLE`/`KNOWLEDGE` 的 Python 枚举值如果确有产品需求，需要先加 PG 枚举值（`ALTER TYPE "CommentCommentabletypeEnum" ADD VALUE ...`）再放行。
- **测试**：`backend/tests/integration/test_comments.py` 增 `test_create_comment_rejects_unknown_type`（`POST /comments/FOO/1` → 400）、`test_create_comment_rejects_material_bundle_until_enum_exists`（→ 400 且库里无行）、`test_list_comments_rejects_unknown_type`（→ 400）。

### 7. 状态码与幂等：关注/采纳/邀请的一族（#28、#29、#30、#31、#45、#2、#15）

- **现状 / 问题**：
  - `#28` `questions.py:213`：`follow_question` 返回 `False`（已关注）→ `BadRequestError("Already followed")` 400；同一动作的 PUT 版（`#41`，`questions.py:480`）却忽略返回值、永远 200。「已关注」是「目标状态已满足」，GitHub 的 star/watch 一族对此都是幂等成功或 409（[best-practices-for-using-the-rest-api](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)）。
  - `#29` `questions.py:231`：`unfollow` 未关注 → 400；DELETE 在 GitHub 是 204/幂等（[troubleshooting-the-rest-api](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api)）。
  - `#30` `questions.py:248`：采纳参数放 query（`alias="answer_id"`），`repositories.py`/service 里 `answer_id` 不属于 `question_id` 时抛 `BadRequestError`（400），而同一族的 `ensure_answer_in_question`（`answers/services.py:170-171`）用的是 404 + "…not found for this question" —— 同一个错配两种答案，本身就是一个存在性探针。
  - `#31` `questions.py:264`：路径名词 `accept` 与 `#30` 的 `acceptance` 不同；额外挂了 `require_permission(Action.ADMIN, Resource.QUESTION, "question_id")`（`questions.py:266-268`），而 `#30` 只用 `require_auth_user`；结果相同（出题人即 OWNER，见 `backend/app/auth/domains/question.py`），但多一次权限解析，且两条路的鉴权来源不一致。
  - `#45` `questions.py:584`：重复邀请抛 `BadRequestError`（400），仓库里已有 `ConflictError`（409）可表达「资源已存在」。
  - `#2` `answers.py:77`：`has_user_answered_question` 先查后插（`answers/services.py:141-142`），并发下两请求都能过检查，`answer` 表没有 `(question_id, created_by_id)` 唯一约束（迁移里只有 PK）→ 可落两条「同题同人」答案。
  - `#15` `comments.py:48`：成功文案整句 `"You have expressed your attitude towards the comment"`，其余端点 `"OK"`。
- **优化**：统一口径（以 GitHub 惯例：目标状态已满足 → 幂等成功；资源已存在且是 POST 创造 → 409；父级不匹配 → 404）：

```python
# backend/app/domain/questions/services.py —— 改动点：新增公开包装（仓库里已有
# repositories.py:192 count_followers，路由不该直接碰 _repo）
    async def count_followers(self, question_id: int) -> int:
        return await self._repo.count_followers(question_id)
```

```python
# backend/app/api/routes/questions.py:213 —— 改动点：幂等成功，保留真实计数
    changed = await service.follow_question(question_id=question_id, user_id=auth_user.user_id)
    follow_count = await service.count_followers(question_id)   # 改动点：走公开方法
    return {"code": 200, "message": "OK", "data": {"follow_count": follow_count, "changed": changed}}

# backend/app/api/routes/questions.py:231 —— 改动点：未关注也 200（幂等）
    changed = await service.unfollow_question(question_id=question_id, user_id=auth_user.user_id)
    follow_count = await service.count_followers(question_id)
    return {"code": 200, "message": "OK", "data": {"follow_count": follow_count, "changed": changed}}

# backend/app/api/routes/questions.py:584 —— 改动点：已邀请 → 409
# （QuestionInvitationService 里把 BadRequestError("Already invited") 换成
#   ConflictError("Already invited", data={"inviteeId": invitee_id})）

# backend/app/domain/answers/services.py:141 —— 改动点：唯一约束兜底（新迁移）
#   op.create_unique_constraint("uq_answer_question_author", "answer", ["question_id", "created_by_id"])
```

  捕获竞态：`create_answer` 里 `except IntegrityError: raise ConflictError("You have already answered this question") from None`。`#30` 的错配改 404、`#31` 的路径改成 `DELETE /questions/{id}/acceptance` 与 `PUT /questions/{id}/acceptance` 对齐（并发一次重命名属于 **breaking**，见契约）。
- **契约**：`#28` 由 201+400 改为 200 恒成功（`frontend/src/network/api/questions/index.ts` 的调用方需确认不依赖 400 判定「已关注」）；`#29` 由 400 改 200（同理）；`#31` 路径改名是 **breaking change**，建议保留旧路径一个版本（`@router.delete("/{question_id}/accept", deprecated=True)` 共存）；`#2` 的唯一约束迁移若已有重复数据需先去重。
- **测试**：`backend/tests/integration/test_questions.py` 增 `test_following_twice_is_idempotent`、`test_unfollowing_when_not_following_is_idempotent`、`test_accepting_an_answer_of_another_question_is_404`、`test_inviting_twice_is_409`；`backend/tests/integration/test_answers.py` 增 `test_second_answer_by_the_same_user_is_409`。

### 8. 同一动作的多套端点（#3/#4/#5 与 #14、#32/#33 与 #43、#28 与 #41、#12/#13）

- **现状**：题目有 `POST/DELETE/GET /questions/{id}/vote`（`questions.py:281/298/313`）**和** `POST /questions/{id}/attitudes`（`questions.py:513`）；答案有 `POST/DELETE/GET …/vote`（`answers.py:102/123/142`）与 `POST …/attitudes`（`answers.py:379`）；关注有 `POST`（`questions.py:213`）与 `PUT`（`questions.py:480`）；评论态度只有 `POST /comments/{id}/attitudes`（`comments.py:48`）。
- **问题**：grep 前端手写层与组件，`vote` 一族**没有任何调用方**（`frontend/src/network/api/questions/index.ts`、`answers/index.ts` 里 grep 不到 `vote`，页面用的是 `AnswersApi.postAttitude`，见 `frontend/src/components/answer/AnswerCard.vue:104-113`）。两套入口对同一张 `attitude` 表写入，语义还有差异：`/vote` 的 body 是 `voteType`（默认 `"UPVOTE"`，非法 → 400，`questions.py:287`、`answers.py:112`），`/attitudes` 的是 `attitude_type`（默认 `"UNDEFINED"` → 取消），且 `attitudes` 响应里的 `user_attitude` 是**原样回显请求值**（`questions.py:533`、`answers.py:403`），传 `"NEGATIVE "` 也会回显。
- **优化**：保留 `/attitudes`（有真实调用方）作为唯一写入口，`/vote` 一族标记 `deprecated=True` 并返回与 `/attitudes` 相同形状；`user_attitude` 改出自查询而非回显：

```python
# backend/app/api/routes/questions.py:513 attitude_question —— 改动点：user_attitude 出自库而不是回显
    if vote_type is None:
        result = await service.remove_question_vote(question_id=question_id, user_id=auth_user.user_id)
    else:
        result = await service.vote_question(question_id=question_id, user_id=auth_user.user_id, vote_type=vote_type)
    stored = await service.get_user_attitude(            # 改动点：新方法，读 attitude 表
        question_id=question_id, user_id=auth_user.user_id
    )
    attitudes = {
        "positive_count": result.get("upvotes", 0),
        "negative_count": result.get("downvotes", 0),
        "difference": result.get("upvotes", 0) - result.get("downvotes", 0),
        "user_attitude": stored,                          # 改动点
    }
    return {"code": 200, "message": "OK", "data": {"attitudes": attitudes}}

# backend/app/api/routes/questions.py:277 —— 改动点：旧入口只做转发、标弃用
@router.post("/{question_id}/vote", summary="Vote on Question (deprecated)", deprecated=True)
async def vote_question(...):
    attitude = "POSITIVE" if payload.get("voteType") == "UPVOTE" else "NEGATIVE"
    ...
```

- **契约**：被弃用端点行为不变（避免破坏潜在外部调用方），只加 `deprecated` 标记；`user_attitude` 由回显变真实值属**修正**，前端 `frontend/src/constants.ts` 的 `NewAttitudeType` 只有 `UNDEFINED|POSITIVE|NEGATIVE`，值域一致。
- **测试**：`backend/tests/integration/test_questions.py` 增 `test_attitude_reports_stored_value`（POST `/attitudes` body `{"attitude_type": "NEGATIVE "}` → 响应 `user_attitude == "UNDEFINED"` 或 400，二者之一是确定的，不能再回显原串）；`test_vote_endpoints_are_deprecated` 断言 OpenAPI 里 `deprecated is True`。

### 9. `GET /comments/{commentId}`（#16）读路径重复富化 + 与列表形状不一致

- **现状**：`comments.py:81` 先 `service.get_comment(...)` 拿到带作者 DTO 的评论，再自己 `auth_service._user_repo.get_by_id` + `auth_service._profile_repo.get_profile_by_user_id` + `auth_service.build_user_dto(...)` 覆盖 `comment["user"]`（`comments.py:88-95`）。
- **问题**：多 2 次取数 + `build_user_dto` 内部还有关注/粉丝/提问/回答计数（≈4 条查询），单请求多 6 次往返；且 `comments.py:89` 直接访问 `auth_service` 的**私有属性** `_user_repo`/`_profile_repo`（同一文件 `:29-31` 的 `get_user_auth_service` 依赖能拿到 service，但拿不到公开的「按 id 取用户 DTO」）。副作用是同一资源两种形状：详情里 `user` 是 `build_user_dto`（`/users/{id}` 形状），列表 `comments.py:139` 里 `user` 是 service 的轻量 DTO。
- **优化**：把「用户 DTO 富化」变成 `UserAuthService` 的公开方法，列表与详情共用一份：

```python
# backend/app/domain/user/services.py（UserAuthService）—— 改动点：公开方法
    async def get_user_dto_by_id(self, user_id: int, *, viewer_id: int | None) -> dict | None:
        user = await self._user_repo.get_by_id(user_id)
        if user is None:
            return None
        profile = await self._profile_repo.get_profile_by_user_id(user_id)
        if profile is None:
            return None
        return await self.build_user_dto(user, profile, viewer_id=viewer_id)
```

```python
# backend/app/api/routes/comments.py:81 —— 改动点：不再碰私有属性
    comment_dto = await service.get_comment(commentId, viewer_id=auth_user.user_id)
    author_id = comment_dto.get("created_by_id")
    if author_id:
        rich = await auth_service.get_user_dto_by_id(author_id, viewer_id=auth_user.user_id)
        if rich is not None:
            comment_dto["user"] = rich
```

- **契约**：响应形状不变（详情仍是富 `user`）；如果决定把列表也对齐成富形状，那才是契约变更（会让列表多 4N 次查询，不推荐）。
- **测试**：`backend/tests/integration/test_comments.py` 增 `test_get_comment_by_id_shape_matches_list` 与 `test_get_comment_by_id_does_not_read_private_repos`（后者可用 `monkeypatch` 断言不经 `_user_repo`）。

### 10. 响应形状三处不一致（#25 / #38、#39、#40、#44）

- **现状**：`POST /questions`（`questions.py:135`）201 只回 `{"data": {"id": …}}`；`PUT /questions/{id}`（`questions.py:409`）回整份 `_question_to_dto`（含 `createdAt`/`updatedAt`，**无** `created_at`）；`GET /questions/{id}`（`questions.py:198`）经 `get_question`（`questions/services.py:190-276`）把键改成 snake_case（`created_at`），并额外带作者、话题、计数。
- **问题**：同一资源三个形状、两种时间键。GitHub 的惯例是创建后返回**完整表示**（或 201 + `Location`），至少形状与 GET 一致（[best-practices-for-using-the-rest-api](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)）。前端建题后必须再 GET 一次才能拿到详情（`frontend/src/views/question/Ask.vue`）。
- **优化**：建题返回与 `GET /questions/{id}` 同形的 `{"question": …}`，并统一时间键为 **camelCase `createdAt`/`updatedAt`**（前端 `Page` 已经是 camelCase，同仓 `test_page_camel_case_keys.py` 就是这个方向）：

```python
# backend/app/api/routes/questions.py:135 add_question —— 改动点：回完整表示 + Location
    question = await service.create_question(...)
    return JSONResponse(                                   # 改动点
        status_code=201,
        headers={"Location": f"/api/questions/{question['id']}"},
        content={"code": 201, "message": "Created", "data": {"question": question}},
    )

# backend/app/domain/questions/services.py —— 改动点：统一时间键（_question_to_dto 已是 camelCase，
# get_question 里把它改成 snake_case 的那一段删掉即可；并在 create_question 返回里补 author/topics）
    dto = _question_to_dto(question)                       # 已是 createdAt/updatedAt
    dto["topicIds"] = topic_ids
    dto["topics"] = await self._topic_repo.list_tags([question.id])   # 需要时
    return dto
```

- **契约**：`POST /questions` 的响应体**扩大**（前端只用 `data.id`，`Ask.vue` 兼容）；`created_at` → `createdAt` 是 breaking（需同时 grep 前端 `created_at` 的读取点，`frontend/src/network/api/questions/types` 与页面）。
- **测试**：`backend/tests/contract/test_questions_contract.py` 增 `test_create_question_returns_full_representation`（断言 `data.question.createdAt` 存在、无 `created_at`）；`backend/tests/integration/test_questions.py` 增 `test_create_and_get_question_have_the_same_shape`（键集合相等）。

### 11. tags 三条（#49、#51）

- **现状**：`tags.py:36` `list_tags` 要求登录（`require_auth_user`），`tags.py:41-42` 手写了 `page_start < 0 → NotFoundError`（不可达，`Query(ge=0)` 已在进入 handler 前答 422）；`tags/services.py:34-43` 在 `keyword` 为空时直接返回空页；`tags.py:71` `create_tag` 只校验 `name` 是非空字符串、不 trim。
- **问题**：(a) 「列出标签」这个基本读操作必须登录，且不传 `q` 时恒空 —— 一个列表接口按名字/关键词检索才有结果，客户端必须先知道名字；(b) `create_tag` 写入 `name` 原样（`tags/services.py:74`），`" foo "` 与 `"foo"` 会各存一行，`get_by_name` 查不到另一条；(c) `tag` 表没有 `name` 唯一索引（迁移里 `tag` 只有 PK），`get_by_name` 先查后插在并发下可落两条同名；(d) 那条 404 分支是死代码。
- **优化**：

```python
# backend/app/api/routes/tags.py:36 —— 改动点：去掉不可达分支；q 为空时按 id 列出
async def list_tags(
    q: str | None = Query(default=None),
    page: PageQuery = Depends(page_50),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TagService = Depends(get_tag_service),
) -> dict:
    tags, page_info = await service.list_tags(
        keyword=q, page_start=page.start, page_size=page.size
    )
    return {"code": 200, "message": "OK", "data": {"topics": tags, "page": page_info}}

# backend/app/domain/tag/services.py:34 —— 改动点：无关键词时列全量（游标分页已有）
    async def list_tags(self, *, keyword, page_start, page_size):
        if keyword is not None and not keyword.strip():
            keyword = None                      # 只把「空串」当没传，仍走列表分支
        tags, prev_id, has_more, next_id = await self._repo.list_tags_cursor(
            keyword=keyword, page_start=page_start, page_size=page_size
        )
        ...

# backend/app/domain/tag/services.py:70 —— 改动点：先归一化再查重
    async def create_tag(self, *, name: str, created_by_id: int) -> dict:
        normalized = name.strip()               # 改动点
        if not normalized:
            raise BadRequestError("name is required")
        existing = await self._repo.get_by_name(normalized)
        if existing is not None:
            raise ConflictError("Topic already exists", data={"name": normalized})
        tag = await self._repo.create(name=normalized, created_by_id=created_by_id)
        return {"id": tag.id}
```

```python
# 新迁移 —— 改动点：唯一索引兜底（先清理同名重复行）
    op.execute("DELETE FROM tag a USING tag b WHERE a.name = b.name AND a.id > b.id")
    op.create_index("ux_tag_name", "tag", ["name"], unique=True)
```

- **契约**：`GET /tags` 不传 `q` 从「空页」变成「有数据」；`name` 唯一索引/trim 是行为收紧（历史重复数据需清理），建议顺带把 `get_by_name` 的比较也改成 `func.lower(name) = normalized.lower()` 再建函数索引，否则大小写仍可重复。
- **测试**：`backend/tests/integration/test_tags.py` 增 `test_list_tags_without_keyword_returns_rows`、`test_create_tag_trims_name`、`test_create_tag_is_case_insensitively_unique`。

### 12. `GET /questions/{question_id}/invitations/recommendations`（#46）不是推荐

- **现状**：`questions.py:618` → `QuestionInvitationService.get_recommendations`（`questions/services.py:627-633`）→ `UserProfileRepository.list_profiles`（`backend/app/domain/user/repositories.py:416-425`）。
- **问题**：`list_profiles` 就是 `SELECT … ORDER BY user_id ASC LIMIT limit OFFSET offset`。`get_recommendations`（`questions/services.py:627-632`）拿到这一页后**原样映射**、没有任何过滤：所以「推荐」= 全站最早注册的 N 个用户，与题目内容、话题、答题历史都无关，并且包含提问者本人和已被邀请的人（它连 `question_id` 除了 `_ensure_question_exists` 之外都没用上）。
- **优化**：要么改名为「更多用户」（诚实描述），要么按可解释的规则排序：排除提问者与已邀请者、优先同话题活跃者。最小可用版本：

```python
# backend/app/domain/questions/services.py:627 —— 改动点：排除提问者与已邀请者
    async def get_recommendations(self, *, question_id: int, limit: int) -> list[dict]:
        question = await self._question_repo.get_by_id(question_id)     # 改动点：拿提问者
        if question is None:
            raise NotFoundError("Question not found", data={"id": question_id})
        excluded = await self._repo.list_invited_user_ids(question_id)  # 改动点（下面新增）
        excluded.add(question.created_by_id)
        # list_profiles 增加 exclude_user_ids（保持 ORDER BY user_id ASC 作为稳定兜底）
        profiles = await self._profile_repo.list_profiles(
            limit=limit, offset=0, exclude_user_ids=excluded
        )
        return [_profile_to_user(p) for p in profiles]
```

```python
# backend/app/domain/questions/repositories.py —— 改动点：新增（QuestionInvitation.user_id；
# 该表删除是 hard_delete、没有 deleted_at）
    async def list_invited_user_ids(self, question_id: int) -> set[int]:
        stmt = select(QuestionInvitation.user_id).where(
            QuestionInvitation.question_id == question_id
        )
        return {uid for (uid,) in (await self._session.execute(stmt)).all()}
```

```python
# backend/app/domain/user/repositories.py:416 —— 改动点：加可选排除参数
    async def list_profiles(self, *, limit: int, offset: int, exclude_user_ids: set[int] | None = None):
        stmt = (
            select(UserProfile)
            .where(UserProfile.deleted_at.is_(None))
            .order_by(UserProfile.user_id.asc())
            .limit(limit)
            .offset(offset)
        )
        if exclude_user_ids:
            stmt = stmt.where(UserProfile.user_id.notin_(exclude_user_ids))
        return list((await self._session.execute(stmt)).scalars().all())
```

- **契约**：返回集合变小（排除了本人/已邀请），前端 `components/questions/InvitationList.vue` 只是列表渲染，无破坏。若希望「真推荐」，需要产品先定义排序依据（本学期活跃度/同话题），属于新需求。
- **测试**：`backend/tests/integration/test_an_invitation_belongs_to_its_question.py` 或 `test_questions.py` 增 `test_recommendations_exclude_self_and_invited`。

### 13. 删除评论时的作者判定（#8、#37）

- **现状**：`answers.py:224` `delete_answer_comment` / `questions.py:381` `delete_question_comment` 都先 `discussion_service.get_discussion(comment_id, user_id)` 再取 `discussion["sender"]["id"]` 比对。
- **问题**：`get_discussion` 走 `_build_discussion_dto(..., include_subs=True)`，为判一次作者拉子回复 + reaction + 两条子 DTO（≈6 次往返以上）；且 `sender` 在发送者档案缺失时是 `None`（`discussion/services.py:258` `user_map.get(entity.sender_id)`，`_load_user_map` 找不到就跳过），此时 `discussion["sender"]["id"]`（`answers.py:248`、`questions.py:399`）抛 `TypeError` → 500 —— 作者注销/档案被删的评论谁都删不掉，而且报的是 500 不是 403/404。`answers.py` 里还有 5 处在函数体内部临时 import 错误类（`:233`、`:249`、`:270`、`:300`、`:328`），而该文件顶部（`answers.py:7`）只 import 了 `BadRequestError, NotFoundError` —— 第 233/249 行要的 `ForbiddenError` 正是这么来的。
- **优化**：仓库层给一个「只取作者」的方法，判定不构造 DTO：

```python
# backend/app/domain/discussion/repositories.py —— 改动点：轻量取行
    async def get_sender_id(self, discussion_id: int) -> int | None:
        stmt = select(Discussion.sender_id).where(
            Discussion.id == discussion_id, Discussion.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
```

```python
# backend/app/api/routes/questions.py:381 —— 改动点：先绑定父级、再判作者，全程 1–2 次查询
    discussion = await discussion_service.get_discussion_meta(comment_id)   # 新增：只回 modelType/modelId/senderId
    if discussion is None:
        raise NotFoundError("Comment not found")
    if discussion["modelType"] != DiscussableModelType.QUESTION.value or discussion["modelId"] != question_id:
        raise NotFoundError("Comment not found for this question")
    if discussion["senderId"] != auth_user.user_id:
        raise ForbiddenError("Only the author can delete this comment")
    await discussion_service.delete_discussion(comment_id)
    return {"code": 200, "message": "OK", "data": {"deleted": True}}
```

- **契约**：错配仍答 404、非作者仍答 403（`backend/tests/integration/test_an_answer_belongs_to_its_question.py` 一族的既有断言不变）；「发送者档案缺失」由 500 变 403，属修正。
- **测试**：`backend/tests/integration/test_discussion_board_authz.py` 增 `test_deleting_a_comment_whose_author_profile_is_gone_is_403`（造评论后删档案 → 403 而非 500）；`backend/tests/unit/test_discussion_service.py` 增 `test_delete_does_not_build_full_dto`（断言不调 `get_reaction_summary`）。

## 三、模块级建议

### 3.1 分页协议：一套参数、一套响应、可预期的边界

- **适用**：本节 18 条分页端点（#1、#6、#7、#14、#19、#20、#24、#26、#35、#40、#44、#49 及对应列表）。
- **做法**：请求侧统一走第 1 条的 `PageQuery` 依赖（两种拼法都认），响应侧 `page` 对象保持 camelCase 六个键（`pageStart/pageSize/hasPrev/prevStart/hasMore/nextStart`），并**明确同一个字段现在承担两种语义**：`answers`（`answers/services.py:80-88`，`nextStart` 是下一个答案 id，`answers/repositories.py:35-51` 按 `Answer.id >= cursor_id` 取）与 `tags`（`tags/services.py:56-60`，`list_tags_cursor` 用 `id > cursor`）是**游标 id**；`questions` 全域（`questions/services.py:97/302/477/541`，一律 `"pageStart": offset`、`nextStart = offset + returned`）、`discussion`（`discussion/services.py:146-152`）、`comments`（`comments/services.py:73`）是**offset**。同一个 `page` 对象里 `pageStart` 一会儿是 id 一会儿是下标，`usePaging`（`frontend/src/utils/paging.ts:53`）只把它原样回传、无法分辨 —— 要么统一成游标（更稳，见 GitHub 的 `before`/`after`），要么至少把 `pageSize` 一律回**请求的页大小**（现在 `answers/services.py:83` 和 `discussion/services.py:148` 回的是「实际返回数」，只有 `tags` 一致）。
- **为什么**：GitHub 用 `Link` 头 + `per_page`（上限 100，超限静默截断而非报错），并**明确不要假设所有端点用同一组参数** —— 但同一产品的同一份前端不该有两种拼法。参考：<https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api>、<https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api>。
- **落地顺序**：先改 `PageQuery` + 测试（低风险），再逐个路由换参数名，最后（可选）把 offset 系改成游标。

### 3.2 状态码与错误信封：把「已存在 / 不存在 / 不属于父级 / 没权限」分开

- **适用**：全组写侧端点（#2、#3/#4、#15、#20、#28、#29、#30、#31、#42、#45）。
- **做法**：沿用仓库已有的 `ConflictError`(409)/`NotFoundError`(404)/`ForbiddenError`(403)/`BadRequestError`(400)/`AuthenticationRequiredError`(401)（`backend/app/core/errors.py`），并固定四条判据：(1) POST 造同键资源已存在 → 409；(2) 目标状态已满足的幂等动作（关注/收藏/投票取消）→ 200 + `changed: false`；(3) 父级错配一律 404 且 message 与读路径逐字相同（`answers/services.py:170-181` 的注释已经立了这条规矩，但 #30 的 `accept_answer` 仍用 400）；(4) 越权与不存在**不要**用同一码泄漏存在性（GitHub 明确建议对无权限的私有资源答 404 而不是 403，避免变成存在性探针）。
- **为什么**：GitHub 的 404/403 口径与 `message` + `documentation_url` 信封：<https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api>；幂等语义同 <https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api>。（Stripe/Slack 的同类惯例为业界通行，未逐条查证。）
- **补一条灰度**：`DELETE /questions/{id}/accept` 路径改名（3.3 之外）与 #48 的「不存在答 400」都已被现有用例钉住，改之前先确认没有外部调用方；仓库里的既有裁决（`questions.py:538-557` 的邀请读侧口径）属于**产品决定**，本次不建议动。

### 3.3 鉴权一致性：`GET /questions/{question_id}/followers` 是本组唯一裸接口

- **适用**：#40（`questions.py:461`）。
- **做法**：加 `auth_user: AuthUserInfo = Depends(require_auth_user)`，并把响应从 `[{"id": uid}]` 换成 `frontend` 能直接渲染的用户 DTO（`_profile_to_dto`，与 `GET /questions/{id}` 的作者形状一致）。
- **为什么**：`questions.py` 的路由表里**只有这一条**没有鉴权（同文件其余 27 条都挂了 `require_auth_user` 或 `require_permission`），而它返回的是「谁关注了这道题」的关系数据；同族的 `list_followed`（`questions.py:180`）要求登录。GitHub 对需要凭据的资源一律要求认证：<https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api>。这条不改也能跑，但「组内唯一例外」本身会让人误判其他端点的口径。

### 3.4 读路径的副作用与缓存：`view_count` 要么后台写，要么别在 GET 里写

- **适用**：#9、#27（`answers.py:273-279`、`questions.py:198`，以及第 2 条要新增的题目埋点）。
- **做法**：读接口埋点写日志后，`GET` 不再幂等、无法安全重放/缓存。短期：写库失败不影响读（`log_view` 包 `try/except` 记 warning，和 `search_helper.index_document` 的 best-effort 注释同一风格，`search_helper.py:55-68`）。中期：改成 `BackgroundTasks` 或 `app/domain/discussion` 已有的 `deliver` 式「先记账、后投递」模型。可缓存的那部分给 `GET /questions/{id}` 加 `ETag`：`hash(id, updated_at)` + `If-None-Match` → 304。
- **为什么**：GitHub 明确推荐条件请求来省配额并说明「未修改返回 304」：<https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api>；写路径阻塞读也是同页强调的失败模式。（用 ETag 表达「资源未变」是业界通行，未逐条查证。）

### 3.5 缺索引：先补「读日志 + 评论 + 反应」五张表

- **适用**：#1、#6、#19、#21、#22、#23、#35、#44。
- **做法**（新增一条迁移，全部只加索引，无数据迁移）：

```python
def upgrade() -> None:
    op.create_index("ix_question_query_log_created_at_question_id", "question_query_log", ["created_at", "question_id"])
    op.create_index("ix_question_search_log_created_at", "question_search_log", ["created_at"])
    op.create_index("ix_discussion_model_type_model_id", "discussion", ["model_type", "model_id", "created_at"])
    op.create_index("ix_discussion_parent_id", "discussion", ["parent_id"])
    op.create_index("ix_discussion_reaction_discussion_id", "discussion_reaction", ["discussion_id"])
    op.create_index("ix_comment_commentable_type_commentable_id", "comment", ["commentable_type", "commentable_id"])
    op.create_index("ix_answer_favorited_by_user_user_id", "answer_favorited_by_user", ["user_id", "answer_id"])
    op.create_index("ux_tag_name", "tag", ["name"], unique=True)
```

- **为什么**：这些表在 `alembic/versions/a95752502bb0_initial_schema.py` 里只有主键（`question_query_log`:489-498、`question_search_log`:500-514、`discussion`:190-201、`discussion_reaction`:210-222、`comment`），而 `count_comments`（`answers/repositories.py:215-224`）、`get_trending_questions`（`repositories.py:349-373`）、`list_discussions`（`discussion/repositories.py` 的 `find_all`/`count_children`）全部按这些列过滤。注意 `discussion`/`comment` 是既有数据量较大的表，生产上加索引建议 `CREATE INDEX CONCURRENTLY`（Alembic 里需 `with op.get_context().autocommit_block():`）。GitHub 的分页/筛选同样要求在需要处提供索引支撑，官方文档未展开 DDL 细节，此处属工程通行做法，未逐条查证。

### 3.6 两套评论系统并存（legacy `comment` vs `discussion`）

- **适用**：#15、#16、#17、#18、#19、#20（`/comments/*`）与 #6、#7、#8、#35、#36、#37（题目/答案的 `/comments`）。
- **做法**：`/comments/*` 一族基于 legacy `comment` 表（`CommentService`），题目/答案下面的评论基于 `discussion` 表（`DiscussionService`），两张表各有自己的 commentable 枚举与分页形状，语义重叠。建议在文档里明确「新功能只用 discussion」，给 `/comments/*` 标注 `deprecated=True`（响应不变），并把这个决定写进 `docs/api-conventions.md`。
- **为什么**：同一个词（comment）在同一份 API 里指两个不同资源、两套形状（`comment` 表列是 PG 枚举、`discussion` 表列是 `String(255)` + Python 枚举），是本次调研里唯一无法用「补校验」收敛的重复——不收敛的话，每一次「评论」相关的改动都要做两遍，且两遍的口径会继续分叉（现状已经分叉：`#19` 空类型静默 200 vs `#35` 同样静默 200，而 `#20` 会 500）。GitHub 对嵌套资源只有一套 comments 端点族：<https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api>。

### 3.7 公共依赖抽取：路由里不该出现的东西

- **适用**：#20（`QuestionRepository(session=db)` 直接在 handler 里 new，`comments.py:179`）、#8/#37（body 里临时 import 错误类，`answers.py:233/249/270/300/328`）、#28/#41（`service._repo.count_followers` 穿透私有属性，`questions.py:223/486`）、#16（`auth_service._user_repo` / `_profile_repo`，`comments.py:89-90`）、#1/#26（`pageStart` 混进 Python 形参名，`questions.py:181`）。
- **做法**：仓库服务通过 `app/api/routes/*` 顶部的 `get_*_service` 工厂注入（已有 9 个这样的工厂），路由只依赖 service；service 需要的能力（计数、按 id 取用户 DTO）开成公开方法；import 一律放模块顶部（仓库里有 `backend/tests/unit/test_domain_import_guard.py` 守着领域层不能反向 import API 层，路由层的这类写法没有守卫，建议顺带加一条 lint/单测：路由模块内不得出现 `_repo` / `_user_repo` 这类下划线属性访问）。
- **为什么**：`service._repo` 让路由直接依赖仓库接口，任何仓库方法改名都会同时改到 API 层，边界失效；这也是本次能发现「同一行查两遍」的根因——校验散在路由和 service 两处。GitHub 的 REST 设计说明里强调「资源表示与端点行为要可预测」，分层是实现侧手段。<https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api>

### 3.8 速率限制与批量操作

- **适用**：写侧全组，重点是 #45（邀请）、#36/#7（评论）、#20（通用评论）、#51（建标签）。
- **做法**：仓库已有认证侧的尝试限制（`backend/tests/integration/test_auth_attempt_limits.py`、`backend/tests/unit/test_login_attempt_budget.py` 一族），把这套「按用户+动作计数」的机制复用到邀请/评论，超限答 429 + `Retry-After`；邀请接口同时把 `limit`（recommendations）与单题邀请总数加上上限。
- **为什么**：这几条都是「任何登录用户可调、写库、会给人发通知」的路径（`discussion/services.py:62-95` 的 mention 会落投递事件），没有上限就既能刷量也能打扰他人。GitHub 的速率限制惯例（未认证 60/h、认证 5000/h，超限 403/429 + 头）：<https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api>。

### 3.9 死代码与表

- **适用**：`questions/repositories.py:242-258`（`log_query`）、`:323-347`（`log_search`，第 2 条要重新启用就留着）、`comments/models.py:11-16`（`CommentableType`，全仓无使用）、`tags.py:41-42`（不可达的 404 分支）、`backend/app/domain/answers/models.py:28` 的 `AnswerVote`/`answer_vote` 表（全仓只有定义，答案投票实际走 `attitude` 表）。
- **做法**：`AnswerVote`、`CommentableType` 与那条不可达分支直接删（表删需要迁移）；`log_query`/`log_search` 是第 2 条的实施载体，保留。
- **为什么**：这些残留会误导下一位读者（`AnswerVote` 会让人以为答案投票有新表；`CommentableType` 会让人以为 `MATERIAL_BUNDLE` 已支持，而实际会 500），本次第 6 条的结论正是靠「谁在用」这个问题得出的。
