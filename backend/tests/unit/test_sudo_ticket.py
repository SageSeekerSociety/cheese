"""A sudo ticket says one user re-authenticated for one operation (#389).

Everything the platform signs is signed with the same key, so "this JWT
verifies" says nothing on its own. What separates a ticket from the access
token sitting in the same browser is the ``type`` and ``purpose`` it carries —
and those checks are the whole of the gate, so they get tested directly rather
than only through the endpoint that happens to call them today.

Single use is not tested here: it lives in the Redis reservation, not the
token, and is covered where it can actually be observed (tests/integration).
"""

import time

import jwt

from app.common.auth import (
    SUDO_TICKET_TTL_S,
    SudoPurpose,
    create_access_token,
    mint_2fa_pending_token,
    mint_sudo_ticket,
    verify_sudo_ticket,
)
from app.core.config import settings


def test_a_minted_ticket_names_its_user_and_its_operation() -> None:
    minted = mint_sudo_ticket(77, SudoPurpose.TWO_FA_DISABLE)

    claims = verify_sudo_ticket(minted.token)
    assert claims is not None
    assert claims.user_id == 77
    assert claims.purpose is SudoPurpose.TWO_FA_DISABLE
    # The jti is what the caller reserves, so it has to be the same string the
    # verifier hands back — otherwise nothing could ever be claimed.
    assert claims.jti == minted.jti


def test_being_signed_in_is_not_having_re_authenticated() -> None:
    """An access token is signed by the same key and names the same user. If
    the ``type`` check were missing, holding a session would satisfy a gate
    whose entire purpose is to ask for more than a session."""
    assert verify_sudo_ticket(create_access_token(77)) is None


def test_a_half_finished_login_is_not_a_sudo_ticket() -> None:
    """The 2FA pending token is the closest neighbour: same shape, same jti
    discipline, and its holder has proved a password but is not signed in."""
    assert verify_sudo_ticket(mint_2fa_pending_token(77).token) is None


def test_a_ticket_from_another_signing_key_is_not_a_ticket() -> None:
    forged = jwt.encode(
        {
            "sub": "77",
            "type": "sudo",
            "purpose": SudoPurpose.TWO_FA_DISABLE.value,
            "jti": "0" * 32,
            "exp": int(time.time()) + 300,
        },
        "not-the-platform-secret",
        algorithm="HS256",
    )
    assert verify_sudo_ticket(forged) is None


def test_an_expired_ticket_is_not_a_ticket() -> None:
    """The window is what keeps "proved presence" from meaning "proved it at
    some point this month"."""
    expired = jwt.encode(
        {
            "sub": "77",
            "type": "sudo",
            "purpose": SudoPurpose.TWO_FA_DISABLE.value,
            "jti": "0" * 32,
            "exp": int(time.time()) - 1,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    assert verify_sudo_ticket(expired) is None
    assert 0 < SUDO_TICKET_TTL_S <= 15 * 60


def test_a_purpose_this_build_does_not_know_is_refused() -> None:
    """An unrecognised name cannot be matched against the entrance redeeming
    it, so treating it as a wildcard is the one reading that must not happen."""
    unknown = jwt.encode(
        {
            "sub": "77",
            "type": "sudo",
            "purpose": "billing:wire-transfer",
            "jti": "0" * 32,
            "exp": int(time.time()) + 300,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    assert verify_sudo_ticket(unknown) is None


def test_a_ticket_with_no_jti_cannot_be_spent_once_so_is_no_ticket() -> None:
    no_jti = jwt.encode(
        {
            "sub": "77",
            "type": "sudo",
            "purpose": SudoPurpose.TWO_FA_DISABLE.value,
            "exp": int(time.time()) + 300,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    assert verify_sudo_ticket(no_jti) is None
