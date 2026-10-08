"""把任务领域的行摆成接口要的那些 dict。

从 ``routes/tasks.py`` 挪出来的 —— 那个文件早已越过体积帽（超帽只许缩），而「把任务
行摆成接口形状」本来就不碰路由、不碰会话，独立成篇也说得通。放在 ``app/api`` 而不是
``app/api/routes``：它不是路由，也不该吃「路由不许直接 import 领域的 models」那条约束
（``deps.py`` / ``doc_store.py`` 是同样的先例）。``#944`` 的题目级指导只是这张 dict
里的一格，读法在 ``task_teaching``。

这一层是 **transport 的序列化**：库形状 → 接口形状、以及列表页那几张「填进去」的
enrichment（作者、管理员、分类、附件数、话题、当前用户的参与状态）。它们不决定谁能
看、能不能改 —— 那些判据在 ``app.domain.task`` 里。
"""

from collections.abc import Sequence
from datetime import datetime

from app.api.task_teaching import read_task_teaching
from app.domain.attachment.models import Attachment
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceDomainGroupDomainRepository,
    SpaceRepository,
)
from app.domain.task.attachment_service import TaskAttachmentRepository
from app.domain.task.models import (
    Task,
    TaskAttachment,
    TaskMembership,
    TaskSubmissionSchemaEntry,
)
from app.domain.task.repositories import (
    TaskMembershipRepository,
    TaskSubmissionRepository,
    TaskSubmissionSchemaRepository,
    TopicRepository,
)
from app.domain.task.services import TaskMembershipService
from app.domain.task.submission_state import claim_state
from app.domain.team.models import Team
from app.domain.team.summary import team_summary
from app.domain.user.repositories import UserProfileRepository, UserRepository


def _ms(moment: datetime | None) -> int | None:
    """A moment as epoch milliseconds, the unit the task API speaks; None stays None."""
    return int(moment.timestamp() * 1000) if moment is not None else None


def _task_to_api_model(task: Task) -> dict:
    created_at_ms = _ms(task.created_at) or 0
    updated_at_ms = _ms(task.updated_at) or 0
    deadline_ms = _ms(task.deadline)
    # 审核痕迹是后加的两列：老题（以及还没审过的题）没有它，一律回 null ——
    # 界面上「没有审核人」与「不知道审核人」是同一件事，不做区分。
    reviewed_by = getattr(task, "reviewed_by", None)
    published_at_ms = _ms(getattr(task, "published_at", None))
    ended_at_ms = _ms(getattr(task, "ended_at", None))
    reviewed_at_ms = _ms(getattr(task, "reviewed_at", None))
    registration_start_ms = _ms(task.registration_start_at)
    approved_map = {0: "APPROVED", 1: "DISAPPROVED", 2: "NONE"}
    submitter_type_map = {0: "USER", 1: "TEAM"}
    return {
        "id": task.id,
        "name": task.name,
        "intro": task.intro,
        "description": task.description,
        "deadline": deadline_ms,
        "registrationStartAt": registration_start_ms,
        "defaultDeadline": task.default_deadline,
        "resubmittable": task.resubmittable,
        "editable": task.editable,
        "approved": approved_map.get(task.approved, "NONE"),
        "rank": task.rank,
        "submitterType": submitter_type_map.get(task.submitter_type, "USER"),
        "submissionSchema": [],
        "space": {"id": task.space_id},
        "category": {"id": task.category_id, "name": ""},
        "categoryId": task.category_id,
        "createdBy": task.creator_id,
        "creator": {"id": task.creator_id},
        "requireRealName": task.require_real_name,
        "participantLimit": task.participant_limit,
        "minTeamSize": task.min_team_size,
        "maxTeamSize": task.max_team_size,
        "teamLockingPolicy": task.team_locking_policy,
        "rejectReason": task.reject_reason,
        "accessControlEnabled": task.access_control_enabled,
        # 这道题自己的「给 AI 队友的指导」(#944)：报的是**原始覆盖**，不是解析后的
        # 赢家 —— 发题页编的是这道题自己写了什么，空 = 继承，只有对着原始值才讲得通。
        "teaching": read_task_teaching(task),
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
        "publishedAt": published_at_ms,
        "endedAt": ended_at_ms,
        "reviewedBy": reviewed_by,
        "reviewedAt": reviewed_at_ms,
    }


_SUBMISSION_SCHEMA_TYPES = {0: "TEXT", 1: "FILE"}


def _submission_schema_to_api(
    entries: Sequence[TaskSubmissionSchemaEntry],
) -> list[dict]:
    """表单行的库形状 → 接口形状。详情与列表两处都报同一份，在这里收口。"""
    return [
        {
            "prompt": entry.description,
            "type": _SUBMISSION_SCHEMA_TYPES.get(entry.type, "TEXT"),
        }
        for entry in entries
    ]


async def _enrich_task_submission_schema(db, task_models: list[dict]) -> None:
    """把这一页每道题的提交表单填进列表响应（就地改）。

    列表默认不带这张表单（``_task_to_api_model`` 报空数组），因为它的调用方
    很多、多数不关心；审核页那种要显示「提交要求」的读法点名要它。
    整页一次查询，不按题各发一条。
    """
    task_ids = [
        task_model["id"]
        for task_model in task_models
        if isinstance(task_model.get("id"), int)
    ]
    if not task_ids:
        return

    grouped = await TaskSubmissionSchemaRepository(session=db).list_by_task_ids(
        task_ids
    )
    for task_model in task_models:
        model_id = task_model.get("id")
        if not isinstance(model_id, int):
            continue
        task_model["submissionSchema"] = _submission_schema_to_api(
            grouped.get(model_id, [])
        )


async def _enrich_task_models(
    db,
    task_models: list[dict],
    *,
    space_id: int,
) -> list[dict]:
    if not task_models:
        return task_models

    task_ids = [task_model["id"] for task_model in task_models]
    creator_ids = [
        task_model.get("creator", {}).get("id") for task_model in task_models
    ]
    creator_ids = [
        creator_id for creator_id in creator_ids if isinstance(creator_id, int)
    ]

    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    membership_repo = TaskMembershipRepository(session=db)
    category_repo = SpaceCategoryRepository(session=db)
    admin_repo = SpaceAdminRelationRepository(session=db)
    space_repo = SpaceRepository(session=db)

    admin_relations = await admin_repo.list_admins(space_id)
    admin_user_ids = [rel.user_id for rel in admin_relations]
    all_user_ids = list({*creator_ids, *admin_user_ids})

    users = await user_repo.get_by_ids(all_user_ids)
    profiles = await profile_repo.get_profiles_by_user_ids(all_user_ids)
    # 头像走「挑过的」判据，别回档案上的 avatar_id：每个注册路径都往那一列写死了全局
    # 默认（见 UserProfileRepository.chosen_avatar_ids），直接回它会让所有没挑过头像
    # 的人共用同一张脸。没挑过的人不在映射里，下面回 None，前端画彩色首字母。
    chosen_avatars = await profile_repo.chosen_avatar_ids(all_user_ids)
    memberships = await membership_repo.list_memberships_for_space(space_id)
    categories = await category_repo.list_categories_for_space(
        space_id, include_archived=True
    )
    space = await space_repo.get_by_id(space_id)
    space_name = space.name if space is not None else "Unknown Space"

    role_name_map = {0: "OWNER", 1: "ADMIN"}
    admins_payload: list[dict] = []
    for rel in admin_relations:
        user = users.get(rel.user_id)
        profile = profiles.get(rel.user_id)
        if user is not None:
            nickname = (
                profile.nickname if profile and profile.nickname else user.username
            )
            avatar_id = chosen_avatars.get(rel.user_id)
            intro = profile.intro if profile else ""
            user_payload = {
                "id": user.id,
                "username": user.username,
                "nickname": nickname,
                "avatarId": avatar_id,
                "intro": intro,
                "question_count": 0,
                "answer_count": 0,
            }
        else:
            user_payload = {
                "id": rel.user_id,
                "username": "unknown",
                "nickname": "unknown",
                "avatarId": None,
                "intro": "",
                "question_count": 0,
                "answer_count": 0,
            }

        admins_payload.append(
            {
                "role": role_name_map.get(rel.role, "ADMIN"),
                "user": user_payload,
            }
        )

    category_name_map: dict[int, str] = {
        int(category.id): category.name
        for category in categories
        if getattr(category, "name", None)
    }

    participant_counts: dict[int, int] = {task_id: 0 for task_id in task_ids}
    for membership in memberships:
        if membership.task_id in participant_counts:
            participant_counts[membership.task_id] += 1

    for task_model in task_models:
        creator_id = task_model.get("creator", {}).get("id")
        category_id = task_model.get("categoryId")
        if not isinstance(category_id, int):
            category_id = task_model.get("category", {}).get("id")
        user = users.get(creator_id) if isinstance(creator_id, int) else None
        profile = profiles.get(creator_id) if isinstance(creator_id, int) else None

        if user is not None:
            nickname = (
                profile.nickname if profile and profile.nickname else user.username
            )
            avatar_id = chosen_avatars.get(creator_id)
            intro = profile.intro if profile else ""
            task_model["creator"] = {
                "id": user.id,
                "username": user.username,
                "nickname": nickname,
                "avatarId": avatar_id,
                "intro": intro,
            }
        else:
            task_model["creator"] = {
                "id": creator_id,
                "username": "unknown",
                "nickname": "unknown",
                "avatarId": None,
                "intro": "",
            }

        task_model["participants"] = {
            "total": participant_counts.get(task_model["id"], 0),
            "examples": [],
        }

        current_space_id = task_model.get("space", {}).get("id")
        if not isinstance(current_space_id, int):
            current_space_id = space_id
        task_model["space"] = {
            "id": current_space_id,
            "name": space_name,
            "admins": admins_payload,
        }

        resolved_category_name = (
            category_name_map.get(category_id) if isinstance(category_id, int) else None
        )
        task_model["category"] = {
            "id": category_id,
            "name": resolved_category_name or "Uncategorized",
        }
        task_model["categoryId"] = category_id

    # Resolve accessDomainGroupIds from stored TaskAccessDomain rows. 整页一次取回，
    # 不按题各发两条 —— 一屏 20 道题就是 40 条查询。只有开了访问控制的题要问，
    # 答案按题归属（见仓储方法的注释）。
    access_control_task_ids = [
        task_model["id"]
        for task_model in task_models
        if isinstance(task_model.get("id"), int)
        and task_model.get("accessControlEnabled")
    ]
    access_group_ids = await SpaceDomainGroupDomainRepository(
        session=db
    ).list_group_ids_by_task_ids(space_id=space_id, task_ids=access_control_task_ids)
    # 传入的 dict 不能预置非空的 accessDomainGroupIds：这里只在命中组时覆盖，没有
    # 命中组就是「保持 setdefault 的空列表」——调用方预置了值、这道题又存了域但在本
    # 板里没有命中组时，那个预置值会留下来（旧的逐题写法会覆盖成空）。现有调用点都用
    # _task_to_api_model 构造 dict，不带这个键。
    for task_model in task_models:
        task_model.setdefault("accessDomainGroupIds", [])
        model_id = task_model.get("id")
        if isinstance(model_id, int) and access_group_ids.get(model_id):
            task_model["accessDomainGroupIds"] = sorted(access_group_ids[model_id])

    return task_models


async def _enrich_task_attachment_counts(db, task_models: list[dict]) -> None:
    """把每道题挂着几个材料填进列表响应（就地改）。

    只是**一个数**：不带文件本体、不带文件名，也不问谁能下载 —— 卡片上那格「附件
    N」就靠它，而清单与下载各有自己的门（`GET /tasks/{id}/attachments`）。一个材料
    都没有的题给 0，不是缺字段：卡片按「有就列、没有就不显示」写，而 0 与「不知道」
    是两件事。

    整页一次分组计数，不按题各发一条。
    """
    task_ids = [
        task_model["id"]
        for task_model in task_models
        if isinstance(task_model.get("id"), int)
    ]
    if not task_ids:
        return
    counts = await TaskAttachmentRepository(session=db).count_live_by_task_ids(
        task_ids=task_ids
    )
    for task_model in task_models:
        model_id = task_model.get("id")
        if not isinstance(model_id, int):
            continue
        task_model["attachmentCount"] = counts.get(model_id, 0)


async def _enrich_task_topics(db, task_models: list[dict]) -> None:
    """Populate `task.topics: Topic[]` for the given task dicts in-place.

    Frontend `Task.topics` is an array of {id, name} objects, accessed via
    `task.topics.length` in TaskCard.vue, so we always return at least an
    empty array (not undefined).

    整页一次取回，不按题各发一条 —— 卡片现在每道题都显示标签，逐题各查一次就是
    一屏 20 条查询。
    """
    if not task_models:
        return
    task_ids = [
        task_model["id"]
        for task_model in task_models
        if isinstance(task_model.get("id"), int)
    ]
    grouped = await TopicRepository(session=db).list_by_task_ids(task_ids)
    for task_model in task_models:
        model_id = task_model.get("id")
        if not isinstance(model_id, int):
            continue
        task_model["topics"] = [
            {"id": t.id, "name": t.name} for t in grouped.get(model_id, [])
        ]


async def _enrich_task_user_state(
    membership_service: TaskMembershipService,
    tasks: list[Task],
    task_models: list[dict],
    *,
    user_id: int,
    query_joinability: bool,
    db,
) -> None:
    """Populate per-user task state (joined / submittable / userDeadline / ...).

    Mirrors the per-task computation in `get_task_detail` so list responses
    expose the same fields the frontend expects when query flags are set.
    """
    if user_id <= 0:
        # Anonymous viewer — set placeholders so the keys exist (matches the
        # detail endpoint response shape).
        for task_model in task_models:
            task_model.setdefault("joined", False)
            task_model.setdefault("joinedTeams", [])
            task_model.setdefault("submittable", None)
            task_model.setdefault("submittableAsTeam", [])
            task_model.setdefault("userDeadline", None)
            task_model.setdefault("participationEligibility", None)
            task_model.setdefault("myClaimStatus", None)
        return

    # Frontend Task.joinedTeams / submittableAsTeam are typed `Team[]`; the
    # leave-task UI accesses joinedTeams[0].id and .name directly. We used to
    # ship arrays of bare ids / `{id}` stubs, so the dialog rendered
    # "确定要让小队\"undefined\"退出该赛题吗？". Bulk-fetch real Team rows
    # for every team_id that shows up across the task list.
    from app.domain.team.repositories import TeamRepository as _TeamRepo

    team_repo = _TeamRepo(session=db)

    by_id = {task.id: task for task in tasks}

    # First pass: collect team_ids needed across all rows in this list.
    pending: dict[int, dict] = {}
    needed_team_ids: set[int] = set()
    for task_model in task_models:
        task_id = task_model["id"]
        user_membership = await membership_service.get_user_membership(
            task_id=task_id, user_id=user_id
        )
        team_memberships = await membership_service.list_team_memberships_for_user(
            task_id=task_id, user_id=user_id
        )
        for m in team_memberships:
            needed_team_ids.add(m.member_id)
        pending[task_id] = {
            "user_membership": user_membership,
            "team_memberships": team_memberships,
        }

    teams_map = (
        await team_repo.get_by_ids(list(needed_team_ids)) if needed_team_ids else {}
    )

    def _team_summary(team_id: int) -> dict:
        return team_summary(teams_map.get(team_id), fallback_id=team_id)

    # 我在每道题上走到哪一步（卡片上那格「我的领取档位」）。只问我**本人**那条
    # 领取：队友那条说的是小队走到哪了，不是这张卡要说的事。判决照
    # `app.domain.task.submission_state` 的 `claim_state` 算 —— 它和
    # `completion_status` 同一份优先级，但这一格不看截止时间，所以按判决直接翻，
    # 而不是读那一列。整页一次查询，不按题各发一条。
    my_membership_by_task_id = {
        task_id: state["user_membership"].id
        for task_id, state in pending.items()
        if state["user_membership"] is not None
        and state["user_membership"].approved != 1
    }
    verdicts_by_membership_id = await TaskSubmissionRepository(
        session=db
    ).list_review_verdicts_for_memberships(
        membership_ids=list(my_membership_by_task_id.values())
    )

    for task_model in task_models:
        task_id = task_model["id"]
        task = by_id.get(task_id)
        submitter_type = task.submitter_type if task is not None else 0
        user_membership = pending[task_id]["user_membership"]
        team_memberships = pending[task_id]["team_memberships"]

        # DISAPPROVED memberships should NOT show as "joined" — otherwise the
        # frontend renders "退出赛题" button for rejected applications.
        joined = bool(
            (user_membership and user_membership.approved != 1)
            or any(m.approved != 1 for m in team_memberships)
        )
        joined_teams = [_team_summary(m.member_id) for m in team_memberships]
        is_user_approved = bool(user_membership and user_membership.approved == 0)

        submittable: bool | None = None
        submittable_as_team: list[dict] = []
        user_deadline_ms: int | None = None

        if submitter_type == 0:  # USER
            submittable = is_user_approved
            if user_membership and user_membership.deadline:
                user_deadline_ms = int(user_membership.deadline.timestamp() * 1000)
        elif submitter_type == 1:  # TEAM
            approved_team_memberships = [m for m in team_memberships if m.approved == 0]
            submittable = bool(approved_team_memberships)
            submittable_as_team = [
                _team_summary(m.member_id) for m in approved_team_memberships
            ]
            if team_memberships and team_memberships[0].deadline:
                user_deadline_ms = int(team_memberships[0].deadline.timestamp() * 1000)

        participation_eligibility: dict | None = None
        if query_joinability and task is not None:
            participation_eligibility = (
                await membership_service.get_participation_eligibility(
                    task=task,
                    user_id=user_id,
                )
            )

        # 没领过的题给 null（卡片那一格整块不出现），领过的题给四档之一 ——
        # 一条提交都没有的领取是 IN_PROGRESS，不是 null：领了没交和没领是两件事。
        my_membership_id = my_membership_by_task_id.get(task_id)
        my_claim_status: str | None = None
        if my_membership_id is not None:
            my_claim_status = claim_state(
                verdicts_by_membership_id.get(my_membership_id, [])
            )

        task_model.update(
            {
                "joined": joined,
                "joinedTeams": joined_teams,
                "submittable": submittable,
                "submittableAsTeam": submittable_as_team,
                "userDeadline": user_deadline_ms,
                "participationEligibility": participation_eligibility,
                "myClaimStatus": my_claim_status,
            }
        )


def _build_participant_user_info(
    membership: TaskMembership,
    *,
    user_map: dict | None = None,
    profile_map: dict | None = None,
    team_map: dict | None = None,
    avatar_map: dict | None = None,
) -> dict:
    """Build the user or team identity displayed on a registration.

    ``avatar_map`` 是 user_id → 这个人**自己挑过**的头像 id，由调用方经
    ``UserProfileRepository.chosen_avatar_ids`` 批量取得。不能拿 ``profile_map`` 里的
    ``profile.avatar_id``：每个注册路径都往那一列写死了全局默认（默认头像是哪一行因
    环境而异），直接回它等于给所有没挑过头像的人同一张脸。没挑过的人不在映射里，这里
    回 None，前端 ``RosterView`` 据此画彩色首字母。
    """
    user_map = user_map or {}
    profile_map = profile_map or {}
    team_map = team_map or {}
    avatar_map = avatar_map or {}
    if membership.is_team and membership.member_id in team_map:
        return team_summary(
            team_map[membership.member_id], fallback_id=membership.member_id
        )
    if not membership.is_team and membership.member_id in user_map:
        user = user_map[membership.member_id]
        profile = profile_map.get(membership.member_id)
        nickname = (
            profile.nickname
            if profile and getattr(profile, "nickname", None)
            else user.username
        )
        return {
            "id": user.id,
            "username": user.username,
            "nickname": nickname,
            "name": nickname,
            "avatarId": avatar_map.get(membership.member_id),
            "intro": profile.intro if profile else "",
        }
    return {"id": membership.member_id}


def _membership_to_api_model(
    membership: TaskMembership,
    *,
    participant_info: dict | None = None,
    team: Team | None = None,
) -> dict:
    """Minimal TaskMembership representation for participants list.

    NOTE: This is a simplified view that focuses on structure. More fields
    (real name info, team members, etc.) can be added as needed.

    ``team`` 是这条报名背后的队（只由批量查过队名的调用者传）。传了才多出
    ``team`` 字段；没传（单条、PATCH 那几条路由）返回体与以前一模一样。
    """
    created_at_ms = _ms(membership.created_at) or 0
    updated_at_ms = _ms(membership.updated_at) or 0

    participant = participant_info or {"id": membership.member_id}
    member = participant

    approved_map = {0: "APPROVED", 1: "DISAPPROVED", 2: "NONE"}
    approved_str = approved_map.get(membership.approved, "NONE")

    model = {
        "id": membership.id,
        "taskId": membership.task_id,
        "memberId": membership.member_id,
        "member": member,
        "participant": participant,
        "isTeam": membership.is_team,
        "email": membership.email,
        "phone": membership.phone,
        "completionStatus": membership.completion_status,
        "approved": approved_str,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
        # 出题人给这个人单设的提交截止时间（批准时按「提交期限」定，之后可单独改）。
        "deadline": _ms(membership.deadline),
        "applyReason": membership.pitch or None,
    }
    if membership.is_team and team is not None:
        # 队名：看板按它给「小队构成」分桶、在名册里写下是哪支队伍。个人领取没有
        # 这个字段；团队领取但队已不在（查不到行）也没有 —— 前端据此退回「小队」。
        model["team"] = {"id": team.id, "name": team.name}
    return model


def _uploaded_attachment_to_api(attachment: Attachment) -> dict:
    """一个**还没挂到任何题上**的文件在预览响应里的样子。

    与 ``_task_attachment_to_api`` 是同一套字段来源（名字、大小、类型都取自
    ``meta``），只是没有那两样要有关联行才成立的东西：下载计数、挂上来的时间。同样
    **不带 url** —— 存储给的是直链，发出去就等于绕开下载那道门。
    """
    return {
        "id": attachment.id,
        "name": attachment.meta.get("filename") or f"attachment_{attachment.id}",
        "size": attachment.meta.get("size", 0),
        "contentType": attachment.meta.get("contentType", "application/octet-stream"),
    }


def _task_attachment_to_api(*, attachment: Attachment, link: TaskAttachment) -> dict:
    """一个附件在接口上的样子。

    **不带 url**：存储给的是直链（本地 ``/uploads/...``、S3 公开地址），把它发出去
    就等于把「下载限人」这道门绕过去了。要文件就走下面那个下载端点，门在那里。
    """
    return {
        "id": attachment.id,
        "name": attachment.meta.get("filename") or f"attachment_{attachment.id}",
        "size": attachment.meta.get("size", 0),
        "contentType": attachment.meta.get("contentType", "application/octet-stream"),
        "uploaderId": attachment.meta.get("uploaderId"),
        "downloadCount": link.download_count,
        "createdAt": int(link.created_at.timestamp() * 1000),
    }
