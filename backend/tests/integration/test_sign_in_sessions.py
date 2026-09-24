"""A sign-in is a session the server keeps, and its refresh token rotates (#1481).

Everything here goes through the API a browser uses: sign in, refresh with
the cookie the server set, read the device list, end a session. The cookie is
passed by hand because the TestClient talks to the backend without the
gateway in front, and the cookie's path is the one the browser sees.
"""

import re
import time
from http.cookies import SimpleCookie

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.integration.conftest import CreatedUser, UserCreator

COOKIE = "cheese_refresh"
REFRESH = "/users/auth/refresh-token"


def _cookies(resp) -> SimpleCookie:
    jar: SimpleCookie = SimpleCookie()
    for header in resp.headers.get_list("set-cookie"):
        jar.load(header)
    return jar


def _refresh_token(resp) -> str | None:
    morsel = _cookies(resp).get(COOKIE)
    return morsel.value if morsel is not None and morsel.value else None


class SignIn:
    def __init__(self, access: str, refresh: str) -> None:
        self.access = access
        self.refresh = refresh

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


def _sign_in(client: TestClient, user: CreatedUser) -> SignIn:
    resp = client.post(
        "/users/auth/login",
        json={"username": user.username, "password": user.password},
    )
    assert resp.status_code == 200, resp.text
    refresh = _refresh_token(resp)
    assert refresh, resp.headers.get_list("set-cookie")
    return SignIn(resp.json()["data"]["accessToken"], refresh)


def _refresh(client: TestClient, token: str):
    return client.post(REFRESH, headers={"Cookie": f"{COOKIE}={token}"})


def _rotate(client: TestClient, sign_in: SignIn) -> SignIn:
    """Refresh and follow the rotation, as a browser tab would."""
    resp = _refresh(client, sign_in.refresh)
    assert resp.status_code == 200, resp.text
    successor = _refresh_token(resp)
    assert successor, "a refresh must hand out a new refresh token"
    return SignIn(resp.json()["data"]["accessToken"], successor)


def _sessions(client: TestClient, sign_in: SignIn) -> list[dict]:
    resp = client.get("/users/me/sessions", headers=sign_in.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["sessions"]


@pytest.fixture
def user(user_client: UserCreator) -> CreatedUser:
    return user_client.create_user()


def test_the_refresh_cookie_is_sent_to_the_auth_routes_only(
    api_client: TestClient, user: CreatedUser
):
    resp = api_client.post(
        "/users/auth/login",
        json={"username": user.username, "password": user.password},
    )

    morsel = _cookies(resp)[COOKIE]
    assert morsel["path"] == "/api/users/auth"
    assert morsel["httponly"]
    assert morsel["samesite"].lower() == "lax"
    assert abs(int(morsel["max-age"]) - settings.refresh_token_expires_seconds) <= 5


def test_a_refresh_rotates_the_token_and_the_new_one_keeps_working(
    api_client: TestClient, user: CreatedUser
):
    first = _sign_in(api_client, user)

    second = _rotate(api_client, first)
    third = _rotate(api_client, second)

    assert len({first.refresh, second.refresh, third.refresh}) == 3
    me = api_client.get("/users/me", headers=third.headers)
    assert me.status_code == 200, me.text
    assert me.json()["data"]["user"]["id"] == user.user_id


def test_a_second_tab_refreshing_at_the_same_moment_is_not_signed_out(
    api_client: TestClient, user: CreatedUser
):
    """Both tabs present the same cookie; one rotates it, the other arrives a
    moment later with the token that was just replaced."""
    tab = _sign_in(api_client, user)
    winner = _rotate(api_client, tab)

    loser = _refresh(api_client, tab.refresh)

    assert loser.status_code == 200, loser.text
    assert loser.json()["data"]["accessToken"]
    assert _refresh_token(loser) is None, "the winner already holds the successor"
    # The session survived the race: the successor still refreshes.
    assert _refresh(api_client, winner.refresh).status_code == 200


def test_a_replaced_token_presented_after_the_grace_window_ends_the_sign_in(
    api_client: TestClient, user: CreatedUser, monkeypatch
):
    monkeypatch.setattr(settings, "refresh_reuse_grace_seconds", 1)
    stolen = _sign_in(api_client, user)
    elsewhere = _sign_in(api_client, user)
    owner = _rotate(api_client, stolen)
    time.sleep(1.2)

    replay = _refresh(api_client, stolen.refresh)

    assert replay.status_code == 401, replay.text
    # Whoever holds the current token is signed out too: the server cannot
    # tell which of the two is the owner.
    assert _refresh(api_client, owner.refresh).status_code == 401
    # Another sign-in of the same account is a different session.
    assert _refresh(api_client, elsewhere.refresh).status_code == 200


def test_a_sign_in_left_unused_past_the_idle_timeout_is_over(
    api_client: TestClient, user: CreatedUser, monkeypatch
):
    monkeypatch.setattr(settings, "refresh_idle_timeout_seconds", 1)
    sign_in = _rotate(api_client, _sign_in(api_client, user))
    time.sleep(1.2)

    assert _refresh(api_client, sign_in.refresh).status_code == 401


def test_refreshing_does_not_extend_a_sign_in_past_its_lifetime(
    api_client: TestClient, user: CreatedUser, monkeypatch
):
    monkeypatch.setattr(settings, "refresh_token_expires_seconds", 2)
    sign_in = _sign_in(api_client, user)
    time.sleep(1.1)

    resp = _refresh(api_client, sign_in.refresh)
    assert resp.status_code == 200, resp.text
    # The new cookie ends when the sign-in does, not two seconds from now.
    assert int(_cookies(resp)[COOKIE]["max-age"]) <= 1
    successor = _refresh_token(resp)
    assert successor
    time.sleep(1.1)

    assert _refresh(api_client, successor).status_code == 401


def test_a_refresh_token_nobody_issued_is_refused(api_client: TestClient):
    assert _refresh(api_client, "not-a-token").status_code == 401
    assert api_client.post(REFRESH).status_code == 401


def test_the_device_list_shows_each_sign_in_and_which_one_is_asking(
    api_client: TestClient, user: CreatedUser
):
    here = _rotate(api_client, _sign_in(api_client, user))
    _sign_in(api_client, user)

    sessions = _sessions(api_client, here)

    assert len(sessions) == 2
    assert [s["current"] for s in sessions].count(True) == 1
    assert {s["loginMethod"] for s in sessions} == {"password"}


def test_ending_another_sign_in_stops_its_refresh_token(
    api_client: TestClient, user: CreatedUser
):
    here = _sign_in(api_client, user)
    there = _sign_in(api_client, user)
    (other,) = [s for s in _sessions(api_client, here) if not s["current"]]

    resp = api_client.delete(f"/users/me/sessions/{other['id']}", headers=here.headers)

    assert resp.status_code == 200, resp.text
    assert _refresh(api_client, there.refresh).status_code == 401
    assert _refresh(api_client, here.refresh).status_code == 200
    assert other["id"] not in [s["id"] for s in _sessions(api_client, here)]


def test_signing_out_other_devices_keeps_this_one(
    api_client: TestClient, user: CreatedUser
):
    here = _sign_in(api_client, user)
    others = [_sign_in(api_client, user), _sign_in(api_client, user)]

    resp = api_client.delete("/users/me/sessions", headers=here.headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["revokedCount"] == 2
    for other in others:
        assert _refresh(api_client, other.refresh).status_code == 401
    assert _refresh(api_client, here.refresh).status_code == 200


def test_a_session_of_another_account_cannot_be_ended(
    api_client: TestClient, user: CreatedUser, user_client: UserCreator
):
    mine = _sign_in(api_client, user)
    theirs = _sign_in(api_client, user_client.create_user())
    (their_session,) = _sessions(api_client, theirs)

    resp = api_client.delete(
        f"/users/me/sessions/{their_session['id']}", headers=mine.headers
    )

    assert resp.status_code == 404, resp.text
    assert _refresh(api_client, theirs.refresh).status_code == 200


def test_signing_out_ends_the_sign_in_on_the_server(
    api_client: TestClient, user: CreatedUser
):
    here = _sign_in(api_client, user)
    elsewhere = _sign_in(api_client, user)

    resp = api_client.post(
        "/users/auth/logout", headers={"Cookie": f"{COOKIE}={here.refresh}"}
    )

    assert resp.status_code == 200, resp.text
    assert int(_cookies(resp)[COOKIE]["max-age"]) == 0
    assert _refresh(api_client, here.refresh).status_code == 401
    assert _refresh(api_client, elsewhere.refresh).status_code == 200


def test_signing_out_without_a_cookie_still_succeeds(api_client: TestClient):
    assert api_client.post("/users/auth/logout").status_code == 200


def test_changing_the_password_signs_out_every_other_device(
    api_client: TestClient, user: CreatedUser
):
    here = _sign_in(api_client, user)
    elsewhere = _sign_in(api_client, user)
    ticket = api_client.post(
        "/users/auth/sudo",
        headers=here.headers,
        json={
            "method": "password",
            "credentials": {"password": user.password},
            "purpose": "password:change",
        },
    ).json()["data"]["sudoTicket"]

    resp = api_client.patch(
        f"/users/{user.user_id}/password",
        headers=here.headers,
        json={"password": "brand-New-pass1", "sudoTicket": ticket},
    )

    assert resp.status_code == 200, resp.text
    assert _refresh(api_client, elsewhere.refresh).status_code == 401
    assert _refresh(api_client, here.refresh).status_code == 200


class _Outbox:
    def __init__(self) -> None:
        self.is_configured = True
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> bool:
        self.sent.append(kwargs)
        return True


def test_resetting_a_forgotten_password_signs_out_everywhere(
    api_client: TestClient, user: CreatedUser, monkeypatch
):
    import app.core.email as email_module

    outbox = _Outbox()
    monkeypatch.setattr(email_module, "get_email_sender", lambda: outbox)
    sign_ins = [_sign_in(api_client, user), _sign_in(api_client, user)]
    api_client.post("/users/recover/password/request", json={"email": user.email})
    deadline = time.monotonic() + 5
    while not outbox.sent:
        assert time.monotonic() < deadline, "the recovery mail never left"
        time.sleep(0.01)
    match = re.search(r"token=([\w.-]+)", outbox.sent[-1]["body_text"])
    assert match, outbox.sent[-1]

    resp = api_client.post(
        "/users/recover/password/verify",
        json={"token": match.group(1), "password": "brand-New-pass1"},
    )

    assert resp.status_code == 200, resp.text
    for sign_in in sign_ins:
        assert _refresh(api_client, sign_in.refresh).status_code == 401
