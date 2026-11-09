"""What a person may do to one kind of object, as one typed object per kind.

Each kind — a team, a space, a problem, a question, an answer — has a
:class:`Policy`: what its permissions are, which path parameter names one, how
to load it, whether a person may do a thing to it, and whether it exists for
them at all. ``app.auth.can`` asks the policy a permission belongs to, and a
route names what it needs with ``Can(...)`` (``app.api.write_access``).

Kinds where people hold a seat — owner, admin, member — and each seat holds a
fixed set of permissions are a :class:`SeatPolicy`: they say how to find the
seat and give the table. Kinds that follow from who the person is to the
object (a problem's setter, a question's asker) answer :meth:`Policy.decide`
themselves. A kind whose rows belong to one person and to nobody else — a rule
芝士 keeps for someone, a conversation of theirs — is an :class:`OwnerPolicy`.

The platform's admins manage every space and team without a seat in it. Each
kind says which of its permissions that covers (:attr:`Policy.overseen`), and
:meth:`Policy.may` grants those to an overseer whatever the kind's own rule
says. Who is an overseer is the admin domain's answer,
registered here by the application (:func:`oversee_with`) because no kind's
policy may import that domain. What belongs to one person — a private room,
a conversation, a document — is never overseen.

Most kinds are keyed by an ``int`` id; a kind keyed by a UUID says so with
:attr:`Policy.id_type`, and its ``load`` takes a UUID (``app.domain.assistant``
permissions, whose rows have no number at all).
"""

from __future__ import annotations

import enum
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

#: What names an object of some kind: its number, or its UUID.
ObjectId = int | uuid.UUID

#: Whether a person is one of the platform's admins. Set once, at import, by
#: the application that assembles the routes (``app.main``); it lives for the
#: process. Nothing registered — a script, a unit test that builds no app —
#: means nobody oversees anything.
_overseer: Callable[[AsyncSession, int], Awaitable[bool]] | None = None


def oversee_with(judge: Callable[[AsyncSession, int], Awaitable[bool]]) -> None:
    """Name who oversees every object: ``judge(session, user_id)``."""
    global _overseer
    _overseer = judge


async def oversees(session: AsyncSession, user_id: int | None) -> bool:
    """Whether the person holds every kind's :attr:`Policy.overseen`
    permissions on every object of that kind. Nobody signed in never does."""
    if not user_id or _overseer is None:
        return False
    return await _overseer(session, user_id)


class Policy[O, P: enum.StrEnum](ABC):
    #: The kind's name, as a refusal names it ("team").
    kind: ClassVar[str]
    #: The path parameter that names an object of this kind ("teamId").
    path_param: ClassVar[str]
    #: The kind's permissions.
    permissions: ClassVar[type[enum.StrEnum]]
    #: What the path parameter holds: its number (the default), or a UUID.
    #: A route's declared parameter is validated as this, so a kind that says
    #: the wrong thing answers 422 to every request instead of judging one.
    id_type: ClassVar[type[int] | type[uuid.UUID]] = int
    #: The permissions the platform's admins hold on every object of this kind
    #: (:func:`oversees`): managing it, never reading what is someone's own.
    #: An overseer sees every object of a kind that names any.
    overseen: ClassVar[frozenset[Any]] = frozenset()

    @abstractmethod
    async def load(self, session: AsyncSession, id: ObjectId) -> O | None:
        """The object, or ``None`` when there is none (deleted counts as none)."""

    @abstractmethod
    async def decide(
        self, session: AsyncSession, obj: O, user_id: int, permission: P
    ) -> bool:
        """The kind's own rule: whether the person may, by who they are to it."""

    async def visible(self, session: AsyncSession, obj: O, user_id: int) -> bool:
        """The kind's own rule for whether the object exists for this person.
        Anyone may see one by default; a kind that hides its objects says who
        sees them."""
        return True

    def overseeable(self, obj: O) -> bool:
        """Whether this object is under the platform's oversight at all. Every
        object of a kind with :attr:`overseen` permissions is, unless the kind
        says one belongs to a single person (a personal team)."""
        return True

    async def may(
        self, session: AsyncSession, obj: O, user_id: int, permission: P
    ) -> bool:
        """Whether the person may do ``permission`` to the object: the kind's
        own rule, or an overseer's hold on what the kind says is overseen."""
        if await self.decide(session, obj, user_id, permission):
            return True
        return permission in self.overseen and await self.overseen_by(
            session, obj, user_id
        )

    async def sees(self, session: AsyncSession, obj: O, user_id: int) -> bool:
        """Whether the object exists for this person."""
        if await self.visible(session, obj, user_id):
            return True
        return bool(self.overseen) and await self.overseen_by(session, obj, user_id)

    async def overseen_by(self, session: AsyncSession, obj: O, user_id: int) -> bool:
        """Whether the person holds this object's :attr:`overseen` permissions
        as a platform admin, whatever seat they hold in it."""
        return self.overseeable(obj) and await oversees(session, user_id)

    async def allows(
        self, session: AsyncSession, id: ObjectId, user_id: int, permission: P
    ) -> bool:
        """:meth:`may` for an object named by its id, for code that holds only
        the id — a service acting on a team's devices, say."""
        obj = await self.load(session, id)
        return obj is not None and await self.may(session, obj, user_id, permission)


class OwnerPolicy[O, P: enum.StrEnum](Policy[O, P]):
    """A kind whose every object belongs to one person, and to nobody else.

    The question is only «is this row theirs?». There is no seat to hold, so a
    stranger who holds the id is answered 404 rather than 403 — the row does
    not exist for them, which is what ``app.core.policy`` means by «what a
    person cannot see». Every permission is the owner's.
    """

    @abstractmethod
    async def owner(self, obj: O) -> int | None:
        """The person the row belongs to; ``None`` when it belongs to nobody."""

    async def decide(
        self, session: AsyncSession, obj: O, user_id: int, permission: P
    ) -> bool:
        return await self.owner(obj) == user_id

    async def visible(self, session: AsyncSession, obj: O, user_id: int) -> bool:
        return await self.owner(obj) == user_id


class SeatPolicy[O, P: enum.StrEnum, S](Policy[O, P]):
    #: What each seat may do.
    grants: ClassVar[Mapping[Any, frozenset[Any]]]

    @abstractmethod
    async def seat(self, session: AsyncSession, obj: O, user_id: int) -> S | None:
        """Where the person sits; ``None`` when they hold no seat."""

    async def decide(
        self, session: AsyncSession, obj: O, user_id: int, permission: P
    ) -> bool:
        seat = await self.seat(session, obj, user_id)
        return seat is not None and permission in self.grants[seat]

    async def visible(self, session: AsyncSession, obj: O, user_id: int) -> bool:
        return await self.seat(session, obj, user_id) is not None
