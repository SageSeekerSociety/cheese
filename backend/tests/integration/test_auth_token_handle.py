"""refresh-token 与 passkey 登录签发的 access token 必须带 handle claim.

背景:鉴权端(ActorResolver._recover_numeric_handle)已经能在 handle 缺失时按
uid 反查回填,但源头签发应直接带上 handle,减少对兜底逻辑的依赖
(见 backend/app/common/auth.py::create_access_token 的 handle 参数)。
本文件锁定 refresh-token 和 passkey 登录这两个此前遗漏的签发点。
"""

from unittest.mock import AsyncMock

import jwt
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.passkey.services import PasskeyService
from tests.integration.conftest import CreatedUser, UserCreator

from .test_passkey import _fake_credential


def _decode(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


class TestRefreshTokenCarriesHandle:
    def test_refresh_token_response_carries_handle(
        self, authenticated_user: CreatedUser, api_client: TestClient
    ):
        # login() already left REFRESH_TOKEN cookie set on api_client's jar.
        resp = api_client.post("/users/auth/refresh-token")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        access_token = resp.json()["data"]["accessToken"]
        claims = _decode(access_token)
        assert claims["handle"] == authenticated_user.username


class TestPasskeyVerifyCarriesHandle:
    def test_passkey_verify_response_carries_handle(
        self,
        monkeypatch,
        user_client: UserCreator,
        api_client: TestClient,
    ):
        user = user_client.create_user()

        options_resp = api_client.post(
            "/users/auth/passkey/options",
            json={"userId": user.user_id},
        )
        assert options_resp.status_code == 200, options_resp.text
        challenge = options_resp.json()["data"]["options"]["challenge"]

        monkeypatch.setattr(
            PasskeyService,
            "verify_authentication",
            AsyncMock(return_value=user.user_id),
        )

        verify_resp = api_client.post(
            "/users/auth/passkey/verify",
            json={"response": _fake_credential(challenge)},
        )
        assert verify_resp.status_code == 201, (
            f"Expected 201, got {verify_resp.status_code}: {verify_resp.text}"
        )
        access_token = verify_resp.json()["data"]["accessToken"]
        claims = _decode(access_token)
        assert claims["handle"] == user.username
