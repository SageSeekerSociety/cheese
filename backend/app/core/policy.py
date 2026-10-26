"""What a person may do to one kind of object, as one typed object per kind.

Each kind — a team, a space, a problem, a question, an answer — has a
:class:`Policy`: what its permissions are, which path parameter names one, how
to load it, whether a person may do a thing to it, and whether it exists for
them at all. ``app.auth.can`` asks the policy a permission belongs to, and a
route names what it needs with ``Can(...)`` (``app.api.write_access``).

Kinds where people hold a seat — owner, admin, member — and each seat holds a
fixed set of permissions are a :class:`SeatPolicy`: they say how to find the
seat and give the table. Kinds that follow from who the person is to the
object (a problem's setter, a question's asker) answer :meth:`Policy.may`
themselves.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession


class Policy[O, P: enum.StrEnum](ABC):
    #: The kind's name, as a refusal names it ("team").
    kind: ClassVar[str]
    #: The path parameter that names an object of this kind ("teamId").
    path_param: ClassVar[str]
    #: The kind's permissions.
    permissions: ClassVar[type[enum.StrEnum]]

    @abstractmethod
    async def load(self, session: AsyncSession, id: int) -> O | None:
        """The object, or ``None`` when there is none (deleted counts as none)."""

    @abstractmethod
    async def may(
        self, session: AsyncSession, obj: O, user_id: int, permission: P
    ) -> bool: ...

    async def sees(self, session: AsyncSession, obj: O, user_id: int) -> bool:
        """Whether the object exists for this person. Anyone may see one by
        default; a kind that hides its objects says who sees them."""
        return True

    async def allows(
        self, session: AsyncSession, id: int, user_id: int, permission: P
    ) -> bool:
        """:meth:`may` for an object named by its id, for code that holds only
        the id — a service acting on a team's devices, say."""
        obj = await self.load(session, id)
        return obj is not None and await self.may(session, obj, user_id, permission)


class SeatPolicy[O, P: enum.StrEnum, S](Policy[O, P]):
    #: What each seat may do.
    grants: ClassVar[Mapping[Any, frozenset[Any]]]

    @abstractmethod
    async def seat(self, session: AsyncSession, obj: O, user_id: int) -> S | None:
        """Where the person sits; ``None`` when they hold no seat."""

    async def may(
        self, session: AsyncSession, obj: O, user_id: int, permission: P
    ) -> bool:
        seat = await self.seat(session, obj, user_id)
        return seat is not None and permission in self.grants[seat]

    async def sees(self, session: AsyncSession, obj: O, user_id: int) -> bool:
        return await self.seat(session, obj, user_id) is not None
