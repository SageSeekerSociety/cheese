"""Session token sign/verify (P1 真人类鉴权)."""

import uuid

from app.core.tokens import mint_session_token, verify_session_token


def test_roundtrip_carries_handle_and_uid():
    uid = uuid.uuid4()
    token = mint_session_token(handle="alice", user_id=uid)
    claims = verify_session_token(token)
    assert claims is not None
    assert claims["sub"] == "alice"
    assert claims["uid"] == str(uid)
    assert claims["type"] == "access"


def test_uid_optional():
    claims = verify_session_token(mint_session_token(handle="bob", user_id=None))
    assert claims is not None
    assert claims["sub"] == "bob"
    assert claims["uid"] is None


def test_tampered_token_rejected():
    token = mint_session_token(handle="alice", user_id=None)
    # Flip the last char of the signature segment.
    body, sig = token.rsplit(".", 1)
    forged = f"{body}.{'A' if sig[0] != 'A' else 'B'}{sig[1:]}"
    assert verify_session_token(forged) is None


def test_expired_token_rejected():
    token = mint_session_token(handle="alice", user_id=None, ttl_s=-1)
    assert verify_session_token(token) is None


def test_garbage_and_empty_rejected():
    assert verify_session_token("") is None
    assert verify_session_token("not-a-jwt") is None
