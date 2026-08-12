"""The invite-code endpoints are admin-only — and now actually enforce it.

Regression for the hole where all three routes carried "(admin)" in their
summary but only ``require_auth_user``: any logged-in user could read every
code in plaintext (defeating the invite-only signup gate), mint unlimited
codes, or deactivate somebody else's.

The three cases that matter, per endpoint: anonymous → 401, an ordinary logged-in
user → **403** (the core of this regression), a handle listed in
``settings.admin_handles`` → 2xx. Plus fail-closed: an empty roster denies
everyone, including a user who would otherwise look privileged.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

from .conftest import CreatedUser, UserCreator

LIST = ("GET", "/users/invite-codes")
CREATE = ("POST", "/users/invite-codes")
DEACTIVATE = ("DELETE", "/users/invite-codes/1")
ALL_ENDPOINTS = [LIST, CREATE, DEACTIVATE]


def _call(
    client: TestClient,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
):
    kwargs: dict = {"headers": headers or {}}
    if method == "POST":
        kwargs["json"] = {"maxUses": 1, "note": "pytest"}
    return client.request(method, path, **kwargs)


@pytest.fixture
def admin_user(
    user_client: UserCreator,
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> CreatedUser:
    """A real DB user whose handle is in the configured admin roster."""
    user = user_client.create_user()
    monkeypatch.setattr(settings, "admin_handles", [user.username])
    user.token = user_client.login(api_client, user.username, user.password)
    return user


@pytest.mark.parametrize(("method", "path"), ALL_ENDPOINTS)
def test_invite_code_endpoints_reject_anonymous(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    # Roster non-empty on purpose: the 401 must come from "no credentials",
    # not from the fail-closed branch that would 403 everyone anyway.
    monkeypatch.setattr(settings, "admin_handles", ["somebody"])

    response = _call(api_client, method, path)

    assert response.status_code == 401, response.text


@pytest.mark.parametrize(("method", "path"), ALL_ENDPOINTS)
def test_invite_code_endpoints_reject_plain_logged_in_user(
    api_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    """The regression itself: authenticated but not on the roster → 403, not 200."""
    monkeypatch.setattr(settings, "admin_handles", ["someone-who-is-not-this-user"])

    response = _call(api_client, method, path, auth_headers)

    assert response.status_code == 403, response.text


@pytest.mark.parametrize(("method", "path"), ALL_ENDPOINTS)
def test_invite_code_endpoints_deny_everyone_when_roster_is_empty(
    api_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    """Fail closed: unconfigured roster means nobody is an admin — never everybody."""
    monkeypatch.setattr(settings, "admin_handles", [])

    response = _call(api_client, method, path, auth_headers)

    assert response.status_code == 403, response.text


def test_configured_admin_can_create_list_and_deactivate(
    api_client: TestClient,
    admin_user: CreatedUser,
) -> None:
    headers = {"Authorization": f"Bearer {admin_user.token}"}

    # HTTP 200 with an envelope code of 201 — the route sets no status_code, which
    # is this API's house style, not a bug to fix under this change.
    created = _call(api_client, *CREATE, headers)
    assert created.status_code == 200, created.text
    assert created.json()["code"] == 201
    code_id = created.json()["data"]["id"]

    listed = _call(api_client, *LIST, headers)
    assert listed.status_code == 200, listed.text
    assert code_id in [c["id"] for c in listed.json()["data"]["codes"]]

    deactivated = _call(api_client, "DELETE", f"/users/invite-codes/{code_id}", headers)
    assert deactivated.status_code == 200, deactivated.text

    after = _call(api_client, *LIST, headers)
    entry = next(c for c in after.json()["data"]["codes"] if c["id"] == code_id)
    assert entry["isActive"] is False


def test_admin_roster_matches_on_handle_not_substring(
    api_client: TestClient,
    authenticated_user: CreatedUser,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handle that merely contains an admin's handle is not that admin."""
    monkeypatch.setattr(settings, "admin_handles", [authenticated_user.username[:6]])

    response = _call(api_client, *LIST, auth_headers)

    assert response.status_code == 403, response.text
