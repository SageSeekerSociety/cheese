"""一个人的时区记在账号上：安静时段按它的钟点算，所以它得留得住、乱填的进不来。"""

import pytest


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _report(client, token: str, timezone: str):
    return client.put(
        "/users/me/timezone", json={"timezone": timezone}, headers=_headers(token)
    )


def _sign_in(api_client, person) -> dict:
    resp = api_client.post(
        "/users/auth/login",
        json={"username": person.username, "password": person.password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_the_reported_time_zone_is_kept_on_the_account(api_client, user_client):
    person = user_client.create_user()
    first = _sign_in(api_client, person)
    assert first["user"]["timezone"] is None
    assert _report(api_client, first["accessToken"], "Europe/London").status_code == 200

    assert _sign_in(api_client, person)["user"]["timezone"] == "Europe/London"
    me = api_client.get("/users/me", headers=_headers(first["accessToken"]))
    assert me.json()["data"]["user"]["timezone"] == "Europe/London"


@pytest.mark.parametrize("timezone", ["Mars/Olympus", "", "../../etc/passwd"])
def test_a_time_zone_that_does_not_exist_is_refused(api_client, user_client, timezone):
    person = user_client.create_user()
    token = _sign_in(api_client, person)["accessToken"]
    assert _report(api_client, token, timezone).status_code == 400
    assert _sign_in(api_client, person)["user"]["timezone"] is None


def test_someone_else_never_sees_the_time_zone(api_client, user_client):
    alice, bob = user_client.create_user(), user_client.create_user()
    _report(api_client, _sign_in(api_client, alice)["accessToken"], "Europe/London")
    resp = api_client.get(
        f"/users/{alice.user_id}",
        headers=_headers(_sign_in(api_client, bob)["accessToken"]),
    )
    assert resp.status_code == 200, resp.text
    assert "timezone" not in resp.json()["data"]["user"]
