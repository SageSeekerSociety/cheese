"""SRP login over HTTP, driven by values the frontend's SRP library computed.

The server's ephemeral secret is pinned to the vector's, so the proof the
routes return can be compared with the one the JS client expects.
"""

from collections.abc import Generator

import pyotp
import pytest
import redis
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.user.login_security import (
    LOGIN_ATTEMPTS_PREFIX,
    LOGIN_LOCKOUT_PREFIX,
    MAX_LOGIN_ATTEMPTS,
    STEP_UP_PASSWORD_ATTEMPTS_PREFIX,
    STEP_UP_PASSWORD_LOCKOUT_PREFIX,
    TOTP_ALWAYS_PREFIX,
    TOTP_BACKUP_PREFIX,
    TOTP_SECRET_PREFIX,
    TWO_FACTOR_ATTEMPTS_PREFIX,
    TWO_FACTOR_LOCKOUT_PREFIX,
)
from tests.integration.conftest import CreatedUser, UserCreator
from tests.support import srp_vectors as js

MALFORMED = [("abc", js.M1), ("zz", js.M1), ("", js.M1), (js.A, "abc")]


@pytest.fixture
def srp_user(user_client: UserCreator, monkeypatch) -> Generator[CreatedUser]:
    monkeypatch.setattr(
        "app.api.routes.users._srp_generate_ephemeral",
        lambda _verifier: (js.B, js.B_SECRET),
    )
    user = user_client.create_user(
        username=js.USERNAME,
        hashed_password=f"SRP:{js.SALT}:{js.VERIFIER}",
    )
    yield user
    # Redis does not roll back with the test's transaction, and both the
    # username and the user id come back in later tests.
    r = redis.Redis.from_url(settings.redis_url)
    r.delete(
        f"{LOGIN_ATTEMPTS_PREFIX}{user.username}",
        f"{LOGIN_LOCKOUT_PREFIX}{user.username}",
        *(
            f"{prefix}{user.user_id}"
            for prefix in (
                TOTP_SECRET_PREFIX,
                TOTP_BACKUP_PREFIX,
                TOTP_ALWAYS_PREFIX,
                TWO_FACTOR_ATTEMPTS_PREFIX,
                TWO_FACTOR_LOCKOUT_PREFIX,
                STEP_UP_PASSWORD_ATTEMPTS_PREFIX,
                STEP_UP_PASSWORD_LOCKOUT_PREFIX,
            )
        ),
    )
    r.close()


def _init(client: TestClient) -> dict:
    resp = client.post("/users/auth/srp/init", json={"username": js.USERNAME})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _verify(client: TestClient, a: str = js.A, m1: str = js.M1):
    return client.post(
        "/users/auth/srp/verify",
        json={"username": js.USERNAME, "clientPublicEphemeral": a, "clientProof": m1},
    )


def _srp_login(client: TestClient, a: str = js.A, m1: str = js.M1):
    data = _init(client)
    assert (data["salt"], data["serverPublicEphemeral"]) == (js.SALT, js.B)
    return _verify(client, a, m1)


def _enable_2fa(client: TestClient, user: CreatedUser, token: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    sudo_init = client.post(
        "/users/auth/sudo",
        headers=headers,
        json={"method": "srp", "credentials": {}, "purpose": "2fa:enable"},
    )
    assert sudo_init.status_code == 200, sudo_init.text
    sudo = client.post(
        "/users/auth/sudo",
        headers=headers,
        json={
            "method": "srp",
            "credentials": {"clientPublicEphemeral": js.A, "clientProof": js.M1},
            "purpose": "2fa:enable",
        },
    )
    assert sudo.status_code == 200, sudo.text
    url = f"/users/{user.user_id}/2fa/enable"
    init = client.post(
        url, headers=headers, json={"sudoTicket": sudo.json()["data"]["sudoTicket"]}
    )
    assert init.status_code == 200, init.text
    secret = init.json()["data"]["secret"]
    confirm = client.post(
        url,
        headers=headers,
        json={"secret": secret, "code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 200, confirm.text
    return secret


def test_login_returns_the_proof_the_client_expects(
    api_client: TestClient, srp_user: CreatedUser
):
    resp = _srp_login(api_client)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["requires2FA"] is False
    assert data["serverProof"] == js.M2
    assert data["user"]["id"] == srp_user.user_id


def test_login_with_2fa_returns_the_proof_before_asking_for_a_code(
    api_client: TestClient, srp_user: CreatedUser
):
    token = _srp_login(api_client).json()["data"]["accessToken"]
    secret = _enable_2fa(api_client, srp_user, token)

    resp = _srp_login(api_client)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["requires2FA"] is True
    assert data["serverProof"] == js.M2

    done = api_client.post(
        "/users/auth/verify-2fa",
        json={"temp_token": data["tempToken"], "code": pyotp.TOTP(secret).now()},
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["user"]["id"] == srp_user.user_id


@pytest.mark.parametrize(("a", "m1"), MALFORMED)
def test_malformed_client_values_are_a_wrong_password(
    api_client: TestClient, srp_user: CreatedUser, a: str, m1: str
):
    wrong = _srp_login(api_client, js.A, "00" * 32)
    assert wrong.status_code == 401, wrong.text

    resp = _srp_login(api_client, a, m1)
    assert resp.status_code == 401, resp.text
    assert resp.json()["message"] == wrong.json()["message"]


def test_malformed_client_values_count_toward_lockout(
    api_client: TestClient, srp_user: CreatedUser
):
    for _ in range(MAX_LOGIN_ATTEMPTS):
        assert _srp_login(api_client, "abc").status_code == 401

    assert _srp_login(api_client).status_code == 403


def test_sudo_treats_malformed_client_values_as_a_wrong_password(
    api_client: TestClient, srp_user: CreatedUser
):
    token = _srp_login(api_client).json()["data"]["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}

    init = api_client.post(
        "/users/auth/sudo",
        headers=headers,
        json={"method": "srp", "credentials": {}},
    )
    assert init.status_code == 200, init.text

    resp = api_client.post(
        "/users/auth/sudo",
        headers=headers,
        json={
            "method": "srp",
            "credentials": {"clientPublicEphemeral": "abc", "clientProof": js.M1},
        },
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize(
    ("salt", "verifier"),
    [("abc", js.VERIFIER), (js.SALT, "zz"), (f"{js.SALT}:00", js.VERIFIER)],
)
def test_registration_refuses_malformed_srp_credentials(
    api_client: TestClient, salt: str, verifier: str
):
    resp = api_client.post(
        "/users",
        json={
            "username": "srp_malformed",
            "nickname": "srp_malformed",
            "email": "srp-malformed@ruc.edu.cn",
            "emailCode": "000000",
            "srpSalt": salt,
            "srpVerifier": verifier,
        },
    )
    assert resp.status_code == 400, resp.text


def test_password_reset_refuses_malformed_srp_credentials(api_client: TestClient):
    resp = api_client.post(
        "/users/recover/password/verify",
        json={"token": "unused", "srpSalt": "abc", "srpVerifier": js.VERIFIER},
    )
    assert resp.status_code == 400, resp.text
