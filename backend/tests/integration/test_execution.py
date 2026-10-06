"""施工现场 transcript + 资源用量 (spec §7.1/§9.1)."""

import asyncio
import json
import time

from app.domain.usage.subscription_ingest import ingest_once
from tests.integration.conftest import (
    post_message,
    post_project,
    session_auth_headers,
)


def _topic(client) -> tuple[str, str]:
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题"},
    ).json()["data"]
    return p["id"], t["id"]


def _chat(client, room: str) -> str:
    """Call 芝士 in the channel's main line; it answers in the message's 支线,
    whose id this returns once the turn has left its steps there."""
    asked = post_message(client, room, "user-1", {"content": "@芝士 hi"})
    thread = client.post(
        f"/blocks/{asked['id']}/thread", headers=session_auth_headers("user-1")
    ).json()["data"]["id"]
    deadline = time.monotonic() + 30
    while not client.get(f"/topics/{thread}/transcript").json()["data"]["data"]:
        assert time.monotonic() < deadline, "the turn left nothing in its 支线"
        time.sleep(0.2)
    return thread


def _metered(client, pid: str, tid: str, log) -> None:
    """What the metering proxy logged for the turn's model traffic, ingested.

    A Claude Code session reports no usage of its own; the proxy's log is where
    a room's spend comes from."""
    log.write_text(
        json.dumps(
            {
                # Logged as the turn ran, so it counts toward that turn.
                "ts": time.time(),
                "project_id": pid,
                "topic_id": tid,
                "model": "claude-opus-5",
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "total_tokens": 15,
                "provider": "subscription",
            }
        )
        + "\n"
    )
    asyncio.run(ingest_once(client.test_factory, log))


def test_transcript_and_usage_after_chat(client, tmp_path):
    pid, room = _topic(client)
    tid = _chat(client, room)
    # The proxy logs the channel a session works in.
    _metered(client, pid, room, tmp_path / "usage.jsonl")

    # 施工现场 = 工作细节 only: chat messages (human or AI) never mirror into
    # the transcript — they live in the conversation pane.
    tr = client.get(f"/topics/{tid}/transcript").json()["data"]["data"]
    assert all(b["kind"] == "event" for b in tr)
    assert not any(b["kind"] == "message" for b in tr)

    # 资源用量: token/cost recorded for the topic and rolled up to the project.
    tu = client.get(f"/topics/{tid}/usage").json()["data"]
    assert tu["total_tokens"] == 15  # 10 + 5, as the proxy logged it
    assert tu["turns"] == 1
    pu = client.get(f"/projects/{pid}/usage").json()["data"]
    assert pu["total_tokens"] == 15
