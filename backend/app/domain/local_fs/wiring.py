"""The 本机目录授权 seam: callers ask here, and never touch ``sql_repository``.

Same shape and same reason as ``device.wiring``: the service knows only the
repository protocol, so every caller would otherwise write
``LocalDirectoryService(SqlLocalFsRepository(session))`` and import the
repository module to do it — which is what makes repository modules stop being
domain-internal. This module is that missing seam.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.local_fs.service import LocalDirectoryService

__all__ = ["sql_local_directory_service"]


def sql_local_directory_service(session: AsyncSession) -> LocalDirectoryService:
    """A SQL-backed ``LocalDirectoryService`` on this session."""
    from app.domain.local_fs.sql_repository import SqlLocalFsRepository

    return LocalDirectoryService(SqlLocalFsRepository(session))
