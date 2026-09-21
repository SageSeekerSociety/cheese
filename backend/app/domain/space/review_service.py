"""Application and review transitions for newly created spaces."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.domain.space.models import Space, SpaceAdminRelation, SpaceAdminRole
from app.domain.user.models import User


def application_view(space: Space) -> dict:
    return {
        "id": space.id,
        "name": space.name,
        "intro": space.intro,
        "avatarId": space.avatar_id,
        "description": space.description,
        "reviewStatus": space.review_status,
        "reviewReason": space.review_reason,
        "reviewedBy": space.reviewed_by,
        "reviewedAt": space.reviewed_at.isoformat() if space.reviewed_at else None,
        "createdAt": space.created_at.isoformat(),
    }


class SpaceReviewService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def is_owner(self, space_id: int, user_id: int) -> bool:
        return (
            await self.session.execute(
                select(SpaceAdminRelation.id).where(
                    SpaceAdminRelation.space_id == space_id,
                    SpaceAdminRelation.user_id == user_id,
                    SpaceAdminRelation.role == SpaceAdminRole.OWNER.value,
                    SpaceAdminRelation.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none() is not None

    async def list_applications(
        self,
        *,
        owner_id: int | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        stmt = select(Space).where(Space.deleted_at.is_(None))
        if owner_id is not None:
            stmt = stmt.join(
                SpaceAdminRelation, SpaceAdminRelation.space_id == Space.id
            ).where(
                SpaceAdminRelation.user_id == owner_id,
                SpaceAdminRelation.role == SpaceAdminRole.OWNER.value,
                SpaceAdminRelation.deleted_at.is_(None),
            )
        if status:
            stmt = stmt.where(Space.review_status == status)
        rows = (
            (
                await self.session.execute(
                    stmt.order_by(Space.updated_at.desc(), Space.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        owners = (
            await self.session.execute(
                select(SpaceAdminRelation.space_id, User.username)
                .join(User, User.id == SpaceAdminRelation.user_id)
                .where(
                    SpaceAdminRelation.space_id.in_([row.id for row in rows]),
                    SpaceAdminRelation.role == SpaceAdminRole.OWNER.value,
                    SpaceAdminRelation.deleted_at.is_(None),
                )
            )
        ).all()
        handles = {space_id: username for space_id, username in owners}
        return [{**application_view(row), "owner": handles.get(row.id)} for row in rows]

    async def locked_space(self, space_id: int) -> Space:
        row = (
            await self.session.execute(
                select(Space)
                .where(Space.id == space_id, Space.deleted_at.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Space not found")
        return row

    async def review(
        self, space_id: int, *, approved: bool, reason: str, reviewer: str
    ) -> dict:
        row = await self.locked_space(space_id)
        if row.review_status != "PENDING":
            raise ConflictError("This application has already been reviewed")
        if not approved and not reason.strip():
            raise BadRequestError("A rejection reason is required")
        row.review_status = "APPROVED" if approved else "REJECTED"
        row.review_reason = None if approved else reason.strip()
        row.reviewed_by = reviewer
        now = datetime.now(UTC)
        row.reviewed_at = now
        row.updated_at = now
        await self.session.flush()
        return application_view(row)

    async def resubmit(
        self,
        space_id: int,
        *,
        user_id: int,
        name: str,
        intro: str,
        avatar_id: int | None = None,
    ) -> dict:
        if not await self.is_owner(space_id, user_id):
            raise NotFoundError("Space not found")
        row = await self.locked_space(space_id)
        if row.review_status != "REJECTED":
            raise ConflictError("Only rejected applications can be resubmitted")
        if not name.strip():
            raise BadRequestError("Space name is required")
        duplicate = (
            await self.session.execute(
                select(Space.id).where(
                    Space.name == name.strip(),
                    Space.id != space_id,
                    Space.deleted_at.is_(None),
                )
            )
        ).first()
        if duplicate:
            raise ConflictError("A space with this name already exists")
        row.name, row.intro = name.strip(), intro.strip()
        if avatar_id is not None:
            row.avatar_id = avatar_id
        row.review_status = "PENDING"
        row.review_reason = row.reviewed_by = row.reviewed_at = None
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return application_view(row)
