"""个人主页 cross-project profile (spec §7.2)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.memory.models import MemoryScope, user_scope_id
from app.domain.memory.store import DbMemoryStore
from app.domain.project.services import ProjectService
from app.domain.team.models import (
    Team,
    TeamMemberRole,
    TeamUserRelation,
    TeamVisibility,
)
from app.domain.topic.models import Topic
from app.domain.user.models import User, UserProfile
from tests.integration.conftest import (
    a_team,
    add_external_member,
    post_project,
    registered,
)


def _remember(client, project_id: str, about: str, content: str) -> None:
    """The project's 芝士 notes something about ``about``."""

    async def _run() -> None:
        async with client.test_factory() as s:
            project = await ProjectService(s).get_or_404(uuid.UUID(project_id))
            agent = await AgentInstanceService(s).for_project(project)
            await DbMemoryStore(s).remember(
                MemoryScope.user,
                user_scope_id(project.id, agent.handle, about),
                content,
            )
            await s.commit()

    asyncio.run(_run())


def _topic(client, project_id: str, title: str) -> str:
    resp = client.post(
        "/topics", json={"project_id": project_id, "title": title, "created_by": "u1"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _private_chat(client, project_id: str) -> str:
    async def _run() -> str:
        async with client.test_factory() as s:
            topic = Topic(
                project_id=uuid.UUID(project_id),
                title="私聊",
                created_by="u1",
                is_private=True,
            )
            s.add(topic)
            await s.commit()
            return str(topic.id)

    return asyncio.run(_run())


def _wrote(
    client,
    topic_id: str,
    *,
    at: datetime,
    n: int = 1,
    author_type: AuthorType = AuthorType.participant,
) -> None:
    """u1 wrote ``n`` blocks in this topic at ``at``."""

    async def _run() -> None:
        async with client.test_factory() as s:
            topic = await s.get(Topic, uuid.UUID(topic_id))
            assert topic is not None
            for _ in range(n):
                s.add(
                    Block(
                        project_id=topic.project_id,
                        topic_id=topic.id,
                        kind=BlockKind.message,
                        author_type=author_type,
                        author="u1",
                        content="…",
                        created_at=at,
                    )
                )
            await s.commit()

    asyncio.run(_run())


def _noon(days_ago: int) -> datetime:
    """Noon UTC, ``days_ago`` days back: well inside that UTC day."""
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days_ago)


def _day(days_ago: int) -> str:
    return _noon(days_ago).date().isoformat()


def _profile(client, bearer, handle: str, *, viewer: str) -> dict:
    resp = client.get(f"/users/{handle}/profile", headers=bearer(viewer))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _topics(client, bearer, handle: str, *, viewer: str, **params) -> list[dict]:
    resp = client.get(f"/users/{handle}/topics", params=params, headers=bearer(viewer))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["topics"]


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
    _remember(client, p1, "u1", "擅长后端架构")
    _remember(client, p2, "u1", "喜欢先写测试")
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
    # Their own page gathers what every project's 芝士 holds about them, and
    # says which project each note came from.
    noted = {u["content"]: u["project_name"] for u in prof["understanding"]}
    assert noted == {"擅长后端架构": "P1", "喜欢先写测试": "P2"}


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


def test_activity_counts_only_projects_the_viewer_may_read(client, bearer):
    p1, p2 = _seed_u1_with_two_projects(client)
    add_external_member(client, p2, "u2", by="u1")
    in_p1 = _topic(client, p1, "甲")
    in_p2 = _topic(client, p2, "乙")
    _wrote(client, in_p1, at=_noon(0), n=2)
    _wrote(client, in_p1, at=_noon(400))  # more than a year ago
    _wrote(client, in_p2, at=_noon(10), n=3)
    # A platform event under u1's name is not something u1 wrote.
    _wrote(client, in_p2, at=_noon(0), author_type=AuthorType.platform)

    own = _profile(client, bearer, "u1", viewer="u1")
    days = {d["date"]: d["count"] for d in own["activity"]["days"]}
    assert len(days) == 365
    assert (days[_day(0)], days[_day(10)], own["activity"]["total"]) == (2, 3, 5)
    rows = {p["name"]: p for p in own["projects"]}
    assert rows["P1"]["contributions"] == 3
    assert datetime.fromisoformat(rows["P1"]["last_active_at"]) == _noon(0)
    # Twelve weeks, oldest first, the last one ending today.
    assert rows["P1"]["weekly"] == [0] * 11 + [2]
    assert rows["P2"]["weekly"] == [0] * 10 + [3, 0]

    # u2 reads P2 and not P1: none of P1 reaches their view of u1's year.
    shared = _profile(client, bearer, "u1", viewer="u2")
    days = {d["date"]: d["count"] for d in shared["activity"]["days"]}
    assert (days[_day(0)], days[_day(10)], shared["activity"]["total"]) == (0, 3, 3)

    stranger = _profile(client, bearer, "u1", viewer="u3")
    assert stranger["activity"]["total"] == 0


def test_topics_are_the_ones_the_viewer_may_open_and_never_a_private_chat(
    client, bearer
):
    p1, p2 = _seed_u1_with_two_projects(client)
    add_external_member(client, p2, "u2", by="u1")
    in_p1 = _topic(client, p1, "甲")
    in_p2 = _topic(client, p2, "乙")
    private = _private_chat(client, p2)
    _wrote(client, in_p1, at=_noon(3))
    _wrote(client, in_p2, at=_noon(2), n=2)
    _wrote(client, private, at=_noon(1))

    # Their own list: newest participation first, and without the private chat
    # — it is not part of any topic listing, theirs included.
    own = _topics(client, bearer, "u1", viewer="u1")
    assert [(t["title"], t["project_name"], t["contributions"]) for t in own] == [
        ("乙", "P2", 2),
        ("甲", "P1", 1),
    ]
    assert [t["title"] for t in _topics(client, bearer, "u1", viewer="u2")] == ["乙"]
    assert _topics(client, bearer, "u1", viewer="u3") == []
    assert client.get("/users/u1/topics").status_code == 401


def test_topics_in_a_date_range_count_only_that_range(client, bearer):
    p1, _ = _seed_u1_with_two_projects(client)
    first = _topic(client, p1, "甲")
    second = _topic(client, p1, "乙")
    _wrote(client, first, at=_noon(20), n=2)
    _wrote(client, second, at=_noon(20))
    _wrote(client, first, at=_noon(19))
    _wrote(client, second, at=_noon(5))

    def listed(since: int, until: int) -> list[tuple]:
        range_ = {"from": _day(since), "to": _day(until)}
        return [
            (t["title"], t["contributions"], t["last_participated_at"])
            for t in _topics(client, bearer, "u1", viewer="u1", **range_)
        ]

    assert sorted(listed(20, 20)) == [
        ("乙", 1, _noon(20).isoformat()),
        ("甲", 2, _noon(20).isoformat()),
    ]
    assert listed(19, 19) == [("甲", 1, _noon(19).isoformat())]
    assert listed(20, 19) == [
        ("甲", 3, _noon(19).isoformat()),
        ("乙", 1, _noon(20).isoformat()),
    ]
    backwards = client.get(
        "/users/u1/topics",
        params={"from": _day(19), "to": _day(20)},
        headers=bearer("u1"),
    )
    assert backwards.status_code == 422


def test_a_person_deletes_only_what_was_noted_about_them(client, bearer):
    p1, _ = _seed_u1_with_two_projects(client)
    _remember(client, p1, "u2", "u2 爱写文档")

    def notes(handle: str) -> dict[str, str]:
        understanding = _profile(client, bearer, handle, viewer=handle)["understanding"]
        return {u["content"]: u["id"] for u in understanding}

    about_u1 = notes("u1")["擅长后端架构"]
    about_u2 = notes("u2")["u2 爱写文档"]
    missing = client.delete(
        f"/users/me/understanding/{uuid.uuid4()}", headers=bearer("u1")
    )
    assert missing.status_code == 404

    # A note about someone else answers exactly as one that does not exist.
    for viewer, entry in (("u1", about_u2), ("u2", about_u1)):
        resp = client.delete(f"/users/me/understanding/{entry}", headers=bearer(viewer))
        assert (resp.status_code, resp.json()["message"]) == (
            404,
            missing.json()["message"],
        )
    assert "u2 爱写文档" in notes("u2")
    assert client.delete(f"/users/me/understanding/{about_u1}").status_code == 401

    resp = client.delete(f"/users/me/understanding/{about_u1}", headers=bearer("u1"))
    assert resp.status_code == 200, resp.text
    assert set(notes("u1")) == {"喜欢先写测试"}


def test_a_stealth_team_is_named_only_to_its_members(client, bearer):
    _seed_u1_with_two_projects(client)

    async def _teams() -> tuple[int, int]:
        async with client.test_factory() as s:
            public = await a_team(s, "u1")
            stealth = await a_team(s, "u1")
            team = await s.get(Team, stealth)
            assert team is not None
            team.visibility = TeamVisibility.STEALTH.value
            now = datetime.now(UTC)
            s.add(
                TeamUserRelation(
                    team_id=stealth,
                    user_id=await registered(s, "u4"),
                    role=TeamMemberRole.MEMBER,
                    created_at=now,
                    updated_at=now,
                )
            )
            await s.commit()
            return public, stealth

    public, stealth = asyncio.run(_teams())

    def seen_by(viewer: str) -> set[int]:
        teams = _profile(client, bearer, "u1", viewer=viewer)["teams"]
        return {t["id"] for t in teams}

    assert seen_by("u3") == {public}
    assert seen_by("u4") == {public, stealth}
    # Their own page: both, and not the personal team their projects live in.
    assert seen_by("u1") == {public, stealth}
