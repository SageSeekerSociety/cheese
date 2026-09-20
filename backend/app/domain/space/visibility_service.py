"""The one place that answers「这个空间谁能看见」.

Same shape as ``app/domain/task/visibility_service.py``, deliberately: a
service that answers the question for one space, and a static predicate that
answers it for a whole query — so hiding a space from the list and refusing a
direct link are the SAME rule rather than two rules that agree today.

The predicate is written against the ``space`` table, so it can be dropped
into any ``select(...).where(...)`` that selects from ``Space``.
"""

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceMember,
    SpaceVisibility,
)


class SpaceVisibilityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def can_view_space(self, *, space_id: int, user_id: int) -> bool:
        """Whether this user may see this space at all.

        Note what this does NOT consider: tasks, submissions, projects and
        the rest of what lives inside. Those keep their own rules, which is
        why removing a member hides the space and nothing else.
        """
        stmt = select(Space.id).where(
            Space.id == space_id,
            Space.deleted_at.is_(None),
            self.build_visibility_predicate(user_id=user_id),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    def build_visibility_predicate(*, user_id: int) -> ColumnElement[bool]:
        """The same rule as ``can_view_space``, as a SQL predicate over Space.

        Pass this to a query that selects spaces and it filters the list; a
        space that fails it is invisible both ways.
        """
        if user_id <= 0:
            # A guest holds no membership and no admin relation, so the only
            # branch that could match is the public one.
            return Space.visibility == SpaceVisibility.PUBLIC.value

        member_exists = exists().where(
            SpaceMember.space_id == Space.id,
            SpaceMember.user_id == user_id,
            SpaceMember.deleted_at.is_(None),
        )
        # Admins see the space they administer whether or not anyone ever
        # wrote a member row for them — spaces created before membership
        # existed have admins and no rows at all.
        admin_exists = exists().where(
            SpaceAdminRelation.space_id == Space.id,
            SpaceAdminRelation.user_id == user_id,
            SpaceAdminRelation.deleted_at.is_(None),
        )
        return or_(
            Space.visibility == SpaceVisibility.PUBLIC.value,
            member_exists,
            admin_exists,
        )
