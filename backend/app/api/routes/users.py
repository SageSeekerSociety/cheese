"""User routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.core.tokens import mint_session_token
from app.domain.user.schemas import UserCreate, UserLogin, UserOut, UserUpdate
from app.domain.user.services import UserService

router = APIRouter(prefix="/api/users", tags=["users"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("")
async def create_user(body: UserCreate, db: DbSession) -> dict:
    user = await UserService(db).create(body)
    return ok(UserOut.model_validate(user).model_dump(mode="json"))


@router.post("/login")
async def login(body: UserLogin, db: DbSession) -> dict:
    """极简登录 (Phase 0 UX kept): get-or-create by handle, no password. P1 adds a
    signed session **token** to the response — the frontend stores it and sends it
    as ``Authorization: Bearer`` so the actor is resolved from a verified token,
    not a body field. The handle is still returned for the compat fallback."""
    user = await UserService(db).login(handle=body.handle, name=body.name)
    payload = UserOut.model_validate(user).model_dump(mode="json")
    payload["token"] = mint_session_token(handle=user.handle, user_id=user.id)
    return ok(payload)


@router.get("")
async def list_users(db: DbSession) -> dict:
    users, total = await UserService(db).list_all()
    items = [UserOut.model_validate(u).model_dump(mode="json") for u in users]
    return ok(page(items, total))


@router.get("/{handle}")
async def get_user(handle: str, db: DbSession) -> dict:
    user = await UserService(db).get_or_404(handle)
    return ok(UserOut.model_validate(user).model_dump(mode="json"))


@router.put("/{handle}")
async def update_user(handle: str, body: UserUpdate, db: DbSession) -> dict:
    user = await UserService(db).update(handle, body)
    return ok(UserOut.model_validate(user).model_dump(mode="json"))
