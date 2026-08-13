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
            BACKUP_CODE_ATTEMPTS_PREFIX,
            BACKUP_CODE_LOCKOUT_PREFIX,
            TOTP_ALWAYS_PREFIX,
            TOTP_BACKUP_PREFIX,
            TOTP_SECRET_PREFIX,
            TWO_FACTOR_ATTEMPTS_PREFIX,
            TWO_FACTOR_LOCKOUT_PREFIX,
        )

        r = redis.Redis.from_url(settings.redis_url)
        r.delete(
            *(
                f"{p}{self.user.user_id}"
                for p in (
                    TOTP_SECRET_PREFIX,
                    TOTP_BACKUP_PREFIX,
                    TOTP_ALWAYS_PREFIX,
                    # A 15-minute lockout outlives the test that earned it,
                    # and user ids restart with each session's fresh DB.
                    TWO_FACTOR_ATTEMPTS_PREFIX,
                    TWO_FACTOR_LOCKOUT_PREFIX,
                    BACKUP_CODE_ATTEMPTS_PREFIX,
                    BACKUP_CODE_LOCKOUT_PREFIX,
                )
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
        """A ticket obtained the way a client gets one: by logging in.

        Not minted directly — a ticket is only redeemable if its ``jti`` was
        reserved when it was issued (#357), and going through the endpoint is
        what proves the issuing side actually does that.
        """
        resp = self.client.post(
            "/users/auth/login",
            json={"username": self.user.username, "password": self.user.password},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["requires2FA"] is True, data
        return data["tempToken"]

    def _wrong_totp(self, secret: str) -> str:
        """A 6-digit code that is definitely not valid for ``secret`` now."""
        totp = pyotp.TOTP(secret)
        for candidate in ("000000", "111111", "222222"):
            if not totp.verify(candidate, valid_window=1):
                return candidate
        raise AssertionError("unreachable: three codes cannot all be valid")

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

    # ── #357: the second step must not be a free code oracle ──────────────
    #
    # 2FA exists for exactly one situation — the password has leaked — so
    # every test here assumes the attacker already logs in successfully and
    # only has to guess the second factor. With valid_window=1 that is 3 live
    # codes in 10^6, ~333k expected guesses: trivial if nothing counts them,
    # and out of reach the moment something does.

    def _verify(self, temp_token: str, code: str):
        return self.client.post(
            "/users/auth/verify-2fa", json={"temp_token": temp_token, "code": code}
        )

    def test_pending_ticket_is_redeemable_only_once(self):
        secret, _codes = self._enable_2fa()
        ticket = self._temp_token()

        first = self._verify(ticket, pyotp.TOTP(secret).now())
        assert first.status_code == 200

        replay = self._verify(ticket, pyotp.TOTP(secret).now())
        assert replay.status_code == 401

    def test_a_wrong_code_burns_the_ticket_and_hands_back_a_new_one(self):
        """The bound is per ticket, not per success: a spent ticket is spent.

        But the rejection carries a replacement, because making a typo cost a
        whole password round trip would spend the *password* budget — five
        misses and a legitimate user has locked themselves out of their own
        account, while the attacker (who has the password) shrugs."""
        secret, _codes = self._enable_2fa()
        ticket = self._temp_token()

        bad = self._verify(ticket, self._wrong_totp(secret))
        assert bad.status_code == 401
        replacement = bad.json()["error"]["data"]
        assert replacement["reason"] == "invalid_code"
        assert replacement["tempToken"]

        # The burnt one stays burnt, right code or not.
        retry = self._verify(ticket, pyotp.TOTP(secret).now())
        assert retry.status_code == 401
        assert retry.json()["error"]["data"]["reason"] == "session_expired"

        # The replacement works, and only once.
        good = self._verify(replacement["tempToken"], pyotp.TOTP(secret).now())
        assert good.status_code == 200, good.text

    def test_a_re_issued_ticket_does_not_reset_the_attempt_budget(self):
        """The load-bearing test for re-issue. Ride the replacement chain —
        never a fresh password login — and the budget must still run out. If
        handing back a ticket ever cleared the counter, the fix would be gone
        and everything would still look like it worked."""
        from app.domain.user.login_security import MAX_TWO_FACTOR_ATTEMPTS

        secret, _codes = self._enable_2fa()
        wrong = self._wrong_totp(secret)
        ticket = self._temp_token()

        for attempt in range(MAX_TWO_FACTOR_ATTEMPTS - 1):
            resp = self._verify(ticket, wrong)
            assert resp.status_code == 401, f"attempt {attempt + 1}: {resp.text}"
            data = resp.json()["error"]["data"]
            assert data["reason"] == "invalid_code"
            assert data["attemptsRemaining"] == MAX_TWO_FACTOR_ATTEMPTS - attempt - 1
            ticket = data["tempToken"]

        # The last one empties the budget, and hands nothing back.
        last = self._verify(ticket, wrong)
        assert last.status_code == 403, last.text
        assert last.json()["error"]["data"]["reason"] == "too_many_attempts"

        # N+1, with the CORRECT code and a ticket from a brand-new password
        # login: refused on the budget, not on the code or the ticket.
        blocked = self._verify(self._temp_token(), pyotp.TOTP(secret).now())
        assert blocked.status_code == 403, blocked.text
        assert blocked.json()["error"]["data"]["reason"] == "too_many_attempts"

    def test_a_re_issued_ticket_inherits_the_original_deadline(self):
        """Otherwise guessing wrong forever keeps the half-authenticated
        window — password accepted, 2FA not yet — alive forever.

        The wait is load-bearing, not politeness. Both tickets carry a
        300-second window, so if they are minted inside the same second a
        *fresh* deadline is indistinguishable from an inherited one and this
        test passes while asserting nothing. Letting the clock tick past a
        second is what gives the two outcomes different numbers — verified by
        reverting the inheritance and watching this go red."""
        import time

        import jwt

        from app.common.auth import PENDING_2FA_TTL_S
        from app.core.config import settings

        secret, _codes = self._enable_2fa()
        ticket = self._temp_token()
        time.sleep(1.1)

        bad = self._verify(ticket, self._wrong_totp(secret))
        replacement = bad.json()["error"]["data"]["tempToken"]

        def claims(token: str) -> dict:
            return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])

        assert claims(replacement)["exp"] == claims(ticket)["exp"]
        # Said the other way round, in case the deadline ever stops being an
        # `exp`: the replacement's own lifetime is SHORTER than a full window.
        fresh = claims(replacement)
        assert fresh["exp"] - fresh["iat"] < PENDING_2FA_TTL_S

    def test_the_three_refusals_are_told_apart(self):
        """Retry here / sign in again / wait fifteen minutes each need a
        different move from the user, so a client must be able to tell them
        apart without parsing prose."""
        from app.domain.user.login_security import MAX_TWO_FACTOR_ATTEMPTS

        secret, _codes = self._enable_2fa()

        def reason_of(resp) -> str:
            return resp.json()["error"]["data"]["reason"]

        assert reason_of(
            self._verify(self._temp_token(), self._wrong_totp(secret))
        ) == ("invalid_code")
        assert reason_of(self._verify("not-even-a-jwt", "000000")) == "session_expired"

        for _ in range(MAX_TWO_FACTOR_ATTEMPTS):
            self._verify(self._temp_token(), self._wrong_totp(secret))
        locked = self._verify(self._temp_token(), pyotp.TOTP(secret).now())
        assert reason_of(locked) == "too_many_attempts"
        assert locked.json()["error"]["data"]["retryAfterSeconds"] > 0

    def test_verify_2fa_locks_out_after_the_attempt_budget(self):
        """A fresh ticket per guess — the loop an attacker with the password
        would actually run — still hits a wall."""
        from app.domain.user.login_security import MAX_TWO_FACTOR_ATTEMPTS

        secret, _codes = self._enable_2fa()
        wrong = self._wrong_totp(secret)

        for attempt in range(MAX_TWO_FACTOR_ATTEMPTS):
            resp = self._verify(self._temp_token(), wrong)
            assert resp.status_code in (401, 403), f"attempt {attempt + 1}: {resp.text}"

        # N+1 is refused without the code being looked at — proven by sending
        # the CORRECT one.
        blocked = self._verify(self._temp_token(), pyotp.TOTP(secret).now())
        assert blocked.status_code == 403, blocked.text

    def test_inline_totp_on_password_login_is_budgeted_too(self):
        """/auth/login validates a TOTP inline and never mints a ticket, so
        the single-use ticket cannot bound it — only the per-user budget can.
        Left unbudgeted this endpoint would be a complete bypass of the fix."""
        from app.domain.user.login_security import MAX_TWO_FACTOR_ATTEMPTS

        secret, _codes = self._enable_2fa()
        wrong = self._wrong_totp(secret)

        def login(code: str):
            return self.client.post(
                "/users/auth/login",
                json={
                    "username": self.user.username,
                    "password": self.user.password,
                    "totp_code": code,
                },
            )

        for attempt in range(MAX_TWO_FACTOR_ATTEMPTS):
            resp = login(wrong)
            assert resp.status_code in (401, 403), f"attempt {attempt + 1}: {resp.text}"

        blocked = login(pyotp.TOTP(secret).now())
        assert blocked.status_code == 403, blocked.text

    def test_backup_codes_have_their_own_tighter_budget(self):
        from app.domain.user.login_security import MAX_BACKUP_CODE_ATTEMPTS

        secret, codes = self._enable_2fa()

        for attempt in range(MAX_BACKUP_CODE_ATTEMPTS):
            resp = self._verify(self._temp_token(), "deadbeef")
            assert resp.status_code in (401, 403), f"attempt {attempt + 1}: {resp.text}"

        # Spent: even a genuine backup code is refused.
        blocked = self._verify(self._temp_token(), codes[0])
        assert blocked.status_code == 403, blocked.text

        # But TOTP still works — that is what "separate budget" buys, and it
        # is why backup guesses can be capped harder than a phone code.
        ok = self._verify(self._temp_token(), pyotp.TOTP(secret).now())
        assert ok.status_code == 200, ok.text

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
