"""个人主页 cross-project profile (spec §7.2)."""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.memory.models import MemoryScope, user_scope_id
from app.domain.memory.store import DbMemoryStore
from app.domain.project.services import ProjectService
from app.domain.user.models import User, UserProfile
from tests.integration.conftest import add_external_member, post_project


def _seed_u1_with_two_projects(client) -> tuple[str, str]:
    """u1 (小林, 后端) owns P1 and P2, started a topic in P1, and the 芝士 of each
    project remembers something about them. Returns (P1 id, P2 id)."""

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

    p1 = post_project(client, json={"name": "P1", "owner_handle": "u1"}).json()["data"][
        "id"
    ]
    p2 = post_project(client, json={"name": "P2", "owner_handle": "u1"}).json()["data"][
        "id"
    ]
    client.post(
        "/topics",
        json={"project_id": p1, "title": "我的话题", "created_by": "u1"},
    )

    # 芝士 对 u1 的理解。这一页问的是「大家对我的认识」，而池已经是每个项目那位芝士
    # 自己的东西了（结论 8），所以每个项目各种一份；这一页要做的就是把每一份都收
    # 上来。
    async def _seed_memory(project_id: str, content: str) -> None:
        async with client.test_factory() as s:
            project = await ProjectService(s).get_or_404(uuid.UUID(project_id))
            agent = await AgentInstanceService(s).for_project(project)
            await DbMemoryStore(s).remember(
                MemoryScope.user,
                user_scope_id(project.id, agent.handle, "u1"),
                content,
            )
            await s.commit()

    asyncio.run(_seed_memory(p1, "擅长后端架构"))
    asyncio.run(_seed_memory(p2, "喜欢先写测试"))
    return p1, p2


def test_user_profile_aggregates_across_projects(client, bearer):
    _seed_u1_with_two_projects(client)

    resp = client.get("/users/u1/profile", headers=bearer("u1"))
    assert resp.status_code == 200, resp.text
    prof = resp.json()["data"]
    assert prof["name"] == "小林"  # UserProfile.nickname
    assert prof["bio"] == "后端"  # UserProfile.intro
    assert {p["name"] for p in prof["projects"]} == {"P1", "P2"}
    p1row = next(p for p in prof["projects"] if p["name"] == "P1")
    # How u1 is in P1: its owner.
    assert p1row["source"] == "owner"
    assert p1row["topics_started"] >= 1
    # Their own page gathers what every project's 芝士 holds about them.
    assert any("后端架构" in u for u in prof["understanding"])
    assert any("先写测试" in u for u in prof["understanding"])


def test_user_profile_requires_a_signed_in_caller(client):
    _seed_u1_with_two_projects(client)

    resp = client.get("/users/u1/profile")
    assert resp.status_code == 401


def test_user_profile_shows_another_viewer_only_projects_they_share(client, bearer):
    _, p2 = _seed_u1_with_two_projects(client)
    # u2 is on P2's roster and in nothing else of u1's.
    add_external_member(client, p2, "u2", by="u1")

    resp = client.get("/users/u1/profile", headers=bearer("u2"))
    assert resp.status_code == 200, resp.text
    prof = resp.json()["data"]
    assert prof["name"] == "小林"
    assert [p["name"] for p in prof["projects"]] == ["P2"]
    # What the agents remember about u1 is u1's alone to read — including what
    # the 芝士 of the project u2 shares with them holds.
    assert prof["understanding"] == []


def test_user_profile_shows_a_stranger_no_projects(client, bearer):
    _seed_u1_with_two_projects(client)
    post_project(client, json={"name": "Elsewhere", "owner_handle": "u3"})

    resp = client.get("/users/u1/profile", headers=bearer("u3"))
    assert resp.status_code == 200, resp.text
    prof = resp.json()["data"]
    assert prof["projects"] == []
    assert prof["understanding"] == []
