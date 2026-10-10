"""An admin records a person's training opt-out on their behalf.

The privacy policy (1.2) lets a person opt out by writing to ops@okcheese.com;
whoever answers that mail sets it here, by the person's handle. A person can
also set it themselves (`users_training_use.py`), and both write the same
column.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.response import ok
from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import NotFoundError
from app.domain.user.services import set_training_opt_out, user_by_handle

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[ROUTE_DECIDES])


class TrainingUseUpdate(BaseModel):
    allowed: bool


@router.put("/users/{handle}/training-use")
async def set_training_use(
    handle: str, payload: TrainingUseUpdate, db: DbSession, _admin: PlatformAdminDep
) -> dict:
    user = await user_by_handle(db, handle)
    if user is None:
        raise NotFoundError("User not found")
    await set_training_opt_out(db, user.id, opted_out=not payload.allowed)
    return ok(
        {
            "handle": handle,
            "allowed": user.training_opted_out_at is None,
            "opted_out_at": user.training_opted_out_at.isoformat()
            if user.training_opted_out_at
            else None,
        }
    )
