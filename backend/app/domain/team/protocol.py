"""Team values and read interfaces shared with service callers."""

from datetime import datetime
from enum import Enum
from typing import Protocol

from sqlalchemy.orm import Mapped


class TeamVisibility(str, Enum):
    """Who can find a team without being in it.

    PUBLIC teams show up in search and open by id; STEALTH teams do neither and
    are reached only through the team's join link. How joining then works is
    the team's ``join_approval`` — the same switch a project has.
    """

    PUBLIC = "public"
    STEALTH = "stealth"


class TeamMemberRole:
    OWNER = 0
    ADMIN = 1
    MEMBER = 2


class ApplicationStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    CANCELED = "CANCELED"


class RecruitmentStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class TeamView(Protocol):
    """A team row as the route reads it, in the shape the ORM declares it.

    The members are ``Mapped[...]``, not the unwrapped values: pyright compares a
    caller's ``Team`` (whose attributes are ``Mapped`` descriptors) against this
    protocol's *class* members, so a plain ``int`` here would not match. Reading
    one off an instance still yields the value it wraps. Same shape as
    ``SpaceRow`` in ``routes/spaces.py``.
    """

    id: Mapped[int]
    name: Mapped[str]
    handle: Mapped[str | None]
    personal_owner_user_id: Mapped[int | None]
    intro: Mapped[str]
    description: Mapped[str]
    avatar_id: Mapped[int]
    visibility: Mapped[str]
    join_approval: Mapped[bool]
    join_token: Mapped[str | None]
    plan_key: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class TeamMemberView(Protocol):
    """A ``team_user_relation`` row as the route reads it, ``Mapped``-shaped for
    the same reason as ``TeamView`` above."""

    user_id: Mapped[int]
    role: Mapped[int]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
