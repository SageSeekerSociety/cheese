"""Shared sudo requests, WebAuthn parsing, tickets and sign-in sessions.

Fourth slice of `app/api/routes/users.py` (arch review C-backend.md §3.3):
the passkey, two-factor and real-name identity groups move into
`users_passkey.py`, `users_2fa.py` and `users_identity.py`. Each of the three
gates a route on a fresh re-authentication, so each spends a sudo ticket
through `_spend_sudo_ticket`, and so do two things that stay behind:
`PATCH /users/{userId}/password` in `users_password.py`, and the OAuth unbind
in `users.py` itself. A helper five modules redeem through does not belong to
any one of them, which is what makes this module the one place it can live.

The stateful helpers have two groups. The first is `_spend_sudo_ticket` and the
reservation scope it claims in. The scope travels with it rather than staying
behind because it is a single value, not a name to re-export: the ticket's
issuer, `_issue_sudo_ticket`, is the only other writer, and it lives in
`users.py` with the routes that mint tickets (`POST /users/auth/sudo`, and the
passkey-enrollment offer a finished sign-in hands back). Leaving the constant
in `users.py` while the spender imports it would make these two modules import
each other; importing it from here keeps the dependency one-way (`users.py` ->
`users_common.py`) and keeps both halves claiming in the same namespace, so
they cannot drift apart.

The second group is the session every sign-in funnels through: `issue_session`
and its whole closure. The closure is the refresh-cookie family
(`REFRESH_COOKIE`, `_REFRESH_COOKIE_PATH`, `_set_refresh_cookie`,
`_clear_refresh_cookie` — setter and clearer claim the same name and path, so
the pair travels together and cannot drift apart), the trust-cookie family
(`TRUST_COOKIE`, `_set_trust_cookie`), and the sudo sign-in vocabulary
(`_SUDO_SIGN_IN_METHODS`, `_sign_in_opens_sudo`). Every way of signing in ends
in `issue_session`, and sign-ins are minted from three modules — `users.py`
itself (five call sites), `app_sign_in.py` and `users_passkey.py` — so the
mint belongs beside the ticket those modules already share. `REFRESH_COOKIE`
goes with the family because the routes that read the raw cookie (refresh,
logout) can import a constant from here as well as from anywhere.

`issue_session` takes the trusted device as a plain `uuid.UUID`, not the ORM
row: a route module may not import a domain's models (C2), and the id is all
the issue needs — the domain's `TrustedDeviceService.mark_used` records the
sign-in against it, and refuses when the id names no device.

The shared pure contracts are `SudoTicketRequest`, used by passkey, two-factor,
identity and OAuth-unbind operations, and `challenge_from_credential`, used by passkey
verification and sudo's WebAuthn assertion. Neither performs I/O or grants
authorization; the calling route retains those responsibilities.

What is deliberately not here. `_issue_sudo_ticket` stays in `users.py`: only
that file mints, so moving it would put the minting side of the pair in a
module named after what the *four* sides share while its sole caller sits
somewhere else.
`_trusted_device` stays: its callers read `trust is None` to pick the login
method, so they still need the ORM row itself — only the session issue needed
the id. `_require_same_origin` stays: it guards refresh and logout, not the
mint.

Boundaries. Nothing here imports a domain's `models` or `repositories` module:
the imports are `app.common.auth` (the ticket format and the access token),
`app.core.*` (config, refusals, the client address), and the session and
trusted-device *services*, so no line moves in `.importlinter` or in
`tests/unit/test_domain_import_guard.py`. There is no router here and nothing
to mount: `app.main._discover_routers` finds no module-level `APIRouter`, and
the names below are imported by the modules that do mount one.
"""

import base64
import json as _json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import SudoPurpose, create_access_token, verify_sudo_ticket
from app.core.client_address import resolved_client_address
from app.core.config import GATEWAY_MOUNT, settings
from app.core.errors import BadRequestError, InternalServerError, SudoRequiredError
from app.core.sentences import say
from app.core.single_use_state import SingleUseUnavailableError, claim
from app.domain.user.sessions import SessionService
from app.domain.user.trusted_devices import Granted, TrustedDeviceService

logger = logging.getLogger(__name__)

# Namespaces the reservations behind a sudo ticket. Its own scope, so a ticket
# can never be redeemed by whatever else happens to hold a matching ``jti``.
_SUDO_TICKET_SCOPE = "sudo_ticket"


class SudoTicketRequest(BaseModel):
    """The body of an operation that redeems a sudo ticket and needs nothing
    else."""

    model_config = ConfigDict(populate_by_name=True)

    sudo_ticket: str | None = Field(default=None, alias="sudoTicket")


def challenge_from_credential(credential: dict) -> str:
    """Recover the challenge echoed inside the WebAuthn clientDataJSON. The
    reference contract sends only the credential — the server must not trust a
    separately-supplied challenge anyway."""

    try:
        raw = credential["response"]["clientDataJSON"]
        padded = raw + "=" * (-len(raw) % 4)
        client_data = _json.loads(base64.urlsafe_b64decode(padded))
        challenge = client_data["challenge"]
        if not isinstance(challenge, str) or not challenge:
            raise KeyError("challenge")
        return challenge
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequestError("Malformed WebAuthn credential") from exc


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


# The refresh token rides in this cookie and is sent to the auth routes only.
# Its path is the one the browser sees, through the gateway, not the route's.
REFRESH_COOKIE = "cheese_refresh"
_REFRESH_COOKIE_PATH = f"{GATEWAY_MOUNT}/users/auth"


def _set_refresh_cookie(
    response: Response, refresh_token: str, expires_at: datetime
) -> None:
    """The cookie lasts as long as the sign-in behind it. Without a Max-Age
    the browser drops it when the session ends while the access token in
    localStorage survives, and the next refresh signs the user out."""
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=max(0, int((expires_at - datetime.now(UTC)).total_seconds())),
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )


# A browser trusted to skip two-step verification holds this cookie. Scoped
# like the refresh cookie: only the sign-in routes ever read it.
TRUST_COOKIE = "cheese_trusted_device"


def _set_trust_cookie(response: Response, granted: Granted) -> None:
    response.set_cookie(
        TRUST_COOKIE,
        granted.token,
        max_age=max(0, int((granted.expires_at - datetime.now(UTC)).total_seconds())),
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )


# The credentials ``/auth/sudo`` accepts from any account that has them. A
# mailed code is one only without two-step verification, which is why an
# email-code sign-in that a trusted device let past the second step is not.
_SUDO_SIGN_IN_METHODS = frozenset({"passkey", "password", "totp"})


def _sign_in_opens_sudo(login_method: str, two_factor_skipped: bool) -> bool:
    """Whether this sign-in proved a credential sudo would have accepted, so
    that asking for one again straight away would only repeat it."""
    if login_method in _SUDO_SIGN_IN_METHODS:
        return True
    return login_method == "email_code" and not two_factor_skipped


async def issue_session(
    response: Response,
    request: Request,
    db: AsyncSession,
    *,
    user_id: int,
    handle: str,
    login_method: str,
    trust_device_id: uuid.UUID | None = None,
    grant_trust: bool = False,
) -> str:
    """Sign the user in: open a session, put its refresh token in the cookie
    on ``response``, and return the access token for the body.

    Every way of signing in ends here, so every sign-in is a session the
    account can see and end.

    ``trust_device_id`` is the trusted device that stood in for two-step
    verification, when one did. ``grant_trust`` trusts this browser from now
    on: the sign-in has just passed two-step verification and its owner asked
    not to be asked again here.
    """

    user_agent = request.headers.get("user-agent", "")
    # Behind a proxy that is not trusted to name the client, the peer is the
    # proxy; the device list shows no address rather than the proxy's.
    started = await SessionService(db).start(
        user_id,
        login_method,
        ip=resolved_client_address(request) or "",
        user_agent=user_agent,
        two_factor_skipped=trust_device_id is not None,
        sudo=_sign_in_opens_sudo(login_method, trust_device_id is not None),
    )
    trusted = TrustedDeviceService(db)
    if trust_device_id is not None:
        await trusted.mark_used(trust_device_id, started.session_id)
    if grant_trust:
        _set_trust_cookie(
            response,
            await trusted.grant(
                user_id,
                started.session_id,
                user_agent=user_agent,
                replacing=request.cookies.get(TRUST_COOKIE),
            ),
        )
    _set_refresh_cookie(response, started.refresh_token, started.expires_at)
    return create_access_token(user_id, handle=handle, sid=started.session_id)
