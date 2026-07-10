"""个人主页 cross-project profile (spec §7.2)."""

import asyncio

from app.domain.memory.models import MemoryScope
from app.domain.memory.store import DbMemoryStore


def test_user_profile_aggregates_across_projects(client):
    client.post(
        "/api/users",
        json={"handle": "u1", "name": "小林", "skills": ["Python"], "bio": "后端"},
    )
    # Two projects, member of both; started a topic in one.
    p1 = client.post("/api/projects", json={"name": "P1"}).json()["data"]["id"]
    p2 = client.post("/api/projects", json={"name": "P2"}).json()["data"]["id"]
    client.post(
        f"/api/projects/{p1}/members", json={"user_handle": "u1", "role": "lead"}
    )
    client.post(f"/api/projects/{p2}/members", json={"user_handle": "u1"})
    client.post(
        "/api/topics",
        json={"project_id": p1, "title": "我的话题", "created_by": "u1"},
    )

    # 芝士's understanding of u1 (personal memory).
    async def _seed() -> None:
        async with client.test_factory() as s:
            await DbMemoryStore(s).remember(MemoryScope.user, "u1", "擅长后端架构")
            await s.commit()

    asyncio.run(_seed())

    prof = client.get("/api/users/u1/profile").json()["data"]
    assert prof["name"] == "小林"
    assert "Python" in prof["skills"]
    assert {p["name"] for p in prof["projects"]} == {"P1", "P2"}
    p1row = next(p for p in prof["projects"] if p["name"] == "P1")
    assert p1row["role"] == "lead"
    assert p1row["topics_started"] >= 1
    assert any("后端架构" in u for u in prof["understanding"])
