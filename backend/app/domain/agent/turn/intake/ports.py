"""Public message/session capabilities used by turn admission.

These describe calls admission actually performs; they do not expose a root
implementation, arbitrary private helpers, or a service locator.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Protocol, TypedDict, Unpack

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.turn.state.live import LiveWork
from app.domain.delivery.addressing import Addressed
from app.domain.delivery.input_identity import InputReconciliationPending
from app.domain.identity.actor import Actor


class MessageService(Protocol):
    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]: ...

    live: LiveWork

    def has_running_turn(self, topic_id: uuid.UUID) -> bool: ...

    def replaying(self, topic_id: uuid.UUID) -> asyncio.Task | None: ...

    def session_took_over(self, topic_id: uuid.UUID, turn_id: uuid.UUID) -> bool: ...

    async def post_user_message(
        self,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        turn_id: uuid.UUID | None = None,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        client_id: str | None = None,
        quoted_context: dict | None = None,
    ) -> tuple[list[dict], uuid.UUID, list[uuid.UUID], bool]: ...

    async def post_system_event(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID | None = None,
        *,
        meta: dict | None = None,
    ) -> dict | None: ...

    async def work_policy(
        self,
        topic_id: uuid.UUID,
        agent_instance_id: uuid.UUID | None = None,
    ) -> dict | None: ...

    async def merge_into_running_turn(
        self,
        topic_id: uuid.UUID,
        block_ids: list[uuid.UUID],
        content: str,
        author: str,
        attachments: list[dict] | None = None,
        *,
        recipient_handle: str | None = None,
        owes_reply: bool = True,
    ) -> bool | InputReconciliationPending | None: ...

    def converse(
        self,
        *,
        topic_id: uuid.UUID,
        author: str,
        content: str,
        summon: bool,
        turn_id: uuid.UUID,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> AsyncIterator[dict]: ...

    def converse_prepared(
        self,
        *,
        topic_id: uuid.UUID,
        author: str,
        content: str,
        turn_id: uuid.UUID,
        user_block_id: uuid.UUID,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
        recipient_instance_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
    ) -> AsyncIterator[dict]: ...


class RecipientOptions(TypedDict, total=False):
    recipient_handle: str


class ReceivedMessage(TypedDict):
    addressed: Addressed
    continuation_id: uuid.UUID
    author: str
    content: str
    reply_to: str | None
    attachments: list[dict] | None
    provision_actor: Actor | None
    landed_user_block_id: uuid.UUID
    landed_user_block_ids: list[uuid.UUID]
    live_delivery_expected: bool
    recipient_handle: str | None


class MessageScheduler(Protocol):
    def __call__(
        self,
        chat_service: MessageService,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        /,
        **message: Unpack[ReceivedMessage],
    ) -> None: ...


class WorkExecutor(Protocol):
    async def __call__(
        self,
        chat_service: MessageService,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        content: str,
        addressed: Addressed,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
        frames: AsyncIterator[dict] | None = None,
        lifecycle: dict[str, bool] | None = None,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> None: ...


class InitialSeatResolver(Protocol):
    async def __call__(
        self,
        chat_service: MessageService,
        topic_id: uuid.UUID,
        /,
        *,
        user_block_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
    ) -> str: ...
