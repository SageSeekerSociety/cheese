"""A person can close their own account (注销), and only their own.

Once closed, nothing signs in as them again — not their password, not a
refresh of an old sign-in, not an access token issued before — while what
they wrote in shared rooms stays, and is recognisably a closed account's.
Their email address is free to register again; their handle is not, because
rooms and rosters still name it."""

import uuid

import pytest
from sqlalchemy import select

from app.core.redis import get_redis_client
from app.domain.block.models import Block
from tests.integration.conftest import post_project, sign_in
from tests.integration.test_account_uniqueness import _arm_email_code
from tests.support.consent import SIGNUP_CONSENT

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _forget_closings(client):
    """User ids start over in every test's rolled-back transaction, so a
    closing recorded by one test would otherwise refuse the next test's
    account that got the same id within the same second."""
    yield

    async def forget() -> None:
        redis = get_redis_client()
        if redis is not None:
            async for key in redis.scan_iter(match="account-closed:*"):
                await redis.delete(key)

    client.portal.call(forget)


def _handle(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ticket(client, token: str) -> str:
    r = client.post(
        "/users/auth/sudo",
        headers=_auth(token),
        json={
            "method": "password",
            "credentials": {"password": PASSWORD},
            "purpose": "account:delete",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["sudoTicket"]


def _close(client, token: str, *, username: str, ticket: str | None):
    body: dict = {"username": username}
    if ticket is not None:
        body["sudoTicket"] = ticket
    return client.request("DELETE", "/users/me", headers=_auth(token), json=body)


def _can_sign_in(client, handle: str) -> bool:
    r = client.post(
        "/users/auth/login", json={"username": handle, "password": PASSWORD}
    )
    return r.status_code == 200


def test_a_closed_account_signs_in_by_no_means(client):
    person = sign_in(client, _handle("closer"))

    closed = _close(
        client,
        person.token,
        username=person.handle,
        ticket=_ticket(client, person.token),
    )

    assert closed.status_code == 200, closed.text
    assert not _can_sign_in(client, person.handle)
    refreshed = client.post(
        "/users/auth/refresh-token",
        headers={"Cookie": f"cheese_refresh={person.refresh}"},
    )
    assert refreshed.status_code != 200, refreshed.text
    # The access token it held before is still within its lifetime.
    assert client.get("/users/me", headers=_auth(person.token)).status_code == 401


def test_its_email_registers_again_but_its_handle_does_not(client):
    person = sign_in(client, _handle("reuse"))
    email = f"{person.handle}@example.com"
    assert (
        _close(
            client,
            person.token,
            username=person.handle,
            ticket=_ticket(client, person.token),
        ).status_code
        == 200
    )

    def signup(username: str, email: str):
        payload = {
            "username": username,
            "nickname": username,
            "email": email,
            "password": "TestPassword123!",
            "consent": SIGNUP_CONSENT,
        }
        payload["emailCode"] = client.portal.call(_arm_email_code, email)
        return client.post("/users", json=payload)

    assert signup(person.handle, f"{person.handle}@example.org").status_code == 409
    fresh = signup(_handle("newcomer"), email)
    assert fresh.status_code == 200, fresh.text


def test_what_it_wrote_in_a_shared_room_stays_and_reads_as_closed(client):
    author = sign_in(client, _handle("writer"))
    reader = sign_in(client, _handle("reader"))
    project = post_project(
        client, json={"name": "Shared"}, headers=_auth(author.token)
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=_auth(author.token),
    ).json()["data"]
    sent = client.post(
        f"/topics/{room['id']}/messages",
        json={"content": "留给大家的话", "request_id": str(uuid.uuid4())},
        headers=_auth(author.token),
    )
    assert sent.status_code == 200, sent.text

    assert (
        _close(
            client,
            author.token,
            username=author.handle,
            ticket=_ticket(client, author.token),
        ).status_code
        == 200
    )

    async def kept() -> list[str]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Block.content).where(Block.author == author.handle)
            )
            return [str(c) for c in rows]

    assert any("留给大家的话" in c for c in client.portal.call(kept))
    closed = client.get(
        "/closed-accounts",
        params={"handles": f"{author.handle},{reader.handle}"},
        headers=_auth(reader.token),
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["data"]["handles"] == [author.handle]


def test_nobody_closes_someone_elses_account(client):
    owner = sign_in(client, _handle("owner"))
    other = sign_in(client, _handle("other"))

    typed_theirs = _close(
        client, other.token, username=owner.handle, ticket=_ticket(client, other.token)
    )
    via_admin = client.request(
        "DELETE", f"/admin/users/{owner.handle}", headers=_auth(other.token)
    )

    assert typed_theirs.status_code == 422, typed_theirs.text
    assert via_admin.status_code == 403, via_admin.text
    assert _can_sign_in(client, owner.handle)
    assert _can_sign_in(client, other.handle)


def test_backing_out_closes_nothing(client):
    person = sign_in(client, _handle("hesitant"))

    unconfirmed = _close(client, person.token, username=person.handle, ticket=None)
    mistyped = _close(
        client,
        person.token,
        username="someone-else",
        ticket=_ticket(client, person.token),
    )

    assert unconfirmed.status_code == 403, unconfirmed.text
    assert mistyped.status_code == 422, mistyped.text
    assert _can_sign_in(client, person.handle)
    assert client.get("/users/me", headers=_auth(person.token)).status_code == 200


def test_an_admin_cannot_close_an_agents_account(client, monkeypatch):
    from app.core.config import settings
    from app.domain.identity.services import IdentityService
    from app.domain.machine.models import HOST_OWNER

    admin = sign_in(client, _handle("ops"))
    monkeypatch.setattr(settings, "platform_admin_handles", [admin.handle])

    async def ensure_host_pool() -> None:
        async with client.test_factory() as session:
            await IdentityService(session).ensure_agent_user(handle=HOST_OWNER)
            await session.commit()

    client.portal.call(ensure_host_pool)

    refused = client.request(
        "DELETE", f"/admin/users/{HOST_OWNER}", headers=_auth(admin.token)
    )

    assert refused.status_code == 400, refused.text
    still = client.get(
        "/closed-accounts",
        params={"handles": HOST_OWNER},
        headers=_auth(admin.token),
    )
    assert still.json()["data"]["handles"] == []


def test_a_closed_readers_entries_stay_in_the_real_name_access_log(client):
    from app.domain.user.models import User
    from app.domain.user.repositories import UserRealNameRepository

    target = sign_in(client, _handle("target"))
    gone = sign_in(client, _handle("gone-reader"))
    stays = sign_in(client, _handle("live-reader"))

    async def ids() -> dict[str, int]:
        async with client.test_factory() as session:
            rows = await session.execute(
                select(User.username, User.id).where(
                    User.username.in_([target.handle, gone.handle, stays.handle])
                )
            )
            return dict(rows.all())

    by_handle = client.portal.call(ids)

    async def read_by(*readers: str) -> None:
        async with client.test_factory() as session:
            repo = UserRealNameRepository(session)
            for reader in readers:
                await repo.create_access_log(
                    accessor_id=by_handle[reader],
                    target_id=by_handle[target.handle],
                    access_reason="roster",
                    ip_address="127.0.0.1",
                    access_type="EXPORT",
                )
            await session.commit()

    # Oldest first: the live reader's entry sits behind the closed one's.
    client.portal.call(read_by, stays.handle, gone.handle, gone.handle)
    assert (
        _close(
            client, gone.token, username=gone.handle, ticket=_ticket(client, gone.token)
        ).status_code
        == 200
    )

    def page(size: int, start: int | None = None) -> dict:
        params: dict = {"pageSize": size}
        if start is not None:
            params["pageStart"] = start
        r = client.get(
            f"/users/{by_handle[target.handle]}/identity/access-logs",
            params=params,
            headers=_auth(target.token),
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]

    whole = page(20)
    assert [e["accessor"]["username"] for e in whole["logs"]] == [
        gone.handle,
        gone.handle,
        stays.handle,
    ]
    assert whole["page"]["total"] == 3
    assert not whole["page"]["hasMore"]

    first = page(2)
    assert len(first["logs"]) == 2
    assert first["page"]["hasMore"]
    rest = page(2, first["page"]["nextStart"])
    assert [e["accessor"]["username"] for e in rest["logs"]] == [stays.handle]


def _shared_team(client, owner: str, *others: str) -> tuple[int, dict[str, int]]:
    """A shared team ``owner`` owns with ``others`` as members; its id, and
    each person's user id."""
    from datetime import UTC, datetime

    from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation
    from tests.integration.conftest import registered

    async def make() -> tuple[int, dict[str, int]]:
        async with client.test_factory() as session:
            now = datetime.now(UTC)
            team = Team(
                name=f"Lab {uuid.uuid4().hex[:6]}",
                handle=f"t-{uuid.uuid4().hex[:12]}",
                intro="",
                description="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
            session.add(team)
            await session.flush()
            ids = {h: await registered(session, h) for h in (owner, *others)}
            for handle, user_id in ids.items():
                role = (
                    TeamMemberRole.OWNER if handle == owner else TeamMemberRole.MEMBER
                )
                session.add(
                    TeamUserRelation(
                        team_id=team.id,
                        user_id=user_id,
                        role=role,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await session.commit()
            return team.id, ids

    return client.portal.call(make)


def _owner_of(client, team_id: int) -> list[int]:
    from app.domain.team.models import TeamMemberRole, TeamUserRelation

    async def read() -> list[int]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(TeamUserRelation.user_id).where(
                    TeamUserRelation.team_id == team_id,
                    TeamUserRelation.role == TeamMemberRole.OWNER,
                    TeamUserRelation.deleted_at.is_(None),
                )
            )
            return list(rows)

    return client.portal.call(read)


def test_the_only_owner_of_a_shared_team_must_hand_it_on_first(client):
    owner = sign_in(client, _handle("lead"))
    member = sign_in(client, _handle("member"))
    team_id, ids = _shared_team(client, owner.handle, member.handle)

    refused = _close(
        client, owner.token, username=owner.handle, ticket=_ticket(client, owner.token)
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "accountCloseOwnsTeams"
    assert "Lab " in refused.json()["error"]["message"]  # names the team
    assert _can_sign_in(client, owner.handle)
    assert _owner_of(client, team_id) == [ids[owner.handle]]

    handed = client.put(
        f"/teams/{team_id}/owner",
        json={"userId": ids[member.handle]},
        headers=_auth(owner.token),
    )
    assert handed.status_code == 200, handed.text
    closed = _close(
        client, owner.token, username=owner.handle, ticket=_ticket(client, owner.token)
    )

    assert closed.status_code == 200, closed.text
    assert _owner_of(client, team_id) == [ids[member.handle]]


def test_the_admin_route_refuses_the_only_owner_of_a_shared_team(client, monkeypatch):
    from app.core.config import settings

    admin = sign_in(client, _handle("ops"))
    monkeypatch.setattr(settings, "platform_admin_handles", [admin.handle])
    owner = sign_in(client, _handle("lead"))
    member = sign_in(client, _handle("member"))
    team_id, ids = _shared_team(client, owner.handle, member.handle)

    refused = client.request(
        "DELETE", f"/admin/users/{owner.handle}", headers=_auth(admin.token)
    )

    assert refused.status_code == 409, refused.text
    assert _can_sign_in(client, owner.handle)
    assert _owner_of(client, team_id) == [ids[owner.handle]]


def test_a_personal_team_or_a_team_of_one_does_not_hold_closing(client):
    from app.domain.team.repositories import TeamRepository
    from app.domain.team.services import TeamService
    from app.domain.user.models import User

    person = sign_in(client, _handle("solo"))
    _shared_team(client, person.handle)

    async def personal_team() -> None:
        async with client.test_factory() as session:
            user_id = await session.scalar(
                select(User.id).where(User.username == person.handle)
            )
            await TeamService(TeamRepository(session)).ensure_personal_team(user_id)
            await session.commit()

    client.portal.call(personal_team)

    closed = _close(
        client,
        person.token,
        username=person.handle,
        ticket=_ticket(client, person.token),
    )

    assert closed.status_code == 200, closed.text


def _user_id(client, handle: str) -> int:
    from app.domain.user.models import User

    async def read() -> int:
        async with client.test_factory() as session:
            return await session.scalar(select(User.id).where(User.username == handle))

    return client.portal.call(read)


def _close_with_ticket(client, person):
    return _close(
        client,
        person.token,
        username=person.handle,
        ticket=_ticket(client, person.token),
    )


def test_a_pending_invitation_holds_closing_until_it_is_cancelled(client):
    """Alone in the team, but someone could still accept their way in: closing
    first would leave them in a team nobody can ever manage."""
    owner = sign_in(client, _handle("inviter"))
    invited = sign_in(client, _handle("invited"))
    team_id, _ = _shared_team(client, owner.handle)
    invitation = client.post(
        f"/teams/{team_id}/invitations",
        json={"userId": _user_id(client, invited.handle)},
        headers=_auth(owner.token),
    )
    assert invitation.status_code == 201, invitation.text

    refused = _close_with_ticket(client, owner)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "accountCloseOwnsTeams"
    assert _can_sign_in(client, owner.handle)

    invitation_id = invitation.json()["data"]["invitation"]["id"]
    cancelled = client.delete(
        f"/teams/{team_id}/invitations/{invitation_id}", headers=_auth(owner.token)
    )
    assert cancelled.status_code in (200, 204), cancelled.text
    assert _close_with_ticket(client, owner).status_code == 200


def test_joining_without_approval_holds_closing_until_approval_is_back(client):
    owner = sign_in(client, _handle("opener"))
    team_id, _ = _shared_team(client, owner.handle)

    def approval(on: bool) -> None:
        r = client.patch(
            f"/teams/{team_id}/join-link",
            json={"approval": on},
            headers=_auth(owner.token),
        )
        assert r.status_code == 200, r.text

    approval(False)
    refused = _close_with_ticket(client, owner)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "accountCloseOwnsTeams"
    assert _can_sign_in(client, owner.handle)

    approval(True)
    assert _close_with_ticket(client, owner).status_code == 200
