"""A turn that starts its seat's session names the launch it started, as that
launch's own timing lines name it.

The count of turns a spare Claude Code would serve
(`scripts/dev/count-fresh-cold-launches.py`) joins a cold turn's
`room_send_timing` to the launch's `session_launch_timing` lines by that name:
by time and seat, two launches a few seconds apart were taken for one, and the
ready line names the seat by another handle than the turn's.

The room's own ``ensure`` starts the real runner here, with a stand-in
`claude`, on the session host double of `test_claude_channel_startup`.
"""

import logging
import shlex
import sys

import pytest

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host.host import SessionHost
from tests.unit.test_a_launch_says_whether_it_began_a_conversation import STAND_IN
from tests.unit.test_claude_channel_startup import AGENT, PROJECT, TOPIC, Host


@pytest.mark.anyio
async def test_a_cold_start_names_the_launch_its_greeting_names(tmp_path, caplog):
    stand_in = tmp_path / "claude.py"
    stand_in.write_text(STAND_IN)
    host = Host(tmp_path, f"{shlex.quote(sys.executable)} {shlex.quote(str(stand_in))}")
    room = RoomSessions(
        host,  # type: ignore[arg-type]
        CLAUDE_CODE,
        SessionHost(host, screens=host),  # type: ignore[arg-type]
    )
    notes: dict[str, object] = {}
    caplog.set_level(logging.INFO)
    try:
        await room.ensure(
            SessionRef(PROJECT, TOPIC, AGENT, harness=CLAUDE_CODE),
            system_prompt="",
            notes=notes,
        )
    finally:
        host.stop()

    [greeted] = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("session_launch_timing")
    ]
    assert notes["cold"] is True
    assert notes.get("launch")
    assert f" launch={notes['launch']} stage=greeted " in greeted
