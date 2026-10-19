"""An admin records a person's training opt-out on their behalf.

The privacy policy (1.2) lets a person opt out by writing to ops@okcheese.com;
whoever answers that mail reads and sets it here, by the person's handle. There
is no page for it: this is the only way the column is written.
"""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.admin_deps import PlatformAdminDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.user.services import set_training_opt_out, user_by_handle

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[ROUTE_DECIDES])


class TrainingUseUpdate(BaseModel):
    allowed: bool


class TrainingUse(BaseModel):
    handle: str
    allowed: bool
    opted_out_at: str | None


def _view(handle: str, opted_out_at: datetime | None) -> dict:
    return ok(
        {
            "handle": handle,
            "allowed": opted_out_at is None,
            "opted_out_at": opted_out_at.isoformat() if opted_out_at else None,
        }
    )


@router.get("/users/{handle}/training-use", **typed_response(TrainingUse))
async def get_training_use(
    handle: str, db: DbSession, _admin: PlatformAdminDep
) -> dict:
    user = await user_by_handle(db, handle)
    if user is None:
        raise NotFoundError(say("userNotFound"))
    return _view(handle, user.training_opted_out_at)


@router.put("/users/{handle}/training-use", **typed_response(TrainingUse))
async def set_training_use(
    handle: str, payload: TrainingUseUpdate, db: DbSession, _admin: PlatformAdminDep
) -> dict:
    user = await user_by_handle(db, handle)
    if user is None:
        raise NotFoundError(say("userNotFound"))
    await set_training_opt_out(db, user.id, opted_out=not payload.allowed)
    return _view(handle, user.training_opted_out_at)
