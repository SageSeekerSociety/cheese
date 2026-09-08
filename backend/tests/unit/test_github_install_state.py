"""Signed state for the #192 GitHub App install / account-link flows."""

import base64
import uuid

from app.core import github_install_state as gis
from app.core import tokens
from app.core.sandbox_auth import mint_scoped_token


def test_install_state_roundtrip():
    pid = uuid.uuid4()
    state = gis.mint_install_state(pid, user_id=1, handle="alice")
    claims = gis.verify_install_state(state)
    assert claims is not None
    assert (claims.project_id, claims.user_id, claims.handle) == (pid, 1, "alice")
    assert claims.jti


def test_install_state_expired_rejected():
    state = gis.mint_install_state(uuid.uuid4(), user_id=1, handle="alice", ttl_s=-1)
    assert gis.verify_install_state(state) is None


def test_install_state_garbage_rejected():
    assert gis.verify_install_state("not-a-jwt") is None
    assert gis.verify_install_state("") is None


def test_install_state_tampered_rejected():
    state = gis.mint_install_state(uuid.uuid4(), user_id=1, handle="alice")
    header, payload, sig = state.split(".")
    # Tamper with the *decoded* signature. The obvious version of this — overwrite
    # the last two base64url characters with "xx" — is not always a forgery: an
    # HS256 signature is 32 bytes, which base64url-encodes to 43 characters, and
    # 43 characters carry 258 bits. The two spare bits mean four different final
    # characters (w/x/y/z) decode to the same byte, so whenever a signature
    # happened to end in "xw", "xx", "xy" or "xz" the "forged" token decoded to
    # the genuine signature and verified — measured at 209 in 200_000 mints
    # (0.10%), which is how this test turned up red on a PR that touches nothing
    # near it. Flipping a bit after decoding always changes the HMAC.
    raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    forged_sig = base64.urlsafe_b64encode(bytes([raw[0] ^ 0x01]) + raw[1:]).rstrip(b"=")
    forged = f"{header}.{payload}.{forged_sig.decode()}"
    assert forged != state
    assert gis.verify_install_state(forged) is None


def test_install_state_does_not_validate_as_account_link_state():
    """Forging one flow's state from the other must not work, even though
    both are HS256/jwt_secret — the `type` claim is the only thing keeping
    them apart."""
    pid = uuid.uuid4()
    install_state = gis.mint_install_state(pid, user_id=1, handle="alice")
    assert gis.verify_account_link_state(install_state) is None


def test_install_state_does_not_validate_as_session_token_or_scoped_token():
    """A #192 state leaks into browser history / GitHub referrers — it must
    never double as a session token or a sandbox scoped-token, even though
    all three are HMAC-signed with material derived the same way."""
    pid = uuid.uuid4()
    install_state = gis.mint_install_state(pid, user_id=1, handle="alice")
    assert tokens.verify_session_token(install_state) is None

    scoped = mint_scoped_token(project_id=str(pid))
    assert gis.verify_install_state(scoped) is None


def test_account_link_state_roundtrip_with_and_without_return_project():
    claims = gis.verify_account_link_state(gis.mint_account_link_state(42).state)
    assert claims is not None
    assert claims.user_id == 42
    assert claims.return_project_id is None

    pid = uuid.uuid4()
    claims2 = gis.verify_account_link_state(
        gis.mint_account_link_state(42, return_project_id=pid).state
    )
    assert claims2 is not None
    assert claims2.return_project_id == pid


def test_account_link_state_expired_rejected():
    state = gis.mint_account_link_state(1, ttl_s=-1).state
    assert gis.verify_account_link_state(state) is None


def test_account_link_state_does_not_validate_as_install_state():
    state = gis.mint_account_link_state(1).state
    assert gis.verify_install_state(state) is None
