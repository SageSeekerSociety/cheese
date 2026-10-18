import hashlib

from app.domain.user import sessions, trusted_devices
from app.domain.user.tokens import digest_token


def test_user_token_entry_points_produce_the_sha256_digest() -> None:
    token = "user-token"
    expected = hashlib.sha256(token.encode()).hexdigest()

    assert digest_token(token) == expected
    assert sessions.digest_token(token) == expected
    assert trusted_devices.digest_token(token) == expected
