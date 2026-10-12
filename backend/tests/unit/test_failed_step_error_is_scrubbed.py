"""A failed step's error is stored scrubbed, like the step's output.

The error text of a failed command is shown in the room and read back by
transcript tooling; a credential on the command line comes back in it.
"""

import uuid
from contextlib import asynccontextmanager

import pytest

from app.domain.agent.turn.store import events


class _Session:
    async def commit(self):
        return None


@asynccontextmanager
async def _sessions():
    yield _Session()


@pytest.mark.anyio
async def test_a_failed_steps_error_is_stored_without_the_token(monkeypatch):
    stored = {}

    async def fake_fail_step(session, block_id, error):
        stored["error"] = error
        return None

    monkeypatch.setattr(events, "fail_step", fake_fail_step)
    await events._mark_step_failed(
        _sessions,
        uuid.uuid4(),
        "push failed: https://x:ghp_abcdefgh12345@github.com/o/r",
    )
    assert "ghp_" not in stored["error"]
    assert "push failed" in stored["error"]


def test_a_tool_lines_command_is_stored_without_the_token():
    from app.domain.agent.event_lines import _format_tool_event, _tool_event_meta
    from app.domain.agent.tool_preview import ToolPreview

    command = (
        "git push https://x:ghp_abcdefgh12345@github.com/o/r && echo password=hunter22"
    )
    preview = ToolPreview(text=command)
    meta = _tool_event_meta("Bash", preview, platform=False, detail=command)
    text = _format_tool_event("Bash", preview)
    for stored in (meta["arg"], meta["detail"], text):
        assert "ghp_" not in stored and "hunter22" not in stored
        assert "git push" in stored


def test_a_failed_turns_notice_is_stored_without_the_token():
    from app.domain.agent.turn.state.notices import _turn_failure_notice

    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    said = f"upstream refused: Authorization: Bearer {secret}"
    log = f"curl -H 'Authorization: Bearer {secret}' failed"
    line, meta = _turn_failure_notice(said, None, log=log)
    detail = meta.get("detail")
    stored = repr(line) + repr(meta) + repr(getattr(detail, "descriptor", dict)())
    assert secret not in stored
    # The service's words are still kept, only the credential is gone.
    assert "upstream refused" in stored
