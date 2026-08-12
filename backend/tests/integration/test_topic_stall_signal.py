"""GET /api/topics/{id}/status → `stall`: did a turn die on this topic?

The 8-hour incident (2026-08-11): three topics stopped mid-work — container
recreate, OOM-killed child, sandbox image swap — and every surface kept saying
the same thing it says about healthy work. Topic `status` was `active`, the
newest block was an ordinary tool action, and nothing anywhere could be ASKED
"is this one dead?". These tests are that question, and its answer must hold up
against the case that makes it hard: a turn legitimately grinding away for hours
must never be reported dead.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.api.deps import get_turn_runner
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.main import app


def _project_and_topic(client) -> tuple[str, str]:
    pid = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    tid = client.post(
        "/api/topics", json={"project_id": pid, "title": "做一个东西"}
    ).json()["data"]["id"]
    return pid, tid


def _seed_block(
    client,
    project_id: str,
    topic_id: str,
    *,
    kind: BlockKind,
    author_type: AuthorType,
    content: str,
    meta: dict | None = None,
    age: timedelta = timedelta(0),
) -> None:
    """One block, planted at a chosen age — the shape a topic is left in."""

    async def _run() -> None:
        async with client.test_factory() as session:
            block = await BlockRepository(session).add(
                project_id=uuid.UUID(project_id),
                topic_id=uuid.UUID(topic_id),
                author="cheese" if author_type != AuthorType.human else "u",
                author_type=author_type,
                content=content,
                kind=kind,
                meta=meta,
            )
            block.created_at = datetime.now(UTC) - age
            await session.commit()

    asyncio.run(_run())


def _tool_block(client, pid: str, tid: str, *, age: timedelta) -> None:
    """What a turn leaves behind when it is cut off between doing something and
    reporting it — the exact last block of the three dead topics."""
    _seed_block(
        client,
        pid,
        tid,
        kind=BlockKind.event,
        author_type=AuthorType.ai,
        content="运行 `pytest -q`",
        meta={"tool": "Bash", "platform": False},
        age=age,
    )


def _stall(client, topic_id: str) -> dict:
    r = client.get(f"/api/topics/{topic_id}/status")
    assert r.status_code == 200
    return r.json()["data"]["stall"]


def test_a_turn_that_died_mid_tool_is_reported_stalled(client):
    pid, tid = _project_and_topic(client)
    _tool_block(client, pid, tid, age=timedelta(minutes=45))

    stall = _stall(client, tid)
    assert stall["stalled"] is True
    # Nobody is executing this topic, which is why the silence counts.
    assert stall["reason"] == "no_live_turn"
    assert stall["live_turn"] is None
    assert 2600 < stall["silent_for_s"] < 2800
    # The verdict shows its work: a reader can go look at this exact block.
    assert stall["last_block"]["tool"] == "Bash"
    assert stall["last_block"]["kind"] == "event"


def test_a_topic_that_just_acted_is_not_stalled(client):
    """Same shape, seconds old — a turn between two tool calls is the normal
    state of a working topic, and calling that dead would make the signal
    useless within a day."""
    pid, tid = _project_and_topic(client)
    _tool_block(client, pid, tid, age=timedelta(seconds=5))

    stall = _stall(client, tid)
    assert stall["stalled"] is False
    assert stall["reason"] is None


def test_a_turn_that_finished_hours_ago_is_not_stalled(client):
    """An idle topic is not a dead one. A turn that ends leaves a message, so
    an old message means "nothing to do", not "something died"."""
    pid, tid = _project_and_topic(client)
    _tool_block(client, pid, tid, age=timedelta(hours=6))
    _seed_block(
        client,
        pid,
        tid,
        kind=BlockKind.message,
        author_type=AuthorType.ai,
        content="跑完了，全绿。",
        age=timedelta(hours=5),
    )

    assert _stall(client, tid)["stalled"] is False


def test_the_signal_goes_quiet_once_the_platform_has_announced_the_death(client):
    """The orphan sweep posts a ⚠️ system event when it tears a wedged turn
    down. After that the topic SAYS what happened, so it is no longer an
    unanswered question — and a signal that keeps firing past its own fix is one
    people learn to ignore."""
    pid, tid = _project_and_topic(client)
    _tool_block(client, pid, tid, age=timedelta(hours=6))
    assert _stall(client, tid)["stalled"] is True

    _seed_block(
        client,
        pid,
        tid,
        kind=BlockKind.event,
        author_type=AuthorType.system,
        content="⚠️ 芝士上一轮卡死了，已强制结束。",
        age=timedelta(hours=5),
    )
    assert _stall(client, tid)["stalled"] is False


def test_an_empty_topic_is_not_stalled(client):
    _, tid = _project_and_topic(client)
    stall = _stall(client, tid)
    assert stall["stalled"] is False
    assert stall["last_block"] is None
    assert stall["silent_for_s"] is None


class _RunnerWithLiveTurn:
    """A runner that IS executing a turn on this topic, still publishing frames.

    Stands in for the real one because a live turn cannot be held across a
    blocking HTTP call from a test; that the real runner reports a genuinely
    streaming turn this way is pinned separately in
    tests/unit/test_runtime.py::test_live_turn_for_topic_tracks_a_running_turn.
    """

    def __init__(self, topic_id: str, *, silent_for_s: float) -> None:
        self._topic_id = topic_id
        self._silent_for_s = silent_for_s

    def live_turn_for_topic(self, topic_id: uuid.UUID) -> dict | None:
        if str(topic_id) != self._topic_id:
            return None
        return {"turn_id": "t-1", "silent_for_s": self._silent_for_s}

    def topic_turn(self, topic_id: uuid.UUID) -> dict | None:
        return None

    def active_turns(self) -> int:
        return 1

    def project_queue_depth(self, project_id: uuid.UUID | str) -> int:
        return 0


def test_a_turn_still_running_is_never_called_dead(client):
    """The load-bearing case. A turn deep in one long tool call adds no block
    for as long as that call takes, so the timeline alone cannot tell it from a
    corpse. The heartbeat — frames the runner is still publishing, tool calls
    included — is what separates them, and it has to win over the timeline."""
    pid, tid = _project_and_topic(client)
    _tool_block(client, pid, tid, age=timedelta(hours=3))
    assert _stall(client, tid)["stalled"] is True  # ...with no turn running

    app.dependency_overrides[get_turn_runner] = lambda: _RunnerWithLiveTurn(
        tid, silent_for_s=12.0
    )
    try:
        stall = _stall(client, tid)
        assert stall["stalled"] is False
        assert stall["live_turn"]["silent_for_s"] == 12.0
    finally:
        app.dependency_overrides.pop(get_turn_runner, None)


def test_a_turn_that_is_live_but_long_silent_is_still_caught(client):
    """A task can outlive what it was driving: the container dies, the stream
    never ends, and the turn sits in the runner producing nothing. Being
    "running" is not proof of life — the frames are."""
    pid, tid = _project_and_topic(client)
    _tool_block(client, pid, tid, age=timedelta(hours=3))

    app.dependency_overrides[get_turn_runner] = lambda: _RunnerWithLiveTurn(
        tid, silent_for_s=3 * 3600
    )
    try:
        stall = _stall(client, tid)
        assert stall["stalled"] is True
        # Named apart from the process-is-gone case: this one still holds a task.
        assert stall["reason"] == "silent_turn"
    finally:
        app.dependency_overrides.pop(get_turn_runner, None)
