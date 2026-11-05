"""A space is joined through its link, end to end.

Each space has one join link, the shape a team's has: whoever holds it sees
which space it names and joins it — at once, or, while the space's owner has
switched approval on, by a request an owner or admin approves or rejects.
Resetting the link makes the old one answer like a space that does not exist;
the people already in stay. A link that names no live, reviewed space answers
404, the same as a space the reader may not see.
"""

import secrets

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import Space
from tests.integration.conftest import UserCreator, create_approved_space, unique_int


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _person(user_client: UserCreator, api_client: TestClient):
    user = user_client.create_user()
    return user, user_client.login(api_client, user.username, user.password)


def _space(api_client: TestClient, token: str) -> int:
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Course ({unique_int(10000000, 99999999)})",
            "intro": "第七周的练习都在这里。",
            "description": "",
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["space"]["id"]


def _link(api_client: TestClient, token: str, space_id: int) -> dict:
    resp = api_client.get(f"/spaces/{space_id}/join-link", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _sees(api_client: TestClient, token: str, space_id: int) -> bool:
    status = api_client.get(f"/spaces/{space_id}", headers=_auth(token)).status_code
    assert status in (200, 404), status
    return status == 200


def _requests(api_client: TestClient, token: str, space_id: int) -> list[dict]:
    resp = api_client.get(f"/spaces/{space_id}/join-requests", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["requests"]


def test_holding_the_link_shows_the_space_and_joining_lets_you_in(
    user_client: UserCreator, api_client: TestClient
):
    _, owner = _person(user_client, api_client)
    _, student = _person(user_client, api_client)
    space_id = _space(api_client, owner)
    link = _link(api_client, owner, space_id)
    assert link["token"] and link["approval"] is False

    shown = api_client.get(f"/space-invites/{link['token']}", headers=_auth(student))
    assert shown.status_code == 200, shown.text
    body = shown.json()["data"]
    assert body["space"]["id"] == space_id
    assert body["space"]["intro"] == "第七周的练习都在这里。"
    assert body["joinApproval"] is False
    assert body["joinStatus"] == "none"
    # Opening the link joins nobody.
    assert not _sees(api_client, student, space_id)

    joined = api_client.post(
        f"/space-invites/{link['token']}/join", headers=_auth(student)
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["data"]["joinStatus"] == "member"
    assert _sees(api_client, student, space_id)

    # Joining again is where they already stand, not an error.
    again = api_client.post(
        f"/space-invites/{link['token']}/join", headers=_auth(student)
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["joinStatus"] == "member"


def test_with_approval_on_joining_waits_until_an_owner_answers(
    user_client: UserCreator, api_client: TestClient
):
    _, owner = _person(user_client, api_client)
    accepted, accepted_token = _person(user_client, api_client)
    refused, refused_token = _person(user_client, api_client)
    space_id = _space(api_client, owner)
    switched = api_client.patch(
        f"/spaces/{space_id}/join-link", json={"approval": True}, headers=_auth(owner)
    )
    assert switched.status_code == 200, switched.text
    token = switched.json()["data"]["token"]
    assert switched.json()["data"]["approval"] is True

    for who in (accepted_token, refused_token):
        asked = api_client.post(f"/space-invites/{token}/join", headers=_auth(who))
        assert asked.status_code == 200, asked.text
        assert asked.json()["data"]["joinStatus"] == "pending"
        # Asking twice is still one request.
        api_client.post(f"/space-invites/{token}/join", headers=_auth(who))
        assert not _sees(api_client, who, space_id)
    shown = api_client.get(f"/space-invites/{token}", headers=_auth(accepted_token))
    assert shown.json()["data"]["joinStatus"] == "pending"
    assert shown.json()["data"]["joinApproval"] is True

    waiting = {r["userId"]: r for r in _requests(api_client, owner, space_id)}
    assert set(waiting) == {accepted.user_id, refused.user_id}
    assert waiting[accepted.user_id]["user"] is not None

    approve = api_client.post(
        f"/spaces/{space_id}/join-requests/{waiting[accepted.user_id]['id']}/approve",
        headers=_auth(owner),
    )
    assert approve.status_code == 204, approve.text
    reject = api_client.post(
        f"/spaces/{space_id}/join-requests/{waiting[refused.user_id]['id']}/reject",
        headers=_auth(owner),
    )
    assert reject.status_code == 204, reject.text

    assert _sees(api_client, accepted_token, space_id)
    assert not _sees(api_client, refused_token, space_id)
    assert _requests(api_client, owner, space_id) == []
    after = api_client.get(f"/space-invites/{token}", headers=_auth(refused_token))
    assert after.json()["data"]["joinStatus"] == "none"
    # An answered request cannot be answered again.
    twice = api_client.post(
        f"/spaces/{space_id}/join-requests/{waiting[refused.user_id]['id']}/approve",
        headers=_auth(owner),
    )
    assert twice.status_code == 404, twice.text
    assert not _sees(api_client, refused_token, space_id)


def test_resetting_the_link_shuts_the_old_one_and_keeps_the_members(
    user_client: UserCreator, api_client: TestClient
):
    _, owner = _person(user_client, api_client)
    _, member = _person(user_client, api_client)
    _, latecomer = _person(user_client, api_client)
    space_id = _space(api_client, owner)
    old = _link(api_client, owner, space_id)["token"]
    assert (
        api_client.post(f"/space-invites/{old}/join", headers=_auth(member))
    ).status_code == 200

    reset = api_client.post(f"/spaces/{space_id}/join-link/reset", headers=_auth(owner))
    assert reset.status_code == 200, reset.text
    new = reset.json()["data"]["token"]
    assert new != old
    assert _link(api_client, owner, space_id)["token"] == new

    assert (
        api_client.get(f"/space-invites/{old}", headers=_auth(latecomer))
    ).status_code == 404
    assert (
        api_client.post(f"/space-invites/{old}/join", headers=_auth(latecomer))
    ).status_code == 404
    assert not _sees(api_client, latecomer, space_id)
    assert _sees(api_client, member, space_id)

    assert (
        api_client.post(f"/space-invites/{new}/join", headers=_auth(latecomer))
    ).status_code == 200
    assert _sees(api_client, latecomer, space_id)


def test_a_link_that_names_no_space_is_a_404(
    user_client: UserCreator, api_client: TestClient
):
    _, someone = _person(user_client, api_client)
    made_up = secrets.token_urlsafe(32)
    for resp in (
        api_client.get(f"/space-invites/{made_up}", headers=_auth(someone)),
        api_client.post(f"/space-invites/{made_up}/join", headers=_auth(someone)),
    ):
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["name"] == "NotFoundError", resp.text


def test_a_link_lets_nobody_into_a_space_review_has_not_cleared(
    user_client: UserCreator,
    api_client: TestClient,
    db_session: AsyncSession,
    _portal,
):
    """A link handed out while the space was approved stops working once it
    goes back to review, and answers like a space that does not exist."""
    _, owner = _person(user_client, api_client)
    _, outsider = _person(user_client, api_client)
    space_id = _space(api_client, owner)
    token = _link(api_client, owner, space_id)["token"]

    async def back_to_review() -> None:
        await db_session.execute(
            update(Space).where(Space.id == space_id).values(review_status="PENDING")
        )
        await db_session.flush()

    _portal.call(back_to_review)
    joined = api_client.post(f"/space-invites/{token}/join", headers=_auth(outsider))
    assert joined.status_code == 404, joined.text
    assert "reviewStatus" not in joined.text


def test_only_the_spaces_managers_handle_its_link_and_requests(
    user_client: UserCreator, api_client: TestClient
):
    _, owner = _person(user_client, api_client)
    _, member = _person(user_client, api_client)
    _, outsider = _person(user_client, api_client)
    space_id = _space(api_client, owner)
    token = _link(api_client, owner, space_id)["token"]
    api_client.post(f"/space-invites/{token}/join", headers=_auth(member))

    for who, expected in ((member, 403), (outsider, 404)):
        for method, path, body in (
            ("GET", "/join-link", None),
            ("PATCH", "/join-link", {"approval": True}),
            ("POST", "/join-link/reset", None),
            ("GET", "/join-requests", None),
        ):
            resp = api_client.request(
                method, f"/spaces/{space_id}{path}", json=body, headers=_auth(who)
            )
            assert resp.status_code == expected, f"{method} {path}: {resp.text}"
    # Nothing they tried changed the link.
    assert _link(api_client, owner, space_id) == {"token": token, "approval": False}


def test_nobody_signed_in_reads_or_joins_through_a_link(
    user_client: UserCreator, api_client: TestClient
):
    _, owner = _person(user_client, api_client)
    token = _link(api_client, owner, _space(api_client, owner))["token"]
    assert api_client.get(f"/space-invites/{token}").status_code == 401
    assert api_client.post(f"/space-invites/{token}/join").status_code == 401
