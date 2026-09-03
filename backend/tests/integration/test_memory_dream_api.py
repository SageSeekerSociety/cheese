"""POST /topics/{id}/memory/dream — 记忆整理's one write path.

Two things are being pinned here. That a pass actually lands what 芝士 proposed,
and that the path is behind the per-turn token gate — a cheese-only write left
off ``app.main._CHEESE_WRITE_PATHS`` does not 401, it passes straight through
unchecked, which is the failure nobody notices.
"""

from app.core.sandbox_auth import mint_scoped_token


def _project_topic(client) -> tuple[str, str]:
    p = client.post("/projects", json={"name": "Mem"}).json()["data"]
    t = client.post(
        "/topics", json={"project_id": p["id"], "title": "T", "created_by": "u"}
    ).json()["data"]
    return p["id"], t["id"]


def _facts(client, pid: str) -> list[str]:
    rows = client.get(f"/memory?project_id={pid}").json()["data"]["data"]
    return [r["content"] for r in rows]


def _entries(client, pid: str) -> dict[str, str]:
    rows = client.get(f"/memory?project_id={pid}").json()["data"]["data"]
    return {r["content"]: r["id"] for r in rows}


def test_a_pass_lands_what_it_proposed(client):
    pid, tid = _project_topic(client)
    for fact in ("沙箱里没有 docker", "沙箱没装 docker", "前端用 pnpm 不用 npm"):
        assert (
            client.post(f"/projects/{pid}/memory", json={"content": fact}).status_code
            == 200
        )
    ids = _entries(client, pid)

    r = client.post(
        f"/topics/{tid}/memory/dream",
        json={
            "merges": [
                {
                    "replaces": [ids["沙箱里没有 docker"], ids["沙箱没装 docker"]],
                    "content": "沙箱里没有 docker，用 dev-db.sh 起测试库",
                }
            ],
            "drops": [ids["前端用 pnpm 不用 npm"]],
            "adds": ["质量闸门只跑 lint，不跑测试"],
            "summary": "合并 2 条、退休 1 条、新增 1 条",
        },
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert (data["applied"], data["retired"], data["added"]) == (True, 3, 2)
    assert data["skipped"] == []
    # What a human opening the memory panel sees afterwards: the merged line and
    # the new fact, and no trace of what the pass retired.
    assert sorted(_facts(client, pid)) == sorted(
        ["沙箱里没有 docker，用 dev-db.sh 起测试库", "质量闸门只跑 lint，不跑测试"]
    )


def test_the_listing_carries_the_timestamp_the_snapshot_is_taken_from(client):
    """DREAM_PROMPT tells 芝士 to compute `snapshot_at` from the largest
    `updated_at` in this listing — using the backend's clock rather than the
    container's, which is not the same one. If the field is not there, 芝士 has
    to fall back to its own now(), and the concurrency check silently stops
    protecting anything on a container whose clock runs fast."""
    pid, _ = _project_topic(client)
    client.post(f"/projects/{pid}/memory", json={"content": "一条记忆"})

    row = client.get(f"/memory?project_id={pid}").json()["data"]["data"][0]

    assert "updated_at" in row
    assert row["updated_at"] >= row["created_at"]


def test_a_pass_that_changes_nothing_is_accepted(client):
    """Empty is a legitimate outcome — a pass that found nothing worth changing
    must not have to invent something to report."""
    _, tid = _project_topic(client)
    r = client.post(f"/topics/{tid}/memory/dream", json={"summary": "没什么可整理的"})
    assert r.status_code == 200
    assert r.json()["data"]["retired"] == 0


def test_another_topics_token_cannot_land_a_pass(client):
    pid, tid = _project_topic(client)
    seed = client.post(f"/projects/{pid}/memory", json={"content": "一条记忆"})
    assert seed.status_code == 200
    body = {"drops": [_entries(client, pid)["一条记忆"]]}

    _, other = _project_topic(client)
    other_project = client.get(f"/topics/{other}").json()["data"]["project_id"]
    r = client.post(
        f"/topics/{tid}/memory/dream",
        json=body,
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=str(other_project), topic_id=other
            )
        },
    )

    assert r.status_code == 401
    assert _facts(client, pid) == ["一条记忆"]

    no_token = client.post(
        f"/topics/{tid}/memory/dream", json=body, headers={"X-Cheese-Token": ""}
    )
    assert no_token.status_code == 401
