"""The 本机目录授权 decision, and the only place a grant is created or revoked.

Everything a path question needs goes through :meth:`LocalDirectoryService.authorize`,
and it answers in one of exactly two ways: allowed, with the grant that allowed it,
or denied, with a named reason a person can read. It also writes an
:class:`~app.domain.local_fs.records.AccessRecord` either way — an allow that was
not recorded is a hole in the only evidence the owner has about their own disk.

Two refusals are worth naming here because they are policy, not mechanism:

* **A whole disk cannot be granted.** ``/`` and ``C:/`` are refused by
  :meth:`grant_directory`. The unit of authorization is a directory, and a
  refusal at this boundary is much cheaper than discovering later that every
  grant any user ever made is effectively 整台电脑.
* **A read grant does not authorize a write.** The covering grant is found
  first, and then asked whether its mode is enough; when it is not, the denial
  says so by name, so the screen can say 这个目录只授权了读取 rather than the
  useless 越界了.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from app.domain.local_fs.paths import (
    PathRefused,
    Platform,
    contains,
    normalize,
)
from app.domain.local_fs.records import (
    AccessRecord,
    Decision,
    DirectoryGrant,
    GrantMode,
    GrantScope,
    LocalFsRepository,
    Verdict,
)

__all__ = [
    "AuthorizeRequest",
    "DeviceGrants",
    "GrantRefused",
    "LocalDirectoryService",
    "MAX_ACCESS_PAGE",
]

# The audit screen is a list a person reads, not an export. The cap lives here so
# every caller of the audit read shares one number instead of each route picking
# its own.
MAX_ACCESS_PAGE = 200


class GrantRefused(Exception):
    """A grant that will not be created, carrying a reason the UI can show."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(reason + ": " + detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class AuthorizeRequest:
    """One question about one path, with the coordinates the audit needs.

    ``actor_handle`` and the three ids are all optional because the platform
    asks this question in places that do not know them yet — and a denial that is
    recorded with less context is still far better than one that is not recorded.
    """

    device_id: str
    path: str
    platform: Platform
    needed: GrantMode
    project_id: uuid.UUID | None = None
    actor_handle: str | None = None
    topic_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class DeviceGrants:
    """The grant set as the device must hold it.

    ``fingerprint`` covers the identity, path, mode and scope of every live grant
    but NOT its revocation: a revoke empties the set, and the fingerprint of the
    emptied set differs from the fingerprint of the set that had the grant, so
    the device is re-sent. That property is the whole reason a revoke reaches a
    device that is offline at the moment it happens.
    """

    device_id: str
    grants: tuple[DirectoryGrant, ...]
    fingerprint: str


def _fingerprint(grants: list[DirectoryGrant]) -> str:
    parts = sorted(
        g.id.hex
        + "|"
        + g.key
        + "|"
        + g.mode.value
        + "|"
        + g.scope.value
        + "|"
        + (g.project_id.hex if g.project_id else "")
        for g in grants
    )
    return ";".join(parts)


class LocalDirectoryService:
    """Grants, revocations, decisions and the access log — over any repository."""

    def __init__(self, repository: LocalFsRepository) -> None:
        self._repo = repository

    # -- lifecycle ---------------------------------------------------------

    async def grant_directory(
        self,
        *,
        device_id: str,
        owner_user_id: int,
        path: str,
        platform: Platform,
        mode: GrantMode,
        scope: GrantScope,
        project_id: uuid.UUID | None = None,
    ) -> DirectoryGrant:
        """Authorize one directory, or refuse by name.

        The path arrives already resolved on the device (symlinks followed, a
        tilde expanded to a home directory) — the platform has no way to resolve
        either, so it normalizes what it was given and refuses anything that is
        not already absolute.
        """
        if scope is GrantScope.PROJECT and project_id is None:
            raise GrantRefused(
                "project_required",
                "限定到某个项目的授权必须指明项目",
            )
        if scope is GrantScope.USER and project_id is not None:
            # A user-scope grant that named a project would be a project grant
            # wearing the wider label — the two must not be confusable, because
            # the wider one is what the decision reads as 对这个人所有事都生效.
            raise GrantRefused(
                "project_forbidden",
                "限到所有事的授权不能绑定单个项目",
            )

        try:
            normalized = normalize(path, platform)
        except PathRefused as refused:
            raise GrantRefused(refused.reason, refused.detail) from refused

        if normalized.is_root:
            # The unit of authorization is a directory, never a disk. This is
            # criterion one, and it is refused here rather than in the UI so that
            # no other caller can reach the store without passing it.
            raise GrantRefused(
                "root_not_grantable",
                "只能授权具体目录，不能授权整个盘或整台电脑",
            )

        existing = await self._repo.list_grants_for_device(device_id)
        for grant in existing:
            if (
                grant.key == normalized.key
                and grant.scope is scope
                and grant.project_id == project_id
                and grant.mode is mode
            ):
                # Idempotent: authorizing the same folder for the same work twice
                # is one grant, not two rows the audit would double-count.
                return grant

        grant = DirectoryGrant(
            id=uuid.uuid4(),
            device_id=device_id,
            path=normalized.text,
            key=normalized.key,
            platform=platform,
            mode=mode,
            scope=scope,
            owner_user_id=owner_user_id,
            created_at=datetime.now(UTC),
            project_id=project_id,
        )
        await self._repo.add_grant(grant)
        return grant

    async def revoke(
        self,
        grant_id: uuid.UUID,
        *,
        owner_user_id: int,
    ) -> DirectoryGrant | None:
        """Revoke one grant. Idempotent, and effective on the next question asked.

        The row is kept rather than deleted: the audit trail refers to it, and a
        trail whose grants have been deleted cannot say 为哪件事.
        """
        grant = await self._repo.get_grant(grant_id)
        if grant is None or grant.owner_user_id != owner_user_id:
            return None
        if grant.revoked:
            return grant
        # ``replace`` rather than a __dict__ splat: the record is a frozen
        # slots dataclass, which has no __dict__ at all.
        revoked = replace(
            grant,
            revoked_at=datetime.now(UTC),
            revoked_by_user_id=owner_user_id,
        )
        await self._repo.save_grant(revoked)
        return revoked

    async def list_grants(
        self,
        owner_user_id: int,
        *,
        device_id: str | None = None,
        include_revoked: bool = False,
    ) -> list[DirectoryGrant]:
        if device_id is not None:
            grants = await self._repo.list_grants_for_device(
                device_id, include_revoked=include_revoked
            )
            return [g for g in grants if g.owner_user_id == owner_user_id]
        return await self._repo.list_grants_for_owner(
            owner_user_id, include_revoked=include_revoked
        )

    async def effective_grants(
        self,
        device_id: str,
        *,
        project_id: uuid.UUID | None = None,
    ) -> DeviceGrants:
        """The live grants that apply to work in ``project_id`` on this device.

        This is what gets pushed to the device. A revoked grant cannot appear
        here — ``list_grants_for_device`` excludes revoked rows — so the push is
        also the revocation mechanism rather than a separate one.
        """
        live = await self._repo.list_grants_for_device(device_id)
        applicable = [g for g in live if g.covers_project(project_id)]
        applicable.sort(key=lambda g: g.key)
        return DeviceGrants(
            device_id=device_id,
            grants=tuple(applicable),
            fingerprint=_fingerprint(applicable),
        )

    # -- the decision ------------------------------------------------------

    async def authorize(self, request: AuthorizeRequest) -> Verdict:
        """Answer one path question, and record the answer.

        ``path`` is expected to be the path as the *device* resolved it. A path
        that cannot be normalized is a denial, not an error: the caller asked a
        question about a path, and 这个路径不合法 is an answer to it. Raising
        instead would let a malformed path crash the call and, worse, skip the
        audit row.
        """
        try:
            candidate = normalize(request.path, request.platform)
        except PathRefused as refused:
            return await self._record(
                request,
                Decision.DENIED,
                refused.reason,
                refused.detail,
                grant=None,
                key=request.path,
            )

        grants = await self._repo.list_grants_for_device(request.device_id)
        covering: list[DirectoryGrant] = []
        for grant in grants:
            if not grant.covers_project(request.project_id):
                continue
            if contains(normalize(grant.path, grant.platform), candidate):
                covering.append(grant)

        if not covering:
            return await self._record(
                request,
                Decision.DENIED,
                "no_grant",
                "这个路径不在任何授权目录里",
                grant=None,
                key=candidate.key,
            )

        for grant in covering:
            if grant.mode.permits(request.needed):
                return await self._record(
                    request,
                    Decision.ALLOWED,
                    "granted",
                    "已授权",
                    grant=grant,
                    key=candidate.key,
                )

        # Something covers the path, but not for this much. Reporting the
        # narrower cause separately is what lets the refusal be actionable.
        narrowest = covering[0]
        return await self._record(
            request,
            Decision.DENIED,
            "read_only_grant",
            "这个目录只授权了读取，不能写入",
            grant=narrowest,
            key=candidate.key,
        )

    async def _record(
        self,
        request: AuthorizeRequest,
        decision: Decision,
        reason: str,
        detail: str,
        *,
        grant: DirectoryGrant | None,
        key: str,
    ) -> Verdict:
        record = AccessRecord(
            id=uuid.uuid4(),
            device_id=request.device_id,
            path=request.path,
            key=key,
            mode=request.needed,
            decision=decision,
            reason=reason,
            created_at=datetime.now(UTC),
            grant_id=grant.id if grant else None,
            actor_handle=request.actor_handle,
            project_id=request.project_id,
            topic_id=request.topic_id,
            task_id=request.task_id,
            detail=detail,
        )
        # Written before the verdict is returned, and without swallowing a
        # failure: an access that happened but was not logged is exactly the
        # state an audit is supposed to make impossible, so a failed write has
        # to fail the call rather than quietly succeed behind an empty log.
        await self._repo.add_access(record)
        return Verdict(
            decision=decision,
            reason=reason,
            detail=detail,
            grant=grant,
        )

    # -- the audit ---------------------------------------------------------

    async def list_access(
        self,
        owner_user_id: int,
        *,
        device_id: str | None = None,
        limit: int = 100,
    ) -> list[AccessRecord]:
        """What was read or written, for the owner to read back."""
        capped = max(1, min(limit, MAX_ACCESS_PAGE))
        return await self._repo.list_access(
            owner_user_id, device_id=device_id, limit=capped
        )
