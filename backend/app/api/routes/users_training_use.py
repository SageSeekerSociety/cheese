"""Whether a person lets their content be used to train models (privacy policy 1.2).

The policy says content may be used, anonymised, to train models and be given
to AI model and technology providers we work with, and that a person may opt
out. This is where they do it themselves (`PUT /users/me/training-use`); an
emailed request is honoured by an admin through `admin_training_use.py`. The
owner's user record carries the current answer as `trainingAllowed`
(`UserAuthService.build_user_dto`).

Ordering. This module sorts after the `users` package, so its router mounts
after that package's. `PUT /users/me/training-use` has two segments under
`/users`, and no earlier PUT route has that shape, so nothing registered before
it can shadow it.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.domain.user.services import set_training_opt_out

router = APIRouter(prefix="/users", tags=["Users"], dependencies=[ROUTE_DECIDES])


class TrainingUseRequest(BaseModel):
    allowed: bool


@router.put(
    "/me/training-use",
    summary="Let or stop the signed-in user's content being used for training",
)
async def set_my_training_use(
    payload: TrainingUseRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    user = await set_training_opt_out(
        session, auth_user.user_id, opted_out=not payload.allowed
    )
    if user is None:
        raise NotFoundError("User not found")
    return ok({"allowed": user.training_opted_out_at is None})
