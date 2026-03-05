from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError
from app.db.session import get_db
from app.domain.llm.chat_service import AIChatService
from app.domain.llm.repositories import (
    AIConversationRepository,
    AIMessageRepository,
    AIUserQuotaRepository,
)
from app.domain.llm.services import AiAdviceService, QuotaExceededError


router = APIRouter(prefix="/ai", tags=["AI"])


async def get_ai_service(db=Depends(get_db)) -> AiAdviceService:
    repo = AIUserQuotaRepository(session=db)
    return AiAdviceService(repo=repo)


async def get_chat_service(db=Depends(get_db)) -> AIChatService:
    conv_repo = AIConversationRepository(session=db)
    msg_repo = AIMessageRepository(session=db)
    quota_repo = AIUserQuotaRepository(session=db)
    return AIChatService(
        conversation_repo=conv_repo,
        message_repo=msg_repo,
        quota_repo=quota_repo,
    )


@router.get("/quota", summary="Get Current User's AI Quota")
async def get_ai_quota(
    service: AiAdviceService = Depends(get_ai_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    quota = await service.get_quota(user_id=auth_user.user_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "quota": {
                "remaining": quota.remaining,
                "daily": quota.total,
                "used": quota.total - quota.remaining,
                "resetTime": quota.reset_time.isoformat(),
            }
        },
    }


@router.get("/models", summary="List Available AI Models")
async def list_ai_models(
    service: AIChatService = Depends(get_chat_service),
) -> dict:
    models = service.list_models()
    return {"code": 200, "message": "OK", "data": {"models": models}}


@router.get("/conversations", summary="List AI Conversations")
async def list_conversations(
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=100),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AIChatService = Depends(get_chat_service),
) -> dict:
    convs, page = await service.list_conversations(
        user_id=auth_user.user_id,
        page_start=pageStart,
        page_size=pageSize,
    )
    return {"code": 200, "message": "OK", "data": {"conversations": convs, "page": page}}


@router.post("/conversations", summary="Create AI Conversation", status_code=201)
async def create_conversation(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AIChatService = Depends(get_chat_service),
) -> dict:
    title = payload.get("title")
    model_id = payload.get("modelId")
    conv = await service.create_conversation(
        user_id=auth_user.user_id,
        title=title,
        model_id=model_id,
    )
    return {"code": 201, "message": "Created", "data": {"conversation": conv}}


@router.get("/conversations/{conversationId}", summary="Get AI Conversation")
async def get_conversation(
    conversation_id: Annotated[int, Path(ge=1, alias="conversationId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AIChatService = Depends(get_chat_service),
) -> dict:
    conv = await service.get_conversation(
        conversation_id=conversation_id,
        user_id=auth_user.user_id,
    )
    return {"code": 200, "message": "OK", "data": {"conversation": conv}}


@router.delete("/conversations/{conversationId}", summary="Delete AI Conversation", status_code=204)
async def delete_conversation(
    conversation_id: Annotated[int, Path(ge=1, alias="conversationId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AIChatService = Depends(get_chat_service),
) -> None:
    await service.delete_conversation(
        conversation_id=conversation_id,
        user_id=auth_user.user_id,
    )


@router.patch("/conversations/{conversationId}", summary="Update AI Conversation")
async def update_conversation(
    conversation_id: Annotated[int, Path(ge=1, alias="conversationId")],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AIChatService = Depends(get_chat_service),
) -> dict:
    title = payload.get("title")
    if title is None:
        raise BadRequestError("title is required")
    conv = await service.update_conversation_title(
        conversation_id=conversation_id,
        user_id=auth_user.user_id,
        title=title,
    )
    return {"code": 200, "message": "OK", "data": {"conversation": conv}}


@router.post("/chat", summary="Chat with AI")
async def chat_with_ai(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: AIChatService = Depends(get_chat_service),
) -> dict:
    message = payload.get("message")
    if not message or not isinstance(message, str):
        raise BadRequestError("message is required")

    conversation_id = payload.get("conversationId")
    model_id = payload.get("modelId")

    try:
        result = await service.chat(
            user_id=auth_user.user_id,
            message=message,
            conversation_id=conversation_id,
            model_id=model_id,
        )
        return {"code": 200, "message": "OK", "data": result}
    except QuotaExceededError as e:
        raise BadRequestError(str(e))
