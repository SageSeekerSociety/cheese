from __future__ import annotations

from dataclasses import dataclass

import openai
import tiktoken

from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.llm.models import AIConversation, AIMessage
from app.domain.llm.repositories import (
    AIConversationRepository,
    AIMessageRepository,
    AIUserQuotaRepository,
)
from app.domain.llm.services import AiAdviceService, QuotaExceededError


@dataclass
class ModelInfo:
    id: str
    name: str
    provider: str
    seu_cost: float
    max_tokens: int
    is_available: bool


AVAILABLE_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        provider="openai",
        seu_cost=0.5,
        max_tokens=4096,
        is_available=True,
    ),
    ModelInfo(
        id="gpt-4o",
        name="GPT-4o",
        provider="openai",
        seu_cost=2.0,
        max_tokens=4096,
        is_available=True,
    ),
    ModelInfo(
        id="o1-mini",
        name="O1 Mini (Reasoning)",
        provider="openai",
        seu_cost=5.0,
        max_tokens=8192,
        is_available=True,
    ),
]


class AIChatService:
    def __init__(
        self,
        conversation_repo: AIConversationRepository,
        message_repo: AIMessageRepository,
        quota_repo: AIUserQuotaRepository,
    ) -> None:
        self._conversation_repo = conversation_repo
        self._message_repo = message_repo
        self._quota_service = AiAdviceService(repo=quota_repo)
        self._client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

    def list_models(self) -> list[dict]:
        return [
            {
                "id": m.id,
                "name": m.name,
                "provider": m.provider,
                "seuCost": m.seu_cost,
                "maxTokens": m.max_tokens,
                "isAvailable": m.is_available,
            }
            for m in AVAILABLE_MODELS
        ]

    async def create_conversation(
        self,
        *,
        user_id: int,
        title: str | None = None,
        model_id: str | None = None,
    ) -> dict:
        conv = await self._conversation_repo.create(
            user_id=user_id,
            title=title,
            model_type="standard",
            module_type="GENERAL",
        )
        return self._conversation_to_dict(conv)

    async def list_conversations(
        self,
        *,
        user_id: int,
        page_start: int | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict], dict]:
        convs, page = await self._conversation_repo.list_by_user(
            user_id, page_start=page_start, page_size=page_size
        )
        return [self._conversation_to_dict(c) for c in convs], page

    async def get_conversation(
        self,
        *,
        conversation_id: int,
        user_id: int,
    ) -> dict:
        conv = await self._conversation_repo.get_by_id(conversation_id)
        if conv is None:
            raise NotFoundError("Conversation not found")
        if conv.owner_id != user_id:
            raise ForbiddenError("Access denied")

        messages = await self._message_repo.list_by_conversation(conversation_id)
        result = self._conversation_to_dict(conv)
        result["messages"] = [self._message_to_dict(m) for m in messages]
        return result

    async def delete_conversation(
        self,
        *,
        conversation_id: int,
        user_id: int,
    ) -> bool:
        conv = await self._conversation_repo.get_by_id(conversation_id)
        if conv is None:
            raise NotFoundError("Conversation not found")
        if conv.owner_id != user_id:
            raise ForbiddenError("Access denied")
        return await self._conversation_repo.delete(conversation_id)

    async def update_conversation_title(
        self,
        *,
        conversation_id: int,
        user_id: int,
        title: str,
    ) -> dict:
        conv = await self._conversation_repo.get_by_id(conversation_id)
        if conv is None:
            raise NotFoundError("Conversation not found")
        if conv.owner_id != user_id:
            raise ForbiddenError("Access denied")
        updated = await self._conversation_repo.update_title(conversation_id, title)
        if updated is None:
            raise NotFoundError("Conversation not found")
        return self._conversation_to_dict(updated)

    async def chat(
        self,
        *,
        user_id: int,
        message: str,
        conversation_id: int | None = None,
        model_id: str | None = None,
    ) -> dict:
        has_quota = await self._quota_service.pre_check_and_reserve(
            user_id=user_id, estimated_tokens=1000
        )
        if not has_quota:
            raise QuotaExceededError("AI quota exhausted")

        if conversation_id is None:
            conv = await self._conversation_repo.create(
                user_id=user_id,
                title=message[:50] if len(message) > 50 else message,
                model_type="standard",
                module_type="GENERAL",
            )
            conversation_id = conv.id
        else:
            conv = await self._conversation_repo.get_by_id(conversation_id)
            if conv is None:
                raise NotFoundError("Conversation not found")
            if conv.owner_id != user_id:
                raise ForbiddenError("Access denied")

        await self._message_repo.create(
            conversation_id=conversation_id,
            role="user",
            content=message,
            tokens_used=self._count_tokens(message),
        )

        history = await self._message_repo.list_by_conversation(conversation_id)
        messages_for_api = [{"role": m.role, "content": m.content} for m in history]

        model = model_id or settings.openai_default_model

        response = await self._client.chat.completions.create(
            model=model,
            messages=messages_for_api,
            max_tokens=settings.openai_max_tokens,
            temperature=settings.openai_temperature,
        )

        assistant_content = response.choices[0].message.content or ""
        tokens_used = response.usage.total_tokens if response.usage else 0

        assistant_msg = await self._message_repo.create(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_content,
            tokens_used=tokens_used,
        )

        quota_info = await self._quota_service.consume_tokens(user_id=user_id, tokens=tokens_used)

        return {
            "conversationId": conversation_id,
            "message": self._message_to_dict(assistant_msg),
            "tokensUsed": tokens_used,
            "seuConsumed": tokens_used / 1000.0,
            "quotaRemaining": quota_info.remaining,
        }

    def _conversation_to_dict(self, conv: AIConversation) -> dict:
        return {
            "id": conv.id,
            "conversationId": conv.conversation_id,
            "userId": conv.owner_id,
            "title": conv.title,
            "modelType": conv.model_type,
            "moduleType": conv.module_type,
            "createdAt": int(conv.created_at.timestamp() * 1000),
            "updatedAt": int(conv.updated_at.timestamp() * 1000),
        }

    def _message_to_dict(self, msg: AIMessage) -> dict:
        return {
            "id": msg.id,
            "conversationId": msg.conversation_id,
            "role": msg.role,
            "content": msg.content,
            "tokensUsed": msg.tokens_used,
            "createdAt": int(msg.created_at.timestamp() * 1000),
        }

    def _count_tokens(self, text: str) -> int:
        try:
            enc = tiktoken.encoding_for_model("gpt-4o")
            return len(enc.encode(text))
        except Exception:
            return len(text) // 4
