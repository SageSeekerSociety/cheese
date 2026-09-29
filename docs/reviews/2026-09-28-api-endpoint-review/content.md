# 内容、文件与知识（`content`）— 逐接口分析

范围：`/tmp/api-review/out/content.list.md` 的 53 个接口，一个不落。全部按 `line` 号读了 handler，并追进 `app/domain/{attachment,discussion,knowledge,legal,materials,memory,project,documents,docs_site}` 与 `app/api/{response,conditional,auth}.py`。

判断参照：GitHub REST（分页 `link` 头、错误体 `message`/`documentation_url`、ETag/条件请求、`X-RateLimit-*`）优先，其次 Stripe（`Idempotency-Key`）。项目既有约定优先于外部惯例：响应信封 `{"code","message","data"}` 不动。

**清单与源码的一致性**：53 条的 `file:line` 与 `method+path` 逐条核对过，**没有发现不一致**。几点核对过程与结论：

- 每个模块只有一个 `APIRouter`（`attachments.py:41`、`discussions.py:25`、`docs_site.py:38`、`knowledge.py:26`、`legal.py:22`、`materialbundles.py:15`、`materials.py:13`、`memory.py:36`、`memory_files.py:39`、`room_files.py:41`），清单里的 path 都是「路由自身 path」，与 `main.py:368` 的自动挂载 + `main.py:374-380` 记录的 `/api` 前缀合起来才是对外 URL，清单与该约定一致。
- `GET /discussions/reactions`（`discussions.py:269`）**声明在** `GET /discussions/{discussionId}`（`discussions.py:280`）**之前**，所以不会被路径参数吞掉 —— 顺序是对的，别动。
- `/docs/...` 三条（`docs_site.py:233,262`）带 `include_in_schema=False`，是有意的内部工具面，不是漏挂。

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | POST | `/attachments` | 可优化 | 上传时把整个文件读进内存只为量 size（`services.py:64-65`），且路由层没有大小上限 |
| 2 | GET | `/attachments/{attachmentId}` | 可优化 | 一次查询 + 一次题域判据，本身干净；但 `to_dict` 把 `meta.uploaderId`（内部用户 id）一并给了任何登录读者 |
| 3 | GET | `/attachments/{attachmentId}/download` | 可优化 | 同一行被读两次（`routes:162` 判权 + `services.download:147` 再读一次），响应整体进内存 |
| 4 | DELETE | `/attachments/{attachmentId}` | 暂无 | uploader 判据在服务层（`services.py:165`），存储删失败时的「查一次再决定」是对的 |
| 5 | POST | `/discussions` | 可优化 | 刚建的帖子 reaction 摘要必然为空，却固定花 3 条查询；body 是裸 `dict` |
| 6 | GET | `/discussions` | 可优化 | 一页 20 条 ≈ 340 条 SQL：reaction / 子回复 / 被 @ 人三重 N+1 |
| 7 | GET | `/discussions/reactions` | 可优化 | 本组唯一不鉴权的接口；GET 里带 INSERT（`ensure_defaults`），每调一次先 COUNT 一次 |
| 8 | GET | `/discussions/{discussionId}` | 可优化 | 子回复被取两遍（`services.py:272` 的 examples + `routes:313` 的分页），reaction 逐行算 |
| 9 | PATCH | `/discussions/{discussionId}` | 可优化 | 同一行 `get_by_id` 三次（`routes:148` → `services:158` → `services:169`），每次还带一条 mention 查询 |
| 10 | GET | `/discussions/{discussionId}/sub-discussions` | 可优化 | 与 8 走同一段 `list_discussions`，逐行 reaction 摘要 |
| 11 | DELETE | `/discussions/{discussionId}` | 可优化 | 为了拿 `sender.id` 建了整个 DTO（含子树与 reaction），约 12 条 SQL |
| 12 | POST | `/discussions/{discussionId}/reactions/{reactionTypeId}` | 可优化 | 每次 toggle 先跑 `ensure_defaults` 的 COUNT；toggle 语义非幂等 |
| 13 | DELETE | `/discussions/{discussionId}/reactions/{reactionTypeId}` | 可优化 | 同 12（同一段 `reaction_services.toggle/remove`） |
| 14 | POST | `/docs/dev-access` | 暂无 | 签 httpOnly + SameSite=strict 的短票，仅平台管理员 |
| 15 | GET | `/docs/dev-access/check` | 暂无 | 管理员名单有 60s 缓存（`access.AdminSet`），每个静态文件都查一次也不是问题 |
| 16 | POST | `/docs/ask` | 可优化 | `_refuse` 的错误体是 `{code,message}`，与平台统一错误体（多一个 `error{}`）不一致 |
| 17 | POST | `/docs/agent/search` | 暂无 | 索引 10 分钟缓存（`retrieval.IndexSource`），命中即内存 |
| 18 | POST | `/docs/agent/read` | 可优化 | 同一份静态文档每次请求都重新 HTTP 拉一遍 —— 索引有缓存，页面没有 |
| 19 | POST | `/knowledge` | 暂无 | 建一条 4 条查询组装 DTO，校验顺序合理 |
| 20 | GET | `/knowledge` | 可优化 | 前端发的 `sort_by`/`sort_order` 被静默忽略；仓库层同一组过滤条件抄了两份 |
| 21 | GET | `/knowledge/{knowledgeId}` | 暂无 | 单行 + 团队判据；批量 DTO 组装 |
| 22 | PATCH | `/knowledge/{knowledgeId}` | 可优化 | `name` 没有空白与长度校验，而列是 `String(255)`；超长直接 500 |
| 23 | DELETE | `/knowledge/{knowledgeId}` | 可优化 | 回包缺 `data` 键，同模块其余全部三键齐全（契约测试也这么断言） |
| 24 | POST | `/knowledge/{knowledgeId}/upvote` | 可优化 | SELECT-再-INSERT：并发下撞唯一约束 `uq_knowledge_upvote` 变成 500 |
| 25 | DELETE | `/knowledge/{knowledgeId}/upvote` | 暂无 | 幂等（不存在即返回），权限走同一条 `_ensure_team_member` |
| 26 | GET | `/legal/documents` | 可优化 | 内容随部署固定、进程内已 `@cache`，却没有任何 ETag/304 |
| 27 | GET | `/legal/documents/{document}` | 可优化 | 回包里本来就有 `sha256`，拿它当 ETag 是零成本的 |
| 28 | GET | `/legal/documents/{document}/versions/{version}` | 可优化 | 同 27；`version` 路径参数无格式约束，靠字典查不到来 404 |
| 29 | GET | `/users/me/consents` | 暂无 | 单查询 + 内存文档表，只给本人 |
| 30 | POST | `/users/me/consents` | 暂无 | 先校验版本再落库，响应前显式 `commit`（`legal.py:142`），是这组里最稳的一处 |
| 31 | GET | `/material-bundles` | 可优化 | 每次都把全表 id 拉进内存；`page_start` 不在集合里时元数据与 items 互相矛盾 |
| 32 | GET | `/material-bundles/{bundle_id}` | 可优化 | 每个 material 一次 `get_by_id`（`services.py:226-229`），N+1 |
| 33 | POST | `/material-bundles` | 可优化 | 每个 id 一次存在性查询 + 每个 id 一次 INSERT |
| 34 | PATCH | `/material-bundles/{bundle_id}` | 可优化 | 差集里每个 id 各一次查询（`add/remove_material_to_bundle` 各自先 SELECT） |
| 35 | DELETE | `/material-bundles/{bundle_id}` | 暂无 | creator 判据 + 单次删除 |
| 36 | POST | `/materials` | 可优化 | `await file.read()`（`routes/materials.py:44`）整体进内存，最坏 100MB/请求 |
| 37 | GET | `/materials/{material_id}` | 待确认 | 任何登录用户可读任意材料的元数据与 URL —— 服务层注释明说这是设计（`services.py:71-80`），要不要收紧是产品决定 |
| 38 | DELETE | `/materials/{material_id}` | 暂无 | uploader 判据在服务层（`services.py:119`） |
| 39 | GET | `/memory` | 可优化 | 无分页无上限，按 scope 前缀全量返回，`pageSize` 恒等于行数 |
| 40 | DELETE | `/memory/{entry_id}` | 暂无 | 越权一律答「不存在」，判据与列表同一处，做对了 |
| 41 | GET | `/memory/files` | 暂无 | 单查询；「一个作用域一次性给全」是写明的产品决定 |
| 42 | PUT | `/memory/files` | 可优化 | 与 43 有约 30 行逐字重复的 body 解析（`memory_files.py:167-197` vs `237-258`） |
| 43 | POST | `/memory/files/delete` | 可优化 | 动词进路径；同一件事 `DELETE /memory/files` 更合适 |
| 44 | GET | `/topics/{topic_id}/files/raw` | 可优化 | 已经在回 `X-Cheese-Version`（内容哈希），却不认 `If-None-Match`：每次 pull 全量回 |
| 45 | GET | `/topics/{topic_id}/files/revisions` | 可优化 | 某路径的**全部**历史一次返回，无 limit 无分页 |
| 46 | GET | `/topics/{topic_id}/files/revisions/{revision_id}/raw` | 暂无 | 单行 + `room_id` 归属比对（`room_files.py:273`） |
| 47 | POST | `/topics/{topic_id}/files/revisions/{revision_id}/restore` | 暂无 | 恢复本身也落成一条新 revision，可撤销；提交时机正确 |
| 48 | POST | `/topics/{topic_id}/files/copy` | 暂无 | 重名先拒、写库前 `commit`、路径过 `_room_path` |
| 49 | GET | `/topics/{topic_id}/files/templates` | 暂无 | 内存模板目录，无 DB |
| 50 | POST | `/topics/{topic_id}/files/new` | 暂无 | 校验模板后缀 + 重名，路径同 48 |
| 51 | GET | `/topics/{topic_id}/files/editor` | 暂无 | 每次现签一份短票；未启用时 `enabled:false` 也走 200 信封，一致 |
| 52 | GET | `/office-editor/files/{link}` | 暂无 | 链接 token 即凭据（写明），字节整体进内存 —— 见 M4 的条件请求建议 |
| 53 | POST | `/office-editor/callback/{link}` | 暂无 | 验签 + `saved_by_session` 幂等 + 冲突另存，并发处理是全模块最讲究的一处 |

小计：**可优化 31 / 暂无 21 / 待确认 1**，合计 53。

### 两个跨组发现对本组的适用性

**(a) 请求侧分页参数名与前端不一致：部分适用，形态不同。** 本组的 `pageStart`/`pageSize`（camelCase）与前端契约一致的有 `/discussions*`（`discussions.py:220-221`，前端 `types.ts:42-43`）和 `/knowledge`（`knowledge.py:109-110`，前端 `types.ts:54-55`）—— **这两个是好的**。真正不一致的是两处：

1. `/material-bundles` 只认下划线：`page_start`/`page_size`（`materialbundles.py:30-31`，`alias="page_start"`）。前端全局用 `pageStart`/`pageSize`（`src/utils/paging.ts:8`、`src/types/commons.ts:10`），照这个惯例调用会被**静默忽略** → 永远第一页 + `hasMore:true` → 无限滚动卡死。今天前端没有调用方（`grep -rn materialBundle frontend/src` 为空），所以是潜伏的，不是现患。
2. `/knowledge` 的**排序**参数：前端与遗留契约快照发的都是下划线 `sort_by`/`sort_order`（`frontend/src/types/knowledges.ts:56-57`、`Knowledge.vue:809-810`；`backend/tests/contract/api_catalog.json` 里 `/knowledge` GET 的 `parameters` 也正是 `sort_by`/`sort_order`），而 handler 只声明了 `sortBy`/`sortOrder`（`knowledge.py:111-112`，无 alias）→ 前端的排序参数**从来没生效过**。同模块的 `/discussions` 就同时接受两种（`discussions.py:224-225,233-234`），说明这是漏改而不是决定。
3. 顺带：`GET /discussions` 的 `parentId`（`discussions.py:219`）没有 alias，而前端类型与契约快照写的是 `parent_id`（`frontend/src/network/api/discussions/types.ts:41`、`api_catalog.json`）。前端目前没有调用方传它，但同一个 handler 里 `modelType`/`modelId`/`pageStart` 是 camelCase、这一个按快照应该是 snake_case，两种写法混在一个签名里。

**(b) 无上限查询 + 逐行追问：适用，共 4 处，其中一处是本组最重的。**
- `GET /discussions`（及 8/10，共用同一段 service）—— 三重 N+1，最重，见下。
- `GET /material-bundles` —— 无上限查询（全表 id）叠加两条互相独立的取数语句，见下。
- `GET /material-bundles/{id}`、`POST/PATCH /material-bundles` —— 逐 id 追问，见下。
- 另有两处是「无上限」而非 N+1：`GET /memory`（`memory.py:117-123`）、`GET /topics/{topic_id}/files/revisions`（`room_files.py:253-266`）。

## 二、详细分析（按收益从高到低）

### GET /discussions

- **现状**：`routes/discussions.py:215-266`。默认 `withReactions=True`、`withSubDiscussions=True`（`discussions.py:226-227`），交给 `DiscussionService.list_discussions`（`domain/discussion/services.py:113-153`）→ `_build_discussion_dtos`（`services.py:217-242`）逐行 `await _build_discussion_dto`。
- **问题**：一页 20 条根帖实际发出 **≈322~420 条 SQL**，明细（都在已读的代码里）：
  - 主查询 1 条 + `find_all` 里**逐行**一条 mention 查询（`repositories.py:125-128`，20 条）+ count 1 条 = 22；
  - 每行 `get_reaction_summary`（`reaction_services.py:81-107`）= `ensure_defaults` 的 COUNT + `count_by_discussion` + `list_active`，合计里 count>0 的类型再各一次 `has_user_reacted` → **每行 3~5 条**；
  - 每行 `include_subs` 又调一次 `list_discussions(page_size=2)`（`services.py:272-283`）→ 里面是 1 条 select + 2 条 mention + 1 条 count + `_load_user_map` 1 条 + 两个 example 各自的 3~5 条 reaction = **每行约 15 条**；
  - 每行再一条 `count_children`（`services.py:284`，`repositories.py:165-171`）。
  合计 `22 + 20×(3~5) + 20×(15) ≈ 340`。这些查询全是同一批 id 的重复问法，没有任何一条需要按行发。
- **优化**：把「按行算」换成本组已在用的批量形状（`_build_dtos` 那种）。三处替换：

  ```python
  # domain/discussion/repositories.py —— 1) 被 @ 的人：一条代替 N 条
  async def load_mentioned_user_ids_many(
      self, discussion_ids: Sequence[int]
  ) -> dict[int, list[int]]:
      if not discussion_ids:
          return {}
      stmt = select(
          DiscussionMentionedUser.discussion_id, DiscussionMentionedUser.user_id
      ).where(DiscussionMentionedUser.discussion_id.in_(list(discussion_ids)))
      out: dict[int, list[int]] = {}
      for did, uid in (await self._session.execute(stmt)).all():
          out.setdefault(did, []).append(uid)
      return out

  # 2) 子帖计数：一条 GROUP BY 代替每行一条 count_children
  async def count_children_by_parent(
      self, parent_ids: Sequence[int]
  ) -> dict[int, int]:
      if not parent_ids:
          return {}
      stmt = (
          select(Discussion.parent_id, func.count(Discussion.id))
          .where(
              Discussion.parent_id.in_(list(parent_ids)),
              Discussion.deleted_at.is_(None),
          )
          .group_by(Discussion.parent_id)
      )
      return {r[0]: int(r[1]) for r in (await self._session.execute(stmt)).all()}
  ```

  ```python
  # find_all 里删掉循环（repositories.py:125-128），改成：
  row_ids = [row.id for row in rows]
  mention_map = await self.load_mentioned_user_ids_many(row_ids)
  for row in rows:
      row.mentioned_user_ids = mention_map.get(row.id, [])  # type: ignore[attr-defined]

  # domain/discussion/reaction_services.py —— 3) 一页的 reaction 摘要：3 条查询
  async def summaries_for(
      self, *, discussion_ids: Sequence[int], current_user_id: int | None
  ) -> dict[int, list[dict]]:
      """一次算完一页：批量 count + 一次 list_active + 一次「我点过哪些」。"""
      if not discussion_ids:
          return {}
      await self.ensure_default_reaction_types()
      counts = await self._reaction_repo.counts_by_discussions(discussion_ids)
      types = await self._reaction_type_repo.list_active()
      mine = (
          await self._reaction_repo.user_reactions(discussion_ids, current_user_id)
          if current_user_id is not None
          else set()
      )
      return {
          did: [
              {
                  "reactionType": self._reaction_type_to_dict(rt),
                  "count": counts.get(did, {}).get(rt.id, 0),
                  "hasReacted": (did, rt.id) in mine,
              }
              for rt in types
          ]
          for did in discussion_ids
      }
  ```

  ```python
  # 配套的两个仓库方法
  # DiscussionReactionRepository.counts_by_discussions / user_reactions
  async def counts_by_discussions(self, discussion_ids: Sequence[int]) -> dict[int, dict[int, int]]:
      if not discussion_ids:
          return {}
      stmt = (
          select(
              DiscussionReaction.discussion_id,
              DiscussionReaction.reaction_type_id,
              func.count(DiscussionReaction.id),
          )
          .where(
              DiscussionReaction.discussion_id.in_(list(discussion_ids)),
              DiscussionReaction.deleted_at.is_(None),
          )
          .group_by(DiscussionReaction.discussion_id, DiscussionReaction.reaction_type_id)
      )
      out: dict[int, dict[int, int]] = {}
      for did, rtid, n in (await self._session.execute(stmt)).all():
          out.setdefault(did, {})[rtid] = int(n)
      return out

  async def user_reactions(
      self, discussion_ids: Sequence[int], user_id: int
  ) -> set[tuple[int, int]]:
      if not discussion_ids:
          return set()
      stmt = select(
          DiscussionReaction.discussion_id, DiscussionReaction.reaction_type_id
      ).where(
          DiscussionReaction.discussion_id.in_(list(discussion_ids)),
          DiscussionReaction.user_id == user_id,
          DiscussionReaction.deleted_at.is_(None),
      )
      return {(r[0], r[1]) for r in (await self._session.execute(stmt)).all()}
  ```

  `_build_discussion_dtos` 里一次取好 `reaction_map` 与 `sub_count_map`，`_build_discussion_dto` 加两个可选参数（`reactions: list[dict] | None = None`、`sub_count: int | None = None`）：拿到就不算，拿不到沿用旧路径 —— 单条路径（`create_discussion` / `get_discussion`）不受影响。改完一页是 **6 条**（主查询、count、mention、reaction×2、子计数），与行数无关。
  **第二步（可选、有契约影响）**：列表路径的 `subDiscussions.examples` 前端一次都不读（`Discussions.vue:66-77` 只读 `.count`），而它正是每行 15 条查询的来源。若接受列表路径返回 `{"count": n, "examples": []}`，`GET /discussions` 可降到 5 条查询。
- **契约**：**响应形状不变**（`data.discussions[].reactions[]`、`subDiscussions.{count,examples}` 字段齐全，值相同）。第二步会改 `examples` 的内容（空数组），前端无影响，但属于对外可见变化，建议单独一条 commit + 变更说明。分页语义不变。无数据迁移。
- **测试**：`tests/integration/test_hot_path_queries.py` 已有 `counting_sql()`（`Engine` 级监听，`:29-45`），照它的形状加：
  `test_discussion_list_round_trips_do_not_grow_with_the_page`：同一块板上发 3 条与 24 条讨论，两次 `GET /discussions?modelType=TASK&modelId=…&pageSize=20`，断言两次 SQL 条数之差 ≤ 2；再断言 `reactions` 与 `subDiscussions.count` 的值与改动前一致（`tests/integration/test_discussion.py:108` 的 `test_list_discussions` 保留为行为回归）。

### GET /material-bundles

- **现状**：`routes/materialbundles.py:28-44` → `MaterialBundleService.list_bundles`（`domain/materials/services.py:158-209`）。先 `list_all_bundle_ids()`（`repositories.py:83-101`）把**全部**匹配 id 拉成 Python 列表，用它算 `start_idx/has_more/next_start`；再单独 `list_bundles()` 取那一页。
- **问题**：
  1. `repositories.py:83-101` 无 limit 无 offset：一次列表请求把整张表的 id 读进内存（素材包是全平台共享的，不按项目过滤），页面越用越慢。
  2. `services.py:174-183` 与 `:185-191` 是两条**互相独立**的取数：items 来自 SQL 游标，元数据来自内存列表。`page_start` 只要不是过滤集里的成员就分裂。可复现：`?sort=newest&page_start=1`（表里没有 id=1 的包）→ `all_ids.index(1)` 抛 `ValueError` → `start_idx=0`（元数据按「第一页」算：`hasMore=true`、`nextStart=<第 11 个 id>`），而 SQL 侧的 `id <= 1` 返回**空 items**。响应变成「items 为空 + hasMore 为真 + 报了 nextStart」，前端 `usePaging` 会照 `nextStart` 一直请求下去。
- **优化**：不要内存里的 id 列表，用「多取一行」判 `hasMore`，用一条索引查询求 `prevStart`。`services.py:158-209` 换成：

  ```python
  async def list_bundles(self, *, keyword, page_start, page_size, sort=None):
      title_keyword, id_gte = self._parse_search_query(keyword)
      descending = sort == "newest"
      rows, has_more = await self._repo.page_bundles(
          keyword=title_keyword, id_gte=id_gte,
          cursor_id=page_start, limit=page_size, descending=descending,
      )
      items = [_bundle_to_dto(b) for b in rows]
      first_id = rows[0].id if rows else 0
      last_id = rows[-1].id if rows else 0
      next_start = rows[page_size].id if has_more else 0
      prev_start = (
          await self._repo.id_before(
              keyword=title_keyword, id_gte=id_gte,
              cursor=first_id, descending=descending,
          )
          or 0
      )
      return items, {
          "pageStart": first_id,
          "pageSize": len(items),
          "hasPrev": prev_start != 0,
          "prevStart": prev_start,
          "hasMore": has_more,
          "nextStart": next_start,
      }
  ```

  ```python
  # MaterialBundleRepository —— 两条有界查询取代 list_all_bundle_ids / list_bundles
  def _filters(self, keyword: str | None, id_gte: int | None) -> list:
      where = []
      if keyword:
          where.append(MaterialBundle.title.ilike(f"%{keyword.strip()}%"))
      if id_gte is not None:
          where.append(MaterialBundle.id >= id_gte)
      return where

  async def page_bundles(self, *, keyword, id_gte, cursor_id, limit, descending):
      stmt = select(MaterialBundle).where(*self._filters(keyword, id_gte))
      if cursor_id is not None:
          stmt = stmt.where(
              MaterialBundle.id <= cursor_id if descending else MaterialBundle.id >= cursor_id
          )
      stmt = stmt.order_by(
          MaterialBundle.id.desc() if descending else MaterialBundle.id.asc()
      ).limit(limit + 1)  # +1：hasMore 是事实，不是切片推出来的
      rows = list((await self._session.execute(stmt)).scalars().all())
      return rows[:limit], len(rows) > limit

  async def id_before(self, *, keyword, id_gte, cursor: int, descending) -> int | None:
      """过滤集里紧挨着这一页之前的那一个 id；没有就 None（hasPrev 的来源）。"""
      stmt = select(MaterialBundle.id).where(*self._filters(keyword, id_gte))
      stmt = stmt.where(
          MaterialBundle.id > cursor if descending else MaterialBundle.id < cursor
      )
      stmt = stmt.order_by(
          MaterialBundle.id.asc() if descending else MaterialBundle.id.desc()
      ).limit(1)
      return await self._session.scalar(stmt)
  ```

  注意 `id_before` 必须带上同一组过滤（`keyword` + `id_gte`），否则 `id:>=` 搜索语法下的 `hasPrev` 会从 `false` 变成 `true`，`tests/integration/test_materials.py:414-430` 会红。
- **契约**：请求/响应形状都不变，`pageStart/prevStart/nextStart` 的值语义也不变（现有测试逐值钉死了它们：`test_materials.py:395-455`）。`page_start` 指向过滤集之外的 id 时行为变化：旧代码静默回第一页且 `hasMore` 可能为真，新代码回空页 + `hasMore:false`（正确的「游标到头」）。无迁移。
- **测试**：`tests/integration/test_materials.py` 同一类里加
  `test_unknown_cursor_is_an_empty_page`：`params={"q": unique, "page_size": 10, "page_start": 1_000_000_000, "sort": "newest"}` → 断言 `materials == []`、`page.hasMore is False`、`page.nextStart == 0`；再加 `test_bundle_list_round_trips_do_not_grow_with_the_table`（`counting_sql()`）：造 5 个包与 60 个包各请求一次，SQL 条数相等。

### GET /discussions/{discussionId}（以及 10、11 同一段 service）

- **现状**：`routes/discussions.py:280-335`。`service.get_discussion(...)`（`services.py:100-111`）传 `include_subs=True` → `_build_discussion_dto` 里再查一次子帖 examples + `count_children`（`services.py:272-288`）；随后路由**又**调一次 `service.list_discussions(parent_id=discussion_id, ...)`（`discussions.py:313-324`）取分页，并且 `_ensure_discussion_visible` 已经读过这一行（`discussions.py:308` → `repositories.get_by_id`）。
- **问题**：
  1. 子帖被取两遍：`services.py:272` 取最新 2 条（`createdAt desc`），`discussions.py:313` 取同一批的最前 2 条（前端传 `sort_order=asc`，`DiscussionItem.vue:181-183`），加上两次 count（`count_children` 与 `find_all` 里的 count）—— 一次详情请求里同一批子行与同一个计数各算两遍。
  2. `DELETE /discussions/{discussionId}`（`discussions.py:399-416`）更亏：`_ensure_discussion_visible`（2 条）→ `service.get_discussion`（`routes:411`，为了读 `sender.id`！）建整个 DTO（子树 + reaction + user map，约 8 条）→ `soft_delete` 里 `get_by_id` 再读一次（`repositories.py:158`）→ 约 12 条。
  3. `PATCH`（`discussions.py:338-359`）同一行读三次：`routes:353` 的 `_ensure_discussion_visible`、`services.py:158`、`services.py:169` 的 `update_content`。
- **优化**：把「谁在改/删」与「DTO 长什么样」分开。
  - `services.py` 加一个只回答归属的读法，供 PATCH/DELETE 用：

    ```python
    async def assert_author(self, discussion_id: int, user_id: int) -> None:
        """只回答「这条是不是他写的」，不建 DTO。DELETE 只要 sender.id 却
        建了整棵子树，是这一处 12 条 SQL 的原因。"""
        entity = await self._repo.get_by_id(discussion_id)
        if entity is None:
            raise NotFoundError(
                "Resource discussion not found",
                data={"type": "discussion", "id": discussion_id},
            )
        if entity.sender_id != user_id:
            raise ForbiddenError("Only the author can delete this discussion")
    ```

    `delete_discussion` 路由改成 `await service.assert_author(discussion_id, auth_user.user_id)`（`_ensure_discussion_visible` 保留，它答的是「这块板他看不看得见」，与归属是两件事）。
  - `update_discussion`/`soft_delete` 里把已加载的 entity 传进去，别让 `update_content`（`repositories.py:146-155`）与 `soft_delete`（`:157-163`）各自再 `get_by_id` 一次：加 `update_content(entity, content_json)` 重载或 `entity` 参数。
  - `_build_discussion_dto` 的 `sub_info` 与路由的分页合并：路由已经拿到了分页结果，把 `examples` 直接取自该页的前 2 条（或让 `get_discussion` 接受 `sub_examples` 注入）。
- **契约**：响应形状不变（`discussion.subDiscussions.{count,examples}` 与 `subDiscussions.{discussions,page}` 字段与顺序不变；`examples` 的取法从 `createdAt desc` 改成「分页的前 2 条」，在 `sort_order=asc` 下 examples 会由最新两条变成最早两条 —— 这一条**要前端确认**，`Discussion.vue` 目前只读 `count`，`examples` 的消费方是 `DiscussionDetail.vue`（注释 `discussions.py:293-298` 说明它是为了折叠展示）。若不想动值，就保留 examples 的独立查询，只做上面的 1、2 两项收益。状态码不变。
- **测试**：`tests/integration/test_discussion.py`：`test_delete_discussion_only_loads_one_row`（用 `counting_sql()` 断言 DELETE 的 SQL 条数 ≤ 5，且 404/403 分支与 `test_discussion_board_authz.py` 一致）；`test_get_discussion_fetches_sub_replies_once`（断言 `SELECT ... FROM discussion WHERE parent_id` 只出现 1 次）。

### POST /attachments + GET /attachments/{attachmentId}/download + POST /materials

- **现状**：`routes/attachments.py:96-124` 上传 → `AttachmentService.upload`（`domain/attachment/services.py:44-87`）；下载 `routes/attachments.py:155-179`；素材上传 `routes/materials.py:35-85`。
- **问题**：
  1. `services.py:64-65`：`file_content = await asyncio.to_thread(file.read)` 只为拿 `len()`，整个文件（`frontend/nginx.conf:27` 的 `client_max_body_size 100M`）进内存；紧接着 `compute_file_hash(file)`（`storage.py:276-281`）是分块读的，那条路本来就是对的。上传路由没有任何字节数上限。
  2. 下载：`routes/attachments.py:161-166` 先用 `service.get(attachmentId)` 判权，`:167` 的 `service.download(attachmentId)` 内部又 `self.get(attachment_id)`（`services.py:147`）—— 同一行两次 SELECT；`storage.download` 再整体取回内存。
  3. `routes/materials.py:44` 的 `file_content = await file.read()` 之后 `:58` 用 `io.BytesIO(file_content)` 再喂给存储 —— 同样 100MB 上限下最坏 100MB 常驻，而且 `import io` 在函数体里（`:54`）。
- **优化**：

  ```python
  # domain/attachment/services.py —— 量大小不该把文件读出来
  import os

  def _stream_size(file: BinaryIO) -> int:
      """文件当前有多少字节，不把它读进内存。Starlette 的 UploadFile.file 是
      SpooledTemporaryFile，seek/tell 都可用。"""
      here = file.tell()
      file.seek(0, os.SEEK_END)
      size = file.tell()
      file.seek(here)
      return size

  # upload() 里替换 services.py:64-65
  file_size = await asyncio.to_thread(_stream_size, file)
  ```

  ```python
  # routes/attachments.py —— 判权与取字节共用一次读
  async def download_attachment(...) -> Response:
      attachment = await service.get(attachmentId)      # 只读一次
      await _ensure_may_read(
          attachment=attachment, user_id=auth_user.user_id,
          service=service, task_attachments=task_attachments,
      )
      content, filename, content_type = await service.download_bytes(attachment)
      ...

  # domain/attachment/services.py —— 收一个已加载的行
  async def download_bytes(self, attachment: Attachment) -> tuple[bytes, str, str]:
      """已经拿在手上的那一行，别再问一次仓库（原 download 的 self.get 去掉）。"""
      storage_key = attachment.meta.get("storageKey")
      if not storage_key:
          raise NotFoundError("Attachment storage key not found")
      content = await self._storage.download(storage_key)
      if content is None:
          raise NotFoundError("Attachment file not found in storage")
      return (
          content,
          attachment.meta.get("filename", f"attachment_{attachment.id}"),
          attachment.meta.get("contentType", "application/octet-stream"),
      )
  ```

  ```python
  # routes/materials.py —— 直接把手上的流交给存储，不再自己搬一遍字节
  # 删掉 :54 的 import io 与 :44/:58 的整体读
  file.file.seek(0)
  file_size = await asyncio.to_thread(_stream_size, file.file)
  storage_key = generate_storage_key(file_name, prefix=f"materials/{type}")
  url = await storage.upload(file.file, storage_key, file_mime)
  ```

  另外给两个上传路由加一条显式上限（与 nginx 的 100M 对齐或更小），超限答 413/422 而不是把内存押上去：

  ```python
  MAX_UPLOAD_BYTES = 100 * 1024 * 1024
  file.file.seek(0, os.SEEK_END)
  if file.file.tell() > MAX_UPLOAD_BYTES:
      raise UnprocessableEntityError("文件超过 100MB")
  file.file.seek(0)
  ```
- **契约**：上传与下载的响应形状、状态码都不变（201 / 200 + `Content-Disposition` 不变）。`meta.size` 的值不变（`_stream_size` 给的同样是字节数）。新增 413/422 只会在**今天必失败**（内存耗尽/超时）的请求上出现。无迁移。
- **测试**：`tests/unit/test_attachment_service.py` 加 `test_upload_does_not_read_the_whole_file`（给一个记录 `read()` 调用的 fake file，断言只发生分块读）；`tests/integration/test_attachments.py` 加 `test_download_reads_the_row_once`（`counting_sql()` 里 `FROM attachment` 只出现一次）与 `test_upload_rejects_over_the_limit`。

### GET /knowledge（含 POST /knowledge、PATCH、DELETE、upvote 四处小改）

- **现状**：`routes/knowledge.py:99-153`；仓库 `domain/knowledge/repositories.py:117-209`。
- **问题**（四条独立、都便宜）：
  1. **排序参数被静默忽略**：handler 只声明 `sortBy`/`sortOrder`（`knowledge.py:111-112`），前端与遗留契约快照发的是 `sort_by`/`sort_order`（`frontend/src/types/knowledges.ts:56-57`、`api_catalog.json` 的 `/knowledge` GET `parameters`）→ 参数被丢弃、恒定 `createdAt desc`，**不报错也不生效**。
  2. **过滤条件抄了两份**：`repositories.py:130-160`（数据）与 `:176-205`（计数）逐字重复 `project_id/type/query/labels` 四段，将来加一个过滤条件只改一处就会让 `total` 与 items 不一致。
  3. **`DELETE /knowledge/{knowledgeId}` 的信封缺 `data`**：`knowledge.py:208-211` 回 `{"code":200,"message":"OK"}`，而同模块其余全部是 `{"code","message","data"}`（`response.ok()` 也总是带 `data`），契约测试 `tests/contract/test_knowledge_contract.py:17` 断言的正是三键齐全。
  4. **`POST /knowledge/{id}/upvote` 的竞态**：`repositories.py:272-288` 先 SELECT 再 INSERT，而表上有 `UniqueConstraint("knowledge_id","user_id", name="uq_knowledge_upvote")`（`models.py:96-98`）。两个并发 POST 都读到「没有」→ 都 INSERT → 一个是 `IntegrityError` → 走 `_handle_unexpected` 变 500（`errors.py:434-457`），而正确答案是 400「已点过」。
  5. **长度校验缺失**：`name` 列是 `String(255)`（`models.py:44`）、`KnowledgeLabel.label` 是 `String(50)`（`models.py:70`），而 `create_knowledge`（`knowledge.py:59-60`）与 `PatchKnowledgeRequest.name`（`knowledge.py:20`）都不限长，`update_labels` 也不截断。`{"name": "x"*300}` 或 51 字的 label 在 PostgreSQL 上是 `StringDataRightTruncation` → 500，不是 400。
- **优化**：

  ```python
  # 1) knowledge.py:111-112 —— 与 /discussions 同一做法，两种拼写都认
  sortBy: str = Query(default="createdAt", alias="sortBy"),
  sortOrder: str = Query(default="desc", alias="sortOrder"),
  sort_by: str | None = Query(default=None, alias="sort_by"),
  sort_order: str | None = Query(default=None, alias="sort_order"),
  ...
  effective_sort_by = sort_by or sortBy
  effective_sort_order = sort_order or sortOrder
  if effective_sort_by not in {"createdAt", "updatedAt"}:
      raise BadRequestError(f"Invalid sortBy: {effective_sort_by}")
  if effective_sort_order.lower() not in {"asc", "desc"}:
      raise BadRequestError(f"Invalid sortOrder: {effective_sort_order}")
  ```

  ```python
  # 2) repositories.py —— 过滤条件一处，数据与计数共用
  def _filters(self, *, team_id, project_id, type_, query, labels) -> list:
      where = [Knowledge.team_id == team_id, Knowledge.deleted_at.is_(None)]
      if project_id is not None:
          where.append(Knowledge.project_id == project_id)
      if type_ is not None:
          where.append(Knowledge.type == type_)
      if query:
          pattern = f"%{query.lower()}%"
          where.append(
              func.lower(Knowledge.name).ilike(pattern)
              | func.lower(Knowledge.description).ilike(pattern)
          )
      if labels:
          normalized = sorted({lbl.strip() for lbl in labels if lbl.strip()})
          if normalized:
              where.append(Knowledge.id.in_(self._label_subquery(normalized)))
      return where

  # find_all 里两处改成
  stmt = select(Knowledge).where(*self._filters(...))
  count_stmt = select(func.count(Knowledge.id)).where(*self._filters(...))
  ```

  ```python
  # 3) knowledge.py:208-211
  return {"code": 200, "message": "OK", "data": None}

  # 4) repositories.py:add_upvote —— 撞约束就是答案，不是故障
  async def add_upvote(self, knowledge_id: int, user_id: int) -> None:
      now = datetime.now(UTC)
      try:
          async with self._session.begin_nested():
              self._session.add(
                  KnowledgeUpvote(
                      knowledge_id=knowledge_id, user_id=user_id,
                      created_at=now, updated_at=now,
                  )
              )
              await self._session.flush()
      except IntegrityError as exc:
          raise BadRequestError("Already upvoted") from exc

  # 5) 长度在 schema 边界拒掉（create 与 patch 同一份）
  class PatchKnowledgeRequest(BaseModel):
      name: str | None = Field(default=None, max_length=255)
      labels: list[str] | None = Field(default=None)
      # ...
      @field_validator("labels")
      @classmethod
      def _labels(cls, v):
          for label in v or []:
              if len(label) > 50:
                  raise ValueError("label 最长 50 字符")
          return v
  ```

  `create_knowledge` 侧把同样的上限放进 `Body` 校验（或一处 `_validate_labels()`），`name` 用 `Field(max_length=255)` 的 pydantic 模型替代裸 `dict`。
- **契约**：1、3 是纯修正（前端本来就期望这样），会让 `sort_by=updatedAt` **开始生效** —— 值得在变更说明里点明「以前静默忽略、现在真的会换排序」。4 会把并发下的一次 500 变成 400。5 会把「超长 500」变成 422（`validation_exception_handler` 统一给 400，`errors.py:235-249`）。信封形状不变。无迁移。
- **测试**：`tests/contract/test_knowledge_contract.py` 加 `test_delete_knowledge_envelope_has_data`（`set(body) == {"code","message","data"}`）；`tests/integration/test_knowledge.py` 加 `test_list_honours_snake_case_sort`（`sort_by=updatedAt&sort_order=asc` 下先改一条旧记录的 name，断言它排最前）、`test_overlong_name_is_refused`（300 字 name → 400/422，不是 500）、`test_concurrent_upvote_is_a_400_not_a_500`。

### GET /memory + PUT /memory/files + POST /memory/files/delete

- **现状**：`routes/memory.py:57-124`、`routes/memory_files.py:155-269`。
- **问题**：
  1. `GET /memory`（`memory.py:117-123`）没有 limit/offset，`_entry_out` 逐行序列化，`page(items, len(items))` 的 `total` 恒等于返回行数、`pageSize` 也没有。memory_entries 已无写入方（`domain/memory/store.py:3-8` 写明），但历史行 + 多个 agent pool 仍可能上百条，且这个列表直接进界面面板。
  2. `PUT /memory/files`（`memory_files.py:167-197`）与 `POST /memory/files/delete`（`:237-258`）有约 30 行**逐字重复**的 body 解析（`project_id` 的 UUID 解析、`scope`、`owner_handle`、`_writable`、`_checked_path`、`version` 转 int）—— 两条路的拒绝文案必须一字不差，现在靠两处手抄维持。
  3. `DELETE` 语义的删除走 `POST /memory/files/delete`（动词进路径）。
- **优化**：

  ```python
  # memory.py —— 与列表端点同一套分页（前端 usePaging 认 pageStart/pageSize）
  @router.get("")
  async def list_memory(
      db: DbSession,
      resolver: ActorResolverDep,
      project_id: uuid.UUID,
      user_handle: str | None = None,
      agent_handle: str | None = None,
      page_start: int | None = Query(default=None, ge=0, alias="pageStart"),
      page_size: int = Query(default=50, ge=1, le=200, alias="pageSize"),
  ) -> dict:
      ...
      offset = page_start or 0
      total = await db.scalar(
          select(func.count(MemoryEntry.id)).where(cond, live_entries())
      )
      rows = (
          await db.scalars(
              select(MemoryEntry)
              .where(cond, live_entries())
              .order_by(MemoryEntry.created_at.desc())
              .limit(page_size)
              .offset(offset)
          )
      ).all()
      return ok(
          {
              "data": [_entry_out(e) for e in rows],
              "total": int(total or 0),
              "pageStart": offset,
              "pageSize": len(rows),
              "hasMore": offset + len(rows) < int(total or 0),
              "nextStart": offset + len(rows) if offset + len(rows) < int(total or 0) else None,
          }
      )
  ```

  ```python
  # memory_files.py —— 两处解析收成一处
  @dataclass(frozen=True)
  class _MemoryWrite:
      project_id: uuid.UUID
      scope: MemoryFileScope
      owner: str | None
      path: str
      version: int | None

  async def _parse_write(
      db: AsyncSession, resolver: ActorResolverDep, body: dict, *, require_path: bool
  ) -> tuple[_MemoryWrite, str]:
      """写与删共用的 body 解析：两条路的拒绝文案只有一份。"""
      try:
          project_id = uuid.UUID(str(body.get("project_id") or ""))
      except ValueError as exc:
          raise ValidationError("project_id 无效") from exc
      which = _scope_of(str(body.get("scope") or "team"))
      owner = _owner_of(which, (body.get("owner_handle") or "").strip() or None)
      actor = await _writable(db, resolver, project_id, owner)
      raw_path = str(body.get("path") or "").strip()
      if require_path and not raw_path:
          raise ValidationError("path 不能为空")
      path = _checked_path(raw_path)
      raw_version = body.get("version")
      expected: int | None = None
      if raw_version is not None:
          try:
              expected = int(raw_version)
          except (TypeError, ValueError) as exc:
              raise ValidationError("version 必须是整数") from exc
      return _MemoryWrite(project_id, which, owner, path, expected), actor
  ```

  3. 新增 `DELETE /memory/files`（body 带 `project_id/scope/owner_handle/path/version`）把逻辑指向同一个 `_parse_write`，旧的 `POST /memory/files/delete` 保留为一个不写进 schema 的兼容入口（`include_in_schema=False`），一轮之后再删。
- **契约**：`GET /memory` 的 `page` 是**新增字段**（旧的 `{data,total}` 保留，`data` 变成有界的一页）—— 这是行为变化：以前一次给全、现在默认 50 条，界面若不分页会少看。建议 `page_size` 默认给大一点（200）并在变更说明里写清，或先只加分页参数、默认仍全量再收紧。`DELETE /memory/files` 是纯新增；`POST /memory/files/delete` 不变。无迁移。
- **测试**：`tests/integration/test_memory_files_are_shared_by_scope.py` 加 `test_delete_needs_the_same_scope_owner_and_version_as_write`（复用同一组越权矩阵，但打新路径，证明两条路的拒绝一字不差）；`tests/unit/` 加一个 `_parse_write` 的单测（`project_id="x"`、`version="abc"`、`scope="nope"`、`private` 缺 owner 四种拒绝）。

### GET /topics/{topic_id}/files/raw、GET .../files/revisions

- **现状**：`routes/room_files.py:85-101`（raw）、`:104-115`（revisions）、`domain/project/room_files.py:253-266`。
- **问题**：
  1. `room_file_raw` 每次都把整份文件从盘上读出来（`:98-100` 的 `library.read_room_file`），并以 `Cache-Control: no-store` 回包；而它**已经在回**内容哈希（`_bytes_response` 的 `X-Cheese-Version`，`:70-82`）。`cheese pull` 是轮询式的（`routes/room_files.py:89` 的注释），于是每次轮询都全量搬一遍字节 —— GitHub 用 `ETag` + `If-None-Match`（https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api ）正是为了这一条。
  2. `list_file_revisions`（`room_files.py:253-266`）无 limit：某路径的全部历史一次返回。房间文件被 agent 反复保存时 revision 会累积（`save_room_file` 每次写都留一条），`revision_out` 每条 11 个字段。
- **优化**：

  ```python
  # routes/room_files.py —— 条件请求：版本号就是内容哈希，用现成的共用件
  from app.api.conditional import if_none_match_hits

  @router.get("/topics/{topic_id}/files/raw")
  async def room_file_raw(
      topic_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep,
      if_none_match: Annotated[str | None, Header()] = None,
  ) -> Response:
      place, _ = await _in_room(db, resolver, topic_id)
      clean = _clean_artifact_path(path)
      name = library.library_name(clean)
      read = library.read_library_file if name is not None else None
      data = await asyncio.to_thread(
          read if read else library.read_room_file,
          *( (place.project_id, name) if read else (place.project_id, place.room_id, clean) ),
      )
      version = content_version(data)
      if if_none_match and if_none_match_hits(if_none_match, version):
          return Response(status_code=304, headers={"X-Cheese-Version": version})
      return _bytes_response(data, clean.rsplit("/", 1)[-1], version)
  ```

  `_bytes_response` 里补一条 `"ETag": f'"{version}"'`（`Cache-Control: no-store` 保留：写入路径的语义不变，条件请求是客户端主动发 `If-None-Match` 才生效的）。
  2. `list_revisions` 加 `limit`/`offset`，默认 `limit=50` 并回 `hasMore`：

  ```python
  # domain/project/room_files.py
  async def list_revisions(
      session: AsyncSession, room_id: uuid.UUID, path: str,
      *, limit: int = 50, offset: int = 0,
  ) -> tuple[list[RoomFileRevision], bool]:
      stmt = (
          select(RoomFileRevision)
          .where(RoomFileRevision.room_id == room_id, RoomFileRevision.path == path)
          .order_by(RoomFileRevision.seq.desc())
          .limit(limit + 1).offset(offset)
      )
      rows = list((await session.execute(stmt)).scalars().all())
      return rows[:limit], len(rows) > limit
  ```

  路由侧 `page_start` 复用 `pageStart`（offset）语义，与 `/discussions` 一致。
- **契约**：`raw` 新增 304 只在客户端发 `If-None-Match` 时出现，是纯增量（GitHub 的 304 也是这么约定的）；响应头新增 `ETag`。`revisions` 加 `limit` 后**行为变化**：以前一次给全部，现在默认 50 条 —— 前端 `room_files` 的调用方需要一起改（`cli` 侧同名工具）。若不想打破调用方，就把 `limit` 默认设成 `None`（不分页）并只提供可选的 `pageSize`，收益留给有意识的调用方。
- **测试**：`tests/integration/test_room_file_history.py` 加 `test_raw_supports_if_none_match`（第二次带 `If-None-Match` 得到 304 且 body 为空）与 `test_revisions_are_bounded`（造 60 版，断言默认只回 50 + `hasMore`）。

### GET /legal/documents、/legal/documents/{document}、/legal/documents/{document}/versions/{version}

- **现状**：`routes/legal.py:58-86`，正文来自 `@cache` 的 `text_of`（`domain/legal/documents.py:75-81`），`_full` 里已经算了 `sha256`。
- **问题**：三份文档是**随部署固定**的内容，进程内已缓存，每个页面/每次刷新仍然完整回一遍正文（`content` 是整篇 markdown），没有任何缓存协商。平台里已经有共用件与现成写法（`app/api/conditional.py:42` 的 `etag_for_json`、`routes/admin_members.py:52-69` 的 200/304 双形状），这三条没有用上。
- **优化**：`_full` 返回的 `sha256` 就是内容哈希，直接当 ETag（不用再算一遍 body）：

  ```python
  @router.get("/legal/documents/{document}", summary="Current text of a document")
  async def get_document(
      document: Annotated[str, Path()],
      response: Response,
      if_none_match: Annotated[str | None, Header()] = None,
  ) -> dict | Response:
      doc = _document_or_404(document)
      data = _full(doc, doc.current)
      headers = {"ETag": f'"{data["sha256"]}"', "Cache-Control": "public, max-age=300"}
      if if_none_match and if_none_match_hits(if_none_match, data["sha256"]):
          return Response(status_code=304, headers=headers)
      response.headers.update(headers)
      return {"code": 200, "message": "OK", "data": data}
  ```

  `/legal/documents`（列表）用 `etag_for_json(data)` 同一形状；`versions/{version}` 同 `get_document`。
- **契约**：200 的信封不变（多两个响应头），304 只在客户端发 `If-None-Match` 时出现。无迁移。
- **测试**：`tests/integration/test_legal_consent.py` 加 `test_document_etag_round_trip`（200 拿 `ETag` → 带 `If-None-Match` 再请求 → 304 空 body，且 304 上也带 `ETag`）。

### POST /docs/agent/read、POST /docs/ask

- **现状**：`routes/docs_site.py:262-278` → `library.read_page`（`domain/docs_site/library.py:110-135`）；`routes/docs_site.py:124-188`（ask）。
- **问题**：
  1. `read_page` 每次请求都新建一个 `httpx.AsyncClient` 拉一次页面（10s 超时），同一份**静态**文档被反复拉；而同一个模块的检索索引有 10 分钟缓存（`domain/docs_site/retrieval.py:162-213`，注释写明「docs changing is rare」）。缓存这一条标准已经由同文件自己立了，页面这条没有。
  2. `_refuse`（`docs_site.py:117-121`）回 `{"code": status, "message": message}`，而平台其余所有错误回的是 `{"code","message","error":{"name","message","data","retryable"}}`（`core/errors.py:35-45`）—— 429/503 的客户端要按两种形状解析。
- **优化**：

  ```python
  # domain/docs_site/library.py —— 与 IndexSource 同一形状的小缓存
  _PAGE_TTL = 300.0
  _pages: dict[tuple[str, bool], tuple[float, str | None]] = {}

  async def read_page(page: str, *, dev: bool, transport=None) -> str | None:
      slug = page_slug(page)
      ...  # 校验与 slug 处理不变
      key = (slug, dev)
      hit = _pages.get(key)
      if hit is not None and time.monotonic() - hit[0] < _PAGE_TTL:
          return hit[1]
      ...  # 原 HTTP 读取
      _pages[key] = (time.monotonic(), text)
      return text
  ```

  （进程内字典即可：文档是随部署更新的静态文件，与 `IndexSource` 的取舍一致；`escape`/多进程部署下最坏是多拉几次，不会错。）

  ```python
  # routes/docs_site.py —— 与平台错误体同形
  def _refuse(status: int, message: str, retry_after: int = 0) -> JSONResponse:
      headers = {"Retry-After": str(retry_after)} if retry_after else {}
      body = {
          "code": status,
          "message": message,
          "error": {"name": "DocsAskRefused", "message": message, "data": None, "retryable": True},
      }
      return JSONResponse(body, status_code=status, headers=headers)
  ```

- **契约**：`_refuse` 的 body 多一个 `error` 键（前端若按 `data`/`message` 读不受影响；这是**加字段**不是换 shape）；`read_page` 的缓存会让「改了文档立刻可见」变成最多 5 分钟后可见 —— 与检索索引 10 分钟的既有取舍同类，值得在变更说明里写清。
- **测试**：`tests/integration/test_docs_for_agents.py` 加 `test_read_page_is_cached`（monkeypatch `httpx.AsyncClient.get` 计数，连打两次断言只拉一次）；`tests/integration/test_docs_site.py` 加 `test_ask_refusal_uses_the_platform_error_shape`（断言 429 的 body 里有 `error.name`）。

### GET /material-bundles/{bundle_id}、POST /material-bundles、PATCH /material-bundles/{bundle_id}

- **现状**：`domain/materials/services.py:220-233`、`:235-259`、`:261-293`。
- **问题**：
  1. `get_bundle_detail`（`:226-229`）逐 id `self._material_repo.get_by_id(mid)` —— 一个包 20 个课件就是 20 条 SELECT，而仓库里已经有现成的 `list_by_ids`（`repositories.py:21-37`，一条 `IN` 且保序）。
  2. `create_bundle`（`:244-247`）逐个 id 查存在性；`:255-258` 再逐个 `add_material_to_bundle`，而后者自己还要 SELECT 一次（`repositories.py:190-200`）→ 每个 id 两条。
  3. `update_bundle`（`:277-288`）对差集里每个 id 各一次 add/remove，每次一条 SELECT。
- **优化**：

  ```python
  # get_bundle_detail —— 一条代替 N 条
  async def get_bundle_detail(self, bundle_id: int) -> dict:
      bundle = await self._repo.get_by_id(bundle_id)
      if bundle is None:
          raise NotFoundError("Material bundle not found", data={"id": bundle_id})
      material_ids = await self._repo.get_materials_for_bundle(bundle_id)
      materials = [
          _material_to_dto(m)
          for m in await self._material_repo.list_by_ids(material_ids)  # 已存在，保序
      ]
      dto = _bundle_to_dto(bundle)
      dto["materials"] = materials
      dto["creator"] = {"id": bundle.creator_id}
      return dto

  # create_bundle —— 一次校验、一次插入
  async def create_bundle(self, *, title, content, creator_id, material_ids=None):
      ids = list(dict.fromkeys(material_ids or []))  # 去重且保序
      if ids:
          found = {m.id for m in await self._material_repo.list_by_ids(ids)}
          missing = [mid for mid in ids if mid not in found]
          if missing:
              raise NotFoundError("Material not found", data={"id": missing[0], "missingIds": missing})
      bundle = await self._repo.create(title=title, content=content, creator_id=creator_id)
      if ids:
          await self._repo.add_materials_to_bundle(bundle_id=bundle.id, material_ids=ids)
      return {"id": bundle.id}
  ```

  ```python
  # repositories.py —— 批量挂钩，不再逐个 SELECT
  async def add_materials_to_bundle(self, *, bundle_id: int, material_ids: list[int]) -> None:
      if not material_ids:
          return
      existing = set(
          (await self._session.execute(
              select(MaterialBundleRelation.material_id).where(
                  MaterialBundleRelation.bundle_id == bundle_id,
                  MaterialBundleRelation.material_id.in_(material_ids),
              )
          )).scalars().all()
      )
      for mid in material_ids:
          if mid not in existing:
              self._session.add(MaterialBundleRelation(bundle_id=bundle_id, material_id=mid))
      await self._session.flush()
  ```

  `update_bundle` 用同一个批量方法 + 一条 `delete(...).where(bundle_id==, material_id.in_(removed))` 取代逐个删除。
- **契约**：响应形状与状态码完全不变（`data.materialBundle.materials[]` 的**顺序**现在由 `list_by_ids` 保证与 `materialIds` 一致，这正是 `test_materials.py:493-505` 断言的「三个 id 都在」的超集，不会更松）。`create_bundle` 的 404 从「遇到的第一个缺失 id」变成「第一个缺失 id」—— 同一个值（`missing[0]` 按传入顺序取），文案不变。无迁移。
- **测试**：`tests/integration/test_materials.py` 的 `TestMaterialBundles*` 保留（它们是行为回归），另加 `test_bundle_detail_round_trips_do_not_grow_with_materials`（`counting_sql()`：3 个课件与 30 个课件的详情 SQL 条数相等）。

## 三、模块级建议

**M1. 请求侧参数拼写：以遗留契约快照为准，两处补齐。**
适用：`GET /material-bundles`（`page_start`/`page_size`）、`GET /knowledge`（`sortBy`/`sortOrder`）、`GET /discussions`（`parentId`）。
做法：统一成「camelCase 为主 + snake_case 别名」，与本组 `/discussions` 现有写法一致：
```python
pageStart: int | None = Query(default=None, ge=0, alias="pageStart"),
pageSize: int = Query(default=20, ge=1, le=100, alias="pageSize"),
page_start: int | None = Query(default=None, ge=0, alias="page_start"),
page_size: int | None = Query(default=None, ge=1, le=100, alias="page_size"),
```
为什么：项目自己的契约快照（`backend/tests/contract/api_catalog.json`，由 `scripts/generate_api_catalog.py` 从 `cheese-backend-nt/design/API/NT-API.yml` 生成）对 `/knowledge` 写的就是 `sort_by`/`sort_order` + `pageStart`/`pageSize`，前端类型（`frontend/src/types/knowledges.ts:54-57`）与它一致。GitHub 对「客户端写错参数」的答案是**忽略**（https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api —— 未知 query 参数不报错），所以「静默忽略」本身不算错，错在**平台自己的两个门面（快照 vs handler）对不上**，后果是排序功能静默失效。

**M2. `pageStart` 的两种语义要在文档里分开。**
适用：`/discussions*`、`/knowledge`（`pageStart` = **offset**，`services.py:127`、`knowledge.py:125`）与 `/material-bundles`（`pageStart` = **游标 id**，`materials/services.py:202`）。
问题：前端只有一个 `Page<C>` 类型与一个 `usePaging` 钩子（`frontend/src/utils/paging.ts:8-53`），它对 `pageStart` 的解释是「上一页给的 `nextStart`」，两种语义下都能跑，但**跨端点的行为不同**（offset 在并发写入下会漏行/重行，游标不会）。
做法：不动语义（返工成本大于收益），但在 OpenAPI 描述里写清 `pageStart` 的含义，并给 `Query(..., description=...)`；新端点一律用游标。GitHub 的官方立场是游标/`Link` 头（https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api ），Stripe 也只用游标（`starting_after`/`ending_before`）—— 所以新接口不要再引入 offset。

**M3. 批量 DTO 组装应成为本组的公共依赖，并且用 SQL 计数钉住。**
适用：`/discussions*`（本报告第一条）、`/material-bundles/{id}`、`/knowledge`（已经做对了：`KnowledgeService._build_dtos` 是模板）。
做法：一个域的「列表 → DTO」只有两条路：批量（`_build_dtos`）与单条（`_build_dto`），列表路径**不许**走单条。测试用现成的 `tests/integration/test_hot_path_queries.py:29-45` 的 `counting_sql()`：**断言 SQL 条数不随页面行数增长**，而不是断言绝对条数 —— 这条形状是这个仓库已经在用的判据（`test_roster_round_trips_do_not_grow_with_the_roster`），把它复制到 `/discussions`、`/material-bundles` 即可。
为什么：functional 测试对 N+1 完全无感（该文件开头的注释已经说了这件事）；GitHub 也对列表端点设了硬上限（`per_page` 最大 100、静默夹取）。

**M4. 条件请求本组还有三处没吃上现成的共用件。**
适用：`GET /legal/documents*`（内容随部署固定）、`GET /topics/{topic_id}/files/raw`（已有内容版本号）、`GET /attachments/{attachmentId}/download` 与 `GET /office-editor/files/{link}`（字节流，可加 `ETag` 并可考虑 `Range`）。
做法：用 `app/api/conditional.py` 的两个函数，照 `routes/admin_members.py:52-69` 的「200 带头 / 304 空体」双形状写；`ETag` 用**已有**的哈希（legal 的 `sha256`、room file 的 `content_version`、attachment 的 `meta.hash`），不要重新算 body。
为什么：GitHub 的条件请求与 `304` 是 REST 惯例的正典（https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api ），本仓库也已经把它做成共用件了 —— 缺的不是能力，是覆盖。

**M5. 错误体与信封的两处小口子。**
适用：`POST /docs/ask` 的 `_refuse`（缺 `error{}`）；`DELETE /knowledge/{knowledgeId}`（缺 `data`）。
做法：错误一律走 `core/errors.py` 的 `BaseError` 家族或至少复刻 `to_response_body()` 的三段形状；成功一律走 `app/api/response.ok()`（它会保证 `data` 存在）。
为什么：`routes/docs_site.py:117` 是平台里少见的「自己拼错误体」；`routes/knowledge.py:208` 是少见的「成功体少一键」，而契约测试 `tests/contract/test_knowledge_contract.py:17` 断言的正是三键齐全 —— 两处都是同一类漂移，`ok()` / `BaseError` 就是为了根治它才存在的。

**M6. 输入校验要落在 schema 边界，不要留给数据库。**
适用：`POST /knowledge`、`PATCH /knowledge/{id}`（裸 `dict` + 无长度校验 → `String(255)`/`String(50)` 列溢出成 500）、`POST /discussions`、`PATCH /discussions/{id}`、`POST /material-bundles`、`PATCH /material-bundles/{id}`（都是裸 `dict` + 手写 `payload.get`）。
问题：本组有 8 个写接口的 body 是 `payload: dict = Body(...)`，字段名、类型、长度全靠手写 `isinstance` —— 校验与列定义（`models.py`）之间没有任何联系，溢出/漏字段的失败面是 500 而不是 400（`core/errors.py:434-457`）。
做法：给每个写接口一个 pydantic 模型（`PatchKnowledgeRequest` 就是现成的正面例子），长度上限从列定义抄一次；`tests/unit/` 加一条「每个 `Body(...)` 都不该是裸 dict」的守卫测试（本仓库已有 `tests/unit/test_domain_import_guard.py` 这种守卫式测试的先例）。
为什么：GitHub 的 422（`message` + `errors[]`）与 Stripe 的 `invalid_request_error` 都是先校验、再落库；把 500 变成 400 是**可观测性**的改善（`_handle_unexpected` 会把 traceback 记进日志，噪声会淹掉真事故）。

**M7.（给前端的连带项，不必改后端）** 讨论列表页每行带附件图时，前端会对每个 `attachmentId` 各打一次 `GET /attachments/{id}`（`frontend/src/views/spaces/detail/Discussions.vue:218`）。一页 20 行最多 20 次往返，而那时的 `data.meta.width/height` 只用来算一个宽高比。后端这边 `GET /attachments/{id}` 的判权是对的、不要松开；要省就省在前端（并发去重/按需加载），或让列表 DTO 直接带上图的实际 URL（`content` 里嵌的 attachment id 在服务端本来就是可解析的）。
