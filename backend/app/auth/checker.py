from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.core import AuthUserInfo, SystemRole
from app.core.errors import AuthenticationRequiredError
from app.core.obs import bind_context
from app.db.session import get_db


async def get_auth_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthUserInfo:
    """The person a request acts as, as every other route resolves them
    (`ActorResolver.person`): their sign-in, or their 芝士 on a route listed for
    it. Anyone else is the guest."""
    # Deferred: app.api.auth's resolver imports app.auth (audience,
    # project_access), and this package's __init__ imports this module.
    from app.api.auth import get_actor_resolver

    person = await get_actor_resolver(request, db).person()
    if person is None or person.user_id is None:
        return AuthUserInfo(user_id=0, system_roles={SystemRole.GUEST})
    user_id = person.user_id
    # Every authenticated request funnels through here, so this is the one
    # place to attribute the request to a user: request.state feeds the
    # middleware's final "req" line, bind_context stamps every log line
    # emitted while handling it (the binding dies with the request task).
    request.state.auth_user_id = user_id
    bind_context(user=user_id)
    return AuthUserInfo(user_id=user_id, system_roles={SystemRole.USER})


async def require_auth_user(
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> AuthUserInfo:
    """Like get_auth_user but rejects anonymous/guest access."""
    if auth_user.user_id == 0:
        raise AuthenticationRequiredError("Login required")
    return auth_user
