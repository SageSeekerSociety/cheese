# 平台 API 逐接口优化调研 — 组 `teams`

- 负责文件：`backend/app/api/routes/{admin_members,groups,members,recruitment,teams}.py`，共 **61 个接口**
- 仓库（只读）：见派工消息里的 `$WT`；行号均指上表 module 内的行，除注明外
- 判断依据：优先 GitHub REST 惯例（第三节给出已查证的 URL），其次 Stripe / skills.sh；查不到的按业界通行做法并注明

## 清单核对（先说不一致的地方）

`teams.list.md` 的 61 行与源码逐条对过，**method / path / handler / def 行号全部一致**，未发现错记。清单里有两处「一个模块导出多个 router」的地方，机生成结果是对的，记录在此以免后来者怀疑：

- `routes/recruitment.py` 导出两个 router：`router = APIRouter(tags=["Recruitment"])`（第 54 行，无 prefix，挂 `/recruitment*`）与 `team_recruitment_router = APIRouter(prefix="/teams", ...)`（第 240 行，挂 `#33`/`#34`）。`main.py:339` 的 `_discover_routers` 扫的是模块级所有 `APIRouter` 实例（`for attr, value in vars(module).items()`），所以两个都挂上了。
- `routes/teams.py` 同样两个：`router = APIRouter(prefix="/teams", ...)`（第 92 行）与 `invites_router = APIRouter(prefix="/team-invites", ...)`（第 95 行，挂 `#43`/`#44`）。清单把 `/team-invites/{token}` 的两条单列，正确。

## 一、接口清单结论

| # | method | path | 结论 | 一句话 |
|---|---|---|---|---|
| 1 | GET | `/admin/admins` | 暂无 | `ETag` + `304` + `private, no-cache` + 一次读 memo，是本仓库条件请求的样板 |
| 2 | GET | `/admin/users` | 暂无 | 搜索在 SQL 里，`q` 1..64、`limit` ≤50，与 `/users?q=` 的「只搜一页」区分清楚 |
| 3 | POST | `/admin/admins` | 暂无 | `INSERT ... ON CONFLICT DO NOTHING RETURNING` 幂等，`created` 说清是不是真加了 |
| 4 | DELETE | `/admin/admins/{target}` | 暂无 | `DELETE ... RETURNING` 幂等，根管理员 409 且判断权在服务里 |
| 5 | GET | `/groups` | 可优化 | `groups/services.py:88` 每行一次 `count_members`（N+1）；`question_count`/`answer_count` 恒为 0 |
| 6 | POST | `/groups` | 可优化 | body 是裸 `dict`，`name`/`intro` 无长度上限，OpenAPI 里没有 schema |
| 7 | GET | `/groups/{group_id}` | 可优化 | 响应里的 `question_count`/`answer_count` 永远是 0（假数据） |
| 8 | PUT | `/groups/{group_id}` | 可优化 | 部分更新却用 PUT（应为 PATCH）；`group_profile` 行缺失时 `intro`/`avatarId` 静默丢弃 |
| 9 | DELETE | `/groups/{group_id}` | 暂无 | 204，owner 校验在服务层 |
| 10 | GET | `/groups/{group_id}/members` | 可优化 | 游标分页每页 3 条 SQL（prev 那条多余）+ repo 里一段死分支 |
| 11 | POST | `/groups/{group_id}/members` | 暂无 | 常量 4 条 SQL，重复入队 409 |
| 12 | DELETE | `/groups/{group_id}/members` | 暂无 | owner 不能退；非成员 409 |
| 13 | GET | `/groups/{group_id}/targets` | 暂无 | 列表 + COUNT 两条，常量 |
| 14 | POST | `/groups/{group_id}/targets` | 可优化 | `startedAt` 非数字 → `TypeError` 冒到 500；无 `endedAt > startedAt` 校验 |
| 15 | GET | `/groups/{group_id}/targets/{target_id}` | 暂无 | `target.group_id != group_id` 校验到位，无跨组 IDOR |
| 16 | PUT | `/groups/{group_id}/targets/{target_id}` | 可优化 | 同 14 的 `fromtimestamp`；`attendanceFrequency` 任意字符串直存 |
| 17 | DELETE | `/groups/{group_id}/targets/{target_id}` | 暂无 | 归属校验 + 角色校验 |
| 18 | GET | `/groups/{group_id}/questions` | 暂无 | 只回 id 列表 + COUNT，常量 |
| 19 | POST | `/groups/{group_id}/questions` | 暂无 | `questionId` 校验 `int ≥ 1`，重复添加幂等 |
| 20 | DELETE | `/groups/{group_id}/questions/{question_id}` | 暂无 | `(group_id, question_id)` 作用域正确 |
| 21 | POST | `/projects/{project_id}/members` | 暂无 | agent 座位原语；`require_manager` 门在服务里，不接受 body 里的 handle |
| 22 | GET | `/projects/{project_id}/members` | 可优化 | `members.py:65` 把 `list_for_project` 的 COUNT 查完就丢；`data.data` 嵌套 + `total` 名不副实 |
| 23 | DELETE | `/projects/{project_id}/members/{user_handle}` | 暂无 | 席位先撤后删行，顺序对 |
| 24 | DELETE | `/projects/{project_id}/membership` | 暂无 | 身份只从 resolver 来，`/membership` 命名避开了 `me` 被当 handle |
| 25 | POST | `/projects/{project_id}/invitations` | 暂无 | 四条拒绝都在服务里，通知一条 |
| 26 | GET | `/projects/{project_id}/invitations` | 可优化 | `describe()` 逐行取项目行 + 邀请人资料（2N 条查询） |
| 27 | GET | `/me/invitations` | 可优化 | 同上，`members.py:170` |
| 28 | POST | `/invitations/{invitation_id}/respond` | 暂无 | 只有被邀请人能答；重复答复 400 并说明已结束 |
| 29 | DELETE | `/invitations/{invitation_id}` | 暂无 | 撤回不删行，保留唯一记录 |
| 30 | GET | `/recruitment` | 可优化 | stealth 队的 OPEN 帖连同 `contact` 进匿名广场；游标按 `id`、排序按 `created_at`；过期帖照列 |
| 31 | PATCH | `/recruitment/{postId}` | 可优化 | `maxMembers`/`expiresAt` 无上下界 |
| 32 | DELETE | `/recruitment/{postId}` | 暂无 | 团队 ADMIN+ 校验在服务里 |
| 33 | POST | `/teams/{teamId}/recruitment` | 可优化 | `title`/`content`/`contact`/`maxMembers` 无长度与数值约束；过去的 `expiresAt` 照收 |
| 34 | GET | `/teams/{teamId}/recruitment` | 可优化 | 无分页、无上限，一队的帖全量返回 |
| 35 | GET | `/teams` | 可优化 | 每队 3 条 SQL（默认 20 队 = 61 条）；`pageStart` 非法值静默当 0 |
| 36 | GET | `/teams/my-teams` | 可优化 | 每队 3 条 SQL，且没有分页；侧边栏轮询的就是它 |
| 37 | GET | `/teams/{teamId}` | 可优化 | `is_team_member` 的同一条判定在一个请求里查两遍 |
| 38 | GET | `/teams/by-handle/{handle}` | 可优化 | 与 37 同一条 `_team_profile`，同样重复 |
| 39 | POST | `/teams/{teamId}/join` | 可优化 | 入队前后各算一遍 `join_status`；成功后又整份重算 profile |
| 40 | GET | `/teams/{teamId}/join-link` | 暂无 | 管理员门 + 一次 flush，`token_urlsafe(32)` |
| 41 | PATCH | `/teams/{teamId}/join-link` | 暂无 | 同上，复用 `join_link()` |
| 42 | POST | `/teams/{teamId}/join-link/reset` | 暂无 | 重置即换 token，旧链接 404 |
| 43 | GET | `/team-invites/{token}` | 暂无 | 持链接可达、与 id 路分开的设计清楚；需登录是既有口径（测试钉住） |
| 44 | POST | `/team-invites/{token}/join` | 暂无 | 与 39 同一服务，同 39 的重复判定（低） |
| 45 | GET | `/teams/{teamId}/resource-quotas` | 可优化 | 逐项目跑一次 `for_project` 全表聚合；项目列表查了两遍 |
| 46 | GET | `/teams/{teamId}/members` | 可优化 | 无分页；`queryRealNameStatus=true` 时每人一次查询（N+1） |
| 47 | POST | `/teams` | 暂无 | 重名 409 带结构化 `data`，handle 冲突有校验 |
| 48 | PATCH | `/teams/{teamId}` | 暂无 | 路由门 ADMIN+ 与服务一致，回更新后的整份 team |
| 49 | DELETE | `/teams/{teamId}` | 暂无 | 只 OWNER；软删成员 + 软删队伍 |
| 50 | DELETE | `/teams/{teamId}/members/{userId}` | 暂无 | 服务内分级判权（owner/admin、admin 不能删 admin） |
| 51 | PATCH | `/teams/{teamId}/members/{userId}` | 暂无 | 路由门是 ADMIN、服务要求 OWNER，服务更严不算漏洞，只是判定重复 |
| 52 | POST | `/teams/{teamId}/members` | 可优化 | 路由直接调 `service._repo.add_member`（私有件），且不校验目标用户存在 |
| 53 | GET | `/teams/{teamId}/join-requests` | 可优化 | `pageSize` 无上限（`le` 缺失）；列表 + COUNT 两条；`status` 白名单与 55 重复一份 |
| 54 | GET | `/teams/{teamId}/requests` | 可优化 | 与 53 逐字重复的别名（同一个 handler 转调） |
| 55 | GET | `/teams/{teamId}/invitations` | 可优化 | 同 53 |
| 56 | POST | `/teams/{teamId}/invitations` | 暂无 | 角色校验 + 服务内 `is_team_at_least_admin` |
| 57 | DELETE | `/teams/{teamId}/invitations/{invitationId}` | 暂无 | 置 CANCELED，不删行 |
| 58 | POST | `/teams/{teamId}/join-requests/{requestId}/approve` | 暂无 | 204；服务内判权 + 团队锁校验 |
| 59 | POST | `/teams/{teamId}/requests/{requestId}/approve` | 可优化 | 与 58 重复的别名 |
| 60 | POST | `/teams/{teamId}/join-requests/{requestId}/reject` | 暂无 | 同 58 |
| 61 | POST | `/teams/{teamId}/requests/{requestId}/reject` | 可优化 | 与 60 重复的别名 |

统计：**可优化 27 / 暂无 34 / 待确认 0**。

## 二、详细分析（按收益从高到低）

### GET `/teams` — 页大小 × 3 条 SQL 的 N+1

- **现状**：`routes/teams.py:394-435`。先 `enumerate_teams`（1 条），然后 `for team in teams_to_emit:` 里 `service.get_team_members(team_id=team.id)`（`teams.py:412`）+ `_load_team_user_maps(db, members)`（`teams.py:413`）。
- **问题**：`_load_team_user_maps`（`teams.py:281-292`）本身发两条 SQL（`user_repo.get_by_ids` + `profile_repo.get_profiles_by_user_ids`），所以**每支队伍 3 条 SQL**。默认 `pageSize=20` 时一次请求 **1 + 20×3 = 61 条**；`pageSize=100` 时 301 条。这是列表页，与 `tests/integration/test_hot_path_queries.py` 那条「工作不应随被列出东西的大小增长」的形状判据直接冲突。`TeamRepository` 已经有 `get_by_ids` 这种批量件的写法，members 只是漏了。
- **优化**：给 repo 加一个批量版成员读，路由改为「一次取全部成员 + 一次取全部用户/资料」。

```python
# app/domain/team/repositories.py — TeamRepository 内，list_members_of_team 之后新增
    async def list_members_of_teams(
        self, team_ids: Sequence[int]
    ) -> dict[int, list[TeamUserRelation]]:
        """Several teams' membership rows in one read.

        The page shapes (``GET /teams``, ``GET /teams/my-teams``) need every
        listed team's members; ``list_members_of_team`` in a loop is one query
        per team.
        """
        if not team_ids:
            return {}
        stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation).where(
            and_(
                TeamUserRelation.team_id.in_(list(team_ids)),
                TeamUserRelation.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        grouped: dict[int, list[TeamUserRelation]] = {tid: [] for tid in team_ids}
        for rel in result.scalars().all():
            grouped[rel.team_id].append(rel)
        return grouped
```

```python
# app/domain/team/services.py — TeamService 内（get_team_members 旁边）
    async def get_members_of_teams(
        self, team_ids: Sequence[int]
    ) -> dict[int, list[TeamUserRelation]]:
        """Members of several teams at once — the list pages' half."""
        return await self._repo.list_members_of_teams(team_ids)
```

```python
# app/api/routes/teams.py — _load_team_user_maps 之后新增
async def _load_team_user_maps_for(
    db, relations: list[TeamUserRelation]
) -> tuple[dict, dict]:
    """Bulk-load User + UserProfile for the union of these relations' users."""
    user_ids = list({rel.user_id for rel in relations})
    if not user_ids:
        return {}, {}
    return (
        await UserRepository(session=db).get_by_ids(user_ids),
        await UserProfileRepository(session=db).get_profiles_by_user_ids(user_ids),
    )
```

```python
# app/api/routes/teams.py:410-421 替换为（下一步的 next_start 计算不动）
    members_by_team = await service.get_members_of_teams([t.id for t in teams_to_emit])
    users_map, profiles_map = await _load_team_user_maps_for(
        db, [rel for rels in members_by_team.values() for rel in rels]
    )
    items = [
        _team_to_api_model(
            team,
            members=members_by_team.get(team.id, []),
            users_map=users_map,
            profiles_map=profiles_map,
        )
        for team in teams_to_emit
    ]
```

改写后一次请求固定 4 条 SQL（列表 + 成员 + users + profiles），与 `pageSize` 无关。

- **契约**：不变。`_team_to_api_model` 的输入是同一种 `TeamUserRelation` 列表，`admins`/`members` 的 `total`/`examples` 逐字相同。
- **测试**：`tests/integration/test_hot_path_queries.py` 里加一条 `test_the_team_list_does_not_scale_with_the_page_size`，用现成的 `counting_sql()` 先后请求 `?pageSize=1` 与 `?pageSize=10`（都需要至少 10 支队），断言两次 SQL 条数相等（且 ≤5）；`tests/contract/test_teams_contract.py::test_python_get_teams_shape` 继续钉住响应形状。

### GET `/teams/my-teams` — 同样的 N+1，且完全没有分页

- **现状**：`routes/teams.py:442-470`。`ensure_personal_team` 之后 `get_teams_of_user`（1 条），然后 `teams.py:454-465` 又是 `get_team_members` + `_load_team_user_maps` 的逐队循环。
- **问题**：与 `GET /teams` 同一处 N+1，但这个接口**没有任何 limit**：一个用户的队伍数没有上限（个人队 + 所有加入的队），`items` 无界。侧边栏（`我的小队`）调的就是它，队伍多的用户每次渲染都是 3N 条 SQL + 全量序列化。
- **优化**：照 35 的批量件改；同一循环换成 `members_by_team` + `_load_team_user_maps_for`。`teams.sort(key=lambda t: (t.personal_owner_user_id is None, -t.id))`（`teams.py:452`）之后取前 N 支，或先分页再装配：

```python
# app/api/routes/teams.py:450-465 替换为
    teams = list(await service.get_teams_of_user(user_id=auth_user.user_id))
    # Personal team first, then the rest by recency (stable for the sidebar).
    teams.sort(key=lambda t: (t.personal_owner_user_id is None, -t.id))
    visible = teams[: limit]          # limit: int = Query(default=50, ge=1, le=100)
    members_by_team = await service.get_members_of_teams([t.id for t in visible])
    users_map, profiles_map = await _load_team_user_maps_for(
        db, [rel for rels in members_by_team.values() for rel in rels]
    )
    items = [
        _team_to_api_model(
            team,
            members=members_by_team.get(team.id, []),
            current_user_id=auth_user.user_id,
            users_map=users_map,
            profiles_map=profiles_map,
        )
        for team in visible
    ]
```

- **契约**：加 `limit` 查询参数、`data` 里补一个 `"page": {"pageSize": len(items), "hasMore": len(teams) > len(visible)}` 是**新增字段**（不删不加），老客户端照旧读 `data.teams`。若不加 `limit`，至少也要把 N+1 修掉。
- **测试**：`tests/integration/test_team.py`（或新建 `test_team_my_teams_queries.py`）：`test_my_teams_query_count_is_flat`，一人建 1 支个人队 + 5 支共享队，断言 SQL 条数不随队数增长；`test_my_teams_keeps_the_personal_team_first` 钉住排序（个人队仍在第 0 位）。

### GET `/teams/{teamId}` / `/teams/by-handle/{handle}` / POST `/teams/{teamId}/join` / GET+POST `/team-invites/{token}*` — 同一个成员判定查两遍

- **现状**：四条读路都走 `_team_profile`（`teams.py:473-491`）。以 `get_team` 为例（`teams.py:498-506`）：`service.visible_team(...)`（`services.py:100-115`）里已经 `await self._repo.is_team_member(team_id, user_id)`，紧接着 `_team_profile` 第 490 行又 `await membership_service.join_status(team.id, user_id)`，而 `join_status`（`membership_services.py:111-117`）第一件事就是**同一条** `is_team_member` SELECT。
- **问题**：一次 `GET /teams/{teamId}` 共 7 条 SQL，其中 2 条是逐字相同的成员判定；`join_team`（`teams.py:522-536`）更差——`visible_team` 算一次、`join()` 内的 `join_status` 算一次、成功后再进 `_team_profile` 又算一次，同一条 SELECT 一个请求里跑三遍。这不是「慢」，是同一事实的三份声明。
- **优化**：让 `visible_team` 把「顺便算出来的成员答案」交出来（加一个返回元组的方法，`visible_team` 变成它的薄包装，不动 `discussions.py:116` / `recruitment.py:298` 的调用点），`_team_profile` 与 `join_status` 接受这个已知值。

```python
# app/domain/team/services.py — visible_team 保留，新增一个带答案的版本
    async def visible_team_with_membership(
        self, team_id: int, user_id: int
    ) -> tuple[Team, bool]:
        """The team as ``user_id`` may see it by id, plus whether they are in it.

        Same rule as :meth:`visible_team`, but the membership answer that the
        rule already had to compute comes back with it — a caller that needs
        ``joinStatus`` right after does not ask the same question twice.
        """
        team = await self._repo.get_by_id(team_id)
        if team is not None:
            is_member = await self._repo.is_team_member(team_id, user_id)
            if is_member or _open_to_all(team):
                return team, is_member
        raise NotFoundError(
            "Resource team not found", data={"type": "team", "id": team_id}
        )

    async def visible_team(self, team_id: int, user_id: int) -> Team:
        team, _ = await self.visible_team_with_membership(team_id, user_id)
        return team
```

```python
# app/domain/team/membership_services.py:111 — join_status 接受已知答案
    async def join_status(
        self, team_id: int, user_id: int, *, is_member: bool | None = None
    ) -> str:
        """``member``, ``pending`` (a request waits for approval) or ``none``.

        Pass ``is_member`` when the caller already asked (``visible_team``
        does) so the same SELECT is not issued twice.
        """
        if is_member is None:
            is_member = await self._team_repo.is_team_member(team_id, user_id)
        if is_member:
            return "member"
        if await self._app_repo.exists_pending_for_user_and_team(user_id, team_id):
            return "pending"
        return "none"
```

```python
# app/api/routes/teams.py:473-491 — _team_profile 收下这个事实
async def _team_profile(
    team: Team,
    user_id: int,
    service: TeamService,
    membership_service: TeamMembershipService,
    db,
    *,
    is_member: bool | None = None,
) -> dict:
    ...
    data["joinStatus"] = await membership_service.join_status(
        team.id, user_id, is_member=is_member
    )
    return {"code": 200, "message": "OK", "data": {"team": data}}


# app/api/routes/teams.py:498-506 — get_team 用它
@router.get("/{teamId}", summary="Query Team")
async def get_team(...) -> dict:
    team, is_member = await service.visible_team_with_membership(
        team_id, auth_user.user_id
    )
    return await _team_profile(
        team, auth_user.user_id, service, membership_service, db, is_member=is_member
    )
```

`get_team_by_handle`（`teams.py:509-518`）同理：`visible_team_by_handle` 内部也走 `visible_team`，可在该处加一个返回元组的同名变体；`join_team` 把 `join()` 返回的 status 传给 `_team_profile`（`membership_service.join` 已经返回 `"member"`/`"pending"`，见 `membership_services.py:119-169`）——成功加入后的那次 `join_status` 直接可以省掉。

- **契约**：响应完全不变（`joinStatus` 取值集合不变）。内部签名变化要同步 3 个调用点，`visible_team` 保留所以外部无感。
- **测试**：`tests/integration/test_team.py`（新增 `test_team_detail_asks_membership_once`）：对 `GET /teams/{id}` 用 `counting_sql()` 断言 `team_user_relation` 上的 SELECT 只出现一次；`test_team_join_links.py` 的存量用例继续覆盖 `joinStatus` 三种取值。

### GET `/recruitment` — 隐身队的帖子进了匿名广场（越权）+ 游标与排序不同键

- **现状**：`routes/recruitment.py:133-163`，**没有任何鉴权依赖**（广场是公开的，`tests/integration/test_team_recruitment_authz.py:228` 把它钉住，这条不动）。数据来自 `RecruitmentRepository.list_open`（`recruitment_repositories.py:57-84`）。
- **问题**（两条，第二条是硬 bug）：
  1. `list_open` 的 WHERE 只有 `status == OPEN` 与 `deleted_at IS NULL`（`recruitment_repositories.py:68-71`），**没有团队可见性过滤**。于是一支 `visibility=stealth` 的队的 OPEN 帖——`title`/`content`/`contact`（表格里的 `secret-xxx@example.com` 那类）以及 `team` 摘要（`team_summary`：handle + name + intro，`summary.py:21-27`）——对**匿名人**可见。`models.py:35-42` 写的口径是「stealth 队既不出现在搜索里，也不能靠 id 打开」；`tests/integration/test_team_recruitment_authz.py:1-22` 为团队作用域那条路专门写了用例守这条口径，广场这条路漏了同一个判据。
  2. 排序键与游标键不是同一个：`base.order_by(created_at.desc())`（`:74`）但游标是 `id <= page_start`、`next_id = rows[-1].id - 1`（`:76`、`:83`）。`created_at` 有并列（同毫秒插入）时页边界不唯一，翻页会重复或漏帖。GitHub 的分页要求一个稳定全序。
- **优化**：① 排序与游标对齐（不改契约的部分）；② 把团队可见性加进广场的谓词。`TeamRecruitmentPost.team_id` 本身有外键，加一个 join 即可：

```python
# app/domain/team/recruitment_repositories.py — list_open
from app.domain.team.models import Team, TeamVisibility  # 顶部补 Team / TeamVisibility

        base = (
            select(TeamRecruitmentPost)
            .join(Team, Team.id == TeamRecruitmentPost.team_id)
            .where(
                TeamRecruitmentPost.status == RecruitmentStatus.OPEN.value,
                TeamRecruitmentPost.deleted_at.is_(None),
                Team.deleted_at.is_(None),
                # 广场是一个搜索面：隐身队不出现在搜索里（models.py 的
                # TeamVisibility 口径），个人队也不是给别人看的。
                Team.personal_owner_user_id.is_(None),
                Team.visibility == TeamVisibility.PUBLIC.value,
                # 过期帖不是 OPEN 帖（``RecruitmentStatus.EXPIRED`` 存在但全仓
                # 没有任何地方写过它 —— 见下面的第 3 条）。
                or_(
                    TeamRecruitmentPost.expires_at.is_(None),
                    TeamRecruitmentPost.expires_at > datetime.now(UTC),
                ),
            )
            # 游标按 id 走，排序就必须以 id 收尾：created_at 并列时
            # （测试夹具里很常见）单独的 created_at desc 给不出唯一页边界。
            .order_by(TeamRecruitmentPost.id.desc())
        )
```

- **契约**：① 排序键换成 `id desc` 后条目集合与「新的在前」语义不变（id 是自增且与 created_at 同向），只是页边界唯一了——`tests/integration/test_team_recruitment.py:206` 起的翻页用例应继续通过。② **广场不再列 stealth / 个人队的帖子，也不再列过期帖**，这是行为变化，需要产品确认；前端若依赖广场能看见自己的隐身队帖，要一起改。`tests/integration/test_team_recruitment_authz.py` 里已有一条只覆盖团队作用域的同类用例，新增一条覆盖广场即可。
- **测试**：`tests/integration/test_team_recruitment_authz.py` 加 `test_the_plaza_does_not_list_a_stealth_teams_post`（匿名 `GET /recruitment`，断言 marker 与 contact 都不在响应体里）；`tests/integration/test_team_recruitment.py` 加 `test_the_plaza_page_boundary_is_unique`（同毫秒造 3 条帖，`pageSize=2` 翻两页，断言两页 id 无交集、并集等于全集）。

### GET `/teams/{teamId}/resource-quotas` — 逐项目一次全表聚合 + 项目列表查两遍

- **现状**：`routes/teams.py:612-661`。`machines`、`grants`、`projects` 各一批，然后 `projects` 的列表推导里对每个项目 `await usage.for_project(p.id)`（`teams.py:651`）。
- **问题**：
  1. `UsageRepository.for_project` → `_agg(ResourceUsage.project_id, pid)`（`usage/repositories.py:160-161`、`87`）是**一次对 `resource_usage` 的聚合**，而这张表的自述是「全平台增长最快的一张表（每调一次 `/v1/messages` 一行）」（`usage/repositories.py:166-168`）。N 个项目 = N 次全表聚合。
  2. `ComputeGrantRepository.list_for_team`（`usage/repositories.py:386-398`）内部自己调了一次 `ProjectService.list_for_team(team_id)` → `ProjectRepository.list_by_team`；路由第 628 行又调了一次**同一条** `ProjectService(db).list_for_team(team_id)`。同一个 SELECT 一个请求里跑两遍。
- **优化**：先读项目，再把已读到的 id 交给 grants；`for_project` 换成一次 `GROUP BY`。

```python
# app/domain/usage/repositories.py — UsageRepository，for_project 之后新增
    async def total_tokens_by_project(
        self, project_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """``for_project(p)["total_tokens"]`` for many projects in one pass.

        ``for_project`` in a loop is one full aggregate over ``resource_usage``
        — the table every ``/v1/messages`` call writes a row to — per project.
        Same predicate, grouped.
        """
        if not project_ids:
            return {}
        rows = await self._session.execute(
            select(
                ResourceUsage.project_id,
                func.coalesce(func.sum(ResourceUsage.total_tokens), 0),
            )
            .where(ResourceUsage.project_id.in_(project_ids))
            .group_by(ResourceUsage.project_id)
        )
        totals = {pid: int(tokens) for pid, tokens in rows}
        for pid in project_ids:
            totals.setdefault(pid, 0)
        return totals
```

```python
# app/domain/usage/repositories.py — ComputeGrantRepository，list_for_team 之后新增
    async def list_for_team_of_projects(
        self, team_id: int, project_ids: list[uuid.UUID]
    ) -> list[ComputeGrant]:
        """``list_for_team`` when the caller already read the team's projects."""
        result = await self._session.execute(
            select(ComputeGrant)
            .where(
                or_(
                    ComputeGrant.team_id == team_id,
                    ComputeGrant.project_id.in_(project_ids),
                )
            )
            .order_by(ComputeGrant.created_at, ComputeGrant.id)
        )
        return list(result.scalars().all())
```

```python
# app/api/routes/teams.py:618-661 — 组装部分
    repo = TeamRepository(db)
    if not await repo.get_by_id(team_id) or not await repo.is_team_member(
        team_id, auth_user.user_id
    ):
        raise NotFoundError("Resource team not found")
    machines = await MachineService(db).quota_machines(team_id)
    projects = await ProjectService(db).list_for_team(team_id)
    grants = await ComputeGrantRepository(db).list_for_team_of_projects(
        team_id, [p.id for p in projects]
    )
    shared = [g for g in grants if g.project_id is None]
    total = sum(g.credits_total for g in shared)
    used = sum(g.credits_used for g in shared)
    tokens_by_project = await UsageRepository(db).total_tokens_by_project(
        [p.id for p in projects]
    )
    return {
        ...  # machines / credits 两块不动
            "projects": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "machines_used": sum(m.project_id == p.id for m in machines),
                    "total_tokens": tokens_by_project.get(p.id, 0),
                    "restricted_credits_remaining": sum(
                        g.credits_total - g.credits_used
                        for g in grants
                        if g.project_id == p.id
                    ),
                }
                for p in projects
            ],
    }
```

- **契约**：响应逐字不变（`total_tokens` 仍是整数，零用量仍是 0）。`for_project` 保留不动，别的调用方无感。
- **测试**：`tests/integration/test_team_resource_quotas.py` 加 `test_quotas_read_the_projects_once`（`counting_sql()` 断言 `FROM project` 与 `resource_usage` 聚合各只出现一次）；存量用例继续覆盖数字。

### GET `/teams/{teamId}/members` — `queryRealNameStatus=true` 是 O(N) 次查询，且列表没有分页

- **现状**：`routes/teams.py:668-707`。`relations = get_team_members(...)`（1 条）+ `_load_team_user_maps`（2 条）+ 逐成员 `_member_to_api_model`。`queryRealNameStatus: bool = Query(default=False)`，为真时进入 `teams.py:690-699` 的循环。
- **问题**：`for uid in member_user_ids: if not await realname_repo.has_identity(uid)` —— `UserRealNameRepository` 只有单条版 `has_identity`（`user/repositories.py:451`），所以是**每个成员一次查询**（最坏 N 次，全实名时一次不落）。`tests/integration/test_bug8_team_realname.py` 说明这条路确实有人在用。另外 `relations` 全量返回，没有分页也没有上限（队伍可以很大）。
- **优化**：给 repo 加批量判定（`IdentityService.agents_among` 是同一形状的先例），一次查完：

```python
# app/domain/user/repositories.py — UserRealNameRepository，has_identity 之后新增
    async def verified_user_ids(self, user_ids: list[int]) -> set[int]:
        """Which of these users hold a real-name identity — one read, not N."""
        if not user_ids:
            return set()
        rows = await self._session.execute(
            select(UserRealNameIdentity.user_id).where(
                UserRealNameIdentity.user_id.in_(user_ids)
            )
        )
        return set(rows.scalars().all())
```

```python
# app/api/routes/teams.py:688-699 替换为
    all_verified: bool | None = None
    if queryRealNameStatus:
        from app.domain.user.repositories import UserRealNameRepository

        verified = await UserRealNameRepository(session=db).verified_user_ids(
            [rel.user_id for rel in relations]
        )
        all_verified = all(rel.user_id in verified for rel in relations)
```

- **契约**：`allMembersVerified` 的语义不变（空队员时原实现返回 `True`，上式 `all([])` 也是 `True`，一致）。分页若加，需要前端配合，建议单独提。
- **测试**：`tests/integration/test_bug8_team_realname.py` 里加 `test_the_real_name_check_is_one_query_per_request`（`counting_sql()`，3 个成员，断言 `user_real_name_identities` 上的 SELECT 恰好 1 条）。

### GET `/projects/{project_id}/invitations` 与 GET `/me/invitations` — `describe()` 逐行两查

- **现状**：`routes/members.py:154` 与 `170`：`rows = [await svc.describe(i) for i in items]`。
- **问题**：`InvitationService.describe`（`membership/services.py:341-358`）每行做两件事——`self._projects.get(invitation.project_id)`（`Session.get`，1 条）和 `self._projects.person(invitation.invitee_handle)`（→ `_profile_of`，`project/repositories.py` 的 user⋈user_profile⋈avatar 三表 join，1 条）。一页 N 张邀请 = **2N 条查询**，而这两种数据分别来自两张表。
- **优化**：加一个批量描述，路由改调它。

```python
# app/domain/project/repositories.py — ProjectRepository，person 之后新增
    async def get_many(
        self, project_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Project]:
        if not project_ids:
            return {}
        rows = await self._session.scalars(
            select(Project).where(Project.id.in_(project_ids))
        )
        return {p.id: p for p in rows}

    async def people_by_handle(self, handles: list[str]) -> dict[str, dict]:
        """``person(handle)`` for a batch — the same join, once."""
        if not handles:
            return {}
        rows = await self._session.execute(
            select(
                User.username,
                UserProfile.nickname,
                UserProfile.avatar_id,
                Avatar.avatar_type,
            )
            .select_from(User)
            .outerjoin(UserProfile, UserProfile.user_id == User.id)
            .outerjoin(Avatar, Avatar.id == UserProfile.avatar_id)
            .where(User.username.in_(handles))
        )
        return {
            username: {
                "name": nickname or username,
                "avatar_id": None if avatar_type == "default" else avatar_id,
            }
            for username, nickname, avatar_id, avatar_type in rows
        }
```

```python
# app/domain/membership/services.py — InvitationService，describe 之后新增
    async def describe_many(
        self, invitations: Sequence[ProjectInvitation]
    ) -> list[dict]:
        """``describe`` for a whole page: two reads, not two per invitation."""
        from app.domain.membership.schemas import InvitationOut

        rows = [
            InvitationOut.model_validate(i).model_dump(mode="json") for i in invitations
        ]
        projects = await self._projects.get_many(
            list({i.project_id for i in invitations})
        )
        people = await self._projects.people_by_handle(
            list({i.invitee_handle for i in invitations})
        )
        for row, invitation in zip(rows, invitations, strict=True):
            project = projects.get(invitation.project_id)
            person = people.get(invitation.invitee_handle)
            row["project_name"] = project.name if project else ""
            row["invitee_name"] = (
                person["name"] if person else invitation.invitee_handle
            )
            row["invitee_avatar_id"] = person["avatar_id"] if person else None
        return rows
```

```python
# app/api/routes/members.py:154 与 170 两处
    rows = await svc.describe_many(items)
```

- **契约**：不变。`project_name` / `invitee_name` / `invitee_avatar_id` 三个键、取值口径与 `person()` 的 `name or handle` 一致（`project/repositories.py:218-226`）。`describe()` 留着给单条调用方。
- **测试**：`tests/integration/test_project_invitations.py` 加 `test_the_invitation_list_does_not_scale_with_its_length`（`counting_sql()`，一页 5 张邀请，断言 `FROM project` 与三表 join 各只 1 条）。

### GET `/projects/{project_id}/members` — 查了又丢的 COUNT，和名不副实的 `total`

- **现状**：`routes/members.py:53-81`。第 65 行 `members, _ = await MemberService(db).list_for_project(project_id)`；第 81 行 `return ok(page(items, len(items)))`。
- **问题**：`MemberService.list_for_project`（`membership/services.py:141-148`）返回 `(列表, 数量)`，数量由 `MemberRepository.count_for_project`（`members/repositories.py:93-99`）**第二条 SQL** 算出，而唯一调用方用一个 `_` 把它丢了。全仓只有这一处调它（已 grep 确认）。另外 `page()` 的 `total` 现在等于 `len(items)`——因为这条接口没有分页所以**恰好**对，一旦以后加 `limit`，`total` 会静默变成「本页条数」（`topics.md` 里 `/transcript` 就是这个坑）。
- **优化**：服务只回列表，把数量交给真正需要它的调用方。

```python
# app/domain/membership/services.py:141-148 替换为
    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMember]:
        """The roster's stored rows.

        The count that used to ride along came from a second query that the
        only caller (``list_members``) discarded; a caller that needs it asks
        ``count_for_project`` itself.
        """
        await self._ensure_project(project_id)
        return await self._repo.list_for_project(project_id)
```

- **契约**：响应 JSON 一字不变（`data.data` + `data.total`）。内部签名变化只影响这一处。
- **测试**：`tests/integration/test_members.py` 加 `test_the_roster_costs_one_read`（`counting_sql()` 断言 `FROM project_member` 只 1 条）；`test_a_project_needs_a_team` 等存量用例继续钉住 `data.data` 形状。

### GET `/groups` — 每行一次 `count_members`

- **现状**：`routes/groups.py:43-61` → `GroupsService.list_groups`（`groups/services.py:63-101`）。第 83 行批量取了 profile（正确），第 86-89 行的循环里 `member_count = await self._membership_repo.count_members(row.id)`。
- **问题**：`count_members`（`groups/repositories.py:317-323`）是每行一次 `SELECT count(*)`，`page_size` 默认 20 → 20 条额外 SQL（外加一次 total COUNT、一次 profile 批读）。修法与 `GET /teams` 同形：一条 `GROUP BY`。
- **优化**：

```python
# app/domain/groups/repositories.py — GroupMembershipRepository，count_members 之后新增
    async def count_members_by_group(
        self, group_ids: Sequence[int]
    ) -> dict[int, int]:
        """Member counts for several groups in one read.

        ``count_members`` in a loop is one COUNT per row of the page.
        """
        if not group_ids:
            return {}
        rows = await self._session.execute(
            select(GroupMembership.group_id, func.count(GroupMembership.id))
            .where(
                GroupMembership.group_id.in_(list(group_ids)),
                GroupMembership.deleted_at.is_(None),
            )
            .group_by(GroupMembership.group_id)
        )
        return {gid: int(n) for gid, n in rows}
```

```python
# app/domain/groups/services.py:82-89 替换为
        group_ids = [row.id for row in rows]
        profiles = await self._profile_repo.get_profiles_by_group_ids(group_ids)
        counts = await self._membership_repo.count_members_by_group(group_ids)
        items = [
            _group_to_dto(
                row,
                profiles.get(row.id),
                member_count=counts.get(row.id, 0),
            )
            for row in rows
        ]
```

- **契约**：不变（`member_count` 仍是那个数）。
- **测试**：`tests/integration/test_groups.py` 加 `test_the_group_list_counts_members_in_one_read`（`counting_sql()`，5 个组，断言 `group_membership` 上的 SELECT 只 1 条）。

### GET `/groups` 与 GET `/groups/{group_id}` — `question_count` / `answer_count` 恒为 0

- **现状**：`_group_to_dto`（`groups/services.py:20-47`）把 `question_count=0, answer_count=0` 作为默认参数，三个调用点（`list_groups:89`、`create_group:129-138`、`get_group:153-162`、`update_group:191-200`）**没有一个**传真值。
- **问题**：响应里 `question_count` / `answer_count` 永远是 `0`，而同模块就有 `GET /groups/{group_id}/questions` 这条真数据来源。前端若把这两个字段画成「0 个问题」，是一条稳定的假信息（`list_group_questions` 已经证明数据存在）。
- **优化**：二选一——要么算真的（`GroupQuestionRepository` 加一个 `count_by_group`，与 `list_by_group` 的 `total` 同一条谓词），要么把这两个字段从 DTO 里删掉。若短期只想去掉假信息、又不想动前端，最小改动是删字段前先跟前端确认（见「契约」）。

```python
# 方案 A：算真的（groups/repositories.py — GroupQuestionRepository）
    async def count_by_group(self, group_id: int) -> int:
        stmt = select(func.count(GroupQuestionRelationship.id)).where(
            GroupQuestionRelationship.group_id == group_id,
            GroupQuestionRelationship.deleted_at.is_(None),
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)
```
（`answer_count` 本组数据里没有出处——`questions` 域才有答案——所以建议直接**删掉这个字段**，而不是编一个数。）

- **契约**：删字段是**破坏性**的（前端 `types` 里若声明为必填会报类型错）；改真值最多让页面从「0」变成「N」，是修 bug 不是改契约。建议只做方案 A 的 `question_count`，`answer_count` 单独提一条给前端。**不确定 `answer_count` 的产品预期，需产品确认后再删。**
- **测试**：`tests/integration/test_groups.py` 加 `test_a_groups_question_count_is_not_zero_when_it_has_questions`（给组挂 2 个问题，断言 `GET /groups/{id}` 与 `GET /groups` 里 `question_count == 2`）。

### POST `/teams/{teamId}/members` — 路由直接用了 repo 的私有件，且不校验目标用户

- **现状**：`routes/teams.py:872-910`。第 898 行 `relation = await service._repo.add_member(team_id, user_id, role_val)`。
- **问题**：
  1. 绕过了 `TeamMembershipService`：`_validate_user_can_apply_or_be_invited`（`membership_services.py:81-95`，会查 `User.id` 存在、已是成员、已有 pending）一个都没走。`userId=999999` 时会在 `team_user_relation` 落一行**指向不存在用户**的成员行，随后 `GET /teams/{id}/members` 用 `_user_payload(None, ...)` 兜底成一个空用户名（`teams.py:113-124`）——一行谁也认不出的成员。
  2. `service._repo` 是下划线属性，路由伸手进去就是第二个写入口；别处（`team/services.py:261-286`）同类写入都有服务方法。
  3. 直接入队不发任何通知，`TeamMembershipService` 的邀请/审批路都会 `_notify`。这是否是产品预期（ADMIN 免同意直接拉人）没有文档说明——**需产品确认**，本条只按「绕过校验」报。
- **优化**：在服务上开一个显式方法，路由只调它。

```python
# app/domain/team/membership_services.py — TeamMembershipService 内新增
    async def add_member_directly(
        self, *, team_id: int, actor_user_id: int, user_id: int, role: int
    ) -> TeamUserRelation:
        """Put a user on the team without asking them — an ADMIN+ primitive
        (course rosters and test fixtures ride on it).

        Same checks as the invitation path runs on its target: the user must
        exist, must not already be in, and must not already have something
        pending. The team's lock still applies.
        """
        if not await self._team_repo.is_team_at_least_admin(team_id, actor_user_id):
            raise ForbiddenError("Only team admins or owner can add members")
        await self._validate_user_can_apply_or_be_invited(user_id, team_id)
        from app.domain.team.services import check_team_locking_status

        await check_team_locking_status(self._session, team_id)
        return await self._team_repo.add_member(team_id, user_id, role)
```

```python
# app/api/routes/teams.py:885-898 — 路由改成
    role_map = {"MEMBER": TeamMemberRole.MEMBER, "ADMIN": TeamMemberRole.ADMIN}
    role_val = role_map.get(role_str)
    if role_val is None:
        raise BadRequestError(f"Invalid role: {role_str}. Must be MEMBER or ADMIN")

    relation = await membership_service.add_member_directly(
        team_id=team_id,
        actor_user_id=auth_user.user_id,
        user_id=user_id,
        role=role_val,
    )
```
（`add_team_member_entry` 需要注入 `TeamMembershipService`；`require_permission(Action.CREATE, Resource.TEAM_MEMBERSHIP, "teamId")` 可保留，服务里再判一次是既有的双层口径。）

- **契约**：成功路径的响应不变。新增失败路径：不存在的 userId → 404、已在队 → 409（与邀请路一致）。已依赖这条接口的测试（`test_course_roster.py:128`、`test_space_units.py:64` 等）用的都是真实用户，不受影响。
- **测试**：`tests/integration/test_team.py` 加 `test_adding_a_member_that_does_not_exist_is_not_found`（`{"userId": 999999999}` → 404，且 `GET members` 不出现该行）。

### POST `/teams/{teamId}/recruitment` 与 PATCH `/recruitment/{postId}` — 字段没有边界

- **现状**：请求模型在 `routes/recruitment.py:33-51`。`CreateRecruitmentPostRequest.max_members: int | None = Field(default=None, alias="maxMembers")`、`expires_at: int | None`；`PatchRecruitmentPostRequest` 同。路由里只校验 `title` 非空（`:174-175`）与 `status` 在枚举内（`:176-179`）。
- **问题**：
  1. `maxMembers` 没有下界：`-5` 照收并入库（`team_recruitment_post.max_members`，`models.py:212`）。
  2. `expiresAt` 没有下界：一个过去的时间戳照收（`datetime.fromtimestamp(payload.expires_at / 1000, tz=UTC)`，`routes/recruitment.py:187`、`257`），帖子立刻是过期状态却没有过期语义（见 30）。
  3. `title` 只在 `min_length=1`、`content` 只在 `min_length=1`，没有上界；`contact` 完全没有约束。入库列分别是 `String(255)`/`Text`/`String(255)`（`models.py:205-208`），`contact` 超出 255 会在 flush 时抛 `DataError` → 500 而不是 400。
- **优化**：

```python
# app/api/routes/recruitment.py:33-51
class CreateRecruitmentPostRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=20000)
    contact: str | None = Field(default=None, max_length=255)
    max_members: int | None = Field(default=None, alias="maxMembers", ge=1, le=10000)
    # Milliseconds since epoch. A deadline in the past is not a deadline.
    expires_at: int | None = Field(default=None, alias="expiresAt", ge=0)


class PatchRecruitmentPostRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    contact: str | None = Field(default=None, max_length=255)
    max_members: int | None = Field(default=None, alias="maxMembers", ge=1, le=10000)
    status: str | None = None
    expires_at: int | None = Field(default=None, alias="expiresAt", ge=0)
```

- **契约**：超长/越界从 500（或静默入库）变成 422（Pydantic 校验失败，错误体走 `app.core.errors` 的 422 通道）。**上限值应照前端现有输入框的 maxlength 定**，`content` 的 20000 是估的，落地前要跟前端对一次。
- **测试**：`tests/integration/test_team_recruitment.py` 加 `test_a_recruitment_post_rejects_out_of_range_fields`（`maxMembers: 0` / `-1`、`contact` 300 字符 → 422）。

### GET `/teams/{teamId}/recruitment` — 一队的帖全量返回

- **现状**：`routes/recruitment.py:287-311`。`posts = await service.list_by_team(team_id)` → `RecruitmentRepository.list_by_team`（`recruitment_repositories.py:86-96`）只有 `team_id + deleted_at` 两个谓词，**没有 limit/offset**。
- **问题**：一支长期招人的队伍，帖数无上限，响应无界。同一个模块的广场（`list_open`）是有游标的（`page_size + 1` + `next_id`），这条没有——同一份数据的两个读法不一致。
- **优化**：复用广场的游标形状：

```python
# app/domain/team/recruitment_repositories.py — list_by_team 改造
    async def list_by_team(
        self, *, team_id: int, page_size: int = 20, page_start: int | None = None
    ) -> tuple[list[TeamRecruitmentPost], bool, int | None]:
        """A team's posts, newest first, same cursor shape as the plaza."""
        base = select(TeamRecruitmentPost).where(
            TeamRecruitmentPost.team_id == team_id,
            TeamRecruitmentPost.deleted_at.is_(None),
        )
        if page_start is not None:
            base = base.where(TeamRecruitmentPost.id <= page_start)
        rows = list(
            (
                await self._session.execute(
                    base.order_by(TeamRecruitmentPost.id.desc()).limit(page_size + 1)
                )
            )
            .scalars()
            .all()
        )
        has_more = len(rows) > page_size
        if has_more:
            rows = rows[:page_size]
        next_id = rows[-1].id - 1 if has_more and rows else None
        return rows, has_more, next_id
```

```python
# app/api/routes/recruitment.py:287-311 的路由签名与返回
async def list_team_recruitment_posts(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    page_start: int | None = Query(default=None, ge=0, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    ...
) -> dict:
    ...
    posts, has_more, next_start = await service.list_by_team_page(
        team_id, page_size=page_size, page_start=page_start
    )
    ...
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "posts": items,
            "page": {
                "pageStart": page_start,
                "pageSize": len(items),
                "hasMore": has_more,
                "nextStart": next_start,
            },
        },
    }
```

- **契约**：`data.posts` 不变；新增 `data.page`，是**新增字段**。老客户端不传 `pageStart`/`pageSize` 时行为从「全量」变成「最新 20 条」——这是**行为变化**，若前端页面依赖一次拿全，需要同步改（`tests/integration/test_team_recruitment.py:201-245` 附近有用例覆盖这几种调用）。
- **测试**：`tests/integration/test_team_recruitment.py` 加 `test_a_team_list_is_paged_and_does_not_repeat_across_pages`（一队 3 条帖，`pageSize=2` 翻两页，断言 id 无交集）。

### POST `/groups/{group_id}/targets` 与 PUT `/groups/{group_id}/targets/{target_id}` — 输入校验

- **现状**：`routes/groups.py:232-263` 与 `285-315`。
- **问题**：
  1. `datetime.fromtimestamp(started_at / 1000, tz=UTC)`（`groups.py:251-252`、`300-303`）。body 是裸 `dict`（`payload.get("startedAt")`），客户端传字符串（`"1700000000000"`）时 `str / int` 抛 `TypeError`；传 1e18 这类越界值 `datetime.fromtimestamp` 抛 `ValueError/OverflowError`。都不是 `BadRequestError` 的子类，会冒到全局 500 处理器，调用方拿到的是 500 而不是 400/422。
  2. `attendance_frequency` 从 body 直取直存（`groups.py:244`、`298`，默认 `"DAILY"`），`GroupTarget.attendance_frequency` 是自由 `String`（`groups/models.py:73`），全仓没有枚举定义（已 grep）。任意字符串（含 10KB 的）都能入库。
  3. 没有 `endedAt > startedAt` 校验：`started_at` 晚于 `ended_at` 照收。
- **优化**：

```python
# app/api/routes/groups.py 顶部（import 区）
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

#: 出勤频率的取值范围（与前端一致；新增取值时这里与前端一起加）
AttendanceFrequency = Literal["DAILY", "WEEKLY", "MONTHLY"]


class GroupTargetIn(BaseModel):
    """创建/更新小组目标。``startedAt`` / ``endedAt`` 是毫秒时间戳。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=255)
    intro: str = Field(default="", max_length=5000)
    started_at: int = Field(..., alias="startedAt", ge=0)
    ended_at: int = Field(..., alias="endedAt", ge=0)
    attendance_frequency: AttendanceFrequency = Field(
        default="DAILY", alias="attendanceFrequency"
    )


def _ms_to_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()
```

```python
# create_group_target（groups.py:232-263）改成
async def create_group_target(
    group_id: Annotated[int, Path(ge=0)],
    payload: GroupTargetIn,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupTargetService = Depends(get_target_service),
) -> dict:
    started_at = _ms_to_date(payload.started_at)
    ended_at = _ms_to_date(payload.ended_at)
    if ended_at < started_at:
        raise BadRequestError("endedAt must not be earlier than startedAt")
    result = await service.create_target(
        group_id=group_id,
        user_id=auth_user.user_id,
        name=payload.name.strip(),
        intro=payload.intro,
        started_at=started_at,
        ended_at=ended_at,
        attendance_frequency=payload.attendance_frequency,
    )
    return {"code": 201, "message": "Created", "data": result}
```
（`GroupTargetService.create_target` 的 `started_at: datetime` 形参随之改成 `date`，`GroupTargetRepository.create` 里的 `_to_date` 也就直通了；`update_group_target` 用同一个模型的「全可选」版，字段缺省即不动。）注意：非法值现在由 Pydantic 拦成 **422**，而现有手写校验抛的是 **400**（`raise BadRequestError("startedAt and endedAt are required")`）。见「契约」。

- **契约**：① 请求体形状不变（还是 `name` / `intro` / `startedAt` / `endedAt` / `attendanceFrequency`），但**校验失败的状态码从 400 变成 422**（Pydantic 默认）。前端 `request()` 若只判 `code === 200` 就不受影响；若把 400 当特例，需要一起改。② `attendanceFrequency` 一旦收紧成 allowlist，前端现有取值必须都在集合内——**落地前必须对着前端确认取值域**（本仓测试只出现过 `DAILY`/`WEEKLY`）。③ 新增 `endedAt >= startedAt` 拒绝是行为变化（此前照收）。
- **测试**：`tests/integration/test_groups.py`（现有 `startedAt` 用例在第 913 行附近）加 `test_a_non_numeric_started_at_is_rejected`（`"startedAt": "abc"` → 4xx 且不是 5xx）、`test_an_ended_at_before_started_at_is_rejected`、`test_an_unknown_attendance_frequency_is_rejected`。

### PUT `/groups/{group_id}` 与 PUT `/groups/{group_id}/targets/{target_id}` — 用的是 PUT 的壳、PATCH 的语义

- **现状**：`routes/groups.py:102-122`（`update_group`）、`281-315`（`update_group_target`）。两者都只改 body 里出现的字段：`update_group` 传 `name=None` 时 `GroupRepository.update_group`（`groups/repositories.py:109-114`）不动 name；`update_group_target` 同理。
- **问题**：HTTP 语义上 PUT 是「用这份表示替换那个资源」，缺失字段应被置空/默认；这两条是部分更新。同一份代码库里 `PATCH /teams/{teamId}`、`PATCH /teams/{teamId}/members/{userId}` 走的是 PATCH（`teams.py:746`、`821`），`PATCH /recruitment/{postId}` 也是——同一个平台两种写法。GitHub REST 的更新一律是 `PATCH`（`PATCH /repos/{owner}/{repo}` 等），没有 PUT 的部分更新。
- **优化**：把两条改为 `@router.patch(...)`，路径不变：

```python
# app/api/routes/groups.py:102
@router.patch(
    "/{group_id}",
    summary="Update Group",
)
async def update_group(...)
```
```python
# app/api/routes/groups.py:281
@router.patch(
    "/{group_id}/targets/{target_id}",
    summary="Update Group Target",
)
async def update_group_target(...)
```
另：`GroupsService.update_group`（`groups/services.py:182-186`）里 `profile = await self._profile_repo.get_by_group_id(group_id)`，`if profile:` 为假时 `intro`/`avatarId` 的改动**静默丢弃**（老数据里没有 profile 行的组，改名改简介会看不到变化）。应补一行：profile 缺失时创建。

- **契约**：**路径与语义都不变**，只换 HTTP 方法名。`PUT` 会变成 405，如果前端或脚本在用 PUT，需要同步改（建议先同时保留两条路由一个版本，或直接把 `PUT` 也注册到同一个 handler）。**这是需要确认的破坏面**：先 grep 前端调用点再改。
- **测试**：`tests/integration/test_groups.py` 加 `test_updating_a_group_is_a_patch`（`client.patch` 200、`client.put` 405 或 200，按最终取舍断言）；`test_a_group_without_a_profile_row_can_still_be_renamed`。

### GET `/groups/{group_id}/members` — 每页 3 条 SQL 与一段死分支

- **现状**：`routes/groups.py:142-155` → `GroupsService.list_members`（`groups/services.py:222-273`）→ `GroupMembershipRepository.list_members_cursor`（`groups/repositories.py:249-315`）。
- **问题**：
  1. 一次翻页最多 3 条 SELECT：主体（`repositories.py:252-267`）+ prev 存在性（`:270-297`）+ next 存在性（`:299-313`）。prev 那条对「向后翻」没有用（游标是 `>= cursor` 的升序），next 那条完全是 `limit + 1` 能回答的事。
  2. `repositories.py:283-297` 是死代码：`elif cursor is None and not rows: pass` / `elif cursor is not None and not rows:` 的分支体与第一个 `if cursor is not None and rows:` 的**逐字相同**，可以合并成 `if cursor is not None:`。
  3. `services.py:233-242` 的 `if page_size <= 0:` 分支不可达（路由 `page_size: int = Query(default=20, ge=1, le=100)`，`groups.py:145`）。
- **优化**：

```python
# app/domain/groups/repositories.py:249-315 — list_members_cursor 重写
    async def list_members_cursor(
        self, *, group_id: int, cursor: int | None, limit: int
    ) -> tuple[list[GroupMembership], int | None, int | None]:
        """One page by member id, plus whether a previous / next page exists.

        ``limit + 1`` answers "has more" without a second probe; the previous
        page is at most the row just before the cursor, which is the same
        single-row probe (and only worth asking when the caller paged back).
        """
        base = (
            select(GroupMembership)
            .where(
                GroupMembership.group_id == group_id,
                GroupMembership.deleted_at.is_(None),
            )
            .order_by(GroupMembership.member_id.asc())
        )
        if cursor is not None:
            base = base.where(GroupMembership.member_id >= cursor)
        rows = list(
            (await self._session.execute(base.limit(limit + 1))).scalars().all()
        )
        has_next = len(rows) > limit
        rows = rows[:limit]

        prev_member_id: int | None = None
        if cursor is not None:
            prev_stmt = (
                select(GroupMembership.member_id)
                .where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.deleted_at.is_(None),
                    GroupMembership.member_id < cursor,
                )
                .order_by(GroupMembership.member_id.desc())
                .limit(1)
            )
            prev_member_id = (await self._session.execute(prev_stmt)).scalar_one_or_none()

        next_member_id = rows[-1].member_id if has_next and rows else None
        return rows, prev_member_id, next_member_id
```
另删掉 `services.py:233-242` 的 `page_size <= 0` 分支（`ge=1` 已经保证）。

- **契约**：`page.pageStart` / `prevStart` / `nextStart` 的取值口径不变（`nextStart` 仍是「有下一页时的最后一行的 member_id」——原实现是「比最后一行大的第一个 id」，两者在稠密 id 下等价，但**语义上原本是「下一页的第一行」**，改成「本页最后一行」后前端传回来的 `page_start` 仍落在 `>= cursor` 的同一批里，翻页结果相同）。若前端有基于 `nextStart` 的展示（如「跳到第 N 条」），需要确认口径。为避免这处含糊，更稳的做法是保留 next 探针、只删 prev 那条——**待确认前端怎么用 `nextStart`** 后再定。
- **测试**：`tests/integration/test_groups.py` 加 `test_a_member_page_uses_one_main_query`（`counting_sql()`）+ `test_paging_forward_does_not_repeat_a_member`。

### GET `/teams/{teamId}/join-requests` / `/requests` / `/invitations` — `pageSize` 没有上限

- **现状**：`routes/teams.py:917-963`（`join-requests`）、`966-989`（`requests` 别名）、`992-1042`（`invitations`）。三处都是 `pageSize: int | None = Query(default=None, ge=1)`——**只有下界**。
- **问题**：`?pageSize=1000000` 会一路传到 `TeamMembershipApplicationRepository.list_for_team` 的 `.limit(limit)`（`team/repositories.py:436-440`），把整张申请表读进内存并逐行装配（每行还要 `_user_payload`）。同模块的 `GET /teams` 与广场都是 `le=100`（`teams.py:397`、`recruitment.py:137`），这三条漏了。另外每页固定两条 SQL（列表 + COUNT，`team/repositories.py:444-455`）。
- **优化**：补上界（一行一处），并复用同一个状态白名单常量：

```python
# app/api/routes/teams.py — 文件顶部，_ROLE_NAME_TO_VALUE 旁边
_APPLICATION_STATUSES = {
    "PENDING",
    "APPROVED",
    "REJECTED",
    "ACCEPTED",
    "DECLINED",
    "CANCELED",
}


def _parse_application_status(status: str | None) -> ApplicationStatus | None:
    """One whitelist for every list that filters applications by status."""
    if status is None:
        return None
    upper = status.upper()
    if upper not in _APPLICATION_STATUSES:
        raise BadRequestError(f"Invalid status: {status}")
    return ApplicationStatus[upper]
```

```python
# 三处的签名与校验
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int | None = Query(default=None, ge=1, le=100),   # 补 le
    ...
    status_enum = _parse_application_status(status)
```

- **契约**：`pageSize > 100` 从「照做」变成 422；`status` 非法仍是 400（保持现在的行为，不引 Pydantic 的 422，避免同一条接口两种错误码）。响应形状不变。COUNT 那条查询是 `hasMore`/`total` 的来源，本组不改（GitHub 的 Link 方案是另一条更大的路，见第三节）。
- **测试**：`tests/integration/test_team_application.py` 加 `test_a_page_size_over_the_cap_is_rejected`（`?pageSize=1000` → 422）。

### 别名端点：`/requests` vs `/join-requests`、`/requests/{id}/approve` vs `/join-requests/{id}/approve`

- **现状**：`routes/teams.py:966-989`（`list_team_requests_alias` 转调 `list_team_join_requests`）、`:1121-1139`、`:1163-1181`。
- **问题**：同一个 handler 体量翻倍：`#54` 是 `#53` 的逐字转调，`#59`/`#61` 是 `#58`/`#60` 的逐字转调。四份重复的依赖声明（`require_permission(Action.UPDATE, Resource.TEAM_REQUEST, "teamId")` 写四遍），改一处口径要记得改四处；OpenAPI 里出现两条等价路径，调用方不知道哪条是正式的。GitHub 对已发布的旧路径是「保留 + 文档标注 deprecated」，不会长期维护两份真身。
- **优化**：把别名收敛成一条正式路径，另一条要么删、要么用 308 转发（保 URL 兼容）：

```python
# 方案 A（推荐，改动小）：别名只留转发，不再重复声明依赖与校验
@router.get("/{teamId}/requests", summary="List Team Requests", deprecated=True)
async def list_team_requests_alias(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    request: Request,
) -> Response:
    """老路径。正式路径是 ``/{teamId}/join-requests``。"""
    query = request.url.query
    target = f"/teams/{team_id}/join-requests" + (f"?{query}" if query else "")
    return RedirectResponse(url=target, status_code=status.HTTP_308_PERMANENT_REDIRECT)
```
（FastAPI 的 `deprecated=True` 会写进 OpenAPI；308 保留方法语义，POST 的 approve/reject 别名也能照做。若不想让调用方看到跳转，方案 B 是直接删掉四条别名——但那要先确认前端/脚本没在用；**需确认破坏面**。）

- **契约**：方案 A 下老 URL 仍可用但回答 308（客户端跟随则无感，不跟随的脚本会拿到 308 而不是 200/204）；方案 B 直接 404。两者都需要先确认调用方。**建议先加 `deprecated=True` 与文档，观察一个版本再删。**
- **测试**：`tests/contract/test_teams_contract.py` 加 `test_the_request_aliases_are_marked_deprecated`（读 `/openapi.json` 断言 `deprecated: true`）；`tests/integration/test_team.py` 里存量别名用例保留（若走方案 A，断言 308 + `Location`）。

## 三、模块级建议（跨接口）

1. **补 `team_user_relation` 与 `team_membership_application` 的索引（本组收益最高的一条，且不需要改任何路由）。**
   - 适用接口：几乎全部 27 条 `/teams/*`，以及每一条走 `require_permission(..., Resource.TEAM_*, "teamId")` 的写接口（`auth/domains/team.py:14-40` 的 `get_team_roles`）。
   - 现状：`alembic/versions/a95752502bb0_initial_schema.py:921-933` 建 `team_user_relation` 时只声明了主键与外键，**没有 `team_id` / `user_id` 上的索引**；`team_membership_application`（同文件 `:897-918`）同样只有主键。模型侧也没有（`domain/team/models.py:112-132` 没有 `__table_args__`，`user_id`/`team_id` 没有 `index=True`）。外键在 PostgreSQL 里**不自动建索引**，所以 `is_team_member`、`is_team_at_least_admin`、`list_members_of_team`、`list_teams_of_user`、`get_team_roles`、`exists_pending_for_user_and_team` 现在全是顺扫。
   - 做法（新增一个迁移）：

```python
# alembic/versions/xxxx_team_membership_indexes.py
def upgrade() -> None:
    # 每一次权限判定都是 (team_id, user_id) 上的等值查（get_team_roles /
    # is_team_member / is_team_at_least_admin），每次列成员是 team_id 上的范围查。
    op.create_index(
        "ix_team_user_relation_team_user",
        "team_user_relation",
        ["team_id", "user_id"],
    )
    op.create_index(
        "ix_team_user_relation_user",
        "team_user_relation",
        ["user_id"],
    )
    op.create_index(
        "ix_team_application_team_type_status",
        "team_membership_application",
        ["team_id", "type", "status"],
    )
    op.create_index(
        "ix_team_application_user_type_status",
        "team_membership_application",
        ["user_id", "type", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_team_user_relation_team_user", "team_user_relation")
    op.drop_index("ix_team_user_relation_user", "team_user_relation")
    op.drop_index("ix_team_application_team_type_status", "team_membership_application")
    op.drop_index("ix_team_application_user_type_status", "team_membership_application")
```
  模型侧同步 `Index(...)` 到 `__table_args__`，否则 `Base.metadata.create_all`（测试库）与迁移会分叉。注意：`get_team_roles` 用的 `scalar_one_or_none()`（`auth/domains/team.py:26-28`）在**一个用户在一支队伍里有两行**时会抛 `MultipleResultsFound`（500）——加了索引不解决这个，若要彻底，唯一约束才是答案；`tests/integration/test_team.py` 里已有「加两次成员」的用例（`test_add_already_member_fails`），现状不会出现两行。
   - 出处：GitHub 的 REST 惯例里没有索引这一条（这是数据库层），列在此是因为它同时决定「每个带 `teamId` 的接口要扫多少行」。

2. **分页协议统一（`pageStart`/`pageSize` vs `page_start`/`page_size`；offset vs 游标）。**
   - 适用范围：本组内的三种写法——`teams.py:396-397`（`pageStart`/`pageSize`，**offset**，值是字符串）、`groups.py:45-46`（`page_start`/`page_size`，列表是 offset、成员是游标）、`teams.py:920-921`（`pageStart`/`pageSize`，offset 但**无上限**）、`recruitment.py:136-137`（`pageStart` 是 **id 游标**）。同一个 `pageStart` 在四个接口里有三种含义。
   - 做法：请求参数统一 camelCase 的 `pageStart`/`pageSize`（前端已有 camelCase 习惯，`tests/integration/test_page_camel_case_keys.py` 说明这是既有方向）；`pageSize` 一律 `le=100`；响应 `page` 块统一 `{pageStart, pageSize, hasMore, nextStart, total?}`，并**写清 `pageStart` 是 offset 还是游标**（游标的接口在 `page` 里回 `nextStart` 的同时把 `total` 省掉）。
   - 为什么：GitHub 的列表接口统一用 `per_page` + `page`，并在 `Link` 头里给 `rel="next"` 的完整 URL，响应里不回 total；上游对「游标用于大表、offset 用于小表」有明确分工（https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api）。我们不必一次换成 Link，但至少要让 `pageStart` 的含义在一个接口里唯一。同组里 **`GET /teams` 的 `pageStart` 还有一个额外问题**：`int(page_start) if page_start and page_start.isdigit() else 0`（`teams.py:404`）——非法值被静默当作 0（应 422），且 `pageStart` 类型声明是 `str`（`teams.py:396`）而别处是 `int`。

3. **删除的响应契约统一：`204 空体` vs `200 + {"deleted": true}`。**
   - 适用范围：本组内 `routes/teams.py:786-799`、`:802-818`、`:1079-1097`（204）；`routes/groups.py:125-136`、`:318-331`、`:379-394`（204，`-> None`）；而 `routes/members.py:84-95`、`:98-112`（200 + `ok({"deleted": True})`），`routes/admin_members.py:109-126`（200 + 整份名单）。
   - 做法：写操作删除统一 204（HTTP 语义与 GitHub 一致——`DELETE /repos/{o}/{r}` 返回 204），需要回状态的（如 `removed: bool`）才用 200 + 信封。**不能改的是信封本身**（`{"code","message","data"}` 是项目既定约定，`app/api/response.py:1` 写着），能改的是「哪些接口用信封、哪些用 204」。
   - 影响面：前端 `request()` 现在要对这两种都处理；统一后 `members.py` 两处会从 200 变 204，属**破坏性**，要跟前端一起改。
   - 出处：https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api

4. **同一请求内重复的成员/权限判定，收进一个请求级 memo。**
   - 适用范围：`GET /teams/{teamId}`（`is_team_member` 两遍，见第二节）、`POST /teams/{teamId}/join`（三遍）、`GET /teams/{teamId}/members`（`require_permission` 走 `get_team_roles` 一次 + 服务里再来一次 `is_team_at_least_admin` 的地方）。
   - 做法：`AdminService.admin_handles`（`domain/admin/services.py:42-58`）已经把「一个请求里同一个问题只问一次」写出了可抄的样板（实例级 memo，随请求生命周期消失）。`TeamMembershipService` / `TeamService` 是每次请求新建的（`teams.py:98-110` 的 `Depends`），照抄即可：

```python
# app/domain/team/repositories.py — TeamRepository.__init__
        self._session = session
        #: 一个实例（= 一个请求）里 (team_id, user_id) -> 是不是成员。
        self._member_cache: dict[tuple[int, int], bool] = {}

    async def is_team_member(self, team_id: int, user_id: int) -> bool:
        key = (team_id, user_id)
        if key not in self._member_cache:
            stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation.id).where(
                and_(
                    TeamUserRelation.team_id == team_id,
                    TeamUserRelation.user_id == user_id,
                    TeamUserRelation.deleted_at.is_(None),
                )
            )
            self._member_cache[key] = (
                await self._session.execute(stmt)
            ).scalar_one_or_none() is not None
        return self._member_cache[key]
```
  注意：memo 的失效条件是「同一个请求里写入改变了自己的答案」——`join()`、`remove_team_member`、`approve_*` 都会改成员表。这些路径要么不走 `is_team_member`（`add_member` 走 `get_member_relation`），要么写完后不再问，所以在**路由请求范围内**安全；但必须在 docstring 里写清「写路径改了成员后要清缓存」，否则下一个人会踩。
   - 出处：这是平台内既有样板（`AdminService.admin_handles` 的第 42-58 行注释），不是外部协议。

5. **列表读的 `ETag` / `304` 可以推广。**
   - 适用范围：`GET /teams`、`GET /teams/{teamId}/members`、`GET /groups`、`GET /projects/{project_id}/members`——这些都是页面会反复轮询/切回来重拉的读。
   - 做法：`app/api/conditional.py` 的 `etag_for_json` / `if_none_match_hits` 已经在用（`routes/admin_members.py:40-69` 是完整样板：`Cache-Control: private, no-cache` + `ETag` + 命中回 304 且 304 也带这两个头）。注意这些读**不能给 `public`**（`admin_members.py:33-37` 写清了理由：被 CDN 缓存下来等于发给任何人）——`/teams` 与 `/groups` 的响应里带成员昵称，只能是 `private, no-cache`。
   - 出处：https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api

6. **创建类 POST 的幂等键（本组里 `POST /teams`、`POST /teams/{id}/join`、`POST /teams/{id}/invitations`、`POST /projects/{id}/invitations`）。**
   - 现状：平台里已有「用同一个 `request_id` 认同一件事」的先例（`POST /topics/{id}/messages` 走 `publication_id`），但本组这四个 POST 没有：网络抖动重试会开出第二支同名队伍（`create_team` 只会被 `exists_by_name` 挡住同名，第二支换个名字就进去了）、发第二张邀请（这个被 `pending_for` 挡住了）、重复入队（`join` 幂等，答 where they stand）。
   - 做法：接 Stripe 的 `Idempotency-Key` 头（键 → 首次的状态码与响应体，存 24h，参数不一致报错）。**这一条是全平台级的改动，建议单独排期**，本组只需登记：`POST /teams` 是风险最高的一条。
   - 出处：https://docs.stripe.com/api/idempotent_requests

7. **错误体补 `documentation_url` 与 `retry-after` 语境（低优先，但和别的组重复出现）。**
   - 本组里 `BadRequestError` / `ConflictError` / `NotFoundError`（`app/core/errors.py:48-95`）已经带结构化 `data`（`{"field": "name", "value": ...}`），比 GitHub 的 `errors[]` 还细。缺的是「指到哪里去看」与「能不能重试」。GitHub 的错误体固定三段 `message` / `documentation_url` / `status`（https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api），其中 `status` 我们已有（`code`）。**信封不能换 shape**，只能在信封内加字段。
   - 同样值得加的是响应头 `X-Request-Id`（GitHub 每个响应都带 `x-github-request-id`），排障时一次定位那一次请求。

8. **团队/小组名称搜索没有可用的索引（低优先，数据量大了再说）。**
   - `TeamRepository._name_search_filter`（`team/repositories.py:48-60`）在长查询上走 `to_tsvector('simple', name) @@ plainto_tsquery(...)` —— 没有表达式索引，必然顺扫；短查询回退 `name ILIKE '%x%'`，`ix_team_name`（`alembic/.../a95752502bb0_initial_schema.py:646`）是普通 btree，`%x%` 用不上。`GroupRepository.search`（`groups/repositories.py:65-67`）、`RecruitmentRepository._keyword_filter`（`recruitment_repositories.py:129-146`）同形。
   - 做法：要么给 `team.name` 建 `GIN (to_tsvector('simple', name))`，要么装 `pg_trgm` 建 `GIN (name gin_trgm_ops)` 让 ILIKE 走索引。**这条要给 DBA 看**，本组只是把证据（三处 FTS/ILIKE 的写法与列）列出来。

## 四、一句话总结

本组的核心问题集中在三处：**`team_user_relation` 没有索引**（每一条 `/teams/*` 都在为它顺扫）、**列表接口的 N+1**（`GET /teams`/`my-teams` 每队 3 条 SQL、`GET /groups` 每行一次 COUNT、两个邀请列表每行两次查询）、**同一请求里重复的成员判定**（一个 `GET /teams/{id}` 把同一条 SELECT 跑两遍）。`admin_members.py` 四个接口与团队的写路径（审批、邀请、加入链接）判权清晰、幂等做得扎实，是这一组里最不需要动的部分。安全上唯一实质发现是 `GET /recruitment` 的广场没有团队可见性过滤，隐身队的帖连同联系方式对匿名可见。
