"""Signed state for the #192 GitHub App install / account-link flows."""

import uuid

from app.core import github_install_state as gis
from app.core import tokens
from app.core.sandbox_auth import mint_scoped_token


def test_install_state_roundtrip():
    pid = uuid.uuid4()
    state = gis.mint_install_state(pid)
    assert gis.verify_install_state(state) == pid


def test_install_state_expired_rejected():
    state = gis.mint_install_state(uuid.uuid4(), ttl_s=-1)
    assert gis.verify_install_state(state) is None


def test_install_state_garbage_rejected():
    assert gis.verify_install_state("not-a-jwt") is None
    assert gis.verify_install_state("") is None


def test_install_state_tampered_rejected():
    state = gis.mint_install_state(uuid.uuid4())
    header, payload, sig = state.split(".")
    forged = f"{header}.{payload}.{sig[:-2]}xx"
    assert gis.verify_install_state(forged) is None


def test_install_state_does_not_validate_as_account_link_state():
    """Forging one flow's state from the other must not work, even though
    both are HS256/jwt_secret — the `type` claim is the only thing keeping
    them apart."""
    pid = uuid.uuid4()
    install_state = gis.mint_install_state(pid)
    assert gis.verify_account_link_state(install_state) is None


def test_install_state_does_not_validate_as_session_token_or_scoped_token():
    """A #192 state leaks into browser history / GitHub referrers — it must
    never double as a session token or a sandbox scoped-token, even though
    all three are HMAC-signed with material derived the same way."""
    pid = uuid.uuid4()
    install_state = gis.mint_install_state(pid)
    assert tokens.verify_session_token(install_state) is None

    scoped = mint_scoped_token(project_id=str(pid))
    assert gis.verify_install_state(scoped) is None


def test_account_link_state_roundtrip_with_and_without_return_project():
    claims = gis.verify_account_link_state(gis.mint_account_link_state(42))
    assert claims is not None
    assert claims.user_id == 42
    assert claims.return_project_id is None

    pid = uuid.uuid4()
    claims2 = gis.verify_account_link_state(
        gis.mint_account_link_state(42, return_project_id=pid)
    )
    assert claims2 is not None
    assert claims2.return_project_id == pid


def test_account_link_state_expired_rejected():
    state = gis.mint_account_link_state(1, ttl_s=-1)
    assert gis.verify_account_link_state(state) is None


def test_account_link_state_does_not_validate_as_install_state():
    state = gis.mint_account_link_state(1)
    assert gis.verify_install_state(state) is None
