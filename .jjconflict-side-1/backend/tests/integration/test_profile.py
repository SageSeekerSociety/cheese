"""个人主页 cross-project profile (spec §7.2)."""

import asyncio
from datetime import UTC, datetime

from app.domain.memory.models import MemoryScope
from app.domain.memory.store import DbMemoryStore
from app.domain.user.models import User, UserProfile


def test_user_profile_aggregates_across_projects(client, bearer):
    # The cheesex POST /api/users stub (handle-only user with skills/bio) was
    # retired in the fusion merge (unify P3); identity is 知是's int User +
    # UserProfile, keyed by username == handle. Seed one directly. The merged
    # schema has no skills/interests columns, and display name/bio come from
    # UserProfile.nickname/intro.
    async def _seed_user() -> None:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            u = User(
                username="u1",
                email="u1@example.com",
                created_at=now,
                updated_at=now,
            )
            s.add(u)
            await s.flush()
            s.add(
                UserProfile(
                    user_id=u.id,
                    nickname="小林",
                    intro="后端",
                    avatar_id=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await s.commit()

    asyncio.run(_seed_user())

    # Two projects, member of both; started a topic in one.
    p1 = client.post("/projects", json={"name": "P1", "owner_handle": "u1"}).json()[
        "data"
    ]["id"]
    p2 = client.post("/projects", json={"name": "P2", "owner_handle": "u1"}).json()[
        "data"
    ]["id"]
    # Roster writes need the owner's token, so u1 adds itself to both projects.
    client.post(
        f"/projects/{p1}/members",
        json={"user_handle": "u1", "role": "lead"},
        headers=bearer("u1"),
    )
    client.post(
        f"/projects/{p2}/members",
        json={"user_handle": "u1"},
        headers=bearer("u1"),
    )
    client.post(
        "/topics",
        json={"project_id": p1, "title": "我的话题", "created_by": "u1"},
    )

    # 芝士's understanding of u1 (personal memory).
    async def _seed() -> None:
        async with client.test_factory() as s:
            await DbMemoryStore(s).remember(MemoryScope.user, "u1", "擅长后端架构")
            await s.commit()

    asyncio.run(_seed())

    prof = client.get("/users/u1/profile").json()["data"]
    assert prof["name"] == "小林"  # UserProfile.nickname
    assert prof["bio"] == "后端"  # UserProfile.intro
    assert {p["name"] for p in prof["projects"]} == {"P1", "P2"}
    p1row = next(p for p in prof["projects"] if p["name"] == "P1")
    assert p1row["role"] == "lead"
    assert p1row["topics_started"] >= 1
    assert any("后端架构" in u for u in prof["understanding"])
