import json
import secrets
from collections.abc import AsyncIterator
from typing import Any

from app.domain.llm.llm_client import LLMClient, GeneratedAdvice, StreamChunk
from app.core.errors import QuotaExceededError
from app.domain.llm.services import AiAdviceService, QuotaInfo
from app.domain.task.models import TaskAIAdvice
from app.domain.task.repositories import (
    AIConversationRepository,
    AIMessageRepository,
    TaskAIAdviceContextRepository,
    TaskAIAdviceRepository,
    TaskRepository,
)
from app.domain.task.types import TaskAIAdviceStatus


class LLMTimeoutError(Exception):
    """Raised when LLM request times out."""


class LLMModelError(Exception):
    """Raised when LLM returns an error."""


class TaskAIAdviceService:
    def __init__(
        self,
        *,
        advice_repo: TaskAIAdviceRepository,
        conversation_repo: AIConversationRepository,
        message_repo: AIMessageRepository,
        context_repo: TaskAIAdviceContextRepository,
        task_repo: TaskRepository,
        quota_service: AiAdviceService,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._advice_repo = advice_repo
        self._conversation_repo = conversation_repo
        self._message_repo = message_repo
        self._context_repo = context_repo
        self._task_repo = task_repo
        self._quota_service = quota_service
        self._llm_client = llm_client or LLMClient()

    async def request_advice(self, *, task_id: int, user_id: int) -> tuple[str, QuotaInfo]:
        advice = await self._advice_repo.get_latest(task_id)
        if advice is None or advice.status != TaskAIAdviceStatus.COMPLETED.value:
            has_quota = await self._quota_service.check_quota(user_id=user_id, amount=1.0)
            if not has_quota:
                raise QuotaExceededError("AI quota exhausted")
            advice = await self._generate_advice(task_id)
            await self._quota_service.consume_quota(user_id=user_id, amount=1.0)
        quota = await self._quota_service.get_quota(user_id=user_id)
        return advice.status, quota

    async def list_advices(self, *, task_id: int) -> list[dict[str, Any]]:
        advices = await self._advice_repo.list_by_task(task_id)
        return [self._serialize_advice(a) for a in advices]

    async def get_status(self, *, task_id: int) -> str:
        latest = await self._advice_repo.get_latest(task_id)
        if latest is None:
            return "NONE"
        return latest.status

    async def create_conversation(
        self,
        *,
        task_id: int,
        user_id: int,
        question: str,
        parent_id: int | None,
        context: dict[str, Any] | None,
        conversation_id: str | None = None,
    ) -> tuple[dict, QuotaInfo]:
        has_quota = await self._quota_service.check_quota(user_id=user_id, amount=1.0)
        if not has_quota:
            raise QuotaExceededError("AI quota exhausted")

        convo = None
        if conversation_id:
            convo = await self._conversation_repo.get_by_conversation_id(conversation_id)
            if convo is None or convo.context_id != task_id or convo.owner_id != user_id:
                raise ValueError("Conversation not found")
        else:
            conversation_id = secrets.token_hex(12)
            convo = await self._conversation_repo.create(
                conversation_id=conversation_id,
                task_id=task_id,
                owner_id=user_id,
                title=question[:60] or "AI 对话",
            )

        if context:
            section = context.get("section")
            index = context.get("sectionIndex") or context.get("index")
            if section is not None:
                await self._context_repo.get_or_create(
                    task_id=task_id,
                    section=section,
                    section_index=index,
                )

        user_message = await self._message_repo.create_message(
            conversation_id=convo.id,
            role="user",
            content=question,
            parent_id=parent_id,
        )

        history = await self._build_message_history(convo.id, task_id, context)
        llm_response = await self._llm_client.get_completion_with_history(messages=history)

        await self._message_repo.create_message(
            conversation_id=convo.id,
            role="assistant",
            content=llm_response.content,
            parent_id=user_message.id,
            tokens_used=llm_response.total_tokens,
        )

        quota = await self._quota_service.consume_tokens(
            user_id=user_id, tokens=llm_response.total_tokens
        )

        payload = await self.get_conversation(conversation_id=conversation_id)
        return payload, quota

    async def stream_conversation(
        self,
        *,
        task_id: int,
        user_id: int,
        question: str,
        conversation_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a conversation response via SSE."""
        has_quota = await self._quota_service.check_quota(user_id=user_id, amount=1.0)
        if not has_quota:
            raise QuotaExceededError("AI quota exhausted")

        convo = None
        if conversation_id:
            convo = await self._conversation_repo.get_by_conversation_id(conversation_id)
            if convo is None or convo.context_id != task_id or convo.owner_id != user_id:
                raise ValueError("Conversation not found")
        else:
            conversation_id = secrets.token_hex(12)
            convo = await self._conversation_repo.create(
                conversation_id=conversation_id,
                task_id=task_id,
                owner_id=user_id,
                title=question[:60] or "AI 对话",
            )

        user_message = await self._message_repo.create_message(
            conversation_id=convo.id,
            role="user",
            content=question,
            parent_id=None,
        )

        history = await self._build_message_history(convo.id, task_id, context)
        full_content = ""
        total_tokens = 0

        async for chunk in self._llm_client.stream_completion_with_history(messages=history):
            if chunk.content:
                full_content += chunk.content
            if chunk.total_tokens:
                total_tokens = chunk.total_tokens
            yield chunk

        await self._message_repo.create_message(
            conversation_id=convo.id,
            role="assistant",
            content=full_content,
            parent_id=user_message.id,
            tokens_used=total_tokens,
        )

        if total_tokens > 0:
            await self._quota_service.consume_tokens(user_id=user_id, tokens=total_tokens)

    async def _build_message_history(
        self,
        conversation_db_id: int,
        task_id: int,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Build message history for LLM context, including system prompt."""
        task = await self._task_repo.get_by_id(task_id)
        task_context = ""
        if task:
            task_context = (
                f"任务：{task.name}\n简介：{task.intro or ''}\n描述：{task.description or ''}"
            )

        context_info = ""
        if context:
            section = context.get("section", "")
            index = context.get("sectionIndex") or context.get("index")
            if section:
                context_info = f"\n当前关注的内容区块：{section}"
                if index is not None:
                    context_info += f"，索引：{index}"

        system_prompt = f"""你是一位专业的学习顾问和项目导师，正在帮助用户完成一个任务。

{task_context}{context_info}

请根据任务背景和用户的问题，提供专业、有建设性的回答。回答应该：
1. 切合任务主题
2. 具有可操作性
3. 简洁明了"""

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

        existing_messages = await self._message_repo.list_for_conversation(conversation_db_id)
        for msg in existing_messages:
            messages.append({"role": msg.role, "content": msg.content})

        return messages

    async def list_conversations_grouped(self, *, task_id: int) -> list[dict[str, Any]]:
        conversations = await self._conversation_repo.list_for_task(task_id)
        groups: list[dict[str, Any]] = []
        for convo in conversations:
            groups.append(
                {
                    "conversationId": convo.conversation_id,
                    "title": convo.title or "AI 对话",
                    "createdAt": convo.created_at.isoformat(),
                }
            )
        return groups

    async def get_conversation(self, *, conversation_id: str) -> dict:
        convo = await self._conversation_repo.get_by_conversation_id(conversation_id)
        if convo is None:
            raise ValueError("Conversation not found")
        messages = await self._message_repo.list_for_conversation(convo.id)
        return {
            "conversation": {
                "conversationId": convo.conversation_id,
                "title": convo.title,
                "createdAt": convo.created_at.isoformat(),
                "messages": [
                    {
                        "id": message.id,
                        "role": message.role,
                        "content": message.content,
                        "createdAt": message.created_at.isoformat(),
                        "tokensUsed": message.tokens_used,
                    }
                    for message in messages
                ],
            }
        }

    async def delete_conversation(self, *, conversation_id: str) -> None:
        convo = await self._conversation_repo.get_by_conversation_id(conversation_id)
        if convo is None:
            return
        await self._conversation_repo.soft_delete(convo)

    async def _generate_advice(self, task_id: int) -> TaskAIAdvice:
        task = await self._task_repo.get_by_id(task_id)
        if task is None:
            raise ValueError("Task not found")
        generated = await self._llm_client.generate_task_advice(task)
        advice = await self._advice_repo.create(
            task_id=task_id,
            model_hash=secrets.token_hex(16),
            status=TaskAIAdviceStatus.COMPLETED.value,
            topic_summary=json.dumps(generated.topic_summary, ensure_ascii=False),
            knowledge_fields=json.dumps(generated.knowledge_fields, ensure_ascii=False),
            learning_paths=json.dumps(generated.learning_paths, ensure_ascii=False),
            methodology=json.dumps(generated.methodology, ensure_ascii=False),
            team_tips=json.dumps(generated.team_tips, ensure_ascii=False),
            raw_response=json.dumps(
                {
                    "topic_summary": generated.topic_summary,
                    "knowledge_fields": generated.knowledge_fields,
                    "learning_paths": generated.learning_paths,
                    "methodology": generated.methodology,
                    "team_tips": generated.team_tips,
                },
                ensure_ascii=False,
            ),
        )
        await self._ensure_contexts(task_id, generated)
        return advice

    async def _ensure_contexts(self, task_id: int, payload: GeneratedAdvice) -> None:
        sections = {
            "knowledge_fields": payload.knowledge_fields,
            "learning_paths": payload.learning_paths,
            "methodology": payload.methodology,
            "team_tips": payload.team_tips,
        }
        for section, items in sections.items():
            for index, _ in enumerate(items or []):
                await self._context_repo.get_or_create(
                    task_id=task_id,
                    section=section,
                    section_index=index,
                )

    def _serialize_advice(self, advice: TaskAIAdvice) -> dict[str, Any]:
        def _loads(blob: str | None) -> Any:
            if not blob:
                return None
            try:
                return json.loads(blob)
            except json.JSONDecodeError:
                return None

        return {
            "id": advice.id,
            "taskId": advice.task_id,
            "status": advice.status,
            "topicSummary": _loads(advice.topic_summary),
            "knowledgeFields": _loads(advice.knowledge_fields),
            "learningPaths": _loads(advice.learning_paths),
            "methodology": _loads(advice.methodology),
            "teamTips": _loads(advice.team_tips),
            "updatedAt": advice.updated_at.isoformat() if advice.updated_at else None,
        }

    def _safe_load(self, blob: str | None) -> Any:
        if not blob:
            return None
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            return None
