"""What a room does with what its sessions say and do.

A room's sessions (`room/sessions/`) hand the room one stream of items
(``RoomReader``) once their own books have taken each: what the session
said and did, when it started and stopped working, which inputs it took in,
how its work ended, whether the machine under it is reachable, and what it is
in the middle of writing. Each is routed here to the room's books for it, in
the order it was read: the event log and the room's timeline
(``turn.intake.hooks``), the realtime 「谁在干活」 frames, the input ledger
(``turn_inputs``), what a compacted session has to be told again
(``agent_sessions.told``), the waiting-for-the-machine notes, and the live
frames.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.domain.agent.harness import SessionRef
from app.domain.agent.live_frames import publish_live
from app.domain.agent.reads import (
    Abandoned,
    Compacted,
    Followed,
    Reachable,
    Read,
    Received,
    Replaced,
    Report,
    ResultLanded,
    Working,
    Writing,
)
from app.domain.agent.service import AgentEvent
from app.domain.delivery.input_identity import InputReceipt

# Where the room hears its sessions: one item at a time, each for the session
# that produced it, in the order its sessions were read. What the session said
# and did, when it started and stopped working, which inputs it took in, what
# its runner reported (``Report``), work abandoned with nobody left to report
# on it, whether the machine under it is reachable, and what it is in
# the middle of writing all arrive here (``reads.Read``); the
# room decides what each one means for it.
#
# A ``Report`` is applied once (``agent.runner_reports``): the reader answers
# False for one it had applied and followed through before, and what follows
# from it is not done a second time; ``Followed`` says it was followed through.
# Every other item answers nothing.
RoomReader = Callable[[SessionRef, Read], Awaitable[object]]


class NativeEvents(Protocol):
    """The event receiver supplied by composition, not a turn implementation import."""

    async def accept(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        event: AgentEvent,
        eid: str | None,
        result_text_seen: bool,
        platform_unsolicited: bool,
    ) -> None: ...


class RoomBooks(Protocol):
    """What a room keeps of its sessions (``chat.ChatService``)."""

    async def confirm_prompt_receipt(self, receipt: InputReceipt) -> None: ...

    async def apply_report(self, report: Report) -> object: ...

    async def report_followed(self, report: Report) -> None: ...

    async def result_landed(self, report: Report) -> None: ...

    async def inputs_replaced(
        self,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        native: str,
        *,
        read: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> None: ...

    async def confirm_work_abandoned(self, work_id: uuid.UUID) -> None: ...

    async def context_compacted(self, session: SessionRef) -> None: ...

    async def _set_hook_activity(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        active: bool,
        *,
        agent_handle: str | None = None,
    ) -> None: ...

    async def _note_reachability(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        reachable: bool,
        reason: str,
    ) -> None: ...

    @property
    def hook_events(self) -> NativeEvents: ...


def reader(chat: RoomBooks) -> RoomReader:
    """The room's ear, for ``chat``'s rooms."""

    async def hear(session: SessionRef, read: Read) -> object:
        event = read.event
        work = uuid.UUID(read.work_id) if read.work_id else None
        if isinstance(event, Received):
            await chat.confirm_prompt_receipt(event.receipt)
        elif isinstance(event, Report):
            return await chat.apply_report(event)
        elif isinstance(event, Followed):
            await chat.report_followed(event.report)
        elif isinstance(event, ResultLanded):
            await chat.result_landed(event.report)
        elif isinstance(event, Replaced):
            await chat.inputs_replaced(
                session.conversation_id,
                session.agent_handle,
                session.harness,
                event.conversation,
                read=event.read,
            )
        elif isinstance(event, Compacted):
            await chat.context_compacted(session)
        elif isinstance(event, Writing):
            await publish_live(
                session.conversation_id,
                work,
                event.author or session.agent_handle,
                list(event.blocks),
            )
        else:
            assert work is not None, "a room's session says nothing outside work"
            if isinstance(event, Working):
                await chat._set_hook_activity(
                    session.project_id,
                    session.conversation_id,
                    work,
                    event.active,
                    agent_handle=session.agent_handle,
                )
            elif isinstance(event, Reachable):
                await chat._note_reachability(
                    session.project_id,
                    session.conversation_id,
                    work,
                    event.yes,
                    event.reason,
                )
            elif isinstance(event, Abandoned):
                await chat.confirm_work_abandoned(work)
            elif isinstance(event, AgentEvent):
                await chat.hook_events.accept(
                    session.project_id,
                    session.conversation_id,
                    work,
                    event,
                    read.eid,
                    read.text_seen,
                    read.unsolicited,
                )

    return hear
