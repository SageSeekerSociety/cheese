"""Who sees which projects.

The unscoped listing handed every project to every caller. That survives five
projects and breaks the moment a class arrives: a student would find every other
team's work — and every piece of debugging debris — in their own sidebar.

The scoping lives in the repository, and that is where it is tested: the
integration client here is anonymous, so driving it over HTTP would exercise the
unauthenticated path and prove nothing about the scoped one.
"""

from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.repositories import ProjectRepository


def test_scoping_covers_owner_roster_and_team(
    db_session: AsyncSession, _portal: BlockingPortal
):
    async def _run() -> None:
        from app.domain.membership.repositories import MemberRepository
        from app.domain.project.models import ProjectRole

        repo = ProjectRepository(db_session)
        mine = await repo.add(name="我拥有的", owner_handle="alice")
        rostered = await repo.add(name="我在名册上的", owner_handle="bob")
        theirs = await repo.add(name="别人的", owner_handle="bob")
        await MemberRepository(db_session).add(
            project_id=rostered.id, user_handle="alice", role=ProjectRole.member
        )

        seen = {
            p.name for p in await repo.list_visible_to(handle="alice", user_id=None)
        }
        assert "我拥有的" in seen
        assert "我在名册上的" in seen, "being on the roster is a claim"
        assert "别人的" not in seen, "a stranger's project is not mine to see"
        assert theirs.name == "别人的"
        assert mine.owner_handle == "alice"

    _portal.call(_run)


def test_nobody_identifiable_claims_nothing(
    db_session: AsyncSession, _portal: BlockingPortal
):
    """With neither handle nor user, the answer is empty — not everything.

    The route decides separately what to do for an anonymous CALLER (see the
    test below); the repository must never treat "no one asked" as "show all".
    """

    async def _run() -> None:
        repo = ProjectRepository(db_session)
        await repo.add(name="某人的项目", owner_handle="someone")
        assert await repo.list_visible_to(handle=None, user_id=None) == []

    _portal.call(_run)


def test_the_unauthenticated_surface_is_deliberately_unchanged(client):
    """Scoping applies to people, and an anonymous caller is not one.

    Every 2.0 route on this deployment is reachable without a credential
    (handle-fallback, Phase 0), so making the sidebar the one exception would
    protect nothing — a caller could simply not authenticate. Asserted so the
    choice is visible rather than accidental, and so it fails loudly on the day
    that surface is tightened as a whole.
    """
    client.post("/api/projects", json={"name": "任何人的项目"})
    assert client.get("/api/projects").json()["data"]["total"] >= 1


def test_a_team_id_filter_still_answers_for_that_team(client):
    r = client.get("/api/projects?team_id=999999")
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 0
