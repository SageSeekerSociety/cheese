# Spaces 组 API 逐接口分析（`backend/app/api/routes/spaces.py`，67 个接口）

分析范围：接口清单 `/tmp/api-review/out/spaces.list.md` 的 67 条，全部逐个读过 handler，
并追进 `app/domain/space/*`、`app/domain/task/*`、`app/domain/teaching/*`、
`app/domain/user/*`、`app/auth/*` 的具体查询。只读仓库，未改任何文件、未跑测试。

判断标准：优先 GitHub REST 惯例（分页 Link 头、错误体 `message`/`documentation_url`、
条件请求 ETag、POST 幂等），其次 Stripe/Twilio/Slack/Discord/OpenAI；查不到出处的一律注明
「业界通行，未逐条查证」。

一句话总览：这个模块的分页（`pageStart/pageSize/hasMore/nextStart/total`）、
错误信封（`code/message/data`、`BaseError`）、可见性策略（非成员 404、成员非管理员 403）
在**绝大多数接口上是一致的、有测试钉住的**；真正的问题集中在 5 处：
`GET /spaces` 的管理员 N+1、导出接口的逐人审计写入、`/submissions` 的逐行 N+1、
分析类接口的全量载入 + 无分页、学习看板的逐项目鉴权。

---

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | GET | `/spaces/{spaceId}` | 可优化 | 同一行 Space 一次请求里被独立读了 3 次（router 依赖 / 可见性 / 主体），可合并为 1 次。 |
| 2 | GET | `/spaces` | 可优化 | 管理员按人 hydrate，每位管理员 2 条 SQL，随页大小线性增长；同文件的批量写法没在这里用。 |
| 3 | POST | `/spaces` | 可优化 | `exists_by_name` 与 INSERT 之间存在竞态，`Space.name` 没有唯一索引兜底，可落进同名板。 |
| 4 | PATCH | `/spaces/{spaceId}` | 暂无 | 字段校验齐、`*_set` 语义清楚，仅受 #1 的重复读影响。 |
| 5 | DELETE | `/spaces/{spaceId}` | 暂无 | 204、软删、服务层判管理员，未见问题。 |
| 6 | POST | `/spaces/join` | 暂无 | 兑换是原子的、重复兑换幂等，返回整份 space 是前端 store 覆盖所需。 |
| 7 | POST | `/spaces/{spaceId}/enroll` | 待确认 | 幂等重入做得好，但响应里 `project.root_topic_id` 是 snake_case，与同响应其它 camelCase 字段不一致；需确认是否有意（前端 `cx_types.ts` 依赖它）。 |
| 8 | GET | `/spaces/{spaceId}/course-link` | 可优化 | GET 会写库（没有可用码时现造一张码），不满足「GET 安全/幂等」。 |
| 9 | GET | `/spaces/{spaceId}/members` | 可优化 | 无分页；且任何成员都能读到每条记录的 `inviteCode.code`（默认 50 次、永不过期），而签发/查看邀请码本身是管理员权限。 |
| 10 | GET | `/spaces/{spaceId}/course/roster` | 暂无 | 管理员门 + `_hydrate_people` 批量，未见问题。 |
| 11 | GET | `/spaces/{spaceId}/course/my-group` | 暂无 | 查自己那行，团队信息批量 hydrate，未见问题。 |
| 12 | POST | `/spaces/{spaceId}/members` | 暂无 | 先判可见性再判用户存在，随后批量 hydrate 一行，写法规范。 |
| 13 | DELETE | `/spaces/{spaceId}/members/{userId}` | 暂无 | 204、可见性在前、服务层判管理员，未见问题。 |
| 14 | POST | `/spaces/{spaceId}/leave` | 暂无 | 204、幂等，未见问题。 |
| 15 | GET | `/spaces/{spaceId}/invite-codes` | 暂无 | 管理员门在服务层（对不存在与无权限同为 403，不泄漏存在性），`createdBy` 批量。 |
| 16 | POST | `/spaces/{spaceId}/invite-codes` | 可优化 | `expiresAt` 直接 `datetime.fromtimestamp(ms/1000)`，越界值触发 500 而不是 422/400。 |
| 17 | PATCH | `/spaces/{spaceId}/invite-codes/{codeId}` | 可优化 | 同上越界 500；其余字段的 set/clear 语义清楚。 |
| 18 | DELETE | `/spaces/{spaceId}/invite-codes/{codeId}` | 暂无 | 204、撤销靠 `consume_use` 一处判据，未见问题。 |
| 19 | GET | `/spaces/{spaceId}/categories` | 暂无 | 可见性门 + 单次列表查询，未见问题。 |
| 20 | GET | `/spaces/{spaceId}/analytics/tasks` | 可优化 | 无分页，返回空间内全部任务；`hasPending*` 在 Python 里后过滤。 |
| 21 | GET | `/spaces/{spaceId}/publishers/participation` | 暂无 | 已标 `deprecated`，同 #25 的数据，单独看无新问题（成本随 #25 一并优化）。 |
| 22 | GET | `/spaces/{spaceId}/participants/export` | 暂无 | 已标 `deprecated`；CSV 公式注入已有测试覆盖（`test_csv_export_formula_injection.py`）。 |
| 23 | GET | `/spaces/{spaceId}/analytics/overview` | 可优化 | 每次请求 `_load_context` 全量载入本板 4 张表的行再在 Python 聚合。 |
| 24 | GET | `/spaces/{spaceId}/analytics/alerts` | 可优化 | 同上，为了几个提醒数字载入全板行。 |
| 25 | GET | `/spaces/{spaceId}/analytics/publishers` | 可优化 | 同上，发布者对比表也是全量载入后计算。 |
| 26 | GET | `/spaces/{spaceId}/analytics/participants` | 可优化 | 同上，另加真实身份解密，全在内存里做。 |
| 27 | GET | `/spaces/{spaceId}/analytics/participants/export` | 可优化 | 每个成员 3 条 SQL 写审计日志（2 次存在性检查 + 1 次 INSERT），人数即往返数。 |
| 28 | GET | `/spaces/{spaceId}/submissions` | 可优化 | 分页规范，但每行再发 2 条 SQL（entry + review）；`summary_for_space` 又把全板报名行全取出来只为 `len()`。 |
| 29 | GET | `.../analytics/learning/filters` | 可优化 | 逐个项目调 `may_read_project`，每项目最多 7~9 条 SELECT。 |
| 30 | GET | `.../analytics/learning/questions` | 可优化 | 同 #29 的逐项目鉴权循环。 |
| 31 | GET | `.../analytics/learning/queues` | 可优化 | 同 #29。 |
| 32 | POST | `.../analytics/learning/outline` | 可优化 | 同 #29。 |
| 33 | GET | `/spaces/{spaceId}/analytics/tasks/export` | 可优化 | 复用 `get_tasks`，因此继承 #20 的全量载入；导出全量本身合理，但可以在 SQL 侧聚合。 |
| 34 | GET | `/spaces/{spaceId}/analytics/publishers/export` | 可优化 | 同上。 |
| 35 | GET | `/spaces/{spaceId}/me/publishing` | 暂无 | 固定 5 条左右 SQL、范围是自己发的题，批量写法正确，未见问题。 |
| 36 | GET | `/spaces/{spaceId}/me/publishing/tasks` | 可优化 | 带任一筛选时会**再**发一条不带筛选的全量查询，就为了算可见性徽章。 |
| 37 | GET | `/spaces/{spaceId}/me/participating` | 暂无 | `_load_context` 是按用户载入、已分批，未见问题。 |
| 38 | GET | `/spaces/{spaceId}/me/participations` | 暂无 | 同上，范围是自己，未分页但天然有界。 |
| 39 | GET | `/spaces/{spaceId}/topics` | 可优化 | 声明 `le=100` 却静默截到 50，客户端要 100 只会拿到 50 且无从察觉。 |
| 40 | POST | `/spaces/{spaceId}/categories` | 暂无 | 服务层判管理员，201 + 单对象，未见问题。 |
| 41 | PATCH | `/spaces/{spaceId}/categories/{categoryId}` | 暂无 | `archived`/`archivedAt` 双写法兼容有注释说明，未见问题。 |
| 42 | GET | `/spaces/{spaceId}/categories/{categoryId}` | 暂无 | 可见性门 + 单行读，未见问题。 |
| 43 | DELETE | `/spaces/{spaceId}/categories/{categoryId}` | 暂无 | 未见问题。 |
| 44 | POST | `/spaces/{spaceId}/categories/{categoryId}/archive` | 暂无 | 未见问题。 |
| 45 | DELETE | `/spaces/{spaceId}/categories/{categoryId}/archive` | 暂无 | 未见问题。 |
| 46 | GET | `/spaces/{spaceId}/domain-groups` | 暂无 | 按设计对所有登录用户开放（`list_domain_groups` 注释），未见问题。 |
| 47 | POST | `/spaces/{spaceId}/domain-groups` | 暂无 | 服务层判管理员，未见问题。 |
| 48 | PATCH | `/spaces/{spaceId}/domain-groups/{groupId}` | 暂无 | 未见问题。 |
| 49 | DELETE | `/spaces/{spaceId}/domain-groups/{groupId}` | 暂无 | 未见问题。 |
| 50 | GET | `/spaces/{spaceId}/managers` | 暂无 | 可见性门，避免泄漏「谁在管一个你没被邀请的板」，未见问题。 |
| 51 | POST | `/spaces/{spaceId}/managers` | 暂无 | 角色字符串在路由校验，返回整份 payload 供前端 store 覆盖，未见问题。 |
| 52 | DELETE | `/spaces/{spaceId}/managers/{userId}` | 暂无 | 未见问题。 |
| 53 | PATCH | `/spaces/{spaceId}/managers/{userId}` | 暂无 | 未见问题。 |
| 54 | GET | `/spaces/{spaceId}/units` | 暂无 | `canTeach` + `quiz_ids_by_unit` 批量取小测 id，未见问题。 |
| 55 | POST | `/spaces/{spaceId}/units` | 暂无 | 管理员门，未见问题。 |
| 56 | PATCH | `/spaces/{spaceId}/units/{unitId}` | 暂无 | 未见问题。 |
| 57 | DELETE | `/spaces/{spaceId}/units/{unitId}` | 暂无 | 未见问题。 |
| 58 | GET | `/spaces/{spaceId}/units/{unitId}/quiz` | 暂无 | 提交/复核队列的人名走 `_attach_people_to` 两查询批量，未见问题。 |
| 59 | GET | `/spaces/{spaceId}/quizzes/{quizId}` | 暂无 | 同 #58。 |
| 60 | POST | `/spaces/{spaceId}/units/{unitId}/quiz` | 暂无 | 管理员门，未见问题。 |
| 61 | PATCH | `/spaces/{spaceId}/quizzes/{quizId}` | 暂无 | 未见问题。 |
| 62 | DELETE | `/spaces/{spaceId}/quizzes/{quizId}` | 暂无 | 未见问题。 |
| 63 | POST | `/spaces/{spaceId}/quizzes/{quizId}/questions` | 暂无 | 未见问题。 |
| 64 | PATCH | `/spaces/{spaceId}/quizzes/{quizId}/questions/{questionId}` | 暂无 | 未见问题。 |
| 65 | DELETE | `/spaces/{spaceId}/quizzes/{quizId}/questions/{questionId}` | 暂无 | 未见问题。 |
| 66 | PUT | `/spaces/{spaceId}/quizzes/{quizId}/my-attempt` | 可优化 | 换卷是「清空 + 逐题 INSERT」，N 道题 N 条 SQL；动词与幂等语义这里是对的。 |
| 67 | PATCH | `/spaces/{spaceId}/quizzes/{quizId}/answers/{answerId}` | 暂无 | 管理员门 + 单行更新，未见问题。 |

---

## 二、详细分析（只写有发现的，按收益从高到低）

### GET /spaces

- **现状**：`backend/app/api/routes/spaces.py:872` 分页列出可见题目板（`pageSize` 默认 20、`le=200`），
  第 905-923 行逐个空间取管理员并**按人**取用户资料，最后拼 `admins`。
- **问题**：`routes/spaces.py:905-923` 是双重 N+1：
  `service.list_admins(s.id)` 每空间 1 条 SQL，内层 `user_repo.get_by_id(rel.user_id)` +
  `profile_repo.get_profile_by_user_id(rel.user_id)` 又**每位管理员 2 条 SQL**。
  一页 20 个板、每板 2 名管理员 = 20 + 80 = 100 条 SQL，且随 `pageSize`（上限 200）线性增长。
  同一文件里已经有现成的批量写法（`_hydrate_people` 在 `spaces.py:635`，注释明确说它替换掉了
  「per person 的 get_by_id + get_profile_by_user_id 一对」；`_build_admins_payload` 在
  `spaces.py:675` 也是这么写的），**只有 `get_spaces` 没有走它**。
  另一个随行数增长的是 `spaces.py:903` 的 `service.get_user_rank(s.id, viewer_id)`：每空间 1 条 SQL
  （`domain/space/repositories.py:275`），`queryMyRank=true` 时又是一页 N 条。
- **优化**：整页的管理员关系问 1 次、人问 2 次（`_hydrate_people`）、我的排名问 1 次。
  仓库层加一个按 space 分组的批量读：

  ```python
  # backend/app/domain/space/repositories.py — SpaceAdminRelationRepository（放在 list_admins 之后）
  from typing import Sequence

  async def list_admins_for_spaces(
      self, space_ids: Sequence[int]
  ) -> dict[int, list[SpaceAdminRelation]]:
      """一页题目板的管理员，一次问完（键是 space_id，值是按创建时间排好的关系行）。"""
      if not space_ids:
          return {}
      stmt: Select[tuple[SpaceAdminRelation]] = (
          select(SpaceAdminRelation)
          .where(
              SpaceAdminRelation.space_id.in_(list(space_ids)),
              SpaceAdminRelation.deleted_at.is_(None),
          )
          .order_by(
              SpaceAdminRelation.space_id.asc(),
              SpaceAdminRelation.created_at.asc(),
          )
      )
      result = await self._session.execute(stmt)
      grouped: dict[int, list[SpaceAdminRelation]] = {}
      for rel in result.scalars().all():
          grouped.setdefault(rel.space_id, []).append(rel)
      return grouped
  ```

  排名同样批量（`SpaceUserRank` 有 `space_id`/`user_id`/`rank`，见 `domain/space/models.py:171`）：

  ```python
  # backend/app/domain/space/repositories.py — SpaceUserRankRepository
  async def get_ranks(
      self, space_ids: Sequence[int], user_id: int
  ) -> dict[int, int]:
      """这些板里我这个人的排名；没有行的板不在返回里（调用方给 0）。"""
      if not space_ids:
          return {}
      stmt = select(SpaceUserRank.space_id, SpaceUserRank.rank).where(
          SpaceUserRank.space_id.in_(list(space_ids)),
          SpaceUserRank.user_id == user_id,
          SpaceUserRank.deleted_at.is_(None),
      )
      rows = (await self._session.execute(stmt)).all()
      return {space_id: int(rank or 0) for space_id, rank in rows}
  ```

  路由侧（`routes/spaces.py:890-930`，改动点已标出；`SpaceService` 上各加一个同名透传方法即可）：

  ```python
      user_repo = UserRepository(session=db)
      profile_repo = UserProfileRepository(session=db)

      space_ids = [s.id for s in spaces]
      topics_by_space = await service.list_classification_topics_for_spaces(space_ids)
      course_shells = await service.default_category_shells(space_ids=space_ids)
      # 改动点 1：整页管理员一次问完，人再一次性 hydrate（与 _hydrate_people 同一条路）
      admins_by_space = await service.list_admins_for_spaces(space_ids)
      people = await _hydrate_people(
          [rel.user_id for rels in admins_by_space.values() for rel in rels],
          user_repo=user_repo,
          profile_repo=profile_repo,
      )
      # 改动点 2：我的排名一次问完
      ranks: dict[int, int] = {}
      if queryMyRank and viewer_id is not None:
          ranks = await service.get_user_ranks(space_ids, viewer_id)

      items: list[dict] = []
      for s in spaces:
          dto = _space_to_api_model(s)
          if queryMyRank:
              dto["myRank"] = ranks.get(s.id, 0)
          dto["admins"] = [
              _admin_to_api_model(rel, people[rel.user_id])
              for rel in admins_by_space.get(s.id, [])
          ]
          dto["classificationTopics"] = [
              {"id": t.id, "name": t.name} for t in topics_by_space.get(s.id, [])
          ]
          dto["isCourse"] = is_course_shell(course_shells.get(s.id))
          items.append(dto)
  ```

  `_hydrate_people` 对查不到的人返回 `{"id": …, "username": "unknown"}` 占位（`spaces.py:661-671`），
  与现有内联分支的语义完全一致，所以 `people[rel.user_id]` 不会 KeyError，未知用户的表现不变。
  效果：查询数从 `2 + 3×N_space + 2×N_admin` 降到固定 7 条（列表/计数/分类/壳/管理员/人/排名），
  与页大小、管理员人数都无关。
- **契约**：请求、响应字段与顺序都不变；不需要数据迁移。风险低，唯一可见差异是
  `list_admins_for_spaces` 的排序需与 `list_admins` 一致（已按 `created_at asc`），否则管理员顺序会变。
- **测试**：`backend/tests/integration/test_hot_path_queries.py` 已有 `counting_sql()`（`Engine` 级
  `after_cursor_execute` 监听，见该文件 31-49 行）。照
  `test_roster_round_trips_do_not_grow_with_the_roster`（同文件 80 行）加：
  - `test_the_space_list_does_not_grow_with_its_page_size`：建 3 个板、每板 2 名管理员，取一页；
    把板加到 12 个再取一页，断言两次 SQL 条数**相等**。
  - `test_the_space_list_still_names_every_manager`：断言 `admins[].user.username` 都在（防批量改造把
    人名丢成 unknown）。
  与 `backend/tests/contract/test_spaces_contract.py:31`（`test_python_get_spaces_shape`）互为补位：
  那条管形状，这两条管成本。

### GET /spaces/{spaceId}/analytics/participants/export

- **现状**：`routes/spaces.py:1790` 管理员门后调 `export_participants_csv` 拿 CSV 与成员行，然后
  第 1838-1859 行逐人写真实姓名访问审计。
- **问题**：`routes/spaces.py:1844-1853` 的循环里，每次 `realname_service.log_access` 都是 3 条 SQL：
  `domain/user/realname_services.py:123-144` 先 `_ensure_user_exists(accessor_id)`、
  再 `_ensure_user_exists(target_id)`（各 1 条 SELECT，见同文件 55-61），最后
  `create_access_log` 1 条 INSERT。一个 100 人的班 = 300 条 SQL；accessor 永远是同一个人，
  却被重复校验了 100 次。这条路由只被管理员用，且返回前要等全部审计写完。
- **优化**：批量形态的审计写入：accessor 查 1 次，target 用 `get_by_ids` 1 次，目标行一次 INSERT 全部写入。

  ```python
  # backend/app/domain/user/realname_services.py — UserRealNameService，放在 log_access 之后
  from typing import Sequence

  from app.domain.user.models import UserRealNameAccessLog

  async def log_access_many(
      self,
      *,
      accessor_id: int,
      target_ids: Sequence[int],
      access_reason: str,
      access_type: str,
      ip_address: str,
      module_type: str | None = None,
      module_entity_id: int | None = None,
  ) -> int:
      """一次导出给每个目标各留一行审计，用 2 条 SELECT 换掉 2×N 条。

      查不到的 target（已软删）跳过并返回写入成功的行数 —— 与
      ``routes/spaces.py`` 里那条 ``except NotFoundError`` 的取舍一致。
      """
      unique_ids = list(dict.fromkeys(int(t) for t in target_ids))
      if not unique_ids:
          return 0
      await self._ensure_user_exists(accessor_id)
      existing = await self._user_repo.get_by_ids(unique_ids)
      now = datetime.now(UTC)
      rows = [
          UserRealNameAccessLog(
              accessor_id=accessor_id,
              target_id=target_id,
              module_type=module_type,
              module_entity_id=module_entity_id,
              access_reason=access_reason,
              ip_address=ip_address,
              access_type=access_type,
              created_at=now,
              updated_at=now,
              deleted_at=None,
          )
          for target_id in unique_ids
          if target_id in existing
      ]
      self._session.add_all(rows)      # 一次 flush 就是一条多值 INSERT
      await self._session.flush()
      return len(rows)
  ```

  路由侧把 1838-1859 行的整段循环换成：

  ```python
      target_ids = [
          m.member_id for m in memberships if not m.is_team
      ]
      await realname_service.log_access_many(
          accessor_id=auth_user.user_id,
          target_ids=target_ids,
          access_reason=access_reason,
          access_type="EXPORT",
          ip_address=ip_address,
          module_type="SPACE",
          module_entity_id=space_id,
      )
  ```

  `log_access_many` 内部已去重（原来靠 `seen_target_ids` 集合去重，现在用 `dict.fromkeys`），
  顺序保持「按成员行首次出现」，日志行的目标集合与原来相同。
- **契约**：响应（CSV 内容与表头）不变，写入的审计行集合不变；不需要迁移。风险：原来「target 不存在 →
  跳过这一行并 `_logger.warning`」的日志没有了，若运维依赖该 warning，可在 `log_access_many` 里对
  `set(unique_ids) - set(existing)` 补一条 warning。
- **测试**：`backend/tests/integration/test_realname_record.py` 已有 `_logs()` 辅助（174 行）与
  按板建题、拉人入班的完整脚手架。加：
  - `test_the_participant_export_logs_one_row_per_student`：3 名学生导出一次，断言每个学生恰有 1 条
    `access_type=EXPORT` 的日志（防批量化后漏写或重复写）。
  - `test_the_participant_export_does_not_grow_with_the_class`（放进 `test_hot_path_queries.py`）：
    3 人与 12 人的班各导一次，断言两次 SQL 条数相等（CSV 生成本身是全量的，这里量的是「审计写入不随人增长」）。
  - `test_a_soft_deleted_student_is_skipped_but_the_export_succeeds`：软删一名学生后导出仍 200，其余学生日志齐全。

### POST /spaces/{spaceId}/invite-codes 与 PATCH /spaces/{spaceId}/invite-codes/{codeId}

- **现状**：`routes/spaces.py:1477` 与 `:1506` 都把 `expiresAt`（毫秒时间戳，客户端传入）直接
  转成 `datetime`，没有任何上下界校验。
- **问题**：`routes/spaces.py:1486` 与 `:1516` 的
  `datetime.fromtimestamp(payload.expires_at / 1000.0, tz=UTC)` 对越界值抛 `ValueError`，
  不在 `BaseError` 家族内，被 catch-all 处理成 500。实测（本机 Python 3.13）：
  `datetime.fromtimestamp(99999999999999999/1000, tz=UTC)` → `ValueError: year 3170843 is out of range`，
  `-99999999999999` → `year -1199 is out of range`，`253402300800000` → `year 10000 is out of range`。
  即客户端给一个「永不过期」的极大值，得到的是 500「服务器内部错误」（`app/core/errors.py`
  的兜底 handler），而不是 4xx 的输入错误 —— 与 GitHub 对非法字段值答 422 的做法相悖
  （<https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api#validation-failed-or-field-is-not-valid>）。
- **优化**：把毫秒时间戳的换算收进一个带边界的函数，越界按 422/400 答（本文件 `BaseError` 家族的
  `BadRequestError` 已经能给出 `{"code","message","error":{...}}`）：

  ```python
  # backend/app/api/routes/spaces.py — 放在 create_space_invite_code 之前（模块级辅助）
  # 上下界取「能安全地做 fromtimestamp」的范围；2038 之后仍然可用，
  # 只挡住会抛 ValueError 的越界值。
  _MIN_EXPIRES_AT_MS = -62_135_596_800_000        # 0001-01-01T00:00:00Z
  _MAX_EXPIRES_AT_MS = 253_402_300_799_000        # 9999-12-31T23:59:59Z


  def _expires_at_from_ms(value: int | None) -> datetime | None:
      """客户端给的毫秒时间戳 → datetime；越界是 400，不是 500。"""
      if value is None:
          return None
      if not (_MIN_EXPIRES_AT_MS <= value <= _MAX_EXPIRES_AT_MS):
          raise BadRequestError(
              "expiresAt is out of range",
              data={"expiresAt": value},
          )
      return datetime.fromtimestamp(value / 1000.0, tz=UTC)
  ```

  两处调用点各改成一行（`routes/spaces.py:1484-1486`、`:1514-1516`）：

  ```python
      expires_at = _expires_at_from_ms(payload.expires_at)
  ```

- **契约**：合法输入的行为完全不变；越界值从 500 变成 400，`error.name` 变成 `BadRequestError`。
  客户端（`frontend`）从不发送越界值，无迁移。
- **测试**：`backend/tests/contract/test_spaces_contract.py` 加：
  - `test_creating_a_code_with_an_out_of_range_expiry_is_refused`：`expiresAt=99999999999999999`
    断言 `status_code == 400` 且 `body["code"] != 500`；
  - `test_patching_a_code_with_an_out_of_range_expiry_is_refused`：同上走 PATCH；
  - `test_creating_a_code_with_a_sane_far_future_expiry_still_works`：`expiresAt=4102444800000`（2100 年）
    断言 201（防把上界收得过紧）。

### GET /spaces/{spaceId}/submissions

- **现状**：`routes/spaces.py:1905` 管理员看整门课的提交与验收队列，分页规范
  （`pageSize` 默认 20、`le=100`，`pageStart` 游标式），随后取 summary。
- **问题**：两处：
  1. `domain/task/services.py:925` 的 `TaskSubmissionService.list_for_space`：`rows` 拿到之后，
     第 959-964 行**每行**再发 2 条 SQL —— `_entry_repo.list_by_submission_id` 与
     `_review_repo.get_by_submission_id`。一页 100 行 = 200 条额外 SQL。
     （`_build_member_summary` / `_build_submitter_summary` / `_build_review_dto` 是纯字典构造，
     见 `domain/task/services.py:649-685`，本身不发查询 —— 问题只在上面那两条。）
  2. `domain/task/services.py:978` 的 `summary_for_space`：第 996 行
     `self._membership_repo.list_memberships_for_space(space_id)` 把全板**所有题的报名行**都读出来，
     只为第 1000 行的 `len(memberships)` 和第 1003 行的 `len(memberships) - submissions`。
     一个 500 人的班、10 道题 = 5000 行只为了一个数；而它挂在每一页请求上
     （`routes/spaces.py:1944`）。
- **优化**：两条都用「按 id 集合查」的既有仓库方法，把 per-row 换成 per-page；
  summary 的两个数字直接问 COUNT。

  ```python
  # backend/app/domain/task/services.py — list_for_space 的循环体（原 958-974 行）
          submission_ids = [submission.id for submission, _, _ in rows]
          entries_by_submission: dict[int, list[TaskSubmissionEntry]] = {}
          # 改动点 1：一页的 entry 一次问完（仓库需加 list_for_submissions(submission_ids)）
          for entry in await self._entry_repo.list_for_submissions(submission_ids):
              entries_by_submission.setdefault(entry.submission_id, []).append(entry)
          # 改动点 2：一页的 review 一次问完（仓库需加 get_by_submission_ids(submission_ids)）
          reviews_by_submission = await self._review_repo.get_by_submission_ids(
              submission_ids
          )

          items: list[dict] = []
          for submission, membership, task in rows:
              dto = await self._build_submission_dto(
                  submission=submission,
                  membership=membership,
                  entries=entries_by_submission.get(submission.id, []),
                  review=reviews_by_submission.get(submission.id),
              )
              ...
  ```

  两个仓库方法照现有单行版写即可（`TaskSubmissionEntryRepository` / `TaskSubmissionReviewRepository`）：

  ```python
  # backend/app/domain/task/repositories.py
  async def list_for_submissions(
      self, submission_ids: Sequence[int]
  ) -> Sequence[TaskSubmissionEntry]:
      if not submission_ids:
          return []
      stmt = select(TaskSubmissionEntry).where(
          TaskSubmissionEntry.submission_id.in_(list(submission_ids)),
          TaskSubmissionEntry.deleted_at.is_(None),
      )
      return list((await self._session.execute(stmt)).scalars().all())

  async def get_by_submission_ids(
      self, submission_ids: Sequence[int]
  ) -> dict[int, TaskSubmissionReview]:
      """每条提交的最新一条复核（多行时按 updated_at 取新的）。"""
      if not submission_ids:
          return {}
      stmt = select(TaskSubmissionReview).where(
          TaskSubmissionReview.submission_id.in_(list(submission_ids)),
          TaskSubmissionReview.deleted_at.is_(None),
      )
      latest: dict[int, TaskSubmissionReview] = {}
      for review in (await self._session.execute(stmt)).scalars().all():
          current = latest.get(review.submission_id)
          if current is None or review.updated_at > current.updated_at:
              latest[review.submission_id] = review
      return latest
  ```

  （`get_by_submission_id` 现在返回的是单行，注意保持「取最新」的语义一致；
  若 `list_for_submissions` 的顺序影响响应中 `entries` 的顺序，加 `.order_by(TaskSubmissionEntry.id.asc())`。）
  summary 侧：

  ```python
  # backend/app/domain/task/services.py — summary_for_space（原 996-1007 行）
          # 改动点：报名人数与「没交的人数」都问 COUNT，不再读全板报名行
          participants = await self._membership_repo.count_for_space_members(
              space_id=space_id, task_id=task_id
          )
          return {
              "participants": participants,
              "submissions": submissions,
              "pendingReview": pending,
              "missing": participants - submissions,
          }
  ```

  `count_for_space_members` 照 `SpaceAnalyticsService` 的口径写
  （`select(func.count(TaskMembership.id)).join(Task, Task.id == TaskMembership.task_id).where(Task.space_id == space_id, Task.deleted_at.is_(None), TaskMembership.deleted_at.is_(None))`，
  `task_id` 非空时再加 `Task.id == task_id`）—— 与 `TaskSubmissionRepository.count_for_space`
  （`repositories.py:896`）用的是同一套 join 条件。
- **契约**：不变（同样的行、同样的三个数字）。风险：`entries` 顺序需要钉住（见上）；`missing` 原来是
  Python 减出来的，改成 SQL 计数后语义相同（`count_for_space` 每人只算最新一版，注释已在
  `domain/task/repositories.py:903-907` 说明）。
- **测试**：`backend/tests/integration/test_space_submission_queue.py` 已有整门课的脚手架
  （`test_a_teacher_sees_the_whole_course_in_one_request`、`test_the_numbers_add_up`）。加：
  - `test_the_submission_queue_does_not_grow_with_the_page`：3 份提交与 12 份提交各取一页，
    断言 SQL 条数相等；
  - `test_the_summary_counts_do_not_read_every_membership`：加 3 个不相关的板/题，断言
    `missing` 数字不变（防 summary 又把别的板算进来）；
  - `test_the_numbers_add_up` 保持通过（`participants - submissions == missing`）。

### GET /spaces/{spaceId}/analytics/tasks、/overview、/alerts、/publishers、/participants（+ 两个导出）

- **现状**：`routes/spaces.py:1579 / 1671 / 1702 / 1719 / 1750 / 2089 / 2128`，
  全部经 `SpaceAnalyticsViewService`，其中 `domain/space/analytics_view_service.py:612` 的
  `_load_context` 把本板**全部** Task / TaskMembership / TaskSubmission / TaskSubmissionReview
  读进内存，再在 Python 里聚合。
- **问题**：`analytics_view_service.py:619-702` 一共 6~8 条 SQL，但每条都是**无上限的全表扫描**：
  `select(Task).where(Task.space_id == space_id, …)`（第 624 行）→ 该板所有题；
  第 641 行的 memberships 按 `task_ids.in_(…)`；第 655 行的 submissions 按 `membership_ids.in_(…)`；
  第 670 行的 reviews 按 `submission_ids.in_(…)` —— 后两条的 `in_` 列表长度随前一张表增长。
  代价：`get_tasks`（`:249`）对**每个任务**返回一行且没有分页，`hasPendingReview`/
  `hasPendingApproval` 还是 Python 后过滤（`:288-295`）；`get_overview`（`:136`）`get_alerts`（`:180`）
  `get_publishers`（`:216`）`get_participants`（`:414`）各自把同一份 `_load_context` 重算一遍
  （7 个接口 = 7 次全量载入，无缓存）。这也是这一组接口相对其它接口最贵的部分。
  （附带事实：两个 `deprecated` 的旧接口 `routes/spaces.py:1623` / `:1641` 走的是
  `SpaceAnalyticsService`，`domain/space/analytics_service.py:32` 的 `_fetch_all_tasks` +
  `list_memberships_for_space`（`:104` / `:165`），同样是全量，但已废弃，不单独提收益。）
- **优化**：分两步，收益从大到小：
  1. **`get_tasks` 加分页**（与 `GET /spaces/{spaceId}/submissions` 同一套 `pageStart/pageSize`，
     响应里加 `page` 对象），先 `LIMIT/OFFSET` 出这一页的 task，再只为这一页的 task 取 memberships /
     submissions。这是把「一屏表格」从 O(全板) 降到 O(页大小)。
  2. **`overview` / `alerts` / `publishers` 改成 SQL 聚合**，不再读行。以发布者一屏最常用的
     每任务计数为例：

  ```python
  # backend/app/domain/space/analytics_view_service.py — 新增一个聚合读取
  from sqlalchemy import func, select

  async def _task_counts_by_task(
      self, *, task_ids: list[int]
  ) -> dict[int, dict[str, int]]:
      """每个任务的报名/审批计数，在 SQL 里数，不把行读回来。"""
      if not task_ids:
          return {}
      approved = func.count().filter(TaskMembership.approved == 0)
      pending = func.count().filter(TaskMembership.approved == 2)
      stmt = (
          select(
              TaskMembership.task_id,
              func.count(TaskMembership.id).label("participant_count"),
              approved.label("approved_count"),
              pending.label("pending_count"),
          )
          .where(
              TaskMembership.task_id.in_(task_ids),
              TaskMembership.deleted_at.is_(None),
          )
          .group_by(TaskMembership.task_id)
      )
      rows = (await self._session.execute(stmt)).all()
      return {
          task_id: {
              "participantCount": participant_count,
              "approvedParticipantCount": approved_count,
              "pendingParticipantApprovalCount": pending_count,
          }
          for task_id, participant_count, approved_count, pending_count in rows
      }
  ```

  （PostgreSQL 的 `count(*) FILTER (WHERE …)`，SQLAlchemy 侧就是 `func.count().filter(...)`；
  这块板的后端是 PostgreSQL，`JSONB` 已在用。）
- **契约**：`get_tasks` 加分页是**响应形状变化**（新增 `page`，默认仍返回第一页），
  前端 `views/spaces/board/` 的分析表需要按 `nextStart` 取下一页 —— 建议与 `submissions` 用同一个
  `page` 形状，前端可复用同一个分页组件；SQL 聚合不改变响应字段。风险：聚合口径必须与
  `_compute_task_row`（`:947`）逐字段对齐，否则数字会漂 —— 这也是为什么建议先加分页、再迁聚合。
- **测试**：`backend/tests/integration/test_analytics_sort_defaults.py`（已有默认排序用例）与
  `test_space_analytics_submitted_counts.py` 两侧都值得加：
  - `test_the_task_analytics_page_does_not_load_the_whole_board`：一页 5 行与一页 50 行 SQL 条数相同；
  - `test_the_task_analytics_page_reports_the_same_totals_as_before`：把分页前的全量结果与分页后
    逐页拼起来比对（防聚合口径漂移）；
  - `test_the_overview_counts_match_the_row_level_view`：`overview` 的 KPI 与 `/analytics/tasks`
    的行求和相等。

### GET /spaces/{spaceId}/analytics/learning/{filters,questions,queues} 与 POST .../outline

- **现状**：`routes/spaces.py:1982 / 2009 / 2037 / 2063`，四条都进 `SpaceLearningService`，
  第一条要回答「这门课里哪些项目我看得到」。
- **问题**：`domain/space/learning_service.py:128` 的 `_readable_project_ids`：第 140 行
  `list_ids_for_space_tasks(space_id)` 一条无上限 join 取全板项目 id，然后第 142-147 行**逐个项目**
  调 `may_read_project`。后者（`app/auth/project_access.py:58`）在一个「不是成员、不是所有者」的项目上
  最多发 7~9 条 SELECT：名册行（`:68`）、项目本身（`:70`）、`get_by_username`（`:75`）、
  出题者那条的 `TaskRepository.get_by_id`（`:133`）、题目板管理员那条**又取一次同一个 Task**
  （`app/auth/space_access.py:104`）再 `is_space_admin`、`MemberRepository.is_excluded`（`:102`）、
  `TeamRepository.is_team_member`（`:106`）。这门课有 40 个项目时，一次 `filters` 就是几百条 SQL，
  四个路由各来一次；而且同一个 Task 在一次鉴权里被读了两遍。
- **优化**：两条都做：
  1. `may_read_project` 内把 `Task` 复用一次（`_is_asker_of_the_task` 与 `is_admin_of_projects_task`
     各读一次同一行）：把 `Project` 已有的 `external_task_id` 对应的 Task 在函数入口取一次，
     传给两个判据（`is_admin_of_projects_task` 加一个 `task: Task | None = None` 的重载参数，
     缺省仍自行读取，其它调用方不受影响）：

  ```python
  # backend/app/auth/project_access.py — may_read_project 内部（原 78-91 行）
      task = (
          await TaskRepository(session).get_by_id(project.external_task_id)
          if project.external_task_id is not None
          else None
      )
      if task is not None and task.creator_id == user.id:
          return True
      # 改动点：把已经读到的 task 传进去，省掉 is_admin_of_projects_task 里的第二次读
      if await is_admin_of_projects_task(
          session, external_task_id=project.external_task_id, user_id=user.id, task=task
      ):
          return True
  ```

  ```python
  # backend/app/auth/space_access.py — is_admin_of_projects_task
  async def is_admin_of_projects_task(
      session: AsyncSession,
      *,
      external_task_id: int | None,
      user_id: int | None,
      task: Task | None = None,          # 改动点：调用方已读过时可传进来
  ) -> bool:
      if external_task_id is None:
          return False
      if task is None:
          task = await TaskRepository(session).get_by_id(external_task_id)
      if task is None or task.space_id is None:
          return False
      return await is_space_admin(session, space_id=task.space_id, user_id=user_id)
  ```

  2. `_readable_project_ids` 对同一请求内的重复判断收一个请求内缓存（同一门课的四个接口会各自调一次，
     但**一次请求内**每个项目只判一次）：

  ```python
  # backend/app/domain/space/learning_service.py
  from functools import lru_cache  # 或直接在 __init__ 里挂一个 dict

  class SpaceLearningService:
      def __init__(self, session: AsyncSession) -> None:
          self._session = session
          self._readable_cache: dict[str, list[uuid.UUID]] = {}   # 改动点：请求内缓存

      async def _readable_project_ids(
          self, *, space_id: int, handle: str | None
      ) -> list[uuid.UUID]:
          key = f"{space_id}:{handle or ''}"
          cached = self._readable_cache.get(key)
          if cached is not None:
              return cached
          ids = await ProjectRepository(self._session).list_ids_for_space_tasks(space_id)
          allowed: list[uuid.UUID] = []
          for project_id in ids:
              if await may_read_project(self._session, project_id=project_id, handle=handle):
                  allowed.append(project_id)
          self._readable_cache[key] = allowed
          return allowed
  ```

  （服务由 `get_space_learning_service(db=Depends(get_db))` 每请求新建，`routes/spaces.py:1965`，
  所以这个 dict 的生命周期就是一次请求 —— 不会跨请求缓存鉴权结论。）
- **契约**：完全不变（鉴权结论不变，响应不变）。风险：缓存键必须含 `handle`，否则跨用户串权限；
  上面已含。更彻底的解法是把鉴权下推成一条 SQL（项目 id → 名册/所有者/出题者/板管理员 的并集），
  但那是跨模块（`auth` → SQL）的重构，建议单独跟进，不塞进这次。
- **测试**：`backend/tests/integration/test_space_learning_filters_visibility.py` 已专门测这组可见性。加：
  - `test_the_learning_board_does_not_ask_per_project_twice`：用 `test_hot_path_queries.py` 的
    `counting_sql()`，对同一门课连着请求 `filters` 与 `questions`，断言 SQL 条数不随项目数增长；
  - `test_a_teacher_still_sees_every_project_of_his_board` / `test_a_stranger_still_sees_none`：
    保证下推/缓存没有改变谁看得见（两条已有同类用例，扩展即可）。

### POST /spaces

- **现状**：`routes/spaces.py:951` 创建题目板，第 957 行先 `exists_by_name` 查重。
- **问题**：`routes/spaces.py:957-958` 的 `exists_by_name` 与后面的 INSERT 之间没有任何约束兜底：
  `Space.name` 在 `domain/space/models.py:36` 只是 `String(255), nullable=False`，**没有唯一索引**
  （`exists_by_name` 在 `domain/space/repositories.py:146` 也只是普通 SELECT）。
  两个同名请求并发时都能通过查重，最终落两行同名板 —— 典型的 TOCTOU。
- **优化**：给「未删除的板名」加唯一索引兜底，并把竞态转成 409。因为唯一性要排除软删行，
  用部分唯一索引：

  ```python
  # backend/app/domain/space/models.py — Space（配合一条 Alembic 迁移）
  from sqlalchemy import Index

  __table_args__ = (
      # 未删除的题目板名字唯一：查重与 INSERT 之间的窗口由数据库关掉。
      Index(
          "uq_space_name_live",
          "name",
          unique=True,
          postgresql_where=text("deleted_at IS NULL"),
      ),
  )
  ```

  ```python
  # backend/app/api/routes/spaces.py — create_space（原 957-974 行）
      try:
          space = await service.create_space(...)
      except IntegrityError as exc:                    # 改动点：并发同名落到唯一索引
          await db.rollback()
          raise ConflictError(
              f"Space with name '{payload.name}' already exists"
          ) from exc
  ```

  （`ConflictError` 已在本文件 `:958` 用过，是 `BaseError` 家族。）
- **契约**：响应不变（同名仍是 409）；需要一次数据迁移，且**迁移前要先清理已存在的同名板**，
  否则建索引会失败。风险：名字是用户可见的标识，如果产品允许重名，则这条应改成「保留重名、
  去掉查重」—— 需要先拍板，故本条建议以「先确认 product 口径」为前提。
- **测试**：`backend/tests/integration/` 有专门迁移测试的目录（如 `test_account_unique_indexes_migration.py`）。
  加：
  - `test_two_spaces_cannot_share_a_live_name`：同一名字两次创建，断言第二次 409；
  - `test_a_deleted_space_frees_its_name`：删掉后可以再用同名创建（部分索引的语义）。

### PUT /spaces/{spaceId}/quizzes/{quizId}/my-attempt

- **现状**：`routes/spaces.py:3243` 学生交卷；服务层 `domain/teaching/quiz_services.py:308` 先按
  「重交 = 换一份答案」清空旧答案，再逐题写入。
- **问题**：`domain/teaching/quiz_services.py:351-363` 的 `for question in questions:` 里
  `await self._answers.create(...)` 一题一条 INSERT，20 道题的小测就是 20 次数据库往返；
  加上前面的 `_questions.list_for_quiz`、attempt 查询/更新，一次交卷 25+ 条 SQL。
  交卷是学生高峰动作（下课集中交），这个放大倍数会直接体现在响应时间上。
- **优化**：一次多值 INSERT。

  ```python
  # backend/app/domain/teaching/quiz_services.py — submit（替换原 351-363 行的循环）
          now = datetime.now(UTC)
          rows = []
          for question in questions:
              response = answers.get(question.id)
              rows.append(
                  QuizAnswer(
                      attempt_id=attempt.id,
                      question_id=question.id,
                      response=response,
                      awarded_points=(
                          _grade_objective(question=question, response=response)
                          if is_objective(question.kind)
                          else None
                      ),
                      created_at=now,
                      updated_at=now,
                      deleted_at=None,
                  )
              )
          self._session.add_all(rows)     # 改动点：一题一条 INSERT → 一次多值 INSERT
          await self._session.flush()
  ```

  （字段名照 `QuizAnswer` 模型与 `_answers.create` 的实现填；若 `_answers.create` 里有额外的
  归一化，先把它提成纯函数，别让两条写入路径的默认值分叉。）
- **契约**：不变。风险：第 364 行随后调的 `_settle_attempt`（定义在 `:373`）依赖答案行已 flush，
  `add_all + flush` 同样满足；`clear_for_attempt` 先删后插的顺序不变。
- **测试**：`backend/tests/integration/test_space_quiz.py` 已有交卷用例。加：
  - `test_answering_a_quiz_does_not_go_per_question`：3 题与 12 题的小测各交一次，断言两次
    INSERT 条数相等（读操作可能随题数增长，这里只数 `INSERT INTO` 语句）；
  - `test_resubmitting_replaces_the_answers_not_appends`（已有同类语义，确保批量化后仍是替换）。

### GET /spaces/{spaceId}/course-link

- **现状**：`routes/spaces.py:1245` 老师取一个可复制的进课链接，管理员门之后挑一张可用码，
  没有可用码就现造一张。
- **问题**：`routes/spaces.py:1270-1273` 在这个 **GET** 里调 `service.create_invite_code(...)`
  —— 一次读请求会写库。REST 语义上 GET 必须安全、幂等
  （<https://docs.microsoft.com/rest/api/> 之外，HTTP 语义见 RFC 9110 §9.2.1；
  GitHub 的「读接口不产生副作用」同样如此），否则任何预取、重试或爬虫都可能为这个板无上限地制造邀请码行。
  另外这里 `service.list_invite_codes`（`:1258`）会为「挑一张」把整板码全读出来再在 Python 里 `next(...)`。
- **优化**：拆成「读优先、写兜底」的显式动作：GET 只返回现成可用码（没有就 404/deprecated 语义），
  造码留给已经存在的 `POST /spaces/{spaceId}/invite-codes`；若确实要保留一键取的体验，
  用 `POST .../course-link` 承载这个副作用（GitHub 里「会创建资源的动作一律用 POST」）。
  同时把「挑一张可用码」下推到一条 SQL（`domain/space/space_invite_code` 有 `space_id`/`expires_at`/
  `use_count`/`max_uses`）：

  ```python
  # backend/app/domain/space/repositories.py — SpaceInviteCodeRepository
  async def get_usable_for_space(self, space_id: int) -> SpaceInviteCode | None:
      """这个板上一张还能用的码（没过期、没用满），SQL 里挑第一张。"""
      now = datetime.now(UTC)
      stmt = (
          select(SpaceInviteCode)
          .where(
              SpaceInviteCode.space_id == space_id,
              SpaceInviteCode.deleted_at.is_(None),
              or_(
                  SpaceInviteCode.expires_at.is_(None),
                  SpaceInviteCode.expires_at > now,
              ),
              SpaceInviteCode.use_count < SpaceInviteCode.max_uses,
          )
          .order_by(SpaceInviteCode.created_at.asc())
          .limit(1)
      )
      return (await self._session.execute(stmt)).scalar_one_or_none()
  ```

- **契约**：若把副作用搬到 POST，路由方法变化（`GET` → `POST`），前端 `SpacesApi` 要跟着改；
  若保留 GET 但改为「没有可用码时 404」，是行为变化。**两种都需要前端确认**，
  所以本条建议先定为「把写动作挪出 GET」，并在报告里标为需产品/前端拍板。
- **测试**：`backend/tests/integration/test_course_link.py` 已有 7 条用例
  （含 `test_only_the_boards_teachers_hand_out_the_link`、`test_an_outsider_still_reads_the_board_as_absent`）。加：
  - `test_reading_the_course_link_does_not_mint_a_code`：连着 GET 两次，断言码的条数与 `id` 都不变；
  - `test_the_link_is_the_same_code_while_it_stays_usable`：一张可用码存在时两次 GET 返回同一张码。

### GET /spaces/{spaceId}/members

- **现状**：`routes/spaces.py:1296` 任何人（成员）可读的花名册，`_ensure_space_visible` 之后
  把每行交给 `_hydrate_members`（批量，写法正确）。
- **问题**：两件事：
  1. **无分页**（`domain/space/repositories.py:640` 的 `list_members` 没有 limit），
     人数随班级增长，一次响应就是全班人与码。
  2. **`inviteCode` 泄漏**：`routes/spaces.py:571-575` 的 `_member_to_api_model` 把每行的
     `inviteCode` 组织成 `{"id", "code"}` —— 即**码的明文**；而 `_hydrate_members`
     （`:719`）用的 `get_by_ids` 明确保留软删的码。默认那张码是 `max_uses=50`、
     `expires_at=None`（`domain/space/services.py:42`、`create_space` 里 `expires_at=None`），
     也就是说：**任何成员都能从花名册里拿到一张还能用 50 次、永不过期的码**，
     而签发/查看邀请码本身是管理员权限（`services.py:789` / `:804` 的 `_ensure_admin`）。
     前端确实在用这个字段（`frontend/src/types/spaces.ts:79`、`Members.vue` 的「加入方式」列），
     测试 `test_space_membership.py` 也断言成员能读花名册 —— 所以「成员能读花名册」是有意的，
     **泄漏的到底是「这张码的明文」还是只需要「这张码的 id/说明」值得确认**。
- **优化**：两件事分开：
  1. 分页（与 `submissions` 同一形状，加 `pageStart/pageSize`），响应加 `page`；
  2. 把「码的明文」按权限收窄：管理员看到 `{"id", "code"}`，非管理员只看到
     `{"id"}`（有 id 就能显示「通过某张码加入」，而拿到 id 无法兑换）：

  ```python
  # backend/app/api/routes/spaces.py — list_space_members（原 1302-1310 行）
      await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
      is_admin = await is_space_admin(
          session=db, space_id=space_id, user_id=auth_user.user_id
      )
      members = await service.list_members(space_id, limit=page_size, offset=offset)
      items = await _hydrate_members(...)
      if not is_admin:                     # 改动点：非管理员只留 id，不留可兑换的明文
          for item in items:
              if item.get("inviteCode"):
                  item["inviteCode"] = {"id": item["inviteCode"]["id"]}
  ```

- **契约**：分页是响应形状变化（新增 `page`），前端花名册要支持翻页；
  `inviteCode.code` 对非管理员消失是响应内容变化，**需要产品确认**（也可以反过来：认为
  成员本来就该能拉人，那就把「加入方式」列改成只给管理员看）。不需要迁移。
- **测试**：`backend/tests/integration/test_space_membership.py` 已有 `sql_log` / `_selects` 夹具
  （210-238 行）与 `test_a_member_of_the_board_still_reads_the_roster`（1307 行）。加：
  - `test_the_roster_does_not_grow_with_the_class`（已有 `test_hydrating_the_roster_does_not_grow_with_the_roster`
    在 1131 行，补分页后重跑）；
  - `test_a_plain_member_does_not_get_a_usable_code`：普通成员读花名册，断言
    `inviteCode` 没有 `code` 字段；管理员读同一份，断言有；
  - `test_a_revoked_code_still_names_how_a_member_joined`：保住 `get_by_ids` 保留软删码的既有语义。

### GET /spaces/{spaceId}/me/publishing/tasks

- **现状**：`routes/spaces.py:2187` 我发布的题列表，服务在
  `domain/space/member_publishing_service.py:81`。
- **问题**：`member_publishing_service.py:115-122`：只要带了
  `from/to/categoryId/approved` **任意一个**筛选，就再发一条**不带筛选**的
  `_list_my_publishing_tasks`（`:162-191`），把我在这个板发布过的题全部读第二遍，
  只为算「可见性徽章」（`visibleTaskLimit` 的排名）。同时这个接口本身没有分页
  （`get_my_published_tasks` 返回 list，响应是 `{"tasks": items}`），排序与
  `hasPendingParticipantApproval` / `hasPendingReview` 都在 Python 里做（`:130-151`）。
- **优化**：徽章只需要「我这些题的 id 与创建时间」，不需要整行 Task：

  ```python
  # backend/app/domain/space/member_publishing_service.py — _list_my_publishing_tasks 的窄版本
  async def _list_my_publishing_task_keys(
      self, *, space_id: int, user_id: int
  ) -> list[tuple[int, datetime | None]]:
      """只取 id 与 created_at，给可见性徽章排名用（比读整行便宜）。"""
      stmt = (
          select(Task.id, Task.created_at)
          .where(
              Task.deleted_at.is_(None),
              Task.space_id == space_id,
              Task.creator_id == user_id,
          )
      )
      return list((await self._session.execute(stmt)).all())
  ```

  （`_compute_visible_approved_task_ids` 只用 `id` 与 `created_at` 的话，这两个字段就够了 ——
  改造前先确认该方法的输入需求，若它读 `approved`/`ended_at` 则一并 select 出来。）
- **契约**：不变。收益：小（自己在板里发的题通常几十条），所以这条排在最后；
  真要彻底，把排名做成 SQL 窗口函数 `row_number() over (partition by … order by created_at)`。
- **测试**：`backend/tests/integration/test_space_publishing_visibility_badges.py`（已存在，专测徽章）。加：
  - `test_the_badge_query_is_not_run_twice_for_a_filtered_page`：带 `categoryId` 请求一次，
    断言 SQL 里针对 `task` 的全量 SELECT 只有一条。

### GET /spaces/{spaceId}

- **现状**：`routes/spaces.py:828` 取单板详情：`service.get_space`（`:837`）+ 可见性
  （`:842`）+ `_build_full_space_payload`（`:856`）。
- **问题**：同一张 `space` 表在一次请求里被读了 3 次：router 级依赖
  `require_reviewed_space`（`:287-313`，注册在 `:311`）对**每个** `/spaces` 请求都做一次
  `SpaceRepository(db).get_by_id(space_id)`（`:299`，整行）；`_ensure_space_visible`（`:443`）
  为了判成员资格再进 `SpaceRepository.is_member`（`domain/space/repositories.py:35-47`，
  发的是 `select(Space.id).where(Space.id == space_id, …, build_membership_predicate(...))`
  —— 也是查 `space` 表，只是只取 id 并带上成员谓词）；`service.get_space` 再读一次
  （`domain/space/services.py` 的 `_get_space_or_error`）。另外
  `_build_full_space_payload` 里的 `service.is_course(...)` 还要算一次默认分组的壳。
  多次单行主键读代价不大，但 router 依赖这一次是**每个** `/spaces/*` 请求都付的重复成本
  （含 `PATCH`/`DELETE`/`members`/`quizzes` 等全部 60+ 条子路由）。
- **优化**：把 router 依赖已经读到的那行放进 `request.state`，主体与 `_ensure_space_visible`
  复用；或把 `require_reviewed_space` 从 router 级依赖改为按需依赖（只有真正要判审核态的路由才挂）：

  ```python
  # backend/app/api/routes/spaces.py — require_reviewed_space（原 287-308 行）
  async def require_reviewed_space(
      request: Request,
      user: AuthUserInfo = Depends(require_auth_user),
      db=Depends(get_db),
  ) -> None:
      space_id = request.path_params.get("spaceId")
      if space_id is None:
          return
      try:
          space_id = int(space_id)
      except ValueError:
          return  # Path validation supplies the normal 422 response.
      space = await SpaceRepository(db).get_by_id(space_id)
      # 改动点：这一行已经被读过，放进 request.state，主体不用再读
      request.state.space = space
      ...
  ```

  主体里 `service.get_space` 改为先看 `getattr(request.state, "space", None)`，
  命中则直接用（`Space` 实例在同一 session 的 identity map 里本来就相同）。
- **契约**：不变。收益：小到中（省 1 次主键查询/请求，所有 `/spaces/*` 都受益）。
- **测试**：`backend/tests/contract/test_spaces_contract.py:13` 的形状用例 + 运行时查询计数：
  - `test_getting_one_space_reads_its_row_once`：用 `counting_sql()` 断言
    `GET /spaces/{id}` 里命中 `space` 表的语句数为 2（现在是 3：`require_reviewed_space`
    整行、`is_member` 的 id 版、`get_space` 整行；复用 `request.state` 后省掉最后一次）。
    这个测试同时钉住「不许再涨回 3」和「不许为了降数字把成员检查省掉」。

### GET /spaces/{spaceId}/topics

- **现状**：`routes/spaces.py:2285`：`limit` 声明 `ge=1, le=100`，实际第 2302 行
  `safe_limit = min(limit, 50)`。
- **问题**：声明的上限（100）与实际生效的上限（50）不一致，且**静默** —— 客户端要 100 拿到 50，
  响应里没有 `total`/`page`，也没有任何提示（注释说这是对齐 NT 的行为）。
  按 GitHub 的做法，超出上限的 `per_page` 要么按上限返回并**在链接/总数里体现**，
  要么直接拒绝（<https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api>）。
- **优化**：把声明改成实际值，让契约与行为一致（这一条是纯声明变更，零风险）：

  ```python
      # routes/spaces.py:2289 —— 声明即真相
      limit: int = Query(default=20, ge=1, le=50),
  ```

  若产品确实要支持 100，则改为 `limit: int = Query(default=20, ge=1, le=100)` 并去掉 `safe_limit`。
- **契约**：`le=100 → le=50` 会让「传 60」从「静默拿 50」变成 422 —— 这是**更诚实**的失败，
  但仍是行为变化，需要前端确认（前端若从不传 >50 则无影响）。
- **测试**：`backend/tests/contract/test_spaces_contract.py` 加：
  - `test_the_topic_limit_that_is_declared_is_the_limit_that_applies`：传 `limit=100`，
    断言「要么 422，要么返回 ≤ 声明上限且响应里能看出被截断」。

### POST /spaces/{spaceId}/enroll（待确认，暂不建议改动）

- **现状**：`routes/spaces.py:1180` 课程链接一次性入课：兑换码 → 确认可见 → 确保项目存在。
- **问题（仅为命名，不是缺陷）**：响应里 `routes/spaces.py:1231` 用 `"root_topic_id"`（snake_case），
  而同响应其它键（`id`、`name`、`project`）与本文件其它所有接口都是 camelCase。
  查证：前端 `frontend/src/cx_types.ts:938` 与 `App.vue:554` 读的就是 `root_topic_id`
  —— 它对齐的是 **projects** 域那份响应（`projects` 模块整体用 snake_case），
  所以这大概率是**有意的对齐**而不是笔误。
- **优化**：不建议现在改。若要统一命名，需要和 `projects` 域一起改（否则同一个前端字段在
  两个域之间对不上）。写在这里只是备案。
- **契约 / 测试**：都会随命名方案变化，暂不列。

---

## 三、模块级建议（跨接口）

### 1. 分页协议：把 `pageStart/pageSize/hasMore/nextStart/total` 补齐到还缺的读接口

- **适用接口**：`GET /spaces/{spaceId}/members`（#9）、`GET /spaces/{spaceId}/analytics/tasks`（#20）、
  `GET /spaces/{spaceId}/me/publishing/tasks`（#36）、`GET /spaces/{spaceId}/me/participations`（#38），
  以及 #33/#34 两个导出（导出可保持全量，但内部的读取应改为按页流式）。
- **做法**：直接复用本模块已有的 `page` 形状（`routes/spaces.py:936-942` 与 `:1954-1960` 是同一套字段），
  不要新造；上游分析表按 `nextStart` 取下一页。**不要**引入 `total` 之外的第二种总数口径
  （现有实现都是「过滤后的总数」）。
- **为什么**：GitHub 的分页约定是「参数化 + 响应里给出下一页的指引」（`per_page` 上限 100 +
  `Link` 头 <https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api>），
  Stripe/Slack 同为「cursor + 明确的 has_more」（业界通行，未逐条查证）。
  本项目的 `nextStart` 就是 cursor 位置，已经符合；缺的只是「有的接口没上」。

### 2. 错误格式：统一 `message`/`documentation_url` 风格的机器可读字段

- **适用接口**：全部 67 条（出现在两类差异里）。
- **做法**：现状的错误体是 `{"code", "message", "error": {"name", "retryable", "message", "data"}}`
  （`app/core/errors.py:176-200`），成功体是 `{"code","message","data"}` —— 内部一致，但对外
  缺少「去哪看这个错误」的稳定入口。GitHub 的错误对象固定带 `message` 与
  `documentation_url`（<https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api#about-error-messages>），
  或改用 RFC 7807 `application/problem+json`。建议**不推翻现有信封**，只在 `error` 里补一个
  `documentation_url`（可由 `error.name` 拼出）与 `status`，成本极低、向后兼容。
  同时修掉一处**同一条件两种说法**：非管理员访问管理员版面时，走
  `_ensure_space_admin` 的路由答 `"Only a board manager can perform this action"`
  （`routes/spaces.py:468`），走服务层 `_ensure_admin` 的路由答
  `"Only space admins can perform this action"` 或 `"Only space owner can perform this action"`
  （`domain/space/services.py:1041` / `:1043`）—— 同一种拒绝，两种 message，客户端的
  文案映射会漏。建议把这三种文案收进一个常量表（`error.data.reason` 用同一个键）。
- **为什么**：GitHub 的错误体是「固定字段 + 文档链接」，RFC 7807 是业界另一种通行写法
  （<https://www.rfc-editor.org/rfc/rfc7807>）；两种都要求「同一类失败，同一个可判定的字段」。

### 3. 公共依赖抽取：`_ensure_space_admin` 与 `SpaceService._ensure_admin` 是同一道门的两份实现

- **适用接口**：#20、#23~#28、#33、#34、#54~#57、#60~#65、#67 走 `_ensure_space_admin`
  （`routes/spaces.py:449-468`）；#15~#18、#40、#41、#43~#45、#47~#49、#51~#53 走服务层
  `_ensure_admin`（`domain/space/services.py:1028-1043`）。两者都调到
  `app/auth/space_access.is_space_admin`，判据本身没分叉，但**门怎么回话**分叉了
  （见上一条），而且服务层那份在 `space_id` 不存在时也答 403 —— 我逐个核对过：
  对「不存在」与「无权」两类输入，两处都给同一个 403，**没有泄漏存在性**，与模块
  「非成员 404」的策略不冲突（它连「存在」都不确认）。
- **做法**：让服务层只做业务、门由路由统一挂（或反过来，两处都调用同一个
  `_ensure_space_admin`-style 帮助函数），错误文案与 `error.data` 从一处出。
- **为什么**：GitHub 的 REST 文档把「同一个授权失败在同一资源下应当一致」写进约定
  （<https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api#403-forbidden>）；
  一份判据两处回话，正是这份代码库自己在 `app/auth/project_access.py` 模块注释里批评过的
  「an answer that lives in three places is three answers」。

### 4. 响应形状：`announcements` / `taskTemplates` 被双重编码成 JSON 字符串

- **适用接口**：#1、#2、#3、#4、#6、#51、#53（凡是返回 `space` 的都带这两个字段）。
- **问题**：`routes/spaces.py:485-486` 的 `json.dumps(space.announcements or [])` 把
  JSONB 列（`domain/space/models.py:61-70`，本来就是 `list`）再序列化成一个**字符串**，
  于是 API 表面类型是 `string`（`frontend/src/types/spaces.ts` 也这样声明），
  客户端必须再 `JSON.parse` 一次；写入路径 `_expect_list`（`:339-352`）也不得不接受
  「list 或 JSON 字符串」两种输入。
- **做法**：直接返回数组（`"announcements": list(space.announcements or [])`），
  写入侧仍可容忍字符串一段时间（读宽写严的反面），前端一处 `JSON.parse` 随之删除。
- **为什么**：GitHub 对「数组字段」从不返回被序列化的字符串，`labels`/`assignees` 都是数组
  （<https://docs.github.com/en/rest/issues/issues>，业界通行，未逐条查证）。
  这是 API 设计里经典的 double-encoding 味道，改成数组后前后端都少一层解析。
- **风险**：这是一个**破坏性响应变更**，必须与前端同版本发布（前端类型声明要一起改
  `announcements: string → unknown[]`）。所以只作为建议提出，不放进高收益清单。

### 5. 条件请求（ETag）与幂等键

- **适用接口**：`GET /spaces/{spaceId}`（#1）适合 ETag；`POST /spaces`（#3）适合幂等键。
- **做法**：`Space` 有 `updated_at`（`_space_to_api_model` 已经在返回 `updatedAt`，`spaces.py:494`），
  可直接作为 ETag 的种子（`W/"space-{id}-{updated_int}"`），并在 `GET /spaces/{spaceId}` 上支持
  `If-None-Match` → 304；`POST /spaces` 接受 `Idempotency-Key` 头（配一张短期键表）避免重复建板。
- **为什么**：GitHub 有专门一节讲条件请求与 ETag
  （<https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests-if-appropriate>），
  Stripe 有 `Idempotency-Key`（业界通行，未逐条查证：<https://docs.stripe.com/api/idempotent_requests>）。
  这两条都是**新增能力**、默认不改变现有行为，可以独立排期，不急。
- **说明**：本项目现有的 `pageStart`/`nextStart` 分页与条件请求不冲突，可并存。
