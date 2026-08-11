"""2FA (TOTP) end-to-end: setup → status → login completion → backup codes →
settings → disable. Contract mirrors the reference NestJS users controller
(/users/{id}/2fa/* + /users/auth/verify-2fa)."""

import pyotp
import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator


class TestTwoFactorIntegration:
    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        yield
        # TOTP state lives in Redis and does NOT roll back with the per-test
        # DB transaction, while user ids restart with each session's fresh DB —
        # stale keys would flip "2FA required" on unrelated future test users.
        import redis

        from app.core.config import settings
        from app.domain.user.login_security import (
            TOTP_ALWAYS_PREFIX,
            TOTP_BACKUP_PREFIX,
            TOTP_SECRET_PREFIX,
        )

        r = redis.Redis.from_url(settings.redis_url)
        r.delete(
            *(
                f"{p}{self.user.user_id}"
                for p in (TOTP_SECRET_PREFIX, TOTP_BACKUP_PREFIX, TOTP_ALWAYS_PREFIX)
            )
        )
        r.close()

    def _enable_2fa(self) -> tuple[str, list[str]]:
        """Run the two-phase enable dance; returns (secret, backup_codes)."""
        init = self.client.post(
            f"/users/{self.user.user_id}/2fa/enable", headers=self.headers, json={}
        )
        assert init.status_code == 200
        data = init.json()["data"]
        assert data["otpauth_url"].startswith("otpauth://")
        assert data["qrcode"].startswith("data:image/png;base64,")
        assert data["backup_codes"] == []
        secret = data["secret"]

        confirm = self.client.post(
            f"/users/{self.user.user_id}/2fa/enable",
            headers=self.headers,
            json={"secret": secret, "code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200
        body = confirm.json()
        assert body["code"] == 201
        codes = body["data"]["backup_codes"]
        assert len(codes) == 10 and all(len(c) == 8 for c in codes)
        return secret, codes

    def _temp_token(self) -> str:
        from app.common.auth import create_2fa_pending_token

        return create_2fa_pending_token(self.user.user_id)

    def test_enable_status_disable_roundtrip(self):
        secret, _codes = self._enable_2fa()

        status = self.client.get(
            f"/users/{self.user.user_id}/2fa/status", headers=self.headers
        )
        assert status.status_code == 200
        assert status.json()["data"] == {
            "enabled": True,
            "has_passkey": False,
            "always_required": False,
        }

        again = self.client.post(
            f"/users/{self.user.user_id}/2fa/enable", headers=self.headers, json={}
        )
        assert again.status_code == 400

        disable = self.client.post(
            f"/users/{self.user.user_id}/2fa/disable", headers=self.headers, json={}
        )
        assert disable.status_code == 200
        assert disable.json()["data"] == {"success": True}

        status = self.client.get(
            f"/users/{self.user.user_id}/2fa/status", headers=self.headers
        )
        assert status.json()["data"]["enabled"] is False

    def test_enable_rejects_bad_confirmation_code(self):
        init = self.client.post(
            f"/users/{self.user.user_id}/2fa/enable", headers=self.headers, json={}
        )
        secret = init.json()["data"]["secret"]
        confirm = self.client.post(
            f"/users/{self.user.user_id}/2fa/enable",
            headers=self.headers,
            json={"secret": secret, "code": "000000"},
        )
        assert confirm.status_code == 422

    def test_verify_2fa_login_with_totp(self):
        secret, _codes = self._enable_2fa()

        resp = self.client.post(
            "/users/auth/verify-2fa",
            json={"temp_token": self._temp_token(), "code": pyotp.TOTP(secret).now()},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 201
        data = body["data"]
        assert data["requires2FA"] is False
        assert data["usedBackupCode"] is False
        assert data["user"]["id"] == self.user.user_id
        assert resp.cookies.get("REFRESH_TOKEN")

        # the minted access token is a real session
        me = self.client.get(
            f"/users/{self.user.user_id}",
            headers={"Authorization": f"Bearer {data['accessToken']}"},
        )
        assert me.status_code == 200

    def test_verify_2fa_login_with_backup_code_is_one_time(self):
        _secret, codes = self._enable_2fa()
        code = codes[0]

        first = self.client.post(
            "/users/auth/verify-2fa",
            json={"temp_token": self._temp_token(), "code": code},
        )
        assert first.status_code == 200
        assert first.json()["data"]["usedBackupCode"] is True

        replay = self.client.post(
            "/users/auth/verify-2fa",
            json={"temp_token": self._temp_token(), "code": code},
        )
        assert replay.status_code == 401

    def test_verify_2fa_rejects_wrong_token_type(self):
        secret, _codes = self._enable_2fa()
        # a real ACCESS token must not pass as a 2fa_pending token
        resp = self.client.post(
            "/users/auth/verify-2fa",
            json={"temp_token": self.user.token, "code": pyotp.TOTP(secret).now()},
        )
        assert resp.status_code == 401

    def test_backup_codes_regenerate_invalidates_old(self):
        _secret, old_codes = self._enable_2fa()

        regen = self.client.post(
            f"/users/{self.user.user_id}/2fa/backup-codes", headers=self.headers
        )
        assert regen.status_code == 200
        body = regen.json()
        assert body["code"] == 201
        new_codes = body["data"]["backup_codes"]
        assert len(new_codes) == 10
        assert not set(new_codes) & set(old_codes)

        stale = self.client.post(
            "/users/auth/verify-2fa",
            json={"temp_token": self._temp_token(), "code": old_codes[0]},
        )
        assert stale.status_code == 401

    def test_settings_always_required_roundtrip(self):
        self._enable_2fa()

        put = self.client.put(
            f"/users/{self.user.user_id}/2fa/settings",
            headers=self.headers,
            json={"always_required": True},
        )
        assert put.status_code == 200
        assert put.json()["data"] == {"success": True, "always_required": True}

        status = self.client.get(
            f"/users/{self.user.user_id}/2fa/status", headers=self.headers
        )
        assert status.json()["data"]["always_required"] is True

    def test_2fa_management_is_self_only(self):
        other = self.user_client.create_user()
        other_token = self.user_client.login(
            self.client, other.username, other.password
        )
        other_headers = {"Authorization": f"Bearer {other_token}"}

        for method, path in [
            ("post", f"/users/{self.user.user_id}/2fa/enable"),
            ("post", f"/users/{self.user.user_id}/2fa/disable"),
            ("post", f"/users/{self.user.user_id}/2fa/backup-codes"),
            ("get", f"/users/{self.user.user_id}/2fa/status"),
        ]:
            resp = getattr(self.client, method)(path, headers=other_headers)
            assert resp.status_code == 403, path
