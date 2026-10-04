"""A Claude Code session that dies on its way up: the room is told one sentence,
and what the session printed is in 现场.

The failure is the one the channel raises from the runner's log
(``test_claude_channel_startup.py`` shows it getting there); this follows it
through a real turn to the two places people read it: the room's blocks and the
site's transcript.
"""

import pytest

from app.domain.agent.session_host.driver import startup_refused
from tests.conftest import StubChannel
from tests.integration.conftest import (
    chat_ws_url,
    post_message,
    post_project,
)

# What dev's session host recorded for a session relaunched onto a machine the
# work lease answered 504 for (2026-09-25), paths shortened.
LOG = """cheese-runner 0f0f ended: Claude Code exited with status 1 before it started:
Traceback (most recent call last):
  File "/h/.cheese/remote-execution/client.py", line 544, in _take_leased_machine
    client.acquire(deadline=time.monotonic())
  File "/h/.cheese/remote-execution/executor_transport.py", line 515, in acquire
    raise PlatformHTTPError(response.status, body)
executor_transport.PlatformHTTPError: Platform HTTP 504: {"code":504}"""
SENTENCE = "Claude Code 启动失败：这个房间的工作电脑还在准备"


class DiesOnItsWayUp(StubChannel):
    async def open(self, session, agent, launch):
        del session, agent, launch
        raise startup_refused(LOG, harness="Claude Code")


@pytest.fixture
def stub_hooks() -> DiesOnItsWayUp:
    # Overrides conftest's stub_hooks for this module; `client` picks it up.
    return DiesOnItsWayUp()


def _turn(client) -> tuple[str, list[dict]]:
    project = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "话题"},
    ).json()["data"]["id"]
    frames = []
    with client.websocket_connect(chat_ws_url(room, "user-1")) as ws:
        post_message(client, room, "user-1", {"content": "@芝士 hi"})
        while (frame := ws.receive_json())["type"] != "done":
            frames.append(frame)
    return room, frames


def _raw_lines() -> list[str]:
    return [line.strip() for line in LOG.splitlines() if line.strip()]


def test_the_room_hears_one_sentence_and_the_site_keeps_the_log(client):
    room, frames = _turn(client)

    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    notice = next(b for b in blocks if b["kind"] == "event" and b["author"] == "system")
    assert notice["content"] == SENTENCE
    # Nothing the session printed is in what the room renders: the line, the
    # card's title, or its fold.
    meta = notice["meta"]
    shown = " ".join(
        str(meta.get(key) or "") for key in ("title", "detail", "detail_label")
    )
    for line in _raw_lines():
        assert line not in notice["content"]
        assert line not in shown
    # The live frame says the same sentence.
    errors = [f for f in frames if f["type"] == "error"]
    assert [f["message"] for f in errors] == [SENTENCE]

    # 现场 has the same row, failed, with every line the session printed.
    site = client.get(f"/topics/{room}/transcript").json()["data"]["data"]
    row = next(b for b in site if b["id"] == notice["id"])
    assert row["meta"]["failed"] is True
    for line in _raw_lines():
        assert line in row["meta"]["error"]
