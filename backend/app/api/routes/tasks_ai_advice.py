"""A task's AI advice: its records, its status, and the conversations about it.

Slice of `app/api/routes/tasks.py` (tracking issue #2143, the oversized-file
ratchet): the eight `/tasks/{taskId}/ai-advice*` routes, which are one concept --
"what did the model say about this task, and what have I asked it" -- plus the
two request models only they use, `TaskAIAdviceConversationContext` and the
`CreateTaskAIAdviceConversationRequest` that wraps it. Every handler reads and
writes through `TaskAIAdviceService` and nothing else, so the block moves as a
whole and `tasks.py` keeps every other route untouched.

What stays behind, and why. Two names are defined in `tasks.py` and imported
here. `_ensure_task_visible_for_advice` is the door these routes share -- the
advice quotes the task, so reading it takes the same visibility judgment as
reading the task itself; it reads `TaskRepository` directly, and the ratchet in
`tests/unit/test_domain_import_guard.py` counts (route module, repository module)
pairs, so moving it here would add a line to that ratchet for a function that did
not change. `get_task_ai_advice_service` is the dependency factory, and the unit
test in `tests/unit/test_task_ai_advice_routes.py` overrides it by importing it
from `app.api.routes.tasks`. A move changes where a handler lives and nothing
else, so both stay where they are and this module imports them -- the shape
`topics_documents.py` uses for the helpers it shares with `topics.py`. `tasks.py`
imports nothing from this module, so there is no cycle.

Ordering, which this group is unusually sensitive to. This module sorts after
`tasks.py` (`.` < `_`), so its router mounts after that file's. Inside the group
the route order is copied verbatim, and it matters:
`/{taskId}/ai-advice/conversations/{conversationId}` is registered before
`/{taskId}/ai-advice/conversations/stream`, so a GET of the stream URL resolves
to `get_ai_advice_conversation`. That is a pre-existing quirk of the group, not
something this move introduces -- and one that would change if the two were
reordered. Nothing left in `tasks.py` is a parameterized path that could shadow
any of these (`/{taskId}` is a single segment), so every moved path still reaches
the same handler and `app.openapi()` comes out byte-identical.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the same
`APIRouter(prefix="/tasks", tags=["Tasks"])` is all it takes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.routes.tasks import (
    _ensure_task_visible_for_advice,
    get_task_ai_advice_service,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    QuotaExceededError,
)
from app.db.session import get_db
from app.domain.task.task_ai_advice_service import TaskAIAdviceService

router = APIRouter(prefix="/tasks", tags=["Tasks"])


class TaskAIAdviceConversationContext(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    section: str | None = None
    section_index: int | None = Field(default=None, alias="sectionIndex")
    index: int | None = None


class CreateTaskAIAdviceConversationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    question: str
    parent_id: int | None = Field(default=None, alias="parentId")
    conversation_id: str | None = Field(default=None, alias="conversationId")
    model_type: str | None = Field(default=None, alias="modelType")
    context: TaskAIAdviceConversationContext | None = None

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        return value.strip()


@router.post(
    "/{taskId}/ai-advice",
    summary="Request AI Advice Generation",
)
async def request_task_ai_advice(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    ai_service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_task_visible_for_advice(db=db, task_id=task_id, auth_user=auth_user)
    try:
        status_value, quota_info = await ai_service.request_advice(
            task_id=task_id, user_id=auth_user.user_id
        )
    except QuotaExceededError:
        raise
    quota = {
        "remaining": quota_info.remaining,
        "total": quota_info.total,
        "reset_time": quota_info.reset_time.isoformat(),
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "status": status_value,
            "quota": quota,
        },
    }


@router.get(
    "/{taskId}/ai-advice",
    summary="List AI Advice Records",
)
async def list_task_ai_advice(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    await _ensure_task_visible_for_advice(db=db, task_id=task_id, auth_user=auth_user)
    advices = await service.list_advices(task_id=task_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"advices": advices},
    }


@router.get(
    "/{taskId}/ai-advice/status",
    summary="Get AI Advice Status",
)
async def get_task_ai_advice_status(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    await _ensure_task_visible_for_advice(db=db, task_id=task_id, auth_user=auth_user)
    status_value = await service.get_status(task_id=task_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"status": status_value},
    }


@router.get(
    "/{taskId}/ai-advice/conversations/grouped",
    summary="List AI Advice Conversations (Grouped)",
)
async def list_ai_advice_conversations_grouped(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    await _ensure_task_visible_for_advice(db=db, task_id=task_id, auth_user=auth_user)
    # Frontend `TasksApi.getGroupedConversations` types the response as
    # { conversations: ConversationGroupSummary[] }; "groups" was a Python-
    # side name that left data.conversations undefined and nothing rendered.
    # The sidebar it feeds is 「我的对话」 (新建/搜索/删除对话都在自己那一列上，
    # 标题是提问的前 60 字)，so the list is the caller's own — see the service/repo.
    groups = await service.list_conversations_grouped(
        task_id=task_id, user_id=auth_user.user_id
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"conversations": groups},
    }


@router.get(
    "/{taskId}/ai-advice/conversations/{conversationId}",
    summary="Get AI Advice Conversation",
)
async def get_ai_advice_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    conversation_id: Annotated[str, Path(alias="conversationId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
    db=Depends(get_db),
) -> dict:
    # Frontend (TaskAIAdviceChatService.getConversationById) expects
    #   { conversations: TaskAIAdviceConversation[] }
    # where each entry is a Q&A pair. Our internal storage is per-message
    # (role/content rows) so we pair user→assistant rows back into Q&A
    # records. Empty conversation = empty array.
    # The conversation id alone used to be the whole credential: `task_id` and
    # the authenticated caller were both discarded right here, so any signed-in
    # caller could read any task's advice conversation by id.
    await _ensure_task_visible_for_advice(db=db, task_id=task_id, auth_user=auth_user)
    try:
        payload = await service.get_conversation(
            task_id=task_id, conversation_id=conversation_id, user_id=auth_user.user_id
        )
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc

    convo = payload.get("conversation") or {}
    messages = convo.get("messages") or []
    paired: list[dict] = []
    pending_user: dict | None = None
    for msg in messages:
        if msg.get("role") == "user":
            pending_user = msg
        elif msg.get("role") == "assistant" and pending_user is not None:
            paired.append(
                {
                    "id": msg.get("id", 0),
                    "taskId": task_id,
                    "question": pending_user.get("content") or "",
                    "response": msg.get("content") or "",
                    "modelType": "standard",
                    "followupQuestions": [],
                    "conversationId": convo.get("conversationId"),
                    "createdAt": msg.get("createdAt"),
                    "tokensUsed": str(msg.get("tokensUsed") or ""),
                }
            )
            pending_user = None
    return {"code": 200, "message": "OK", "data": {"conversations": paired}}


@router.post(
    "/{taskId}/ai-advice/conversations",
    summary="Create AI Advice Conversation",
)
async def create_ai_advice_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    payload: CreateTaskAIAdviceConversationRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
    db=Depends(get_db),
) -> dict:
    await _ensure_task_visible_for_advice(db=db, task_id=task_id, auth_user=auth_user)
    question = payload.question.strip()
    if not question.strip():
        raise BadRequestError("question is required")
    context_payload = (
        payload.context.model_dump(by_alias=True, exclude_none=True)
        if payload.context
        else None
    )
    try:
        conversation, quota_info = await service.create_conversation(
            task_id=task_id,
            user_id=auth_user.user_id,
            question=question,
            parent_id=payload.parent_id,
            conversation_id=payload.conversation_id,
            context=context_payload,
        )
    except QuotaExceededError:
        raise
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    quota = {
        "remaining": quota_info.remaining,
        "total": quota_info.total,
        "reset_time": quota_info.reset_time.isoformat(),
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "conversation": conversation.get("conversation"),
            "quota": quota,
        },
    }


@router.delete(
    "/{taskId}/ai-advice/conversations/{conversationId}",
    summary="Delete AI Advice Conversation",
)
async def delete_ai_advice_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    conversation_id: Annotated[str, Path(alias="conversationId")],
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    await _ensure_task_visible_for_advice(db=db, task_id=task_id, auth_user=auth_user)
    # 删的就是读的那一个地址（题 + id + 主人），所以没有第二次判据：delete 自己
    # 会用同一句话拒绝「不是你的」和「不存在」。
    try:
        await service.delete_conversation(
            task_id=task_id, conversation_id=conversation_id, user_id=auth_user.user_id
        )
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    return {"code": 200, "message": "OK", "data": None}


@router.get(
    "/{taskId}/ai-advice/conversations/stream",
    summary="Stream AI Advice Conversation (SSE)",
)
async def stream_ai_advice_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    question: str = Query(..., description="User question"),
    modelType: str | None = Query(default=None),
    section: str | None = Query(default=None),
    index: int | None = Query(default=None),
    conversationId: str | None = Query(default=None),
    parentId: int | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TaskAIAdviceService = Depends(get_task_ai_advice_service),
    db=Depends(get_db),
):
    """Stream AI response via Server-Sent Events (SSE).

    EventSource (the browser API the frontend uses) only supports GET, so
    this endpoint accepts query params instead of a JSON body. Mirrors NT
    streamTaskAiAdviceConversation in TaskController.kt.
    """
    import json as json_module

    from fastapi.responses import StreamingResponse

    from app.domain.llm.llm_client import (
        LLMAPIError,
        LLMConnectionError,
        LLMRateLimitError,
        LLMTimeoutError,
    )

    await _ensure_task_visible_for_advice(db=db, task_id=task_id, auth_user=auth_user)
    question = question.strip() if question else ""
    if not question:
        raise BadRequestError("question is required")

    context_payload: dict | None = None
    if section:
        context_payload = {"section": section}
        if index is not None:
            context_payload["index"] = index
    _ = modelType  # streamed model selection not yet plumbed end-to-end
    _ = parentId  # parent message id for branching, not yet plumbed

    async def event_generator():
        try:
            async for chunk in service.stream_conversation(
                task_id=task_id,
                user_id=auth_user.user_id,
                question=question,
                conversation_id=conversationId,
                context=context_payload,
            ):
                if chunk.content:
                    data = json_module.dumps({"type": "content", "data": chunk.content})
                    yield f"data: {data}\n\n"
                if chunk.is_final and chunk.total_tokens:
                    data = json_module.dumps(
                        {
                            "type": "done",
                            "tokens": chunk.total_tokens,
                        }
                    )
                    yield f"data: {data}\n\n"
        except QuotaExceededError as exc:
            data = json_module.dumps(
                {"type": "error", "error": "quota_exceeded", "message": str(exc)}
            )
            yield f"data: {data}\n\n"
        except LLMTimeoutError as exc:
            data = json_module.dumps(
                {"type": "error", "error": "timeout", "message": str(exc)}
            )
            yield f"data: {data}\n\n"
        except LLMConnectionError as exc:
            data = json_module.dumps(
                {"type": "error", "error": "connection", "message": str(exc)}
            )
            yield f"data: {data}\n\n"
        except LLMRateLimitError as exc:
            data = json_module.dumps(
                {"type": "error", "error": "rate_limit", "message": str(exc)}
            )
            yield f"data: {data}\n\n"
        except LLMAPIError as exc:
            data = json_module.dumps(
                {
                    "type": "error",
                    "error": "llm_error",
                    "message": str(exc),
                    "status_code": exc.status_code,
                }
            )
            yield f"data: {data}\n\n"
        except ValueError as exc:
            data = json_module.dumps(
                {"type": "error", "error": "not_found", "message": str(exc)}
            )
            yield f"data: {data}\n\n"
        except Exception as exc:
            data = json_module.dumps(
                {"type": "error", "error": "internal", "message": str(exc)}
            )
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
