# users 组 — 逐接口优化分析（69 个接口）

- 负责文件：`backend/app/api/routes/users.py`（4711 行）
- 清单来源：`/tmp/api-review/out/users.list.md`
- 只读分析，未运行测试、未改仓库；行号均指本会话读到的 `users.py` 版本。
- 结论口径：`可优化` / `暂无` / `待确认`。「暂无」= 按 BRIEF 的五个维度读下来没发现值得改的点，不代表接口完美。

---

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | GET | `/users/lookup` | 可优化(低) | 精确匹配且走 `lower(username)`/`lower(email)` 唯一索引，无查询问题；只是 `avatar_id` 是 snake_case，同族其余字段全是 camelCase |
| 2 | DELETE | `/users/me/team-requests/{requestId}` | 暂无 | 204 + 服务层按 `user_id` 限定，越权面已封 |
| 3 | DELETE | `/users/me/teams/{teamId}` | 暂无 | 退队复用 `TeamService`，无额外查询 |
| 4 | POST | `/users/me/team-invitations/{invitationId}/accept` | 暂无 | 204，服务层带 `user_id` 归属校验 |
| 5 | POST | `/users/me/team-invitations/{invitationId}/decline` | 暂无 | 同 4 |
| 6 | GET | `/users/me/team-requests` | 可优化 | `pageStart`/`pageSize` 无 `ge/le`：负值会走到 SQL `OFFSET/LIMIT`，pageSize 无上限；status 解析 20 行与 7 重复 |
| 7 | GET | `/users/me/team-invitations` | 可优化 | 同 6（两段 handler 除类型外逐字相同） |
| 8 | GET | `/users/{userId}/follow/questions` | 可优化 | 与 40 的 handler 逐字重复；不校验目标用户存在（9 校验）；`pageStart` 未校验 |
| 9 | GET | `/users/{userId}/questions` | 可优化 | `pageStart` 未校验（负值→500）；与 8/40 三份同样的 DTO 拼装循环 |
| 10 | GET | `/users/{userId}/answers` | 可优化 | 先全量拉该用户所有答案 id 再 `list.index()` 算游标，用户答案多时线性内存与耗时；`pageStart` 语义与同族其余端点不同 |
| 11 | POST | `/users/verify/email` | 可优化 | 未鉴权、且 409 判定在任何节流之前，等于一个不限速的「这个邮箱注册了吗」判定器 |
| 12 | GET | `/users/registration-config` | 暂无 | 公开只读，无 DB 访问 |
| 13 | POST | `/users` | 暂无 | 校验顺序（邀请码→用户名→密码→邮箱码）合理，`consume_code` 原子；仅响应体 `code` 与 HTTP 状态不一致（见 M3） |
| 14 | GET | `/users/me` | 可优化 | 每次 8 条查询（user+profile+6 个 count），是前端热路径；平台已有条件请求件可挂 ETag |
| 15 | POST | `/users/me/email/code` | 暂无 | 15 分钟登录窗口 + 已有邮箱拒绝 + 配额，检查齐 |
| 16 | POST | `/users/me/email` | 暂无 | 先查重再花码，`IntegrityError` 兜底，顺序正确 |
| 17 | GET | `/users/me/auth-methods` | 暂无 | 2 次查询（count + 2FA 事实），只给本人 |
| 18 | POST | `/users/me/sudo/email-code` | 暂无 | 发信前重查 2FA 与占位邮箱，配额同族共用 |
| 19 | GET | `/users/{userId}` | 可优化(低) | 公开资料页同样触发 6 个 count；可挂 ETag 或让计数可选 |
| 20 | PATCH | `/users/{userId}` | 可优化 | body 是裸 `dict`，`{"nickname":123}` 会在 `normalize_nickname` 的 `.strip()` 上 AttributeError→500 |
| 21 | PUT | `/users/{userId}` | 可优化 | 与 PATCH 函数体逐字相同、参数相同，PUT 并没有「整体替换」语义 |
| 22 | POST | `/users/auth/login` | 可优化(低) | 每次成功登录都铸造并预约一张 sudo 票据（Redis 写）即使不需要；未装 `_require_same_origin` |
| 23 | POST | `/users/auth/verify-2fa` | 可优化(低) | body 裸 `dict` + 手写取值，票据/预算逻辑本身正确 |
| 24 | POST | `/users/auth/email-code` | 暂无 | 配额在查账号之前、邮件 spawn 到响应之后、未知地址同答复，反枚举做对了 |
| 25 | POST | `/users/auth/email-code/verify` | 暂无 | 共享 `email_code` 客户端预算，2FA 分支与密码路径一致 |
| 26 | POST | `/users/auth/refresh-token` | 可优化 | 每次刷新都重算整个 user DTO（8 条查询），而客户端手上已有该对象 |
| 27 | POST | `/users/auth/logout` | 暂无 | 幂等，清除 cookie，装了同源校验 |
| 28 | POST | `/users/auth/sudo` | 可优化(低) | 160 行 if/elif 单函数，四种方法各自开 Redis、各自校验，可拆 |
| 29 | GET | `/users/{userId}/identity` | 可优化 | `sudoTicket` 走 query string：凭据进 URL、进访问日志、进 Referer |
| 30 | PUT | `/users/{userId}/identity` | 暂无 | 票据在 body、先验票后写，正确 |
| 31 | PATCH | `/users/{userId}/identity` | 可优化(低) | 读-改-写两步，两并发 PATCH 会互相覆盖 |
| 32 | DELETE | `/users/{userId}/identity` | 暂无 | 独立 purpose 的票据，语义清楚 |
| 33 | GET | `/users/{userId}/identity/access-logs` | 可优化(低) | `pageStart` 未校验；`pageSize` 已有 `le=200` |
| 34 | GET | `/users/me/sessions` | 暂无 | 2 条查询，`current`/`trusted` 标记一次算清 |
| 35 | DELETE | `/users/me/sessions/{sessionId}` | 暂无 | 返回体而非 204 与同族 3/4 不一致，但带 `user_id` 过滤，安全面正确 |
| 36 | DELETE | `/users/me/sessions` | 暂无 | `keep=current` 语义正确 |
| 37 | POST | `/users/recover/password/request` | 暂无 | 配额先花、邮件 spawn、未知地址同答复 |
| 38 | POST | `/users/recover/password/verify` | 暂无 | 一次性 token + 全量吊销会话/信任设备 |
| 39 | PATCH | `/users/{userId}/password` | 暂无 | 票据 + 保留当前会话的吊销策略一致 |
| 40 | GET | `/users/{userId}/favorites/questions` | 可优化 | 与 8 逐字重复（问题只有「关注」一种收藏关系，语义没错，是代码重复） |
| 41 | GET | `/users/{userId}/favorites/answers` | 可优化 | 循环内每条答案一次 `get_profile_by_user_id`，页大小 100 就是 100 次往返 |
| 42 | GET | `/users/{userId}/settings` | 可优化 | 返回写死的四个默认值，不是账户真实设置 |
| 43 | PATCH | `/users/{userId}/settings` | 可优化 | 不回写任何存储，把入参回显当成「保存成功」 |
| 44 | GET | `/users` | 可优化 | 最重：每行 2 次 `get_by_id` + 每次 `build_user_dto` 6 个 count（8N+1 条）；`q` 在 Python 里过滤；`hasMore` 用 `returned == page_size` 判，过滤后失真 |
| 45 | POST | `/users/{userId}/2fa/enable` | 暂无 | 两段式：第一段花票，第二段只认第一段存下的 pending secret，不可跳过 |
| 46 | POST | `/users/{userId}/2fa/disable` | 可优化 | `_notify_2fa_disabled` 在请求路径上 await SMTP（单次超时 30s，回退发送器最多两次），响应会被邮件拖住 |
| 47 | GET | `/users/{userId}/2fa/status` | 暂无 | 2 次查询，只给本人 |
| 48 | POST | `/users/{userId}/2fa/backup-codes` | 暂无 | 票据 purpose 独立，重发即作废旧码 |
| 49 | POST | `/users/{userId}/passkeys/options` | 暂无 | 花票 + challenge 存 Redis 300s，单次注册绑定 |
| 50 | POST | `/users/{userId}/passkeys` | 暂无 | challenge 存在性检查在验证之前，且一次性删除 |
| 51 | POST | `/users/{userId}/passkeys/prompt/dismiss` | 暂无 | 只在 offer 到期时计数，双击不会推进轮次 |
| 52 | POST | `/users/auth/passkey/options` | 暂无 | challenge 随机、300s TTL；存进 Redis 的 `userId` 目前没被校验方使用（死数据，非漏洞） |
| 53 | POST | `/users/auth/passkey/verify` | 暂无 | 断言是签名不是口令，无口令猜测面；登录方式计入 sudo 窗口 |
| 54 | GET | `/users/{userId}/passkeys` | 暂无 | 单查询 + 只给本人 |
| 55 | DELETE | `/users/{userId}/passkeys/{credentialId}` | 暂无 | 花票 + 删除按 `user_id` 限定 |
| 56 | GET | `/users/auth/oauth/providers` | 暂无 | 只回 id/name/scope，未泄漏 secret |
| 57 | GET | `/users/auth/oauth/login/{providerId}` | 暂无 | state 服务端预约 + httpOnly cookie 绑定浏览器，回跳路径按 provider 的 redirect_url |
| 58 | GET | `/users/auth/oauth/callback/{providerId}` | 可优化(低) | `except Exception` 把任何异常转成 302 错误页（含编程错误）；`state` 明文在 URL |
| 59 | GET | `/users/auth/oauth/state` | 可优化 | 未鉴权 + bearer 票据在 query string + 解码不消费，票据 15 分钟内可被任何拿到 URL 的人重放读取；响应无 `Cache-Control: no-store` |
| 60 | POST | `/users/auth/oauth/email/code` | 暂无 | 只认已签发的 stateToken，受地址配额限制 |
| 61 | POST | `/users/auth/oauth/email/verify` | 暂无 | 邮箱属于他人时转 ownership 页，不按邮箱静默绑定 |
| 62 | POST | `/users/auth/oauth/verify` | 暂无 | pending session 取走即删（MULTI/EXEC），密码尝试走登录同一个 delay |
| 63 | POST | `/users/oauth/create` | 可优化(低) | 145 行单函数；票据在诸多检查之后才消费（与 64 相反）；`except Exception` 一律 CREATION_FAILED |
| 64 | POST | `/users/oauth/bind` | 可优化 | 票据在密码校验之前就被消耗：密码打错一次，整个 OAuth 决策流程作废；且直接用了 `auth_service._user_repo` 私有属性 |
| 65 | GET | `/users/invite-codes` | 可优化 | 被更早注册的 `GET /users/{userId}` 遮蔽，实际不可达（`invite-codes` 落到 int 转换上回 400），代码注释里已承认 |
| 66 | POST | `/users/invite-codes` | 可优化(低) | `maxUses` 无 `ge/le`，可传 0 或负数或极大值 |
| 67 | DELETE | `/users/invite-codes/{code_id}` | 可优化(低) | 不存在的 id 也回 200 成功，DELETE 的幂等/404 语义应说明 |
| 68 | GET | `/users/{userId}/oauth/connections` | 暂无 | 只回元数据，注释明确不回 token，只给本人 |
| 69 | DELETE | `/users/{userId}/oauth/connections/{connectionId}` | 暂无 | 花票 + 「最后一条登录途径」行锁保护，做得比同族细 |

统计：可优化 31，暂无 38，待确认 0。

---

## 二、详细分析（按收益从高到低）

### GET /users

- **现状**：`users.py:3105`，`profile_repo.list_profiles(limit, offset)` 取一页 profile，然后逐行处理。
- **问题**（数字都可复核）：
  - `users.py:3128` 在 `q` 分支里对每个 profile 调一次 `user_repo.get_by_id`；`users.py:3138` 又在主循环里对同一个 `profile.user_id` 再调一次 — 同一行两次查询。
  - `users.py:3140` 每行调 `auth_service.build_user_dto`，而 `domain/user/services.py:474` 里 `self._stats_repo.aggregate(user.id)` 是 6 条独立 count（`domain/user/repositories.py:644-650`：teams、task_memberships、knowledge、submissions、questions、answers 各一条）。所以一页 100 人 = 2×100 + 6×100 + 1 = 801 条语句；`pageSize` 上限就是 100。
  - `users.py:3125-3134`：`q` 在 Python 里过滤**已经分页后的那一页**，所以「命中数」取决于第几页，`total` 也不存在；`users.py:3151` 的 `hasMore: returned == page_size` 在过滤后必然误判（过滤掉一行就以为到底了）。
- **优化**：把 join + 过滤交给 SQL，把 6 个 count 合并成 6 条 group by。

  `domain/user/repositories.py`（`UserProfileRepository` 内新增，类里已有 `Select/and_/func/or_/select` 的 import）：

  ```python
  async def list_profiles_with_users(
      self, *, q: str | None, limit: int, offset: int
  ) -> tuple[list[tuple[User, UserProfile]], int]:
      """One page of (User, UserProfile) + total, searched in SQL.

      ``q`` is matched on username and nickname the same way the caller used to
      do it in Python — the difference is that the match now picks the page
      instead of being applied to one.
      """
      filters = [User.deleted_at.is_(None), UserProfile.deleted_at.is_(None)]
      if q:
          pattern = (
              q.lower()
              .replace("\\", "\\\\")
              .replace("%", "\\%")
              .replace("_", "\\_")
          )
          filters.append(
              or_(
                  func.lower(User.username).like(f"%{pattern}%", escape="\\"),
                  func.lower(UserProfile.nickname).like(f"%{pattern}%", escape="\\"),
              )
          )
      base = select(User, UserProfile).join(
          UserProfile, UserProfile.user_id == User.id
      )
      rows = (
          await self._session.execute(
              base.where(*filters).order_by(User.id.asc()).limit(limit).offset(offset)
          )
      ).all()
      count_stmt = (
          select(func.count())
          .select_from(User)
          .join(UserProfile, UserProfile.user_id == User.id)
          .where(*filters)
      )
      total = int((await self._session.execute(count_stmt)).scalar_one() or 0)
      return [(u, p) for u, p in rows], total
  ```

  `domain/user/repositories.py`（`UserStatisticsRepository` 内新增批量版，沿用文件里既有的「局部 import 避免环」写法）：

  ```python
  _COUNTER_SOURCES = (
      # (输出键, 仓储方法要数的那张表)  —— 与既有 count_* 一一对应
      ("teamCount", "teams"),
      ("taskParticipationCount", "tasks"),
      ("knowledgeCount", "knowledge"),
      ("submissionCount", "submissions"),
      ("questionCount", "questions"),
      ("answerCount", "answers"),
  )

  async def aggregate_many(
      self, user_ids: Sequence[int]
  ) -> dict[int, dict[str, int]]:
      """The six counters for many users: six grouped queries, not six each."""
      from app.domain.task.models import TaskMembership, TaskSubmission
      from app.domain.team.models import TeamUserRelation

      if not user_ids:
          return {}
      ids = list(user_ids)
      out = {uid: dict.fromkeys((k for k, _ in _COUNTER_SOURCES), 0) for uid in ids}
      grouped = {
          "teams": select(TeamUserRelation.user_id, func.count()).where(
              TeamUserRelation.user_id.in_(ids),
              TeamUserRelation.deleted_at.is_(None),
          ),
          "tasks": select(TaskMembership.member_id, func.count()).where(
              TaskMembership.member_id.in_(ids),
              TaskMembership.is_team.is_(False),
              TaskMembership.deleted_at.is_(None),
          ),
          "knowledge": select(Knowledge.created_by, func.count()).where(
              Knowledge.created_by.in_(ids), Knowledge.deleted_at.is_(None)
          ),
          "submissions": select(TaskSubmission.submitter_id, func.count()).where(
              TaskSubmission.submitter_id.in_(ids),
              TaskSubmission.deleted_at.is_(None),
          ),
          "questions": select(Question.created_by_id, func.count()).where(
              Question.created_by_id.in_(ids), Question.deleted_at.is_(None)
          ),
          "answers": select(Answer.created_by_id, func.count()).where(
              Answer.created_by_id.in_(ids), Answer.deleted_at.is_(None)
          ),
      }
      for key, stmt in grouped.items():
          for uid, n in (
              await self._session.execute(stmt.group_by(stmt.selected_columns[0]))
          ).all():
              out[uid][key] = int(n)
      return out
  ```

  `domain/user/services.py:466` 让 `build_user_dto` 能收下算好的计数（既有调用点零改动）：

  ```python
  async def build_user_dto(
      self,
      user: User,
      profile: UserProfile,
      viewer_id: int | None = None,
      *,
      stats: dict[str, int] | None = None,
  ) -> dict:
      base = self._base_user_dto(user, profile)
      stats = stats if stats is not None else await self._stats_repo.aggregate(user.id)
      ...
  ```

  `users.py:3105`：

  ```python
  async def list_users(
      q: str | None = Query(default=None),
      page_start: int = Query(default=0, ge=0, alias="pageStart"),
      page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
      auth_user: AuthUserInfo = Depends(require_auth_user),
      db=Depends(get_db),
  ) -> dict:
      """List users with optional search query."""
      profile_repo = UserProfileRepository(session=db)
      stats_repo = UserStatisticsRepository(session=db)
      auth_service = UserAuthService(
          user_repo=UserRepository(session=db),
          profile_repo=profile_repo,
          stats_repo=stats_repo,
      )
      pairs, total = await profile_repo.list_profiles_with_users(
          q=q, limit=page_size, offset=page_start
      )
      stats = await stats_repo.aggregate_many([u.id for u, _ in pairs])
      users = [
          await auth_service.build_user_dto(
              user=user,
              profile=profile,
              viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
              stats=stats.get(user.id),
          )
          for user, profile in pairs
      ]
      returned = len(users)
      has_more = page_start + returned < total
      return {
          "code": 200,
          "message": "OK",
          "data": {
              "users": users,
              "page": {
                  "pageStart": page_start,
                  "pageSize": returned,
                  "hasMore": has_more,
                  "nextStart": page_start + returned if has_more else None,
                  "total": total,
              },
          },
      }
  ```

  效果：一页 100 人从 801 条降到 8 条（1 页 + 1 count + 6 group by），且 `q` 的分页语义变成真的。
- **契约**：`pageStart` 由「无默认」变成 `default=0`（原为 `None`→0，等价）；`page` 里**新增 `total`**（纯增量）；`hasMore/nextStart` 在带 `q` 时答案会变（原来错，现在对）——这是唯一的行为变化，需要通知客户端。无数据迁移。
- **测试**：`backend/tests/integration/test_user_directory.py`
  - `test_list_users_costs_a_constant_number_of_queries`：用 `tests/integration/test_teaching_context.py:106` 的 `event.listen(engine, "before_cursor_execute", _count)` 记语句，插 30 个用户，断言 `GET /users?pageSize=30` 的语句数 ≤ 10（改前是 200+）。
  - `test_search_pages_in_the_database`：`q` 命中第 2 页的人时仍能返回，断言 `total` 与 `hasMore`。
  - `test_page_start_must_not_be_negative`：`?pageStart=-1` 断言 422（现在是 500，见 M2）。

### GET /users/{userId}/favorites/answers

- **现状**：`users.py:3003`，`answer_repo.list_favorites_by_user` 拿一页答案（一次查询），随后拼 DTO。
- **问题**：`users.py:3022` `profile = await profile_repo.get_profile_by_user_id(row.created_by_id)` 在 `for row in rows` 循环体内 — 收藏的答案来自不同作者，所以每条一次查询。`pageSize` 上限 100 ⇒ 1 + 100 条语句。（对照：`users.py:1167` 的 `/answers` 把 profile 查询提到循环外，因为那里的答案作者恒为同一人；收藏列表作者不同，所以偷懒会在这一条上暴露。）
- **优化**：一次批量取作者 profile（同文件 `teams.py:366` 已在用这个方法）。

  ```python
      rows, total = await answer_repo.list_favorites_by_user(
          user_id=user_id, limit=page_size, offset=offset
      )
      # One query for the whole page's authors, not one per answer.
      profiles = await profile_repo.get_profiles_by_user_ids(
          list({row.created_by_id for row in rows})
      )

      def _sender(user_id_of_author: int) -> dict | None:
          profile = profiles.get(user_id_of_author)
          if profile is None:
              return None
          return {
              "id": profile.user_id,
              "nickname": profile.nickname,
              "avatarId": profile.avatar_id,
              "intro": profile.intro,
          }

      answers = []
      for row in rows:
          ...
          dto = {
              ...
              "sender": _sender(row.created_by_id),
              ...
          }
  ```
- **契约**：无变化（`sender` 仍可能为 null）。
- **测试**：`backend/tests/integration/test_user_favorites.py::test_favorite_answers_cost_one_profile_query`：造 3 个作者各收藏若干，断言语句数不随页大小增长；另测 `pageSize` 内只有 1 个作者时不多查。

### GET /users/{userId}/answers

- **现状**：`users.py:1132`，先用 `answer_repo.list_all_answer_ids_by_user(user_id)`（`domain/answers/repositories.py:282`）把该用户**全部**答案 id 拉成 list，再用 `all_ids.index(page_start)` 定位、切片算 `hasMore`，最后才用 cursor 查真正的那一页。
- **问题**：
  - `users.py:1149` 与 `domain/answers/repositories.py:287-291`：一次无 limit 的全量 id 扫描 — 一个写了 5000 条答案的用户，每次翻 20 条都要把这 5000 个 id 拉回来、在 Python 里建 list。
  - `users.py:1153` `all_ids.index(page_start)` 是 O(n) 线性查找，找不到时静默 `start_idx = 0`（`users.py:1155`），客户端游标过期会莫名回到第一页。
  - `users.py:1196-1198` 响应里的 `pageStart` 是该页第一行的**答案 id**，而 `/{userId}/questions`（`users.py:1047`）、`/favorites/*`（`users.py:2983`）里的 `pageStart` 是**偏移量**。同名参数在同一个 router 前缀下两种语义，客户端只能靠接口各自记。
- **优化**：改成 keyset，多取一行来判 `hasMore`，`nextStart`/`pageStart` 的取值与现状**完全一致**（`nextStart` 原本就是 `all_ids[end_idx]`，即下一页第一行的 id），同时删掉全量扫描。

  ```python
      # No full-id scan: one extra row answers "is there a next page".
      rows, total = await answer_repo.list_by_user(
          user_id=user_id, limit=page_size + 1, cursor=page_start or None
      )
      has_more = len(rows) > page_size
      next_start = rows[page_size].id if has_more else 0
      rows = rows[:page_size]

      returned = len(rows)
      page = {
          "pageStart": rows[0].id if rows else 0,
          "pageSize": returned,
          "hasMore": has_more,
          "nextStart": next_start,
          "total": total,
      }
  ```

  `domain/answers/repositories.py:294` 的 `limit` 语义仍是原样（`LIMIT limit`），只需调用方传 `page_size + 1`；`list_all_answer_ids_by_user` 全仓只有这一处调用（`grep -rn` 确认），可以一并删除。
- **契约**：正常游标下响应逐字段不变；差异只在「客户端传来的 id 已不存在/乱填」时：旧代码回到第一页，新代码回空页（游标语义下这是更正确的答案）。`pageStart` 仍是答案 id，所以**没有**统一到 offset（想统一是另一个破坏性变更，见 M2）。无迁移。
- **测试**：`backend/tests/integration/test_user_answers_paging.py`
  - `test_answers_page_does_not_scan_every_id`：造 50 条答案，断言 `GET /users/{id}/answers?pageSize=20` 的语句数 ≤ 3（改前固定 3 条但其中一条返回 50 行，用语句数抓不到 —— 改用断言那条 SQL 带 `LIMIT`：监听 `before_cursor_execute` 收集 statement，断言其中没有不带 limit 的 `SELECT answer.id`）。
  - `test_answers_cursor_walks_pages_without_overlap`：按 `nextStart` 连翻三页，断言 id 不重不漏、`total` 恒定。
  - `test_stale_cursor_returns_empty_page`。

### GET /users/{userId}/favorites/questions 与 GET /users/{userId}/follow/questions

- **现状**：`users.py:2944` 与 `users.py:1008` 两个 handler，除 `summary`、`message` 和 `q`/分页参数写法外逐字相同（各约 55 行），都调 `question_repo.list_followed`（`domain/questions/repositories.py:163`）。
- **问题**：
  - 语义本身没错：仓库里没有任何 `QuestionFavorite` 模型（`grep -rn "QuestionFavorite" app/` 无命中），问题只有「关注」一种收藏关系。
  - 真正的问题是两份一模一样的 55 行：DTO 拼装、`page` 计算、`hasMore/nextStart` 逻辑各写一遍，改一处必漏另一处；`follow/questions` 还少了 `questions` 分支里的用户存在性校验（`users.py:1077-1079`），同一个 `/users/{userId}/...` 前缀下对一个不存在的 userId 一个回 404、一个回空列表。
- **优化**：抽一个私有函数，两个路由各留 3 行。

  ```python
  _QUESTION_LIST_FIELDS = (
      "id", "title", "type", "group_id", "bounty", "accepted_answer_id", "created_by_id",
  )

  async def _question_page(
      *, db: AsyncSession, user_id: int, page_start: int | None, page_size: int
  ) -> dict:
      """The one shape both `/follow/questions` and `/favorites/questions` return."""
      rows, total = await QuestionRepository(session=db).list_followed(
          user_id=user_id, limit=page_size, offset=page_start or 0
      )
      topic_map = await QuestionTopicRepository(session=db).list_topic_ids(
          [row.id for row in rows]
      )
      offset = page_start or 0
      questions = [
          {
              "id": row.id,
              "title": row.title,
              "content": None,
              "type": row.type,
              "groupId": row.group_id,
              "bounty": row.bounty,
              "acceptedAnswerId": row.accepted_answer_id,
              "createdBy": row.created_by_id,
              "createdAt": int(row.created_at.timestamp() * 1000) if row.created_at else 0,
              "updatedAt": int(row.updated_at.timestamp() * 1000) if row.updated_at else 0,
              "topicIds": topic_map.get(row.id, []),
          }
          for row in rows
      ]
      returned = len(questions)
      has_more = offset + returned < total
      return {
          "questions": questions,
          "page": {
              "pageStart": offset,
              "pageSize": returned,
              "hasMore": has_more,
              "nextStart": offset + returned if has_more and returned > 0 else None,
              "total": total,
          },
      }
  ```

  `/{userId}/questions`（`users.py:1067`）的循环与这个几乎相同，只是仓储方法换成 `list_by_user`，可以再抽一层「把 rows 变 DTO」。
- **契约**：无变化（`follow/questions` 仍不校验用户存在）。若要抹平 404/空列表的差异，那是行为变更，需一并决定。
- **测试**：`backend/tests/integration/test_user_question_lists.py`：`test_followed_and_favorites_return_the_same_shape`（两个端点响应的键集合完全相同）、`test_unknown_user_is_404_or_empty_consistently`（把现状钉住，改语义时才需要改这条）。

### PATCH /users/{userId} 与 PUT /users/{userId}

- **现状**：`users.py:1754`、`users.py:1775`，都是 `payload: dict = Body(...)`，都只取 `nickname/intro/avatarId`，都调 `profile_service.update_profile`。
- **问题**：
  - `users.py:1762` 与 `users.py:1783` 的函数体逐字相同：PUT 声明为「Update user profile (full)」，行为却是 patch——没有缺省字段清空、也没有必填校验，`PUT` 的「整体替换」语义完全没有兑现。
  - `payload: dict` 没有 schema：`{"nickname": 123}` 会走到 `domain/user/services.py:271` 的 `normalize_nickname`，`(123).strip()` → `AttributeError` → 500；`{"avatarId": "abc"}` 会以字符串写进 `Integer` 列，由 asyncpg 抛 `DataError` → 500。请求体里也没有长度上限，`intro` 可传任意长的文本。
- **优化**：给 PATCH 一个 Pydantic 模型（文件顶部已经有十个同类模型，风格现成），PUT 复用同一个并补上它声称的语义。

  ```python
  class UpdateProfileRequest(BaseModel):
      model_config = ConfigDict(populate_by_name=True)

      nickname: str | None = Field(default=None, min_length=1, max_length=NICKNAME_MAX_LENGTH)
      intro: str | None = Field(default=None, max_length=500)
      avatar_id: int | None = Field(default=None, alias="avatarId", ge=1)
  ```

  ```python
  @router.patch("/{userId}", summary="Update user profile (partial)")
  async def patch_user_profile(
      user_id: Annotated[int, Path(ge=1, alias="userId")],
      payload: UpdateProfileRequest,
      auth_user: AuthUserInfo = Depends(require_auth_user),
      profile_service: UserProfileService = Depends(get_user_profile_service),
  ) -> dict:
      if auth_user.user_id != user_id:
          raise ForbiddenError("Only the user themselves can update their profile.")
      await profile_service.update_profile(
          user_id=user_id,
          nickname=payload.nickname,
          intro=payload.intro,
          avatar_id=payload.avatar_id,
      )
      return {"code": 200, "message": "Success", "data": {}}
  ```

  PUT 要么按自己的声明实现（缺省字段置空、`nickname` 必填），要么删掉——两个都留、行为又相同，只会让人以为 PUT 更严格。若要保留兼容，最小一步是让 PUT 继承这个模型并把 `nickname` 设为必填：

  ```python
  class ReplaceProfileRequest(UpdateProfileRequest):
      nickname: str = Field(..., min_length=1, max_length=NICKNAME_MAX_LENGTH)
      intro: str = Field(default="", max_length=500)  # 未给的字段是「清空」而非「保持」
  ```
  并把 `UserProfileService.update_profile` 的 `if x is not None` 语义换成显式 set（否则 PUT 传空串也会被当成「不改」）。
- **契约**：请求体从「任意 JSON」收窄成有 schema 的对象：非法类型从 500 变成 422，这是修复而非破坏；`avatarId=0` 原本允许、改后 422（`avatars.id` 从 1 起，无实际影响）。PUT 若改成真替换，需要客户端确认。
- **测试**：`backend/tests/integration/test_user_profile.py::test_update_user_profile_rejects_a_non_string_nickname`（断言 422 而非 500）、`::test_avatar_id_must_be_an_integer`、`backend/tests/contract/test_users_contract.py::test_python_put_profile_requires_a_nickname`。

### GET /users/{userId}/settings 与 PATCH /users/{userId}/settings

- **现状**：`users.py:3066` 返回写死的 `{emailNotification: True, pushNotification: True, language: "zh-CN", theme: "light"}`；`users.py:3085` 把入参回显一遍，不入库、下面也没有任何存储。
- **问题**：`users.py:3072-3077` 与 `users.py:3092-3097` — 这两个端点没有任何持久化。GET 永远返回默认值（用户改过的开关下次读还是 True），PATCH 返回 200 和它自己收到的值，客户端据此显示「已保存」。仓库里也没有可复用的用户设置表（`grep __tablename__ app/domain/user/models.py` 只有 user / user_profile / real_name / two_factor / backup_code / sessions / trusted_devices），所以这不是「接错了表」，是功能没实现。仓库里也没有任何调用方（前端 `grep -rn "favorites" frontend/src/api.ts` 无命中，这两个端点连前端都没接）。
- **优化**：两条路，二选一，别维持现状。
  1. 真做：新增 `user_setting` 表（`user_id` 主键 + 四列，`migrations` 一支 alembic），GET 读、PATCH 用 upsert 写。

     ```python
     ### domain/user/models.py
     class UserSetting(Base):
         __tablename__ = "user_setting"

         user_id: Mapped[int] = mapped_column(
             Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
         )
         email_notification: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
         push_notification: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
         language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
         theme: Mapped[str] = mapped_column(String(16), nullable=False, default="light")
     ```
     PATCH 的 upsert 沿用文件里已有的 `postgresql.insert` 写法（`domain/passkey/prompt.py:11` 就是这么做的）。
  2. 不做：删掉这两条路由，让「设置」回到它真正的归属（分页/主题在前端本地、通知开关在通知域）。返回假数据比回 404 更坏 —— 它会让调用方以为配置生效了。
- **契约**：只有路线 1 保住现有契约（GET/PATCH 不变，多一次迁移）；路线 2 是删除接口。
- **测试**：`backend/tests/integration/test_user_settings.py::test_settings_survive_a_reread`（PATCH `theme=dark` 后 GET 必须是 `dark`，这条今天会红）、`::test_settings_are_owner_only`。

### GET /users/me（以及 POST /users/auth/refresh-token 的同一笔开销）

- **现状**：`users.py:1506`，`auth_service.get_user_with_profile` + `build_user_dto`；refresh 在 `users.py:2274` 做同样的事。
- **问题**：`build_user_dto` 每次 6 条 count（见 GET /users 一条）＋ user/profile 各一条 ⇒ 每次 `/users/me` 8 条语句。它是前端每次加载都要打的端点；`/auth/refresh-token`（`users.py:2274`）在每次换令牌时也重算整份 DTO —— 那个响应里客户端**已经**有 user 对象了（登录时刚拿过），6 条 count 纯属重复。仓库里已有条件请求件却没挂（`app/api/conditional.py` 的 `etag_for_json` + `if_none_match_hits`，`api/routes/admin_members.py:61` 已经在用）。
- **优化**：
  - 短平快：refresh 的响应不再回 user（`users.py:2274-2285` 删掉 `user_dto` 那段），契约上 `data.accessToken` 足够，客户端 login 时已存过 user。
  - 更进一步：`/users/me` 挂 ETag/304。

    ```python
    from app.api.conditional import etag_for_json, if_none_match_hits   # users.py 顶部

    async def get_current_user(
        request: Request,
        response: Response,
        auth_user: AuthUserInfo = Depends(require_auth_user),
        auth_service: UserAuthService = Depends(get_user_auth_service),
    ) -> dict | Response:
        user, profile = await auth_service.get_user_with_profile(auth_user.user_id)
        user_dto = await auth_service.build_user_dto(
            user=user, profile=profile, viewer_id=auth_user.user_id
        )
        payload = {"code": 200, "message": "Query current user successfully.",
                   "data": {"user": user_dto}}
        etag = f'"{etag_for_json(payload)}"'
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "private, no-cache"
        if if_none_match_hits(request.headers.get("if-none-match", ""), etag.strip('"')):
            return Response(status_code=304, headers={"ETag": etag})
        return payload
    ```
    （`api/conditional.py:40` 的 `etag_for_json` 返回不带引号的摘要，`if_none_match_hits` 比较的也是不带引号的，照上面这段用即可。）
- **契约**：`/users/me` 多出 `ETag` 头与 304 分支（增量）；refresh 去掉 `data.user` 是**破坏性变更**，需客户端确认，或者先只做 `/users/me` 的 ETag。
- **测试**：`backend/tests/integration/test_user_me_etag.py::test_second_call_with_if_none_match_is_304`、`::test_etag_changes_when_the_profile_changes`（先例：`tests/integration` 里已有 admin 名单的条件请求测试）。

### POST /users/verify/email（账号枚举）

- **现状**：`users.py:1280`，公开端点（无 `Depends(require_auth_user)`），先校验邀请码与邮箱格式，再 `user_repo.is_email_taken(email)`。
- **问题**：`users.py:1318-1320`：邮箱已注册 → `ConflictError("Email already registered")`（409）；未注册 → 走到 `users.py:1322` 才连 Redis 发码，回 201。这个 409 分支**完全不碰 Redis**，也就是不受地址配额与客户端预算限制 —— 任何人都能用一个请求一秒几十次地问「这个邮箱注册了吗」。同一份代码在别处明确反对这件事：`users.py:2065-2071`（sign-in code）与 `users.py:2756-2762`（找回密码）都写了「答复必须一致，否则等于告诉调用方这个地址有没有账号」。注册流程确实想早告诉用户「邮箱被占了」，但那个答复可以放在**验证码通过之后**（`users.py:1450` 的 `register_with_password` 已经会回 `EMAIL_TAKEN`）。
- **优化**：把存在性检查从发码前挪到验码后（或至少挪到 `send_verification_code` 之后），保持「已注册」由 `POST /users` 回答。

  ```python
      email = payload.email
      ...
      # 存在性判定不再放在这里：它是免鉴权、不花配额的枚举入口。
      # 「已注册」由验码后的 POST /users 回答（见 email_taken 分支）。
      redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
      try:
          service = EmailVerificationService(redis)
          await service.send_verification_code(email, resolved_client_address(request))
      finally:
          await redis.aclose()
  ```

  如果产品上必须即时提示，第二步是给它加同一套节流：`ClientFailureBudget(redis, "email_code")` 的 `spend` 放在 `is_email_taken` 之前，把枚举也计入预算。
- **契约**：无变化（`POST /users` 已回 `EMAIL_TAKEN`）；用户体验上「填了已注册邮箱」从「点发码就报错」变成「收到码、再提交时才知道」，需要产品确认。
- **测试**：`backend/tests/integration/test_account_email.py::test_registered_address_is_not_an_oracle`（对同一个邮箱连打 N 次，断言没有 409；或断言 409 也被计入地址预算）、`::test_unknown_address_and_registered_address_answer_the_same`。

### POST /users/oauth/bind（票据被过早消耗）

- **现状**：`users.py:4506`，先 `_redeem_oauth_state_token(jti)`（`users.py:4519`），再 `_spend_oauth_password_attempt`、查用户、验密码。
- **问题**：
  - `users.py:4519` 在密码校验之前就把一次性 stateToken 花掉了。这个票据是决策页 URL 里的那一个（TTL 15 分钟，只能消费一次，见 `users.py:3728-3736` 的注释），所以**密码打错一次，整个 OAuth 流程作废**，用户必须回到起点重新走一遍 provider 授权。对照 `oauth_create_user`：同样的票据在 `users.py:4427` 才消费，前面那些检查（用户名格式、邀请码、占用）都不花票据。
  - `users.py:4525` `await auth_service._user_repo.get_by_username(username)` — 路由直接伸手进 service 的私有属性。`UserAuthService` 没有公开的按用户名取用户的方法，这是个该补的口子。
- **优化**：把消费挪到验密成功之后，并给 service 补一个公开方法。

  ```python
  ### domain/user/services.py
      async def get_user_by_username(self, username: str) -> User | None:
          return await self._user_repo.get_by_username(username)
  ```

  ```python
      if not await _spend_oauth_password_attempt(username):
          return _oauth_too_many_attempts_redirect()

      user = await auth_service.get_user_by_username(username)
      # Unknown users and wrong passwords get the same answer.
      if user is None or not await auth_service.verify_password(user, password):
          return _oauth_error_redirect("INVALID_CREDENTIALS", "Invalid credentials")
      await _clear_oauth_password_attempts(username)
      # 只在真的要用它绑定的时候才花掉这张票：打错一次密码不该让用户
      # 重走一遍 provider 授权。
      if not await _redeem_oauth_state_token(jti):
          return _oauth_error_redirect("TOKEN_EXPIRED", "Session expired")
  ```

  注意顺序的代价：失败重试期间票据和重试预算同时存在，所以暴力破解的边界仍由 `_spend_oauth_password_attempt`（登录同一个 `LoginDelay`）守着，票据不再是那道的约束——这正是把消费后移的前提。
- **契约**：无变化（错误码与状态码不变）；行为变化是「密码打错后可重试」，需安全上确认可接受。
- **测试**：`backend/tests/integration/test_oauth_client_flow.py::test_bind_typo_does_not_burn_the_decision_token`（先用错密码、再用对的，断言第二次成功）、`::test_bind_without_a_token_is_refused`。

### GET /users/{userId}/identity（票据走 query string）

- **现状**：`users.py:2484`，`sudo_ticket: str | None = Query(default=None, alias="sudoTicket")`，`precise=true` 时 `_spend_sudo_ticket`。
- **问题**：`users.py:2492` 把一个 bearer 凭据放在 URL 里。URL 会进：访问日志、浏览器历史、`Referer`（这一页还会跳转到别的资源）、以及前端的「复制链接」。同文件其余五处需要 sudo 票据的写操作都在请求体里（`users.py:2632`、`3309`、`3385`、`3437`、`3681` 的 `SudoTicketRequest`），只有这一条 GET 走 query —— 大概是为了「GET 没有 body」，但这不是理由：真实姓名的明文读取本来就不该用 GET。另外 URL 里的票据同时也就有了长度与转义问题。
- **优化**：把精确读取改成 POST（或让 GET 只读掩码版，精确版单独一个端点），票据进 body；顺带利用平台已有的 `SudoTicketRequest`。

  ```python
  @router.post(
      "/{userId}/identity/precise",
      summary="Read the unmasked identity (re-authenticated)",
  )
  async def read_precise_identity(
      user_id: Annotated[int, Path(ge=1, alias="userId")],
      payload: SudoTicketRequest,                      # 票据在 body，不进 URL
      request: Request,
      accessReason: str | None = Body(default=None, alias="accessReason"),
      auth_user: AuthUserInfo = Depends(require_auth_user),
      realname_service: UserRealNameService = Depends(get_user_realname_service),
  ) -> dict:
      if auth_user.user_id != user_id:
          raise ForbiddenError("Only the user themselves can view identity.")
      await _spend_sudo_ticket(
          payload.sudo_ticket, user_id=user_id, purpose=SudoPurpose.REALNAME_VIEW
      )
      ...
  ```

  过渡期最小改动：保留 GET，但只在 `accessType == "VIEW"` 且不带 `precise` 时可用，`precise=true` 一律 400 提示走新端点。
- **契约**：新端点，旧 GET 的 `precise` 参数下线 → 前端要改一处；无迁移。
- **测试**：`backend/tests/integration/test_realname_sudo.py`（已存在该类覆盖）加 `::test_precise_read_takes_the_ticket_in_the_body`、`::test_precise_read_without_ticket_is_403`，并在 `backend/tests/contract/test_users_contract.py` 加响应结构断言。

### POST /users/{userId}/2fa/disable（请求路径上等 SMTP）

- **现状**：`users.py:3307`，`_notify_2fa_disabled(user.email, user.username)` 在 `users.py:3342` 被 await。
- **问题**：`_notify_2fa_disabled`（`users.py:3247`）里面 `await get_email_sender().send(...)`（`users.py:3296`），而 `core/email.py:88` 是 `aiosmtplib.send(..., timeout=30)`，且 `get_email_sender()` 可能返回 `FallbackEmailSender`（`core/email.py:144`），失败时**再发一次**——最坏 60 秒挂在这个响应上。同文件里另外两处邮件都刻意挪出了请求路径：`users.py:2140` 用 `spawn(_mail_sign_in_code(...))`、`users.py:2854` 用 `spawn(_send_recovery_mail(...))`，注释写明是为了不让响应时长随邮件变化。2FA 关闭是安全敏感操作，但「通知已发出」不是这次响应能保证的事，拖住它没有收益。
- **优化**：与其他两处一致，换成 spawn；先取出邮件信息（spawn 之后会话可能已关闭）。

  ```python
      user, _profile = await auth_service.get_user_with_profile(auth_user.user_id)
      # 邮件的成败不影响这次「已关闭」的答复：发送挪到响应之后，
      # 否则一次 SMTP 握手（最坏 30s × 2 个发送器）会挂在这个响应上。
      spawn(_notify_2fa_disabled(user.email, user.username), name="2fa disabled notice")
  ```
  （`from app.core.background import spawn` 放到文件顶部或按本文件习惯在函数内 import。）
- **契约**：无变化。
- **测试**：`backend/tests/integration/test_2fa.py::test_disable_answers_without_waiting_for_mail`：把 email sender 换成 `tests/support/hang.py` 里的挂起实现，断言端点仍在超时内返回 200；`::test_disable_still_notifies_the_owner`（spawn 之后 `inflight_count` / 出站记录里能看到这封信，`tests/support/outbox.py` 可用）。

### GET /users/{userId}/follow/questions 等分页参数缺校验

- **现状**：`users.py:1010`、`1069`、`1134`、`2946`、`3005`、`3107` 的 `page_start: int | None = Query(default=None, alias="pageStart")`；`users.py:904`、`905`、`955`、`956` 的 `pageStart/pageSize: int | None`。
- **问题**：没有 `ge/le`。`pageStart=-1` 会原样进 `offset`（`users.py:1018`、`1083`、`2954`、`3122`、`domain/user/repositories.py:416` 的 `.offset(offset)`），Postgres 对 `OFFSET -1` 直接报错（`OFFSET must not be negative`）→ 500 而不是 400/422；`pageSize=-1` 同理（`LIMIT must not be negative`）。`/users/me/team-requests` 的 `pageSize` 连上限都没有，`limit = page_size or self._default_page_size`（`domain/team/membership_services.py:507`）会照单执行。仓库里已有正确样例：`api/routes/feedback.py:218` 的 `page_start: int = Query(default=0, ge=0)`。（这条我按 Postgres 语义判断，本报告未执行代码验证，故请在测试里钉住。）
- **优化**：八处统一加上边界，与同文件里已经写对的 `pageSize: int = Query(default=20, ge=1, le=100, ...)`（`users.py:1011`）保持一致：

  ```python
      page_start: int = Query(default=0, ge=0, alias="pageStart"),
      page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
  ```
  对 `users.py:904-905`、`955-956` 这两条（`int | None`）改成同样的范围；`domain/team/membership_services.py:478/507` 的两个 `limit = page_size or self._default_page_size` 可以随之简化，但保留也无害。
- **契约**：非法值从 500 变成 422 — 修复；`pageStart` 默认值从 `None` 变 `0` 等价（两处都 `or 0`）。
- **测试**：`backend/tests/contract/test_users_contract.py::test_python_pagination_params_are_bounded`：对 8 条路由各打一次 `pageStart=-1` 与 `pageSize=100000`，断言 422。

### GET /users/invite-codes（不可达）

- **现状**：`users.py:4575` 的 `@router.get("/invite-codes", ...)`（handler 在 `users.py:4579`）注册在 `GET /users/{userId}`（`users.py:1719`）之后。
- **问题**：FastAPI 按注册顺序匹配，`GET /users/invite-codes` 会先命中 `GET /users/{userId}`，`"invite-codes"` 过不了 `int` 转换 → 400 `int_parsing`。这条路由今天**永远打不到**。文件里 `users.py:4567-4572` 的注释已经完整记录了这件事，并说明「改顺序不在这次改动里」。同一批的 `POST /users/invite-codes`、`DELETE /users/invite-codes/{code_id}` 不受影响（没有 `POST/DELETE /users/{userId}` 与之竞争），这是为什么问题只在 GET 上暴露。
- **优化**：把三条邀请码路由整组移到 `/{userId}` 家族之前（例如紧挨 `users.py:1335` 的 `/registration-config` 之后）。移动的爆炸半径比注释里设想的小：只需要保证「字面量路径段」排在「`{userId}` 通配」之前，`/users/lookup`（`users.py:238`）已经是这个位置的正确样例。

  ```python
  # 放在 GET /{userId}（users.py:1719）之前，与 /lookup、/registration-config 同族：
  # 字面量段必须先于 {userId} 注册，否则 "invite-codes" 会落进 int 转换。
  @router.get("/invite-codes", summary="List invite codes (admin)")
  async def list_invite_codes(...) -> dict:
      ...
  ```
- **契约**：无（这条路径今天返回 400，修复后才第一次可用）；无迁移。
- **测试**：`backend/tests/integration/test_invite_codes_admin.py::test_list_invite_codes_is_reachable`（断言 200 而非 400）、`::test_list_invite_codes_requires_a_platform_admin`（匿名 401、普通用户 403 — 与文件注释里承诺的两个答案一致）。

### POST /users/auth/sudo（160 行单函数）

- **现状**：`users.py:2318`，`method` 的 `password` / `totp` / `passkey` / `email_code` 四个分支各约 25 行，各开一次 `AsyncRedis.from_url`，各自的校验与错误信息。
- **问题**：单函数 160 行、圈复杂度过高（五条出口），四种凭据的代码放在一起，加一种就要再切一刀；每一支自己 `AsyncRedis.from_url(...)` + `finally: aclose()`（`users.py:2385`、`2403`、`2428`、`2465`），Redis 连接与分支耦合。
- **优化**：按凭据拆成 `_sudo_via_password/_totp/_passkey/_email_code`，各自接收 `redis` 与 `session`，入口只做分派；Redis 交给依赖（见 M1）。

  ```python
  _SUDO_METHODS: dict[str, Callable[..., Awaitable[str]]] = {
      "password": _sudo_via_password,
      "totp": _sudo_via_totp,
      "passkey": _sudo_via_passkey,
      "email_code": _sudo_via_email_code,
  }
  ...
      if method is None:
          ...
      handler = _SUDO_METHODS.get(method)
      if handler is None:
          raise BadRequestError(f"Unknown auth method: {method}")
      message = await handler(
          credentials=credentials, user_id=auth_user.user_id, session=session, redis=redis
      )
      return await verified(message)
  ```
- **契约**：无变化（响应体与 `verified`/`ticketed` 语义原样保留）。
- **测试**：`backend/tests/integration/test_sudo_window.py` 已有覆盖四种方法的用例；拆分后加 `::test_unknown_sudo_method_is_400`（现状也测得到，用于钉住重构）。

### GET /users/auth/oauth/state（未鉴权的 bearer 票据，且在 URL 里）

- **现状**：`users.py:4192`，`token: str = Query(...)`，`_decode_oauth_state_token`（`users.py:3769`）解码后返回 `providerId/userInfo/suggestedUsername/suggestedNickname`。
- **问题**：
  - 端点**没有** `require_auth_user`（决策页要在登录前调用，这是有意为之），票据就是唯一凭据，而它在 query string 里 —— 同 `GET /users/{userId}/identity` 的问题，且这里是一个可以读出第三方身份信息（`userInfo` 含 provider 的 id/email/name）的票据。
  - `users.py:4196` 只 `_decode_oauth_state_token`，**不消费** jti（`users.py:3770`「Reading does not spend the token」）。也就是说票据在 15 分钟内可以被任何拿到这个 URL 的人反复读取；库里 `core/single_use_state.py:1-23` 的模块注释专门讲了「用户会把这类链接粘到群聊里」的 #222 事故。
  - 响应没有 `Cache-Control: no-store`（仓库里 `api/routes/forge_token.py:162`、`api/routes/preview_sessions.py:35` 这类带凭据的响应都写了）。
- **优化**：
  1. 至少给带票据的响应加 `response.headers["Cache-Control"] = "no-store"`，避免中间缓存与浏览器把 `userInfo` 留住。
  2. 把「读」也计入使用：`_decode` 保留（决策页可能刷新），但给同一个 stateToken 加一个短的读取计数/或者干脆在首次读取后缩短 TTL；更彻底的做法是让决策页用一个单独的短票（一次性 GET）换取展示数据。
  3. 长票改 POST（body 传 token），把 query string 清空：

     ```python
     @router.post("/auth/oauth/state", summary="Decode the OAuth decision-page state token")
     async def get_oauth_state(
         response: Response,
         token: str = Body(..., embed=True),
         auth_service: UserAuthService = Depends(get_user_auth_service),
     ) -> dict:
         response.headers["Cache-Control"] = "no-store"
         provider_id, user_info, _jti = _decode_oauth_state_token(token)
         ...
     ```
- **契约**：改 POST 是破坏性变更（前端 `frontend/src/api.ts` 需要同步），加 `no-store` 是增量。无迁移。
- **测试**：`backend/tests/integration/test_oauth_login_state.py::test_state_response_is_not_cacheable`（断言 `Cache-Control: no-store`）、`::test_state_token_is_not_replayable_after_the_decision`（若采纳第 2 点）。

### 其余可优化项（改动小，逐条列）

- **GET /users（44）里的 `total`** 与 **`pageStart` 契约**已在上面详述；`users.py:3151` 的 `hasMore: returned == page_size` 在改造后换成 `page_start + returned < total`。
- **GET /users/{userId}（19）**：`users.py:1736` 同样触发 6 个 count，公开资料页每次访问都付。可挂 ETag（同 `/users/me`），或让非本人视角的 DTO 不带这六个计数（那是契约变更，需前端确认）。
- **POST /users（13）/POST /users/auth/login（22）/refresh（26）/logout（27）/2fa enable（45）/backup-codes（48）/passkey register（50）**：响应体 `code` 是 201 而 HTTP 状态是 200（见 M3），统一改 HTTP 状态即可，不必逐个改。
- **POST /users/auth/login（22）**：`users.py:1933` 无条件调 `_passkey_enrollment`，里面 `_issue_sudo_ticket`（`users.py:673`）每次登录都铸一张票据并往 Redis 写一条预约，即使 `offer` 为 false（用户已有 passkey 或已拒绝过）。可以先判 `prompt.due` 再票据。
- **POST /users/auth/verify-2fa（23）**：`payload: dict`（`users.py:1945`）+ 手写 `payload.get(...)`，与同文件其余请求体的 Pydantic 风格不一致；换成模型后 `trust_device` 的 `is True` 判断也能写成 `bool`。
- **POST /users/recover/password/verify（38）**：`_require_new_password`（`users.py:2877`）在消费 token（`users.py:2882`）之前调用，顺序正确 —— 弱密码不会白白作废一个重置链接。无发现。
- **GET /users（44）`q` 的大小**：`q: str | None` 无 `max_length`，配合 LIKE 建议加 `max_length=64`（`/users/lookup` 的 `q` 已限 254）。
- **POST /users/invite-codes（66）**：`users.py:231` `max_uses: int = Field(default=1, alias="maxUses")` 无 `ge/le`。```max_uses: int = Field(default=1, ge=1, le=1000, alias="maxUses")```。
- **DELETE /users/invite-codes/{code_id}（67）**：`users.py:4653` 无论 `deactivate_code` 是否命中都回 200。`InviteCodeService.deactivate_code`（`domain/invite/services.py:73`）返回 `None`，改成返回 bool，未命中抛 `NotFoundError`（GitHub 的 DELETE 对不存在的资源回 404）。
- **GET /users/lookup（1）**：`users.py:253` 返回 `{"handle","name","avatar_id"}`，`domain/user/services.py:101` 的键是 snake_case，而同一文件的 DTO 一律 camelCase（`avatarId`）。前端 `frontend/src/api.ts:2276` 已经按 `avatar_id` 消费，改键是破坏性的，所以只标注：再加字段时别在这条上延续 snake_case。
- **PATCH /users/{userId}/identity（31）**：`users.py:2591-2614` 先读后拼再写，两个并发 PATCH 会丢更新。改用一条 `UPDATE ... SET col = COALESCE(:new, col)`（或对读加 `with_for_update()`），省掉一次往返。

---

## 三、模块级建议

### M1. Redis 客户端一律用 `app.core.redis.get_redis_client()`（本文件 26 处）

- **范围**：`users.py` 里全部 26 个 `AsyncRedis.from_url(...)` 调用点（`grep -c` 得 26；整个 backend 只有 27 处，另一处在 `api/routes/health.py:89`）。典型：`users.py:1240`、`1258`、`1322`、`1417`、`1709`、`1854`、`2006`、`2092`、`2130`、`2169`、`2385`、`2403`、`2428`、`2465`、`2779`、`2809`、`2842`、`2879`、`3471`、`3510`、`3576`、`3612`、`3814`、`3826`、`3854`、`3869`。
- **做法**：每条都是「新建客户端 → 用 → `aclose()`」，即每次请求一次 TCP 连接建立＋（如启用 TLS）握手＋销毁。平台已经有单例：`app/core/redis.py:8` 的 `get_redis_client()`（`lru_cache`，带连接池，`decode_responses=False`），`core/single_use_state.py:54`、`api/routes/docs_site.py:47` 都在用。做成 FastAPI 依赖后，26 处 `try/finally` 一起消失：

  ```python
  ### app/api/deps.py（或 users.py 顶部，取决于是否要跨路由共用）
  from collections.abc import AsyncIterator
  from app.core.redis import get_redis_client

  async def get_redis() -> AsyncIterator["Redis"]:
      """The shared client. Not closed here: it is a process-wide singleton."""
      client = get_redis_client()
      if client is None:
          raise InternalServerError("Redis is not configured")
      yield client
  ```

  调用处：`redis: Redis = Depends(get_redis)`，删掉 `AsyncRedis.from_url(...)` 与 `finally: await redis.aclose()`。注意两个例外：`_store_oauth_pending`/`_pop_oauth_pending`（`users.py:3854`、`3869`）用的是 `decode_responses=True`，改用共享客户端后需要在 `json.loads(raw.decode())` 上补一次解码。
- **为什么**：`app/core/redis.py` 的 docstring 写明「连接在第一条命令 await 时才建立」，也就是说 26 处各自建池再销毁，省下的正是连接建立的开销与文件句柄抖动。这是本文件里唯一一处「平台已有公共件却没被用」的成片问题。

### M2. 分页协议统一：参数边界 + 一套游标语义

- **范围**：`users.py` 的 8 条列表端点（6、7、8、9、10、40、41、44），以及 33。
- **做法**：
  1. 一律 `page_start: int = Query(default=0, ge=0, alias="pageStart")`、`page_size: int = Query(default=20, ge=1, le=100, alias="pageSize")`（`le=100` 已有先例：`users.py:1011`、`3070`）。
  2. `pageStart` 的语义在同一个 router 前缀下有两种（offset 见 `users.py:1018`；答案 id 游标见 `users.py:1153`）。建议保留答案 id 游标的现状（改动最小、它才是稳定的），但在文档里把两种写清楚，或统一到一套 cursor + `nextStart`（响应里已经同时给了 `hasMore`/`nextStart`，客户端按 `nextStart` 翻页就不用理解语义差别）。
- **为什么**：GitHub REST 的做法是 `per_page`（默认 30，**上限 100**）＋ Link 头里的 `next`，并且明确 `page` 只适合小数据集、大数据集用 cursor：https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api ；Stripe 用 `limit`（上限 100）+ `starting_after`/`has_more`：https://docs.stripe.com/api/pagination 。本平台的 `pageStart/pageSize/hasMore/nextStart/total` 已经是 Stripe 形状，缺的就是边界与语义统一。

### M3. 响应体 `code` 与 HTTP 状态码不一致

- **范围**：`register_user`（`users.py:1493` 回 `code:201`）、`send_register_email_code`（`1330`）、`user_login`（`1927`）、`verify_2fa_login`（`2044`）、`refresh_access_token`（`2280`）、`user_logout`（`2309`）、`enable_user_2fa`（`3218`）、`regenerate_backup_codes`（`3406`）、`passkey_register_verify`（`3528`）、`passkey_authenticate_verify`（`3644`）、`create_invite_code`（`4630`）。这些都是 HTTP 200 但 body 写 `code: 201`，`user_logout` 甚至在「登出成功」上写 201。
- **做法**：要么给路由加 `status_code=status.HTTP_201_CREATED` 并让 body 的 `code` 与它一致，要么把 body 的 `code` 改成 200。**不要**让两者不一致。
- **为什么**：GitHub 对创建返回 `201 Created` + `Location`（https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api#http-response-codes ），其它一律 200。客户端与 API 目录（本仓 `backend/tests/contract/api_catalog.json`）都以 HTTP 状态为准，body 里的 `code` 一旦不一致就会两头打架。这条改动会影响既有契约测试（`tests/integration/test_user_profile.py` 断言 `data["code"] == 200`），所以要成批做、一次改完。

### M4. 该挂条件请求的读端点

- **范围**：`GET /users/me`（14）、`GET /users/{userId}`（19）、`GET /users`（44）、`GET /users/me/sessions`（34）。
- **做法**：用平台已有的 `app/api/conditional.py` 的 `etag_for_json`（`api/conditional.py:40`）算摘要、`if_none_match_hits`（`api/conditional.py:24`）判命中，配 `Cache-Control: private, no-cache`；先例是 `api/routes/admin_members.py:61` 的名单。
- **为什么**：GitHub 的 REST 文档专门有一节 conditional requests（ETag + `If-None-Match` → 304，且 304 不计入速率限制）：https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests 。这四条恰好是「内容大（6 个计数）、变化慢、客户端反复轮询」的形状。

### M5. 邮件与其它慢副作用一律 `spawn`

- **范围**：`_notify_2fa_disabled`（`users.py:3342`，唯一的反例）；正例是 `users.py:2140`、`users.py:2854`。
- **做法**：`from app.core.background import spawn`（`core/background.py:102`），把协程交出去，`name=` 带上用途（测试里可以用 `inflight_count()`（`core/background.py:121`）与 `tests/support/outbox.py` 观测）。
- **为什么**：`core/email.py:88` 的 `timeout=30`、加 `FallbackEmailSender` 的第二次尝试，最坏 60 秒；`users.py:2080` 与 `users.py:2765` 的注释已经把这个理由写清楚了，2FA 那条只是漏了。

### M6. 错误格式：已统一，保持

- 平台统一为 `{"code", "message", "error": {"name", "message", "data", "retryable"}}`（`core/errors.py:34-44`），`reason` 之类的机器可读信息放在 `error.data` 里（如 `users.py:537` 的 `retryAfterSeconds`）。**没有**改的必要。可选增量：GitHub 的错误体带 `documentation_url`（https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api#error-response-body ），若要给客户端一个稳定锚点，可加一个指向本平台 API 文档的 `error.docs`。**不建议**改成 RFC 7807，那会是一次全平台破坏性变更而收益只是形式统一。

### M7. 本文件的重复代码与私有跨模块引用

- `_EMAIL_FORMAT` 在同一文件里定义了**两次**（`users.py:1214`、`users.py:2063`，内容相同，后者遮蔽前者），另有两处内联同款正则（`users.py:1314`、`users.py:2835`）。抽一个 `_EMAIL_FORMAT` 与一个 `_valid_email(raw) -> str` 放文件顶部即可。
- 团队状态校验：`users.py:912-927` 与 `users.py:965-980` 两段 16 行逐字相同（同一个集合、同一个 `ApplicationStatus[upper]`）。抽成一个 `Query` 依赖或一个 `_parse_application_status(raw) -> ApplicationStatus | None`。
- `users.py:910` / `963`：`from app.api.routes.teams import _application_to_api_model, _load_application_maps`——路由模块之间 import 下划线私有函数。这两个函数已经是「跨路由复用」的事实，应该搬到中立位置（`app/api/` 或团队域的一个展示模块），否则下一个人改 `teams.py` 的私有函数时不会知道这里在用。
- `users.py:909`、`1013`、`1102` 等：`db=Depends(get_db)` 没有类型标注（同文件别处是 `session: AsyncSession = Depends(get_db)`）。加标注即可。

### M8. 未挂同源校验的 cookie 设置端点

- **范围**：`POST /users/auth/login`（`users.py:1832`）、`POST /users/auth/email-code/verify`（`users.py:2148`）、`POST /users/auth/passkey/verify`（`users.py:3595`）、`POST /users/auth/verify-2fa`（`users.py:1944`）——它们都会 `Set-Cookie: cheese_refresh`，但 `_require_same_origin`（`users.py:322`）只装在了 refresh（`users.py:2254`）与 logout（`users.py:2303`）上。
- **做法**：在这四条入口的第一行加 `_require_same_origin(request)`。
- **为什么**：登录 CSRF（把受害者登进攻击者的账号，之后受害者的输入会落在攻击者可见的账号里）是公开的类别攻击，OWASP 的 CSRF 章节把它与「状态改变请求」并列：https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html 。这里已经有现成的实现，只是没铺满。

### M9. 时间/命名的表示形式

- 时间戳两种：epoch 毫秒（`users.py:1026`、`1091`）与 `isoformat()`（`users.py:2681`、`4591`）。GitHub 一律用 ISO 8601 UTC（`2026-09-28T12:00:00Z`）。同一份 API 里两种表示，客户端要么读两遍要么写错。建议统一到 epoch 毫秒（改动面更大的是 isoformat 那两处：`/users/me/sessions` 与 `/users/invite-codes`），或反过来，选一个并在契约测试里钉住。
- 主键类型：`/users/me/sessions` 的 `id` 是字符串（`users.py:2677`，UUID 的必然），其余是整数；`/users/invite-codes` 用 `code_id` 而路径里写 `code_id`（`users.py:4641`），与同文件 `{credentialId}`、`{connectionId}` 的 camelCase 风格不一致。都属低优先，列在这里是为了以后不要再多出第三种写法。

---

## 附：本次分析用到的判断来源

- GitHub REST 分页（`per_page` 上限 100 / Link 头 / 大数据集用 cursor）：https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api
- GitHub 条件请求（ETag / If-None-Match / 304）：https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests
- GitHub 响应码（201 创建、404 删除不存在的资源）：https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api#http-response-codes
- GitHub 错误体（`message` + `documentation_url`）：https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api#error-response-body
- Stripe 分页（`limit` 上限 100 + `starting_after` + `has_more`）：https://docs.stripe.com/api/pagination
- OWASP CSRF 防护速查表（登录 CSRF）：https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- 未查证但属业界通行的判断：`Offset`/`Limit` 的负数边界处理、`except Exception` 的收窄、把 SMTP 移出请求路径 —— 这三条按通行做法判断，未逐条查证具体文档。
