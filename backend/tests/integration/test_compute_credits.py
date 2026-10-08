"""Compute credits end-to-end (spec §9.1 机构提供算力 → real quotas).

Issuance: a project created FROM a 赛题 whose 项目集 carries a compute_credits
资源包 gets a ComputeGrant (#370 — this used to be a separate "link a cheesex
task" step). Deduction: the tokens a turn spent, as the metering proxy logs
them, are priced at the model's rates and charged as cost over the price per
credit, oldest grant first. Exhaustion: a project whose grants are spent gets
its turn refused with the platform's structured event; a project belonging to
no 赛题 is unlimited.
"""

import json
import time

import pytest

from app.domain.usage.credits import CREDIT_USD
from tests.conftest import seed_task_with_protocol, seed_user, wait_work_idle
from tests.integration.conftest import (
    free_plan_credits,
    in_thread,
    post_message,
    post_project,
    room_socket,
)

# A Claude Code session reports no usage of its own: the metering proxy logs
# each model response it carried, and that log is what burns credits. Each
# turn here is metered at 10 input + 5 output tokens of a model priced at $5 /
# $25 per million, at $0.01 per credit.
STUB_TURN_TOKENS = 15
_RATES = {"claude-opus-5": (5e-6, 25e-6, 5e-7, 6.25e-6)}
CREDITS_PER_TURN = (10 * 5e-6 + 5 * 25e-6) / CREDIT_USD


@pytest.fixture(autouse=True)
def _priced(monkeypatch):
    """The gateway's price table and the deployment's price per credit."""
    from app.domain.feature_stats import pricing

    async def rates(transport=None):
        return _RATES

    monkeypatch.setattr(pricing, "model_rates", rates)


def _mk_project(client, name: str = "Demo", *, from_task: int | None = None) -> str:
    """A project. With `from_task`, created FROM that 赛题 — which is how a
    project accepts its 项目集's protocol and receives the 资源包 (#370).

    资源包只发给**过审的报名者**，所以从赛题建项目前先给 `u1` 补一条已通过的
    报名：这条路径（报名 → 过审 → 拿到额度）是产品的正路，缺了它建出来的项目是
    「与这道赛题无关的人随手建的项目」，本来就不该有额度。
    """
    client.headers["Authorization"] = f"Bearer {seed_user(client, 'u1')}"
    body: dict = {"name": name}
    if from_task is not None:
        _approve(client, from_task, "u1")
        body["external_task_id"] = from_task
    r = post_project(client, json=body)
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _approve(client, task_id: int, handle: str) -> None:
    """`handle` 在这道赛题上的报名，状态为已通过（``ApproveType.APPROVED = 0``）。"""
    import asyncio as _asyncio
    from datetime import UTC, datetime

    from app.domain.task.models import TaskMembership
    from app.domain.user.repositories import UserRepository

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None, handle
            session.add(
                TaskMembership(
                    task_id=task_id,
                    member_id=user.id,
                    is_team=False,
                    approved=0,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    _asyncio.run(_seed())


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

    from app.domain.usage.ledger import Ledger

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            await Ledger(session).grant_earmark(
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
        json={"project_id": project_id, "title": "聊聊"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _metered(client, project_id: str, topic_id: str, log) -> None:
    """The proxy's log line for the turn's traffic, and the ingest that reads it."""
    import asyncio as _asyncio

    from app.domain.usage.subscription_ingest import ingest_once

    with log.open("a") as out:
        out.write(
            json.dumps(
                {
                    # Logged as the turn ran, so it counts toward that turn.
                    "ts": time.time(),
                    "project_id": project_id,
                    "topic_id": topic_id,
                    "model": "claude-opus-5",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "total_tokens": STUB_TURN_TOKENS,
                    "provider": "subscription",
                }
            )
            + "\n"
        )
    _asyncio.run(ingest_once(client.test_factory, log))  # type: ignore[attr-defined]


def _run_turn(client, topic_id: str, meter=None) -> list[dict]:
    """One summoned turn over the WS; returns all frames up to done/error.

    ``meter`` is called once the turn has run, as the proxy would have logged
    it; a refused turn never reached the model, so it is not metered."""
    frames: list[dict] = []
    with room_socket(client, topic_id, "u1") as ws:
        post_message(client, topic_id, "u1", {"content": "@芝士 你好"})
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                break
    wait_work_idle()
    if meter is not None and frames[-1]["type"] == "done":
        meter()
    return frames


def _free_month(client) -> float:
    async def read() -> float:
        from app.domain.usage.models import Plan

        async with client.test_factory() as session:  # type: ignore[attr-defined]
            plan = await session.get(Plan, "free")
            assert plan is not None and plan.credits_per_period is not None
            return plan.credits_per_period

    import asyncio as _asyncio

    return _asyncio.run(read())


def test_a_project_from_no_赛题_spends_its_teams_free_month(client):
    project_id = _mk_project(client)
    data = _credits(client, project_id)
    assert data["unlimited"] is False
    assert data["credits_remaining"] == _free_month(client)


def test_creating_a_project_from_a_赛题_issues_its_资源包(client):
    task_id = _mk_task(client, compute_credits=1000)

    project_id = _mk_project(client, from_task=task_id)

    data = _credits(client, project_id)
    assert data["credits_total"] == 1000.0 + _free_month(client)
    assert data["credits_used"] == 0.0
    assert [(g["source_task_id"], g["credits_total"]) for g in data["grants"]] == [
        (task_id, 1000.0)
    ]


def test_a_赛题_with_no_credits_pack_grants_nothing(client):
    task_id = _mk_task(client, compute_credits=None)
    project_id = _mk_project(client, from_task=task_id)
    data = _credits(client, project_id)
    assert data["grants"] == []  # no 资源包: only the team's plan pays


def test_turn_deducts_credits_from_grant(client, tmp_path):
    task_id = _mk_task(client, compute_credits=1)
    project_id = _mk_project(client, from_task=task_id)

    room = _mk_topic(client, project_id)
    # 芝士 answers in a 支线; the proxy logs the channel it works in.
    topic_id = in_thread(client, room, "u1")
    log = tmp_path / "usage.jsonl"
    frames = _run_turn(
        client, topic_id, lambda: _metered(client, project_id, room, log)
    )
    assert frames[-1]["type"] == "done"

    data = _credits(client, project_id)
    [earmark] = [g for g in data["grants"] if g["source_task_id"] == task_id]
    assert earmark["credits_used"] == pytest.approx(CREDITS_PER_TURN)
    assert data["credits_used"] == pytest.approx(CREDITS_PER_TURN)


def test_deduction_drains_oldest_grant_first(client, tmp_path):
    # Grant 1 (older) is smaller than one turn's cost → it must be drained
    # fully, with the overflow charged to grant 2.
    task_a = _mk_task(client, compute_credits=0.001)
    task_b = _mk_task(client, compute_credits=1)
    project_id = _mk_project(client, from_task=task_a)
    _add_grant(client, project_id, 1, task_b)

    room = _mk_topic(client, project_id)
    # 芝士 answers in a 支线; the proxy logs the channel it works in.
    topic_id = in_thread(client, room, "u1")
    log = tmp_path / "usage.jsonl"
    _run_turn(client, topic_id, lambda: _metered(client, project_id, room, log))

    data = _credits(client, project_id)
    by_task = {g["source_task_id"]: g for g in data["grants"]}
    assert by_task[task_a]["credits_used"] == pytest.approx(0.001)
    assert by_task[task_b]["credits_used"] == pytest.approx(CREDITS_PER_TURN - 0.001)
    assert data["credits_used"] == pytest.approx(CREDITS_PER_TURN)


def test_exhausted_credits_refuse_next_turn(client, tmp_path):
    # One turn more than exhausts this grant (0.0001 < 0.0175), and the team's
    # plan issues nothing to fall back on.
    free_plan_credits(client, 0.0)
    task_id = _mk_task(client, compute_credits=0.0001)
    project_id = _mk_project(client, from_task=task_id)

    room = _mk_topic(client, project_id)
    # 芝士 answers in a 支线; the proxy logs the channel it works in.
    topic_id = in_thread(client, room, "u1")
    log = tmp_path / "usage.jsonl"

    def meter() -> None:
        _metered(client, project_id, room, log)

    first = _run_turn(client, topic_id, meter)
    assert first[-1]["type"] == "done"  # had remaining balance → allowed to run

    data = _credits(client, project_id)
    assert data["credits_remaining"] < 0  # overdrawn, recorded truthfully

    second = _run_turn(client, topic_id, meter)
    types = [f["type"] for f in second]
    # The human's message still lands; the agent never replies — instead the
    # platform's structured exhaustion event closes the turn.
    assert types == ["user_block", "event_block", "error"]
    assert "额度已用完" in second[1]["block"]["content"]
    assert second[-1]["persisted"] is True
    assert not any(t == "assistant_block" for t in types)

    # The refusal event is persisted in the topic 现场 (survives reload).
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    assert any("额度已用完" in b["content"] for b in blocks)

    # And no further credits were burned by the refused turn.
    after = client.get(f"/projects/{project_id}/credits").json()["data"]
    assert after["credits_used"] == pytest.approx(data["credits_used"])


def test_market_nodes_board(client):
    """节点看板 shows the machine pools this deployment offers, and marks the one
    an unconfigured topic lands on. It used to show exactly one node — the
    platform's own container host — which was the one pool nobody could pick."""
    from app.core.config import settings
    from app.domain.agent.market import compute_default_name, compute_listings

    r = client.get("/market/nodes")
    assert r.status_code == 200
    data = r.json()["data"]

    ids = [n["id"] for n in data["nodes"]]
    assert ids == [p.id for p in compute_listings(settings)]
    assert data["current_provider"] == compute_default_name(settings)
    assert [n["id"] for n in data["nodes"] if n["current"]] == [
        data["current_provider"]
    ]
    for node in data["nodes"]:
        assert isinstance(node["online"], bool)
        assert node["detail"]
