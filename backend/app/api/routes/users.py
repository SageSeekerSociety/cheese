"""User routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
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
    """极简登录 (Phase 0): get-or-create by handle, no password. The frontend
    keeps the returned identity locally and sends it as the author of actions."""
    user = await UserService(db).login(handle=body.handle, name=body.name)
    return ok(UserOut.model_validate(user).model_dump(mode="json"))


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
