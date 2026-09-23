"""Secrets at rest: sealed per purpose and per record, under a rotatable key.

What must hold, observed from outside ``app.core.crypto``:

- a value reads back under the purpose and record it was written for, and under
  nothing else — a ciphertext copied into another user's row does not decrypt;
- after rotating DATA_ENCRYPTION_KEY to "new,old", old values still read and new
  values are written under the new key (they survive dropping the old one);
- a deployment refuses to boot without a real key, and nobody boots on a
  malformed one.
"""

import base64
import os

import pytest

from app.core.config import DEVELOPMENT_DATA_ENCRYPTION_KEY, Settings, settings
from app.core.crypto import DecryptionError, Purpose, decrypt, encrypt


def _new_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


@pytest.fixture
def use_key(monkeypatch: pytest.MonkeyPatch):
    def configure(value: str) -> None:
        monkeypatch.setattr(settings, "data_encryption_key", value)

    return configure


def test_round_trip_keeps_the_text(use_key) -> None:
    use_key(_new_key())
    for text in ["张三", "", "gho_" + "x" * 200]:
        stored = encrypt(Purpose.REALNAME, text, bound_to="user:7:real_name")
        assert text == "" or text not in stored
        assert decrypt(Purpose.REALNAME, stored, bound_to="user:7:real_name") == text


def test_the_same_text_never_encrypts_the_same_way_twice(use_key) -> None:
    use_key(_new_key())
    first = encrypt(Purpose.TOTP_SECRET, "JBSWY3DPEHPK3PXP", bound_to="user:1")
    second = encrypt(Purpose.TOTP_SECRET, "JBSWY3DPEHPK3PXP", bound_to="user:1")
    assert first != second


def test_a_value_copied_to_another_users_row_does_not_decrypt(use_key) -> None:
    use_key(_new_key())
    alice = encrypt(Purpose.TOTP_SECRET, "JBSWY3DPEHPK3PXP", bound_to="user:1")
    with pytest.raises(DecryptionError):
        decrypt(Purpose.TOTP_SECRET, alice, bound_to="user:2")


def test_a_value_does_not_decrypt_under_another_purpose(use_key) -> None:
    use_key(_new_key())
    stored = encrypt(Purpose.TOTP_PENDING_SECRET, "JBSWY3DPEHPK3PXP", bound_to="user:1")
    with pytest.raises(DecryptionError):
        decrypt(Purpose.TOTP_SECRET, stored, bound_to="user:1")


@pytest.mark.parametrize("garbage", ["", "plaintext", "v1:", "v1:abc:def", "gAAAA"])
def test_garbage_is_refused_rather_than_returned(use_key, garbage: str) -> None:
    use_key(_new_key())
    with pytest.raises(DecryptionError):
        decrypt(Purpose.OAUTH_TOKEN, garbage, bound_to="user:1:github:access_token")


def test_rotation_reads_old_values_and_writes_new_ones(use_key) -> None:
    old, new = _new_key(), _new_key()
    use_key(old)
    before = encrypt(Purpose.FORGE_PASSWORD, "hunter2", bound_to="project:p")

    use_key(f"{new},{old}")
    assert decrypt(Purpose.FORGE_PASSWORD, before, bound_to="project:p") == "hunter2"
    after = encrypt(Purpose.FORGE_PASSWORD, "hunter3", bound_to="project:p")

    # Only the new key left: what was written after the rotation still reads,
    # what was written before it no longer does.
    use_key(new)
    assert decrypt(Purpose.FORGE_PASSWORD, after, bound_to="project:p") == "hunter3"
    with pytest.raises(DecryptionError):
        decrypt(Purpose.FORGE_PASSWORD, before, bound_to="project:p")


def test_a_value_written_under_another_key_does_not_decrypt(use_key) -> None:
    use_key(_new_key())
    stored = encrypt(Purpose.FORGE_TOKEN, "token", bound_to="project:p")
    use_key(_new_key())
    with pytest.raises(DecryptionError):
        decrypt(Purpose.FORGE_TOKEN, stored, bound_to="project:p")


# --- boot guard ----------------------------------------------------------------

_REAL_SECRET = "a-genuinely-random-48-char-secret-value"


def _deployment(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret=_REAL_SECRET,
        platform_admin_handles=["ops"],
        **overrides,
    )


@pytest.mark.parametrize(
    "signal",
    [
        {"environment": "production"},
        {"environment": "development", "deployed_via_compose": True},
    ],
)
@pytest.mark.parametrize("key", ["", "  ", DEVELOPMENT_DATA_ENCRYPTION_KEY])
def test_a_deployment_refuses_to_boot_without_a_real_key(signal, key: str) -> None:
    with pytest.raises(RuntimeError) as excinfo:
        _deployment(data_encryption_key=key, **signal)
    assert "DATA_ENCRYPTION_KEY" in str(excinfo.value)


@pytest.mark.parametrize(
    "key",
    [
        "not-base64-at-all",
        base64.urlsafe_b64encode(os.urandom(16)).decode(),
        base64.urlsafe_b64encode(os.urandom(48)).decode(),
        _new_key() + ",",
        _new_key() + ",short",
    ],
)
@pytest.mark.parametrize("environment", ["production", "development", "test"])
def test_a_malformed_key_is_refused_everywhere(key: str, environment: str) -> None:
    with pytest.raises(RuntimeError) as excinfo:
        _deployment(data_encryption_key=key, environment=environment)
    message = str(excinfo.value)
    assert "DATA_ENCRYPTION_KEY" in message
    for entry in key.split(","):
        if entry:
            assert entry not in message


def test_a_deployment_with_a_real_key_boots() -> None:
    key = f"{_new_key()},{_new_key()}"
    booted = _deployment(data_encryption_key=key, deployed_via_compose=True)
    assert booted.data_encryption_key == key


@pytest.mark.parametrize("environment", ["development", "test"])
def test_development_and_tests_need_no_key(environment: str) -> None:
    booted = Settings(_env_file=None, environment=environment)
    assert booted.data_encryption_key == ""


def test_the_development_key_does_not_depend_on_jwt_secret(use_key, monkeypatch):
    use_key("")
    monkeypatch.setattr(settings, "jwt_secret", "one-secret")
    stored = encrypt(Purpose.REALNAME, "张三", bound_to="user:1:real_name")
    monkeypatch.setattr(settings, "jwt_secret", "another-secret")
    assert decrypt(Purpose.REALNAME, stored, bound_to="user:1:real_name") == "张三"
