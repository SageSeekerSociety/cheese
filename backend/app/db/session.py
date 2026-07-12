"""Compat module: 知是 routes/services import their session machinery from here.

Fusion unify: there is ONE engine / ONE connection pool for the whole app —
``app.core.db``. This module only re-exports it under the names the 知是 half
has always used, so both halves share the same pool and a request that spans
them cannot end up on two connections.
"""

from app.core.db import (
    async_session_factory as AsyncSessionLocal,
)
from app.core.db import (
    engine,
    get_db,
)

__all__ = ["AsyncSessionLocal", "engine", "get_db"]
