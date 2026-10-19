"""A person may refuse that their content be used for training or given to partners
(privacy policy 1.2), by writing to ops@. A platform admin records the answer on
their account; nobody else reads or changes it."""

import pytest

from app.core.config import settings
from tests.integration.conftest import session_auth_headers

ADMIN = "tu-admin"


def _bearer(api_client, person) -> dict:
    resp = api_client.post(
        "/users/auth/login",
        json={"username": person.username, "password": person.password},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return session_auth_headers(ADMIN)


def _read(api_client, headers, handle):
    return api_client.get(f"/admin/users/{handle}/training-use", headers=headers)


def _write(api_client, headers, handle, allowed: bool):
    return api_client.put(
        f"/admin/users/{handle}/training-use",
        json={"allowed": allowed},
        headers=headers,
    )


def test_a_new_account_has_not_opted_out(api_client, user_client, admin):
    person = user_client.create_user()
    resp = _read(api_client, admin, person.username)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["allowed"] is True


def test_an_emailed_opt_out_stays_on_the_account_and_can_be_undone(
    api_client, user_client, admin
):
    person = user_client.create_user()
    assert _write(api_client, admin, person.username, False).status_code == 200
    data = _read(api_client, admin, person.username).json()["data"]
    assert data["allowed"] is False and data["opted_out_at"]

    assert _write(api_client, admin, person.username, True).status_code == 200
    assert _read(api_client, admin, person.username).json()["data"]["allowed"] is True


def test_nobody_but_an_admin_reads_or_changes_it(api_client, user_client, admin):
    person, other = user_client.create_user(), user_client.create_user()
    for headers in (_bearer(api_client, other), _bearer(api_client, person)):
        assert _read(api_client, headers, person.username).status_code in (401, 403)
        assert _write(api_client, headers, person.username, False).status_code in (
            401,
            403,
        )
    assert _read(api_client, admin, person.username).json()["data"]["allowed"] is True


def test_it_is_not_shown_on_the_account_itself(api_client, user_client):
    person = user_client.create_user()
    me = api_client.get("/users/me", headers=_bearer(api_client, person))
    assert me.status_code == 200, me.text
    assert "trainingAllowed" not in me.json()["data"]["user"]
