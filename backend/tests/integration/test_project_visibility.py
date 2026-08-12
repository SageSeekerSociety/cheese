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


def test_an_unidentifiable_caller_gets_none_not_all(client):
    """认不出人 ≠ 认识所有人。

    This asserted the opposite until 2026-08-12, on the argument that every 2.0
    route is reachable without a credential anyway, so tightening one protects
    nothing — and it said, in as many words, that it should fail loudly on the
    day that surface was tightened. It was tightened for a reason it did not
    anticipate.

    The caller that lands here is not an anonymous stranger browsing. It is a
    LOGGED-IN user whose token just lapsed: the 2.0 access token lives about
    three minutes, and the fetch layer that carries it has no refresh (raw
    `fetch`, so the axios 401 interceptor never sees it). Measured on dev, one
    browser, one second: a valid token returned 1 project, `Bearer not.a.jwt`
    returned 12 — four other people's among them. The route answers 200 either
    way, so the client cannot tell "mine" from "everyone's" and cached the leak
    under the user's own handle.

    Whatever else is open, this route's meaning without `team_id` is "the
    caller's OWN projects". With no caller, the honest answer is none.
    """
    client.post("/api/projects", json={"name": "任何人的项目"})
    body = client.get("/api/projects").json()["data"]
    assert body["total"] == 0
    assert body["data"] == []


def test_a_team_id_filter_still_answers_for_that_team(client):
    r = client.get("/api/projects?team_id=999999")
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 0
