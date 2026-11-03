"""Whether a space is members-only or open, and the review opening it waits for.

A members-only space is read by its managers and members. An open space is
read, and taken part in, by everyone signed in whose registered email is in
its scope — a list of suffixes such as ``ruc.edu.cn`` (``Space.open_scope``;
the rule itself is ``SpaceRepository.build_reader_predicate``).

Widening who reads a space is the platform's call, as approving a new space
is: everything in it becomes readable to a whole school. So opening a space,
or adding a suffix its scope does not already cover, files a
``SpaceOpenRequest`` and leaves the space as it was until a platform admin
approves it. Narrowing — back to members-only, or a scope inside the current
one — takes effect at once, because it reveals nothing.
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.domain.space.models import (
    Space,
    SpaceOpenRequest,
    SpaceOpenRequestStatus,
)

#: A suffix is a domain: labels of letters, digits and ``-``, joined by dots,
#: at least two labels. Nothing else is allowed in, so a stored suffix can
#: never act as a ``LIKE`` wildcard in the scope predicate.
_SUFFIX = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
#: An open space's scope names a few institutions, not a directory.
MAX_SUFFIXES = 20


def normalize_scope(raw: Sequence[str]) -> list[str]:
    """The suffixes as stored: trimmed, lower-case, a leading ``@`` or ``.``
    dropped, duplicates removed, in the order given. At least one is required:
    an open space with no scope is not something this release offers."""
    scope: list[str] = []
    for entry in raw:
        value = entry.strip().lower().lstrip("@.")
        if not value:
            continue
        if not _SUFFIX.match(value):
            raise BadRequestError(
                f"Invalid email suffix: {entry}", data={"suffix": entry}
            )
        if value not in scope:
            scope.append(value)
    if not scope:
        raise BadRequestError("An open space needs at least one email suffix")
    if len(scope) > MAX_SUFFIXES:
        raise BadRequestError(f"An open space takes at most {MAX_SUFFIXES} suffixes")
    return scope


def _covered(scope: Sequence[str], suffix: str) -> bool:
    """Whether everyone ``suffix`` admits is already admitted by ``scope`` —
    the same matching the scope predicate does."""
    return any(suffix == s or suffix.endswith("." + s) for s in scope)


class SpaceAccessService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_request(self, space_id: int) -> SpaceOpenRequest | None:
        """The space's most recent request to open, whatever became of it."""
        return await self._session.scalar(
            select(SpaceOpenRequest)
            .where(SpaceOpenRequest.space_id == space_id)
            .order_by(SpaceOpenRequest.created_at.desc(), SpaceOpenRequest.id.desc())
            .limit(1)
        )

    async def set_members_only(self, space_id: int) -> Space:
        """Back to members-only, at once; a request still waiting is dropped."""
        space = await self._locked_space(space_id)
        space.open_scope = None
        space.updated_at = datetime.now(UTC)
        await self._drop_pending(space.id)
        await self._session.flush()
        return space

    async def open(
        self, space_id: int, *, scope: Sequence[str], requested_by: int
    ) -> Space:
        """Open the space to ``scope``. A scope the space already reads to
        (open, and every suffix covered) applies at once; anything wider files
        or rewrites the pending request and leaves the space as it is."""
        space = await self._locked_space(space_id)
        if space.review_status != "APPROVED":
            raise ConflictError("A space under review cannot be opened")
        wanted = normalize_scope(scope)
        current = space.open_scope
        if current is not None and all(_covered(current, s) for s in wanted):
            space.open_scope = wanted
            space.updated_at = datetime.now(UTC)
            await self._drop_pending(space.id)
            await self._session.flush()
            return space
        now = datetime.now(UTC)
        pending = await self._session.scalar(
            select(SpaceOpenRequest)
            .where(
                SpaceOpenRequest.space_id == space.id,
                SpaceOpenRequest.status == SpaceOpenRequestStatus.PENDING.value,
            )
            .with_for_update()
        )
        if pending is None:
            pending = SpaceOpenRequest(
                space_id=space.id,
                status=SpaceOpenRequestStatus.PENDING.value,
                created_at=now,
            )
            self._session.add(pending)
        pending.scope = wanted
        pending.requested_by = requested_by
        pending.updated_at = now
        await self._session.flush()
        return space

    async def review(
        self, request_id: int, *, approved: bool, reason: str, reviewer: str
    ) -> tuple[SpaceOpenRequest, Space]:
        """A platform admin's decision. Approving opens the space to the
        request's scope; rejecting needs a reason the owner will read."""
        request = await self._session.scalar(
            select(SpaceOpenRequest)
            .where(SpaceOpenRequest.id == request_id)
            .with_for_update()
        )
        if request is None:
            raise NotFoundError.for_resource("spaceOpenRequest", request_id)
        if request.status != SpaceOpenRequestStatus.PENDING.value:
            raise ConflictError("This request has already been reviewed")
        space = await self._locked_space(request.space_id)
        if not approved and not reason.strip():
            raise BadRequestError("A rejection reason is required")
        now = datetime.now(UTC)
        request.status = (
            SpaceOpenRequestStatus.APPROVED.value
            if approved
            else SpaceOpenRequestStatus.REJECTED.value
        )
        request.reason = None if approved else reason.strip()
        request.reviewed_by = reviewer
        request.reviewed_at = now
        request.updated_at = now
        if approved:
            space.open_scope = list(request.scope)
            space.updated_at = now
        await self._session.flush()
        return request, space

    async def list_requests(
        self, *, status: str, limit: int
    ) -> list[tuple[SpaceOpenRequest, Space]]:
        """Requests in ``status`` with their spaces, newest first."""
        rows = await self._session.execute(
            select(SpaceOpenRequest, Space)
            .join(Space, Space.id == SpaceOpenRequest.space_id)
            .where(
                SpaceOpenRequest.status == status,
                Space.deleted_at.is_(None),
            )
            .order_by(SpaceOpenRequest.updated_at.desc(), SpaceOpenRequest.id.desc())
            .limit(limit)
        )
        return [(request, space) for request, space in rows.all()]

    async def _locked_space(self, space_id: int) -> Space:
        space = await self._session.scalar(
            select(Space)
            .where(Space.id == space_id, Space.deleted_at.is_(None))
            .with_for_update()
        )
        if space is None:
            raise NotFoundError.for_resource("space", space_id)
        return space

    async def _drop_pending(self, space_id: int) -> None:
        await self._session.execute(
            delete(SpaceOpenRequest).where(
                SpaceOpenRequest.space_id == space_id,
                SpaceOpenRequest.status == SpaceOpenRequestStatus.PENDING.value,
            )
        )
