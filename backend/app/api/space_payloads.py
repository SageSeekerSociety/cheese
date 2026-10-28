import json
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from app.domain.space.services import SpaceService
from app.domain.task.protocol import Teaching
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRepository,
)


class SpaceRow(Protocol):
    id: Mapped[int]
    name: Mapped[str]
    intro: Mapped[str]
    description: Mapped[str]
    review_status: Mapped[str]
    review_reason: Mapped[str | None]
    avatar_id: Mapped[int | None]
    enable_rank: Mapped[bool]
    visible_task_limit: Mapped[int | None]
    default_category_id: Mapped[int | None]
    task_templates: Mapped[list]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class SpaceInviteCodeRow(Protocol):
    id: Mapped[int]
    space_id: Mapped[int]
    code: Mapped[str]
    max_uses: Mapped[int]
    use_count: Mapped[int]
    expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    note: Mapped[str | None]
    created_by: Mapped[int | None]


class SpaceCategoryRow(Protocol):
    id: Mapped[int]
    space_id: Mapped[int]
    name: Mapped[str]
    description: Mapped[str | None]
    display_order: Mapped[int]
    archived_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class SpaceDomainGroupRow(Protocol):
    id: Mapped[int]
    space_id: Mapped[int]
    name: Mapped[str]
    description: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class SpaceAdminRelationRow(Protocol):
    user_id: Mapped[int]
    role: Mapped[int]
    created_at: Mapped[datetime]


class TeachingRequest(BaseModel):
    """给 AI 队友的指导 (#8d772257 项目集级, #944 空间级与题目级) — 最小编辑入口。

    **The strict end of this key.** `Teaching.from_json` on the read path drops a
    bad field rather than raising, because `resolve()` runs on every turn of
    every project and a typo in one field of a 项目集 must not take down the
    twenty 赛题 under it. Here a person is looking at the form and can be told
    which field is wrong, so every field is checked and the ids are typed.

    Defined above the three request models that carry it (空间 PATCH, 项目集
    PATCH, 题目 create/patch) so all of them can name the type directly rather
    than through a forward reference.
    """

    model_config = ConfigDict(populate_by_name=True)

    #: How the AI teammate helps: ``guide`` | ``example`` | ``spec`` | ``free``;
    #: None = this level does not say (a 题目 keeps its space's).
    mode: Literal["guide", "example", "spec", "free"] | None = None
    #: The teacher's own requirements, in their words.
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    #: 出题人的**私有**指导（#944 第二种可见性）：进 AI 队友的 prompt、**不进学生
    #: 可读的响应**。写它和写 `systemPrompt` 同一条路，读它只在出题人/管理员那几条
    #: 读写上（`teaching_to_api(include_private=True)`）。
    private_prompt: str | None = Field(default=None, alias="privatePrompt")
    #: 本节学习目标。写上它这份指导就按教育性走：`systemPrompt` 留空时带上
    #: 平台的默认教学要求（`harness.opening.DEFAULT_TEACHING_PROMPT`）。
    learning_goals: list[str] = Field(default_factory=list, alias="learningGoals")
    #: 课件 / 知识材料的引用。正文不在这里 —— 它们各自有自己的库和接口，这里只
    #: 存指向它们的 id。
    material_ids: list[Annotated[int, Field(gt=0)]] = Field(
        default_factory=list, alias="materialIds"
    )
    #: The team documents the course points its AI teammates at (#3774).
    document_ids: list[
        Annotated[
            str, Field(pattern=r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
        ]
    ] = Field(default_factory=list, alias="documentIds")
    #: Skills every AI teammate of the projects under this level carries on
    #: every turn (`Teaching.skills`): names from the platform's skill catalog
    #: (`GET /skill-catalog`), or a skill a participant's own machine holds.
    skills: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list
    )


def teaching_to_api(teaching: Teaching, *, include_private: bool = False) -> dict:
    """The read side of `TeachingRequest`: the same camelCase keys.

    The column stores snake_case (the spelling `Teaching.from_json` reads), and
    every response that carries a 指导 goes through here rather than returning
    the stored dict: a form fills itself from what it reads and saves that back
    whole, so a read in another spelling shows it blank and the next save erases
    the stored config.

    ``include_private`` is the one key that is NOT always present: the 私有指导
    (`privatePrompt`) is 出题人可见, so it is carried only on the reads/writes a
    teacher or a board manager is looking at. Default off — every reader that
    did not ask is a student's, and a forgotten call site then leaks nothing.
    """
    data = {
        "mode": teaching.mode,
        "systemPrompt": teaching.system_prompt,
        "learningGoals": list(teaching.learning_goals),
        "materialIds": list(teaching.material_ids),
        "documentIds": list(teaching.document_ids),
        "skills": list(teaching.skills),
    }
    if include_private:
        data["privatePrompt"] = teaching.private_prompt
    return data


class CreateSpaceCategoryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1)
    description: str | None = None
    display_order: int = Field(default=0, alias="displayOrder")
    #: 项目集级默认队友 (#944) — same semantics as the PATCH: omitting it leaves
    #: the column unset; an empty string means the same as omitting it here
    #: (there is no inherited value to clear on a row that does not exist yet).
    default_role: str | None = Field(default=None, alias="defaultRole")


class PatchSpaceCategoryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    display_order: int | None = Field(default=None, alias="displayOrder")
    archived: bool | None = None
    archived_at: int | None = Field(default=None, alias="archivedAt")
    #: 项目集级默认队友 (#944) — a platform-shipped agent type name every 题目
    #: under this 分组 starts from. Omitting it leaves the stored value alone; an
    #: empty string clears it (the 题目 then inherits nothing at this level).
    default_role: str | None = Field(default=None, alias="defaultRole")


class CreateSpaceDomainGroupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1)
    description: str | None = None
    domains: list[str] = Field(default_factory=list)


class PatchSpaceDomainGroupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    domains: list[str] | None = None


class CreateSpaceInviteCodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_uses: int | None = Field(default=None, alias="maxUses")
    expires_at: int | None = Field(default=None, alias="expiresAt")
    # 这张码给谁 / 干什么用. Optional and free text; omitted or blank is stored
    # as NULL, which the read side reports as "no note" rather than "".
    note: str | None = Field(default=None, alias="note")


class PatchSpaceInviteCodeRequest(BaseModel):
    """What may still be changed on a live code.

    Both fields are nullable and both are optional, and the two mean different
    things: an absent ``expiresAt`` leaves the date alone, an explicit null
    clears it. The route reads ``model_fields_set`` to tell them apart.
    ``note`` follows the same rule: absent leaves the 说明 alone, null (or a
    blank string, which the service normalises to the same thing) clears it.
    """

    model_config = ConfigDict(populate_by_name=True)

    max_uses: int | None = Field(default=None, alias="maxUses")
    expires_at: int | None = Field(default=None, alias="expiresAt")
    note: str | None = Field(default=None, alias="note")


class AddSpaceManagerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId", gt=0)
    role: str = "ADMIN"


class PatchSpaceManagerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str = Field(..., min_length=1)


def _space_to_api_model(space: SpaceRow, *, include_private: bool = False) -> dict:
    created_at_ms = int(space.created_at.timestamp() * 1000) if space.created_at else 0
    updated_at_ms = int(space.updated_at.timestamp() * 1000) if space.updated_at else 0
    return {
        "id": space.id,
        "name": space.name,
        "intro": space.intro,
        "description": space.description,
        "reviewStatus": space.review_status,
        "reviewReason": space.review_reason,
        "avatarId": space.avatar_id,
        "enableRank": space.enable_rank,
        "visibleTaskLimit": space.visible_task_limit,
        "defaultCategoryId": space.default_category_id,
        "taskTemplates": json.dumps(space.task_templates or []),
        # 空间级「给 AI 队友的指导」(#944) — the settings form reads it back, so it
        # has to be here rather than only on the write path. `include_private` is
        # set only where the caller is a board manager (the settings page).
        "teaching": teaching_to_api(
            Teaching.from_json(getattr(space, "teaching", None)),
            include_private=include_private,
        ),
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _invite_code_to_api_model(
    invite: SpaceInviteCodeRow, created_by: dict | None = None
) -> dict:
    """One code as the 邀请码 screen reads it.

    ``createdBy`` is the same hydrated person object a member row carries, and
    is passed in rather than read off the row because turning a user id into a
    name is a batched query the caller already runs for the whole list. It is
    ``None`` when the row names nobody — the screen says 未知 rather than
    rendering an empty name.
    """
    created_at_ms = (
        int(invite.created_at.timestamp() * 1000) if invite.created_at else 0
    )
    expires_at_ms = (
        int(invite.expires_at.timestamp() * 1000) if invite.expires_at else None
    )
    return {
        "id": invite.id,
        "spaceId": invite.space_id,
        "code": invite.code,
        "maxUses": invite.max_uses,
        "useCount": invite.use_count,
        "expiresAt": expires_at_ms,
        "createdAt": created_at_ms,
        "note": invite.note,
        "createdBy": created_by,
    }


async def _invite_codes_to_api_models(
    invites: Sequence[SpaceInviteCodeRow],
    *,
    db: AsyncSession,
) -> list[dict]:
    """The list form of the above, with its makers hydrated in one query.

    Two queries for however many codes, the same shape ``_hydrate_members``
    uses: a per-row lookup would grow with the number of codes on the board.
    """
    maker_ids = [c.created_by for c in invites if c.created_by is not None]
    people = (
        await _hydrate_people(
            maker_ids,
            user_repo=UserRepository(session=db),
            profile_repo=UserProfileRepository(session=db),
        )
        if maker_ids
        else {}
    )
    return [
        _invite_code_to_api_model(
            invite, people.get(invite.created_by) if invite.created_by else None
        )
        for invite in invites
    ]


def _category_to_api_model(
    cat: SpaceCategoryRow, *, include_private: bool = False
) -> dict:
    created_at_ms = int(cat.created_at.timestamp() * 1000) if cat.created_at else 0
    updated_at_ms = int(cat.updated_at.timestamp() * 1000) if cat.updated_at else 0
    raw_archived_at = getattr(cat, "archived_at", None)
    archived_at_ms = (
        int(raw_archived_at.timestamp() * 1000) if raw_archived_at else None
    )
    return {
        "id": cat.id,
        "spaceId": cat.space_id,
        "name": cat.name,
        "description": cat.description,
        "displayOrder": cat.display_order,
        # 项目集级默认队友 (#944) — the edit form reads it back too.
        "defaultRole": getattr(cat, "default_role", None),
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
        "archivedAt": archived_at_ms,
    }


def _domain_group_to_api_model(group: SpaceDomainGroupRow, domains: list[str]) -> dict:
    created_at_ms = int(group.created_at.timestamp() * 1000) if group.created_at else 0
    updated_at_ms = int(group.updated_at.timestamp() * 1000) if group.updated_at else 0
    return {
        "id": group.id,
        "spaceId": group.space_id,
        "name": group.name,
        "description": group.description,
        "domains": domains,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _admin_to_api_model(
    rel: SpaceAdminRelationRow,
    user_info: dict | None = None,
) -> dict:
    created_at_ms = int(rel.created_at.timestamp() * 1000) if rel.created_at else 0
    result: dict = {
        "userId": rel.user_id,
        # The stored role is an int the space domain names (`SpaceAdminRole`);
        # `admin_role_name` reads that name without this module importing the
        # enum off the model.
        "role": SpaceService.admin_role_name(rel.role),
        "createdAt": created_at_ms,
    }
    if user_info is not None:
        result["user"] = user_info
    return result


async def _hydrate_people(
    user_ids: Sequence[int],
    *,
    user_repo: UserRepository,
    profile_repo: UserProfileRepository,
) -> dict[int, dict]:
    """user_id → the display fields a roster row carries. Two queries for
    however many people are asked about.

    This used to be a `get_by_id` + `get_profile_by_user_id` pair per person,
    inside the loop that built the list — which made rendering a space cost
    two queries per member plus two per admin, on every read of it, with the
    count growing as the class does. Both repositories have batched readers
    for exactly this (`get_by_ids`, `get_profiles_by_user_ids`).

    Someone whose `user` row is gone falls back to the id and a placeholder
    name: the membership row is still real, and dropping them from the list
    would make the roster disagree with itself.
    """
    ids = list(user_ids)
    users = await user_repo.get_by_ids(ids)
    profiles = await profile_repo.get_profiles_by_user_ids(ids)
    # 「挑过的头像」和档案分开问，别回档案上的 avatar_id：每个注册路径都往那一列写死
    # 了全局默认（默认头像 id 因环境而异，见 UserProfileRepository.chosen_avatar_ids），
    # 直接回它会让所有没挑过头像的人共用同一张脸 —— 而区分人正是头像唯一的活。没挑过
    # 的人不在映射里，这里回 None，前端据此画彩色首字母。
    chosen_avatars = await profile_repo.chosen_avatar_ids(ids)
    people: dict[int, dict] = {}
    for user_id in ids:
        user = users.get(user_id)
        profile = profiles.get(user_id)
        people[user_id] = (
            {
                "id": user.id,
                "username": user.username,
                "nickname": profile.nickname if profile else user.username,
                "avatarId": chosen_avatars.get(user_id),
                "intro": profile.intro if profile else "",
            }
            if user
            else {"id": user_id, "username": "unknown"}
        )
    return people


async def _build_admins_payload(
    space_id: int,
    *,
    service: SpaceService,
    user_repo: UserRepository,
    profile_repo: UserProfileRepository,
) -> list[dict]:
    """Hydrate admin relations with full user objects.

    The frontend Space type requires `admins[].user.id`, and stores/space.ts
    overwrites local state with whatever each mutation returns — so any
    response that includes `space` must include hydrated admins, otherwise
    admin-only UI silently disappears after a PATCH.
    """
    admin_relations = await service.list_admins(space_id)
    people = await _hydrate_people(
        [rel.user_id for rel in admin_relations],
        user_repo=user_repo,
        profile_repo=profile_repo,
    )
    return [_admin_to_api_model(rel, people[rel.user_id]) for rel in admin_relations]


async def _build_full_space_payload(
    space: SpaceRow,
    *,
    service: SpaceService,
    db,
    include_private: bool = False,
) -> dict:
    """Build a Space response dict that matches the frontend Space type.

    Always includes `admins` (hydrated) and `classificationTopics` so that any
    GET/POST/PATCH response is interchangeable from the frontend's perspective
    (its store overwrites local state with the response payload).

    ``include_private`` is passed through to the teaching payload — a board
    manager's view (settings page, create/patch reply) carries the 私有指导;
    a member's does not.
    """
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    space_data = _space_to_api_model(space, include_private=include_private)
    space_data["admins"] = await _build_admins_payload(
        space.id, service=service, user_repo=user_repo, profile_repo=profile_repo
    )
    topics = await service.list_classification_topics(space.id)
    space_data["classificationTopics"] = [{"id": t.id, "name": t.name} for t in topics]
    return space_data
