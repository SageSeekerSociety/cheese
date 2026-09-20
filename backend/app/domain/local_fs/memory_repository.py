"""In-memory repository — the service's tests run on this, with no database.

Same contract as the SQL repository (``records.LocalFsRepository``), which is
what lets the decision logic be exercised purely: every containment and mode
case in ``tests/unit/test_local_fs_service.py`` runs here, so a failure there is a
failure of the decision and never of a session, a fixture or a rollback.

Deliberately the dumbest possible store: it keeps the rows in a list and does not
index them, because the one thing a test double must not do is make a decision on
its own. Anything that looks like an index here would be a second implementation
of containment that the SQL path does not share.
"""

from __future__ import annotations

import uuid

from app.domain.local_fs.records import (
    AccessRecord,
    DirectoryGrant,
    LocalFsRepository,
)

__all__ = ["InMemoryLocalFsRepository"]


class InMemoryLocalFsRepository(LocalFsRepository):
    """Grants in a dict, access records in a list."""

    def __init__(self) -> None:
        self._grants: dict[uuid.UUID, DirectoryGrant] = {}
        self._access: list[AccessRecord] = []

    async def add_grant(self, grant: DirectoryGrant) -> None:
        self._grants[grant.id] = grant

    async def get_grant(self, grant_id: uuid.UUID) -> DirectoryGrant | None:
        return self._grants.get(grant_id)

    async def list_grants_for_device(
        self, device_id: str, *, include_revoked: bool = False
    ) -> list[DirectoryGrant]:
        return [
            g
            for g in self._grants.values()
            if g.device_id == device_id and (include_revoked or not g.revoked)
        ]

    async def list_grants_for_owner(
        self, owner_user_id: int, *, include_revoked: bool = False
    ) -> list[DirectoryGrant]:
        return [
            g
            for g in self._grants.values()
            if g.owner_user_id == owner_user_id and (include_revoked or not g.revoked)
        ]

    async def save_grant(self, grant: DirectoryGrant) -> None:
        self._grants[grant.id] = grant

    async def add_access(self, record: AccessRecord) -> None:
        self._access.append(record)

    async def list_access(
        self,
        owner_user_id: int,
        *,
        device_id: str | None = None,
        limit: int = 100,
    ) -> list[AccessRecord]:
        # By the owner on the row, not by the devices they currently hold grants
        # for: after a device is forgotten there are no grants left to derive it
        # from, and that is when the question gets asked.
        rows = [
            r
            for r in self._access
            if r.owner_user_id == owner_user_id
            and (device_id is None or r.device_id == device_id)
        ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:limit]

    async def delete_grants_for_device(self, device_id: str) -> None:
        """Drop every grant for a device — what the ``device`` cascade does in
        production, in the explicit form a test can call."""
        for grant_id in [
            g.id for g in self._grants.values() if g.device_id == device_id
        ]:
            del self._grants[grant_id]
