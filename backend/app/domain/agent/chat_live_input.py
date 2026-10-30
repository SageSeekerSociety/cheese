"""ChatService 的两头「话还没被读到」的那一半。

一头是往**正在干活的那个会话**里送东西：人中途插一句话
（`merge_into_running_session`），平台告诉它世界变了（`notify_running_session`），
或者账本上的一份投递（`join_running_session`）。会话在不在干活，问的是它自己的
runner（`ComputePool.working_seat` 找座位，steer 之前再问一次 `status`），不是哪
一轮开着；收据认下才算送到。

另一头是问「还有什么没被读到」：`oldest_unread_at`（最久的那一条等了多久）、
`has_unread_input`（房间里还有没有待读）、`pending_seat`（那批是点名给谁的）。
三个问的是同一个窗口——`_pending_input_blocks`——所以放在一起，谁改了窗口语义
另外两个跟着改。

Mixin：方法仍挂在 `ChatService` 上，实例的 monkeypatch（`chat.notify_running_session`、
`chat.merge_into_running_session`）和 `_compute.report_to` 收的回调都不动。
"""

import logging
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.domain.agent.harness.prompt import (
    live_input_lines,
    platform_prompt,
    publication_prompt,
    strip_platform_notice,
)
from app.domain.agent.input_registration import input_registrar
from app.domain.agent.prompt import live_inputs, model_images
from app.domain.agent.turn.state.inputs import _pending_input_blocks
from app.domain.delivery.agent import DeliveryTargetChanged
from app.domain.delivery.input_identity import (
    InputEffects,
    InputOutcomeUnconfirmed,
    InputReconciliationPending,
    InputRegistrar,
)
from app.domain.delivery.mention import BY_PERSON
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import recipient_seat
from app.domain.notice_meta import EVENT_ROUTINE_RUN
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.place import PlaceResolver
from app.domain.thread.services import conversation_inputs
from app.domain.topic.models import TopicStatus
from app.domain.user.services import language_of

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.domain.agent.compute import ComputePool
    from app.domain.agent.turn.state.live import LiveWork

logger = logging.getLogger(__name__)


class LiveInput:
    """往正在跑的轮次里送话、以及问还有什么没读到——`ChatService` 的另一半。

    属性和方法是服务的，这里声明出来，让类型检查看得见本 mixin 自己的契约。
    """

    if TYPE_CHECKING:
        _sessions: async_sessionmaker
        _compute: ComputePool
        live: LiveWork

    async def merge_into_running_session(
        self,
        topic_id: uuid.UUID,
        user_block_ids: list[uuid.UUID],
        content: str,
        author: str,
        attachments: list[dict] | None = None,
        recipient_handle: str | None = None,
        *,
        owes_reply: bool = True,
    ) -> bool | InputReconciliationPending | None:
        """Inject a just-posted human message into the session already working
        in this topic: the recipient's seat, or the room's only working one.

        ``owes_reply`` is whether the message was addressed to the agent: then
        the session answers it in the room before it uses any other tool. One
        said to somebody else in the room is only for the agent to know about.

        ``True`` means transport acceptance was recorded. A reconciliation
        result holds the registered identity without authorizing a queued retry.
        ``False`` means a pre-send failure; ``None`` means no session of that
        seat is working here, or the conversation is a closed task: its work is
        over and its session is being stopped (`close_task`), so what is said
        there is answered as anything said in a closed task is.

        The text is labelled the same way `prompt_line` labels a pending block,
        so a message that arrives mid-work reads identically to one that came in
        the prompt — 芝士 must not have to tell the two apart to know who spoke.

        Images use the same @path input path as an initial prompt. Interactive
        providers resolve that path into a native image block before the model
        sees the message; a remote device first stages the exact bytes and acks
        the file write."""
        working = self._compute.working_seat(topic_id, recipient_handle)
        if working is None:
            return None
        # The seat aims the steer; the handle it acts under is who reads the
        # message (``seen_by``) and whose own earlier message a reply quotes.
        seat_agent, acting = working
        async with self._sessions() as session:
            stored, replied = await live_inputs(session, user_block_ids)
            # 中途插进来的话也带着平台自己那几句，同样按正在说话的那个人的
            # 语言走（`user.language`）。
            language = await language_of(session, author)
        files = [
            (str(a["path"]), str(a.get("mime") or ""))
            for a in attachments or []
            if a.get("path")
        ]
        registrar = self._input_registrar(
            InputEffects(
                held_block_ids=tuple(user_block_ids),
                block_ids=tuple(user_block_ids),
                seen_block_ids=tuple(user_block_ids),
                seen_by=acting,
            ),
            probe_unread=True,
        )
        try:
            # Read the room's status, then deliver with no transaction open: a
            # row lock held across the device call queues every writer of the
            # room behind it with a pool connection each (dev outage of
            # 2026-09-18), and it never held archival off anyway — the archive
            # path takes the same non-conflicting KEY SHARE lock.
            async with self._sessions() as session:
                place = await PlaceResolver(session).conversation(topic_id)
                task = place.task if place is not None else None
                if task is not None and task.status == TaskStatus.closed:
                    return None
                archived = place is None or place.room.status == TopicStatus.archived
                # The pictures are read here, and the lines written after: a line
                # says whether its file arrives as a picture, and only reading
                # the bytes can tell (`model_images`).
                images, undecodable = (
                    await model_images(
                        session, place.room.project_id, place.room.id, files
                    )
                    if place is not None
                    else ({}, set())
                )
            pictures = [images[path] for path, _ in files if path in images]
            line = publication_prompt(
                "\n".join(
                    live_input_lines(
                        author,
                        content,
                        attachments,
                        stored=stored,
                        replied=replied,
                        recipient=acting,
                        undecodable=undecodable,
                    )
                ),
                language,
            )
            if archived:
                delivered = False
            else:
                # 话说给在干活的那个座位：同房间另一个 agent 也在干活时，不带
                # 座位就是把话猜进别人的会话里。
                delivered = await self._compute.steer(
                    topic_id,
                    line,
                    images=pictures or None,
                    register_input=registrar,
                    agent_handle=seat_agent,
                    owes_reply=owes_reply,
                )
        except InputOutcomeUnconfirmed as exc:
            # The registered input may already be in the native session. Leave
            # it with that session for reconciliation, never enqueue a new input.
            logger.exception(
                "live input requires reconciliation (topic=%s, input=%s)",
                topic_id,
                exc.identity.input_id,
            )
            return InputReconciliationPending(exc.identity, exc.accepted)
        except Exception:  # pre-send failure may queue a fallback
            logger.exception("merge into running session failed (topic=%s)", topic_id)
            delivered = False
        if not delivered:
            return False
        return True

    async def notify_running_session(
        self,
        topic_id: uuid.UUID,
        notice: str,
        *,
        blocks: Sequence[uuid.UUID] = (),
        recipient_seat: str | None = None,
    ) -> bool | InputReconciliationPending:
        """Tell the session already working in this topic that the world
        changed under it: ``recipient_seat``'s, or the room's only working one.
        Returns whether the live session took it.

        The same channel as a person's mid-work message, carrying the other kind
        of thing a working session needs to hear. Its session is built on a
        snapshot taken when it started — the doc, the roster, the cards — and
        the only way anything could reach it afterwards was somebody typing. So
        a person editing the living doc mid-work changed nothing 芝士 could see,
        and it kept working from, and writing back, the version it started with.

        Framed as a platform notice, and the body is neutralized first: the
        marker is the one thing in a prompt that claims institutional authority,
        so a heading someone typed into the doc must not be able to carry it in.

        ``blocks`` is where this notice is written down. Given them, delivery
        stops being all-or-nothing: the receipt stamps them consumed, and a
        notice that never landed stays pending for the next prompt to read —
        the same 宁可重复不可丢失 a person's mid-work message already gets.
        Without them a notice that misses is gone, which is right only for
        something that is worthless a minute later (the chat-silence reminder),
        and was wrong for everything else: a reused session keeps the system
        prompt it was started with, so nothing else would tell it.
        """
        working = self._compute.working_seat(topic_id, recipient_seat)
        if working is None:
            return False
        seat_agent, _acting = working
        line = platform_prompt(strip_platform_notice(notice))
        registrar = self._input_registrar(
            InputEffects(held_block_ids=tuple(blocks), block_ids=tuple(blocks)),
            probe_unread=bool(blocks),
        )
        try:
            return bool(
                await self._compute.steer(
                    topic_id,
                    line,
                    register_input=registrar,
                    agent_handle=seat_agent,
                )
            )
        except InputOutcomeUnconfirmed as exc:
            logger.exception(
                "notice input requires reconciliation (topic=%s, input=%s)",
                topic_id,
                exc.identity.input_id,
            )
            return InputReconciliationPending(exc.identity, exc.accepted)
        except Exception:  # a failed notice must not fail the write
            logger.exception(
                "platform notice into running session failed (topic=%s)", topic_id
            )
            return False

    async def join_running_session(
        self, delivery_id: uuid.UUID, attempt_id: uuid.UUID
    ) -> bool | InputReconciliationPending | None:
        """Hand a ledger delivery to its seat's session when that session is
        already working in the conversation, the way a person's mid-work
        message is handed.

        A seat's session does one piece of work at a time, so a delivery that
        waits for work of its own waits for the current work to end — for
        hours behind a long one, and on dev it was given up after half an hour
        for 369 of 420 in the 15 days to 2026-10-16. Joined, it is read at the
        session's next tool boundary.

        ``None``: the seat's session is not working here, or this delivery is
        work of its own. A routine's run is: its session is assembled with the
        routine owner's memory (`turn_speakers.routine_owner`), which a session
        already working for somebody else does not carry. The caller starts it
        as before. ``False``: the session is working and the input did not
        reach it; nothing was sent (`receipts.withdraw_input` puts the attempt
        back to ``claimed``), so the attempt is refused and retried like any
        other. ``True``: the session's transport took it. Only the session's
        echo of it settles the delivery (`receipts.record_receipt`), as for
        any input; until then it is not sent again.
        ``InputReconciliationPending``: the outcome is unknown, which is never
        permission to send it again (`delivery.agent`).

        The input is registered under this delivery's attempt and fenced
        (``fence_delivery``): an attempt a newer notice replaced, or that lost
        its lease, is refused before anything is sent. A platform instruction
        is read when the session gets to it and does not move its foreground
        work aside; a person's @ does, and is owed an answer, as their own
        message would be.
        """
        async with self._sessions() as session:
            row = await session.get(Delivery, delivery_id)
        payload = (row.payload or {}) if row is not None else {}
        if (
            row is None
            or row.attempt_id != attempt_id
            or row.conversation_id is None
            or payload.get("eventType") == EVENT_ROUTINE_RUN
        ):
            return None
        working = self._compute.working_seat(row.conversation_id, row.recipient_handle)
        if working is None:
            return None
        seat_agent, _acting = working
        # Framed as the work it would otherwise have started frames it: a
        # ledger delivery's work is the platform's, which reads the content
        # under the platform marker (`turn.intake.preparation.prompt`).
        line = platform_prompt(str(payload.get("content") or ""))
        by_person = payload.get("eventType") == BY_PERSON
        registrar = self._input_registrar(
            InputEffects(delivery_id=delivery_id, attempt_id=attempt_id),
            fence_delivery=True,
        )
        try:
            return bool(
                await self._compute.steer(
                    row.conversation_id,
                    line,
                    register_input=registrar,
                    agent_handle=seat_agent,
                    owes_reply=by_person,
                )
            )
        except DeliveryTargetChanged:
            raise
        except InputOutcomeUnconfirmed as exc:
            logger.exception(
                "delivery into running session requires reconciliation "
                "(delivery=%s, input=%s)",
                delivery_id,
                exc.identity.input_id,
            )
            return InputReconciliationPending(exc.identity, exc.accepted)
        except Exception:  # refused and retried, never dropped
            logger.exception(
                "delivery into running session failed (delivery=%s)", delivery_id
            )
            return False

    def oldest_unread_at(self, topic_id: uuid.UUID) -> float | None:
        """When the longest-waiting unconsumed injection into this topic was
        written, on the loop clock, or None when nothing is waiting.

        The mirror of `confirm_prompt_receipt`: that one clears an entry when
        the session proves it read the text, this one reports what is left. A
        session that has stopped reading keeps every other liveness signal
        looking healthy, because those all watch what it PRODUCES, and it can
        produce output forever with its input queue frozen. What it cannot do is
        answer anybody, so this is the check that has a person behind it.
        """
        pending = self.live.unread_inputs.get(topic_id)
        return min(pending.values()) if pending else None

    def _input_registrar(
        self,
        effects: InputEffects,
        *,
        probe_unread: bool = False,
        fence_delivery: bool = False,
    ) -> InputRegistrar:

        return input_registrar(
            self._sessions,
            effects,
            self.live,
            probe_unread=probe_unread,
            fence_delivery=fence_delivery,
        )

    async def has_unread_input(self, topic_id: uuid.UUID) -> bool:
        """Whether anything said in the room is still waiting to reach 芝士.

        「一个人说的」不再是判据：人和 agent 是同一种参与者（结论 1），一个 AI
        队友说进房间的一句话同样是没人读过的输入。不算数的是芝士**自己跑出来的
        产出** —— 那一条在写入端就不带待读标记（`BlockRepository.add`）。

        「忘了 @」的补救按钮问的就是这一句，所以它必须和真正组装 prompt 时问的
        是同一个问题 —— 同一个 `_pending_input_blocks`，不是一份近似的复制品。
        一份复制品会在窗口语义改动时悄悄和它分叉，而分叉的表现是按钮说「它还没
        看到」、点下去却什么也没有可读，白烧一轮。
        """
        async with self._sessions() as session:
            history = await conversation_inputs(session, topic_id)
            return bool(_pending_input_blocks(history))

    async def pending_seat(self, topic_id: uuid.UUID) -> str | None:
        """The teammate this room's unread inputs were addressed to, if any.

        重试按钮问的就是这一句。那批还没人读的消息**是点名交给谁的**，这一轮就
        该交给谁：一个房间可以坐好几位 AI 队友，而「房间的默认席位」是另一个答
        案 —— 取它的话，另一位队友的轮次失败之后一点重试就换成默认芝士来接，而
        默认芝士那一轮的待读窗口里根本没有点名给那位队友的消息（`_addressed_to`
        按收件人过滤），于是它接了一轮却读不到真正找它的那句话。

        「没人被点名」是常态而不是异常（没 @ 不等于没说）：那种消息本来就是房间
        认的那一位的事，所以返回 None，由调用点回落到默认席位。

        窗口语义与 :meth:`has_unread_input` 共用同一个 `_pending_input_blocks`，
        理由同它：一份近似的复制品会在窗口语义改动时悄悄和它分叉。
        """
        async with self._sessions() as session:
            history = await conversation_inputs(session, topic_id)
            # 从新到旧：最近一次点名是这批消息现在要交给谁的最新说法。
            for block in reversed(_pending_input_blocks(history)):
                recipient = (block.meta or {}).get("agent_recipient") or {}
                if recipient.get("mentioned"):
                    return recipient_seat(recipient)
            return None

    def session_took_over(self, topic_id: uuid.UUID, turn_id: uuid.UUID) -> bool:
        """Did the live session take responsibility for THIS turn's indicator?

        The `session_lifecycle` frame says a session will own the ending; it
        does not say which turn's. When one is already running on the topic, the
        session reuses its activity rather than opening a second — so the newer
        turn gets no start of its own and will get no ending either. Asking by
        turn id is the difference between a handover and an assumption.
        """
        return turn_id in self.live.active_turn_ids.get(topic_id, ())
