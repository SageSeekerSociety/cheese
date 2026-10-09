"""Session recovery after the process changed: listen again to the sessions
that outlived it, hand their unread tails to the rooms, and fold the round's
per-conversation death evidence into the durable set (FB-56 legacy③).

The mixin holds the orchestration, a room's replay included; ChatService keeps
the room-side pieces it calls (`_begin_self_started_turn`, the hook-work
registry).
"""

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from app.domain.agent import death_evidence
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import SessionRef
from app.domain.agent.pending_messages import nudge_messages

if TYPE_CHECKING:
    from app.domain.agent.compute import ComputePool
    from app.domain.agent.live_work import HookWorkState, LiveWork
    from app.domain.agent.prewarm import SeatPrewarm

logger = logging.getLogger(__name__)


class SessionRecovery:
    """The recovery/replay orchestration half of ChatService. The attributes
    and the room-side method are the service's; declared here so the
    type checker sees the mixin's own contract."""

    if TYPE_CHECKING:
        _compute: ComputePool
        live: LiveWork

        async def _begin_self_started_turn(
            self,
            project_id: uuid.UUID,
            topic_id: uuid.UUID,
            turn_id: uuid.UUID,
            *,
            opened: bool = False,
            agent_handle: str | None = None,
            session_id: str | None = None,
        ) -> HookWorkState | None: ...

        _replay_slots: asyncio.Semaphore
        prewarm: SeatPrewarm

    async def recover_sessions(self, device_id: str | None = None) -> int:
        """Listen again to sessions that outlived this process, and start
        landing what they said while nobody was.

        Two calls to the runtime, and the split is deliberate: ``recover``
        establishes that we are listening, ``replay`` hands over the tail. What
        the room already shows is ours to supply; which of the harness's own
        records are still unlanded is its.

        Returns once every session is listened to and its turn's bookkeeping is
        back, which is all a turn elsewhere needs. The replays go on in the
        background, a room at a time (``replaying``): a session that was not
        read for a day can take longer to replay than this process stays up,
        and waiting for it held every room's turns, not only its own.
        """
        sessions = await self._compute.recover_sessions(device_id)
        # Death evidence refreshes HERE, recovered sessions or not (FB-56
        # legacy③): zero handles is exactly the case terminal evidence exists
        # for. A service without a sessions factory — a contract double —
        # cannot read the pointers, and cannot means unknown: nothing marks.
        sessions_factory = getattr(self, "_sessions", None)
        if sessions_factory is not None:
            async with sessions_factory() as session:
                await death_evidence.refresh(session, self._compute, self.live)
        # One per seat, not per room: teammates in one room run side by side,
        # and a seat left out here is re-attached but never read again until
        # somebody next addresses it. A seat is a conversation's — the room's
        # own line, a task's or a 支线's — and so is the work it runs.
        unique = {
            (session.conversation_id, session.agent_handle): session
            for session in sessions
        }
        rooms: dict[uuid.UUID, list[SessionRef]] = {}
        for session in unique.values():
            # A turn still running there was fed by a process that is gone,
            # and its result lands here. Without its bookkeeping that result
            # closes nothing: the batch it answered is never stamped
            # consumed, and the next turn sends it again.
            work = self._compute.work_in_flight(
                session.conversation_id, session.agent_handle or None
            )
            if (
                work is not None
                and (session.conversation_id, work) not in self.live.hook_work
            ):
                try:
                    found = self._compute.found_conversations(
                        session.conversation_id, session.agent_handle
                    )
                    await self._begin_self_started_turn(
                        session.project_id,
                        session.conversation_id,
                        work,
                        opened=True,
                        agent_handle=session.agent_handle or None,
                        session_id=next(iter(found)) if len(found) == 1 else None,
                    )
                except Exception:  # noqa: BLE001 — one topic cannot block startup
                    logger.exception(
                        "session recovery failed for topic %s", session.conversation_id
                    )
                    continue
            rooms.setdefault(session.conversation_id, []).append(session)
        for topic_id, seats in rooms.items():
            # A device reconnecting while its room still replays: the new
            # replay starts where that one stops, not beside it.
            replay = asyncio.create_task(
                self._replay_room(seats, after=self.live.replays.get(topic_id)),
                name=f"replay:{topic_id}",
            )
            self.live.replays[topic_id] = replay
            replay.add_done_callback(self._replayed)
        return len(unique)

    async def _replay_room(
        self, seats: list[SessionRef], *, after: asyncio.Task | None
    ) -> None:
        if after is not None:
            await asyncio.gather(after, return_exceptions=True)
        async with self._replay_slots:
            for session in seats:
                try:
                    await self._compute.replay(session)
                except DeviceOffline:
                    # The machine holding this session is not there. Nothing to
                    # recover and nothing to fix; its next connection runs this.
                    logger.warning(
                        "session not recovered for topic %s: device offline",
                        session.conversation_id,
                    )
                except DeviceCallError as exc:
                    # The machine is there and said no — its runner's socket is
                    # not up yet (a cold one takes about a minute), or the room's
                    # home is gone. Same standing as the machine being away: the
                    # next connection recovers this session, and the machine's
                    # own words are what somebody reading this would act on.
                    logger.warning(
                        "session not recovered for topic %s: %s",
                        session.conversation_id,
                        exc,
                    )
                except Exception:  # noqa: BLE001 — one topic cannot block startup
                    logger.exception(
                        "session recovery failed for topic %s", session.conversation_id
                    )
                else:
                    nudge_messages(self, session.conversation_id)
                    # Taken over, read up: now compare it with what this
                    # release would start, while nobody is waiting on it.
                    self.prewarm.nudge(session)

    def retire_unheard(self, turn_ids: set[uuid.UUID]) -> None:
        """Let go of the live state of turns the orphan sweep closed because
        their prompt reached nobody. Their message goes out again in a new turn
        (or to a person), and no session will ever end them; kept, each one is
        a second live turn on its seat, which refuses that seat's questions as
        ambiguous and keeps reminding a turn nobody runs to speak."""
        for key in [key for key in self.live.hook_work if key[1] in turn_ids]:
            del self.live.hook_work[key]
            self.live.mark_turn_inactive(*key)

    def _replayed(self, replay: asyncio.Task) -> None:
        for topic_id, current in list(self.live.replays.items()):
            if current is replay:
                del self.live.replays[topic_id]

    def replaying(self, topic_id: uuid.UUID) -> asyncio.Task | None:
        """The replay a turn in this room has to wait for, if one is running."""
        replay = self.live.replays.get(topic_id)
        return None if replay is None or replay.done() else replay

    async def replays_settled(self) -> None:
        """Wait until no room is replaying."""
        while self.live.replays:
            await asyncio.gather(*self.live.replays.values(), return_exceptions=True)
