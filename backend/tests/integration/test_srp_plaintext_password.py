"""Accounts registered with SRP sign in with their plaintext password.

Each record below was produced by the frontend's `secure-remote-password`
(0.3.1) under node — ``client.derivePrivateKey(salt, username, password)`` then
``client.deriveVerifier`` — with the salts fixed so the values are stable.
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.users import _issue_oauth_state_token, _store_oauth_pending
from app.common.auth import create_access_token
from app.core.config import settings
from app.domain.user.login_security import (
    LOGIN_ATTEMPTS_PREFIX,
    LOGIN_LOCKOUT_PREFIX,
    MAX_LOGIN_ATTEMPTS,
    STEP_UP_PASSWORD_ATTEMPTS_PREFIX,
    STEP_UP_PASSWORD_LOCKOUT_PREFIX,
)
from app.domain.user.models import User
from tests.integration.conftest import CreatedUser, UserCreator


@dataclass(frozen=True)
class SrpRecord:
    username: str
    password: str
    salt: str
    verifier: str

    @property
    def stored(self) -> str:
        return f"SRP:{self.salt}:{self.verifier}"


MIXED_CASE = SrpRecord(
    username="Srp_MixedCase",
    password="Correct-Horse-7",
    salt="0000c0ffee15600dd00dfeedfacecafebeef0123456789abcdef0123456789ab",
    verifier=(
        "04ea343ac92bff3f7303c7b8b0836f3fc43ce88d9fbd5b66d1d404614b0009c5"
        "5138263854e5105e00173b2fb5ce23abb2473a70b89734ef8459e94bd384a10b"
        "d7f3e9a209d37e5dc26d4821c3fa6fff889625e2c1baffd70561872a7fd1513d"
        "c8c7b0b8486cb7abaecfb92c2db13b0081a9a68879df07fceb643e67a14fdc0a"
        "13473c068b9f14d2a23d0fd1921e799f054d8a7b214cd61fd5b3f5aba68c6cc6"
        "24a46334d5aa5558b34bb77f66c2abb501a604446b7f9f3e39cc61864307bb7c"
        "7dcb3d6577792268c185a417b1fcb9b4c1eba0a4531692599da10d265d141a70"
        "8cebbe9971e0ca42a5e657fad16c78b76703d8d20f4bb9387195c4f6f80902d3"
    ),
)
UNICODE = SrpRecord(
    username="srp_unicode",
    password="Пароль密码ü🔑",
    salt="5a1f00000000000000000000000000000000000000000000000000000000a7e1",
    verifier=(
        "2fd3d43bfba5073ce77e3681fe774603f8b2348648aec0b744112496a284abe4"
        "4788290160e725d0a9ea0e317bcc5083e3fbf446ceae77feb37984ee7251ea49"
        "3b17690d07b371ca6397e3a88fb7be3273cbb191b43941f9ee59a607c4136fff"
        "f7a851e7c87a4ef2b5d54010d94ea32370f31dbb6dbbcc098959d44aaa3b8bc2"
        "f3a23121b0a9297f1cc07a5c34f192856793ba0c4706f8b3fd304917d14be510"
        "ca28b542ce5655cb992c4d193b27fedb3da58632d55848f83139054ed73d3c39"
        "251f3fb9876cd2a37ae4255ee1f7686b9bb16df28a9d3ad4399833721db4e49b"
        "2135fe2acc8c84a2f76e5f67818aa70b5f449c1197defa096489a8851f6c6dc1"
    ),
)
# 86 UTF-8 bytes: more than bcrypt can hold.
LONG = SrpRecord(
    username="srp_long",
    password="long-passphrase-" * 5 + "密码",
    salt="ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100",
    verifier=(
        "3b0d837297cf4be4f0c18450cb8ba7b65c4b70cc8702dfd141f7e66c96f30b0f"
        "7d6ed08dc4faba6735343f04c7650c4346e8efcf40e488578cd95f9b54f8d4d9"
        "739eb90b885924d6df25b76a9667bd234b9217764cdd08a237bd225d781a4c5c"
        "c9a432a7834e4c7b714a61a2297c2316d5cf4a87b5dead612f8d849317cd6c2d"
        "59f29bb6485a4138ea23937249ef58a68c9f053cd001d386e0a966c882feab35"
        "7fb25f83e9e3faf0dd1a842ce0228cf1ac7fecc335c714ce3641c53851d4a453"
        "aa442257bbf5cdbc5113d1f6a9c42f9234ea6998f4eb04c86da6e15da4d48170"
        "eadc6bb20af16c491afe5b495667fb76206006da794ae671c27be634789c93ca"
    ),
)
VECTOR = SrpRecord(
    username="srp_vector",
    password="密码:Abc!😀",
    salt="00a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f",
    verifier=(
        "4de103f1ba7535099fc8db57da22f0f33e0c41206d4359a5d5d94c7cdecd595f"
        "d3b3aebac557bd52b326341529f49574d1823bf78a60676e592b7bfa40878153"
        "8199971f231aaa808ba496c284ba6f019b9495b5ad5d90c9b80f2e550e1a737d"
        "7dac94b5ceff6aeb253eabb42b0dd334fbadd9339c803785b6755376669ad7e4"
        "387aa008fa1ac713f6b8a3eb2e08e22d3b15eeea22c4a4f2c2e92dc1bd9a09e1"
        "2a3399acaf3aa2c548755875192981e9dddec750367f41e3602c4349d3989242"
        "8314f96a8cbdf4256f11d98a1c5ddb6447233daa4d28aec6106bdab34c672bc4"
        "f8e2e5da4ccb9d2f78fe9aa25ce8a97c88d44d772f67fe1e00fdb21b7d684785"
    ),
)
UNKNOWN_USERNAME = "srp_nobody_here"


@pytest.fixture
def srp_account(
    user_client: UserCreator,
) -> Generator[Callable[[SrpRecord], CreatedUser]]:
    """Creates SRP accounts and clears the Redis budgets they leave behind:
    those do not roll back with the test's transaction, and the usernames and
    user ids come back in later tests."""
    created: list[CreatedUser] = []

    def create(record: SrpRecord) -> CreatedUser:
        user = user_client.create_user(
            username=record.username, hashed_password=record.stored
        )
        created.append(user)
        return user

    yield create

    r = redis.Redis.from_url(settings.redis_url)
    r.delete(
        f"{LOGIN_ATTEMPTS_PREFIX}{UNKNOWN_USERNAME}",
        f"{LOGIN_LOCKOUT_PREFIX}{UNKNOWN_USERNAME}",
    )
    for user in created:
        r.delete(
            f"{LOGIN_ATTEMPTS_PREFIX}{user.username}",
            f"{LOGIN_LOCKOUT_PREFIX}{user.username}",
            f"{STEP_UP_PASSWORD_ATTEMPTS_PREFIX}{user.user_id}",
            f"{STEP_UP_PASSWORD_LOCKOUT_PREFIX}{user.user_id}",
        )
    r.close()


def _stored(db_session: AsyncSession, _portal, user_id: int) -> str:
    async def read() -> str:
        result = await db_session.execute(
            select(User.hashed_password).where(User.id == user_id)
        )
        return result.scalar_one() or ""

    return _portal.call(read)


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/users/auth/login", json={"username": username, "password": password}
    )


def _q(resp) -> dict:
    assert resp.status_code == 302, resp.text
    return {
        k: v[0] for k, v in parse_qs(urlparse(resp.headers["location"]).query).items()
    }


class TestPasswordLogin:
    @pytest.mark.parametrize("record", [MIXED_CASE, UNICODE, VECTOR])
    def test_signs_in_and_moves_the_account_to_bcrypt(
        self, api_client: TestClient, srp_account, db_session, _portal, record
    ):
        user = srp_account(record)

        first = _login(api_client, record.username, record.password)
        assert first.status_code == 200, first.text
        assert first.json()["data"]["user"]["id"] == user.user_id

        assert _stored(db_session, _portal, user.user_id).startswith("$2")

        second = _login(api_client, record.username, record.password)
        assert second.status_code == 200, second.text

    def test_wrong_password_is_answered_like_an_unknown_user(
        self, api_client: TestClient, srp_account, db_session, _portal
    ):
        user = srp_account(MIXED_CASE)

        wrong = _login(api_client, MIXED_CASE.username, "Correct-Horse-8")
        unknown = _login(api_client, UNKNOWN_USERNAME, "Correct-Horse-8")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["message"] == unknown.json()["message"]
        assert _stored(db_session, _portal, user.user_id) == MIXED_CASE.stored

    def test_the_verifier_is_bound_to_the_username(
        self, api_client: TestClient, srp_account
    ):
        user = srp_account(MIXED_CASE)
        # The same record under another username does not accept the password.
        other = srp_account(
            SrpRecord(
                "srp_other_name",
                MIXED_CASE.password,
                MIXED_CASE.salt,
                MIXED_CASE.verifier,
            )
        )

        assert (
            _login(api_client, other.username, MIXED_CASE.password).status_code == 401
        )
        assert _login(api_client, user.username, MIXED_CASE.password).status_code == 200

    def test_wrong_passwords_spend_the_login_budget(
        self, api_client: TestClient, srp_account
    ):
        srp_account(UNICODE)
        for _ in range(MAX_LOGIN_ATTEMPTS):
            assert _login(api_client, UNICODE.username, "wrong").status_code in (
                401,
                403,
            )

        assert _login(api_client, UNICODE.username, UNICODE.password).status_code == 403

    def test_a_password_bcrypt_cannot_hold_signs_in_and_stays_srp(
        self, api_client: TestClient, srp_account, db_session, _portal
    ):
        user = srp_account(LONG)

        resp = _login(api_client, LONG.username, LONG.password)
        assert resp.status_code == 200, resp.text
        assert _stored(db_session, _portal, user.user_id) == LONG.stored


class TestSudoPassword:
    def _sudo(self, client: TestClient, user: CreatedUser, password: str):
        token = create_access_token(user.user_id, handle=user.username)
        return client.post(
            "/users/auth/sudo",
            headers={"Authorization": f"Bearer {token}"},
            json={"method": "password", "credentials": {"password": password}},
        )

    def test_srp_account_re_proves_with_its_password(
        self, api_client: TestClient, srp_account, db_session, _portal
    ):
        user = srp_account(UNICODE)

        resp = self._sudo(api_client, user, UNICODE.password)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["verified"] is True
        assert _stored(db_session, _portal, user.user_id).startswith("$2")

    def test_wrong_passwords_spend_the_step_up_budget(
        self, api_client: TestClient, srp_account, db_session, _portal
    ):
        user = srp_account(UNICODE)
        for _ in range(MAX_LOGIN_ATTEMPTS):
            assert self._sudo(api_client, user, "wrong").status_code in (401, 403)

        assert self._sudo(api_client, user, UNICODE.password).status_code == 403
        assert _stored(db_session, _portal, user.user_id) == UNICODE.stored


class TestOAuth:
    def _state_token(self, _portal, uid: str) -> str:
        return _portal.call(
            _issue_oauth_state_token,
            "ruc",
            {"id": uid, "name": "Prov User", "preferredUsername": "provuser"},
        )

    def _bind(self, client: TestClient, token: str, username: str, password: str):
        return client.post(
            "/users/oauth/bind",
            data={"stateToken": token, "username": username, "password": password},
            follow_redirects=False,
        )

    def test_bind_accepts_an_srp_accounts_password(
        self, api_client: TestClient, srp_account, db_session, _portal
    ):
        user = srp_account(MIXED_CASE)

        resp = self._bind(
            api_client,
            self._state_token(_portal, "uid-srp-bind"),
            MIXED_CASE.username,
            MIXED_CASE.password,
        )
        assert _q(resp)["bound"] == "true"
        assert _stored(db_session, _portal, user.user_id).startswith("$2")

    def test_bind_answers_a_wrong_srp_password_like_an_unknown_user(
        self, api_client: TestClient, srp_account, _portal
    ):
        srp_account(MIXED_CASE)

        wrong = self._bind(
            api_client,
            self._state_token(_portal, "uid-srp-bind-wrong"),
            MIXED_CASE.username,
            "wrong",
        )
        unknown = self._bind(
            api_client,
            self._state_token(_portal, "uid-srp-bind-nouser"),
            UNKNOWN_USERNAME,
            "wrong",
        )
        assert _q(wrong)["error_code"] == _q(unknown)["error_code"]

    def _seed_pending(self, _portal, session_id: str, user: CreatedUser) -> None:
        # What the callback stores when the provider's email belongs to an
        # existing account.
        _portal.call(
            _store_oauth_pending,
            session_id,
            {
                "type": "password",
                "providerId": "ruc",
                "userInfo": {"id": f"uid-{session_id}"},
                "userId": user.user_id,
                "username": user.username,
            },
        )

    def _verify(self, client: TestClient, session_id: str, password: str):
        return client.post(
            "/users/auth/oauth/verify",
            json={"sessionId": session_id, "password": password},
            follow_redirects=False,
        )

    def test_verify_page_accepts_an_srp_accounts_password(
        self, api_client: TestClient, srp_account, db_session, _portal
    ):
        user = srp_account(UNICODE)
        self._seed_pending(_portal, "oauth_srp_plain_ok", user)

        resp = self._verify(api_client, "oauth_srp_plain_ok", UNICODE.password)
        assert _q(resp)["linked"] == "true"
        assert _stored(db_session, _portal, user.user_id).startswith("$2")

    def test_verify_page_refuses_a_wrong_srp_password(
        self, api_client: TestClient, srp_account, db_session, _portal
    ):
        user = srp_account(UNICODE)
        self._seed_pending(_portal, "oauth_srp_plain_bad", user)

        resp = self._verify(api_client, "oauth_srp_plain_bad", "wrong")
        assert _q(resp)["error_code"] == "INVALID_PASSWORD"
        assert _stored(db_session, _portal, user.user_id) == UNICODE.stored
