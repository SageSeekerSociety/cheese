"""A person may refuse that their content be used for training or given to partners
(privacy policy 1.2): their answer stays on the account, only they or a platform
admin change it, and nobody else reads it."""

import pytest

from app.core.config import settings
from tests.integration.conftest import session_auth_headers

ADMIN = "tu-admin"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _sign_in(api_client, person) -> dict:
    resp = api_client.post(
        "/users/auth/login",
        json={"username": person.username, "password": person.password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _allowed(api_client, token: str) -> bool:
    me = api_client.get("/users/me", headers=_headers(token))
    assert me.status_code == 200, me.text
    return me.json()["data"]["user"]["trainingAllowed"]


def _set(api_client, token: str, allowed: bool):
    return api_client.put(
        "/users/me/training-use", json={"allowed": allowed}, headers=_headers(token)
    )


def test_a_new_account_has_not_opted_out(api_client, user_client):
    token = _sign_in(api_client, user_client.create_user())["accessToken"]
    assert _allowed(api_client, token) is True


def test_opting_out_stays_on_the_account_and_can_be_undone(api_client, user_client):
    person = user_client.create_user()
    token = _sign_in(api_client, person)["accessToken"]

    assert _set(api_client, token, False).status_code == 200
    assert _sign_in(api_client, person)["user"]["trainingAllowed"] is False

    assert _set(api_client, token, True).status_code == 200
    assert _allowed(api_client, token) is True


def test_someone_else_neither_reads_nor_changes_it(api_client, user_client):
    alice, bob = user_client.create_user(), user_client.create_user()
    alice_token = _sign_in(api_client, alice)["accessToken"]
    bob_token = _sign_in(api_client, bob)["accessToken"]
    _set(api_client, alice_token, False)

    seen = api_client.get(f"/users/{alice.user_id}", headers=_headers(bob_token))
    assert seen.status_code == 200, seen.text
    assert "trainingAllowed" not in seen.json()["data"]["user"]

    # Bob's write lands on Bob's own account and nowhere else.
    _set(api_client, bob_token, True)
    assert _allowed(api_client, alice_token) is False


def test_an_admin_records_an_emailed_opt_out(
    api_client, user_client, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    person = user_client.create_user()
    token = _sign_in(api_client, person)["accessToken"]

    resp = api_client.put(
        f"/admin/users/{person.username}/training-use",
        json={"allowed": False},
        headers=session_auth_headers(ADMIN),
    )
    assert resp.status_code == 200, resp.text
    assert _allowed(api_client, token) is False


def test_only_an_admin_records_it_for_someone_else(api_client, user_client):
    person, other = user_client.create_user(), user_client.create_user()
    token = _sign_in(api_client, person)["accessToken"]

    resp = api_client.put(
        f"/admin/users/{person.username}/training-use",
        json={"allowed": False},
        headers=_headers(_sign_in(api_client, other)["accessToken"]),
    )
    assert resp.status_code in (401, 403), resp.text
    assert _allowed(api_client, token) is True
