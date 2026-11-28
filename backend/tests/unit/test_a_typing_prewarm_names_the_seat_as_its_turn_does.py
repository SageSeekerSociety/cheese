"""A session started because somebody is typing to a teammate is logged under
the seat a turn's lines name, whichever handle the page named the teammate by.

The page names the teammate by the handle it acts under (`recipient_seat`);
the turn that follows logs the seat's own (`room_send_timing seat=`). The count
of turns a spare Claude Code would serve
(`scripts/dev/count-fresh-cold-launches.py`) leaves out a turn a prewarm
started the session for, which it can tell only if both lines name one seat.
"""

import asyncio
import logging
import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.prewarm import SeatPrewarm

SEAT = "reviewer"
ACTING = "cheese-111111112222"


class _Runtime:
    def starts_sessions(self) -> bool:
        return True

    async def ensure(self, session, **_) -> None:
        return None


@pytest.mark.anyio
async def test_a_typing_prewarm_is_logged_under_the_seat_a_turn_names(caplog):
    project, room = uuid.uuid4(), uuid.uuid4()
    launch = SimpleNamespace(
        session=SessionRef(project, room, SEAT, harness=CLAUDE_CODE),
        runtime=_Runtime(),
        system_prompt="",
        resume_token=None,
        retired_token=None,
        model=None,
        env=None,
        acting=ACTING,
        needs_place=True,
        keeps_nothing=False,
    )

    async def launch_inputs(conversation_id, agent_handle, **_):
        return launch

    async def activate(session, runtime) -> None:
        return None

    lock = asyncio.Lock()
    chat = SimpleNamespace(
        turn_preparation=SimpleNamespace(launch_inputs=launch_inputs),
        _compute=SimpleNamespace(holder=lambda *_: None, activate=activate),
        live=SimpleNamespace(seat_lock_for=lambda *_: lock),
    )
    prewarm = SeatPrewarm(chat)  # type: ignore[arg-type]

    async def has_room(_room) -> bool:
        return True

    prewarm._memory = SimpleNamespace(has_room=has_room)  # type: ignore[assignment]
    caplog.set_level(logging.INFO, logger="app.domain.agent.prewarm")

    await prewarm._addressed(room, room, ACTING)

    [line] = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("session_prewarm ")
    ]
    assert f"topic={room} seat={SEAT} outcome=started " in line
