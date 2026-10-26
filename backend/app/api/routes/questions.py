from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.api.deps import get_discussion_service
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES, Can
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.db.session import get_db
from app.domain.answers.repositories import AnswerRepository
from app.domain.discussion.services import DiscussableModelType, DiscussionService
from app.domain.questions.permissions import Permission as QuestionPermission
from app.domain.questions.repositories import (
    QuestionInvitationRepository,
    QuestionRepository,
    QuestionTopicRepository,
)
from app.domain.questions.services import QuestionInvitationService, QuestionsService
from app.domain.user.repositories import UserProfileRepository

router = APIRouter(prefix="/questions", tags=["Questions"])


async def get_questions_service(db=Depends(get_db)) -> QuestionsService:
    repo = QuestionRepository(session=db)
    topic_repo = QuestionTopicRepository(session=db)
    answer_repo = AnswerRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    return QuestionsService(
        repo=repo,
        topic_repo=topic_repo,
        answer_repo=answer_repo,
        profile_repo=profile_repo,
    )


async def get_invitation_service(db=Depends(get_db)) -> QuestionInvitationService:
    invitation_repo = QuestionInvitationRepository(session=db)
    question_repo = QuestionRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    answer_repo = AnswerRepository(session=db)
    return QuestionInvitationService(
        repo=invitation_repo,
        question_repo=question_repo,
        profile_repo=profile_repo,
        answer_repo=answer_repo,
    )


@router.get(
    "/trending",
    summary="Get Trending Questions",
)
async def get_trending_questions(
    limit: int = Query(default=10, ge=1, le=50),
    days: int = Query(default=7, ge=1, le=30),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    items = await service.get_trending_questions(limit=limit, days=days)
    return ok({"questions": items})


@router.get(
    "/stats",
    summary="Get Question Stats",
)
async def get_question_stats(
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    stats = await service.get_stats()
    return ok(stats)


@router.get(
    "/search-terms",
    summary="Get Popular Search Terms",
)
async def get_popular_search_terms(
    limit: int = Query(default=10, ge=1, le=50),
    days: int = Query(default=7, ge=1, le=30),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    terms = await service.get_popular_search_terms(limit=limit, days=days)
    return ok({"terms": terms})


@router.get(
    "",
    summary="Search Questions",
)
async def search_questions(
    q: str | None = Query(default=None),
    page_start: int | None = Query(default=None, ge=0, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    _ = auth_user
    items, page = await service.search_questions(
        keyword=q,
        page_size=page_size,
        page_start=page_start,
        sort_by="createdAt",
        sort_order="desc",
    )
    return ok({"questions": items, "page": page})


@router.post(
    "", summary="Create Question", status_code=201, dependencies=[ROUTE_DECIDES]
)
async def add_question(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    title = payload.get("title") or ""
    content = payload.get("content") or ""
    # `type`/`bounty` must be numeric — a non-numeric value is a client error (400),
    # not an unhandled 500 from a bare int().
    try:
        type_ = int(payload.get("type", 0))
        bounty = int(payload.get("bounty", 0))
    except (TypeError, ValueError):
        raise BadRequestError("type and bounty must be integers") from None
    group_id = payload.get("groupId")
    topic_ids = payload.get("topics") or payload.get("topicIds") or []
    if not isinstance(topic_ids, list):
        raise BadRequestError("topics must be an array")
    # Coerce each entry: frontend sometimes ships ["5","7"] from a select
    # control without numeric binding. Drop anything that fails int().
    topic_ints: list[int] = []
    for t in topic_ids:
        if isinstance(t, bool):
            continue
        try:
            topic_ints.append(int(t))
        except (TypeError, ValueError):
            continue

    question = await service.create_question(
        user_id=auth_user.user_id,
        title=title,
        content=content,
        type_=type_,
        group_id=group_id,
        bounty=bounty,
        topic_ids=topic_ints,
    )
    return ok({"id": question["id"]})


@router.get(
    "/followed",
    summary="List Followed Questions",
)
async def list_followed_questions(
    pageStart: int | None = Query(default=None, ge=0, alias="page_start"),
    pageSize: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    items, page = await service.list_followed(
        user_id=auth_user.user_id,
        page_size=pageSize,
        page_start=pageStart,
    )
    return ok({"questions": items, "page": page})


@router.get(
    "/{question_id}",
    summary="Get Question",
)
async def get_question(
    question_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    question = await service.get_question(question_id, user_id)
    return ok({"question": question})


@router.post(
    "/{question_id}/followers",
    summary="Follow Question",
    status_code=201,
    dependencies=[ROUTE_DECIDES],
)
async def follow_question(
    question_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    changed = await service.follow_question(
        question_id=question_id, user_id=auth_user.user_id
    )
    if not changed:
        raise BadRequestError("Already followed")
    follow_count = await service._repo.count_followers(question_id)
    return ok({"follow_count": follow_count})


@router.delete(
    "/{question_id}/followers",
    summary="Unfollow Question",
    dependencies=[ROUTE_DECIDES],
)
async def unfollow_question(
    question_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    changed = await service.unfollow_question(
        question_id=question_id, user_id=auth_user.user_id
    )
    if not changed:
        raise BadRequestError("Not followed")
    return ok({"unfollowed": True})


@router.put(
    "/{question_id}/acceptance",
    summary="Accept Answer",
    dependencies=[Can(QuestionPermission.manage)],
)
async def accept_answer(
    question_id: Annotated[int, Path(ge=0)],
    answer_id: int = Query(..., alias="answer_id"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    question = await service.accept_answer(question_id=question_id, answer_id=answer_id)
    return ok({"question": question})


@router.delete(
    "/{question_id}/accept",
    summary="Unaccept Answer",
    dependencies=[Can(QuestionPermission.manage)],
)
async def unaccept_answer(
    question_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    question = await service.unaccept_answer(question_id=question_id)
    return ok({"question": question})


@router.post(
    "/{question_id}/vote", summary="Vote on Question", dependencies=[ROUTE_DECIDES]
)
async def vote_question(
    question_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    vote_type = payload.get("voteType", "UPVOTE")
    result = await service.vote_question(
        question_id=question_id, user_id=auth_user.user_id, vote_type=vote_type
    )
    return ok(result)


@router.delete(
    "/{question_id}/vote",
    summary="Remove Vote from Question",
    dependencies=[ROUTE_DECIDES],
)
async def remove_question_vote(
    question_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    result = await service.remove_question_vote(
        question_id=question_id, user_id=auth_user.user_id
    )
    return ok(result)


@router.get(
    "/{question_id}/vote",
    summary="Get Question Votes",
)
async def get_question_votes(
    question_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    result = await service.get_question_votes(question_id=question_id, user_id=user_id)
    return ok(result)


@router.get(
    "/{question_id}/comments",
    summary="List Question Comments",
)
async def list_question_comments(
    question_id: Annotated[int, Path(ge=0)],
    page_start: int | None = Query(default=None, ge=0, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    sort_by: str = Query(default="createdAt"),
    sort_order: str = Query(default="asc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    items, page = await discussion_service.list_discussions(
        model_type=DiscussableModelType.QUESTION.value,
        model_id=question_id,
        parent_id=None,
        page_start=page_start,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        current_user_id=user_id,
        include_subs=True,
        with_reactions=True,
    )
    return ok({"comments": items, "page": page})


@router.post(
    "/{question_id}/comments",
    summary="Create Question Comment",
    status_code=201,
    dependencies=[ROUTE_DECIDES],
)
async def create_question_comment(
    question_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    content = payload.get("content", "")
    parent_id = payload.get("parentId")
    mentioned_user_ids = payload.get("mentionedUserIds", [])
    comment = await discussion_service.create_discussion(
        user_id=auth_user.user_id,
        content=content,
        model_type=DiscussableModelType.QUESTION.value,
        model_id=question_id,
        parent_id=parent_id,
        mentioned_user_ids=mentioned_user_ids,
    )
    return ok({"comment": comment})


@router.delete(
    "/{question_id}/comments/{comment_id}",
    summary="Delete Question Comment",
    dependencies=[ROUTE_DECIDES],
)
async def delete_question_comment(
    question_id: Annotated[int, Path(ge=0)],
    comment_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    discussion_service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    discussion = await discussion_service.get_discussion(comment_id, auth_user.user_id)
    # 父级绑定：URL 里的 question_id 得和这条评论挂的题对上，不然拿别的题（哪怕
    # 不存在的题）做前缀照样能把一条题目评论删掉（`_ = question_id` 的同一形状，
    # 口径同上面邀请那一族和 `delete_answer_comment`）。404 而不是 403：错配的
    # id 不指向任何东西，403 会承认「这条评论存在，只是不在这道题上」。
    if (
        discussion["modelType"] != DiscussableModelType.QUESTION.value
        or discussion["modelId"] != question_id
    ):
        raise NotFoundError("Comment not found for this question")
    # A closed account's comment has no sender left to compare with.
    if (discussion["sender"] or {}).get("id") != auth_user.user_id:
        raise ForbiddenError("Only the author can delete this comment")
    await discussion_service.delete_discussion(comment_id)
    return ok({"deleted": True})


@router.put(
    "/{question_id}",
    summary="Update Question",
    dependencies=[Can(QuestionPermission.manage)],
)
async def update_question(
    question_id: Annotated[int, Path()],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    title = payload.get("title")
    content = payload.get("content")
    type_ = payload.get("type")
    topics = payload.get("topics")
    topic_ids = None
    if topics is not None:
        if not isinstance(topics, list):
            raise BadRequestError("topics must be an array")
        topic_ids = []
        for t in topics:
            if isinstance(t, bool):
                continue
            try:
                v = int(t)
            except (TypeError, ValueError):
                continue
            if v > 0:
                topic_ids.append(v)
    question = await service.update_question(
        question_id=question_id,
        user_id=auth_user.user_id,
        title=title,
        content=content,
        type_=type_,
        topic_ids=topic_ids,
    )
    return ok({"question": question})


@router.delete(
    "/{question_id}",
    summary="Delete Question",
    status_code=204,
    dependencies=[Can(QuestionPermission.manage)],
)
async def delete_question(
    question_id: Annotated[int, Path()],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> None:
    await service.delete_question(question_id=question_id)


@router.get(
    "/{question_id}/followers",
    summary="List Question Followers",
)
async def list_question_followers(
    question_id: Annotated[int, Path(ge=0)],
    page_start: int | None = Query(default=None, ge=0, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    # Who follows a question is read only signed in, as the question itself is.
    _ = auth_user
    follower_ids, page = await service.list_followers(
        question_id=question_id,
        page_size=page_size,
        page_start=page_start,
    )
    users = [{"id": uid} for uid in follower_ids]
    return ok({"users": users, "page": page})


@router.put(
    "/{question_id}/followers",
    summary="Follow Question (PUT)",
    dependencies=[ROUTE_DECIDES],
)
async def follow_question_put(
    question_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    await service.follow_question(question_id=question_id, user_id=auth_user.user_id)
    follow_count = await service._repo.count_followers(question_id)
    return ok({"follow_count": follow_count})


@router.put(
    "/{question_id}/bounty",
    summary="Set Question Bounty",
    dependencies=[Can(QuestionPermission.manage)],
)
async def set_question_bounty(
    question_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    bounty = int(payload.get("bounty", 0))
    result = await service.set_bounty(question_id=question_id, bounty=bounty)
    return ok({"bounty": result["bounty"]})


@router.post(
    "/{question_id}/attitudes",
    summary="Attitude on Question (Vote)",
    dependencies=[ROUTE_DECIDES],
)
async def attitude_question(
    question_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionsService = Depends(get_questions_service),
) -> dict:
    attitude_type = payload.get("attitude_type", "UNDEFINED")
    vote_type = attitude_type if attitude_type in ("POSITIVE", "NEGATIVE") else None
    if vote_type is None:
        result = await service.remove_question_vote(
            question_id=question_id, user_id=auth_user.user_id
        )
    else:
        result = await service.vote_question(
            question_id=question_id, user_id=auth_user.user_id, vote_type=vote_type
        )
    attitudes = {
        "positive_count": result.get("upvotes", 0),
        "negative_count": result.get("downvotes", 0),
        "difference": result.get("upvotes", 0) - result.get("downvotes", 0),
        "user_attitude": attitude_type,
    }
    return ok({"attitudes": attitudes})


# ── 邀请的读侧 ──────────────────────────────────────────────────────────
#
# 这一族有三条读路由：列表、推荐、详情。三条以前**都不问「你是谁」**（连
# `require_auth_user` 都没有），详情那条还不看 URL 里的 `question_id`
# （`_ = question_id`），于是不带任何凭据、顺着小整数 id 就能拼出「谁被邀请去答了
# 哪些题、答没答」。
#
# 口径是**任何登录用户**，理由是同族的写侧就是这个口径：`create_invitation` 只问
# 「登录了没有」（`invite_user_to_answer` 上面那条），页面上也是这么用的 ——
# `views/question/Detail.vue` 的邀请对话框只要题目有赏金、没采纳答案就出现，没有
# 作者判断，`components/questions/InvitationList.vue` 就直接调这三条。要收成
# 「只有出题人」的话，改的不止读侧，还有写侧和页面上的对话框 —— 那是产品口径的
# 决定，不是权限修复能顺手带走的东西。今天也没有免登录的公开入口读邀请：详情那条
# 全仓库没有调用方，列表和推荐只从登录后的页面里调。
#
# 详情（和它下面的 DELETE）还要**绑父级**：`question_id` 与邀请自己的
# `question_id` 不一致 → 404。错配的 id 不指向任何东西，403 会承认这张邀请存在。
# 绑定写在 `QuestionInvitationService` 里，因为它是一个领域规则、不是一处路由的
# 装饰：路由只负责把 URL 里那个 id 交进去，`_ = question_id` 这种「读进来再丢掉」
# 的写法从此无处可写。


@router.get(
    "/{question_id}/invitations",
    summary="List Question Invitations",
)
async def list_question_invitations(
    question_id: Annotated[int, Path(ge=0)],
    page_start: int | None = Query(default=None, ge=0, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionInvitationService = Depends(get_invitation_service),
) -> dict:
    items, page = await service.list_invitations(
        question_id=question_id,
        page_start=page_start,
        page_size=page_size,
    )
    return ok({"invitations": items, "page": page})


@router.post(
    "/{question_id}/invitations",
    summary="Invite User to Answer",
    status_code=201,
    dependencies=[ROUTE_DECIDES],
)
async def invite_user_to_answer(
    question_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionInvitationService = Depends(get_invitation_service),
) -> dict:
    raw_invitee = payload.get("user_id")
    if isinstance(raw_invitee, bool) or raw_invitee is None:
        raise BadRequestError("user_id is required")
    try:
        invitee_id = int(raw_invitee)
    except (TypeError, ValueError):
        raise BadRequestError("user_id must be a positive integer") from None
    if invitee_id <= 0:
        raise BadRequestError("user_id must be a positive integer")
    result = await service.create_invitation(
        question_id=question_id,
        inviter_id=auth_user.user_id,
        invitee_id=invitee_id,
    )
    return ok(
        {
            "invitationId": result["invitation_id"],
            "invitation": result["invitation"],
        }
    )


@router.get(
    "/{question_id}/invitations/recommendations",
    summary="Get Invitation Recommendations",
)
async def get_invitation_recommendations(
    question_id: Annotated[int, Path(ge=0)],
    limit: int = Query(default=10, ge=1, le=50),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionInvitationService = Depends(get_invitation_service),
) -> dict:
    users = await service.get_recommendations(question_id=question_id, limit=limit)
    return ok({"users": users})


@router.get(
    "/{question_id}/invitations/{invitation_id}",
    summary="Get Invitation Detail",
)
async def get_invitation_detail(
    question_id: Annotated[int, Path(ge=0)],
    invitation_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionInvitationService = Depends(get_invitation_service),
) -> dict:
    invitation = await service.get_invitation(
        question_id=question_id, invitation_id=invitation_id
    )
    return ok({"invitation": invitation})


@router.delete(
    "/{question_id}/invitations/{invitation_id}",
    summary="Delete Invitation",
    dependencies=[Can(QuestionPermission.manage)],
)
async def delete_invitation(
    question_id: Annotated[int, Path(ge=0)],
    invitation_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: QuestionInvitationService = Depends(get_invitation_service),
) -> dict:
    await service.delete_invitation(
        question_id=question_id, invitation_id=invitation_id
    )
    return ok({"deleted": True})
