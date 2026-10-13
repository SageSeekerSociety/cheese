"""The two dependencies every route module asks for, defined once.

A route module that needs the database session or the signed-in user imports the
alias from here; it does not rebuild it with `Annotated[..., Depends(...)]`, and it
does not borrow it from another route module. Light on purpose: the standalone
connection apps import route modules, and must not pick up `app.api.deps`.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.db import get_db

DbSession = Annotated[AsyncSession, Depends(get_db)]
AuthUser = Annotated[AuthUserInfo, Depends(require_auth_user)]
