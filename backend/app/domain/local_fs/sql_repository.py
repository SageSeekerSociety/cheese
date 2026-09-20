"""SQL repository for 本机目录授权 — the rows of ``models`` behind the contract.

The only interesting thing in here is what it refuses to do: there is no method
that looks a grant up by path, and no LIKE, and no startswith. Grant lookup
happens in the service over normalized segments, on the full (small) set of a
device's grants. That is a deliberate cost — a device has a handful of grants,
and a segment comparison over all of them is cheap — paid to keep exactly one
implementation of containment, the one in ``paths.contains``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.local_fs.models import LocalDirectoryGrantRow, LocalFsAccessRow
from app.domain.local_fs.records import (
    AccessRecord,
    DirectoryGrant,
    LocalFsRepository,
)

__all__ = ["SqlLocalFsRepository"]


def _to_grant(row: LocalDirectoryGrantRow) -> DirectoryGrant:
    return DirectoryGrant(
        id=row.id,
        device_id=row.device_id,
        path=row.path,
        key=row.key,
        platform=row.platform,
        mode=row.mode,
        scope=row.scope,
        owner_user_id=row.owner_user_id,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        revoked_by_user_id=row.revoked_by_user_id,
        project_id=row.project_id,
    )


def _to_record(row: LocalFsAccessRow) -> AccessRecord:
    return AccessRecord(
        id=row.id,
        device_id=row.device_id,
        grant_id=row.grant_id,
        path=row.path,
        key=row.key,
        mode=row.mode,
        decision=row.decision,
        reason=row.reason,
        detail=row.detail,
        actor_handle=row.actor_handle,
        project_id=row.project_id,
        topic_id=row.topic_id,
        task_id=row.task_id,
        created_at=row.created_at,
    )


class SqlLocalFsRepository(LocalFsRepository):
    """Grants and the access log, on a session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_grant(self, grant: DirectoryGrant) -> None:
        self._session.add(
            LocalDirectoryGrantRow(
                id=grant.id,
                device_id=grant.device_id,
                owner_user_id=grant.owner_user_id,
                path=grant.path,
                key=grant.key,
                platform=grant.platform,
                mode=grant.mode,
                scope=grant.scope,
                project_id=grant.project_id,
                revoked_at=grant.revoked_at,
                revoked_by_user_id=grant.revoked_by_user_id,
                created_at=grant.created_at,
                updated_at=grant.created_at,
            )
        )
        await self._session.flush()

    async def get_grant(self, grant_id: uuid.UUID) -> DirectoryGrant | None:
        row = await self._session.get(LocalDirectoryGrantRow, grant_id)
        return _to_grant(row) if row is not None else None

    async def list_grants_for_device(
        self, device_id: str, *, include_revoked: bool = False
    ) -> list[DirectoryGrant]:
        query = select(LocalDirectoryGrantRow).where(
            LocalDirectoryGrantRow.device_id == device_id
        )
        if not include_revoked:
            query = query.where(LocalDirectoryGrantRow.revoked_at.is_(None))
        ordered = query.order_by(LocalDirectoryGrantRow.key)
        rows = (await self._session.execute(ordered)).scalars()
        return [_to_grant(row) for row in rows]

    async def list_grants_for_owner(
        self, owner_user_id: int, *, include_revoked: bool = False
    ) -> list[DirectoryGrant]:
        query = select(LocalDirectoryGrantRow).where(
            LocalDirectoryGrantRow.owner_user_id == owner_user_id
        )
        if not include_revoked:
            query = query.where(LocalDirectoryGrantRow.revoked_at.is_(None))
        ordered = query.order_by(LocalDirectoryGrantRow.key)
        rows = (await self._session.execute(ordered)).scalars()
        return [_to_grant(row) for row in rows]

    async def save_grant(self, grant: DirectoryGrant) -> None:
        row = await self._session.get(LocalDirectoryGrantRow, grant.id)
        if row is None:
            await self.add_grant(grant)
            return
        row.path = grant.path
        row.key = grant.key
        row.platform = grant.platform
        row.mode = grant.mode
        row.scope = grant.scope
        row.project_id = grant.project_id
        row.revoked_at = grant.revoked_at
        row.revoked_by_user_id = grant.revoked_by_user_id
        await self._session.flush()

    async def add_access(self, record: AccessRecord) -> None:
        self._session.add(
            LocalFsAccessRow(
                id=record.id,
                device_id=record.device_id,
                grant_id=record.grant_id,
                path=record.path,
                key=record.key,
                mode=record.mode,
                decision=record.decision,
                reason=record.reason,
                detail=record.detail,
                actor_handle=record.actor_handle,
                project_id=record.project_id,
                topic_id=record.topic_id,
                task_id=record.task_id,
                created_at=record.created_at,
            )
        )
        await self._session.flush()

    async def list_access(
        self,
        owner_user_id: int,
        *,
        device_id: str | None = None,
        limit: int = 100,
    ) -> list[AccessRecord]:
        # The owner's devices, from the grants they hold. A device with no grants
        # has no audit worth showing, and this avoids reaching into the device
        # domain from here (see the domain import guard).
        owned = (
            select(LocalDirectoryGrantRow.device_id)
            .where(LocalDirectoryGrantRow.owner_user_id == owner_user_id)
            .distinct()
        )
        query = select(LocalFsAccessRow).where(LocalFsAccessRow.device_id.in_(owned))
        if device_id is not None:
            query = query.where(LocalFsAccessRow.device_id == device_id)
        query = query.order_by(LocalFsAccessRow.created_at.desc()).limit(limit)
        rows = (await self._session.execute(query)).scalars()
        return [_to_record(row) for row in rows]

    async def list_access_for_device(
        self, device_id: str, *, limit: int = 100
    ) -> list[AccessRecord]:
        query = (
            select(LocalFsAccessRow)
            .where(LocalFsAccessRow.device_id == device_id)
            .order_by(LocalFsAccessRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(query)).scalars()
        return [_to_record(row) for row in rows]

    async def device_ids_for_owner(self, owner_user_id: int) -> Sequence[str]:
        query = (
            select(LocalDirectoryGrantRow.device_id)
            .where(LocalDirectoryGrantRow.owner_user_id == owner_user_id)
            .distinct()
        )
        return list((await self._session.execute(query)).scalars())

    async def delete_grants_for_device(self, device_id: str) -> None:
        """Drop every grant for a device. Present because the device cascade is
        what removes them in production; this is the explicit form the tests use
        to prove the audit rows are NOT removed with them."""
        await self._session.execute(
            delete(LocalDirectoryGrantRow).where(
                LocalDirectoryGrantRow.device_id == device_id
            )
        )
        await self._session.flush()
