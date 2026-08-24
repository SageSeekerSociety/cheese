"""Compute credits end-to-end (spec §9.1 机构提供算力 → real quotas).

Issuance: a project created FROM a 赛题 whose 项目集 carries a compute_credits
资源包 gets a ComputeGrant (#370 — this used to be a separate "link a cheesex
task" step). Deduction: a finished turn folds its token usage into credits
(1 credit = settings.compute_credit_tokens tokens) and deducts oldest grant
first. Exhaustion: a project whose grants are spent gets its turn refused with
the platform's structured event; a project belonging to no 赛题 is unlimited.
"""

import pytest

from tests.conftest import seed_task_with_protocol, wait_work_idle
from tests.integration.conftest import chat_ws_url

# The stub agent reports usage of 10 input + 5 output tokens per turn; at the
# default rate (1 credit = 10k tokens) one turn costs 0.0015 credits.
STUB_TURN_TOKENS = 15
CREDITS_PER_TURN = STUB_TURN_TOKENS / 10_000


def _mk_project(client, name: str = "Demo", *, from_task: int | None = None) -> str:
    """A project. With `from_task`, created FROM that 赛题 — which is how a
    project accepts its 项目集's protocol and receives the 资源包 (#370)."""
    body: dict = {"name": name}
    if from_task is not None:
        body["external_task_id"] = from_task
    r = client.post("/projects", json=body)
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _mk_task(client, *, compute_credits: float | None) -> int:
    """A 赛题 under a 项目集 carrying a compute_credits 资源包."""
    pack = {} if compute_credits is None else {"compute_credits": compute_credits}
    return seed_task_with_protocol(client, resource_pack=pack)


def _add_grant(client, project_id: str, credits: float, source_task_id: int) -> None:
    """Put a second grant on a project, directly.

    A project is created from ONE 赛题 and receives ONE 资源包, so the drain-order
    behaviour below cannot be set up through the API any more. It is still real —
    grants accumulate (a top-up, a second 赛题 linked later) — and the deduction
    order is what this test is about, so the fixture is seeded rather than the
    behaviour dropped.
    """
    import asyncio as _asyncio
    import uuid as _uuid

    from app.domain.usage.repositories import ComputeGrantRepository

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            await ComputeGrantRepository(session).grant(
                project_id=_uuid.UUID(project_id),
                source_task_id=source_task_id,
                credits_total=credits,
            )
            await session.commit()

    _asyncio.run(_seed())


def _credits(client, project_id: str) -> dict:
    r = client.get(f"/projects/{project_id}/credits")
    assert r.status_code == 200
    return r.json()["data"]


def _mk_topic(client, project_id: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": "聊聊", "created_by": "u1"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _run_turn(client, topic_id: str) -> list[dict]:
    """One summoned turn over the WS; returns all frames up to done/error."""
    frames: list[dict] = []
    with client.websocket_connect(chat_ws_url(topic_id, "u1")) as ws:
        ws.send_json({"type": "message", "content": "你好", "summon": True})
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                break
    wait_work_idle()
    return frames


def test_a_project_from_no_赛题_is_unlimited(client):
    project_id = _mk_project(client)
    data = _credits(client, project_id)
    assert data["unlimited"] is True
    assert data["grants"] == []


def test_creating_a_project_from_a_赛题_issues_its_资源包(client):
    task_id = _mk_task(client, compute_credits=1000)

    project_id = _mk_project(client, from_task=task_id)

    data = _credits(client, project_id)
    assert data["unlimited"] is False
    assert data["credits_total"] == 1000.0
    assert data["credits_used"] == 0.0
    assert data["credits_remaining"] == 1000.0
    assert len(data["grants"]) == 1
    assert data["grants"][0]["source_task_id"] == task_id


def test_a_赛题_with_no_credits_pack_grants_nothing(client):
    task_id = _mk_task(client, compute_credits=None)
    project_id = _mk_project(client, from_task=task_id)
    data = _credits(client, project_id)
    assert data["unlimited"] is True  # no grant issued → still自治/unlimited


def test_turn_deducts_credits_from_grant(client):
    task_id = _mk_task(client, compute_credits=1)
    project_id = _mk_project(client, from_task=task_id)

    topic_id = _mk_topic(client, project_id)
    frames = _run_turn(client, topic_id)
    assert frames[-1]["type"] == "done"

    data = _credits(client, project_id)
    assert data["credits_used"] == pytest.approx(CREDITS_PER_TURN)
    assert data["credits_remaining"] == pytest.approx(1 - CREDITS_PER_TURN)


def test_deduction_drains_oldest_grant_first(client):
    # Grant 1 (older) is smaller than one turn's cost → it must be drained
    # fully, with the overflow charged to grant 2.
    task_a = _mk_task(client, compute_credits=0.001)
    task_b = _mk_task(client, compute_credits=1)
    project_id = _mk_project(client, from_task=task_a)
    _add_grant(client, project_id, 1, task_b)

    topic_id = _mk_topic(client, project_id)
    _run_turn(client, topic_id)

    data = _credits(client, project_id)
    by_task = {g["source_task_id"]: g for g in data["grants"]}
    assert by_task[task_a]["credits_used"] == pytest.approx(0.001)
    assert by_task[task_b]["credits_used"] == pytest.approx(CREDITS_PER_TURN - 0.001)
    assert data["credits_used"] == pytest.approx(CREDITS_PER_TURN)


def test_exhausted_credits_refuse_next_turn(client):
    # One turn more than exhausts this grant (0.0001 < 0.0015).
    task_id = _mk_task(client, compute_credits=0.0001)
    project_id = _mk_project(client, from_task=task_id)

    topic_id = _mk_topic(client, project_id)
    first = _run_turn(client, topic_id)
    assert first[-1]["type"] == "done"  # had remaining balance → allowed to run

    data = _credits(client, project_id)
    assert data["credits_remaining"] < 0  # overdrawn, recorded truthfully

    second = _run_turn(client, topic_id)
    types = [f["type"] for f in second]
    # The human's message still lands; the agent never replies — instead the
    # platform's structured exhaustion event closes the turn.
    assert types == ["user_block", "event_block", "error"]
    assert "算力额度已用完" in second[1]["block"]["content"]
    assert second[-1]["persisted"] is True
    assert not any(t == "assistant_block" for t in types)

    # The refusal event is persisted in the topic 现场 (survives reload).
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    assert any("算力额度已用完" in b["content"] for b in blocks)

    # And no further credits were burned by the refused turn.
    after = client.get(f"/projects/{project_id}/credits").json()["data"]
    assert after["credits_used"] == pytest.approx(data["credits_used"])


def test_market_nodes_board(client):
    r = client.get("/market/nodes")
    assert r.status_code == 200
    data = r.json()["data"]
    ids = [n["id"] for n in data["nodes"]]
    assert "tmux-hooks" in ids
    local = data["nodes"][ids.index("tmux-hooks")]
    assert isinstance(local["online"], bool)
    assert isinstance(local["active_turns"], int)
    assert data["current_provider"] == "tmux-hooks"
