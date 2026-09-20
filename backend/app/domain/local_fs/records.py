"""Storage-agnostic 本机目录授权 records + the repository contract.

The dataclasses are the shape ``LocalDirectoryService`` works with; a repository
(in-memory or SQL) converts to and from its own rows. Kept apart from the
SQLAlchemy models for the same reason ``device`` keeps them apart: the service
can then be exercised with the in-memory repo — no database, no app, no HTTP.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from app.domain.local_fs.paths import Platform

__all__ = [
    "AccessRecord",
    "Decision",
    "DirectoryGrant",
    "GrantMode",
    "GrantScope",
    "LocalFsRepository",
    "Verdict",
]


class GrantMode(str, Enum):
    """How much of the directory a grant exposes.

    Two modes, not a permission matrix: read-only is for material the assistant
    should read and cannot damage (a grade sheet, a roster, a set of reference
    files), and read-write is for a working folder it is meant to change. A read
    grant never satisfies a write, which is the whole reason the distinction is
    stored rather than inferred.
    """

    READ = "read"
    READ_WRITE = "read_write"

    def permits(self, needed: GrantMode) -> bool:
        """Whether a grant held at this mode is enough for a request needing
        ``needed``. Read-write covers read; read does not cover read-write."""
        if self is GrantMode.READ_WRITE:
            return True
        return needed is GrantMode.READ


class GrantScope(str, Enum):
    """How far a grant reaches across the owner's own work.

    Project scope is this one piece of work, so a folder authorized for a term
    paper is not silently usable by next month's task. User scope is every one of
    the owner's matters, until they revoke it. The narrower scope is what the UI
    offers first, because authorizing one piece of work is the safer of the two
    to get wrong.
    """

    PROJECT = "project"
    USER = "user"


class Decision(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class DirectoryGrant:
    """One authorized directory on one machine.

    ``path`` is the canonical text (what the UI shows); ``key`` is the folded
    comparison key the decision is made on. They are stored together because a
    grant that can only be compared cannot be shown, and one that can only be
    shown cannot be compared safely.
    """

    id: uuid.UUID
    device_id: str
    path: str
    key: str
    platform: Platform
    mode: GrantMode
    scope: GrantScope
    owner_user_id: int
    created_at: datetime
    revoked_at: datetime | None = None
    revoked_by_user_id: int | None = None
    project_id: uuid.UUID | None = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def covers_project(self, project_id: uuid.UUID | None) -> bool:
        """Whether this grant applies to work in ``project_id``."""
        if self.scope is GrantScope.USER:
            return True
        return project_id is not None and self.project_id == project_id


@dataclass(frozen=True, slots=True)
class AccessRecord:
    """One line of the audit trail: every decision, allowed or denied.

    Denials are recorded with the same weight as successes. A log of only what
    worked cannot answer the boundary question — it shows the refusals that never
    happened.
    """

    id: uuid.UUID
    device_id: str
    path: str
    key: str
    mode: GrantMode
    decision: Decision
    reason: str
    created_at: datetime
    grant_id: uuid.UUID | None = None
    actor_handle: str | None = None
    project_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    """The answer to whether this path may be touched, with the reason kept for
    the screen."""

    decision: Decision
    reason: str
    detail: str
    grant: DirectoryGrant | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOWED


class LocalFsRepository(Protocol):
    """Data access for grants and the access log.

    Both the in-memory and the SQL repository satisfy this. Note what is NOT
    here: nothing reads a grant by path prefix, and nothing lists grants for
    anything but a device or an owner. Containment is decided in the service, on
    normalized segments — a repository that could answer which grant covers a
    path by query would be the string-prefix bug with a database behind it.
    """

    async def add_grant(self, grant: DirectoryGrant) -> None: ...
    async def get_grant(self, grant_id: uuid.UUID) -> DirectoryGrant | None: ...
    async def list_grants_for_device(
        self, device_id: str, *, include_revoked: bool = False
    ) -> list[DirectoryGrant]: ...
    async def list_grants_for_owner(
        self, owner_user_id: int, *, include_revoked: bool = False
    ) -> list[DirectoryGrant]: ...
    async def save_grant(self, grant: DirectoryGrant) -> None: ...

    async def add_access(self, record: AccessRecord) -> None: ...
    async def list_access(
        self, owner_user_id: int, *, device_id: str | None = None, limit: int = 100
    ) -> list[AccessRecord]: ...
    async def list_access_for_device(
        self, device_id: str, *, limit: int = 100
    ) -> list[AccessRecord]: ...

    async def device_ids_for_owner(self, owner_user_id: int) -> Sequence[str]: ...
