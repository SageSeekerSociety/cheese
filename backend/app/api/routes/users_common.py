"""The sudo ticket, for the route modules that redeem one.

Fourth slice of `app/api/routes/users.py` (arch review C-backend.md §3.3):
the passkey, two-factor and real-name identity groups move into
`users_passkey.py`, `users_2fa.py` and `users_identity.py`. Each of the three
gates a route on a fresh re-authentication, so each spends a sudo ticket
through `_spend_sudo_ticket`, and so do two things that stay behind:
`PATCH /users/{userId}/password` in `users_password.py`, and the OAuth unbind
in `users.py` itself. A helper five modules redeem through does not belong to
any one of them, which is what makes this module the one place it can live.

What is here. `_spend_sudo_ticket` and the reservation scope it claims in.
The scope travels with it rather than staying behind because it is a single
value, not a name to re-export: the ticket's issuer, `_issue_sudo_ticket`, is
the only other writer, and it lives in `users.py` with the routes that mint
tickets (`POST /users/auth/sudo`, and the passkey-enrollment offer a finished
sign-in hands back). Leaving the constant in `users.py` while the spender
imports it would make these two modules import each other; importing it from
here keeps the dependency one-way (`users.py` -> `users_common.py`) and keeps
both halves claiming in the same namespace, so they cannot drift apart.

What is deliberately not here. `_issue_sudo_ticket` stays in `users.py`: only
that file mints, so moving it would put the minting side of the pair in a
module named after what the *four* sides share while its sole caller sits
somewhere else. `_challenge_from_credential` also stays: `POST /users/auth/sudo`
reads a WebAuthn assertion too, so it is not the passkey module's to keep.

Boundaries. Nothing here imports a domain's `models` or `repositories` module:
the two imports are `app.common.auth` (the ticket format) and `app.core.errors`
(the two refusals), so no line moves in `.importlinter` or in
`tests/unit/test_domain_import_guard.py`. There is no router here and nothing
to mount: `app.main._discover_routers` finds no module-level `APIRouter`, and
the two names below are imported by the modules that do mount one.
"""

import logging

from app.common.auth import SudoPurpose
from app.core.errors import InternalServerError, SudoRequiredError
from app.domain.block.notice_text import say

logger = logging.getLogger(__name__)

# Namespaces the reservations behind a sudo ticket. Its own scope, so a ticket
# can never be redeemed by whatever else happens to hold a matching ``jti``.
_SUDO_TICKET_SCOPE = "sudo_ticket"


async def _spend_sudo_ticket(
    ticket: object, *, user_id: int, purpose: SudoPurpose
) -> None:
    """Redeem a sudo ticket for exactly this user and this operation, once.

    Every refusal is the same ``SudoRequiredError``, because every refusal has
    the same remedy — re-authenticate and try again — and because saying which
    of the four checks failed would tell a holder of a stolen session whether
    a captured ticket was expired, already spent, or simply for something
    else.

    Fail-closed when Redis is unreachable: without the reservation there is no
    way to tell a first use from a replay, and "cannot tell" is not "allow".
    """
    from app.common.auth import verify_sudo_ticket
    from app.core.single_use_state import SingleUseUnavailableError, claim

    claims = verify_sudo_ticket(ticket) if isinstance(ticket, str) and ticket else None
    if claims is None or claims.user_id != user_id or claims.purpose != purpose:
        raise SudoRequiredError("Re-authentication required for this operation")

    try:
        spent = await claim(_SUDO_TICKET_SCOPE, claims.jti)
    except SingleUseUnavailableError:
        logger.exception("sudo: cannot claim ticket uid=%s", user_id)
        raise InternalServerError(say("securityCheckUnavailable")) from None
    if not spent:
        raise SudoRequiredError("Re-authentication required for this operation")
