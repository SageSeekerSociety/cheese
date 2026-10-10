"""Durable human intake, live delivery and admission orchestration.

The root runner schedules and tracks tasks and owns process handover/recovery.
This collaborator consumes those arrivals; no second scheduler is introduced.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import Unpack

from app.core.redis import get_redis_client
from app.core.sentences import error_frame, say
from app.domain.agent.admission import (
    HOST_BUSY_META,
    QUEUED_META,
    Slot,
    enter,
    queued_text,
    wait_for_host,
)
from app.domain.agent.initial_admission import admitted_initial
from app.domain.agent.platform_notices import (
    EVENT_TURN_QUEUED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    delivery_checking_notice,
    delivery_fallback_notice,
    notice,
)
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.turn.intake.ports import (
    InitialSeatResolver,
    MessageScheduler,
    MessageService,
    ReceivedMessage,
    RecipientOptions,
    WorkExecutor,
)
from app.domain.agent.turn.state.execution import ExecutionState
from app.domain.agent.turn_ledger import project_pool
from app.domain.delivery.addressing import Addressed
from app.domain.delivery.input_identity import InputReconciliationPending
from app.domain.identity.actor import Actor
from app.domain.identity.handles import names_a_person, recipient_seat
from app.domain.thread.services import answer_place
from app.domain.usage.credits import CREDITS_EXHAUSTED_META, credits_event

logger = logging.getLogger("cheesex.runtime")


class RunnerIntake:
    def __init__(
        self,
        broker: InProcessBroker,
        state: ExecutionState,
        *,
        wait_to_start: Callable[[], Awaitable[None]],
        schedule_message: MessageScheduler,
        execute: WorkExecutor,
        nudge: Callable[[MessageService, uuid.UUID], None],
        host_has_room: Callable[[uuid.UUID], Awaitable[bool]] | None,
        address_agent: Callable[[str | None], Addressed],
        is_addressed: Callable[[Addressed], bool],
        resolve_initial_seat: InitialSeatResolver,
    ) -> None:
        self._broker = broker
        self.state = state
        self._wait_to_start = wait_to_start
        self._schedule_message = schedule_message
        self._execute = execute
        self._nudge = nudge
        self._host_has_room = host_has_room
        self._address_agent = address_agent
        self._is_addressed = is_addressed
        self._resolve_initial_seat = resolve_initial_seat
        self._message_locks: dict[tuple[uuid.UUID, str | None], asyncio.Lock] = {}

    REPLAY_NOTICE_S = 20.0

    _CATCHING_UP_META = notice(
        EVENT_TURN_QUEUED,
        severity=SEVERITY_INFO,
        who=WHO_PLATFORM,
        detail=say("catchingUpDetail"),
        detail_label=say("labelWhatHappensNext"),
    )

    async def receive_message(
        self,
        chat_service: MessageService,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        provision_actor: Actor | None = None,
        client_id: str | None = None,
        quoted_context: dict | None = None,
    ) -> uuid.UUID:
        """Persist one human message now, then deliver it to whoever it named.

        Only model turns use queue/credit gates; quotes carry no summon authority.
        **谁被点名是这里算的，不是发送方算好递进来的**（不变量 I13）。以前还有一个
        `summon: bool` 入参，从浏览器的帧上一路传到这里，和服务端解析出来的 @ 做或
        运算 —— 也就是说一条谁也没 @ 的消息，只要客户端把那个布尔置真，照样起一轮。
        现在只认落库那一刻解析出来的 `agent_recipient`：@ 了谁，就是点了谁的名。
        """
        received_at = time.monotonic()
        channel = str(topic_id)
        (
            payloads,
            user_block_id,
            user_block_ids,
            duplicate,
        ) = await chat_service.human_messages.post_user_message(
            topic_id,
            author=author,
            content=content,
            turn_id=None,
            reply_to=reply_to,
            attachments=attachments,
            client_id=client_id,
            quoted_context=quoted_context,
        )
        turn_id = user_block_id
        recipient = next(
            (
                (payload.get("meta") or {}).get("agent_recipient")
                for payload in payloads
                if (payload.get("meta") or {}).get("agent_recipient")
            ),
            None,
        )
        recipient_handle = (recipient or {}).get("handle")
        # Was this message meant to reach a session that was already running?
        # Asked of the RECIPIENT's own seat, never of the room: teammates in one
        # room run side by side, so a message @-ing an idle one queues beside
        # another one's live turn — that is the ordinary path, not a degraded
        # delivery, and claiming otherwise put a false 「未能送达」 line in the
        # room. The handle is the same key `merge_into_running_turn` filters on,
        # so "a live handoff was expected" and "the handoff failed" answer with
        # one key rather than two that can disagree.
        live_delivery_expected = recipient_handle is not None and (
            chat_service.has_running_turn(topic_id, recipient_handle)
        )
        mentioned = any(
            (payload.get("meta") or {}).get("agent_recipient", {}).get("mentioned")
            for payload in payloads
        )
        # 点到的是**席位**，不是这个队友自己的名字。`agent_recipient.handle` 是项目
        # 给它起的名（`reviewer`、`planner`…，`AgentInstance.handle` 允许任意小写
        # 串），而一条投递怎么到达是按席位的命名规矩判出来的
        # （`how_it_arrives` → `looks_like_agent_handle`）。拿实例名去问，一个没叫
        # `cheese` 开头的队友就永远不是「靠一轮收到」—— @ 它、和它私聊，都跑不起一
        # 轮。席位由实例 id 定，和名册上坐的那个字符串是同一个。
        #
        # `recipient_handle` 不跟着改：`converse_prepared` / `merge_into_running_turn`
        # 问的是「哪个实例在跑」，那边认的就是实例名。
        addressed = self._address_agent(
            recipient_seat(recipient) if mentioned else None
        )
        persisted_at = time.monotonic()
        for payload in payloads:
            await self._broker.publish(
                channel, {"type": "user_block", "block": payload}
            )
        logger.info(
            "chat_receive_timing topic=%s turn=%s persist_ms=%.3f publish_ms=%.3f",
            topic_id,
            turn_id,
            (persisted_at - received_at) * 1000,
            (time.monotonic() - persisted_at) * 1000,
        )
        if duplicate:
            return turn_id
        # A message to 芝士 in a channel's main line is answered in its 支线.
        answer_in = await answer_place(
            chat_service.session_factory, user_block_id if mentioned else None, topic_id
        )
        if answer_in != topic_id:
            live_delivery_expected = chat_service.has_running_turn(answer_in)
        self._schedule_message(
            chat_service,
            answer_in,
            turn_id,
            addressed=addressed,
            continuation_id=turn_id,
            author=author,
            content=next(
                (
                    payload["content"]
                    for payload in payloads
                    if payload.get("id") == str(user_block_id) and content
                ),
                content,
            ),
            reply_to=reply_to,
            attachments=attachments,
            provision_actor=provision_actor,
            landed_user_block_id=user_block_id,
            landed_user_block_ids=user_block_ids,
            live_delivery_expected=live_delivery_expected,
            recipient_handle=recipient_handle,
        )
        return turn_id

    async def _consume_message(
        self,
        chat_service: MessageService,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        **message: Unpack[ReceivedMessage],
    ) -> None:
        # Order each recipient's messages. Waiting for another agent's turn must
        # not block a follow-up addressed to the agent that is still running.
        # Before anything else: whether this joins a running turn or starts one
        # depends on knowing what is running, which only the owner does.
        await self._wait_to_start()
        key = (topic_id, message["recipient_handle"])
        lock = self._message_locks.setdefault(key, asyncio.Lock())
        # The window this and the two lines below measure runs from a message
        # being durably received to its turn starting to assemble. Every other
        # stretch of a turn is timed; this one never was, and it is not small —
        # it holds a queue behind the recipient's other messages, a live-delivery
        # attempt that reaches the database, and the credit and concurrency gates.
        # Measured through it, a turn only shows a gap with nothing in it.
        admission_started = time.monotonic()
        try:
            async with lock:
                logger.info(
                    "chat_admission_timing topic=%s turn=%s phase=message_lock "
                    "elapsed_ms=%.3f unix_ms=%.3f",
                    topic_id,
                    turn_id,
                    (time.monotonic() - admission_started) * 1000,
                    time.time() * 1000,
                )
                if not self._is_addressed(message["addressed"]):
                    if message["content"] or message["attachments"]:
                        # Heard, not asked: nothing here is the agent's to answer.
                        await chat_service.merge_into_running_turn(
                            topic_id,
                            message["landed_user_block_ids"] or [turn_id],
                            message["content"],
                            message["author"],
                            message["attachments"],
                            **(
                                {"recipient_handle": message["recipient_handle"]}
                                if message["recipient_handle"] is not None
                                else {}
                            ),
                            owes_reply=False,
                        )
                    await self._broker.publish(str(topic_id), {"type": "done"})
                    return
                if await self._deliver_message(
                    chat_service,
                    topic_id,
                    turn_id,
                    recipient_handle=message["recipient_handle"],
                    live_delivery_expected=message["live_delivery_expected"],
                    landed_user_block_id=message["landed_user_block_id"],
                    landed_user_block_ids=message["landed_user_block_ids"],
                    content=message["content"],
                    author=message["author"],
                    attachments=message["attachments"],
                ):
                    return
            logger.info(
                "chat_admission_timing topic=%s turn=%s phase=live_delivery_declined "
                "elapsed_ms=%.3f unix_ms=%.3f",
                topic_id,
                turn_id,
                (time.monotonic() - admission_started) * 1000,
                time.time() * 1000,
            )
            await self._run(
                chat_service,
                topic_id,
                turn_id,
                addressed=message["addressed"],
                continuation_id=message["continuation_id"],
                author=message["author"],
                content=message["content"],
                reply_to=message["reply_to"],
                attachments=message["attachments"],
                provision_actor=message["provision_actor"],
                landed_user_block_id=message["landed_user_block_id"],
                recipient_handle=message["recipient_handle"],
                recipient_instance_id=message.get("recipient_instance_id"),
            )
        except Exception:
            logger.exception(
                "agent message subscription failed topic=%s turn=%s", topic_id, turn_id
            )
            await self._broker.publish(
                str(topic_id),
                error_frame(say("agentDeliveryFailed"), type="error"),
            )

    async def _wait_for_replay(
        self, chat_service: MessageService, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> None:
        """Wait for the room's replay of what its sessions said while nobody
        listened (`ChatService.recover_sessions`).

        The turn it answered is closed, and the messages it took stamped
        consumed, only as that replay lands; a turn started before then sends
        them again. Nothing outside the room waits for it.
        """
        told = False
        while (replay := chat_service.replaying(topic_id)) is not None:
            done, _ = await asyncio.wait(
                {replay}, timeout=None if told else self.REPLAY_NOTICE_S
            )
            if not done:
                told = True
                await self._post_event(
                    chat_service,
                    topic_id,
                    turn_id,
                    say("catchingUp"),
                    meta=self._CATCHING_UP_META,
                )

    async def _post_event(
        self,
        chat_service: MessageService,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        text: str,
        *,
        meta: dict | None = None,
    ) -> bool:
        """Persist + broadcast a platform system event (queue/refusal). Reuses
        the post_system_event + broker path the nudge mechanism uses."""
        try:
            block = await chat_service.post_system_event(
                topic_id, text, turn_id, meta=meta
            )
        except Exception:  # noqa: BLE001 — visibility is best-effort
            logger.exception("failed to post admission event for %s", topic_id)
            return False
        if block is not None:
            await self._broker.publish(
                str(topic_id), {"type": "event_block", "block": block}
            )
        return block is not None

    async def _admit(
        self,
        chat_service: MessageService,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        agent=None,
    ) -> tuple[str, Slot | None]:
        """Admission control (spec §9.1 算力额度真实化), before any execution:

        - credits exhausted → ("reject", None): the caller refuses the turn.
        - its session starts on a session host with no memory for it → wait.
        - project concurrency full → wait for one of the project's slots, first
          come first served, after posting a visible "排队中" system event.
          Returns ("ok", slot) with the slot held (caller must release).
        - topic unknown / policy lookup failed → ("ok", None): admit ungated;
          the turn itself surfaces the real error.
        """
        try:
            policy = await chat_service.work_policy(topic_id, agent)
        except Exception:  # noqa: BLE001 — admission must never kill a turn
            logger.exception("work_policy failed for %s; admitting", topic_id)
            policy = None
        if policy is None:
            return "ok", None
        if policy["credits_exhausted"]:
            logger.info("turn %s rejected: credits exhausted", turn_id)
            return "reject", None
        await wait_for_host(
            self._host_has_room if policy.get("on_session_host") else None,
            topic_id,
            lambda text: self._post_event(
                chat_service, topic_id, turn_id, text, meta=HOST_BUSY_META
            ),
        )

        async def tell_queued(ahead: int) -> None:
            await self._post_event(
                chat_service, topic_id, turn_id, queued_text(ahead), meta=QUEUED_META
            )
            logger.info(
                "turn %s queued (project=%s ahead=%s)",
                turn_id,
                policy["project_id"],
                ahead,
            )

        slot = await enter(
            get_redis_client(),
            str(turn_id),
            pool=project_pool(policy["project_id"], policy["max_concurrent_turns"]),
            on_queued=tell_queued,
        )
        return "ok", slot

    async def _refuse_exhausted(
        self,
        chat_service: MessageService,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        content: str,
        reply_to: str | None,
        attachments: list[dict] | None,
        is_message_turn: bool,
        message_landed: bool = False,
    ) -> None:
        """Refuse a turn the credits do not admit. A human's message still lands
        (only the AI turn is metered); the platform's own event says why."""
        policy = await chat_service.work_policy(topic_id)
        reason = (policy or {}).get("credits_exhausted")
        event = credits_event(reason)

        channel = str(topic_id)
        if is_message_turn and not message_landed and (content or attachments):
            try:
                async for frame in chat_service.converse(
                    topic_id=topic_id,
                    author=author,
                    content=content,
                    summon=False,
                    turn_id=turn_id,
                    reply_to=reply_to,
                    attachments=attachments,
                ):
                    if frame.get("type") != "done":
                        await self._broker.publish(channel, frame)
            except Exception:  # noqa: BLE001 — still surface the refusal
                logger.exception("failed to land message for refused turn")
        posted = await self._post_event(
            chat_service,
            topic_id,
            turn_id,
            event,
            meta=CREDITS_EXHAUSTED_META,
        )
        await self._broker.publish(
            channel,
            error_frame(event, type="error", persisted=posted),
        )
        self.state.recent.append(
            {
                "turn_id": str(turn_id),
                "topic_id": str(topic_id),
                "status": "rejected",
                "detail": "额度已用完，未执行",
                "started_at": time.time(),
            }
        )

    async def _deliver_message(
        self,
        chat_service: MessageService,
        topic_id,
        turn_id,
        *,
        recipient_handle: str | None = None,
        live_delivery_expected: bool = False,
        landed_user_block_id: uuid.UUID | None = None,
        landed_user_block_ids: list[uuid.UUID] | None = None,
        content="",
        author="",
        attachments: list[dict] | None = None,
    ) -> bool:
        if landed_user_block_id is not None and (content or attachments):
            recipient: RecipientOptions = (
                {"recipient_handle": recipient_handle}
                if recipient_handle is not None
                else {}
            )
            delivered = await chat_service.merge_into_running_turn(
                topic_id,
                landed_user_block_ids or [landed_user_block_id],
                content,
                author,
                attachments,
                **recipient,
            )
            if isinstance(delivered, InputReconciliationPending):
                checking, checking_meta = delivery_checking_notice()
                await self._post_event(
                    chat_service, topic_id, turn_id, checking, meta=checking_meta
                )
                return True
            if delivered is True:
                # 这里曾经打 👀。现在不打了：这一句证明的是「传输层收下了这次写
                # 入」，而 harness 说「会话把它读进去了」是另一件事，由
                # `confirm_prompt_receipt` 统一落记号——两处都打会变成同一条消息
                # 上先后出现两个来源不同、含义不同的同一个符号。
                return True
            if delivered is False or live_delivery_expected:
                logger.warning(
                    "live delivery fell back to the queue (topic=%s, "
                    "block=%s, delivered=%s, live_expected=%s)",
                    topic_id,
                    landed_user_block_id,
                    delivered,
                    live_delivery_expected,
                )
                fallback_text, fallback_meta = delivery_fallback_notice()
                await self._post_event(
                    chat_service,
                    topic_id,
                    turn_id,
                    fallback_text,
                    meta=fallback_meta,
                )

        return False

    async def _run(
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
        # Human message already persisted by ``receive_message``. Its AI work is
        # still pending admission and may instead merge into a live turn.
        landed_user_block_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> None:
        channel = str(topic_id)
        # 一轮只有一种开法：converse。以前还有第二种 —— kickoff 把一条预制的帧流从
        # 外面递进来，平台用它起「没有人写过提示词」的那种轮次。那条路整条退场
        # （I12），所以这里没有外来的帧流可接，只剩下面自己准备的那一份。
        frames: AsyncIterator[dict] | None = None
        # 算力闸 (spec §9.1): refuse on exhausted credits, queue when the
        # project's concurrent-turn ceiling is reached. Both states are posted
        # into the topic as platform system events, so people SEE why nothing
        # is streaming yet.
        await self._wait_to_start()
        await self._wait_for_replay(chat_service, topic_id, turn_id)
        admit_started = time.monotonic()
        verdict, gate = await self._admit(
            chat_service, topic_id, turn_id, recipient_instance_id
        )
        logger.info(
            "chat_admission_timing topic=%s turn=%s phase=admitted verdict=%s "
            "elapsed_ms=%.3f unix_ms=%.3f",
            topic_id,
            turn_id,
            verdict,
            (time.monotonic() - admit_started) * 1000,
            time.time() * 1000,
        )
        if verdict == "reject":
            await self._refuse_exhausted(
                chat_service,
                topic_id,
                turn_id,
                author=author,
                content=content,
                reply_to=reply_to,
                attachments=attachments,
                # A human message turn lands its message even when refused;
                # resume/nudge turns have nothing to land.
                #
                # 判据是**有没有人说过话**，不是「是不是 resume」：平台自己那几条投
                # 递（机器接入、环境修好、记忆整理）作者是 `system`、正文是平台写的
                # 一段提示，既不 resume 也没有 nudge —— 当成「一条人发的消息」补落，
                # 就是把平台的提示词当人话写进时间线。
                is_message_turn=(
                    not is_resume and nudge_event is None and names_a_person(author)
                ),
                message_landed=landed_user_block_id is not None,
            )
            return
        lifecycle = {"started": False, "session_owned": False}
        try:

            async def post_admission_event(
                conversation_id: uuid.UUID,
                text: str,
                *,
                meta: dict | None = None,
            ) -> dict | None:
                return await chat_service.post_system_event(
                    conversation_id, text, meta=meta
                )

            async with admitted_initial(
                chat_service.session_factory,
                lambda conversation_id, seat: chat_service.live.seat_lock_for(
                    conversation_id, seat
                ),
                partial(self._resolve_initial_seat, chat_service),
                post_admission_event,
                topic_id,
                delivery_id,
                user_block_id=landed_user_block_id,
                recipient_instance_id=recipient_instance_id,
                recipient_handle=recipient_handle,
            ) as offered:
                if offered:
                    return
                if landed_user_block_id is not None:
                    frames = chat_service.converse_prepared(
                        topic_id=topic_id,
                        author=author,
                        content=content,
                        turn_id=turn_id,
                        user_block_id=landed_user_block_id,
                        continuation_id=continuation_id,
                        provision_actor=provision_actor,
                        recipient_instance_id=recipient_instance_id,
                        **(
                            {"recipient_handle": recipient_handle}
                            if recipient_handle is not None
                            else {}
                        ),
                    )
                await self._execute(
                    chat_service,
                    topic_id,
                    turn_id,
                    author=author,
                    content=content,
                    addressed=addressed,
                    reply_to=reply_to,
                    attachments=attachments,
                    is_resume=is_resume,
                    resume_reason=resume_reason,
                    nudge_event=nudge_event,
                    nudge_meta=nudge_meta,
                    continuation_id=continuation_id,
                    provision_actor=provision_actor,
                    frames=frames,
                    lifecycle=lifecycle,
                    delivery_id=delivery_id,
                    recipient_instance_id=recipient_instance_id,
                )
        finally:
            # Close what this turn opened, unless the session took over THIS
            # turn — asking by id rather than trusting the handover.
            #
            # The mark gets opened on the first frame, and the frames that
            # arrive first are not this turn producing anything: they are the
            # room acknowledging the request (`ack_summon`'s 👀, a system event
            # block). `session_lifecycle` comes after them, so by the time the
            # runner learns a session will own the ending it has already opened
            # a mark — and used to decline to close it on the strength of that
            # frame alone.
            #
            # That holds only while the session opens its activity under the
            # SAME work id, which collapses the two into one entry. When a turn
            # begins on a topic that already has one running, the session reuses
            # the standing activity instead of opening a second, so the newer
            # turn is never started on the session's books and never ended
            # either. Nothing else publishes `turn_finished`, so the mark
            # outlives the turn by the length of the process: the room keeps
            # 正在思考, and `/health`'s drain count never reaches zero (#604).
            handed_over = lifecycle["session_owned"]
            session_ended_it = handed_over and chat_service.session_took_over(
                topic_id, turn_id
            )
            if lifecycle["started"] and not session_ended_it:
                await self._broker.publish(
                    channel, {"type": "turn_finished", "turn_id": str(turn_id)}
                )
            # Drop the liveness mark here, not in `_execute`: a turn killed by
            # task cancellation (CancelledError is a BaseException — it misses
            # every `except` inside `_execute`, including the registry cleanup)
            # must stop counting as live, so the next sweep can claim it. The
            # open interval deliberately survives — that is what gets it resumed.
            self.state.live.pop(str(turn_id), None)
            self.state.delivered.discard(str(turn_id))
            self.state.last_frame_at.pop(str(turn_id), None)
            self.state.live_topics.pop(str(turn_id), None)
            if gate is not None:
                await gate.release()
            self._nudge(chat_service, topic_id)
