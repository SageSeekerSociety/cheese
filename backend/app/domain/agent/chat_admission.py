"""ChatService 的准入那一半：一条消息怎么落到某一席上，然后开这一轮。

`converse` 是对外的入口，人和平台发的都走它；`converse_prepared` 是同一条路的
另一头，消息已经落库、项目也准入了。两者先问出这一轮按谁的座位串行
（`_turn_seat_handle`），再拿那一席的锁（`_prompt_lock`），然后交给
`TurnPreparation` 真跑。

Mixin，和 `recovery.py` 的 `SessionRecovery` 同一个理由：这些方法仍挂在
`ChatService` 上——调用点、子类重载（`tests/support/work_chat.py`、`RacingSeat`）
和测试对实例的 monkeypatch 用的都是同一个名字，所以方法不能挪出这个类。这里只
声明自己要用的属性与同族方法，实现留在本模块。
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent.platform_notices import (
    delivery_checking_notice,
    delivery_fallback_notice,
)
from app.domain.agent.queries import _agent_at
from app.domain.agent.seat_admission import seat_admission
from app.domain.agent.turn.state.inputs import _pending_input_blocks
from app.domain.agent_instance.models import AgentInstance
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.input_identity import InputReconciliationPending
from app.domain.identity.handles import names_a_person
from app.domain.room_task.place import PlaceResolver

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.errors import BaseError
    from app.domain.agent.turn.intake.human import HumanMessages
    from app.domain.agent.turn.intake.preparation import TurnPreparation
    from app.domain.agent.turn.state.live import LiveWork

logger = logging.getLogger(__name__)


class TurnAdmission:
    """把一条消息接进某一席，并开一轮——`ChatService` 的准入那一半。

    属性和同族方法是服务的，这里声明出来，让类型检查看得见本 mixin 自己的契约。
    """

    if TYPE_CHECKING:
        _sessions: async_sessionmaker
        live: LiveWork

        @property
        def human_messages(self) -> HumanMessages: ...

        @property
        def turn_preparation(self) -> TurnPreparation: ...

        def _refuse_turn(self, message: str) -> BaseError: ...

        async def has_running_turn(
            self, topic_id: uuid.UUID, agent_handle: str | None = None
        ) -> bool: ...

        async def merge_into_running_turn(
            self,
            topic_id: uuid.UUID,
            user_block_ids: list[uuid.UUID],
            content: str,
            author: str,
            attachments: list[dict] | None = None,
            recipient_handle: str | None = None,
            *,
            owes_reply: bool = True,
        ) -> bool | InputReconciliationPending | None: ...

        async def post_system_event(
            self,
            topic_id: uuid.UUID,
            content: str,
            turn_id: uuid.UUID | None = None,
            *,
            meta: dict | None = None,
        ) -> dict | None: ...

    async def _turn_seat_handle(
        self,
        topic_id: uuid.UUID,
        *,
        user_block_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
    ) -> str:
        """The conversation handle whose seat this turn serializes on.

        Resolved BEFORE the seat lock is taken, from the same facts
        `TurnPreparation.prepare` will resolve the running agent from: an explicitly
        addressed instance, the recipient the message was stamped with at
        posting, the oldest pending message's addressee (a platform turn picks
        that conversation up), and finally the room's default agent. The two
        reads moments apart agree unless the roster mutates mid-admission; a
        stale key then costs one extra parallel turn, never a lost one.
        """
        if recipient_handle is not None:
            return recipient_handle

        async with self._sessions() as session:
            instance_id = recipient_instance_id
            if instance_id is None:
                blocks = BlockRepository(session)
                addressed = None
                if user_block_id is not None:
                    addressed = await blocks.get(user_block_id)
                else:
                    place = await PlaceResolver(session).conversation(topic_id)
                    if place is not None:
                        pending = _pending_input_blocks(
                            await blocks.turn_history(place.conversation_id)
                        )
                        addressed = pending[0] if pending else None
                recipient = (
                    (addressed.meta or {}).get("agent_recipient")
                    if addressed is not None
                    else None
                )
                if recipient is not None and recipient.get("instance_id") is not None:
                    instance_id = uuid.UUID(recipient["instance_id"])
            if instance_id is not None:
                instance = await session.get(AgentInstance, instance_id)
                if instance is not None and instance.is_active:
                    return instance.handle
            place = await PlaceResolver(session).conversation(topic_id)
            if place is None:
                raise NotFoundError("Topic not found")
            agent = await _agent_at(session, place)
            return agent.handle

    @asynccontextmanager
    async def _prompt_lock(
        self,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        seat_handle: str,
    ) -> AsyncIterator[None]:

        async with seat_admission(self.live.seat_lock_for(topic_id, seat_handle)):
            # 这里不标「在跑」：轮次那行 `AgentTurn` 要到 `yield` 里面
            # （`open_turn` / `open_interval`）才提交，先标就是让缓存替一个不
            # 存在的轮次说话。记号落在提交之后那一步：`steps/send.py`
            # （`send_prepared`），自起的轮次在 `native_turns.begin`——见
            # `mark_turn_active`。
            try:
                yield
            finally:
                if (topic_id, work_id) not in self.live.hook_work:
                    await self.live.mark_turn_inactive(topic_id, work_id)

    async def converse(
        self,
        *,
        topic_id: uuid.UUID,
        author: str,
        content: str,
        summon: bool,
        turn_id: uuid.UUID | None = None,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> AsyncIterator[dict]:
        """Post the human message instantly, then (if summoned) run the agent
        turn serialized per topic (spec §9.1 串行队列). 现场必须实时: the human
        block persists + broadcasts BEFORE the lock, so a post never queues
        behind a running agent turn. turn_id groups this turn's blocks (R4);
        reply_to threads this message under another (B3); attachments are
        uploaded worktree images this message carries (图片输入).

        ``continuation_id`` is the logical unit of work this turn belongs to — a
        turn and every auto-resume of it share one, so a message the interrupted
        attempt already posted is not posted again (④).

        ``summon`` 没有默认值：这一轮跑不跑是**调用点算出来的一个答案**（由
        `runtime._a_turn_was_addressed` 从寻址结果读出），不是一个可以不写、写不写
        都默认「跑」的开关。给它默认值，就等于平台又有了一条不点名也能起轮次的路。
        """
        # 谁写了这一轮的正文：不是人写的，就是平台写的。判据是**作者**，不是
        # 「带没带 nudge」—— 平台那几条投递（机器接入、环境修好、记忆整理、闸门红
        # 了…）作者一律是 `system`，正文是平台写的一段提示词，把它当人话落进时间
        # 线就是让平台冒充人说话。房间里那一行由谁写是另一件事，见下面。
        platform_wrote_this = (
            is_resume or nudge_event is not None or not names_a_person(author)
        )
        # 平台指令那一档：`TurnPreparation.converse` 保证指令不被待读消息挤掉。
        # 重发不算：重发的 `content` 是原话再送一次，待读窗口本来就会把同
        # 一段话重新递上来，两边都拼就是同一句说两遍。
        platform_turn = platform_wrote_this and not is_resume
        if platform_wrote_this:
            turn_id = turn_id or uuid.uuid4()
            continuation_id = continuation_id or turn_id
            # System-initiated turn (重发 / 冲突调度…): no human
            # spoke — the opener is a SYSTEM event in the 现场, and the
            # instruction goes straight to the agent as the prompt.
            #
            # 平台提示统一契约: `nudge_event` is the one line the room sees,
            # `nudge_meta` its structured payload — which is where a caller puts
            # the长文 (CI 日志 / 检查输出 / 冲突文件清单) so the room stays
            # glanceable while nothing is lost. `content` is untouched: it is
            # still the whole instruction 芝士 gets as its prompt.
            if is_resume and not nudge_event:
                nudge_event = resume_reason or say("turnResent")
            # 开场白留空 = 调用点已经自己写好了那一行。Cloud 机器接入就是这一种：
            # 那一行同时是房间的生命周期记录，必须在这一轮排队之前就落库，否则算力
            # 闸一拒就永远不写（见 api/deps.py 的 `deliver_held`）。这一轮照样不说
            # 人话 —— 只是这次没有第二句要说。
            if nudge_event is not None:
                payload = await self.post_system_event(
                    topic_id, nudge_event, turn_id, meta=nudge_meta
                )
                if payload is None:
                    raise NotFoundError("Topic not found")
                yield {"type": "event_block", "block": payload}
            if not summon:
                yield {"type": "done"}
                return
            user_block_id = None
        else:
            (
                user_payloads,
                user_block_id,
                user_block_ids,
                _duplicate,
            ) = await self.human_messages.post_user_message(
                topic_id,
                author=author,
                content=content,
                turn_id=turn_id,
                reply_to=reply_to,
                attachments=attachments,
            )
            turn_id = turn_id or user_block_id
            continuation_id = continuation_id or turn_id
            for payload in user_payloads:
                yield {"type": "user_block", "block": payload}

            # Default human-to-human: post and stay quiet (spec C3 / §7.1).
            if not summon:
                yield {"type": "done"}
                return

            # The read marker belongs to the structured native echo's database
            # transaction. Neither accepting the human message here nor an RPC
            # acknowledgement proves the native session read the input.

        # 收件人：点名的实例（恢复/重发那一类），或落库时记在消息上的那位。
        # 注入、降级判断和座位锁共用一个答案，所以只解析一次。
        recipient_handle = None
        if recipient_instance_id is not None:
            async with self._sessions() as session:
                instance = await session.get(AgentInstance, recipient_instance_id)
                if instance is None or not instance.is_active:
                    raise self._refuse_turn(say("addressedAgentUnavailable"))
                recipient_handle = instance.handle
        seat_handle = await self._turn_seat_handle(
            topic_id,
            user_block_id=user_block_id,
            recipient_handle=recipient_handle,
        )

        # A turn is already running for THIS seat: hand the message to it rather
        # than queue behind it. 判据是收件人自己那一席在不在跑，不是房间里有没有
        # 人在跑 —— 另一位队友在跑、收件人空闲时，排队本来就是正常路径，不记降级。
        # 这一支进去就说明本来指望实时递交，所以除了真递进去，剩下的都是降级。
        if user_block_id is not None and await self.has_running_turn(
            topic_id, seat_handle
        ):
            delivered = await self.merge_into_running_turn(
                topic_id,
                user_block_ids,
                content,
                author,
                attachments,
                recipient_handle=seat_handle,
            )
            if isinstance(delivered, InputReconciliationPending):
                checking, checking_meta = delivery_checking_notice()
                await self.post_system_event(
                    topic_id, checking, turn_id, meta=checking_meta
                )
                return
            if delivered is True:
                # The answer streams out of the turn already in flight, which
                # every client in this topic is subscribed to — this request has
                # nothing left to yield.
                return
            logger.warning(
                "live delivery fell back to the queue (topic=%s, turn=%s, "
                "delivered=%s)",
                topic_id,
                turn_id,
                delivered,
            )
            fallback_text, fallback_meta = delivery_fallback_notice()
            await self.post_system_event(
                topic_id, fallback_text, turn_id, meta=fallback_meta
            )

        async with self._prompt_lock(topic_id, turn_id, seat_handle):
            async for frame in self.turn_preparation.converse(
                topic_id=topic_id,
                content=content,
                turn_id=turn_id,
                author=author,
                user_block_id=user_block_id,
                is_resume=is_resume,
                continuation_id=continuation_id,
                platform_turn=platform_turn,
                delivery_id=delivery_id,
                recipient_instance_id=recipient_instance_id,
            ):
                yield frame

    async def converse_prepared(
        self,
        *,
        topic_id: uuid.UUID,
        author: str,
        content: str,
        turn_id: uuid.UUID,
        user_block_id: uuid.UUID,
        continuation_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> AsyncIterator[dict]:
        """Prepare an already durable message after project admission."""
        seat_handle = await self._turn_seat_handle(
            topic_id,
            user_block_id=user_block_id,
            recipient_handle=recipient_handle,
        )
        async with self._prompt_lock(topic_id, turn_id, seat_handle):
            async for frame in self.turn_preparation.converse(
                topic_id=topic_id,
                content=content,
                turn_id=turn_id,
                author=author,
                user_block_id=user_block_id,
                continuation_id=continuation_id,
                recipient_instance_id=recipient_instance_id,
            ):
                yield frame
