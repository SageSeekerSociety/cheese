"""后台任务跑完 → 唤醒话题（`cheese await` 的平台侧）。

The bug this closes: an agent that ends its turn to "wait for" a long command
used to freeze its topic, because nothing on the platform starts a new turn when
a detached process exits. These drive the whole path over HTTP — register the
command, report how it ended, assert what the platform did with the result — and
cover the four guards: 唤醒风暴 / 话题正在跑 / 带上下文 / 归档与结算不唤醒.

The turn runner is replaced with a recording fake: what matters here is WHETHER a
turn is summoned and WHAT it carries, not the agent's reply (which the chat-flow
tests already cover), and a fake keeps the storm-brake test from running six real
turns.
"""

import time
import uuid

import pytest

from app.api.deps import get_turn_runner
from app.domain.agent import awaited_tasks
from app.main import app


class FakeRunner:
    """Records summons and lets a test say which topics are mid-turn."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.running: set[uuid.UUID] = set()

    def submit(self, chat_service, topic_id, **kw) -> uuid.UUID:
        self.submitted.append({"topic_id": topic_id, **kw})
        return uuid.uuid4()

    def running_topic_ids(self) -> set[uuid.UUID]:
        return set(self.running)


@pytest.fixture
def runner():
    fake = FakeRunner()
    awaited_tasks.reset()
    app.dependency_overrides[get_turn_runner] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_turn_runner, None)
    awaited_tasks.reset()


def _topic(client) -> str:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    return client.post(
        "/api/topics", json={"project_id": p["id"], "title": "T"}
    ).json()["data"]["id"]


def _register(client, tid: str, **over) -> dict:
    body = {
        "command": "bash .claude/scripts/check.sh --full",
        "label": "全量检查",
        "timeout_s": 3600,
        "log_path": "/home/node/.cheese/await/run.log",
    }
    body.update(over)
    r = client.post(f"/api/topics/{tid}/background-task", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _done(client, tid: str, task: dict, **over):
    body = {"exit_code": 0, "tail": "12 passed", "duration_s": 2400.0}
    body.update(over)
    return client.post(
        f"/api/topics/{tid}/background-task/{task['task_id']}/done",
        json=body,
        headers={"X-Cheese-Token": task["wake_token"]},
    )


def _blocks(client, tid: str) -> list[dict]:
    return client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]


def test_a_finished_task_wakes_the_topic_with_its_result(client, runner):
    """The end-to-end shape: register → the command exits → a turn is summoned
    carrying the exit code and the output tail."""
    tid = _topic(client)
    task = _register(client, tid)
    assert task["task_id"] and task["wake_token"]

    r = _done(client, tid, task, exit_code=1, tail="FAILED tests/test_x.py::test_y")
    assert r.status_code == 200
    assert r.json()["data"]["woke"] is True

    assert len(runner.submitted) == 1
    turn = runner.submitted[0]
    assert turn["topic_id"] == uuid.UUID(tid)
    assert turn["summon"] is True
    # 带上下文 (guard 3): the woken agent must not have to guess what finished.
    assert "全量检查" in turn["content"]
    assert "退出码 1" in turn["content"]
    assert "FAILED tests/test_x.py::test_y" in turn["content"]
    assert "/home/node/.cheese/await/run.log" in turn["content"]
    # It opens as a system event, not as a fake human message.
    assert "全量检查" in turn["nudge_event"]


def test_the_result_also_lands_in_the_timeline(client, runner):
    """Even for a human scrolling the topic: the outcome is on the record."""
    tid = _topic(client)
    task = _register(client, tid)
    _done(client, tid, task, exit_code=0, tail="全绿 12 passed")

    landed = [
        b for b in _blocks(client, tid) if "全绿 12 passed" in (b["content"] or "")
    ]
    assert len(landed) == 1
    assert landed[0]["author_type"] == "system"


def test_wake_carries_a_timeout_verdict_not_a_bare_exit_code(client, runner):
    tid = _topic(client)
    task = _register(client, tid, timeout_s=60, command="sleep 99999")
    _done(client, tid, task, exit_code=124, tail="", duration_s=60.0)

    assert "超时" in runner.submitted[0]["content"]


# --- guard: 话题正在跑时不重复唤醒 --------------------------------------------


def test_a_running_topic_is_not_interrupted(client, runner):
    tid = _topic(client)
    runner.running.add(uuid.UUID(tid))
    task = _register(client, tid)

    r = _done(client, tid, task)
    assert r.status_code == 200
    assert r.json()["data"]["woke"] is False
    assert runner.submitted == []
    # …but the result is still on the record — deferred, not dropped.
    assert any("12 passed" in (b["content"] or "") for b in _blocks(client, tid))


def test_a_deferred_wake_fires_once_the_topic_goes_idle(client, runner, monkeypatch):
    monkeypatch.setattr(awaited_tasks, "DEFER_POLL_S", 0.02)
    tid = _topic(client)
    runner.running.add(uuid.UUID(tid))
    task = _register(client, tid)
    _done(client, tid, task)
    assert runner.submitted == []

    runner.running.clear()  # the turn ends
    for _ in range(500):  # ≤5s; returns as soon as the deferred wake lands
        if runner.submitted:
            break
        time.sleep(0.01)
    assert len(runner.submitted) == 1
    assert runner.submitted[0]["summon"] is True


# --- guard: 不造成唤醒风暴 ----------------------------------------------------


def test_registration_is_capped_per_topic(client, runner):
    """An agent looping on background work is stopped at registration."""
    tid = _topic(client)
    for _ in range(awaited_tasks.MAX_ACTIVE_PER_TOPIC):
        _register(client, tid)
    r = client.post(
        f"/api/topics/{tid}/background-task",
        json={"command": "sleep 1", "label": "", "timeout_s": 60, "log_path": "/x"},
    )
    assert r.status_code == 409


def test_wakes_are_rate_limited_per_topic(client, runner):
    """Past the rolling ceiling the result still lands, but no turn is started —
    a wake→spawn→wake loop can't burn credits unattended."""
    tid = _topic(client)
    for _ in range(awaited_tasks.MAX_WAKES_PER_WINDOW):
        task = _register(client, tid)
        assert _done(client, tid, task).json()["data"]["woke"] is True
    assert len(runner.submitted) == awaited_tasks.MAX_WAKES_PER_WINDOW

    task = _register(client, tid)
    data = _done(client, tid, task, tail="over the line").json()["data"]
    assert data["woke"] is False
    assert "上限" in data["reason"]
    assert len(runner.submitted) == awaited_tasks.MAX_WAKES_PER_WINDOW
    # The suppressed one is still visible to a human.
    assert any("over the line" in (b["content"] or "") for b in _blocks(client, tid))


# --- guard: 归档 / 卡已结算不唤醒 ---------------------------------------------


def test_an_archived_topic_takes_the_result_without_waking(client, runner):
    tid = _topic(client)
    task = _register(client, tid)
    _archive(client, tid)

    data = _done(client, tid, task).json()["data"]
    assert data["woke"] is False
    assert "归档" in data["reason"]
    assert runner.submitted == []
    assert any("12 passed" in (b["content"] or "") for b in _blocks(client, tid))


def test_an_archived_topic_refuses_new_background_tasks(client, runner):
    tid = _topic(client)
    _archive(client, tid)
    r = client.post(
        f"/api/topics/{tid}/background-task",
        json={"command": "sleep 1", "label": "", "timeout_s": 60, "log_path": "/x"},
    )
    assert r.status_code == 422


def test_a_settled_accept_card_stops_the_wake(client, runner):
    tid = _topic(client)
    task = _register(client, tid)
    _settle_card(client, tid)

    data = _done(client, tid, task).json()["data"]
    assert data["woke"] is False
    assert runner.submitted == []


# --- 一次性：回报过的任务不能再回报一次 ---------------------------------------


def test_a_task_cannot_report_twice(client, runner):
    tid = _topic(client)
    task = _register(client, tid)
    assert _done(client, tid, task).status_code == 200
    assert _done(client, tid, task).status_code == 404
    assert len(runner.submitted) == 1


def test_a_report_needs_the_wake_token(client, runner):
    tid = _topic(client)
    task = _register(client, tid)
    r = client.post(
        f"/api/topics/{tid}/background-task/{task['task_id']}/done",
        json={"exit_code": 0, "tail": "", "duration_s": 1.0},
        headers={"X-Cheese-Token": "not-a-real-token"},
    )
    assert r.status_code == 401
    assert runner.submitted == []


# --- helpers that reach past the HTTP surface for setup only ------------------


def _archive(client, tid: str) -> None:
    import asyncio

    from app.domain.topic.models import Topic, TopicStatus

    async def _go() -> None:
        async with client.test_factory() as session:
            topic = await session.get(Topic, uuid.UUID(tid))
            assert topic is not None
            topic.status = TopicStatus.archived
            await session.commit()

    asyncio.run(_go())


def _settle_card(client, tid: str) -> None:
    import asyncio

    from app.domain.review.models import AcceptCard, AcceptStatus

    async def _go() -> None:
        async with client.test_factory() as session:
            session.add(
                AcceptCard(
                    topic_id=uuid.UUID(tid),
                    reviewer_handle="alice",
                    routing_reason="",
                    status=AcceptStatus.accepted,
                )
            )
            await session.commit()

    asyncio.run(_go())
