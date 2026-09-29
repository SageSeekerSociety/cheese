from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.api.routes.answers import get_answers_service
from app.api.routes.knowledge import get_knowledge_service
from app.api.routes.questions import get_questions_service
from app.api.routes.spaces import _ensure_space_visible
from app.api.routes.tasks import _ensure_task_readable, get_task_service
from app.api.routes.teams import get_team_service
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.db.session import get_db
from app.domain.discussion.models import DiscussableModelType
from app.domain.discussion.reaction_services import DiscussionReactionService
from app.domain.discussion.repositories import (
    DiscussionReactionRepository,
    DiscussionRepository,
    ReactionTypeRepository,
)
from app.domain.discussion.services import DiscussionService
from app.domain.user.repositories import UserProfileRepository

router = APIRouter(prefix="/discussions", tags=["Discussions"])


def build_discussion_service(db) -> DiscussionService:
    repo = DiscussionRepository(session=db)
    reaction_repo = DiscussionReactionRepository(session=db)
    reaction_type_repo = ReactionTypeRepository(session=db)
    reaction_service = DiscussionReactionService(reaction_repo, reaction_type_repo)
    profile_repo = UserProfileRepository(session=db)
    return DiscussionService(
        repo=repo,
        reaction_service=reaction_service,
        profile_repo=profile_repo,
        session=db,
    )


async def get_discussion_service(db=Depends(get_db)) -> DiscussionService:
    return build_discussion_service(db)


async def _ensure_model_visible(
    *,
    db,
    model_type: str,
    model_id: int,
    auth_user: AuthUserInfo,
) -> None:
    """「这条讨论挂着的那块板，这个人看得见吗」—— 一处，按各域已有的门问。

    ``discussion`` 表是多态的：一行只记 ``model_type`` + ``model_id`` 两个字符串 /
    整数（``app.domain.discussion.models``），没有任何外键，也从不 join 被挂的对象。
    于是路由层若只看「登录了没有」，就等于把这块板的**存在、内容、以及往里发帖的
    权利**交给任何一个登录用户 —— 这正是本条活的洞。所以每个 handler 先经过这里：
    把 ``model_id`` 指的那个父对象**加载出来**，再问它自己所属域已经问了很多遍的
    那句话。不在这里另写一套判据：每一条都是同域其它读路由正在用的那一条。

    各类型对应哪条判据（一句话一个）。每一条都是**用那个域自己的 service 问的**，
    不是摸它的 repository：分层纪律见 ``tests/unit/test_domain_import_guard.py``，
    路由 import 对方的 repository 会让那道守卫红。取服务走的是各路由模块现成的工厂
    （``get_task_service`` 等），与 ``app/api/routes/dashboard.py`` import
    ``get_space_service``、``_require_project_access`` 是同一个做法：

    * ``SPACE`` —— ``_ensure_space_visible``（``SpaceRepository.is_member``：成员行
      或管理员关系），不是成员答 404，和 ``/spaces/{spaceId}/...`` 一致。
    * ``TASK`` —— ``TaskService.get_task`` 取那一行（不存在 404），再交给
      ``_ensure_task_readable``（未过审 403、``can_view_task`` 不可见 404、超本板上限
      404）—— 与题目详情、材料清单同一处判断。
    * ``PROJECT`` —— 一律 404。本平台的项目主键是 UUID，而 ``Discussion.model_id``
      这一列是整数，没有任何整数能指向一个项目，父对象因此**加载不出来**；加载不出
      来就不放行（fail closed）。若将来给项目板一个整数可寻址的键，这里应当挂
      ``app.auth.project_access.may_read_project`` —— 项目读权那一条单点判据（服务上
      没有可用的读法，届时也不需要摸 repository）。
    * ``TEAM`` —— ``TeamService.visible_team``（成员，或公开共享团队），看不见答
      404，与 ``GET /teams/{teamId}`` 同一句话。
    * ``KNOWLEDGE`` —— ``KnowledgeService.get`` 本身就是那道门：条目不存在 404、条目
      所属团队的非成员 403，与 ``/knowledge/{knowledgeId}`` 一致。
    * ``QUESTION`` / ``ANSWER`` —— ``QuestionsService.get_question`` /
      ``AnswersService.get_answer``：只要这个问答对象存在（``... not found`` 404）。
      学习问答按设计对每一个登录用户公开（``GET /questions/{id}``、
      ``GET /answers`` 本来就不做逐人判断），所以这里照抄「存在即可见」，不收紧也
      不放松 —— 讨论板不比它挂着的那道题更私密。

    未知 / 非法 ``model_type`` 一律 400，而不是「没认出来就不判」。
    """
    try:
        kind = DiscussableModelType(model_type.upper())
    except ValueError as exc:
        raise BadRequestError(f"Invalid modelType: {model_type}") from exc

    user_id = auth_user.user_id

    if kind is DiscussableModelType.SPACE:
        await _ensure_space_visible(db=db, space_id=model_id, user_id=user_id)
        return

    if kind is DiscussableModelType.TASK:
        task = await (await get_task_service(db=db)).get_task(model_id)
        if task is None:
            raise NotFoundError(
                "Resource task not found", data={"type": "task", "id": model_id}
            )
        await _ensure_task_readable(db=db, task=task, auth_user=auth_user)
        return

    if kind is DiscussableModelType.PROJECT:
        raise NotFoundError(
            "Resource project not found", data={"type": "project", "id": model_id}
        )

    if kind is DiscussableModelType.TEAM:
        await (await get_team_service(db=db)).visible_team(model_id, user_id)
        return

    if kind is DiscussableModelType.KNOWLEDGE:
        # ``KnowledgeService.get`` 自己就是那道门：条目不存在 404，条目所属团队的
        # 非成员 403 —— 与 ``/knowledge/{knowledgeId}`` 逐字相同。
        await (await get_knowledge_service(db=db)).get(
            knowledge_id=model_id, user_id=user_id
        )
        return

    if kind is DiscussableModelType.QUESTION:
        # 学习问答对每个登录用户公开，所以这里的门就是「这道题存在」。
        await (await get_questions_service(db=db)).get_question(model_id, user_id)
        return

    if kind is DiscussableModelType.ANSWER:
        await (await get_answers_service(db=db)).get_answer(
            answer_id=model_id, user_id=user_id
        )
        return


async def _ensure_discussion_visible(
    *, db, discussion_id: int, auth_user: AuthUserInfo
) -> None:
    """按 id 操作的几条路由共用的入口：先找到这一行挂在哪块板上，再问那块板可见否。

    ``discussionId`` 是客户端给的，它挂着哪块板只有这一行自己知道 —— 所以先加载这
    一行（不存在就 404），再走 ``_ensure_model_visible``。看不见那块板的人，对这条
    讨论应当得到「不存在」，而不是「存在但你不能看」。
    """
    entity = await DiscussionRepository(session=db).get_by_id(discussion_id)
    if entity is None:
        raise NotFoundError(
            "Resource discussion not found",
            data={"type": "discussion", "id": discussion_id},
        )
    await _ensure_model_visible(
        db=db,
        model_type=entity.model_type,
        model_id=entity.model_id,
        auth_user=auth_user,
    )


@router.post("", summary="Create Discussion", status_code=201)
async def create_discussion(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
    db=Depends(get_db),
) -> dict:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    model_type = payload.get("modelType")
    model_id = payload.get("modelId")
    content = payload.get("content")
    parent_id = payload.get("parentId")
    mentioned = payload.get("mentionedUserIds") or []
    if not isinstance(model_type, str) or not model_type.strip():
        raise BadRequestError("modelType is required")
    if not isinstance(model_id, int) or model_id <= 0:
        raise BadRequestError("modelId must be a positive integer")
    if parent_id is not None and (not isinstance(parent_id, int) or parent_id <= 0):
        raise BadRequestError("parentId must be positive when provided")
    mentioned_ids = [int(x) for x in mentioned if isinstance(x, int) and x > 0]

    # 发帖是「往那块板里写」：先证明这个人看得见那块板，再落这一行。
    await _ensure_model_visible(
        db=db, model_type=model_type, model_id=model_id, auth_user=auth_user
    )
    if parent_id is not None:
        # 回复只能回同一块板里的讨论。不判的话，任何人都能把一条回复折到**别人
        # 版面上**的帖子里去（父对象由客户端给，行却挂在自己的板上），于是那条帖
        # 的楼里凭空多出一段自己写的内容。父对象找不到、或不属于这块板，一律
        # 答「这条讨论不存在」—— 与「看不见的讨论不存在」同一个答案。
        parent = await DiscussionRepository(session=db).get_by_id(parent_id)
        if (
            parent is None
            or parent.model_type != model_type.upper()
            or parent.model_id != model_id
        ):
            raise NotFoundError(
                "Resource discussion not found",
                data={"type": "discussion", "id": parent_id},
            )

    discussion = await service.create_discussion(
        user_id=auth_user.user_id,
        content=str(content or ""),
        model_type=model_type,
        model_id=model_id,
        parent_id=parent_id,
        mentioned_user_ids=mentioned_ids,
    )
    return {"code": 201, "message": "Created", "data": {"discussion": discussion}}


@router.get("", summary="List Discussions")
async def list_discussions(
    modelType: str | None = Query(default=None),
    modelId: int | None = Query(default=None),
    parentId: int | None = Query(default=None),
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str = Query(default="createdAt", alias="sortBy"),
    sortOrder: str = Query(default="desc", alias="sortOrder"),
    sort_by: str | None = Query(default=None, alias="sort_by"),
    sort_order: str | None = Query(default=None, alias="sort_order"),
    withReactions: bool = Query(default=True),
    withSubDiscussions: bool = Query(default=True),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
    db=Depends(get_db),
) -> dict:
    # Frontend sends snake_case sort params for these endpoints; accept both.
    effective_sort_by = sort_by or sortBy
    effective_sort_order = sort_order or sortOrder
    if effective_sort_by not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {effective_sort_by}")
    if effective_sort_order.lower() not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {effective_sort_order}")
    if modelId is not None and modelId <= 0:
        raise BadRequestError("modelId must be positive if provided")
    if parentId is not None and parentId <= 0:
        raise BadRequestError("parentId must be positive if provided")
    # 这块板必须被指名，而且必须看得见。从前省略 modelType / modelId 等于「不过滤」，
    # 于是这个路由成了全平台的讨论总汇 —— 一次请求拿到所有人所有板上的帖子。没有
    # 父对象就没有可以问的那句话，所以缺一即拒。
    if not isinstance(modelType, str) or not modelType.strip():
        raise BadRequestError("modelType is required")
    if modelId is None:
        raise BadRequestError("modelId is required")
    await _ensure_model_visible(
        db=db, model_type=modelType, model_id=modelId, auth_user=auth_user
    )

    rows, page = await service.list_discussions(
        model_type=modelType.upper(),
        model_id=modelId,
        parent_id=parentId,
        page_start=pageStart,
        page_size=pageSize,
        sort_by=effective_sort_by,
        sort_order=effective_sort_order,
        current_user_id=auth_user.user_id,
        include_subs=withSubDiscussions,
        with_reactions=withReactions,
    )
    return {"code": 200, "message": "OK", "data": {"discussions": rows, "page": page}}


@router.get(
    "/reactions",
    summary="Get all reaction types",
)
async def list_reaction_types(
    service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    types = await service.list_reaction_types()
    return {"code": 200, "message": "OK", "data": {"reactionTypes": types}}


@router.get("/{discussionId}", summary="Get Discussion")
async def get_discussion(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str = Query(default="createdAt", alias="sortBy"),
    sortOrder: str = Query(default="desc", alias="sortOrder"),
    sort_by: str | None = Query(default=None, alias="sort_by"),
    sort_order: str | None = Query(default=None, alias="sort_order"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
    db=Depends(get_db),
) -> dict:
    # Frontend's discussionStore.loadDiscussion expects
    #   data: { discussion, subDiscussions: { discussions, page } }
    # so Detail.vue can render replies inline. Without subDiscussions the
    # `currentMessage.subDiscussions.examples` access in DiscussionDetail.vue
    # is always undefined and the replies area never renders. Mirrors NT
    # DiscussionController.getDiscussion which returns the same envelope.
    # Accept both camelCase (sortBy) and snake_case (sort_by) for sort
    # params — the frontend sends snake_case for these two keys.
    effective_sort_by = sort_by or sortBy
    effective_sort_order = sort_order or sortOrder
    if effective_sort_by not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {effective_sort_by}")
    if effective_sort_order.lower() not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {effective_sort_order}")

    await _ensure_discussion_visible(
        db=db, discussion_id=discussion_id, auth_user=auth_user
    )

    discussion = await service.get_discussion(discussion_id, auth_user.user_id)
    sub_rows, sub_page = await service.list_discussions(
        model_type=None,
        model_id=None,
        parent_id=discussion_id,
        page_start=pageStart,
        page_size=pageSize,
        sort_by=effective_sort_by,
        sort_order=effective_sort_order,
        current_user_id=auth_user.user_id,
        include_subs=False,
        with_reactions=True,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "discussion": discussion,
            "subDiscussions": {
                "discussions": sub_rows,
                "page": sub_page,
            },
        },
    }


@router.patch("/{discussionId}", summary="Update Discussion")
async def patch_discussion(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
    db=Depends(get_db),
) -> dict:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise BadRequestError("content is required")
    # 「作者能改」之外还有一道：这块板本身得是这个人看得见的。少了它，一个人即使
    # 恰好是作者（例如已被移出题目板），仍然改得动一条他再也看不见的帖子。
    await _ensure_discussion_visible(
        db=db, discussion_id=discussion_id, auth_user=auth_user
    )
    discussion = await service.update_discussion(
        discussion_id, content=content.strip(), user_id=auth_user.user_id
    )
    return {"code": 200, "message": "OK", "data": {"discussion": discussion}}


@router.get("/{discussionId}/sub-discussions", summary="List Sub Discussions")
async def list_sub_discussions(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str = Query(default="createdAt", alias="sortBy"),
    sortOrder: str = Query(default="desc", alias="sortOrder"),
    sort_by: str | None = Query(default=None, alias="sort_by"),
    sort_order: str | None = Query(default=None, alias="sort_order"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
    db=Depends(get_db),
) -> dict:
    effective_sort_by = sort_by or sortBy
    effective_sort_order = sort_order or sortOrder
    if effective_sort_by not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {effective_sort_by}")
    if effective_sort_order.lower() not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {effective_sort_order}")
    await _ensure_discussion_visible(
        db=db, discussion_id=discussion_id, auth_user=auth_user
    )
    rows, page = await service.list_discussions(
        model_type=None,
        model_id=None,
        parent_id=discussion_id,
        page_start=pageStart,
        page_size=pageSize,
        sort_by=effective_sort_by,
        sort_order=effective_sort_order,
        current_user_id=auth_user.user_id,
        include_subs=False,
        with_reactions=True,
    )
    return {"code": 200, "message": "OK", "data": {"discussions": rows, "page": page}}


@router.delete("/{discussionId}", summary="Delete Discussion", status_code=204)
async def delete_discussion(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
    db=Depends(get_db),
) -> None:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    await _ensure_discussion_visible(
        db=db, discussion_id=discussion_id, auth_user=auth_user
    )
    discussion = await service.get_discussion(discussion_id, auth_user.user_id)
    sender = discussion.get("sender")
    if sender is None or sender["id"] != auth_user.user_id:
        raise ForbiddenError("Only the author can delete this discussion")
    await service.delete_discussion(discussion_id)
    return None


@router.post(
    "/{discussionId}/reactions/{reactionTypeId}",
    summary="Toggle Discussion Reaction",
)
async def toggle_reaction(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    reaction_type_id: Annotated[int, Path(ge=1, alias="reactionTypeId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
    db=Depends(get_db),
) -> dict:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    await _ensure_discussion_visible(
        db=db, discussion_id=discussion_id, auth_user=auth_user
    )
    result = await service.toggle_reaction(
        discussion_id=discussion_id,
        reaction_type_id=reaction_type_id,
        user_id=auth_user.user_id,
    )
    return {"code": 200, "message": "OK", "data": {"reaction": result}}


@router.delete(
    "/{discussionId}/reactions/{reactionTypeId}",
    summary="Remove Discussion Reaction",
)
async def remove_reaction(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    reaction_type_id: Annotated[int, Path(ge=1, alias="reactionTypeId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
    db=Depends(get_db),
) -> dict:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    await _ensure_discussion_visible(
        db=db, discussion_id=discussion_id, auth_user=auth_user
    )
    result = await service.remove_reaction(
        discussion_id=discussion_id,
        reaction_type_id=reaction_type_id,
        user_id=auth_user.user_id,
    )
    return {"code": 200, "message": "OK", "data": {"reaction": result}}
