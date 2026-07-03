"""Compute credits end-to-end (spec §9.1 机构提供算力 → real quotas).

Issuance: linking a Task whose Template resource_pack carries compute_credits
creates a ComputeGrant. Deduction: a finished turn folds its token usage into
credits (1 credit = settings.compute_credit_tokens tokens) and deducts oldest
grant first. Exhaustion: a project whose grants are spent gets its turn
refused with the platform's structured event; unlinked projects are unlimited.
"""

import pytest

from tests.conftest import wait_turns_idle

# The stub agent reports usage of 10 input + 5 output tokens per turn; at the
# default rate (1 credit = 10k tokens) one turn costs 0.0015 credits.
STUB_TURN_TOKENS = 15
CREDITS_PER_TURN = STUB_TURN_TOKENS / 10_000


def _mk_project(client, name: str = "Demo") -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _mk_task(client, *, compute_credits: float | None) -> str:
    """space → template (with a compute_credits resource pack) → task."""
    space_id = client.post("/api/spaces", json={"name": "信院"}).json()["data"]["id"]
    pack = {} if compute_credits is None else {"compute_credits": compute_credits}
    template_id = client.post(
        f"/api/spaces/{space_id}/templates",
        json={"name": "创研课", "resource_pack": pack},
    ).json()["data"]["id"]
    r = client.post(f"/api/templates/{template_id}/tasks", json={"title": "题目A"})
    assert r.json()["code"] == 200
    return r.json()["data"]["id"]


def _credits(client, project_id: str) -> dict:
    r = client.get(f"/api/projects/{project_id}/credits")
    assert r.status_code == 200
    return r.json()["data"]


def _mk_topic(client, project_id: str) -> str:
    r = client.post(
        "/api/topics", json={"project_id": project_id, "title": "聊聊"}
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _run_turn(client, topic_id: str) -> list[dict]:
    """One summoned turn over the WS; returns all frames up to done/error."""
    frames: list[dict] = []
    with client.websocket_connect(f"/api/topics/{topic_id}/chat") as ws:
        ws.send_json(
            {"type": "message", "content": "你好", "author": "u1", "summon": True}
        )
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                break
    wait_turns_idle()
    return frames


def test_unlinked_project_is_unlimited(client):
    project_id = _mk_project(client)
    data = _credits(client, project_id)
    assert data["unlimited"] is True
    assert data["grants"] == []


def test_link_task_issues_grant_from_resource_pack(client):
    project_id = _mk_project(client)
    task_id = _mk_task(client, compute_credits=1000)

    r = client.post(f"/api/projects/{project_id}/tasks", json={"task_id": task_id})
    assert r.json()["code"] == 200

    data = _credits(client, project_id)
    assert data["unlimited"] is False
    assert data["credits_total"] == 1000.0
    assert data["credits_used"] == 0.0
    assert data["credits_remaining"] == 1000.0
    assert len(data["grants"]) == 1
    assert data["grants"][0]["source_task_id"] == task_id


def test_link_task_without_credits_pack_grants_nothing(client):
    project_id = _mk_project(client)
    task_id = _mk_task(client, compute_credits=None)
    client.post(f"/api/projects/{project_id}/tasks", json={"task_id": task_id})
    data = _credits(client, project_id)
    assert data["unlimited"] is True  # no grant issued → still自治/unlimited


def test_turn_deducts_credits_from_grant(client):
    project_id = _mk_project(client)
    task_id = _mk_task(client, compute_credits=1)
    client.post(f"/api/projects/{project_id}/tasks", json={"task_id": task_id})

    topic_id = _mk_topic(client, project_id)
    frames = _run_turn(client, topic_id)
    assert frames[-1]["type"] == "done"

    data = _credits(client, project_id)
    assert data["credits_used"] == pytest.approx(CREDITS_PER_TURN)
    assert data["credits_remaining"] == pytest.approx(1 - CREDITS_PER_TURN)


def test_deduction_drains_oldest_grant_first(client):
    project_id = _mk_project(client)
    # Grant 1 (older) is smaller than one turn's cost → it must be drained
    # fully, with the overflow charged to grant 2.
    task_a = _mk_task(client, compute_credits=0.001)
    task_b = _mk_task(client, compute_credits=1)
    client.post(f"/api/projects/{project_id}/tasks", json={"task_id": task_a})
    client.post(f"/api/projects/{project_id}/tasks", json={"task_id": task_b})

    topic_id = _mk_topic(client, project_id)
    _run_turn(client, topic_id)

    data = _credits(client, project_id)
    by_task = {g["source_task_id"]: g for g in data["grants"]}
    assert by_task[task_a]["credits_used"] == pytest.approx(0.001)
    assert by_task[task_b]["credits_used"] == pytest.approx(
        CREDITS_PER_TURN - 0.001
    )
    assert data["credits_used"] == pytest.approx(CREDITS_PER_TURN)


def test_exhausted_credits_refuse_next_turn(client):
    project_id = _mk_project(client)
    # One turn more than exhausts this grant (0.0001 < 0.0015).
    task_id = _mk_task(client, compute_credits=0.0001)
    client.post(f"/api/projects/{project_id}/tasks", json={"task_id": task_id})

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
    blocks = client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]
    assert any("算力额度已用完" in b["content"] for b in blocks)

    # And no further credits were burned by the refused turn.
    after = client.get(f"/api/projects/{project_id}/credits").json()["data"]
    assert after["credits_used"] == pytest.approx(data["credits_used"])


def test_market_nodes_board(client):
    r = client.get("/api/market/nodes")
    assert r.status_code == 200
    data = r.json()["data"]
    ids = [n["id"] for n in data["nodes"]]
    assert "local-docker" in ids
    local = data["nodes"][ids.index("local-docker")]
    assert local["online"] is True
    assert isinstance(local["active_turns"], int)
    assert data["current_provider"] in ("local", "remote")
