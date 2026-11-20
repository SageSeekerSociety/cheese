"""Which name a person's sign-in carries.

2.0 membership is keyed by handle, so a token is turned into the handle it
names rather than its user id; a token that names no handle is resolved through
its user id. A handle is the person's NAME — the string every authorization key
in the platform is spelled with (``member_handle``, ``owner_handle``) — and
:func:`handle_named_by` is the single answer to "which name does this token
carry": ``ActorResolver`` names every request's person with it, and the
sockets that verify their own ``?token=`` (``app.api.proxy``, the device
screen) with the same function.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import AccessClaims
from app.core.obs import get_logger
from app.domain.user.repositories import UserRepository

_log = get_logger("cheesex.auth")


async def handle_named_by(session: AsyncSession, claims: AccessClaims) -> str:
    """The handle the caller of ``claims`` goes by.

    A token that states its ``handle`` claim names the person directly, and
    that value IS the handle. An all-digit one is no less a name: usernames are
    ``[a-zA-Z0-9_-]{4,32}`` (``app.domain.user.services``), so "2026201782" is
    an ordinary account name, and ``isdigit`` says nothing about whether a
    value is a name or an id.

    Reading one as the other is what this replaces: a caller named
    ``2026201782`` was taken for user id 2026201782, no such user existed, and
    the caller was told their sign-in had lapsed — on the files and git-diff
    routes alone, because every other route resolves the caller through
    ``ActorResolver``, which repairs the same confusion
    (``app.api.auth.resolver.ActorResolver._recover_numeric_handle``).

    Only a token that states no claim carries an id: its ``handle`` is then
    ``sub``, the int user PK, and the name has to come from the row. An id that
    resolves to nobody keeps its value — authorization still denies, as it must
    — and is logged rather than denied in silence.
    """
    if claims.handle_from_claim:
        return claims.handle
    if claims.user_id is not None:
        user = await UserRepository(session).get_by_id(claims.user_id)
        if user is not None:
            return user.username
        _log.warning("token_handle_unresolved", user_id=claims.user_id)
    return claims.handle
